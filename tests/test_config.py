"""A leitura e a gravação dos JSON que o fluxo edita: a config dele e a do pr-reviewer."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import RAIZ  # noqa: F401  (põe o lib/ no sys.path)
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


if __name__ == "__main__":
    unittest.main()
