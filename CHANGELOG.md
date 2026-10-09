# Changelog

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
