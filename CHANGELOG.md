# Changelog

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
  - O dono vive se o pane do tmux dele existe, ou se o pid informado está vivo.
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
