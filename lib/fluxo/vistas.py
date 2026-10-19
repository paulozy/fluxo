"""As vistas do `fluxo panel`: funções puras, dos dados para as linhas da tela.

Cada linha é uma lista de segmentos (texto, estilo). A tela traduz o estilo em cor, e o --text o ignora.
Nada aqui lê disco: quem lê é o `panel.Coletor`. O texto de terceiro (prompt, título de PR, linha de
revisão, erro do watch) passa por `limpo` antes de ir para a linha, com ou sem cor.

Estilos: titulo, ok, atencao, erro, fraco, borda, sel, aba, aba_ativa, a faixa do cabeçalho (cab, cab_forte,
cab_fraco, cab_alerta, cab_erro) e a família do modelo (m_opus, m_sonnet, m_haiku, m_gpt); None é o normal.
"""
import re
import time
import unicodedata

from . import fonte

ABAS = (("session", "sessão"), ("task", "tarefa"), ("global", "global"), ("list", "sessões"))
ESTILO_DO_ESTADO = {"pergunta": "erro", "sua vez": "atencao", "trabalhando": "ok", "parada": "fraco",
                    "acabou": "fraco"}
ESTILO_DO_VEREDITO = {"APROVÁVEL": "ok", "REPROVAR": "erro"}
# só glifos de largura 1 (East Asian Width N ou A, que é 1 nos terminais ocidentais): emoji tem largura incerta no
# wcwidth do Mac e desalinha as bordas
GLIFOS_UTF8 = {"cheio": "█", "vazio": "░", "ponto": "•", "seta": "→", "sel": "▸", "borda": "╭╮╰╯─│", "giro": "◐◓◑◒",
               "estado": {"pergunta": "?", "sua vez": "●", "trabalhando": "◐", "parada": "○", "acabou": "✓"}}
GLIFOS_ASCII = {"cheio": "#", "vazio": ".", "ponto": "*", "seta": ">", "sel": ">", "borda": "++++-|", "giro": "-\\|/",
                "estado": {"pergunta": "?", "sua vez": "*", "trabalhando": "~", "parada": "o", "acabou": "x"}}
MODELOS = (("opus", "m_opus"), ("sonnet", "m_sonnet"), ("haiku", "m_haiku"), ("gpt", "m_gpt"), ("codex", "m_gpt"))
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


def _esperando(dados):
    return sum(1 for x in dados.get("sessoes") or () if x["estado"] in ("pergunta", "sua vez"))


def _nos_extremos(esq, dir_, largura, estilo):
    """`esq` encostado à esquerda e `dir_` à direita, com `estilo` no meio; sem espaço, a direita sai."""
    usado = sum(colunas(t) for t, _ in esq)
    resto = largura - usado - sum(colunas(t) for t, _ in dir_)
    if resto < 1:
        dir_, resto = [], largura - usado
    return ajustar(esq + [(" " * max(0, resto), estilo)] + dir_, largura)


def cabecalho(tela, dados, largura, glifos=GLIFOS_UTF8):
    """As 2 linhas de cima de toda aba: a faixa (quantas sessões, quem espera você e a cota do mês) e as abas."""
    n, esperando, p = len(dados.get("sessoes") or ()), _esperando(dados), glifos["ponto"]
    esq = [(" fluxo ", "cab_forte"), (f"{p} {n} {'sessão' if n == 1 else 'sessões'} ", "cab")]
    if esperando:
        esq.append((f"{p} {esperando} esperando você ", "cab_alerta"))
    faixa = _nos_extremos(esq, _cota_na_faixa(dados.get("cota"), dados["agora"], glifos), largura, "cab")
    abas = []
    for i, (chave, rotulo) in enumerate(ABAS, 1):
        abas += [(f" {i} {rotulo} ", "aba_ativa" if tela.get("aba") == chave else "aba"), (" ", None)]
    return [faixa, ajustar(abas, largura)]


