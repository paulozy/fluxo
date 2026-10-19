"""As perguntas do fluxo: o `fluxo init` sem --tool/--forge e os menus do `task`, do `modules` e do `validate`
quando falta o que eles precisam. Numa lista numerada, com o padrão entre colchetes.

As respostas preenchem o mesmo Namespace das flags, que segue para a validação de sempre. Só pergunta com stdin e
stdout em terminal (o `CanPrompt` do gh, em pkg/iostreams/iostreams.go), e nunca com --no-input
(https://clig.dev/#interactivity), com `CI` definida, com `TERM=dumb` (o is-interactive, do sindresorhus) ou com
a marca que um agente põe nos comandos que roda (`AGENTES`).

A pergunta é escrita aqui, no stderr, e o `input()` é chamado sem argumento: com o prompt dado a ele, o
destino muda conforme o readline esteja importado e o stdin seja terminal (Parser/myreadline.c, bpo-1927). O
readline não é importado: no Python do Xcode ele é libedit.
"""
import json
import re
import shlex
import shutil
import sys
from pathlib import Path

from . import config as cfgmod
from . import montar, task, validate


class Cancelado(Exception):
    """EOF ou Ctrl-C numa pergunta: nada é gravado."""


def _tty(f):
    """Com o fd fechado (`0<&-`), o Python põe None no sys.stdin; um stream fechado levanta ValueError."""
    try:
        return f is not None and f.isatty()
    except (ValueError, OSError):
        return False


# As marcas que um agente põe nos comandos que roda pela ferramenta de shell. Com elas, nunca pergunta, nem com
# TTY: o Codex dá um PTY quando o modelo pede `tty:true`, e o Kiro pode herdar o stdin. Um falso positivo só
# devolve o erro de sempre, e um falso negativo travaria o agente esperando resposta. O `CLAUDECODE` fica de
# fora: ele também aparece no terminal da IDE, onde quem digita é gente.
# - AI_AGENT: o VS Code (src/vs/platform/chat/common/aiAgentEnv.ts); o Claude Code também, medido na 2.1.
# - CLAUDE_CODE_CHILD_SESSION: só nas ferramentas e nos hooks (https://code.claude.com/docs/en/env-vars).
# - CODEX_CI, CODEX_THREAD_ID: todo comando do Codex (codex-rs/core/src/unified_exec/process_manager.rs e
#   codex-rs/protocol/src/shell_environment.rs, rust-v0.163.0).
# - COPILOT_CLI: o Copilot CLI, "to skip interactive prompts" (changelog.md do github/copilot-cli).
# - AGENT_CONTEXT_OUT, AGENT_DISPLAY_OUT: o Kiro, "only set when the agent is driving the command"
#   (https://kiro.dev/docs/reference/built-in-tools.md, "Side channels").
AGENTES = ("AI_AGENT", "CLAUDE_CODE_CHILD_SESSION", "CODEX_CI", "CODEX_THREAD_ID", "COPILOT_CLI",
           "AGENT_CONTEXT_OUT", "AGENT_DISPLAY_OUT")


def desligado(args, env):
    """O motivo de não perguntar mesmo num terminal, ou None."""
    if getattr(args, "no_input", False):
        return "--no-input"
    if "CI" in env:
        return "a variável CI"
    if env.get("TERM") == "dumb":
        return "TERM=dumb"
    agente = next((v for v in AGENTES if v in env), None)
    return f"a variável {agente}" if agente else None


def pode_perguntar(args, stdin, stdout, env):
    return _tty(stdin) and _tty(stdout) and not desligado(args, env)


