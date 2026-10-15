# Fluxo de desenvolvimento

## Método: pesquisar → planejar → implementar

Para qualquer tarefa não trivial, siga o ciclo de três fases:

1. **Pesquisar** (`{{skill.pesquisar}}`): o subagente `researcher` mapeia o código antes de agir. O usuário
   revisa antes de seguir.
2. **Planejar** (`{{skill.planejar}}`): o `architect` pesquisa a solução estabelecida e o plano vai para um
   arquivo. Espere a aprovação.
3. **Implementar** (`{{skill.implementar}}`): fase por fase, com o `test-runner` depois de cada uma. Nunca
   tudo de uma vez.

Antes de implementar algo não trivial, consulte o `architect`: existe algoritmo, padrão ou
biblioteca estabelecida para o problema? Nunca resolva na força bruta.

## Contexto enxuto

- Mantenha a conversa entre 40% e 60% da janela de contexto.
- Delegue pesquisa e testes aos subagentes: a saída longa fica isolada neles.
- Resuma os resultados: preserve as decisões e descarte o ruído.
- Uma sessão é uma feature ou uma correção.

## Comunicação

- Afirmação sobre como uma ferramenta, framework ou recurso funciona vem com fonte: link da
  documentação oficial ou `arquivo:linha` do código.
- Na dúvida, diga que não tem certeza. Não apresente suposição como fato.
- Quando for corrigido, reconheça o erro antes de dar a informação certa.

## Perguntar é obrigatório

Dúvida sobre o funcionamento do projeto que não dá para inferir do código vira pergunta explícita
ao usuário, não suposição escrita no plano.

## Regras gerais

- Todo código novo tem teste, e os testes rodam (pelo `test-runner`) antes de a tarefa ficar pronta.
- Não adicione dependência sem confirmar com o usuário.
- Não mude a arquitetura sem discutir antes.
- Resolva o problema atual, não os hipotéticos.
