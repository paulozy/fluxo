"""Painel: o hook (que nunca falha nem imprime), a normalização por ferramenta, as sessões e os hooks instalados."""
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from base import RAIZ, escrever_config
from fluxo import config, montar, panel

T0 = 1_791_000_000

# fixtures reduzidas dos payloads das docs (Copilot: hooks-reference; Kiro: hooks/types)
COPILOT_POST = {"sessionId": "cop-1", "timestamp": T0 * 1000, "cwd": "/repo/api", "toolName": "bash",
                "toolArgs": json.dumps({"command": "npm test\nmais"}), "toolResult": {"resultType": "success"}}
KIRO_POST = {"hook_event_name": "PostToolUse", "cwd": "/repo/web", "session_id": "kiro-1", "tool_name": "fs_write",
             "tool_input": {"path": "src/app.ts", "content": "…"}, "tool_response": {"success": True}}


class Normalizar(unittest.TestCase):
    def test_copilot_e_kiro(self):
        e = panel.normalizar("copilot", "postToolUse", COPILOT_POST, env={"TMUX_PANE": "%4"}, agora=T0)
        self.assertEqual((e["sessao"], e["evento"], e["tool"], e["alvo"], e["pane"]),
                         ("cop-1", "ferramenta", "bash", "npm test", "%4"))
        k = panel.normalizar("kiro", None, KIRO_POST, env={}, agora=T0)  # o nome do evento vem no payload
        self.assertEqual((k["sessao"], k["evento"], k["tool"], k["alvo"], k["pane"]),
                         ("kiro-1", "ferramenta", "fs_write", "src/app.ts", ""))
        self.assertEqual(panel.normalizar("kiro", "stop", {"session_id": "x"}, env={})["evento"], "fim_turno")
        self.assertEqual(panel.normalizar("copilot", "permissionRequest", {"sessionId": "x"}, env={})["evento"], "espera")


