"""Linha de comando do fluxo.

Saídas: 0 ok; 1 erro; 2 config ou uso inválido; 3 conflito (nada foi gravado); 130 uma pergunta cancelada (EOF
ou Ctrl-C), no init ou num menu.
"""
import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod
from . import __version__
from . import ajustes, assistente, completar, demo, doctor, empacotar, fonte, instalar, lock, messages, montar, panel, render, task, tmux, uso, validate, varredura, vistas, watch

ERROS_DE_USO = (cfgmod.ErroConfig, montar.ErroMontagem, render.ErroTemplate, fonte.ErroFonte)


def _caminhos(args):
    return cfgmod.Caminhos(home=getattr(args, "home", None))


def _carregar(args, raiz, modulo=None):
    cam = _caminhos(args)
    cfg = cfgmod.carregar(cam, montar.modulos_existentes(raiz))
    if modulo and modulo not in cfg["modules"]:
        raise cfgmod.ErroConfig(f"o módulo {modulo} está desligado: fluxo modules --enable {modulo} && fluxo install")
    return cam, cfg


def _perguntas(args, ler, saida, interativo):
    """As Perguntas, se dá para perguntar aqui; senão None, e o comando segue com o erro de sempre."""
    if interativo is None:
        interativo = assistente.pode_perguntar(args, sys.stdin, sys.stdout, os.environ)
    return assistente.Perguntas(ler, saida or sys.stderr) if interativo else None


def _dica(args):
    """O fim da mensagem quando falta algo e não deu para perguntar."""
    motivo = assistente.desligado(args, os.environ)
    return f"{motivo} desliga as perguntas" if motivo else "ou rode num terminal para escolher"


def _o_mesmo(perguntas, argv):
    """O comando que faz o mesmo sem o menu, para a próxima vez."""
    perguntas.escrever("o mesmo, sem menu: " + shlex.join(["fluxo", *argv]))


def _oferecer_install(args, raiz, perguntas, feito):
    """Depois de mudar o que os arquivos instalados usam: num terminal, pergunta se roda o install; sem, só avisa."""
    if perguntas:
        try:
            rodar = perguntas.sim(f"{feito} Rodar fluxo install agora?", True)
        except (assistente.Cancelado, KeyboardInterrupt):
            rodar = False
        if rodar:
            return cmd_install(argparse.Namespace(dry_run=False, force=False, verbose=False, home=args.home), raiz)
    print(f"{feito} Agora: fluxo install")
    return 0


def _cancelado(perguntas, o_que):
    perguntas.escrever(f"\ncancelado; {o_que}")
    return 130


def _adaptador(raiz, tool):
    return json.loads((Path(raiz) / "adaptadores" / "ferramenta" / f"{tool}.json").read_text(encoding="utf-8"))


def _config_bruta(cam):
    """(config como está no arquivo, os bytes lidos), para editar e regravar com o cfgmod.gravar_json."""
    try:
        return cfgmod.ler_json(cam.config)
    except FileNotFoundError:
        raise cfgmod.ErroConfig(f"sem config em {cam.config}: rode `fluxo init` antes") from None


def cmd_modules(args, raiz, ler=input, saida=None, interativo=None):
    cam = _caminhos(args)
    existentes = montar.modulos_existentes(raiz)
    vazias = [flag for flag, valor in (("--enable", args.enable), ("--disable", args.disable)) if valor == []]
    if vazias:  # `--enable` sem nome: o menu escolhe
        perguntas = _perguntas(args, ler, saida, interativo)
        if not perguntas:
            print(f"fluxo modules: {' e '.join(vazias)} sem módulo ({_dica(args)})", file=sys.stderr)
            return 2
        bruto, _ = _config_bruta(cam)
        antes = bruto.get("modules") if isinstance(bruto.get("modules"), list) else []
        # um módulo que não existe mais (de uma versão antiga) não impede o menu, e o --disable o oferece
        cfg = cfgmod.validar(dict(bruto, modules=[m for m in antes if m in existentes]), existentes)
        extintos = [m for m in antes if m not in existentes]
        try:
            for flag in ("enable", "disable"):
                if getattr(args, flag) == []:
                    setattr(args, flag, assistente.menu_modulos(perguntas, cfg, existentes, raiz, flag == "enable",
                                                                extintos))
        except (assistente.Cancelado, KeyboardInterrupt):
            return _cancelado(perguntas, "nada foi gravado")
        if not (args.enable or args.disable):
            perguntas.escrever("nada foi gravado")
            return 0
        _o_mesmo(perguntas, ["modules", *(["--enable", *args.enable] if args.enable else []),
                             *(["--disable", *args.disable] if args.disable else []),
                             *(["--home", args.home] if args.home else [])])
    if args.enable or args.disable:
        bruto, lido = _config_bruta(cam)
        ligados = [m for m in bruto.get("modules", []) if m not in (args.disable or [])]
        ligados = list(dict.fromkeys(ligados + list(args.enable or [])))  # sem repetir: duplicaria as instruções
        bruto["modules"] = ligados
        cfgmod.validar(bruto, existentes)  # módulo inexistente não chega ao arquivo
        cfgmod.gravar_json(cam.config, bruto, antes=lido)
        return _oferecer_install(args, raiz, perguntas if vazias else _perguntas(args, ler, saida, interativo),
                                 f"módulos ligados: {', '.join(ligados) or 'nenhum'}.")
    cfg = cfgmod.carregar(cam, existentes)
    for m in existentes:
        meta = json.loads((Path(raiz) / "modulos" / m / "modulo.json").read_text(encoding="utf-8"))
        print(f"  [{'x' if m in cfg['modules'] else ' '}] {m:<10} {meta.get('descricao', '')}")
    return 0


def _argv_task(args):
    argv = ["task", *([args.id] if args.id else [])]
    if args.prompt:  # `-p -x` o argparse lê como outra flag
        argv += [f"--prompt={args.prompt}"] if args.prompt.startswith("-") else ["-p", args.prompt]
    for flag, valor in (("-n", args.name), ("--tool", args.tool), ("--dir", args.dir), ("--target", args.target),
                        ("--home", args.home)):
        argv += [flag, valor] if valor else []
    return argv + [flag for flag, ligada in (("--force", args.force), ("--dry-run", args.dry_run)) if ligada]


def _abrir_editor(caminho):
    # a ordem do git (editor.c): VISUAL, que o TERM=dumb pula, EDITOR e vi; o caminho vai por "$@", fora do texto
    editor = (os.environ.get("VISUAL") if os.environ.get("TERM") != "dumb" else None) or os.environ.get("EDITOR") or "vi"
    subprocess.call(["sh", "-c", f'{editor} "$@"', "sh", str(caminho)])


