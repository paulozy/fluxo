"""Painel: os hooks das ferramentas gravam eventos, e o `fluxo panel` mostra as sessões ao vivo.

O hook (`fluxo hook --tool F --event E`) roda em todo evento de toda sessão:
- nunca imprime nada (no Kiro, o stdout do SessionStart entra no contexto do agente);
- sempre sai com 0 (no Copilot, um preToolUse que falha nega a ferramenta; por isso ele nem é usado);
- grava uma linha com um único os.write num fd O_APPEND, para hooks concorrentes não intercalarem;
- erro vai para estado/hook-erros.log.

Eventos normalizados: inicio, turno (o usuário mandou prompt), ferramenta, espera (pergunta ou pedido
de permissão na tela: só o Copilot avisa), subagente_inicio, subagente_fim, fim_turno, fim.

Fontes dos eventos e dos campos: https://docs.github.com/en/copilot/reference/hooks-reference,
https://kiro.dev/docs/hooks/types/ e /docs/hooks/actions/, https://learn.chatgpt.com/docs/hooks.
"""
import collections
import json
import os
import re
import sys
import time
from pathlib import Path

from . import config as cfgmod
from . import lock, task, tmux, vistas, watch

EVENTOS = {
    # sem permissionRequest: ele dispara antes das regras que liberam sozinhas, e não quer dizer que o
    # usuário foi perguntado; isso é a notification com permission_prompt (NOTIFICACAO)
    "copilot": {"sessionStart": "inicio", "userPromptSubmitted": "turno", "postToolUse": "ferramenta",
                "postToolUseFailure": "ferramenta", "notification": "notificacao",
                "subagentStart": "subagente_inicio", "subagentStop": "subagente_fim", "agentStop": "fim_turno",
                "sessionEnd": "fim"},
    # V3 em PascalCase; V2 (embutido no agente) em camelCase
    "kiro": {"SessionStart": "inicio", "agentSpawn": "inicio", "UserPromptSubmit": "turno",
             "userPromptSubmit": "turno", "PostToolUse": "ferramenta", "postToolUse": "ferramenta",
             "Stop": "fim_turno", "stop": "fim_turno", "SessionEnd": "fim"},
    # sem PermissionRequest: a doc não diz se um hook que falha ou estoura o tempo ali nega a ação
    "codex": {"SessionStart": "inicio", "UserPromptSubmit": "turno", "PostToolUse": "ferramenta",
              "SubagentStart": "subagente_inicio", "SubagentStop": "subagente_fim", "Stop": "fim_turno",
              "SessionEnd": "fim"},
}
# a notification do Copilot, pelo notification_type: só o que pede algo do usuário, ou que devolve a vez
NOTIFICACAO = {"permission_prompt": "espera", "elicitation_dialog": "espera",
               "agent_idle": "fim_turno", "agent_completed": "fim_turno"}
SUBAGENTE_KIRO = ("subagent", "use_subagent")  # no Kiro, o subagente é uma ferramenta do agente principal
GUARDA_DIAS = 7  # o panel apaga os eventos mais velhos que isso (o hook nunca apaga nada)
LOG = 30
PATCH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.M)
DIA = re.compile(r"^eventos-\d{8}\.jsonl$")
PARADA = 30 * 60
SOME = 60 * 60  # sessão que acabou some do painel depois disso
ORDEM = {"pergunta": 0, "sua vez": 1, "trabalhando": 2, "parada": 3, "acabou": 4}


def _limpo(texto, n=80):
    """Uma linha, sem caracteres de controle e com os espaços colapsados: o --text e a tela imprimem isso no
    terminal, e o texto pode ser de terceiro (prompt, título de PR)."""
    return vistas.limpo(texto, n)


def _texto(valor):
    patch = PATCH.search(valor)  # o apply_patch do Codex: o primeiro arquivo, e não o cabeçalho
    return _limpo(patch.group(1) if patch else valor)


def _alvo(entrada):
    """Uma linha curta do que a ferramenta fez: o comando, o caminho, ou o primeiro texto."""
    if isinstance(entrada, str):
        try:
            entrada = json.loads(entrada)
        except ValueError:
            return _texto(entrada)
    if not isinstance(entrada, dict):
        return ""
    for chave in ("command", "cmd", "path", "file_path", "filePath", "pattern", "url", "query"):
        valor = entrada.get(chave)
        if isinstance(valor, str) and valor:
            return _texto(valor)
    for valor in entrada.values():
        if isinstance(valor, str) and valor:
            return _texto(valor)
    return ""


