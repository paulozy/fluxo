"""Os menus do `task`, do `modules` e do `validate`: só abrem quando falta algo e há terminal de verdade; fora
disso, o erro de sempre."""
import contextlib
import io
import json
import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from base import RAIZ, Terminal, ambiente_de_terminal, escrever_config, roteiro
from fluxo import assistente, cli, config, task, validate


def no_pty(argv, entrada, env, prazo=15):
    """Roda `argv` com stdin, stdout e stderr num pseudoterminal, com `entrada` já digitada. Devolve (código, o que
    apareceu na tela); passado o prazo, mata o processo (um menu esperando resposta não trava a suíte)."""
    mestre, escravo = os.openpty()
    try:
        proc = subprocess.Popen(argv, stdin=escravo, stdout=escravo, stderr=escravo, env=env, start_new_session=True)
    except OSError:
        os.close(mestre)
        raise
    finally:
        os.close(escravo)
    tela, fim = b"", time.monotonic() + prazo
    try:
        os.write(mestre, entrada.encode())
        while time.monotonic() < fim:
            if select.select([mestre], [], [], 0.2)[0]:
                try:
                    bloco = os.read(mestre, 4096)
                except OSError:  # EIO no Linux: o outro lado fechou
                    break
                if not bloco:
                    break
                tela += bloco
            elif proc.poll() is not None:
                break
        else:
            proc.kill()  # passou do prazo: o -9 no código diz que travou
        return proc.wait(timeout=5), tela.decode("utf-8", "replace")
    finally:
        if proc.poll() is None:  # saiu por exceção
            proc.kill()
            proc.wait()
        os.close(mestre)


class Guarda(unittest.TestCase):
    def test_marca_de_agente_desliga_mesmo_com_tty(self):
        for var in assistente.AGENTES:
            env = {"TERM": "xterm", var: "1"}
            self.assertFalse(assistente.pode_perguntar(Namespace(), Terminal(), Terminal(), env), var)
            self.assertEqual(assistente.desligado(Namespace(), env), f"a variável {var}")
        # o CLAUDECODE também aparece no terminal da IDE, onde quem digita é gente
        self.assertTrue(assistente.pode_perguntar(Namespace(), Terminal(), Terminal(),
                                                  {"TERM": "xterm", "CLAUDECODE": "1"}))

    def test_namespace_sem_no_input(self):
        self.assertIsNone(assistente.desligado(Namespace(), {"TERM": "xterm"}))
        self.assertEqual(assistente.desligado(Namespace(no_input=True), {}), "--no-input")

    def test_no_input_nos_comandos_com_menu(self):
        for argv in (["task"], ["modules"], ["validate", "status"]):
            args = cli.parser().parse_args([*argv, "--no-input"])
            self.assertTrue(args.no_input, argv)
            self.assertEqual(assistente.desligado(args, {}), "--no-input")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.parser().parse_args(["install", "--no-input"])  # só onde há o que perguntar


