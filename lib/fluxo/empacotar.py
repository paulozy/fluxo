"""`fluxo package`: um zip para levar o fluxo a uma máquina sem git (o WSL bloqueado).

O zip leva um arquivo VERSAO, porque lá não há `git describe`. Antes, a varredura de dados: um zip
com dado de empresa não sai.
"""
import subprocess
import zipfile
from pathlib import Path

from . import config, varredura

PULAR = {".git", "dist", "__pycache__"}


def versao(raiz):
    raiz = Path(raiz)
    if (raiz / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(raiz), "describe", "--tags", "--always", "--dirty"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        return (raiz / "VERSAO").read_text().strip() or "dev"
    except FileNotFoundError:
        return "dev"


def arquivos(raiz):
    """Os arquivos versionados (git ls-files) ou, sem git, todos fora das pastas puladas."""
    raiz = Path(raiz)
    if (raiz / ".git").exists():
        r = subprocess.run(["git", "-C", str(raiz), "ls-files", "-z"], capture_output=True, timeout=10)
        if r.returncode == 0:
            return sorted(Path(p.decode()) for p in r.stdout.split(b"\0") if p)
    return sorted(p.relative_to(raiz) for p in raiz.rglob("*")
                  if p.is_file() and not (set(p.relative_to(raiz).parts) & PULAR))


def empacotar(raiz, saida=None):
    raiz = Path(raiz)
    achados = varredura.varrer(raiz, extra=config.Caminhos().config_dir / "proibidos.txt")
    if achados:
        raise RuntimeError("varredura achou dado proibido; nada empacotado:\n" +
                           "\n".join(f"  {a}:{n} {o}" for a, n, o in achados[:20]))
    v = versao(raiz)
    destino = Path(saida or raiz / "dist") / f"fluxo-{v}.zip"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in arquivos(raiz):
            if rel.as_posix() == "VERSAO":
                continue
            info = zipfile.ZipInfo.from_file(raiz / rel, f"fluxo/{rel.as_posix()}")
            with open(raiz / rel, "rb") as fh:
                z.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)  # from_file guarda o modo (o +x do fluxo)
        z.writestr("fluxo/VERSAO", v + "\n")
    return destino
