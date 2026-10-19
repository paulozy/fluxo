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
from fluxo import config, montar, panel, vistas

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
                         ("cop-1", "ferramenta", "bash", "npm test mais", "%4"))  # as linhas viram uma
        k = panel.normalizar("kiro", None, KIRO_POST, env={}, agora=T0)  # o nome do evento vem no payload
        self.assertEqual((k["sessao"], k["evento"], k["tool"], k["alvo"], k["pane"]),
                         ("kiro-1", "ferramenta", "fs_write", "src/app.ts", ""))
        self.assertEqual(panel.normalizar("kiro", "stop", {"session_id": "x"}, env={})["evento"], "fim_turno")
        # o permissionRequest dispara antes das regras que liberam sozinhas: não é pergunta, e não é ouvido
        self.assertIsNone(panel.normalizar("copilot", "permissionRequest", {"sessionId": "x"}, env={}))

    def test_notificacao_do_copilot_pelo_tipo(self):
        for tipo, esperado in (("permission_prompt", "espera"), ("elicitation_dialog", "espera"),
                               ("agent_idle", "fim_turno"), ("agent_completed", "fim_turno"),
                               ("outra_coisa", None), (None, None)):
            with self.subTest(tipo=tipo):
                e = panel.normalizar("copilot", "notification", {"sessionId": "x", "notification_type": tipo}, env={})
                self.assertEqual(e and e["evento"], esperado)
        camel = panel.normalizar("copilot", "notification", {"sessionId": "x", "notificationType": "permission_prompt"}, env={})
        self.assertEqual(camel["evento"], "espera")

    def test_campos_novos(self):
        longo = "faça\n  isto " + "x" * 300
        turno = panel.normalizar("copilot", "userPromptSubmitted", {"sessionId": "x", "prompt": longo}, env={})
        self.assertEqual(turno["prompt"], ("faça isto " + "x" * 300)[:160])
        self.assertNotIn("prompt", panel.normalizar("copilot", "postToolUse", {"sessionId": "x", "prompt": "não"}, env={}))
        inicio = panel.normalizar("copilot", "sessionStart", {"sessionId": "x", "initialPrompt": "/planejar X"}, env={})
        self.assertEqual(inicio["prompt"], "/planejar X")
        falha = panel.normalizar("copilot", "postToolUseFailure", {"sessionId": "x", "toolName": "bash", "error": "1"}, env={})
        self.assertEqual((falha["evento"], falha["falhou"]), ("ferramenta", True))
        self.assertNotIn("falhou", panel.normalizar("copilot", "postToolUse", {"sessionId": "x"}, env={}))
        sub = panel.normalizar("copilot", "subagentStart", {"sessionId": "x", "agentName": "researcher",
                                                            "agentDisplayName": "Researcher"}, env={})
        self.assertEqual((sub["evento"], sub["agente"], sub["agente_tipo"]), ("subagente_inicio", "researcher", "Researcher"))
        # no Copilot, a ferramenta de dentro do subagente não diz de quem é
        self.assertNotIn("agente", panel.normalizar("copilot", "postToolUse", {"sessionId": "x", "agentName": "r"}, env={}))
        cx = panel.normalizar("codex", "PostToolUse", {"session_id": "x", "agent_id": "a1", "agent_type": "explorer",
                                                       "tool_name": "Bash"}, env={})
        self.assertEqual((cx["agente"], cx["agente_tipo"]), ("a1", "explorer"))
        self.assertEqual(panel.normalizar("codex", "SubagentStop", {"session_id": "x", "agent_id": "a1"}, env={})["evento"],
                         "subagente_fim")

    def test_o_que_nao_e_guardado_nao_vira_erro(self):
        with tempfile.TemporaryDirectory() as d:
            estado = Path(d) / "estado"
            for ferramenta, evento, payload in (("copilot", "notification", {"sessionId": "s", "notification_type": "x"}),
                                                ("copilot", "permissionRequest", {"sessionId": "s"}),  # hook antigo
                                                ("kiro", "EventoQueNaoExiste", {"session_id": "s"})):
                with self.subTest(evento=evento):
                    self.assertEqual(panel.hook(estado, ferramenta, evento, stdin=io.StringIO(json.dumps(payload)), env={}), 0)
            self.assertEqual(list(estado.glob("eventos-*.jsonl")), [])
            self.assertFalse((estado / "hook-erros.log").exists())


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
            self.assertEqual(sorted(e["sessao"] for e in panel.LeitorEventos(estado).ler(T0)), ["hoje", "ontem"])

    def test_poda_de_sete_dias(self):
        with tempfile.TemporaryDirectory() as d:
            estado = Path(d)
            for dias in (0, 6, 7, 8, 30):
                (estado / time.strftime("eventos-%Y%m%d.jsonl", time.localtime(T0 - dias * 86400))).write_text("{}\n")
            (estado / "eventos-outra-coisa.jsonl").write_text("{}\n")
            panel.podar(estado, agora=T0)
            sobra = sorted(p.name for p in estado.glob("eventos-*.jsonl"))
            self.assertEqual(sobra, sorted([time.strftime("eventos-%Y%m%d.jsonl", time.localtime(T0 - dias * 86400))
                                            for dias in (0, 6, 7)] + ["eventos-outra-coisa.jsonl"]))

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

    def test_subagentes_de_cada_ferramenta(self):
        codex = [self.ev("cx", "turno", T0, prompt="mapeie o login", ferramenta="codex"),
                 self.ev("cx", "subagente_inicio", T0 + 1, agente="a1", agente_tipo="explorer"),
                 self.ev("cx", "ferramenta", T0 + 2, tool="Bash", agente="a1", agente_tipo="explorer"),
                 self.ev("cx", "ferramenta", T0 + 3, tool="Bash", agente="a1", agente_tipo="explorer"),
                 self.ev("cx", "subagente_fim", T0 + 4, agente="a1", agente_tipo="explorer"),
                 self.ev("cx", "ferramenta", T0 + 5, tool="apply_patch")]
        (x,) = panel.sessoes(codex, agora=T0 + 6)
        self.assertEqual(x["ultimo_prompt"], "mapeie o login")
        self.assertEqual(dict(x["contagem"]), {"apply_patch": 1})  # as do subagente são dele
        sub = x["subagentes"]["a1"]
        self.assertEqual((sub["rotulo"], sub["fim"], dict(sub["contagem"])), ("explorer", T0 + 4, {"Bash": 2}))
        copilot = [self.ev("cp", "subagente_inicio", T0, agente="researcher", agente_tipo="Researcher"),
                   self.ev("cp", "ferramenta", T0 + 1, tool="bash")]
        (y,) = panel.sessoes(copilot, agora=T0 + 2)
        self.assertEqual((y["subagentes"]["researcher"]["fim"], dict(y["contagem"])), (None, {"bash": 1}))
        kiro = [self.ev("k", "ferramenta", T0, tool="subagent", alvo="researcher", ferramenta="kiro")]
        (z,) = panel.sessoes(kiro, agora=T0 + 1)
        (sub,) = z["subagentes"].values()
        self.assertEqual((sub["rotulo"], sub["fim"]), ("researcher", T0))

    def test_subagente_depois_do_stop_nao_volta_a_trabalhar(self):
        evs = [self.ev("s", "ferramenta", T0, tool="bash"), self.ev("s", "fim_turno", T0 + 1),
               self.ev("s", "subagente_fim", T0 + 2, agente="a1"), self.ev("s", "ferramenta", T0 + 3, agente="a1")]
        (x,) = panel.sessoes(evs, agora=T0 + 4)
        self.assertEqual(x["estado"], "sua vez")

    def test_log_guarda_os_ultimos(self):
        evs = [self.ev("s", "ferramenta", T0 + i, tool=f"t{i}") for i in range(panel.LOG + 1)]
        (x,) = panel.sessoes(evs, agora=T0 + 100)
        self.assertEqual(len(x["log"]), panel.LOG)
        self.assertEqual(x["log"][0]["tool"], "t1")

    def test_pane_reaproveitado_nao_herda_a_tarefa(self):
        with tempfile.TemporaryDirectory() as d:
            cam = config.Caminhos(home=d)
            cam.estado.mkdir(parents=True)
            (cam.estado / "tasks.jsonl").write_text(json.dumps({"nome": "PROJ-1", "id": "PROJ-1", "pane": "%3",
                                                                 "pane_pid": 111, "kind": "task"}) + "\n")
            agora = time.time()
            panel.gravar_evento(cam.estado, self.ev("nova", "inicio", agora, pane="%3", cwd="/repo/web"))
            for pid, nome in (("999", "web"), ("111", "PROJ-1")):  # outro processo no %3, ou o da tarefa
                with self.subTest(pid=pid):
                    (x,) = panel.Coletor(cam).coletar(agora, vivos={("%3", pid)})["sessoes"]
                    self.assertEqual(x["nome"], nome)
                    self.assertEqual(bool(x["tarefa"]), nome == "PROJ-1")
            # sem tmux, nenhum pane conta como morto (senão tudo seria "acabou")
            (x,) = panel.Coletor(cam).coletar(agora, vivos=set())["sessoes"]
            self.assertEqual(x["estado"], "sua vez")

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
        dados = {"agora": T0 + 5, "sessoes": panel.sessoes(evs, agora=T0 + 5)}
        linhas = vistas.como_texto(vistas.vista_sessoes(dados, {}, 100, 40)).splitlines()
        self.assertTrue(all(vistas.colunas(l) <= 100 for l in linhas))

    def test_subagente_do_copilot_e_instancia(self):
        """O Copilot dá o nome do tipo, e não um id: o mesmo nome de novo é outra rodada."""
        evs = [self.ev("s", "subagente_inicio", T0, agente="explore"), self.ev("s", "subagente_fim", T0 + 1, agente="explore"),
               self.ev("s", "subagente_inicio", T0 + 2, agente="explore"),  # de novo: está rodando
               self.ev("s", "subagente_inicio", T0 + 3, agente="explore"),  # e outro em paralelo
               self.ev("s", "subagente_fim", T0 + 4, agente="explore")]     # fecha o mais velho aberto
        (x,) = panel.sessoes(evs, agora=T0 + 5)
        fins = sorted(((v["inicio"], v["fim"]) for v in x["subagentes"].values()), key=lambda t: t[0])
        self.assertEqual(fins, [(T0, T0 + 1), (T0 + 2, T0 + 4), (T0 + 3, None)])

    def test_payload_com_campo_que_nao_e_texto(self):
        """Um sessionId que chega como objeto não pode virar chave do painel (quebraria por dois dias)."""
        e = panel.normalizar("copilot", "postToolUse", {"sessionId": {"x": 1}, "toolName": ["bash"], "cwd": 3}, env={"TMUX_PANE": "%1"})
        self.assertEqual((e["sessao"], e["tool"], e["cwd"]), ("pane-%1", "", "3"))
        k = panel.normalizar("kiro", "PostToolUse", {"session_id": "k", "tool_name": "subagent",
                                                     "tool_input": {"command": "x", "name": "researcher"}}, env={})
        self.assertEqual(k["alvo"], "researcher")  # o nome, e não um command do subagente


