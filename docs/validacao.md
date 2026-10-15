# Roteiro de validação na máquina real

Os formatos do Copilot e do Kiro foram tirados da documentação oficial em 08/10/2026. Alguns pontos
a doc não confirma, e só o teste na máquina responde. Cada divergência volta para o código, com
teste.

## Preparação (as duas máquinas)

```bash
python3 --version                 # 3.9 ou mais novo
python3 -m unittest discover -s tests   # dentro do repo (ou do zip extraído): tem que passar
./fluxo init --tool … --forge … && ./fluxo install && fluxo doctor
```

No Mac, se o `python3` abrir o instalador das ferramentas de linha de comando, instale-as primeiro
(`xcode-select --install`).

**Canário:** acrescente uma linha `Palavra-canário: abacaxi-azul` ao fim das instruções geradas, da
skill `revisar` e do agente `code-reviewer`. Nos passos abaixo, pergunte pela palavra para saber se
o arquivo foi carregado. Depois rode `fluxo install --force` para voltar ao original, ou desfaça a
edição à mão.

## Mac, Copilot CLI

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 1 | Na sessão: "qual a palavra-canário das suas instruções?" | `abacaxi-azul` | o `~/.copilot/instructions/*.instructions.md` não carrega: testar sem o `applyTo`, ou o `copilot-instructions.md` |
| 2 | `/skills` | as 6 skills do fluxo (sem `pr-feedback` com GitLab) | conferir `~/.copilot/skills` e o `COPILOT_SKILLS_DIRS` |
| 3 | `/agent` | os 7 agentes | conferir `~/.copilot/agents/*.agent.md` |
| 4 | `copilot -p "diga a palavra-canário do seu agente" --agent=code-reviewer` | `abacaxi-azul` | o formato do agente |
| 5 | `/pesquisar onde fica o login` | o pedido aparece no texto da skill | o `{{argumentos}}` precisa de outro mapeamento (`$ARGUMENTS`?) |
| 6 | `/planejar` numa tarefa pequena | plano em `~/.local/share/fluxo/planos/`, e a sessão para pedindo aprovação | |
| 7 | `/commitar` num repo de teste | `git log -1 --format=%B` sem `Co-authored-by` | reforçar a regra no committer |
| 8 | `fluxo doctor` com e sem a VPN | sem VPN: AVISO "VPN desligada?", e nenhuma FALHA | |

### Mac, módulos (v0.2.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 9 | `fluxo modules --enable task messages lock && fluxo install`, depois `fluxo task -p "diga oi"` dentro do tmux | uma janela "avulsa" com o Copilot já respondendo | as flags `-n` + `--session-id` + `-i` juntas não funcionam: ajuste `task.launch` na config (ex.: `{"copilot": ["copilot", "-i", "{prompt}"]}`) e avise para corrigir o adaptador |
| 10 | `fluxo task --list` | a sessão aberta | |
| 11 | `echo "ação, já é" \| fluxo msg copy` e colar no Teams | o acento intacto | o `pbcopy` precisa de outro locale |
| 12 | `fluxo lock acquire --id t1` em dois panes | o segundo sai com 4; depois de fechar o primeiro pane, o segundo pega | |

## WSL, Kiro CLI (motor V2)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 1 | `fluxo doctor` | `kiro agent validate` OK nos 7 agentes | o nome de alguma ferramenta do V2 está errado: corrigir `adaptadores/ferramenta/kiro.json` (a doc cita também `fs_read`, `fs_write` e `execute_bash`) |
| 2 | Na sessão padrão: "qual a palavra-canário das suas instruções?" | `abacaxi-azul` | o steering global não carrega no agente padrão: criar um agente `fluxo` com `resources` e `kiro-cli agent set-default` |
| 3 | `/skills` ou `/pesquisar …` | as skills aparecem | idem: `resources` com `skill://` |
| 4 | Pedir algo que use o `researcher` | o subagente é chamado | o V2 pode não delegar: as skills passam a dizer "faça você mesmo, no papel de researcher" |
| 5 | `kiro-cli chat --no-interactive --agent code-reviewer "palavra-canário?"` | `abacaxi-azul` (exige `KIRO_API_KEY`) | |
| 6 | `python3 --version` na distro | 3.9 ou mais novo | o Ubuntu 20.04 traz o 3.8: instalar um Python mais novo ou atualizar a distro |

### Mac, reviewer e GitLab (v0.3.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 13 | `/revisar-pr <id>` numa branch com plano | relatório em `~/.local/share/fluxo/revisoes/<id>/`, com veredito | o subagente `verificador` não carregou: conferir `/agent` |
| 14 | Numa MR de teste: `glab api --hostname <host> -X POST "projects/<grupo%2Fprojeto>/merge_requests/<iid>/notes" -F body=@resposta.md` | a nota aparece com o **conteúdo** do arquivo | saiu o caminho literal: apague a nota e avise (o `glab` dessa versão não lê `@arquivo`) |
| 15 | `/pr-feedback <url da MR>` com um comentário de teste | mostra a correção e a resposta, e só publica depois do seu ok | |

