"""Config local da máquina (~/.config/fluxo/config.json) e os caminhos que o fluxo usa.

A config é JSON, e não TOML, porque o Python 3.9 do Mac não tem `tomllib`.
"""
import json
import os
import platform
import re
from pathlib import Path

TOOLS = ("claude", "copilot", "kiro", "codex")  # a primeira de `tools` na config é a padrão do `fluxo task`
EIXOS = {
    "forge": ("github", "gitlab"),
    "tracker": ("none", "jira"),
    "chat": ("draft", "slack"),
    "os": ("linux", "mac", "wsl"),
}
ENGINES = ("v2", "v3")  # Kiro CLI: o V2 é o padrão do 2.x; o V3 entra com --v3
NIVEIS = ("strong", "medium", "light")  # modelo abstrato dos agentes
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")  # nome de branch simples: sem espaço nem caractere de shell

PADRAO = {
    "version": 1,
    "tracker": "none",
    "chat": "draft",
    "modules": [],
    "git": {"base": "develop"},
    "code_dir": "~/code",
    "models": {},
    "task": {"max": 2, "launch": {}},
    "watch": {"interval": 300},
    "promote": {"targets": ["main"]},  # as bases depois da git.base, em ordem (ex.: staging, main)
}


class ErroConfig(Exception):
    pass


class Caminhos:
    """Onde ficam a config, os dados e o manifesto. Com `home`, tudo vai para dentro dele (os testes
    e o `--home`), sem olhar o XDG do ambiente."""

    def __init__(self, home=None, env=None):
        env = os.environ if env is None else env
        if home is not None and not str(home).strip():
            raise ErroConfig("--home vazio: passe uma pasta (o vazio cairia no HOME de verdade)")
        if home:
            self.home = Path(home).expanduser().resolve()
            self.config_dir = self.home / ".config" / "fluxo"
            self.dados = self.home / ".local" / "share" / "fluxo"
        else:
            self.home = Path.home()
            self.config_dir = Path(env.get("XDG_CONFIG_HOME") or self.home / ".config") / "fluxo"
            self.dados = Path(env.get("XDG_DATA_HOME") or self.home / ".local" / "share") / "fluxo"
        self.config = self.config_dir / "config.json"
        self.manifesto = self.dados / "manifesto.json"
        self.estado = self.dados / "estado"  # o que os módulos gravam rodando: tasks, trava, vigia, eventos

    def exibir(self, caminho, shell=False):
        """O caminho como as instruções mostram: com ~ (ou $HOME, para comando de shell) quando fica no
        HOME, para o texto gerado não depender de quem instalou e os testes compararem byte a byte. O
        bash não expande o ~ depois de `=@` nem entre aspas: em comando, vai o $HOME."""
        caminho = Path(caminho)
        try:
            return ("$HOME/" if shell else "~/") + caminho.relative_to(self.home).as_posix()
        except ValueError:
            return caminho.as_posix()


def criar_estado(estado):
    """O estado/ é 700: os eventos guardam o começo dos prompts e dos comandos, e o tasks.jsonl, o prompt
    inteiro. O chmod vale também para quem vem de uma versão que a criava com o umask."""
    Path(estado).mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(estado, 0o700)
    except OSError:
        pass


def detectar_so(sistema=None, versao_kernel=None):
    sistema = sistema or platform.system()
    if sistema == "Darwin":
        return "mac"
    if versao_kernel is None:
        try:
            versao_kernel = Path("/proc/version").read_text(errors="replace")
        except OSError:
            versao_kernel = ""
    return "wsl" if "microsoft" in versao_kernel.lower() else "linux"


