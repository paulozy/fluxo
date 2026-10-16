"""fluxo lock: dono vivo pelo pid (com hora de início) ou pelo par pane+pid, reentrância, rodar, CLI."""
import contextlib
import io
import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from base import RAIZ, escrever_config
from fluxo import cli, lock


class Sistema:
    """Responde ao `tmux list-panes`, ao `ps -o lstart=` e ao `tmux display-message` como o sistema faria."""

    def __init__(self, panes=(), pids=None):
        self.panes = set(panes)  # {(pane, pane_pid)}
        self.pids = dict(pids or {})  # pid → hora de início

    def __call__(self, argv, **kw):
        if argv[:2] == ["tmux", "list-panes"]:
            return subprocess.CompletedProcess(argv, 0, "".join(f"{p} {q} 0\n" for p, q in sorted(self.panes)), "")
        if argv[:3] == ["ps", "-o", "lstart="]:
            # a hora sai igual para qualquer sessão só com locale e fuso fixos
            assert kw["env"]["LC_ALL"] == "C" and kw["env"]["TZ"] == "UTC0", "ps sem LC_ALL=C/TZ=UTC0"
            pid = int(argv[-1])
            return subprocess.CompletedProcess(argv, 0 if pid in self.pids else 1, self.pids.get(pid, ""), "")
        raise AssertionError(f"comando inesperado: {argv}")