class Perguntas:
    def __init__(self, ler, saida):
        self.ler, self.saida = ler, saida

    def escrever(self, texto=""):
        self.saida.write(texto + "\n")
        self.saida.flush()

    def _resposta(self, texto, padrao):
        self.saida.write(f"{texto}{f' [{padrao}]' if padrao else ''}: ")
        self.saida.flush()
        try:
            r = self.ler()
        except (EOFError, KeyboardInterrupt):
            raise Cancelado() from None
        return r.strip() or (padrao or "")

    def _lista(self, titulo, opcoes, notas):
        self.escrever(f"\n{titulo}")
        largura = max(len(o) for o in opcoes)
        for i, o in enumerate(opcoes, 1):
            nota = notas.get(o, "")
            self.escrever(f"  {i}) {o:<{largura}}  {nota}".rstrip())

    def _itens(self, texto, opcoes):
        """`1,3`, `1 3` ou os nomes, na ordem digitada e sem repetir; None se algum não existe."""
        out = []
        for t in re.split(r"[,\s]+", texto.strip()):
            if not t:
                continue
            if re.fullmatch(r"[0-9]+", t) and 1 <= int(t) <= len(opcoes):  # o isdigit aceitaria `²`
                t = opcoes[int(t) - 1]
            if t not in opcoes:
                return None
            if t not in out:
                out.append(t)
        return out

    def uma(self, titulo, opcoes, padrao, notas=None):
        padrao = padrao if padrao in opcoes else None  # um `forge: "bitbucket"` antigo não vira padrão
        self._lista(titulo, opcoes, notas or {})
        while True:
            itens = self._itens(self._resposta("Número ou nome", padrao), opcoes)
            if itens and len(itens) == 1:
                return itens[0]
            self.escrever(f"  escolha um: o número (1 a {len(opcoes)}) ou o nome")

    def varias(self, titulo, opcoes, padrao, notas=None, recusar=None, vazio_ok=False):
        """`recusar`: {opção: motivo} das que aparecem na lista mas não podem ser escolhidas."""
        self._lista(titulo, opcoes, notas or {})
        dica = "Números ou nomes, separados por vírgula" + ('; "nenhum" para pular' if vazio_ok else "")
        while True:
            r = self._resposta(dica, ",".join(padrao) or ("nenhum" if vazio_ok else ""))
            if vazio_ok and r.lower() == "nenhum":
                return []
            itens = self._itens(r, opcoes)
            if itens is None:
                self.escrever(f"  não entendi {r!r}: use os números (1 a {len(opcoes)}) ou os nomes")
            elif not itens:
                self.escrever("  escolha pelo menos um")
            elif any(i in (recusar or {}) for i in itens):
                self.escrever("".join(f"  {i}: {recusar[i]}\n" for i in itens if i in recusar).rstrip("\n"))
            else:
                return itens

    def texto(self, titulo, padrao, valida):
        while True:
            r = self._resposta(titulo, padrao)
            erro = valida(r)
            if not erro:
                return r
            self.escrever(f"  {erro}")

    def sim(self, titulo, padrao):
        while True:
            r = self._resposta(f"{titulo} {'[S/n]' if padrao else '[s/N]'}", None).lower()
            if not r:
                return padrao
            if r in ("s", "sim", "y", "yes"):
                return True
            if r in ("n", "nao", "não", "no"):
                return False
            self.escrever("  responda s ou n")


def _branch(r):
    return None if cfgmod.BRANCH.fullmatch(r) else "nome de branch simples, sem espaço (ex.: develop)"


def _host(r):
    if not r:
        return "o host é obrigatório com o gitlab (ex.: gitlab.empresa.com.br)"
    if "://" in r or "/" in r or re.search(r"\s", r):
        return "só o host, sem https:// nem caminho (ex.: gitlab.empresa.com.br)"
    return None


def _pasta(r):
    return None if r and "\n" not in r else "a pasta onde ficam os clones (ex.: ~/code)"


def _bases(base):
    def valida(r):
        alvos = [a.strip() for a in r.split(",") if a.strip()]
        if not alvos or not all(cfgmod.BRANCH.fullmatch(a) for a in alvos) or len(set(alvos)) != len(alvos):
            return "nomes de branch, em ordem, separados por vírgula (ex.: staging,main)"
        if base in alvos:
            return f"sem a própria base ({base})"
        return None
    return valida


def _binario(raiz, tool):
    adapt = json.loads((Path(raiz) / "adaptadores" / "ferramenta" / f"{tool}.json").read_text(encoding="utf-8"))
    return adapt["lancar"][0]


