"""Monta, em memória, todos os arquivos que o `install` grava para as ferramentas configuradas.

É uma função pura: lê as fontes do repo e devolve (arquivos, avisos). Quem grava é o `instalar`.
"""
import importlib
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


def _pastas(raiz, modulos, sub):
    """A pasta do núcleo e as dos módulos ligados, nessa ordem."""
    raiz = Path(raiz)
    return [raiz / "nucleo" / sub] + [raiz / "modulos" / m / sub for m in modulos]


def _eixos(cfg, tool, raiz, modulos):
    engine = cfg["kiro"]["engine"] if tool == "kiro" else "-"
    eixos = {e: cfg[e] for e in cfgmod.EIXOS}
    eixos.update(tool=tool, engine=engine, module=set(modulos))
    validos = dict(cfgmod.EIXOS, tool=cfgmod.TOOLS, engine=cfgmod.ENGINES, module=modulos_existentes(raiz))
    return eixos, validos


def modulos_da_ferramenta(cfg, tool, raiz):
    """Os módulos ligados cujo `requer` a ferramenta atende (o panel, por exemplo, não existe no Claude)."""
    eixos, validos = _eixos(cfg, tool, raiz, cfg["modules"])
    out = []
    for m in cfg["modules"]:
        meta = _json(Path(raiz) / "modulos" / m / "modulo.json")
        if not meta.get("requer") or render.condicao(meta["requer"], eixos, validos, f"modulos/{m}/modulo.json"):
            out.append(m)
    return out


def contexto(cfg, tool, raiz, caminhos, modulos):
    adapt = _json(Path(raiz) / "adaptadores" / "ferramenta" / f"{tool}.json")
    forge = dict(_json(Path(raiz) / "adaptadores" / "forge" / f"{cfg['forge']}.json"))
    if cfg["forge"] == "gitlab":
        forge["host"] = ((cfg.get("gitlab") or {}).get("host") or "").strip()
        if not forge["host"]:  # sairia `glab api --hostname  "projects/…"`, e o endpoint viraria o host
            raise ErroMontagem("gitlab.host está vazio: fluxo init --forge gitlab --gitlab-host <host> --force")
    eixos, validos = _eixos(cfg, tool, raiz, modulos)
    ctx = {"forge": forge, "git": {"base": cfg["git"]["base"]}, "codigo_dir": cfg["code_dir"],
           "dados": caminhos.exibir(caminhos.dados), "dados_sh": caminhos.exibir(caminhos.dados, shell=True),
           "config": caminhos.exibir(caminhos.config_dir), "config_sh": caminhos.exibir(caminhos.config_dir, shell=True),
           "promover": {"alvos": ", ".join(map(str, cfg["promote"]["targets"] or ["main"])),
                        "ultimo": str((cfg["promote"]["targets"] or ["main"])[-1])},
           "ferramenta": {"nome": adapt["nome"], "id": tool}}
    ctx.update(adapt["vars"])
    return adapt, ctx, eixos, validos


def _traduzir(abstratas, tabela, onde, avisos, nome):
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
        avisos.append(f"{onde}: sem equivalente no {nome} para {', '.join(faltam)} (omitido)")
    return nativas


def _codigo(m):
    """O lib/fluxo/<módulo>.py, ou None para um módulo só de fontes (promote, reviewer)."""
    try:
        return importlib.import_module(f"fluxo.{m}")
    except ModuleNotFoundError as exc:
        if exc.name != f"fluxo.{m}":  # o módulo existe, mas importa algo que falta: é bug
            raise
        return None


def montar(cfg, raiz, caminhos):
    """Uma montagem por ferramenta de `tools`, mais o que é da máquina (o wrapper e o agendamento do
    watch). Destino repetido é erro, mesmo com bytes iguais: esconderia um arquivo gerado duas vezes."""
    raiz = Path(raiz)
    por_ferramenta = [(t, modulos_da_ferramenta(cfg, t, raiz)) for t in cfg["tools"]]
    for m in cfg["modules"]:
        if not any(m in mods for _, mods in por_ferramenta):
            requer = _json(raiz / "modulos" / m / "modulo.json")["requer"]
            raise ErroMontagem(f"o módulo {m} requer {requer}, e esta config não atende")
    arquivos, avisos = [], []
    for tool, modulos in por_ferramenta:
        mais, mais_avisos = _montar_ferramenta(cfg, tool, modulos, raiz, caminhos)
        arquivos += mais
        avisos += [a for a in mais_avisos if a not in avisos]
    arquivos.append(wrapper(raiz))
    for m in cfg["modules"]:  # arquivos da máquina (o agendamento do watch)
        codigo = _codigo(m)
        if hasattr(codigo, "arquivos_extras"):
            extras, mais_avisos = codigo.arquivos_extras(cfg, Arquivo)
            arquivos += extras
            avisos += [a for a in mais_avisos if a not in avisos]
    vistos = set()
    for a in arquivos:
        if a.rel in vistos:
            raise ErroMontagem(f"dois arquivos para o mesmo destino: {a.rel}")
        vistos.add(a.rel)
    return arquivos, avisos


