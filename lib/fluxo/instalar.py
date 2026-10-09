"""Instala e desinstala o que o `montar` gerou, com um manifesto do que é do fluxo.

Regra central: nunca pisar num arquivo que não é nosso, nem num nosso que o usuário editou. Qualquer
conflito aborta o install inteiro (nada é gravado) até ele decidir: `--forcar` grava com backup, e o
`uninstall` devolve o backup ao lugar.
"""
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

CRIAR, ATUALIZAR, IGUAL, ADOTAR, CONFLITO, REMOVER, MANTER = (
    "criar", "atualizar", "igual", "adotar", "conflito", "remover", "manter")


@dataclass
class Acao:
    tipo: str
    rel: str
    arquivo: object = None  # o Arquivo a gravar (CRIAR, ATUALIZAR, IGUAL, ADOTAR, CONFLITO)
    motivo: str = ""
    backup: str = ""  # nome do .fluxo-bak que o --forcar deixou: o uninstall o devolve


class ConflitoErro(Exception):
    def __init__(self, conflitos):
        self.conflitos = conflitos
        super().__init__(f"{len(conflitos)} conflito(s)")


def sha(dados):
    return hashlib.sha256(dados).hexdigest()


def rel_seguro(rel):
    """O destino sempre dentro do HOME: nada absoluto, nada de `..`."""
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or ".." in p.parts or "\\" in rel:
        raise RuntimeError(f"caminho fora do HOME recusado: {rel!r}")
    return rel


def ler_manifesto(caminho):
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise RuntimeError(f"manifesto ilegível ({caminho}): {exc}; conserte ou apague-o com cuidado")
    arquivos = dados.get("arquivos") if isinstance(dados, dict) else None
    if not isinstance(arquivos, dict) or not all(
            isinstance(v, dict) and isinstance(v.get("sha256"), str) for v in arquivos.values()):
        raise RuntimeError(f"manifesto fora do formato ({caminho}): esperado "
                           '{"arquivos": {"<caminho>": {"sha256": "…"}}}; conserte ou apague-o com cuidado')
    for rel in arquivos:
        rel_seguro(rel)
    return arquivos


def _atual(caminho):
    try:
        return sha(caminho.read_bytes())
    except (FileNotFoundError, NotADirectoryError):
        return None
    except IsADirectoryError:
        return "<pasta>"


def planejar(arquivos, manifesto, home):
    home = Path(home)
    acoes, pedidos = [], set()
    for arq in arquivos:
        rel_seguro(arq.rel)
        pedidos.add(arq.rel)
        destino = home / arq.rel
        antes = manifesto.get(arq.rel) or {}
        if destino.is_symlink():  # gravar ou dar chmod seguiria o link para fora do HOME
            acoes.append(Acao(CONFLITO, arq.rel, arq, "é um link simbólico", antes.get("backup", "")))
            continue
        atual, novo = _atual(destino), sha(arq.conteudo)
        if atual is None:
            acoes.append(Acao(CRIAR, arq.rel, arq, backup=antes.get("backup", "")))
        elif atual == novo:
            acoes.append(Acao(IGUAL if antes.get("sha256") == novo else ADOTAR, arq.rel, arq,
                              backup=antes.get("backup", "")))
        elif antes.get("sha256") == atual:
            acoes.append(Acao(ATUALIZAR, arq.rel, arq, backup=antes.get("backup", "")))
        else:
            motivo = "editado depois do install" if antes else "já existe e não é do fluxo"
            acoes.append(Acao(CONFLITO, arq.rel, arq, motivo, antes.get("backup", "")))
    for rel, info in sorted(manifesto.items()):
        if rel in pedidos:
            continue
        atual = _atual(home / rel)
        if atual is None:
            continue  # já sumiu: só sai do manifesto
        acoes.append(Acao(REMOVER, rel, backup=info.get("backup", "")) if atual == info["sha256"]
                     else Acao(MANTER, rel, motivo="saiu do fluxo, mas foi editado: fica, e deixa de ser do fluxo"))
    return acoes


