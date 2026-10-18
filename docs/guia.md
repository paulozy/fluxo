# Guia de uso do fluxo

Este guia é o passo a passo para quem vai usar o fluxo pela primeira vez: instalar, levar uma tarefa do card à
PR e ligar os módulos. O [README](../README.md) é a referência das chaves, dos arquivos e das particularidades de
cada ferramenta.

Nos exemplos, a tarefa é `PROJ-12`, o repo é `api`, a base é `develop` e os clones ficam em `~/code`.

## 1. O que o fluxo é

O fluxo é um processo de desenvolvimento escrito uma vez e instalado em quatro ferramentas: **Claude Code**,
**GitHub Copilot CLI**, **Kiro CLI** e **OpenAI Codex CLI**. O processo é:

```
pesquisar → planejar → (sua aprovação) → implementar → revisar → E2E local
  → concluir (validar → docs → PR/MR → aviso → limpeza)
```

O fluxo tem quatro peças:

| Peça | O que é | Onde mora |
|---|---|---|
| **Instruções** | o processo: as fases, as regras de git, de E2E e de segurança. A ferramenta as lê em toda sessão. | `~/.claude/rules/fluxo.md`, `~/.copilot/instructions/`, `~/.kiro/steering/`, `~/.codex/AGENTS.md` |
| **Subagentes** | 7 especialistas que a sessão chama: `architect`, `researcher`, `test-runner`, `code-reviewer`, `debugger`, `doc-writer` e `committer` | a pasta `agents/` de cada ferramenta |
| **Skills** | os comandos que você digita: `/planejar`, `/implementar`, `/concluir`… (no Codex, `$planejar`) | a pasta `skills/` de cada ferramenta |
| **O CLI `fluxo`** | instala, confere e roda os módulos (`fluxo task`, `fluxo lock`, `fluxo validate`…) | `~/.local/bin/fluxo` |

**Duas regras que valem sempre:**
- **Nada entra no repo da empresa.** O fluxo nunca cria `CLAUDE.md`, `AGENTS.md`, `.claude/`, `.kiro/` nem
  `.github/copilot-instructions.md` no projeto. O que é local vai para o `.git/info/exclude`, nunca para o
  `.gitignore`.
- **O plano é o elo entre as sessões.** O `/planejar` grava o plano em `~/.local/share/fluxo/planos/<id>.md`.
  Uma sessão nova, ou outra ferramenta, retoma a tarefa a partir dele.
- **Os clones seguem um layout fixo:** o repo em `<pasta dos clones>/<repo>` e a worktree da tarefa em
  `<pasta dos clones>/<repo>-<id>`, com o mesmo id em todas as skills. O `/validar` só acha a worktree nesse
  caminho.

**Como o fluxo chega à ferramenta.** As fontes ficam neste repo (`nucleo/` e `modulos/`). O `fluxo install` as
transforma no formato de cada ferramenta e grava no seu HOME. As instruções e as skills geradas avisam "edite a
fonte e rode `fluxo install`": para mudar o fluxo, edite a fonte, nunca o gerado.

**Os subagentes:**

| Agente | Papel | Pode editar? | Modelo (Claude) |
|---|---|---|---|
| `architect` | pesquisa algoritmo, padrão ou biblioteca antes de qualquer código, com prós, contras e fontes | não | opus |
| `researcher` | mapeia os arquivos (`arquivo:linha`), os padrões, as dependências e os testes | não | sonnet |
| `test-runner` | roda os testes e devolve só o resumo e as falhas | não | haiku |
| `code-reviewer` | revisa o diff: crítico, aviso e sugestão, com `arquivo:linha` | não | opus |
| `debugger` | acha a causa raiz e propõe a correção, sem aplicar | não | sonnet |
| `doc-writer` | atualiza só a documentação que o projeto já mantém | sim | sonnet |
| `committer` | faz o commit no padrão do repo, sem trailer de IA e sem push | só o commit | haiku |

Fora do Claude, os agentes herdam o modelo da sessão, a menos que `models` na config diga outro. No Codex, que
não restringe ferramenta de subagente, o "não edita" vira uma regra escrita no texto do agente.

## 2. Instalar

### 2.1 Requisitos

- **Python 3.9 ou mais novo,** só a biblioteca padrão. No Mac, o do Xcode serve.
- **A ferramenta** (`claude`, `copilot`, `kiro-cli` ou `codex`), já logada, e o **`git`**.
- **`gh` (GitHub) ou `glab` (GitLab),** para abrir a PR/MR e para o `/pr-feedback`.
- **Por módulo:** o `tmux` para o `task`, o `lock` e o `panel`; um utilitário de área de transferência para o
  `messages`; e para o `validate`, o Node 22.18+ e o docker.

