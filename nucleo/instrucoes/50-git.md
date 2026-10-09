## Git

- **Os commits são do usuário.** Nunca adicione `Co-authored-by` de IA (Claude, Copilot, Kiro ou
  outra), nem menção a ferramenta de IA no autor, no committer ou na mensagem. Depois de commitar,
  confira com `git log -1 --format=%B`.
- A mesma regra vale para a descrição da {{forge.pr}}: sem assinatura do tipo "gerado por…".
- A mensagem segue o padrão do repo (`git log --oneline -10`): idioma, formato e escopo.
- Push só na branch da feature, com `-u origin <branch>` no primeiro. Nunca na `{{git.base}}` nem na
  main.
- Antes do push, procure segredo no diff (token, chave, senha, URL com credencial). Se aparecer, não
  commite: tire o segredo e avise o usuário.