def _gravar(destino, dados, modo):
    """Temporário único na mesma pasta + os.replace: atômico, e o replace troca um link pelo arquivo
    em vez de seguir o link."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(destino.parent), prefix=f".{destino.name}.", suffix=".fluxo-tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(dados)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, modo)
        os.replace(tmp, destino)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _backup(destino, agora):
    """Move o arquivo para um .fluxo-bak-<data>[-n] que ainda não exista. Devolve o nome."""
    carimbo = time.strftime("%Y%m%d-%H%M%S", time.localtime(agora))
    n = 0
    while True:
        nome = f"{destino.name}.fluxo-bak-{carimbo}" + (f"-{n}" if n else "")
        bak = destino.with_name(nome)
        try:  # link em vez de rename: o link falha se o nome existe, o rename sobrescreveria o backup anterior
            if destino.is_symlink():
                os.symlink(os.readlink(destino), bak)
            else:
                os.link(destino, bak)
        except FileExistsError:
            n += 1
            continue
        os.unlink(destino)
        return nome


def _remover(home, rel, backup=""):
    caminho = Path(home) / rel
    try:
        caminho.unlink()
    except FileNotFoundError:
        pass
    if backup:  # o --forcar tinha tirado um arquivo do usuário daqui: ele volta
        guardado = caminho.with_name(backup)
        if guardado.exists() or guardado.is_symlink():
            os.replace(guardado, caminho)
            return
    pasta = caminho.parent
    if pasta.parent.name == "skills":  # a pasta da skill vazia sai junto; as pastas-raiz ficam
        try:
            pasta.rmdir()
        except OSError:
            pass


def aplicar(acoes, home, manifesto_path, forcar=False, agora=None):
    """Grava. Devolve o novo manifesto. Com conflito e sem `forcar`, não grava nada.

    O manifesto é gravado por último: se algo falhar no meio, o antigo continua valendo, e o
    próximo install adota o que já ficou igual em vez de acusar conflito."""
    home = Path(home)
    conflitos = [a for a in acoes if a.tipo == CONFLITO]
    if conflitos and not forcar:
        raise ConflitoErro(conflitos)
    novo = {}
    for a in acoes:
        destino = home / a.rel
        backup = a.backup
        if a.tipo == CONFLITO:
            backup = _backup(destino, agora) if (destino.exists() or destino.is_symlink()) else backup
        if a.tipo in (CRIAR, ATUALIZAR, CONFLITO):
            _gravar(destino, a.arquivo.conteudo, a.arquivo.modo)
        elif a.tipo in (ADOTAR, IGUAL) and (destino.stat().st_mode & 0o777) != a.arquivo.modo:
            os.chmod(destino, a.arquivo.modo)  # ex.: o wrapper que perdeu o +x
        elif a.tipo == REMOVER:
            _remover(home, a.rel, a.backup)
        if a.arquivo is not None:
            novo[a.rel] = {"sha256": sha(a.arquivo.conteudo), **({"backup": backup} if backup else {})}
    escrever_manifesto(manifesto_path, novo)
    return novo


def escrever_manifesto(caminho, arquivos):
    caminho = Path(caminho)
    if not arquivos:
        try:
            caminho.unlink()
        except FileNotFoundError:
            pass
        return
    texto = json.dumps({"versao": 1, "arquivos": arquivos}, indent=2, sort_keys=True) + "\n"
    _gravar(caminho, texto.encode(), 0o644)


def planejar_remocao(manifesto, home):
    acoes = []
    for rel, info in sorted(manifesto.items()):
        atual = _atual(Path(home) / rel)
        if atual is None:
            continue
        if atual == info["sha256"]:
            acoes.append(Acao(REMOVER, rel, backup=info.get("backup", ""),
                              motivo=f"volta o seu {info['backup']}" if info.get("backup") else ""))
        else:
            acoes.append(Acao(MANTER, rel, motivo="editado depois do install: fica" +
                              (f" (o seu original está em {info['backup']})" if info.get("backup") else "")))
    return acoes


def desinstalar(acoes, home, manifesto_path):
    for a in acoes:
        if a.tipo == REMOVER:
            _remover(home, a.rel, a.backup)
    escrever_manifesto(manifesto_path, {})


def resumo(acoes, mostrar_iguais=False):
    linhas = []
    for a in acoes:
        if a.tipo == IGUAL and not mostrar_iguais:
            continue
        linhas.append(f"  {a.tipo:<10} ~/{a.rel}" + (f"  ({a.motivo})" if a.motivo else ""))
    contagem = {}
    for a in acoes:
        contagem[a.tipo] = contagem.get(a.tipo, 0) + 1
    linhas.append("  " + ", ".join(f"{n} {t}" for t, n in sorted(contagem.items())) if contagem else "  nada a fazer")
    return "\n".join(linhas)
