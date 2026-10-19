"""O uso do Copilot no painel (lib/fluxo/uso.py): a statusline, o modelo e o effort do events.jsonl, a cota do cache e
o settings.json. As fixtures são reduzidas dos formatos reais do Copilot CLI 1.0.98, com id e login falsos."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from base import RAIZ, escrever_config
from fluxo import cli, config, montar, panel, uso, vistas

T0 = 1_791_000_000
# o statusObject do cli-main.js, reduzido
STATUS = {"cwd": "/repo/api", "session_id": "cop-1", "session_name": None, "transcript_path": None,
          "model": {"id": "claude-sonnet-4.5", "display_name": "Claude Sonnet 4.5", "auto_tier": None},
          "cost": {"total_api_duration_ms": 4200, "total_lines_added": 3, "total_lines_removed": 1,
                   "total_duration_ms": 9000, "total_premium_requests": 2},
          "context_window": {"total_input_tokens": 1_240_000, "total_output_tokens": 20_300,
                             "total_cache_read_tokens": 980_000, "total_cache_write_tokens": 0, "total_tokens": 1_260_300,
                             "context_window_size": 200_000, "used_percentage": 70, "current_context_used_percentage": 63,
                             "displayed_context_limit": 128_000},
          "ai_used": {"total_nano_aiu": 12_340_000_000, "formatted": "12.34 AIC"}, "allow_all_enabled": False}
# o events.jsonl da sessão (session-events.schema.json), reduzido
EVENTOS = [
    {"type": "session.start", "data": {"sessionId": "cop-1", "selectedModel": "gpt-5.1", "reasoningEffort": "medium",
                                       "context": {"cwd": "/repo/api"}}, "id": "e1", "timestamp": "2026-10-17T22:49:18.493Z"},
    {"type": "user.message", "data": {"content": 'escreva "session.model_change" num arquivo'}, "id": "e2"},
    {"type": "session.model_change", "data": {"cause": "user", "newModel": "claude-sonnet-4.5", "reasoningEffort": "high"},
     "id": "e3"},
]
COTA = {"business": {"login": "usuario-teste", "copilot_plan": "business", "token_based_billing": True,
                     "quota_reset_date_utc": "2026-11-01T00:00:00.000Z",
                     "quota_snapshots": {"premium_interactions": {"entitlement": 1900, "remaining": 105, "percent_remaining": 5.5,
                                                                  "unlimited": False, "has_quota": True, "overage_count": 0,
                                                                  "quota_reset_at": 0, "token_based_billing": True}}},
        "outra": {"login": "outra-conta", "copilot_plan": "individual",
                  "quota_snapshots": {"premium_interactions": {"entitlement": 0, "remaining": 0, "percent_remaining": 0,
                                                               "unlimited": False, "has_quota": False}}}}


@contextlib.contextmanager
def fuso(nome):
    """O TZ do processo durante o bloco: o teste da data em UTC não pode depender do fuso da máquina."""
    antes = os.environ.get("TZ")
    os.environ["TZ"] = nome
    time.tzset()
    try:
        yield
    finally:
        if antes is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = antes
        time.tzset()


def jsonl(eventos):
    return "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in eventos)


def cache(*entradas):
    """O copilot-user-cache.json: o comentário // e uma entrada por token."""
    corpo = {"copilotUserCache": {f"v1:{i}": {"retrievedAt": quando, "response": r} for i, (quando, r) in enumerate(entradas)}}
    return "// Disposable cache for Copilot user responses, safe to delete. Managed automatically.\n" + json.dumps(corpo)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cam = escrever_config(self.home, "copilot-tudo")
        self.pastas = uso.pastas_copilot(self.cam)

    def tearDown(self):
        self.tmp.cleanup()