### Mac, watch e panel (v0.3.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 16 | `fluxo modules --enable watch && fluxo install && fluxo watch tick` (com a VPN) | "linha de base: N itens" | `glab` sem login, ou `gitlab.host` errado |
| 17 | Rode `osascript -e 'display notification "oi" with title "fluxo"'` no Terminal, libere as notificações do Script Editor, e depois `fluxo watch enable` | a notificação aparece; o `launchctl print gui/$UID/dev.fluxo.watch` mostra o agente | o LaunchAgent não alcança a sessão: avisar |
| 18 | Peça um review de teste numa MR sua | uma notificação em até 5 min | |
| 19 | `fluxo modules --enable panel && fluxo install`; abra uma sessão com `fluxo task -p "oi"` e rode `fluxo panel` | a sessão aparece com o nome da janela; Enter leva até ela | os hooks não disparam: conferir `~/.copilot/hooks/fluxo.json`, o sandbox do Copilot (`sandbox.userPolicy`) e o `~/.local/share/fluxo/estado/hook-erros.log` |
| 20 | No `eventos-*.jsonl`, o campo `sessao` de uma sessão do Copilot | o id da sessão | veio `pane-%N`: o payload não traz `sessionId` |
| 21 | Aprove uma MR de teste depois do último push | aviso "aprovada por …" | sem aviso: o `approved_by[].approved_at` não vem nessa versão do GitLab, ou o pipeline é de "merged results" (o sha do pipeline é o do merge, e o CI também não avisa) |

### Mac, promoção (v0.4.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 22 | `fluxo init … --promote-to staging,main --force`, `fluxo modules --enable promote && fluxo install` | a seção "Depois do QA: promoção para staging, main" nas instruções | |
| 23 | Num repo de teste: `git fetch origin merge-requests/<iid>/head:refs/promover/<iid>` de uma MR mergeada | o ref é criado | o GitLab da empresa não expõe o ref da MR: avisar (a skill precisa de outro caminho para os commits) |
| 24 | O `diff_refs.base_sha` de uma MR mergeada com squash: `git rev-list --reverse --no-merges <base_sha>..refs/promover/<iid>` | os commits da MR, e só eles | avisar |
| 25 | `/promover <id>` numa tarefa de teste já mergeada na develop | mostra as MRs para staging e main antes de publicar; os commits vazios aparecem como pulados | |

### Mac, duas ferramentas (v0.5.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 26 | `fluxo init --tool copilot --tool claude … --force && fluxo install && fluxo doctor` | `~/.copilot` e `~/.claude` com o fluxo; o doctor mostra as duas, com o copilot de padrão | conflito no `~/.claude`: o install não grava nada, nem no `~/.copilot`; renomeie os seus arquivos de mesmo nome |
| 27 | `fluxo task -p "diga oi" --tool claude` dentro do tmux | janela "avulsa" com o Claude respondendo | as flags do `lancar` do Claude não funcionam nessa versão: ajuste `task.launch.claude` e avise para corrigir o adaptador |
| 28 | Numa sessão do Claude, peça uma sessão paralela | ela roda `fluxo task … --tool claude` | abriu no Copilot: as instruções do Claude não trazem o `--tool` |
| 29 | Tire o `claude` de `tools` e rode `fluxo install` | o `~/.claude` perde os arquivos do fluxo; o `~/.copilot` fica igual | |
| 30 | Com as duas instaladas, `/skills` no Copilot | cada skill do fluxo uma vez, do `~/.copilot/skills` | aparece em dobro: essa versão lê também o `~/.claude/skills` (a [doc](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills) só cita `~/.copilot/skills` e `~/.agents/skills`, mas há [relato](https://github.com/github/copilot-cli/issues/2161) do contrário). Anote qual cópia ele usa: a do Claude traz o `$ARGUMENTS` |

### WSL, módulos (v0.2.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 7 | `fluxo task -p "diga oi"` dentro do tmux | janela "avulsa" com o Kiro respondendo | |
| 8 | `echo "ação, já é" \| fluxo msg copy` e colar no Teams/Slack | o acento intacto | o `clip.exe` não entende UTF-16: testar `powershell.exe Set-Clipboard` e avisar |

### WSL, watch e panel (v0.3.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 9 | `fluxo watch tick`, depois `fluxo watch enable` dentro do tmux | uma janela "watch" com um tick a cada 5 min | |
| 10 | Peça um review de teste numa PR sua | um toast do Windows ("Windows PowerShell") | o toast está bloqueado: fica o `tmux display-message`; avisar |
| 11 | `fluxo modules --enable panel && fluxo install && fluxo panel enable`, depois uma sessão nova do Kiro | o agente `fluxo` é o padrão e a sessão aparece no `fluxo panel` | o V2 não aceitou o agente: rodar `kiro-cli agent validate --path ~/.kiro/agents/fluxo.json` e mandar o erro |
| 12 | Na sessão: "qual a palavra-canário das suas instruções?" (com o agente `fluxo`) | a palavra | os `resources` com `~` não expandem: trocar para caminho absoluto |
| 13 | Uma sessão do Kiro aberta no tmux: o painel mostra o pane dela? | sim | o hook do Kiro não herda o `TMUX_PANE`: o painel mostra a pasta e não deixa pular |
| 14 | Com o agente `fluxo` como padrão: `/mcp` | os mesmos MCPs de antes | o `includeMcpJson` não tem esse efeito: avisar |
| 15 | No `~/.local/share/fluxo/estado/eventos-*.jsonl`, o campo `sessao` | o id da sessão do Kiro | veio `pane-%N`: o payload não traz `session_id` |
| 16 | Kiro V3 (`--v3`): com o painel, os hooks de `~/.kiro/hooks/fluxo.json` disparam | eventos no arquivo | conferir o nome dos triggers e a unidade do `timeout` (segundos?) |

## Depois

Registre o resultado de cada linha no CHANGELOG, na versão que fechar a validação. O que divergiu
da doc vira correção com teste.
