## Validação antes da {{forge.pr}} (`{{skill.validar}}`)

- `{{skill.validar}} <id>` revisa a tarefa com o pr-reviewer: confere a premissa do plano, revisa o código com
  agentes isolados e, quando o repo tem runbook, executa a mudança num stack local, na base e na branch. O
  veredito é REPROVAR, PRECISA DE HUMANO ou APROVÁVEL.
- `{{skill.receita}} <repo>` escreve, com a confirmação do usuário, o runbook que ensina o pr-reviewer a subir o
  stack do repo, e testa esse runbook subindo o stack.
- Os dois rodam em segundo plano (`fluxo validate start`) e são acompanhados com `fluxo validate status --wait 100`
  (rodado de novo até terminar): uma revisão leva de 4 a 20 minutos, mais que o limite de um comando da ferramenta.
- Os agentes do pr-reviewer nunca leem arquivo de env, e você também não. As chaves de integração ficam em
  `~/.config/pr-reviewer/envs/<repo>.env`, que só o usuário edita e que só o pr-reviewer lê, para subir o stack.
- O pr-reviewer vem para a máquina com `fluxo validate enable`, que o usuário roda (ele clona por git, com o
  acesso dele).
<!-- se module:reviewer -->
- Com o `{{skill.validar}}` no fluxo, o `{{skill.revisar-pr}}` fica para quando o usuário pedir.
<!-- fim -->
<!-- se tool:copilot -->
- No Copilot, o pr-reviewer ainda não roda os agentes dele aqui: ele usa o backend da config dele (o padrão é o
  Claude), com o custo desse backend.
<!-- fim -->
