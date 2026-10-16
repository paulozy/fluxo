"""As vistas do `fluxo panel`: funções puras, dos dados para as linhas da tela.

Cada linha é uma lista de segmentos (texto, estilo). A tela traduz o estilo em cor, e o --text o ignora.
Nada aqui lê disco: quem lê é o `panel.Coletor`. O texto de terceiro (prompt, título de PR, linha de
revisão, erro do watch) passa por `limpo` antes de ir para a linha, com ou sem cor.

Estilos: titulo, ok, atencao, erro, fraco, sel, aba (e None, o normal).
"""
import re
import time
import unicodedata

from . import fonte

ABAS = (("session", "sessão"), ("task", "tarefa"), ("global", "global"), ("list", "sessões"))
ESTILO_DO_ESTADO = {"pergunta": "erro", "sua vez": "atencao", "trabalhando": "ok", "parada": "fraco",
                    "acabou": "fraco"}
ESTILO_DO_VEREDITO = {"APROVÁVEL": "ok", "REPROVAR": "erro"}
GLIFOS_UTF8 = {"cheio": "█", "vazio": "░", "ponto": "•", "seta": "→"}
GLIFOS_ASCII = {"cheio": "#", "vazio": ".", "ponto": "*", "seta": ">"}
VEREDITO = re.compile(r"^\s*(?:\*\*)?Veredito:(?:\*\*)?\s*(.+?)\s*$", re.M)
CAIXA = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.*\S)\s*$")
RODADA = re.compile(r"(\d{8}-\d{4})-r(\d+)\.md$")
SEGUE_TROCA = 30  # s: a sessão seguida só troca sozinha depois de ficar parada ou na sua vez por isso
DIA = 86400


# ── texto e largura ─────────────────────────────────────────────────────────

def limpo(texto, n=200):
    """Uma linha, sem caracteres de controle e com os espaços colapsados."""
    if not texto:
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in str(texto)).split())[:n]


def _largura(c):
    if unicodedata.combining(c):
        return 0
    return 2 if unicodedata.east_asian_width(c) in "WF" else 1


def colunas(texto):
    """Quantas colunas o texto ocupa no terminal (CJK e emoji ocupam 2), e não quantos caracteres tem."""
    return sum(_largura(c) for c in texto)


def cortar(texto, n):
    out, usado = [], 0
    for c in texto:
        w = _largura(c)
        if usado + w > n:
            break
        out.append(c)
        usado += w
    return "".join(out)


def preencher(texto, n):
    """O texto em exatamente `n` colunas: cortado, ou completado com espaços (o `:<n` conta caracteres)."""
    t = cortar(texto, n)
    return t + " " * (n - colunas(t))


def ajustar(linha, largura):
    """Corta os segmentos para caber em `largura` colunas."""
    out, resto = [], max(0, largura)
    for texto, estilo in linha:
        if resto <= 0:
            break
        t = cortar(texto, resto)
        if t:
            out.append((t, estilo))
            resto -= colunas(t)
    return out


def como_texto(linhas):
    return "\n".join("".join(t for t, _ in linha) for linha in linhas)