### 2.2 Configurar

```bash
git clone git@github.com:paulozy/fluxo.git ~/fluxo
cd ~/fluxo
./fluxo init
```

O `init` pergunta cada opção, com o padrão entre colchetes. Enter aceita o padrão, e as listas aceitam os números
ou os nomes, separados por vírgula. Uma conversa típica:

```
Configurando o fluxo nesta máquina. Enter aceita o padrão entre colchetes; Ctrl-C cancela.

Ferramentas (a primeira é a padrão do `fluxo task`):
  1) claude   (no PATH)
  2) copilot  (no PATH)
  3) kiro
  4) codex
Números ou nomes, separados por vírgula: copilot

Onde ficam os repos:
  1) github
  2) gitlab
Número ou nome [github]: 2
Host do GitLab: gitlab.empresa.com.br

Branch de desenvolvimento (de onde saem as branches) [develop]:
Pasta dos clones [~/code]:

Sistema:
  1) linux
  2) mac
  3) wsl
Número ou nome [mac]:

Módulos (opcionais; `fluxo modules` muda depois):
  1) lock      fluxo lock: uma sessão por vez no stack local (docker compose)
  2) messages  /aviso e fluxo msg: mensagem no seu tom, copiada para colar no chat
  …
  7) validate  /validar e /receita: o pr-reviewer revisa e executa a mudança antes da PR/MR (…)
  8) watch     fluxo watch: avisa das suas PRs/MRs (review, aprovação, CI, merge); só avisa
Números ou nomes, separados por vírgula; "nenhum" para pular [nenhum]: task, lock, validate

A config:
{ … }
Gravar em ~/.config/fluxo/config.json? [S/n]:
config gravada em ~/.config/fluxo/config.json. Agora:
  ~/fluxo/fluxo install --dry-run   # mostra o que vai gravar
  ~/fluxo/fluxo install
  fluxo doctor
  fluxo validate enable   # traz o pr-reviewer
```

**Detalhes:**
- **A primeira ferramenta é a padrão:** é nela que o `fluxo task` abre as sessões (veja a seção 6).
- **Módulo que as ferramentas não atendem é recusado:** o `panel`, por exemplo, não existe no Claude, porque lá
  o painel é o agent-tree.
- **Rodar de novo** pergunta se refaz a config, e os valores dela viram os padrões.
- **Ctrl-C** cancela sem gravar nada, com saída 130.
- **Sem terminal,** ou com `--no-input`, `CI` ou `TERM=dumb`, o `init` não pergunta, e faltar `--tool` ou
  `--forge` é erro de uso (saída 2).

**Em script, sem perguntas,** passe as flags. Com `--tool` e `--forge`, nada é perguntado:

```bash
./fluxo init --tool copilot --forge gitlab --gitlab-host gitlab.empresa.com.br --code-dir ~/code
```

### 2.3 Instalar e conferir

```bash
./fluxo install --dry-run
```

O `--dry-run` mostra uma linha por arquivo que muda (os iguais, só com `-v`), sem gravar nada:

```
aviso: validate: rode `fluxo validate enable` para trazer o pr-reviewer (o install não usa rede)
fluxo install (copilot; gitlab) em <o seu HOME>:
  criar      ~/.copilot/instructions/fluxo.instructions.md
  criar      ~/.copilot/agents/architect.agent.md
  …
  criar      ~/.local/bin/fluxo
  18 criar
dry-run: nada foi gravado
```

**As ações possíveis:**
- **`criar`, `atualizar`, `igual`:** o arquivo é do fluxo, e não há o que decidir;
- **`adotar`:** o arquivo já existe com o mesmo conteúdo, e passa a ser do fluxo;
- **`remover`:** o arquivo saiu do fluxo, e é apagado;
- **`manter`:** o arquivo saiu do fluxo, mas você o editou, e ele fica;
- **`conflito`:** o arquivo não é do fluxo, você o editou depois do install, ou é um link simbólico. Com um
  conflito, nada é gravado e a saída é 3.

Depois, grave e confira:

```bash
./fluxo install
fluxo doctor
```

**O que o `doctor` confere:**
- o Python;
- a config;
- o binário de cada ferramenta;
- o `git`, e o `gh`/`glab` com o login;
- o host do forge alcançável, que avisa se a VPN está desligada;
- o que cada módulo ligado pede;
- o `~/.local/bin` no PATH;
- os arquivos instalados, que avisa se algum sumiu ou foi editado.

