"""fluxo msg (área de transferência por sistema, sem enviar nada) e fluxo modules."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import RAIZ, escrever_config
from fluxo import cli, config, messages, montar


def rodar(*argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    antes = os.sys.stdin
    if stdin is not None:
        os.sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), RAIZ)
    finally:
        os.sys.stdin = antes
    return code, out.getvalue(), err.getvalue()


class Copiar(unittest.TestCase):
    def test_comando_por_sistema(self):
        tudo = lambda b: f"/bin/{b}"
        self.assertEqual(messages.comando_copiar("mac", tudo)[0], ["pbcopy"])
        self.assertEqual(messages.comando_copiar("wsl", tudo)[:2], (["clip.exe"], "utf-16"))
        self.assertEqual(messages.comando_copiar("linux", lambda b: f"/bin/{b}" if b == "xsel" else None)[0],
                         ["xsel", "--clipboard", "--input"])
        self.assertIsNone(messages.comando_copiar("linux", lambda b: None))

    def test_encoding_vai_por_stdin(self):
        visto = {}

        def run(argv, **kw):
            visto.update(argv=argv, **kw)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.assertEqual(messages.copiar("ação", "wsl", run=run, which=lambda b: b), (True, "clip.exe"))
        self.assertTrue(visto["input"].startswith(b"\xff\xfe"))  # UTF-16 com BOM, o que o clip.exe entende
        self.assertEqual(visto["input"].decode("utf-16"), "ação")
        messages.copiar("ação", "mac", run=run, which=lambda b: b)
        self.assertEqual((visto["input"], visto["env"]["LANG"], visto["env"]["LC_ALL"]),
                         ("ação".encode(), "en_US.UTF-8", "en_US.UTF-8"))
        self.assertEqual(visto["argv"], ["pbcopy"])  # o texto nunca vai no argv
        visto.clear()
        messages.copiar("x", "linux", run=run, which=lambda b: b)
        # xclip/wl-copy ficam servindo a seleção: com pipes capturados, o run esperaria o timeout
        self.assertEqual((visto["stdout"], visto["stderr"]), (subprocess.DEVNULL, subprocess.DEVNULL))
        self.assertNotIn("capture_output", visto)

    def test_falhas(self):
        self.assertFalse(messages.copiar("x", "linux", which=lambda b: None)[0])
        ruim = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"sem display")
        ok, como = messages.copiar("x", "mac", run=ruim, which=lambda b: b)
        self.assertFalse(ok)
        self.assertIn("sem display", como)

    def test_tom_nao_sobrescreve(self):
        with tempfile.TemporaryDirectory() as d:
            destino, criado = messages.criar_tom(d)
            self.assertTrue(criado)
            self.assertIn("Exemplos aprovados", destino.read_text())
            destino.write_text("meu tom\n")
            self.assertEqual(messages.criar_tom(d), (destino, False))
            self.assertEqual(destino.read_text(), "meu tom\n")


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_msg_copy_com_xclip_falso(self):
        escrever_config(self.home, "copilot-tudo", os="linux")
        bin_ = self.home / "bin"
        bin_.mkdir()
        falso = bin_ / "xclip"
        falso.write_text(f"#!/bin/sh\n{shutil.which('cat')} > {self.home}/colado.txt\n")  # o PATH do teste só tem bin_
        falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
        antes = os.environ["PATH"]
        os.environ["PATH"] = str(bin_)
        try:
            code, out, err = rodar("msg", "copy", "--home", str(self.home), stdin="bea, consegue validar?\n")
        finally:
            os.environ["PATH"] = antes
        self.assertEqual(code, 0, err)
        self.assertIn("copiado (xclip)", out)
        self.assertEqual((self.home / "colado.txt").read_text(), "bea, consegue validar?\n")
        guardadas = list((config.Caminhos(home=self.home).dados / "mensagens").glob("*.md"))
        self.assertEqual(len(guardadas), 1)

    def test_msg_sem_ferramenta_guarda_e_sai_1(self):
        escrever_config(self.home, "copilot-tudo", os="linux")
        antes = os.environ["PATH"]
        os.environ["PATH"] = str(self.home)  # nenhuma ferramenta de área de transferência
        try:
            code, _, err = rodar("msg", "copy", "--home", str(self.home), stdin="oi\n")
        finally:
            os.environ["PATH"] = antes
        self.assertEqual(code, 1)
        self.assertIn("o texto está em", err)
        self.assertEqual(rodar("msg", "copy", "--home", str(self.home), stdin="  \n")[0], 2)

    def test_modules_enable_disable(self):
        escrever_config(self.home, "copilot")
        code, out, _ = rodar("modules", "--home", str(self.home), "--enable", "task", "messages")
        self.assertEqual(code, 0)
        self.assertIn("task, messages", out)
        cfg = json.loads(config.Caminhos(home=self.home).config.read_text())
        self.assertEqual(cfg["modules"], ["task", "messages"])
        code, _, err = rodar("modules", "--home", str(self.home), "--enable", "nao-existe")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(config.Caminhos(home=self.home).config.read_text())["modules"], ["task", "messages"])
        rodar("modules", "--home", str(self.home), "--disable", "task")
        code, out, _ = rodar("modules", "--home", str(self.home))
        self.assertIn("[x] messages", out)
        self.assertIn("[ ] task", out)

    def test_modules_sem_repetir_e_config_ilegivel(self):
        escrever_config(self.home, "copilot")
        rodar("modules", "--home", str(self.home), "--enable", "task", "task")
        self.assertEqual(json.loads(config.Caminhos(home=self.home).config.read_text())["modules"], ["task"])
        config.Caminhos(home=self.home).config.write_text("{ruim")
        code, _, err = rodar("modules", "--home", str(self.home), "--enable", "task")
        self.assertEqual(code, 2)
        self.assertIn("não é JSON", err)
        with self.assertRaisesRegex(config.ErroConfig, "repetição"):
            config.validar({"version": 1, "tools": ["kiro"], "forge": "github", "os": "wsl", "modules": ["task", "task"]},
                           montar.modulos_existentes(RAIZ))

    def test_traco_duplo_so_no_rodar(self):
        escrever_config(self.home, "copilot-tudo", os="linux")
        arquivo = self.home / "x.md"
        arquivo.write_text("oi\n")
        # fora do `trava rodar`, o -- não é cortado em silêncio: o que sobra é erro de uso, não é ignorado
        with mock.patch("fluxo.messages.copiar", return_value=(True, "falso")), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as saida:
            cli.main(["msg", "copy", "--home", str(self.home), "--", str(arquivo), "sobra"], RAIZ)
        self.assertEqual(saida.exception.code, 2)

    def test_validacao_das_opcoes_de_modulo(self):
        base = {"version": 1, "tools": ["kiro"], "forge": "github", "os": "wsl"}
        for extra, trecho in (({"task": {"max": 0}}, "task.max"), ({"task": {"launch": "copilot"}}, "task.launch"),
                              ({"task": {"launch": {"copilot": []}}}, "task.launch"), ({"task": {"launch": {"cursor": ["x"]}}}, "task.launch"),
                              ({"watch": {"interval": 10}}, "watch.interval")):
            with self.subTest(extra=extra), self.assertRaisesRegex(config.ErroConfig, trecho):
                config.validar(dict(base, **extra))

    def test_modulo_que_atende_o_requer(self):
        repo = self.home / "repo"
        shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "esperado", "__pycache__"))
        (repo / "modulos" / "task" / "modulo.json").write_text(json.dumps({"descricao": "x", "requer": "os:wsl|mac"}))
        cam = escrever_config(self.home, "kiro-v2", modules=["task"])
        arquivos, _ = montar.montar(config.carregar(cam, montar.modulos_existentes(repo)), repo, cam)
        self.assertTrue(arquivos)

    def test_modulo_que_nao_atende_o_requer(self):
        repo = self.home / "repo"
        shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "esperado", "__pycache__"))
        (repo / "modulos" / "task" / "modulo.json").write_text(json.dumps({"descricao": "x", "requer": "os:mac"}))
        cam = escrever_config(self.home, "kiro-v2", modules=["task"])
        with self.assertRaisesRegex(montar.ErroMontagem, "requer os:mac"):
            montar.montar(config.carregar(cam, montar.modulos_existentes(repo)), repo, cam)


if __name__ == "__main__":
    unittest.main()
