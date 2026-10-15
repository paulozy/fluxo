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
from fluxo.ferramentas import codex

UUID_FALSO = "-".join(["0123abcd", "0123", "4abc", "8abc", "0123456789ab"])
TOOLS_KIRO = {
    "v2": {"read", "write", "shell", "grep", "glob", "web_search", "web_fetch"},
    "v3": {"read", "write", "shell", "web", "subagent"},
}


def gerar(cenario, **extra):
    with tempfile.TemporaryDirectory() as d:
        cam = escrever_config(d, cenario, **extra)
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
                    if rel.startswith(".kiro/"):
                        self.assertEqual(set(meta), {"name", "description"})

    def test_agentes_copilot_e_claude(self):
        for cenario, pasta, sufixo, n in (("copilot", ".copilot/agents/", ".agent.md", 7), ("claude", ".claude/agents/", ".md", 7),
                                          ("copilot-tudo", ".copilot/agents/", ".agent.md", 8),
                                          ("claude-tudo", ".claude/agents/", ".md", 8)):
            gerado, _ = gerar(cenario)
            agentes = {rel: t for rel, t in gerado.items() if rel.startswith(pasta)}
            self.assertEqual(len(agentes), n, cenario)
            for rel, texto in agentes.items():
                meta, corpo = fonte.ler(texto, rel)
                self.assertTrue(rel.endswith(sufixo))
                self.assertTrue(meta["description"])
                self.assertTrue(corpo.strip())
                if rel.startswith(".copilot/"):
                    self.assertNotIn("model", meta)  # sem `models` na config: herda o da sessão
                    self.assertTrue(set(meta["tools"]) <= {"read", "search", "edit", "execute", "web", "agent"})
                else:
                    self.assertIn(meta["model"], ("opus", "sonnet", "haiku"))

    def test_agentes_kiro(self):
        for motor, extra in (("v2", {}), ("v3", {}), ("v2", {"modules": ["reviewer"]})):
            gerado, avisos = gerar(f"kiro-{motor}", **extra)
            agentes = {rel: json.loads(t) for rel, t in gerado.items() if rel.startswith(".kiro/agents/")}
            self.assertEqual(len(agentes), 8 if extra else 7)
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
                    if not rel.startswith(".claude/"):
                        self.assertNotIn("$ARGUMENTS", texto)

    def test_forge_certo_em_cada_cenario(self):
        copilot, _ = gerar("copilot")
        concluir = copilot[".copilot/skills/concluir/SKILL.md"]
        self.assertIn("glab mr create", concluir)
        self.assertNotIn("gh pr create", concluir)
        self.assertNotIn("--remove-source-branch", concluir)  # apagar a branch no merge é decisão do projeto
        feedback = copilot[".copilot/skills/pr-feedback/SKILL.md"]
        self.assertIn("glab api --hostname gitlab.exemplo.com", feedback)
        self.assertNotIn("gh api", feedback)
        self.assertNotIn("/revisar-pr", concluir)  # módulo reviewer desligado
        claude, _ = gerar("claude")
        self.assertIn("gh pr create --base develop", claude[".claude/skills/concluir/SKILL.md"])
        self.assertNotIn("glab", claude[".claude/skills/pr-feedback/SKILL.md"])

    def test_comandos_do_forge_linha_a_linha(self):
        """Invariantes por linha, que o golden não garante: regenerar o golden aceitaria um erro."""
        for cenario in ("copilot-tudo", "claude-tudo"):
            gerado, _ = gerar(cenario)
            for rel, texto in gerado.items():
                if not rel.endswith(".md"):
                    continue
                for n, linha in enumerate(texto.splitlines(), 1):
                    with self.subTest(cenario=cenario, arquivo=rel, linha=n):
                        if "glab api" in linha:
                            self.assertIn("--hostname gitlab.exemplo.com", linha)
                        if "glab mr" in linha and " -R " in linha:
                            self.assertIn("-R https://gitlab.exemplo.com/", linha)
                        self.assertNotRegex(linha, r"projects/<[^>%]*/")  # o projeto sempre codificado
                        self.assertNotRegex(linha, r"\bbody=(?!@)")  # resposta sempre por arquivo
                        if cenario == "copilot-tudo":
                            self.assertNotRegex(linha, r"`gh |\bgh (api|pr)\b")
                        else:
                            self.assertNotIn("glab", linha)
                        if re.search(r"\bgit\b.*\bpush\b", linha):  # push sem força, nunca numa base
                            self.assertNotRegex(linha, r"--force|\s-f\b|\s\+\S")
                        # branch nova a partir de uma base sempre com --no-track (sem ele, o git sugere push na
                        # base); a partir da própria branch remota (o /pr-feedback), rastrear é o certo
                        if "worktree add" in linha and " -b " in linha and "'origin/<branch>'" not in linha:
                            self.assertIn("--no-track", linha)
                        if "glab mr " in linha and "glab mr create" in linha:
                            self.assertRegex(linha, r"cd |-R https://")  # o host vem do remote ou da URL

    def test_gitlab_sem_host_e_recusado(self):
        with self.assertRaisesRegex(montar.ErroMontagem, "gitlab.host está vazio"):
            gerar("copilot", gitlab={"host": " "})

    def test_revisao_vem_antes_do_push(self):
        for cenario in ("copilot-tudo", "claude-tudo"):
            gerado, _ = gerar(cenario)
            concluir = next(t for rel, t in gerado.items() if rel.endswith("skills/concluir/SKILL.md"))
            self.assertLess(concluir.index("/revisar-pr"), concluir.index("1. **Resumo"))
            self.assertLess(concluir.index("/revisar-pr"), concluir.index("Push da branch"))

    def test_promote(self):
        tudo, _ = gerar("copilot-tudo")
        skill = tudo[".copilot/skills/promover/SKILL.md"]
        self.assertIn("(staging, main)", skill)
        self.assertNotIn("homolog", skill)  # as bases vêm da config, não do fluxo de origem
        self.assertIn("(normalmente `main`)", skill)  # o último alvo leva os testes
        self.assertLess(skill.index("Mostre antes de publicar"), skill.index("glab mr create"))
        self.assertIn("<id>-<repo>-<alvo>.md", skill)  # dois repos não dividem o arquivo da descrição
        self.assertIn('--title "$(cat ', skill)  # o título original (de outra pessoa) vai por arquivo
        self.assertIn("Siga a skill `/aviso`", skill)
        self.assertIn("merge-requests/<iid>/head", skill)
        instr = tudo[".copilot/instructions/fluxo.instructions.md"]
        self.assertIn("## Depois do QA: promoção para staging, main", instr)
        claude, _ = gerar("claude-promote")
        gh = claude[".claude/skills/promover/SKILL.md"]
        self.assertIn("(main)", gh)  # o padrão
        self.assertIn("pull/<N>/head", gh)
        self.assertNotIn("/aviso", gh)  # sem o módulo messages: o rascunho
        self.assertIn("Monte um rascunho", gh)
        sem, _ = gerar("copilot")
        self.assertNotIn(".copilot/skills/promover/SKILL.md", sem)
        self.assertNotIn("Depois do QA", sem[".copilot/instructions/fluxo.instructions.md"])

    def test_reviewer_ligado(self):
        tudo, _ = gerar("copilot-tudo")
        self.assertIn("/revisar-pr", tudo[".copilot/skills/concluir/SKILL.md"])
        self.assertIn("glab mr diff", tudo[".copilot/skills/revisar-pr/SKILL.md"])
        self.assertIn(".copilot/agents/verificador.agent.md", tudo)
        self.assertIn("`verificador`", tudo[".copilot/instructions/fluxo.instructions.md"])

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