def cmd_config(args, raiz, ler=input, saida=None, interativo=None, abrir=_abrir_editor):
    cam = _caminhos(args)
    if args.acao == "path":
        print(f"fluxo        {cam.config}")
        print(f"pr-reviewer  {validate.config_pr_reviewer(cam, os.environ)}")
        return 0
    arqs = ajustes.Arquivos(cam, montar.modulos_existentes(raiz))
    perguntas = _perguntas(args, ler, saida, interativo) if args.acao in (None, "edit", "set", "unset") else None
    if args.acao is None and perguntas:
        mudadas = []
        try:
            assistente.menu_config(perguntas, arqs, mudadas)
        except (assistente.Cancelado, KeyboardInterrupt):
            perguntas.escrever("\ncancelado; o que já foi gravado fica")
        return _depois_de_mudar(args, raiz, perguntas, mudadas)
    if args.acao in (None, "list"):
        if arqs.erro:
            print(f"fluxo config: a config não fecha ({arqs.erro}); fluxo config edit conserta", file=sys.stderr)
        linhas = [(c.nome, *arqs.valor(c)) for c in ajustes.visiveis(arqs.cfg, arqs.bruto)]
        if args.json:
            print(json.dumps({nome: {"valor": v, "origem": o} for nome, v, o in linhas}, ensure_ascii=False, indent=2))
            return 0
        largura = max(len(nome) for nome, _, _ in linhas)
        for nome, valor, origem in linhas:
            print(f"{nome:<{largura}}  {ajustes.mostrar(valor) or '—':<28}  {origem or ''}".rstrip())
        return 0
    if args.acao == "edit":
        if not perguntas:
            raise cfgmod.ErroConfig("o fluxo config edit abre o editor: rode num terminal, ou use fluxo config set")
        try:
            gravou = assistente.editar_config(perguntas, arqs, abrir)
        except (assistente.Cancelado, KeyboardInterrupt):
            return _cancelado(perguntas, "nada foi gravado")
        return _oferecer_install(args, raiz, perguntas, "config gravada.") if gravou else 0
    if not args.chave:
        raise cfgmod.ErroConfig(f"fluxo config {args.acao} <chave>")
    c = ajustes.chave(args.chave)
    if args.acao == "get":
        valor, _ = arqs.valor(c)
        if valor is None:  # como o git: sem valor, sai com 1 e nada no stdout
            return 1
        print(ajustes.mostrar(valor))
        return 0
    if args.acao == "set" and not args.valores:
        raise cfgmod.ErroConfig(f"fluxo config set {c.nome} <valor>")
    novo = ajustes.converter(c, args.valores) if args.acao == "set" else None
    if not arqs.gravar(c, novo):
        print(f"{c.nome}: nada mudou")
        return 0
    print(f"{c.nome} = {ajustes.mostrar(novo)}" if novo is not None else f"{c.nome}: volta ao padrão")
    return _depois_de_mudar(args, raiz, perguntas, [c])


def _depois_de_mudar(args, raiz, perguntas, mudadas):
    if any(c.nome == "validate.dir" for c in mudadas):
        print("aviso: o clone só troca com fluxo validate disable e fluxo validate enable")
    if any(c.install for c in mudadas):
        return _oferecer_install(args, raiz, perguntas, "a config mudou o que o install grava.")
    return 0


def cmd_task(args, raiz, ler=input, saida=None, interativo=None):
    cam, cfg = _carregar(args, raiz, "task")
    if args.list:
        return task.listar(cam)
    tool = args.tool or cfg["tools"][0]
    if tool not in cfg["tools"]:  # sem as instruções e as skills do fluxo, a sessão não teria o planejar
        print(f"fluxo task: {tool} não está em tools ({', '.join(cfg['tools'])}); acrescente na config e rode "
              "fluxo install", file=sys.stderr)
        return 2
    adapt, skills = _adaptador(raiz, tool), montar.nomes_de_skills(raiz)
    # sem ID, falta a ação (sem -p) ou o ID de um -p de tarefa; sem terminal, o resolver dá o erro de sempre
    falta = args.id is None and (not (args.prompt or "").strip()
                                 or (task.skill_de_tarefa(args.prompt, adapt["prefixo"]) and not args.name))
    perguntas = _perguntas(args, ler, saida, interativo) if falta else None
    pasta = Path(args.dir or os.path.expanduser(cfg["code_dir"]))
    try:
        if perguntas:
            try:
                args.id, args.prompt = assistente.menu_task(perguntas, adapt["prefixo"], skills,
                                                            (args.prompt or "").strip() or None, args.name)
            except (assistente.Cancelado, KeyboardInterrupt):
                return _cancelado(perguntas, "nenhuma sessão foi aberta")
            _o_mesmo(perguntas, _argv_task(args))
        return task.abrir(args.id, args.prompt, args.name, cfg, tool, adapt, cam, pasta, alvo=args.target,
                          forcar=args.force, dry_run=args.dry_run, dentro_do_tmux=bool(os.environ.get("TMUX")),
                          skills=skills)
    except task.ErroUso as exc:
        print(f"fluxo task: {exc}", file=sys.stderr)
        return 2


def cmd_msg(args, raiz):
    cam, cfg = _carregar(args, raiz, "messages")
    if args.acao == "tone":
        destino, criado = messages.criar_tom(cam.config_dir)
        print(f"{'criado' if criado else 'já existe'}: {destino}. Cole ali umas 20 mensagens suas, no fim, em 'Exemplos seus'.")
        return 0
    texto = sys.stdin.read() if args.arquivo in (None, "-") else Path(args.arquivo).read_text(encoding="utf-8")
    if not texto.strip():
        print("fluxo msg: mensagem vazia", file=sys.stderr)
        return 2
    salvo = messages.guardar(texto, cam.dados)
    copiou, como = messages.copiar(texto, cfg["os"])
    if copiou:
        print(f"copiado ({como}); cópia em {salvo}")
        return 0
    print(f"fluxo msg: não copiei ({como}); o texto está em {salvo}", file=sys.stderr)
    return 1


def cmd_lock(args, raiz):
    cam, cfg = _carregar(args, raiz, "lock")
    t = lock.Trava(cam.estado)
    if args.acao == "status":
        dono = t.ler()
        print(lock.descrever(dono, bool(dono) and t.vivo(dono)))
        return 0
    if args.acao == "break":
        ok, dono = t.quebrar(forcar=args.force)
        if not ok:
            print(f"fluxo lock: {lock.descrever(dono, True)}; o dono está vivo (--force quebra assim mesmo)", file=sys.stderr)
            return 1
        if dono and dono.get("projeto"):
            print(f"trava quebrada. Se o stack dele ficou de pé: docker compose -p {dono['projeto']} down -v")
        else:
            print("trava quebrada" if dono else "a trava já estava livre")
        return 0
    pane, pane_pid = lock.pane_atual()
    try:
        sessao = lock.quem(args.session, pane=pane, pane_pid=pane_pid)
    except lock.ErroTrava as exc:
        print(f"fluxo lock: {exc}", file=sys.stderr)
        return 2
    if args.acao == "release":
        ok, dono = t.soltar(sessao)
        if not ok:
            print(f"fluxo lock: a trava é de outra sessão ({lock.descrever(dono, True)})", file=sys.stderr)
            return 1
        print("trava solta" if dono else "a trava já estava livre")
        return 0
    pid = args.pid if args.pid is not None else lock.pid_da_ferramenta()
    kw = dict(sessao=sessao, pane=pane, pane_pid=pane_pid, pid=pid, id_=args.id, projeto=args.project)
    try:
        if args.acao == "run":
            comando = args.comando
            if not comando:
                print("fluxo lock run: falta o comando depois de --", file=sys.stderr)
                return 2
            return lock.rodar(t, comando, args.wait, **kw)
        ok, dono, _ = lock.pegar_esperando(t, args.wait, **kw)
    except lock.ErroTrava as exc:
        print(f"fluxo lock: {exc}", file=sys.stderr)
        return 2
    print(("trava sua: " if ok else "") + lock.descrever(dono, True))
    return 0 if ok else lock.OCUPADA