def _campo(valor, n=200):
    """Só texto: um campo que chega como objeto (o payload é de terceiro) viraria uma chave que não é
    hasheável, e o painel quebraria por dois dias."""
    return _limpo(valor, n) if isinstance(valor, (str, int, float)) and not isinstance(valor, bool) else ""


def normalizar(ferramenta, evento, payload, env=None, agora=None):
    """O evento como o painel guarda, ou None para o que ele não guarda: a notificação que não pede nada
    do usuário, e o evento que esta versão não ouve (o de um hook antigo, antes do próximo install)."""
    env = os.environ if env is None else env
    p = payload if isinstance(payload, dict) else {}
    evento = evento or p.get("hook_event_name") or p.get("hookEventName") or ""
    normal = EVENTOS.get(ferramenta, {}).get(evento)
    if normal == "notificacao":
        normal = NOTIFICACAO.get(p.get("notification_type") or p.get("notificationType") or "")
    if normal is None:
        return None
    pane = _campo(env.get("TMUX_PANE"))
    tool = _campo(p.get("toolName") or p.get("tool_name"), 60)
    entrada = p.get("toolArgs") if "toolArgs" in p else p.get("tool_input")
    if tool in SUBAGENTE_KIRO and isinstance(entrada, dict):  # o nome do subagente, e não um `command` dele
        entrada = next((entrada[k] for k in ("agent_name", "name", "agent", "subagent") if entrada.get(k)), entrada)
    registro = {
        "ts": agora or time.time(), "ferramenta": ferramenta,
        # sem id no payload (a doc não garante o campo), o pane identifica a sessão
        "sessao": _campo(p.get("sessionId") or p.get("session_id"), 120) or (f"pane-{pane}" if pane else ""),
        "evento": normal,
        "tool": tool,
        "alvo": _alvo(entrada),
        "cwd": _campo(p.get("cwd"), 300),
        "pane": pane,
    }
    prompt = p.get("prompt") if normal == "turno" else p.get("initialPrompt") if normal == "inicio" else None
    if _campo(prompt):
        registro["prompt"] = _campo(prompt, 160)
    if evento == "postToolUseFailure":
        registro["falhou"] = True
    # o subagente: no Codex, o agent_id vem em todo evento de dentro dele; no Copilot, o agentName só no
    # início e no fim (as ferramentas de dentro chegam sem dizer de quem são)
    if ferramenta == "copilot":
        agente = _campo(p.get("agentName"), 60) if normal.startswith("subagente") else ""
        rotulo = _campo(p.get("agentDisplayName") or p.get("agentType"), 40) or agente
    else:
        agente, rotulo = _campo(p.get("agent_id"), 60), _campo(p.get("agent_type"), 40)
    if agente:
        registro.update(agente=agente, agente_tipo=rotulo or agente)
    return registro


def gravar_evento(estado, registro):
    # 700/600: a primeira linha de cada comando da ferramenta pode ter um token
    cfgmod.criar_estado(estado)
    caminho = estado / time.strftime("eventos-%Y%m%d.jsonl", time.localtime(registro["ts"]))
    linha = (json.dumps(registro) + "\n").encode()  # ASCII: um surrogate solto não derruba o evento
    fd = os.open(str(caminho), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, linha)
    finally:
        os.close(fd)


def hook(estado, ferramenta, evento, stdin=None, env=None):
    """O que o `fluxo hook` faz. Nunca levanta: qualquer erro vira uma linha em hook-erros.log."""
    try:
        bruto = (stdin or sys.stdin).read()
        try:
            payload = json.loads(bruto) if bruto.strip() else {}
        except ValueError:
            payload = {}
        registro = normalizar(ferramenta, evento, payload, env)
        if registro is None:  # o que o painel não guarda: não é erro
            return 0
        if not registro["sessao"]:  # nem id nem pane: o painel não teria como mostrar
            raise ValueError("evento sem id de sessão e sem TMUX_PANE")
        gravar_evento(estado, registro)
    except Exception as exc:  # noqa: BLE001 — o hook não pode falhar nunca
        try:
            cfgmod.criar_estado(estado)
            with open(estado / "hook-erros.log", "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {ferramenta} {evento}: {exc!r}\n")
        except Exception:  # noqa: BLE001
            pass
    return 0


