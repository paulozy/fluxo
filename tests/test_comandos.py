"""Os nomes que o fluxo grava e cita: o comando que outra ferramenta chama depois tem de existir no parser,
nenhuma fonte cita grafia antiga, e cada módulo acha o próprio código."""
import contextlib
import io
import json
import plistlib
import re
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import CENARIOS, RAIZ, escrever_config
from fluxo import cli, config, montar, watch

CHAMADA = re.compile(r"\.local/bin/fluxo\"?\s+(.+)$")
SEM_CODIGO = {"promote", "reviewer"}  # só fontes (skills, agentes, instruções)

# o que a 0.5.0 renomeou: procurado como comando, e não como palavra solta ("a trava" é texto legítimo)
ANTIGOS = [
    re.compile(r"\bfluxo (modulos|empacotar|varrer|trava|vigia|painel)\b"),
    re.compile(r"\bfluxo msg (copiar|tom)\b"),
    re.compile(r"\bfluxo (lock|watch|panel|msg) (pegar|soltar|quebrar|rodar|ligar|desligar|copiar|tom)\b"),
    re.compile(r"--(ferramenta|forcar|verboso|codigo-dir|saida|ligar|desligar|lista|nome|projeto|sessao|esperar"
               r"|intervalo|texto|evento|motor|alvos|so)\b"),
    re.compile(r"\bFLUXO_SESSAO\b"),
]
ONDE_PROCURAR = ("nucleo", "modulos", "lib", "adaptadores", ".githooks", "fluxo", "README.md", "docs", "CLAUDE.md")
# fora: o CHANGELOG, que guarda o histórico dos nomes, e os testes, que usam a grafia antiga de propósito


def _textos(valor):
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, dict):
        for v in valor.values():
            yield from _textos(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from _textos(v)


OU_VERDADEIRO = re.compile(r"\s+\|\|\s+true$")  # os hooks do Kiro: o comando nunca sai com 1


def chamadas(rel, conteudo):
    """Os argv de `fluxo …` dentro de um arquivo gerado (hooks em JSON, plist, unit do systemd)."""
    if rel.endswith(".json"):
        textos = list(_textos(json.loads(conteudo)))
    elif rel.endswith(".plist"):
        textos = list(_textos(plistlib.loads(conteudo)))
    else:
        textos = conteudo.decode().splitlines()
    out = []
    for t in textos:
        m = CHAMADA.search(t)
        if m:
            comando = m.group(1)
            if rel.startswith(".kiro/") and not OU_VERDADEIRO.search(comando):
                raise AssertionError(f"{rel}: o comando do Kiro tem de terminar em `|| true`: {comando}")
            out.append(shlex.split(OU_VERDADEIRO.sub("", comando)))
    return out


def gerado(cenario, **extra):
    with tempfile.TemporaryDirectory() as d:
        cam = escrever_config(d, cenario, **extra)
        arquivos, _ = montar.montar(config.carregar(cam, montar.modulos_existentes(RAIZ)), RAIZ, cam)
        return [(a.rel, a.conteudo) for a in arquivos if a.rel != ".local/bin/fluxo"]


class ComandosGravados(unittest.TestCase):
    def assertParseia(self, argv, onde):
        try:
            cli.parser().parse_args(argv)
        except SystemExit:
            self.fail(f"{onde}: `fluxo {shlex.join(argv)}` não existe no parser")

    def test_hooks_units_e_plist(self):
        casos = [(c, {}) for c in CENARIOS] + [("kiro-v3", {"modules": ["panel"]})]
        com_chamada = set()
        for cenario, extra in casos:
            for rel, conteudo in gerado(cenario, **extra):
                for argv in chamadas(rel, conteudo):
                    com_chamada.add(rel)
                    with self.subTest(cenario=cenario, arquivo=rel):
                        self.assertParseia(argv, rel)
        # se a busca deixar de achar um deles, o teste não valeria para ele
        self.assertLessEqual({".copilot/hooks/fluxo.json", ".kiro/hooks/fluxo.json", ".kiro/agents/fluxo.json", ".codex/hooks.json",
                              "Library/LaunchAgents/dev.fluxo.watch.plist", ".config/systemd/user/fluxo-watch.service"},
                             com_chamada)

    def test_loop_do_watch_no_wsl(self):
        with tempfile.TemporaryDirectory() as d:
            cam = escrever_config(d, "kiro-v2", modules=["watch"])
            cfg = config.carregar(cam, montar.modulos_existentes(RAIZ))
            abertos = []
            with mock.patch("fluxo.watch.tmux.abrir_janela",
                            side_effect=lambda nome, pasta, comando, alvo, run: abertos.append(comando) or ("@1", "%1", 1)), \
                    mock.patch("fluxo.watch.tmux.panes_vivos", return_value=set()):
                self.assertEqual(watch.ligar(cfg, cam, run=None, dentro_do_tmux=True)[0], 0)
        (comando,) = abertos
        self.assertTrue(comando[0].endswith("/.local/bin/fluxo"))
        self.assertParseia(comando[1:], "watch.ligar")


class GrafiaAntiga(unittest.TestCase):
    def test_nenhuma_fonte_cita_nome_antigo(self):
        achados = []
        for pasta in ONDE_PROCURAR:
            raiz = RAIZ / pasta
            for p in sorted(raiz.rglob("*")) if raiz.is_dir() else [raiz]:
                if not p.is_file() or "__pycache__" in p.parts:
                    continue
                for n, linha in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                    if any(r.search(linha) for r in ANTIGOS):
                        achados.append(f"{p.relative_to(RAIZ)}:{n}: {linha.strip()}")
        self.assertEqual(achados, [])


class ChamadaDeSkill(unittest.TestCase):
    def test_fontes_chamam_a_skill_pelo_prefixo_da_ferramenta(self):
        """`/planejar` cru sairia errado no Codex (lá é `$planejar`): a fonte usa `{{skill.planejar}}`."""
        chamada = re.compile(r"(?<![\w./~-])/(" + "|".join(map(re.escape, montar.nomes_de_skills(RAIZ))) + r")(?![\w-])")
        fontes = list((RAIZ / "nucleo").rglob("*.md")) + list((RAIZ / "adaptadores").rglob("*.json")) + [
            p for sub in ("instrucoes", "skills", "agentes") for p in (RAIZ / "modulos").glob(f"*/{sub}/**/*.md")]
        achados = [f"{p.relative_to(RAIZ)}:{n}: {linha.strip()}" for p in sorted(fontes)
                   for n, linha in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if chamada.search(linha)]
        self.assertGreater(len(fontes), 20)
        self.assertEqual(achados, [])

    def test_o_prefixo_vem_do_adaptador(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "esperado", "__pycache__"))
            adapt = repo / "adaptadores" / "ferramenta" / "claude.json"
            adapt.write_text(json.dumps(dict(json.loads(adapt.read_text()), prefixo="%")))
            cam = escrever_config(Path(d) / "casa", "claude-tudo")
            arquivos, _ = montar.montar(config.carregar(cam, montar.modulos_existentes(repo)), repo, cam)
            gerado = {a.rel: a.conteudo.decode() for a in arquivos}
        implementar = gerado[".claude/skills/implementar/SKILL.md"]
        self.assertIn("# %implementar", implementar)
        self.assertIn("%planejar", implementar.split("---")[1])  # a description também passa pelo template
        self.assertIn("%revisar-pr", gerado[".claude/agents/verificador.md"].split("---")[1])
        self.assertNotRegex("\n".join(gerado.values()), r"(?<![\w./~-])/(planejar|implementar|concluir)(?![\w-])")


