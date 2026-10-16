"""Varre o repo atrás de dado de empresa: ids, caminhos de home e termos proibidos.

Os termos ficam como sha256 de cada termo normalizado (`tests/proibidos.sha256`), para nem o nome
deles entrar no repo. Um `~/.config/fluxo/proibidos.txt` local (um termo por linha) soma outros.
Roda no teste, no pre-commit e antes do `empacotar`. O `scripts/scan.ts` do pr-reviewer usa o mesmo
formato e a mesma tokenização: as duas mudam juntas.
"""
import hashlib
import os
import re
import unicodedata
from pathlib import Path

PADROES = (
    ("UUID", re.compile(r"(?<![0-9a-f-])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])", re.I)),
    ("id hex de 32 (Notion)", re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", re.I)),
    ("id do Slack", re.compile(r"(?<![A-Za-z0-9])[CTU]0[A-Z0-9]{8,}(?![A-Za-z0-9])")),
    ("caminho de home", re.compile(r"/home/[a-z]|/Users/[A-Za-z]|[A-Za-z]:\\Users\\")),
)
PULAR_PASTAS = {".git", "dist", "__pycache__"}
LISTA = Path("tests") / "proibidos.sha256"


def normalizar(termo):
    """Palavras em minúscula separadas por um espaço: `Core-Algo` e `core algo` viram `core algo`."""
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", termo).lower()))


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


def _digito(c):
    return bool(c) and unicodedata.category(c) == "Nd"


def partes(token):
    """`FOO_BAR_URL` e `FooBar` viram foo, bar (e url): o termo casa nas duas grafias."""
    out = []
    for pedaco in token.split("_"):
        atual = ""
        for i, c in enumerate(pedaco):
            ant = pedaco[i - 1] if i else ""
            prox = pedaco[i + 1] if i + 1 < len(pedaco) else ""
            if atual and c.isupper() and (ant.islower() or _digito(ant) or (ant.isupper() and prox.islower())):
                out.append(atual)
                atual = ""
            atual += c
        if atual:
            out.append(atual)
    return [p.lower() for p in out]


def _texto(dados):
    """UTF-16 pelo BOM; NUL sem BOM é binário (None); o resto é UTF-8, trocando o que não decodifica."""
    if dados[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return dados.decode("utf-16", "replace")
    if b"\0" in dados:
        return None
    return dados.decode("utf-8-sig", "replace")


def varrer_texto(rel, texto, hashes, locais, primeira=1):
    """Os n-gramas de até 3 palavras correm o texto inteiro, então um termo que quebra a linha casa
    (na linha da primeira palavra)."""
    achados = {}
    inteiras, partidas = [], []
    for i, bruta in enumerate(unicodedata.normalize("NFKC", texto).splitlines()):
        n = primeira + i
        for nome, rx in PADROES:
            if rx.search(bruta):
                achados[(n, nome)] = (rel, n, nome)
        palavras = re.findall(r"\w+", bruta.lower())
        pedacos = [p for t in re.findall(r"\w+", bruta) for p in partes(t)]
        inteiras += [(w, n) for w in palavras]
        partidas += [(w, n) for w in pedacos]
        for plano in {" ".join(palavras), " ".join(pedacos)}:
            for t in locais:
                if t in plano:
                    o = f"termo do proibidos.txt local {t!r}"
                    achados[(n, o)] = (rel, n, o)
    for corrente in (inteiras, partidas):
        for i in range(len(corrente)):
            for k in range(1, 4):
                if i + k <= len(corrente):
                    g = " ".join(w for w, _ in corrente[i:i + k])
                    if hashlib.sha256(g.encode()).hexdigest() in hashes:
                        o = f"termo proibido {g!r}"
                        achados[(corrente[i][1], o)] = (rel, corrente[i][1], o)
    return list(achados.values())


def arquivos(raiz):
    raiz = Path(raiz)
    for p in sorted(raiz.rglob("*")):
        rel = p.relative_to(raiz)
        if (p.is_file() or p.is_symlink()) and not (set(rel.parts) & PULAR_PASTAS) and rel != LISTA:
            yield p, rel


def varrer(raiz, extra=None):
    """Lista de (arquivo, linha, o que achou). Vazia = limpo. O caminho do arquivo entra como a linha 0,
    e um symlink é varrido pelo caminho-alvo."""
    hashes, locais = proibidos(raiz), termos_locais(extra)
    achados = []
    for p, rel in arquivos(raiz):
        nome = rel.as_posix()
        achados += varrer_texto(nome, nome, hashes, locais, primeira=0)
        try:
            texto = os.readlink(p) if p.is_symlink() else _texto(p.read_bytes())
        except OSError:
            continue
        if texto is not None:
            achados += varrer_texto(nome, texto, hashes, locais)
    return achados