def cmd_watch(args, raiz):
    cam, cfg = _carregar(args, raiz, "watch")
    if args.acao is None:
        print(watch.status(cam))
        return 0
    if args.acao == "tick":
        code, resumo = watch.tick(cfg, cam)
        print(resumo)
        return code
    if args.acao == "loop":
        return watch.loop(cfg, cam, args.interval or cfg["watch"]["interval"])
    if args.acao == "enable":
        code, msg = watch.ligar(cfg, cam, alvo=args.target, dentro_do_tmux=bool(os.environ.get("TMUX")))
    else:
        code, msg = watch.desligar(cfg, cam)
    print(msg, file=sys.stderr if code else sys.stdout)
    return code


def _argv_validate(args):
    argv = ["validate", args.acao, *([args.alvo] if args.alvo else [])]
    for flag, valor in (("--recipe", args.recipe), ("--backend", args.backend), ("--home", args.home),
                        ("--profile", args.profile if args.profile != "local" else None),
                        ("--wait", str(args.wait) if args.wait else None)):
        argv += [flag, valor] if valor else []
    return argv + [flag for flag, ligada in (("--fix", args.fix), ("--check", args.check)) if ligada]


def _validate_aqui(args, cfg, cam, modo, perguntas):
    """O recipe e o doctor do pr-reviewer neste terminal. Sem terminal (num agente), o recipe vai com --json: não
    pergunta, não grava e devolve o comando do --apply."""
    if args.acao == "doctor":
        print("o doctor do pr-reviewer faz uma chamada de verdade ao agente (a sonda de contenção)", file=sys.stderr)
        return validate.em_primeiro_plano(cam, validate.comando(cfg, cam, ["doctor", *validate._backend(args.backend)]))
    repo = args.alvo or args.recipe
    if not repo:
        if not perguntas:
            raise cfgmod.ErroConfig("fluxo validate recipe <repo> [--check | --fix]")
        try:
            repo, modo = assistente.menu_receita(perguntas, modo)
        except (assistente.Cancelado, KeyboardInterrupt):
            return _cancelado(perguntas, "nada foi feito")
        args.alvo, args.recipe, args.fix, args.check = repo, None, modo == "fix", modo == "check"
        _o_mesmo(perguntas, _argv_validate(args))
    argv = validate.preparar_receita(cfg, cam, repo, modo or "proposta", args.backend, json_=not perguntas)
    trava = modo == "check" and "lock" in cfg["modules"]  # só o --check sobe o stack
    return validate.em_primeiro_plano(cam, argv, f"receita/{repo}", trava, args.wait)


def cmd_validate(args, raiz, ler=input, saida=None, interativo=None):
    perguntas = None
    if args.acao is None:
        perguntas = _perguntas(args, ler, saida, interativo)
        if not perguntas:
            print("fluxo validate: falta a ação: enable, disable, start, status, stop, recipe, doctor ou exec "
                  f"({_dica(args)})", file=sys.stderr)
            return 2
        try:
            args.acao = assistente.menu_validate_acao(perguntas)
        except (assistente.Cancelado, KeyboardInterrupt):
            return _cancelado(perguntas, "nada foi feito")
    # o disable vale com o módulo desligado: é o que tira o clone depois de desligá-lo
    cam, cfg = _carregar(args, raiz, None if args.acao == "disable" else "validate")
    if args.fix and args.check:
        raise cfgmod.ErroConfig("--fix e --check não combinam")
    if args.recipe and args.alvo:
        raise cfgmod.ErroConfig("é a tarefa ou o --recipe <repo>, não os dois")
    modo = "fix" if args.fix else "check" if args.check else None
    if args.acao in ("recipe", "doctor"):
        return _validate_aqui(args, cfg, cam, modo, perguntas or _perguntas(args, ler, saida, interativo))
    # o menu do alvo só onde ele resolve: no start, o --fix/--check já diz receita; no status e no stop, não cabe
    if args.acao in ("start", "status", "stop") and not (args.alvo or args.recipe) and (args.acao == "start" or not modo):
        perguntas = perguntas or _perguntas(args, ler, saida, interativo)
        if perguntas:  # sem terminal, segue para o erro de sempre
            try:
                alvo = assistente.menu_validate_alvo(perguntas, cam, args.acao, modo)
            except (assistente.Cancelado, KeyboardInterrupt):
                return _cancelado(perguntas, "nada foi feito")
            if alvo is None:
                return 1
            args.alvo, args.recipe, modo = alvo
            args.fix, args.check = modo == "fix", modo == "check"
    if perguntas:
        _o_mesmo(perguntas, _argv_validate(args))
    if modo and not args.recipe:
        raise cfgmod.ErroConfig("--fix e --check são do --recipe")
    if args.acao == "exec":
        argv = validate.comando(cfg, cam, args.comando)
        os.execvp(argv[0], argv)
        return 1  # o execvp só volta se falhar; nunca segue para outra ação
    if args.acao in ("start", "status", "stop", "_rodar") and not (args.alvo or args.recipe):
        raise cfgmod.ErroConfig(f"fluxo validate {args.acao} <id>, ou --recipe <repo>")
    if args.acao == "_rodar":  # o processo de fundo que o start solta: o alvo já é a chave
        return validate.rodar(cam, args.alvo)
    if args.acao in ("start", "status", "stop"):
        ch = validate.chave("receita", args.recipe) if args.recipe else validate.chave("revisao", args.alvo)
        alvo = f"--recipe {args.recipe}" if args.recipe else args.alvo
    if args.acao == "start":
        if args.recipe:
            modo = "fix" if args.fix else "check" if args.check else "proposta"
            argv, avisos = validate.preparar_receita(cfg, cam, args.recipe, modo, args.backend), []
            trava = modo == "check" and "lock" in cfg["modules"]  # só o --check sobe o stack
        else:
            argv, avisos = validate.preparar_revisao(cfg, cam, args.alvo, args.backend)
            trava = "lock" in cfg["modules"]
        validate.start(cfg, cam, raiz, ch, argv, trava, id_=args.alvo or args.recipe)
        for a in avisos:
            print(f"aviso: {a}")
        print(f"{alvo}: rodando em segundo plano. Acompanhe com: fluxo validate status {alvo} --wait 540")
        return 0
    if args.acao == "status":
        code, linhas = validate.status(cam, ch, args.wait)
        for linha in linhas:
            print(linha)
        return code
    if args.acao == "stop":
        code, msg = validate.stop(cam, ch)
        print(msg, file=sys.stderr if code else sys.stdout)
        return code
    if args.acao == "enable":
        code, msgs = validate.enable(cfg, cam, run=subprocess.run, profile=args.profile)
    elif args.acao == "disable":
        code, msgs = validate.disable(cfg, cam, run=subprocess.run)
    else:
        raise cfgmod.ErroConfig(f"ação {args.acao!r} desconhecida")
    for m in msgs:
        print(m, file=sys.stderr if code else sys.stdout)
    return code