def _tempo(segundos):
    m = int(max(0, segundos) // 60)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d}"


def _hora(ts):
    return time.strftime("%H:%M", time.localtime(ts)) if ts else "--:--"


# ── o que vem dos arquivos (o Coletor lê; aqui só se interpreta o texto) ───

def ler_plano(texto):
    """O plano de {dados}/planos/<id>.md: o frontmatter e o checklist do fluxo. Nunca levanta.

    O checklist é só o trecho de `## Checklist do fluxo` até o próximo `## `: o critério de aceite também
    tem caixas, e o /concluir acrescenta um `## Resumo`. None quando o plano não tem a seção."""
    texto = (texto or "").replace("\r\n", "\n")
    try:
        meta, corpo = fonte.ler(texto, "plano")
    except Exception:  # noqa: BLE001 — ErroFonte, ou um frontmatter torto: o painel não cai por isso
        meta, corpo = {}, texto
    checklist, dentro = None, False
    for linha in corpo.splitlines():
        if linha.startswith("## "):
            dentro = linha[3:].strip().lower() == "checklist do fluxo"
            if dentro:
                checklist = []
            continue
        m = CAIXA.match(linha) if dentro else None
        if m:
            checklist.append((m.group(1) != " ", limpo(m.group(2), 80)))
    repos = meta.get("repos")
    return {"status": limpo(meta.get("status"), 20) or "?",
            "repos": ", ".join(limpo(r, 40) for r in repos) if isinstance(repos, list) else limpo(repos, 80),
            "base": limpo(meta.get("base"), 40), "criado": limpo(meta.get("criado"), 20), "checklist": checklist}


def ler_revisao(nome, texto):
    """Uma rodada do /revisar-pr: <AAAAMMDD-HHMM>-r<n>.md, com a linha `Veredito:` (ou `**Veredito:**`)."""
    m, v = RODADA.search(nome), VEREDITO.search(texto or "")
    return {"quando": m.group(1) if m else "", "rodada": int(m.group(2)) if m else 0,
            "veredito": limpo(v.group(1), 40) if v else "?"}


def cita(id_, texto):
    """O texto cita a tarefa: `PROJ-1` não casa com `PROJ-12`, e o id pode ter ponto ou hífen."""
    return bool(id_) and re.search(r"(?<![\w.-])" + re.escape(id_) + r"(?![\w-])", texto or "", re.I) is not None


# ── o que a tela mostra ─────────────────────────────────────────────────────

def seguir(sessoes, tela, agora):
    """A sessão da aba 1: a fixada (f, --follow); senão a mais recente, com histerese, para a vista não
    pular de uma para outra a cada segundo com sessões em paralelo."""
    por_id = {x["sessao"]: x for x in sessoes}
    if tela.get("fixada"):  # fixada fica fixada, mesmo que tenha sumido: a aba 1 diz isso
        return tela.get("seguida")
    atual = por_id.get(tela.get("seguida"))
    if atual and (atual["estado"] == "trabalhando"
                  or (atual["estado"] in ("sua vez", "pergunta") and agora - atual["ultimo"] < SEGUE_TROCA)):
        return atual["sessao"]
    recente = max(sessoes, key=lambda x: x["ultimo"], default=None)
    return recente["sessao"] if recente else None


def ordem_das_tarefas(dados):
    """Os ids com plano, do mais novo para o mais velho (as setas da aba 2 andam nessa ordem)."""
    planos = dados.get("planos") or {}
    return sorted(planos, key=lambda i: planos[i].get("mtime", 0), reverse=True)


def tarefa_atual(dados, tela, seguida=None):
    """A tarefa da aba 2: a fixada (←/→, --task); senão a da sessão seguida; senão o plano mais novo."""
    if tela.get("tarefa"):
        return tela["tarefa"]
    for x in dados.get("sessoes") or ():
        if x["sessao"] == seguida and (x.get("tarefa") or {}).get("id"):
            return x["tarefa"]["id"]
    ordem = ordem_das_tarefas(dados)
    return ordem[0] if ordem else None


def alertas(dados):
    """O que pede atenção agora: (texto, estilo)."""
    agora, out = dados["agora"], []
    for x in dados.get("sessoes") or ():
        if x["estado"] == "pergunta":
            out.append((f"{limpo(x['nome'], 30)} está esperando uma resposta sua", "erro"))
    erros = dados.get("erros_hook") or []
    if erros:
        out.append((f"{len(erros)} erro(s) de hook nas últimas 24 h: {limpo(erros[-1], 120)}", "atencao"))
    trava = dados.get("trava")
    if trava and trava.get("dono") and trava.get("vivo") is False:
        out.append(("a trava está com um dono morto: o próximo acquire fica com ela", "atencao"))
    for ref, aviso in sorted(_ultimo_ci(dados.get("avisos") or ()).items()):
        if aviso.get("ci") == "FAILURE":
            out.append((f"CI quebrou em {limpo(ref, 40)} ({limpo(aviso.get('titulo'), 50)})", "erro"))
    for erro in dados.get("erros_leitura") or ():
        out.append((f"o panel não leu {limpo(erro, 120)}", "atencao"))
    erro_watch = (dados.get("vigia") or {}).get("erro")
    if erro_watch:
        out.append((f"o watch falhou: {limpo(erro_watch, 100)}", "atencao"))
    return out


def _ultimo_ci(avisos):
    ultimo = {}
    for a in avisos:
        if a.get("tipo") == "ci" and a.get("ref"):
            if a["ref"] not in ultimo or a.get("ts", 0) >= ultimo[a["ref"]].get("ts", 0):
                ultimo[a["ref"]] = a
    return ultimo


def barra_de_abas(tela, dados):
    linha = []
    for n, (chave, rotulo) in enumerate(ABAS, 1):
        linha.append((f" {n} {rotulo} ", "aba" if tela.get("aba") == chave else "fraco"))
    esperando = sum(1 for x in dados.get("sessoes") or () if x["estado"] in ("pergunta", "sua vez"))
    if esperando:
        linha.append((f"   {esperando} esperando você", "atencao"))
    return linha


def glifos_para(codificacao):
    """Fora de UTF-8, o curses não desenha █ nem •: os glifos caem em ASCII."""
    return GLIFOS_UTF8 if "utf" in (codificacao or "").lower().replace("-", "") else GLIFOS_ASCII


def rodape(tela, glifos=GLIFOS_UTF8):
    comum = "1-4 abas · ↑↓ escolher · Enter ir até a sessão · q sair"
    extra = {"session": " · f seguir esta · Tab próxima · PgUp/PgDn rolar",
             "task": " · ←→ outra tarefa", "list": " · f seguir · h esconder"}.get(tela.get("aba"), "")
    texto = comum + extra
    if glifos is GLIFOS_ASCII:
        texto = texto.replace(" · ", " | ").replace("↑↓", "j/k").replace("←→", "<-/->")
        texto = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return [(texto, "fraco")]


# ── as teclas: a tela só desenha; o que cada tecla faz é daqui, e é testável ──

TECLAS = {"1": "aba:session", "2": "aba:task", "3": "aba:global", "4": "aba:list", "k": "acima", "j": "abaixo",
          "\n": "ir", "\r": "ir", "f": "seguir", "\t": "proxima", "h": "esconder", "q": "sair", "\x1b": "sair"}
ESPECIAIS = {"KEY_UP": "acima", "KEY_DOWN": "abaixo", "KEY_LEFT": "tarefa_ant", "KEY_RIGHT": "tarefa_prox",
             "KEY_PPAGE": "pagina_acima", "KEY_NPAGE": "pagina_abaixo", "KEY_ENTER": "ir", "KEY_RESIZE": None}
PAGINA = 10


def acao_da_tecla(tecla, curses_mod):
    """A ação de uma tecla do getch: o nome em ESPECIAIS (KEY_UP…, pelos códigos do curses) ou em TECLAS."""
    for nome, acao in ESPECIAIS.items():
        if tecla == getattr(curses_mod, nome, object()):
            return acao
    return TECLAS.get(chr(tecla)) if isinstance(tecla, int) and 0 <= tecla < 256 else None


def visiveis(dados, tela):
    return [x for x in dados.get("sessoes") or () if x["sessao"] not in (tela.get("escondidas") or ())]


def aplicar(acao, tela, dados):
    """Muda o estado da tela (`tela`, um dict) conforme a ação. O `ir` (pular para o pane) é da tela."""
    ids = [x["sessao"] for x in visiveis(dados, tela)]
    if not acao:
        return tela
    if acao.startswith("aba:"):
        tela.update(aba=acao[4:], rolagem=0)
    elif acao in ("acima", "abaixo") and ids:
        chave = "seguida" if tela.get("aba") == "session" else "escolhida"  # na aba 1, ↑↓ trocam a seguida
        i = ids.index(tela.get(chave)) if tela.get(chave) in ids else 0
        tela[chave] = ids[max(0, i - 1) if acao == "acima" else min(len(ids) - 1, i + 1)]
        if chave == "seguida":
            tela.update(fixada=True, rolagem=0)
    elif acao == "seguir" and tela.get("aba") == "session":  # na aba 1, `f` fixa a que está na tela
        tela["fixada"] = bool(tela.get("seguida"))
    elif acao == "seguir" and tela.get("escolhida") in ids:
        tela.update(seguida=tela["escolhida"], fixada=True, rolagem=0)
    elif acao == "proxima" and ids:
        i = ids.index(tela.get("seguida")) if tela.get("seguida") in ids else -1
        tela.update(seguida=ids[(i + 1) % len(ids)], fixada=True, rolagem=0)
    elif acao in ("tarefa_ant", "tarefa_prox"):
        ordem = ordem_das_tarefas(dados)
        if ordem:
            atual = tarefa_atual(dados, tela, tela.get("seguida"))
            i = ordem.index(atual) if atual in ordem else -1
            tela.update(tarefa=ordem[(i + (1 if acao == "tarefa_prox" else -1)) % len(ordem)], rolagem=0)
    elif acao == "pagina_acima":
        tela["rolagem"] = max(0, (tela.get("rolagem") or 0) - PAGINA)
    elif acao == "pagina_abaixo":
        tela["rolagem"] = min(1000, (tela.get("rolagem") or 0) + PAGINA)
    elif acao == "esconder" and tela.get("escolhida") in ids:
        tela.setdefault("escondidas", set()).add(tela["escolhida"])
    return tela


def _janela(linhas, altura, inicio):
    """As `altura` linhas a partir de `inicio`, sem passar do fim."""
    if altura <= 0:
        return []
    inicio = max(0, min(inicio, max(0, len(linhas) - altura)))
    return linhas[inicio:inicio + altura]


def vista_sessoes(dados, tela, largura, altura, glifos=GLIFOS_UTF8):
    """Aba 4: uma linha por sessão viva, com quem espera você em cima (a ordem do panel.ORDEM)."""
    lista = [x for x in dados.get("sessoes") or () if x["sessao"] not in (tela.get("escondidas") or ())]
    if not lista:
        return [[("nenhuma sessão com eventos hoje (os hooks estão instalados? fluxo panel enable)", "fraco")]]
    cab = [[(f"{'sessão':<18} {'ferramenta':<10} {'estado':<11} {'há':>8}  {'pane':<5} última ação", "titulo")],
           [("-" * min(largura, 90), "fraco")]]
    corpo, escolhida = [], None
    for n, x in enumerate(lista):
        sel = x["sessao"] == tela.get("escolhida")
        escolhida = n if sel else escolhida
        acao = limpo(f"{x.get('tool', '')} {x.get('alvo', '')}", 80)
        corpo.append([(f"{preencher(limpo(x['nome'], 40), 18)} {preencher(x.get('ferramenta') or '', 10)} ", "sel" if sel else None),
                      (f"{x['estado']:<11}", "sel" if sel else ESTILO_DO_ESTADO[x["estado"]]),
                      (f" {_tempo(x['ha']):>8}  {(x.get('pane') or '')[:5]:<5} {acao}", "sel" if sel else None)])
    visiveis = max(1, altura - len(cab))
    inicio = 0 if escolhida is None or escolhida < visiveis else escolhida - visiveis + 1
    return [ajustar(l, largura) for l in cab + _janela(corpo, visiveis, inicio)]


def vista_sessao(dados, tela, largura, altura, glifos=GLIFOS_UTF8):
    """Aba 1: a sessão seguida, com o prompt, as ferramentas, os subagentes e o log."""
    x = next((s for s in dados.get("sessoes") or () if s["sessao"] == tela.get("seguida")), None)
    if x is None and tela.get("fixada") and tela.get("seguida"):
        return [[(f"a sessão fixada ({limpo(tela['seguida'], 40)}) não está entre as de hoje: Tab passa para outra", "atencao")]]
    if x is None:
        return [[("nenhuma sessão para seguir (abra uma com fluxo task, ou espere um evento)", "fraco")]]
    out = [[("sessão ", "fraco"), (limpo(x["nome"], 30), "titulo"), (f"  {x.get('ferramenta') or ''}  ", None),
            (x["estado"], ESTILO_DO_ESTADO[x["estado"]]), (f"  há {_tempo(x['ha'])}  pane {x.get('pane') or '-'}", "fraco")]]
    tarefa = (x.get("tarefa") or {}).get("id")
    out.append([("pasta ", "fraco"), (limpo(x.get("cwd"), 100) or "-", None)]
               + ([("  tarefa ", "fraco"), (limpo(tarefa, 40), "titulo")] if tarefa else []))
    out.append([("prompt ", "fraco"), (limpo(x.get("ultimo_prompt"), 160) or "-", None)])
    out.append([])
    out.append([("ferramentas", "titulo")])
    contagem = sorted((x.get("contagem") or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    maior = max((n for _, n in contagem), default=1)
    for nome, n in contagem:
        cheio = max(1, round(20 * n / maior))
        out.append([(f"  {preencher(limpo(nome, 40), 18)} ", None), (glifos["cheio"] * cheio, "ok"),
                    (glifos["vazio"] * (20 - cheio), "fraco"), (f" {n}", "fraco")])
    if not contagem:
        out.append([("  nenhuma ainda", "fraco")])
    out.append([])
    out.append([("subagentes", "titulo")])
    subs = sorted((x.get("subagentes") or {}).values(), key=lambda s: s["inicio"])
    for s in subs:
        dentro = ", ".join(f"{limpo(t, 16)} {n}" for t, n in sorted((s.get("contagem") or {}).items(), key=lambda kv: -kv[1]))
        out.append([(f"  {glifos['ponto']} {preencher(limpo(s['rotulo'], 40), 24)} ", None),
                    ("rodando" if s["fim"] is None else "terminou", "ok" if s["fim"] is None else "fraco"),
                    (f"  {dentro}" if dentro else "", "fraco")])
    if not subs:
        out.append([("  nenhum", "fraco")])
    out.append([])
    out.append([("log", "titulo")])
    log = []
    for e in reversed(x.get("log") or ()):
        quem = f"{limpo(e.get('agente_tipo') or e.get('agente'), 16)}: " if e.get("agente") else ""
        texto = limpo(f"{quem}{e.get('tool') or ''} {e.get('alvo') or e.get('prompt') or ''}", 120)
        log.append([(f"  {time.strftime('%H:%M:%S', time.localtime(e['ts']))} ", "fraco"),
                    (f"{e.get('evento') or '':<16} ", "erro" if e.get("falhou") else "fraco"), (texto, None)])
    espaco = max(1, altura - len(out))
    out += _janela(log, espaco, tela.get("rolagem") or 0)
    return [ajustar(l, largura) for l in out]


def vista_tarefa(dados, tela, largura, altura, glifos=GLIFOS_UTF8):
    """Aba 2: o plano da tarefa, o checklist do fluxo, as sessões, as revisões e as PRs/MRs."""
    id_ = tarefa_atual(dados, tela, tela.get("seguida"))
    if not id_:
        return [[("nenhuma tarefa com plano ainda (o planejar grava em planos/<id>.md)", "fraco")]]
    ordem = ordem_das_tarefas(dados)
    posicao = f"  ({ordem.index(id_) + 1}/{len(ordem)})" if id_ in ordem else ""
    out = [[("tarefa ", "fraco"), (limpo(id_, 40), "titulo"), (posicao, "fraco")]]
    plano = (dados.get("planos") or {}).get(id_)
    if not plano:
        out.append([(f"  sem plano em planos/{limpo(id_, 40)}.md", "fraco")])
    else:
        status = plano["status"]
        out.append([("status ", "fraco"), (status, "ok" if status in ("aprovado", "concluido") else "atencao"),
                    ("  repos ", "fraco"), (plano["repos"] or "-", None), ("  base ", "fraco"), (plano["base"] or "-", None),
                    ("  criado ", "fraco"), (plano["criado"] or "-", None)])
        out.append([])
        checklist = plano["checklist"]
        if checklist is None:
            out.append([("sem checklist no plano", "fraco")])
        else:
            feitos, total = sum(1 for f, _ in checklist if f), len(checklist)
            cheio = round(20 * feitos / total) if total else 0
            out.append([("checklist ", "titulo"), (glifos["cheio"] * cheio, "ok"), (glifos["vazio"] * (20 - cheio), "fraco"),
                        (f" {feitos}/{total}", "fraco")])
            for feito, texto in checklist:
                out.append([(f"  [{'x' if feito else ' '}] ", "ok" if feito else "fraco"), (texto, None if feito else "fraco")])
    out.append([])
    out.append([("sessões", "titulo")])
    vivas = [x for x in dados.get("sessoes") or () if (x.get("tarefa") or {}).get("id") == id_]
    for x in vivas:
        out.append([(f"  {preencher(limpo(x['nome'], 40), 18)} {preencher(x.get('ferramenta') or '', 8)} ", None),
                    (x["estado"], ESTILO_DO_ESTADO[x["estado"]])])
    if not vivas:
        out.append([("  nenhuma viva", "fraco")])
    ligados = dados.get("ligados") or set()
    if "reviewer" in ligados:
        out.append([])
        out.append([("revisões", "titulo")])
        revs = sorted((dados.get("revisoes") or {}).get(id_, ()), key=lambda r: r["rodada"])
        for r in revs:
            out.append([(f"  r{r['rodada']} {r['quando']}  ", "fraco"), (r["veredito"], ESTILO_DO_VEREDITO.get(r["veredito"], "atencao"))])
        if not revs:
            out.append([("  nenhuma rodada", "fraco")])
    if "watch" in ligados:
        out.append([])
        out.append([("PRs/MRs", "titulo")])
        da_tarefa = [a for a in dados.get("avisos") or () if cita(id_, a.get("titulo"))]
        por_ref = {}
        for a in da_tarefa:  # o último aviso de cada PR/MR, e o último de CI
            por_ref.setdefault(a.get("ref"), {})["ci" if a.get("tipo") == "ci" else "outro"] = a
        for ref, avisos in sorted(por_ref.items(), key=lambda kv: str(kv[0])):
            linha = [(f"  {limpo(ref, 40)}  ", None)]
            if "outro" in avisos:
                linha.append((limpo(avisos["outro"].get("texto"), 50), None))
            if "ci" in avisos:
                linha.append((f"  {limpo(avisos['ci'].get('texto'), 20)}", "erro" if avisos["ci"].get("ci") == "FAILURE" else "ok"))
            out.append(linha)
        if not por_ref:
            out.append([("  nenhum aviso que cite a tarefa no título", "fraco")])
    return [ajustar(l, largura) for l in _janela(out, max(1, altura), tela.get("rolagem") or 0)]


def vista_global(dados, tela, largura, altura, glifos=GLIFOS_UTF8):
    """Aba 3: as sessões por estado, a trava, o watch, as revisões e os alertas."""
    agora, ligados = dados["agora"], dados.get("ligados") or set()
    contagem = {}
    for x in dados.get("sessoes") or ():
        contagem[x["estado"]] = contagem.get(x["estado"], 0) + 1
    out = [[("sessões ", "titulo")] + ([(f" {estado} {contagem[estado]} ", ESTILO_DO_ESTADO[estado])
                                         for estado in ESTILO_DO_ESTADO if estado in contagem] or [(" nenhuma", "fraco")])]
    out.append([])
    out.append([("alertas", "titulo")])
    out += [[(f"  {glifos['ponto']} {texto}", estilo)] for texto, estilo in alertas(dados)] or [[("  nenhum", "fraco")]]
    if "lock" in ligados:
        trava = dados.get("trava") or {}
        dono = trava.get("dono")
        out.append([])
        out.append([("trava ", "titulo"), (limpo(trava.get("texto") or "livre", 120),
                                            "atencao" if dono and trava.get("vivo") is False else None)])
    if "watch" in ligados:
        vigia = dados.get("vigia") or {}
        ultimo = vigia.get("ultimo_tick")
        out.append([])
        out.append([("watch ", "titulo"), (f"último tick há {_tempo(agora - ultimo)}" if ultimo else "nenhum tick ainda", "fraco")])
        for a in sorted(dados.get("avisos") or (), key=lambda a: a.get("ts", 0), reverse=True)[:10]:
            estilo = ("erro" if a.get("ci") == "FAILURE" else "ok" if a.get("ci") == "SUCCESS" else None) if a.get("tipo") == "ci" else None
            out.append([(f"  {_hora(a.get('ts'))} ", "fraco"), (f"{limpo(a.get('ref'), 30)} ", None),
                        (limpo(a.get("texto"), 60), estilo)])
    if "reviewer" in ligados:
        out.append([])
        out.append([("revisões das últimas 24 h", "titulo")])
        recentes = sorted(((id_, r) for id_, revs in (dados.get("revisoes") or {}).items() for r in revs
                           if agora - r.get("mtime", 0) < DIA), key=lambda ir: ir[1].get("mtime", 0), reverse=True)
        for id_, r in recentes[:10]:
            out.append([(f"  {limpo(id_, 30)} r{r['rodada']}  ", None), (r["veredito"], ESTILO_DO_VEREDITO.get(r["veredito"], "atencao"))])
        if not recentes:
            out.append([("  nenhuma", "fraco")])
    return [ajustar(l, largura) for l in _janela(out, max(1, altura), tela.get("rolagem") or 0)]


VISTAS = {"session": vista_sessao, "task": vista_tarefa, "global": vista_global, "list": vista_sessoes}