class Task(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        escrever_config(self.home, "copilot-tudo")

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, *respostas, argv=(), interativo=True, abrir=None):
        """Devolve (código, (id, prompt, nome) que chegaram ao abrir, ou None, o que foi perguntado, o leitor, stderr)."""
        args = cli.parser().parse_args(["task", "--home", str(self.home), "--dir", str(self.home), *argv])
        ler, perguntas, err = roteiro(*respostas), io.StringIO(), io.StringIO()
        with mock.patch("fluxo.task.abrir", return_value=0) as falso, contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            if abrir:
                falso.side_effect = abrir
            code = cli.cmd_task(args, RAIZ, ler=ler, saida=perguntas, interativo=interativo)
        chegou = tuple(falso.call_args[0][:3]) if falso.called else None
        return code, chegou, perguntas.getvalue(), ler, err.getvalue()

    def test_cada_acao(self):
        for respostas, esperado in ((("", "PROJ-1"), ("PROJ-1", None, None)),  # planejar: vale o padrão com ID
                                    (("2", "PROJ-1"), ("PROJ-1", "/implementar PROJ-1", None)),
                                    (("concluir", "PROJ-1"), ("PROJ-1", "/concluir PROJ-1", None)),
                                    (("livre", "/revisar o diff", ""), (None, "/revisar o diff", None)),
                                    (("4", "olha o log", "PROJ-2"), ("PROJ-2", "olha o log", None))):
            with self.subTest(respostas=respostas):
                code, chegou, perguntas, ler, _ = self.task(*respostas)
                self.assertEqual((code, chegou, ler.restantes), (0, esperado, []))
                self.assertIn("Ação da sessão:", perguntas)

    def test_o_comando_equivalente(self):
        _, _, perguntas, _, _ = self.task("implementar", "PROJ-1", argv=["--dry-run"])
        self.assertIn("o mesmo, sem menu: fluxo task PROJ-1 -p '/implementar PROJ-1' --dir ", perguntas)
        self.assertTrue(perguntas.rstrip().endswith("--dry-run"))

    def test_id_invalido_ou_vazio_repete(self):
        code, chegou, perguntas, ler, _ = self.task("planejar", "", "id ruim", "PROJ-1")
        self.assertEqual((code, chegou[0], ler.restantes), (0, "PROJ-1", []))
        self.assertIn("o ID é obrigatório aqui", perguntas)
        self.assertIn("ID inválido", perguntas)

    def test_livre_com_skill_de_tarefa_exige_o_id(self):
        code, chegou, perguntas, ler, _ = self.task("livre", "/implementar PROJ-3", "", "PROJ-3")
        self.assertEqual((code, chegou[:2], ler.restantes), (0, ("PROJ-3", "/implementar PROJ-3"), []))
        self.assertIn("o ID é obrigatório aqui", perguntas)

    def test_prefixo_do_codex(self):
        escrever_config(self.home, "codex", modules=["task"])
        code, chegou, _, _, _ = self.task("implementar", "PROJ-1")
        self.assertEqual((code, chegou[1]), (0, "$implementar PROJ-1"))
        code, chegou, perguntas, ler, _ = self.task("livre", "/implementar PROJ-1", "$revisar", "")
        self.assertEqual((code, chegou[:2], ler.restantes), (0, (None, "$revisar"), []))
        self.assertIn("nesta ferramenta a skill é chamada com $", perguntas)

    def test_p_de_tarefa_sem_id_pede_so_o_id(self):
        code, chegou, perguntas, ler, _ = self.task("PROJ-1", argv=["-p", "/implementar"])
        self.assertEqual((code, chegou, ler.restantes), (0, ("PROJ-1", "/implementar PROJ-1", None), []))
        self.assertNotIn("Ação da sessão", perguntas)
        code, chegou, _, _, _ = self.task("PROJ-1", argv=["-p", "/concluir com cuidado"])
        self.assertEqual(chegou, ("PROJ-1", "/concluir com cuidado", None))  # como `fluxo task PROJ-1 -p …`

    def test_id_do_prompt_e_o_padrao(self):
        code, chegou, perguntas, _, _ = self.task("", argv=["-p", "/implementar PROJ-1"])
        self.assertEqual((code, chegou[:2]), (0, ("PROJ-1", "/implementar PROJ-1")))
        self.assertIn("[PROJ-1]", perguntas)

    def test_p_de_tarefa_com_nome_e_o_erro_de_sempre(self):
        code, chegou, perguntas, ler, err = self.task("PROJ-1", argv=["-p", "/implementar", "-n", "minha"],
                                                      abrir=task.abrir)
        self.assertEqual((code, perguntas, ler.restantes), (2, "", ["PROJ-1"]))
        self.assertIn("são de uma tarefa", err)

    def test_nome_invalido_nao_espera_o_prompt(self):
        for nome in ("Ruim X", "watch"):
            with self.subTest(nome=nome):
                code, chegou, perguntas, ler, err = self.task("oi", argv=["-n", nome])
                self.assertEqual((code, chegou, ler.restantes), (2, None, ["oi"]))
                self.assertIn("inválido", err)

    def test_comando_equivalente_com_prompt_de_traco_e_target(self):
        _, _, perguntas, _, _ = self.task("livre", "-x tudo", "", argv=["--target", "main:"])
        self.assertIn("fluxo task '--prompt=-x tudo' --dir ", perguntas)
        self.assertIn("--target main:", perguntas)
        args = cli.parser().parse_args(["task", "--prompt=-x tudo"])
        self.assertEqual(args.prompt, "-x tudo")

    def test_com_nome_pede_so_o_prompt(self):
        code, chegou, perguntas, ler, _ = self.task("/implementar x", "olha o log", argv=["-n", "minha"])
        self.assertEqual((code, chegou, ler.restantes), (0, (None, "olha o log", "minha"), []))
        self.assertIn("com -n a sessão é avulsa", perguntas)

    def test_sem_falta_nada_nao_pergunta(self):
        for argv in (["PROJ-1"], ["-p", "/revisar"], ["--list"]):
            with self.subTest(argv=argv), mock.patch("fluxo.task.listar", return_value=0):
                code, _, perguntas, ler, _ = self.task("x", argv=argv)
                self.assertEqual((code, perguntas, ler.restantes), (0, "", ["x"]))

    def test_sem_terminal_o_erro_de_sempre(self):
        for argv, erro in (([], "informe o ID da tarefa"), (["-p", "/implementar"], "são de uma tarefa")):
            with self.subTest(argv=argv):
                code, _, perguntas, ler, err = self.task("x", argv=argv, interativo=False,
                                                         abrir=task.abrir)
                self.assertEqual((code, perguntas, ler.restantes), (2, "", ["x"]))
                self.assertIn(erro, err)

    def test_cancelar_nao_abre_nada(self):
        for respostas in ((), ("implementar", KeyboardInterrupt)):
            with self.subTest(respostas=respostas):
                code, chegou, perguntas, _, _ = self.task(*respostas)
                self.assertEqual((code, chegou), (130, None))
                self.assertIn("cancelado; nenhuma sessão foi aberta", perguntas)

    def test_marca_de_agente_num_terminal_nao_abre_o_menu(self):
        args = cli.parser().parse_args(["task", "--home", str(self.home)])
        for extra, esperado in (({"COPILOT_CLI": "1"}, 2), ({}, 130)):
            ler, err = roteiro(), io.StringIO()
            with self.subTest(extra=extra), mock.patch.dict(os.environ, dict(ambiente_de_terminal(), **extra), clear=True), \
                    mock.patch.object(sys, "stdin", Terminal()), contextlib.redirect_stdout(Terminal()), \
                    contextlib.redirect_stderr(err):
                self.assertEqual(cli.cmd_task(args, RAIZ, ler=ler), esperado)  # sem a marca, o EOF cancela
            self.assertEqual("Ação da sessão" in err.getvalue(), not extra)


