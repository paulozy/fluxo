---
applyTo: "**"
---
<!-- gerado pelo fluxo a partir de nucleo/instrucoes: edite a fonte e rode `fluxo install` -->

# Fluxo de desenvolvimento

## Método: pesquisar → planejar → implementar

Para qualquer tarefa não trivial, siga o ciclo de três fases:

1. **Pesquisar** (`/pesquisar`): o subagente `researcher` mapeia o código antes de agir. O usuário
   revisa antes de seguir.
2. **Planejar** (`/planejar`): o `architect` pesquisa a solução estabelecida e o plano vai para um
   arquivo. Espere a aprovação.
3. **Implementar** (`/implementar`): fase por fase, com o `test-runner` depois de cada uma. Nunca
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

## Entrega de uma feature

Toda feature segue estas fases, em ordem, sem pular nenhuma.

1. **Branch nova, por repositório, num worktree.** Em cada repo envolvido:
   ```bash
   R=~/code/<repo>    # o clone principal
   git -C $R fetch origin develop
   git -C $R worktree add --no-track -b <branch> $R-<id> origin/develop
   # primeiro push: git -C $R-<id> push -u origin <branch>
   ```
   - `<id>` identifica a tarefa: o número do card, ou um nome curto.
   - O `--no-track` é obrigatório. Sem ele, a branch nasce rastreando `origin/develop`, e o
     próprio git sugere `git push origin HEAD:develop`. **Nunca dê push direto na
     `develop`.**
   - Parta da `develop`, nunca da main.
   - Trabalhe só no worktree: duas tarefas em paralelo não podem disputar a mesma pasta.
   - Siga os padrões do projeto: nome de branch, estrutura de pastas, commits e estilo.
2. **Desenvolvimento**, seguindo os padrões existentes. Todo código novo tem teste, e os testes
   rodam (pelo `test-runner`) antes de a fase ficar pronta.
3. **Code review obrigatório** com o `code-reviewer` sobre o diff completo, mesmo com os testes
   verdes. Aplique os ajustes apontados.
4. **E2E local** (regras abaixo). Suba todos os projetos envolvidos, não só o principal.
5. **Ajustes pós-E2E**, se precisar.
6. **Conclusão** (`/concluir`), nesta ordem: resumo do que **de fato** foi feito, docs e CHANGELOG
   que o projeto já mantém, a PR (com a sua aprovação), o rascunho do aviso ao time, a
   limpeza e o fechamento do plano.

## Depois do QA: promoção para main

- Quando o usuário disser que o QA aprovou uma tarefa, siga a skill `/promover` com o `<id>` dela. Ela
  leva os commits que entraram na `develop` para main, por cherry-pick, com uma
  PR por base.
- O merge dessas PRs é sempre do usuário ou do time. A de `main` vai para
  produção.

## Sessões paralelas (`fluxo task`)

- Para abrir outra sessão sem sair desta (uma tarefa paralela, ou uma sessão dedicada a algo longo),
  use `fluxo task`. Ela nasce numa janela do tmux, em segundo plano:
  - `fluxo task <ID> --tool copilot` abre a tarefa com `/planejar <ID>`, e ocupa vaga;
  - `fluxo task -p '<prompt>' --tool copilot` abre uma sessão avulsa, que não ocupa vaga. O
    prompt vai entre aspas simples: com aspas duplas, o shell trocaria um `$…` por nada.
  - O `--tool copilot` abre a sessão nesta mesma ferramenta: sem ele, abre na padrão da config,
    que pode ser a que o usuário deixou de usar.
- `fluxo task --list` mostra as sessões vivas abertas pelo fluxo. Saída 4 é "sem vaga" ou "o nome já
  tem sessão viva": não force sem perguntar ao usuário.

## E2E local

- **Antes da PR, o `/validar`:** ele revisa e executa a mudança num stack local, já com
  estas regras. As regras abaixo valem para o E2E que você roda à mão.
- **Nunca use o `.env` do projeto.** Ele costuma apontar para ambientes reais (sandbox, homologação,
  produção), e um E2E que o lê fala com eles sem avisar. Monte um `.env.e2e` descartável a partir do
  `.env.example` e do que o `docker-compose.yml` documenta, e exclua-o do git localmente:
  `echo .env.e2e >> "$(git rev-parse --git-path info/exclude)"`. Num worktree, `.git` é um arquivo, e
  esse comando acha o `info/exclude` certo.
- **Suba a infra pelo `docker-compose.yml` da raiz, com um nome de projeto próprio:**
  `docker compose -p <repo>-<id> up -d`. Se o compose define `name:`, o worktree e o clone principal
  dividiriam o mesmo projeto, e um `down -v` apagaria os volumes do seu ambiente de dev. Confira com
  `docker compose ls` antes de derrubar.
- **Uma sessão por vez no stack: `fluxo lock`.** As portas e a memória não se dividem.
  - Antes de subir: `fluxo lock acquire --id <id> --project <repo>-<id>`. Saída 0 é sua; saída 4 é
    de outra sessão. Nesse caso, avise o usuário e espere: não force nem quebre a trava. Saída 2 é
    "não sei quem é esta sessão" (fora do tmux): siga a regra do `docker ps` abaixo e avise o usuário.
  - Depois do `down`: `fluxo lock release`.
  - `fluxo lock status` mostra quem está com ela.
