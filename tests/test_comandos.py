"""Os nomes que o fluxo grava e cita: o comando que outra ferramenta chama depois tem de existir no parser,
nenhuma fonte cita grafia antiga, e cada módulo acha o próprio código."""
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


def chamadas(rel, conteudo):
    """Os argv de `fluxo …` dentro de um arquivo gerado (hooks em JSON, plist, unit do systemd)."""
    if rel.endswith(".json"):
        textos = list(_textos(json.loads(conteudo)))
    elif rel.endswith(".plist"):
        textos = list(_textos(plistlib.loads(conteudo)))
    else:
        textos = conteudo.decode().splitlines()
    return [shlex.split(m.group(1)) for t in textos for m in [CHAMADA.search(t)] if m]


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