Cada linha sai com `OK`, `AVISO` ou `FALHA`. Só a `FALHA` faz o `doctor` sair com 1.

### 2.4 O que muda em cada ferramenta

| | Claude Code | Copilot CLI | Kiro CLI | Codex CLI |
|---|---|---|---|---|
| Skill | `/planejar` | `/planejar` | `/planejar` | `$planejar` |
| Instruções | `~/.claude/rules/fluxo.md` | `~/.copilot/instructions/fluxo.instructions.md` | `~/.kiro/steering/fluxo.md` | `~/.codex/AGENTS.md` |
| Agentes | `~/.claude/agents/*.md` | `~/.copilot/agents/*.agent.md` | `~/.kiro/agents/*.json` | `~/.codex/agents/*.toml` |
| Skills | `~/.claude/skills/` | `~/.copilot/skills/` | `~/.kiro/skills/` | `~/.agents/skills/` (ou `~/.codex/skills/` com o Copilot junto) |
| Painel | o agent-tree (o `panel` não entra) | hooks em `~/.copilot/hooks/` | hooks; no V2, o agente `fluxo` vira o padrão | hooks em `~/.codex/hooks.json`, aprovados em `/hooks` |

**No Codex:**
- **Um `AGENTS.md` global seu** dá conflito no install. Junte os dois num arquivo seu, ou deixe o do fluxo.
- **Um `AGENTS.override.md` não vazio** tira o do fluxo de cena, e o `doctor` avisa.
- **O sandbox do Codex continua valendo.** Gravar o plano em `~/.local/share/fluxo` e a rede do `gh`/`glab`
  podem pedir aprovação.

### 2.5 Máquina sem git

```bash
./fluxo package        # aqui: dist/fluxo-<versão>.zip, depois da varredura de dados
# lá, depois de copiar o zip:
python3 -m zipfile -e fluxo-<versão>.zip ~/fluxo-src
python3 ~/fluxo-src/fluxo/fluxo init
python3 ~/fluxo-src/fluxo/fluxo install
```

## 3. Uma tarefa de ponta a ponta

O exemplo é um bug: "o endpoint de reservas aceita data de saída antes da de entrada". O card é o `PROJ-12`, e
você já reproduziu o erro e tem a resposta da API em mãos.

### 3.0 Abrir a sessão

Com o módulo `task`, de dentro do tmux:

```bash
fluxo task PROJ-12
```

O comando abre uma janela nova do tmux, com a ferramenta padrão já rodando `/planejar PROJ-12`, e ocupa uma vaga
(até 2, pela chave `task.max`).

Sem o módulo, abra a ferramenta na pasta dos clones e digite as skills você mesmo.

### 3.1 `/pesquisar` (opcional, para entender antes de planejar)

```
/pesquisar como o endpoint de reservas valida as datas no api
```

O `researcher` mapeia os arquivos, os padrões e os testes, com `arquivo:linha`. Se existir,
`~/.local/share/fluxo/projetos/api.md` é lido antes: é a memória do repo, com o que as tarefas anteriores
aprenderam. A skill **para** e mostra o resumo e as perguntas em aberto, para você revisar. Nada é gravado.

### 3.2 `/planejar` e a sua aprovação

```
/planejar PROJ-12 corrigir a validação de datas da reserva
```

**Antes de escrever:**
- num bug, a skill exige o erro real (a mensagem, o payload ou os passos) e pergunta se faltar;
- uma dúvida de regra de negócio que o código não responde vira pergunta, nunca suposição;
- o `researcher` mapeia o código, e o `architect` pesquisa a solução.

**O plano** vai para `~/.local/share/fluxo/planos/PROJ-12.md`:

```markdown
---
id: PROJ-12
status: rascunho
repos: [api]
base: develop
criado: 2026-10-17
---

## Contexto
## Causa raiz
## Escopo real
## Plano de implementação
## Fora do escopo
## Critério de aceite
## Riscos
## Checklist do fluxo
- [ ] plano aprovado
- [ ] branch e worktree
- [ ] implementação com testes
- [ ] code review
- [ ] E2E local
- [ ] PR/MR aberta
- [ ] aviso ao time
- [ ] limpeza
- [ ] resumo
```

A skill pergunta "aprova o plano em `<caminho>`?". Responda na conversa, ou troque `status: rascunho` por
`status: aprovado` no arquivo e peça para ela reler. **Nada é implementado com o plano em rascunho.**

Os campos `repos` e `base` do frontmatter são lidos pelo `/validar` e pelo painel. Um plano para dois repos tem
`repos: [api, web]`.

