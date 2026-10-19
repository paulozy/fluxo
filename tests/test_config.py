"""A leitura e a gravação dos JSON que o fluxo edita (a config dele e a do pr-reviewer) e o `fluxo config`."""
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import RAIZ, escrever_config, roteiro
from fluxo import cli, montar, validate
from fluxo import config as cfgmod


class GravarJson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pasta = Path(self.tmp.name).resolve()
        self.arq = self.pasta / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def sobras(self):
        return [p.name for p in self.pasta.iterdir() if p.name.endswith(".tmp")]

    def test_grava_le_e_mantem_o_formato(self):
        cfgmod.gravar_json(self.arq, {"b": 1, "a": "ação"})
        self.assertEqual(self.arq.read_text(encoding="utf-8"), '{\n  "b": 1,\n  "a": "ação"\n}\n')
        self.assertEqual(stat.S_IMODE(self.arq.stat().st_mode), 0o600)  # arquivo novo: o modo pedido
        dados, lido = cfgmod.ler_json(self.arq)
        self.assertEqual((list(dados), lido), (["b", "a"], self.arq.read_bytes()))  # a ordem fica
        self.assertEqual(self.sobras(), [])

    def test_mantem_o_modo_e_o_link(self):
        """A config do pr-reviewer pode ser um link do dotfiles, com outro modo: grava no alvo, e o link fica."""
        alvo = self.pasta / "dotfiles" / "config.json"
        alvo.parent.mkdir()
        alvo.write_text('{"x": 1}\n')
        os.chmod(alvo, 0o664)
        self.arq.symlink_to(alvo)
        dados, lido = cfgmod.ler_json(self.arq)
        cfgmod.gravar_json(self.arq, dict(dados, y=2), antes=lido)
        self.assertTrue(self.arq.is_symlink())
        self.assertEqual(json.loads(alvo.read_text()), {"x": 1, "y": 2})
        self.assertEqual(stat.S_IMODE(alvo.stat().st_mode), 0o664)
        self.assertEqual([p.name for p in alvo.parent.iterdir()], ["config.json"])  # sem tmp esquecido

    def test_recusa_json_que_nao_serve(self):
        for texto, erro in (('{"a": 1,}', r"config.json não é JSON válido \(linha 1, coluna \d+"),
                            ('{"a": 1, "a": 2}', "aparece duas vezes"),
                            ("[1]", "a raiz precisa ser um objeto")):
            with self.subTest(texto=texto):
                self.arq.write_text(texto)
                with self.assertRaisesRegex(cfgmod.ErroConfig, erro):
                    cfgmod.ler_json(self.arq)
        with self.assertRaises(FileNotFoundError):
            cfgmod.ler_json(self.pasta / "nao-existe.json")

    def test_mudou_no_meio_nao_grava(self):
        self.arq.write_text('{"a": 1}\n')
        dados, lido = cfgmod.ler_json(self.arq)
        self.arq.write_text('{"a": 1, "outro": "editou"}\n')
        with self.assertRaisesRegex(cfgmod.ErroConfig, "mudou enquanto era editado"):
            cfgmod.gravar_json(self.arq, dict(dados, b=2), antes=lido)
        self.assertEqual(json.loads(self.arq.read_text()), {"a": 1, "outro": "editou"})
        self.assertEqual(self.sobras(), [])

    def test_falha_no_meio_nao_deixa_tmp(self):
        self.arq.write_text('{"a": 1}\n')
        with mock.patch.object(cfgmod.os, "replace", side_effect=OSError("disco cheio")), \
                self.assertRaises(OSError):
            cfgmod.gravar_json(self.arq, {"a": 2})
        self.assertEqual((self.arq.read_text(), self.sobras()), ('{"a": 1}\n', []))


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.cam = escrever_config(self.home, "copilot-claude")
        ambiente = mock.patch.dict(os.environ)
        ambiente.start()
        self.addCleanup(ambiente.stop)
        os.environ.pop("PR_REVIEWER_CONFIG_DIR", None)
        self.pr = validate.config_pr_reviewer(self.cam, {})

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, *argv, respostas=(), interativo=False, abrir=None):
        """(código, stdout, o que foi perguntado, o leitor)."""
        args = cli.parser().parse_args(["config", *argv, "--home", str(self.home)])
        ler, perguntas, out = roteiro(*respostas), io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.cmd_config(args, RAIZ, ler=ler, saida=perguntas, interativo=interativo,
                                  abrir=abrir or (lambda caminho: None))
        return code, out.getvalue(), perguntas.getvalue(), ler

    def gravada(self):
        return json.loads(self.cam.config.read_text())

    def main(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["config", *argv, "--home", str(self.home)], RAIZ)
            except SystemExit as exc:  # o erro de uso do argparse
                code = exc.code
        return code, err.getvalue()

    def com_config_do_pr(self, dados=None, modo=0o664):
        """A config do pr-reviewer como um link para o dotfiles, com outro modo: o caso desta máquina."""
        alvo = self.home / "dotfiles" / "pr-reviewer.json"
        alvo.parent.mkdir(exist_ok=True)
        alvo.write_text(json.dumps(dados or {"workspaceRoot": "/x", "profile": "local"}, indent=2) + "\n")
        os.chmod(alvo, modo)
        self.pr.parent.mkdir(parents=True, exist_ok=True)
        self.pr.symlink_to(alvo)
        return alvo

    def test_list_com_a_origem(self):
        code, out, _, _ = self.config("list")
        linhas = {l.split()[0]: l.split()[1:] for l in out.splitlines()}
        self.assertEqual(code, 0)
        self.assertEqual(linhas["tools"], ["copilot,claude", "config"])
        self.assertEqual(linhas["task.max"], ["2", "padrão"])
        self.assertEqual(linhas["models.copilot.strong"], ["—"])
        self.assertNotIn("models.kiro.strong", linhas)  # o kiro não está em tools
        self.assertEqual(linhas["pr-reviewer.backend"], ["—"])  # sem a config do pr-reviewer
        _, out, _, _ = self.config("list", "--json")
        dados = json.loads(out)
        self.assertEqual(dados["git.base"], {"valor": "develop", "origem": "padrão"})
        self.assertEqual(self.config()[1], self.config("list")[1])  # sem terminal, sem ação é o list

    def test_get(self):
        self.assertEqual(self.config("get", "task.max")[:2], (0, "2\n"))
        self.assertEqual(self.config("get", "tools")[:2], (0, "copilot,claude\n"))
        self.assertEqual(self.config("get", "models.copilot.strong")[:2], (1, ""))  # sem valor nem padrão
        code, err = self.main("get", "task.mx")
        self.assertEqual(code, 2)
        self.assertIn("quis dizer task.max?", err)
        self.assertIn("fluxo modules", self.main("get", "modules")[1])

    def test_set_e_unset(self):
        self.assertEqual(self.config("set", "task.max", "4")[:2], (0, "task.max = 4\n"))  # não pede install
        self.assertEqual(self.gravada()["task"], {"max": 4})
        self.assertIn("Agora: fluxo install", self.config("set", "git.base", "release")[1])
        self.config("set", "tools", "claude", "copilot")
        self.assertEqual(self.gravada()["tools"], ["claude", "copilot"])
        self.config("set", "models.copilot.strong", "gpt-x")
        self.assertEqual(self.gravada()["models"], {"copilot": {"strong": "gpt-x"}})
        self.config("unset", "models.copilot.strong")
        self.assertNotIn("models", self.gravada())  # sem objeto vazio para trás
        self.config("unset", "task.max")
        self.assertNotIn("task", self.gravada())
        self.assertEqual(self.config("unset", "task.max")[:2], (0, "task.max: nada mudou\n"))
        antes = self.cam.config.read_bytes()
        for argv, erro in ((["set", "watch.interval", "30"], "no mínimo 60"),
                           (["set", "tools", "copilot,copilot"], "sem repetir"),
                           (["set", "forge", "bitbucket"], "não vale"),
                           (["set", "promote.targets", "main,main"], "sem repetir"),
                           (["set", "git.base"], "falta VALUE"),
                           (["unset", "tools"], "é obrigatória")):
            with self.subTest(argv=argv):
                code, err = self.main(*argv)
                self.assertEqual(code, 2)
                self.assertIn(erro, err)
        code, err = self.main("set", "promote.targets", "release")  # a config inteira: os alvos sem a base
        self.assertEqual(code, 2)
        self.assertIn("promote.targets não pode incluir a própria git.base", err)
        self.assertEqual(self.cam.config.read_bytes(), antes)

    def test_pr_reviewer(self):
        code, err = self.main("set", "pr-reviewer.backend", "copilot")
        self.assertEqual(code, 2)
        self.assertIn("fluxo validate enable", err)
        alvo = self.com_config_do_pr()
        self.assertEqual(self.config("get", "pr-reviewer.backend")[:2], (0, "claude\n"))  # o padrão do pr-reviewer
        self.assertEqual(self.config("set", "pr-reviewer.backend", "copilot")[0], 0)
        self.assertTrue(self.pr.is_symlink())
        self.assertEqual(json.loads(alvo.read_text()), {"workspaceRoot": "/x", "profile": "local", "backend": "copilot"})
        self.assertEqual(stat.S_IMODE(alvo.stat().st_mode), 0o664)
        linha = next(l for l in self.config("list")[1].splitlines() if l.startswith("pr-reviewer.backend"))
        self.assertEqual(linha.split()[1:], ["copilot", "pr-reviewer"])
        for argv, erro in ((["set", "pr-reviewer.backend", "gpt"], "não vale"),
                           (["set", "pr-reviewer.profile", "a b"], "um nome simples")):
            with self.subTest(argv=argv):
                self.assertIn(erro, self.main(*argv)[1])

    def test_path(self):
        out = self.config("path")[1]
        self.assertIn(str(self.cam.config), out)
        self.assertIn(str(self.pr), out)

    def test_menu(self):
        code, out, perguntas, ler = self.config(respostas=("task.max", "x", "5", "", ""), interativo=True)
        self.assertEqual((code, ler.restantes, self.gravada()["task"]), (0, [], {"max": 5}))
        self.assertIn("task.max: 2 → 5. Gravar?", perguntas)
        self.assertIn("um número inteiro", perguntas)
        self.assertNotIn("install", out)  # o task.max não muda o que o install grava
        code, out, perguntas, ler = self.config(respostas=("tools", "claude", "", "git.base", "main", "", "git.base",
                                                           "release", "", "", "n"), interativo=True)
        self.assertEqual((ler.restantes, self.gravada()["tools"], self.gravada()["git"]), ([], ["claude"], {"base": "release"}))
        self.assertIn("não pode incluir a própria git.base; nada foi gravado", perguntas)
        self.assertEqual(perguntas.count("Rodar fluxo install agora?"), 1)  # uma oferta só, no fim
        self.assertIn("Agora: fluxo install", out)

    def test_menu_oferece_e_roda_o_install(self):
        with mock.patch.object(cli, "cmd_install", return_value=0) as install:
            self.config(respostas=("git.base", "release", "", "", ""), interativo=True)
        self.assertEqual(install.call_count, 1)

    def test_menu_cancelado_guarda_o_que_gravou(self):
        code, _, perguntas, _ = self.config(respostas=("task.max", "3", "", KeyboardInterrupt), interativo=True)
        self.assertEqual((code, self.gravada()["task"]), (0, {"max": 3}))
        self.assertIn("cancelado; o que já foi gravado fica", perguntas)

    def test_edit(self):
        def editor(*textos):
            fila = list(textos)
            return lambda caminho: Path(caminho).write_text(fila.pop(0)) if fila else None

        novo = json.dumps(dict(self.gravada(), task={"max": 7}))
        code, _, perguntas, ler = self.config("edit", respostas=("editar", "n"), interativo=True,
                                              abrir=editor('{"tools": ', novo))
        self.assertEqual((code, ler.restantes, self.gravada()["task"]), (0, [], {"max": 7}))
        self.assertIn("não é JSON válido", perguntas)
        antes = self.cam.config.read_bytes()
        code, _, perguntas, _ = self.config("edit", respostas=("descartar",), interativo=True,
                                            abrir=editor(json.dumps(dict(self.gravada(), forge="bitbucket"))))
        self.assertEqual((code, self.cam.config.read_bytes()), (0, antes))
        self.assertIn("nada foi gravado", perguntas)
        self.assertIn("nada mudou", self.config("edit", interativo=True)[2])
        self.assertEqual([p.name for p in self.cam.config_dir.iterdir() if "editando" in p.name], [])
        self.assertIn("rode num terminal", self.main("edit")[1])

    def test_config_que_nao_fecha_se_conserta_pelo_config(self):
        """O status manda usar o fluxo config para consertar: ele não pode recusar a config que vai consertar."""
        bruto = self.gravada()
        self.cam.config.write_text(json.dumps(dict(bruto, task={"max": 0})))
        code, err = self.main("list")
        self.assertEqual(code, 0)
        self.assertIn("a config não fecha", err)
        self.assertEqual(self.config("set", "task.max", "3")[0], 0)
        self.assertEqual(self.gravada()["task"], {"max": 3})
        self.cam.config.write_text("{ estragada")  # JSON inválido: o set recusa, o edit conserta
        self.assertIn("conserte com fluxo config edit", self.main("set", "task.max", "4")[1])
        code, _, _, _ = self.config("edit", respostas=("n",), interativo=True,
                                    abrir=lambda caminho: Path(caminho).write_text(json.dumps(bruto)))
        self.assertEqual((code, self.gravada()), (0, bruto))

    def test_config_do_pr_reviewer_ilegivel_nao_trava_as_do_fluxo(self):
        self.pr.parent.mkdir(parents=True)
        self.pr.write_text('{"backend": "kiro", "backend": "copilot"}')  # o JSON.parse dele aceita; o fluxo não grava
        self.assertEqual(self.config("set", "task.max", "3")[0], 0)
        linha = next(l for l in self.config("list")[1].splitlines() if l.startswith("pr-reviewer.backend"))
        self.assertEqual(linha.split()[1:], ["—", "ilegível"])
        code, err = self.main("set", "pr-reviewer.backend", "copilot")
        self.assertEqual(code, 2)
        self.assertIn("aparece duas vezes", err)

    def test_arquivo_mudado_durante_o_edit_nao_e_sobrescrito(self):
        def abrir(caminho):
            Path(caminho).write_text(json.dumps(dict(self.gravada(), task={"max": 9})))
            self.cam.config.write_text(json.dumps(dict(self.gravada(), forge="gitlab", gitlab={"host": "g.x"})))
        with self.assertRaisesRegex(cfgmod.ErroConfig, "mudou enquanto era editado"):
            self.config("edit", interativo=True, abrir=abrir)
        self.assertEqual(self.gravada()["forge"], "gitlab")

    def test_modules_nao_sobrescreve_o_que_mudou_no_meio(self):
        real = cfgmod.validar

        def validar_e_alguem_grava(bruto, existentes):  # outra gravação entre a leitura e a escrita
            self.cam.config.write_text(json.dumps(dict(self.gravada(), forge="gitlab", gitlab={"host": "g.x"})))
            return real(bruto, existentes)
        with mock.patch.object(cfgmod, "validar", side_effect=validar_e_alguem_grava):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = cli.main(["modules", "--enable", "lock", "--home", str(self.home)], RAIZ)
        self.assertEqual(code, 2)
        self.assertIn("mudou enquanto era editado", err.getvalue())
        self.assertEqual(self.gravada()["forge"], "gitlab")

    def test_doctor_sem_config_do_pr_reviewer_nao_fala_do_backend(self):
        escrever_config(self.home, "copilot-tudo")
        cfg = cfgmod.carregar(self.cam, montar.modulos_existentes(RAIZ))
        linhas = validate.checar(cfg, self.cam, run=lambda *a, **k: None, which=lambda b: None, env={})
        self.assertFalse([l for l in linhas if "roda os agentes" in l[1]])

    def test_doctor_avisa_o_backend_fora_de_tools(self):
        escrever_config(self.home, "copilot-tudo")  # só o copilot: o claude, padrão do pr-reviewer, não está aqui
        self.com_config_do_pr({"workspaceRoot": os.path.expanduser("~/code")})
        cfg = cfgmod.carregar(self.cam, montar.modulos_existentes(RAIZ))
        linhas = validate.checar(cfg, self.cam, run=lambda *a, **k: None, which=lambda b: None, env={})
        self.assertIn(("AVISO", "o pr-reviewer roda os agentes no claude, que não está em tools: "
                                "fluxo config set pr-reviewer.backend copilot"), linhas)
        escrever_config(self.home, "copilot-claude")
        cfg = cfgmod.carregar(self.cam, montar.modulos_existentes(RAIZ))
        self.assertFalse([l for l in validate.checar(cfg, self.cam, run=lambda *a, **k: None, which=lambda b: None, env={})
                          if "roda os agentes" in l[1]])  # com o claude em tools, nada a avisar


if __name__ == "__main__":
    unittest.main()