- **Uma sessão por vez no stack.** Antes de subir, confira com `docker ps` que nenhuma outra sessão
  sua está com containers de pé: as portas e a memória não se dividem.
- **Containers só enquanto são usados.** Derrube (`docker compose -p <repo>-<id> down -v`) assim que o
  E2E acabar.
- **Serviços complementares: suba só os necessários.** Quando um serviço não é preciso para o caminho
  sob teste, prefira stub ou mock.
- **Confirme com quem você está falando.** Valide a identidade pela forma da resposta do healthcheck,
  não pelo fato de a porta responder. Confira também se não há uma instância do projeto rodando com o
  `.env` real.
- **Reproduza o bug antes de provar a correção.** O roteiro tem que falhar no estado anterior e passar
  no novo; senão, ele não testou a mudança.

## Validação antes da PR (`/validar`)

- `/validar <id>` revisa a tarefa com o pr-reviewer: confere a premissa do plano, revisa o código com
  agentes isolados e, quando o repo tem runbook, executa a mudança num stack local, na base e na branch. O
  veredito é REPROVAR, PRECISA DE HUMANO ou APROVÁVEL.
- `/receita <repo>` escreve, com a confirmação do usuário, o runbook que ensina o pr-reviewer a subir o
  stack do repo, e testa esse runbook subindo o stack.
- Os dois rodam em segundo plano (`fluxo validate start`) e são acompanhados com `fluxo validate status --wait 100`
  (rodado de novo até terminar): uma revisão leva de 4 a 20 minutos, mais que o limite de um comando da ferramenta.
- Os agentes do pr-reviewer nunca leem arquivo de env, e você também não. As chaves de integração ficam em
  `~/.config/pr-reviewer/envs/<repo>.env`, que só o usuário edita e que só o pr-reviewer lê, para subir o stack.
- O pr-reviewer vem para a máquina com `fluxo validate enable`, que o usuário roda (ele clona por git, com o
  acesso dele).
- Com o `/validar` no fluxo, o `/revisar-pr` fica para quando o usuário pedir.
- No Copilot, o pr-reviewer ainda não roda os agentes dele aqui: ele usa o backend da config dele (o padrão é o
  Claude), com o custo desse backend.

## Nada no repo da empresa

Esta configuração é do usuário, não do time. O repo só recebe a entrega.

- Nunca crie, dentro do projeto: `CLAUDE.md`, `AGENTS.md`, `.claude/`, `.kiro/`,
  `.github/copilot-instructions.md`, `.github/agents/`, `.github/skills/` ou `.github/instructions/`.
  Se o time **já** mantém um desses, ele é doc do time: só mexa nele se a mudança pedir, e pergunte
  antes.
- Arquivo local (`.env.e2e`, notas, saída de ferramenta) vai para o exclude local do git, nunca para o
  `.gitignore`, que é do time: `echo <arquivo> >> "$(git rev-parse --git-path info/exclude)"`. Funciona
  também dentro de worktree.
- O que vale lembrar do projeto entre sessões (como rodar, armadilhas, decisões) fica em
  `~/.local/share/fluxo/projetos/<repo>.md`, fora do repo. Leia esse arquivo no começo da sessão, se existir, e
  atualize-o quando aprender algo que a próxima sessão precisaria redescobrir.
- Os planos ficam em `~/.local/share/fluxo/planos/`, e os temporários (descrição de PR, resposta de
  review) em `~/.local/share/fluxo/tmp/`, nunca dentro do worktree.
- O `~` desses caminhos é o seu HOME. Em comando de shell e ao gravar arquivo, use o caminho expandido
  (`$HOME/.local/share/fluxo`, ou o absoluto): o shell não expande `~` entre aspas nem depois de `=@`.
- O que entra no repo é o código, os testes, e as docs e o CHANGELOG que o projeto **já** mantém.

## Git

- **Os commits são do usuário.** Nunca adicione `Co-authored-by` de IA (Claude, Copilot, Kiro ou
  outra), nem menção a ferramenta de IA no autor, no committer ou na mensagem. Depois de commitar,
  confira com `git log -1 --format=%B`.
- A mesma regra vale para a descrição da PR: sem assinatura do tipo "gerado por…".
- A mensagem segue o padrão do repo (`git log --oneline -10`): idioma, formato e escopo.
- Push só na branch da feature, com `-u origin <branch>` no primeiro. Nunca na `develop` nem na
  main.
- Antes do push, procure segredo no diff (token, chave, senha, URL com credencial). Se aparecer, não
  commite: tire o segredo e avise o usuário.

## Subagentes

Use-os para manter a conversa enxuta: a saída longa (pesquisa, testes, revisão) fica com eles.

- `architect`: Software architect and solution researcher.
- `code-reviewer`: Reviews code for quality, security, and best practices.
- `committer`: Creates git commits.
- `debugger`: Debugging specialist for errors, test failures, and unexpected behavior.
- `doc-writer`: Technical documentation writer.
- `researcher`: Codebase researcher.
- `test-runner`: Runs tests and reports results.
- `verificador`: Checks a branch diff against the approved plan: does it deliver the premise, which acceptance criteria are not covered by a test, what went out of scope, and which risks have no coverage.
