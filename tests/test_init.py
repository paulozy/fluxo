"""O `fluxo init` interativo: quando pergunta, as perguntas e os padrões, o cancelamento e a config igual à
que as flags gravam."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from base import RAIZ, escrever_config
from fluxo import assistente, cli, config, montar


def roteiro(*respostas):
    """Uma resposta por pergunta; uma exceção na lista é levantada, e o fim da lista vira EOF (Ctrl-D)."""
    fila = list(respostas)

    def ler():
        if not fila:
            raise EOFError
        r = fila.pop(0)
        if isinstance(r, type) and issubclass(r, BaseException):
            raise r
        return r
    ler.restantes = fila
    return ler


class Terminal(io.StringIO):
    def __init__(self, tty=True):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


class Init(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.cam = config.Caminhos(home=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def init(self, *respostas, argv=(), interativo=True):
        """Roda o init com as respostas dadas; devolve (código, stdout, o que foi perguntado, o leitor)."""
        args = cli.parser().parse_args(["init", "--home", str(self.home), *argv])
        ler, perguntas, out = roteiro(*respostas), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.cmd_init(args, RAIZ, ler=ler, saida=perguntas, interativo=interativo)
        return code, out.getvalue(), perguntas.getvalue(), ler

    def gravada(self):
        return json.loads(self.cam.config.read_text(encoding="utf-8"))

    def test_o_assistente_grava_o_mesmo_que_as_flags(self):
        code, out, perguntas, ler = self.init("2,1", "gitlab", "gitlab.exemplo.com", "", "~/src", "mac",
                                              "task lock", "")
        self.assertEqual((code, ler.restantes), (0, []))
        pelo_assistente = self.gravada()
        self.cam.config.unlink()
        argv = ["--home", str(self.home)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["init", *argv, "--tool", "copilot", "--tool", "claude", "--forge", "gitlab",
                                       "--gitlab-host", "gitlab.exemplo.com", "--code-dir", "~/src", "--os", "mac"],
                                      RAIZ), 0)
            self.assertEqual(cli.main(["modules", *argv, "--enable", "task", "lock"], RAIZ), 0)
        self.assertEqual(pelo_assistente, self.gravada())
        self.assertEqual(pelo_assistente["tools"], ["copilot", "claude"])  # a ordem digitada: a primeira é a padrão
        self.assertEqual(pelo_assistente["git"], {"base": "develop"})

    def test_as_perguntas_vao_para_o_stderr_e_o_stdout_fica_com_a_saida(self):
        code, out, perguntas, _ = self.init("claude", "github", "", "", "linux", "", "")
        self.assertEqual(code, 0)
        self.assertIn("Ferramentas", perguntas)
        self.assertIn('"tools": [', perguntas)  # a config é mostrada antes de gravar
        self.assertNotIn("Ferramentas", out)
        self.assertTrue(out.startswith("config gravada em ~/.config/fluxo/config.json. Agora:"))
        self.assertFalse((self.cam.config_dir / ".config.json.tmp").exists())  # gravado por rename

    def test_flag_dada_nao_vira_pergunta(self):
        code, _, perguntas, ler = self.init("github", "v3", "", "", "wsl", "", "", argv=["--tool", "kiro"])
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertNotIn("Ferramentas", perguntas)
        self.assertIn("Engine do Kiro", perguntas)
        cfg = self.gravada()
        self.assertEqual((cfg["tools"], cfg["kiro"], cfg["os"]), (["kiro"], {"engine": "v3"}, "wsl"))
        code, _, perguntas, ler = self.init("", "", "", "", argv=["--tool", "claude", "--forge", "github", "--base",
                                                                   "main", "--os", "linux", "--force"])
        self.assertEqual(code, 0)  # com as duas flags, o init de sempre: nada é perguntado
        self.assertEqual((perguntas, len(ler.restantes)), ("", 4))

    def test_resposta_invalida_repete_a_pergunta(self):
        code, _, perguntas, ler = self.init("9", "claude,nada", "1", "bitbucket", "github", "minha branch",
                                            "develop", "", "linux", "tudo", "", "")
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertIn("não entendi '9'", perguntas)
        self.assertIn("não entendi 'claude,nada'", perguntas)
        self.assertIn("escolha um", perguntas)
        self.assertIn("nome de branch simples", perguntas)
        self.assertEqual(self.gravada()["tools"], ["claude"])

    def test_eof_e_ctrl_c_cancelam_sem_gravar(self):
        for respostas in (("claude",), ("claude", KeyboardInterrupt)):
            code, out, perguntas, _ = self.init(*respostas)
            self.assertEqual(code, 130)
            self.assertIn("cancelado; nada foi gravado", perguntas)
            self.assertEqual(out, "")
            self.assertFalse(self.cam.config.exists())
        code, _, _, _ = self.init("claude", "github", "", "", "linux", "", KeyboardInterrupt)  # no "gravar?"
        self.assertEqual(code, 130)
        self.assertFalse(self.cam.config.exists())

    def test_nao_gravar_no_fim(self):
        code, out, perguntas, _ = self.init("claude", "github", "", "", "linux", "", "n")
        self.assertEqual(code, 0)
        self.assertIn("nada foi gravado", perguntas)
        self.assertFalse(self.cam.config.exists())

    def test_sem_terminal_falta_flag_e_erro_de_uso(self):
        code, out, perguntas, ler = self.init("claude", interativo=False)
        self.assertEqual((code, perguntas, ler.restantes), (2, "", ["claude"]))
        self.assertFalse(self.cam.config.exists())
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["init", "--home", str(self.home), "--forge", "github"], RAIZ), 2)
        self.assertIn("falta --tool (ou rode num terminal", err.getvalue())

    def test_quando_pode_perguntar(self):
        def pode(no_input=False, stdin=True, stdout=True, env=None):
            return assistente.pode_perguntar(Namespace(no_input=no_input), Terminal(stdin), Terminal(stdout),
                                             {"TERM": "xterm"} if env is None else env)
        self.assertTrue(pode())
        self.assertFalse(pode(no_input=True))
        self.assertFalse(pode(stdin=False))
        self.assertFalse(pode(stdout=False))  # `fluxo init | tee`: a regra do gh pede os dois em terminal
        self.assertFalse(pode(env={"TERM": "xterm", "CI": "true"}))
        self.assertFalse(pode(env={"TERM": "dumb"}))
        args = cli.parser().parse_args(["init", "--no-input"])
        self.assertTrue(args.no_input)

    def test_config_que_ja_existe_vira_padrao(self):
        escrever_config(self.home, "copilot", modules=["task", "promote"], promote={"targets": ["staging", "main"]})
        antes = self.gravada()
        code, _, perguntas, _ = self.init("n")
        self.assertEqual(code, 0)
        self.assertIn("Já existe", perguntas)
        self.assertEqual(self.gravada(), antes)
        # refazer aceitando todos os padrões mantém tudo
        code, _, perguntas, ler = self.init("s", "", "", "", "", "", "", "", "", "")
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertIn("[copilot]", perguntas)
        self.assertIn("[gitlab.exemplo.com]", perguntas)
        self.assertIn("[task,promote]", perguntas)
        self.assertIn("[staging,main]", perguntas)
        cfg = self.gravada()
        self.assertEqual((cfg["tools"], cfg["forge"], cfg["gitlab"], cfg["os"]),
                         (["copilot"], "gitlab", {"host": "gitlab.exemplo.com"}, "mac"))
        self.assertEqual((cfg["modules"], cfg["promote"]), (["task", "promote"], {"targets": ["staging", "main"]}))

    def test_com_force_nao_pergunta_se_refaz(self):
        escrever_config(self.home, "claude")
        code, _, perguntas, ler = self.init("", "", "", "", "", "", "", argv=["--force"])
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertNotIn("Já existe", perguntas)

    def test_gitlab_pede_o_host_e_promote_pede_as_bases(self):
        code, _, perguntas, ler = self.init("codex", "gitlab", "", "https://gitlab.exemplo.com",
                                            "gitlab.exemplo.com", "", "", "", "promote",
                                            "develop,main", "staging, main", "")
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertIn("o host é obrigatório", perguntas)
        self.assertIn("só o host", perguntas)
        self.assertIn("sem a própria base (develop)", perguntas)
        cfg = self.gravada()
        self.assertEqual((cfg["gitlab"], cfg["promote"]), ({"host": "gitlab.exemplo.com"},
                                                           {"targets": ["staging", "main"]}))
        self.assertNotIn("Engine do Kiro", perguntas)

    def test_modulo_que_as_ferramentas_nao_atendem_e_recusado(self):
        code, _, perguntas, ler = self.init("claude", "github", "", "", "linux", "panel", "task", "")
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertIn("panel: requer tool:copilot|kiro|codex", perguntas)
        self.assertEqual(self.gravada()["modules"], ["task"])

    def test_atende(self):
        parcial = {"tools": ["claude"], "forge": "github", "os": "linux", "chat": "draft", "tracker": "none",
                   "kiro": {"engine": "v2"}}
        self.assertFalse(montar.atende(parcial, "panel", RAIZ))
        self.assertTrue(montar.atende(dict(parcial, tools=["claude", "copilot"]), "panel", RAIZ))
        self.assertTrue(montar.atende(parcial, "task", RAIZ))

    def test_ferramentas_no_path_aparecem_marcadas(self):
        args = cli.parser().parse_args(["init", "--forge", "github", "--base", "develop", "--code-dir", "~/code",
                                        "--os", "linux"])
        saida = io.StringIO()
        assistente.perguntar(args, {}, RAIZ, roteiro("kiro", "", ""), saida, achar=lambda b: b == "kiro-cli")
        self.assertRegex(saida.getvalue(), r"3\) kiro +\(no PATH\)")
        self.assertNotRegex(saida.getvalue(), r"1\) claude +\(no PATH\)")

    def test_proximos_passos_seguem_os_modulos(self):
        code, out, _, _ = self.init("kiro,claude", "github", "", "", "", "linux",
                                    "validate watch panel messages", "")
        self.assertEqual(code, 0)
        prog = config.Caminhos(home=self.home).exibir(RAIZ / "fluxo")
        for passo in (f"{prog} install --dry-run", f"{prog} install", "fluxo doctor", "fluxo validate enable",
                      "fluxo watch enable", "fluxo panel enable", "fluxo msg tone"):
            self.assertIn(passo, out)
        cfg = dict(self.gravada(), kiro={"engine": "v3"})
        self.assertNotIn("panel", " ".join(assistente.proximos_passos(cfg, "fluxo")))  # o enable é só do V2
        self.assertEqual(len(assistente.proximos_passos(dict(cfg, modules=[]), "fluxo")), 3)


    def test_com_as_flags_nem_olha_o_terminal(self):
        # `fluxo init --tool claude --forge github 0<&-`: o Python põe None no sys.stdin
        with mock.patch.object(sys, "stdin", None), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["init", "--home", str(self.home), "--tool", "claude", "--forge", "github"],
                                      RAIZ), 0)

    def test_flag_vazia_continua_erro_de_uso(self):
        for flag in ("--base", "--code-dir"):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["init", "--home", str(self.home), "--tool", "claude", "--forge", "github",
                                           flag, ""], RAIZ), 2)
            self.assertFalse(self.cam.config.exists())

    def test_o_terminal_de_verdade_e_as_perguntas_no_stderr(self):
        args = cli.parser().parse_args(["init", "--home", str(self.home)])
        out, err = Terminal(), io.StringIO()
        ambiente = {k: v for k, v in os.environ.items() if k != "CI"}
        ambiente["TERM"] = "xterm"
        with mock.patch.dict(os.environ, ambiente, clear=True), mock.patch.object(sys, "stdin", Terminal()), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_init(args, RAIZ, ler=roteiro("claude", "github", "", "", "linux", "", ""))
        self.assertEqual(code, 0)
        self.assertIn("Ferramentas", err.getvalue())
        self.assertNotIn("Ferramentas", out.getvalue())
        self.assertIn("config gravada", out.getvalue())
        with mock.patch.dict(os.environ, dict(ambiente, CI="1"), clear=True), \
                mock.patch.object(sys, "stdin", Terminal()), contextlib.redirect_stdout(Terminal()), \
                contextlib.redirect_stderr(err):
            self.assertEqual(cli.cmd_init(cli.parser().parse_args(["init", "--home", str(self.home)]), RAIZ), 2)
        self.assertIn("a variável CI desliga as perguntas", err.getvalue())

    def test_config_estragada_nao_derruba_o_assistente(self):
        self.cam.config_dir.mkdir(parents=True)
        self.cam.config.write_text(json.dumps({
            "version": 1, "tools": 1, "forge": 5, "git": {"base": 5}, "code_dir": 5, "os": "windows",
            "chat": "teams", "modules": 3, "gitlab": {"host": 5}, "promote": {"targets": "main"},
            "watch": {"interval": 10}, "task": {"max": 3}}), encoding="utf-8")
        code, _, perguntas, ler = self.init("s", "claude", "", "", "", "", "linux", "promote", "", "")
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertIn("aviso: watch da config de antes ficou de fora", perguntas)
        self.assertIn("aviso: promote da config de antes ficou de fora", perguntas)
        self.assertNotIn("m,a,i,n", perguntas)
        cfg = self.gravada()
        self.assertEqual((cfg["forge"], cfg["git"], cfg["code_dir"], cfg["chat"]),
                         ("github", {"base": "develop"}, "~/code", "draft"))
        self.assertEqual((cfg["promote"], cfg["task"]), ({"targets": ["main"]}, {"max": 3}))  # o válido fica
        self.assertNotIn("watch", cfg)

    def test_padrao_dos_modulos_tira_o_que_nao_serve_mais(self):
        escrever_config(self.home, "kiro-v2-panel")  # panel e watch, no Kiro
        code, _, perguntas, ler = self.init("s", "claude", "", "", "", "", "", "")
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertIn("[watch]", perguntas)
        self.assertEqual(self.gravada()["modules"], ["watch"])

    def test_enter_no_refazer_e_nao(self):
        escrever_config(self.home, "claude")
        antes = self.gravada()
        code, _, _, ler = self.init("", "sobra")
        self.assertEqual((code, ler.restantes), (0, ["sobra"]))
        self.assertEqual(self.gravada(), antes)

    def test_nenhum_tira_os_modulos_de_antes(self):
        escrever_config(self.home, "claude", modules=["task"])
        code, _, _, _ = self.init("s", "", "", "", "", "", "nenhum", "")
        self.assertEqual((code, self.gravada()["modules"]), (0, []))

    def test_numeros_fora_da_lista(self):
        code, _, perguntas, ler = self.init("0", "²", "claude", "1,2", "github", "", "", "linux", "", "")
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertIn("não entendi '0'", perguntas)
        self.assertIn("não entendi '²'", perguntas)
        self.assertIn("escolha um", perguntas)  # o forge é uma escolha só

    def test_ctrl_c_fora_da_leitura(self):
        with mock.patch.object(assistente, "_binario", side_effect=KeyboardInterrupt):
            code, _, perguntas, _ = self.init()
        self.assertEqual(code, 130)
        self.assertFalse(self.cam.config.exists())

    def test_gitlab_host_vazio_na_flag_e_perguntado(self):
        code, _, perguntas, ler = self.init("gitlab", "gitlab.exemplo.com", "", "", "linux", "", "",
                                            argv=["--tool", "claude", "--gitlab-host", ""])
        self.assertEqual((code, ler.restantes), (0, []))
        self.assertEqual(self.gravada()["gitlab"], {"host": "gitlab.exemplo.com"})

    def test_caminho_com_espaco_nos_proximos_passos(self):
        passos = assistente.proximos_passos({"tools": ["claude"], "modules": []}, "~/meu fluxo/fluxo")
        self.assertEqual(passos[0].split("   #")[0], "~/'meu fluxo/fluxo' install --dry-run")

if __name__ == "__main__":
    unittest.main()
