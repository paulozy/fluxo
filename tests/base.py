"""Comum aos testes: o lib/ no sys.path e os cenários de config das quatro combinações."""
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "lib"))

from fluxo import config as cfgmod  # noqa: E402

CENARIOS = {
    "claude": {"ferramenta": "claude", "forge": "github", "so": "linux"},
    "copilot": {"ferramenta": "copilot", "forge": "gitlab", "so": "mac", "gitlab": {"host": "gitlab.exemplo.com"}},
    "kiro-v2": {"ferramenta": "kiro", "forge": "github", "so": "wsl", "kiro": {"motor": "v2"}},
    "kiro-v3": {"ferramenta": "kiro", "forge": "github", "so": "wsl", "kiro": {"motor": "v3"}},
}
PASTAS_FERRAMENTA = (".claude", ".copilot", ".kiro")


def escrever_config(home, cenario, **extra):
    cam = cfgmod.Caminhos(home=home)
    cam.config_dir.mkdir(parents=True, exist_ok=True)
    dados = dict(CENARIOS[cenario], versao=1, **extra)
    cam.config.write_text(json.dumps(dados), encoding="utf-8")
    return cam


def arvore(home, pastas=PASTAS_FERRAMENTA):
    """{caminho relativo: bytes} dos arquivos sob as pastas das ferramentas."""
    home = Path(home)
    out = {}
    for pasta in pastas:
        for p in sorted((home / pasta).rglob("*")):
            if p.is_file():
                out[p.relative_to(home).as_posix()] = p.read_bytes()
    return out
