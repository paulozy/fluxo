"""fluxo task: nomes, vagas, tmux falso (e um teste com o tmux de verdade, num servidor isolado)."""
import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from base import RAIZ, escrever_config
from fluxo import cli, config, montar, task, tmux


class Resp:
    def __init__(self, code=0, out=""):
        self.returncode, self.stdout, self.stderr = code, out, ""


class TmuxFalso:
    """Responde como o tmux: list-sessions, list-panes e new-window. `morre` faz a ferramenta cair ao abrir."""

    def __init__(self):
        self.vivos, self.abertas, self.morre, self.n, self.mortos, self.sem_servidor = set(), [], False, 6, set(), False

    def __call__(self, argv, **kw):
        if argv[:2] == ["tmux", "list-sessions"]:
            return Resp(1, "") if self.sem_servidor else Resp(0, "main: 1 windows")
        if argv[:2] == ["tmux", "list-panes"]:
            # pane_dead=1: a ferramenta saiu e a janela ficou (remain-on-exit): não é sessão viva
            return Resp(0, "".join(f"{p} {pid} 0\n" for p, pid in sorted(self.vivos))
                        + "".join(f"{p} {pid} 1\n" for p, pid in sorted(self.mortos)))
        if argv[:2] == ["tmux", "new-window"]:
            self.n += 1
            pane, pid = f"%{self.n}", str(1000 + self.n)
            self.abertas.append(argv)
            if not self.morre:
                self.vivos.add((pane, pid))
            return Resp(0, f"@{self.n} {pane} {pid}\n")
        raise AssertionError(f"comando inesperado: {argv}")


