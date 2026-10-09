# fluxo

O fluxo de desenvolvimento (pesquisar → planejar → implementar, worktree, testes, code review, E2E,
PR e conclusão) para usar com **Claude Code**, **GitHub Copilot CLI** ou **Kiro CLI**. Tudo é
instalado no nível do usuário (`~/.claude`, `~/.copilot`, `~/.kiro`): **nada entra no repo da
empresa**.

## Requisitos

- Python 3.9 ou mais novo, só a biblioteca padrão. O do Xcode, no Mac, serve.
- A ferramenta (`claude`, `copilot` ou `kiro-cli`) e o `git`.
- Para abrir PR/MR e usar o `/pr-feedback`: `gh` (GitHub) ou `glab` (GitLab).

## Instalar

```bash
git clone git@github.com:paulozy/fluxo.git ~/fluxo   # ou o zip, veja abaixo
cd ~/fluxo
./fluxo init --ferramenta copilot --forge gitlab --gitlab-host gitlab.empresa.com --codigo-dir ~/code
./fluxo install --dry-run     # mostra o que vai gravar
./fluxo install
fluxo doctor                  # o install cria ~/.local/bin/fluxo
```

O `init` grava `~/.config/fluxo/config.json`:

| Chave | Valores | Para quê |
|---|---|---|
| `ferramenta` | `claude`, `copilot`, `kiro` | onde instalar e em que formato |
| `forge` | `github`, `gitlab` | `gh` ou `glab`; PR ou MR |
| `kiro.motor` | `v2` (padrão), `v3` | nomes de ferramenta dos agentes do Kiro |
| `git.base` | `develop` | de onde saem as branches |
| `codigo_dir` | `~/code` | onde ficam os clones |
| `chat`, `tracker`, `so` | `rascunho`, `nenhum`, detectado | usados pelos módulos das próximas fases |
| `modelos` | `{"forte": "…", "medio": "…", "leve": "…"}` | modelo de cada agente; vazio = herda o da sessão (padrão fora do Claude) |

## O que é instalado

- **Instruções** (o processo): `~/.claude/rules/fluxo.md`, `~/.copilot/instructions/fluxo.instructions.md`
  ou `~/.kiro/steering/fluxo.md`.
- **7 subagentes:** architect, researcher, test-runner, code-reviewer, debugger, doc-writer e committer.
- **Skills:**

  | Skill | O que faz |
  |---|---|
  | `/pesquisar` | o `researcher` mapeia o código; você revisa |
  | `/planejar` | o `architect` pesquisa; o plano vai para `~/.local/share/fluxo/planos/<id>.md` e espera a sua aprovação |
  | `/implementar` | fase por fase, com `test-runner` e `code-reviewer` |
  | `/revisar` | o `code-reviewer` sobre o diff |
  | `/commitar` | commit no padrão do repo, sem trailer de IA |
  | `/concluir` | resumo, docs, PR/MR (com aprovação), rascunho do aviso, limpeza, fechamento do plano |
  | `/pr-feedback` | responde o review de uma PR (GitHub) ou MR (GitLab), mostrando tudo antes de publicar |

- **`~/.local/bin/fluxo`**, que roda a cópia de onde o install saiu.

Os nomes são em português de propósito: o Copilot e o Kiro já têm `/plan` e `/review` nativos.

## Módulos

Desligados por padrão. `fluxo modulos` lista os módulos; `fluxo modulos --ligar task mensagens` liga
e lembra de rodar `fluxo install`.

