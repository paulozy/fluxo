"""fluxo msg (área de transferência por sistema, sem enviar nada) e fluxo modulos."""
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
from fluxo import cli, config, mensagens, montar


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
        self.assertEqual(mensagens.comando_copiar("mac", tudo)[0], ["pbcopy"])
        self.assertEqual(mensagens.comando_copiar("wsl", tudo)[:2], (["clip.exe"], "utf-16"))
        self.assertEqual(mensagens.comando_copiar("linux", lambda b: f"/bin/{b}" if b == "xsel" else None)[0],
                         ["xsel", "--clipboard", "--input"])
        self.assertIsNone(mensagens.comando_copiar("linux", lambda b: None))

    def test_encoding_vai_por_stdin(self):
        visto = {}

        def run(argv, **kw):
            visto.update(argv=argv, **kw)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.assertEqual(mensagens.copiar("ação", "wsl", run=run, which=lambda b: b), (True, "clip.exe"))
        self.assertTrue(visto["input"].startswith(b"\xff\xfe"))  # UTF-16 com BOM, o que o clip.exe entende
        self.assertEqual(visto["input"].decode("utf-16"), "ação")
        mensagens.copiar("ação", "mac", run=run, which=lambda b: b)
        self.assertEqual((visto["input"], visto["env"]["LANG"], visto["env"]["LC_ALL"]),
                         ("ação".encode(), "en_US.UTF-8", "en_US.UTF-8"))
        self.assertEqual(visto["argv"], ["pbcopy"])  # o texto nunca vai no argv
        visto.clear()
        mensagens.copiar("x", "linux", run=run, which=lambda b: b)
        # xclip/wl-copy ficam servindo a seleção: com pipes capturados, o run esperaria o timeout
        self.assertEqual((visto["stdout"], visto["stderr"]), (subprocess.DEVNULL, subprocess.DEVNULL))
        self.assertNotIn("capture_output", visto)

    def test_falhas(self):
        self.assertFalse(mensagens.copiar("x", "linux", which=lambda b: None)[0])
        ruim = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"sem display")
        ok, como = mensagens.copiar("x", "mac", run=ruim, which=lambda b: b)
        self.assertFalse(ok)
        self.assertIn("sem display", como)

    def test_tom_nao_sobrescreve(self):
        with tempfile.TemporaryDirectory() as d:
            destino, criado = mensagens.criar_tom(d)
            self.assertTrue(criado)
            self.assertIn("Exemplos aprovados", destino.read_text())
            destino.write_text("meu tom\n")
            self.assertEqual(mensagens.criar_tom(d), (destino, False))
            self.assertEqual(destino.read_text(), "meu tom\n")


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def test_msg_copiar_com_xclip_falso(self):
        escrever_config(self.home, "copilot-tudo", so="linux")
        bin_ = self.home / "bin"
        bin_.mkdir()
        falso = bin_ / "xclip"
        falso.write_text(f"#!/bin/sh\n{shutil.which('cat')} > {self.home}/colado.txt\n")  # o PATH do teste só tem bin_
        falso.chmod(falso.stat().st_mode | stat.S_IEXEC)
        antes = os.environ["PATH"]
        os.environ["PATH"] = str(bin_)
        try:
            code, out, err = rodar("msg", "copiar", "--home", str(self.home), stdin="bea, consegue validar?\n")
        finally:
            os.environ["PATH"] = antes
        self.assertEqual(code, 0, err)
        self.assertIn("copiado (xclip)", out)
        self.assertEqual((self.home / "colado.txt").read_text(), "bea, consegue validar?\n")
        guardadas = list((config.Caminhos(home=self.home).dados / "mensagens").glob("*.md"))
        self.assertEqual(len(guardadas), 1)

    def test_msg_sem_ferramenta_guarda_e_sai_1(self):
        escrever_config(self.home, "copilot-tudo", so="linux")
        antes = os.environ["PATH"]
        os.environ["PATH"] = str(self.home)  # nenhuma ferramenta de área de transferência
        try:
            code, _, err = rodar("msg", "copiar", "--home", str(self.home), stdin="oi\n")
        finally:
            os.environ["PATH"] = antes
        self.assertEqual(code, 1)
        self.assertIn("o texto está em", err)
        self.assertEqual(rodar("msg", "copiar", "--home", str(self.home), stdin="  \n")[0], 2)

    def test_modulos_ligar_desligar(self):
        escrever_config(self.home, "copilot")
        code, out, _ = rodar("modulos", "--home", str(self.home), "--ligar", "task", "mensagens")
        self.assertEqual(code, 0)
        self.assertIn("task, mensagens", out)
        cfg = json.loads(config.Caminhos(home=self.home).config.read_text())
        self.assertEqual(cfg["modulos"], ["task", "mensagens"])
        code, _, err = rodar("modulos", "--home", str(self.home), "--ligar", "nao-existe")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(config.Caminhos(home=self.home).config.read_text())["modulos"], ["task", "mensagens"])
        rodar("modulos", "--home", str(self.home), "--desligar", "task")
        code, out, _ = rodar("modulos", "--home", str(self.home))
        self.assertIn("[x] mensagens", out)
        self.assertIn("[ ] task", out)

    def test_modulos_sem_repetir_e_config_ilegivel(self):
        escrever_config(self.home, "copilot")
        rodar("modulos", "--home", str(self.home), "--ligar", "task", "task")
        self.assertEqual(json.loads(config.Caminhos(home=self.home).config.read_text())["modulos"], ["task"])
        config.Caminhos(home=self.home).config.write_text("{ruim")
        code, _, err = rodar("modulos", "--home", str(self.home), "--ligar", "task")
        self.assertEqual(code, 2)
        self.assertIn("não é JSON", err)
        with self.assertRaisesRegex(config.ErroConfig, "repetição"):
            config.validar({"versao": 1, "ferramenta": "kiro", "forge": "github", "so": "wsl", "modulos": ["task", "task"]},
                           montar.modulos_existentes(RAIZ))

    def test_traco_duplo_so_no_rodar(self):
        escrever_config(self.home, "copilot-tudo", so="linux")
        arquivo = self.home / "x.md"
        arquivo.write_text("oi\n")
        # fora do `trava rodar`, o -- não é cortado em silêncio: o que sobra é erro de uso, não é ignorado
        with mock.patch("fluxo.mensagens.copiar", return_value=(True, "falso")), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as saida:
            cli.main(["msg", "copiar", "--home", str(self.home), "--", str(arquivo), "sobra"], RAIZ)
        self.assertEqual(saida.exception.code, 2)

    def test_validacao_das_opcoes_de_modulo(self):
        base = {"versao": 1, "ferramenta": "kiro", "forge": "github", "so": "wsl"}
        for extra, trecho in (({"task": {"max": 0}}, "task.max"), ({"task": {"lancar": "copilot"}}, "task.lancar"),
                              ({"task": {"lancar": []}}, "task.lancar"), ({"vigia": {"intervalo": 10}}, "vigia.intervalo")):
            with self.subTest(extra=extra), self.assertRaisesRegex(config.ErroConfig, trecho):
                config.validar(dict(base, **extra))

    def test_modulo_que_atende_o_requer(self):
        repo = self.home / "repo"
        shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "esperado", "__pycache__"))
        (repo / "modulos" / "task" / "modulo.json").write_text(json.dumps({"descricao": "x", "requer": "so:wsl|mac"}))
        cam = escrever_config(self.home, "kiro-v2", modulos=["task"])
        arquivos, _ = montar.montar(config.carregar(cam, montar.modulos_existentes(repo)), repo, cam)
        self.assertTrue(arquivos)

    def test_modulo_que_nao_atende_o_requer(self):
        repo = self.home / "repo"
        shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "esperado", "__pycache__"))
        (repo / "modulos" / "task" / "modulo.json").write_text(json.dumps({"descricao": "x", "requer": "so:mac"}))
        cam = escrever_config(self.home, "kiro-v2", modulos=["task"])
        with self.assertRaisesRegex(montar.ErroMontagem, "requer so:mac"):
            montar.montar(config.carregar(cam, montar.modulos_existentes(repo)), repo, cam)


if __name__ == "__main__":
    unittest.main()