def _cota_na_faixa(cota, agora, glifos):
    if not cota:
        return [(time.strftime("%H:%M ", time.localtime(agora)), "cab_fraco")]
    if cota.get("ilimitado"):
        return [("cota ilimitada ", "cab_fraco")]
    pct = cota["pct"]
    estilo = "cab_erro" if pct >= 80 else "cab_alerta" if pct >= 50 else "cab"
    cheio = max(0, min(10, round(pct / 10)))
    return [("cota ", "cab_fraco"), (glifos["cheio"] * cheio, estilo), (glifos["vazio"] * (10 - cheio), "cab_fraco"),
            (f" {pct:.0f}% ", estilo)] + ([(f"renova {_dia(cota['renova'])} ", "cab_fraco")] if cota.get("renova") else [])


def _dia(ts):
    """O dia da renovação da cota em UTC: ela vira à meia-noite UTC do dia 1º, que no Brasil ainda é o dia 31."""
    return time.strftime("%d/%m", time.gmtime(ts)) if ts else "--/--"


def glifos_para(codificacao):
    """Fora de UTF-8, o curses não desenha █ nem •: os glifos caem em ASCII."""
    return GLIFOS_UTF8 if "utf" in (codificacao or "").lower().replace("-", "") else GLIFOS_ASCII


TROCAS_ASCII = {"—": "-", "–": "-", "…": ".", "•": "*", "·": "."}


def em_ascii(texto):
    """Fora de UTF-8, o curses não desenha acento nem travessão: "sessões" vira "sessoes", e o que sobra fora do
    ASCII vira "?". Caractere a caractere, cada um nas mesmas colunas de antes: a tela avança o x pela largura
    original, e um "…" que virasse "..." escreveria por cima do segmento seguinte."""
    out = []
    for c in texto:
        n = _largura(c)
        if c.isascii() or not n:
            out.append(c if n else "")
            continue
        base = "".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x))
        troca = TROCAS_ASCII.get(c) or (base if base.isascii() and len(base) == 1 else "?")
        out.append(troca[:n].ljust(n))
    return "".join(out)


def rodape(tela, glifos=GLIFOS_UTF8):
    comum = "1-4 abas · ↑↓ escolher · Enter ir · q sair"
    extra = {"session": " · f fixar · Tab próxima · PgUp/PgDn rolar",
             "task": " · ←→ outra tarefa", "list": " · f seguir · h esconder"}.get(tela.get("aba"), "")
    texto = comum + extra
    if glifos is GLIFOS_ASCII:
        texto = em_ascii(texto.replace(" · ", " | ").replace("↑↓", "j/k").replace("←→", "<-/->"))
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


# ── as peças das vistas: a caixa, a barra e os números ──────────────────────

def _cols(linha):
    return sum(colunas(t) for t, _ in linha)


def caixa(titulo, corpo, largura, glifos=GLIFOS_UTF8, direita=(), borda="borda"):
    """O corpo numa moldura, com o título na borda de cima e `direita` (segmentos) no canto dela. Em menos de 8
    colunas a moldura não cabe: vão o título e o corpo."""
    if largura < 8:
        return [ajustar(l, largura) for l in [[(limpo(titulo, 80), "titulo")]] + list(corpo)]
    ce, cd, be, bd, h, v = glifos["borda"]
    dentro = largura - 4
    nome = cortar(f" {limpo(titulo, 80)} ", largura - 4)
    canto = ajustar([(" ", None)] + list(direita) + [(" ", None)], largura - 5 - colunas(nome)) if direita else []
    out = [[(ce + h, borda), (nome, "titulo"), (h * (largura - 4 - colunas(nome) - _cols(canto)), borda)]
           + canto + [(h + cd, borda)]]
    for linha in corpo:
        linha = ajustar(linha, dentro)
        out.append([(v + " ", borda)] + linha + [(" " * (dentro - _cols(linha)) + " " + v, borda)])
    out.append([(be + h * (largura - 2) + bd, borda)])
    return out


def barra(valor, total, n, glifos=GLIFOS_UTF8, estilo="ok"):
    """`n` colunas, cheias na proporção valor/total; qualquer valor acima de zero pinta pelo menos uma."""
    n = max(0, n)
    cheio = 0 if not total or not valor or valor <= 0 else max(1, min(n, round(n * valor / total)))
    return [(glifos["cheio"] * cheio, estilo), (glifos["vazio"] * (n - cheio), "fraco")]


