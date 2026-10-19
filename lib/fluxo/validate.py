"""Módulo validate: traz o pr-reviewer para a máquina e roda a revisão dele pelo fluxo.

O `fluxo install` não usa rede: quem clona é o `fluxo validate enable` (como o `watch enable` e o `panel enable`),
numa tag fixada por esta versão do fluxo, conferindo o sha do commit, porque uma tag pode ser movida. Um clone com
qualquer coisa sua (arquivo, commit, ref, reflog, worktree) nunca é descartado: o enable e o disable avisam e
seguem. O clone é feito numa pasta irmã e troca de lugar no fim, porque o timeout mata o git com SIGKILL e deixaria
a pasta pela metade.
"""
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import config as cfgmod
from . import fonte as fontemod
from . import lock
from .task import ID_RE

# a versão do pr-reviewer que esta versão do fluxo usa
PR_REVIEWER = {
    # HTTPS: o fluxo já usa o gh, e o `gh auth setup-git` libera o clone de um repo privado; quem usa ssh redireciona
    # com um insteadOf
    "url": "https://github.com/paulozy/pr-reviewer-core.git",
    "tag": "v0.6.0",
    "sha": "17351992631ca50d1ef35ec0c9059c2323701ed5",
}
ESTADO = "pr-reviewer.json"
TIMEOUT = 300
DICAS = ("confira o acesso ao repo: `gh auth setup-git` (com o gh logado); com ssh, "
         "`git config --global url.git@github.com:paulozy/.insteadOf https://github.com/paulozy/` e `ssh -T git@github.com`")
# o `git rev-parse --local-env-vars`: com uma delas herdada, o git falaria com outro repo
GIT_LOCAIS = ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
              "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
              "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX",
              "GIT_INTERNAL_SUPER_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR")
PERFIL_RE = re.compile(r"^[A-Za-z0-9_-]+$")  # o pr-reviewer aceita só este formato (config.ts)


class ErroClone(Exception):
    pass


def pasta(cfg, cam):
    """O clone: o da chave validate.dir (um clone seu, que o fluxo nunca mexe) ou o do fluxo, nos dados."""
    proprio = (cfg.get("validate") or {}).get("dir")
    return Path(os.path.expanduser(proprio)) if proprio else cam.dados / "pr-reviewer"


def config_pr_reviewer(cam, env):
    """Onde o pr-reviewer lê a config dele (src/config.ts): PR_REVIEWER_CONFIG_DIR, ou XDG_CONFIG_HOME/pr-reviewer,
    ou ~/.config/pr-reviewer. Com --home (os testes), tudo fica dentro do home de teste."""
    if env.get("PR_REVIEWER_CONFIG_DIR"):
        return Path(env["PR_REVIEWER_CONFIG_DIR"]) / "config.json"
    return cam.config_dir.parent / "pr-reviewer" / "config.json"


def wrapper(clone):
    return ("#!/bin/sh\n# criado pelo fluxo validate enable; o fluxo validate disable remove\n"
            f'exec node {shlex.quote(str(clone / "bin" / "pr-review.ts"))} "$@"\n')


def _e_nosso(bin_, clone):
    """O comando pr-review é o que o enable criou para este clone (comparado byte a byte, sem seguir link)."""
    try:
        return bin_.is_file() and not bin_.is_symlink() and bin_.read_bytes() == wrapper(clone).encode()
    except OSError:
        return False


