## Entrega de uma feature

Toda feature segue estas fases, em ordem, sem pular nenhuma.

1. **Branch nova, por repositório, num worktree.** Em cada repo envolvido:
   ```bash
   R={{codigo_dir}}/<repo>    # o clone principal
   git -C $R fetch origin {{git.base}}
   git -C $R worktree add --no-track -b <branch> $R-<id> origin/{{git.base}}
   # primeiro push: git -C $R-<id> push -u origin <branch>
   ```
   - `<id>` identifica a tarefa: o número do card, ou um nome curto.
   - O `--no-track` é obrigatório. Sem ele, a branch nasce rastreando `origin/{{git.base}}`, e o
     próprio git sugere `git push origin HEAD:{{git.base}}`. **Nunca dê push direto na
     `{{git.base}}`.**
   - Parta da `{{git.base}}`, nunca da main.
   - Trabalhe só no worktree: duas tarefas em paralelo não podem disputar a mesma pasta.
   - Siga os padrões do projeto: nome de branch, estrutura de pastas, commits e estilo.
2. **Desenvolvimento**, seguindo os padrões existentes. Todo código novo tem teste, e os testes
   rodam (pelo `test-runner`) antes de a fase ficar pronta.
3. **Code review obrigatório** com o `code-reviewer` sobre o diff completo, mesmo com os testes
   verdes. Aplique os ajustes apontados.
4. **E2E local** (regras abaixo). Suba todos os projetos envolvidos, não só o principal.
5. **Ajustes pós-E2E**, se precisar.
6. **Conclusão** (`{{skill.concluir}}`), nesta ordem: resumo do que **de fato** foi feito, docs e CHANGELOG
   que o projeto já mantém, a {{forge.pr}} (com a sua aprovação), o rascunho do aviso ao time, a
   limpeza e o fechamento do plano.
