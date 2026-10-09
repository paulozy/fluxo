"""`fluxo vigia`: olha as suas PRs/MRs e avisa das novidades. Só avisa: nunca faz merge, nunca comenta,
nunca abre sessão.

    fluxo vigia tick       # uma passada (o launchd, o systemd ou o loop do WSL chamam)
    fluxo vigia            # status: último tick, erro, últimos avisos
    fluxo vigia ligar      # Mac: launchctl; Linux: systemctl --user; WSL: janela "vigia" no tmux
    fluxo vigia desligar

O primeiro tick é a linha de base: marca tudo o que já existe como visto e não avisa nada. Depois,
cada novidade tem uma chave (review:<ref>:<id>, aprovada:<ref>@<sha>:<autor>, ci:<ref>@<sha>:<estado>,
merge:<ref>), avisada uma vez só. Sem rede (a VPN desligada), o erro é registrado uma vez e o próximo
tick tenta de novo.
"""
import fcntl
import json
import os
import plistlib
import subprocess
import time

from . import avisar, forge, tmux

ROTULO = "dev.fluxo.vigia"
GUARDAR = 30 * 86400  # chaves vistas há mais que isso são esquecidas
MAX_AVISOS = 4  # numa leva maior, um aviso só com o total (o detalhe fica no avisos.jsonl)