### 3.3 `/implementar`

```
/implementar PROJ-12
```

1. **Cria a branch numa worktree, uma por repo,** sempre a partir da base e nunca da `main`:
   ```bash
   git -C ~/code/api worktree add --no-track -b fix/PROJ-12-datas ~/code/api-PROJ-12 origin/develop
   ```
2. **Implementa fase por fase,** com o `test-runner` ao fim de cada uma.
3. **Chama o `code-reviewer` no fim,** e aplica os críticos e os avisos.
4. **Faz o E2E local,** quando há comportamento observável (veja abaixo).
5. **Atualiza o checklist do plano,** só com o que foi verificado.

**As regras do E2E local:**
- **Nunca usa o `.env` do projeto.** Monta um `.env.e2e` descartável, que vai para o `.git/info/exclude`.
- **Sobe a infra pelo `docker-compose.yml` da raiz,** com um projeto próprio:
  `docker compose -p api-PROJ-12 up -d`.
- **Com o módulo `lock`, pega a trava antes de subir:**
  `fluxo lock acquire --id PROJ-12 --project api-PROJ-12`. A saída 4 diz que outra sessão está usando o stack: a
  sessão avisa e espera, sem forçar.
- **Confirma com quem está falando:** pela forma da resposta do healthcheck, não pelo fato de a porta responder.
- **Reproduz o bug antes de provar a correção.**
- **No fim, derruba tudo:** `docker compose -p api-PROJ-12 down -v` e `fluxo lock release`.

### 3.4 `/revisar` e `/commitar`

- **`/revisar`:** o `code-reviewer` lê o diff contra a base. A skill aplica os críticos e os avisos, e as sugestões
  ficam a seu critério.
- **`/commitar`:** o `committer` faz o commit no padrão do repo. Ele adiciona os arquivos pelo nome (nunca
  `git add -A`), não põe trailer de IA e **não dá push**.

### 3.5 `/validar` (módulo `validate`), opcional antes do `/concluir`

O `/concluir` já começa pelo `/validar` (seção 3.6). Chame o `/validar` à parte só para validar antes, no meio da
implementação:

```
/validar PROJ-12
```

O pr-reviewer faz três coisas:
- confere a premissa do plano;
- revisa o código com agentes isolados;
- se o repo tem runbook, executa a mudança num stack local, na base e na branch.

A revisão leva de 4 a 20 minutos e roda em segundo plano. A sessão acompanha e devolve um veredito:
- **APROVÁVEL:** pode seguir;
- **REPROVAR:** mostra os achados, corrige, commita e roda de novo. Depois de 2 rodadas, para e fala com você;
- **PRECISA DE HUMANO:** para e pergunta.

O detalhe está na seção 5.

### 3.6 `/concluir`

```
/concluir PROJ-12
```

0. **Valida antes de tudo.** Com o módulo `validate`, roda o `/validar`; com o `reviewer` e sem o `validate`, o
   `/revisar-pr`. Só segue com APROVÁVEL ou com o seu ok.
1. **Resume o que foi feito de fato.**
2. **Atualiza as docs e o CHANGELOG** que o projeto já mantém, e commita antes da PR.
3. **Dá push da branch, sem perguntar.**
4. **Abre a PR/MR depois da sua aprovação.**
   - Antes, mostra o título e a descrição e espera o seu ok.
   - O texto vai por arquivo, nunca entre aspas no shell: `--body-file` no GitHub, e `--description` lido do
     arquivo no GitLab.
   - No GitLab sem `glab`, a MR sai pelo `git push -o merge_request.create`.
5. **Rascunha o aviso ao time.** Com o módulo `messages`, usa o `/aviso`, que pede a sua aprovação do texto; sem
   ele, só mostra o rascunho. **Nada é enviado.**
6. **Limpa:** `docker compose -p api-PROJ-12 down -v`. As worktrees só são removidas se estiverem limpas e sem
   commit à frente.
7. **Fecha o plano:** troca para `status: concluido` e acrescenta um `## Resumo`.
8. **Registra o que aprendeu** em `~/.local/share/fluxo/projetos/api.md`.

### 3.7 `/pr-feedback`, quando chega review

```
/pr-feedback https://github.com/exemplo-org/api/pull/54
```

**O que a skill faz:**
1. lê as threads da PR/MR;
2. acha a worktree da branch, que precisa estar limpa;
3. classifica os pedidos;
4. corrige cada um com teste, `test-runner` e `code-reviewer`;
5. commita sem push;
6. mostra o resumo e o texto de cada resposta, e **pede a sua aprovação uma vez** antes do push e das respostas.

