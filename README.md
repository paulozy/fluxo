# fluxo

O fluxo de desenvolvimento (pesquisar → planejar → implementar, worktree, testes, code review, E2E,
PR e conclusão) para usar com **Claude Code**, **GitHub Copilot CLI**, **Kiro CLI** ou **OpenAI Codex
CLI**. Tudo é instalado no nível do usuário (`~/.claude`, `~/.copilot`, `~/.kiro`, `~/.codex`):
**nada entra no repo da empresa**.

Primeira vez? O [guia de uso](docs/guia.md) leva da instalação a uma tarefa inteira, com exemplos.

## Requisitos

- Python 3.9 ou mais novo, só a biblioteca padrão. O do Xcode, no Mac, serve.
- A ferramenta (`claude`, `copilot`, `kiro-cli` ou `codex`) e o `git`.
- Para abrir PR/MR e usar o `/pr-feedback`: `gh` (GitHub) ou `glab` (GitLab).

## Instalar

```bash
git clone git@github.com:paulozy/fluxo.git ~/fluxo   # ou o zip, veja abaixo
cd ~/fluxo
./fluxo init                  # pergunta as ferramentas, o forge, a base, a pasta dos clones e os módulos
./fluxo install --dry-run     # mostra o que vai gravar
./fluxo install
fluxo doctor                  # o install cria ~/.local/bin/fluxo
fluxo                         # o resumo: a config, os módulos, o install e o que está rodando
```

Depois, `fluxo config` muda a config sem abrir o arquivo (o menu num terminal, ou `fluxo config set task.max 3`),
`fluxo -h` mostra os comandos por área e `fluxo completion zsh|bash` gera o completar com Tab.

O `init` pergunta cada opção no terminal, com o padrão entre colchetes (Enter aceita). As listas aceitam os
números ou os nomes, separados por vírgula:

```
Ferramentas (a primeira é a padrão do `fluxo task`):
  1) claude   (no PATH)
  2) copilot  (no PATH)
  3) kiro
  4) codex
Números ou nomes, separados por vírgula: 2,1
Onde ficam os repos:
  1) github
  2) gitlab
Número ou nome [github]: gitlab
Host do GitLab: gitlab.empresa.com
Branch de desenvolvimento (de onde saem as branches) [develop]:
…
```

No fim, ele mostra a config, pergunta se grava e lista o que falta rodar, como o `fluxo validate enable`
quando o módulo `validate` foi escolhido. Ctrl-C cancela sem gravar nada, com saída 130.

Rodado de novo, o `init` pergunta se refaz a config, e os valores dela viram os padrões. Os módulos são
perguntados de novo, com os ligados de padrão. As opções deles (`task`, `watch`, `promote`, `validate`) e o
`models` ficam.

**Para script, sem perguntas,** passe as flags. Com `--tool` e `--forge`, nada é perguntado:

```bash
./fluxo init --tool copilot --forge gitlab --gitlab-host gitlab.empresa.com --code-dir ~/code \
  --promote-to staging,main     # só para o módulo promote: as bases depois da develop
```

Sem terminal, com `--no-input`, com a variável `CI` ou com `TERM=dumb`, o `init` nunca pergunta. Nesses casos,
faltar `--tool` ou `--forge` é erro de uso, com saída 2. Uma config que já existe só é refeita com `--force`.
Também não pergunta dentro de um agente: as marcas que Claude Code, Codex, Copilot e Kiro põem nos comandos que
rodam (`AI_AGENT`, `CODEX_CI`, `COPILOT_CLI` etc.) desligam as perguntas.

A mesma regra vale para os menus. Num terminal, `fluxo task` sem ID, `fluxo validate` sem ação ou sem alvo e
`fluxo modules --enable` ou `--disable` sem nome perguntam o que falta, no lugar do erro.

O `init` grava `~/.config/fluxo/config.json`:

