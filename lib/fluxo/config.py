"""Config local da máquina (~/.config/fluxo/config.json) e os caminhos que o fluxo usa.

A config é JSON, e não TOML, porque o Python 3.9 do Mac não tem `tomllib`.
"""
import json
import os
import platform
import re
from pathlib import Path

EIXOS = {
    "ferramenta": ("claude", "copilot", "kiro"),
    "forge": ("github", "gitlab"),
    "tracker": ("nenhum", "jira"),
    "chat": ("rascunho", "slack"),
    "so": ("linux", "mac", "wsl"),
}
MOTORES = ("v2", "v3")  # Kiro CLI: o V2 é o padrão do 2.x; o V3 entra com --v3
NIVEIS = ("forte", "medio", "leve")  # modelo abstrato dos agentes
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")  # nome de branch simples: sem espaço nem caractere de shell

PADRAO = {
    "versao": 1,
    "tracker": "nenhum",
    "chat": "rascunho",
    "modulos": [],
    "git": {"base": "develop"},
    "codigo_dir": "~/code",
    "modelos": {},
    "task": {"max": 2},
    "vigia": {"intervalo": 300},
    "promover": {"alvos": ["main"]},  # as bases depois da git.base, em ordem (ex.: staging, main)
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
    if out.get("versao") != 1:
        problemas.append(f"versao {out.get('versao')!r} desconhecida (esperado 1)")
    for eixo, valores in EIXOS.items():
        if eixo == "so" and not out.get("so"):
            out["so"] = detectar_so()
        if out.get(eixo) not in valores:
            problemas.append(f"{eixo}: {out.get(eixo)!r} não é um de {', '.join(valores)}")
    if out.get("ferramenta") == "kiro":
        motor = (out.get("kiro") or {}).get("motor", "v2")
        if motor not in MOTORES:
            problemas.append(f"kiro.motor: {motor!r} não é um de {', '.join(MOTORES)}")
        out["kiro"] = dict(out.get("kiro") or {}, motor=motor)
    if not isinstance(out.get("modulos"), list):
        problemas.append("modulos precisa ser uma lista")
    else:
        if len(set(out["modulos"])) != len(out["modulos"]):
            problemas.append("modulos tem repetição")
        for m in out["modulos"]:
            if m not in modulos_existentes:
                problemas.append(f"módulo {m!r} não existe" + (f" (há: {', '.join(modulos_existentes)})"
                                                               if modulos_existentes else " (ainda não há módulos)"))
    base = (out.get("git") or {}).get("base")
    if not isinstance(base, str) or not base.strip():
        problemas.append("git.base precisa ser o nome da branch de desenvolvimento (ex.: develop)")
    if not isinstance(out.get("codigo_dir"), str) or not out["codigo_dir"].strip():
        problemas.append("codigo_dir precisa ser a pasta dos clones (ex.: ~/code)")
    task = out.get("task")
    if not isinstance(task, dict):
        problemas.append("task precisa ser um objeto")
    else:
        out["task"] = dict(PADRAO["task"], **task)
        if not isinstance(out["task"]["max"], int) or out["task"]["max"] < 1:
            problemas.append("task.max: número de tarefas com ID ao mesmo tempo (1 ou mais)")
        lancar = out["task"].get("lancar")
        if lancar is not None and (not isinstance(lancar, list) or not lancar or not all(isinstance(a, str) for a in lancar)):
            problemas.append('task.lancar: lista de strings, com {nome}, {uuid} e {prompt} (ex.: ["copilot", "-i", "{prompt}"])')
    vigia = out.get("vigia")
    if not isinstance(vigia, dict):
        problemas.append("vigia precisa ser um objeto")
    else:
        out["vigia"] = dict(PADRAO["vigia"], **vigia)
        if not isinstance(out["vigia"]["intervalo"], int) or out["vigia"]["intervalo"] < 60:
            problemas.append("vigia.intervalo: segundos entre as passadas (60 ou mais)")
    promover = out.get("promover")
    if not isinstance(promover, dict):
        problemas.append("promover precisa ser um objeto")
    else:
        out["promover"] = dict(PADRAO["promover"], **promover)
        alvos = out["promover"]["alvos"]
        ligado = isinstance(out.get("modulos"), list) and "promover" in out["modulos"]
        # o formato vale sempre (vira texto das skills e argumento de git); só "sem a própria base" depende
        # do módulo ligado, porque o padrão ["main"] conflita com quem usa a main como base
        if (not isinstance(alvos, list) or not alvos or not all(isinstance(a, str) and BRANCH.fullmatch(a) for a in alvos)
                or len(set(alvos)) != len(alvos)):
            problemas.append('promover.alvos: lista de nomes de branch, em ordem, sem repetir (ex.: ["staging", "main"])')
        elif ligado and (out.get("git") or {}).get("base") in alvos:
            problemas.append("promover.alvos não pode incluir a própria git.base")
    modelos = out.get("modelos")
    if not isinstance(modelos, dict) or any(k not in NIVEIS or not isinstance(v, str) for k, v in modelos.items()):
        problemas.append(f"modelos: um objeto com {', '.join(NIVEIS)} → nome do modelo na ferramenta")
    if problemas:
        raise ErroConfig("; ".join(problemas))
    return out


def carregar(caminhos, modulos_existentes=()):
    try:
        bruto = json.loads(caminhos.config.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ErroConfig(f"sem config em {caminhos.config}: rode `fluxo init --ferramenta … --forge …`")
    except ValueError as exc:
        raise ErroConfig(f"{caminhos.config} não é JSON válido: {exc}")
    return validar(bruto, modulos_existentes)
