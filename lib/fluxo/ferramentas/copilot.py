"""GitHub Copilot CLI: ~/.copilot/instructions, ~/.copilot/agents e ~/.copilot/skills.

O Copilot não lê ~/.claude. Fontes:
https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-config-dir-reference,
https://docs.github.com/en/copilot/reference/custom-agents-configuration.
"""
from .. import fonte


def instrucoes(texto, Arquivo):
    # um arquivo próprio em instructions/, e não o copilot-instructions.md, que pode já ser do usuário
    return [Arquivo(".copilot/instructions/fluxo.instructions.md", fonte.escrever({"applyTo": "**"}, texto).encode())]


def agente(ag, Arquivo, motor=None, permitidas=()):
    meta = {"name": ag["name"], "description": ag["description"], "tools": ag["tools"], "model": ag["model"]}
    return Arquivo(f".copilot/agents/{ag['name']}.agent.md", fonte.escrever(meta, ag["corpo"]).encode())


def skill(sk, Arquivo):
    meta = {"name": sk["name"], "description": sk["description"], "argument-hint": sk["argument-hint"]}
    return Arquivo(f".copilot/skills/{sk['name']}/SKILL.md", fonte.escrever(meta, sk["corpo"]).encode())
