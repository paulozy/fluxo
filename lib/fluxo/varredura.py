"""Varre o repo atrás de dado de empresa: ids, caminhos de home e termos proibidos.

Os termos ficam como sha256 de cada palavra em minúscula (`tests/proibidos.sha256`), para nem o
nome deles entrar no repo. Um `~/.config/fluxo/proibidos.txt` local (um termo por linha) soma outros.
Roda no teste, no pre-commit e antes do `empacotar`.
"""
import hashlib
import re
from pathlib import Path

PADROES = (
    ("UUID", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("id hex de 32 (Notion)", re.compile(r"\b[0-9a-f]{32}\b", re.I)),
    ("id do Slack", re.compile(r"\b[CU]0[A-Z0-9]{8,}\b")),
    ("caminho de home", re.compile(r"/home/[a-z]|/Users/[A-Za-z]")),
)
PULAR_PASTAS = {".git", "dist", "__pycache__"}
LISTA = Path("tests") / "proibidos.sha256"


def normalizar(termo):
    """Palavras em minúscula separadas por um espaço: `Core-Algo` e `core algo` viram `core algo`."""
    return " ".join(re.findall(r"\w+", termo.lower()))


def _hash(termo):
    return hashlib.sha256(normalizar(termo).encode()).hexdigest()


def proibidos(raiz):
    try:
        return {h.strip() for h in (Path(raiz) / LISTA).read_text().split() if h.strip()}
    except FileNotFoundError:
        return set()


def termos_locais(extra):
    """O proibidos.txt local é texto puro: casa como substring, em minúscula."""
    try:
        return [normalizar(t) for t in Path(extra).read_text(encoding="utf-8").splitlines() if normalizar(t)]
    except (FileNotFoundError, TypeError):
        return []


def _ngramas(palavras, ate=3):
    for i in range(len(palavras)):
        for k in range(1, ate + 1):
            if i + k <= len(palavras):
                yield " ".join(palavras[i:i + k])


def arquivos(raiz):
    raiz = Path(raiz)
    for p in sorted(raiz.rglob("*")):
        rel = p.relative_to(raiz)
        if p.is_file() and not (set(rel.parts) & PULAR_PASTAS) and rel != LISTA:
            yield p, rel


def varrer(raiz, extra=None):
    """Lista de (arquivo, linha, o que achou). Vazia = limpo. Os hashes valem para termos de até 3
    palavras (`acme corp` casa em "Acme-Corp")."""
    hashes, locais = proibidos(raiz), termos_locais(extra)
    achados = []
    for p, rel in arquivos(raiz):
        try:
            texto = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, linha in enumerate(texto.splitlines(), start=1):
            for nome, rx in PADROES:
                if rx.search(linha):
                    achados.append((rel.as_posix(), n, nome))
            palavras = re.findall(r"\w+", linha.lower())
            for g in set(_ngramas(palavras)):
                if _hash(g) in hashes:
                    achados.append((rel.as_posix(), n, f"termo proibido {g!r}"))
            normal = " ".join(palavras)
            for t in locais:
                if t in normal:
                    achados.append((rel.as_posix(), n, f"termo do proibidos.txt local {t!r}"))
    return achados
