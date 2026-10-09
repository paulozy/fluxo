---
name: revisar
description: "Revisão de código das mudanças: delega ao subagente code-reviewer e aplica o que ele apontar. Use depois de implementar ou modificar código."
argument-hint: "[o que revisar]"
---

# /revisar

O pedido: {{argumentos}}

1. Use o subagente `code-reviewer` sobre as mudanças: o `git diff` do que está aberto ou, numa
   branch de feature, o diff contra a base (`git diff origin/{{git.base}}...HEAD`).
2. Mostre os achados por prioridade (crítico, aviso, sugestão).
3. Aplique os críticos e os avisos, com teste quando mudar comportamento, e rode os testes pelo
   `test-runner`. Sugestão fica a critério do usuário.
