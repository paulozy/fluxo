---
name: concluir
description: "Fecha a entrega de uma tarefa (o passo 6 do fluxo): resumo do que foi feito de verdade, docs e CHANGELOG do projeto, abertura da PR ou MR com aprovação do usuário, rascunho do aviso ao time, limpeza e fechamento do plano."
argument-hint: "<id da tarefa>"
---

# /concluir

O pedido: {{argumentos}}

Rode depois do code review e do E2E. Siga esta ordem: o plano fecha por último, porque só então
existem os links.

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
   gh pr create --base {{git.base}} --head <branch> --title "<título>" --body-file <arquivo com a descrição>
   ```
<!-- senao -->
   ```bash
   glab mr create --target-branch {{git.base}} --source-branch <branch> --title "<título>" \
     --description "$(cat <arquivo com a descrição>)" --remove-source-branch=false
   ```
   Sem o `glab`: `git push -o merge_request.create -o merge_request.target={{git.base}} origin <branch>`,
   e a descrição vai à mão.
<!-- fim -->
   Escreva a descrição com a ferramenta de escrever arquivos em `{{dados_sh}}/tmp/<id>-descricao.md`
   (caminho expandido, fora do worktree), nunca entre aspas no shell: crase e `$(…)` digitados entre
   aspas duplas viram comando. (O `"$(cat arquivo)"` acima é seguro: o shell não reexecuta o que o
   `cat` devolve.)
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
   A branch continua no repo e no remoto.
6. **Feche o plano** (`{{dados}}/planos/<id>.md`):
   - troque o `status` para `concluido`;
   - acrescente `## Resumo`, com o que foi feito, as divergências do plano, os links da {{forge.pr}},
     os testes que rodaram e o resultado real, e o que o E2E cobriu e não cobriu;
   - marque no checklist **só** o que foi verificado.
   Nunca reescreva as seções do plano: elas são o registro do que se pensou antes.
7. Se a sessão aprendeu algo sobre o projeto que a próxima precisaria redescobrir (como rodar, uma
   armadilha), registre em `{{dados}}/projetos/<repo>.md`.
