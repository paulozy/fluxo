"""Módulo validate: traz o pr-reviewer para a máquina e roda a revisão dele pelo fluxo.

O `fluxo install` não usa rede: quem clona é o `fluxo validate enable` (como o `watch enable` e o `panel enable`),
numa tag fixada por esta versão do fluxo, conferindo o sha do commit, porque uma tag pode ser movida. Um clone com
mudança local nunca é descartado: o enable e o disable avisam e seguem. O clone é feito numa pasta irmã e troca de
lugar no fim, porque o timeout mata o git com SIGKILL e deixaria a pasta pela metade.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config as cfgmod
from . import fonte as fontemod
from . import lock
from .task import ID_RE

# a versão do pr-reviewer que esta versão do fluxo usa
PR_REVIEWER = {
    "url": "git@github.com:paulozy/pr-reviewer-core.git",
    "tag": "v0.5.0",
    "sha": "d10f37c88c4a073a492755017902c9730eff677e",
}
ESTADO = "pr-reviewer.json"
TIMEOUT = 300
DICAS = "confira o acesso ao repo: `ssh -T git@github.com`, ou `gh auth setup-git` se você usa HTTPS"


class ErroClone(Exception):
    pass


def pasta(cfg, cam):
    """O clone: o da chave validate.dir (um clone seu, que o fluxo nunca mexe) ou o do fluxo, nos dados."""
    proprio = (cfg.get("validate") or {}).get("dir")
    return Path(os.path.expanduser(proprio)) if proprio else cam.dados / "pr-reviewer"


def config_pr_reviewer(cam, env):
    """Onde o pr-reviewer lê a config dele (src/config.ts): PR_REVIEWER_CONFIG_DIR, ou XDG_CONFIG_HOME/pr-reviewer,
    ou ~/.config/pr-reviewer. Com --home (os testes), tudo fica dentro do home de teste."""
    if env.get("PR_REVIEWER_CONFIG_DIR"):
        return Path(env["PR_REVIEWER_CONFIG_DIR"]) / "config.json"
    return cam.config_dir.parent / "pr-reviewer" / "config.json"


def wrapper(clone):
    return ("#!/bin/sh\n# criado pelo fluxo validate enable; o fluxo validate disable remove\n"
            f'exec node {shlex.quote(str(clone / "bin" / "pr-review.ts"))} "$@"\n')


def _ambiente(env, run):
    """Sem pergunta de senha, sem as variáveis de um repo de fora, e o ssh em modo não interativo quando você não
    configurou um ssh próprio para o git."""
    novo = {k: v for k, v in env.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    novo.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    if "GIT_SSH_COMMAND" not in env:
        proprio = run(["git", "config", "--get", "core.sshCommand"], capture_output=True, text=True, env=novo,
                      stdin=subprocess.DEVNULL, timeout=30)
        if proprio.returncode != 0 or not proprio.stdout.strip():
            novo["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=15"
    return novo


def _git(run, env, args, cwd=None, timeout=TIMEOUT):
    # o teto impede que o git suba para um repo de fora (um HOME versionado) se a pasta do clone perder o .git
    extra = {"GIT_CEILING_DIRECTORIES": str(Path(cwd).parent)} if cwd else {}
    return run(["git", *args], cwd=cwd, env=dict(env, **extra), stdin=subprocess.DEVNULL, capture_output=True,
               text=True, timeout=timeout)


def _fim(texto, linhas=6):
    return "\n".join(texto.strip().splitlines()[-linhas:])


def _ler(caminho):
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _gravar(caminho, dados):
    cfgmod.criar_estado(caminho.parent)
    tmp = caminho.with_name(f".{caminho.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, caminho)


def limpo(run, env, clone, sha):
    """(limpo, motivo): no sha registrado, sem mudança (nem arquivo novo ou ignorado, que a troca apagaria), sem
    branch local e sem stash, que o status não mostra."""
    topo = _git(run, env, ["rev-parse", "--show-toplevel"], cwd=clone)
    if topo.returncode != 0 or Path(topo.stdout.strip()).resolve() != clone.resolve():
        return False, "não é um clone do git"
    head = _git(run, env, ["rev-parse", "HEAD"], cwd=clone).stdout.strip()
    if head != sha:
        return False, f"está em {head[:12]}, e não no {sha[:12]} que o fluxo clonou"
    status = _git(run, env, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored"], cwd=clone)
    if status.returncode != 0 or status.stdout.strip("\0"):
        return False, "tem arquivos mudados, novos ou ignorados"
    refs = _git(run, env, ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/stash"], cwd=clone)
    if refs.returncode != 0 or refs.stdout.strip():
        return False, "tem branch local ou stash"
    return True, ""


def clonar(run, env, fonte, clone):
    """Clona a tag numa pasta irmã, confere o sha e troca de lugar com o clone de antes, se houver."""
    clone.parent.mkdir(parents=True, exist_ok=True)
    tmp = clone.with_name(f".{clone.name}.novo-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        r = run(["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1", "--branch", fonte["tag"],
                 fonte["url"], str(tmp)], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ErroClone(f"o git clone de {fonte['url']} passou de {TIMEOUT} s; {DICAS}") from None
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ErroClone(f"o git clone de {fonte['url']} ({fonte['tag']}) falhou:\n{_fim(r.stderr)}\n{DICAS}")
    head = _git(run, env, ["rev-parse", "HEAD"], cwd=tmp).stdout.strip()
    if head != fonte["sha"]:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ErroClone(f"a tag {fonte['tag']} aponta para {head[:12]}, e esta versão do fluxo espera {fonte['sha'][:12]}: "
                        "a tag foi movida no repo; nada foi trocado")
    velho = clone.with_name(f".{clone.name}.velho-{os.getpid()}")
    if clone.exists():
        os.replace(clone, velho)
    os.replace(tmp, clone)
    shutil.rmtree(velho, ignore_errors=True)


def _criar_exclusivo(caminho, conteudo, modo):
    """Só cria: um arquivo (ou link) que já existe fica como está. O modo da criação passa pelo umask."""
    try:
        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), modo)
    except FileExistsError:
        return False
    try:
        os.fchmod(fd, modo)
        os.write(fd, conteudo.encode())
    finally:
        os.close(fd)
    return True


def criar_config(cfg, cam, env, profile):
    destino = config_pr_reviewer(cam, env)
    destino.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conteudo = {
        "workspaceRoot": os.path.expanduser(cfg["code_dir"]),
        "primaryBase": cfg["git"]["base"],
        "defaultBase": f"origin/{cfg['git']['base']}",
        "profile": profile,
    }
    if _criar_exclusivo(destino, json.dumps(conteudo, indent=2, ensure_ascii=False) + "\n", 0o600):
        return f"config do pr-reviewer criada em {destino} (perfil {profile})"
    return f"config do pr-reviewer mantida em {destino} (já existia)"


def enable(cfg, cam, run=subprocess.run, env=None, fonte=None, profile="local"):
    """(código, mensagens)."""
    env = dict(os.environ if env is None else env)
    fonte = fonte or PR_REVIEWER
    clone = pasta(cfg, cam)
    estado_arq = cam.estado / ESTADO
    estado = _ler(estado_arq)
    msgs = []
    proprio = bool((cfg.get("validate") or {}).get("dir"))
    try:
        genv = _ambiente(env, run)
    except FileNotFoundError:
        return 1, ["sem git nesta máquina: o pr-reviewer vem por git clone"]
    except subprocess.TimeoutExpired:
        return 1, ["o git não respondeu"]
    if proprio:
        if not (clone / "bin" / "pr-review.ts").exists():
            return 1, [f"validate.dir aponta para {clone}, que não tem bin/pr-review.ts"]
        msgs.append(f"usando o seu clone em {clone} (o fluxo nunca mexe nele)")
        estado = {"dir": str(clone), "proprio": True}
    elif not clone.exists():
        try:
            clonar(run, genv, fonte, clone)
        except ErroClone as exc:
            return 1, [str(exc)]
        msgs.append(f"pr-reviewer {fonte['tag']} clonado em {clone}")
        estado = {"dir": str(clone), "tag": fonte["tag"], "sha": fonte["sha"], "wrapper": estado.get("wrapper", False)}
    elif estado.get("dir") != str(clone) or not estado.get("sha"):
        return 1, [f"{clone} existe e não foi o fluxo que clonou; não mexo nele (apague-o, ou use validate.dir)"]
    elif estado["sha"] == fonte["sha"]:
        msgs.append(f"pr-reviewer já está na {fonte['tag']} em {clone}")
    else:
        ok, motivo = limpo(run, genv, clone, estado["sha"])
        if not ok:
            msgs.append(f"não atualizei {clone} para a {fonte['tag']}: o clone {motivo}; nada foi descartado")
        else:
            try:
                clonar(run, genv, fonte, clone)
            except ErroClone as exc:
                return 1, [str(exc)]
            msgs.append(f"pr-reviewer atualizado para a {fonte['tag']} em {clone}")
            estado = dict(estado, tag=fonte["tag"], sha=fonte["sha"])
    msgs.append(criar_config(cfg, cam, env, profile))
    bin_ = cam.home / ".local" / "bin" / "pr-review"
    bin_.parent.mkdir(parents=True, exist_ok=True)
    if _criar_exclusivo(bin_, wrapper(clone), 0o755):
        estado["wrapper"] = True
        msgs.append(f"comando pr-review criado em {bin_}")
    elif estado.get("wrapper") and bin_.is_file() and not bin_.is_symlink() and bin_.read_text(encoding="utf-8") == wrapper(clone):
        msgs.append(f"comando pr-review mantido em {bin_}")
    else:
        estado["wrapper"] = False
        msgs.append(f"{bin_} já existia e não foi o fluxo que criou: as skills usam `fluxo validate`, que não depende dele")
    _gravar(estado_arq, estado)
    return 0, msgs


def disable(cfg, cam, run=subprocess.run, env=None):
    env = dict(os.environ if env is None else env)
    estado_arq = cam.estado / ESTADO
    estado = _ler(estado_arq)
    if not estado:
        return 0, ["o validate não estava ligado nesta máquina"]
    clone = Path(estado["dir"])
    msgs = []
    bin_ = cam.home / ".local" / "bin" / "pr-review"
    if estado.get("wrapper") and bin_.is_file() and not bin_.is_symlink() and bin_.read_text(encoding="utf-8") == wrapper(clone):
        bin_.unlink()
        msgs.append(f"comando pr-review removido de {bin_}")
    elif estado.get("wrapper"):
        msgs.append(f"{bin_} mudou desde o enable; ficou como está")
    tira_estado = True
    if estado.get("proprio"):
        msgs.append(f"o seu clone em {clone} fica (o fluxo nunca mexe nele)")
    elif clone.exists():
        try:
            ok, motivo = limpo(run, _ambiente(env, run), clone, estado.get("sha", ""))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            ok, motivo = False, "não deu para conferir (sem git)"
        if ok:
            shutil.rmtree(clone)
            msgs.append(f"clone do pr-reviewer removido de {clone}")
        else:
            tira_estado = False
            msgs.append(f"o clone em {clone} {motivo}; ficou como está")
    if tira_estado:
        estado_arq.unlink()
    msgs.append(f"a config do pr-reviewer fica em {config_pr_reviewer(cam, env)}")
    return 0, msgs


def comando(cfg, cam, args):
    """O argv que roda o pr-reviewer do clone (o `fluxo validate exec` e o `start` usam)."""
    alvo = pasta(cfg, cam) / "bin" / "pr-review.ts"
    if not alvo.exists():
        raise cfgmod.ErroConfig(f"{alvo} não existe: rode `fluxo validate enable`")
    return ["node", str(alvo), *args]


def avisos_do_install(cfg, cam):
    if "validate" in cfg["modules"] and not (cam.estado / ESTADO).exists():
        return ["validate: rode `fluxo validate enable` para trazer o pr-reviewer (o install não usa rede)"]
    return []


def avisos_do_uninstall(cam):
    if (cam.estado / ESTADO).exists():
        return ["validate: o clone do pr-reviewer, o comando pr-review e a config dele ficam; "
                "rode `fluxo validate disable` antes, se quiser tirá-los"]
    return []


# ── a revisão e a receita em segundo plano ───────────────────────────────────
#
# Uma revisão leva de 4 a 20 min, e o comando da ferramenta tem limite de tempo (no Claude, 10 min); o timeout
# mata o que ela rodou. Por isso o `start` solta um processo desligado da sessão, que pega a trava do stack com o
# próprio pid (ela vale enquanto ele vive) e grava o fim; o `status` acompanha. A trava ocupada nunca se confunde
# com a saída 4 do pr-review, que é REPROVAR.

SEM_ADAPTADOR = {"copilot"}  # o pr-reviewer ainda não roda os agentes nela: vale o backend da config dele
ESPERA = 3600
CHAVE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")


def _pasta_execucao(cam, chave):
    return cam.estado / "validar" / chave


def _vivo(pid):
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def _rodando(pasta):
    fase = _ler(pasta / "fase.json")
    return bool(fase.get("pid")) and not (pasta / "fim.json").exists() and _vivo(fase["pid"])


def _backend(backend):
    return [] if not backend or backend in SEM_ADAPTADOR else ["--backend", backend]


def preparar_revisao(cfg, cam, id_, backend, run=subprocess.run):
    """O argv do pr-review para a tarefa: um --local por repo do plano, na branch da worktree, e a base do plano."""
    if not ID_RE.fullmatch(id_):
        raise cfgmod.ErroConfig(f"id {id_!r} inválido")
    plano = cam.dados / "planos" / f"{id_}.md"
    try:
        meta, _ = fontemod.ler(plano.read_text(encoding="utf-8"), str(plano))
    except FileNotFoundError:
        raise cfgmod.ErroConfig(f"{plano} não existe: o /planejar grava o plano antes") from None
    repos = meta.get("repos")
    if isinstance(repos, str):
        repos = [repos]
    if not isinstance(repos, list) or not repos or not all(isinstance(r, str) and re.fullmatch(r"[\w.-]+", r) for r in repos):
        raise cfgmod.ErroConfig(f"{plano}: o frontmatter precisa de repos: [repo, ...]")
    base = meta.get("base") or cfg["git"]["base"]
    if not isinstance(base, str) or not cfgmod.BRANCH.fullmatch(base):
        raise cfgmod.ErroConfig(f"{plano}: base {base!r} inválida")
    locais, avisos = [], []
    for repo in repos:
        wt = Path(os.path.expanduser(cfg["code_dir"])) / f"{repo}-{id_}"
        if not wt.is_dir():
            raise cfgmod.ErroConfig(f"a worktree {wt} não existe")
        branch = run(["git", "-C", str(wt), "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
        if not branch:
            raise cfgmod.ErroConfig(f"{wt} não está numa branch")
        sujo = run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        if sujo:
            raise cfgmod.ErroConfig(f"{wt} tem mudança não commitada; o pr-review só vê o que está commitado: commite antes")
        if run(["git", "-C", str(wt), "fetch", "-q", "origin", base], capture_output=True, text=True).returncode != 0:
            avisos.append(f"{repo}: o git fetch origin {base} falhou; a revisão usa a origin/{base} que já está aqui")
        locais += ["--local", f"{repo}:{branch}"]
    argv = comando(cfg, cam, [*locais, "--base", f"origin/{base}", "--plan", str(plano), *_backend(backend)])
    return argv, avisos


def preparar_receita(cfg, cam, repo, modo, backend):
    if not re.fullmatch(r"[\w-]+", repo):
        raise cfgmod.ErroConfig(f"nome de repo inválido: {repo!r}")
    extra = {"proposta": [], "fix": ["--fix"], "check": ["--check"]}[modo]
    # o --check não roda agente, e o pr-review recusa --backend com ele
    return comando(cfg, cam, ["recipe", repo, *extra, "--json", *([] if modo == "check" else _backend(backend))])


def start(cfg, cam, raiz, chave, argv, trava, id_=None, popen=subprocess.Popen, espera=ESPERA):
    """Solta o processo que roda o pr-review desligado da sessão. Devolve a pasta da execução."""
    if not CHAVE_RE.fullmatch(chave):
        raise cfgmod.ErroConfig(f"chave {chave!r} inválida")
    pasta = _pasta_execucao(cam, chave)
    if _rodando(pasta):
        raise cfgmod.ErroConfig(f"já há uma execução de {chave} rodando: fluxo validate status {chave}")
    cfgmod.criar_estado(cam.estado)
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir(parents=True, mode=0o700)
    _gravar(pasta / "pedido.json", {"argv": argv, "trava": trava, "id": id_ or chave, "espera": espera,
                                    "criado": time.strftime("%Y-%m-%d %H:%M:%S")})
    home = ["--home", str(cam.home)] if cam.home != Path.home() else []
    with open(pasta / "saida.log", "wb") as out, open(pasta / "erros.log", "wb") as err:
        proc = popen([sys.executable, str(Path(raiz) / "fluxo"), "validate", "_rodar", chave, *home],
                     stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
    # já com o pid: até o processo gravar a fase dele, o status o daria por morto
    _gravar(pasta / "fase.json", {"fase": "começando", "pid": proc.pid})
    return pasta


def rodar(cam, chave, run=subprocess.run):
    """O processo de fundo: pega a trava (se pedido) com o próprio pid, roda o pr-review e grava o fim."""
    pasta = _pasta_execucao(cam, chave)
    pedido = _ler(pasta / "pedido.json")
    sessao = f"validar-{chave}"
    t = lock.Trava(cam.estado) if pedido.get("trava") else None
    if t:
        _gravar(pasta / "fase.json", {"fase": "esperando a trava do stack", "pid": os.getpid()})
        ok, dono, _ = lock.pegar_esperando(t, int(pedido.get("espera", ESPERA)), sessao=sessao, pid=os.getpid(), id_=pedido.get("id"))
        if not ok:
            _gravar(pasta / "fim.json", {"ocupada": lock.descrever(dono, True)})
            return lock.OCUPADA
    try:
        _gravar(pasta / "fase.json", {"fase": "rodando", "pid": os.getpid()})
        codigo = run(pedido["argv"], stdin=subprocess.DEVNULL).returncode
        _gravar(pasta / "fim.json", {"codigo": codigo, "terminado": time.strftime("%Y-%m-%d %H:%M:%S")})
        return codigo
    finally:
        if t:
            t.soltar(sessao)


def _relatorio(pasta):
    """O report.json da revisão: a linha `relatório: <pasta>/report.md` que o pr-review imprime no fim."""
    for nome in ("saida.log", "erros.log"):
        texto = (pasta / nome).read_text(encoding="utf-8", errors="replace") if (pasta / nome).exists() else ""
        m = re.search(r"^relatório: (.+)/report\.md$", texto, re.M)
        if m:
            return Path(m.group(1)) / "report.json"
    return None


def status(cam, chave, esperar=0, dormir=time.sleep, relogio=time.monotonic):
    """(código, linhas): 0 terminou, 75 ainda rodando, 1 não há execução ou ela falhou sem resultado."""
    pasta = _pasta_execucao(cam, chave)
    pedido = _ler(pasta / "pedido.json")
    if not pedido:
        return 1, [f"não há execução de {chave}: fluxo validate start {chave}"]
    fim_em = relogio() + max(0, min(esperar, 540))
    while not (pasta / "fim.json").exists() and _rodando(pasta) and relogio() < fim_em:
        dormir(min(5, max(0.5, fim_em - relogio())))
    fim = _ler(pasta / "fim.json")
    logs = f"logs: {pasta / 'saida.log'} e {pasta / 'erros.log'}"
    if not fim:
        if _rodando(pasta):
            return 75, [f"{chave}: {_ler(pasta / 'fase.json').get('fase', 'rodando')} (desde {pedido.get('criado')})", logs]
        return 1, [f"{chave}: o processo terminou sem registrar o fim", logs]
    if "ocupada" in fim:
        return 1, [f"{chave}: a trava do stack continuou ocupada ({fim['ocupada']}); rode de novo quando ela soltar", logs]
    if "recipe" in pedido["argv"]:
        return 0, _resumo_receita(pasta, fim, logs)
    report = _relatorio(pasta)
    dados = _ler(report) if report else {}
    if not dados:
        return 1, [f"{chave}: o pr-review saiu com {fim['codigo']} sem relatório", logs]
    veredito = dados.get("verdict") or {}
    linhas = [f"veredito: {veredito.get('decision', '?')}{' (só estática)' if veredito.get('staticOnly') else ''}"]
    linhas += [f"- {r}" for r in veredito.get("reasons") or []]
    linhas.append(f"relatório: {report.with_name('report.md')}")
    return 0, linhas


def _resumo_receita(pasta, fim, logs):
    try:
        r = json.loads((pasta / "saida.log").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"o recipe saiu com {fim['codigo']} sem o JSON", logs]
    linhas = [f"receita de {r.get('repo')}: {r.get('status')}"]
    if r.get("status") in ("healthy", "unhealthy"):
        linhas += [f"- {'ok' if st.get('ok') else 'falhou'} {st.get('phase')} {st.get('name')}: {(st.get('detail') or '').splitlines()[0] if st.get('detail') else ''}"
                   for st in r.get("steps") or []]
        return linhas + [logs]
    linhas += r.get("summary") or []
    linhas += [f"atenção: {w}" for w in r.get("warnings") or []]
    linhas += r.get("diff") or []
    linhas += r.get("failures") or []
    if r.get("status") == "pending":
        linhas.append(f"para gravar, depois do OK: fluxo validate exec -- recipe {r.get('repo')} --apply={r.get('proposal')} --confirm={r.get('confirm')}")
    return linhas + [logs]