class Statusline(Base):
    def rodar(self, payload, *argv):
        stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = uso.statusline(["--home", str(self.home), *argv], stdin=stdin, agora=T0)
        self.assertEqual((code, out.getvalue()), (0, ""))  # nunca imprime e sempre sai com 0

    def test_grava_o_que_o_painel_mostra(self):
        self.rodar(STATUS)
        arquivo = self.cam.estado / "uso" / "cop-1.json"
        r = json.loads(arquivo.read_text())
        self.assertEqual((r["modelo"], r["ctx_pct"], r["ctx_max"], r["entrada"], r["nano_aiu"], r["premium"]),
                         ("claude-sonnet-4.5", 63, 128_000, 1_240_000, 12_340_000_000, 2))  # o % e o limite do /context
        self.assertEqual(arquivo.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in arquivo.parent.iterdir()], ["cop-1.json"])  # sem .tmp sobrando
        u = uso.ler_uso(r, {"effort": "high"})
        self.assertAlmostEqual(u["creditos"], 12.34)
        self.assertEqual((u["effort"], u["fonte"], u["ts"]), ("high", "statusline", T0))

    def test_entrada_ruim_nao_grava_nem_falha(self):
        for payload in ("", "lixo", "[1]", dict(STATUS, session_id=None), dict(STATUS, session_id="../fora"),
                        dict(STATUS, session_id="sessão-ç"), dict(STATUS, session_id="x" * 121)):
            with self.subTest(payload=str(payload)[:40]):
                self.rodar(payload)
        self.assertFalse((self.cam.estado / "uso").exists())
        self.assertNotIn("Traceback", (self.cam.estado / "hook-erros.log").read_text())
        self.assertIn("copilot statusline", (self.cam.estado / "hook-erros.log").read_text())  # o lixo vira erro de hook

    def test_campos_que_nao_sao_numero_ficam_de_fora(self):
        torto = dict(STATUS, model="x", context_window={"used_percentage": "63", "total_input_tokens": True},
                     ai_used=None, cost=[1])
        r = uso.normalizar(torto, T0)
        self.assertEqual((r["modelo"], r["ctx_pct"], r["entrada"], r["nano_aiu"], r["premium"]), (None,) * 5)
        auto = uso.normalizar(dict(STATUS, model={"id": "auto", "auto_tier": "premium"}), T0)
        self.assertEqual(auto["modelo"], "auto (premium)")
        antigo = uso.normalizar(dict(STATUS, context_window={"used_percentage": 70, "context_window_size": 200_000}), T0)
        self.assertEqual((antigo["ctx_pct"], antigo["ctx_max"]), (70, 200_000))  # sem os campos do /context

    def test_pelo_script_sem_o_cli(self):
        """O atalho do script fluxo: o Copilot roda isto a cada evento, e o cli importa 20 módulos."""
        r = subprocess.run([sys.executable, str(RAIZ / "fluxo"), "statusline", "--home", str(self.home)],
                           input=json.dumps(STATUS), capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertTrue((self.cam.estado / "uso" / "cop-1.json").exists())
        r = subprocess.run([sys.executable, "-X", "importtime", str(RAIZ / "fluxo"), "statusline", "--home", str(self.home)],
                           input="{}", capture_output=True, text=True, timeout=30)
        self.assertNotIn("fluxo.cli", r.stderr)

    def test_pelo_cli(self):
        with mock.patch("sys.stdin", io.StringIO(json.dumps(STATUS))), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["statusline", "--home", str(self.home)], RAIZ), 0)
        self.assertEqual(out.getvalue(), "")
        self.assertTrue((self.cam.estado / "uso" / "cop-1.json").exists())