class Instalacao(unittest.TestCase):
    def gerar(self, cenario, **cfg):
        with tempfile.TemporaryDirectory() as d:
            cam = escrever_config(d, cenario, modules=["panel"], **cfg)
            arquivos, avisos = montar.montar(config.carregar(cam, montar.modulos_existentes(RAIZ)), RAIZ, cam)
            return {a.rel: a.conteudo.decode() for a in arquivos}, avisos

    def test_hooks_do_codex(self):
        gerado, _ = self.gerar("codex")
        hooks = json.loads(gerado[".codex/hooks.json"])["hooks"]
        # sem PermissionRequest: a doc não diz se um hook que falha ali nega a ação
        self.assertEqual(list(hooks), ["SessionStart", "UserPromptSubmit", "PostToolUse", "SubagentStart",
                                       "SubagentStop", "Stop", "SessionEnd"])
        self.assertNotIn("PermissionRequest", hooks)
        for evento, grupos in hooks.items():
            with self.subTest(evento=evento):
                (grupo,) = grupos
                self.assertNotIn("matcher", grupo)
                (h,) = grupo["hooks"]
                self.assertEqual(h["type"], "command")
                self.assertIn(f"hook --tool codex --event {evento}", h["command"])
                self.assertLessEqual(h["timeout"], 3 if evento == "SessionEnd" else 5)
        self.assertEqual(set(hooks), set(panel.EVENTOS["codex"]))

    def test_copilot_nunca_pre_tool_use(self):
        gerado, _ = self.gerar("copilot")
        hooks = json.loads(gerado[".copilot/hooks/fluxo.json"])["hooks"]
        self.assertNotIn("preToolUse", hooks)
        self.assertNotIn("permissionRequest", hooks)
        for ev in ("postToolUseFailure", "subagentStart", "subagentStop", "notification"):
            self.assertIn(ev, hooks)
        self.assertEqual(set(hooks), set(panel.EVENTOS["copilot"]))
        self.assertTrue(all(h[0]["bash"].startswith('"$HOME/.local/bin/fluxo" hook') for h in hooks.values()))

    def test_kiro_v3_arquivo_e_v2_agente(self):
        v3, _ = self.gerar("kiro-v3")
        hooks_v3 = json.loads(v3[".kiro/hooks/fluxo.json"])["hooks"]
        self.assertIn("PostToolUse", [h["trigger"] for h in hooks_v3])
        # um Stop que sai com 1 faz o Kiro V3 começar outro turno: o comando nunca sai com 1
        self.assertTrue(all(h["action"]["command"].endswith(" || true") for h in hooks_v3))
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