def _montar_ferramenta(cfg, tool, modulos, raiz, caminhos):
    adapt, ctx, eixos, validos = contexto(cfg, tool, raiz, caminhos, modulos)
    motor = eixos["engine"]
    tabela = adapt["tools"][motor] if tool == "kiro" else adapt["tools"]
    rend = RENDERIZADORES[tool]
    avisos, arquivos = [], []

    def tpl(texto, onde):
        return render.renderizar(texto, ctx, eixos, validos, onde)

    agentes = []
    for pasta in _pastas(raiz, modulos, "agentes"):
        for p in sorted(pasta.glob("*.md")):
            onde = p.relative_to(raiz).as_posix()
            meta, corpo = fonte.ler(p.read_text(encoding="utf-8"), onde)
            for chave in ("name", "description", "tools", "model"):
                if chave not in meta:
                    raise ErroMontagem(f"{onde}: falta `{chave}` no frontmatter")
            if meta["name"] != p.stem:
                raise ErroMontagem(f"{onde}: name {meta['name']!r} difere do nome do arquivo")
            if meta["model"] not in cfgmod.NIVEIS:
                raise ErroMontagem(f"{onde}: model {meta['model']!r} não é um de {', '.join(cfgmod.NIVEIS)}")
            agentes.append({
                "name": meta["name"], "description": meta["description"],
                "tools": _traduzir(meta["tools"], tabela, onde, avisos, adapt["nome"]),
                "model": cfg["models"].get(tool, {}).get(meta["model"]) or adapt["modelos"].get(meta["model"]),
                "corpo": tpl(corpo, onde).strip() + "\n", "fonte": onde,
            })

    skills = []
    for pasta in _pastas(raiz, modulos, "skills"):
        for p in sorted(pasta.glob("*/SKILL.md")):
            onde = p.relative_to(raiz).as_posix()
            meta, corpo = fonte.ler(p.read_text(encoding="utf-8"), onde)
            if meta.get("name") != p.parent.name:
                raise ErroMontagem(f"{onde}: o name tem que ser igual ao nome da pasta ({p.parent.name})")
            if not meta.get("description") or len(meta["description"]) > 1024:
                raise ErroMontagem(f"{onde}: description obrigatória, com até 1024 caracteres")
            skills.append({"name": meta["name"], "description": meta["description"],
                           "argument-hint": meta.get("argument-hint"),
                           "corpo": CABECALHO.format(fonte=onde) + tpl(corpo, onde).lstrip("\n")})

    partes = []
    for pasta in _pastas(raiz, modulos, "instrucoes"):
        partes += list(pasta.glob("[0-9][0-9]-*.md"))
    partes.sort(key=lambda p: p.name)  # o prefixo NN intercala núcleo e módulos
    textos = [tpl(p.read_text(encoding="utf-8"), p.relative_to(raiz).as_posix()).strip() for p in partes]
    textos.append(lista_de_agentes(agentes))
    instrucoes = CABECALHO.format(fonte="nucleo/instrucoes") + "\n" + "\n\n".join(textos) + "\n"

    arquivos += rend.instrucoes(instrucoes, Arquivo)
    arquivos += [rend.agente(a, Arquivo, motor=motor, permitidas=adapt.get("permitidas", {}).get(motor, []))
                 for a in agentes]
    arquivos += [rend.skill(s, Arquivo) for s in skills]
    for m in modulos:  # arquivos da ferramenta (os hooks e o agente do panel)
        codigo = _codigo(m)
        if hasattr(codigo, "arquivos_da_ferramenta"):
            extras, mais_avisos = codigo.arquivos_da_ferramenta(cfg, tool, Arquivo, agentes=agentes, skills=skills)
            arquivos += extras
            avisos += mais_avisos
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
