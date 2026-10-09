"""Leitura dos arquivos-fonte do núcleo: frontmatter mínimo + corpo.

O frontmatter das fontes é nosso, então aceita só o subconjunto que usamos (`chave: valor`, listas
`[a, b]` e strings entre aspas), sem PyYAML.
"""
import json
import re

CHAVE = re.compile(r"^([A-Za-z][\w-]*):\s*(.*)$")


class ErroFonte(Exception):
    pass


def _valor(bruto, onde):
    bruto = bruto.strip()
    if bruto.startswith("[") and bruto.endswith("]"):
        itens = [i.strip() for i in bruto[1:-1].split(",")]
        return [i.strip("\"'") for i in itens if i]
    if bruto.startswith('"'):
        try:
            return json.loads(bruto)
        except ValueError:
            raise ErroFonte(f"{onde}: string entre aspas inválida: {bruto[:60]}")
    return bruto


def ler(texto, onde="?"):
    """Devolve (frontmatter: dict, corpo: str). Sem frontmatter, ({}, texto)."""
    if not texto.startswith("---\n"):
        return {}, texto
    fim = texto.find("\n---\n", 4)
    if fim < 0:
        raise ErroFonte(f"{onde}: frontmatter sem o --- de fechamento")
    meta = {}
    for n, linha in enumerate(texto[4:fim].splitlines(), start=2):
        if not linha.strip() or linha.lstrip().startswith("#"):
            continue
        m = CHAVE.match(linha)
        if not m:
            raise ErroFonte(f"{onde}:{n}: linha de frontmatter fora do formato `chave: valor`: {linha[:60]}")
        meta[m.group(1)] = _valor(m.group(2), f"{onde}:{n}")
    return meta, texto[fim + 5:]


def escrever(meta, corpo):
    """Frontmatter de saída: cada valor como JSON (string JSON é YAML válido), na ordem dada."""
    linhas = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items() if v is not None]
    return "\n".join(linhas) + "\n---\n" + corpo.lstrip("\n")
