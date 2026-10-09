"""Comum aos testes: o lib/ no sys.path e os cenários de config das quatro combinações."""
import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "lib"))

# Nenhum teste fala com o tmux do usuário. Dentro do tmux, a variável TMUX aponta para o servidor dele
# (em 09/10 um kill-server de teste o derrubou); sem TMUX e sem TMUX_TMPDIR, o tmux cai no socket padrão,
# que também é o dele. Daqui em diante, qualquer `tmux` dos testes vai para uma pasta vazia.
os.environ.pop("TMUX", None)
os.environ.pop("TMUX_PANE", None)
os.environ["TMUX_TMPDIR"] = os.path.realpath(tempfile.mkdtemp(prefix="fxt", dir="/tmp"))
atexit.register(shutil.rmtree, os.environ["TMUX_TMPDIR"], True)

from fluxo import config as cfgmod  # noqa: E402

CENARIOS = {
    "claude": {"ferramenta": "claude", "forge": "github", "so": "linux"},
    "copilot": {"ferramenta": "copilot", "forge": "gitlab", "so": "mac", "gitlab": {"host": "gitlab.exemplo.com"}},
    "kiro-v2": {"ferramenta": "kiro", "forge": "github", "so": "wsl", "kiro": {"motor": "v2"}},
    "kiro-v3": {"ferramenta": "kiro", "forge": "github", "so": "wsl", "kiro": {"motor": "v3"}},
    # todos os módulos ligados: cada fase acrescenta os seus
    "copilot-tudo": {"ferramenta": "copilot", "forge": "gitlab", "so": "mac", "gitlab": {"host": "gitlab.exemplo.com"},
                     "modulos": ["mensagens", "task", "trava"]},
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