def menu_task(p, prefixo, skills, prompt=None, nome=None):
    """O que falta ao `fluxo task` sem ID: a ação e o ID; só o prompt, com -n; ou só o ID, com um -p de tarefa.
    Devolve (id, prompt), com o prompt None no planejar (vale o padrão). Levanta Cancelado."""
    def valida_id(obrigatorio):
        def valida(r):
            if not r:
                return "o ID é obrigatório aqui" if obrigatorio else None
            return None if task.ID_RE.fullmatch(r) else "ID inválido: letras, números, ponto, hífen e sublinhado, até 40"
        return valida

    def valida_prompt(r):
        if not r:
            return "escreva o prompt"
        try:
            task.barra_no_lugar_do_prefixo(r, prefixo, skills)
        except task.ErroUso as exc:
            return str(exc)
        if nome and task.skill_de_tarefa(r, prefixo):
            return f"com -n a sessão é avulsa, e {prefixo}{task.skill_de_tarefa(r, prefixo)} é de uma tarefa"
        return None

    skill = task.skill_de_tarefa(prompt, prefixo)
    if skill:  # `-p /implementar`: só a skill ganha o ID; com mais texto, o prompt fica como veio
        id_ = p.texto(f"ID da tarefa do {prefixo}{skill}", None, valida_id(True))
        return id_, f"{prefixo}{skill} {id_}" if prompt.strip() == prefixo + skill else prompt
    if nome:
        return None, p.texto("Prompt da sessão (uma linha)", None, valida_prompt)
    notas = {s: f"{prefixo}{s} <ID>" for s in task.DE_TAREFA}
    notas["planejar"] += " (o padrão com ID)"
    notas["livre"] = "um prompt seu, com ID ou numa sessão avulsa"
    acao = p.uma("Ação da sessão:", [*task.DE_TAREFA, "livre"], "planejar", notas)
    if acao == "livre":
        prompt = p.texto("Prompt (uma linha)", None, valida_prompt)
        de_tarefa = bool(task.skill_de_tarefa(prompt, prefixo))
        id_ = p.texto("ID da tarefa" + ("" if de_tarefa else " (vazio: sessão avulsa)"), None, valida_id(de_tarefa))
        return id_ or None, prompt
    id_ = p.texto("ID da tarefa", None, valida_id(True))
    return id_, None if acao == "planejar" else f"{prefixo}{acao} {id_}"


ACOES_VALIDATE = {"enable": "clona o pr-reviewer, ou leva o clone para a tag desta versão",
                  "disable": "tira o clone",
                  "start": "revisa uma tarefa, ou roda o runbook de um repo, em segundo plano",
                  "status": "acompanha uma execução",
                  "stop": "cancela uma execução"}
MODOS_RECEITA = {"proposta": "um agente lê o código e propõe o runbook",
                 "check": "sobe o stack com o runbook, sem agente",
                 "fix": "refaz com o último --check que falhou"}


def menu_validate_acao(p):
    """A ação do `fluxo validate` sem ação. O exec fica de fora: precisa do `-- …`. Levanta Cancelado."""
    return p.uma("Ação do validate:", list(ACOES_VALIDATE), "start", ACOES_VALIDATE)


def menu_validate_alvo(p, cam, acao, modo=None):
    """O alvo que falta ao start, ao status ou ao stop: (id, repo, modo). `modo` é o das flags (check ou fix), que
    só a receita tem. No status e no stop, escolhe entre as execuções registradas (no stop, só as que rodam); sem
    nenhuma, devolve None. Levanta Cancelado."""
    if acao != "start":
        chaves = validate.execucoes(cam, rodando=acao == "stop")
        if not chaves:
            p.escrever("nenhuma validação rodando" if acao == "stop" else
                       "nenhuma validação registrada: fluxo validate start")
            return None
        tipo, _, nome = p.uma("Qual execução:", chaves, chaves[0]).partition("/")
        return (nome, None, modo) if tipo == "revisao" else (None, nome, modo)
    o_que = "receita" if modo else p.uma("Validar o quê:", ["tarefa", "receita"], "tarefa", {
        "tarefa": "a revisão do pr-reviewer, com o plano da tarefa (o /validar)",
        "receita": "o runbook de um repo (o /receita)"})
    if o_que == "tarefa":
        def valida_id(r):
            if not task.ID_RE.fullmatch(r):
                return "ID inválido: letras, números, ponto, hífen e sublinhado, até 40"
            if not (cam.dados / "planos" / f"{r}.md").is_file():
                return f"a tarefa {r} não tem plano: o /planejar grava o plano antes"
            return None
        return p.texto("ID da tarefa", None, valida_id), None, None
    repo = p.texto("Repo (o nome da pasta, em code_dir)", None,
                   lambda r: None if validate.RECEITA_RE.fullmatch(r) else "nome de repo inválido: letras, números, "
                   "hífen e sublinhado")
    return None, repo, modo or p.uma("Modo da receita:", list(MODOS_RECEITA), "proposta", MODOS_RECEITA)


