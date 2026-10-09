"""Linha de comando do fluxo.

Saídas: 0 ok; 1 erro; 2 config ou uso inválido; 3 conflito (nada foi gravado).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from . import config as cfgmod
from . import doctor, empacotar, fonte, instalar, mensagens, montar, painel, render, task, trava, varredura, vigia

ERROS_DE_USO = (cfgmod.ErroConfig, montar.ErroMontagem, render.ErroTemplate, fonte.ErroFonte)


def _caminhos(args):
    return cfgmod.Caminhos(home=getattr(args, "home", None))


def _carregar(args, raiz, modulo=None):
    cam = _caminhos(args)
    cfg = cfgmod.carregar(cam, montar.modulos_existentes(raiz))
    if modulo and modulo not in cfg["modulos"]:
        raise cfgmod.ErroConfig(f"o módulo {modulo} está desligado: fluxo modulos --ligar {modulo} && fluxo install")
    return cam, cfg


def _adaptador(raiz, cfg):
    return json.loads((Path(raiz) / "adaptadores" / "ferramenta" / f"{cfg['ferramenta']}.json").read_text(encoding="utf-8"))


def cmd_modulos(args, raiz):
    cam = _caminhos(args)
    existentes = montar.modulos_existentes(raiz)
    if args.ligar or args.desligar:
        try:
            bruto = json.loads(cam.config.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise cfgmod.ErroConfig(f"sem config em {cam.config}: rode `fluxo init` antes")
        except ValueError as exc:
            raise cfgmod.ErroConfig(f"{cam.config} não é JSON válido: {exc}")
        ligados = [m for m in bruto.get("modulos", []) if m not in (args.desligar or [])]
        ligados = list(dict.fromkeys(ligados + list(args.ligar or [])))  # sem repetir: duplicaria as instruções
        bruto["modulos"] = ligados
        cfgmod.validar(bruto, existentes)  # módulo inexistente não chega ao arquivo
        tmp = cam.config.with_name(".config.json.tmp")
        tmp.write_text(json.dumps(bruto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, cam.config)
        print(f"módulos ligados: {', '.join(ligados) or 'nenhum'}. Agora: fluxo install")
        return 0
    cfg = cfgmod.carregar(cam, existentes)
    for m in existentes:
        meta = json.loads((Path(raiz) / "modulos" / m / "modulo.json").read_text(encoding="utf-8"))
        print(f"  [{'x' if m in cfg['modulos'] else ' '}] {m:<10} {meta.get('descricao', '')}")
    return 0


def cmd_task(args, raiz):
    cam, cfg = _carregar(args, raiz, "task")
    if args.lista:
        return task.listar(cam)
    pasta = Path(args.dir or os.path.expanduser(cfg["codigo_dir"]))
    try:
        return task.abrir(args.id, args.prompt, args.nome, cfg, _adaptador(raiz, cfg), cam, pasta, alvo=args.target,
                          forcar=args.force, dry_run=args.dry_run, dentro_do_tmux=bool(os.environ.get("TMUX")))
    except task.ErroUso as exc:
        print(f"fluxo task: {exc}", file=sys.stderr)
        return 2


def cmd_msg(args, raiz):
    cam, cfg = _carregar(args, raiz, "mensagens")
    if args.acao == "tom":
        destino, criado = mensagens.criar_tom(cam.config_dir)
        print(f"{'criado' if criado else 'já existe'}: {destino}. Cole ali umas 20 mensagens suas, no fim, em 'Exemplos seus'.")
        return 0
    texto = sys.stdin.read() if args.arquivo in (None, "-") else Path(args.arquivo).read_text(encoding="utf-8")
    if not texto.strip():
        print("fluxo msg: mensagem vazia", file=sys.stderr)
        return 2
    salvo = mensagens.guardar(texto, cam.dados)
    copiou, como = mensagens.copiar(texto, cfg["so"])
    if copiou:
        print(f"copiado ({como}); cópia em {salvo}")
        return 0
    print(f"fluxo msg: não copiei ({como}); o texto está em {salvo}", file=sys.stderr)
    return 1


def cmd_trava(args, raiz):
    cam, cfg = _carregar(args, raiz, "trava")
    t = trava.Trava(cam.estado)
    if args.acao == "status":
        dono = t.ler()
        print(trava.descrever(dono, bool(dono) and t.vivo(dono)))
        return 0
    if args.acao == "quebrar":
        ok, dono = t.quebrar(forcar=args.forcar)
        if not ok:
            print(f"fluxo trava: {trava.descrever(dono, True)}; o dono está vivo (--forcar quebra assim mesmo)", file=sys.stderr)
            return 1
        if dono and dono.get("projeto"):
            print(f"trava quebrada. Se o stack dele ficou de pé: docker compose -p {dono['projeto']} down -v")
        else:
            print("trava quebrada" if dono else "a trava já estava livre")
        return 0
    pane, pane_pid = trava.pane_atual()
    try:
        sessao = trava.quem(args.sessao, pane=pane, pane_pid=pane_pid)
    except trava.ErroTrava as exc:
        print(f"fluxo trava: {exc}", file=sys.stderr)
        return 2
    if args.acao == "soltar":
        ok, dono = t.soltar(sessao)
        if not ok:
            print(f"fluxo trava: a trava é de outra sessão ({trava.descrever(dono, True)})", file=sys.stderr)
            return 1
        print("trava solta" if dono else "a trava já estava livre")
        return 0
    pid = args.pid if args.pid is not None else trava.pid_da_ferramenta()
    kw = dict(sessao=sessao, pane=pane, pane_pid=pane_pid, pid=pid, id_=args.id, projeto=args.projeto)
    try:
        if args.acao == "rodar":
            comando = args.comando
            if not comando:
                print("fluxo trava rodar: falta o comando depois de --", file=sys.stderr)
                return 2
            return trava.rodar(t, comando, args.esperar, **kw)
        ok, dono, _ = trava.pegar_esperando(t, args.esperar, **kw)
    except trava.ErroTrava as exc:
        print(f"fluxo trava: {exc}", file=sys.stderr)
        return 2
    print(("trava sua: " if ok else "") + trava.descrever(dono, True))
    return 0 if ok else trava.OCUPADA


def cmd_vigia(args, raiz):
    cam, cfg = _carregar(args, raiz, "vigia")
    if args.acao is None:
        print(vigia.status(cam))
        return 0
    if args.acao == "tick":
        code, resumo = vigia.tick(cfg, cam)
        print(resumo)
        return code
    if args.acao == "loop":
        return vigia.loop(cfg, cam, args.intervalo or cfg["vigia"]["intervalo"])
    if args.acao == "ligar":
        code, msg = vigia.ligar(cfg, cam, alvo=args.target, dentro_do_tmux=bool(os.environ.get("TMUX")))
    else:
        code, msg = vigia.desligar(cfg, cam)
    print(msg, file=sys.stderr if code else sys.stdout)
    return code


def cmd_hook(args, raiz):
    """Chamado pela ferramenta em cada evento: não lê a config (que pode estar quebrada), nunca imprime,
    sempre sai com 0."""
    return painel.hook(_caminhos(args).estado, args.ferramenta, args.evento)


def cmd_painel(args, raiz):
    cam, cfg = _carregar(args, raiz, "painel")
    if args.acao in ("ligar", "desligar"):
        code, msg = (painel.ligar if args.acao == "ligar" else painel.desligar)(cfg, cam, run=subprocess.run)
        print(msg, file=sys.stderr if code else sys.stdout)
        return code
    if args.texto or not sys.stdout.isatty():
        print("\n".join(painel.linhas_texto(painel.ver(cam))))
        return 0
    return painel.tela(cam, run=subprocess.run)


def cmd_init(args, raiz):
    cam = _caminhos(args)
    if cam.config.exists() and not args.forcar:
        print(f"fluxo: {cam.config} já existe; --forcar para sobrescrever", file=sys.stderr)
        return 2
    cfg = {"versao": 1, "ferramenta": args.ferramenta, "forge": args.forge, "tracker": args.tracker,
           "chat": args.chat, "so": args.so or cfgmod.detectar_so(), "modulos": [],
           "git": {"base": args.base}, "codigo_dir": args.codigo_dir, "modelos": {}}
    if args.ferramenta == "kiro":
        cfg["kiro"] = {"motor": args.motor}
    if args.forge == "gitlab":
        cfg["gitlab"] = {"host": args.gitlab_host or ""}
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
    for a in avisos:
        print(f"aviso: {a}")
    print(f"fluxo install ({cfg['ferramenta']}{' ' + cfg['kiro']['motor'] if cfg['ferramenta'] == 'kiro' else ''}, "
          f"{cfg['forge']}) em {cam.home}:")
    print(instalar.resumo(acoes, mostrar_iguais=args.verboso))
    conflitos = [a for a in acoes if a.tipo == instalar.CONFLITO]
    if args.dry_run:
        print("dry-run: nada foi gravado")
        return 3 if conflitos and not args.forcar else 0
    try:
        instalar.aplicar(acoes, cam.home, cam.manifesto, forcar=args.forcar)
    except instalar.ConflitoErro as exc:
        print(f"fluxo: {exc}. Nada foi gravado. Confira os arquivos acima; --forcar grava com backup "
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


def cmd_varrer(args, raiz):
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


def cmd_empacotar(args, raiz):
    try:
        destino = empacotar.empacotar(raiz, args.saida)
    except RuntimeError as exc:
        print(f"fluxo: {exc}", file=sys.stderr)
        return 1
    print(f"{destino}\nNa outra máquina: python3 -m zipfile -e {destino.name} ~/fluxo-src && "
          "python3 ~/fluxo-src/fluxo/fluxo init … && python3 ~/fluxo-src/fluxo/fluxo install")
    return 0


def parser():
    ap = argparse.ArgumentParser(prog="fluxo", description="o fluxo de dev portátil (Claude Code, Copilot CLI, Kiro CLI)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="cria a config desta máquina")
    p.add_argument("--ferramenta", required=True, choices=cfgmod.EIXOS["ferramenta"])
    p.add_argument("--forge", required=True, choices=cfgmod.EIXOS["forge"])
    p.add_argument("--base", default="develop", help="branch de desenvolvimento (padrão: develop)")
    p.add_argument("--codigo-dir", default="~/code", help="onde ficam os clones (padrão: ~/code)")
    p.add_argument("--so", choices=cfgmod.EIXOS["so"], help="padrão: detectado")
    p.add_argument("--chat", default="rascunho", choices=cfgmod.EIXOS["chat"])
    p.add_argument("--tracker", default="nenhum", choices=cfgmod.EIXOS["tracker"])
    p.add_argument("--motor", default="v2", choices=cfgmod.MOTORES, help="Kiro: v2 (padrão do 2.x) ou v3")
    p.add_argument("--gitlab-host", help="ex.: gitlab.empresa.com.br")
    p.add_argument("--forcar", action="store_true")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("install", help="grava instruções, agentes e skills no nível do usuário")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--forcar", action="store_true", help="grava por cima de conflito, com backup")
    p.add_argument("-v", "--verboso", action="store_true", help="lista também os arquivos iguais")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("uninstall", help="remove o que o fluxo instalou (o que você editou fica)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_uninstall)

    p = sub.add_parser("doctor", help="confere a máquina e a instalação")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("empacotar", help="zip para levar a uma máquina sem git")
    p.add_argument("--saida", help="pasta do zip (padrão: dist/)")
    p.set_defaults(fn=cmd_empacotar)

    p = sub.add_parser("modulos", help="lista os módulos; --ligar/--desligar editam a config")
    p.add_argument("--ligar", nargs="+", metavar="MODULO")
    p.add_argument("--desligar", nargs="+", metavar="MODULO")
    p.set_defaults(fn=cmd_modulos)

    p = sub.add_parser("task", help="abre uma sessão numa janela do tmux (módulo task)")
    p.add_argument("id", nargs="?", help="ID da tarefa (ex.: a chave do card); sem ID é uma sessão avulsa")
    p.add_argument("-p", "--prompt", help="prompt inicial (com ID, o padrão é /planejar <ID>)")
    p.add_argument("-n", "--nome", help="nome da sessão avulsa (padrão: o /comando, ou avulsa)")
    p.add_argument("--dir", help="pasta da sessão (padrão: codigo_dir)")
    p.add_argument("--target", help="sessão do tmux onde abrir (ex.: main:); obrigatório fora do tmux")
    p.add_argument("--force", action="store_true", help="abre mesmo sem vaga")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--lista", action="store_true", help="lista as sessões vivas abertas pelo fluxo")
    p.set_defaults(fn=cmd_task)

    p = sub.add_parser("msg", help="copia uma mensagem para a área de transferência (módulo mensagens)")
    p.add_argument("acao", choices=("copiar", "tom"))
    p.add_argument("arquivo", nargs="?", help="arquivo com a mensagem (padrão: stdin)")
    p.set_defaults(fn=cmd_msg)

    p = sub.add_parser("trava", help="uma sessão por vez no stack local (módulo trava)")
    p.add_argument("acao", choices=("pegar", "soltar", "status", "quebrar", "rodar"))
    p.add_argument("--id", help="a tarefa que segura a trava")
    p.add_argument("--projeto", help="o projeto do docker compose (-p), para o quebrar sugerir o down")
    p.add_argument("--sessao", help="identidade da sessão (padrão: FLUXO_SESSAO, a do Claude, ou o pane)")
    p.add_argument("--pid", type=int, help="pid da ferramenta (padrão: o CLAUDE_PID, ou o avô deste processo)")
    p.add_argument("--esperar", type=int, default=0, help="segundos esperando a trava (padrão: só tenta)")
    p.add_argument("--forcar", action="store_true", help="quebrar: mesmo com o dono vivo")
    p.set_defaults(fn=cmd_trava)

    p = sub.add_parser("vigia", help="avisa das suas PRs/MRs (módulo vigia); sem ação, mostra o status")
    p.add_argument("acao", nargs="?", choices=("tick", "loop", "ligar", "desligar"))
    p.add_argument("--intervalo", type=int, help="loop: segundos entre as passadas (padrão: vigia.intervalo)")
    p.add_argument("--target", help="ligar no WSL: sessão do tmux onde abrir a janela do vigia")
    p.set_defaults(fn=cmd_vigia)

    p = sub.add_parser("hook", help="(chamado pelas ferramentas) grava um evento para o painel")
    p.add_argument("--ferramenta", required=True)
    p.add_argument("--evento", default="")
    p.set_defaults(fn=cmd_hook)

    p = sub.add_parser("painel", help="as sessões ao vivo (módulo painel); ligar/desligar no Kiro V2")
    p.add_argument("acao", nargs="?", choices=("ligar", "desligar"))
    p.add_argument("--texto", action="store_true", help="imprime uma vez, sem a tela interativa")
    p.set_defaults(fn=cmd_painel)

    p = sub.add_parser("varrer", help="procura dado de empresa no repo (o pre-commit roda)")
    p.add_argument("--hash", metavar="TERMO", help="só imprime o hash do termo, para o tests/proibidos.sha256")
    p.set_defaults(fn=cmd_varrer)

    for nome in ("init", "install", "uninstall", "doctor", "modulos", "task", "msg", "trava", "vigia", "hook", "painel"):
        sub.choices[nome].add_argument("--home", help="usa esta pasta no lugar do HOME para config, dados e "
                                       "arquivos instalados (teste); o tmux e o ~ do codigo_dir continuam os reais")
    return ap


def main(argv, raiz):
    argv, comando = list(argv), []
    if argv[:2] == ["trava", "rodar"] and "--" in argv:  # `fluxo trava rodar --id X -- docker compose up`
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
