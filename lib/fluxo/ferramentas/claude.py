"""Claude Code: ~/.claude/rules, ~/.claude/agents e ~/.claude/skills.

Fonte: https://code.claude.com/docs/en/memory, /sub-agents e /skills. As instruções vão para
`rules/fluxo.md` e não para o CLAUDE.md, que continua sendo do usuário.
"""
from .. import fonte


def instrucoes(texto, Arquivo):
    return [Arquivo(".claude/rules/fluxo.md", texto.encode())]


def agente(ag, Arquivo, motor=None, permitidas=()):
    meta = {"name": ag["name"], "description": ag["description"], "tools": ", ".join(ag["tools"]),
            "model": ag["model"]}
    return Arquivo(f".claude/agents/{ag['name']}.md", fonte.escrever(meta, ag["corpo"]).encode())


def skill(sk, Arquivo):
    meta = {"name": sk["name"], "description": sk["description"], "argument-hint": sk["argument-hint"]}
    return Arquivo(f".claude/skills/{sk['name']}/SKILL.md", fonte.escrever(meta, sk["corpo"]).encode())
