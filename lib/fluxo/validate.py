"""Módulo validate: traz o pr-reviewer para a máquina e roda a revisão dele pelo fluxo.

O `fluxo install` não usa rede: quem clona é o `fluxo validate enable` (como o `watch enable` e o `panel enable`),
numa tag fixada por esta versão do fluxo, conferindo o sha do commit, porque uma tag pode ser movida. Um clone com
mudança local nunca é descartado: o enable e o disable avisam e seguem. O clone é feito numa pasta irmã e troca de
lugar no fim, porque o timeout mata o git com SIGKILL e deixaria a pasta pela metade.
"""
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from . import config as cfgmod

# a versão do pr-reviewer que esta versão do fluxo usa
PR_REVIEWER = {
    "url": "git@github.com:paulozy/pr-reviewer-core.git",
    "tag": "v0.5.0",
    "sha": "d10f37c88c4a073a492755017902c9730eff677e",
}
ESTADO = "pr-reviewer.json"
TIMEOUT = 300
DICAS = "confira o acesso ao repo: `ssh -T git@github.com`, ou `gh auth setup-git` se você usa HTTPS"


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


def _ambiente(env, run):
    """Sem pergunta de senha, sem as variáveis de um repo de fora, e o ssh em modo não interativo quando você não
    configurou um ssh próprio para o git."""
    novo = {k: v for k, v in env.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    novo.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    if "GIT_SSH_COMMAND" not in env:
        proprio = run(["git", "config", "--get", "core.sshCommand"], capture_output=True, text=True, env=novo,
                      stdin=subprocess.DEVNULL, timeout=30)
        if proprio.returncode != 0 or not proprio.stdout.strip():
            novo["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=15"
    return novo


def _git(run, env, args, cwd=None, timeout=TIMEOUT):
    # o teto impede que o git suba para um repo de fora (um HOME versionado) se a pasta do clone perder o .git
    extra = {"GIT_CEILING_DIRECTORIES": str(Path(cwd).parent)} if cwd else {}
    return run(["git", *args], cwd=cwd, env=dict(env, **extra), stdin=subprocess.DEVNULL, capture_output=True,
               text=True, timeout=timeout)


def _fim(texto, linhas=6):
    return "\n".join(texto.strip().splitlines()[-linhas:])


def _ler(caminho):
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def _gravar(caminho, dados):
    cfgmod.criar_estado(caminho.parent)
    tmp = caminho.with_name(f".{caminho.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, caminho)


def limpo(run, env, clone, sha):
    """(limpo, motivo): no sha registrado, sem mudança (nem arquivo novo ou ignorado, que a troca apagaria), sem
    branch local e sem stash, que o status não mostra."""
    topo = _git(run, env, ["rev-parse", "--show-toplevel"], cwd=clone)
    if topo.returncode != 0 or Path(topo.stdout.strip()).resolve() != clone.resolve():
        return False, "não é um clone do git"
    head = _git(run, env, ["rev-parse", "HEAD"], cwd=clone).stdout.strip()
    if head != sha:
        return False, f"está em {head[:12]}, e não no {sha[:12]} que o fluxo clonou"
    status = _git(run, env, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored"], cwd=clone)
    if status.returncode != 0 or status.stdout.strip("\0"):
        return False, "tem arquivos mudados, novos ou ignorados"
    refs = _git(run, env, ["for-each-ref", "--format=%(refname)", "refs/heads", "refs/stash"], cwd=clone)
    if refs.returncode != 0 or refs.stdout.strip():
        return False, "tem branch local ou stash"
    return True, ""


def clonar(run, env, fonte, clone):
    """Clona a tag numa pasta irmã, confere o sha e troca de lugar com o clone de antes, se houver."""
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
    }
    if _criar_exclusivo(destino, json.dumps(conteudo, indent=2, ensure_ascii=False) + "\n", 0o600):
        return f"config do pr-reviewer criada em {destino} (perfil {profile})"
    return f"config do pr-reviewer mantida em {destino} (já existia)"


def enable(cfg, cam, run=subprocess.run, env=None, fonte=None, profile="local"):
    """(código, mensagens)."""
    env = dict(os.environ if env is None else env)
    fonte = fonte or PR_REVIEWER
    clone = pasta(cfg, cam)
    estado_arq = cam.estado / ESTADO
    estado = _ler(estado_arq)
    msgs = []
    proprio = bool((cfg.get("validate") or {}).get("dir"))
    try:
        genv = _ambiente(env, run)
    except FileNotFoundError:
        return 1, ["sem git nesta máquina: o pr-reviewer vem por git clone"]
    except subprocess.TimeoutExpired:
        return 1, ["o git não respondeu"]
    if proprio:
        if not (clone / "bin" / "pr-review.ts").exists():
            return 1, [f"validate.dir aponta para {clone}, que não tem bin/pr-review.ts"]
        msgs.append(f"usando o seu clone em {clone} (o fluxo nunca mexe nele)")
        estado = {"dir": str(clone), "proprio": True}
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
        ok, motivo = limpo(run, genv, clone, estado["sha"])
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
    elif estado.get("wrapper") and bin_.is_file() and not bin_.is_symlink() and bin_.read_text(encoding="utf-8") == wrapper(clone):
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
    clone = Path(estado["dir"])
    msgs = []
    bin_ = cam.home / ".local" / "bin" / "pr-review"
    if estado.get("wrapper") and bin_.is_file() and not bin_.is_symlink() and bin_.read_text(encoding="utf-8") == wrapper(clone):
        bin_.unlink()
        msgs.append(f"comando pr-review removido de {bin_}")
    elif estado.get("wrapper"):
        msgs.append(f"{bin_} mudou desde o enable; ficou como está")
    tira_estado = True
    if estado.get("proprio"):
        msgs.append(f"o seu clone em {clone} fica (o fluxo nunca mexe nele)")
    elif clone.exists():
        try:
            ok, motivo = limpo(run, _ambiente(env, run), clone, estado.get("sha", ""))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            ok, motivo = False, "não deu para conferir (sem git)"
        if ok:
            shutil.rmtree(clone)
            msgs.append(f"clone do pr-reviewer removido de {clone}")
        else:
            tira_estado = False
            msgs.append(f"o clone em {clone} {motivo}; ficou como está")
    if tira_estado:
        estado_arq.unlink()
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
