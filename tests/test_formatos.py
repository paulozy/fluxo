"""O que cada ferramenta exige do arquivo gerado (pelas docs oficiais) e o que não pode sobrar nele."""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from base import CENARIOS, RAIZ, escrever_config
from fluxo import config, fonte, montar

UUID_FALSO = "-".join(["0123abcd", "0123", "4abc", "8abc", "0123456789ab"])
TOOLS_KIRO = {
    "v2": {"read", "write", "shell", "grep", "glob", "web_search", "web_fetch"},
    "v3": {"read", "write", "shell", "web", "subagent"},
}


def gerar(cenario):
    with tempfile.TemporaryDirectory() as d:
        cam = escrever_config(d, cenario)
        arquivos, avisos = montar.montar(config.carregar(cam, montar.modulos_existentes(RAIZ)), RAIZ, cam)
        return {a.rel: a.conteudo.decode() for a in arquivos}, avisos


class Formatos(unittest.TestCase):
    def test_skills(self):
        for cenario in CENARIOS:
            gerado, _ = gerar(cenario)
            skills = {rel: t for rel, t in gerado.items() if rel.endswith("/SKILL.md")}
            self.assertGreaterEqual(len(skills), 6, cenario)
            for rel, texto in skills.items():
                with self.subTest(cenario=cenario, skill=rel):
                    meta, corpo = fonte.ler(texto, rel)
                    self.assertEqual(meta["name"], Path(rel).parent.name)  # o Kiro exige igual à pasta
                    self.assertTrue(0 < len(meta["description"]) <= 1024)
                    self.assertNotIn("requer", meta)  # chave nossa, não vai para a ferramenta
                    if cenario.startswith("kiro"):
                        self.assertEqual(set(meta), {"name", "description"})

    def test_agentes_copilot_e_claude(self):
        for cenario, pasta, sufixo in (("copilot", ".copilot/agents/", ".agent.md"), ("claude", ".claude/agents/", ".md")):
            gerado, _ = gerar(cenario)
            agentes = {rel: t for rel, t in gerado.items() if rel.startswith(pasta)}
            self.assertEqual(len(agentes), 7)
            for rel, texto in agentes.items():
                meta, corpo = fonte.ler(texto, rel)
                self.assertTrue(rel.endswith(sufixo))
                self.assertTrue(meta["description"])
                self.assertTrue(corpo.strip())
                if cenario == "copilot":
                    self.assertNotIn("model", meta)  # sem `modelos` na config: herda o da sessão
                    self.assertTrue(set(meta["tools"]) <= {"read", "search", "edit", "execute", "web", "agent"})
                else:
                    self.assertIn(meta["model"], ("opus", "sonnet", "haiku"))

    def test_agentes_kiro(self):
        for motor in ("v2", "v3"):
            gerado, avisos = gerar(f"kiro-{motor}")
            agentes = {rel: json.loads(t) for rel, t in gerado.items() if rel.startswith(".kiro/agents/")}
            self.assertEqual(len(agentes), 7)
            for rel, ag in agentes.items():
                with self.subTest(motor=motor, agente=rel):
                    self.assertEqual(Path(rel).stem, ag["name"])
                    self.assertTrue(ag["description"] and ag["prompt"].strip())
                    self.assertTrue(set(ag["tools"]) <= TOOLS_KIRO[motor], ag["tools"])
                    self.assertTrue(set(ag.get("allowedTools", [])) <= set(ag["tools"]))
                    self.assertNotIn("shell", ag.get("allowedTools", []))  # shell sempre pede aprovação
                    if motor == "v3":
                        self.assertNotIn("allowedTools", ag)

    def test_nada_sobra_do_template(self):
        for cenario in CENARIOS:
            gerado, _ = gerar(cenario)
            for rel, texto in gerado.items():
                if rel == ".local/bin/fluxo":
                    continue
                with self.subTest(cenario=cenario, arquivo=rel):
                    self.assertNotIn("{{", texto)
                    self.assertNotRegex(texto, r"<!-- (se|senao|fim)\b")
                    self.assertNotRegex(texto, r"/home/|/Users/")
                    if cenario != "claude":
                        self.assertNotIn("$ARGUMENTS", texto)

    def test_forge_certo_em_cada_cenario(self):
        copilot, _ = gerar("copilot")
        concluir = copilot[".copilot/skills/concluir/SKILL.md"]
        self.assertIn("glab mr create", concluir)
        self.assertNotIn("gh pr create", concluir)
        self.assertNotIn(".copilot/skills/pr-feedback/SKILL.md", copilot)  # requer forge:github
        claude, _ = gerar("claude")
        self.assertIn("gh pr create --base develop", claude[".claude/skills/concluir/SKILL.md"])

    def test_instrucoes_listam_os_agentes(self):
        gerado, _ = gerar("kiro-v2")
        texto = gerado[".kiro/steering/fluxo.md"]
        self.assertFalse(texto.startswith("---"))  # sem frontmatter: inclusão always, a única que o V2 carrega
        for nome in ("architect", "researcher", "test-runner", "code-reviewer"):
            self.assertIn(f"`{nome}`", texto)
        self.assertLess(texto.index("## Método"), texto.index("## Entrega"))  # ordem pelo prefixo NN
        copilot, _ = gerar("copilot")
        self.assertEqual(fonte.ler(copilot[".copilot/instructions/fluxo.instructions.md"])[0], {"applyTo": "**"})

    @unittest.skipUnless(shutil.which("kiro-cli"), "sem kiro-cli nesta máquina")
    def test_kiro_cli_valida(self):
        for motor in ("v2", "v3"):
            with tempfile.TemporaryDirectory() as d:
                gerado, _ = gerar(f"kiro-{motor}")
                p = Path(d) / "architect.json"
                p.write_text(gerado[".kiro/agents/architect.json"])
                r = subprocess.run(["kiro-cli", "agent", "validate", "--path", str(p)],
                                   capture_output=True, text=True, timeout=30)
                if "log in" in (r.stdout + r.stderr):
                    self.skipTest("kiro-cli sem login: o validate exige")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class SemDados(unittest.TestCase):
    def test_repo_limpo(self):
        from fluxo import varredura
        self.assertEqual(varredura.varrer(RAIZ), [])

    def test_acha_o_que_foi_plantado(self):
        from fluxo import varredura
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d) / "repo"
            (raiz / "tests").mkdir(parents=True)
            shutil.copy(RAIZ / varredura.LISTA, raiz / varredura.LISTA)
            (raiz / "a.md").write_text("ok\nnada aqui\n")
            # montados aqui, para o próprio teste não sujar a varredura do repo
            (raiz / "b.md").write_text(f"o caminho /{'home'}/alguem/x\nid {UUID_FALSO}\n")
            extra = Path(d) / "proibidos.txt"
            extra.write_text("Acme\n")
            (raiz / "c.md").write_text("trabalho na ACME\n")
            achados = varredura.varrer(raiz, extra=extra)
            self.assertEqual([(a, n, o) for a, n, o in achados if a != "c.md"],
                             [("b.md", 1, "caminho de home"), ("b.md", 2, "UUID")])
            self.assertEqual([a for a, _, _ in achados if a == "c.md"], ["c.md"])
            hashes = varredura.proibidos(RAIZ)
            self.assertTrue(hashes and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes))

    def test_termos_de_varias_palavras(self):
        from fluxo import varredura
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d) / "repo"
            (raiz / "tests").mkdir(parents=True)
            (raiz / varredura.LISTA).write_text(varredura._hash("Acme Corp") + "\n")
            (raiz / "a.md").write_text("na ACME-Corp, o time\nsó acme, sem corp\n")
            extra = Path(d) / "proibidos.txt"
            extra.write_text("Outra Empresa\n")
            (raiz / "b.md").write_text("trabalho na outra-empresa\n")
            achados = varredura.varrer(raiz, extra=extra)
            self.assertEqual([(a, n) for a, n, _ in achados], [("a.md", 1), ("b.md", 1)])


class Python39(unittest.TestCase):
    """Rede de segurança barata. No 3.12, o feature_version=(3, 9) ainda aceita `int | None` em anotação
    e f-string com aspas aninhadas: a garantia real é a suíte no `python:3.9-slim` (README)."""

    def test_sintaxe_e_modulos(self):
        import ast
        fontes = list((RAIZ / "lib").rglob("*.py")) + list((RAIZ / "tests").glob("*.py")) + [RAIZ / "fluxo"]
        for p in fontes:
            texto = p.read_text(encoding="utf-8")
            with self.subTest(arquivo=p.name):
                ast.parse(texto, filename=str(p), feature_version=(3, 9))
                self.assertNotRegex(texto, r"^\s*(import|from)\s+tomllib", "tomllib só existe a partir do 3.11")
                self.assertNotRegex(texto, r"zip\([^)]*strict=", "zip com strict só existe a partir do 3.10")


if __name__ == "__main__":
    unittest.main()
