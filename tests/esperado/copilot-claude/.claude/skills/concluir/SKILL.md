---
name: "concluir"
description: "Fecha a entrega de uma tarefa (o passo 6 do fluxo): resumo do que foi feito de verdade, docs e CHANGELOG do projeto, abertura da PR ou MR com aprovação do usuário, rascunho do aviso ao time, limpeza e fechamento do plano."
argument-hint: "<id da tarefa>"
---
<!-- gerado pelo fluxo a partir de nucleo/skills/concluir/SKILL.md: edite a fonte e rode `fluxo install` -->
# /concluir

O pedido: $ARGUMENTS

Rode depois do code review e do E2E. Siga esta ordem: o plano fecha por último, porque só então
existem os links.

**Antes de tudo, a revisão:** siga a skill `/revisar-pr` com o `<id>` da tarefa. Continue só com
APROVÁVEL, ou com o ok explícito do usuário sobre o relatório. Se a revisão mudou código, o resumo,
as docs e o CHANGELOG abaixo descrevem o código novo, e o fechamento do plano registra o que mudou
depois do E2E.

1. **Resumo do que foi de fato implementado**, não do que foi planejado. Se uma fase caiu ou mudou,
   diga qual e por quê. Compare com o plano em `~/.local/share/fluxo/planos/<id>.md`.
2. **Docs e CHANGELOG que o projeto já mantém.** Atualize-os e commite **antes** da PR, para
   eles entrarem nela e não virarem commit solto. Não crie doc que o time não usa, nem arquivo de IA
   no repo (veja "Nada no repo da empresa").
3. **Push e PR.** Push da branch da feature (`git push -u origin <branch>`). Monte título e
   descrição no padrão do repo: o que mudou, por quê, como testar. Sem assinatura de IA. Mostre ao
   usuário e peça aprovação (use a ferramenta AskUserQuestion e espere a resposta). Com a aprovação:
   ```bash
   gh pr create --base develop --head '<branch>' --title "$(cat "$HOME/.local/share/fluxo/tmp/<id>-<repo>.titulo")" \
     --body-file "$HOME/.local/share/fluxo/tmp/<id>-<repo>.md"
   ```
   Escreva o título e a descrição com a ferramenta de escrever arquivos em
   `$HOME/.local/share/fluxo/tmp/<id>-<repo>.titulo` e `.md` (caminho expandido, fora do worktree), nunca entre
   aspas no shell: crase e `$(…)` digitados entre aspas duplas viram comando. (O `"$(cat arquivo)"`
   acima é seguro: o shell não reexecuta o que o `cat` devolve.)
4. **Aviso ao time:** siga a skill `/aviso` (tom do usuário, aprovação, cópia para a área de
   transferência), com o link da PR e uma linha sobre o que muda. **Nada é enviado** por você.
5. **Limpeza.** `docker compose -p <repo>-<id> down -v` nos stacks desta tarefa (confira o nome com
   `docker compose ls`: nunca derrube o projeto do seu ambiente de dev). Remova os worktrees
   (`git worktree remove`) depois de conferir que cada um está limpo e sem commits à frente do remoto.
   Se a PR já foi mergeada e o forge apagou a branch remota, compare o `git rev-parse HEAD`
   do worktree com o último commit da PR mergeada (no merge por squash, os commits não
   aparecem iguais na `develop`): sendo o mesmo, nada se perde. A branch local continua no clone. A
   remota segue o padrão do projeto.
6. **Feche o plano** (`~/.local/share/fluxo/planos/<id>.md`):
   - troque o `status` para `concluido`;
   - acrescente `## Resumo`, com o que foi feito, as divergências do plano, os links da PR,
     os testes que rodaram e o resultado real, e o que o E2E cobriu e não cobriu;
   - marque no checklist **só** o que foi verificado.
   Nunca reescreva as seções do plano: elas são o registro do que se pensou antes.
7. Se a sessão aprendeu algo sobre o projeto que a próxima precisaria redescobrir (como rodar, uma
   armadilha), registre em `~/.local/share/fluxo/projetos/<repo>.md`.
