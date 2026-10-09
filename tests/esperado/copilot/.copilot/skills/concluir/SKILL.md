---
name: "concluir"
description: "Fecha a entrega de uma tarefa (o passo 6 do fluxo): resumo do que foi feito de verdade, docs e CHANGELOG do projeto, abertura da PR ou MR com aprovação do usuário, rascunho do aviso ao time, limpeza e fechamento do plano."
argument-hint: "<id da tarefa>"
---
<!-- gerado pelo fluxo a partir de nucleo/skills/concluir/SKILL.md: edite a fonte e rode `fluxo install` -->
# /concluir

O pedido: o texto que veio junto do comando (se não veio nada, pergunte)

Rode depois do code review e do E2E. Siga esta ordem: o plano fecha por último, porque só então
existem os links.

1. **Resumo do que foi de fato implementado**, não do que foi planejado. Se uma fase caiu ou mudou,
   diga qual e por quê. Compare com o plano em `~/.local/share/fluxo/planos/<id>.md`.
2. **Docs e CHANGELOG que o projeto já mantém.** Atualize-os e commite **antes** da MR, para
   eles entrarem nela e não virarem commit solto. Não crie doc que o time não usa, nem arquivo de IA
   no repo (veja "Nada no repo da empresa").
3. **Push e MR.** Push da branch da feature (`git push -u origin <branch>`). Monte título e
   descrição no padrão do repo: o que mudou, por quê, como testar. Sem assinatura de IA. Mostre ao
   usuário e peça aprovação (faça a pergunta na conversa e pare o turno até a resposta). Com a aprovação:
   ```bash
   ( cd "<worktree>" && glab mr create --target-branch develop --source-branch '<branch>' \
       --title "$(cat "$HOME/.local/share/fluxo/tmp/<id>-<repo>.titulo")" \
       --description "$(cat "$HOME/.local/share/fluxo/tmp/<id>-<repo>.md")" )
   ```
   O `cd` (num subshell) faz o `glab` achar o host pelo remote.
   Sem o `glab`: `git push -o merge_request.create -o merge_request.target=develop origin <branch>`,
   e a descrição vai à mão.
   Escreva o título e a descrição com a ferramenta de escrever arquivos em
   `$HOME/.local/share/fluxo/tmp/<id>-<repo>.titulo` e `.md` (caminho expandido, fora do worktree), nunca entre
   aspas no shell: crase e `$(…)` digitados entre aspas duplas viram comando. (O `"$(cat arquivo)"`
   acima é seguro: o shell não reexecuta o que o `cat` devolve.)
4. **Rascunho do aviso ao time.** Uma mensagem curta, no tom do usuário (informal, direto, sem
   formato de e-mail), com o link da MR e uma linha sobre o que muda. Mostre na conversa,
   pronta para copiar. **Nada é enviado** por você.
5. **Limpeza.** `docker compose -p <repo>-<id> down -v` nos stacks desta tarefa (confira o nome com
   `docker compose ls`: nunca derrube o projeto do seu ambiente de dev). Remova os worktrees
   (`git worktree remove`) depois de conferir que cada um está limpo e sem commits à frente do remoto.
   Se a MR já foi mergeada e o forge apagou a branch remota, compare o `git rev-parse HEAD`
   do worktree com o último commit da MR mergeada (no merge por squash, os commits não
   aparecem iguais na `develop`): sendo o mesmo, nada se perde. A branch local continua no clone. A
   remota segue o padrão do projeto.
6. **Feche o plano** (`~/.local/share/fluxo/planos/<id>.md`):
   - troque o `status` para `concluido`;
   - acrescente `## Resumo`, com o que foi feito, as divergências do plano, os links da MR,
     os testes que rodaram e o resultado real, e o que o E2E cobriu e não cobriu;
   - marque no checklist **só** o que foi verificado.
   Nunca reescreva as seções do plano: elas são o registro do que se pensou antes.
7. Se a sessão aprendeu algo sobre o projeto que a próxima precisaria redescobrir (como rodar, uma
   armadilha), registre em `~/.local/share/fluxo/projetos/<repo>.md`.