def cmd_hook(args, raiz):
    """Chamado pela ferramenta em cada evento: não lê a config (que pode estar quebrada), nunca imprime,
    sempre sai com 0."""
    return panel.hook(_caminhos(args).estado, args.tool, args.event)


def cmd_panel(args, raiz):
    if args.acao and (args.view or args.task or args.follow or args.demo):
        print("fluxo panel: --view, --task, --follow e --demo são da tela, e não de enable/disable", file=sys.stderr)
        return 2
    if args.task and not task.ID_RE.fullmatch(args.task):
        print(f"fluxo panel: id {args.task!r} inválido", file=sys.stderr)
        return 2
    tela = {"aba": args.view or "list", "tarefa": args.task, "seguida": args.follow, "fixada": bool(args.follow)}
    if args.demo:  # antes da config e da poda: o visual se confere sem instalar nada, e sem tocar o disco
        if args.text or not sys.stdout.isatty():
            print(panel.texto(None, demo.LIGADOS, tela, de_mentira=True))
            return 0
        return panel.tela(None, run=subprocess.run, ligados=demo.LIGADOS, inicial=tela, de_mentira=True)
    # o disable vale com o módulo desligado: é o que devolve o agente padrão do Kiro depois de tirá-lo
    cam, cfg = _carregar(args, raiz, None if args.acao == "disable" else "panel")
    if args.acao in ("enable", "disable"):
        if args.acao == "enable":  # a statusline do Copilot só é gravada com o "sim" no terminal
            code, msg = panel.ligar(cfg, cam, subprocess.run, _perguntas(args, input, None, None))
        else:
            code, msg = panel.desligar(cfg, cam, subprocess.run)
        print(msg, file=sys.stderr if code else sys.stdout)
        return code
    panel.podar(cam.estado)  # só o panel apaga eventos velhos: o hook nunca apaga nada
    if args.text or not sys.stdout.isatty():
        print(panel.texto(cam, cfg["modules"], tela, ferramentas=cfg["tools"]))
        return 0
    return panel.tela(cam, run=subprocess.run, ligados=cfg["modules"], inicial=tela, ferramentas=cfg["tools"])


INIT_PADROES = {"base": "develop", "code_dir": "~/code", "chat": "draft", "tracker": "none", "engine": "v2"}


def cmd_init(args, raiz, ler=input, saida=None, interativo=None):
    """Com --tool e --forge, grava sem perguntar. Sem eles, num terminal, o assistente pergunta o resto."""
    cam = _caminhos(args)
    saida = saida or sys.stderr
    faltam = [flag for flag, valor in (("--tool", args.tool), ("--forge", args.forge)) if not valor]
    if faltam and interativo is None:  # com as duas flags, nem olha o terminal (o stdin pode estar fechado)
        interativo = assistente.pode_perguntar(args, sys.stdin, sys.stdout, os.environ)
    perguntando = bool(faltam) and bool(interativo)
    if faltam and not perguntando:
        motivo = assistente.desligado(args, os.environ)
        dica = f"{motivo} desliga as perguntas" if motivo else "ou rode num terminal para responder às perguntas"
        print(f"fluxo: {'faltam' if len(faltam) > 1 else 'falta'} {' e '.join(faltam)} ({dica})", file=sys.stderr)
        return 2
    if cam.config.exists() and not args.force and not perguntando:
        print(f"fluxo: {cam.config} já existe; --force para sobrescrever", file=sys.stderr)
        return 2
    anterior = {}
    if cam.config.exists():  # o --force refaz os eixos, mas não apaga os módulos nem as opções deles
        try:
            anterior = json.loads(cam.config.read_text(encoding="utf-8"))
        except ValueError:
            anterior = {}
    existentes = montar.modulos_existentes(raiz)
    perguntas = assistente.Perguntas(ler, saida)
    try:
        if perguntando and cam.config.exists() and not args.force and not perguntas.sim(
                f"Já existe {cam.exibir(cam.config)}. Refazer, com os valores dela de padrão?", False):
            print("nada foi gravado", file=saida)
            return 0
        if perguntando:
            herdado, avisos = assistente.herdadas(anterior, existentes)
            for aviso in avisos:
                perguntas.escrever(f"aviso: {aviso}")
            assistente.perguntar(args, anterior, raiz, ler, saida)
        else:
            herdado = {k: anterior[k] for k in assistente.HERDADAS + ("modules",)
                       if isinstance(anterior, dict) and k in anterior}
    except (assistente.Cancelado, KeyboardInterrupt):
        print("\ncancelado; nada foi gravado", file=saida)
        return 130
    for chave, padrao in INIT_PADROES.items():  # `--base ""` continua chegando ao validar, e dá erro
        if getattr(args, chave) is None:
            setattr(args, chave, padrao)
    tools = list(dict.fromkeys(args.tool))  # `--tool copilot --tool claude`: a primeira é a padrão
    cfg = {"version": 1, "tools": tools, "forge": args.forge, "tracker": args.tracker,
           "chat": args.chat, "os": args.os or cfgmod.detectar_so(), "modules": [],
           "git": {"base": args.base}, "code_dir": args.code_dir, "models": {}}
    cfg.update(herdado)
    if args.modulos is not None:  # o assistente perguntou os módulos
        cfg["modules"] = args.modulos
    if "kiro" in tools:
        cfg["kiro"] = {"engine": args.engine}
    if args.forge == "gitlab":
        cfg["gitlab"] = {"host": args.gitlab_host or ""}
    if args.promote_to:
        cfg["promote"] = {"targets": [a.strip() for a in args.promote_to.split(",") if a.strip()]}
    try:
        cfgmod.validar(cfg, existentes)
    except cfgmod.ErroConfig as exc:
        print(f"fluxo: {exc}", file=sys.stderr)
        return 2
    texto = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    if perguntando:
        print(f"\nA config:\n{texto}", file=saida, end="")
        try:
            if not perguntas.sim(f"Gravar em {cam.exibir(cam.config)}?", True):
                print("nada foi gravado", file=saida)
                return 0
        except (assistente.Cancelado, KeyboardInterrupt):
            print("\ncancelado; nada foi gravado", file=saida)
            return 130
    cam.config_dir.mkdir(parents=True, exist_ok=True)
    cfgmod.gravar_json(cam.config, cfg)  # refazer é o caminho comum: um Ctrl-C não deixa meio arquivo
    if not perguntando:
        print(f"config gravada em {cam.config}. Agora: fluxo install --dry-run")
        return 0
    print(f"config gravada em {cam.exibir(cam.config)}. Agora:")
    for passo in assistente.proximos_passos(cfg, cam.exibir(Path(raiz) / "fluxo")):
        print(f"  {passo}")
    return 0


def _montar(args, raiz):
    cam = _caminhos(args)
    cfg = cfgmod.carregar(cam, montar.modulos_existentes(raiz))
    arquivos, avisos = montar.montar(cfg, raiz, cam)
    return cam, cfg, arquivos, avisos