class Modules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.cam = escrever_config(self.home, "claude", modules=["task", "watch"])

    def tearDown(self):
        self.tmp.cleanup()

    def modules(self, *respostas, argv=(), interativo=True):
        args = cli.parser().parse_args(["modules", "--home", str(self.home), *argv])
        ler, perguntas, out, err = roteiro(*respostas), io.StringIO(), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_modules(args, RAIZ, ler=ler, saida=perguntas, interativo=interativo)
        return code, out.getvalue(), perguntas.getvalue(), ler, err.getvalue()

    def ligados(self):
        return json.loads(self.cam.config.read_text(encoding="utf-8"))["modules"]

    def test_ligar_pelo_menu(self):
        code, out, perguntas, ler, _ = self.modules("panel", "lock, promote", argv=["--enable"])
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertEqual(self.ligados(), ["task", "watch", "lock", "promote"])
        self.assertIn("Módulos para ligar:", perguntas)
        self.assertNotIn(") task", perguntas)  # só os desligados
        self.assertIn("panel: requer tool:copilot|kiro|codex", perguntas)  # só com o Claude, o panel não serve
        self.assertIn("o mesmo, sem menu: fluxo modules --enable lock promote --home ", perguntas)
        self.assertIn("Agora: fluxo install", out)

    def test_desligar_pelo_menu(self):
        code, _, perguntas, _, _ = self.modules("2", argv=["--disable"])
        self.assertEqual((code, self.ligados()), (0, ["task"]))
        self.assertIn("Módulos para desligar:", perguntas)

    def test_desligar_nao_recusa_o_que_nao_serve(self):
        escrever_config(self.home, "claude", modules=["panel", "task"])  # um panel de quando havia o copilot
        code, _, perguntas, _, _ = self.modules("panel", argv=["--disable"])
        self.assertEqual((code, self.ligados()), (0, ["task"]))
        self.assertIn("não serve", perguntas)

    def test_modulo_que_nao_existe_mais(self):
        escrever_config(self.home, "claude", modules=["task", "velho"])
        code, _, perguntas, _, _ = self.modules("velho", argv=["--disable"])
        self.assertEqual((code, self.ligados()), (0, ["task"]))
        self.assertIn("velho  não existe mais", perguntas)

    def test_nenhum_nao_grava(self):
        antes = self.cam.config.read_bytes()
        for respostas in (("",), ("nenhum",)):
            code, _, perguntas, _, _ = self.modules(*respostas, argv=["--enable"])
            self.assertEqual((code, self.cam.config.read_bytes()), (0, antes))
            self.assertIn("nada foi gravado", perguntas)
        escrever_config(self.home, "copilot-tudo")
        code, _, perguntas, ler, _ = self.modules("x", argv=["--enable"])
        self.assertEqual((code, ler.restantes), (0, ["x"]))
        self.assertIn("todos os módulos já estão ligados", perguntas)

    def test_sem_terminal_e_erro(self):
        antes = self.cam.config.read_bytes()
        code, _, perguntas, ler, err = self.modules("lock", argv=["--enable"], interativo=False)
        self.assertEqual((code, perguntas, ler.restantes, self.cam.config.read_bytes()), (2, "", ["lock"], antes))
        self.assertIn("fluxo modules: --enable sem módulo (", err)

    def test_cancelar_nao_grava(self):
        antes = self.cam.config.read_bytes()
        code, _, perguntas, _, _ = self.modules(KeyboardInterrupt, argv=["--disable"])
        self.assertEqual((code, self.cam.config.read_bytes()), (130, antes))
        self.assertIn("cancelado; nada foi gravado", perguntas)

    def test_sem_flag_e_com_nome_nao_pergunta(self):
        code, out, perguntas, ler, _ = self.modules("x")
        self.assertEqual((code, perguntas, ler.restantes), (0, "", ["x"]))
        self.assertIn("[x] task", out)
        code, _, perguntas, ler, _ = self.modules("x", argv=["--enable", "lock"])
        self.assertEqual((code, perguntas, ler.restantes, self.ligados()), (0, "", ["x"], ["task", "watch", "lock"]))