def notas_dos_modulos(cfg, modulos, raiz):
    """({módulo: descrição}, {módulo: por que não serve}): o que as ferramentas de `cfg` não atendem é recusado."""
    notas, recusar = {}, {}
    for m in modulos:
        meta = json.loads((Path(raiz) / "modulos" / m / "modulo.json").read_text(encoding="utf-8"))
        notas[m] = meta.get("descricao", "")
        if not montar.atende(cfg, m, raiz):
            recusar[m] = f"requer {meta['requer']}, e as ferramentas escolhidas não atendem"
            notas[m] += f" (não serve: requer {meta['requer']})"
    return notas, recusar


def menu_modulos(p, cfg, existentes, raiz, ligar):
    """Os módulos a ligar, dentre os desligados, ou a desligar, dentre os ligados; [] se nenhum. Levanta Cancelado."""
    opcoes = [m for m in existentes if (m not in cfg["modules"]) == ligar]
    if not opcoes:
        p.escrever("todos os módulos já estão ligados" if ligar else "nenhum módulo está ligado")
        return []
    notas, recusar = notas_dos_modulos(cfg, opcoes, raiz)
    return p.varias(f"Módulos para {'ligar' if ligar else 'desligar'}:", opcoes, [], notas,
                    recusar if ligar else None, vazio_ok=True)  # desligar sempre pode, mesmo o que não serve


