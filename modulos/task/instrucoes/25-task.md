## Sessões paralelas (`fluxo task`)

- Para abrir outra sessão sem sair desta (uma tarefa paralela, ou uma sessão dedicada a algo longo),
  use `fluxo task`. Ela nasce numa janela do tmux, em segundo plano:
  - `fluxo task <ID>` abre a tarefa com `/planejar <ID>`, e ocupa vaga;
  - `fluxo task -p "<prompt>"` abre uma sessão avulsa, que não ocupa vaga.
- `fluxo task --lista` mostra as sessões vivas abertas pelo fluxo. Saída 4 é "sem vaga" ou "o nome já
  tem sessão viva": não force sem perguntar ao usuário.