class Revisao(unittest.TestCase):
    """Os casos da revisão de 09/10 do painel."""

    def test_sem_id_usa_o_pane(self):
        self.assertEqual(panel.normalizar("kiro", "Stop", {}, env={"TMUX_PANE": "%3"})["sessao"], "pane-%3")

    def test_alvo_sem_controle(self):
        e = panel.normalizar("copilot", "postToolUse", {"sessionId": "s", "toolArgs": {"command": "ls \x1b[2J; x"}}, env={})
        self.assertEqual(e["alvo"], "ls [2J; x")

    def test_arquivo_de_eventos_so_do_usuario_e_ascii(self):
        with tempfile.TemporaryDirectory() as d:
            estado = Path(d) / "estado"
            panel.hook(estado, "copilot", "postToolUse",
                        stdin=io.StringIO(json.dumps({"sessionId": "s", "toolArgs": {"command": "eco \udc80"}})), env={})
            (arquivo,) = estado.glob("eventos-*.jsonl")
            self.assertEqual(arquivo.stat().st_mode & 0o777, 0o600)
            self.assertEqual(estado.stat().st_mode & 0o777, 0o700)
            arquivo.read_bytes().decode("ascii")  # o surrogate solto não derruba o evento

    def test_le_so_hoje_e_ontem(self):
        with tempfile.TemporaryDirectory() as d:
            estado = Path(d)
            for dias, sessao in ((0, "hoje"), (1, "ontem"), (8, "velha")):
                nome = time.strftime("eventos-%Y%m%d.jsonl", time.localtime(T0 - dias * 86400))
                (estado / nome).write_text(json.dumps({"sessao": sessao, "ts": T0, "evento": "inicio"}) + "\n")
            self.assertEqual(sorted(e["sessao"] for e in panel.ler_eventos(estado, agora=T0)), ["hoje", "ontem"])

    def test_nova_e_sua_vez_e_pane_morto_acabou(self):
        evs = [{"sessao": "nova", "evento": "inicio", "ts": T0, "pane": "%1"},
               {"sessao": "caiu", "evento": "ferramenta", "ts": T0, "pane": "%2"}]
        estados = {x["sessao"]: x["estado"] for x in panel.sessoes(evs, agora=T0 + 60, vivos={("%1", "10")})}
        self.assertEqual(estados, {"nova": "sua vez", "caiu": "acabou"})


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.estado = Path(self.tmp.name) / "estado"

    def tearDown(self):
        self.tmp.cleanup()

    def test_grava_uma_linha(self):
        panel.hook(self.estado, "copilot", "postToolUse", stdin=io.StringIO(json.dumps(COPILOT_POST)), env={})
        linhas = [json.loads(l) for p in self.estado.glob("eventos-*.jsonl") for l in p.read_text().splitlines()]
        self.assertEqual([l["sessao"] for l in linhas], ["cop-1"])

    def test_entrada_ruim_nunca_falha(self):
        self.assertEqual(panel.hook(self.estado, "kiro", "Stop", stdin=io.StringIO("{nao é json"), env={}), 0)
        bloqueado = Path(self.tmp.name) / "arquivo"
        bloqueado.write_text("não é pasta")
        self.assertEqual(panel.hook(bloqueado / "estado", "kiro", "Stop", stdin=io.StringIO("{}"), env={}), 0)

    def test_como_a_ferramenta_roda(self):
        """Subprocesso, JSON no stdin, sem nada no stdout e saída 0, como a ferramenta chama o hook."""
        home = Path(self.tmp.name) / "casa"
        escrever_config(home, "copilot-tudo")
        r = subprocess.run(["python3", str(RAIZ / "fluxo"), "hook", "--home", str(home), "--tool", "copilot",
                            "--event", "sessionStart"], input=json.dumps({"sessionId": "s9", "cwd": "/x"}),
                           capture_output=True, text=True, timeout=30, env=dict(os.environ, TMUX_PANE="%7"))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        cam = config.Caminhos(home=home)
        (linha,) = [json.loads(l) for p in cam.estado.glob("eventos-*.jsonl") for l in p.read_text().splitlines()]
        self.assertEqual((linha["sessao"], linha["evento"], linha["pane"]), ("s9", "inicio", "%7"))
        r = subprocess.run(["python3", str(RAIZ / "fluxo"), "hook", "--home", str(home), "--tool", "copilot",
                            "--event", "x"], input="lixo", capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_flag_que_o_parser_recusa_nao_imprime(self):
        """Entre um `git pull` e o próximo install, o hook instalado ainda manda as flags da versão anterior:
        o argparse imprimiria o uso e sairia com 2."""
        home = Path(self.tmp.name) / "casa"
        escrever_config(home, "copilot-tudo")
        for argv in (["--ferramenta", "copilot", "--evento", "x"], [], ["--tool"]):
            with self.subTest(argv=argv):
                r = subprocess.run(["python3", str(RAIZ / "fluxo"), "hook", "--home", str(home), *argv], input="{}",
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))


class Sessoes(unittest.TestCase):
    def ev(self, sessao, evento, ts, **kw):
        return dict({"sessao": sessao, "evento": evento, "ts": ts, "ferramenta": "copilot", "tool": "", "alvo": "",
                     "cwd": "/repo/api", "pane": ""}, **kw)

    def test_estados_e_ordem(self):
        evs = [self.ev("a", "inicio", T0), self.ev("a", "ferramenta", T0 + 5, tool="bash", alvo="npm test", pane="%1"),
               self.ev("b", "inicio", T0), self.ev("b", "espera", T0 + 10),
               self.ev("c", "inicio", T0), self.ev("c", "fim_turno", T0 + 1),
               self.ev("d", "inicio", T0), self.ev("d", "fim_turno", T0 - 3600),
               self.ev("e", "inicio", T0), self.ev("e", "fim", T0 + 2),
               self.ev("f", "inicio", T0 - 7200), self.ev("f", "fim", T0 - 7000)]
        lista = panel.sessoes(evs, tarefas=[{"pane": "%1", "nome": "PROJ-1"}], agora=T0 + 60)
        self.assertEqual([(x["sessao"], x["estado"]) for x in lista],
                         [("b", "pergunta"), ("c", "sua vez"), ("a", "trabalhando"), ("d", "parada"), ("e", "acabou")])
        a = [x for x in lista if x["sessao"] == "a"][0]
        self.assertEqual((a["nome"], a["tool"], a["alvo"]), ("PROJ-1", "bash", "npm test"))

    def test_texto_cabe_em_100_colunas(self):
        evs = [self.ev(f"s{i}", "ferramenta", T0, tool="bash", alvo="x" * 300, cwd="/" + "y" * 80) for i in range(30)]
        linhas = panel.linhas_texto(panel.sessoes(evs, agora=T0 + 5), largura=100)
        self.assertTrue(all(len(l) <= 100 for l in linhas))
        self.assertIn("nenhuma sessão", panel.linhas_texto([])[0])