class Acumulador:
    """O estado de cada sessão, montado evento a evento e guardado entre os quadros: a cada segundo, só
    os eventos novos passam por aqui, e não os do dia inteiro.

    Evento de subagente (início, fim, ou do Codex de dentro dele) não mexe no estado da sessão: um fim de
    subagente atrasado, depois do Stop, não pode pô-la de volta em "trabalhando". O subagente é uma
    instância: o Copilot só dá o nome do tipo, então um novo início com o mesmo nome abre outra entrada, e
    o fim fecha a mais velha aberta."""

    def __init__(self):
        self.s = {}

    def aplicar(self, eventos):
        for e in eventos:
            atual = self.s.setdefault(e["sessao"], {
                "sessao": e["sessao"], "ferramenta": e.get("ferramenta"), "inicio": e["ts"], "ultimo": e["ts"],
                "tool": "", "alvo": "", "cwd": "", "pane": "", "evento": None, "contagem": collections.Counter(),
                "subagentes": {}, "ultimo_prompt": "", "log": collections.deque(maxlen=LOG)})
            atual["ultimo"] = e["ts"]
            atual["log"].append(e)
            for chave in ("cwd", "pane"):
                if e.get(chave):
                    atual[chave] = e[chave]
            evento, agente = e.get("evento"), e.get("agente")
            if agente or evento in ("subagente_inicio", "subagente_fim"):
                self._subagente(atual["subagentes"], agente or "?", e)
                continue
            atual["evento"] = evento
            if e.get("prompt"):
                atual["ultimo_prompt"] = e["prompt"]
            if evento == "ferramenta":
                atual.update(tool=e.get("tool", ""), alvo=e.get("alvo", ""))
                atual["contagem"][e.get("tool") or "?"] += 1
                if e.get("tool") in SUBAGENTE_KIRO:  # o Kiro só avisa da chamada, quando ela termina
                    atual["subagentes"][f"{e.get('tool')}#{len(atual['subagentes']) + 1}"] = {
                        "base": e.get("tool"), "rotulo": e.get("alvo") or e.get("tool"), "inicio": e["ts"],
                        "fim": e["ts"], "contagem": collections.Counter()}

    @staticmethod
    def _subagente(subs, agente, e):
        evento = e.get("evento")
        abertos = [k for k, v in subs.items() if v["base"] == agente and v["fim"] is None]
        if evento == "subagente_inicio" or not abertos:  # o fim (ou uma ferramenta) sem início também abre
            chave = agente if agente not in subs else f"{agente}#{len(subs) + 1}"
            subs[chave] = {"base": agente, "rotulo": e.get("agente_tipo") or agente, "inicio": e["ts"], "fim": None,
                           "contagem": collections.Counter()}
            if evento == "subagente_inicio":
                return
            abertos = [chave]
        alvo = subs[abertos[0]]  # o mais velho aberto
        if evento == "subagente_fim":
            alvo["fim"] = e["ts"]
        elif evento == "ferramenta":
            alvo["contagem"][e.get("tool") or "?"] += 1

    def lista(self, tarefas=(), agora=None, vivos=None):
        """Uma linha por sessão, com o estado de agora. `tarefas`: registros vivos do tasks.jsonl (a tarefa
        pelo pane). `vivos`: os panes vivos do tmux; a sessão cujo pane morreu acabou, mesmo sem o fim."""
        agora = agora or time.time()
        por_pane = {t.get("pane"): t for t in tarefas if t.get("pane")}
        panes = {p for p, _ in vivos} if vivos is not None else None
        out = []
        for x in self.s.values():
            evento, ha = x.get("evento"), agora - x["ultimo"]
            if evento == "fim" or (panes is not None and x["pane"] and x["pane"] not in panes):
                if ha > SOME:
                    continue
                estado = "acabou"
            elif evento == "espera":
                estado = "pergunta"
            elif evento in ("fim_turno", "inicio", None):  # nova, esperando o primeiro prompt, também é a sua vez
                estado = "parada" if ha > PARADA else "sua vez"
            else:
                estado = "trabalhando"
            tarefa = por_pane.get(x["pane"])
            nome = (tarefa or {}).get("nome") or (Path(x["cwd"]).name if x["cwd"] else x["sessao"][:8])
            out.append(dict(x, estado=estado, nome=nome, ha=ha, tarefa=tarefa, log=list(x["log"]),
                            contagem=dict(x["contagem"]),
                            subagentes={k: dict(v, contagem=dict(v["contagem"])) for k, v in x["subagentes"].items()}))
        out.sort(key=lambda x: (ORDEM[x["estado"]], -x["ultimo"]))
        return out


