# Changelog

## [0.4.0] — 2026-10-09

### Adicionado

- **Módulo `promover`:** a skill `/promover <id>` (a versão genérica da daqui) e a seção "Depois do QA"
  nas instruções.
  - Leva os commits que entraram na base para as bases de `promover.alvos` (ex.: staging e main), em
    branches `<head>-<alvo>` por cherry-pick, com uma PR/MR por base.
  - Os commits saem da faixa da própria PR/MR: no GitLab, `merge-requests/<iid>/head` e
    `diff_refs.base_sha`, que vale para merge, squash e rebase.
  - Commit vazio é pulado; conflito, teste falhando no último alvo ou PR/MR com outra tarefa param e
    perguntam.
  - Mostra títulos e descrições antes do push; o merge é sempre do usuário ou do time.
  - `fluxo init --alvos staging,main` grava `promover.alvos`, validado só com o módulo ligado.

### Corrigido

- **O vigia não conta mais PR/MR de repositório arquivado:**
  - GitHub: `repository.isArchived`, agora exigido na query por teste;
  - GitLab: `non_archived=true` na lista, e a MR que já estava aberta quando o projeto foi arquivado
    sai da conta.
- **O `fluxo init --forcar` não apaga mais os módulos ligados** nem as opções deles (`modelos`, `task`,
  `vigia`, `promover`).
- **O `/concluir` grava o título da PR/MR em arquivo, como a descrição, e no GitLab roda o `glab mr
  create` com `cd` no worktree** (o host vem do remote).
- **O `/concluir` no GitLab não força mais `--remove-source-branch=false`:** apagar a branch no
  merge segue o padrão do projeto. A limpeza diz como conferir os commits quando a branch remota já
  foi apagada.

## [0.3.0] — 2026-10-09

As fases 3, 4 e 5 do plano, numa versão só: revisor lite, vigia e painel.

### Adicionado

- **Módulo `revisor`:** a skill `/revisar-pr` e o agente `verificador` (o diff contra o plano:
  premissa, critérios de aceite com teste, escopo, riscos).
  - Duas revisões isoladas por rodada, com o diff `<base>...<sha>` explícito para os dois.
  - Revisor que não citou o diff invalida a rodada (PRECISA DE HUMANO).
  - No máximo 2 revisões: a correção entre elas é commitada, para a rodada 2 e o push a enxergarem.
  - PR/MR de outra pessoa é só leitura (sem rodar os testes dela).
  - É a versão lite do pr-reviewer: sem stack e sem E2E automático.
- **`/concluir`:** com o revisor ligado, a revisão vem antes do resumo e da PR/MR, e só se segue com
  APROVÁVEL ou com o ok do usuário sobre o relatório.
- **`/pr-feedback` para GitLab:**
  - discussões com `--paginate` e respostas por arquivo (`-F body=@`), sempre com `--hostname`;
  - o projeto com todas as barras codificadas;
  - a menção `@login` entra no texto que o usuário aprova antes de publicar.
- **`gitlab.host` vazio é recusado no `install`:** antes, ele virava `--hostname` sem valor.
- **Módulo `vigia`:** `fluxo vigia [tick|loop|ligar|desligar]`, que só avisa.
  - **Forge:** GitHub pelo GraphQL do `gh`, com todas as abertas mais as 30 mergeadas recentes em
    consultas separadas. GitLab pelo `glab api --hostname`, com MRs, discussões, aprovações (válidas
    se vieram depois da última versão), pipeline e merge (a MR que sumiu da lista).
  - **Avisos:** cada novidade tem uma chave e é avisada uma vez. O primeiro tick é a linha de base.
    Uma leva grande vira um aviso só.
  - **Erros:** sem rede (VPN) o erro é registrado uma vez; estado corrompido recomeça pela base;
    dois ticks juntos não rodam (flock).
  - **Aviso na tela:**
    - Mac: `osascript` com o texto por argv;
    - WSL: toast do PowerShell com o texto por variável de ambiente e `WSLENV`;
    - Linux: `notify-send`.

    Se nenhum funcionar, vai `tmux display-message`. O título da PR nunca entra num script.
  - **Agendamento:**
    - Mac: LaunchAgent;
    - Linux: systemd `--user`;
    - WSL: uma janela do tmux com `fluxo vigia loop`, porque o systemd não segura o WSL.