class Sessao(Base):
    def test_modelo_e_effort_aos_pedacos(self):
        arquivo = self.pastas["home"] / "session-state" / "cop-1" / "events.jsonl"
        arquivo.parent.mkdir(parents=True)
        arquivo.write_text(jsonl(EVENTOS[:2]))
        leitor = uso.LeitorSessao()
        self.assertEqual(leitor.ler(uso.eventos_da_sessao(self.pastas, "cop-1")), {"modelo": "gpt-5.1", "effort": "medium"})
        linha = json.dumps(EVENTOS[2], separators=(",", ":"))
        with open(arquivo, "a") as fh:
            fh.write(linha[:20])  # o Copilot no meio da linha
        self.assertEqual(leitor.ler(arquivo)["effort"], "medium")
        with open(arquivo, "a") as fh:
            fh.write(linha[20:] + "\n")
        self.assertEqual(leitor.ler(arquivo), {"modelo": "claude-sonnet-4.5", "effort": "high"})
        with mock.patch("fluxo.uso.json.loads", wraps=json.loads) as loads:
            with open(arquivo, "a") as fh:
                fh.write(jsonl([{"type": "assistant.message", "data": {"content": "x" * 1000}}] * 50))
            leitor.ler(arquivo)
        self.assertEqual(loads.call_count, 0)  # só os 3 tipos passam pelo json.loads
        with open(arquivo, "a") as fh:
            fh.write(jsonl([{"type": "session.model_change", "data": {"newModel": "auto", "reasoningEffort": None}}]))
        self.assertEqual(leitor.ler(arquivo), {"modelo": "auto", "effort": None})  # null é o padrão do modelo
        arquivo.unlink()
        arquivo.write_text(jsonl([{"type": "session.resume", "data": {"selectedModel": "gpt-5.1"}}]))  # outro inode
        self.assertEqual(leitor.ler(arquivo), {"modelo": "gpt-5.1", "effort": None})
        self.assertIsNone(leitor.ler(arquivo.parent / "nada.jsonl"))
        self.assertIsNone(leitor.ler(arquivo.parent))  # o transcript_path é de terceiro: uma pasta não derruba
        os.chmod(arquivo, 0)
        try:
            if not os.access(arquivo, os.R_OK):  # como root, o chmod não tira a leitura
                self.assertIsNone(uso.LeitorSessao().ler(arquivo))
        finally:
            os.chmod(arquivo, 0o600)

    def test_caminho_da_statusline_vale_mais(self):
        self.assertEqual(uso.eventos_da_sessao(self.pastas, "cop-1", {"transcript": "/outro/events.jsonl"}),
                         Path("/outro/events.jsonl"))
        self.assertEqual(uso.eventos_da_sessao(self.pastas, "cop-1", {"transcript": "relativo"}),
                         self.pastas["home"] / "session-state" / "cop-1" / "events.jsonl")


class Pastas(unittest.TestCase):
    def test_com_home_nada_de_fora(self):
        with tempfile.TemporaryDirectory() as d:
            cam = config.Caminhos(home=d, env={"COPILOT_HOME": "/x", "XDG_CACHE_HOME": "/y"})
            p = uso.pastas_copilot(cam, env={"COPILOT_HOME": "/x", "XDG_CACHE_HOME": "/y"})
        self.assertEqual(p["home"], Path(d).resolve() / ".copilot")
        self.assertEqual(p["cache"], [Path(d).resolve() / ".cache" / "copilot"])

    def test_sem_home_o_ambiente(self):
        cam = config.Caminhos(env={})
        p = uso.pastas_copilot(cam, env={"COPILOT_HOME": "/x", "COPILOT_CACHE_HOME": "/c", "XDG_CACHE_HOME": "/y"})
        self.assertEqual(p["home"], Path("/x"))
        self.assertEqual(p["cache"][:2], [Path("/c"), Path("/y/copilot")])
        self.assertIn(Path.home() / "Library" / "Caches" / "copilot", p["cache"])  # o do Mac, a confirmar


