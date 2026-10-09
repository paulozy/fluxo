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

### WSL, módulos (v0.2.0)

| # | Teste | Esperado | Se falhar |
|---|---|---|---|
| 7 | `fluxo task -p "diga oi"` dentro do tmux | janela "avulsa" com o Kiro respondendo | |
| 8 | `echo "ação, já é" \| fluxo msg copiar` e colar no Teams/Slack | o acento intacto | o `clip.exe` não entende UTF-16: testar `powershell.exe Set-Clipboard` e avisar |

## Depois

Registre o resultado de cada linha no CHANGELOG, na versão que fechar a validação. O que divergiu
da doc vira correção com teste.
