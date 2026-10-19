"""As chaves do `fluxo config`: onde cada uma mora, o tipo, a ajuda e se pede `fluxo install` depois. O menu, o
list, o get/set/unset e o completar saem desta tabela, para não sair do passo uma da outra.

A maioria fica na config do fluxo; as `pr-reviewer.*` ficam na config do pr-reviewer (src/config.ts na v0.6.0:
o `backend` é um de claude, kiro, codex ou copilot, e o `profile` é um nome simples). Ficam de fora: `modules`
(é o `fluxo modules`), `task.launch` (argv é ruim de digitar: `fluxo config edit`) e `tracker`/`chat`
(reservadas; nenhuma fonte usa)."""
import difflib
import json
import os
import re

from . import config as cfgmod
from . import validate

FLUXO, PR_REVIEWER = "fluxo", "pr-reviewer"
TEXTO, INTEIRO, ESCOLHA, LISTA = "texto", "inteiro", "escolha", "lista"
PERFIL_RE = re.compile(r"[A-Za-z0-9_-]+")  # o `^[\w-]+$` do pr-reviewer, que é JavaScript: \w só em ASCII


class Chave:
    def __init__(self, nome, tipo, ajuda, escolhas=(), onde=FLUXO, install=False, obrigatoria=False, minimo=None):
        self.nome, self.tipo, self.ajuda, self.escolhas = nome, tipo, ajuda, tuple(escolhas)
        self.onde, self.install, self.obrigatoria, self.minimo = onde, install, obrigatoria, minimo
        self.caminho = nome.split(".")[1:] if onde == PR_REVIEWER else nome.split(".")


CHAVES = [
    Chave("tools", LISTA, "as ferramentas; a primeira é a padrão do fluxo task", cfgmod.TOOLS, install=True,
          obrigatoria=True),
    Chave("forge", ESCOLHA, "onde ficam os repos: gh ou glab, PR ou MR", cfgmod.EIXOS["forge"], install=True,
          obrigatoria=True),
    Chave("os", ESCOLHA, "a área de transferência, o aviso e o agendamento do watch (padrão: detectado)",
          cfgmod.EIXOS["os"], install=True),
    Chave("git.base", TEXTO, "a branch de desenvolvimento, de onde saem as branches", install=True),
    Chave("code_dir", TEXTO, "onde ficam os clones", install=True),
    Chave("gitlab.host", TEXTO, "o host do GitLab, com o forge gitlab", install=True),
    Chave("kiro.engine", ESCOLHA, "Kiro: v2 (o padrão do 2.x) ou v3", cfgmod.ENGINES, install=True),
    Chave("task.max", INTEIRO, "quantas tarefas o fluxo task deixa abertas ao mesmo tempo", minimo=1),
    Chave("watch.interval", INTEIRO, "segundos entre as passadas do watch", minimo=60, install=True),
    Chave("promote.targets", LISTA, "as bases depois da git.base, em ordem, para o /promover", install=True),
    Chave("validate.dir", TEXTO, "um clone seu do pr-reviewer, que o fluxo usa sem mexer (sem ela: o fluxo clona)"),
    *[Chave(f"models.{t}.{n}", TEXTO, f"o modelo {n} dos agentes no {t} (sem ela: o da sessão)", install=True)
      for t in cfgmod.TOOLS for n in cfgmod.NIVEIS],
    Chave("pr-reviewer.backend", ESCOLHA, "a ferramenta que roda os agentes do pr-reviewer (sem ela: claude)",
          cfgmod.TOOLS, onde=PR_REVIEWER),
    Chave("pr-reviewer.profile", TEXTO, "o perfil do pr-reviewer: os runbooks e os envs", onde=PR_REVIEWER),
]
POR_NOME = {c.nome: c for c in CHAVES}
FORA = {"modules": "os módulos são do `fluxo modules --enable/--disable`",
        "task.launch": "o argv de cada ferramenta: `fluxo config edit`"}


