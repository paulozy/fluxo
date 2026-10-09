---
name: concluir
description: "Fecha a entrega de uma tarefa (o passo 6 do fluxo): resumo do que foi feito de verdade, docs e CHANGELOG do projeto, abertura da PR ou MR com aprovação do usuário, rascunho do aviso ao time, limpeza e fechamento do plano."
argument-hint: "<id da tarefa>"
---

# /concluir

O pedido: {{argumentos}}

Rode depois do code review e do E2E. Siga esta ordem: o plano fecha por último, porque só então
existem os links.
<!-- se modulo:revisor -->

**Antes de tudo, a revisão:** siga a skill `/revisar-pr` com o `<id>` da tarefa. Continue só com
APROVÁVEL, ou com o ok explícito do usuário sobre o relatório. Se a revisão mudou código, o resumo,
as docs e o CHANGELOG abaixo descrevem o código novo, e o fechamento do plano registra o que mudou
depois do E2E.
<!-- fim -->

1. **Resumo do que foi de fato implementado**, não do que foi planejado. Se uma fase caiu ou mudou,
   diga qual e por quê. Compare com o plano em `{{dados}}/planos/<id>.md`.
2. **Docs e CHANGELOG que o projeto já mantém.** Atualize-os e commite **antes** da {{forge.pr}}, para
   eles entrarem nela e não virarem commit solto. Não crie doc que o time não usa, nem arquivo de IA
   no repo (veja "Nada no repo da empresa").
3. **Push e {{forge.pr}}.** Push da branch da feature (`git push -u origin <branch>`). Monte título e
   descrição no padrão do repo: o que mudou, por quê, como testar. Sem assinatura de IA. Mostre ao
   usuário e peça aprovação ({{pergunte}}). Com a aprovação:
<!-- se forge:github -->
   ```bash
   gh pr create --base {{git.base}} --head '<branch>' --title "$(cat "{{dados_sh}}/tmp/<id>-<repo>.titulo")" \
     --body-file "{{dados_sh}}/tmp/<id>-<repo>.md"
   ```
<!-- senao -->
   ```bash
   ( cd "<worktree>" && glab mr create --target-branch {{git.base}} --source-branch '<branch>' \
       --title "$(cat "{{dados_sh}}/tmp/<id>-<repo>.titulo")" \
       --description "$(cat "{{dados_sh}}/tmp/<id>-<repo>.md")" )
   ```
   O `cd` (num subshell) faz o `glab` achar o host pelo remote.
   Sem o `glab`: `git push -o merge_request.create -o merge_request.target={{git.base}} origin <branch>`,
   e a descrição vai à mão.
<!-- fim -->
   Escreva o título e a descrição com a ferramenta de escrever arquivos em
   `{{dados_sh}}/tmp/<id>-<repo>.titulo` e `.md` (caminho expandido, fora do worktree), nunca entre
   aspas no shell: crase e `$(…)` digitados entre aspas duplas viram comando. (O `"$(cat arquivo)"`
   acima é seguro: o shell não reexecuta o que o `cat` devolve.)
<!-- se modulo:mensagens -->
4. **Aviso ao time:** siga a skill `/aviso` (tom do usuário, aprovação, cópia para a área de
   transferência), com o link da {{forge.pr}} e uma linha sobre o que muda. **Nada é enviado** por você.
<!-- senao -->
4. **Rascunho do aviso ao time.** Uma mensagem curta, no tom do usuário (informal, direto, sem
   formato de e-mail), com o link da {{forge.pr}} e uma linha sobre o que muda. Mostre na conversa,
   pronta para copiar. **Nada é enviado** por você.
<!-- fim -->
5. **Limpeza.** `docker compose -p <repo>-<id> down -v` nos stacks desta tarefa (confira o nome com
   `docker compose ls`: nunca derrube o projeto do seu ambiente de dev). Remova os worktrees
   (`git worktree remove`) depois de conferir que cada um está limpo e sem commits à frente do remoto.
   Se a {{forge.pr}} já foi mergeada e o forge apagou a branch remota, compare o `git rev-parse HEAD`
   do worktree com o último commit da {{forge.pr}} mergeada (no merge por squash, os commits não
   aparecem iguais na `{{git.base}}`): sendo o mesmo, nada se perde. A branch local continua no clone. A
   remota segue o padrão do projeto.
6. **Feche o plano** (`{{dados}}/planos/<id>.md`):
   - troque o `status` para `concluido`;
   - acrescente `## Resumo`, com o que foi feito, as divergências do plano, os links da {{forge.pr}},
     os testes que rodaram e o resultado real, e o que o E2E cobriu e não cobriu;
   - marque no checklist **só** o que foi verificado.
   Nunca reescreva as seções do plano: elas são o registro do que se pensou antes.
7. Se a sessão aprendeu algo sobre o projeto que a próxima precisaria redescobrir (como rodar, uma
   armadilha), registre em `{{dados}}/projetos/<repo>.md`.
