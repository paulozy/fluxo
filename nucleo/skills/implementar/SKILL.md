---
name: implementar
description: "Implementa uma tarefa já planejada, fase por fase: worktree, testes pelo test-runner depois de cada fase e code review no fim. Use depois do {{skill.planejar}} aprovado."
argument-hint: "<id da tarefa>"
---

# {{skill.implementar}}

O pedido: {{argumentos}}

1. **O plano.** Se houver plano em `{{dados}}/planos/<id>.md`, ele precisa estar com
   `status: aprovado`. Em rascunho, pare e peça a aprovação. Sem plano numa tarefa não trivial,
   comece pelo `{{skill.planejar}}`.
2. **Worktree** (passo 1 da entrega): branch nova a partir de `origin/{{git.base}}`, com
   `--no-track`, em cada repo envolvido.
3. **Fase por fase**, seguindo os padrões do código em volta. Toda mudança de comportamento tem
   teste. Depois de cada fase, use o `test-runner`; se falhar, corrija antes de seguir.
4. **No fim, o `code-reviewer`** sobre o diff completo da branch. Aplique os ajustes e rode os
   testes de novo.
5. **E2E local** quando a mudança tem comportamento observável (regras nas instruções do fluxo).
6. **Atualize o checklist do plano** só com o que foi verificado. Se algo saiu diferente do plano,
   anote para o resumo do `{{skill.concluir}}`.