class Codex(unittest.TestCase):
    def test_normaliza_o_payload(self):
        patch = {"session_id": "cx-1", "hook_event_name": "PostToolUse", "tool_name": "apply_patch", "cwd": "/r",
                 "tool_input": "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** End Patch"}
        e = panel.normalizar("codex", None, patch, env={"TMUX_PANE": "%2"}, agora=T0)
        self.assertEqual((e["sessao"], e["evento"], e["tool"], e["alvo"], e["pane"]),
                         ("cx-1", "ferramenta", "apply_patch", "src/a.py", "%2"))
        bash = panel.normalizar("codex", "PostToolUse", {"session_id": "cx-1", "tool_name": "Bash",
                                                          "tool_input": {"command": "npm test"}}, env={})
        self.assertEqual(bash["alvo"], "npm test")
        self.assertEqual(panel.normalizar("codex", "Stop", {"session_id": "cx-1"}, env={})["evento"], "fim_turno")
        for entrada, alvo in (("*** Begin Patch\n*** Add File: novo.py\n+x\n*** End Patch", "novo.py"),
                              ({"input": "*** Begin Patch\n*** Delete File: velho.py\n*** End Patch"}, "velho.py")):
            with self.subTest(alvo=alvo):
                e = panel.normalizar("codex", "PostToolUse", {"session_id": "s", "tool_input": entrada}, env={})
                self.assertEqual(e["alvo"], alvo)

    def test_ligar_lembra_do_hooks(self):
        with tempfile.TemporaryDirectory() as d:
            run = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
            for tools, trechos in ((["codex"], ["nada a ligar", "/hooks"]),
                                   (["kiro", "codex"], ["agente fluxo é o padrão do Kiro agora", "/hooks"])):
                with self.subTest(tools=tools):
                    cam = escrever_config(d, "kiro-v2", tools=tools, modules=["panel"])
                    code, msg = panel.ligar(config.carregar(cam, montar.modulos_existentes(RAIZ)), cam, run)
                    self.assertEqual(code, 0)
                    for t in trechos:
                        self.assertIn(t, msg)


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


