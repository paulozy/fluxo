---
name: "commitar"
description: "Commita as mudanças pelo subagente committer, no padrão do repo e sem nenhum trailer de IA. Use quando o usuário pedir commit."
---
<!-- gerado pelo fluxo a partir de nucleo/skills/commitar/SKILL.md: edite a fonte e rode `fluxo install` -->
# $commitar

1. Use o subagente `committer` para commitar as mudanças atuais, no padrão de mensagem do repo.
2. Confira com `git log -1 --format=%B` que não entrou `Co-authored-by` nem menção a ferramenta de
   IA. Se entrou, e o commit ainda não foi enviado, corrija com `git commit --amend`.
3. Não dê push: o push é da conclusão (`$concluir`) ou de um pedido explícito do usuário.
