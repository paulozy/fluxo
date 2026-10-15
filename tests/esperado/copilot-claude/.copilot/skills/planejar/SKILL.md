---
name: "planejar"
description: "Planeja uma tarefa antes de implementar: pesquisa o código e a solução estabelecida (architect), grava o plano num arquivo fora do repo e só segue depois da aprovação do usuário. Use no lugar do modo de plano nativo."
argument-hint: "<id da tarefa> <o que fazer>"
---
<!-- gerado pelo fluxo a partir de nucleo/skills/planejar/SKILL.md: edite a fonte e rode `fluxo install` -->
# /planejar

O pedido: o texto que veio junto do comando (se não veio nada, pergunte)

## Antes de escrever

1. **O discovery é do usuário.** Num bug, o plano só presta com o erro real: mensagem, console,
   payload ou passo a passo para reproduzir. Se o pedido não traz isso, peça antes de planejar
   (faça a pergunta na conversa e pare o turno até a resposta). Análise estática sozinha costuma achar o componente certo e a causa errada.
2. **Identificador:** a chave do card, se houver; senão, um nome curto (`<repo>-<assunto>`). O plano
   fica em `~/.local/share/fluxo/planos/<id>.md`. Se o arquivo já existir, atualize-o em vez de criar outro.
3. **Pesquise o código** (subagente `researcher`) e **a solução** (subagente `architect`). O plano só
   vale com referências `arquivo:linha` conferidas, inclusive dentro das dependências, quando a causa
   está no contrato de uma biblioteca.
4. Dúvida sobre regra de negócio que o código não responde vira pergunta, não suposição no plano.

## O arquivo

```markdown
---
id: <id>
status: rascunho
repos: [<repo>, ...]
base: develop
criado: <AAAA-MM-DD>
---
# <id> — <descrição curta>

## Contexto
O que o pedido trazia e o que o discovery acrescentou. Diga onde a análise sozinha não chegou.

## Causa raiz (bug) ou premissa (feature)
Com o trecho de código que prova.

## Escopo real
Os consumidores afetados (quase sempre mais que o pedido).

## Plano de implementação
Branch `<tipo>/<id>-<slug>` a partir de `develop`. Fases numeradas, cada uma com o teste que a fecha.

## Fora do escopo
O que você achou e não vai fazer, com o motivo.

## Critério de aceite
- [ ] …

## Riscos
Inclusive o que o E2E não vai cobrir.

## Checklist do fluxo
- [ ] plano aprovado
- [ ] branch e worktree
- [ ] implementação com testes
- [ ] code review
- [ ] E2E local
- [ ] PR aberta
- [ ] aviso ao time
- [ ] limpeza
- [ ] resumo
```

## Aprovação

Depois de gravar, mostre o caminho e um resumo de 5 a 10 linhas. Pergunte: "aprova o plano em
`<caminho>`?" (faça a pergunta na conversa e pare o turno até a resposta).

- **Aprovado:** troque `status: rascunho` por `status: aprovado` e marque "plano aprovado" no
  checklist. Depois, siga com `/implementar`.
- **Pedido de mudança:** ajuste o arquivo e pergunte de novo.
- O usuário também pode aprovar editando o arquivo (`status: aprovado`). Releia o arquivo antes de
  seguir.

**Nunca implemente com o plano em rascunho.**
