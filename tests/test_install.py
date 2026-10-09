"""install/uninstall num HOME temporário: golden, idempotência, conflitos, remoção e segurança.

Golden: `tests/esperado/<cenario>/` guarda a árvore gerada byte a byte. Depois de mudar uma fonte de
propósito, regenere com `FLUXO_ATUALIZAR_ESPERADO=1 python3 -m unittest discover -s tests` e revise o diff.
"""
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from base import CENARIOS, RAIZ, arvore, escrever_config
from fluxo import cli, config, instalar, montar

ESPERADO = RAIZ / "tests" / "esperado"


def rodar(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv), RAIZ)
    return code, out.getvalue(), err.getvalue()


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def instalar(self, cenario="claude", *extra, **cfg):
        escrever_config(self.home, cenario, **cfg)
        return rodar("install", "--home", str(self.home), *extra)

    def test_golden(self):
        for cenario in CENARIOS:
            with self.subTest(cenario=cenario):
                home = self.home / cenario
                escrever_config(home, cenario)
                code, out, err = rodar("install", "--home", str(home))
                self.assertEqual(code, 0, err)
                gerado = arvore(home)
                pasta = ESPERADO / cenario
                if os.environ.get("FLUXO_ATUALIZAR_ESPERADO"):
                    shutil.rmtree(pasta, ignore_errors=True)
                    for rel, dados in gerado.items():
                        (pasta / rel).parent.mkdir(parents=True, exist_ok=True)
                        (pasta / rel).write_bytes(dados)
                self.assertEqual(sorted(gerado), sorted(arvore(pasta)), "arquivos diferentes do esperado")
                for rel, dados in gerado.items():
                    self.assertEqual(dados, (pasta / rel).read_bytes(), f"{cenario}: {rel} difere do esperado")

    def test_idempotente(self):
        self.assertEqual(self.instalar()[0], 0)
        alvo = self.home / ".claude" / "rules" / "fluxo.md"
        antes = alvo.stat().st_mtime_ns
        code, out, _ = self.instalar()
        self.assertEqual(code, 0)
        self.assertNotIn("criar", out)
        self.assertNotIn("atualizar", out)
        self.assertEqual(alvo.stat().st_mtime_ns, antes)  # nada regravado
        self.assertTrue(os.access(self.home / ".local" / "bin" / "fluxo", os.X_OK))

    def test_arquivo_alheio_e_conflito_e_nada_e_gravado(self):
        # a máquina daqui: um ~/.claude com agentes próprios de mesmo nome
        alheio = self.home / ".claude" / "agents" / "code-reviewer.md"
        alheio.parent.mkdir(parents=True)
        alheio.write_text("meu agente, não do fluxo\n")
        code, out, err = self.instalar()
        self.assertEqual(code, 3)
        self.assertIn("  conflito   ~/.claude/agents/code-reviewer.md  (já existe e não é do fluxo)", out)
        self.assertIn("Nada foi gravado", err)
        self.assertEqual(alheio.read_text(), "meu agente, não do fluxo\n")
        self.assertFalse((self.home / ".claude" / "rules").exists())  # all-or-nothing
        self.assertFalse(config.Caminhos(home=self.home).manifesto.exists())

    def test_forcar_faz_backup(self):
        alheio = self.home / ".claude" / "agents" / "code-reviewer.md"
        alheio.parent.mkdir(parents=True)
        alheio.write_text("meu\n")
        self.assertEqual(self.instalar("claude", "--forcar")[0], 0)
        backups = list(alheio.parent.glob("code-reviewer.md.fluxo-bak-*"))
        self.assertEqual([b.read_text() for b in backups], ["meu\n"])
        self.assertIn("senior code reviewer", alheio.read_text())

    def test_editado_depois_do_install_e_conflito(self):
        self.instalar()
        skill = self.home / ".claude" / "skills" / "planejar" / "SKILL.md"
        skill.write_text(skill.read_text() + "\nminha nota\n")
        # mudar a base muda o texto da skill: o install quer atualizar, mas o arquivo foi editado
        code, out, _ = self.instalar(git={"base": "main"})
        self.assertEqual(code, 3)
        self.assertIn("  conflito   ~/.claude/skills/planejar/SKILL.md  (editado depois do install)", out)
        self.assertIn("minha nota", skill.read_text())

    def test_igual_fora_do_manifesto_e_adotado(self):
        self.instalar()
        cam = config.Caminhos(home=self.home)
        cam.manifesto.unlink()  # install interrompido antes de gravar o manifesto
        code, out, _ = self.instalar()
        self.assertEqual(code, 0)
        self.assertIn("  adotar     ~/.claude/rules/fluxo.md", out)
        self.assertIn(".claude/rules/fluxo.md", instalar.ler_manifesto(cam.manifesto))

    def test_o_que_sai_da_config_e_removido(self):
        self.instalar("kiro-v2", modulos=["mensagens"])
        skill = self.home / ".kiro" / "skills" / "aviso" / "SKILL.md"
        self.assertTrue(skill.exists())
        code, out, _ = self.instalar("kiro-v2")  # módulo desligado
        self.assertEqual(code, 0)
        self.assertIn("remover", out)
        self.assertFalse(skill.parent.exists())  # a pasta da skill sai junto

    def test_removido_mas_editado_fica(self):
        self.instalar("kiro-v2", modulos=["mensagens"])
        skill = self.home / ".kiro" / "skills" / "aviso" / "SKILL.md"
        skill.write_text("editei\n")
        code, out, _ = self.instalar("kiro-v2")
        self.assertEqual(code, 0)
        self.assertIn("  manter     ~/.kiro/skills/aviso/SKILL.md  (saiu do fluxo, mas foi editado", out)
        self.assertEqual(skill.read_text(), "editei\n")
        cam = config.Caminhos(home=self.home)
        self.assertNotIn(".kiro/skills/aviso/SKILL.md", instalar.ler_manifesto(cam.manifesto))

    def test_troca_de_motor_atualiza_os_agentes(self):
        self.instalar("kiro-v2")
        agente = self.home / ".kiro" / "agents" / "architect.json"
        self.assertIn('"web_search"', agente.read_text())
        code, out, _ = self.instalar("kiro-v3")
        self.assertEqual(code, 0)
        self.assertIn("atualizar", out)
        self.assertIn('"web"', agente.read_text())
        self.assertNotIn("allowedTools", agente.read_text())

    def test_dry_run_e_uninstall(self):
        escrever_config(self.home, "copilot")
        code, out, _ = rodar("install", "--home", str(self.home), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("nada foi gravado", out)
        self.assertEqual(arvore(self.home), {})
        rodar("install", "--home", str(self.home))
        editado = self.home / ".copilot" / "skills" / "revisar" / "SKILL.md"
        editado.write_text("minha versão\n")
        code, out, _ = rodar("uninstall", "--home", str(self.home), "--dry-run")
        self.assertIn("remover", out)
        self.assertGreater(len(arvore(self.home)), 1)
        code, out, _ = rodar("uninstall", "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertEqual(arvore(self.home), {".copilot/skills/revisar/SKILL.md": b"minha vers\xc3\xa3o\n"})
        self.assertFalse((self.home / ".local" / "bin" / "fluxo").exists())
        self.assertFalse(config.Caminhos(home=self.home).manifesto.exists())

    def test_dois_installs_e_uninstall_limpam_tudo(self):
        self.instalar("copilot")
        self.instalar("copilot")  # o segundo é todo IGUAL: o manifesto tem que continuar com tudo
        self.assertEqual(rodar("uninstall", "--home", str(self.home))[0], 0)
        self.assertEqual(arvore(self.home), {})

    def test_falha_no_meio_nao_vira_conflito_depois(self):
        escrever_config(self.home, "claude")
        original, chamadas = instalar._gravar, []

        def quebra(*a, **k):
            chamadas.append(1)
            if len(chamadas) == 3:
                raise OSError("disco cheio")
            return original(*a, **k)

        instalar._gravar = quebra
        try:
            code, _, err = rodar("install", "--home", str(self.home))
        finally:
            instalar._gravar = original
        self.assertEqual(code, 1)
        self.assertIn("disco cheio", err)
        code, out, _ = rodar("install", "--home", str(self.home))  # o manifesto vem por último
        self.assertEqual(code, 0)
        self.assertNotIn("conflito", out)

    def test_link_simbolico_no_destino(self):
        fora = self.home / "fora.md"
        fora.write_text("não é do fluxo\n")
        destino = self.home / ".claude" / "rules" / "fluxo.md"
        destino.parent.mkdir(parents=True)
        destino.symlink_to(fora)
        code, out, _ = self.instalar()
        self.assertEqual(code, 3)
        self.assertIn("(é um link simbólico)", out)
        self.assertEqual(self.instalar("claude", "--forcar")[0], 0)
        self.assertEqual(fora.read_text(), "não é do fluxo\n")  # o link foi trocado, não seguido
        self.assertFalse(destino.is_symlink())
        self.assertTrue([b for b in destino.parent.glob("fluxo.md.fluxo-bak-*") if b.is_symlink()])

    def test_forcar_e_uninstall_devolvem_o_original(self):
        meu = self.home / ".claude" / "agents" / "code-reviewer.md"
        meu.parent.mkdir(parents=True)
        meu.write_text("meu\n")
        self.instalar("claude", "--forcar")
        self.instalar("claude")  # um install depois mantém o registro do backup
        code, out, _ = rodar("uninstall", "--home", str(self.home))
        self.assertEqual(code, 0)
        self.assertIn("volta o seu code-reviewer.md.fluxo-bak-", out)
        self.assertEqual(meu.read_text(), "meu\n")
        self.assertEqual(sorted(p.name for p in meu.parent.iterdir()), ["code-reviewer.md"])

    def test_backup_nunca_sobrescreve_outro(self):
        alvo = self.home / "a.md"
        nomes = []
        for conteudo in ("um\n", "dois\n"):
            alvo.write_text(conteudo)
            nomes.append(instalar._backup(alvo, agora=1_000_000_000))  # o mesmo segundo
        self.assertEqual(len(set(nomes)), 2)
        self.assertEqual(sorted((self.home / n).read_text() for n in nomes), ["dois\n", "um\n"])

    def test_manifesto_estragado_ou_com_caminho_de_fora(self):
        cam = escrever_config(self.home, "claude")
        fora = self.home.parent / "vitima.txt"
        for conteudo in ("[]", '{"arquivos": []}', '{"arquivos": {"x": "abc"}}', '{"versao": 1}',
                         '{"arquivos": {"%s": {"sha256": "x"}}}' % fora,
                         '{"arquivos": {"../vitima.txt": {"sha256": "x"}}}'):
            with self.subTest(conteudo=conteudo):
                cam.dados.mkdir(parents=True, exist_ok=True)
                cam.manifesto.write_text(conteudo)
                for cmd in ("install", "uninstall"):
                    code, _, err = rodar(cmd, "--home", str(self.home))
                    self.assertEqual(code, 1, f"{cmd}: {err}")
                    self.assertTrue("manifesto" in err or "fora do HOME" in err, err)

    def test_dry_run_com_conflito_sai_3_e_home_vazio_e_erro(self):
        alheio = self.home / ".claude" / "agents" / "debugger.md"
        alheio.parent.mkdir(parents=True)
        alheio.write_text("meu\n")
        escrever_config(self.home, "claude")
        self.assertEqual(rodar("install", "--home", str(self.home), "--dry-run")[0], 3)
        self.assertEqual(rodar("install", "--home", "")[0], 2)

    def test_reinstall_devolve_o_modo(self):
        self.instalar()
        wrapper = self.home / ".local" / "bin" / "fluxo"
        os.chmod(wrapper, 0o644)
        self.instalar()
        self.assertTrue(os.access(wrapper, os.X_OK))

    def test_erros_de_uso(self):
        code, _, err = rodar("install", "--home", str(self.home))
        self.assertEqual(code, 2)
        self.assertIn("fluxo init", err)
        self.assertEqual(rodar("init", "--home", str(self.home), "--ferramenta", "kiro", "--forge", "github")[0], 0)
        code, _, err = rodar("init", "--home", str(self.home), "--ferramenta", "kiro", "--forge", "github")
        self.assertEqual(code, 2)
        self.assertIn("--forcar", err)

    def test_skill_quebrada_vira_erro_curto(self):
        raiz = self.home / "repo"
        shutil.copytree(RAIZ, raiz, ignore=shutil.ignore_patterns(".git", "esperado", "__pycache__"))
        p = raiz / "nucleo" / "skills" / "revisar" / "SKILL.md"
        p.write_text(p.read_text().replace("{{git.base}}", "{{git.nada}}"))
        escrever_config(self.home, "claude")
        with self.assertRaisesRegex(Exception, "git.nada"):
            montar.montar(config.carregar(config.Caminhos(home=self.home)), raiz, config.Caminhos(home=self.home))


if __name__ == "__main__":
    unittest.main()