CHAMADA_COM_BARRA = re.compile(r"(?<![\w./~-])/(" + "|".join(map(re.escape, montar.nomes_de_skills(RAIZ))) + r")(?![\w-])")
CONTROLE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")


def ler_toml(texto, rel):
    """O TOML que o fluxo grava: uma `chave = "string básica"` por linha, e a string sai do json.dumps.
    Sem tomllib (o 3.9 não tem, e o repo não usa): confere o formato e devolve os valores."""
    valores = {}
    for linha in texto.splitlines():
        chave, sep, valor = linha.partition(" = ")
        if not sep or not re.fullmatch(r"[a-z_]+", chave) or CONTROLE.search(valor) or not valor.startswith('"'):
            raise AssertionError(f"{rel}: linha fora do formato: {linha[:80]}")
        valores[chave] = json.loads(valor)
    return valores


class Codex(unittest.TestCase):
    def test_agentes_em_toml(self):
        gerado, _ = gerar("copilot-codex")
        tomls = {rel: t for rel, t in gerado.items() if rel.startswith(".codex/agents/")}
        self.assertEqual(len(tomls), 8)
        for rel, texto in tomls.items():
            with self.subTest(agente=rel):
                valores = ler_toml(texto, rel)
                # o Codex lê com deny_unknown_fields: uma chave a mais (como `tools`) derruba o agente
                self.assertLessEqual(set(valores), {"name", "description", "developer_instructions", "model"})
                self.assertEqual(valores["name"], Path(rel).stem)
                self.assertTrue(valores["developer_instructions"].strip())
                self.assertNotIn("model", valores)  # sem `models.codex` na config: herda o da sessão
        so_leitura = "**Read-only.**"
        for nome, tem in (("architect", True), ("researcher", True), ("code-reviewer", True),
                          ("committer", False), ("doc-writer", False), ("debugger", False), ("test-runner", False)):
            with self.subTest(so_leitura=nome):
                self.assertEqual(so_leitura in gerado[f".codex/agents/{nome}.toml"], tem)
        self.assertNotIn(so_leitura, gerado[".copilot/agents/architect.agent.md"])

    def test_models_do_codex(self):
        gerado, _ = gerar("codex", models={"codex": {"strong": "modelo-x"}})
        self.assertEqual(ler_toml(gerado[".codex/agents/architect.toml"], "a")["model"], "modelo-x")
        self.assertNotIn("model", ler_toml(gerado[".codex/agents/debugger.toml"], "d"))

    def test_string_toml_com_caracteres_dificeis(self):
        texto = 'aspas " barra \\ linha\nDEL \x7f acento ção, tab\t e fim'
        valor = codex.toml_str(texto)
        self.assertNotRegex(valor, CONTROLE)
        self.assertIn("\\u007f", valor)
        self.assertEqual(ler_toml(f"x = {valor}", "x"), {"x": texto})

    def test_instrucoes_e_skills_com_o_prefixo_do_codex(self):
        gerado, _ = gerar("codex", modules=["messages", "promote", "reviewer", "task"])
        self.assertFalse(gerado[".codex/AGENTS.md"].startswith("---"))  # markdown puro
        skills = {rel: t for rel, t in gerado.items() if rel.endswith("/SKILL.md")}
        self.assertEqual({rel.split("/")[0] for rel in skills}, {".agents"})
        for rel, texto in skills.items():
            with self.subTest(skill=rel):
                self.assertEqual(set(fonte.ler(texto, rel)[0]), {"name", "description"})
        textos = "\n".join(gerado.values())
        self.assertIn("$planejar", textos)
        self.assertIn("Use depois do $planejar", skills[".agents/skills/implementar/SKILL.md"])  # a description também
        self.assertNotRegex(textos, CHAMADA_COM_BARRA)
        self.assertNotIn("$ARGUMENTS", textos)

    def test_o_prefixo_nao_vaza_entre_ferramentas(self):
        gerado, _ = gerar("copilot-codex")
        do_codex = "\n".join(t for rel, t in gerado.items() if rel.startswith(".codex/"))
        do_copilot = "\n".join(t for rel, t in gerado.items() if rel.startswith(".copilot/"))
        self.assertNotRegex(do_codex, CHAMADA_COM_BARRA)
        self.assertNotIn("$planejar", do_copilot)
        self.assertIn("/planejar", do_copilot)

    def test_pasta_das_skills(self):
        for tools in (["codex", "copilot"], ["copilot", "codex"]):
            with self.subTest(tools=tools):
                gerado, _ = gerar("copilot-codex", tools=tools)
                self.assertEqual([r for r in gerado if r.startswith(".agents/")], [])  # o Copilot também lê ali
                self.assertEqual(len([r for r in gerado if r.startswith(".codex/skills/")]), 10)
        sozinho, _ = gerar("codex")
        self.assertEqual([r for r in sozinho if r.startswith(".codex/skills/")], [])


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
