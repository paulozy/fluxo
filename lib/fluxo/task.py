"""`fluxo task`: abre uma sessão da ferramenta numa janela do tmux, em segundo plano.

    fluxo task PROJ-12                      # tarefa: a skill planejar com PROJ-12; ocupa vaga
    fluxo task PROJ-12 -p '/implementar PROJ-12'      # no Codex: '$implementar PROJ-12'
    fluxo task -p "confere os logs"         # avulsa: não ocupa vaga; nome avulsa, avulsa-b…
    fluxo task -p "/revisar" -n rev         # avulsa com nome (única por nome)
    fluxo task --list

Saídas: 0 abriu (ou só listou); 1 a ferramenta não subiu; 2 uso inválido; 3 nada abriu (sem tmux:
rode com --target); 4 sem vaga, ou o nome já tem sessão viva.
"""
import fcntl
import json
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import config as cfgmod
from . import tmux

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
NOME_RE = re.compile(r"^[a-z][a-z0-9-]{1,29}$")
RESERVADOS = ("watch", "panel")  # janelas que o próprio fluxo abre
DE_TAREFA = ("planejar", "implementar", "concluir")  # as skills que exigem o ID
OCUPADO = 4
SUBIR = 3.0  # segundos até conferir que a ferramenta não caiu ao abrir (o Copilot é Node: demora a falhar)


class ErroUso(Exception):
    pass