def perguntar(args, anterior, raiz, ler=input, saida=None, achar=shutil.which):
    """Preenche em `args` o que as flags não deram, com os valores de `anterior` (a config que já existe, ou
    {}) como padrão. `args.modulos` recebe os módulos escolhidos. Levanta Cancelado no EOF e no Ctrl-C."""
    p = Perguntas(ler, saida or sys.stderr)
    raiz = Path(raiz)
    ant = anterior if isinstance(anterior, dict) else {}

    def de_antes(*chaves):
        v = ant
        for c in chaves:
            v = v.get(c) if isinstance(v, dict) else None
        return v

    # quem roda o init para consertar uma config estragada não pode cair num traceback por causa dela
    def texto_de_antes(*chaves):
        v = de_antes(*chaves)
        return v if isinstance(v, str) and v.strip() else None

    def lista_de_antes(*chaves):
        v = de_antes(*chaves)
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []

    p.escrever("Configurando o fluxo nesta máquina. Enter aceita o padrão entre colchetes; Ctrl-C cancela.")
    if not args.tool:
        notas = {t: "(no PATH)" for t in cfgmod.TOOLS if achar(_binario(raiz, t))}
        antes = [t for t in lista_de_antes("tools") if t in cfgmod.TOOLS]
        args.tool = p.varias("Ferramentas (a primeira é a padrão do `fluxo task`):", list(cfgmod.TOOLS), antes, notas)
    if not args.forge:
        args.forge = p.uma("Onde ficam os repos:", list(cfgmod.EIXOS["forge"]), texto_de_antes("forge") or "github")
    if args.forge == "gitlab" and not args.gitlab_host:  # o host vazio sai como `glab api --hostname ""`
        antes = texto_de_antes("gitlab", "host")
        args.gitlab_host = p.texto("Host do GitLab", antes if antes and not _host(antes) else None, _host)
    if "kiro" in args.tool and args.engine is None:
        args.engine = p.uma("Engine do Kiro (o V3 entra com --v3):", list(cfgmod.ENGINES),
                            texto_de_antes("kiro", "engine") or "v2")
    if args.base is None:
        antes = texto_de_antes("git", "base")
        args.base = p.texto("\nBranch de desenvolvimento (de onde saem as branches)",
                            antes if antes and not _branch(antes) else "develop", _branch)
    if args.code_dir is None:
        antes = texto_de_antes("code_dir")
        args.code_dir = p.texto("Pasta dos clones", antes if antes and not _pasta(antes) else "~/code", _pasta)
    if args.os is None:
        args.os = p.uma("Sistema:", list(cfgmod.EIXOS["os"]), texto_de_antes("os") or cfgmod.detectar_so())
    # sem pergunta, porque hoje nenhuma fonte usa chat nem tracker: fica o de antes, se for válido
    for eixo in ("chat", "tracker"):
        if getattr(args, eixo) is None and de_antes(eixo) in cfgmod.EIXOS[eixo]:
            setattr(args, eixo, de_antes(eixo))

    parcial = {"tools": args.tool, "forge": args.forge, "os": args.os, "chat": args.chat or "draft",
               "tracker": args.tracker or "none", "kiro": {"engine": args.engine or "v2"}}
    existentes = list(montar.modulos_existentes(raiz))
    notas, recusar = notas_dos_modulos(parcial, existentes, raiz)
    antes = [m for m in lista_de_antes("modules") if m in existentes and m not in recusar]
    args.modulos = p.varias("Módulos (opcionais; `fluxo modules` muda depois):", existentes, antes, notas,
                            recusar, vazio_ok=True)
    if "promote" in args.modulos and args.promote_to is None:
        antes = ",".join(lista_de_antes("promote", "targets"))
        valida = _bases(args.base)
        args.promote_to = p.texto(f"Bases depois da {args.base}, em ordem, para o /promover",
                                  antes if antes and not valida(antes) else "main", valida)
    return args


HERDADAS = ("models", "task", "watch", "promote", "validate")  # o init não pergunta: vêm da config de antes


def herdadas(anterior, existentes):
    """As chaves de HERDADAS válidas da config de antes, e um aviso por chave inválida, que fica de fora (vale o
    padrão). Cada uma é conferida sozinha, sobre uma config mínima válida, pelo `cfgmod.validar` de sempre."""
    if not isinstance(anterior, dict):
        return {}, []
    base = {"version": 1, "tools": ["claude"], "forge": "github", "os": "linux"}
    boas, avisos = {}, []
    for chave in HERDADAS:
        if chave not in anterior:
            continue
        try:
            cfgmod.validar(dict(base, **{chave: anterior[chave]}), existentes)
            boas[chave] = anterior[chave]
        except cfgmod.ErroConfig as exc:
            avisos.append(f"{chave} da config de antes ficou de fora (vale o padrão): {exc}")
    return boas, avisos


def proximos_passos(cfg, prog):
    """O que falta rodar depois do init, na ordem, para a config gravada."""
    if prog.startswith("~/"):  # o ~ fica fora das aspas, para o shell expandir
        prog = "~/" + shlex.quote(prog[2:])
    else:
        prog = shlex.quote(prog)
    passos = [f"{prog} install --dry-run   # mostra o que vai gravar",
              f"{prog} install",
              "fluxo doctor"]
    if "validate" in cfg["modules"]:
        passos.append("fluxo validate enable   # traz o pr-reviewer")
    if "watch" in cfg["modules"]:
        passos.append("fluxo watch enable      # agenda o aviso das PRs/MRs")
    if "panel" in cfg["modules"] and (cfg.get("kiro") or {}).get("engine") == "v2" and "kiro" in cfg["tools"]:
        passos.append("fluxo panel enable      # no Kiro V2, o agente fluxo vira o padrão")
    if "messages" in cfg["modules"]:
        passos.append("fluxo msg tone          # cria o seu tom.md, para o /aviso")
    return passos