class Cota(Base):
    def escrever(self, entradas, login="usuario-teste"):
        (self.home / ".cache" / "copilot").mkdir(parents=True, exist_ok=True)
        (self.home / ".cache" / "copilot" / "copilot-user-cache.json").write_text(cache(*entradas))
        if login:
            self.pastas["home"].mkdir(parents=True, exist_ok=True)
            (self.pastas["home"] / "config.json").write_text(
                "// User settings belong in settings.json.\n" + json.dumps({"lastLoggedInUser": {"host": "x", "login": login}}))

    def test_a_entrada_do_login(self):
        # a da outra conta é a mais recente, e não tem cota: pegar a mais recente mostraria 0 de 0
        self.escrever([("2026-10-19T19:55:29.865Z", COTA["business"]), ("2026-10-19T19:55:48.852Z", COTA["outra"])])
        c = uso.ler_cota(self.pastas)
        self.assertEqual((c["usado"], c["total"], c["unidade"], c["plano"]), (1795.0, 1900.0, "créditos", "business"))
        self.assertAlmostEqual(c["pct"], 94.5)
        self.assertEqual(c["renova"], 1793491200.0)  # o quota_reset_at 0 cai no quota_reset_date_utc, com Z
        self.assertAlmostEqual(c["ts"], 1792439729.865)

    def test_a_mais_recente_entre_as_pastas(self):
        self.escrever([("2026-10-01T00:00:00Z", COTA["business"])])
        velha = json.loads(json.dumps(COTA["business"]))
        velha["quota_snapshots"]["premium_interactions"]["remaining"] = 1000
        segunda = self.home / "outra-cache"
        segunda.mkdir()
        (segunda / "copilot-user-cache.json").write_text(cache(("2026-10-19T00:00:00Z", velha)))
        pastas = dict(self.pastas, cache=self.pastas["cache"] + [segunda])
        self.assertEqual(uso.ler_cota(pastas)["usado"], 900.0)  # a da segunda pasta é a mais nova

    def test_sem_login_a_mais_recente(self):
        self.escrever([("2026-10-18T00:00:00Z", COTA["outra"]), ("2026-10-19T00:00:00Z", COTA["business"])], login=None)
        self.assertEqual(uso.ler_cota(self.pastas)["total"], 1900.0)

    def test_login_sem_entrada_e_sem_cota(self):
        self.escrever([("2026-10-19T00:00:00Z", COTA["business"])], login="ninguem")
        self.assertIsNone(uso.ler_cota(self.pastas))  # a cota de outra conta não serve
        self.escrever([("2026-10-19T00:00:00Z", COTA["outra"])], login="outra-conta")
        self.assertIsNone(uso.ler_cota(self.pastas))  # has_quota false

    def test_ilimitada_e_billing_antigo(self):
        ilimitada = dict(COTA["business"], quota_snapshots={"premium_interactions": {"unlimited": True}})
        self.escrever([("2026-10-19T00:00:00Z", ilimitada)])
        self.assertEqual(uso.ler_cota(self.pastas)["ilimitado"], True)
        antiga = json.loads(json.dumps(COTA["business"]))
        antiga["token_based_billing"] = False
        antiga["quota_snapshots"]["premium_interactions"].update(token_based_billing=False, quota_reset_at=1793491200000,
                                                                  overage_count=7, percent_remaining=None)
        self.escrever([("2026-10-19T00:00:00Z", antiga)])
        c = uso.ler_cota(self.pastas)
        self.assertEqual((c["unidade"], c["renova"], c["excedente"]), ("premium requests", 1793491200.0, 7))
        self.assertAlmostEqual(c["pct"], 100 * 1795 / 1900)

    def test_arquivo_faltando_ou_quebrado(self):
        self.assertIsNone(uso.ler_cota(self.pastas))
        (self.home / ".cache" / "copilot").mkdir(parents=True)
        (self.home / ".cache" / "copilot" / "copilot-user-cache.json").write_text("{quebrado")
        self.assertIsNone(uso.ler_cota(self.pastas))