class Validate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.cam = escrever_config(self.home, "claude", modules=["validate", "lock"])
        (self.cam.dados / "pr-reviewer" / "bin").mkdir(parents=True)
        (self.cam.dados / "pr-reviewer" / "bin" / "pr-review.ts").write_text("")
        (self.cam.dados / "planos").mkdir(parents=True)
        (self.cam.dados / "planos" / "T1.md").write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def execucao(self, ch, quando):
        pasta = self.cam.estado / "validar" / ch
        pasta.mkdir(parents=True)
        (pasta / "pedido.json").write_text("{}")
        os.utime(pasta / "pedido.json", (quando, quando))

    def validate(self, *respostas, argv=(), interativo=True):
        """Devolve (código, os starts, o que foi perguntado, o leitor, os mocks de enable/status/stop, stderr)."""
        args = cli.parser().parse_args(["validate", *argv, "--home", str(self.home)])
        ler, perguntas, err, vistos = roteiro(*respostas), io.StringIO(), io.StringIO(), []
        with mock.patch.object(validate, "start", lambda cfg, c, raiz, ch, argv, trava, **kw: vistos.append((ch, argv[2:]))), \
                mock.patch.object(validate, "preparar_revisao", lambda cfg, c, id_, backend: (["node", "x", "--local", id_], [])), \
                mock.patch.object(validate, "enable", return_value=(0, [])) as enable, \
                mock.patch.object(validate, "status", return_value=(0, [])) as status, \
                mock.patch.object(validate, "stop", return_value=(0, "")) as stop, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = cli.cmd_validate(args, RAIZ, ler=ler, saida=perguntas, interativo=interativo)
        mocks = {"enable": enable, "status": status, "stop": stop}
        return code, vistos, perguntas.getvalue(), ler, mocks, err.getvalue()

    def test_sem_acao_sem_terminal_e_erro(self):
        code, _, perguntas, ler, mocks, err = self.validate("enable", interativo=False)
        self.assertEqual((code, perguntas, ler.restantes, mocks["enable"].called), (2, "", ["enable"], False))
        self.assertIn("fluxo validate: falta a ação: enable, disable, start, status, stop ou exec (", err)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):  # o argparse aceita
            self.assertEqual(cli.main(["validate", "--home", str(self.home)], RAIZ), 2)
        self.assertIn("falta a ação", err.getvalue())

    def test_acao_pelo_menu(self):
        code, _, perguntas, ler, mocks, _ = self.validate("enable")
        self.assertEqual((code, ler.restantes, mocks["enable"].called), (0, [], True))
        self.assertIn("Ação do validate:", perguntas)
        self.assertNotIn(") exec", perguntas)
        self.assertIn("o mesmo, sem menu: fluxo validate enable --home ", perguntas)

    def test_start_de_uma_tarefa(self):
        code, vistos, perguntas, ler, _, _ = self.validate("", "", "T2", "T1")  # o padrão: start, tarefa
        self.assertEqual((code, vistos, ler.restantes), (0, [("revisao/T1", ["--local", "T1"])], []))
        self.assertIn("a tarefa T2 não tem plano", perguntas)
        self.assertIn("o mesmo, sem menu: fluxo validate start T1 --home ", perguntas)

    def test_start_de_uma_receita(self):
        code, vistos, perguntas, ler, _, _ = self.validate("receita", "api x", "api", "check", argv=["start"])
        self.assertEqual((code, vistos, ler.restantes), (0, [("receita/api", ["recipe", "api", "--check", "--json"])], []))
        self.assertIn("nome de repo inválido", perguntas)
        self.assertTrue(perguntas.rstrip().endswith("--check"))
        code, vistos, perguntas, _, _, _ = self.validate("api", argv=["start", "--fix"])  # o --fix já diz receita
        self.assertEqual((code, vistos), (0, [("receita/api", ["recipe", "api", "--fix", "--json"])]))
        self.assertNotIn("Validar o quê", perguntas)
        self.assertNotIn("Modo da receita", perguntas)

    def test_status_e_stop_escolhem_a_execucao(self):
        self.execucao("receita/api", 1000)
        self.execucao("revisao/T1", 2000)
        code, _, perguntas, _, mocks, _ = self.validate("", argv=["status"])
        self.assertEqual((code, mocks["status"].call_args[0][1]), (0, "revisao/T1"))  # a mais nova é o padrão
        self.assertIn("1) revisao/T1", perguntas)
        code, _, _, _, mocks, _ = self.validate("2", argv=["status"])
        self.assertEqual(mocks["status"].call_args[0][1], "receita/api")
        with mock.patch.object(validate, "_rodando", lambda pasta: pasta.name == "api"):
            code, _, perguntas, _, mocks, _ = self.validate("", argv=["stop"])
        self.assertEqual((code, mocks["stop"].call_args[0][1]), (0, "receita/api"))
        self.assertNotIn("revisao/T1", perguntas.split("Qual execução:")[1])  # no stop, só as que rodam

    def test_sem_execucao_nada_a_escolher(self):
        for acao, msg in (("status", "nenhuma validação registrada"), ("stop", "nenhuma validação rodando")):
            with self.subTest(acao=acao):
                code, _, perguntas, ler, mocks, _ = self.validate("x", argv=[acao])
                self.assertEqual((code, ler.restantes, mocks[acao].called), (1, ["x"], False))
                self.assertIn(msg, perguntas)

    def test_cancelar_e_com_alvo(self):
        code, vistos, perguntas, _, mocks, _ = self.validate("start", KeyboardInterrupt)
        self.assertEqual((code, vistos), (130, []))
        self.assertIn("cancelado; nada foi feito", perguntas)
        code, _, perguntas, ler, mocks, _ = self.validate("x", argv=["status", "T1"])
        self.assertEqual((code, perguntas, ler.restantes, mocks["status"].call_args[0][1]), (0, "", ["x"], "revisao/T1"))

    def test_flags_que_nao_cabem_nao_abrem_o_menu(self):
        code, _, perguntas, ler, mocks, _ = self.validate("x", argv=["status", "--recipe", "api"])
        self.assertEqual((code, perguntas, ler.restantes), (0, "", ["x"]))
        self.assertEqual(mocks["status"].call_args[0][1], "receita/api")
        for argv, erro in ((["start", "--fix", "--check"], "não combinam"), (["status", "--fix"], "são do --recipe")):
            with self.subTest(argv=argv), self.assertRaisesRegex(config.ErroConfig, erro):
                self.validate("api", "x", argv=argv)

    def test_execucoes_ignora_o_que_nao_e_execucao(self):
        self.execucao("revisao/T1", 1000)
        self.execucao("revisao/.oculta", 2000)
        (self.cam.estado / "validar" / "revisao" / "sem-pedido").mkdir()
        (self.cam.estado / "validar" / "outro" / "x").mkdir(parents=True)
        (self.cam.estado / "validar" / "outro" / "x" / "pedido.json").write_text("{}")
        self.assertEqual(validate.execucoes(self.cam), ["revisao/T1"])