def fmt_tokens(n):
    """1234 → 1,2k; 1_200_000 → 1,2M; sem dado → —."""
    if n is None:
        return "—"
    for limite, sufixo in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if n >= limite:
            v = n / limite
            return (f"{v:.1f}" if v < 9.95 else f"{v:.0f}").replace(".", ",") + sufixo
    return str(int(n))


def fmt_numero(v):
    """Créditos e premium requests: 0,12 · 1,23 · 12,3 · 123."""
    if v is None:
        return "—"
    casas = 2 if v < 9.995 else 1 if v < 99.95 else 0
    return f"{v:.{casas}f}".replace(".", ",")


def gasto(uso):
    """O que a sessão gastou: créditos (a cobrança por token) ou premium requests (a antiga); — sem dado."""
    uso = uso or {}
    if uso.get("creditos") is not None and (uso["creditos"] > 0 or not uso.get("premium")):
        return f"{fmt_numero(uso['creditos'])} cr"
    if uso.get("premium") is not None:
        return f"{fmt_numero(uso['premium'])} req"
    return "—"


def estilo_do_modelo(modelo):
    """A cor da família do modelo, como no agent-tree: opus, sonnet, haiku e gpt."""
    m = (modelo or "").lower()
    return next((estilo for chave, estilo in MODELOS if chave in m), None)


def icone(estado, glifos=GLIFOS_UTF8, agora=0):
    """Trabalhando gira a cada segundo; os outros estados têm um glifo fixo."""
    giro = glifos["giro"]
    return giro[int(agora) % len(giro)] if estado == "trabalhando" else glifos["estado"].get(estado, "?")


def _pct(v):
    return "—" if v is None else f"{v:.0f}%"


def _ha(segundos):
    s = int(max(0, segundos))
    return f"{s} s" if s < 60 else _tempo(s)


# ── as 4 abas ───────────────────────────────────────────────────────────────

def vista_sessoes(dados, tela, largura, altura, glifos=GLIFOS_UTF8):
    """Aba 4: uma linha por sessão viva, com quem espera você em cima (a ordem do panel.ORDEM). A ferramenta e o
    pane só entram com folga: em 100 colunas, a última ação fica com o espaço."""
    lista = visiveis(dados, tela)
    if not lista:
        return [[("nenhuma sessão com eventos hoje (os hooks estão instalados? fluxo panel enable)", "fraco")]]
    dentro, agora = max(0, largura - 4), dados["agora"]
    largo = dentro >= 110
    cab = ("  " + preencher("estado", 15) + preencher("sessão", 18) + (preencher("ferramenta", 9) if largo else "")
           + preencher("modelo", 18) + f"{'ctx':>4} {'gasto':>9} {'há':>7}  " + (preencher("pane", 6) if largo else "")
           + "última ação")
    linhas, escolhida = [], None
    for n, x in enumerate(lista):
        sel, uso, e = x["sessao"] == tela.get("escolhida"), x.get("uso") or {}, x["estado"]
        escolhida = n if sel else escolhida
        modelo = limpo(uso.get("modelo"), 40)
        segs = [(glifos["sel"] + " " if sel else "  ", "titulo"),
                (f"{icone(e, glifos, agora)} {preencher(e, 13)}", ESTILO_DO_ESTADO[e]),
                (preencher(limpo(x["nome"], 40), 17) + " ", None)]
        if largo:
            segs.append((preencher(x.get("ferramenta") or "", 8) + " ", "fraco"))
        segs += [(preencher(modelo or "—", 17) + " ", estilo_do_modelo(modelo) or "fraco"),
                 (f"{_pct(uso.get('ctx_pct')):>4} {gasto(uso):>9} {_tempo(x['ha']):>7}  ", None)]
        if largo:
            segs.append((preencher((x.get("pane") or "")[:5], 6), "fraco"))
        segs.append((limpo(f"{x.get('tool', '')} {x.get('alvo', '')}", 80), None))
        if sel:  # a linha inteira em destaque, até a borda
            segs = [(t, "sel") for t, _ in ajustar(segs, dentro)]
            segs.append((" " * (dentro - _cols(segs)), "sel"))
        linhas.append(segs)
    cabem = max(1, altura - 3)  # a borda de cima, o cabeçalho das colunas e a borda de baixo
    inicio = 0 if escolhida is None or escolhida < cabem else escolhida - cabem + 1
    esperando = _esperando(dados)
    canto = [(f"{esperando} esperando você", "atencao")] if esperando else [(str(len(lista)), "fraco")]
    return caixa("sessões", [[(cab, "fraco")]] + _janela(linhas, cabem, inicio), largura, glifos, canto,
                 "atencao" if esperando else "borda")[:max(1, altura)]