class Settings(Base):
    def perguntas(self, *respostas):
        fila = list(respostas)
        return mock.Mock(escrever=mock.Mock(), sim=mock.Mock(side_effect=lambda *a: fila.pop(0)))

    @property
    def settings(self):
        return self.pastas["home"] / "settings.json"

    def test_liga_com_backup_e_preserva_o_resto(self):
        self.settings.parent.mkdir(parents=True)
        original = '// User settings.\n{\n  "effortLevel": "high",\n  "statusLine": {"padding": 2}\n}\n'
        self.settings.write_text(original)
        code, msg = uso.ligar_statusline(self.pastas, self.perguntas(True))
        self.assertEqual(code, 0, msg)
        texto = self.settings.read_text()
        self.assertTrue(texto.startswith("// User settings.\n{"))
        dados = json.loads(texto.split("\n", 1)[1])
        self.assertEqual(dados, {"effortLevel": "high", "statusLine": dict(uso.STATUSLINE, padding=2)})
        self.assertEqual((self.settings.parent / "settings.json.fluxo-bak").read_text(), original)
        self.assertEqual(uso.estado_da_statusline(self.pastas), "fluxo")
        self.assertEqual(uso.ligar_statusline(self.pastas, None), (0, "a statusline do fluxo já está ligada no Copilot"))
        code, _ = uso.desligar_statusline(self.pastas)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.settings.read_text().split("\n", 1)[1]), {"effortLevel": "high", "statusLine": {"padding": 2}})

    def test_cria_o_arquivo(self):
        self.assertEqual(uso.ligar_statusline(self.pastas, self.perguntas(True))[0], 0)
        self.assertEqual(self.settings.parent.stat().st_mode & 0o777, 0o700)  # o ~/.copilot do Copilot é 700
        self.assertEqual(json.loads(self.settings.read_text()), {"statusLine": uso.STATUSLINE})
        self.assertFalse((self.settings.parent / "settings.json.fluxo-bak").exists())
        self.assertEqual(uso.desligar_statusline(self.pastas)[0], 0)
        self.assertEqual(json.loads(self.settings.read_text()), {})

    def test_arquivo_que_aparece_durante_a_pergunta(self):
        """A pergunta espera o humano: um Copilot aberto pode criar o settings.json nesse meio tempo."""
        dele = json.dumps({"model": "gpt-5.1", "trustedFolders": ["/repo"]})

        def sim(*_):
            self.settings.parent.mkdir(parents=True, exist_ok=True)
            self.settings.write_text(dele)
            return True

        code, msg = uso.ligar_statusline(self.pastas, mock.Mock(escrever=mock.Mock(), sim=sim))
        self.assertEqual(code, 1)
        self.assertIn("mudou enquanto era editado", msg)
        self.assertEqual(self.settings.read_text(), dele)

    def test_ctrl_c_na_pergunta(self):
        from fluxo import assistente
        for erro in (assistente.Cancelado, KeyboardInterrupt):
            with self.subTest(erro=erro.__name__):
                p = mock.Mock(escrever=mock.Mock(), sim=mock.Mock(side_effect=erro))
                self.assertEqual(uso.ligar_statusline(self.pastas, p), (1, "nada foi gravado"))
        self.assertFalse(self.settings.exists())

    def test_sem_terminal_ou_com_nao_nada_muda(self):
        code, msg = uso.ligar_statusline(self.pastas, None)
        self.assertEqual(code, 1)
        self.assertIn('"statusLine": {"type": "command"', msg)  # o trecho, para colar à mão
        self.assertEqual(uso.ligar_statusline(self.pastas, self.perguntas(False)), (1, "nada foi gravado"))
        self.assertFalse(self.settings.exists())

    def test_a_de_outro_avisa_e_para(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "~/minha-linha.sh"}}))
        antes = self.settings.read_bytes()
        p = self.perguntas()
        code, msg = uso.ligar_statusline(self.pastas, p)
        self.assertEqual(code, 1)
        self.assertIn("~/minha-linha.sh", msg)
        p.sim.assert_not_called()
        self.assertEqual(uso.estado_da_statusline(self.pastas), "outra")
        self.assertEqual(uso.desligar_statusline(self.pastas)[0], 0)  # a de outro fica
        self.assertEqual(self.settings.read_bytes(), antes)

    def test_json_torto_recusa(self):
        self.settings.parent.mkdir(parents=True)
        for torto in ("{quebrado", "[1]", '{"a": 1}\n// comentário no meio\n'):
            with self.subTest(torto=torto):
                self.settings.write_text(torto)
                code, msg = uso.ligar_statusline(self.pastas, self.perguntas(True))
                self.assertEqual(code, 1)
                self.assertIn("nada foi gravado", msg)
                self.assertEqual(self.settings.read_text(), torto)
                self.assertEqual(uso.estado_da_statusline(self.pastas), "ilegível")

    def test_reconhece_o_comando_do_fluxo(self):
        for comando in (uso.STATUSLINE["command"], "~/.local/bin/fluxo statusline", "/opt/fluxo/fluxo statusline "):
            self.assertTrue(uso.do_fluxo({"command": comando}), comando)
        for comando in ("fluxo statusline-velha", "minha-statusline", "minha-linha; fluxo statusline",
                        "echo x && ~/.local/bin/fluxo statusline", None):
            self.assertFalse(uso.do_fluxo({"command": comando}), comando)


