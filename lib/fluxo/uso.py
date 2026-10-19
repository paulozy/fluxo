"""O uso das sessões do Copilot CLI para o painel: tokens, contexto, gasto, modelo e effort, e a cota do mês.

Fontes (Copilot CLI 1.0.98; o que não tem doc oficial está no docs/validacao.md):
- statusline: o `copilot help config` documenta o `statusLine.command`, que recebe o estado da sessão em JSON no
  stdin a cada evento. Os campos (session_id, model, context_window, cost, ai_used) não têm doc: vêm do bundle
  (cli-main.js). O `copilot config` recusa gravar o `statusLine.command` ("Edit settings.json directly"), e o
  arquivo é o settings.json do COPILOT_HOME (`copilot config --help`).
- modelo e effort: `selectedModel`/`newModel` e `reasoningEffort` dos eventos session.start, session.resume e
  session.model_change, no events.jsonl da sessão (o schema session-events.schema.json do pacote do CLI). O
  nome da pasta é o sessionId do hook, e a statusline manda o caminho em `transcript_path`.
- cota: o cache copilot-user-cache.json do CLI, que guarda a resposta do /copilot_internal/user (sem doc).
- 1 crédito = 1e9 nanoAiu (bundle); com token_based_billing, a cota é em créditos (1 crédito = US$ 0,01).
"""
import json
import os
import re
import stat
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import config as cfgmod

ID = re.compile(r"[A-Za-z0-9-]{1,120}")
NANO = 1e9
STATUSLINE = {"type": "command", "command": '"$HOME/.local/bin/fluxo" statusline'}
DO_FLUXO = re.compile(r'^\s*"?[^\s;&|"]*fluxo"?\s+statusline\s*$')  # o comando inteiro: `x; fluxo statusline` é seu
TIPOS = (b'"session.start"', b'"session.resume"', b'"session.model_change"')
BLOCO = 4 << 20  # o events.jsonl é lido em blocos: um de 60 MB inteiro na memória custava 117 MB
LIMITE = 1_000_000  # o stdin da statusline: o JSON tem uns poucos KB