class Trava(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.estado = Path(self.tmp.name)
        self.sis = Sistema(panes={("%1", "100"), ("%2", "200")}, pids={500: "seg 09 out 01:00", 600: "seg 09 out 01:05"})
        self.t = lock.Trava(self.estado, run=self.sis, proc="/sem-proc")  # força o caminho do ps (o do Mac)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pegar_reentrante_e_ocupada(self):
        self.assertEqual(self.t.pegar("s1", pane="%1", pane_pid="100", id_="PROJ-1", agora=10)[::2], (True, True))
        self.assertEqual(self.t.pegar("s1", pane="%1", pane_pid="100")[::2], (True, False))  # já era dela
        ok, dono, _ = self.t.pegar("s2", pane="%2", pane_pid="200")
        self.assertFalse(ok)
        self.assertEqual(dono["id"], "PROJ-1")

    def test_dono_pelo_pane_morto_e_ignorado(self):
        self.t.pegar("s1", pane="%1", pane_pid="100")
        self.sis.panes.discard(("%1", "100"))  # a janela fechou
        ok, dono, nova = self.t.pegar("s2", pane="%2", pane_pid="200")
        self.assertTrue(ok and nova)

    def test_pane_reusado_por_outro_processo_nao_e_o_dono(self):
        self.t.pegar("s1", pane="%1", pane_pid="100")
        self.sis.panes = {("%1", "999"), ("%2", "200")}  # o servidor reiniciou e o %1 é outro processo
        self.assertTrue(self.t.pegar("s2", pane="%2", pane_pid="200")[0])

    def test_pid_vale_antes_do_pane(self):
        # o Claude aberto à mão num pane de zsh: morreu, mas o pane (o zsh) continua vivo
        self.t.pegar("s1", pane="%1", pane_pid="100", pid=500)
        self.assertFalse(self.t.pegar("s2", pane="%2", pane_pid="200")[0])
        del self.sis.pids[500]
        self.assertTrue(self.t.pegar("s2", pane="%2", pane_pid="200")[0])

    def test_pid_reaproveitado_nao_e_o_dono(self):
        self.t.pegar("s1", pid=500)
        self.sis.pids[500] = "ter 10 out 09:00"  # outro processo nasceu com o mesmo pid
        self.assertTrue(self.t.pegar("s2", pid=600)[0])

    def test_reentrante_em_outro_pane_atualiza_o_dono(self):
        self.t.pegar("s1", pane="%1", pane_pid="100")
        self.sis.panes.discard(("%1", "100"))
        self.t.pegar("s1", pane="%2", pane_pid="200")  # a sessão foi retomada em outro pane
        self.assertEqual(self.t.ler()["pane"], "%2")
        self.assertFalse(self.t.pegar("s3", pid=600)[0])  # e continua sendo dona

    def test_sem_ps_vale_o_processo_existir(self):
        def sem_ps(argv, **kw):
            if argv[0] == "ps":
                raise FileNotFoundError("ps")
            return self.sis(argv, **kw)

        t = lock.Trava(self.estado, run=sem_ps, proc="/sem-proc")
        t.pegar("s1", pid=os.getpid())  # vivo: o próprio teste
        self.assertFalse(t.pegar("s2", pid=os.getpid())[0])
        t.soltar("s1")
        filho = subprocess.Popen(["sleep", "30"])
        t.pegar("s1", pid=filho.pid)
        filho.kill()
        filho.wait()  # o dono morreu
        self.assertTrue(t.pegar("s2", pid=os.getpid())[0])

    def test_recusas(self):
        with self.assertRaisesRegex(lock.ErroTrava, "presa"):
            self.t.pegar("s1")
        with self.assertRaisesRegex(lock.ErroTrava, "presa"):
            self.t.pegar("s1", pane="%1")  # pane sem pane_pid não identifica
        for pid in (0, 1):
            with self.assertRaisesRegex(lock.ErroTrava, "inválido"):
                self.t.pegar("s1", pid=pid)
        with self.assertRaisesRegex(lock.ErroTrava, "não existe"):
            self.t.pegar("s1", pid=4321)  # sem processo: o dono já nasceria morto

    def test_soltar_so_a_propria(self):
        self.t.pegar("s1", pid=500)
        self.assertFalse(self.t.soltar("s2")[0])
        self.assertTrue(self.t.soltar("s1")[0])
        self.assertIsNone(self.t.ler())
        self.assertEqual(self.t.soltar("s1"), (True, None))

    def test_quebrar(self):
        self.t.pegar("s1", pid=500, projeto="api-proj-1")
        self.assertFalse(self.t.quebrar()[0])  # dono vivo
        ok, dono = self.t.quebrar(forcar=True)
        self.assertTrue(ok)
        self.assertEqual(dono["projeto"], "api-proj-1")
        self.assertIsNone(self.t.ler())

    def test_rodar_solta_so_o_que_pegou_mesmo_com_erro(self):
        class Proc:
            def __init__(self, codigo):
                self.codigo = codigo

            def wait(self):
                return self.codigo

            def poll(self):
                return self.codigo

        ok = lambda cmd: Proc(7)
        self.assertEqual(lock.rodar(self.t, ["x"], 0, popen=ok, sessao="s1", pid=500), 7)
        self.assertIsNone(self.t.ler())  # pegou para rodar: solta

        def quebra(cmd):
            raise FileNotFoundError("comando sumiu")

        with self.assertRaises(FileNotFoundError):
            lock.rodar(self.t, ["x"], 0, popen=quebra, sessao="s1", pid=500)
        self.assertIsNone(self.t.ler())  # a exceção não deixa a trava presa
        self.t.pegar("s1", pid=500)
        lock.rodar(self.t, ["x"], 0, popen=lambda c: Proc(0), sessao="s1", pid=500)
        self.assertEqual(self.t.ler()["sessao"], "s1")  # já era dela antes: continua
        err = io.StringIO()
        self.assertEqual(lock.rodar(self.t, ["x"], 0, erro=err, sessao="s2", pid=600), lock.OCUPADA)
        self.assertIn("ocupada por s1", err.getvalue())

    def test_sinal_e_repassado_e_a_trava_espera_o_comando(self):
        """O timeout da ferramenta manda SIGTERM ao fluxo: o comando recebe o sinal, termina a limpeza
        (o `docker compose down` dele) e só então a trava é solta."""
        for sinal in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sinal=sinal):
                pronto = self.estado / f"limpou-{sinal}"
                script = (f"import signal, sys, time\n"
                          f"def fim(s, f):\n    time.sleep(0.3)\n    open({str(pronto)!r}, 'w').close()\n    sys.exit(0)\n"
                          f"signal.signal({int(sinal)}, fim)\nprint('ok', flush=True)\ntime.sleep(10)\n")
                filho = []

                def popen(cmd):
                    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
                    p.stdout.readline()  # o filho já instalou o handler
                    filho.append(p)
                    threading.Timer(0.1, os.kill, (os.getpid(), sinal)).start()
                    return p

                codigo = lock.rodar(self.t, ["python3", "-c", script], 0, popen=popen, sessao="s1", pid=500)
                self.assertEqual(codigo, 128 + sinal)
                self.assertTrue(pronto.exists())  # a limpeza do comando terminou antes de soltar
                self.assertIsNone(self.t.ler())
                self.assertEqual(signal.getsignal(sinal), signal.SIG_DFL)  # o handler antigo voltou
                filho[0].stdout.close()

    def test_pegar_esperando(self):
        self.t.pegar("s1", pid=500)
        voltas = iter(range(10))

        def dormir(s):
            if next(voltas) >= 2:
                self.sis.pids.pop(500, None)

        ok, _, _ = lock.pegar_esperando(self.t, 30, dormir=dormir, relogio=lambda: 0, sessao="s2", pid=600)
        self.assertTrue(ok)

    def test_flock_segura_a_vez(self):
        self.t.pasta.mkdir(parents=True)
        resultado = []
        import fcntl
        with open(self.t.pasta / "trava.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            outra = threading.Thread(target=lambda: resultado.append(self.t.pegar("s1", pid=500)))
            outra.start()
            outra.join(0.3)
            self.assertTrue(outra.is_alive())  # esperando o flock
        outra.join(5)
        self.assertTrue(resultado[0][0])

    def test_estado_nasce_so_do_usuario(self):
        with tempfile.TemporaryDirectory() as d:
            estado = Path(d) / "estado"
            lock.Trava(estado).pegar("s1", pid=os.getpid())
            self.assertEqual(estado.stat().st_mode & 0o777, 0o700)

    def test_quem(self):
        self.assertEqual(lock.quem(env={}, pane="%3", pane_pid="77"), "pane-%3-77")
        self.assertEqual(lock.quem(env={"CLAUDE_CODE_SESSION_ID": "abc"}, pane="%3", pane_pid="77"), "abc")
        self.assertEqual(lock.quem("minha", env={"FLUXO_SESSION": "x"}), "minha")
        self.assertEqual(lock.quem(env={"FLUXO_SESSION": "x", "CLAUDE_CODE_SESSION_ID": "abc"}, pane="%3"), "x")
        with self.assertRaisesRegex(lock.ErroTrava, "não sei quem"):
            lock.quem(env={})

    def test_pid_da_ferramenta(self):
        self.assertEqual(lock.pid_da_ferramenta(env={"CLAUDE_PID": "4321"}), 4321)

        def ps(arvore):  # pid → (nome, pai)
            def run(argv, **kw):
                pid = int(argv[-1])
                if pid not in arvore:
                    return subprocess.CompletedProcess(argv, 1, "", "")
                nome, pai = arvore[pid]
                return subprocess.CompletedProcess(argv, 0, f"{nome if argv[2] == 'comm=' else pai}\n", "")
            return run

        # bash -c fez exec: o pai já é a ferramenta
        self.assertEqual(lock.pid_da_ferramenta(env={}, run=ps({50: ("node", 10)}), getppid=lambda: 50), 50)
        # houve fork: o pai é o shell, e a ferramenta é o pai dele
        self.assertEqual(lock.pid_da_ferramenta(env={}, run=ps({60: ("bash", 55), 55: ("kiro-cli", 9)}),
                                                 getppid=lambda: 60), 55)
        # à mão num pane: o shell do pane e o servidor do tmux nunca morrem com a sessão
        self.assertIsNone(lock.pid_da_ferramenta(env={}, run=ps({70: ("-zsh", 8), 8: ("tmux: server", 1)}),
                                                  getppid=lambda: 70))
        self.assertIsNone(lock.pid_da_ferramenta(env={}, run=ps({}), getppid=lambda: 70))  # sem ps

    def test_descrever(self):
        self.assertEqual(lock.descrever(None, False), "livre")
        texto = lock.descrever({"sessao": "s", "id": "PROJ-1", "desde": 0, "projeto": "p", "pane": "%1"}, False, agora=120)
        self.assertEqual(texto, "ocupada por PROJ-1, há 2 min, projeto p, pane %1, dono morto: o próximo `acquire` fica com ela")


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        escrever_config(self.home, "copilot-tudo")
        self.env = {k: os.environ.get(k) for k in ("FLUXO_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID")}
        for k in self.env:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def rodar(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["lock", argv[0], "--home", str(self.home), *argv[1:]], RAIZ)
        return code, out.getvalue(), err.getvalue()

    def test_pegar_status_soltar_pelo_pid(self):
        pid = str(os.getpid())  # o processo do teste está vivo
        self.assertEqual(self.rodar("acquire", "--session", "s1", "--pid", pid, "--id", "PROJ-1")[0], 0)
        self.assertIn("ocupada por PROJ-1", self.rodar("status")[1])
        self.assertEqual(self.rodar("acquire", "--session", "s2", "--pid", pid)[0], lock.OCUPADA)
        self.assertEqual(self.rodar("release", "--session", "s2")[0], 1)
        self.assertEqual(self.rodar("release", "--session", "s1")[0], 0)
        self.assertIn("livre", self.rodar("status")[1])

    def test_claude_pid_vale_sem_pid_explicito(self):
        filho = subprocess.Popen(["sleep", "30"])
        os.environ["CLAUDE_PID"] = str(filho.pid)
        try:
            self.assertEqual(self.rodar("acquire", "--session", "s1", "--project", "api-1")[0], 0)
        finally:
            filho.kill()
            filho.wait()
        code, out, _ = self.rodar("status")
        self.assertIn("dono morto", out)  # o CLAUDE_PID foi o pid gravado, e ele morreu
        code, out, _ = self.rodar("break")
        self.assertEqual(code, 0)
        self.assertIn("docker compose -p api-1 down -v", out)

    def test_rodar_com_comando(self):
        code, _, _ = self.rodar("run", "--session", "s1", "--pid", str(os.getpid()), "--", "sh", "-c", "exit 5")
        self.assertEqual(code, 5)
        self.assertEqual(self.rodar("run", "--session", "s1", "--pid", str(os.getpid()))[0], 2)  # sem comando

    def test_sem_identidade(self):
        code, _, err = self.rodar("acquire")
        self.assertEqual(code, 2)
        self.assertIn("não sei quem", err)


if __name__ == "__main__":
    unittest.main()
