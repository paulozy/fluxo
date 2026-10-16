# Changelog

## [Não lançado]

### Mudado

- **O `fluxo scan` pega mais grafias:** um termo proibido casa também em `snake_case` e `camelCase`
  (`FOO_BAR_URL`, `FooBar`), quebrado entre linhas, no nome do arquivo e no alvo de um symlink; o texto é
  normalizado com NFKC, e o UTF-16 é lido pelo BOM. O UUID colado num identificador e o id de workspace do
  Slack também são pegos. A tokenização é a mesma da varredura do pr-reviewer, e um teste de paridade
  confere os dois lados.
- **A lista de termos proibidos ganhou 9 termos.**

## [0.7.0] — 2026-10-15

### Adicionado

- **O `fluxo panel` em 4 abas,** como o agent-tree do Claude, mas com as etapas do próprio fluxo:
  - **sessão:** a seguida, com o último prompt, as ferramentas mais usadas, os subagentes e o log;
  - **tarefa:** o plano e o checklist do fluxo, as sessões da tarefa, os vereditos do `/revisar-pr` e as
    PRs/MRs que citam o id;
  - **global:** os alertas, a trava, o watch e as revisões das últimas 24 h;
  - **sessões:** a lista de hoje, com o estado colorido.
  - **Teclas:** `1`–`4`, `↑↓`, `Enter`, `f`, `Tab`, `←→`, `PgUp`/`PgDn`, `h` e `q`.
  - `fluxo panel --text --view session|task|global|list`, com `--task` e `--follow`.
  - Só stdlib (`curses`): cores quando o terminal tem, ASCII fora de UTF-8 e largura contada em colunas.
- **Eventos novos nos hooks:** o começo do prompt, o subagente (início, fim e, no Codex, as ferramentas de
  dentro dele) e a ferramenta que falhou (`postToolUseFailure` do Copilot).
- **O panel lê os eventos aos pedaços** (só o que entrou), e o resto a cada 5 s, só o que mudou.
- **O panel apaga os eventos com mais de 7 dias.** O `estado/` nasce 700.
- **O aviso de CI do watch grava o estado** (`ci: FAILURE|SUCCESS`), e não só o texto.

### Corrigido

- **Copilot:** "pergunta" vinha do `permissionRequest`, que dispara antes das regras que liberam sozinhas.
  Agora vem da `notification` com `permission_prompt` ou `elicitation_dialog`. O `permissionRequest` saiu dos
  hooks, e o de um hook antigo é ignorado.
- **Kiro V3:** um Stop que sai com 1 faz o Kiro começar outro turno. Os comandos do Kiro terminam em
  `|| true`.
- **Notificação e evento que o painel não guarda** não viram mais "erro de hook".
- **Uma sessão nova num pane reaproveitado não herda mais a tarefa de outra:** a tarefa vem pelo par pane +
  pane_pid.

## [0.6.0] — 2026-10-15

### Adicionado

- **OpenAI Codex CLI como quarta ferramenta:** `fluxo init --tool codex`, sozinho ou com as outras.
  Fontes: learn.chatgpt.com/docs e o código da tag `rust-v0.162.1`; o que só o código confirma está no
  roteiro de validação (C1–C14).
  - **Instruções em `~/.codex/AGENTS.md`.** O Codex lê um arquivo global só, então um seu dá conflito.
  - **Agentes em `~/.codex/agents/*.toml`.** O Codex não tem campo de ferramentas: o "só leitura" do
    architect, do researcher e do code-reviewer vai no texto.
  - **Skills em `~/.agents/skills`,** ou em `~/.codex/skills` quando o Copilot também está em `tools`,
    porque ele lê `~/.agents/skills` e as veria em dobro.
  - **O panel no Codex:** hooks em `~/.codex/hooks.json`, sem o PermissionRequest. O install avisa para
    aprová-los em `/hooks`.
  - **O `fluxo task --tool codex`** abre com `codex --no-daemon -- '$planejar <ID>'`.
    O `-p '/implementar …'` das outras ferramentas é recusado com a grafia certa, e `$HOME quebrou` é
    texto, e não uma sessão chamada home.
  - **O `doctor`** avisa de um `CODEX_HOME` fora de `~/.codex` e de um `AGENTS.override.md` não vazio,
    que tiraria as instruções do fluxo de cena.
