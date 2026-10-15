"""Painel: os hooks das ferramentas gravam eventos, e o `fluxo panel` mostra as sessões ao vivo.

O hook (`fluxo hook --tool F --event E`) roda em todo evento de toda sessão:
- nunca imprime nada (no Kiro, o stdout do SessionStart entra no contexto do agente);
- sempre sai com 0 (no Copilot, um preToolUse que falha nega a ferramenta; por isso ele nem é usado);
- grava uma linha com um único os.write num fd O_APPEND, para hooks concorrentes não intercalarem;
- erro vai para estado/hook-erros.log.

Eventos normalizados: inicio, turno (o usuário mandou prompt), ferramenta, espera (pergunta ou
permissão: só o Copilot avisa), fim_turno, fim.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from . import task, tmux

EVENTOS = {
    "copilot": {"sessionStart": "inicio", "userPromptSubmitted": "turno", "postToolUse": "ferramenta",
                "notification": "espera", "permissionRequest": "espera", "agentStop": "fim_turno",
                "sessionEnd": "fim"},
    # V3 em PascalCase; V2 (embutido no agente) em camelCase
    "kiro": {"SessionStart": "inicio", "agentSpawn": "inicio", "UserPromptSubmit": "turno",
             "userPromptSubmit": "turno", "PostToolUse": "ferramenta", "postToolUse": "ferramenta",
             "Stop": "fim_turno", "stop": "fim_turno", "SessionEnd": "fim"},
    # sem PermissionRequest: a doc não diz se um hook que falha ou estoura o tempo ali nega a ação
    "codex": {"SessionStart": "inicio", "UserPromptSubmit": "turno", "PostToolUse": "ferramenta",
              "Stop": "fim_turno", "SessionEnd": "fim"},
}
PATCH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
DIA = re.compile(r"^eventos-\d{8}\.jsonl$")
PARADA = 30 * 60
SOME = 60 * 60  # sessão que acabou some do painel depois disso
ORDEM = {"pergunta": 0, "sua vez": 1, "trabalhando": 2, "parada": 3, "acabou": 4}


def _limpo(texto, n=80):
    """Uma linha, sem caracteres de controle: o --text e a tela imprimem isso no terminal."""
    return "".join(c for c in str(texto).splitlines()[0] if c.isprintable())[:n] if texto else ""


def _texto(valor):
    patch = PATCH.search(valor)  # o apply_patch do Codex: o primeiro arquivo, e não o cabeçalho
    return _limpo(patch.group(1) if patch else valor)


def _alvo(entrada):
    """Uma linha curta do que a ferramenta fez: o comando, o caminho, ou o primeiro texto."""
    if isinstance(entrada, str):
        try:
            entrada = json.loads(entrada)
        except ValueError:
            return _texto(entrada)
    if not isinstance(entrada, dict):
        return ""
    for chave in ("command", "cmd", "path", "file_path", "filePath", "pattern", "url", "query"):
        valor = entrada.get(chave)
        if isinstance(valor, str) and valor:
            return _texto(valor)
    for valor in entrada.values():
        if isinstance(valor, str) and valor:
            return _texto(valor)
    return ""


def normalizar(ferramenta, evento, payload, env=None, agora=None):
    env = os.environ if env is None else env
    p = payload if isinstance(payload, dict) else {}
    evento = evento or p.get("hook_event_name") or p.get("hookEventName") or ""
    pane = env.get("TMUX_PANE") or ""
    return {
        "ts": agora or time.time(), "ferramenta": ferramenta,
        # sem id no payload (a doc não garante o campo), o pane identifica a sessão
        "sessao": p.get("sessionId") or p.get("session_id") or (f"pane-{pane}" if pane else ""),
        "evento": EVENTOS.get(ferramenta, {}).get(evento, evento),
        "tool": p.get("toolName") or p.get("tool_name") or "",
        "alvo": _alvo(p.get("toolArgs") if "toolArgs" in p else p.get("tool_input")),
        "cwd": p.get("cwd") or "",
        "pane": pane,
    }


def gravar_evento(estado, registro):
    # 700/600: a primeira linha de cada comando da ferramenta pode ter um token
    estado.mkdir(mode=0o700, parents=True, exist_ok=True)
    caminho = estado / time.strftime("eventos-%Y%m%d.jsonl", time.localtime(registro["ts"]))
    linha = (json.dumps(registro) + "\n").encode()  # ASCII: um surrogate solto não derruba o evento
    fd = os.open(str(caminho), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, linha)
    finally:
        os.close(fd)


def hook(estado, ferramenta, evento, stdin=None, env=None):
    """O que o `fluxo hook` faz. Nunca levanta: qualquer erro vira uma linha em hook-erros.log."""
    try:
        bruto = (stdin or sys.stdin).read()
        try:
            payload = json.loads(bruto) if bruto.strip() else {}
        except ValueError:
            payload = {}
        registro = normalizar(ferramenta, evento, payload, env)
        if not registro["sessao"]:  # nem id nem pane: o painel não teria como mostrar
            raise ValueError("evento sem id de sessão e sem TMUX_PANE")
        gravar_evento(estado, registro)
    except Exception as exc:  # noqa: BLE001 — o hook não pode falhar nunca
        try:
            estado.mkdir(parents=True, exist_ok=True)
            with open(estado / "hook-erros.log", "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {ferramenta} {evento}: {exc!r}\n")
        except Exception:  # noqa: BLE001
            pass
    return 0


def ler_eventos(estado, dias=2, agora=None):
    """Os eventos de hoje e de ontem, pela data do nome do arquivo (não os dois últimos arquivos: depois de
    uma semana parado, eles seriam de sessões velhas)."""
    agora = agora or time.time()
    datas = {time.strftime("eventos-%Y%m%d.jsonl", time.localtime(agora - d * 86400)) for d in range(dias)}
    arquivos = sorted(p for p in Path(estado).glob("eventos-*.jsonl") if DIA.match(p.name) and p.name in datas)
    for p in arquivos:
        try:
            linhas = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for linha in linhas:
            try:
                r = json.loads(linha)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("sessao"):
                yield r


def sessoes(eventos, tarefas=(), agora=None, vivos=None):
    """Uma linha por sessão, com o estado de agora. `tarefas`: registros do tasks.jsonl (nome pelo pane).
    `vivos`: os panes vivos do tmux; a sessão cujo pane morreu acabou, mesmo sem o evento de fim."""
    agora = agora or time.time()
    por_pane = {t.get("pane"): t.get("nome") for t in tarefas if t.get("pane")}
    s = {}
    for e in eventos:
        atual = s.setdefault(e["sessao"], {"sessao": e["sessao"], "ferramenta": e.get("ferramenta"), "inicio": e["ts"],
                                           "tool": "", "alvo": "", "cwd": "", "pane": ""})
        atual.update(ultimo=e["ts"], evento=e.get("evento"))
        for chave in ("cwd", "pane"):
            if e.get(chave):
                atual[chave] = e[chave]
        if e.get("evento") == "ferramenta":
            atual.update(tool=e.get("tool", ""), alvo=e.get("alvo", ""))
    out = []
    for x in s.values():
        evento, ha = x.get("evento"), agora - x["ultimo"]
        pane_morto = vivos is not None and x["pane"] and x["pane"] not in {p for p, _ in vivos}
        if evento == "fim" or pane_morto:
            if ha > SOME:
                continue
            estado = "acabou"
        elif evento == "espera":
            estado = "pergunta"
        elif evento in ("fim_turno", "inicio"):  # nova, esperando o primeiro prompt, também é a sua vez
            estado = "parada" if ha > PARADA else "sua vez"
        else:
            estado = "trabalhando"
        nome = por_pane.get(x["pane"]) or (Path(x["cwd"]).name if x["cwd"] else x["sessao"][:8])
        out.append(dict(x, estado=estado, nome=nome, ha=ha))
    out.sort(key=lambda x: (ORDEM[x["estado"]], -x["ultimo"]))
    return out


def _tempo(segundos):
    m = int(segundos // 60)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d}"


def linhas_texto(lista, largura=100):
    if not lista:
        return ["nenhuma sessão com eventos hoje (os hooks estão instalados? fluxo panel enable)"]
    cab = f"{'sessão':<18} {'ferramenta':<10} {'estado':<11} {'há':>8}  {'pane':<5} última ação"
    out = [cab[:largura], "-" * min(largura, len(cab) + 20)]
    for x in lista:
        acao = f"{x['tool']} {x['alvo']}".strip()
        linha = f"{x['nome'][:18]:<18} {(x['ferramenta'] or '')[:10]:<10} {x['estado']:<11} {_tempo(x['ha']):>8}  {x['pane'][:5]:<5} {acao}"
        out.append(linha[:largura])
    return out


def ver(cam, agora=None, run=None):
    tarefas = task.ler_tasks(cam.estado)
    vivos = tmux.panes_vivos(run) if run else tmux.panes_vivos()
    return sessoes(ler_eventos(cam.estado, agora=agora), tarefas, agora, vivos=vivos or None)


def pular(pane, run):
    """Leva o seu client do tmux até o pane. Só seleciona: nunca digita nada (send-keys)."""
    r = run(["tmux", "switch-client", "-t", pane], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()


def tela(cam, run, intervalo=1.0):
    """A casca em curses: ↑↓ escolhe, Enter vai até a sessão, h esconde, q sai."""
    import curses

    escondidas, escolhida = set(), None  # o id da sessão: a lista se reordena a cada segundo

    def principal(scr):
        nonlocal escolhida
        curses.curs_set(0)
        scr.timeout(int(intervalo * 1000))
        nota = ""
        while True:
            lista = [x for x in ver(cam, run=run) if x["sessao"] not in escondidas]
            ids = [x["sessao"] for x in lista]
            i = ids.index(escolhida) if escolhida in ids else 0
            escolhida = ids[i] if ids else None
            altura, largura = scr.getmaxyx()
            scr.erase()
            linhas = linhas_texto(lista, largura - 1)
            for n, linha in enumerate(linhas[: altura - 2]):
                atributo = curses.A_REVERSE if lista and n - 2 == i else curses.A_NORMAL
                if n == 0:
                    atributo = curses.A_BOLD
                scr.addnstr(n, 0, linha, largura - 1, atributo)
            scr.addnstr(altura - 1, 0, (nota or "↑↓ escolher · Enter ir até a sessão · h esconder · q sair"), largura - 1, curses.A_DIM)
            scr.refresh()
            tecla = scr.getch()
            if tecla in (ord("q"), 27):
                return
            if tecla == curses.KEY_UP and ids:
                escolhida = ids[max(0, i - 1)]
            elif tecla == curses.KEY_DOWN and ids:
                escolhida = ids[min(len(ids) - 1, i + 1)]
            elif tecla in (10, 13, curses.KEY_ENTER) and lista:
                alvo = lista[i]
                ok, erro = pular(alvo["pane"], run) if alvo["pane"] else (False, "a sessão não tem pane do tmux")
                nota = "" if ok else f"não deu para ir até a sessão: {erro}"
            elif tecla == ord("h") and lista:
                escondidas.add(lista[i]["sessao"])

    curses.wrapper(principal)
    return 0


# ── o que o `install` grava com o módulo panel ligado ──────────────────────

def _comando(ferramenta, evento):
    return f'"$HOME/.local/bin/fluxo" hook --tool {ferramenta} --event {evento}'


def arquivos_da_ferramenta(cfg, ferr, Arquivo, skills=(), **_):
    """Os hooks de uma ferramenta (o `montar` chama uma vez para cada uma de `tools`)."""
    if ferr == "copilot":
        hooks = {ev: [{"type": "command", "bash": _comando("copilot", ev), "timeoutSec": 5}]
                 for ev in EVENTOS["copilot"]}  # nunca preToolUse: no Copilot ele é fail-closed
        texto = json.dumps({"version": 1, "hooks": hooks}, indent=2) + "\n"
        return [Arquivo(".copilot/hooks/fluxo.json", texto.encode())], []
    if ferr == "kiro" and cfg["kiro"]["engine"] == "v3":
        hooks = [{"name": f"fluxo-{ev}", "trigger": ev, **({"matcher": "*"} if ev == "PostToolUse" else {}),
                  "action": {"type": "command", "command": _comando("kiro", ev)}, "timeout": 5}
                 for ev in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")]
        texto = json.dumps({"version": "v1", "hooks": hooks}, indent=2) + "\n"
        return [Arquivo(".kiro/hooks/fluxo.json", texto.encode())], []
    if ferr == "kiro":
        # o V2 só lê hooks embutidos num agente: o agente `fluxo` vira o padrão (`fluxo panel enable`) e
        # carrega o steering e as skills pelos resources
        hooks = {ev: [dict({"command": _comando("kiro", ev)}, **({"matcher": "*"} if ev == "postToolUse" else {}))]
                 for ev in ("agentSpawn", "userPromptSubmit", "postToolUse", "stop")}
        agente = {"name": "fluxo", "description": "Agente padrão do fluxo: as instruções e as skills do fluxo, com os hooks do painel.",
                  "prompt": "Siga as instruções do fluxo (steering fluxo.md) e use as skills dele.",
                  "tools": ["*"],
                  "includeMcpJson": True,  # como agente padrão, não pode tirar os MCPs do usuário (a validar)
                  "resources": ["file://~/.kiro/steering/fluxo.md"] + [f"skill://~/.kiro/skills/{s['name']}/SKILL.md" for s in skills],
                  "hooks": hooks}
        return [Arquivo(".kiro/agents/fluxo.json", (json.dumps(agente, ensure_ascii=False, indent=2) + "\n").encode())], []
    if ferr == "codex":
        # https://learn.chatgpt.com/docs/hooks: payload em JSON no stdin; o SessionEnd aceita até 3 s.
        # Em ordem fixa: o Codex aprova o hook pelo hash, e um arquivo que muda pede nova aprovação
        hooks = {ev: [{"hooks": [{"type": "command", "command": _comando("codex", ev),
                                  "timeout": 3 if ev == "SessionEnd" else 5}]}]
                 for ev in EVENTOS["codex"]}
        texto = json.dumps({"hooks": hooks}, indent=2) + "\n"
        return [Arquivo(".codex/hooks.json", texto.encode())], []
    return [], []  # Claude: o requer do modulo.json o deixa de fora; lá o painel é o agent-tree


def avisos_do_install(removidos, cam, gravados=()):
    """O que o install precisa dizer sobre os hooks.

    - Vai apagar o agente `fluxo` do Kiro, que o `panel enable` tinha feito padrão: sem o `disable`, o
      Kiro ficaria com um agente padrão que não existe.
    - Vai criar ou mudar o `hooks.json` do Codex: lá, hook novo ou mudado só roda depois de aprovado."""
    avisos = []
    if ".kiro/agents/fluxo.json" in removidos and (cam.estado / "painel-agente-anterior.txt").exists():
        avisos.append("o agente fluxo é o padrão do Kiro (fluxo panel enable): rode fluxo panel disable para devolver o anterior")
    if ".codex/hooks.json" in gravados:
        avisos.append("hooks do panel no Codex: aprove-os em /hooks na próxima sessão (de novo a cada mudança do fluxo)")
    return avisos


def ligar(cfg, cam, run):
    """O Kiro V2 precisa de um passo: tornar o agente `fluxo` o padrão (e guardar o anterior). O Codex,
    de uma aprovação em /hooks, que é do usuário."""
    codex = "; no Codex, aprove os hooks em /hooks" if "codex" in cfg["tools"] else ""
    if not ("kiro" in cfg["tools"] and cfg["kiro"]["engine"] == "v2"):
        return 0, "nada a ligar: os hooks já valem a partir da próxima sessão" + codex
    anterior = (run(["kiro-cli", "settings", "chat.defaultAgent"], capture_output=True, text=True).stdout or "").strip()
    cam.estado.mkdir(parents=True, exist_ok=True)
    guardado = cam.estado / "painel-agente-anterior.txt"
    if anterior != "fluxo" and not guardado.exists():  # ligar duas vezes não pode guardar o próprio fluxo
        guardado.write_text(anterior)
    r = run(["kiro-cli", "agent", "set-default", "fluxo"], capture_output=True, text=True)
    return (0, "agente fluxo é o padrão do Kiro agora" + codex) if r.returncode == 0 else (1, f"kiro-cli: {r.stderr.strip()}")


def desligar(cfg, cam, run):
    """Devolve o agente padrão que o `ligar` guardou. Decide pelo que foi guardado, e não pela config:
    depois de tirar o kiro de `tools`, o engine v2 ou o módulo, o padrão do Kiro ainda é o `fluxo`."""
    guardado = cam.estado / "painel-agente-anterior.txt"
    if not guardado.exists():  # sem `ligar` antes, não há o que devolver: nunca apagar o padrão do usuário
        if "kiro" in cfg["tools"] and (cfg.get("kiro") or {}).get("engine") == "v2":
            return 0, "o painel não tinha mudado o agente padrão"
        return 0, "nada a desligar: para tirar os hooks, fluxo modules --disable panel && fluxo install"
    anterior = guardado.read_text().strip()
    if not re.match(r"^[\w.-]+$", anterior):  # ex.: "No value set": não é nome de agente
        anterior = ""
    argv = ["kiro-cli", "agent", "set-default", anterior] if anterior else ["kiro-cli", "settings", "--delete", "chat.defaultAgent"]
    r = run(argv, capture_output=True, text=True)
    if r.returncode:
        return 1, f"kiro-cli: {r.stderr.strip()}"
    guardado.unlink()
    return 0, f"agente padrão de volta: {anterior or 'o do Kiro'}"
