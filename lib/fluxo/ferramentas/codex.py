"""OpenAI Codex CLI: ~/.codex/AGENTS.md, ~/.codex/agents/*.toml e as skills.

Fontes (as docs de developers.openai.com/codex agora estão em learn.chatgpt.com/docs):
https://learn.chatgpt.com/docs/agent-configuration/agents-md (um AGENTS.md global só: o do usuário, se
existir, dá conflito no install), https://learn.chatgpt.com/docs/agent-configuration/subagents (agente
em TOML: name, description, developer_instructions, model; sem campo de ferramentas) e
https://learn.chatgpt.com/docs/build-skills (skills do usuário em ~/.agents/skills, chamadas com $nome).

O que só o código confirma (tag rust-v0.162.1, a validar na máquina): o ~/.codex/skills, obsoleto, ainda
é lido (codex-rs/ext/skills/src/host_roots.rs), e o agente é lido com deny_unknown_fields
(codex-rs/agent-roles/src/agent_role_config.rs): uma chave a mais derruba o arquivo inteiro.
"""
import json

from .. import fonte


def instrucoes(texto, Arquivo):
    return [Arquivo(".codex/AGENTS.md", texto.encode())]


def toml_str(s):
    """String básica do TOML: os escapes são os do JSON (\\" \\\\ \\n \\uXXXX), mas o DEL não pode ir cru.
    https://toml.io/en/v1.0.0#string"""
    return json.dumps(s, ensure_ascii=False).replace("\x7f", "\\u007f")


def agente(ag, Arquivo, motor=None, permitidas=()):
    campos = [("name", ag["name"]), ("description", ag["description"]),
              ("developer_instructions", ag["corpo"]), ("model", ag["model"])]
    texto = "".join(f"{k} = {toml_str(v)}\n" for k, v in campos if v)
    return Arquivo(f".codex/agents/{ag['name']}.toml", texto.encode())


def skill(sk, Arquivo, tools=()):
    # o Copilot também lê o ~/.agents/skills: com ele junto, as do Codex vão para o ~/.codex/skills,
    # e o Copilot não as vê em dobro (com o `$` do Codex no texto)
    pasta = ".codex/skills" if "copilot" in tools else ".agents/skills"
    meta = {"name": sk["name"], "description": sk["description"]}
    return Arquivo(f"{pasta}/{sk['name']}/SKILL.md", fonte.escrever(meta, sk["corpo"]).encode())
