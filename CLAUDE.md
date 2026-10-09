# Project: fluxo

## Overview

O fluxo de dev do usuário, portável para Claude Code, Copilot CLI e Kiro CLI, em outras empresas. É
instalado no nível do usuário e nunca escreve no repo da empresa.

## Tech Stack

- **Python 3.9 ou mais novo, só stdlib.** O 3.9 é o do Xcode no Mac.
  - Proibido: `match`, `X | Y` avaliado em runtime, `tomllib`, `zip(strict=)`.
  - A config é JSON.
- **Testes:** `unittest`. A suíte também roda no `python:3.9-slim` (comando no README).
- Não adicione dependência sem confirmar.

## Architecture

```
fluxo                 # entrada: põe lib/ no sys.path
lib/fluxo/            # config, fonte (frontmatter), render (template), montar (função pura: fontes →
                      # arquivos), instalar (manifesto), doctor, varredura, empacotar, cli
lib/fluxo/ferramentas # claude.py, copilot.py, kiro.py: onde cada coisa mora e em que formato
nucleo/               # fontes: instrucoes/NN-*.md, agentes/*.md, skills/<n>/SKILL.md
adaptadores/          # dados: tools e modelos por ferramenta; variáveis por forge
modulos/              # fases 2+ (task, mensagens, pr-reviewer lite, vigia, painel)
tests/esperado/       # golden: a árvore gerada em cada cenário
```

## Regras

- **Nenhum dado de empresa no repo:** ids, cloudId, nomes de pessoas, orgs ou caminhos de home.
  - O `fluxo varrer` (pre-commit) e o `test_repo_limpo` barram.
  - Os termos proibidos ficam só como sha256 em `tests/proibidos.sha256`.
  - Dado de teste que pareça proibido é montado em runtime.
- **Formato de ferramenta só com fonte oficial,** citada no docstring do renderizador ou no
  `_fonte` do adaptador. O que a doc não confirma vai para o `docs/validacao.md`.
- **Mudou uma fonte de propósito:** regenere os golden
  (`FLUXO_ATUALIZAR_ESPERADO=1 python3 -m unittest discover -s tests -p test_install.py`) e revise o diff.
- **Não instale para `claude` no `~/.claude` desta máquina:** os agentes de lá têm o mesmo nome. O
  install recusa com CONFLITO, e é isso que o protege.

## Git

- Commits em português, `type(scope): descrição`, sem `Co-Authored-By` de IA.
- O e-mail é o pessoal (`git config user.email` só neste clone).
- O repo é privado.
