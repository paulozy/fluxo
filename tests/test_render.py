"""Template das fontes, frontmatter e config."""
import unittest
from pathlib import Path

from base import cfgmod  # noqa: F401 (põe o lib/ no sys.path)
from fluxo import config, fonte, render

EIXOS = {"ferramenta": "copilot", "forge": "gitlab", "so": "mac", "chat": "rascunho", "tracker": "nenhum",
         "motor": "-", "modulo": {"task"}}
VALIDOS = dict(config.EIXOS, motor=config.MOTORES, modulo=("task", "vigia"))
CTX = {"forge": {"pr": "MR"}, "git": {"base": "develop"}}


def r(texto):
    return render.renderizar(texto, CTX, EIXOS, VALIDOS, "t.md")


class Template(unittest.TestCase):
    def test_variaveis(self):
        self.assertEqual(r("abra a {{forge.pr}} contra a {{ git.base }}"), "abra a MR contra a develop")

    def test_variavel_desconhecida_ou_grupo_e_erro(self):
        with self.assertRaisesRegex(render.ErroTemplate, "desconhecida"):
            r("{{forge.cli}}")
        with self.assertRaisesRegex(render.ErroTemplate, "grupo"):
            r("{{forge}}")

    def test_blocos(self):
        texto = "a\n<!-- se forge:github -->\ngh\n<!-- senao -->\nglab\n<!-- fim -->\nb\n"
        self.assertEqual(r(texto), "a\nglab\nb\n")
        self.assertEqual(r("<!-- se nao so:mac -->\nlinux\n<!-- fim -->\nz"), "z")
        self.assertEqual(r("<!-- se chat:slack|rascunho -->\nmsg\n<!-- fim -->"), "msg\n")
        self.assertEqual(r("<!-- se modulo:task -->\ntask\n<!-- fim -->\n<!-- se modulo:vigia -->\nvigia\n<!-- fim -->\n"),
                         "task\n")
        self.assertEqual(r("  <!-- se forge:gitlab -->\n  indentado\n  <!-- fim -->\n"), "  indentado\n")

    def test_marcadores_com_outra_grafia(self):
        self.assertEqual(r("<!--se forge:gitlab-->\nx\n<!--senao-->\ny\n<!--fim-->\n"), "x\n")
        self.assertEqual(r("<!--  SE forge:github  -->\nx\n<!-- Fim -->\n"), "")
        self.assertEqual(r("<!-- se\tforge:gitlab -->\nx\n  <!-- senao -->\ny\n<!-- fim -->\n"), "x\n")

    def test_chaves_fora_do_formato_e_erro(self):
        for texto in ("{{git-base}}", "{{ forge.pr }", "{{forge.pr|x}}", "fim }}"):
            with self.subTest(texto=texto), self.assertRaisesRegex(render.ErroTemplate, "chaves duplas"):
                r(texto)

    def test_variavel_do_ramo_nao_usado_tambem_e_validada(self):
        with self.assertRaisesRegex(render.ErroTemplate, "desconhecida"):
            r("<!-- se forge:github -->\n{{forge.nada}}\n<!-- fim -->\n")

    def test_condicao_invalida_e_erro(self):
        for cond in ("forge:bitbucket", "cor:azul", "forge", "modulo:painel"):
            with self.subTest(cond=cond), self.assertRaises(render.ErroTemplate):
                r(f"<!-- se {cond} -->\nx\n<!-- fim -->\n")

    def test_aninhado_ou_fora_do_lugar_e_erro(self):
        with self.assertRaisesRegex(render.ErroTemplate, "aninhado"):
            r("<!-- se forge:gitlab -->\n<!-- se so:mac -->\nx\n<!-- fim -->\n<!-- fim -->\n")
        with self.assertRaisesRegex(render.ErroTemplate, "fora do lugar"):
            r("texto <!-- se forge:gitlab --> no meio\n")
        with self.assertRaisesRegex(render.ErroTemplate, "fora do lugar"):
            r("<!-- fim -->\n")


