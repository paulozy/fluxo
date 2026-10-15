## Sessões paralelas (`fluxo task`)

- Para abrir outra sessão sem sair desta (uma tarefa paralela, ou uma sessão dedicada a algo longo),
  use `fluxo task`. Ela nasce numa janela do tmux, em segundo plano:
  - `fluxo task <ID> --tool {{ferramenta.id}}` abre a tarefa com `{{skill.planejar}} <ID>`, e ocupa vaga;
  - `fluxo task -p '<prompt>' --tool {{ferramenta.id}}` abre uma sessão avulsa, que não ocupa vaga. O
    prompt vai entre aspas simples: com aspas duplas, o shell trocaria um `$…` por nada.
  - O `--tool {{ferramenta.id}}` abre a sessão nesta mesma ferramenta: sem ele, abre na padrão da config,
    que pode ser a que o usuário deixou de usar.
- `fluxo task --list` mostra as sessões vivas abertas pelo fluxo. Saída 4 é "sem vaga" ou "o nome já
  tem sessão viva": não force sem perguntar ao usuário.