def cmd_completion(args, raiz):
    print(completar.SCRIPTS[args.acao], end="")
    return 0


def cmd_status(args, raiz):
    """O resumo da máquina, só com leitura local e rápida: é o `fluxo` sem comando, e vale também num agente."""
    cam = _caminhos(args)
    if not cam.config.exists():
        print(f"fluxo {__version__}: sem config em {cam.exibir(cam.config)}. Comece com fluxo init; fluxo -h mostra os "
              "comandos.")
        return 0
    try:
        cfg = cfgmod.carregar(cam, montar.modulos_existentes(raiz))
    except cfgmod.ErroConfig as exc:
        print(f"fluxo {__version__}: a config não fecha: {exc}\nfluxo config edit corrige; fluxo doctor confere o resto.")
        return 0
    tools = ", ".join(t + (" (padrão)" if i == 0 and len(cfg["tools"]) > 1 else "") for i, t in enumerate(cfg["tools"]))
    linhas = [("config", cam.exibir(cam.config)),
              ("ferramentas", f"{tools} · {cfg['forge']} · {cfg['os']}"),
              ("módulos", ", ".join(cfg["modules"]) or "nenhum (fluxo modules --enable)"),
              ("install", _status_install(cfg, cam, raiz))]
    if "task" in cfg["modules"]:
        vivas = task.vivas(cam.estado, tmux.panes_vivos())
        linhas.append(("sessões", ", ".join(f"{t.get('nome')} ({t.get('ferramenta') or '?'})" for t in vivas)
                       if vivas else "nenhuma aberta"))
    if "validate" in cfg["modules"]:
        rodando = validate.execucoes(cam, rodando=True)
        linhas.append(("validações", ", ".join(rodando) + " rodando" if rodando else "nenhuma rodando"))
        linhas.append(("pr-reviewer", _status_pr_reviewer(cam)))
    if "lock" in cfg["modules"]:
        trava = lock.Trava(cam.estado)
        dono = trava.ler()
        linhas.append(("trava", lock.descrever(dono, bool(dono) and trava.vivo(dono))))
    if "watch" in cfg["modules"]:
        linhas.append(("watch", watch.status(cam).splitlines()[0].replace("watch: ", "", 1)))
    largura = max(len(nome) for nome, _ in linhas)
    print(f"fluxo {__version__}")
    for nome, valor in linhas:
        print(f"  {nome:<{largura}}  {valor}")
    print("\nfluxo -h: os comandos · fluxo doctor: confere a máquina")
    return 0


def _status_install(cfg, cam, raiz):
    try:
        arquivos, _ = montar.montar(cfg, raiz, cam)
        acoes = instalar.planejar(arquivos, instalar.ler_manifesto(cam.manifesto), cam.home)
    except ERROS_DE_USO + (RuntimeError, OSError) as exc:
        return f"não monta: {exc}"
    pendentes = sum(a.tipo in (instalar.CRIAR, instalar.ATUALIZAR, instalar.REMOVER) for a in acoes)
    conflitos = sum(a.tipo == instalar.CONFLITO for a in acoes)
    if not pendentes and not conflitos:
        return "em dia"
    partes = ([f"{pendentes} arquivo(s) a gravar"] if pendentes else []) + ([f"{conflitos} conflito(s)"] if conflitos else [])
    return ", ".join(partes) + ": fluxo install --dry-run mostra"


def _status_pr_reviewer(cam):
    try:
        dados, _ = cfgmod.ler_json(validate.config_pr_reviewer(cam, os.environ))
    except FileNotFoundError:
        return "sem config: fluxo validate enable"
    except cfgmod.ErroConfig as exc:
        return str(exc)
    return (f"backend {dados.get('backend') or 'claude (o padrão dele)'}"
            + (f" · perfil {dados['profile']}" if dados.get("profile") else ""))


def cmd_install(args, raiz):
    cam, cfg, arquivos, avisos = _montar(args, raiz)
    acoes = instalar.planejar(arquivos, instalar.ler_manifesto(cam.manifesto), cam.home)
    avisos += validate.avisos_do_install(cfg, cam)
    avisos += panel.avisos_do_install({a.rel for a in acoes if a.tipo == instalar.REMOVER}, cam,
                                      {a.rel for a in acoes if a.tipo in (instalar.CRIAR, instalar.ATUALIZAR)
                                       or (a.tipo == instalar.CONFLITO and args.force)}, cfg)
    for a in avisos:
        print(f"aviso: {a}")
    tools = ", ".join(t + (" " + cfg["kiro"]["engine"] if t == "kiro" else "") for t in cfg["tools"])
    print(f"fluxo install ({tools}; {cfg['forge']}) em {cam.home}:")
    print(instalar.resumo(acoes, mostrar_iguais=args.verbose))
    conflitos = [a for a in acoes if a.tipo == instalar.CONFLITO]
    if args.dry_run:
        print("dry-run: nada foi gravado")
        return 3 if conflitos and not args.force else 0
    try:
        instalar.aplicar(acoes, cam.home, cam.manifesto, forcar=args.force)
    except instalar.ConflitoErro as exc:
        print(f"fluxo: {exc}. Nada foi gravado. Confira os arquivos acima; --force grava com backup "
              "(.fluxo-bak-<data>).", file=sys.stderr)
        return 3
    print("instalado." + (" Confira com: fluxo doctor" if not args.home else ""))
    return 0


def cmd_uninstall(args, raiz):
    cam = _caminhos(args)
    acoes = instalar.planejar_remocao(instalar.ler_manifesto(cam.manifesto), cam.home)
    for a in validate.avisos_do_uninstall(cam):
        print(f"aviso: {a}")
    print(f"fluxo uninstall em {cam.home}:")
    print(instalar.resumo(acoes))
    if args.dry_run:
        print("dry-run: nada foi removido")
        return 0
    instalar.desinstalar(acoes, cam.home, cam.manifesto)
    print("removido.")
    return 0


def cmd_doctor(args, raiz):
    cam = _caminhos(args)
    cfg, erro = None, None
    try:
        cfg = cfgmod.carregar(cam, montar.modulos_existentes(raiz))
    except cfgmod.ErroConfig as exc:
        erro = str(exc)
    resultados = doctor.checar(cam, raiz, cfg=cfg, erro_cfg=erro)
    print(doctor.formatar(resultados))
    return 1 if any(n == doctor.FALHA for n, _ in resultados) else 0


def cmd_scan(args, raiz):
    if args.hash:  # para acrescentar um termo ao tests/proibidos.sha256 sem escrevê-lo no repo
        print(varredura._hash(args.hash))
        return 0
    cam = cfgmod.Caminhos()
    achados = varredura.varrer(raiz, extra=cam.config_dir / "proibidos.txt")
    for arq, n, oque in achados:
        print(f"{arq}:{n}: {oque}")
    if achados:
        print(f"fluxo: {len(achados)} achado(s): tire o dado do arquivo antes de commitar", file=sys.stderr)
        return 1
    return 0