def ler_tasks(estado):
    try:
        linhas = (estado / "tasks.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for linha in linhas:
        try:
            r = json.loads(linha)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def vivas(estado, vivos):
    return [t for t in ler_tasks(estado) if tmux.pane_vivo(t.get("pane"), t.get("pane_pid"), vivos)]


def nome_do_prompt(prompt, prefixo="/", skills=None):
    """O nome de uma avulsa: a skill do prompt (/revisar, ou $revisar no Codex), ou None (texto livre
    vira avulsa). Só o prefixo da ferramenta conta: no Claude, `$HOME …` é texto. Com `skills`, só um
    nome delas vale: no Codex, `$HOME quebrou` é texto, e não uma sessão chamada home."""
    m = re.match(r"\s*" + re.escape(prefixo) + r"([\w:.-]+)", prompt or "")
    nome = m.group(1).split(":")[-1].lower() if m else ""
    if skills is not None and nome not in skills:
        return None
    return nome if NOME_RE.fullmatch(nome) and nome not in RESERVADOS else None


def skill_de_tarefa(prompt, prefixo="/"):
    """A skill de DE_TAREFA com que o prompt começa, ou None."""
    m = re.match(r"\s*" + re.escape(prefixo) + r"(" + "|".join(DE_TAREFA) + r")\b", prompt or "")
    return m.group(1) if m else None


def barra_no_lugar_do_prefixo(prompt, prefixo, skills):
    """No Codex a skill é `$implementar`: o `/implementar` das outras ferramentas iria como texto, e a
    sessão abriria sem rodar a skill."""
    m = re.match(r"\s*/([\w-]+)", prompt or "")
    if prefixo != "/" and m and m.group(1) in (skills or ()):
        raise ErroUso(f"nesta ferramenta a skill é chamada com {prefixo}: -p '{prefixo}{m.group(1)} …'")


def resolver(id_, prompt, nome, prefixo="/", skills=None):
    """Valida e devolve (kind, nome, prompt, sufixar). Levanta ErroUso. `prefixo` é o da chamada de
    skill na ferramenta (o `prefixo` do adaptador); `skills`, os nomes delas."""
    barra_no_lugar_do_prefixo(prompt, prefixo, skills)
    if id_ is not None:
        if not ID_RE.fullmatch(id_):
            raise ErroUso(f"id {id_!r} inválido: letras, números, ponto, hífen e sublinhado, até 40")
        if nome:
            raise ErroUso("com ID, o nome da sessão é o próprio ID: tire o -n")
        return "task", id_, prompt or f"{prefixo}planejar {id_}", False
    if not (prompt or "").strip():
        raise ErroUso("informe o ID da tarefa, ou -p \"…\" para uma sessão avulsa")
    if skill_de_tarefa(prompt, prefixo):
        raise ErroUso(f"{prefixo}planejar, {prefixo}implementar e {prefixo}concluir são de uma tarefa: "
                      "fluxo task <ID> -p '…'")
    if nome:
        nome = nome.strip().lower()
        if not NOME_RE.fullmatch(nome) or nome in RESERVADOS:
            raise ErroUso(f"nome {nome!r} inválido: minúsculas, números e hífen, de 2 a 30 ({', '.join(RESERVADOS)} são do fluxo)")
        return "avulsa", nome, prompt, False
    do_comando = nome_do_prompt(prompt, prefixo, skills if prefixo != "/" else None)
    return ("avulsa", do_comando, prompt, False) if do_comando else ("avulsa", "avulsa", prompt, True)


def argv_ferramenta(modelo, nome, sessao, prompt):
    return [a.replace("{nome}", nome).replace("{uuid}", sessao or "").replace("{prompt}", prompt) for a in modelo]


def abrir(id_, prompt, nome, cfg, tool, adapt, cam, pasta, alvo=None, forcar=False, dry_run=False, dentro_do_tmux=False,
          skills=None,
          run=subprocess.run, dormir=time.sleep, saida=None, erro=None):
    saida, erro = saida or sys.stdout, erro or sys.stderr
    kind, nome, prompt, sufixar = resolver(id_, prompt, nome, adapt["prefixo"], skills)
    pasta = Path(pasta).expanduser().resolve()  # relativa iria assim para o tasks.jsonl
    if not pasta.is_dir():  # o tmux abriria no HOME sem avisar, e o planejar rodaria lá
        raise ErroUso(f"a pasta {pasta} não existe (--dir, ou code_dir na config)")
    modelo = cfg["task"]["launch"].get(tool) or adapt["lancar"]
    cfgmod.criar_estado(cam.estado)
    with open(cam.estado / "task.lock", "w") as trava:  # dois `fluxo task` juntos não passam juntos pela conta
        fcntl.flock(trava, fcntl.LOCK_EX)
        vivos = tmux.panes_vivos(run)
        ativas = vivas(cam.estado, vivos)
        nomes = {t.get("nome") for t in ativas}
        if sufixar:
            nome = next((n for n in [nome, *(f"{nome}-{c}" for c in "bcdefghijklmnopqrstuvwxyz")] if n not in nomes), nome)
        if nome in nomes:
            viva = next(t for t in ativas if t.get("nome") == nome)
            print(f"fluxo task: já há uma sessão {nome!r} viva ({viva.get('ferramenta') or '?'}); vá até ela "
                  "(fluxo panel ou tmux), ou feche-a antes de abri-la de novo", file=erro)
            return OCUPADO
        tarefas = [t for t in ativas if t.get("kind") == "task"]
        if kind == "task" and len(tarefas) >= cfg["task"]["max"] and not forcar:
            print(f"fluxo task: já há {len(tarefas)} tarefas abertas ({', '.join(t['nome'] for t in tarefas)}); "
                  f"o limite é {cfg['task']['max']} (task.max). --force abre mesmo assim.", file=erro)
            return OCUPADO
        sessao = str(uuid.uuid4()) if any("{uuid}" in a for a in modelo) else None
        comando = argv_ferramenta(modelo, nome, sessao, prompt)
        if dry_run:
            print(shlex.join(tmux.argv_janela(nome, pasta, comando, alvo)), file=saida)
            return 0
        if not dentro_do_tmux and not alvo:
            print(shlex.join(comando), file=saida)  # com aspas: o prompt pode ter crase e $()
            print("fluxo task: fora do tmux; rode o comando acima numa aba nova, ou use --target <sessão>:", file=erro)
            return 3
        if not tmux.servidor_de_pe(run):
            print("fluxo task: o servidor do tmux não está de pé; nada foi aberto", file=erro)
            return 3
        try:
            janela, pane, pane_pid = tmux.abrir_janela(nome, pasta, comando, alvo, run)
        except tmux.ErroTmux as exc:
            print(f"fluxo task: {exc}", file=erro)
            return 3
        dormir(SUBIR)
        if not tmux.pane_vivo(pane, pane_pid, tmux.panes_vivos(run)):
            print(f"fluxo task: a ferramenta não subiu na janela {janela} (flag errada? pasta sumiu?); "
                  "nada foi registrado. Rode o comando à mão para ver o erro: " + shlex.join(comando), file=erro)
            return 1
        reg = {"ts": time.time(), "id": id_, "nome": nome, "kind": kind, "ferramenta": tool,
               "sessao": sessao, "dir": str(pasta), "window": janela, "pane": pane, "pane_pid": pane_pid,
               "prompt": prompt}
        with open(cam.estado / "tasks.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(reg, ensure_ascii=False) + "\n")
    print(f"{nome}: janela {janela} (pane {pane}){', sessão ' + sessao[:8] if sessao else ''}.", file=saida)
    return 0


def listar(cam, run=subprocess.run, agora=None, saida=None):
    saida, agora = saida or sys.stdout, agora or time.time()
    ativas = vivas(cam.estado, tmux.panes_vivos(run))
    if not ativas:
        print("nenhuma sessão aberta pelo fluxo", file=saida)
        return 0
    for t in ativas:
        minutos = int((agora - (t.get("ts") or agora)) // 60)
        print(f"{t.get('nome', '?'):<20} {t.get('kind', '?'):<7} {t.get('ferramenta') or '?':<8} há {minutos:>4} min  "
              f"pane {t.get('pane')}  "
              f"{(t.get('prompt') or '')[:50]}", file=saida)
    return 0
