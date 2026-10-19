"""As vistas do panel (lib/fluxo/vistas.py), o Coletor e o --text: dados montados num --home, sem tmux nem
`ps` de verdade (os `vivos` e o `run` são injetados)."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from base import RAIZ, escrever_config
from fluxo import cli, config, demo, forge, panel, vistas, watch

T0 = 1_791_000_000
PLANO = """---
id: PROJ-12
status: aprovado
repos: [api, web]
base: develop
criado: 2026-10-15
---
# PROJ-12

## Critério de aceite
- [ ] o login funciona

## Checklist do fluxo
- [x] plano aprovado
* [X] branch e worktree
- [ ] implementação com testes

## Resumo
- [x] isto não é do checklist
"""


def sessao(id_, estado="trabalhando", ultimo=T0, **kw):
    return dict({"sessao": id_, "nome": id_, "ferramenta": "copilot", "estado": estado, "ultimo": ultimo, "ha": 0,
                 "tool": "", "alvo": "", "pane": "", "cwd": "", "contagem": {}, "subagentes": {}, "log": [],
                 "ultimo_prompt": "", "tarefa": None}, **kw)


def dados(**kw):
    return dict({"agora": T0, "sessoes": [], "planos": {}, "revisoes": {}, "avisos": [], "vigia": {}, "trava": None,
                 "erros_hook": [], "tarefas": [], "ligados": {"reviewer", "watch", "lock"}}, **kw)


def texto(linhas):
    return vistas.como_texto(linhas)


class Leitura(unittest.TestCase):
    def test_plano(self):
        p = vistas.ler_plano(PLANO)
        self.assertEqual((p["status"], p["repos"], p["base"], p["criado"]), ("aprovado", "api, web", "develop", "2026-10-15"))
        # só a seção do checklist: o critério de aceite e o resumo também têm caixas
        self.assertEqual(p["checklist"], [(True, "plano aprovado"), (True, "branch e worktree"),
                                          (False, "implementação com testes")])
        self.assertEqual(vistas.ler_plano(PLANO.replace("\n", "\r\n"))["checklist"], p["checklist"])  # CRLF
        self.assertIsNone(vistas.ler_plano("---\nstatus: rascunho\n---\nsem seção\n")["checklist"])
        self.assertEqual(vistas.ler_plano("---\nrepos: api\n---\n")["repos"], "api")

    def test_plano_torto_nao_levanta(self):
        for texto_ in ("---\nisto não é frontmatter\n---\n", "---\nsem fechamento\n", "", None, "\udc80"):
            with self.subTest(texto=texto_):
                self.assertEqual(vistas.ler_plano(texto_)["status"], "?")

    def test_revisao(self):
        for corpo in ("# Revisão\nVeredito: APROVÁVEL\n", "# Revisão\n**Veredito:** APROVÁVEL\n"):
            with self.subTest(corpo=corpo):
                r = vistas.ler_revisao("20261015-1010-r2.md", corpo)
                self.assertEqual((r["quando"], r["rodada"], r["veredito"]), ("20261015-1010", 2, "APROVÁVEL"))
        self.assertEqual(vistas.ler_revisao("solto.md", "nada")["veredito"], "?")

    def test_cita(self):
        self.assertTrue(vistas.cita("PROJ-1", "PROJ-1: login"))
        self.assertFalse(vistas.cita("PROJ-1", "PROJ-12: login"))
        self.assertFalse(vistas.cita("PROJ-1", "XPROJ-1"))
        self.assertTrue(vistas.cita("v1.2", "release v1.2"))
        self.assertFalse(vistas.cita("a.b", "aXb"))  # o ponto do id é ponto, não qualquer caractere
        self.assertFalse(vistas.cita("", "qualquer"))


class Largura(unittest.TestCase):
    def test_cjk_conta_duas_colunas(self):
        self.assertEqual(vistas.colunas("ação"), 4)
        self.assertEqual(vistas.colunas("漢字"), 4)
        self.assertEqual(vistas.cortar("漢字x", 3), "漢")
        self.assertEqual(vistas.ajustar([("abc", None), ("漢字", "ok")], 4), [("abc", None)])

    def test_nenhuma_vista_passa_da_largura(self):
        x = sessao("s1", nome="漢字のセッション", estado="pergunta", tool="bash", alvo="漢" * 50, ultimo_prompt="字" * 300,
                   contagem={"bash": 3, "漢字": 1}, subagentes={"a": {"rotulo": "漢" * 30, "inicio": T0, "fim": None, "contagem": {"x": 1}}},
                   log=[{"ts": T0, "evento": "ferramenta", "tool": "漢" * 40, "alvo": "字" * 40}], tarefa={"id": "PROJ-12"})
        d = dados(sessoes=[x], planos={"PROJ-12": dict(vistas.ler_plano(PLANO), mtime=T0)},
                  revisoes={"PROJ-12": [{"quando": "x", "rodada": 1, "veredito": "漢" * 30, "mtime": T0}]},
                  avisos=[{"ts": T0, "tipo": "ci", "ref": "r", "texto": "漢" * 40, "titulo": "PROJ-12", "ci": "FAILURE"}],
                  erros_hook=["漢" * 300])
        tela = {"seguida": "s1", "escolhida": "s1", "aba": "session"}
        for largura in (0, 1, 10, 40, 100):
            for nome, vista in vistas.VISTAS.items():
                with self.subTest(vista=nome, largura=largura):
                    for linha in vista(d, tela, largura, 50):
                        self.assertLessEqual(vistas.colunas("".join(t for t, _ in linha)), largura)


class Vistas(unittest.TestCase):
    def test_sessoes_com_a_escolhida_visivel(self):
        lista = [sessao(f"s{i}") for i in range(30)]
        linhas = vistas.vista_sessoes(dados(sessoes=lista), {"escolhida": "s25"}, 100, 10)
        self.assertEqual(len(linhas), 10)
        self.assertIn("s25", texto(linhas))
        self.assertTrue(any(estilo == "sel" for l in linhas for _, estilo in l))
        self.assertIn("nenhuma sessão", texto(vistas.vista_sessoes(dados(), {}, 100, 10)))

    def test_sessao(self):
        x = sessao("cx", ferramenta="codex", ultimo_prompt="mapeie o checkout", contagem={"Bash": 4, "apply_patch": 1},
                   subagentes={"a1": {"rotulo": "explorer", "inicio": T0, "fim": None, "contagem": {"Bash": 2}}},
                   log=[{"ts": T0, "evento": "ferramenta", "tool": "Bash", "alvo": "rg x", "falhou": True}],
                   tarefa={"id": "PROJ-12"})
        linhas = vistas.vista_sessao(dados(sessoes=[x]), {"seguida": "cx"}, 100, 40)
        t = texto(linhas)
        for trecho in ("mapeie o checkout", "explorer", "rodando", "Bash 2", "PROJ-12", "rg x"):
            self.assertIn(trecho, t)
        self.assertTrue(any(estilo == "erro" for l in linhas for _, estilo in l))  # a ferramenta que falhou

    def test_tarefa(self):
        d = dados(planos={"PROJ-12": dict(vistas.ler_plano(PLANO), mtime=T0)},
                  revisoes={"PROJ-12": [{"quando": "20261015-1010", "rodada": 1, "veredito": "REPROVAR", "mtime": T0}]},
                  avisos=[{"ts": T0, "tipo": "ci", "ref": "org/api#7", "texto": "CI quebrou", "titulo": "PROJ-12: login", "ci": "FAILURE"},
                          {"ts": T0, "tipo": "review", "ref": "org/api#9", "texto": "ana comentou", "titulo": "PROJ-123: outro"}])
        t = texto(vistas.vista_tarefa(d, {}, 100, 60))
        self.assertIn("2/3", t)
        self.assertIn("REPROVAR", t)
        self.assertIn("org/api#7", t)
        self.assertNotIn("org/api#9", t)  # PROJ-123 não é PROJ-12
        self.assertIn("sem checklist", texto(vistas.vista_tarefa(
            dados(planos={"X": dict(vistas.ler_plano("---\nstatus: rascunho\n---\n"), mtime=T0)}), {}, 100, 40)))
        self.assertIn("nenhuma tarefa", texto(vistas.vista_tarefa(dados(), {}, 100, 10)))

    def test_qual_tarefa(self):
        d = dados(planos={"VELHA-1": {"mtime": 1}, "NOVA-2": {"mtime": 2}},
                  sessoes=[sessao("s", tarefa={"id": "VELHA-1"})])
        self.assertEqual(vistas.ordem_das_tarefas(d), ["NOVA-2", "VELHA-1"])
        self.assertEqual(vistas.tarefa_atual(d, {"tarefa": "FIXA-3"}, "s"), "FIXA-3")
        self.assertEqual(vistas.tarefa_atual(d, {}, "s"), "VELHA-1")  # a da sessão seguida
        self.assertEqual(vistas.tarefa_atual(d, {}, None), "NOVA-2")  # senão, o plano mais novo

    def test_modulo_desligado_tira_a_secao(self):
        d = dados(ligados=set(), planos={"PROJ-12": dict(vistas.ler_plano(PLANO), mtime=T0)})
        self.assertNotIn("revisões", texto(vistas.vista_tarefa(d, {}, 100, 60)))
        self.assertNotIn("PRs/MRs", texto(vistas.vista_tarefa(d, {}, 100, 60)))
        g = texto(vistas.vista_global(d, {}, 100, 60))
        for secao in ("trava", "watch", "revisões"):
            self.assertNotIn(secao, g)

    def test_alertas(self):
        d = dados(sessoes=[sessao("s", estado="pergunta")], erros_hook=["2026-10-15 10:00:00 copilot x: erro"],
                  trava={"dono": {"sessao": "x"}, "vivo": False, "texto": "ocupada"},
                  vigia={"erro": "gh saiu com 1"},
                  avisos=[{"ts": T0, "tipo": "ci", "ref": "a#1", "ci": "FAILURE", "titulo": "t"},
                          {"ts": T0 + 1, "tipo": "ci", "ref": "a#1", "ci": "SUCCESS", "titulo": "t"},  # voltou ao verde
                          {"ts": T0, "tipo": "ci", "ref": "b#2", "ci": "FAILURE", "titulo": "t"}])
        t = " | ".join(a for a, _ in vistas.alertas(d))
        for trecho in ("esperando uma resposta", "erro(s) de hook", "dono morto", "CI quebrou em b#2", "o watch falhou"):
            self.assertIn(trecho, t)
        self.assertNotIn("a#1", t)
        self.assertEqual(vistas.alertas(dados()), [])

    def test_seguir_com_histerese(self):
        a, b = sessao("a", "trabalhando", T0), sessao("b", "trabalhando", T0 + 5)
        self.assertEqual(vistas.seguir([a, b], {}, T0 + 6), "b")  # sem nenhuma: a mais recente
        self.assertEqual(vistas.seguir([a, b], {"seguida": "a"}, T0 + 6), "a")  # trabalhando: fica
        a_vez = sessao("a", "sua vez", T0)
        self.assertEqual(vistas.seguir([a_vez, b], {"seguida": "a"}, T0 + 10), "a")
        self.assertEqual(vistas.seguir([a_vez, b], {"seguida": "a"}, T0 + vistas.SEGUE_TROCA + 1), "b")
        self.assertEqual(vistas.seguir([sessao("a", "parada", T0), b], {"seguida": "a", "fixada": True}, T0 + 999), "a")
        self.assertIsNone(vistas.seguir([], {}, T0))

    def test_texto_de_terceiro_sai_limpo(self):
        d = dados(avisos=[{"ts": T0, "tipo": "review", "ref": "r\x1b[31m#1", "texto": "x\x1b[2Jy", "titulo": "PROJ-12 \x1b]0;oi\x07"}],
                  planos={"PROJ-12": {"mtime": T0, "status": "aprovado", "repos": "", "base": "", "criado": "", "checklist": []}})
        for vista in vistas.VISTAS.values():
            self.assertNotIn("\x1b", texto(vista(d, {}, 200, 60)))


class Teclas(unittest.TestCase):
    CURSES = type("CursesFalso", (), {"KEY_UP": 259, "KEY_DOWN": 258, "KEY_LEFT": 260, "KEY_RIGHT": 261,
                                      "KEY_PPAGE": 339, "KEY_NPAGE": 338, "KEY_ENTER": 343, "KEY_RESIZE": 410})

    def test_tabela(self):
        for tecla, acao in ((ord("1"), "aba:session"), (ord("4"), "aba:list"), (259, "acima"), (ord("j"), "abaixo"),
                            (261, "tarefa_prox"), (339, "pagina_acima"), (10, "ir"), (343, "ir"), (9, "proxima"),
                            (ord("f"), "seguir"), (ord("h"), "esconder"), (ord("q"), "sair"), (27, "sair"),
                            (410, None), (-1, None), (ord("z"), None), (99999, None)):
            with self.subTest(tecla=tecla):
                self.assertEqual(vistas.acao_da_tecla(tecla, self.CURSES), acao)

    def test_aplicar(self):
        d = dados(sessoes=[sessao("a"), sessao("b"), sessao("c")],
                  planos={"VELHA-1": {"mtime": 1}, "NOVA-2": {"mtime": 2}})
        t = {"aba": "list", "escolhida": "a", "escondidas": set()}
        vistas.aplicar("abaixo", t, d)
        vistas.aplicar("abaixo", t, d)
        vistas.aplicar("abaixo", t, d)  # no fim, fica
        self.assertEqual(t["escolhida"], "c")
        vistas.aplicar("seguir", t, d)
        self.assertEqual((t["seguida"], t["fixada"]), ("c", True))
        vistas.aplicar("proxima", t, d)
        self.assertEqual(t["seguida"], "a")  # dá a volta
        vistas.aplicar("esconder", t, d)
        self.assertEqual(t["escondidas"], {"c"})
        vistas.aplicar("aba:session", t, d)
        vistas.aplicar("abaixo", t, d)  # na aba 1, ↑↓ trocam a seguida
        self.assertEqual((t["aba"], t["seguida"]), ("session", "b"))
        vistas.aplicar("tarefa_prox", t, d)
        self.assertEqual(t["tarefa"], "VELHA-1")  # a atual era a mais nova
        vistas.aplicar("tarefa_prox", t, d)
        self.assertEqual(t["tarefa"], "NOVA-2")
        vistas.aplicar("tarefa_ant", t, d)
        self.assertEqual(t["tarefa"], "VELHA-1")
        vistas.aplicar("pagina_abaixo", t, d)
        vistas.aplicar("pagina_abaixo", t, d)
        vistas.aplicar("pagina_acima", t, d)
        self.assertEqual(t["rolagem"], vistas.PAGINA)
        vistas.aplicar("aba:task", t, d)
        self.assertEqual(t["rolagem"], 0)
        self.assertIs(vistas.aplicar(None, t, d), t)
        vazio = {"aba": "list", "escolhida": None}
        for acao in ("acima", "abaixo", "seguir", "proxima", "esconder", "tarefa_prox"):
            vistas.aplicar(acao, vazio, dados())  # sem sessões nem planos: nada quebra

    def test_glifos_e_rodape_pelo_locale(self):
        self.assertIs(vistas.glifos_para("UTF-8"), vistas.GLIFOS_UTF8)
        self.assertIs(vistas.glifos_para("utf8"), vistas.GLIFOS_UTF8)
        for cod in ("US-ASCII", "ANSI_X3.4-1968", "", None):
            with self.subTest(cod=cod):
                self.assertIs(vistas.glifos_para(cod), vistas.GLIFOS_ASCII)
        ascii_ = vistas.rodape({"aba": "session"}, vistas.GLIFOS_ASCII)[0][0]
        ascii_.encode("ascii")  # nada fora do ASCII
        self.assertIn("j/k", ascii_)
        self.assertIn("PgUp", vistas.rodape({"aba": "session"})[0][0])
        for aba, _ in vistas.ABAS:
            self.assertLessEqual(vistas.colunas(vistas.rodape({"aba": aba})[0][0]), 79)  # o 80x24 do Terminal.app


class Revisao07(unittest.TestCase):
    """As mutações que a revisão da 0.7.0 viu sobreviverem."""

    def test_rolar_alem_do_fim_ainda_mostra_linhas(self):
        x = sessao("s", log=[{"ts": T0 + i, "evento": "ferramenta", "tool": f"t{i}"} for i in range(30)])
        linhas = vistas.vista_sessao(dados(sessoes=[x]), {"seguida": "s", "rolagem": 999}, 100, 20)
        self.assertIn("t0", texto(linhas))  # o mais velho, no fim do log
        recentes = texto(vistas.vista_sessao(dados(sessoes=[x]), {"seguida": "s"}, 100, 40))
        self.assertLess(recentes.index("t29"), recentes.index("t28"))  # o mais novo em cima

    def test_ultimo_ci_de_cada_pr_na_tarefa(self):
        d = dados(planos={"PROJ-12": dict(vistas.ler_plano(PLANO), mtime=T0)},
                  avisos=[{"ts": T0, "tipo": "ci", "ref": "o/r#1", "texto": "CI quebrou", "titulo": "PROJ-12", "ci": "FAILURE"},
                          {"ts": T0 + 1, "tipo": "ci", "ref": "o/r#1", "texto": "CI verde", "titulo": "PROJ-12", "ci": "SUCCESS"}])
        t = texto(vistas.vista_tarefa(d, {}, 100, 60))
        self.assertIn("CI verde", t)
        self.assertNotIn("CI quebrou", t)

    def test_revisoes_da_global_so_das_24h(self):
        d = dados(revisoes={"VELHA-1": [{"quando": "x", "rodada": 1, "veredito": "REPROVAR", "mtime": T0 - 2 * 86400}],
                            "NOVA-2": [{"quando": "y", "rodada": 1, "veredito": "APROVÁVEL", "mtime": T0 - 60}]})
        t = texto(vistas.vista_global(d, {}, 100, 60))
        self.assertIn("NOVA-2", t)
        self.assertNotIn("VELHA-1", t)

    def test_fixada_nao_pula_para_outra(self):
        a, b = sessao("aaa", ultimo=T0), sessao("bbb", ultimo=T0 + 9)
        self.assertEqual(vistas.seguir([a, b], {"seguida": "zzz", "fixada": True}, T0 + 10), "zzz")
        t = texto(vistas.vista_sessao(dados(sessoes=[a, b]), {"seguida": "zzz", "fixada": True}, 100, 10))
        self.assertIn("a sessão fixada (zzz) não está", t)
        tela = {"aba": "session", "seguida": "bbb", "escolhida": "aaa"}
        vistas.aplicar("seguir", tela, dados(sessoes=[a, b]))  # na aba 1, f fixa a que está na tela
        self.assertEqual((tela["seguida"], tela["fixada"]), ("bbb", True))

    def test_colunas_alinhadas_com_cjk(self):
        lista = [sessao("s1", nome="漢字"), sessao("s2", nome="ascii")]
        linhas = [l for l in texto(vistas.vista_sessoes(dados(sessoes=lista), {}, 100, 10)).splitlines() if "漢字" in l or "ascii" in l]
        self.assertEqual(len(linhas), 2)
        self.assertEqual(len({vistas.colunas(l.split("—")[0]) for l in linhas}), 1)  # o modelo começa na mesma coluna
        self.assertEqual(vistas.preencher("漢字x", 4), "漢字")
        self.assertEqual(vistas.preencher("ab", 4), "ab  ")

    def test_texto_de_terceiro_pelo_cli(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            cam = escrever_config(home, "copilot-codex")
            payload = {"sessionId": "s\x1b[2J", "cwd": "/r/\x1b]0;x\x07", "prompt": "oi \x1b[31mvermelho",
                       "toolName": "bash\x1b[0m"}
            panel.gravar_evento(cam.estado, panel.normalizar("copilot", "userPromptSubmitted", payload, env={"TMUX_PANE": "%1"}))
            for vista in ("list", "session", "global", "task"):
                with self.subTest(vista=vista):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out), mock.patch("fluxo.tmux.panes_vivos", return_value=set()):
                        cli.main(["panel", "--home", str(home), "--text", "--view", vista], RAIZ)
                    self.assertNotIn("\x1b", out.getvalue())
                    self.assertNotIn("\x07", out.getvalue())


class LeitorEventos(unittest.TestCase):
    def test_le_so_o_novo_e_guarda_a_linha_inacabada(self):
        with tempfile.TemporaryDirectory() as d:
            estado = Path(d)
            arquivo = estado / time.strftime("eventos-%Y%m%d.jsonl", time.localtime(T0))
            leitor = panel.LeitorEventos(estado)
            linha = lambda s: json.dumps({"sessao": s, "ts": T0, "evento": "inicio"})
            arquivo.write_text(linha("a") + "\n" + linha("b")[:10])  # o hook está no meio da linha de b
            self.assertEqual([e["sessao"] for e in leitor.ler(T0)], ["a"])
            with open(arquivo, "a") as fh:
                fh.write(linha("b")[10:] + "\n")
            self.assertEqual([e["sessao"] for e in leitor.ler(T0)], ["a", "b"])
            arquivo.unlink()
            arquivo.write_text(linha("c") + "\n")  # outro arquivo (outro inode): recomeça
            self.assertEqual([e["sessao"] for e in leitor.ler(T0)], ["c"])
            with open(arquivo, "r+") as fh:
                fh.truncate(0)
            self.assertEqual(leitor.ler(T0), [])


class Coletor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cam = escrever_config(self.home, "copilot-codex")
        (self.cam.dados / "planos").mkdir(parents=True)
        (self.cam.dados / "planos" / "PROJ-12.md").write_text(PLANO)

    def tearDown(self):
        self.tmp.cleanup()

    def test_planos_relidos_so_quando_mudam(self):
        c = panel.Coletor(self.cam, {"task"})
        with mock.patch("fluxo.vistas.ler_plano", wraps=vistas.ler_plano) as ler:
            c.coletar(T0, vivos=set())
            c.coletar(T0 + c.LENTO, vivos=set())  # o mtime não mudou
            self.assertEqual(ler.call_count, 1)
            p = self.cam.dados / "planos" / "PROJ-12.md"
            p.write_text(PLANO.replace("aprovado", "concluido"))
            os.utime(p, (T0 + 100, T0 + 100))
            d = c.coletar(T0 + 2 * c.LENTO, vivos=set())
        self.assertEqual(ler.call_count, 2)
        self.assertEqual(d["planos"]["PROJ-12"]["status"], "concluido")

    def test_lentos_so_a_cada_tanto_e_secoes_por_modulo(self):
        c = panel.Coletor(self.cam, {"reviewer"})
        rev = self.cam.dados / "revisoes" / "feat" / "PROJ-12-login"  # o id pode ser o nome da branch, com /
        rev.mkdir(parents=True)
        (rev / "20261015-1010-r1.md").write_text("Veredito: APROVÁVEL\n")
        d = c.coletar(T0, vivos=set())
        self.assertEqual(d["revisoes"]["feat/PROJ-12-login"][0]["veredito"], "APROVÁVEL")
        self.assertEqual((d["avisos"], d["trava"]), ([], None))  # watch e lock desligados
        (self.cam.dados / "planos" / "NOVA-1.md").write_text(PLANO)
        self.assertNotIn("NOVA-1", c.coletar(T0 + 1, vivos=set())["planos"])  # ainda no cache
        self.assertIn("NOVA-1", c.coletar(T0 + c.LENTO, vivos=set())["planos"])

    def test_trava_sem_tmux_nem_ps(self):
        dono = {"sessao": "s", "id": "PROJ-12", "desde": T0, "pid": 4242}
        (self.cam.estado / "trava").mkdir(parents=True)
        (self.cam.estado / "trava" / "dono.json").write_text(json.dumps(dono))
        c = panel.Coletor(self.cam, {"lock"})
        with mock.patch("fluxo.lock.inicio_do_pid", return_value=None) as inicio:
            d = c.coletar(T0, vivos=set())
            c.coletar(T0 + c.LENTO, vivos=set())  # o início do pid fica em cache
        self.assertIs(d["trava"]["vivo"], False)
        self.assertIn("dono morto", d["trava"]["texto"])
        self.assertEqual(inicio.call_count, 1)

    def test_erros_de_hook_das_ultimas_24h(self):
        velho = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 - 2 * 86400))
        novo = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 - 60))
        self.cam.estado.mkdir(parents=True, exist_ok=True)
        (self.cam.estado / "hook-erros.log").write_text(f"{velho} kiro x: velho\n{novo} kiro x: novo\n")
        d = panel.Coletor(self.cam).coletar(T0, vivos=set())
        self.assertEqual([l[-4:] for l in d["erros_hook"]], ["novo"])


class Incremental(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cam = escrever_config(Path(self.tmp.name), "copilot-codex")

    def tearDown(self):
        self.tmp.cleanup()

    def evento(self, sessao_, evento, ts, **kw):
        panel.gravar_evento(self.cam.estado, dict({"sessao": sessao_, "evento": evento, "ts": ts, "ferramenta": "copilot",
                                                   "tool": "", "alvo": "", "cwd": "", "pane": ""}, **kw))

    def test_cada_quadro_so_aplica_os_novos(self):
        c = panel.Coletor(self.cam)
        self.evento("s", "turno", T0)
        c.coletar(T0, vivos=set())
        self.evento("s", "ferramenta", T0 + 1, tool="bash")
        with mock.patch.object(panel.Acumulador, "aplicar", wraps=c.acumulador.aplicar) as aplicar:
            (x,) = c.coletar(T0 + 2, vivos=set())["sessoes"]
        (novos,), _ = aplicar.call_args
        self.assertEqual([e["evento"] for e in novos], ["ferramenta"])
        self.assertEqual((x["estado"], x["contagem"]), ("trabalhando", {"bash": 1}))
        self.assertEqual(x, panel.sessoes(panel.LeitorEventos(self.cam.estado).ler(T0 + 2), agora=T0 + 2)[0])

    def test_virar_o_dia_remonta(self):
        c = panel.Coletor(self.cam)
        self.evento("velha", "turno", T0 - 2 * 86400 + 60)
        self.evento("hoje", "turno", T0)
        antes = {x["sessao"] for x in c.coletar(T0 - 2 * 86400 + 120, vivos=set())["sessoes"]}
        self.assertEqual(antes, {"velha"})
        depois = {x["sessao"] for x in c.coletar(T0, vivos=set())["sessoes"]}  # dois dias depois
        self.assertEqual(depois, {"hoje"})

    def test_uma_fonte_quebrada_nao_apaga_as_outras(self):
        (self.cam.dados / "planos").mkdir(parents=True)
        (self.cam.dados / "planos" / "PROJ-12.md").write_text(PLANO)
        self.cam.estado.mkdir(parents=True, exist_ok=True)
        (self.cam.estado / "vigia.json").write_text("{quebrado")
        c = panel.Coletor(self.cam, {"watch"})
        with mock.patch.object(c, "_lentos", wraps=c._lentos) as lentos:
            d = c.coletar(T0, vivos=set())
            c.coletar(T0 + 1, vivos=set())  # dentro do LENTO: não relê
        self.assertEqual(lentos.call_count, 1)
        self.assertIn("PROJ-12", d["planos"])
        self.assertTrue(any("vigia.json" in a for a, _ in vistas.alertas(d)))
        with mock.patch("fluxo.watch.ler_avisos", side_effect=RuntimeError("bum")):
            d = panel.Coletor(self.cam, {"watch"}).coletar(T0, vivos=set())
        self.assertIn("PROJ-12", d["planos"])
        self.assertTrue(any("watch" in a and "bum" in a for a, _ in vistas.alertas(d)))

    def test_follow_fixa(self):
        self.evento("aaa-1", "turno", T0)
        self.evento("bbb-2", "turno", T0 + 5)  # a mais recente
        t = panel.texto(self.cam, set(), {"aba": "session", "seguida": "aaa", "fixada": True}, agora=T0 + 6)
        self.assertIn("aaa-1", t.splitlines()[0])  # o título da caixa da sessão
        self.assertNotIn("bbb-2", t)
        self.assertIn("não está", panel.texto(self.cam, set(), {"aba": "session", "seguida": "zzz", "fixada": True}, agora=T0 + 6))

    def test_text_nao_cai(self):
        with mock.patch.object(panel.Coletor, "coletar", side_effect=RuntimeError("disco sumiu")):
            self.assertIn("o panel não montou a vista", panel.texto(self.cam, set(), {"aba": "global"}))

    def test_panel_poda_e_estado_700(self):
        velho = self.cam.estado / time.strftime("eventos-%Y%m%d.jsonl", time.localtime(time.time() - 30 * 86400))
        self.cam.estado.mkdir(parents=True, exist_ok=True)
        os.chmod(self.cam.estado, 0o755)  # de uma versão que criava com o umask
        velho.write_text("{}\n")
        with contextlib.redirect_stdout(io.StringIO()), mock.patch("fluxo.tmux.panes_vivos", return_value=set()):
            cli.main(["panel", "--home", str(self.tmp.name), "--text"], RAIZ)
        self.assertFalse(velho.exists())
        config.criar_estado(self.cam.estado)
        self.assertEqual(self.cam.estado.stat().st_mode & 0o777, 0o700)


class Watch(unittest.TestCase):
    def test_ler_avisos(self):
        with tempfile.TemporaryDirectory() as d:
            estado = Path(d)
            (estado / "avisos.jsonl").write_text("\n".join([json.dumps({"ts": T0 - 90000, "texto": "velho"}), "lixo",
                                                            json.dumps({"ts": T0, "texto": "novo"}), json.dumps([1])]))
            self.assertEqual([a["texto"] for a in watch.ler_avisos(estado, T0 - 86400)], ["novo"])
            self.assertEqual(watch.ler_avisos(estado / "nada", 0), [])

    def test_aviso_de_ci_leva_o_estado(self):
        pr = {"ref": "o/r#1", "titulo": "t", "url": "u", "estado": "aberta", "merged_em": None, "sha": "c1",
              "ci": "FAILURE", "aprovacoes": [], "falas": [], "versao_em": T0}
        (ci,) = [e for e in forge.eventos([pr], "eu", T0) if e["tipo"] == "ci"]
        self.assertEqual(ci["ci"], "FAILURE")


class CLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        escrever_config(self.home, "copilot-codex")

    def tearDown(self):
        self.tmp.cleanup()

    def rodar(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("fluxo.tmux.panes_vivos", return_value=set()):
            code = cli.main(["panel", "--home", str(self.home), *argv], RAIZ)
        return code, out.getvalue(), err.getvalue()

    def test_text_de_cada_vista(self):
        for vista, trecho in (("list", "nenhuma sessão"), ("session", "nenhuma sessão para seguir"),
                              ("task", "nenhuma tarefa"), ("global", "alertas")):
            with self.subTest(vista=vista):
                code, out, _ = self.rodar("--text", "--view", vista)
                self.assertEqual(code, 0)
                self.assertIn(trecho, out)

    def test_recusas(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):  # as abas não são do enable
            cli.main(["panel", "enable", "--home", str(self.home), "--view", "task"], RAIZ)
        self.assertEqual(self.rodar("--text", "--task", "../x")[0], 2)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["panel", "--home", str(self.home), "--view", "sessão"], RAIZ)


USO = {"modelo": "claude-sonnet-4.5", "effort": "high", "ctx_pct": 63.0, "ctx_max": 200000, "entrada": 1_240_000,
       "saida": 20_300, "cache": 980_000, "creditos": 12.34, "premium": None, "fonte": "statusline", "ts": T0 - 3}
COTA = {"usado": 1311.0, "total": 1900.0, "pct": 69.0, "unidade": "créditos", "renova": T0 + 86400 * 10, "ts": T0 - 7200,
        "plano": "business", "excedente": 0, "ilimitado": False}
DESENHO = set("█░•→▸╭╮╰╯─│◐◓◑◒●○✓")  # os glifos de desenho do UTF-8: no ASCII, nenhum aparece


class Pecas(unittest.TestCase):
    def test_caixa(self):
        linhas = vistas.caixa("título", [[("a", None)], [], [("漢" * 80, None)]], 30, direita=[("canto", "fraco")])
        self.assertEqual([vistas.colunas("".join(t for t, _ in l)) for l in linhas], [30] * 5)
        topo = "".join(t for t, _ in linhas[0])
        self.assertTrue(topo.startswith("╭─ título ") and topo.endswith(" canto ─╮"))
        self.assertEqual("".join(t for t, _ in linhas[-1]), "╰" + "─" * 28 + "╯")
        estreita = vistas.caixa("t", [[("abc", None)]], 5)  # sem moldura em menos de 8 colunas
        self.assertEqual(texto(estreita), "t\nabc")
        self.assertIn("+- t ", texto(vistas.caixa("t", [], 20, vistas.GLIFOS_ASCII)))

    def test_barra_e_numeros(self):
        self.assertEqual(vistas.barra(0, 10, 4), [("", "ok"), ("░░░░", "fraco")])
        self.assertEqual(vistas.barra(1, 1000, 4)[0][0], "█")  # pouco, mas não zero, pinta uma
        self.assertEqual(vistas.barra(5, 0, 4)[0][0], "")
        for n, esperado in ((None, "—"), (999, "999"), (1234, "1,2k"), (20_300, "20k"), (1_240_000, "1,2M"), (9_990, "10k")):
            self.assertEqual(vistas.fmt_tokens(n), esperado)
        for v, esperado in ((None, "—"), (0.4211, "0,42"), (12.34, "12,3"), (1311, "1311")):
            self.assertEqual(vistas.fmt_numero(v), esperado)
        self.assertEqual(vistas.gasto(None), "—")
        self.assertEqual(vistas.gasto({"creditos": 12.34}), "12,3 cr")
        self.assertEqual(vistas.gasto({"creditos": 0.0, "premium": 3}), "3,00 req")  # a cobrança antiga
        self.assertEqual(vistas.gasto({"creditos": 0.0}), "0,00 cr")

    def test_modelo_e_icone(self):
        self.assertEqual(vistas.estilo_do_modelo("Claude-Opus-4.5"), "m_opus")
        self.assertEqual(vistas.estilo_do_modelo("gpt-5.1-codex"), "m_gpt")
        self.assertIsNone(vistas.estilo_do_modelo(None))
        self.assertEqual({vistas.icone("trabalhando", agora=s) for s in range(4)}, set("◐◓◑◒"))  # gira
        self.assertEqual(vistas.icone("pergunta", vistas.GLIFOS_ASCII), "?")

    def test_em_ascii(self):
        self.assertEqual(vistas.em_ascii("sessões — você"), "sessoes - voce")
        for texto_ in ("…½ pronto", "漢字x", "ﬁm", "a\u0301b"):  # as mesmas colunas: a tela avança o x pelo original
            with self.subTest(texto=texto_):
                saida = vistas.em_ascii(texto_)
                saida.encode("ascii")
                self.assertEqual(vistas.colunas(saida), vistas.colunas(texto_))
        self.assertEqual(vistas.em_ascii("漢"), "? ")
        self.assertEqual(vistas.em_ascii("…x"), ".x")


class Cabecalho(unittest.TestCase):
    def test_faixa_e_abas(self):
        d = dados(sessoes=[sessao("a", "pergunta"), sessao("b")], cota=COTA)
        faixa, abas = vistas.cabecalho({"aba": "global"}, d, 100)
        t = "".join(x for x, _ in faixa)
        self.assertEqual(vistas.colunas(t), 100)
        for trecho in ("2 sessões", "1 esperando você", "69%", "renova"):
            self.assertIn(trecho, t)
        self.assertTrue(all((estilo or "").startswith("cab") for _, estilo in faixa))  # a faixa inteira tem fundo
        self.assertIn((" 3 global ", "aba_ativa"), abas)
        cheia = dict(COTA, pct=91.0)
        self.assertIn("cab_erro", {e for _, e in vistas.cabecalho({}, dados(cota=cheia), 100)[0]})
        sem = "".join(x for x, _ in vistas.cabecalho({}, dados(), 100)[0])
        self.assertIn(time.strftime("%H:%M", time.localtime(T0)), sem)  # sem a cota, o relógio
        self.assertIn("ilimitada", "".join(x for x, _ in vistas.cabecalho({}, dados(cota=dict(COTA, ilimitado=True)), 100)[0]))
        for largura in (0, 10, 40):
            for linha in vistas.cabecalho({}, d, largura):
                self.assertLessEqual(vistas.colunas("".join(x for x, _ in linha)), largura)


class Demo(unittest.TestCase):
    """O --demo é a fixture lotada: toda vista cabe na largura e na altura, nas duas famílias de glifos."""

    def setUp(self):
        self.d = demo.dados(T0)

    def test_cabe_em_qualquer_tela(self):
        for glifos in (vistas.GLIFOS_UTF8, vistas.GLIFOS_ASCII):
            for aba, vista in vistas.VISTAS.items():
                tela = {"aba": aba, "seguida": "demo-1", "escolhida": "demo-2"}
                for largura in (0, 1, 10, 40, 60, 80, 100, 140):
                    for altura in (1, 5, 21, 43):  # a tela tira 3 linhas: 24x80 e 46x100 dão 21 e 43
                        with self.subTest(aba=aba, largura=largura, altura=altura, ascii=glifos is vistas.GLIFOS_ASCII):
                            linhas = vista(self.d, tela, largura, altura, glifos)
                            self.assertLessEqual(len(linhas), altura)
                            for linha in linhas:
                                self.assertLessEqual(vistas.colunas("".join(t for t, _ in linha)), largura)

    def test_ascii_sem_glifo_de_desenho(self):
        tela = {"seguida": "demo-1", "escolhida": "demo-2"}
        partes = [vistas.como_texto(v(self.d, tela, 100, 43, vistas.GLIFOS_ASCII)) for v in vistas.VISTAS.values()]
        partes.append(vistas.como_texto(vistas.cabecalho(tela, self.d, 100, vistas.GLIFOS_ASCII)))
        self.assertEqual(DESENHO & set("".join(partes)), set())

    def test_altura_curta_corta_o_log_primeiro(self):
        ferramentas = lambda t: t.split("ferramentas")[1].split("subagentes")[0]
        t = texto(vistas.vista_sessao(self.d, {"seguida": "demo-1"}, 80, 21))  # o 80x24 do Terminal.app
        self.assertIn("modelo e uso", t)
        self.assertIn("subagentes", t)
        self.assertNotIn("╭─ log", t)
        self.assertIn("grep", ferramentas(t))  # cortar as ferramentas não daria 3 linhas ao log: ficam todas
        self.assertGreaterEqual(len(t.splitlines()), 20)
        t = texto(vistas.vista_sessao(self.d, {"seguida": "demo-1"}, 80, 24))
        self.assertNotIn("grep", ferramentas(t))  # aqui o top 3 abre o log, com 3 linhas
        self.assertEqual(len(t.split("╭─ log")[1].splitlines()), 5)
        self.assertIn("╭─ log", texto(vistas.vista_sessao(self.d, {"seguida": "demo-1"}, 100, 43)))
        apertada = texto(vistas.vista_sessao(self.d, {"seguida": "demo-1"}, 80, 16))  # nem as caixas: até 1 ferramenta
        self.assertIn("bash", ferramentas(apertada))
        self.assertNotIn("edit", ferramentas(apertada))
        self.assertIn("code-reviewer", apertada)


class Uso(unittest.TestCase):
    def test_colunas_da_lista(self):
        d = dados(sessoes=[sessao("a", uso=USO), sessao("b", ferramenta="kiro")])
        linhas = vistas.vista_sessoes(d, {}, 100, 10)
        t = texto(linhas)
        for trecho in ("claude-sonnet-4.5", "63%", "12,3 cr"):
            self.assertIn(trecho, t)
        self.assertIn(("claude-sonnet-4.5".ljust(17) + " ", "m_sonnet"), [s for l in linhas for s in l])
        b = next(l for l in t.splitlines() if " b " in l)
        self.assertEqual(b.count("—"), 3)  # sem uso: modelo, ctx e gasto

    def test_borda_avisa_quem_espera(self):
        linhas = vistas.vista_sessoes(dados(sessoes=[sessao("a", "pergunta")]), {}, 100, 10)
        self.assertEqual(linhas[0][0][1], "atencao")
        self.assertIn("1 esperando você", texto(linhas))

    def test_caixa_de_uso(self):
        t = texto(vistas.vista_sessao(dados(sessoes=[sessao("a", uso=USO)]), {"seguida": "a"}, 100, 43))
        for trecho in ("effort high", "63% de 200k", "entrada 1,2M", "saída 20k", "cache 980k", "12,3 créditos",
                       "statusline • há 3 s"):
            self.assertIn(trecho, t)
        sem = texto(vistas.vista_sessao(dados(sessoes=[sessao("a")]), {"seguida": "a"}, 100, 43))
        self.assertIn("fluxo panel enable", sem)
        kiro = texto(vistas.vista_sessao(dados(sessoes=[sessao("a", ferramenta="kiro")]), {"seguida": "a"}, 100, 43))
        self.assertIn("o kiro não manda o uso", kiro)

    def test_cota_na_global(self):
        d = dados(sessoes=[sessao("a", uso=USO), sessao("b", uso=dict(USO, creditos=1.0, entrada=10, saida=0))], cota=COTA)
        t = texto(vistas.vista_global(d, {}, 100, 60))
        for trecho in ("cota do mês", "69% usado", "1311 de 1900 créditos", "plano business", "dado de 2 h 00 atrás",
                       "13,3 créditos", "(2 sessões)"):
            self.assertIn(trecho, t)
        self.assertIn("excedente 12", texto(vistas.vista_global(dados(cota=dict(COTA, excedente=12)), {}, 100, 60)))
        self.assertIn("não foi achado", texto(vistas.vista_global(dados(cota=None), {}, 100, 60)))
        self.assertNotIn("cota do mês", texto(vistas.vista_global(dados(), {}, 100, 60)))  # sem o Copilot, sem a caixa


class DemoCLI(unittest.TestCase):
    def test_nao_le_nem_grava_nada(self):
        with tempfile.TemporaryDirectory() as d:  # sem config nenhuma
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["panel", "--home", d, "--demo", "--text", "--view", "global"], RAIZ)
            self.assertEqual(code, 0)
            self.assertIn("cota do mês", out.getvalue())
            self.assertEqual(list(Path(d).iterdir()), [])
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(cli.main(["panel", "--home", d, "--demo", "enable"], RAIZ), 2)
            self.assertIn("--demo", err.getvalue())


if __name__ == "__main__":
    unittest.main()
