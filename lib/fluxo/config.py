"""Config local da máquina (~/.config/fluxo/config.json) e os caminhos que o fluxo usa.

A config é JSON, e não TOML, porque o Python 3.9 do Mac não tem `tomllib`.
"""
import json
import os
import platform
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

PADRAO = {
    "versao": 1,
    "tracker": "nenhum",
    "chat": "rascunho",
    "modulos": [],
    "git": {"base": "develop"},
    "codigo_dir": "~/code",
    "modelos": {},
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
        for m in out["modulos"]:
            if m not in modulos_existentes:
                problemas.append(f"módulo {m!r} não existe" + (f" (há: {', '.join(modulos_existentes)})"
                                                               if modulos_existentes else " (ainda não há módulos)"))
    base = (out.get("git") or {}).get("base")
    if not isinstance(base, str) or not base.strip():
        problemas.append("git.base precisa ser o nome da branch de desenvolvimento (ex.: develop)")
    if not isinstance(out.get("codigo_dir"), str) or not out["codigo_dir"].strip():
        problemas.append("codigo_dir precisa ser a pasta dos clones (ex.: ~/code)")
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