def _linhas_de_uso(x, glifos):
    uso, p = x.get("uso"), glifos["ponto"]
    if not uso:
        dica = ("fluxo panel enable liga a statusline do Copilot" if x.get("ferramenta") == "copilot"
                else f"o {x.get('ferramenta') or 'agente'} não manda o uso para o painel")
        return [[("sem dado de uso: ", "fraco"), (dica, "fraco")]]
    modelo = limpo(uso.get("modelo"), 60)
    out = [[("modelo   ", "fraco"), (modelo or "—", estilo_do_modelo(modelo))]
           + ([(f"  {p} effort ", "fraco"), (limpo(uso["effort"], 20), "titulo")] if uso.get("effort") else [])]
    pct = uso.get("ctx_pct")
    if pct is not None:
        estilo = "erro" if pct >= 80 else "atencao" if pct >= 50 else "ok"
        de = f" de {fmt_tokens(uso['ctx_max'])}" if uso.get("ctx_max") else ""
        out.append([("contexto ", "fraco")] + barra(pct, 100, 20, glifos, estilo) + [(f" {pct:.0f}%{de}", None)])
    out.append([("tokens   ", "fraco"), (f"entrada {fmt_tokens(uso.get('entrada'))}  {p}  saída {fmt_tokens(uso.get('saida'))}"
                                         f"  {p}  cache {fmt_tokens(uso.get('cache'))}", None)])
    partes = ([f"{fmt_numero(uso['creditos'])} créditos"] if uso.get("creditos") is not None else []) + \
             ([f"{fmt_numero(uso['premium'])} premium requests"] if uso.get("premium") else [])
    out.append([("gasto    ", "fraco"), (f"  {p}  ".join(partes) or "—", "titulo")])
    if uso.get("fonte") != "statusline" and x.get("ferramenta") == "copilot":  # o modelo veio do events.jsonl
        out.append([("o gasto e o contexto ao vivo vêm da statusline: fluxo panel enable", "fraco")])
    return out


