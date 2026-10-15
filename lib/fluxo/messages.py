"""`fluxo msg`: guardar e copiar para a área de transferência a mensagem que você vai colar no chat.

Nada é enviado: lá não há conector de Teams nem de Slack. O texto vai sempre por stdin, nunca por argv.
- Mac: pbcopy lê no encoding do locale, então vai com LANG em UTF-8, senão o acento quebra.
- WSL: o clip.exe lê na codepage do Windows; UTF-16 com BOM é o que ele entende (a validar na máquina).
- Linux: wl-copy, xclip ou xsel, o que existir.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

MODELO_TOM = Path(__file__).resolve().parent.parent.parent / "modulos" / "messages" / "tom-modelo.md"


def comando_copiar(so, which=shutil.which):
    """(argv, encoding, env extra) para o sistema, ou None se não houver ferramenta."""
    if so == "mac":
        return (["pbcopy"], "utf-8", {"LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}) if which("pbcopy") else None
    if so == "wsl":
        return (["clip.exe"], "utf-16", {}) if which("clip.exe") else None
    for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        if which(argv[0]):
            return argv, "utf-8", {}
    return None


def guardar(texto, dados, agora=None):
    pasta = Path(dados) / "mensagens"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / time.strftime("%Y%m%d-%H%M%S.md", time.localtime(agora))
    destino.write_text(texto, encoding="utf-8")
    return destino


def copiar(texto, so, run=subprocess.run, which=shutil.which):
    """Devolve (copiou, explicação)."""
    cmd = comando_copiar(so, which)
    if cmd is None:
        return False, "sem ferramenta de área de transferência (pbcopy, clip.exe, wl-copy, xclip, xsel)"
    argv, encoding, env = cmd
    # xclip e wl-copy ficam em segundo plano servindo a seleção: com os pipes capturados, o filho os herdaria
    # e o run esperaria até o timeout. Nesses, a saída vai para o /dev/null.
    saidas = ({"capture_output": True} if so in ("mac", "wsl")
              else {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})
    try:
        r = run(argv, input=texto.encode(encoding), env=dict(os.environ, **env), timeout=10, **saidas)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{argv[0]} falhou: {exc}"
    if r.returncode:
        return False, f"{argv[0]} saiu com {r.returncode}: {(getattr(r, 'stderr', None) or b'').decode(errors='replace').strip()[:120]}"
    return True, argv[0]


def criar_tom(config_dir):
    """Cria o ~/.config/fluxo/tom.md a partir do modelo, se ainda não existir. Devolve (caminho, criado)."""
    destino = Path(config_dir) / "tom.md"
    if destino.exists():
        return destino, False
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(MODELO_TOM.read_text(encoding="utf-8"), encoding="utf-8")
    return destino, True