def validar(cfg, modulos_existentes=()):
    """Devolve a config completa (com os padrões) ou levanta ErroConfig com todos os problemas."""
    if not isinstance(cfg, dict):
        raise ErroConfig("a config precisa ser um objeto JSON")
    out = json.loads(json.dumps(PADRAO))
    out.update(cfg)
    problemas = []
    if out.get("version") != 1:
        problemas.append(f"version {out.get('version')!r} desconhecida (esperado 1)")
    tools = out.get("tools")
    if (not isinstance(tools, list) or not tools or any(t not in TOOLS for t in tools)
            or len(set(tools)) != len(tools)):
        problemas.append(f"tools: lista, sem repetir, com {', '.join(TOOLS)}; a primeira é a padrão")
        tools = []
    for eixo, valores in EIXOS.items():
        if eixo == "os" and not out.get("os"):
            out["os"] = detectar_so()
        if out.get(eixo) not in valores:
            problemas.append(f"{eixo}: {out.get(eixo)!r} não é um de {', '.join(valores)}")
    if "kiro" in tools:
        engine = (out.get("kiro") or {}).get("engine", "v2")
        if engine not in ENGINES:
            problemas.append(f"kiro.engine: {engine!r} não é um de {', '.join(ENGINES)}")
        out["kiro"] = dict(out.get("kiro") or {}, engine=engine)
    if not isinstance(out.get("modules"), list):
        problemas.append("modules precisa ser uma lista")
    else:
        if len(set(out["modules"])) != len(out["modules"]):
            problemas.append("modules tem repetição")
        for m in out["modules"]:
            if m not in modulos_existentes:
                problemas.append(f"módulo {m!r} não existe" + (f" (há: {', '.join(modulos_existentes)})"
                                                               if modulos_existentes else " (ainda não há módulos)"))
    base = (out.get("git") or {}).get("base")
    if not isinstance(base, str) or not base.strip():
        problemas.append("git.base precisa ser o nome da branch de desenvolvimento (ex.: develop)")
    if not isinstance(out.get("code_dir"), str) or not out["code_dir"].strip():
        problemas.append("code_dir precisa ser a pasta dos clones (ex.: ~/code)")
    task = out.get("task")
    if not isinstance(task, dict):
        problemas.append("task precisa ser um objeto")
    else:
        out["task"] = dict(PADRAO["task"], **task)
        if not isinstance(out["task"]["max"], int) or out["task"]["max"] < 1:
            problemas.append("task.max: número de tarefas com ID ao mesmo tempo (1 ou mais)")
        launch = out["task"]["launch"]
        if not _por_ferramenta(launch, lambda v: isinstance(v, list) and v and all(isinstance(a, str) for a in v)):
            problemas.append('task.launch: {ferramenta: lista de strings}, com {nome}, {uuid} e {prompt} '
                             '(ex.: {"copilot": ["copilot", "-i", "{prompt}"]})')
    watch = out.get("watch")
    if not isinstance(watch, dict):
        problemas.append("watch precisa ser um objeto")
    else:
        out["watch"] = dict(PADRAO["watch"], **watch)
        if not isinstance(out["watch"]["interval"], int) or out["watch"]["interval"] < 60:
            problemas.append("watch.interval: segundos entre as passadas (60 ou mais)")
    promote = out.get("promote")
    if not isinstance(promote, dict):
        problemas.append("promote precisa ser um objeto")
    else:
        out["promote"] = dict(PADRAO["promote"], **promote)
        alvos = out["promote"]["targets"]
        ligado = isinstance(out.get("modules"), list) and "promote" in out["modules"]
        # o formato vale sempre (vira texto das skills e argumento de git); só "sem a própria base" depende
        # do módulo ligado, porque o padrão ["main"] conflita com quem usa a main como base
        if (not isinstance(alvos, list) or not alvos or not all(isinstance(a, str) and BRANCH.fullmatch(a) for a in alvos)
                or len(set(alvos)) != len(alvos)):
            problemas.append('promote.targets: lista de nomes de branch, em ordem, sem repetir (ex.: ["staging", "main"])')
        elif ligado and (out.get("git") or {}).get("base") in alvos:
            problemas.append("promote.targets não pode incluir a própria git.base")
    if not _por_ferramenta(out.get("models"), lambda v: isinstance(v, dict) and all(
            k in NIVEIS and isinstance(n, str) for k, n in v.items())):
        problemas.append(f"models: {{ferramenta: {{{', '.join(NIVEIS)} → nome do modelo na ferramenta}}}}")
    if problemas:
        raise ErroConfig("; ".join(problemas))
    return out


def _por_ferramenta(valor, ok):
    """`models` e `task.launch`: {ferramenta: valor}. Vale qualquer ferramenta conhecida, mesmo fora de
    `tools`: tirar uma da lista não obriga a apagar o que é dela."""
    return isinstance(valor, dict) and all(t in TOOLS and ok(v) for t, v in valor.items())


def carregar(caminhos, modulos_existentes=()):
    try:
        bruto = json.loads(caminhos.config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ErroConfig(f"sem config em {caminhos.config}: rode `fluxo init --tool … --forge …`")
    except ValueError as exc:
        raise ErroConfig(f"{caminhos.config} não é JSON válido: {exc}")
    return validar(bruto, modulos_existentes)
