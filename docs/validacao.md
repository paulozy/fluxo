# Roteiro de validação na máquina real

Os formatos do Copilot e do Kiro foram tirados da documentação oficial em 08/10/2026. Alguns pontos
a doc não confirma, e só o teste na máquina responde. Cada divergência volta para o código, com
teste.

## Preparação (as duas máquinas)

```bash
python3 --version                 # 3.9 ou mais novo
python3 -m unittest discover -s tests   # dentro do repo (ou do zip extraído): tem que passar
./fluxo init --ferramenta … --forge … && ./fluxo install && fluxo doctor
```

No Mac, se o `python3` abrir o instalador das ferramentas de linha de comando, instale-as primeiro
(`xcode-select --install`).

**Canário:** acrescente uma linha `Palavra-canário: abacaxi-azul` ao fim das instruções geradas, da
skill `revisar` e do agente `code-reviewer`. Nos passos abaixo, pergunte pela palavra para saber se
o arquivo foi carregado. Depois rode `fluxo install --forcar` para voltar ao original, ou desfaça a
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
| 9 | `fluxo modulos --ligar task mensagens trava && fluxo install`, depois `fluxo task -p "diga oi"` dentro do tmux | uma janela "avulsa" com o Copilot já respondendo | as flags `-n` + `--session-id` + `-i` juntas não funcionam: ajuste `task.lancar` na config (ex.: `["copilot", "-i", "{prompt}"]`) e avise para corrigir o adaptador |
| 10 | `fluxo task --lista` | a sessão aberta | |
| 11 | `echo "ação, já é" \| fluxo msg copiar` e colar no Teams | o acento intacto | o `pbcopy` precisa de outro locale |
| 12 | `fluxo trava pegar --id t1` em dois panes | o segundo sai com 4; depois de fechar o primeiro pane, o segundo pega | |

## WSL, Kiro CLI (motor V2)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 1 | `fluxo doctor` | `kiro agent validate` OK nos 7 agentes | o nome de alguma ferramenta do V2 está errado: corrigir `adaptadores/ferramenta/kiro.json` (a doc cita também `fs_read`, `fs_write` e `execute_bash`) |
| 2 | Na sessão padrão: "qual a palavra-canário das suas instruções?" | `abacaxi-azul` | o steering global não carrega no agente padrão: criar um agente `fluxo` com `resources` e `kiro-cli agent set-default` |
| 3 | `/skills` ou `/pesquisar …` | as skills aparecem | idem: `resources` com `skill://` |
| 4 | Pedir algo que use o `researcher` | o subagente é chamado | o V2 pode não delegar: as skills passam a dizer "faça você mesmo, no papel de researcher" |
| 5 | `kiro-cli chat --no-interactive --agent code-reviewer "palavra-canário?"` | `abacaxi-azul` (exige `KIRO_API_KEY`) | |
| 6 | `python3 --version` na distro | 3.9 ou mais novo | o Ubuntu 20.04 traz o 3.8: instalar um Python mais novo ou atualizar a distro |

### Mac, revisor e GitLab (v0.3.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 13 | `/revisar-pr <id>` numa branch com plano | relatório em `~/.local/share/fluxo/revisoes/<id>/`, com veredito | o subagente `verificador` não carregou: conferir `/agent` |
| 14 | Numa MR de teste: `glab api --hostname <host> -X POST "projects/<grupo%2Fprojeto>/merge_requests/<iid>/notes" -F body=@resposta.md` | a nota aparece com o **conteúdo** do arquivo | saiu o caminho literal: apague a nota e avise (o `glab` dessa versão não lê `@arquivo`) |
| 15 | `/pr-feedback <url da MR>` com um comentário de teste | mostra a correção e a resposta, e só publica depois do seu ok | |

