"""Os menus do `task`, do `modules` e do `validate`: só abrem quando falta algo e há terminal de verdade; fora
disso, o erro de sempre."""
import contextlib
import io
import unittest
from argparse import Namespace

from base import Terminal
from fluxo import assistente, cli


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


if __name__ == "__main__":
    unittest.main()