class Fonte(unittest.TestCase):
    def test_ler(self):
        meta, corpo = fonte.ler('---\nname: x\ndescription: "a: b"\ntools: [read, shell]\n---\ncorpo\n')
        self.assertEqual(meta, {"name": "x", "description": "a: b", "tools": ["read", "shell"]})
        self.assertEqual(corpo, "corpo\n")
        self.assertEqual(fonte.ler("sem frontmatter"), ({}, "sem frontmatter"))

    def test_erros(self):
        with self.assertRaisesRegex(fonte.ErroFonte, "fechamento"):
            fonte.ler("---\nname: x\n")
        with self.assertRaisesRegex(fonte.ErroFonte, "formato"):
            fonte.ler("---\n- item\n---\n")

    def test_escrever_pula_none_e_e_yaml_valido(self):
        texto = fonte.escrever({"name": "x", "model": None, "tools": ["a", "b"], "d": 'aspas " e: dois pontos'}, "\n\ncorpo")
        self.assertEqual(texto, '---\nname: "x"\ntools: ["a", "b"]\nd: "aspas \\" e: dois pontos"\n---\ncorpo')
        self.assertEqual(fonte.ler(texto)[0], {"name": "x", "tools": ["a", "b"], "d": 'aspas " e: dois pontos'})


class Config(unittest.TestCase):
    def test_padroes_e_motor(self):
        cfg = config.validar({"versao": 1, "ferramenta": "kiro", "forge": "github", "so": "wsl"})
        self.assertEqual((cfg["chat"], cfg["git"]["base"], cfg["kiro"]["motor"], cfg["modulos"]), ("rascunho", "develop", "v2", []))

    def test_erros_juntos(self):
        with self.assertRaises(config.ErroConfig) as ctx:
            config.validar({"versao": 2, "ferramenta": "cursor", "forge": "github", "so": "mac",
                            "modulos": ["vigia"], "modelos": {"maximo": "x"}})
        msg = str(ctx.exception)
        for trecho in ("versao", "ferramenta", "módulo 'vigia'", "modelos"):
            self.assertIn(trecho, msg)
        with self.assertRaisesRegex(config.ErroConfig, "motor"):
            config.validar({"versao": 1, "ferramenta": "kiro", "forge": "github", "so": "wsl", "kiro": {"motor": "v9"}})

    def test_promover_alvos(self):
        base = {"versao": 1, "ferramenta": "kiro", "forge": "github", "so": "wsl"}
        self.assertEqual(config.validar(base)["promover"]["alvos"], ["main"])
        self.assertEqual(config.validar(dict(base, git={"base": "main"}))["git"]["base"], "main")  # desligado: não confere
        ligado = dict(base, modulos=["promover"])
        self.assertEqual(config.validar(dict(ligado, promover={"alvos": ["staging", "main"]}), ("promover",))
                         ["promover"]["alvos"], ["staging", "main"])
        for alvos in ([], ["main", "main"], ["develop", "main"], "main", [""], ["main "], ["ma in"], ["main;id"], 5):
            with self.subTest(alvos=alvos), self.assertRaisesRegex(config.ErroConfig, "promover.alvos"):
                config.validar(dict(ligado, promover={"alvos": alvos}), ("promover",))
        for alvos in (5, ["ma in"]):  # o formato vale mesmo com o módulo desligado (o install usa o texto)
            with self.subTest(desligado=alvos), self.assertRaisesRegex(config.ErroConfig, "promover.alvos"):
                config.validar(dict(base, promover={"alvos": alvos}))

    def test_detectar_so(self):
        self.assertEqual(config.detectar_so("Darwin"), "mac")
        self.assertEqual(config.detectar_so("Linux", "Linux version 5.15.0-microsoft-standard-WSL2"), "wsl")
        self.assertEqual(config.detectar_so("Linux", "Linux version 6.8.0-generic"), "linux")

    def test_caminhos_com_home(self):
        cam = config.Caminhos(home="/tmp/casa", env={"XDG_CONFIG_HOME": "/outro"})
        casa = Path("/tmp/casa").resolve()  # no Mac, /tmp é /private/tmp
        self.assertEqual(cam.config, casa / ".config" / "fluxo" / "config.json")  # o --home ignora o XDG
        self.assertEqual(cam.exibir(cam.dados), "~/.local/share/fluxo")
        self.assertEqual(cam.exibir("/var/x"), "/var/x")


if __name__ == "__main__":
    unittest.main()