### Mac, vigia e painel (v0.3.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 16 | `fluxo modulos --ligar vigia && fluxo install && fluxo vigia tick` (com a VPN) | "linha de base: N itens" | `glab` sem login, ou `gitlab.host` errado |
| 17 | Rode `osascript -e 'display notification "oi" with title "fluxo"'` no Terminal, libere as notificações do Script Editor, e depois `fluxo vigia ligar` | a notificação aparece; o `launchctl print gui/$UID/dev.fluxo.vigia` mostra o agente | o LaunchAgent não alcança a sessão: avisar |
| 18 | Peça um review de teste numa MR sua | uma notificação em até 5 min | |
| 19 | `fluxo modulos --ligar painel && fluxo install`; abra uma sessão com `fluxo task -p "oi"` e rode `fluxo painel` | a sessão aparece com o nome da janela; Enter leva até ela | os hooks não disparam: conferir `~/.copilot/hooks/fluxo.json`, o sandbox do Copilot (`sandbox.userPolicy`) e o `~/.local/share/fluxo/estado/hook-erros.log` |
| 20 | No `eventos-*.jsonl`, o campo `sessao` de uma sessão do Copilot | o id da sessão | veio `pane-%N`: o payload não traz `sessionId` |
| 21 | Aprove uma MR de teste depois do último push | aviso "aprovada por …" | sem aviso: o `approved_by[].approved_at` não vem nessa versão do GitLab, ou o pipeline é de "merged results" (o sha do pipeline é o do merge, e o CI também não avisa) |

### Mac, promoção (v0.4.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 22 | `fluxo init … --alvos staging,main --forcar`, `fluxo modulos --ligar promover && fluxo install` | a seção "Depois do QA: promoção para staging, main" nas instruções | |
| 23 | Num repo de teste: `git fetch origin merge-requests/<iid>/head:refs/promover/<iid>` de uma MR mergeada | o ref é criado | o GitLab da empresa não expõe o ref da MR: avisar (a skill precisa de outro caminho para os commits) |
| 24 | O `diff_refs.base_sha` de uma MR mergeada com squash: `git rev-list --reverse --no-merges <base_sha>..refs/promover/<iid>` | os commits da MR, e só eles | avisar |
| 25 | `/promover <id>` numa tarefa de teste já mergeada na develop | mostra as MRs para staging e main antes de publicar; os commits vazios aparecem como pulados | |

### WSL, módulos (v0.2.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 7 | `fluxo task -p "diga oi"` dentro do tmux | janela "avulsa" com o Kiro respondendo | |
| 8 | `echo "ação, já é" \| fluxo msg copiar` e colar no Teams/Slack | o acento intacto | o `clip.exe` não entende UTF-16: testar `powershell.exe Set-Clipboard` e avisar |

### WSL, vigia e painel (v0.3.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 9 | `fluxo vigia tick`, depois `fluxo vigia ligar` dentro do tmux | uma janela "vigia" com um tick a cada 5 min | |
| 10 | Peça um review de teste numa PR sua | um toast do Windows ("Windows PowerShell") | o toast está bloqueado: fica o `tmux display-message`; avisar |
| 11 | `fluxo modulos --ligar painel && fluxo install && fluxo painel ligar`, depois uma sessão nova do Kiro | o agente `fluxo` é o padrão e a sessão aparece no `fluxo painel` | o V2 não aceitou o agente: rodar `kiro-cli agent validate --path ~/.kiro/agents/fluxo.json` e mandar o erro |
| 12 | Na sessão: "qual a palavra-canário das suas instruções?" (com o agente `fluxo`) | a palavra | os `resources` com `~` não expandem: trocar para caminho absoluto |
| 13 | Uma sessão do Kiro aberta no tmux: o painel mostra o pane dela? | sim | o hook do Kiro não herda o `TMUX_PANE`: o painel mostra a pasta e não deixa pular |
| 14 | Com o agente `fluxo` como padrão: `/mcp` | os mesmos MCPs de antes | o `includeMcpJson` não tem esse efeito: avisar |
| 15 | No `~/.local/share/fluxo/estado/eventos-*.jsonl`, o campo `sessao` | o id da sessão do Kiro | veio `pane-%N`: o payload não traz `session_id` |
| 16 | Kiro V3 (`--v3`): com o painel, os hooks de `~/.kiro/hooks/fluxo.json` disparam | eventos no arquivo | conferir o nome dos triggers e a unidade do `timeout` (segundos?) |

## Depois

Registre o resultado de cada linha no CHANGELOG, na versão que fechar a validação. O que divergiu
da doc vira correção com teste.
