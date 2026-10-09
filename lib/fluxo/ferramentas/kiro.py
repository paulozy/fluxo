"""Kiro CLI: ~/.kiro/steering, ~/.kiro/agents (JSON) e ~/.kiro/skills.

O agente em JSON vale para os motores V2 e V3; o `.md` só para o V3. O prompt vai inline, como o
port anterior validou, sem depender de resolver `file://`. Fontes: https://kiro.dev/docs/steering,
https://kiro.dev/docs/custom-agents/configuration-reference e https://kiro.dev/docs/skills.
"""
import json

from .. import fonte


def instrucoes(texto, Arquivo):
    # sem frontmatter, a inclusão é `always`: o único modo que o V2 carrega sozinho
    return [Arquivo(".kiro/steering/fluxo.md", texto.encode())]


def agente(ag, Arquivo, motor="v2", permitidas=()):
    dados = {"name": ag["name"], "description": ag["description"], "prompt": ag["corpo"], "tools": ag["tools"]}
    livres = [t for t in ag["tools"] if t in permitidas]
    if livres:  # leitura e busca sem pedir aprovação a cada uso; shell e escrita continuam pedindo
        dados["allowedTools"] = livres
    if ag["model"]:
        dados["model"] = ag["model"]
    texto = json.dumps(dados, ensure_ascii=False, indent=2) + "\n"
    return Arquivo(f".kiro/agents/{ag['name']}.json", texto.encode())


def skill(sk, Arquivo):
    # o Kiro só documenta name/description/license/compatibility/metadata: o argument-hint fica de fora
    meta = {"name": sk["name"], "description": sk["description"]}
    return Arquivo(f".kiro/skills/{sk['name']}/SKILL.md", fonte.escrever(meta, sk["corpo"]).encode())