def cmd_package(args, raiz):
    try:
        destino = empacotar.empacotar(raiz, args.output)
    except RuntimeError as exc:
        print(f"fluxo: {exc}", file=sys.stderr)
        return 1
    print(f"{destino}\nNa outra máquina: python3 -m zipfile -e {destino.name} ~/fluxo-src && "
          "python3 ~/fluxo-src/fluxo/fluxo init … && python3 ~/fluxo-src/fluxo/fluxo install")
    return 0


class Parser(argparse.ArgumentParser):
    """O argparse do fluxo: a ajuda como foi escrita (os grupos e os exemplos vão no epilog), sem cor (a 3.14 liga
    sozinha) e o erro com a dica do -h. Os subparsers herdam a classe: o parser_class do add_subparsers é type(self)."""

    def __init__(self, *args, **kw):
        kw.setdefault("formatter_class", argparse.RawDescriptionHelpFormatter)
        kw["add_help"] = False  # o -h em português, abaixo
        super().__init__(*args, **kw)
        self.color = False
        # os títulos das seções: "optional arguments" até a 3.9, "options" depois; aqui, os dois em português
        self._positionals.title, self._optionals.title = "argumentos", "opções"
        self.add_argument("-h", "--help", action="help", help="mostra esta ajuda")

    def error(self, message):
        # as três mensagens mais comuns do argparse, em português; o resto vai como vem. Só o começo é conferido:
        # o "(choose from …)" muda de aspas entre patch releases
        if "invalid choice: " in message:
            valor = message.split("invalid choice: ", 1)[1].split(" (choose from", 1)[0].strip("'")
            message = f"{valor!r} não existe aqui"
        elif message.startswith("the following arguments are required: "):
            message = "falta " + message.split(": ", 1)[1]
        elif message.startswith("unrecognized arguments: "):
            message = "não conheço " + message.split(": ", 1)[1]
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: {message} ({self.prog} -h para a ajuda)\n")


# o `fluxo -h`: os comandos por área (o argparse não agrupa subcomandos, gh-53587). O hook fica de fora: quem o
# chama são as ferramentas
GRUPOS = (("começar", ("init", "install", "doctor", "status")),
          ("sessões", ("task", "panel", "watch", "lock", "msg")),
          ("validação", ("validate",)),
          ("config", ("config", "modules")),
          ("manutenção", ("uninstall", "package", "completion", "scan")))
NO_INPUT = "nunca abre o menu: o que faltar vira erro (também com CI definida, TERM=dumb ou num agente)"


def _exemplos(*linhas):
    return "exemplos:\n" + "\n".join(f"  {linha}" for linha in linhas)


def _comuns(p, filho=False, no_input=False):
    """O --home (dos testes; fica fora da ajuda) e o --no-input. No filho de uma ação vão com default=SUPPRESS, para
    não apagar o que o pai já leu (gh-89398): valem `fluxo validate --home X start` e `fluxo validate start --home X`."""
    extra = {"default": argparse.SUPPRESS} if filho else {}
    p.add_argument("--home", help=argparse.SUPPRESS, **extra)
    if no_input:
        p.add_argument("--no-input", action="store_true", help=NO_INPUT, **extra)