| Chave | Valores | Para quê |
|---|---|---|
| `tools` | `claude`, `copilot`, `kiro`, `codex` (uma ou mais) | onde instalar e em que formato; a primeira é a padrão do `fluxo task` (veja [Duas ferramentas](#duas-ferramentas)) |
| `forge` | `github`, `gitlab` | `gh` ou `glab`; PR ou MR |
| `kiro.engine` | `v2` (padrão), `v3` | nomes de ferramenta dos agentes do Kiro |
| `git.base` | `develop` | de onde saem as branches |
| `code_dir` | `~/code` | onde ficam os clones |
| `os` | detectado | a área de transferência, o aviso na área de trabalho e o agendamento do watch |
| `chat`, `tracker` | `draft`, `none` | reservados: hoje nenhuma fonte muda por eles |
| `models` | `{"copilot": {"strong": "…", "medium": "…", "light": "…"}}` | modelo de cada agente, por ferramenta; vazio = herda o da sessão (padrão fora do Claude) |

## O que é instalado

- **Instruções** (o processo), em cada ferramenta de `tools`: `~/.claude/rules/fluxo.md`,
  `~/.copilot/instructions/fluxo.instructions.md`, `~/.kiro/steering/fluxo.md` ou `~/.codex/AGENTS.md`.
- **7 subagentes:** architect, researcher, test-runner, code-reviewer, debugger, doc-writer e committer.
- **Skills:**

  | Skill | O que faz |
  |---|---|
  | `/pesquisar` | o `researcher` mapeia o código; você revisa |
  | `/planejar` | o `architect` pesquisa; o plano vai para `~/.local/share/fluxo/planos/<id>.md` e espera a sua aprovação |
  | `/implementar` | fase por fase, com `test-runner` e `code-reviewer` |
  | `/revisar` | o `code-reviewer` sobre o diff |
  | `/commitar` | commit no padrão do repo, sem trailer de IA |
  | `/concluir` | resumo, docs, PR/MR (com aprovação), rascunho do aviso, limpeza, fechamento do plano |
  | `/pr-feedback` | responde o review de uma PR (GitHub) ou MR (GitLab), mostrando tudo antes de publicar |

- **`~/.local/bin/fluxo`**, que roda a cópia de onde o install saiu.

Os nomes são em português de propósito: o Copilot e o Kiro já têm `/plan` e `/review` nativos. No Codex,
a skill é chamada com `$`: `$planejar`, `$implementar` (veja [Codex](#codex)).

## Módulos

Desligados por padrão. `fluxo modules` lista os módulos; `fluxo modules --enable task messages` liga
e lembra de rodar `fluxo install`.

| Módulo | O que traz | Precisa de |
|---|---|---|
| `task` | `fluxo task <ID>` abre a tarefa numa janela do tmux, em segundo plano, com `/planejar <ID>`, e ocupa vaga (`task.max`, padrão 2). `fluxo task -p "…"` abre uma sessão avulsa (nome do `/comando`, ou `avulsa`, `avulsa-b`…), que não ocupa vaga. `--tool claude` abre noutra ferramenta de `tools`. `--list` mostra as vivas. | tmux |
| `messages` | skill `/aviso`: redige no seu tom (`~/.config/fluxo/tom.md`, criado por `fluxo msg tone`), pede aprovação e copia para a área de transferência (`fluxo msg copy`). Nada é enviado. | pbcopy, clip.exe, wl-copy, xclip ou xsel |
| `lock` | `fluxo lock acquire/release/status/break/run`: uma sessão por vez no stack local. O E2E das instruções passa a usar. | tmux (para saber se o dono vive) |
| `watch` | `fluxo watch tick` lê as suas PRs (GitHub) ou MRs (GitLab) e avisa na área de trabalho: review ou comentário novo, aprovação no commit atual, CI que quebrou ou passou, merge. Só avisa. `fluxo watch enable` agenda: launchd no Mac, systemd no Linux, uma janela do tmux no WSL. `fluxo watch` mostra o status. | `gh` ou `glab` logado |
| `panel` | `fluxo panel`: as sessões ao vivo em 4 abas (sessão, tarefa, global, sessões; veja [Painel](#painel)), com Enter levando até a sessão no tmux. Os hooks vão para `~/.copilot/hooks`, `~/.kiro/hooks` ou `~/.codex/hooks.json` (no Codex, aprovados em `/hooks`); no Kiro V2, um agente `fluxo` vira o padrão (`fluxo panel enable`). Copilot, Kiro e Codex: no Claude o painel é o agent-tree. | tmux |
| `promote` | skill `/promover <id>`: depois do QA, leva os commits que entraram na base para as bases seguintes (`promote.targets`, ex.: staging e main), por cherry-pick, com uma PR/MR por base. Mostra tudo antes de publicar; o merge é sempre seu ou do time. | — |
| `reviewer` | skill `/revisar-pr` e o agente `verificador`: duas revisões isoladas (`code-reviewer` + diff contra o plano), relatório com veredito (REPROVAR, PRECISA DE HUMANO, APROVÁVEL) em `~/.local/share/fluxo/revisoes/`. O `/concluir` roda antes de abrir a PR/MR. | — |
| `validate` | skills `/validar <id>` e `/receita <repo>`, com o pr-reviewer: confere a premissa do plano, revisa o código com agentes isolados e, com runbook, executa a mudança num stack local, na base e na branch. Roda em segundo plano (`fluxo validate start`/`status`). Com ele, o `/concluir` valida antes da PR/MR, e o `/revisar-pr` fica manual. O pr-reviewer chega com `fluxo validate enable` (veja [Validação](#validação-com-o-pr-reviewer)). | Node 22.18+, git com acesso ao repo do pr-reviewer, docker para a execução |

- **Comando de abertura de cada ferramenta:** fica em `adaptadores/ferramenta/*.json` (`lancar`). Se o
  da sua versão for outro, sobrescreva na config, por ferramenta:
  `"task": {"launch": {"copilot": ["copilot", "-i", "{prompt}"]}}`.
- **Fora do tmux:** use `--target <sessão>:`.

## Validação com o pr-reviewer

O `fluxo install` não usa rede. Quem traz o pr-reviewer é o `fluxo validate enable`, como o `watch enable`:

- clona o pr-reviewer na tag que esta versão do fluxo fixa (em `lib/fluxo/validate.py`, com o sha do commit,
  que é conferido) em `~/.local/share/fluxo/pr-reviewer`, por HTTPS, com o seu acesso ao GitHub: o
  `gh auth setup-git` libera o repo privado; com ssh, `git config --global url.git@github.com:paulozy/.insteadOf https://github.com/paulozy/`;
- cria `~/.config/pr-reviewer/config.json` só se não existir: `workspaceRoot` = `code_dir`, as bases do
  `git.base`, o perfil `local` (`--profile <nome>` troca) e o `backend` = a primeira ferramenta de `tools`
  (`fluxo config set pr-reviewer.backend` troca depois);
- cria o comando `~/.local/bin/pr-review` só se não houver um. As skills não dependem dele: usam
  `fluxo validate`.

Rodado de novo depois de atualizar o fluxo, o `enable` leva o clone para a tag nova, mas só se ele estiver
limpo: um arquivo mudado, novo ou ignorado, um commit, um branch ou um stash seus nunca são descartados, e o
`enable` avisa. `fluxo validate disable` tira o comando e o clone, nas mesmas condições; a config do
pr-reviewer fica. Para usar um clone seu (o de desenvolvimento), ponha `"validate": {"dir": "~/code/pr-reviewer-core"}`
na config: o fluxo nunca mexe nele.

- `fluxo validate start <id> [--backend B]` revisa a tarefa em segundo plano: um `--local` por repo do plano,
  na branch da worktree, com a base do plano. Com o módulo `lock`, o processo espera a trava do stack.
- `fluxo validate start --recipe <repo> [--fix|--check]` escreve ou testa o runbook.
- `fluxo validate status <id> [--wait 100]` (ou `--recipe <repo>`) acompanha: saída 75 é "ainda rodando", e no
  fim a primeira linha é o veredito, lido do `report.json`. `fluxo validate stop <id>` cancela, e o pr-reviewer
  derruba o stack antes de sair.
- `fluxo validate recipe <repo> [--check|--fix]` faz a receita neste terminal: mostra a proposta e pergunta o
  "sim" ali mesmo; o `--check` segura a trava do stack.
- `fluxo validate doctor` roda o doctor do pr-reviewer (chama o agente de verdade).
- `fluxo validate exec -- <args>` roda o pr-reviewer do clone (o `--apply` da receita vai por ele).

No Copilot, os agentes do pr-reviewer rodam no próprio Copilot, com o login do keychain (no Linux, a sessão precisa
do D-Bus). Um modelo muito barato pode deixar a revisão incompleta, e ela nunca sai aprovada sem a leitura.

## Duas ferramentas

Para usar o Copilot e, quando a cota dele acabar, o Claude (ou o contrário), instale as duas:

```bash
fluxo init --tool copilot --tool claude --forge github --force   # --force refaz uma config que já existe
fluxo install
```

- **A primeira de `tools` é a padrão.** `fluxo task <ID>` abre nela, e `fluxo task <ID> --tool claude`
  abre na outra.
- **Trocar no meio de uma tarefa:** feche a janela antiga e abra de novo na outra ferramenta, com
  `fluxo task <ID> --tool claude -p '/implementar <ID>'` (no Codex, `'$implementar <ID>'`). O plano (`~/.local/share/fluxo/planos/<id>.md`),
  a branch e o worktree valem para as duas.
- **Uma sessão que abre outra** (`fluxo task` de dentro dela) usa a própria ferramenta: as instruções de
  cada uma já trazem o `--tool` dela.
- **Tirar ou pôr uma ferramenta:** edite `tools` no `config.json` (a ordem decide a padrão) e rode
  `fluxo install`. Os arquivos da que saiu são removidos, menos os que você editou.
- **`models` e `task.launch` são por ferramenta:** nome de modelo e comando de abertura só valem para uma.
- **O `fluxo panel` não mostra as sessões do Claude:** ele não tem os hooks do panel.
- **Conflito numa ferramenta para o install inteiro.** Se a pasta de uma delas já tiver um arquivo com o
  nome de um do fluxo (um agente seu, por exemplo), nada é gravado, nem para a outra. Renomeie o seu, ou
  use `--force`, que guarda um backup.

## Painel

`fluxo panel` (módulo `panel`) abre uma tela com 4 abas. `fluxo panel --text --view session|task|global|list`
imprime uma delas uma vez.

| Aba | O que mostra |
|---|---|
| `1` sessão | a sessão seguida: estado, pasta, tarefa, o último prompt, as ferramentas mais usadas, os subagentes e o log |
| `2` tarefa | o plano (`planos/<id>.md`): status, o checklist do fluxo em barra, as sessões da tarefa, os vereditos do `/revisar-pr` e as PRs/MRs cujo título cita o id |
| `3` global | as sessões por estado, os alertas (sessão esperando você, erro de hook, trava presa, CI quebrado, erro do watch), a trava, o feed do watch e as revisões das últimas 24 h |
| `4` sessões | uma linha por sessão viva, com quem espera você em cima (a aba inicial) |

**Teclas:**
- `1`–`4` trocam a aba, e `↑↓` (ou `j`/`k`) escolhem a sessão.
- `Enter` vai até ela no tmux.
- `f` segue a escolhida, e `Tab` passa para a próxima.
- `←→` trocam a tarefa.
- `PgUp`/`PgDn` rolam.
- `h` esconde, e `q` (ou `Esc`) sai.

Sem nada fixado, a aba 1 segue a sessão mais recente, mas só troca quando a seguida para. `--follow <id>` e
`--task <ID>` fixam.

**O que cada ferramenta entrega na árvore de subagentes:**
- **Codex:** completa, com as ferramentas de cada subagente.
- **Copilot:** o início e o fim de cada subagente, mas as ferramentas ficam na conta da sessão, porque o hook
  não diz de qual subagente são.
- **Kiro:** o subagente aparece como a chamada da ferramenta `subagent`.
- **Claude:** fica de fora, porque lá o painel é o agent-tree.

**O que fica no disco:**
- **Os eventos** (o começo do prompt e de cada comando) ficam em `~/.local/share/fluxo/estado/`, numa pasta
  só sua (700), e os arquivos de eventos são 600.
- **A poda:** o `fluxo panel` apaga os eventos com mais de 7 dias. O hook nunca apaga nada.

## Codex

O Codex tem tudo o que o fluxo usa, mas algumas peças funcionam diferente:

- **Instruções em `~/.codex/AGENTS.md`.** O Codex lê um AGENTS.md global só. Se você já tem um, o install
  para com conflito: junte os dois num arquivo seu, ou deixe o do fluxo. Um `AGENTS.override.md` não
  vazio tira o do fluxo de cena, e o `fluxo doctor` avisa.
- **Skill com `$`:** `$planejar PROJ-12`, e não `/planejar`. No shell, o prompt vai entre aspas simples:
  `fluxo task PROJ-12 --tool codex -p '$implementar PROJ-12'`.
- **Skills em `~/.agents/skills`**, o lugar oficial. Com o Copilot junto, vão para `~/.codex/skills`,
  porque o Copilot também lê `~/.agents/skills` e as veria em dobro. O `~/.codex/skills/.system` é do
  próprio Codex.
- **Agentes em `~/.codex/agents/*.toml`, sem restrição de ferramenta.** O Codex não tem esse campo, e o
  "só leitura" do architect, do researcher e do code-reviewer vira uma regra no texto deles.
- **Hooks do panel em `~/.codex/hooks.json`, que é inteiro do fluxo:** com hooks seus ali, dá conflito.
  Cada hook só roda depois de aprovado em `/hooks`, e de novo quando o fluxo muda o arquivo. O install
  avisa.
- **O sandbox continua o seu:** o que o fluxo faz fora do repo (gravar o plano e a trava em
  `~/.local/share/fluxo`, a rede do `gh`/`glab`) pode pedir aprovação.
- **O `fluxo task` abre com `codex --no-daemon -- '<prompt>'`.** O Codex não aceita id nem nome de
  sessão.
- **Versão:** confira o roteiro de validação com o `codex` atualizado. O formato do Codex muda rápido.

## Atualizar, conflitos e desinstalar

- **Atualizar:** puxe o repo (ou um zip novo) e rode `fluxo install` de novo. Só o que mudou é gravado.
- **Conflito:** o install nunca pisa num arquivo que não é dele, nem num dele que você editou. Com
  conflito, nada é gravado e a saída é 3. `--force` grava assim mesmo, guardando um
  `.fluxo-bak-<data>`.
- **Desinstalar:** `fluxo uninstall`. O que você editou fica.
- Para mudar o fluxo, edite as **fontes** neste repo (`nucleo/`), não os arquivos gerados.
- **Da 0.4 para a 0.5:** os comandos, a config e os módulos passaram para inglês (o
  [CHANGELOG](CHANGELOG.md) tem a lista). Refaça a config com `fluxo init … --force` e rode `fluxo install`.

## Máquina sem git

```bash
./fluxo package                          # aqui: dist/fluxo-<versão>.zip (passa pela varredura antes)
# lá, depois de copiar o zip:
python3 -m zipfile -e fluxo-<versão>.zip ~/fluxo-src
python3 ~/fluxo-src/fluxo/fluxo init --tool kiro --forge github
python3 ~/fluxo-src/fluxo/fluxo install
```

O `install` não usa a rede.

## Validação na máquina real

Antes de confiar numa ferramenta nova, siga o [roteiro de validação](docs/validacao.md): os formatos
do Copilot e do Kiro mudam entre versões.

## Desenvolvimento

```bash
python3 -m unittest discover -s tests          # a suíte
docker run --rm -v "$PWD":/src:ro -w /src python:3.9-slim \
  sh -c 'cp -r /src /tmp/f && cd /tmp/f && python -m unittest discover -s tests'   # no 3.9
FLUXO_ATUALIZAR_ESPERADO=1 python3 -m unittest discover -s tests -p test_install.py  # regenera os golden
./fluxo scan                                   # dado de empresa no repo? (o pre-commit roda)
```

- **Layout:**
  - `nucleo/` tem as fontes: instruções, agentes e skills;
  - `adaptadores/` tem os dados por ferramenta e por forge;
  - `lib/fluxo/` é o código;
  - `modulos/` tem os módulos, um por pasta, com o `modulo.json`.
- **Template das fontes:** `{{forge.pr}}`, `{{git.base}}`, `{{codigo_dir}}`, `{{dados}}`,
  `{{argumentos}}` e `{{pergunte}}`, mais blocos `<!-- se forge:gitlab -->…<!-- senao -->…<!-- fim -->`. Uma condição
  junta termos com ` e ` (`<!-- se module:reviewer e nao module:validate -->`), e todos têm de valer.
  Variável ou condição desconhecida é erro.
- **Agentes:** frontmatter com `tools: [read, search, edit, shell, web, agent]` e
  `model: strong|medium|light`. A tradução para cada ferramenta fica em `adaptadores/ferramenta/`.
