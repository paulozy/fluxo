---
name: "pesquisar"
description: "Mapeia o código antes de agir: delega ao subagente researcher e traz um resumo para o usuário revisar. Use no começo de qualquer tarefa não trivial."
argument-hint: "<o que pesquisar>"
---
<!-- gerado pelo fluxo a partir de nucleo/skills/pesquisar/SKILL.md: edite a fonte e rode `fluxo install` -->
# /pesquisar

O pedido: o texto que veio junto do comando (se não veio nada, pergunte)

1. Se existir `~/.local/share/fluxo/projetos/<repo>.md`, leia antes: é o que as sessões anteriores aprenderam
   sobre o projeto.
2. Use o subagente `researcher` para mapear o código para esse pedido: arquivos relevantes com
   `arquivo:linha`, padrões, dependências, convenções de teste e perguntas em aberto.
3. Apresente o resumo e **pare**: o usuário revisa antes de seguir. As perguntas em aberto vão
   explícitas, não como suposição.
