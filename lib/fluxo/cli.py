"""Linha de comando do fluxo.

Saídas: 0 ok; 1 erro; 2 config ou uso inválido; 3 conflito (nada foi gravado).
"""
import argparse
import json
import sys
from pathlib import Path

from . import config as cfgmod
from . import doctor, empacotar, fonte, instalar, montar, render, varredura

ERROS_DE_USO = (cfgmod.ErroConfig, montar.ErroMontagem, render.ErroTemplate, fonte.ErroFonte)


def _caminhos(args):
    return cfgmod.Caminhos(home=getattr(args, "home", None))


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

    p = sub.add_parser("varrer", help="procura dado de empresa no repo (o pre-commit roda)")
    p.add_argument("--hash", metavar="TERMO", help="só imprime o hash do termo, para o tests/proibidos.sha256")
    p.set_defaults(fn=cmd_varrer)

    for nome in ("init", "install", "uninstall", "doctor"):
        sub.choices[nome].add_argument("--home", help="instala dentro desta pasta em vez do HOME (teste)")
    return ap


def main(argv, raiz):
    args = parser().parse_args(argv)
    try:
        return args.fn(args, Path(raiz))
    except ERROS_DE_USO as exc:
        print(f"fluxo: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, OSError) as exc:  # manifesto ilegível, arquivo que sumiu no meio, permissão
        print(f"fluxo: {exc}", file=sys.stderr)
        return 1
