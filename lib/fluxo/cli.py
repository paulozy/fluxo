"""Linha de comando do fluxo.

Saídas: 0 ok; 1 erro; 2 config ou uso inválido; 3 conflito (nada foi gravado).
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod
from . import doctor, empacotar, fonte, instalar, lock, messages, montar, panel, render, task, varredura, vistas, watch

ERROS_DE_USO = (cfgmod.ErroConfig, montar.ErroMontagem, render.ErroTemplate, fonte.ErroFonte)


def _caminhos(args):
    return cfgmod.Caminhos(home=getattr(args, "home", None))


def _carregar(args, raiz, modulo=None):
    cam = _caminhos(args)
    cfg = cfgmod.carregar(cam, montar.modulos_existentes(raiz))
    if modulo and modulo not in cfg["modules"]:
        raise cfgmod.ErroConfig(f"o módulo {modulo} está desligado: fluxo modules --enable {modulo} && fluxo install")
    return cam, cfg


def _adaptador(raiz, tool):
    return json.loads((Path(raiz) / "adaptadores" / "ferramenta" / f"{tool}.json").read_text(encoding="utf-8"))


def cmd_modules(args, raiz):
    cam = _caminhos(args)
    existentes = montar.modulos_existentes(raiz)
    if args.enable or args.disable:
        try:
            bruto = json.loads(cam.config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise cfgmod.ErroConfig(f"sem config em {cam.config}: rode `fluxo init` antes")
        except ValueError as exc:
            raise cfgmod.ErroConfig(f"{cam.config} não é JSON válido: {exc}")
        ligados = [m for m in bruto.get("modules", []) if m not in (args.disable or [])]
        ligados = list(dict.fromkeys(ligados + list(args.enable or [])))  # sem repetir: duplicaria as instruções
        bruto["modules"] = ligados
        cfgmod.validar(bruto, existentes)  # módulo inexistente não chega ao arquivo
        tmp = cam.config.with_name(".config.json.tmp")
        tmp.write_text(json.dumps(bruto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, cam.config)
        print(f"módulos ligados: {', '.join(ligados) or 'nenhum'}. Agora: fluxo install")
        return 0
    cfg = cfgmod.carregar(cam, existentes)
    for m in existentes:
        meta = json.loads((Path(raiz) / "modulos" / m / "modulo.json").read_text(encoding="utf-8"))
        print(f"  [{'x' if m in cfg['modules'] else ' '}] {m:<10} {meta.get('descricao', '')}")
    return 0


def cmd_task(args, raiz):
    cam, cfg = _carregar(args, raiz, "task")
    if args.list:
        return task.listar(cam)
    tool = args.tool or cfg["tools"][0]
    if tool not in cfg["tools"]:  # sem as instruções e as skills do fluxo, a sessão não teria o planejar
        print(f"fluxo task: {tool} não está em tools ({', '.join(cfg['tools'])}); acrescente na config e rode "
              "fluxo install", file=sys.stderr)
        return 2
    pasta = Path(args.dir or os.path.expanduser(cfg["code_dir"]))
    try:
        return task.abrir(args.id, args.prompt, args.name, cfg, tool, _adaptador(raiz, tool), cam, pasta, alvo=args.target,
                          forcar=args.force, dry_run=args.dry_run, dentro_do_tmux=bool(os.environ.get("TMUX")),
                          skills=montar.nomes_de_skills(raiz))
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


def cmd_hook(args, raiz):
    """Chamado pela ferramenta em cada evento: não lê a config (que pode estar quebrada), nunca imprime,
    sempre sai com 0."""
    return panel.hook(_caminhos(args).estado, args.tool, args.event)


def cmd_panel(args, raiz):
    if args.acao and (args.view or args.task or args.follow):
        print("fluxo panel: --view, --task e --follow são da tela, e não de enable/disable", file=sys.stderr)
        return 2
    if args.task and not task.ID_RE.fullmatch(args.task):
        print(f"fluxo panel: id {args.task!r} inválido", file=sys.stderr)
        return 2
    # o disable vale com o módulo desligado: é o que devolve o agente padrão do Kiro depois de tirá-lo
    cam, cfg = _carregar(args, raiz, None if args.acao == "disable" else "panel")
    if args.acao in ("enable", "disable"):
        code, msg = (panel.ligar if args.acao == "enable" else panel.desligar)(cfg, cam, run=subprocess.run)
        print(msg, file=sys.stderr if code else sys.stdout)
        return code
    panel.podar(cam.estado)  # só o panel apaga eventos velhos: o hook nunca apaga nada
    tela = {"aba": args.view or "list", "tarefa": args.task, "seguida": args.follow, "fixada": bool(args.follow)}
    if args.text or not sys.stdout.isatty():
        print(panel.texto(cam, cfg["modules"], tela))
        return 0
    return panel.tela(cam, run=subprocess.run, ligados=cfg["modules"], inicial=tela)


def cmd_init(args, raiz):
    cam = _caminhos(args)
    if cam.config.exists() and not args.force:
        print(f"fluxo: {cam.config} já existe; --force para sobrescrever", file=sys.stderr)
        return 2
    anterior = {}
    if cam.config.exists():  # o --force refaz os eixos, mas não apaga os módulos nem as opções deles
        try:
            anterior = json.loads(cam.config.read_text(encoding="utf-8"))
        except ValueError:
            anterior = {}
    tools = list(dict.fromkeys(args.tool))  # `--tool copilot --tool claude`: a primeira é a padrão
    cfg = {"version": 1, "tools": tools, "forge": args.forge, "tracker": args.tracker,
           "chat": args.chat, "os": args.os or cfgmod.detectar_so(), "modules": [],
           "git": {"base": args.base}, "code_dir": args.code_dir, "models": {}}
    for chave in ("modules", "models", "task", "watch", "promote"):
        if isinstance(anterior, dict) and chave in anterior:
            cfg[chave] = anterior[chave]
    if "kiro" in tools:
        cfg["kiro"] = {"engine": args.engine}
    if args.forge == "gitlab":
        cfg["gitlab"] = {"host": args.gitlab_host or ""}
    if args.promote_to:
        cfg["promote"] = {"targets": [a.strip() for a in args.promote_to.split(",") if a.strip()]}
    try:
        cfgmod.validar(cfg, montar.modulos_existentes(raiz))
    except cfgmod.ErroConfig as exc:
        print(f"fluxo: {exc}", file=sys.stderr)
        return 2
    cam.config_dir.mkdir(parents=True, exist_ok=True)
    cam.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"config gravada em {cam.config}. Agora: fluxo install --dry-run")
    return 0


def _montar(args, raiz):
    cam = _caminhos(args)
    cfg = cfgmod.carregar(cam, montar.modulos_existentes(raiz))
    arquivos, avisos = montar.montar(cfg, raiz, cam)
    return cam, cfg, arquivos, avisos


def cmd_install(args, raiz):
    cam, cfg, arquivos, avisos = _montar(args, raiz)
    acoes = instalar.planejar(arquivos, instalar.ler_manifesto(cam.manifesto), cam.home)
    avisos += panel.avisos_do_install({a.rel for a in acoes if a.tipo == instalar.REMOVER}, cam,
                                      {a.rel for a in acoes if a.tipo in (instalar.CRIAR, instalar.ATUALIZAR)
                                       or (a.tipo == instalar.CONFLITO and args.force)})
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


def parser():
    ap = argparse.ArgumentParser(prog="fluxo", description="o fluxo de dev portátil (Claude Code, Copilot CLI, Kiro CLI, Codex CLI)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="cria a config desta máquina")
    p.add_argument("--tool", required=True, action="append", choices=cfgmod.TOOLS,
                   help="a ferramenta; repita para instalar várias (a primeira é a padrão do `fluxo task`)")
    p.add_argument("--forge", required=True, choices=cfgmod.EIXOS["forge"])
    p.add_argument("--base", default="develop", help="branch de desenvolvimento (padrão: develop)")
    p.add_argument("--code-dir", default="~/code", help="onde ficam os clones (padrão: ~/code)")
    p.add_argument("--os", choices=cfgmod.EIXOS["os"], help="padrão: detectado")
    p.add_argument("--chat", default="draft", choices=cfgmod.EIXOS["chat"])
    p.add_argument("--tracker", default="none", choices=cfgmod.EIXOS["tracker"])
    p.add_argument("--engine", default="v2", choices=cfgmod.ENGINES, help="Kiro: v2 (padrão do 2.x) ou v3")
    p.add_argument("--gitlab-host", help="ex.: gitlab.empresa.com.br")
    p.add_argument("--promote-to", help="módulo promote: as bases depois da --base, em ordem (ex.: staging,main)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("install", help="grava instruções, agentes e skills no nível do usuário")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="grava por cima de conflito, com backup")
    p.add_argument("-v", "--verbose", action="store_true", help="lista também os arquivos iguais")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("uninstall", help="remove o que o fluxo instalou (o que você editou fica)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_uninstall)

    p = sub.add_parser("doctor", help="confere a máquina e a instalação")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("package", help="zip para levar a uma máquina sem git")
    p.add_argument("--output", help="pasta do zip (padrão: dist/)")
    p.set_defaults(fn=cmd_package)

    p = sub.add_parser("modules", help="lista os módulos; --enable/--disable editam a config")
    p.add_argument("--enable", nargs="+", metavar="MODULE")
    p.add_argument("--disable", nargs="+", metavar="MODULE")
    p.set_defaults(fn=cmd_modules)

    p = sub.add_parser("task", help="abre uma sessão numa janela do tmux (módulo task)")
    p.add_argument("id", nargs="?", help="ID da tarefa (ex.: a chave do card); sem ID é uma sessão avulsa")
    p.add_argument("-p", "--prompt", help="prompt inicial (com ID, o padrão é a skill planejar com o ID)")
    p.add_argument("-n", "--name", help="nome da sessão avulsa (padrão: o /comando, ou avulsa)")
    p.add_argument("--dir", help="pasta da sessão (padrão: code_dir)")
    p.add_argument("--target", help="sessão do tmux onde abrir (ex.: main:); obrigatório fora do tmux")
    p.add_argument("--force", action="store_true", help="abre mesmo sem vaga")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--list", action="store_true", help="lista as sessões vivas abertas pelo fluxo")
    p.add_argument("--tool", choices=cfgmod.TOOLS, help="a ferramenta da sessão (padrão: a primeira de tools)")
    p.set_defaults(fn=cmd_task)

    p = sub.add_parser("msg", help="copia uma mensagem para a área de transferência (módulo messages)")
    p.add_argument("acao", choices=("copy", "tone"))
    p.add_argument("arquivo", nargs="?", metavar="FILE", help="arquivo com a mensagem (padrão: stdin)")
    p.set_defaults(fn=cmd_msg)

    p = sub.add_parser("lock", help="uma sessão por vez no stack local (módulo lock)")
    p.add_argument("acao", choices=("acquire", "release", "status", "break", "run"))
    p.add_argument("--id", help="a tarefa que segura a trava")
    p.add_argument("--project", help="o projeto do docker compose (-p), para o break sugerir o down")
    p.add_argument("--session", help="identidade da sessão (padrão: FLUXO_SESSION, a do Claude, ou o pane)")
    p.add_argument("--pid", type=int, help="pid da ferramenta (padrão: o CLAUDE_PID, ou o avô deste processo)")
    p.add_argument("--wait", type=int, default=0, help="segundos esperando a trava (padrão: só tenta)")
    p.add_argument("--force", action="store_true", help="break: mesmo com o dono vivo")
    p.set_defaults(fn=cmd_lock)

    p = sub.add_parser("watch", help="avisa das suas PRs/MRs (módulo watch); sem ação, mostra o status")
    p.add_argument("acao", nargs="?", choices=("tick", "loop", "enable", "disable"))
    p.add_argument("--interval", type=int, help="loop: segundos entre as passadas (padrão: watch.interval)")
    p.add_argument("--target", help="enable no WSL: sessão do tmux onde abrir a janela do watch")
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("hook", help="(chamado pelas ferramentas) grava um evento para o painel")
    p.add_argument("--tool", required=True)
    p.add_argument("--event", default="")
    p.set_defaults(fn=cmd_hook)

    p = sub.add_parser("panel", help="as sessões ao vivo (módulo panel); enable/disable no Kiro V2")
    p.add_argument("acao", nargs="?", choices=("enable", "disable"))
    p.add_argument("--text", action="store_true", help="imprime uma vez, sem a tela interativa")
    p.add_argument("--view", choices=[chave for chave, _ in vistas.ABAS],
                   help="a aba: session (a sessão seguida), task, global ou list (padrão)")
    p.add_argument("--task", help="a tarefa da aba task (padrão: a da sessão seguida, ou o plano mais novo)")
    p.add_argument("--follow", metavar="SESSION", help="o começo do id da sessão a seguir na aba session")
    p.set_defaults(fn=cmd_panel)

    p = sub.add_parser("scan", help="procura dado de empresa no repo (o pre-commit roda)")
    p.add_argument("--hash", metavar="TERM", help="só imprime o hash do termo, para o tests/proibidos.sha256")
    p.set_defaults(fn=cmd_scan)

    for nome in ("init", "install", "uninstall", "doctor", "modules", "task", "msg", "lock", "watch", "hook", "panel"):
        sub.choices[nome].add_argument("--home", help="usa esta pasta no lugar do HOME para config, dados e "
                                       "arquivos instalados (teste); o tmux e o ~ do code_dir continuam os reais")
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
    if argv[:2] == ["lock", "run"] and "--" in argv:  # `fluxo lock run --id X -- docker compose up`
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