class TaskNoPty(unittest.TestCase):
    """O `fluxo` de verdade, num pseudoterminal: o isatty, o input() e o stderr reais."""

    def setUp(self):
        try:
            for fd in os.openpty():
                os.close(fd)
        except OSError:
            self.skipTest("sem pseudoterminal")
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        escrever_config(self.home, "copilot-tudo")
        self.argv = [sys.executable, str(RAIZ / "fluxo"), "task", "--home", str(self.home), "--dir", str(self.home),
                     "--dry-run"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_o_menu_abre_e_monta_a_janela(self):
        code, tela = no_pty(self.argv, "2\nPROJ-1\n", ambiente_de_terminal())
        self.assertEqual(code, 0, tela)
        self.assertIn("Ação da sessão:", tela)
        self.assertIn("tmux new-window", tela)  # o --dry-run imprime a janela que abriria
        self.assertIn("/implementar PROJ-1", tela.split("tmux new-window", 1)[1])

    def test_num_agente_o_erro_sem_esperar(self):
        code, tela = no_pty(self.argv, "", dict(ambiente_de_terminal(), CODEX_CI="1"))
        self.assertEqual(code, 2, tela)
        self.assertIn("informe o ID da tarefa", tela)
        self.assertNotIn("Ação da sessão", tela)


if __name__ == "__main__":
    unittest.main()
