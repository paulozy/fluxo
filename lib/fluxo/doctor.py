"""`fluxo doctor`: confere a máquina e a instalação, sem mudar nada.

Cada checagem devolve (nível, texto), com nível OK, AVISO ou FALHA. Os comandos externos têm
timeout curto: o host do GitLab atrás da VPN não pode travar o doctor.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod
from . import instalar, messages, uso, validate

OK, AVISO, FALHA = "OK", "AVISO", "FALHA"
BINARIOS = {"claude": "claude", "copilot": "copilot", "kiro": "kiro-cli", "codex": "codex"}
FORGE_CLI = {"github": "gh", "gitlab": "glab"}


def _rodar(run, argv, timeout=10):
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)


def checar(caminhos, raiz, cfg=None, erro_cfg=None, run=subprocess.run, which=shutil.which,
           conectar=socket.create_connection, versao=None, env=None):
    env = os.environ if env is None else env
    versao = versao or sys.version_info
    out = []
    out.append((OK if versao >= (3, 9) else FALHA, f"python {versao[0]}.{versao[1]} ({sys.executable})"))
    if cfg is None:
        out.append((FALHA, f"config: {erro_cfg}"))
        return out
    tools = cfg["tools"]
    out.append((OK, f"config {caminhos.config}: {', '.join(tools)} (padrão: {tools[0]}), {cfg['forge']}, {cfg['os']}"))
    detectado = cfgmod.detectar_so()
    if detectado != cfg["os"]:
        out.append((AVISO, f"os na config é {cfg['os']}, mas esta máquina parece {detectado}"))

    for tool in tools:  # só a padrão é obrigatória: as outras são para quando ela não puder
        binario = BINARIOS[tool]
        if not which(binario):
            out.append((FALHA if tool == tools[0] else AVISO, f"{binario} não está no PATH"))
            continue
        cod, saida = _rodar(run, [binario, "--version"])
        out.append((OK if cod == 0 else AVISO, f"{binario}: {saida.strip().splitlines()[0] if saida.strip() else '?'}"))
        if tool == "kiro":
            out += _kiro_agentes(caminhos, run)
    if "codex" in tools:
        out += _codex(caminhos, env)

    out.append((OK, "git") if which("git") else (FALHA, "git não está no PATH"))
    cli = FORGE_CLI[cfg["forge"]]
    host = "github.com" if cfg["forge"] == "github" else (cfg.get("gitlab") or {}).get("host", "")
    if not which(cli):
        out.append((AVISO, f"{cli} não está no PATH: a abertura de {('PR' if cli == 'gh' else 'MR')} e a skill pr-feedback precisam dele"))
    else:
        argv = [cli, "auth", "status"] + (["--hostname", host] if host and cli == "glab" else [])
        cod, saida = _rodar(run, argv)
        out.append((OK, f"{cli} autenticado") if cod == 0 else (AVISO, f"{cli} auth status falhou: {saida.strip()[:120]}"))
    if not host:
        out.append((AVISO, "gitlab.host não está na config: rode `fluxo init` com --gitlab-host"))
    else:
        try:
            conectar((host, 443), 3).close()
            out.append((OK, f"{host}:443 alcançável"))
        except OSError as exc:
            out.append((AVISO, f"{host}:443 inalcançável ({exc}): VPN desligada?"))

    modulos = cfg.get("modules", [])
    if ("task" in modulos or "lock" in modulos) and not which("tmux"):
        out.append((FALHA if "task" in modulos else AVISO,
                    "tmux não está no PATH: o `fluxo task` precisa dele, e a trava usa o pane para saber se o dono vive"))
    if "messages" in modulos and messages.comando_copiar(cfg["os"], which) is None:
        out.append((AVISO, "sem pbcopy/clip.exe/wl-copy/xclip/xsel: o `fluxo msg` só vai guardar o texto, sem copiar"))
    if "validate" in modulos:
        out += validate.checar(cfg, caminhos, run, which, env)
    if "panel" in modulos and "copilot" in cfg.get("tools", []):
        out.append(_statusline(caminhos, env))

    pasta_bin = str(caminhos.home / ".local" / "bin")
    if pasta_bin not in env.get("PATH", "").split(os.pathsep):
        out.append((AVISO, f"{pasta_bin} não está no PATH: o comando `fluxo` não vai achar o wrapper"))

    try:
        manifesto = instalar.ler_manifesto(caminhos.manifesto)
    except RuntimeError as exc:
        return out + [(FALHA, str(exc))]
    if not manifesto:
        out.append((AVISO, "nada instalado ainda: rode `fluxo install`"))
    else:
        sumiu, editado = [], []
        for rel, info in manifesto.items():
            atual = instalar._atual(caminhos.home / rel)
            if atual is None:
                sumiu.append(rel)
            elif atual != info.get("sha256"):
                editado.append(rel)
        nivel = OK if not (sumiu or editado) else AVISO
        texto = f"{len(manifesto)} arquivos instalados"
        if sumiu:
            texto += f"; {len(sumiu)} sumiram ({', '.join(sumiu[:3])}…)"
        if editado:
            texto += f"; {len(editado)} editados à mão ({', '.join(editado[:3])}…)"
        out.append((nivel, texto))
    return out


def _statusline(caminhos, env):
    """A statusline do fluxo no Copilot: sem ela, o painel fica sem o gasto e o contexto ao vivo."""
    return {"fluxo": (OK, "statusline do fluxo no Copilot: o painel tem o gasto ao vivo"),
            "outra": (AVISO, "o Copilot tem outra statusline: o painel fica sem o gasto ao vivo (fluxo panel enable diz como trocar)"),
            "ilegível": (AVISO, "o settings.json do Copilot não abriu como JSON: o painel não sabe da statusline"),
            None: (AVISO, "sem a statusline do fluxo no Copilot: fluxo panel enable liga o gasto ao vivo no painel")}[
        uso.estado_da_statusline(uso.pastas_copilot(caminhos, env))]


def _codex(caminhos, env):
    """O fluxo grava no ~/.codex. Um CODEX_HOME em outro lugar, ou um AGENTS.override.md não vazio (que o
    Codex lê no lugar do AGENTS.md), deixam as instruções do fluxo de fora sem erro nenhum.
    https://learn.chatgpt.com/docs/agent-configuration/agents-md e /docs/config-file/environment-variables"""
    out = []
    padrao = (caminhos.home / ".codex").resolve()
    valor = env.get("CODEX_HOME") or ""
    lido = (caminhos.home / valor[1:].lstrip("/")) if valor.startswith("~") else Path(valor) if valor else padrao
    lido = lido.resolve()
    if lido != padrao:
        out.append((AVISO, f"CODEX_HOME={valor}: o fluxo instala em ~/.codex, e o Codex vai ler {lido}"))
    override = lido / "AGENTS.override.md"
    try:
        if override.read_text(encoding="utf-8", errors="replace").strip():
            out.append((AVISO, f"{override} não está vazio: o Codex lê só ele, e as instruções do fluxo ficam de fora"))
    except OSError:  # não existe (o normal), ou não deu para ler
        pass
    return out


def _kiro_agentes(caminhos, run):
    out = []
    for p in sorted((caminhos.home / ".kiro" / "agents").glob("*.json")):
        try:
            nome = json.loads(p.read_text(encoding="utf-8")).get("name")
        except (OSError, ValueError):
            continue
        cod, saida = _rodar(run, ["kiro-cli", "agent", "validate", "--path", str(p)], timeout=20)
        if cod == 0:
            out.append((OK, f"kiro agent validate {nome}"))
        elif "log in" in saida:
            return [(AVISO, "kiro-cli sem login: não deu para validar os agentes (kiro-cli login)")]
        else:
            out.append((FALHA, f"kiro agent validate {nome}: {saida.strip()[:160]}"))
    return out


def formatar(resultados):
    return "\n".join(f"{nivel:<6} {texto}" for nivel, texto in resultados)
