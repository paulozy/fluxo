"""Template das fontes: variáveis `{{a.b}}` e blocos `<!-- se eixo:valor -->…<!-- senao -->…<!-- fim -->`.

Estrito de propósito: variável, eixo ou valor desconhecido é erro, para um texto não sair para a
ferramenta com `{{forge.pr}}` cru ou com o bloco errado. Os blocos não se aninham.
"""
import re

VAR = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")  # o hífen é de {{skill.pr-feedback}}
BLOCO = re.compile(
    r"^[ \t]*<!--\s*se\s+(?P<cond>[^>]+?)\s*-->[ \t]*\n(?P<sim>.*?)"
    r"(?:^[ \t]*<!--\s*senao\s*-->[ \t]*\n(?P<nao>.*?))?"
    r"^[ \t]*<!--\s*fim\s*-->[ \t]*(?:\n|\Z)",
    re.M | re.S | re.I,
)
# qualquer grafia de marcador que o BLOCO não consumiu é erro: sem isso, `<!--se x-->` sairia com os
# dois ramos e os marcadores crus
MARCA = re.compile(r"<!--\s*(se|senao|fim)\b", re.I)
SOBRA = re.compile(r"\{\{|\}\}")


class ErroTemplate(Exception):
    pass


def condicao(texto, eixos, valores_validos, onde="?"):
    """`forge:gitlab`, `chat:draft|slack`, `module:task`, `nao os:mac`."""
    texto = texto.strip()
    negar = texto.startswith("nao ")
    if negar:
        texto = texto[4:].strip()
    eixo, sep, alternativas = texto.partition(":")
    if not sep or eixo not in valores_validos:
        raise ErroTemplate(f"{onde}: condição {texto!r}: o eixo tem que ser um de {', '.join(sorted(valores_validos))}")
    pedidos = alternativas.split("|")
    for v in pedidos:
        if v not in valores_validos[eixo]:
            raise ErroTemplate(f"{onde}: condição {texto!r}: {v!r} não é valor de {eixo}")
    atual = eixos.get(eixo)
    vale = bool(set(atual) & set(pedidos)) if isinstance(atual, (set, list, tuple)) else atual in pedidos
    return vale != negar


def _var(nome, contexto, onde):
    atual = contexto
    for parte in nome.split("."):
        if not isinstance(atual, dict) or parte not in atual:
            raise ErroTemplate(f"{onde}: variável desconhecida {{{{{nome}}}}}")
        atual = atual[parte]
    if isinstance(atual, dict):
        raise ErroTemplate(f"{onde}: {{{{{nome}}}}} é um grupo, não um valor")
    return str(atual)


def renderizar(texto, contexto, eixos, valores_validos, onde="?"):
    for m in VAR.finditer(texto):  # todas, inclusive as dos ramos que não vão ser usados nesta máquina
        _var(m.group(1), contexto, onde)

    def bloco(m):
        dentro = (m.group("sim") or "") + (m.group("nao") or "")
        if MARCA.search(dentro):
            raise ErroTemplate(f"{onde}: bloco aninhado em <!-- se {m.group('cond')} -->")
        return m.group("sim") if condicao(m.group("cond"), eixos, valores_validos, onde) else (m.group("nao") or "")

    saida = BLOCO.sub(bloco, texto)
    sobra = MARCA.search(saida)
    if sobra:
        linha = saida[:sobra.start()].count("\n") + 1
        raise ErroTemplate(f"{onde}: marcador de bloco fora do lugar perto da linha {linha} "
                           "(cada marcador fica sozinho na linha, e os blocos não se aninham)")
    saida = VAR.sub(lambda m: _var(m.group(1), contexto, onde), saida)
    sobra = SOBRA.search(saida)
    if sobra:
        linha = saida[:sobra.start()].count("\n") + 1
        raise ErroTemplate(f"{onde}: chaves duplas fora do formato `{{{{nome.campo}}}}` perto da linha {linha}")
    return saida