class Painel(Base):
    """O Coletor junta o uso às sessões do Copilot, e o panel enable e o install falam da statusline."""

    def test_coletor_junta_uso_modelo_e_cota(self):
        panel.gravar_evento(self.cam.estado, {"ts": T0, "ferramenta": "copilot", "sessao": "cop-1", "evento": "turno",
                                              "tool": "", "alvo": "", "cwd": "/repo/api", "pane": ""})
        panel.gravar_evento(self.cam.estado, {"ts": T0, "ferramenta": "kiro", "sessao": "kiro-1", "evento": "turno",
                                              "tool": "", "alvo": "", "cwd": "/repo/web", "pane": ""})
        uso.gravar(self.cam.estado, uso.normalizar(STATUS, T0 - 5))
        eventos = self.pastas["home"] / "session-state" / "cop-1" / "events.jsonl"
        eventos.parent.mkdir(parents=True)
        eventos.write_text(jsonl(EVENTOS))
        Cota.escrever(self, [("2026-10-19T19:55:29.865Z", COTA["business"])])
        d = panel.Coletor(self.cam, {"panel"}, ferramentas=["copilot"]).coletar(T0, vivos=set())
        x = {s["sessao"]: s for s in d["sessoes"]}
        self.assertEqual((x["cop-1"]["uso"]["modelo"], x["cop-1"]["uso"]["effort"], x["cop-1"]["uso"]["ctx_pct"]),
                         ("claude-sonnet-4.5", "high", 63))
        self.assertIsNone(x["kiro-1"]["uso"])
        self.assertEqual(d["cota"]["total"], 1900.0)
        t = vistas.como_texto(vistas.vista_sessoes(d, {}, 100, 10))
        self.assertIn("12,3 cr", t)
        with fuso("America/Sao_Paulo"):  # 00:00 UTC do dia 1º, que aqui ainda é 31/10
            self.assertIn("renova 01/11", vistas.como_texto(vistas.cabecalho({}, d, 100)))
        sem = panel.Coletor(self.cam, {"panel"}).coletar(T0, vivos=set())  # sem o Copilot em tools
        self.assertNotIn("cota", sem)
        self.assertNotIn("uso", sem["sessoes"][0])

    def test_sem_statusline_o_modelo_do_events(self):
        panel.gravar_evento(self.cam.estado, {"ts": T0, "ferramenta": "copilot", "sessao": "cop-1", "evento": "turno",
                                              "tool": "", "alvo": "", "cwd": "", "pane": ""})
        eventos = self.pastas["home"] / "session-state" / "cop-1" / "events.jsonl"
        eventos.parent.mkdir(parents=True)
        eventos.write_text(jsonl(EVENTOS))
        (x,) = panel.Coletor(self.cam, set(), ferramentas=["copilot"]).coletar(T0, vivos=set())["sessoes"]
        self.assertEqual((x["uso"]["modelo"], x["uso"]["fonte"], x["uso"]["creditos"]), ("claude-sonnet-4.5", "sessão", None))

    def test_poda_do_uso(self):
        uso.gravar(self.cam.estado, uso.normalizar(STATUS, T0))
        pasta = self.cam.estado / "uso"
        velho, tmp, novo = pasta / "velha.json", pasta / ".cop-2.x.tmp", pasta / "cop-1.json"
        for p, idade in ((velho, 8 * 86400), (tmp, 7200)):
            p.write_text("{}")
            os.utime(p, (time.time() - idade, time.time() - idade))
        panel.podar(self.cam.estado)
        self.assertEqual((velho.exists(), tmp.exists(), novo.exists()), (False, False, True))

    def test_enable_e_disable(self):
        cfg = config.carregar(self.cam, montar.modulos_existentes(RAIZ))
        run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
        code, msg = panel.ligar(cfg, self.cam, run, Settings.perguntas(self, True))
        self.assertEqual(code, 0, msg)
        self.assertIn("statusline do fluxo ligada", msg)
        self.assertIn("os hooks já valem", msg)
        self.assertEqual(panel.avisos_do_install(set(), self.cam, (), cfg), [])
        sem_copilot = dict(cfg, tools=["kiro"], kiro={"engine": "v3"})
        self.assertIn("fluxo panel disable", " ".join(panel.avisos_do_install(set(), self.cam, (), sem_copilot)))
        code, msg = panel.desligar(sem_copilot, self.cam, run)  # pelo disco, e não pela config
        self.assertEqual(code, 0)
        self.assertIn("statusline do fluxo desligada", msg)
        self.assertIn("fluxo panel enable", " ".join(panel.avisos_do_install(set(), self.cam, (), cfg)))

    def test_uninstall_tira_a_statusline(self):
        self.settings_do_fluxo()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["uninstall", "--home", str(self.home), "--dry-run"], RAIZ), 0)
        self.assertIn("statusline do fluxo", out.getvalue())
        self.assertEqual(uso.estado_da_statusline(self.pastas), "fluxo")  # o dry-run não mexe
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["uninstall", "--home", str(self.home)], RAIZ), 0)
        self.assertIn("statusline do fluxo desligada", out.getvalue())
        self.assertIsNone(uso.estado_da_statusline(self.pastas))

    def settings_do_fluxo(self):
        settings = self.pastas["home"] / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({"statusLine": uso.STATUSLINE}))

    def test_enable_pelo_cli_sem_terminal(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["panel", "--home", str(self.home), "enable"], RAIZ)
        self.assertEqual(code, 1)
        self.assertIn("num terminal", err.getvalue())
        self.assertFalse((self.pastas["home"] / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
