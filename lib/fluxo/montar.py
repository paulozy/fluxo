"""Monta, em memória, todos os arquivos que o `install` grava para a ferramenta configurada.

É uma função pura: lê as fontes do repo e devolve (arquivos, avisos). Quem grava é o `instalar`.
"""
import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from . import config as cfgmod
from . import fonte, render
from .ferramentas import claude, copilot, kiro

RENDERIZADORES = {"claude": claude, "copilot": copilot, "kiro": kiro}
TOOLS_ABSTRATAS = ("read", "search", "edit", "shell", "web", "agent")
CABECALHO = "<!-- gerado pelo fluxo a partir de {fonte}: edite a fonte e rode `fluxo install` -->\n"


@dataclass
class Arquivo:
    rel: str  # relativo ao HOME, com /
    conteudo: bytes
    modo: int = 0o644


class ErroMontagem(Exception):
    pass


def modulos_existentes(raiz):
    return tuple(sorted(p.parent.name for p in (Path(raiz) / "modulos").glob("*/modulo.json")))


def _json(caminho):
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def _pastas(raiz, cfg, sub):
    """A pasta do núcleo e as dos módulos ligados, nessa ordem."""
    raiz = Path(raiz)
    return [raiz / "nucleo" / sub] + [raiz / "modulos" / m / sub for m in cfg["modulos"]]


def contexto(cfg, raiz, caminhos):
    ferr = cfg["ferramenta"]
    adapt = _json(Path(raiz) / "adaptadores" / "ferramenta" / f"{ferr}.json")
    forge = dict(_json(Path(raiz) / "adaptadores" / "forge" / f"{cfg['forge']}.json"))
    if cfg["forge"] == "gitlab":
        forge["host"] = (cfg.get("gitlab") or {}).get("host", "")
    motor = cfg["kiro"]["motor"] if ferr == "kiro" else "-"
    eixos = {e: cfg[e] for e in cfgmod.EIXOS}
    eixos.update(motor=motor, modulo=set(cfg["modulos"]))
    validos = dict(cfgmod.EIXOS, motor=cfgmod.MOTORES, modulo=modulos_existentes(raiz))
    ctx = {"forge": forge, "git": {"base": cfg["git"]["base"]}, "codigo_dir": cfg["codigo_dir"],
           "dados": caminhos.exibir(caminhos.dados), "dados_sh": caminhos.exibir(caminhos.dados, shell=True),
           "ferramenta": {"nome": adapt["nome"]}}
    ctx.update(adapt["vars"])
    return adapt, ctx, eixos, validos


def _traduzir(abstratas, tabela, onde, avisos):
    nativas, faltam = [], []
    for t in abstratas:
        if t not in TOOLS_ABSTRATAS:
            raise ErroMontagem(f"{onde}: tool {t!r} não é uma de {', '.join(TOOLS_ABSTRATAS)}")
        if t not in tabela:
            faltam.append(t)
        for n in tabela.get(t, []):
            if n not in nativas:
                nativas.append(n)
    if faltam:
        avisos.append(f"{onde}: sem equivalente na ferramenta para {', '.join(faltam)} (omitido)")
    return nativas


