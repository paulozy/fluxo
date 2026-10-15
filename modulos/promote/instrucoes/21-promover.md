## Depois do QA: promoção para {{promover.alvos}}

- Quando o usuário disser que o QA aprovou uma tarefa, siga a skill `{{skill.promover}}` com o `<id>` dela. Ela
  leva os commits que entraram na `{{git.base}}` para {{promover.alvos}}, por cherry-pick, com uma
  {{forge.pr}} por base.
- O merge dessas {{forge.prs}} é sempre do usuário ou do time. A de `{{promover.ultimo}}` vai para
  produção.