def vista_sessao(dados, tela, largura, altura, glifos=GLIFOS_UTF8):
    """Aba 1: a sessão seguida (pasta, tarefa e prompt), o modelo e o uso, as ferramentas, os subagentes e o log.
    Sem altura para tudo, o log cede primeiro. As ferramentas só caem para as 3 mais usadas se isso der ao log 3
    linhas; e, se nem as caixas couberem, elas descem até 1 antes de os subagentes cederem."""
    x = next((s for s in dados.get("sessoes") or () if s["sessao"] == tela.get("seguida")), None)
    if x is None and tela.get("fixada") and tela.get("seguida"):
        return [[(f"a sessão fixada ({limpo(tela['seguida'], 40)}) não está entre as de hoje: Tab passa para outra", "atencao")]]
    if x is None:
        return [[("nenhuma sessão para seguir (abra uma com fluxo task, ou espere um evento)", "fraco")]]
    agora, e, p = dados["agora"], x["estado"], glifos["ponto"]
    tarefa = (x.get("tarefa") or {}).get("id")
    topo = [[("pasta  ", "fraco"), (limpo(x.get("cwd"), 100) or "-", None)]
            + ([("   tarefa ", "fraco"), (limpo(tarefa, 40), "titulo")] if tarefa else []),
            [("prompt ", "fraco"), (limpo(x.get("ultimo_prompt"), 160) or "-", None)]]
    canto = [(f"{icone(e, glifos, agora)} {e}", ESTILO_DO_ESTADO[e]),
             (f"  {p} há {_tempo(x['ha'])}  {p} pane {x.get('pane') or '-'}", "fraco")]
    out = caixa(f"{limpo(x['nome'], 30)} {p} {x.get('ferramenta') or ''}", topo, largura, glifos, canto)
    uso = x.get("uso")
    idade = [(f"{limpo(uso.get('fonte'), 20)} {p} há {_ha(agora - uso['ts'])}", "fraco")] if uso and uso.get("ts") else []
    out += caixa("modelo e uso", _linhas_de_uso(x, glifos), largura, glifos, idade)

    contagem = sorted((x.get("contagem") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    subs = sorted((x.get("subagentes") or {}).values(), key=lambda s: s["inicio"])
    resto = altura - len(out)
    nf, ns = min(8, max(1, len(contagem))), max(1, len(subs))
    if resto - (nf + ns + 4) - 2 < 3 <= resto - (min(nf, 3) + ns + 4) - 2:  # o top 3 dá 3 linhas ao log
        nf = min(nf, 3)
    while nf + ns + 4 > resto and nf > 1:
        nf -= 1
    while nf + ns + 4 > resto and ns > 1:
        ns -= 1

    maior, larg_barra = max((n for _, n in contagem), default=1), max(5, min(20, largura - 34))
    ferr = [[(preencher(limpo(nome, 40), 18) + " ", None)] + barra(n, maior, larg_barra, glifos) + [(f" {n}", "fraco")]
            for nome, n in contagem[:nf]] or [[("nenhuma ainda", "fraco")]]
    total = sum(n for _, n in contagem)
    canto_ferr = [(f"{total} {'chamada' if total == 1 else 'chamadas'}", "fraco")] if contagem else []
    out += caixa("ferramentas", ferr, largura, glifos, canto_ferr)
    linhas_sub = []
    for s in subs[-ns:]:  # cortando, ficam os mais novos
        dentro_ = ", ".join(f"{limpo(t, 16)} {n}" for t, n in sorted((s.get("contagem") or {}).items(), key=lambda kv: -kv[1]))
        vivo = s["fim"] is None
        linhas_sub.append([(f"{icone('trabalhando' if vivo else 'acabou', glifos, agora)} ", "ok" if vivo else "fraco"),
                           (preencher(limpo(s["rotulo"], 40), 24) + " ", None),
                           ("rodando" if vivo else "terminou", "ok" if vivo else "fraco"),
                           (f"  {dentro_}" if dentro_ else "", "fraco")])
    rodando = sum(1 for s in subs if s["fim"] is None)
    out += caixa("subagentes", linhas_sub or [[("nenhum", "fraco")]], largura, glifos,
                 [(f"{rodando} rodando", "ok")] if rodando else [])

    log = []
    for ev in reversed(x.get("log") or ()):
        quem = f"{limpo(ev.get('agente_tipo') or ev.get('agente'), 16)}: " if ev.get("agente") else ""
        texto_ = limpo(f"{quem}{ev.get('tool') or ''} {ev.get('alvo') or ev.get('prompt') or ''}", 120)
        log.append([(time.strftime("%H:%M:%S ", time.localtime(ev["ts"])), "fraco"),
                    (f"{ev.get('evento') or '':<16} ", "erro" if ev.get("falhou") else "fraco"), (texto_, None)])
    cabe_log = altura - len(out) - 2
    if cabe_log >= 1:
        out += caixa("log", _janela(log, cabe_log, tela.get("rolagem") or 0) or [[("vazio", "fraco")]], largura, glifos)
    return out[:max(1, altura)]


def vista_tarefa(dados, tela, largura, altura, glifos=GLIFOS_UTF8):
    """Aba 2: o plano da tarefa, o checklist do fluxo, as sessões, as revisões e as PRs/MRs."""
    id_ = tarefa_atual(dados, tela, tela.get("seguida"))
    if not id_:
        return [[("nenhuma tarefa com plano ainda (o planejar grava em planos/<id>.md)", "fraco")]]
    ordem, agora = ordem_das_tarefas(dados), dados["agora"]
    posicao = [(f"{ordem.index(id_) + 1}/{len(ordem)}", "fraco")] if id_ in ordem else []
    plano = (dados.get("planos") or {}).get(id_)
    if not plano:
        corpo = [[(f"sem plano em planos/{limpo(id_, 40)}.md", "fraco")]]
    else:
        status = plano["status"]
        corpo = [[("status ", "fraco"), (status, "ok" if status in ("aprovado", "concluido") else "atencao"),
                  ("   base ", "fraco"), (plano["base"] or "-", None), ("   criado ", "fraco"), (plano["criado"] or "-", None)],
                 [("repos  ", "fraco"), (plano["repos"] or "-", None)]]
    out = caixa(f"tarefa {limpo(id_, 40)}", corpo, largura, glifos, posicao)
    if plano and plano["checklist"] is None:
        out += caixa("checklist", [[("sem checklist no plano", "fraco")]], largura, glifos)
    elif plano:
        checklist = plano["checklist"]
        feitos, total = sum(1 for f, _ in checklist if f), len(checklist)
        itens = [[(f"{glifos['estado']['acabou' if feito else 'parada']} ", "ok" if feito else "fraco"),
                  (texto_, None if feito else "fraco")] for feito, texto_ in checklist]
        out += caixa("checklist", itens or [[("vazio", "fraco")]], largura, glifos,
                     barra(feitos, total, 10, glifos) + [(f" {feitos}/{total}", "fraco")])
    vivas = [x for x in dados.get("sessoes") or () if (x.get("tarefa") or {}).get("id") == id_]
    out += caixa("sessões", [[(f"{icone(x['estado'], glifos, agora)} {preencher(x['estado'], 12)}", ESTILO_DO_ESTADO[x["estado"]]),
                              (preencher(limpo(x["nome"], 40), 18) + " ", None), (gasto(x.get("uso")), "fraco")]
                             for x in vivas] or [[("nenhuma viva", "fraco")]], largura, glifos)
    ligados = dados.get("ligados") or set()
    if "reviewer" in ligados:
        revs = sorted((dados.get("revisoes") or {}).get(id_, ()), key=lambda r: r["rodada"])
        out += caixa("revisões", [[(f"r{r['rodada']}  {r['quando']}  ", "fraco"),
                                   (r["veredito"], ESTILO_DO_VEREDITO.get(r["veredito"], "atencao"))]
                                  for r in revs] or [[("nenhuma rodada", "fraco")]], largura, glifos)
    if "watch" in ligados:
        por_ref = {}
        for a in dados.get("avisos") or ():  # o último aviso de cada PR/MR, e o último de CI
            if cita(id_, a.get("titulo")):
                por_ref.setdefault(a.get("ref"), {})["ci" if a.get("tipo") == "ci" else "outro"] = a
        prs = []
        for ref, avisos in sorted(por_ref.items(), key=lambda kv: str(kv[0])):
            linha = [(preencher(limpo(ref, 40), 22) + " ", None)]
            if "outro" in avisos:
                linha.append((limpo(avisos["outro"].get("texto"), 50) + "  ", "fraco"))
            if "ci" in avisos:
                linha.append((limpo(avisos["ci"].get("texto"), 20), "erro" if avisos["ci"].get("ci") == "FAILURE" else "ok"))
            prs.append(linha)
        out += caixa("PRs/MRs", prs or [[("nenhum aviso que cite a tarefa no título", "fraco")]], largura, glifos)
    return _janela(out, max(1, altura), tela.get("rolagem") or 0)


def _caixa_da_cota(dados, largura, glifos):
    """A cota do mês (o cache do Copilot) e o que as sessões do painel gastaram."""
    cota, agora, p = dados.get("cota"), dados["agora"], glifos["ponto"]
    if not cota:
        corpo = [[("sem dado: o cache do Copilot (copilot-user-cache.json) não foi achado", "fraco")]]
    elif cota.get("ilimitado"):
        corpo = [[("ilimitada", "ok"), (f"  {p} plano {limpo(cota.get('plano'), 20) or '?'}", "fraco")]]
    else:
        pct = cota["pct"]
        estilo = "erro" if pct >= 80 else "atencao" if pct >= 50 else "ok"
        corpo = [barra(pct, 100, 30, glifos, estilo) + [(f" {pct:.0f}% usado", estilo)],
                 [(f"{fmt_numero(cota['usado'])} de {fmt_numero(cota['total'])} {cota['unidade']}", None)]
                 + ([(f"  {p} excedente {fmt_numero(cota['excedente'])}", "erro")] if cota.get("excedente") else []),
                 [(f"plano {limpo(cota.get('plano'), 20) or '?'}  {p} renova {_dia(cota.get('renova'))}", "fraco")]
                 + ([(f"  {p} dado de {_ha(agora - cota['ts'])} atrás", "fraco")] if cota.get("ts") else [])]
    com_uso = [x["uso"] for x in dados.get("sessoes") or () if x.get("uso")]
    if com_uso:
        creditos = sum(u.get("creditos") or 0 for u in com_uso)
        tokens = sum((u.get("entrada") or 0) + (u.get("saida") or 0) for u in com_uso)
        corpo.append([("nas sessões do painel: ", "fraco"), (f"{fmt_numero(creditos)} créditos  {p}  {fmt_tokens(tokens)} tokens", "titulo"),
                      (f"  ({len(com_uso)} {'sessão' if len(com_uso) == 1 else 'sessões'})", "fraco")])
    return caixa("cota do mês", corpo, largura, glifos)


def vista_global(dados, tela, largura, altura, glifos=GLIFOS_UTF8):
    """Aba 3: as sessões por estado, a cota do mês, os alertas, a trava, o watch e as revisões."""
    agora, ligados = dados["agora"], dados.get("ligados") or set()
    contagem = {}
    for x in dados.get("sessoes") or ():
        contagem[x["estado"]] = contagem.get(x["estado"], 0) + 1
    por_estado = [(f"{icone(e, glifos, agora)} {e} {contagem[e]}    ", ESTILO_DO_ESTADO[e]) for e in ESTILO_DO_ESTADO if e in contagem]
    out = caixa("sessões", [por_estado or [("nenhuma", "fraco")]], largura, glifos)
    if "cota" in dados:  # só com o Copilot: o Coletor só põe a chave quando ele está em tools
        out += _caixa_da_cota(dados, largura, glifos)
    lista = alertas(dados)
    out += caixa("alertas", [[(f"{glifos['ponto']} {texto_}", estilo)] for texto_, estilo in lista] or [[("nenhum", "fraco")]],
                 largura, glifos, [(str(len(lista)), "erro")] if lista else [],
                 "erro" if any(estilo == "erro" for _, estilo in lista) else "borda")
    if "lock" in ligados:
        trava = dados.get("trava") or {}
        out += caixa("trava", [[(limpo(trava.get("texto") or "livre", 120),
                                 "atencao" if trava.get("dono") and trava.get("vivo") is False else None)]], largura, glifos)
    if "watch" in ligados:
        ultimo = (dados.get("vigia") or {}).get("ultimo_tick")
        feed = []
        for a in sorted(dados.get("avisos") or (), key=lambda a: a.get("ts", 0), reverse=True)[:10]:
            estilo = ("erro" if a.get("ci") == "FAILURE" else "ok" if a.get("ci") == "SUCCESS" else None) if a.get("tipo") == "ci" else None
            feed.append([(f"{_hora(a.get('ts'))} ", "fraco"), (preencher(limpo(a.get("ref"), 30), 22) + " ", None),
                         (limpo(a.get("texto"), 60), estilo)])
        out += caixa("watch", feed or [[("nenhum aviso nas últimas 24 h", "fraco")]], largura, glifos,
                     [(f"último tick há {_tempo(agora - ultimo)}" if ultimo else "nenhum tick ainda", "fraco")])
    if "reviewer" in ligados:
        recentes = sorted(((id_, r) for id_, revs in (dados.get("revisoes") or {}).items() for r in revs
                           if agora - r.get("mtime", 0) < DIA), key=lambda ir: ir[1].get("mtime", 0), reverse=True)
        out += caixa("revisões das últimas 24 h", [[(preencher(limpo(id_, 30), 18) + f" r{r['rodada']}  ", None),
                                                    (r["veredito"], ESTILO_DO_VEREDITO.get(r["veredito"], "atencao"))]
                                                   for id_, r in recentes[:10]] or [[("nenhuma", "fraco")]], largura, glifos)
    return _janela(out, max(1, altura), tela.get("rolagem") or 0)


VISTAS = {"session": vista_sessao, "task": vista_tarefa, "global": vista_global, "list": vista_sessoes}