def montar(cfg, raiz, caminhos):
    raiz = Path(raiz)
    adapt, ctx, eixos, validos = contexto(cfg, raiz, caminhos)
    ferr = cfg["ferramenta"]
    motor = eixos["motor"]
    tabela = adapt["tools"][motor] if ferr == "kiro" else adapt["tools"]
    rend = RENDERIZADORES[ferr]
    avisos, arquivos = [], []

    def tpl(texto, onde):
        return render.renderizar(texto, ctx, eixos, validos, onde)

    agentes = []
    for pasta in _pastas(raiz, cfg, "agentes"):
        for p in sorted(pasta.glob("*.md")):
            onde = p.relative_to(raiz).as_posix()
            meta, corpo = fonte.ler(p.read_text(encoding="utf-8"), onde)
            for chave in ("name", "description", "tools", "modelo"):
                if chave not in meta:
                    raise ErroMontagem(f"{onde}: falta `{chave}` no frontmatter")
            if meta["name"] != p.stem:
                raise ErroMontagem(f"{onde}: name {meta['name']!r} difere do nome do arquivo")
            if meta["modelo"] not in cfgmod.NIVEIS:
                raise ErroMontagem(f"{onde}: modelo {meta['modelo']!r} não é um de {', '.join(cfgmod.NIVEIS)}")
            agentes.append({
                "name": meta["name"], "description": meta["description"],
                "tools": _traduzir(meta["tools"], tabela, onde, avisos),
                "model": cfg["modelos"].get(meta["modelo"]) or adapt["modelos"].get(meta["modelo"]),
                "corpo": tpl(corpo, onde).strip() + "\n", "fonte": onde,
            })

    skills = []
    for pasta in _pastas(raiz, cfg, "skills"):
        for p in sorted(pasta.glob("*/SKILL.md")):
            onde = p.relative_to(raiz).as_posix()
            meta, corpo = fonte.ler(p.read_text(encoding="utf-8"), onde)
            if meta.get("name") != p.parent.name:
                raise ErroMontagem(f"{onde}: o name tem que ser igual ao nome da pasta ({p.parent.name})")
            if not meta.get("description") or len(meta["description"]) > 1024:
                raise ErroMontagem(f"{onde}: description obrigatória, com até 1024 caracteres")
            if meta.get("requer") and not render.condicao(meta["requer"], eixos, validos, onde):
                continue  # ex.: `requer: forge:github` numa máquina com GitLab
            skills.append({"name": meta["name"], "description": meta["description"],
                           "argument-hint": meta.get("argument-hint"),
                           "corpo": CABECALHO.format(fonte=onde) + tpl(corpo, onde).lstrip("\n")})

    partes = []
    for pasta in _pastas(raiz, cfg, "instrucoes"):
        partes += list(pasta.glob("[0-9][0-9]-*.md"))
    partes.sort(key=lambda p: p.name)  # o prefixo NN intercala núcleo e módulos
    textos = [tpl(p.read_text(encoding="utf-8"), p.relative_to(raiz).as_posix()).strip() for p in partes]
    textos.append(lista_de_agentes(agentes))
    instrucoes = CABECALHO.format(fonte="nucleo/instrucoes") + "\n" + "\n\n".join(textos) + "\n"

    arquivos += rend.instrucoes(instrucoes, Arquivo)
    arquivos += [rend.agente(a, Arquivo, motor=motor, permitidas=adapt.get("permitidas", {}).get(motor, []))
                 for a in agentes]
    arquivos += [rend.skill(s, Arquivo) for s in skills]
    arquivos.append(wrapper(raiz))
    vistos = set()
    for a in arquivos:
        if a.rel in vistos:
            raise ErroMontagem(f"dois arquivos para o mesmo destino: {a.rel}")
        vistos.add(a.rel)
    return arquivos, avisos


def lista_de_agentes(agentes):
    linhas = ["## Subagentes", "",
              "Use-os para manter a conversa enxuta: a saída longa (pesquisa, testes, revisão) fica com eles.", ""]
    for a in agentes:
        primeira = a["description"].split(". ")[0].rstrip(".")
        linhas.append(f"- `{a['name']}`: {primeira}.")
    return "\n".join(linhas)


def wrapper(raiz):
    """~/.local/bin/fluxo aponta para a cópia de onde o install rodou (o clone, ou o zip extraído)."""
    alvo = shlex.quote(str((Path(raiz) / "fluxo").resolve()))
    texto = f"#!/bin/sh\n# gerado pelo `fluxo install`: roda a cópia de onde ele foi instalado\nexec python3 {alvo} \"$@\"\n"
    return Arquivo(".local/bin/fluxo", texto.encode(), 0o755)