class Task(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.cam = escrever_config(self.home, "copilot-tudo")
        self.cfg = config.carregar(self.cam, montar.modulos_existentes(RAIZ))
        self.adapt = cli._adaptador(RAIZ, self.cfg["tools"][0])
        self.tmux = TmuxFalso()

    def tearDown(self):
        self.tmp.cleanup()

    def abrir(self, id_=None, prompt=None, nome=None, cfg=None, **kw):
        out, err = io.StringIO(), io.StringIO()
        kw.setdefault("dentro_do_tmux", True)
        pasta = kw.pop("pasta", self.home)
        cfg = cfg or self.cfg
        tool = kw.pop("tool", cfg["tools"][0])
        code = task.abrir(id_, prompt, nome, cfg, tool, self.adapt, self.cam, pasta, run=self.tmux,
                          dormir=lambda s: None, saida=out, erro=err, **kw)
        return code, out.getvalue(), err.getvalue()

    def registros(self):
        return task.ler_tasks(self.cam.estado)

    def test_avulsa_de_texto_livre_ganha_sufixo(self):
        self.assertEqual(self.abrir(prompt="confere os logs")[0], 0)
        self.assertEqual(self.abrir(prompt="outra coisa")[0], 0)
        self.assertEqual([r["nome"] for r in self.registros()], ["avulsa", "avulsa-b"])
        self.assertEqual({r["kind"] for r in self.registros()}, {"avulsa"})

    def test_nome_do_comando_e_unico(self):
        self.assertEqual(self.abrir(prompt="/revisar o diff")[0], 0)
        code, _, err = self.abrir(prompt="/revisar outra vez")
        self.assertEqual(code, task.OCUPADO)
        self.assertIn("já há uma sessão 'revisar' viva", err)
        self.assertEqual(self.abrir(prompt="/plugin:graphify x")[0], 0)
        self.assertEqual(self.registros()[-1]["nome"], "graphify")

    def test_tarefa_ocupa_vaga_e_avulsa_nao(self):
        for i in (1, 2):
            self.assertEqual(self.abrir(f"PROJ-{i}")[0], 0)
        code, _, err = self.abrir("PROJ-3")
        self.assertEqual(code, task.OCUPADO)
        self.assertIn("o limite é 2", err)
        self.assertEqual(self.abrir(prompt="confere os logs")[0], 0)  # avulsa não conta
        self.assertEqual(self.abrir("PROJ-3", forcar=True)[0], 0)
        self.assertEqual(self.registros()[0]["prompt"], "/planejar PROJ-1")

    def test_sessao_morta_libera_o_nome_e_a_vaga(self):
        self.abrir("PROJ-1")
        self.abrir("PROJ-2")
        self.tmux.vivos.clear()  # as duas janelas fecharam
        self.assertEqual(self.abrir("PROJ-1")[0], 0)

    def test_pane_reaproveitado_por_outro_processo_nao_e_a_sessao(self):
        self.abrir(prompt="/revisar x")
        (pane, pid), = self.tmux.vivos
        self.tmux.vivos = {(pane, "99999")}  # o servidor reiniciou e o %N voltou com outro processo
        self.assertEqual(self.abrir(prompt="/revisar y")[0], 0)

    def test_erros_de_uso(self):
        for args, trecho in (((None, "/planejar PROJ-1", None), "de uma tarefa"),
                             ((None, None, None), "informe o ID"),
                             ((None, "x", "watch"), "inválido"),
                             (("id com espaço", None, None), "inválido"),
                             (("PROJ-1", None, "nome"), "tire o -n")):
            with self.subTest(args=args), self.assertRaisesRegex(task.ErroUso, trecho):
                task.resolver(*args)

    def test_fora_do_tmux_so_imprime(self):
        code, out, err = self.abrir(prompt="oi", dentro_do_tmux=False)
        self.assertEqual(code, 3)
        self.assertTrue(out.startswith("copilot -n avulsa --session-id "))
        self.assertIn("--target", err)
        self.assertEqual(self.registros(), [])

    def test_ferramenta_que_cai_nao_e_registrada(self):
        self.tmux.morre = True
        code, _, err = self.abrir("PROJ-1")
        self.assertEqual(code, 1)
        self.assertIn("não subiu", err)
        self.assertEqual(self.registros(), [])

    def test_dry_run(self):
        code, out, _ = self.abrir("PROJ-1", dry_run=True)
        self.assertEqual(code, 0)
        self.assertIn("tmux new-window -d -P", out)
        self.assertIn("-n PROJ-1", out)
        self.assertEqual(self.registros(), [])
        self.assertEqual(self.tmux.abertas, [])

    def comando(self):
        """O comando da ferramenta: o que vem depois do `--` do new-window (vai em argumentos separados)."""
        argv = self.tmux.abertas[-1]
        return argv[argv.index("--") + 1:]

    def test_comando_de_cada_ferramenta(self):
        self.abrir("PROJ-1")
        copilot = self.comando()
        self.assertEqual(copilot[:4] + copilot[5:], ["copilot", "-n", "PROJ-1", "--session-id", "-i", "/planejar PROJ-1"])
        self.assertEqual(copilot[4], self.registros()[-1]["sessao"])
        self.assertEqual(len(copilot[4]), 36)
        kiro = config.validar({"version": 1, "tools": ["kiro"], "forge": "github", "os": "wsl", "modules": ["task"]},
                              montar.modulos_existentes(RAIZ))
        self.adapt = cli._adaptador(RAIZ, "kiro")
        self.abrir("PROJ-2", cfg=kiro)
        self.assertEqual(self.comando(), ["kiro-cli", "chat", "--", "/planejar PROJ-2"])  # -- : prompt com - não vira flag
        self.assertIsNone(self.registros()[-1]["sessao"])  # o Kiro não aceita id: ele vem pelo hook
        proprio = dict(self.cfg, task={"max": 2, "launch": {"copilot": ["minha-ferramenta", "{prompt}"]}})
        self.abrir(prompt="oi", cfg=proprio)
        self.assertEqual(self.comando(), ["minha-ferramenta", "oi"])

    def test_avulsa_aberta_antes_nao_ocupa_vaga(self):
        self.abrir(prompt="confere os logs")
        self.abrir("PROJ-1")
        self.assertEqual(self.abrir("PROJ-2")[0], 0)  # a avulsa não entrou na conta

    def test_servidor_fora_do_ar(self):
        self.tmux.sem_servidor = True
        code, _, err = self.abrir("PROJ-1")
        self.assertEqual(code, 3)
        self.assertIn("não está de pé", err)

    def test_pane_morto_que_ficou_aberto_nao_e_sessao(self):
        self.abrir(prompt="/revisar x")
        self.tmux.mortos = set(self.tmux.vivos)
        self.tmux.vivos = set()
        self.assertEqual(self.abrir(prompt="/revisar y")[0], 0)

    def test_codex(self):
        cfg = dict(self.cfg, tools=["copilot", "codex"])
        self.adapt = cli._adaptador(RAIZ, "codex")
        self.assertEqual(self.abrir("PROJ-1", cfg=cfg, tool="codex")[0], 0)
        self.assertEqual(self.comando(), ["codex", "--no-daemon", "--", "$planejar PROJ-1"])
        self.assertIsNone(self.registros()[-1]["sessao"])  # o Codex não aceita id: ele vem pelo hook
        self.abrir(prompt="-x estranho", cfg=cfg, tool="codex")
        self.assertEqual(self.comando()[-2:], ["--", "-x estranho"])  # depois do --: não vira flag
        self.abrir(prompt="$revisar agora", cfg=cfg, tool="codex")
        self.assertEqual(self.registros()[-1]["nome"], "revisar")
        with self.assertRaisesRegex(task.ErroUso, r"\$planejar"):
            self.abrir(prompt="$planejar X", cfg=cfg, tool="codex")

    def test_estado_nasce_so_do_usuario(self):
        self.assertFalse(self.cam.estado.exists())
        self.assertEqual(self.abrir("PROJ-1", dry_run=True)[0], 0)
        self.assertEqual(self.cam.estado.stat().st_mode & 0o777, 0o700)

    def test_habito_das_outras_ferramentas_no_codex(self):
        skills = montar.nomes_de_skills(RAIZ)
        for args in ((None, "/implementar PROJ-1", None), ("PROJ-1", "/implementar PROJ-1", None)):
            with self.subTest(args=args), self.assertRaisesRegex(task.ErroUso, r"com \$: -p '\$implementar"):
                task.resolver(*args, "$", skills)
        self.assertEqual(task.resolver(None, "/compact", None, "$", skills)[1], "avulsa")  # não é skill do fluxo
        self.assertEqual(task.resolver(None, "$HOME quebrou", None, "$", skills)[1], "avulsa")  # texto, não sessão "home"
        self.assertEqual(task.resolver(None, "$revisar o diff", None, "$", skills)[1], "revisar")
        with self.assertRaisesRegex(task.ErroUso, "são de uma tarefa"):  # no `/` é a skill de verdade
            task.resolver(None, "/implementar x", None, "/", skills)

    def test_cli_passa_as_skills_ao_task(self):
        escrever_config(self.home, "copilot-codex", modules=["task"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("fluxo.task.tmux.panes_vivos", return_value=set()):
            code = cli.main(["task", "--home", str(self.home), "--tool", "codex", "--dir", str(self.home), "--dry-run",
                             "-p", "/implementar PROJ-1"], RAIZ)
        self.assertEqual(code, 2)
        self.assertIn("'$implementar", err.getvalue())

    def test_prefixo_da_ferramenta(self):
        """O prompt padrão e o nome da avulsa usam o prefixo de skill do adaptador (o `$` do Codex)."""
        self.adapt = dict(self.adapt, prefixo="$")
        self.abrir("PROJ-1")
        self.assertEqual(self.comando()[-1], "$planejar PROJ-1")
        self.assertEqual(task.nome_do_prompt("$revisar x", "$"), "revisar")
        self.assertIsNone(task.nome_do_prompt("$HOME está errado"))  # no `/`, `$…` é texto: vira avulsa
        self.assertEqual(task.resolver(None, "$HOME x", None)[1], "avulsa")
        with self.assertRaisesRegex(task.ErroUso, r"\$planejar, \$implementar"):
            task.resolver(None, "$implementar X", None, "$")
        self.assertEqual(task.resolver(None, "$implementar X", None)[0], "avulsa")  # no `/`, não é skill

    def test_nomes(self):
        self.assertEqual(task.nome_do_prompt("/watch agora"), None)  # reservado: vira avulsa
        self.assertEqual(task.resolver(None, "x", "LOGS")[1], "logs")
        with self.assertRaisesRegex(task.ErroUso, "inválido"):
            task.resolver("PROJ-1\n", None, None)

    def test_pasta_inexistente_e_erro(self):
        with self.assertRaisesRegex(task.ErroUso, "não existe"):
            self.abrir("PROJ-1", pasta=self.home / "nao-existe")

    def test_comandos_impressos_com_aspas(self):
        code, out, _ = self.abrir(prompt="revise `id` e $(hostname)", dentro_do_tmux=False)
        self.assertEqual(code, 3)
        self.assertIn("'revise `id` e $(hostname)'", out)
        _, out, _ = self.abrir("PROJ-1", dry_run=True)
        self.assertIn("'#{window_id} #{pane_id} #{pane_pid}'", out)  # sem aspas, o # viraria comentário

    def test_flock_segura_a_vez(self):
        import fcntl
        import threading
        self.cam.estado.mkdir(parents=True, exist_ok=True)
        resultado = []
        with open(self.cam.estado / "task.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            outra = threading.Thread(target=lambda: resultado.append(self.abrir("PROJ-1")[0]))
            outra.start()
            outra.join(0.3)
            self.assertTrue(outra.is_alive())
        outra.join(5)
        self.assertEqual(resultado, [0])

    def test_lista(self):
        self.abrir("PROJ-1")
        out = io.StringIO()
        task.listar(self.cam, run=self.tmux, saida=out)
        self.assertIn("PROJ-1", out.getvalue())
        self.assertRegex(out.getvalue(), r"PROJ-1 +task +copilot ")  # a ferramenta de cada sessão
        self.tmux.vivos.clear()
        out = io.StringIO()
        task.listar(self.cam, run=self.tmux, saida=out)
        self.assertIn("nenhuma sessão", out.getvalue())  # morta não aparece

    def test_modulo_desligado_e_erro(self):
        escrever_config(self.home, "copilot")
        err = io.StringIO()
        # se a checagem de módulo regredir, nada de verdade abre: o abrir é falso
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("fluxo.task.abrir", side_effect=AssertionError("abriu com o módulo desligado")):
            code = cli.main(["task", "--home", str(self.home), "-p", "oi"], RAIZ)
        self.assertEqual(code, 2)
        self.assertIn("fluxo modules --enable task", err.getvalue())

    def test_duas_ferramentas(self):
        escrever_config(self.home, "copilot-claude")
        for argv, esperado in (([], "copilot"), (["--tool", "claude"], "claude"), (["--tool", "copilot"], "copilot")):
            with self.subTest(argv=argv), mock.patch("fluxo.task.abrir", return_value=0) as abrir:
                self.assertEqual(cli.main(["task", "--home", str(self.home), "-p", "oi", "--dir", str(self.home), *argv],
                                          RAIZ), 0)
                tool, adapt = abrir.call_args[0][4:6]
                self.assertEqual((tool, adapt["binario"]), (esperado, esperado))

    def test_launch_por_ferramenta_e_registro(self):
        cfg = dict(self.cfg, tools=["copilot", "claude"], task={"max": 2, "launch": {"claude": ["meu-claude", "{prompt}"]}})
        self.adapt = cli._adaptador(RAIZ, "claude")
        self.assertEqual(self.abrir("PROJ-1", cfg=cfg, tool="claude")[0], 0)
        self.assertEqual(self.comando(), ["meu-claude", "/planejar PROJ-1"])
        self.assertEqual(self.registros()[-1]["ferramenta"], "claude")
        self.adapt = cli._adaptador(RAIZ, "copilot")
        code, _, err = self.abrir("PROJ-1", cfg=cfg, tool="copilot")  # trocar de ferramenta no meio da tarefa
        self.assertEqual(code, task.OCUPADO)
        self.assertIn("'PROJ-1' viva (claude)", err)
        self.abrir("PROJ-2", cfg=cfg, tool="copilot")
        self.assertEqual(self.comando()[0], "copilot")  # sem launch dele, o do adaptador

    def test_tool_fora_de_tools_e_erro_antes_do_tmux(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch("fluxo.task.abrir", side_effect=AssertionError("abriu com uma ferramenta não instalada")):
            code = cli.main(["task", "--home", str(self.home), "-p", "oi", "--tool", "kiro"], RAIZ)
        self.assertEqual(code, 2)
        self.assertIn("kiro não está em tools", err.getvalue())

    def test_tool_escolhe_o_adaptador(self):
        for argv in ([], ["--tool", "copilot"]):
            with self.subTest(argv=argv), mock.patch("fluxo.task.abrir", return_value=0) as abrir:
                self.assertEqual(cli.main(["task", "--home", str(self.home), "-p", "oi", "--dir", str(self.home), *argv],
                                          RAIZ), 0)
                tool, adapt = abrir.call_args[0][4:6]
                self.assertEqual((tool, adapt["binario"]), ("copilot", "copilot"))


@unittest.skipUnless(shutil.which("tmux"), "sem tmux nesta máquina")
class TmuxDeVerdade(unittest.TestCase):
    """Num servidor isolado, com `sh -c 'sleep'` no lugar da ferramenta.

    Dentro do tmux, a variável TMUX aponta para o servidor do usuário e vale mais que o TMUX_TMPDIR: em
    09/10 um kill-server deste teste derrubou o tmux de verdade. Por isso o teste tira TMUX e TMUX_PANE do
    ambiente e, antes de qualquer kill, confere que o socket é o da pasta temporária."""

    VARS = ("TMUX", "TMUX_PANE", "TMUX_TMPDIR")

    def setUp(self):
        self.antes = {k: os.environ.get(k) for k in self.VARS}
        # realpath: o tmux grava o caminho real no socket_path (no Mac, /tmp é /private/tmp)
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="fx", dir="/tmp"))  # curto: o socket tem limite
        self.home = Path(self.tmp)
        os.environ.pop("TMUX", None)
        os.environ.pop("TMUX_PANE", None)
        os.environ["TMUX_TMPDIR"] = self.tmp
        # -f /dev/null: o tmux.conf do usuário (plugins, run-shell) não roda no servidor de teste
        # com um comando (sleep), o pane não abre o shell interativo do usuário (sem .zshrc)
        subprocess.run(["tmux", "-f", "/dev/null", "new-session", "-d", "-s", "teste", "sleep", "600"],
                       check=True, timeout=10)
        self.sock = self.socket()
        if not self.sock.startswith(self.tmp):
            self.restaurar()
            shutil.rmtree(self.tmp, ignore_errors=True)
            self.fail(f"o tmux do teste não está isolado (socket {self.sock!r}); nada foi derrubado")

    def socket(self):
        r = subprocess.run(["tmux", "display-message", "-p", "-t", "teste", "#{socket_path}"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip()

    def restaurar(self):
        for k, v in self.antes.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def tearDown(self):
        try:
            if self.sock.startswith(self.tmp):  # só o servidor que o próprio teste criou, pelo socket conferido
                subprocess.run(["tmux", "-S", self.sock, "kill-server"], capture_output=True, timeout=10)
        finally:
            self.restaurar()
            shutil.rmtree(self.tmp, ignore_errors=True)

    def test_abre_vive_e_morre(self):
        cam = escrever_config(self.home, "copilot-tudo", task={"max": 2, "launch": {"copilot": ["sh", "-c", "sleep 30"]}})
        cfg = config.carregar(cam, montar.modulos_existentes(RAIZ))
        out, err = io.StringIO(), io.StringIO()
        code = task.abrir("PROJ-1", None, None, cfg, "copilot", cli._adaptador(RAIZ, "copilot"), cam, self.home, alvo="teste:",
                          saida=out, erro=err)
        self.assertEqual(code, 0, err.getvalue())
        reg, = task.ler_tasks(cam.estado)
        self.assertTrue(tmux.pane_vivo(reg["pane"], reg["pane_pid"], tmux.panes_vivos()))
        subprocess.run(["tmux", "kill-pane", "-t", reg["pane"]], check=True, timeout=10)
        time.sleep(0.3)
        self.assertEqual(task.vivas(cam.estado, tmux.panes_vivos()), [])

    def test_ferramenta_que_nao_existe(self):
        cam = escrever_config(self.home, "copilot-tudo", task={"max": 2, "launch": {"copilot": ["ferramenta-que-nao-existe"]}})
        cfg = config.carregar(cam, montar.modulos_existentes(RAIZ))
        err = io.StringIO()
        code = task.abrir("PROJ-1", None, None, cfg, "copilot", cli._adaptador(RAIZ, "copilot"), cam, self.home, alvo="teste:",
                          saida=io.StringIO(), erro=err)
        self.assertEqual(code, 1, err.getvalue())


if __name__ == "__main__":
    unittest.main()
