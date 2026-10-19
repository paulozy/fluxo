"""Os menus do `task`, do `modules` e do `validate`: só abrem quando falta algo e há terminal de verdade; fora
disso, o erro de sempre."""
import contextlib
import io
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
from fluxo import assistente, cli, task


def no_pty(argv, entrada, env, prazo=15):
    """Roda `argv` com stdin, stdout e stderr num pseudoterminal, com `entrada` já digitada. Devolve (código, o que
    apareceu na tela); passado o prazo, mata o processo (um menu esperando resposta não trava a suíte)."""
    mestre, escravo = os.openpty()
    proc = subprocess.Popen(argv, stdin=escravo, stdout=escravo, stderr=escravo, env=env, start_new_session=True)
    os.close(escravo)
    os.write(mestre, entrada.encode())
    tela, fim = b"", time.monotonic() + prazo
    try:
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
            proc.kill()
        return proc.wait(timeout=5), tela.decode("utf-8", "replace")
    finally:
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


class TaskNoPty(unittest.TestCase):
    """O `fluxo` de verdade, num pseudoterminal: o isatty, o input() e o stderr reais."""

    def setUp(self):
        try:
            os.close(os.openpty()[0])
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
