## Nada no repo da empresa

Esta configuração é do usuário, não do time. O repo só recebe a entrega.

- Nunca crie, dentro do projeto: `CLAUDE.md`, `AGENTS.md`, `.claude/`, `.kiro/`,
  `.github/copilot-instructions.md`, `.github/agents/`, `.github/skills/` ou `.github/instructions/`.
  Se o time **já** mantém um desses, ele é doc do time: só mexa nele se a mudança pedir, e pergunte
  antes.
- Arquivo local (`.env.e2e`, notas, saída de ferramenta) vai para o exclude local do git, nunca para o
  `.gitignore`, que é do time: `echo <arquivo> >> "$(git rev-parse --git-path info/exclude)"`. Funciona
  também dentro de worktree.
- O que vale lembrar do projeto entre sessões (como rodar, armadilhas, decisões) fica em
  `{{dados}}/projetos/<repo>.md`, fora do repo. Leia esse arquivo no começo da sessão, se existir, e
  atualize-o quando aprender algo que a próxima sessão precisaria redescobrir.
- Os planos ficam em `{{dados}}/planos/`, e os temporários (descrição de {{forge.pr}}, resposta de
  review) em `{{dados}}/tmp/`, nunca dentro do worktree.
- O `~` desses caminhos é o seu HOME. Em comando de shell e ao gravar arquivo, use o caminho expandido
  (`{{dados_sh}}`, ou o absoluto): o shell não expande `~` entre aspas nem depois de `=@`.
- O que entra no repo é o código, os testes, e as docs e o CHANGELOG que o projeto **já** mantém.