class Instalacao(unittest.TestCase):
    def gerar(self, cenario, **cfg):
        with tempfile.TemporaryDirectory() as d:
            cam = escrever_config(d, cenario, modules=["panel"], **cfg)
            arquivos, avisos = montar.montar(config.carregar(cam, montar.modulos_existentes(RAIZ)), RAIZ, cam)
            return {a.rel: a.conteudo.decode() for a in arquivos}, avisos

    def test_copilot_nunca_pre_tool_use(self):
        gerado, _ = self.gerar("copilot")
        hooks = json.loads(gerado[".copilot/hooks/fluxo.json"])["hooks"]
        self.assertNotIn("preToolUse", hooks)
        self.assertEqual(set(hooks), set(panel.EVENTOS["copilot"]))
        self.assertTrue(all(h[0]["bash"].startswith('"$HOME/.local/bin/fluxo" hook') for h in hooks.values()))

    def test_kiro_v3_arquivo_e_v2_agente(self):
        v3, _ = self.gerar("kiro-v3")
        triggers = [h["trigger"] for h in json.loads(v3[".kiro/hooks/fluxo.json"])["hooks"]]
        self.assertIn("PostToolUse", triggers)
        self.assertNotIn(".kiro/agents/fluxo.json", v3)
        v2, _ = self.gerar("kiro-v2")
        agente = json.loads(v2[".kiro/agents/fluxo.json"])
        self.assertEqual(set(agente["hooks"]), {"agentSpawn", "userPromptSubmit", "postToolUse", "stop"})
        self.assertIn("file://~/.kiro/steering/fluxo.md", agente["resources"])
        self.assertIn("skill://~/.kiro/skills/planejar/SKILL.md", agente["resources"])
        self.assertNotIn(".kiro/hooks/fluxo.json", v2)  # nunca nos dois lugares: dispararia em dobro

    def test_claude_fica_de_fora(self):
        # o fluxo não mexe no settings.json do Claude: lá o painel é o agent-tree
        with self.assertRaisesRegex(montar.ErroMontagem, "requer tool:copilot|kiro"):
            self.gerar("claude")


class LigarKiroV2(unittest.TestCase):
    def test_guarda_e_devolve_o_padrao(self):
        with tempfile.TemporaryDirectory() as d:
            cam = escrever_config(d, "kiro-v2", modules=["panel"])
            cfg = config.carregar(cam, montar.modulos_existentes(RAIZ))
            chamadas = []

            def run(argv, **kw):
                chamadas.append(argv)
                return subprocess.CompletedProcess(argv, 0, "meu-agente\n" if argv[-1] == "chat.defaultAgent" else "", "")

            self.assertEqual(panel.desligar(cfg, cam, run), (0, "o painel não tinha mudado o agente padrão"))
            self.assertEqual(chamadas, [])  # sem ligar antes, nunca apaga o padrão do usuário
            self.assertEqual(panel.ligar(cfg, cam, run)[0], 0)
            self.assertEqual(chamadas[-1], ["kiro-cli", "agent", "set-default", "fluxo"])
            panel.ligar(cfg, cam, run)  # a segunda vez não sobrescreve o anterior guardado
            self.assertEqual(panel.desligar(cfg, cam, run)[0], 0)
            self.assertEqual(chamadas[-1], ["kiro-cli", "agent", "set-default", "meu-agente"])
            self.assertEqual(panel.desligar(cfg, cam, run)[1], "o painel não tinha mudado o agente padrão")

    def test_kiro_que_nao_e_o_padrao(self):
        with tempfile.TemporaryDirectory() as d:
            cam = escrever_config(d, "kiro-v2", tools=["copilot", "kiro"], modules=["panel"])
            cfg = config.carregar(cam, montar.modulos_existentes(RAIZ))
            chamadas = []
            run = lambda argv, **kw: chamadas.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")
            self.assertEqual(panel.ligar(cfg, cam, run)[0], 0)
            self.assertEqual(chamadas[-1], ["kiro-cli", "agent", "set-default", "fluxo"])


if __name__ == "__main__":
    unittest.main()
