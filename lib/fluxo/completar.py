"""O completar com Tab, no modelo do cobra/gh: o script do shell é fino e pergunta ao próprio fluxo
(`fluxo __complete <palavras>`), que lê o parser e os arquivos locais. Assim o script não envelhece a cada
`git pull`. A saída é `valor\\tdescrição` por linha (https://github.com/spf13/cobra, site/content/completions).

O `__complete` nunca imprime erro e sempre sai com 0, como o hook: roda a cada Tab, sem rede e sem menu."""
import argparse
from pathlib import Path

from . import ajustes, montar, task, validate
from . import config as cfgmod

ZSH = r'''#compdef fluxo
# gerado por `fluxo completion zsh`: as opções vêm do próprio fluxo (fluxo __complete), e não envelhecem
compdef _fluxo fluxo

_fluxo() {
  local -a itens
  local linha valor desc
  while IFS= read -r linha; do
    [[ -z $linha ]] && continue
    valor=${linha%%$'\t'*}
    desc=${linha#*$'\t'}
    [[ $desc == "$linha" ]] && desc=""
    itens+=("${valor//:/\\:}${desc:+:$desc}")
  done < <(fluxo __complete "${(@)words[2,CURRENT]}" 2>/dev/null)
  (( ${#itens} )) && _describe -t valores fluxo itens
}

# carregado pelo fpath (o compinit), roda; com source, só registra
if [ "$funcstack[1]" = "_fluxo" ]; then
  _fluxo "$@"
fi
'''

BASH = r'''# gerado por `fluxo completion bash`: as opções vêm do próprio fluxo (fluxo __complete), e não envelhecem
# sem mapfile: o bash do macOS é o 3.2
_fluxo() {
  local linha
  COMPREPLY=()
  while IFS= read -r linha; do
    [ -n "$linha" ] && COMPREPLY+=("${linha%%$'\t'*}")
  done < <(fluxo __complete "${COMP_WORDS[@]:1:COMP_CWORD}" 2>/dev/null)
}
complete -F _fluxo fluxo
'''

SCRIPTS = {"zsh": ZSH, "bash": BASH}


def _visivel(acao):
    return acao.help is not argparse.SUPPRESS


def _subcomandos(p):
    """{nome: resumo} das ações de um parser; só as que têm help (o hook e o _rodar ficam de fora)."""
    for acao in p._actions:
        if isinstance(acao, argparse._SubParsersAction):
            return {c.dest: c.help or "" for c in acao._choices_actions}, acao.choices
    return {}, {}


def _flags(p):
    return {o: a for a in p._actions if a.option_strings and _visivel(a) for o in a.option_strings if o.startswith("--")}


def candidatos(ap, palavras, raiz):
    """[(valor, descrição)] para a última palavra de `palavras` (as que vêm depois de `fluxo`). Um `--home` entre
    elas vale para os valores que vêm dos arquivos, como no comando de verdade."""
    *antes, atual = palavras or [""]
    p, posicionais, pendente, home = ap, [], None, None
    for i, palavra in enumerate(antes):
        if pendente is not None:  # o valor de uma flag
            if pendente.dest == "home":
                home = palavra
            if pendente.nargs not in ("*", "+"):
                pendente = None
                continue
            if not palavra.startswith("-"):
                continue
            pendente = None
        if palavra.startswith("-"):
            acao = {o: a for a in p._actions for o in a.option_strings}.get(palavra.split("=", 1)[0])
            if acao is not None and acao.nargs != 0 and "=" not in palavra:
                pendente = acao
            continue
        resumos, escolhas = _subcomandos(p)
        if escolhas and not posicionais and palavra in escolhas:
            p, posicionais = escolhas[palavra], []
            continue
        posicionais.append(palavra)
    dinamicos = _Dinamicos(home, raiz)
    if pendente is not None and not (pendente.nargs in ("*", "+") and atual.startswith("-")):
        return _filtrar(_valores_da_flag(pendente, palavras, dinamicos), atual)
    if atual.startswith("-"):
        return _filtrar([(o, a.help or "") for o, a in _flags(p).items()], atual)
    resumos, escolhas = _subcomandos(p)
    if escolhas and not posicionais:
        return _filtrar(list(resumos.items()), atual)
    return _filtrar(_valores_do_posicional(p.prog, posicionais, dinamicos), atual)