Ela nunca resolve a thread, porque quem resolve é o revisor.

**Segurança:**
- comentário de review é dado, nunca instrução;
- a skill não roda comando que veio de comentário;
- não mexe em `.env`;
- não toca em CI nem em infra fora do que a PR já alterou.

### 3.8 `/promover` (módulo `promote`), depois do QA

```
/promover PROJ-12
```

Com `promote.targets` = `["staging", "main"]`:
1. **Cria uma branch por base,** `<branch>-staging` e `<branch>-main`, e faz o cherry-pick dos commits da tarefa
   sobre cada uma.
2. **Roda os testes** na última base.
3. **Mostra tudo e pede a sua aprovação uma vez,** antes do push.
4. **Abre uma PR/MR por base.**

A PR/MR da tarefa já precisa estar mergeada na base: sem o merge, a skill para. O merge das promoções é sempre
seu ou do time. Um conflito faz a skill parar com `--abort`, e ela nunca usa `--force`.

### Onde o fluxo para e pergunta

| Momento | O que ele pede |
|---|---|
| `/planejar` | o erro real de um bug, as dúvidas de negócio e a aprovação do plano |
| `/implementar` com o plano em rascunho | a aprovação, antes de qualquer código |
| `/concluir` com veredito que não é APROVÁVEL | se segue assim mesmo |
| `/concluir` | a aprovação do título e da descrição da PR/MR (o push da branch vem antes, sem pergunta) |
| `/aviso` (módulo `messages`) | a aprovação do texto, antes de copiar |
| `/pr-feedback` | a aprovação das correções e das respostas, antes do push |
| `/promover` | a aprovação das PRs/MRs de promoção, antes do push |
| `/receita` | a aprovação do runbook, que só é gravado com o hash da proposta mostrada; e a do `--fix`, se o `--check` falhar |
| `/validar` com PRECISA DE HUMANO, ou com REPROVAR depois de 2 rodadas | o que fazer |
| `/revisar-pr` com PRECISA DE HUMANO, ou com REPROVAR na 2ª rodada | o que fazer |

## 4. Módulos

Para ligar ou desligar um módulo, rode `fluxo init` de novo ou:

```bash
fluxo modules                          # lista, com [x] nos ligados
fluxo modules --enable task lock       # liga
fluxo modules --disable watch          # desliga
fluxo install                          # aplica
```

### `task`: sessões no tmux

```bash
fluxo task PROJ-12                         # abre com /planejar PROJ-12 e ocupa uma vaga
fluxo task PROJ-12 --tool claude           # noutra ferramenta de `tools`
fluxo task PROJ-12 -p '/implementar PROJ-12'
fluxo task -p '/revisar'                   # avulsa: não ocupa vaga; o nome vem da skill (revisar)
fluxo task --list                          # as sessões vivas
fluxo task PROJ-12 --target main:          # fora do tmux, numa sessão já aberta
```

**Saídas:**
- **0:** a sessão abriu;
- **1:** a ferramenta não subiu em 3 segundos. Rode o comando à mão para ver o erro;
- **2:** uso inválido;
- **3:** fora do tmux e sem `--target`;
- **4:** não há vaga, ou já existe uma sessão viva com esse nome. O `--force` só passa do limite de vagas; com
  uma sessão viva de mesmo nome, vá até ela ou feche-a.

No Codex, o prompt é `'$implementar PROJ-12'`, entre aspas simples por causa do `$`.

### `messages`: o aviso no seu tom

```bash
fluxo msg tone            # cria ~/.config/fluxo/tom.md, para você descrever como escreve
```

Na sessão, peça por exemplo `/aviso pedir review da PR do PROJ-12 para o time`.
- A skill redige no seu tom, mostra o texto e copia para a área de transferência depois do seu ok.
- Se você reescrever o texto, a sua versão entra como exemplo no `tom.md`.
- Nada é enviado.

### `lock`: uma sessão por vez no stack local

```bash
fluxo lock acquire --id PROJ-12 --project api-PROJ-12   # 0 pegou; 4 ocupada por outra sessão
fluxo lock status                                       # livre, ou quem tem e há quanto tempo
fluxo lock release
fluxo lock run --id PROJ-12 -- docker compose -p api-PROJ-12 up --abort-on-container-exit
fluxo lock break                                        # só quando o dono morreu (--force com ele vivo)
```