- **As fontes chamam a skill pelo prefixo da ferramenta:** `{{skill.planejar}}` vira `/planejar`, ou
  `$planejar` no Codex. As descriptions de skills e agentes passam pelo template, e o `fluxo task` usa o
  prefixo no prompt padrão e no nome da avulsa.

### Mudou

- **O prompt do `fluxo task -p` vai entre aspas simples nas instruções:** com aspas duplas, o shell
  trocaria o `$planejar` do Codex por nada.

## [0.5.0] — 2026-10-15

### Mudou (incompatível)

**O que se digita passou para inglês.** O texto das instruções, os nomes das skills e dos agentes, as
mensagens e o estado continuam em português. Para atualizar, refaça a config com
`fluxo init … --force` e rode `fluxo install`: a config da 0.4 não é lida.

- **Subcomandos:**
  - `modulos`, `empacotar` e `varrer` viraram `modules`, `package` e `scan`.
  - `trava pegar/soltar/status/quebrar/rodar` virou `lock acquire/release/status/break/run`.
  - `vigia tick/loop/ligar/desligar` virou `watch tick/loop/enable/disable`.
  - `painel ligar/desligar` virou `panel enable/disable`.
  - `msg copiar/tom` virou `msg copy/tone`.
- **Flags:** `--ferramenta`→`--tool`, `--forcar`→`--force`, `--so`→`--os`, `--motor`→`--engine`,
  `--alvos`→`--promote-to`, `--codigo-dir`→`--code-dir`, `--saida`→`--output`, `--verboso`→`--verbose`,
  `--ligar/--desligar`→`--enable/--disable`, `--lista`→`--list`, `--nome`→`--name`,
  `--projeto`→`--project`, `--sessao`→`--session`, `--esperar`→`--wait`, `--intervalo`→`--interval`,
  `--texto`→`--text`, `--evento`→`--event`. Também `FLUXO_SESSAO`→`FLUXO_SESSION`.
- **Config:**
  - `ferramenta` virou `tools`, uma lista.
  - `versao`, `modulos`, `codigo_dir` e `so` viraram `version`, `modules`, `code_dir` e `os`.
  - `vigia.intervalo`, `promover.alvos` e `kiro.motor` viraram `watch.interval`, `promote.targets` e
    `kiro.engine`.
  - `modelos` virou `models`, por ferramenta, com os níveis `strong/medium/light`.
  - `task.lancar` virou `task.launch`, também por ferramenta.
  - Os valores `rascunho` e `nenhum` viraram `draft` e `none`.
- **Módulos:** `mensagens`, `trava`, `vigia`, `painel`, `revisor` e `promover` viraram `messages`, `lock`,
  `watch`, `panel`, `reviewer` e `promote`. Com eles mudaram as janelas do tmux, as units
  `fluxo-watch.*` e o rótulo `dev.fluxo.watch` do launchd.

### Adicionado

- **Várias ferramentas ao mesmo tempo:** `fluxo init --tool copilot --tool claude` instala as duas, e a
  primeira é a padrão.
  - `fluxo task <ID> --tool claude` abre na outra. Uma ferramenta fora de `tools` sai com 2, antes de
    tocar no tmux.
  - As instruções de cada ferramenta trazem o próprio `--tool`, para que uma sessão que abre outra a abra
    nela mesma.
  - Módulo com `requer` entra só nas ferramentas que atendem: o `panel` vale para o Copilot e o Kiro, e
    não para o Claude.
  - **O `doctor`** confere o binário de cada uma. A falta da padrão é FALHA, a das outras é AVISO.
  - **O `task --list`** mostra a ferramenta de cada sessão, e a mensagem de sessão viva com o mesmo nome
    diz em qual ferramenta ela está.
  - Tirar uma ferramenta de `tools` e rodar `fluxo install` remove os arquivos dela.
  - **Aviso no install:** quando ele vai apagar o agente `fluxo` do Kiro que o `panel enable` deixou como
    padrão, pede para rodar `fluxo panel disable` antes.
- **Testes de nome:**
  - os comandos gravados nos hooks, nas units, no plist e no loop do WSL passam pelo parser;
  - nenhuma fonte ou doc cita a grafia antiga;
  - cada módulo acha o próprio `lib/fluxo/<n>.py`.

### Corrigido

- **O `fluxo hook` nunca imprime nem falha, mesmo com argumento que o parser recusa.** Antes, uma flag
  desconhecida imprimia o uso e saía com 2. Entre um `git pull` e o próximo `install`, o hook instalado
  manda as flags da versão anterior.

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