- **Módulo `painel`:** `fluxo hook` + `fluxo painel` (curses, só stdlib), para Copilot e Kiro.
  - **O hook** grava um evento normalizado por linha (`os.write` em O_APPEND). Nunca imprime e sempre
    sai com 0: no Kiro o stdout entra no contexto, e no Copilot um `preToolUse` que falha nega a
    ferramenta. Por isso o painel não usa `preToolUse`.
  - **Onde ficam os hooks:**
    - Copilot: `~/.copilot/hooks/fluxo.json`;
    - Kiro V3: `~/.kiro/hooks/fluxo.json`;
    - Kiro V2: um agente `fluxo` com os hooks embutidos e o steering e as skills nos `resources`. O
      `fluxo painel ligar` o torna o padrão e guarda o anterior.
  - **A tela** mostra os estados trabalhando, sua vez, pergunta (só no Copilot), parada e acabou.
    Enter leva até o pane (`tmux switch-client`, nunca `send-keys`); `--texto` imprime uma vez.

### Fora desta versão

- O `task` usar os eventos do painel para "parada há 30 min não ocupa vaga" (estava no plano): hoje a
  vaga só é liberada quando o pane morre.

## [0.2.0] — 2026-10-09

### Adicionado

- **Módulos**, ligados por config: `fluxo modulos [--ligar X] [--desligar X]`. O `modulo.json` pode
  exigir uma condição (`requer`), e o `install` recusa a config que não a atende.
- **`task`:** `fluxo task [ID] [-p] [-n]` abre a sessão da ferramenta numa janela do tmux.
  - Tarefa com ID ocupa vaga (`task.max`); a sessão avulsa não ocupa.
  - Uma sessão viva por nome.
  - Saber se a sessão vive é ver se o pane (o par pane + pid) está vivo: sem `/proc`, roda no Mac.
  - O comando de abertura vem do adaptador (`lancar`) ou da config.
- **`mensagens`:** a skill `/aviso` e `fluxo msg copiar|tom`.
  - Copia por stdin (UTF-8 no Mac com `LANG`; UTF-16 com BOM no `clip.exe`) e guarda uma cópia.
  - O `tom.md` local aprende com os ajustes aprovados.
- **`trava`:** `fluxo trava pegar|soltar|status|quebrar|rodar`.
  - Dono num arquivo, com flock curto e sem processo segurando a trava.
  - **Dono vivo:** vale o pid da ferramenta (`CLAUDE_PID`, ou a ferramenta que rodou o fluxo), com a
    marca de início (`/proc` no Linux; `ps -o lstart` com `LC_ALL=C TZ=UTC0` no Mac). Sem pid, vale o
    par pane + `pane_pid` do tmux.
  - O `rodar` repassa SIGTERM/SIGHUP ao comando e só solta a trava depois que ele termina.
  - O `quebrar` só sugere o `docker compose down`, não roda nada.
- **`doctor`:** confere tmux e área de transferência quando os módulos estão ligados.

### Corrigido

- **O teste com o tmux de verdade derrubou o servidor do tmux do usuário** (09/10, durante o
  desenvolvimento). Dentro do tmux, a variável `TMUX` vale mais que o `TMUX_TMPDIR`. Agora o teste
  tira `TMUX`/`TMUX_PANE` do ambiente e só roda `kill-server` depois de conferir que o socket é o da
  pasta temporária.

## [0.1.0] — 2026-10-08

Primeira versão: o núcleo do fluxo e o `install` para Claude Code, Copilot CLI e Kiro CLI.

### Adicionado

- **`fluxo init`, `install`, `uninstall`, `doctor`, `empacotar` e `varrer`.** Rodam no Python 3.9 só
  com a stdlib e gravam tudo no nível do usuário.
- **Install com manifesto:**
  - nunca pisa em arquivo alheio nem em arquivo editado; com conflito, não grava nada (saída 3);
  - `--forcar` grava com backup;
  - adota o que já está igual e remove o que saiu da config;
  - rodar de novo não regrava nada.
- **Núcleo:**
  - instruções do processo (método, entrega, E2E, nada no repo da empresa, git);
  - 7 subagentes;
  - skills `/pesquisar`, `/planejar`, `/implementar`, `/revisar`, `/commitar`, `/concluir` e
    `/pr-feedback` (só GitHub).
- **Adaptadores:**
  - ferramenta: Claude, Copilot e Kiro, com os motores V2 e V3;
  - forge: GitHub e GitLab;
  - template estrito com blocos condicionais.
- **Varredura de dado de empresa:**
  - acha ids, caminhos de home e termos proibidos guardados só como hash;
  - roda no teste, no pre-commit e antes do `empacotar`.
- **`docs/validacao.md`:** o roteiro para o Mac (Copilot) e o WSL (Kiro).

### Pendente

- Validar na máquina real os pontos que a documentação não confirma: os nomes de ferramenta do
  Kiro V2, o `$ARGUMENTS` no Copilot, e se o agente padrão do Kiro carrega o steering e as skills
  globais.