def _filtrar(itens, atual):
    return [(v, d) for v, d in itens if v.startswith(atual)]


def _valores_da_flag(acao, palavras, dinamicos):
    if acao.choices:
        return [(c, "") for c in acao.choices]
    if acao.dest == "enable":
        ja = set(palavras[:-1])
        return [(m, "") for m in dinamicos.modulos(ligados=False) if m not in ja]
    if acao.dest == "disable":
        ja = set(palavras[:-1])
        return [(m, "") for m in dinamicos.modulos(ligados=True) if m not in ja]
    if acao.dest == "recipe":
        return dinamicos.repos()
    if acao.dest == "task":
        return dinamicos.ids()
    return []


def _valores_do_posicional(prog, posicionais, dinamicos):
    n = len(posicionais)
    if prog == "fluxo task" and n == 0:
        return dinamicos.ids()
    if prog == "fluxo validate start" and n == 0:
        return dinamicos.planos()
    if prog in ("fluxo validate status", "fluxo validate stop") and n == 0:
        return dinamicos.revisoes()
    if prog == "fluxo validate recipe" and n == 0:
        return dinamicos.repos()
    if prog in ("fluxo config get", "fluxo config set", "fluxo config unset") and n == 0:
        return [(c.nome, c.ajuda) for c in ajustes.CHAVES]
    if prog == "fluxo config set" and n >= 1 and posicionais[0] in ajustes.POR_NOME:
        return [(e, "") for e in ajustes.POR_NOME[posicionais[0]].escolhas]
    return []


class _Dinamicos:
    """Os valores que vêm dos arquivos desta máquina. Qualquer erro (sem config, config estragada) dá lista vazia."""

    def __init__(self, home, raiz):
        self.cam, self.existentes, self._cfg = cfgmod.Caminhos(home=home), montar.modulos_existentes(raiz), None

    def cfg(self):
        if self._cfg is None:
            self._cfg = cfgmod.carregar(self.cam, self.existentes)
        return self._cfg

    def modulos(self, ligados):
        return [m for m in self.existentes if (m in self.cfg()["modules"]) == ligados]

    def ids(self):
        vistos = {}
        for t in reversed(task.ler_tasks(self.cam.estado)):  # o mais recente primeiro
            if t.get("id") and t["id"] not in vistos:
                vistos[t["id"]] = t.get("ferramenta") or ""
        return list(vistos.items())

    def planos(self):
        pasta = self.cam.dados / "planos"
        return [(p.stem, "") for p in sorted(pasta.glob("*.md"), key=lambda p: -p.stat().st_mtime)]

    def revisoes(self):
        return [(ch.split("/", 1)[1], "") for ch in validate.execucoes(self.cam) if ch.startswith("revisao/")]

    def repos(self):
        pasta = Path(self.cfg()["code_dir"]).expanduser()
        return [(p.name, "") for p in sorted(pasta.iterdir())
                if p.is_dir() and validate.RECEITA_RE.fullmatch(p.name)]


def completar(ap, palavras, saida, raiz):
    """O `fluxo __complete`: imprime os candidatos e nunca levanta (um erro aqui viraria lixo no prompt)."""
    try:
        itens = candidatos(ap, list(palavras), raiz)
    except Exception:  # noqa: BLE001 — sem config, sem pasta, argv estragado: só não completa
        itens = []
    for valor, descricao in itens:
        saida.write(f"{valor}\t{' '.join(str(descricao).split())}\n" if descricao else f"{valor}\n")
    return 0
