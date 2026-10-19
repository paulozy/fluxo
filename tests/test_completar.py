"""O completar com Tab: o `fluxo __complete` percorre o parser e os arquivos locais, e os scripts do zsh e do bash só
perguntam a ele."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from base import RAIZ, escrever_config
from fluxo import cli, completar


class Candidatos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.code = self.home / "code"
        for repo in ("api", "web", ".oculto"):
            (self.code / repo).mkdir(parents=True)
        (self.code / "LEIA.md").write_text("")
        self.cam = escrever_config(self.home, "claude", modules=["task", "validate", "watch"], code_dir=str(self.code))

    def tearDown(self):
        self.tmp.cleanup()

    def valores(self, *palavras, home=True):
        """Os valores que o `fluxo __complete` imprime, pela main de verdade. O --home vai logo depois do comando, como
        o usuário digitaria (no fim, ele tiraria a palavra dos valores de um --enable)."""
        extra = ["--home", str(self.home)] if home and len(palavras) > 1 else []
        argv = ["__complete", *palavras[:1], *extra, *palavras[1:]]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(argv, RAIZ), 0)
        return [linha.split("\t")[0] for linha in out.getvalue().splitlines()]

    def test_comandos_acoes_e_flags(self):
        self.assertIn("validate", self.valores(""))
        self.assertNotIn("hook", self.valores(""))
        self.assertEqual(self.valores("val"), ["validate"])
        acoes = self.valores("validate", "")
        self.assertIn("recipe", acoes)
        self.assertNotIn("_rodar", acoes)
        flags = self.valores("validate", "start", "--")
        self.assertIn("--recipe", flags)
        self.assertNotIn("--profile", flags)
        self.assertNotIn("--home", flags)
        self.assertEqual(self.valores("task", "--tool", ""), ["claude", "copilot", "kiro", "codex"])

    def test_valores_dos_arquivos(self):
        self.assertEqual(self.valores("modules", "--enable", "l"), ["lock"])
        desligados = self.valores("modules", "--enable", "lock", "")
        self.assertNotIn("lock", desligados)  # já digitado
        self.assertNotIn("task", desligados)  # já ligado
        self.assertEqual(self.valores("modules", "--disable", ""), ["task", "validate", "watch"])
        self.assertEqual(self.valores("validate", "recipe", ""), ["api", "web"])  # só as pastas com nome de repo
        self.assertEqual(self.valores("validate", "start", "--recipe", ""), ["api", "web"])
        self.assertEqual(self.valores("config", "get", "task."), ["task.max"])
        self.assertEqual(self.valores("config", "set", "forge", ""), ["github", "gitlab"])
        self.cam.estado.mkdir(parents=True, exist_ok=True)
        (self.cam.estado / "tasks.jsonl").write_text(
            "\n".join(json.dumps({"id": i, "ferramenta": "claude"}) for i in ("P-1", "P-2", "P-1")) + "\n")
        self.assertEqual(self.valores("task", ""), ["P-1", "P-2"])  # o mais recente primeiro, sem repetir
        (self.cam.dados / "planos").mkdir(parents=True)
        (self.cam.dados / "planos" / "P-9.md").write_text("")
        self.assertEqual(self.valores("validate", "start", ""), ["P-9"])

    def test_nunca_quebra(self):
        self.cam.config.write_text("{ estragada")
        self.assertEqual(self.valores("modules", "--enable", ""), [])
        self.assertEqual(self.valores("--xis", "???", "validate", "status", "--wait", "", home=False)[:1], [])
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["__complete"], RAIZ), 0)
        self.assertEqual(err.getvalue(), "")


class Scripts(unittest.TestCase):
    def script(self, shell):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["completion", shell], RAIZ), 0)
        return out.getvalue()

    def test_sintaxe(self):
        for shell in ("zsh", "bash"):
            with self.subTest(shell=shell):
                if not shutil.which(shell):
                    self.skipTest(f"sem {shell}")
                r = subprocess.run([shell, "-n"], input=self.script(shell), capture_output=True, text=True)
                self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertTrue(self.script("zsh").startswith("#compdef fluxo\n"))

    def test_o_tab_no_bash(self):
        """O script de verdade, no bash de verdade, com o `fluxo` deste repo no PATH."""
        if not shutil.which("bash"):
            self.skipTest("sem bash")
        with tempfile.TemporaryDirectory() as tmp:
            binario = Path(tmp) / "fluxo"
            binario.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{RAIZ / "fluxo"}" "$@"\n')
            binario.chmod(0o755)
            (Path(tmp) / "fluxo.bash").write_text(self.script("bash"))
            teste = ('source "$1"; COMP_WORDS=(fluxo validate re); COMP_CWORD=2; _fluxo; printf "%s\\n" "${COMPREPLY[@]}"')
            r = subprocess.run(["bash", "-c", teste, "bash", str(Path(tmp) / "fluxo.bash")], capture_output=True,
                               text=True, env=dict(os.environ, PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}"))
        self.assertEqual((r.returncode, r.stdout), (0, "recipe\n"), r.stderr)


class Direto(unittest.TestCase):
    def test_acoes_sem_ler_arquivo(self):
        # as listas que não dependem de arquivo saem mesmo sem config nenhuma
        itens = completar.candidatos(cli.parser(), ["lock", ""], RAIZ)
        self.assertEqual([v for v, _ in itens], ["acquire", "run", "release", "status", "break"])


if __name__ == "__main__":
    unittest.main()