A sessão é identificada pelo `--session`, pela variável `FLUXO_SESSION`, pelo `CLAUDE_CODE_SESSION_ID` (no
Claude) ou pelo pane do tmux. Sem nenhum deles, o `acquire`, o `release` e o `run` saem com 2. O `status` e o
`break` não precisam saber quem você é.

### `watch`: aviso das suas PRs/MRs

```bash
fluxo watch enable     # agenda: launchd no Mac, systemd no Linux, uma janela do tmux no WSL
fluxo watch            # o status: último tick, login, PRs abertas e os últimos avisos
fluxo watch tick       # uma passada agora
```

**Avisa na área de trabalho de:**
- review ou comentário novo;
- aprovação no commit atual;
- CI que quebrou ou passou;
- merge.

O primeiro tick só registra o estado atual, sem avisar nada. O watch nunca faz merge nem comenta.

### `panel`: as sessões ao vivo (Copilot, Kiro e Codex)

```bash
fluxo panel                          # a tela
fluxo panel --text --view global     # uma vez, sem a tela
fluxo panel enable                   # só no Kiro V2: o agente fluxo vira o padrão
```

**As teclas:**
- `1`–`4` trocam a aba: sessão, tarefa, global e sessões;
- `↑↓` escolhem a sessão, e `Enter` vai até ela no tmux;
- `f` segue a sessão escolhida;
- `q` ou `Esc` saem.

### `promote`

Configure as bases no `init`, ou em `promote.targets` na config. O uso está na seção 3.8.

### `reviewer`: revisão lite antes da PR/MR

```
/revisar-pr PROJ-12            # a tarefa: o diff da branch contra a base, e o plano
/revisar-pr fix/PROJ-12-datas  # uma branch
/revisar-pr <url da PR>        # de outra pessoa: só leitura; não corrige nem comenta
```

**Cada rodada:**
- roda duas revisões isoladas, o `code-reviewer` e o `verificador` (o diff contra o plano);
- grava `~/.local/share/fluxo/revisoes/<id>/<data>-r<n>.md`, com o veredito.

São no máximo 2 rodadas. Na sua branch, um REPROVAR na primeira é corrigido e commitado antes da segunda.

### `validate`

O módulo traz o pr-reviewer. O uso está na seção 5.

## 5. Validação com o pr-reviewer

