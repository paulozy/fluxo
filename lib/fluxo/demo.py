"""Os dados de mentira do `fluxo panel --demo`: as 4 abas lotadas, sem ler nem gravar nada no disco.

É o que se olha para conferir o visual num terminal novo (o Terminal.app, o iTerm2, um tmux de outro TERM), e é a
fixture "cheia" dos testes de largura e altura. Tudo aqui tem o mesmo formato do que o `panel.Coletor` devolve.
"""
import calendar
import time

LIGADOS = {"task", "reviewer", "watch", "lock"}
PLANO = {"status": "aprovado", "repos": "api, web", "base": "develop", "criado": "2026-10-15",
         "checklist": [(True, "plano aprovado"), (True, "branch e worktree"), (True, "implementação com testes"),
                       (False, "revisão"), (False, "PRs/MRs abertas")]}


def _sessao(agora, id_, nome, estado, ha, uso=None, **kw):
    log = [{"ts": agora - ha - 60 + i * 5, "evento": "ferramenta", "tool": t, "alvo": a}
           for i, (t, a) in enumerate((("view", "src/pedidos.ts"), ("edit", "src/pedidos.ts"), ("bash", "npm test"),
                                       ("grep", "TODO"), ("bash", "npm run lint")))]
    base = {"sessao": id_, "nome": nome, "ferramenta": "copilot", "estado": estado, "inicio": agora - 3600,
            "ultimo": agora - ha, "ha": ha, "tool": "bash", "alvo": "npm run lint", "cwd": f"~/projetos/{nome}",
            "pane": "", "evento": None, "contagem": {"bash": 9, "edit": 6, "view": 5, "grep": 2},
            "subagentes": {}, "ultimo_prompt": f"/implementar PROJ-12 no {nome}", "log": log, "tarefa": None,
            "uso": dict({"fonte": "statusline", "ts": agora - min(ha, 40)}, **uso) if uso else None}
    return dict(base, **kw)


def dados(agora=None):
    agora = agora or time.time()
    reviewer = {"r": {"base": "code-reviewer", "rotulo": "code-reviewer", "inicio": agora - 90, "fim": None,
                      "contagem": {"view": 4, "grep": 2}},
                "e": {"base": "explore", "rotulo": "explore", "inicio": agora - 900, "fim": agora - 700, "contagem": {}}}
    sessoes = [
        _sessao(agora, "demo-2", "web", "pergunta", 40, pane="%2", tarefa={"id": "PROJ-13"},
                uso={"modelo": "gpt-5.1", "effort": "medium", "ctx_pct": 22.0, "ctx_max": 272000, "entrada": 240000,
                     "saida": 9100, "cache": 180000, "creditos": 4.2, "premium": None}),
        _sessao(agora, "demo-1", "api", "trabalhando", 3, pane="%1", tarefa={"id": "PROJ-12"}, subagentes=reviewer,
                uso={"modelo": "claude-sonnet-4.5", "effort": "high", "ctx_pct": 63.0, "ctx_max": 200000,
                     "entrada": 1240000, "saida": 20300, "cache": 980000, "creditos": 12.34, "premium": None}),
        _sessao(agora, "demo-3", "app", "sua vez", 200, pane="%3",
                uso={"modelo": "claude-opus-4.5", "effort": "high", "ctx_pct": 84.0, "ctx_max": 200000,
                     "entrada": 3100000, "saida": 51000, "cache": 2700000, "creditos": 31.2, "premium": None}),
        _sessao(agora, "demo-4", "lib", "parada", 2700, pane="%4",
                uso={"modelo": "claude-haiku-4.5", "effort": None, "ctx_pct": 12.0, "ctx_max": 200000,
                     "entrada": 52000, "saida": 1800, "cache": 30000, "creditos": 0.42, "premium": None}),
        _sessao(agora, "demo-5", "docs", "acabou", 1500, ferramenta="kiro", pane="", contagem={"fs_read": 3}),
    ]
    ano, mes = time.gmtime(agora)[:2]
    renova = calendar.timegm((ano + mes // 12, mes % 12 + 1, 1, 0, 0, 0))  # o dia 1º do mês que vem, 00:00 UTC
    return {
        "agora": agora, "ligados": set(LIGADOS), "sessoes": sessoes,
        "planos": {"PROJ-12": dict(PLANO, mtime=agora - 600), "PROJ-13": dict(PLANO, status="rascunho", mtime=agora - 9000)},
        "revisoes": {"PROJ-12": [{"quando": "20261019-1010", "rodada": 1, "veredito": "REPROVAR", "mtime": agora - 5400},
                                 {"quando": "20261019-1140", "rodada": 2, "veredito": "APROVÁVEL", "mtime": agora - 1200}]},
        "avisos": [{"ts": agora - 300, "tipo": "ci", "ref": "time/api!42", "texto": "CI verde", "titulo": "PROJ-12: pedidos",
                    "ci": "SUCCESS"},
                   {"ts": agora - 1800, "tipo": "review", "ref": "time/api!42", "texto": "pediram mudança",
                    "titulo": "PROJ-12: pedidos"},
                   {"ts": agora - 2400, "tipo": "ci", "ref": "time/web!17", "texto": "CI quebrou", "titulo": "PROJ-13: carrinho",
                    "ci": "FAILURE"}],
        "vigia": {"ultimo_tick": agora - 70},
        "trava": {"dono": {"sessao": "demo-1", "id": "PROJ-12"}, "vivo": True, "texto": "com PROJ-12 (api) há 4 min"},
        "erros_hook": [], "erros_leitura": [],
        "cota": {"usado": 1311.0, "total": 1900.0, "pct": 69.0, "unidade": "créditos", "renova": renova,
                 "ts": agora - 7200, "plano": "business", "excedente": 0, "ilimitado": False},
    }
