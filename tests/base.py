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
    "claude": {"tools": ["claude"], "forge": "github", "os": "linux"},
    "copilot": {"tools": ["copilot"], "forge": "gitlab", "os": "mac", "gitlab": {"host": "gitlab.exemplo.com"}},
    "kiro-v2": {"tools": ["kiro"], "forge": "github", "os": "wsl", "kiro": {"engine": "v2"}},
    "kiro-v3": {"tools": ["kiro"], "forge": "github", "os": "wsl", "kiro": {"engine": "v3"}},
    # todos os módulos ligados: cada fase acrescenta os seus
    "copilot-tudo": {"tools": ["copilot"], "forge": "gitlab", "os": "mac", "gitlab": {"host": "gitlab.exemplo.com"},
                     "modules": ["messages", "panel", "promote", "reviewer", "task", "lock", "watch"],
                     "git": {"base": "develop"}, "promote": {"targets": ["staging", "main"]}},
    # tudo o que o Claude aceita (o panel não: lá é o agent-tree), no GitHub
    "claude-tudo": {"tools": ["claude"], "forge": "github", "os": "linux",
                    "modules": ["messages", "promote", "reviewer", "task", "lock", "watch"]},
    # o promote sem o módulo messages: o aviso vira rascunho
    "claude-promote": {"tools": ["claude"], "forge": "github", "os": "linux", "modules": ["promote"]},
    # o panel no Kiro V2: os hooks embutidos num agente `fluxo`, que vira o padrão
    "kiro-v2-panel": {"tools": ["kiro"], "forge": "github", "os": "wsl", "kiro": {"engine": "v2"},
                      "modules": ["panel", "watch"]},
    # duas ferramentas, com o copilot de padrão: o panel só vale para ele, e o watch é da máquina
    "copilot-claude": {"tools": ["copilot", "claude"], "forge": "github", "os": "linux",
                       "modules": ["messages", "panel", "promote", "reviewer", "task", "lock", "watch"]},
    # o Codex sozinho: as skills no lugar oficial, ~/.agents/skills
    "codex": {"tools": ["codex"], "forge": "github", "os": "linux"},
    # com o copilot junto, as skills do Codex vão para ~/.codex/skills (o Copilot também lê ~/.agents/skills)
    "copilot-codex": {"tools": ["copilot", "codex"], "forge": "gitlab", "os": "mac", "gitlab": {"host": "gitlab.exemplo.com"},
                      "modules": ["messages", "panel", "promote", "reviewer", "task", "lock", "watch"]},
}
PASTAS_FERRAMENTA = (".claude", ".copilot", ".kiro", ".codex", ".agents", "Library",
                     ".config/systemd")  # + agendamento do watch


def escrever_config(home, cenario, **extra):
    cam = cfgmod.Caminhos(home=home)
    cam.config_dir.mkdir(parents=True, exist_ok=True)
    dados = dict(CENARIOS[cenario], version=1, **extra)
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