def _numero(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def _texto(v, n=200):
    return v[:n] if isinstance(v, str) and v else None


def _objeto(v):
    return v if isinstance(v, dict) else {}


# ── a statusline: o `fluxo statusline` grava o último estado de cada sessão ──

def normalizar(payload, agora):
    """O que o painel guarda do JSON da statusline, ou None sem um id de sessão válido (antes de a sessão existir
    ele vem nulo). Só número e texto: o resto do payload é descartado."""
    p = _objeto(payload)
    sid = p.get("session_id")
    if not isinstance(sid, str) or not ID.fullmatch(sid):
        return None
    modelo, ctx = _objeto(p.get("model")), _objeto(p.get("context_window"))
    nome = _texto(modelo.get("id"), 80) or _texto(modelo.get("display_name"), 80)
    if nome == "auto" and _texto(modelo.get("auto_tier"), 40):
        nome = f"auto ({modelo['auto_tier'][:40]})"
    pct = _numero(ctx.get("current_context_used_percentage"))  # o que bate com o /context
    return {"ts": agora, "sessao": sid, "transcript": _texto(p.get("transcript_path"), 1000), "modelo": nome,
            "ctx_pct": pct if pct is not None else _numero(ctx.get("used_percentage")),
            "ctx_max": _numero(ctx.get("displayed_context_limit")) or _numero(ctx.get("context_window_size")),
            "entrada": _numero(ctx.get("total_input_tokens")), "saida": _numero(ctx.get("total_output_tokens")),
            "cache": _numero(ctx.get("total_cache_read_tokens")),
            "nano_aiu": _numero(_objeto(p.get("ai_used")).get("total_nano_aiu")),
            "premium": _numero(_objeto(p.get("cost")).get("total_premium_requests"))}


def gravar(estado, registro):
    """estado/uso/<sessão>.json, trocado de uma vez: o painel nunca lê um arquivo pela metade."""
    cfgmod.criar_estado(estado)
    pasta = Path(estado) / "uso"
    pasta.mkdir(mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{registro['sessao']}.", suffix=".tmp", dir=str(pasta))  # 600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(registro))
        os.replace(tmp, pasta / f"{registro['sessao']}.json")
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def statusline(argv, stdin=None, env=None, agora=None):
    """O `fluxo statusline`, que o Copilot roda a cada evento. Não imprime nada (o rodapé do Copilot já mostra o
    modelo e a cota), nunca levanta e sempre sai com 0: um erro dali vai para o hook-erros.log, que o painel mostra."""
    estado = None
    try:
        home = argv[argv.index("--home") + 1] if "--home" in argv else None
        estado = cfgmod.Caminhos(home=home, env=env).estado
        registro = normalizar(json.loads((stdin or sys.stdin).read(LIMITE) or "null"), agora or time.time())
        if registro:
            gravar(estado, registro)
    except BaseException as exc:  # noqa: B036 — o Ctrl-C também. O SIGTERM do Copilot (10 s) mata sem passar aqui,
        # e o .tmp que sobrar a poda tira em 1 h
        try:
            if estado is not None:
                cfgmod.criar_estado(estado)
                with open(Path(estado) / "hook-erros.log", "a", encoding="utf-8") as fh:
                    fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} copilot statusline: {exc!r}\n")
        except BaseException:  # noqa: B036
            pass
    return 0


def ler_uso(registro, sessao=None):
    """O dict `uso` que as vistas mostram, do registro da statusline e/ou do que o events.jsonl disse (modelo e
    effort). None sem nenhum dos dois."""
    sessao = sessao or {}
    if not registro and not (sessao.get("modelo") or sessao.get("effort")):
        return None
    r = registro or {}
    nano = r.get("nano_aiu")
    return {"modelo": r.get("modelo") or sessao.get("modelo"), "effort": sessao.get("effort"),
            "ctx_pct": r.get("ctx_pct"), "ctx_max": r.get("ctx_max"), "entrada": r.get("entrada"),
            "saida": r.get("saida"), "cache": r.get("cache"), "creditos": None if nano is None else nano / NANO,
            "premium": r.get("premium"), "fonte": "statusline" if registro else "sessão", "ts": r.get("ts")}


# ── o events.jsonl da sessão: o modelo e o effort ───────────────────────────

class LeitorSessao:
    """O modelo e o effort de cada sessão, lidos do events.jsonl dela aos pedaços (offset + inode, como o
    LeitorEventos do panel). O arquivo pode ter dezenas de MB: só as linhas dos 3 tipos que dizem o modelo passam
    pelo json.loads."""

    def __init__(self):
        self.s = {}

    def ler(self, caminho):
        caminho = str(caminho)
        try:
            st = os.stat(caminho)
        except OSError:
            return None
        if not stat.S_ISREG(st.st_mode):  # o transcript_path é de terceiro: pode ser uma pasta
            return None
        atual = self.s.get(caminho)
        if not atual or atual["ino"] != st.st_ino or st.st_size < atual["pos"]:
            atual = self.s[caminho] = {"ino": st.st_ino, "pos": 0, "resto": b"", "modelo": None, "effort": None}
        if st.st_size > atual["pos"]:
            try:
                with open(caminho, "rb") as fh:
                    fh.seek(atual["pos"])
                    for bloco in iter(lambda: fh.read(BLOCO), b""):
                        atual["pos"] += len(bloco)
                        linhas = (atual["resto"] + bloco).split(b"\n")
                        atual["resto"] = linhas.pop()  # a linha que o Copilot ainda está escrevendo
                        for linha in linhas:
                            if any(t in linha for t in TIPOS):
                                self._evento(atual, linha)
            except OSError:  # sem permissão, por exemplo: as outras sessões seguem
                return None
        return {"modelo": atual["modelo"], "effort": atual["effort"]}

    @staticmethod
    def _evento(atual, linha):
        try:
            ev = json.loads(linha)
        except ValueError:
            return
        tipo, d = _objeto(ev).get("type"), _objeto(_objeto(ev).get("data"))
        if tipo not in ("session.start", "session.resume", "session.model_change"):
            return
        modelo = d.get("newModel") if tipo == "session.model_change" else d.get("selectedModel")
        if _texto(modelo, 80):
            atual["modelo"] = modelo[:80]
        if "reasoningEffort" in d:  # null é o padrão do modelo
            atual["effort"] = _texto(d["reasoningEffort"], 20)


# ── onde o Copilot guarda as coisas ─────────────────────────────────────────

def pastas_copilot(cam, env=None):
    """A pasta do Copilot (config, settings e sessões) e as candidatas do cache. Com --home (os testes), só dentro
    dele, sem olhar o ambiente: um teste nunca lê o ~/.copilot de verdade."""
    env = os.environ if env is None else env
    if cam.isolado:
        return {"home": cam.home / ".copilot", "cache": [cam.home / ".cache" / "copilot"]}
    casa = Path.home()
    home = Path(env["COPILOT_HOME"]).expanduser() if env.get("COPILOT_HOME") else casa / ".copilot"
    cache = [Path(env[v]).expanduser() / sub for v, sub in (("COPILOT_CACHE_HOME", ""), ("XDG_CACHE_HOME", "copilot"))
             if env.get(v)]
    return {"home": home, "cache": cache + [casa / ".cache" / "copilot", casa / "Library" / "Caches" / "copilot"]}


def eventos_da_sessao(pastas, sessao, registro=None):
    """O events.jsonl da sessão: o caminho que a statusline mandou, ou a pasta com o id dela."""
    transcript = (registro or {}).get("transcript")
    if transcript and os.path.isabs(transcript):
        return Path(transcript)
    return pastas["home"] / "session-state" / sessao / "events.jsonl"


# ── a cota do mês ───────────────────────────────────────────────────────────

def _sem_comentario(texto):
    """Os arquivos do Copilot começam com um comentário //, que o json não aceita."""
    return "\n".join(l for l in texto.splitlines() if not l.lstrip().startswith("//"))


def _epoch(v):
    """Epoch em s (ou em ms) ou ISO com Z: o fromisoformat do 3.9 não aceita o Z."""
    if _numero(v):
        return v / 1000 if v > 1e11 else float(v)
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def ler_cota(pastas):
    """A cota de premium_interactions do cache do CLI, ou None (sem cache, sem cota, ou a entrada é de outra conta).

    O cache tem uma entrada por token, e mais de uma conta: vale a do login de agora (lastLoggedInUser do
    config.json) e, entre as dele, a mais recente, juntando as pastas candidatas (uma pode ter um cache velho).
    Sem saber o login, a mais recente de todas. Um token no ambiente (COPILOT_GITHUB_TOKEN, GH_TOKEN) vale mais que
    o login guardado, e aí a cota pode ser de outra conta: o docs/validacao.md confere."""
    login = None
    try:
        login = _objeto(_objeto(json.loads(_sem_comentario((pastas["home"] / "config.json").read_text(
            encoding="utf-8")))).get("lastLoggedInUser")).get("login")
    except (OSError, ValueError):
        pass
    entradas = []
    for pasta in pastas["cache"]:
        try:
            bruto = json.loads(_sem_comentario((pasta / "copilot-user-cache.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        entradas += [e for e in _objeto(_objeto(bruto).get("copilotUserCache")).values()
                     if isinstance(e, dict) and isinstance(e.get("response"), dict)]
    if login:
        entradas = [e for e in entradas if e["response"].get("login") == login]
    if not entradas:
        return None
    e = max(entradas, key=lambda e: _epoch(e.get("retrievedAt")) or 0)
    return _cota(e["response"], _epoch(e.get("retrievedAt")))


def _cota(r, ts):
    q = _objeto(_objeto(r.get("quota_snapshots")).get("premium_interactions"))
    plano = _texto(r.get("copilot_plan"), 40)
    if q.get("unlimited") is True:
        return {"ilimitado": True, "plano": plano, "ts": ts}
    total, resta = _numero(q.get("entitlement")), _numero(q.get("remaining"))
    if not total or q.get("has_quota") is False:
        return None
    usado = total - min(total, resta or 0)
    resta_pct = _numero(q.get("percent_remaining"))
    tokens = q.get("token_based_billing") is True or r.get("token_based_billing") is True
    return {"usado": float(usado), "total": float(total),
            "pct": 100.0 - min(100.0, resta_pct) if resta_pct is not None else 100.0 * usado / total,
            "unidade": "créditos" if tokens else "premium requests",
            "renova": _epoch(q.get("quota_reset_at")) or _epoch(r.get("quota_reset_date_utc")),
            "ts": ts, "plano": plano, "excedente": _numero(q.get("overage_count")) or 0, "ilimitado": False}


# ── ligar e desligar a statusline no settings.json do Copilot ──────────────

def _ler_settings(caminho):
    """(cabeçalho //, objeto, bytes lidos); um arquivo que não existe é ("", {}, None). Levanta ValueError para
    JSON torto: aí nada é gravado."""
    try:
        lido = Path(caminho).read_bytes()
    except FileNotFoundError:
        return "", {}, None
    cabecalho, corpo = [], []
    for linha in lido.decode("utf-8").splitlines():
        (cabecalho if not corpo and linha.lstrip().startswith("//") else corpo).append(linha)
    dados = json.loads("\n".join(corpo).strip() or "{}")
    if not isinstance(dados, dict):
        raise ValueError("a raiz não é um objeto")
    return "\n".join(cabecalho), dados, lido


def do_fluxo(statusline_):
    return isinstance(statusline_, dict) and isinstance(statusline_.get("command"), str) \
        and DO_FLUXO.search(statusline_["command"]) is not None


def estado_da_statusline(pastas):
    """"fluxo", "outra", None (nenhuma) ou "ilegível": para o doctor e os avisos do install."""
    try:
        atual = _ler_settings(pastas["home"] / "settings.json")[1].get("statusLine")
    except (ValueError, UnicodeDecodeError, OSError):
        return "ilegível"
    if do_fluxo(atual):
        return "fluxo"
    return "outra" if isinstance(atual, dict) and atual.get("command") else None


def ligar_statusline(pastas, perguntas):
    """Põe a statusline do fluxo no settings.json do Copilot, com backup e só depois do "sim" no terminal. Com a
    statusline de outro, avisa e para. Devolve (código, mensagem): 1 quando ela não ficou ligada."""
    caminho = pastas["home"] / "settings.json"
    trecho = '"statusLine": ' + json.dumps(STATUSLINE)
    try:
        cabecalho, dados, lido = _ler_settings(caminho)
    except (ValueError, UnicodeDecodeError) as exc:
        return 1, f"{caminho} não abriu como JSON ({exc}): nada foi gravado"
    atual = dados.get("statusLine")
    if do_fluxo(atual):
        return 0, "a statusline do fluxo já está ligada no Copilot"
    if atual is not None and not (isinstance(atual, dict) and not atual.get("command")):
        comando = _objeto(atual).get("command") or json.dumps(atual)
        return 1, (f"o Copilot já tem uma statusline ({str(comando)[:120]}): o fluxo não mexe nela. Para ver o gasto ao "
                   f"vivo no painel, troque-a no {caminho} por {trecho}")
    if perguntas is None:
        return 1, f"a statusline do Copilot pede confirmação: rode fluxo panel enable num terminal, ou ponha no {caminho}: {trecho}"
    perguntas.escrever(f"o fluxo vai gravar no {caminho} (com backup em settings.json.fluxo-bak):\n  {trecho}\n"
                       "feche as sessões do Copilot antes: uma aberta pode regravar o arquivo e desfazer isto")
    from .assistente import Cancelado  # aqui, e não no topo: a statusline não pode pagar o import dele
    try:
        if not perguntas.sim("gravar?", False):
            return 1, "nada foi gravado"
    except (Cancelado, KeyboardInterrupt):  # o Ctrl-C ou o Ctrl-D na pergunta
        return 1, "nada foi gravado"
    novo = dict(dados, statusLine=dict(_objeto(atual), **STATUSLINE))  # um padding ou refreshInterval seu fica
    try:
        caminho.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # o ~/.copilot do Copilot é 700
        if lido is not None:
            fd = os.open(str(caminho) + ".fluxo-bak", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(lido)
        # a pergunta espera o humano: um Copilot aberto pode ter criado o arquivo nesse meio tempo
        cfgmod.gravar_json(caminho, novo, antes=cfgmod.NAO_EXISTIA if lido is None else lido, cabecalho=cabecalho)
    except (OSError, cfgmod.ErroConfig) as exc:
        return 1, f"não deu para gravar o {caminho}: {exc}"
    return 0, "statusline do fluxo ligada no Copilot: vale nas sessões novas (confira em /statusline que o item custom está marcado)"


def desligar_statusline(pastas):
    """Tira do settings.json só a statusline do fluxo: a de outro, ou nenhuma, fica como está."""
    caminho = pastas["home"] / "settings.json"
    try:
        cabecalho, dados, lido = _ler_settings(caminho)
    except (ValueError, UnicodeDecodeError) as exc:
        return 1, f"{caminho} não abriu como JSON ({exc}): nada foi mudado"
    atual = dados.get("statusLine")
    if not do_fluxo(atual):
        return 0, "a statusline do Copilot não é a do fluxo: nada a desligar nela"
    novo, resto = dict(dados), {k: v for k, v in atual.items() if k not in ("type", "command")}
    if resto:
        novo["statusLine"] = resto
    else:
        del novo["statusLine"]
    try:
        cfgmod.gravar_json(caminho, novo, antes=lido, cabecalho=cabecalho)
    except (OSError, cfgmod.ErroConfig) as exc:
        return 1, f"não deu para gravar o {caminho}: {exc}"
    return 0, "statusline do fluxo desligada no Copilot"