class Modulos(unittest.TestCase):
    def test_cada_modulo_acha_o_proprio_codigo(self):
        """O `montar` importa `fluxo.<módulo>` e, se ele não existe, segue sem as extras: um .py com outro
        nome sumiria com os hooks e as units sem erro nenhum."""
        for m in montar.modulos_existentes(RAIZ):
            with self.subTest(modulo=m):
                tem = (RAIZ / "lib" / "fluxo" / f"{m}.py").exists()
                self.assertEqual(tem, m not in SEM_CODIGO)


if __name__ == "__main__":
    unittest.main()


class Parser(unittest.TestCase):
    """A ajuda agrupada e as ações como subcomandos, sem quebrar o argv de quem já chama o fluxo: os hooks instalados,
    o processo de fundo do validate, o loop do watch no WSL, as skills e os testes."""

    def parse(self, *argv):
        return cli.parser().parse_args(list(argv))

    def ajuda(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.parse(*argv, "-h")
        return out.getvalue()

    def test_o_argv_de_hoje_continua_valendo(self):
        casos = (["validate", "_rodar", "receita/api", "--home", "/h"], ["hook", "--tool", "copilot", "--event", "x"],
                 ["panel", "--home", "/h", "--text"], ["panel", "enable"], ["watch"], ["watch", "tick"],
                 ["watch", "loop", "--interval", "60"], ["watch", "enable", "--target", "main:"],
                 ["lock", "acquire", "--id", "P-1", "--project", "p"], ["lock", "run", "--id", "P-1", "--wait", "5"],
                 ["lock", "release", "--session", "s"], ["lock", "status"], ["lock", "break", "--force"],
                 ["msg", "copy"], ["msg", "copy", "aviso.md"], ["msg", "tone"], ["task"], ["task", "P-1", "-p", "x"],
                 ["modules", "--enable"], ["config"], ["config", "set", "tools", "copilot", "claude"],
                 ["config", "list", "--json"], ["validate"], ["validate", "status", "--recipe", "api", "--wait", "9"],
                 ["validate", "recipe", "api", "--check"], ["validate", "enable", "--profile", "x"], ["status"], [])
        for argv in casos:
            with self.subTest(argv=argv):
                self.assertTrue(callable(self.parse(*argv).fn))
        self.assertIs(self.parse().fn, cli.cmd_status)
        self.assertEqual(self.parse("validate", "_rodar", "receita/api").alvo, "receita/api")

    def test_flag_comum_antes_e_depois_da_acao(self):
        for argv in (["validate", "--home", "/h", "--no-input", "start", "T1"],
                     ["validate", "start", "T1", "--home", "/h", "--no-input"]):
            with self.subTest(argv=argv):
                args = self.parse(*argv)
                self.assertEqual((args.home, args.no_input, args.acao, args.alvo), ("/h", True, "start", "T1"))
        args = self.parse("validate", "enable")  # o que a ação não tem vem do padrão do pai
        self.assertEqual((args.home, args.no_input, args.recipe, args.fix, args.wait), (None, False, None, False, 0))

    def test_a_ajuda(self):
        topo = self.ajuda()
        for _, nomes in cli.GRUPOS:
            for nome in nomes:
                self.assertIn(f"\n  {nome} ", topo)
        for oculto in ("hook", "_rodar", "--home", "==SUPPRESS=="):
            self.assertNotIn(oculto, topo)
        validar = self.ajuda("validate")
        self.assertIn("recipe", validar)
        self.assertIn("exemplos:", validar)
        for oculto in ("_rodar", "--home", "==SUPPRESS=="):
            self.assertNotIn(oculto, validar)
        start = self.ajuda("validate", "start")
        self.assertIn("--recipe", start)
        self.assertNotIn("--profile", start)  # é só do enable

    def test_todo_comando_tem_grupo(self):
        visiveis = {n for n in cli.parser()._subparsers._group_actions[0].choices if n != "hook"}
        self.assertEqual(visiveis, {n for _, nomes in cli.GRUPOS for n in nomes})

    def test_a_versao_e_a_do_changelog(self):
        topo = re.search(r"^## \[([0-9.]+)\]", (RAIZ / "CHANGELOG.md").read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual(cli.__version__, topo)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            self.parse("--version")
        self.assertEqual(out.getvalue(), f"fluxo {topo}\n")

    def test_erro_de_uso_em_portugues(self):
        for argv, trecho in ((["lock"], "fluxo lock: falta <ação>"), (["validate", "xis"], "'xis' não existe aqui"),
                             (["task", "--xis"], "não conheço --xis")):
            err = io.StringIO()
            with self.subTest(argv=argv), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as fim:
                self.parse(*argv)
            self.assertEqual(fim.exception.code, 2)
            self.assertIn(trecho, err.getvalue())


class Status(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def status(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["status", "--home", str(self.home)], RAIZ)
        return code, out.getvalue()

    def test_sem_config(self):
        code, out = self.status()
        self.assertEqual(code, 0)
        self.assertIn("sem config", out)
        self.assertIn("fluxo init", out)

    def test_com_config(self):
        escrever_config(self.home, "claude-tudo")
        code, out = self.status()
        self.assertEqual(code, 0)
        linhas = {l.split()[0]: l.split(None, 1)[1] for l in out.splitlines()[1:] if l.startswith("  ")}
        self.assertEqual(set(linhas), {"config", "ferramentas", "módulos", "install", "sessões", "validações",
                                       "pr-reviewer", "trava", "watch"})
        self.assertIn("claude · github · linux", linhas["ferramentas"])
        self.assertIn("a gravar", linhas["install"])  # nada foi instalado ainda
        self.assertEqual(linhas["sessões"], "nenhuma aberta")
        self.assertIn("fluxo validate enable", linhas["pr-reviewer"])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["install", "--home", str(self.home)], RAIZ)
        self.assertIn("install      em dia", self.status()[1])

    def test_so_os_modulos_ligados(self):
        escrever_config(self.home, "claude")  # sem módulo nenhum
        linhas = {l.split()[0] for l in self.status()[1].splitlines()[1:] if l.startswith("  ")}
        self.assertEqual(linhas, {"config", "ferramentas", "módulos", "install"})

    def test_ctrl_c_fora_de_uma_pergunta(self):
        escrever_config(self.home, "claude")
        err = io.StringIO()
        with mock.patch.object(cli, "cmd_status", side_effect=KeyboardInterrupt), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["status", "--home", str(self.home)], RAIZ), 130)
        self.assertIn("interrompido", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_config_que_nao_fecha(self):
        escrever_config(self.home, "claude", forge="bitbucket")
        code, out = self.status()
        self.assertEqual(code, 0)
        self.assertIn("a config não fecha", out)