| Módulo | O que traz | Precisa de |
|---|---|---|
| `task` | `fluxo task <ID>` abre a tarefa numa janela do tmux, em segundo plano, com `/planejar <ID>`, e ocupa vaga (`task.max`, padrão 2). `fluxo task -p "…"` abre uma sessão avulsa (nome do `/comando`, ou `avulsa`, `avulsa-b`…), que não ocupa vaga. `--lista` mostra as vivas. | tmux |
| `mensagens` | skill `/aviso`: redige no seu tom (`~/.config/fluxo/tom.md`, criado por `fluxo msg tom`), pede aprovação e copia para a área de transferência (`fluxo msg copiar`). Nada é enviado. | pbcopy, clip.exe, wl-copy, xclip ou xsel |
| `trava` | `fluxo trava pegar/soltar/status/quebrar/rodar`: uma sessão por vez no stack local. O E2E das instruções passa a usar. | tmux (para saber se o dono vive) |
| `vigia` | `fluxo vigia tick` lê as suas PRs (GitHub) ou MRs (GitLab) e avisa na área de trabalho: review ou comentário novo, aprovação no commit atual, CI que quebrou ou passou, merge. Só avisa. `fluxo vigia ligar` agenda: launchd no Mac, systemd no Linux, uma janela do tmux no WSL. `fluxo vigia` mostra o status. | `gh` ou `glab` logado |
| `painel` | `fluxo painel`: as sessões da ferramenta ao vivo (trabalhando, sua vez, pergunta, parada), com Enter levando até a sessão no tmux. Os hooks vão para `~/.copilot/hooks` ou `~/.kiro/hooks`; no Kiro V2, um agente `fluxo` vira o padrão (`fluxo painel ligar`). Só Copilot e Kiro: no Claude o painel é o agent-tree. | tmux |
| `revisor` | skill `/revisar-pr` e o agente `verificador`: duas revisões isoladas (`code-reviewer` + diff contra o plano), relatório com veredito (REPROVAR, PRECISA DE HUMANO, APROVÁVEL) em `~/.local/share/fluxo/revisoes/`. O `/concluir` roda antes de abrir a PR/MR. | — |

- **Comando de abertura de cada ferramenta:** fica em `adaptadores/ferramenta/*.json` (`lancar`). Se o
  da sua versão for outro, sobrescreva na config: `"task": {"lancar": ["copilot", "-i", "{prompt}"]}`.
- **Fora do tmux:** use `--target <sessão>:`.

## Atualizar, conflitos e desinstalar

- **Atualizar:** puxe o repo (ou um zip novo) e rode `fluxo install` de novo. Só o que mudou é gravado.
- **Conflito:** o install nunca pisa num arquivo que não é dele, nem num dele que você editou. Com
  conflito, nada é gravado e a saída é 3. `--forcar` grava assim mesmo, guardando um
  `.fluxo-bak-<data>`.
- **Desinstalar:** `fluxo uninstall`. O que você editou fica.
- Para mudar o fluxo, edite as **fontes** neste repo (`nucleo/`), não os arquivos gerados.

## Máquina sem git

```bash
./fluxo empacotar                        # aqui: dist/fluxo-<versão>.zip (passa pela varredura antes)
# lá, depois de copiar o zip:
python3 -m zipfile -e fluxo-<versão>.zip ~/fluxo-src
python3 ~/fluxo-src/fluxo/fluxo init --ferramenta kiro --forge github
python3 ~/fluxo-src/fluxo/fluxo install
```

O `install` não usa a rede.

## Validação na máquina real

Antes de confiar numa ferramenta nova, siga o [roteiro de validação](docs/validacao.md): os formatos
do Copilot e do Kiro mudam entre versões.

## Desenvolvimento

```bash
python3 -m unittest discover -s tests          # a suíte
docker run --rm -v "$PWD":/src:ro -w /src python:3.9-slim \
  sh -c 'cp -r /src /tmp/f && cd /tmp/f && python -m unittest discover -s tests'   # no 3.9
FLUXO_ATUALIZAR_ESPERADO=1 python3 -m unittest discover -s tests -p test_install.py  # regenera os golden
./fluxo varrer                                 # dado de empresa no repo? (o pre-commit roda)
```

- **Layout:**
  - `nucleo/` tem as fontes: instruções, agentes e skills;
  - `adaptadores/` tem os dados por ferramenta e por forge;
  - `lib/fluxo/` é o código;
  - `modulos/` recebe os módulos das próximas fases.
- **Template das fontes:** `{{forge.pr}}`, `{{git.base}}`, `{{codigo_dir}}`, `{{dados}}`,
  `{{argumentos}}` e `{{pergunte}}`, mais blocos `<!-- se forge:gitlab -->…<!-- senao -->…<!-- fim -->`.
  Variável ou condição desconhecida é erro.
- **Agentes:** frontmatter com `tools: [read, search, edit, shell, web, agent]` e
  `modelo: forte|medio|leve`. A tradução para cada ferramenta fica em `adaptadores/ferramenta/`.