def parser():
    resumos = {}
    ap = Parser(prog="fluxo", usage="fluxo [<comando>] [-h]",
                description="o fluxo de dev portátil (Claude Code, Copilot CLI, Kiro CLI, Codex CLI)")
    ap.add_argument("--version", action="version", version=f"fluxo {__version__}", help="mostra a versão")
    sub = ap.add_subparsers(dest="cmd", metavar="<comando>", help=argparse.SUPPRESS, prog="fluxo")
    ap.set_defaults(fn=cmd_status)

    def comando(nome, resumo, exemplos=(), **kw):
        resumos[nome] = resumo
        p = sub.add_parser(nome, help=resumo, description=resumo, epilog=_exemplos(*exemplos) if exemplos else None, **kw)
        return p

    def acoes(p, required=False):
        return p.add_subparsers(dest="acao", metavar="<ação>", title="ações", required=required)

    def acao(grupo, nome, resumo, exemplos=(), no_input=False):
        a = grupo.add_parser(nome, help=resumo, description=resumo, epilog=_exemplos(*exemplos) if exemplos else None)
        _comuns(a, filho=True, no_input=no_input)
        return a

    p = comando("init", "cria a config desta máquina; sem --tool e --forge, num terminal, pergunta",
                ("fluxo init", "fluxo init --tool copilot --forge gitlab --gitlab-host gitlab.empresa.com.br"))
    # sem default nas flags: o assistente pergunta só o que não veio, e o cmd_init aplica os padrões
    p.add_argument("--tool", action="append", choices=cfgmod.TOOLS,
                   help="a ferramenta; repita para instalar várias (a primeira é a padrão do `fluxo task`)")
    p.add_argument("--forge", choices=cfgmod.EIXOS["forge"])
    p.add_argument("--base", help="branch de desenvolvimento (padrão: develop)")
    p.add_argument("--code-dir", help="onde ficam os clones (padrão: ~/code)")
    p.add_argument("--os", choices=cfgmod.EIXOS["os"], help="padrão: detectado")
    p.add_argument("--chat", choices=cfgmod.EIXOS["chat"], help="padrão: draft")
    p.add_argument("--tracker", choices=cfgmod.EIXOS["tracker"], help="padrão: none")
    p.add_argument("--engine", choices=cfgmod.ENGINES, help="Kiro: v2 (padrão do 2.x) ou v3")
    p.add_argument("--gitlab-host", help="ex.: gitlab.empresa.com.br")
    p.add_argument("--promote-to", help="módulo promote: as bases depois da --base, em ordem (ex.: staging,main)")
    p.add_argument("--force", action="store_true", help="refaz uma config que já existe")
    p.add_argument("--no-input", action="store_true",
                   help="nunca pergunta: sem --tool e --forge, sai com erro (também com CI definida, TERM=dumb ou num agente)")
    p.set_defaults(fn=cmd_init, modulos=None)

    p = comando("install", "grava instruções, agentes e skills no nível do usuário",
                ("fluxo install --dry-run   # mostra o que vai gravar", "fluxo install"))
    p.add_argument("--dry-run", action="store_true", help="só mostra o que faria")
    p.add_argument("--force", action="store_true", help="grava por cima de conflito, com backup")
    p.add_argument("-v", "--verbose", action="store_true", help="lista também os arquivos iguais")
    p.set_defaults(fn=cmd_install)

    p = comando("uninstall", "remove o que o fluxo instalou (o que você editou fica)")
    p.add_argument("--dry-run", action="store_true", help="só mostra o que faria")
    p.set_defaults(fn=cmd_uninstall)

    p = comando("doctor", "confere a máquina e a instalação")
    p.set_defaults(fn=cmd_doctor)

    p = comando("status", "o resumo da máquina: a config, os módulos, o install e o que está rodando (o `fluxo` sozinho)")
    p.set_defaults(fn=cmd_status)

    p = comando("package", "zip para levar a uma máquina sem git")
    p.add_argument("--output", help="pasta do zip (padrão: dist/)")
    p.set_defaults(fn=cmd_package)

    p = comando("modules", "lista os módulos; --enable e --disable editam a config (sem nome, num terminal, escolhe)",
                ("fluxo modules", "fluxo modules --enable task lock", "fluxo modules --enable   # num terminal: o menu"))
    p.add_argument("--enable", nargs="*", metavar="MODULE", help="liga; sem nome, escolhe entre os desligados")
    p.add_argument("--disable", nargs="*", metavar="MODULE", help="desliga; sem nome, escolhe entre os ligados")
    p.add_argument("--no-input", action="store_true", help=NO_INPUT)
    p.set_defaults(fn=cmd_modules)

    p = comando("config", "mostra e muda a config (a do fluxo e duas chaves do pr-reviewer); sem ação, num terminal, "
                "o menu", ("fluxo config", "fluxo config set task.max 3", "fluxo config set pr-reviewer.backend copilot",
                           "fluxo config get tools", "fluxo config edit"))
    p.add_argument("--no-input", action="store_true", help=NO_INPUT)
    p.set_defaults(fn=cmd_config, acao=None, chave=None, valores=[], json=False)
    g = acoes(p)
    a = acao(g, "list", "cada chave, com o valor e a origem (padrão, config ou pr-reviewer)")
    a.add_argument("--json", action="store_true", help="em JSON")
    a = acao(g, "get", "o valor de uma chave; sem valor nem padrão, sai com 1")
    a.add_argument("chave", metavar="KEY", help="ex.: task.max, models.copilot.strong, pr-reviewer.backend")
    a = acao(g, "set", "muda uma chave, validando a config inteira", no_input=True)
    a.add_argument("chave", metavar="KEY")
    a.add_argument("valores", nargs="+", metavar="VALUE", help="o valor; a lista em vários, ou a,b")
    a = acao(g, "unset", "volta a chave ao padrão", no_input=True)
    a.add_argument("chave", metavar="KEY")
    acao(g, "edit", "abre a config no editor ($VISUAL, $EDITOR ou vi) e só grava o que passa na validação", no_input=True)
    acao(g, "path", "os caminhos da config do fluxo e da do pr-reviewer")

    p = comando("task", "abre uma sessão da ferramenta numa janela do tmux (módulo task); sem ID, num terminal, pergunta",
                ("fluxo task PROJ-12                  # /planejar PROJ-12, numa vaga",
                 "fluxo task PROJ-12 -p '/implementar PROJ-12'", "fluxo task -p '/revisar'           # avulsa",
                 "fluxo task                          # num terminal: o menu", "fluxo task --list"))
    p.add_argument("id", nargs="?", help="ID da tarefa (ex.: a chave do card); sem ID é uma sessão avulsa")
    p.add_argument("-p", "--prompt", help="prompt inicial (com ID, o padrão é a skill planejar com o ID)")
    p.add_argument("-n", "--name", help="nome da sessão avulsa (padrão: o /comando, ou avulsa)")
    p.add_argument("--dir", help="pasta da sessão (padrão: code_dir)")
    p.add_argument("--target", help="sessão do tmux onde abrir (ex.: main:); obrigatório fora do tmux")
    p.add_argument("--force", action="store_true", help="abre mesmo sem vaga")
    p.add_argument("--dry-run", action="store_true", help="só mostra a janela que abriria")
    p.add_argument("--list", action="store_true", help="lista as sessões vivas abertas pelo fluxo")
    p.add_argument("--tool", choices=cfgmod.TOOLS, help="a ferramenta da sessão (padrão: a primeira de tools)")
    p.add_argument("--no-input", action="store_true", help=NO_INPUT)
    p.set_defaults(fn=cmd_task)

    p = comando("msg", "copia uma mensagem para a área de transferência (módulo messages)",
                ("fluxo msg tone", "fluxo msg copy aviso.md", "echo oi | fluxo msg copy"))
    p.set_defaults(fn=cmd_msg, arquivo=None)
    g = acoes(p, required=True)
    a = acao(g, "copy", "copia o arquivo, ou o stdin")
    a.add_argument("arquivo", nargs="?", metavar="FILE", help="arquivo com a mensagem (padrão: stdin)")
    acao(g, "tone", "cria o tom.md, para você descrever como escreve")

    p = comando("lock", "uma sessão por vez no stack local (módulo lock)",
                ("fluxo lock acquire --id PROJ-12 --project api-proj-12", "fluxo lock status", "fluxo lock release",
                 "fluxo lock run --id PROJ-12 -- docker compose up -d"))
    p.set_defaults(fn=cmd_lock, id=None, project=None, session=None, pid=None, wait=0, force=False)
    g = acoes(p, required=True)
    for nome, resumo in (("acquire", "pega a trava: 0 pegou (ou já era sua), 4 ocupada"),
                         ("run", "pega, roda o comando depois de -- e solta")):
        a = acao(g, nome, resumo)
        a.add_argument("--id", help="a tarefa que segura a trava")
        a.add_argument("--project", help="o projeto do docker compose (-p), para o break sugerir o down")
        a.add_argument("--session", help="identidade da sessão (padrão: FLUXO_SESSION, a do Claude, ou o pane)")
        a.add_argument("--pid", type=int, help="pid da ferramenta (padrão: o CLAUDE_PID, ou o avô deste processo)")
        a.add_argument("--wait", type=int, default=0, help="segundos esperando a trava (padrão: só tenta)")
    a = acao(g, "release", "solta a trava, se for desta sessão")
    a.add_argument("--session", help="identidade da sessão (padrão: FLUXO_SESSION, a do Claude, ou o pane)")
    acao(g, "status", "quem está com a trava")
    a = acao(g, "break", "quebra a trava de um dono morto")
    a.add_argument("--force", action="store_true", help="mesmo com o dono vivo")

    p = comando("watch", "avisa das suas PRs/MRs (módulo watch); sem ação, mostra o status",
                ("fluxo watch", "fluxo watch enable", "fluxo watch tick"))
    p.set_defaults(fn=cmd_watch, acao=None, interval=None, target=None)
    g = acoes(p)
    acao(g, "tick", "uma passada (o launchd, o systemd ou o loop do WSL chamam)")
    a = acao(g, "loop", "passadas sem parar (o WSL, numa janela do tmux)")
    a.add_argument("--interval", type=int, help="segundos entre as passadas (padrão: watch.interval)")
    a = acao(g, "enable", "agenda as passadas: launchctl no Mac, systemd no Linux, janela do tmux no WSL")
    a.add_argument("--target", help="WSL: a sessão do tmux onde abrir a janela do watch")
    acao(g, "disable", "tira o agendamento")

    p = comando("validate", "o pr-reviewer (módulo validate): revisa e executa a mudança antes da PR/MR; sem ação, num "
                "terminal, pergunta",
                ("fluxo validate enable                      # traz o pr-reviewer",
                 "fluxo validate start PROJ-12               # revisa em segundo plano",
                 "fluxo validate status PROJ-12 --wait 540",
                 "fluxo validate recipe api                  # o runbook do repo, neste terminal",
                 "fluxo validate recipe api --check", "fluxo validate doctor"))
    p.add_argument("--no-input", action="store_true", help=NO_INPUT)
    p.set_defaults(fn=cmd_validate, acao=None, alvo=None, profile="local", recipe=None, fix=False, check=False,
                   backend=None, wait=0)
    g = acoes(p)
    backend = dict(choices=cfgmod.TOOLS, help="a ferramenta dos agentes do pr-review (padrão: o backend da config dele)")
    a = acao(g, "enable", "clona o pr-reviewer na tag desta versão, ou leva o clone para ela, e cria a config dele")
    a.add_argument("--profile", default="local", help="o perfil da config do pr-reviewer que ele cria (padrão: local)")
    acao(g, "disable", "tira o clone que o fluxo trouxe")
    a = acao(g, "start", "revisa a tarefa, ou roda a receita do --recipe, em segundo plano", no_input=True)
    a.add_argument("alvo", nargs="?", metavar="ID", help="a tarefa (precisa ter plano)")
    a.add_argument("--recipe", metavar="REPO", help="a receita do repo, em vez da revisão da tarefa")
    a.add_argument("--fix", action="store_true", help="--recipe: refaz com o último --check que falhou")
    a.add_argument("--check", action="store_true", help="--recipe: sobe o stack com o runbook do perfil")
    a.add_argument("--backend", **backend)
    for nome, resumo in (("status", "acompanha uma execução: 0 terminou, 75 ainda rodando, 1 não há"),
                         ("stop", "cancela uma execução; o pr-review derruba o stack antes de sair")):
        a = acao(g, nome, resumo, no_input=True)
        a.add_argument("alvo", nargs="?", metavar="ID", help="a tarefa")
        a.add_argument("--recipe", metavar="REPO", help="a receita do repo")
        if nome == "status":
            a.add_argument("--wait", type=int, default=0, help="segundos esperando terminar (até 540)")
    a = acao(g, "recipe", "o runbook do repo, neste terminal: propõe, mostra e pergunta antes de gravar", no_input=True)
    a.add_argument("alvo", nargs="?", metavar="REPO", help="o repo (a pasta em code_dir); sem ele, num terminal, pergunta")
    a.add_argument("--check", action="store_true", help="sobe o stack com o runbook do perfil, sem agente")
    a.add_argument("--fix", action="store_true", help="refaz com o último --check que falhou")
    a.add_argument("--backend", **backend)
    a.add_argument("--wait", type=int, default=0, help="--check: segundos esperando a trava do stack")
    a = acao(g, "doctor", "o doctor do pr-reviewer: o binário, o login e a sonda de contenção (chama o agente)")
    a.add_argument("--backend", **backend)
    acao(g, "exec", "roda o pr-review com os argumentos depois de --")
    a = g.add_parser("_rodar")  # o processo de fundo que o start solta; sem help, fica fora da ajuda
    _comuns(a, filho=True)
    a.add_argument("alvo")

    p = sub.add_parser("hook")  # chamado pelas ferramentas; sem help, fica fora da ajuda
    p.add_argument("--tool", required=True)
    p.add_argument("--event", default="")
    p.set_defaults(fn=cmd_hook)

    p = comando("panel", "as sessões ao vivo (módulo panel); enable liga a statusline do Copilot e o agente do Kiro V2",
                ("fluxo panel", "fluxo panel --demo", "fluxo panel --text", "fluxo panel --view task --task PROJ-12"))
    p.add_argument("--text", action="store_true", help="imprime uma vez, sem a tela interativa")
    p.add_argument("--view", choices=[chave for chave, _ in vistas.ABAS],
                   help="a aba: session (a sessão seguida), task, global ou list (padrão)")
    p.add_argument("--task", help="a tarefa da aba task (padrão: a da sessão seguida, ou o plano mais novo)")
    p.add_argument("--follow", metavar="SESSION", help="o começo do id da sessão a seguir na aba session")
    p.add_argument("--demo", action="store_true", help="dados de mentira, para conferir o visual (não lê nem grava nada)")
    p.set_defaults(fn=cmd_panel, acao=None)
    g = acoes(p)
    acao(g, "enable", "Copilot: a statusline do fluxo (o gasto ao vivo); Kiro V2: o agente fluxo vira o padrão")
    acao(g, "disable", "Copilot: tira a statusline do fluxo; Kiro V2: volta o agente padrão de antes")

    p = comando("completion", "o script do completar com Tab, para o zsh ou o bash",
                ("fluxo completion zsh > ~/.zfunc/_fluxo   # e no .zshrc, antes do compinit: fpath=(~/.zfunc $fpath)",
                 "fluxo completion bash > ~/.local/share/bash-completion/completions/fluxo.bash"))
    p.set_defaults(fn=cmd_completion)
    g = acoes(p, required=True)
    acao(g, "zsh", "o _fluxo, para uma pasta do fpath (ou: source <(fluxo completion zsh))")
    acao(g, "bash", "para ~/.local/share/bash-completion/completions/fluxo.bash, que o bash-completion carrega sozinho")

    p = comando("scan", "procura dado de empresa no repo do fluxo (o pre-commit roda)")
    p.add_argument("--hash", metavar="TERM", help="só imprime o hash do termo, para o tests/proibidos.sha256")
    p.set_defaults(fn=cmd_scan)

    for nome, p in sub.choices.items():
        if nome not in ("scan", "package"):
            _comuns(p)
    largura = max(len(n) for n in resumos)
    ap.epilog = "\n\n".join(f"{grupo}:\n" + "\n".join(f"  {n:<{largura}}  {resumos[n]}" for n in nomes)
                            for grupo, nomes in GRUPOS)
    ap.epilog += "\n\nsem comando: o resumo da máquina (fluxo status). fluxo <comando> -h: a ajuda dele."
    return ap