O pr-reviewer é um revisor que lê a premissa (o plano), revisa o código com agentes isolados e, com um runbook,
executa a mudança num stack local, duas vezes na base e duas na branch. O guia dele é o
[docs/guia.md do pr-reviewer-core](https://github.com/paulozy/pr-reviewer-core/blob/main/docs/guia.md).

### 5.1 Trazer o pr-reviewer

```bash
fluxo validate enable
```

**O que o `enable` faz:**
- **Clona o pr-reviewer** em `~/.local/share/fluxo/pr-reviewer`, por HTTPS, na tag que esta versão do fluxo
  fixa, e confere o sha do commit.
  - O repo é privado: o `gh auth setup-git` libera o acesso.
  - Com ssh: `git config --global url.git@github.com:paulozy/.insteadOf https://github.com/paulozy/`.
- **Cria `~/.config/pr-reviewer/config.json`, só se não existir,** com `workspaceRoot` = a pasta dos clones, as
  bases do `git.base` e o perfil `local` (o `--profile <nome>` troca).
- **Cria o comando `~/.local/bin/pr-review`, só se não houver um.**

Depois, `fluxo doctor` mostra a linha do pr-reviewer, e `fluxo validate exec -- doctor` roda o doctor dele.
**Esse doctor faz uma chamada de verdade ao agente,** de até US$ 0,30 no Claude, para testar a cerca contra
segredos.

**Para executar, além de revisar, o repo precisa de:**
- **um runbook,** `~/.config/pr-reviewer/profiles/local/runbooks/<repo>.json`, que descreve como subir o repo.
  Quem escreve é o `/receita`;
- **um arquivo de valores,** `~/.config/pr-reviewer/envs/<repo>.env`, com permissão 600 e só valores locais ou de
  sandbox. **Você** preenche esse arquivo. Nem a sessão nem os agentes abrem.

Sem runbook, a validação fica só com a revisão estática, e o `status` mostra `veredito: APROVÁVEL (só estática)`.

### 5.2 `/receita api`: o runbook escrito pela IA, com a sua confirmação

1. Um agente lê o código do repo (sem nenhum arquivo de env) e propõe o runbook.
2. A sessão mostra o resumo e o diff contra o runbook de hoje, e pergunta se pode gravar:
   - o resumo diz quais executáveis rodam, quais scripts do `package.json` (com o corpo), as imagens, as portas
     e as chaves;
   - um aviso aparece se um processo ou um health check sumiu.
3. Com o seu ok, ela grava com o comando que o pr-reviewer imprimiu:
   ```bash
   fluxo validate exec -- recipe api --apply=<proposta> --confirm=<hash>
   ```
   O hash prende a gravação à proposta que você viu: outra proposta, ou um runbook que mudou no meio, pedem outra
   confirmação.
4. Se o agente pediu chaves com valor seu, nasce um `envs/api.env` com elas comentadas, e a sessão diz quais
   preencher.
5. A sessão testa com `--check`, que sobe o stack sem agente e relata cada passo. Se falhar, ela faz no máximo um
   `--fix`, com a mesma confirmação.

### 5.3 `/validar PROJ-12`

1. **Confere** que o `doctor` mostra o pr-reviewer, e commita o que falta nas worktrees. O `--local` só vê o que
   está commitado.
2. **Roda** `fluxo validate start PROJ-12 --backend <a ferramenta>`: uma chamada só, com um `--local` por repo do
   plano, na branch da worktree e com a base do plano.
3. **Acompanha** com `fluxo validate status PROJ-12 --wait 100`. A saída 75 diz que ainda está rodando, e no fim
   a primeira linha é o veredito.

**Pelo terminal, os mesmos comandos:**

```bash
fluxo validate start PROJ-12                   # em segundo plano, desligado da sessão
fluxo validate status PROJ-12 --wait 540       # espera até 540 s; 75 = ainda rodando
fluxo validate stop PROJ-12                    # cancela; o pr-reviewer derruba o stack antes de sair
fluxo validate start --recipe api --check      # o --check da receita
fluxo validate status --recipe api
```

**As saídas do `status`:**
- **0:** terminou. A primeira linha é `veredito: …`, depois vêm os motivos e o caminho do `report.md`;
- **75:** ainda rodando, ou esperando a trava do stack;
- **1:** não rodou, ou terminou sem relatório.

**Por ferramenta:**
- **Codex:** docker e rede ficam fora do sandbox, então a sessão pede para rodar cada `fluxo validate` fora dele.
- **Copilot:** os agentes do pr-reviewer rodam no próprio Copilot, com o login do keychain (no Linux, a sessão
  precisa do D-Bus). Um modelo muito barato pode deixar a revisão incompleta, e ela nunca sai aprovada sem a leitura.

## 6. Duas ferramentas

Para usar o Copilot e, quando a cota acabar, o Claude, escolha as duas no `init`, com a padrão primeiro.

- `fluxo task PROJ-12` abre na padrão, e `fluxo task PROJ-12 --tool claude` abre na outra.
- **Para trocar no meio da tarefa,** feche a janela antiga e abra na outra:
  `fluxo task PROJ-12 --tool claude -p '/implementar PROJ-12'`. O plano, a branch e a worktree valem para as
  duas.
- **Um conflito numa ferramenta para o install inteiro,** nas duas. O caso típico é um agente seu com o mesmo
  nome de um do fluxo.

## 7. Problemas comuns

| Sintoma | Causa | O que fazer |
|---|---|---|
| `N conflito(s). Nada foi gravado`, saída 3 | um arquivo com o nome de um do fluxo, que não é dele, foi editado ou é link | renomeie o seu, ou `fluxo install --force`, que guarda um `.fluxo-bak-<data>` |
| `sem config em …: rode fluxo init` | ainda não houve `init` nesta máquina (ou noutro `XDG_CONFIG_HOME`) | `./fluxo init` |
| `o módulo X está desligado` | o comando é de um módulo desligado | `fluxo modules --enable X && fluxo install` |
| `o módulo panel requer tool:copilot\|kiro\|codex` | o panel não existe no Claude | tire o módulo, ou ponha outra ferramenta em `tools` |
| `fluxo: command not found` depois do install | o `~/.local/bin` não está no PATH (o `doctor` avisa) | ponha o `~/.local/bin` no PATH do shell |
| Codex ignora as instruções do fluxo | um `AGENTS.override.md` não vazio (o `doctor` avisa) | esvazie o override, ou junte as instruções nele |
| Codex lê outra pasta | o `CODEX_HOME` aponta para fora de `~/.codex`, onde o fluxo grava (o `doctor` avisa) | tire o `CODEX_HOME`, ou aponte-o para `~/.codex` |
| Hooks do Codex não rodam | o Codex só roda um hook aprovado em `/hooks`, de novo a cada mudança no `hooks.json` | aprove em `/hooks` quando o install avisar que mudou o `hooks.json` |
| `fluxo task` sai com 4 | não há vaga (`task.max`), ou a sessão já existe | sem vaga: feche uma, ou `--force`; sessão que já existe: vá até ela (`fluxo panel` ou tmux) |
| `fluxo task` sai com 3 | fora do tmux | rode dentro do tmux, ou `--target <sessão>:` |
| `fluxo lock acquire` sai com 4 | outra sessão está com o stack | espere; `fluxo lock status` diz quem é. Com o dono morto, o próximo `acquire` fica com ela |
| `validate enable` falha no clone | o git não tem acesso ao repo privado | `gh auth setup-git`, ou o `insteadOf` de ssh da seção 5.1 |
| `não atualizei <clone> …; nada foi descartado` | o clone do pr-reviewer tem mudança, branch ou stash seus | salve o que quiser, apague a pasta (ou deixe o clone limpo) e rode `fluxo validate enable` |
| `node vX: o pr-reviewer precisa do 22.18+` | Node antigo no PATH | instale o 22.18+ (ou o 23.6+) |
| `validate status` sai com 75 por muito tempo | a revisão ainda roda, ou espera a trava do stack | `fluxo lock status`; `fluxo validate stop <id>` cancela |
| `gitlab.host está vazio` | forge gitlab sem host | `fluxo init` de novo, com o host |

## 8. Atualizar

```bash
cd ~/fluxo && git pull
fluxo install           # grava só o que mudou
fluxo doctor
```

- **No Codex,** aprove de novo em `/hooks` se o install avisar que mudou o `hooks.json`.
- **Com o módulo `validate`,** quando a versão nova do fluxo fixa outra tag do pr-reviewer, o `doctor` avisa, e
  `fluxo validate enable` leva o clone para ela.

## 9. Referência rápida

### Comandos

| Comando | O que faz |
|---|---|
| `fluxo init` | cria ou refaz a config; sem `--tool` e `--forge`, num terminal, pergunta |
| `fluxo install [--dry-run] [--force] [-v]` | grava instruções, agentes e skills |
| `fluxo uninstall [--dry-run]` | tira o que o fluxo gravou; o que você editou fica |
| `fluxo doctor` | confere a máquina |
| `fluxo modules [--enable …] [--disable …]` | lista, liga e desliga módulos |
| `fluxo task <ID> [-p …] [--tool …] [--list]` | sessões no tmux (módulo `task`) |
| `fluxo msg tone \| copy` | o tom e a área de transferência (módulo `messages`) |
| `fluxo lock acquire \| release \| status \| break \| run` | a trava do stack (módulo `lock`) |
| `fluxo watch [tick \| enable \| disable]` | aviso das PRs/MRs (módulo `watch`) |
| `fluxo panel [enable \| disable] [--text]` | o painel (módulo `panel`) |
| `fluxo validate enable \| disable \| start \| status \| stop \| exec` | o pr-reviewer (módulo `validate`) |
| `fluxo package` | o zip para máquina sem git |
| `fluxo scan` | procura dado de empresa no repo do fluxo |

### Códigos de saída

| Código | Significado |
|---|---|
| 0 | ok |
| 1 | erro (no `doctor`, alguma FALHA) |
| 2 | config ou uso inválido |
| 3 | conflito no install, nada gravado; no `task`, fora do tmux |
| 4 | ocupado: sem vaga no `task`, trava de outra sessão no `lock` |
| 75 | `validate status`: ainda rodando |
| 130 | `init` cancelado com Ctrl-C ou EOF |

### Onde fica cada coisa

| Caminho | O quê |
|---|---|
| `~/.config/fluxo/config.json` | a config (respeita `XDG_CONFIG_HOME`) |
| `~/.config/fluxo/tom.md` | o seu tom, para o `/aviso` |
| `~/.local/share/fluxo/planos/<id>.md` | os planos (respeita `XDG_DATA_HOME`) |
| `~/.local/share/fluxo/revisoes/<id>/` | as revisões do `/revisar-pr` |
| `~/.local/share/fluxo/projetos/<repo>.md` | a memória de cada repo |
| `~/.local/share/fluxo/manifesto.json` | o que o install gravou, com o sha de cada arquivo |
| `~/.local/share/fluxo/estado/` | as sessões, a trava, o watch, os eventos do painel e as validações (pasta 700) |
| `~/.local/share/fluxo/pr-reviewer/` | o clone do pr-reviewer |
| `~/.local/bin/fluxo`, `~/.local/bin/pr-review` | os comandos |
| `~/.config/pr-reviewer/` | a config, os perfis e os `envs/` do pr-reviewer |