def chave(nome):
    """A Chave, ou ErroConfig com a mais parecida (o difflib) ou o caminho para o que fica de fora."""
    if nome in POR_NOME:
        return POR_NOME[nome]
    raiz = next((f for f in FORA if nome == f or nome.startswith(f + ".")), None)
    if raiz:
        raise cfgmod.ErroConfig(f"{nome}: {FORA[raiz]}")
    parecida = difflib.get_close_matches(nome, POR_NOME, n=1)
    raise cfgmod.ErroConfig(f"chave {nome!r} desconhecida" + (f"; quis dizer {parecida[0]}?" if parecida else
                                                              ": fluxo config list mostra todas"))


def visiveis(cfg, bruto):
    """As chaves do list e do menu: os models só das ferramentas de `tools` (ou os que já têm valor)."""
    return [c for c in CHAVES if not c.nome.startswith("models.") or c.caminho[1] in cfg["tools"]
            or _pegar(bruto, c.caminho) is not None]


class Arquivos:
    """As duas configs: o bruto (o que está no arquivo), os bytes lidos (para o gravar_json recusar o que mudou no
    meio) e a config do fluxo com os padrões.

    Uma config que não fecha não impede o `fluxo config`, que é quem a conserta: o erro fica em `erro`, o list e o
    get mostram o que dá, e o set, o unset e o edit validam só o resultado. A do pr-reviewer só é lida quando a chave
    é dela: um JSON dela estragado não trava as chaves do fluxo."""

    def __init__(self, cam, existentes, env=None):
        self.cam, self.existentes = cam, existentes
        self.caminho_pr = validate.config_pr_reviewer(cam, os.environ if env is None else env)
        self.recarregar()

    def recarregar(self):
        """Lê de novo as duas: depois de uma recusa por "mudou no meio", os bytes velhos recusariam tudo."""
        self.erro, self._pr = None, None
        try:
            self.bruto, self.lido = cfgmod.ler_json(self.cam.config)
        except FileNotFoundError:
            raise cfgmod.ErroConfig(f"sem config em {self.cam.config}: rode `fluxo init`") from None
        except cfgmod.ErroConfig as exc:  # JSON inválido: só o edit conserta, a partir dos bytes
            self.bruto, self.lido, self.erro = {}, self.cam.config.read_bytes(), exc
        try:
            self.cfg = cfgmod.validar(self.bruto, self.existentes)
        except cfgmod.ErroConfig as exc:
            self.erro = self.erro or exc
            self.cfg = dict(_copia(cfgmod.PADRAO), **self.bruto)  # o que der, para mostrar
            if not isinstance(self.cfg.get("tools"), list):
                self.cfg["tools"] = []

    def pr(self):
        """(dados, bytes lidos) da config do pr-reviewer, ou (None, None) se ela não existe. Lida uma vez."""
        if self._pr is None:
            try:
                self._pr = cfgmod.ler_json(self.caminho_pr)
            except FileNotFoundError:
                self._pr = (None, None)
        return self._pr

    def valor(self, c):
        """(valor efetivo, origem): "config" ou "pr-reviewer" quando está no arquivo, "padrão" quando vem do
        padrão, e (None, None) quando não tem nenhum. A config do pr-reviewer ilegível vira (None, "ilegível")."""
        if c.onde == PR_REVIEWER:
            try:
                dados, _ = self.pr()
            except cfgmod.ErroConfig:
                return None, "ilegível"
            v = _pegar(dados or {}, c.caminho)
            if v is not None:
                return v, PR_REVIEWER
            return ("claude", "padrão") if c.nome == "pr-reviewer.backend" and dados is not None else (None, None)
        v = _pegar(self.bruto, c.caminho)
        if v is not None:
            return v, "config"
        padrao = _pegar(self.cfg, c.caminho)
        return (padrao, "padrão") if padrao is not None else (None, None)

    def gravar(self, c, valor):
        """Muda (ou, com None, tira) a chave e grava, validando antes o resultado. Devolve True se mudou algo."""
        if valor is None and c.obrigatoria:
            raise cfgmod.ErroConfig(f"{c.nome} é obrigatória: troque com fluxo config set {c.nome} …")
        if c.onde == PR_REVIEWER:
            atual, lido = self.pr()  # ilegível: levanta, e nada é gravado por cima do que não se leu
            if atual is None:
                raise cfgmod.ErroConfig(f"sem a config do pr-reviewer em {self.caminho_pr}: rode `fluxo validate enable`")
        else:
            if isinstance(self.erro, cfgmod.ErroConfig) and not self.bruto:
                raise cfgmod.ErroConfig(f"{self.erro}: conserte com fluxo config edit")
            atual, lido = self.bruto, self.lido
        dados = _copia(atual)
        if valor is None:
            _tirar(dados, c.caminho)
        else:
            _por(dados, c.caminho, valor)
        if dados == atual:
            return False
        if c.onde == FLUXO:
            cfgmod.validar(dados, self.existentes)  # a config inteira: o promote.targets não pode ter a base
            cfgmod.gravar_json(self.cam.config, dados, antes=lido)
        else:
            cfgmod.gravar_json(self.caminho_pr, dados, antes=lido)
        self.recarregar()
        return True


