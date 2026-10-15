"""install/uninstall num HOME temporário: golden, idempotência, conflitos, remoção e segurança.

Golden: `tests/esperado/<cenario>/` guarda a árvore gerada byte a byte. Depois de mudar uma fonte de
propósito, regenere com `FLUXO_ATUALIZAR_ESPERADO=1 python3 -m unittest discover -s tests` e revise o diff.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import CENARIOS, RAIZ, arvore, escrever_config
from fluxo import cli, config, fonte, instalar, montar, panel

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

    def test_force_faz_backup(self):
        alheio = self.home / ".claude" / "agents" / "code-reviewer.md"
        alheio.parent.mkdir(parents=True)
        alheio.write_text("meu\n")
        self.assertEqual(self.instalar("claude", "--force")[0], 0)
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
        self.instalar("kiro-v2", modules=["messages"])
        skill = self.home / ".kiro" / "skills" / "aviso" / "SKILL.md"
        self.assertTrue(skill.exists())
        code, out, _ = self.instalar("kiro-v2")  # módulo desligado
        self.assertEqual(code, 0)
        self.assertIn("remover", out)
        self.assertFalse(skill.parent.exists())  # a pasta da skill sai junto

    def test_removido_mas_editado_fica(self):
        self.instalar("kiro-v2", modules=["messages"])
        skill = self.home / ".kiro" / "skills" / "aviso" / "SKILL.md"
        skill.write_text("editei\n")
        code, out, _ = self.instalar("kiro-v2")
        self.assertEqual(code, 0)
        self.assertIn("  manter     ~/.kiro/skills/aviso/SKILL.md  (saiu do fluxo, mas foi editado", out)
        self.assertEqual(skill.read_text(), "editei\n")
        cam = config.Caminhos(home=self.home)
        self.assertNotIn(".kiro/skills/aviso/SKILL.md", instalar.ler_manifesto(cam.manifesto))

    def test_troca_de_engine_atualiza_os_agentes(self):
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
        self.assertEqual(self.instalar("claude", "--force")[0], 0)
        self.assertEqual(fora.read_text(), "não é do fluxo\n")  # o link foi trocado, não seguido
        self.assertFalse(destino.is_symlink())
        self.assertTrue([b for b in destino.parent.glob("fluxo.md.fluxo-bak-*") if b.is_symlink()])

    def test_force_e_uninstall_devolvem_o_original(self):
        meu = self.home / ".claude" / "agents" / "code-reviewer.md"
        meu.parent.mkdir(parents=True)
        meu.write_text("meu\n")
        self.instalar("claude", "--force")
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
        self.assertEqual(rodar("init", "--home", str(self.home), "--tool", "kiro", "--forge", "github")[0], 0)
        code, _, err = rodar("init", "--home", str(self.home), "--tool", "kiro", "--forge", "github")
        self.assertEqual(code, 2)
        self.assertIn("--force", err)
        rodar("modules", "--home", str(self.home), "--enable", "watch", "reviewer")
        rodar("init", "--home", str(self.home), "--tool", "kiro", "--forge", "github", "--force",
              "--promote-to", "staging, main")
        cfg = config.carregar(config.Caminhos(home=self.home), montar.modulos_existentes(RAIZ))
        self.assertEqual(cfg["promote"]["targets"], ["staging", "main"])
        self.assertEqual(cfg["modules"], ["watch", "reviewer"])  # o --force não apaga os módulos ligados

    def test_duas_ferramentas_e_o_panel_so_onde_cabe(self):
        self.assertEqual(self.instalar("copilot-claude")[0], 0)
        self.assertTrue((self.home / ".copilot" / "hooks" / "fluxo.json").exists())
        self.assertNotIn("hook", " ".join(arvore(self.home, (".claude",))))
        # a sessão que abre outra a abre na própria ferramenta, e não na padrão
        self.assertIn("fluxo task <ID> --tool claude", (self.home / ".claude" / "rules" / "fluxo.md").read_text())
        copilot = (self.home / ".copilot" / "instructions" / "fluxo.instructions.md").read_text()
        self.assertIn("fluxo task <ID> --tool copilot", copilot)

    def test_tirar_e_por_de_volta_uma_ferramenta(self):
        self.instalar("copilot-claude")
        copilot = arvore(self.home, (".copilot", ".config/systemd"))
        code, out, _ = self.instalar("copilot-claude", tools=["copilot"])
        self.assertEqual(code, 0)
        self.assertEqual(arvore(self.home, (".claude",)), {})
        self.assertEqual(arvore(self.home, (".copilot", ".config/systemd")), copilot)  # a outra e a máquina não mudam
        code, out, _ = self.instalar("copilot-claude")
        self.assertEqual(code, 0)
        self.assertIn("  criar      ~/.claude/rules/fluxo.md", out)

    def test_por_de_volta_com_arquivo_editado_e_conflito(self):
        """O editado fica quando a ferramenta sai, e sai do manifesto: na volta, ele não é mais do fluxo."""
        self.instalar("copilot-claude")
        regra = self.home / ".claude" / "rules" / "fluxo.md"
        regra.write_text("editei\n")
        self.assertEqual(self.instalar("copilot-claude", tools=["copilot"])[0], 0)
        self.assertEqual(regra.read_text(), "editei\n")
        code, out, err = self.instalar("copilot-claude")
        self.assertEqual(code, 3)
        self.assertIn("conflito   ~/.claude/rules/fluxo.md", out)
        self.assertIn("Nada foi gravado", err)
        self.assertEqual(sorted(arvore(self.home, (".claude",))), [".claude/rules/fluxo.md"])  # nada foi gravado

    def test_tirar_o_kiro_com_o_agente_padrao_avisa(self):
        self.instalar("kiro-v2-panel")
        cam = config.Caminhos(home=self.home)
        self.assertNotIn("panel disable", self.instalar("kiro-v2-panel", tools=["copilot"])[1])  # sem enable antes
        self.instalar("kiro-v2-panel")
        cam.estado.mkdir(parents=True, exist_ok=True)
        (cam.estado / "painel-agente-anterior.txt").write_text("kiro_default")  # o `panel enable` rodou
        code, out, _ = self.instalar("kiro-v2-panel", tools=["copilot"])
        self.assertEqual(code, 0)
        self.assertIn("aviso: o agente fluxo é o padrão do Kiro (fluxo panel enable): rode fluxo panel disable", out)
        # o disable devolve o anterior mesmo sem o kiro em tools e sem o módulo
        escrever_config(self.home, "kiro-v2-panel", tools=["copilot"], modules=[])
        chamadas = []
        run = lambda argv, **k: chamadas.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
        with mock.patch("fluxo.cli.subprocess.run", run):
            code, out, err = rodar("panel", "disable", "--home", str(self.home))
        self.assertEqual((code, chamadas), (0, [["kiro-cli", "agent", "set-default", "kiro_default"]]), err)
        self.assertFalse((cam.estado / "painel-agente-anterior.txt").exists())

    def test_modulo_de_uma_ferramenta_nao_vaza_para_a_outra(self):
        """Com o claude primeiro: o requer vale para cada ferramenta, e não só para a padrão."""
        repo = self.home / "repo"
        shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "esperado", "__pycache__"))
        mod = repo / "modulos" / "so-copilot"
        for pasta in ("instrucoes", "skills/so-copilot", "agentes"):
            (mod / pasta).mkdir(parents=True)
        (mod / "modulo.json").write_text(json.dumps({"descricao": "x", "requer": "tool:copilot"}))
        (mod / "instrucoes" / "27-so-copilot.md").write_text("## Só no Copilot\n")
        (mod / "skills" / "so-copilot" / "SKILL.md").write_text("---\nname: so-copilot\ndescription: x\n---\ncorpo\n")
        (mod / "agentes" / "so-copilot.md").write_text(
            "---\nname: so-copilot\ndescription: x\ntools: [read]\nmodel: light\n---\ncorpo\n")
        metodo = repo / "nucleo" / "instrucoes" / "10-metodo.md"
        metodo.write_text(metodo.read_text() + "\n<!-- se module:so-copilot -->\nMARCA-DO-MODULO\n<!-- fim -->\n")
        cam = escrever_config(self.home, "claude", tools=["claude", "copilot"], modules=["so-copilot"])
        arquivos, _ = montar.montar(config.carregar(cam, montar.modulos_existentes(repo)), repo, cam)
        gerado = {a.rel: a.conteudo.decode() for a in arquivos}
        copilot, claude = gerado[".copilot/instructions/fluxo.instructions.md"], gerado[".claude/rules/fluxo.md"]
        for trecho in ("## Só no Copilot", "MARCA-DO-MODULO", "`so-copilot`"):
            self.assertIn(trecho, copilot)
            self.assertNotIn(trecho, claude)
        self.assertIn(".copilot/skills/so-copilot/SKILL.md", gerado)
        self.assertIn(".copilot/agents/so-copilot.agent.md", gerado)
        self.assertEqual([r for r in gerado if r.startswith(".claude/") and "so-copilot" in r], [])

    def test_models_por_ferramenta(self):
        cam = escrever_config(self.home, "copilot-claude", models={"claude": {"strong": "a"}, "copilot": {"strong": "b"}})
        arquivos, _ = montar.montar(config.carregar(cam, montar.modulos_existentes(RAIZ)), RAIZ, cam)
        gerado = {a.rel: a.conteudo.decode() for a in arquivos}
        self.assertEqual(fonte.ler(gerado[".claude/agents/architect.md"])[0]["model"], "a")
        self.assertEqual(fonte.ler(gerado[".copilot/agents/architect.agent.md"])[0]["model"], "b")
        self.assertEqual(fonte.ler(gerado[".claude/agents/debugger.md"])[0]["model"], "sonnet")  # sem override: o do adaptador
        self.assertNotIn("model", fonte.ler(gerado[".copilot/agents/debugger.agent.md"])[0])

    def test_destino_repetido_entre_ferramentas_e_erro(self):
        """Mesmo com bytes iguais: juntar esconderia um arquivo gerado duas vezes."""
        cam = escrever_config(self.home, "copilot-claude")
        cfg = config.carregar(cam, montar.modulos_existentes(RAIZ))
        mesmo = lambda cfg, tool, Arquivo, **_: ([Arquivo("igual.txt", b"x")], [])
        with mock.patch.object(panel, "arquivos_da_ferramenta", mesmo), \
                mock.patch.object(montar, "modulos_da_ferramenta", lambda cfg, tool, raiz: list(cfg["modules"])):
            with self.assertRaisesRegex(montar.ErroMontagem, "mesmo destino: igual.txt"):
                montar.montar(cfg, RAIZ, cam)

    def test_skills_do_codex_mudam_de_pasta_com_o_copilot(self):
        """Com o copilot, ~/.codex/skills; sem ele, ~/.agents/skills. A troca é um install só, sem conflito."""
        self.assertEqual(self.instalar("codex")[0], 0)
        self.assertTrue((self.home / ".agents" / "skills" / "planejar" / "SKILL.md").exists())
        code, out, err = self.instalar("codex", tools=["codex", "copilot"])
        self.assertEqual(code, 0, err)
        self.assertEqual(arvore(self.home, (".agents",)), {})
        self.assertTrue((self.home / ".codex" / "skills" / "planejar" / "SKILL.md").exists())
        code, out, err = self.instalar("codex")
        self.assertEqual(code, 0, err)
        self.assertEqual(arvore(self.home, (".codex/skills",)), {})
        self.assertTrue((self.home / ".agents" / "skills" / "planejar" / "SKILL.md").exists())
        self.assertEqual(rodar("uninstall", "--home", str(self.home))[0], 0)
        self.assertEqual(arvore(self.home), {})

    def test_hooks_do_codex_pedem_aprovacao(self):
        aviso = "aviso: hooks do panel no Codex: aprove-os em /hooks"
        code, out, _ = self.instalar("copilot-codex")
        self.assertEqual(code, 0)
        self.assertIn(aviso, out)
        self.assertNotIn("aprove-os", self.instalar("copilot-codex")[1])  # nada mudou: nada a aprovar de novo
        with mock.patch.dict(panel.EVENTOS, {"codex": {"SessionStart": "inicio"}}):  # o fluxo mudou os hooks
            code, out, _ = self.instalar("copilot-codex")
        self.assertIn("  atualizar  ~/.codex/hooks.json", out)
        self.assertIn(aviso, out)
        self.assertNotIn("aprove-os", self.instalar("copilot-codex", tools=["copilot"])[1])  # removido: nada a aprovar

    def test_force_por_cima_dos_hooks_do_usuario_tambem_avisa(self):
        hooks = self.home / ".codex" / "hooks.json"
        hooks.parent.mkdir(parents=True)
        hooks.write_text('{"hooks": {}}\n')
        self.assertEqual(self.instalar("copilot-codex")[0], 3)
        code, out, _ = self.instalar("copilot-codex", "--force")
        self.assertEqual(code, 0)
        self.assertIn("aprove-os em /hooks", out)

    def test_limite_da_description_depois_do_template(self):
        repo = self.home / "repo"
        shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "esperado", "__pycache__"))
        p = repo / "nucleo" / "skills" / "revisar" / "SKILL.md"
        meta, corpo = fonte.ler(p.read_text())
        # 1017 caracteres na fonte; o {{dados}} vira ~/.local/share/fluxo, e o texto passa de 1024
        p.write_text(fonte.escrever(dict(meta, description="a" * 990 + "{{dados}}" * 3), corpo))
        cam = escrever_config(self.home, "claude")
        with self.assertRaisesRegex(montar.ErroMontagem, "até 1024"):
            montar.montar(config.carregar(cam, montar.modulos_existentes(repo)), repo, cam)

    def test_init_force_mantem_as_opcoes(self):
        """O --force refaz os eixos (ferramentas, forge, os…) e mantém o que o usuário ajustou depois."""
        cam = config.Caminhos(home=self.home)
        self.assertEqual(rodar("init", "--home", str(self.home), "--tool", "claude", "--forge", "github")[0], 0)
        opcoes = {"modules": ["task"], "models": {"claude": {"strong": "x"}}, "task": {"max": 3},
                  "watch": {"interval": 120}, "promote": {"targets": ["staging", "main"]}}
        cam.config.write_text(json.dumps(dict(json.loads(cam.config.read_text()), **opcoes)))
        self.assertEqual(rodar("init", "--home", str(self.home), "--tool", "copilot", "--forge", "gitlab",
                               "--gitlab-host", "g.exemplo.com", "--force")[0], 0)
        novo = json.loads(cam.config.read_text())
        self.assertEqual({k: novo[k] for k in opcoes}, opcoes)
        self.assertEqual((novo["tools"], novo["forge"]), (["copilot"], "gitlab"))

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