def sessoes(eventos, tarefas=(), agora=None, vivos=None):
    """O Acumulador de uma vez só: os eventos todos, e a lista de agora."""
    acc = Acumulador()
    acc.aplicar(eventos)
    return acc.lista(tarefas, agora, vivos)


def podar(estado, agora=None, dias=GUARDA_DIAS):
    """Apaga os eventos-AAAAMMDD.jsonl mais velhos que `dias`, pela data do nome. Só o panel chama: o hook
    nunca apaga nada."""
    limite = time.strftime("eventos-%Y%m%d.jsonl", time.localtime((agora or time.time()) - dias * 86400))
    for p in Path(estado).glob("eventos-*.jsonl"):
        if DIA.match(p.name) and p.name < limite:
            try:
                p.unlink()
            except OSError:
                pass


class LeitorEventos:
    """Os eventos de hoje e de ontem, lidos aos pedaços: a cada chamada, só o que entrou desde a última
    (offset + inode por arquivo). A última linha sem quebra fica para a próxima leitura: o hook pode estar
    no meio dela. Reler os dois dias inteiros a cada segundo custaria dezenas de ms por quadro."""

    def __init__(self, estado):
        self.estado = Path(estado)
        self.arquivos = {}  # nome -> [inode, offset, resto, eventos]
        self.novos, self.recomecou = [], False  # o que a última leitura trouxe, e se algo saiu do conjunto

    def ler(self, agora=None):
        agora = agora or time.time()
        nomes = sorted({time.strftime("eventos-%Y%m%d.jsonl", time.localtime(agora - d * 86400)) for d in range(2)})
        self.novos, self.recomecou = [], False
        for nome in [n for n in self.arquivos if n not in nomes]:  # virou o dia: o de anteontem sai
            del self.arquivos[nome]
            self.recomecou = True
        out = []
        for nome in nomes:
            try:
                st = (self.estado / nome).stat()
            except OSError:
                self.recomecou = self.recomecou or nome in self.arquivos
                self.arquivos.pop(nome, None)
                continue
            atual = self.arquivos.get(nome)
            if atual is not None and (atual[0] != st.st_ino or st.st_size < atual[1]):  # trocado ou truncado
                self.recomecou, atual = True, None
            if atual is None:
                atual = self.arquivos[nome] = [st.st_ino, 0, b"", []]
            if st.st_size > atual[1]:
                try:
                    with open(self.estado / nome, "rb") as fh:
                        fh.seek(atual[1])
                        bloco = fh.read(st.st_size - atual[1])
                except OSError:
                    bloco = b""
                atual[1] += len(bloco)
                *linhas, atual[2] = (atual[2] + bloco).split(b"\n")
                for linha in linhas:
                    try:
                        r = json.loads(linha.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if isinstance(r, dict) and isinstance(r.get("sessao"), str) and r["sessao"] \
                            and isinstance(r.get("ts"), (int, float)):
                        atual[3].append(r)
                        self.novos.append(r)
            out.extend(atual[3])
        return out


class Coletor:
    """Toda a leitura de disco do panel, para as vistas (lib/fluxo/vistas.py) só desenharem. Os eventos a
    cada quadro; o resto (planos, revisões, watch, trava, erros de hook) a cada LENTO segundos, e só o que
    mudou de mtime é relido. Nada aqui levanta: arquivo faltando ou quebrado vira vazio."""

    LENTO = 5.0
    PID = 10.0  # o início do pid do dono da trava (o `ps` do Mac) no máximo a cada isso

    def __init__(self, cam, ligados=(), run=None):
        self.cam, self.ligados = cam, set(ligados)
        self.run = run
        self.eventos = LeitorEventos(cam.estado)
        self.acumulador = Acumulador()
        self._lento, self._lento_em, self._mtimes, self._pids, self._falhas = {}, -1e18, {}, {}, {}

    def _por_mtime(self, caminho, ler):
        try:
            mtime = caminho.stat().st_mtime
        except OSError:
            self._mtimes.pop(caminho, None)
            return None
        guardado = self._mtimes.get(caminho)
        if guardado and guardado[0] == mtime:
            return guardado[1]
        try:
            valor = ler(caminho.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        except Exception as exc:  # noqa: BLE001 — JSON quebrado, por exemplo: vira vazio até o arquivo mudar
            self._falhas[caminho.name] = repr(exc)
            valor = None
        self._mtimes[caminho] = (mtime, valor)
        return valor

    def _inicio(self, pid, agora):
        quando, valor = self._pids.get(pid, (None, None))
        if quando is None or agora - quando > self.PID:
            valor = lock.inicio_do_pid(pid, **({"run": self.run} if self.run else {}))
            self._pids[pid] = (agora, valor)
        return valor

    def _lentos(self, agora, vivos):
        """Uma fonte por vez: a que quebrar fica vazia e vira alerta, e as outras aparecem."""
        estado, dados = self.cam.estado, self.cam.dados
        out = {"planos": {}, "revisoes": {}, "avisos": [], "vigia": {}, "trava": None, "erros_hook": []}
        self._falhas = {}

        def fonte(nome, fazer):
            try:
                fazer()
            except Exception as exc:  # noqa: BLE001
                self._falhas[nome] = repr(exc)

        def planos():
            for p in sorted((dados / "planos").glob("*.md")):
                plano = self._por_mtime(p, vistas.ler_plano)
                if plano is not None:
                    out["planos"][p.stem] = dict(plano, mtime=self._mtimes[p][0])

        def revisoes():
            raiz = dados / "revisoes"
            for p in sorted(raiz.rglob("*.md")):
                rev = self._por_mtime(p, lambda t, nome=p.name: vistas.ler_revisao(nome, t))
                if rev is not None:  # o id pode ter / (o nome da branch): é o caminho até a pasta
                    out["revisoes"].setdefault(p.parent.relative_to(raiz).as_posix(), []).append(
                        dict(rev, mtime=self._mtimes[p][0]))

        def avisos():
            out["avisos"] = watch.ler_avisos(estado, agora - 86400)
            vigia = self._por_mtime(estado / "vigia.json", lambda t: json.loads(t) if t.strip() else {})
            out["vigia"] = vigia if isinstance(vigia, dict) else {}

        def trava():
            dono = lock.Trava(estado).ler()
            vivo = lock.dono_vivo(dono, vivos, lambda pid: self._inicio(pid, agora)) if dono else None
            out["trava"] = {"dono": dono, "vivo": vivo, "texto": lock.descrever(dono, vivo is not False, agora)}

        def erros_hook():
            try:
                linhas = (estado / "hook-erros.log").read_text(encoding="utf-8", errors="replace").splitlines()[-200:]
            except OSError:
                linhas = []
            desde = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(agora - 86400))
            out["erros_hook"] = [l for l in linhas if l[:19] >= desde]

        fonte("planos", planos)
        if "reviewer" in self.ligados:
            fonte("revisões", revisoes)
        if "watch" in self.ligados:
            fonte("watch", avisos)
        if "lock" in self.ligados:
            fonte("trava", trava)
        fonte("hook-erros.log", erros_hook)
        out["erros_leitura"] = [f"{nome}: {erro}" for nome, erro in sorted(self._falhas.items())]
        return out

    def coletar(self, agora=None, vivos=None):
        agora = agora or time.time()
        if vivos is None:
            vivos = tmux.panes_vivos(self.run) if self.run else tmux.panes_vivos()
        if agora - self._lento_em >= self.LENTO:
            self._lento_em = agora  # mesmo que falhe: senão tudo seria relido a cada quadro
            self._lento = self._lentos(agora, vivos)
        todos = self.eventos.ler(agora)
        if self.eventos.recomecou:  # virou o dia, ou um arquivo foi trocado: monta de novo
            self.acumulador = Acumulador()
            self.acumulador.aplicar(todos)
        else:
            self.acumulador.aplicar(self.eventos.novos)
        tarefas = task.vivas(self.cam.estado, vivos)  # pelo par pane + pane_pid
        # sem tmux, `vivos` vem vazio: aí nenhum pane conta como morto (senão tudo viraria "acabou")
        return dict(self._lento, agora=agora, ligados=self.ligados,
                    sessoes=self.acumulador.lista(tarefas, agora, vivos=vivos or None))


def resolver_seguida(sessoes_, prefixo):
    """O --follow aceita o começo do id; sem nenhuma que comece assim, fica o próprio texto (a aba 1 diz
    que a fixada não está entre as vivas, em vez de seguir outra)."""
    return next((x["sessao"] for x in sessoes_ if x["sessao"].startswith(prefixo)), prefixo)


def texto(cam, ligados, tela, largura=100, altura=200, run=None, agora=None):
    """O que o `fluxo panel --text` imprime: uma vista, uma vez, sem cor."""
    try:
        dados = Coletor(cam, ligados, run=run).coletar(agora)
        tela = dict(tela)
        if tela.get("seguida"):
            tela["seguida"] = resolver_seguida(dados["sessoes"], tela["seguida"])
        tela["seguida"] = vistas.seguir(dados["sessoes"], tela, dados["agora"])
        return vistas.como_texto(vistas.VISTAS[tela.get("aba") or "list"](dados, tela, largura, altura))
    except Exception as exc:  # noqa: BLE001 — o --text também não cai por causa de um arquivo
        return f"o panel não montou a vista: {exc!r}"


def pular(pane, run):
    """Leva o seu client do tmux até o pane. Só seleciona: nunca digita nada (send-keys)."""
    r = run(["tmux", "switch-client", "-t", pane], capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or "").strip()


def _atributos(curses):
    """Um atributo por estilo das vistas. Sem cor (NO_COLOR, ou um terminal sem), negrito e normal."""
    attr = {None: curses.A_NORMAL, "titulo": curses.A_BOLD, "fraco": curses.A_DIM, "sel": curses.A_REVERSE,
            "aba": curses.A_REVERSE | curses.A_BOLD, "ok": curses.A_NORMAL, "atencao": curses.A_BOLD, "erro": curses.A_BOLD}
    if os.environ.get("NO_COLOR") or not curses.has_colors():
        return attr
    try:
        curses.use_default_colors()
        fundo = -1
    except curses.error:
        fundo = curses.COLOR_BLACK
    cores = {"ok": curses.COLOR_GREEN, "atencao": curses.COLOR_YELLOW, "erro": curses.COLOR_RED, "titulo": curses.COLOR_CYAN}
    for n, (estilo, cor) in enumerate(cores.items(), 1):
        try:
            curses.init_pair(n, cor, fundo)
            attr[estilo] = curses.color_pair(n) | (curses.A_BOLD if estilo in ("erro", "titulo") else 0)
        except curses.error:
            pass
    return attr


def tela(cam, run, ligados=(), inicial=None, intervalo=1.0):
    """A tela em curses, com as 4 abas das vistas (lib/fluxo/vistas.py). Tudo o que ela desenha vem das
    vistas, e o que cada tecla faz vem de vistas.aplicar: aqui fica só o curses."""
    import curses
    import locale

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    glifos = vistas.glifos_para(locale.getpreferredencoding(False))
    coletor = Coletor(cam, ligados, run=run)
    estado = dict({"aba": "list", "escolhida": None, "seguida": None, "fixada": False, "tarefa": None,
                   "rolagem": 0, "escondidas": set()}, **(inicial or {}))

    def desenhar(scr, attr, linhas, altura, largura):
        scr.erase()
        for y, linha in enumerate(linhas[:max(0, altura)]):
            x = 0
            for texto_, estilo in vistas.ajustar(linha, largura - 1):
                try:
                    scr.addstr(y, x, texto_, attr.get(estilo, curses.A_NORMAL))
                except curses.error:  # o canto da tela, ou um caractere que o locale não desenha
                    pass
                x += vistas.colunas(texto_)
        scr.refresh()

    def principal(scr):
        for passo in (lambda: curses.curs_set(0), lambda: curses.set_escdelay(25)):  # o Esc sem 1 s de espera
            try:
                passo()
            except (curses.error, AttributeError):
                pass
        scr.keypad(True)
        scr.timeout(int(intervalo * 1000))
        attr, nota = _atributos(curses), ""
        if estado.get("seguida"):  # --follow aceita o começo do id
            try:
                estado["seguida"] = resolver_seguida(coletor.coletar()["sessoes"], estado["seguida"])
            except Exception:  # noqa: BLE001 — o laço abaixo mostra o erro
                pass
        while True:
            altura, largura = scr.getmaxyx()
            dados = {"sessoes": [], "agora": time.time()}
            try:
                dados = coletor.coletar()
                estado["seguida"] = vistas.seguir(dados["sessoes"], estado, dados["agora"])
                ids = [x["sessao"] for x in vistas.visiveis(dados, estado)]
                if estado["escolhida"] not in ids:
                    estado["escolhida"] = ids[0] if ids else None
                corpo = vistas.VISTAS[estado["aba"]](dados, estado, largura - 1, max(1, altura - 3), glifos)
                linhas = [vistas.barra_de_abas(estado, dados), []] + corpo
            except Exception as exc:  # noqa: BLE001 — um quadro que não monta vira alerta, e a tela segue
                linhas = [[(f"o panel não montou o quadro: {exc!r}", "erro")]]
            linhas = linhas[:max(0, altura - 1)] + [[]] * max(0, altura - 1 - len(linhas))
            desenhar(scr, attr, linhas + [[(nota, "atencao")] if nota else vistas.rodape(estado, glifos)], altura, largura)
            try:
                tecla = scr.getch()
            except KeyboardInterrupt:
                return
            acao = vistas.acao_da_tecla(tecla, curses)
            if acao == "sair":
                return
            if acao == "ir":
                quem = estado["seguida"] if estado["aba"] == "session" else estado["escolhida"]
                alvo = next((x for x in dados.get("sessoes") or () if x["sessao"] == quem), None)
                if alvo and alvo.get("pane"):
                    ok, erro = pular(alvo["pane"], run)
                    nota = "" if ok else f"não deu para ir até a sessão: {erro}"
                else:
                    nota = "a sessão não tem pane do tmux"
            elif acao:
                nota = ""
                vistas.aplicar(acao, estado, dados)

    try:
        curses.wrapper(principal)
    except KeyboardInterrupt:
        return 0
    except curses.error as exc:
        # o ncurses do sistema (o do Mac) pode não conhecer o TERM do tmux: tenta um parecido, e, se não der,
        # imprime a vista uma vez, como o --text
        if os.environ.get("TERM", "").startswith("tmux") and "setupterm" in str(exc) + repr(exc):
            os.environ["TERM"] = "screen-256color"
            try:
                curses.wrapper(principal)
                return 0
            except curses.error:
                pass
        print(texto(cam, ligados, estado, run=run))
        print(f"fluxo panel: o terminal não abriu a tela ({exc}); acima, a vista uma vez", file=sys.stderr)
    return 0


# ── o que o `install` grava com o módulo panel ligado ──────────────────────

def _comando(ferramenta, evento, ou_verdadeiro=False):
    """`ou_verdadeiro`: o Kiro V3 começa outro turno quando o Stop sai com 1, e o hook só sai com 1 se o
    Python quebrar antes de rodar o fluxo (https://kiro.dev/docs/hooks/actions/)."""
    return f'"$HOME/.local/bin/fluxo" hook --tool {ferramenta} --event {evento}' + (" || true" if ou_verdadeiro else "")


def arquivos_da_ferramenta(cfg, ferr, Arquivo, skills=(), **_):
    """Os hooks de uma ferramenta (o `montar` chama uma vez para cada uma de `tools`)."""
    if ferr == "copilot":
        hooks = {ev: [{"type": "command", "bash": _comando("copilot", ev), "timeoutSec": 5}]
                 for ev in EVENTOS["copilot"]}  # nunca preToolUse: no Copilot ele é fail-closed
        texto = json.dumps({"version": 1, "hooks": hooks}, indent=2) + "\n"
        return [Arquivo(".copilot/hooks/fluxo.json", texto.encode())], []
    if ferr == "kiro" and cfg["kiro"]["engine"] == "v3":
        hooks = [{"name": f"fluxo-{ev}", "trigger": ev, **({"matcher": "*"} if ev == "PostToolUse" else {}),
                  "action": {"type": "command", "command": _comando("kiro", ev, ou_verdadeiro=True)}, "timeout": 5}
                 for ev in ("SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd")]
        texto = json.dumps({"version": "v1", "hooks": hooks}, indent=2) + "\n"
        return [Arquivo(".kiro/hooks/fluxo.json", texto.encode())], []
    if ferr == "kiro":
        # o V2 só lê hooks embutidos num agente: o agente `fluxo` vira o padrão (`fluxo panel enable`) e
        # carrega o steering e as skills pelos resources
        hooks = {ev: [dict({"command": _comando("kiro", ev, ou_verdadeiro=True)}, **({"matcher": "*"} if ev == "postToolUse" else {}))]
                 for ev in ("agentSpawn", "userPromptSubmit", "postToolUse", "stop")}
        agente = {"name": "fluxo", "description": "Agente padrão do fluxo: as instruções e as skills do fluxo, com os hooks do painel.",
                  "prompt": "Siga as instruções do fluxo (steering fluxo.md) e use as skills dele.",
                  "tools": ["*"],
                  "includeMcpJson": True,  # como agente padrão, não pode tirar os MCPs do usuário (a validar)
                  "resources": ["file://~/.kiro/steering/fluxo.md"] + [f"skill://~/.kiro/skills/{s['name']}/SKILL.md" for s in skills],
                  "hooks": hooks}
        return [Arquivo(".kiro/agents/fluxo.json", (json.dumps(agente, ensure_ascii=False, indent=2) + "\n").encode())], []
    if ferr == "codex":
        # https://learn.chatgpt.com/docs/hooks: payload em JSON no stdin; o SessionEnd aceita até 3 s.
        # Em ordem fixa: o Codex aprova o hook pelo hash, e um arquivo que muda pede nova aprovação
        hooks = {ev: [{"hooks": [{"type": "command", "command": _comando("codex", ev),
                                  "timeout": 3 if ev == "SessionEnd" else 5}]}]
                 for ev in EVENTOS["codex"]}
        texto = json.dumps({"hooks": hooks}, indent=2) + "\n"
        return [Arquivo(".codex/hooks.json", texto.encode())], []
    return [], []  # Claude: o requer do modulo.json o deixa de fora; lá o painel é o agent-tree


def avisos_do_install(removidos, cam, gravados=()):
    """O que o install precisa dizer sobre os hooks.

    - Vai apagar o agente `fluxo` do Kiro, que o `panel enable` tinha feito padrão: sem o `disable`, o
      Kiro ficaria com um agente padrão que não existe.
    - Vai criar ou mudar o `hooks.json` do Codex: lá, hook novo ou mudado só roda depois de aprovado."""
    avisos = []
    if ".kiro/agents/fluxo.json" in removidos and (cam.estado / "painel-agente-anterior.txt").exists():
        avisos.append("o agente fluxo é o padrão do Kiro (fluxo panel enable): rode fluxo panel disable para devolver o anterior")
    if ".codex/hooks.json" in gravados:
        avisos.append("hooks do panel no Codex: aprove-os em /hooks na próxima sessão (de novo a cada mudança do fluxo)")
    return avisos


def ligar(cfg, cam, run):
    """O Kiro V2 precisa de um passo: tornar o agente `fluxo` o padrão (e guardar o anterior). O Codex,
    de uma aprovação em /hooks, que é do usuário."""
    codex = "; no Codex, aprove os hooks em /hooks" if "codex" in cfg["tools"] else ""
    if not ("kiro" in cfg["tools"] and cfg["kiro"]["engine"] == "v2"):
        return 0, "nada a ligar: os hooks já valem a partir da próxima sessão" + codex
    anterior = (run(["kiro-cli", "settings", "chat.defaultAgent"], capture_output=True, text=True).stdout or "").strip()
    cfgmod.criar_estado(cam.estado)
    guardado = cam.estado / "painel-agente-anterior.txt"
    if anterior != "fluxo" and not guardado.exists():  # ligar duas vezes não pode guardar o próprio fluxo
        guardado.write_text(anterior)
    r = run(["kiro-cli", "agent", "set-default", "fluxo"], capture_output=True, text=True)
    return (0, "agente fluxo é o padrão do Kiro agora" + codex) if r.returncode == 0 else (1, f"kiro-cli: {r.stderr.strip()}")


def desligar(cfg, cam, run):
    """Devolve o agente padrão que o `ligar` guardou. Decide pelo que foi guardado, e não pela config:
    depois de tirar o kiro de `tools`, o engine v2 ou o módulo, o padrão do Kiro ainda é o `fluxo`."""
    guardado = cam.estado / "painel-agente-anterior.txt"
    if not guardado.exists():  # sem `ligar` antes, não há o que devolver: nunca apagar o padrão do usuário
        if "kiro" in cfg["tools"] and (cfg.get("kiro") or {}).get("engine") == "v2":
            return 0, "o painel não tinha mudado o agente padrão"
        return 0, "nada a desligar: para tirar os hooks, fluxo modules --disable panel && fluxo install"
    anterior = guardado.read_text().strip()
    if not re.match(r"^[\w.-]+$", anterior):  # ex.: "No value set": não é nome de agente
        anterior = ""
    argv = ["kiro-cli", "agent", "set-default", anterior] if anterior else ["kiro-cli", "settings", "--delete", "chat.defaultAgent"]
    r = run(argv, capture_output=True, text=True)
    if r.returncode:
        return 1, f"kiro-cli: {r.stderr.strip()}"
    guardado.unlink()
    return 0, f"agente padrão de volta: {anterior or 'o do Kiro'}"
