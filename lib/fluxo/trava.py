"""`fluxo trava`: uma sessão por vez no stack local (portas, memória e containers não se dividem).

Mais simples que o stack-lock daqui: não há processo segurando flock. O dono fica num arquivo, gravado
sob um flock curto, e o dono que morreu é ignorado.

Dono vivo:
- pelo pid da ferramenta, quando há: o CLAUDE_PID, ou o avô deste processo (a ferramenta → o shell
  que ela abriu → o fluxo). Ele vem com a hora de início (`ps -o lstart`), para um pid reaproveitado
  não passar por vivo. O pid vale antes do pane: um Claude morto num pane de zsh deixa o pane vivo;
- sem pid, pelo pane do tmux, comparado pelo par (pane_id, pane_pid), porque os %N recomeçam quando
  o servidor do tmux reinicia.

    fluxo trava pegar --id PROJ-12 --projeto api-proj-12   # 0 pegou (ou já era sua); 4 ocupada
    fluxo trava soltar
    fluxo trava status
    fluxo trava quebrar [--forcar]                          # só com o dono morto, ou forçando
    fluxo trava rodar --id PROJ-12 -- <comando>             # pega, roda e solta
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager

from . import tmux

OCUPADA = 4


class ErroTrava(Exception):
    pass


def _saida(run, argv):
    try:
        r = run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _pid_existe(pid):
    try:
        os.kill(int(pid), 0)
    except PermissionError:  # existe, mas é de outro usuário
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def inicio_do_pid(pid, run=subprocess.run):
    """A hora de início do processo (igual no Mac e no Linux), None se ele não existe, ou "" se vive mas
    não há `ps` para saber a hora (a imagem slim do Docker não tem): aí vale só o os.kill(pid, 0)."""
    try:
        r = run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "" if _pid_existe(pid) else None
    return (r.stdout.strip() or "") if r.returncode == 0 else None


def pid_da_ferramenta(env=None, run=subprocess.run, getppid=os.getppid):
    """O CLAUDE_PID, ou o pai do nosso pai: a ferramenta abre um shell para rodar o comando, e o shell
    roda o fluxo. Se a ferramenta usar um shell persistente, este é o pid do shell, que morre com ela."""
    env = os.environ if env is None else env
    if env.get("CLAUDE_PID", "").isdigit():
        return int(env["CLAUDE_PID"])
    avo = _saida(run, ["ps", "-o", "ppid=", "-p", str(getppid())])
    return int(avo) if avo and avo.isdigit() and int(avo) > 1 else None


def pane_atual(env=None, run=subprocess.run):
    """(pane_id, pane_pid) do TMUX_PANE herdado, ou (None, None)."""
    env = os.environ if env is None else env
    pane = env.get("TMUX_PANE")
    if not pane:
        return None, None
    return pane, _saida(run, ["tmux", "display-message", "-p", "-t", pane, "#{pane_pid}"])


def quem(sessao=None, env=None, pane=None, pane_pid=None):
    """A identidade da sessão: explícita, a do Claude, ou o pane (o par, para sobreviver ao servidor do
    tmux reiniciando e reusando o %N)."""
    env = os.environ if env is None else env
    sessao = sessao or env.get("FLUXO_SESSAO") or env.get("CLAUDE_CODE_SESSION_ID")
    if not sessao and pane:
        sessao = f"pane-{pane}-{pane_pid or '?'}"
    if not sessao:
        raise ErroTrava("não sei quem é esta sessão: rode dentro do tmux, ou passe --sessao (ou FLUXO_SESSAO)")
    return sessao


def dono_vivo(dono, vivos, inicio):
    if dono.get("pid"):
        agora = inicio(dono["pid"])
        if agora is None:
            return False
        # sem hora de um dos lados (sem ps), não dá para comparar: vale o processo existir
        return not (agora and dono.get("pid_inicio")) or agora == dono["pid_inicio"]
    return tmux.pane_vivo(dono.get("pane"), dono.get("pane_pid"), vivos)


class Trava:
    def __init__(self, estado, run=subprocess.run):
        self.pasta = estado / "trava"
        self.run = run

    @contextmanager
    def _travado(self):
        self.pasta.mkdir(parents=True, exist_ok=True)
        with open(self.pasta / "trava.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            yield

    def ler(self):
        try:
            dono = json.loads((self.pasta / "dono.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return dono if isinstance(dono, dict) and dono.get("sessao") else None

    def _gravar(self, dono):
        tmp = self.pasta / f".dono.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(dono, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.pasta / "dono.json")

    def _apagar(self):
        try:
            (self.pasta / "dono.json").unlink()
        except FileNotFoundError:
            pass

    def vivo(self, dono):
        return dono_vivo(dono, tmux.panes_vivos(self.run), lambda pid: inicio_do_pid(pid, self.run))

    def pegar(self, sessao, pane=None, pane_pid=None, pid=None, id_=None, projeto=None, agora=None):
        """(pegou, dono, nova): pegou também quando já era sua (nova=False); ocupada é (False, dono atual, False)."""
        if pid is not None and int(pid) < 2:
            raise ErroTrava(f"pid {pid} inválido")
        if not pid and not (pane and pane_pid):
            raise ErroTrava("sem pid da ferramenta nem pane do tmux: não teria como saber se o dono morreu, "
                            "e a trava ficaria presa")
        marca = {"pane": pane, "pane_pid": pane_pid, "pid": pid,
                 "pid_inicio": inicio_do_pid(pid, self.run) if pid else None}
        with self._travado():
            dono = self.ler()
            if dono and dono["sessao"] == sessao:
                if any(dono.get(k) != v for k, v in marca.items()):  # retomada em outro pane/processo
                    dono.update(marca)
                    self._gravar(dono)
                return True, dono, False
            if dono and self.vivo(dono):
                return False, dono, False
            novo = dict(marca, sessao=sessao, id=id_, projeto=projeto, desde=agora or time.time())
            self._gravar(novo)
            return True, novo, True

    def soltar(self, sessao):
        with self._travado():
            dono = self.ler()
            if not dono:
                return True, None
            if dono["sessao"] != sessao:
                return False, dono
            self._apagar()
            return True, dono

    def quebrar(self, forcar=False):
        with self._travado():
            dono = self.ler()
            if not dono:
                return True, None
            if self.vivo(dono) and not forcar:
                return False, dono
            self._apagar()
            return True, dono


def descrever(dono, vivo, agora=None):
    if not dono:
        return "livre"
    minutos = int(((agora or time.time()) - (dono.get("desde") or 0)) // 60)
    partes = [f"ocupada por {dono.get('id') or dono['sessao']}", f"há {minutos} min"]
    if dono.get("projeto"):
        partes.append(f"projeto {dono['projeto']}")
    if dono.get("pane"):
        partes.append(f"pane {dono['pane']}")
    if not vivo:
        partes.append("dono morto: o próximo `pegar` fica com ela")
    return ", ".join(partes)


def pegar_esperando(trava, esperar, dormir=time.sleep, relogio=time.monotonic, **kw):
    fim = relogio() + max(0, esperar)
    while True:
        ok, dono, nova = trava.pegar(**kw)
        if ok or relogio() >= fim:
            return ok, dono, nova
        dormir(min(5, max(0.5, fim - relogio())))


def rodar(trava, comando, esperar, run=subprocess.run, erro=None, **kw):
    erro = erro or sys.stderr
    ok, dono, nova = pegar_esperando(trava, esperar, **kw)
    if not ok:
        print(f"fluxo trava: {descrever(dono, True)}", file=erro)
        return OCUPADA

    def sair(sinal, _frame):  # a ferramenta mata o comando por timeout: o finally ainda solta a trava
        raise SystemExit(128 + sinal)

    antigos = {s: signal.signal(s, sair) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        return run(comando).returncode
    finally:
        for s, h in antigos.items():
            signal.signal(s, h)
        if nova:  # se já era da sessão antes do `rodar`, continua sendo depois
            trava.soltar(kw["sessao"])