def _ler_estado(caminho):
    """(estado, corrompido)."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, False
    except ValueError:
        return {}, True
    return (dados, False) if isinstance(dados, dict) else ({}, True)


def _gravar(caminho, dados):
    tmp = caminho.with_name(f".{caminho.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, caminho)


def _registrar(estado_dir, linha):
    with open(estado_dir / "avisos.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(linha, ensure_ascii=False) + "\n")


def _forge(cfg, run):
    if cfg["forge"] == "github":
        return forge.GitHub(run=run)
    return forge.GitLab((cfg.get("gitlab") or {}).get("host", ""), run=run)


def tick(cfg, cam, run=subprocess.run, avisar_fn=None, agora=None):
    """Uma passada. Devolve (código, resumo); o código só não é 0 em bug, para o agendador não marcar
    falha a cada VPN desligada."""
    agora = agora or time.time()
    avisar_fn = avisar_fn or (lambda titulo, texto: avisar.avisar(cfg["so"], titulo, texto))
    cam.estado.mkdir(parents=True, exist_ok=True)
    caminho = cam.estado / "vigia.json"
    with open(cam.estado / "vigia.lock", "w") as trava:
        try:
            fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0, "outro tick está rodando"
        estado, corrompido = _ler_estado(caminho)
        if corrompido:
            os.replace(caminho, caminho.with_name(f"vigia.corrupt-{int(agora)}.json"))
            avisar_fn("fluxo vigia", "o estado estava corrompido: recomecei pela linha de base (nada é avisado duas vezes)")
        try:
            login, prs = _forge(cfg, run).coletar(**({"abertas_antes": estado.get("abertas", [])}
                                                     if cfg["forge"] == "gitlab" else {}))
        except forge.ErroForge as exc:
            if estado.get("erro") != str(exc):  # registra uma vez; a cada 5 min seria ruído
                _registrar(cam.estado, {"ts": agora, "tipo": "erro", "texto": str(exc)})
            estado.update(erro=str(exc), ultimo_tick=agora)
            _gravar(caminho, estado)
            return 0, f"sem resposta do forge: {exc}"
        # o que é mais velho que a memória das chaves nem entra: uma PR antiga que volta à lista (alguém
        # comentou) não pode reavisar o merge de meses atrás
        evs = forge.eventos(prs, login, max(estado.get("desde", agora), agora - GUARDAR))
        vistos = estado.get("vistos", {})
        primeira = "desde" not in estado
        novos = [] if primeira else [e for e in evs if e["chave"] not in vistos]
        for e in evs:
            vistos[e["chave"]] = agora  # a última vez vista: a poda só esquece o que sumiu do forge
        for e in novos:
            _registrar(cam.estado, {"ts": agora, "tipo": e["tipo"], "ref": e["ref"], "texto": e["texto"],
                                    "titulo": e["titulo"], "url": e["url"]})
        if len(novos) > MAX_AVISOS:
            avisar_fn("fluxo vigia", f"{len(novos)} novidades nas suas {('PRs' if cfg['forge'] == 'github' else 'MRs')}: fluxo vigia")
        else:
            for e in novos:
                avisar_fn(f"{e['ref']}: {e['texto']}", e["titulo"])
        estado.update(
            desde=estado.get("desde", agora), ultimo_tick=agora, erro=None, login=login,
            # "desconhecida": a MR saiu da lista e o detalhe falhou (rede); fica para o próximo tick ver o merge
            abertas=sorted(p["ref"] for p in prs if p["estado"] in ("aberta", "desconhecida")),
            vistos={k: v for k, v in vistos.items() if agora - v < GUARDAR},
        )
        _gravar(caminho, estado)
    if primeira:
        return 0, f"linha de base: {len(evs)} itens já existentes marcados como vistos"
    return 0, f"{len(novos)} novidade(s)"


def status(cam, agora=None, n=8):
    agora = agora or time.time()
    estado, _ = _ler_estado(cam.estado / "vigia.json")
    if not estado:
        return "vigia: nenhum tick ainda (fluxo vigia tick, ou fluxo vigia ligar)"
    linhas = [f"vigia: último tick há {int((agora - estado.get('ultimo_tick', agora)) // 60)} min"
              + (f" · login {estado['login']}" if estado.get("login") else "")
              + f" · {len(estado.get('abertas', []))} aberta(s)"]
    if estado.get("erro"):
        linhas.append(f"  erro: {estado['erro']}")
    try:
        avisos = (cam.estado / "avisos.jsonl").read_text(encoding="utf-8").splitlines()[-n:]
    except FileNotFoundError:
        avisos = []
    for linha in avisos:
        try:
            a = json.loads(linha)
        except ValueError:
            continue
        quando = time.strftime("%d/%m %H:%M", time.localtime(a.get("ts", 0)))
        linhas.append(f"  {quando} {a.get('ref', '')} {a.get('texto', '')}"[:110])
    return "\n".join(linhas)


def arquivos_extras(cfg, ctx, Arquivo, **_):
    """O agendamento do tick no Mac (launchd) e no Linux (systemd). Sem caminho absoluto: os arquivos
    chamam o ~/.local/bin/fluxo pelo $HOME (sh -c) ou pelo %h, e o golden fica determinístico."""
    intervalo = cfg["vigia"]["intervalo"]
    if cfg["so"] == "mac":
        plist = {
            "Label": ROTULO,
            "ProgramArguments": ["/bin/sh", "-c", 'exec "$HOME/.local/bin/fluxo" vigia tick'],
            "StartInterval": intervalo,
            "RunAtLoad": True,
            # o launchd não lê o seu shell: o PATH tem de achar gh/glab do Homebrew
            "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "LANG": "en_US.UTF-8"},
        }
        return [Arquivo(f"Library/LaunchAgents/{ROTULO}.plist", plistlib.dumps(plist, sort_keys=True))], []
    if cfg["so"] == "linux":
        servico = ("[Unit]\nDescription=fluxo vigia (avisa das suas PRs/MRs)\n\n[Service]\nType=oneshot\n"
                   "ExecStart=%h/.local/bin/fluxo vigia tick\nTimeoutStartSec=5min\n")
        timer = (f"[Unit]\nDescription=fluxo vigia a cada {intervalo} s\n\n[Timer]\nOnActiveSec=1min\n"
                 f"OnUnitActiveSec={intervalo}s\nAccuracySec=30s\n\n[Install]\nWantedBy=timers.target\n")
        return [Arquivo(".config/systemd/user/fluxo-vigia.service", servico.encode()),
                Arquivo(".config/systemd/user/fluxo-vigia.timer", timer.encode())], []
    return [], ["vigia no WSL: o systemd não segura a instância; `fluxo vigia ligar` abre um loop numa janela do tmux"]


def ligar(cfg, cam, run=subprocess.run, alvo=None, dentro_do_tmux=False):
    """(código, mensagem). Só roda o comando do sistema; os arquivos vêm do `fluxo install`."""
    if cfg["so"] == "mac":
        plist = cam.home / "Library" / "LaunchAgents" / f"{ROTULO}.plist"
        if not plist.exists():
            return 2, f"{plist} não existe: ligue o módulo e rode `fluxo install` antes"
        dominio = f"gui/{os.getuid()}"
        run(["launchctl", "bootout", f"{dominio}/{ROTULO}"], capture_output=True)  # se já estava ligado
        r = run(["launchctl", "bootstrap", dominio, str(plist)], capture_output=True, text=True)
        return (0, "vigia ligado (launchd)") if r.returncode == 0 else (1, f"launchctl: {r.stderr.strip()}")
    if cfg["so"] == "linux":
        for argv in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", "fluxo-vigia.timer"]):
            r = run(argv, capture_output=True, text=True)
            if r.returncode:
                return 1, f"{' '.join(argv)}: {r.stderr.strip()}"
        return 0, "vigia ligado (systemd --user)"
    if not dentro_do_tmux and not alvo:
        return 3, "no WSL o vigia roda numa janela do tmux: rode dentro do tmux, ou com --target <sessão>:"
    cam.estado.mkdir(parents=True, exist_ok=True)  # antes de abrir: sem o registro, o loop ficaria órfão
    janela, _ = _ler_estado(cam.estado / "vigia-janela.json")
    if janela and tmux.pane_vivo(janela.get("pane"), janela.get("pane_pid"), tmux.panes_vivos(run)):
        return 0, f"o vigia já está ligado na janela {janela.get('janela')}"
    comando = [os.path.expanduser("~/.local/bin/fluxo"), "vigia", "loop", "--intervalo", str(cfg["vigia"]["intervalo"])]
    try:
        janela, pane, pid = tmux.abrir_janela("vigia", cam.home, comando, alvo, run)
    except tmux.ErroTmux as exc:
        return 3, str(exc)
    _gravar(cam.estado / "vigia-janela.json", {"janela": janela, "pane": pane, "pane_pid": pid})
    return 0, f"vigia ligado na janela {janela} do tmux"


def desligar(cfg, cam, run=subprocess.run):
    if cfg["so"] == "mac":
        r = run(["launchctl", "bootout", f"gui/{os.getuid()}/{ROTULO}"], capture_output=True, text=True)
        return (0, "vigia desligado") if r.returncode == 0 else (1, f"launchctl: {r.stderr.strip()}")
    if cfg["so"] == "linux":
        r = run(["systemctl", "--user", "disable", "--now", "fluxo-vigia.timer"], capture_output=True, text=True)
        return (0, "vigia desligado") if r.returncode == 0 else (1, f"systemctl: {r.stderr.strip()}")
    janela, _ = _ler_estado(cam.estado / "vigia-janela.json")
    if not janela:
        return 0, "não há janela do vigia registrada"
    # só fecha a janela que o próprio `ligar` abriu, e só se o pane ainda for o mesmo processo
    if not tmux.pane_vivo(janela.get("pane"), janela.get("pane_pid"), tmux.panes_vivos(run)):
        (cam.estado / "vigia-janela.json").unlink()
        return 0, "a janela do vigia já tinha fechado"
    r = run(["tmux", "kill-pane", "-t", janela["pane"]], capture_output=True, text=True)
    (cam.estado / "vigia-janela.json").unlink()
    return (0, "vigia desligado") if r.returncode == 0 else (1, f"tmux: {r.stderr.strip()}")


def loop(cfg, cam, intervalo, dormir=time.sleep, voltas=None, tick=tick):
    """O vigia do WSL: um tick a cada `intervalo` segundos, numa janela do tmux."""
    n = 0
    while voltas is None or n < voltas:
        try:
            _, resumo = tick(cfg, cam)
        except Exception as exc:  # noqa: BLE001 — um bug num tick não pode fechar a janela do vigia
            resumo = f"erro no tick: {exc!r}"
        print(f"{time.strftime('%H:%M')} {resumo}", flush=True)
        n += 1
        if voltas is None or n < voltas:
            dormir(intervalo)
    return 0