class CursesFalso:
    """O que o _atributos usa do curses, com `cores` (curses.COLORS) e um teto de pares que o terminal aceita."""
    A_NORMAL, A_BOLD, A_DIM, A_REVERSE = 0, 1 << 21, 1 << 20, 1 << 18
    COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_YELLOW, COLOR_BLUE, COLOR_MAGENTA, COLOR_CYAN, COLOR_WHITE = range(8)

    class error(Exception):
        pass

    def __init__(self, cores, com_cor=True, pares=999):
        self.COLORS, self.com_cor, self.teto, self.pares = cores, com_cor, pares, {}

    def has_colors(self):
        return self.com_cor

    def use_default_colors(self):
        pass

    def init_pair(self, n, texto, fundo):
        if n > self.teto:
            raise self.error("pares demais")
        self.pares[n] = (texto, fundo)

    def color_pair(self, n):
        return n << 8


class Atributos(unittest.TestCase):
    def par(self, c, attr, estilo):
        return c.pares[attr[estilo] >> 8 & 0xFF]

    def test_sem_cor(self):
        for c, env in ((CursesFalso(256), {"NO_COLOR": "1"}), (CursesFalso(256, com_cor=False), {})):
            attr = panel._atributos(c, env)
            self.assertEqual(c.pares, {})
            self.assertEqual((attr["cab"], attr["sel"], attr["fraco"]), (c.A_REVERSE, c.A_REVERSE, c.A_DIM))
            self.assertEqual(attr["aba_ativa"], c.A_REVERSE | c.A_BOLD)

    def test_256_cores(self):
        c = CursesFalso(256)
        attr = panel._atributos(c, {})
        self.assertEqual(self.par(c, attr, "fraco"), (245, -1))  # cinza de verdade, e não o A_DIM
        self.assertEqual(self.par(c, attr, "cab"), (231, 25))  # quem tem fundo tem texto explícito
        self.assertEqual(self.par(c, attr, "m_opus"), (173, -1))
        self.assertTrue(attr["erro"] & c.A_BOLD)
        self.assertEqual(attr["sel"], c.A_REVERSE)
        self.assertLessEqual(len(c.pares), 20)

    def test_8_cores(self):
        c = CursesFalso(8)
        attr = panel._atributos(c, {})
        self.assertEqual(self.par(c, attr, "ok"), (c.COLOR_GREEN, -1))
        self.assertEqual(self.par(c, attr, "cab"), (c.COLOR_WHITE, c.COLOR_BLUE))
        self.assertEqual(attr["fraco"], c.A_DIM)  # sem cinza em 8 cores

    def test_par_recusado_fica_no_mono(self):
        c = CursesFalso(256, pares=4)
        attr = panel._atributos(c, {})
        self.assertEqual(len(c.pares), 4)
        self.assertEqual(attr["cab"], c.A_REVERSE)


if __name__ == "__main__":
    unittest.main()