def _ambiente(env, run):
    """Sem pergunta de senha, sem as variáveis de um repo de fora, e o ssh em modo não interativo quando você não
    configurou um ssh próprio para o git (na config global ou na do sistema, e não na do repo onde você está)."""
    novo = {k: v for k, v in env.items() if k not in GIT_LOCAIS}
    novo.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    if "GIT_SSH_COMMAND" not in env:
        for escopo in ("--global", "--system"):
            r = run(["git", "config", escopo, "--get", "core.sshCommand"], capture_output=True, text=True, env=novo,
                    stdin=subprocess.DEVNULL, timeout=30)
            if r.returncode == 0 and r.stdout.strip():
                break
        else:
            novo["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=15"
    return novo


def _git(run, env, args, cwd=None, timeout=TIMEOUT):
    # o teto impede que o git suba para um repo de fora (um HOME versionado) se a pasta perder o .git
    extra = {"GIT_CEILING_DIRECTORIES": str(Path(cwd).parent)} if cwd else {}
    return run(["git", *args], cwd=cwd, env=dict(env, **extra), stdin=subprocess.DEVNULL, capture_output=True,
               text=True, timeout=timeout)


def _linha(texto):
    """Texto de terceiro (título de achado, corpo de script, motivo) numa linha só: com uma quebra, ele forjaria uma
    linha do fluxo, como a do comando que grava a receita."""
    return " ".join(str(texto).splitlines())


def _fim(texto, linhas=6):
    return "\n".join(texto.strip().splitlines()[-linhas:])


def _ler(caminho):
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _gravar(caminho, dados):
    cfgmod.criar_estado(caminho.parent)
    tmp = caminho.with_name(f".{caminho.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, caminho)


def limpo(run, env, clone, sha, tag):
    """(limpo, motivo). Limpo é exatamente o que um `clone --depth 1 --branch <tag>` deixa: o HEAD no sha, nenhum
    arquivo mudado, novo ou ignorado (a troca apagaria), só a ref da tag (sem branch, stash ou tag sua), uma entrada
    no reflog (um commit feito e abandonado só existe nele) e uma worktree só."""
    if clone.is_symlink():
        return False, "é um link"
    topo = _git(run, env, ["rev-parse", "--show-toplevel"], cwd=clone)
    if topo.returncode != 0 or Path(topo.stdout.strip()).resolve() != clone.resolve():
        return False, "não é um clone do git"
    head = _git(run, env, ["rev-parse", "HEAD"], cwd=clone).stdout.strip()
    if head != sha:
        return False, f"está em {head[:12]}, e não no {sha[:12]} que o fluxo clonou"
    status = _git(run, env, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored"], cwd=clone)
    if status.returncode != 0 or status.stdout.strip("\0"):
        return False, "tem arquivos mudados, novos ou ignorados"
    refs = _git(run, env, ["for-each-ref", "--format=%(refname)"], cwd=clone)
    if refs.returncode != 0 or refs.stdout.split() != [f"refs/tags/{tag}"]:
        return False, "tem branch, stash ou tag que não é a do fluxo"
    reflog = _git(run, env, ["reflog", "--format=%H", "HEAD"], cwd=clone)
    if reflog.returncode != 0 or len(reflog.stdout.split()) != 1:
        return False, "não tem exatamente uma entrada no reflog (um commit seu pode estar só nele)"
    worktrees = _git(run, env, ["worktree", "list", "--porcelain"], cwd=clone)
    if worktrees.returncode != 0 or sum(l.startswith("worktree ") for l in worktrees.stdout.splitlines()) != 1:
        return False, "tem outra worktree"
    return True, ""


def clonar(run, env, fonte, clone):
    """Clona a tag numa pasta irmã, confere o sha e troca de lugar com o clone de antes, se houver."""
    if clone.is_symlink():
        raise ErroClone(f"{clone} é um link; não mexo nele")
    clone.parent.mkdir(parents=True, exist_ok=True)
    tmp = clone.with_name(f".{clone.name}.novo-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        r = run(["git", "-c", "advice.detachedHead=false", "clone", "--quiet", "--depth", "1", "--branch", fonte["tag"],
                 fonte["url"], str(tmp)], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ErroClone(f"o git clone de {fonte['url']} passou de {TIMEOUT} s; {DICAS}") from None
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ErroClone(f"o git clone de {fonte['url']} ({fonte['tag']}) falhou:\n{_fim(r.stderr)}\n{DICAS}")
    head = _git(run, env, ["rev-parse", "HEAD"], cwd=tmp).stdout.strip()
    if head != fonte["sha"]:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ErroClone(f"a tag {fonte['tag']} aponta para {head[:12]}, e esta versão do fluxo espera {fonte['sha'][:12]}: "
                        "a tag foi movida no repo; nada foi trocado")
    velho = clone.with_name(f".{clone.name}.velho-{os.getpid()}")
    if clone.exists():
        os.replace(clone, velho)
    os.replace(tmp, clone)
    shutil.rmtree(velho, ignore_errors=True)


def _criar_exclusivo(caminho, conteudo, modo):
    """Só cria: um arquivo (ou link) que já existe fica como está. O modo da criação passa pelo umask."""
    try:
        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), modo)
    except FileExistsError:
        return False
    try:
        os.fchmod(fd, modo)
        os.write(fd, conteudo.encode())
    finally:
        os.close(fd)
    return True


def criar_config(cfg, cam, env, profile):
    destino = config_pr_reviewer(cam, env)
    destino.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conteudo = {
        "workspaceRoot": os.path.expanduser(cfg["code_dir"]),
        "primaryBase": cfg["git"]["base"],
        "defaultBase": f"origin/{cfg['git']['base']}",
        "profile": profile,
        # sem ele, o pr-reviewer roda os agentes no claude -p, que pode nem estar nesta máquina
        "backend": cfg["tools"][0],
    }
    if _criar_exclusivo(destino, json.dumps(conteudo, indent=2, ensure_ascii=False) + "\n", 0o600):
        return f"config do pr-reviewer criada em {destino} (perfil {profile})"
    return f"config do pr-reviewer mantida em {destino} (já existia)"


def _alguma_rodando(cam):
    raiz = cam.estado / "validar"
    return [f"{p.parent.name}/{p.name}" for p in sorted(raiz.glob("*/*")) if _rodando(p)] if raiz.is_dir() else []


def enable(cfg, cam, run=subprocess.run, env=None, fonte=None, profile="local"):
    """(código, mensagens)."""
    env = dict(os.environ if env is None else env)
    fonte = fonte or PR_REVIEWER
    if not PERFIL_RE.fullmatch(profile or ""):
        return 1, [f"perfil {profile!r} inválido: só letras, números, _ e - (o pr-reviewer recusa outro formato)"]
    rodando = _alguma_rodando(cam)
    if rodando:
        return 1, [f"há uma validação rodando ({', '.join(rodando)}): espere ela terminar, ou fluxo validate stop"]
    clone = pasta(cfg, cam)
    estado_arq = cam.estado / ESTADO
    estado = _ler(estado_arq)
    msgs = []
    proprio = bool((cfg.get("validate") or {}).get("dir"))
    # trocar entre o clone do fluxo e um seu passa pelo disable: o comando pr-review e o clone ficam com dono certo
    if estado and (bool(estado.get("proprio")) != proprio or (proprio and estado.get("dir") != str(clone))):
        if estado.get("proprio"):
            return 1, [f"o validate está ligado com o seu clone em {estado.get('dir')}: rode `fluxo validate disable` antes de trocar"]
        return 1, [f"o validate está ligado com o clone do fluxo em {estado.get('dir')}: rode `fluxo validate disable` antes de "
                   "trocar (se o disable mantiver o clone, por ter algo seu, salve o que quiser e apague a pasta)"]
    try:
        genv = _ambiente(env, run)
    except FileNotFoundError:
        return 1, ["sem git nesta máquina: o pr-reviewer vem por git clone"]
    except subprocess.TimeoutExpired:
        return 1, ["o git não respondeu"]
    if proprio:
        if not clone.is_absolute() or not (clone / "bin" / "pr-review.ts").is_file():
            return 1, [f"validate.dir aponta para {clone}, que não é um caminho absoluto com bin/pr-review.ts"]
        msgs.append(f"usando o seu clone em {clone} (o fluxo nunca mexe nele)")
        estado = {"dir": str(clone), "proprio": True, "wrapper": estado.get("wrapper", False)}
    elif clone.is_symlink():
        return 1, [f"{clone} é um link; não mexo nele"]
    elif not clone.exists():
        try:
            clonar(run, genv, fonte, clone)
        except ErroClone as exc:
            return 1, [str(exc)]
        msgs.append(f"pr-reviewer {fonte['tag']} clonado em {clone}")
        estado = {"dir": str(clone), "tag": fonte["tag"], "sha": fonte["sha"], "wrapper": estado.get("wrapper", False)}
    elif estado.get("dir") != str(clone) or not estado.get("sha"):
        return 1, [f"{clone} existe e não foi o fluxo que clonou; não mexo nele (apague-o, ou use validate.dir)"]
    elif estado["sha"] == fonte["sha"]:
        msgs.append(f"pr-reviewer já está na {fonte['tag']} em {clone}")
    else:
        ok, motivo = limpo(run, genv, clone, estado["sha"], estado.get("tag", ""))
        if not ok:
            msgs.append(f"não atualizei {clone} para a {fonte['tag']}: o clone {motivo}; nada foi descartado")
        else:
            try:
                clonar(run, genv, fonte, clone)
            except ErroClone as exc:
                return 1, [str(exc)]
            msgs.append(f"pr-reviewer atualizado para a {fonte['tag']} em {clone}")
            estado = dict(estado, tag=fonte["tag"], sha=fonte["sha"])
    msgs.append(criar_config(cfg, cam, env, profile))
    bin_ = cam.home / ".local" / "bin" / "pr-review"
    bin_.parent.mkdir(parents=True, exist_ok=True)
    if _criar_exclusivo(bin_, wrapper(clone), 0o755):
        estado["wrapper"] = True
        msgs.append(f"comando pr-review criado em {bin_}")
    elif estado.get("wrapper") and _e_nosso(bin_, clone):
        msgs.append(f"comando pr-review mantido em {bin_}")
    else:
        estado["wrapper"] = False
        msgs.append(f"{bin_} já existia e não foi o fluxo que criou: as skills usam `fluxo validate`, que não depende dele")
    _gravar(estado_arq, estado)
    return 0, msgs


def disable(cfg, cam, run=subprocess.run, env=None):
    env = dict(os.environ if env is None else env)
    estado_arq = cam.estado / ESTADO
    estado = _ler(estado_arq)
    if not estado:
        return 0, ["o validate não estava ligado nesta máquina"]
    rodando = _alguma_rodando(cam)
    if rodando:
        return 1, [f"há uma validação rodando ({', '.join(rodando)}): espere ela terminar, ou fluxo validate stop"]
    clone = Path(estado["dir"])
    msgs = []
    bin_ = cam.home / ".local" / "bin" / "pr-review"
    if estado.get("wrapper") and _e_nosso(bin_, clone):
        bin_.unlink()
        msgs.append(f"comando pr-review removido de {bin_}")
    elif estado.get("wrapper"):
        msgs.append(f"{bin_} mudou desde o enable; ficou como está")
    tira_estado = True
    if estado.get("proprio"):
        msgs.append(f"o seu clone em {clone} fica (o fluxo nunca mexe nele)")
    elif clone.exists() or clone.is_symlink():
        try:
            ok, motivo = limpo(run, _ambiente(env, run), clone, estado.get("sha", ""), estado.get("tag", ""))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            ok, motivo = False, "não deu para conferir (sem git)"
        if ok:
            shutil.rmtree(clone)
            msgs.append(f"clone do pr-reviewer removido de {clone}")
        else:
            # o estado fica: é ele que diz ao próximo enable que o clone é do fluxo
            tira_estado = False
            msgs.append(f"o clone em {clone} {motivo}; ficou como está (salve o que quiser e apague a pasta, se não serve mais)")
    if tira_estado:
        estado_arq.unlink()
    elif not bin_.exists():
        _gravar(estado_arq, dict(estado, wrapper=False))
    msgs.append(f"a config do pr-reviewer fica em {config_pr_reviewer(cam, env)}")
    return 0, msgs


def comando(cfg, cam, args):
    """O argv que roda o pr-reviewer do clone (o `fluxo validate exec` e o `start` usam)."""
    alvo = pasta(cfg, cam) / "bin" / "pr-review.ts"
    if not alvo.exists():
        raise cfgmod.ErroConfig(f"{alvo} não existe: rode `fluxo validate enable`")
    return ["node", str(alvo), *args]


def avisos_do_install(cfg, cam):
    if "validate" in cfg["modules"] and not (cam.estado / ESTADO).exists():
        return ["validate: rode `fluxo validate enable` para trazer o pr-reviewer (o install não usa rede)"]
    return []


def avisos_do_uninstall(cam):
    if (cam.estado / ESTADO).exists():
        return ["validate: o clone do pr-reviewer, o comando pr-review e a config dele ficam; "
                "rode `fluxo validate disable` antes, se quiser tirá-los"]
    return []


# ── a revisão e a receita em segundo plano ───────────────────────────────────
#
# Uma revisão leva de 4 a 20 min, e o comando da ferramenta tem limite de tempo (no Claude, 10 min); o timeout
# mata o que ela rodou. Por isso o `start` solta um processo desligado da sessão (líder do próprio grupo), que pega
# a trava do stack com o próprio pid (ela vale enquanto ele vive), roda o pr-review, repassa um SIGTERM a ele e só
# solta a trava depois que ele termina. O `status` acompanha e o `stop` cancela o grupo inteiro. A trava ocupada
# nunca se confunde com a saída 4 do pr-review, que é REPROVAR.

ESPERA = 3600
TIPOS = ("revisao", "receita")
NOME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")
REPO_RE = re.compile(r"^(?!\.{1,2}$)[A-Za-z0-9_.-]+$")  # o mesmo do --local do pr-reviewer (cli.ts), em ASCII
RECEITA_RE = re.compile(r"[A-Za-z0-9_-]+")  # o repo do --recipe


def chave(tipo, nome):
    """`revisao/<id>` ou `receita/<repo>`: uma tarefa chamada receita-x não colide com a receita do repo x."""
    if tipo not in TIPOS or not NOME_RE.fullmatch(nome or ""):
        raise cfgmod.ErroConfig(f"{tipo} {nome!r} inválido")
    return f"{tipo}/{nome}"


def _pasta_execucao(cam, ch):
    tipo, _, nome = ch.partition("/")
    chave(tipo, nome)  # valida: um `_rodar ../../x` nunca acha outro pedido.json
    return cam.estado / "validar" / tipo / nome


def _rodando(pasta):
    """O processo da fase vive, e é o mesmo (a hora de início confere: um pid reaproveitado depois de um reboot
    não conta)."""
    fase = _ler(pasta / "fase.json")
    pid = fase.get("pid")
    if not pid or (pasta / "fim.json").exists():
        return False
    agora = lock.inicio_do_pid(pid)
    return agora is not None and (not fase.get("inicio") or not agora or agora == fase["inicio"])


def execucoes(cam, rodando=False):
    """As chaves com pedido gravado, da mais nova para a mais velha; com `rodando`, só as que ainda rodam."""
    achadas = []
    for tipo in TIPOS:
        for pasta in (cam.estado / "validar" / tipo).glob("*"):
            pedido = pasta / "pedido.json"
            if NOME_RE.fullmatch(pasta.name) and pedido.is_file() and (not rodando or _rodando(pasta)):
                achadas.append((pedido.stat().st_mtime, f"{tipo}/{pasta.name}"))
    return [ch for _, ch in sorted(achadas, reverse=True)]


def _backend(backend):
    # a ferramenta da sessão roda os agentes do pr-reviewer: claude, kiro, codex ou copilot (este desde a v0.6.0)
    return ["--backend", backend] if backend else []


def preparar_revisao(cfg, cam, id_, backend, run=subprocess.run, env=None):
    """O argv do pr-review para a tarefa: um --local por repo do plano, na branch da worktree, e a base do plano."""
    if not ID_RE.fullmatch(id_ or ""):
        raise cfgmod.ErroConfig(f"id {id_!r} inválido")
    plano = cam.dados / "planos" / f"{id_}.md"
    try:
        meta, _ = fontemod.ler(plano.read_text(encoding="utf-8"), str(plano))
    except FileNotFoundError:
        raise cfgmod.ErroConfig(f"{plano} não existe: o /planejar grava o plano antes") from None
    repos = meta.get("repos")
    if isinstance(repos, str):
        repos = [repos]
    if not isinstance(repos, list) or not repos or not all(isinstance(r, str) and REPO_RE.fullmatch(r) for r in repos):
        raise cfgmod.ErroConfig(f"{plano}: o frontmatter precisa de repos: [repo, ...], com nomes de repo")
    # a base vem do plano (texto que o agente escreve): o formato de branch impede que ela vire opção do git
    base = meta.get("base") or cfg["git"]["base"]
    if not isinstance(base, str) or not cfgmod.BRANCH.fullmatch(base):
        raise cfgmod.ErroConfig(f"{plano}: base {base!r} inválida")
    try:
        genv = _ambiente(dict(os.environ if env is None else env), run)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        raise cfgmod.ErroConfig("sem git nesta máquina") from None
    locais, avisos = [], []
    for repo in repos:
        wt = Path(os.path.expanduser(cfg["code_dir"])) / f"{repo}-{id_}"
        if not wt.is_dir():
            raise cfgmod.ErroConfig(f"a worktree {wt} não existe")
        try:
            branch = _git(run, genv, ["branch", "--show-current"], cwd=wt, timeout=30).stdout.strip()
            sujo = _git(run, genv, ["status", "--porcelain"], cwd=wt, timeout=60).stdout.strip()
        except subprocess.TimeoutExpired:
            raise cfgmod.ErroConfig(f"o git não respondeu em {wt}") from None
        if not branch:
            raise cfgmod.ErroConfig(f"{wt} não está numa branch")
        if sujo:
            raise cfgmod.ErroConfig(f"{wt} tem mudança não commitada; o pr-review só vê o que está commitado: commite antes")
        try:
            buscou = _git(run, genv, ["fetch", "-q", "origin", base], cwd=wt, timeout=120).returncode == 0
        except subprocess.TimeoutExpired:
            buscou = False
        if not buscou:
            avisos.append(f"{repo}: o git fetch origin {base} falhou; a revisão usa a origin/{base} que já está aqui")
        locais += ["--local", f"{repo}:{branch}"]
    argv = comando(cfg, cam, [*locais, "--base", f"origin/{base}", "--plan", str(plano), *_backend(backend)])
    return argv, avisos


def preparar_receita(cfg, cam, repo, modo, backend, json_=True):
    """O argv do `pr-review recipe`. Sem `json_`, ele mostra a proposta e pergunta o "sim" no /dev/tty (src/util/tty.ts):
    só num terminal de verdade, porque num agente com PTY ficaria esperando."""
    if not RECEITA_RE.fullmatch(repo or ""):
        raise cfgmod.ErroConfig(f"nome de repo inválido: {repo!r}")
    extra = {"proposta": [], "fix": ["--fix"], "check": ["--check"]}[modo]
    # o --check não roda agente, e o pr-review recusa --backend com ele
    return comando(cfg, cam, ["recipe", repo, *extra, *(["--json"] if json_ else []),
                              *([] if modo == "check" else _backend(backend))])


def em_primeiro_plano(cam, argv, ch=None, trava=False, esperar=0, popen=subprocess.Popen):
    """O pr-review neste terminal, para o `fluxo validate recipe` e o `doctor`. Com `trava`, segura a do stack
    enquanto ele roda, como o processo de fundo do start. O Ctrl-C do terminal chega ao pr-review, que é do mesmo
    grupo e derruba o que subiu: aqui só se espera ele terminar."""
    if trava:
        return lock.rodar(lock.Trava(cam.estado), argv, esperar, popen=popen,
                          sessao=f"validar-{ch.replace('/', '-')}", pid=os.getpid(), id_=ch)
    recebido = []
    antigo = signal.signal(signal.SIGINT, lambda sinal, _frame: recebido.append(sinal))
    try:
        codigo = popen(argv).wait()
    finally:
        signal.signal(signal.SIGINT, antigo)
    return 128 + recebido[0] if recebido else codigo


def start(cfg, cam, raiz, ch, argv, trava, id_=None, popen=subprocess.Popen, espera=ESPERA):
    """Solta o processo que roda o pr-review desligado da sessão. Devolve a pasta da execução."""
    pasta = _pasta_execucao(cam, ch)
    if _rodando(pasta):
        raise cfgmod.ErroConfig(f"já há uma execução de {ch} rodando: fluxo validate status")
    cfgmod.criar_estado(cam.estado)
    shutil.rmtree(pasta, ignore_errors=True)
    pasta.mkdir(parents=True, mode=0o700)
    _gravar(pasta / "pedido.json", {"argv": argv, "trava": trava, "id": id_ or ch, "espera": espera,
                                    "criado": time.strftime("%Y-%m-%d %H:%M:%S")})
    home = ["--home", str(cam.home)] if cam.home != Path.home() else []
    with open(pasta / "saida.log", "wb") as out, open(pasta / "erros.log", "wb") as err:
        proc = popen([sys.executable, str(Path(raiz) / "fluxo"), "validate", "_rodar", ch, *home],
                     stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
    # já com o pid: até o processo gravar a fase dele, o status o daria por morto. Só cria: se o filho ganhou a
    # corrida, a fase dele vale
    _criar_exclusivo(pasta / "fase.json", json.dumps({"fase": "começando", "pid": proc.pid,
                                                      "inicio": lock.inicio_do_pid(proc.pid)}), 0o600)
    return pasta


def rodar(cam, ch, popen=subprocess.Popen):
    """O processo de fundo: pega a trava (se pedido) com o próprio pid, roda o pr-review e grava o fim. Um SIGTERM
    ou SIGHUP é repassado ao pr-review, e a trava só sai depois que ele termina."""
    pasta = _pasta_execucao(cam, ch)
    pedido = _ler(pasta / "pedido.json")
    eu = {"pid": os.getpid(), "inicio": lock.inicio_do_pid(os.getpid())}
    sessao = f"validar-{ch.replace('/', '-')}"
    t = lock.Trava(cam.estado) if pedido.get("trava") else None
    recebido, vivo = [], {"proc": None, "esperando": True}

    def repassar(sinal, _frame):
        recebido.append(sinal)
        if vivo["esperando"]:  # ainda sem o pr-review: para aqui mesmo
            raise _Parado()
        proc = vivo["proc"]
        if proc is not None and proc.poll() is None:
            proc.send_signal(sinal)

    antigos = {s: signal.signal(s, repassar) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        try:
            if t:
                _gravar(pasta / "fase.json", dict(eu, fase="esperando a trava do stack"))
                ok, dono, _ = lock.pegar_esperando(t, int(pedido.get("espera", ESPERA)), sessao=sessao, pid=os.getpid(), id_=pedido.get("id"))
                if not ok:
                    _gravar(pasta / "fim.json", {"ocupada": lock.descrever(dono, True)})
                    return lock.OCUPADA
            vivo["esperando"] = False
        except _Parado:
            _gravar(pasta / "fim.json", {"codigo": 128 + recebido[0], "parado": True})
            return 128 + recebido[0]
        _gravar(pasta / "fase.json", dict(eu, fase="rodando"))
        proc = vivo["proc"] = popen(pedido["argv"], stdin=subprocess.DEVNULL)
        if recebido:  # o sinal que chegou antes do popen
            proc.send_signal(recebido[-1])
        codigo = proc.wait()  # o wait é retomado depois do handler
        codigo = 128 + recebido[0] if recebido else codigo
        _gravar(pasta / "fim.json", {"codigo": codigo, "terminado": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     **({"parado": True} if recebido else {})})
        return codigo
    finally:
        for s, h in antigos.items():
            signal.signal(s, h)
        if t:
            t.soltar(sessao)  # só solta se for desta sessão: vale também para um sinal no meio da espera


class _Parado(Exception):
    """Um SIGTERM ou SIGHUP antes do pr-review começar."""


def stop(cam, ch, matar=os.killpg):
    """Cancela a execução: um SIGTERM ao grupo do processo de fundo, que o repassa ao pr-review e solta a trava."""
    pasta = _pasta_execucao(cam, ch)
    if not _rodando(pasta):
        return 1, f"não há execução de {ch} rodando"
    pid = _ler(pasta / "fase.json")["pid"]
    try:
        matar(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        return 1, f"a execução de {ch} já tinha terminado"
    return 0, f"{ch}: pedi para parar (o pr-review derruba o stack antes de sair)"


def _relatorio(pasta):
    """O report.json da revisão: a pasta da linha `contexto em <pasta>`, que o pr-review imprime no começo, antes de
    qualquer texto de terceiro (um título de achado com quebra de linha forjaria uma linha `relatório:`)."""
    texto = (pasta / "saida.log").read_text(encoding="utf-8", errors="replace") if (pasta / "saida.log").exists() else ""
    m = re.search(r"^contexto em (/.+)$", texto, re.M)
    return Path(m.group(1)) / "report.json" if m else None


def status(cam, ch, esperar=0, dormir=time.sleep, relogio=time.monotonic):
    """(código, linhas): 0 terminou, 75 ainda rodando, 1 não há execução ou ela não deu resultado."""
    pasta = _pasta_execucao(cam, ch)
    pedido = _ler(pasta / "pedido.json")
    if not pedido:
        return 1, [f"não há execução de {ch}"]
    fim_em = relogio() + max(0, min(esperar, 540))
    while not (pasta / "fim.json").exists() and _rodando(pasta) and relogio() < fim_em:
        dormir(min(5, max(0.5, fim_em - relogio())))
    fim = _ler(pasta / "fim.json")
    logs = f"logs: {pasta / 'saida.log'} e {pasta / 'erros.log'}"
    if not fim:
        if _rodando(pasta):
            return 75, [f"{ch}: {_ler(pasta / 'fase.json').get('fase', 'rodando')} (desde {pedido.get('criado')})", logs]
        return 1, [f"{ch}: o processo terminou sem registrar o fim", logs]
    if "ocupada" in fim:
        return 1, [f"{ch}: a trava do stack continuou ocupada ({fim['ocupada']}); rode de novo quando ela soltar", logs]
    if fim.get("parado"):
        return 1, [f"{ch}: parado pelo fluxo validate stop (saída {fim['codigo']})", logs]
    if ch.startswith("receita/"):
        return _resumo_receita(pasta, fim, logs)
    report = _relatorio(pasta)
    dados = _ler(report) if report else {}
    if not dados:
        return 1, [f"{ch}: o pr-review saiu com {fim['codigo']} sem relatório", logs]
    veredito = dados.get("verdict") or {}
    linhas = [f"veredito: {_linha(veredito.get('decision', '?'))}{' (só estática)' if veredito.get('staticOnly') else ''}"]
    linhas += [f"- {_linha(r)}" for r in veredito.get("reasons") or []]
    linhas.append(f"relatório: {report.with_name('report.md')}")
    return 0, linhas


def _resumo_receita(pasta, fim, logs):
    try:
        r = json.loads((pasta / "saida.log").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 1, [f"o recipe saiu com {fim['codigo']} sem o JSON", logs]
    linhas = [f"receita de {_linha(r.get('repo'))}: {_linha(r.get('status'))}"]
    if r.get("status") in ("healthy", "unhealthy"):
        for st in r.get("steps") or []:
            detalhe = (st.get("detail") or "").splitlines()
            linhas.append(f"- {'ok' if st.get('ok') else 'falhou'} {_linha(st.get('phase'))} {_linha(st.get('name'))}: {_linha(detalhe[0]) if detalhe else ''}")
        return 0, linhas + [logs]
    # o comando que grava vem logo depois do status, antes de qualquer texto de terceiro
    if r.get("status") == "pending":
        linhas.append(f"para gravar, depois do OK: fluxo validate exec -- recipe {shlex.quote(_linha(r.get('repo')))} "
                      f"--apply={shlex.quote(_linha(r.get('proposal')))} --confirm={shlex.quote(_linha(r.get('confirm')))}")
    linhas += [_linha(x) for x in r.get("summary") or []]
    linhas += [f"atenção: {_linha(w)}" for w in r.get("warnings") or []]
    linhas += [_linha(x) for x in r.get("diff") or []]
    linhas += [_linha(x) for x in r.get("failures") or []]
    return 0, linhas + [logs]


# ── doctor ───────────────────────────────────────────────────────────────────

# o type stripping ligado sem flag: no 22 a partir do 22.18, no 23 a partir do 23.6 (as notas de versão do Node)
def node_serve(major, minor):
    return (major == 22 and minor >= 18) or (major == 23 and minor >= 6) or major >= 24


def checar(cfg, cam, run, which, env):
    """As checagens do módulo, no formato do doctor: [(nível, texto)]. Não roda o `pr-review doctor`, que chama
    agente de verdade: só sugere."""
    from .doctor import AVISO, FALHA, OK, _rodar  # o doctor importa este módulo
    out = []
    if not which("node"):
        out.append((FALHA, "node não está no PATH: o pr-reviewer roda em Node 22.18 ou mais novo"))
    else:
        cod, saida = _rodar(run, ["node", "--version"])
        m = re.match(r"v(\d+)\.(\d+)", saida.strip())
        if cod != 0 or not m:
            out.append((AVISO, f"node --version não respondeu: {saida.strip()[:80]}"))
        elif not node_serve(int(m.group(1)), int(m.group(2))):
            out.append((FALHA, f"node {saida.strip()}: o pr-reviewer precisa do 22.18+ (ou do 23.6+), que roda o .ts direto"))
        else:
            out.append((OK, f"node {saida.strip()}"))
    out.append((OK, "docker") if which("docker") else
               (AVISO, "docker não está no PATH: a validação fica só com a revisão estática"))
    estado = _ler(cam.estado / ESTADO)
    clone = Path(estado["dir"]) if estado.get("dir") else pasta(cfg, cam)
    if not estado:
        out.append((AVISO, "o pr-reviewer não foi trazido: rode `fluxo validate enable`"))
    elif estado.get("proprio"):
        out.append((OK, f"pr-reviewer: o seu clone em {clone}"))
    else:
        cod, saida = _rodar(run, ["git", "-C", str(clone), "rev-parse", "HEAD"])
        if cod == 0 and saida.strip() == PR_REVIEWER["sha"]:
            out.append((OK, f"pr-reviewer {PR_REVIEWER['tag']} em {clone}"))
        else:
            out.append((AVISO, f"o clone em {clone} não está na {PR_REVIEWER['tag']} que esta versão do fluxo usa: "
                               "rode `fluxo validate enable`"))
    conf = config_pr_reviewer(cam, env)
    dados = _ler(conf)
    raiz = os.path.expanduser(cfg["code_dir"])
    if not dados:
        out.append((AVISO, f"{conf} não existe ou não é JSON: rode `fluxo validate enable`"))
    elif not os.path.isabs(str(dados.get("workspaceRoot", ""))) or os.path.normpath(str(dados["workspaceRoot"])) != os.path.normpath(raiz):
        # o pr-reviewer usa o valor como está, sem expandir o ~
        out.append((AVISO, f"o workspaceRoot do pr-reviewer ({dados.get('workspaceRoot')}) não é o code_dir do fluxo "
                           f"({raiz}), em caminho absoluto: a validação não acharia os repos"))
    else:
        out.append((OK, f"config do pr-reviewer em {conf}"))
    if estado:
        out.append((OK, "para conferir os agentes de verdade (custa): fluxo validate exec -- doctor"))
    return out