def converter(c, textos):
    """O valor da chave a partir do que foi digitado (a lista vem em vários argumentos ou como `a,b`). Levanta
    ErroConfig com o que está errado."""
    textos = [t.strip() for t in textos]
    if c.tipo == LISTA:
        itens = [i.strip() for t in textos for i in t.split(",") if i.strip()]
        if not itens:
            raise cfgmod.ErroConfig(f"{c.nome}: pelo menos um valor")
        fora = [i for i in itens if c.escolhas and i not in c.escolhas]
        if fora:
            raise cfgmod.ErroConfig(f"{c.nome}: {', '.join(fora)} não vale; use {', '.join(c.escolhas)}")
        if len(set(itens)) != len(itens):
            raise cfgmod.ErroConfig(f"{c.nome}: sem repetir")
        return itens
    if len(textos) != 1 or not textos[0]:
        raise cfgmod.ErroConfig(f"{c.nome}: um valor só")
    texto = textos[0]
    if c.tipo == INTEIRO:
        if not re.fullmatch(r"[0-9]+", texto):
            raise cfgmod.ErroConfig(f"{c.nome}: um número inteiro")
        if c.minimo is not None and int(texto) < c.minimo:
            raise cfgmod.ErroConfig(f"{c.nome}: no mínimo {c.minimo}")
        return int(texto)
    if c.tipo == ESCOLHA and texto not in c.escolhas:
        raise cfgmod.ErroConfig(f"{c.nome}: {texto!r} não vale; use {', '.join(c.escolhas)}")
    if c.nome == "pr-reviewer.profile" and not PERFIL_RE.fullmatch(texto):
        raise cfgmod.ErroConfig(f"{c.nome}: um nome simples (letras, números, hífen e sublinhado)")
    return texto


def mostrar(valor):
    """O valor numa linha, como o `set` aceita de volta: a lista com vírgula."""
    if valor is None:
        return ""
    return ",".join(str(v) for v in valor) if isinstance(valor, list) else str(valor)


def _pegar(dados, caminho):
    for parte in caminho:
        if not isinstance(dados, dict) or parte not in dados:
            return None
        dados = dados[parte]
    return dados


def _por(dados, caminho, valor):
    for parte in caminho[:-1]:
        if not isinstance(dados.get(parte), dict):
            dados[parte] = {}
        dados = dados[parte]
    dados[caminho[-1]] = valor


def _tirar(dados, caminho):
    """Tira a chave e os objetos que ficaram vazios por causa dela (um `models: {copilot: {}}` não fica)."""
    pais = [dados]
    for parte in caminho[:-1]:
        if not isinstance(pais[-1].get(parte), dict):
            return
        pais.append(pais[-1][parte])
    pais[-1].pop(caminho[-1], None)
    for pai, parte in zip(reversed(pais[:-1]), reversed(caminho[:-1])):
        if pai[parte]:
            break
        del pai[parte]


def _copia(dados):
    return json.loads(json.dumps(dados))