def _hook(argv, raiz):
    """O hook nunca imprime e sempre sai com 0, nem com argumento que o parser recusa: entre um `git pull`
    e o próximo `fluxo install`, o hook instalado ainda manda as flags da versão anterior."""
    try:
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            args = parser().parse_args(argv)
            args.fn(args, Path(raiz))
    except BaseException:  # noqa: B036 — inclui o SystemExit do argparse
        pass
    return 0


def main(argv, raiz):
    argv, comando = list(argv), []
    if argv[:1] == ["hook"]:
        return _hook(argv, raiz)
    if argv[:1] == ["statusline"]:  # como o hook: nunca imprime nem falha (o script fluxo chega aqui antes)
        return uso.statusline(argv[1:])
    if argv[:1] == ["__complete"]:  # o Tab do shell: nunca imprime erro e sempre sai com 0, como o hook
        with contextlib.redirect_stderr(io.StringIO()):
            return completar.completar(parser(), argv[1:], sys.stdout, Path(raiz))
    if argv[:2] in (["lock", "run"], ["validate", "exec"]) and "--" in argv:  # `fluxo lock run --id X -- docker compose up`
        i = argv.index("--")
        argv, comando = argv[:i], argv[i + 1:]
    args = parser().parse_args(argv)
    args.comando = comando
    try:
        return args.fn(args, Path(raiz))
    except ERROS_DE_USO as exc:
        print(f"fluxo: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as exc:  # manifesto ilegível, arquivo que sumiu no meio, permissão
        print(f"fluxo: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:  # o Ctrl-C esperando a trava, ou fora de uma pergunta: sem traceback
        print("\nfluxo: interrompido", file=sys.stderr)
        return 130
