"""Módulo validate: o enable traz o pr-reviewer numa tag fixada, e nada que você mudou no clone se perde."""
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from base import RAIZ, cfgmod, escrever_config
from fluxo import cli, lock, validate
import signal
import sys
import textwrap
import time

TEM_GIT = shutil.which("git") is not None
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@exemplo.com", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@exemplo.com", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, env=dict(os.environ, **GIT_ENV), capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"git {' '.join(args)}: {r.stderr}")
    return r.stdout.strip()


class Remoto:
    """Um repo bare com o pr-reviewer de mentira, e as tags que os testes pedem."""

    def __init__(self, raiz):
        self.trabalho = Path(raiz) / "trabalho"
        self.bare = Path(raiz) / "remoto.git"
        self.trabalho.mkdir(parents=True)
        git(self.trabalho, "init", "-q", "-b", "main")
        (self.trabalho / "bin").mkdir()
        (self.trabalho / "bin" / "pr-review.ts").write_text("console.log('v1');\n")
        (self.trabalho / ".gitignore").write_text("node_modules/\n")
        git(self.trabalho, "add", "-A")
        git(self.trabalho, "commit", "-q", "-m", "v1")
        git(self.trabalho, "tag", "-a", "v1", "-m", "v1")
        subprocess.run(["git", "clone", "-q", "--bare", str(self.trabalho), str(self.bare)], check=True, capture_output=True)

    def fonte(self, tag="v1"):
        return {"url": self.bare.as_uri(), "tag": tag, "sha": git(self.bare, "rev-parse", f"{tag}^{{commit}}")}

    def nova_tag(self, tag, conteudo):
        (self.trabalho / "bin" / "pr-review.ts").write_text(conteudo)
        git(self.trabalho, "commit", "-q", "-am", tag)
        git(self.trabalho, "tag", "-a", tag, "-m", tag)
        git(self.trabalho, "push", "-q", str(self.bare), "main", tag)
        return self.fonte(tag)


def rodar_git(argv, **kw):
    """O run de verdade, com a identidade e sem a config global do usuário."""
    if argv[:1] == ["git"]:
        kw["env"] = dict(kw.get("env") or os.environ, **GIT_ENV)
    return subprocess.run(argv, **kw)


@unittest.skipUnless(TEM_GIT, "sem git nesta máquina")
class EnableDisable(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.remoto = Remoto(self.tmp)
        self.home = self.tmp / "home"
        self.cam = escrever_config(self.home, "claude", modules=["validate"], code_dir="~/code-de-teste")
        self.cfg = cfgmod.carregar(self.cam, ("validate",))
        self.clone = self.cam.dados / "pr-reviewer"
        self.bin = self.home / ".local" / "bin" / "pr-review"
        self.config_pr = self.home / ".config" / "pr-reviewer" / "config.json"

    def enable(self, fonte=None, **kw):
        return validate.enable(self.cfg, self.cam, run=rodar_git, env={}, fonte=fonte or self.remoto.fonte(), **kw)

    def disable(self):
        return validate.disable(self.cfg, self.cam, run=rodar_git, env={})

    def test_clona_a_tag_cria_a_config_e_o_comando(self):
        code, msgs = self.enable()
        self.assertEqual(code, 0, msgs)
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), self.remoto.fonte()["sha"])
        self.assertEqual((self.clone / "bin" / "pr-review.ts").read_text(), "console.log('v1');\n")
        self.assertFalse([p for p in self.clone.parent.iterdir() if p.name.startswith(".pr-reviewer.")], "no temp dir left")
        conf = json.loads(self.config_pr.read_text())
        self.assertEqual(conf, {"workspaceRoot": os.path.expanduser("~/code-de-teste"), "primaryBase": "develop",
                                "defaultBase": "origin/develop", "profile": "local"})
        self.assertEqual(stat.S_IMODE(self.config_pr.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.config_pr.parent.stat().st_mode), 0o700)
        self.assertEqual(self.bin.read_text(), validate.wrapper(self.clone))
        self.assertEqual(stat.S_IMODE(self.bin.stat().st_mode), 0o755)
        self.assertIn(f'exec node {self.clone}/bin/pr-review.ts "$@"', self.bin.read_text())
        estado = json.loads((self.cam.estado / validate.ESTADO).read_text())
        self.assertEqual(estado, {"dir": str(self.clone), "tag": "v1", "sha": self.remoto.fonte()["sha"], "wrapper": True})
        self.assertEqual(self.enable()[1][0], f"pr-reviewer já está na v1 em {self.clone}")

    def test_recusa_a_tag_que_foi_movida_e_nao_deixa_nada(self):
        fonte = dict(self.remoto.fonte(), sha="0" * 40)
        code, msgs = self.enable(fonte)
        self.assertEqual(code, 1)
        self.assertRegex(msgs[0], r"a tag v1 aponta para [0-9a-f]{12}, e esta versão do fluxo espera 000000000000: a tag foi movida")
        self.assertFalse(self.clone.exists())
        self.assertFalse(self.cam.dados.exists() and [p for p in self.cam.dados.iterdir() if p.name.startswith(".pr-reviewer.")])
        self.assertFalse(self.config_pr.exists(), "nothing else was done")

    def test_atualiza_um_clone_limpo_para_a_tag_nova(self):
        self.enable()
        v2 = self.remoto.nova_tag("v2", "console.log('v2');\n")
        code, msgs = self.enable(v2)
        self.assertEqual(code, 0, msgs)
        self.assertEqual(msgs[0], f"pr-reviewer atualizado para a v2 em {self.clone}")
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), v2["sha"])
        self.assertEqual(json.loads((self.cam.estado / validate.ESTADO).read_text())["sha"], v2["sha"])
        self.assertEqual([p.name for p in self.clone.parent.iterdir() if p.name.startswith(".pr-reviewer.")], [], "no old or new dir left")
        v3 = self.remoto.nova_tag("v3", "console.log('v3');\n")
        self.assertEqual(self.enable(v3)[1][0], f"pr-reviewer atualizado para a v3 em {self.clone}", "the tag of v2 is the expected ref now")

    def test_nunca_descarta_o_que_voce_mudou_no_clone(self):
        casos = {
            "arquivo mudado": lambda c: (c / "bin" / "pr-review.ts").write_text("meu ajuste\n"),
            "arquivo novo": lambda c: (c / "notas.txt").write_text("x"),
            "arquivo ignorado": lambda c: ((c / "node_modules").mkdir(), (c / "node_modules" / "x").write_text("x")),
            "stash": lambda c: ((c / "bin" / "pr-review.ts").write_text("guardado\n"), git(c, "stash", "-q")),
            "branch local": lambda c: git(c, "branch", "meu-branch"),
            "outro commit": lambda c: ((c / "a.txt").write_text("a"), git(c, "add", "a.txt"), git(c, "commit", "-q", "-m", "meu")),
            # um commit feito e abandonado: só o reflog ainda o tem
            "commit so no reflog": lambda c: ((c / "a.txt").write_text("a"), git(c, "add", "a.txt"), git(c, "commit", "-q", "-m", "meu"),
                                              git(c, "checkout", "-q", git(c, "rev-parse", "HEAD~0^{commit}").strip() and self.remoto.fonte()["sha"])),
            "tag sua": lambda c: git(c, "tag", "minha"),
            "worktree": lambda c: git(c, "worktree", "add", "-q", "--detach", str(c.parent / f"wt-{c.parent.name}")),
        }
        for nome, mudar in casos.items():
            with self.subTest(nome):
                shutil.rmtree(self.cam.dados, ignore_errors=True)
                remoto = Remoto(self.tmp / nome.replace(" ", "-"))
                self.remoto = remoto
                self.enable()
                mudar(self.clone)
                antes = sorted(p.relative_to(self.clone).as_posix() for p in self.clone.rglob("*") if ".git" not in p.parts)
                v2 = remoto.nova_tag("v2", "console.log('v2');\n")
                code, msgs = self.enable(v2)
                self.assertEqual(code, 0, msgs)
                self.assertRegex(msgs[0], rf"não atualizei {self.clone} para a v2: o clone .*; nada foi descartado")
                depois = sorted(p.relative_to(self.clone).as_posix() for p in self.clone.rglob("*") if ".git" not in p.parts)
                self.assertEqual(antes, depois)
                self.assertNotEqual(git(self.clone, "rev-parse", "HEAD"), v2["sha"])
                code, msgs = self.disable()
                self.assertTrue(self.clone.exists(), "the disable keeps it too")
                self.assertTrue(any("ficou como está" in m for m in msgs), msgs)
                self.assertTrue((self.cam.estado / validate.ESTADO).exists(), "and the state: it says the clone is ours")
                self.assertFalse(json.loads((self.cam.estado / validate.ESTADO).read_text())["wrapper"], "the command is gone")
                self.assertFalse(any("mudou desde o enable" in m for m in self.disable()[1]), "a second disable does not complain")
                self.assertNotIn("não foi o fluxo que clonou", " ".join(self.enable(v2)[1]))

    def test_disable_tira_o_clone_limpo_e_o_comando_e_deixa_a_config(self):
        self.enable()
        code, msgs = self.disable()
        self.assertEqual(code, 0)
        self.assertFalse(self.clone.exists())
        self.assertFalse(self.bin.exists())
        self.assertTrue(self.config_pr.exists())
        self.assertFalse((self.cam.estado / validate.ESTADO).exists())
        self.assertIn(f"a config do pr-reviewer fica em {self.config_pr}", msgs)
        self.assertEqual(self.disable()[1], ["o validate não estava ligado nesta máquina"])

    def test_nao_toca_numa_config_nem_num_comando_que_ja_existiam(self):
        self.config_pr.parent.mkdir(parents=True)
        self.config_pr.write_text('{"workspaceRoot": "/outro"}\n')
        self.bin.parent.mkdir(parents=True)
        self.bin.write_text("#!/bin/sh\necho meu\n")
        code, msgs = self.enable(profile="empresa")
        self.assertEqual(code, 0, msgs)
        self.assertEqual(self.config_pr.read_text(), '{"workspaceRoot": "/outro"}\n')
        self.assertEqual(self.bin.read_text(), "#!/bin/sh\necho meu\n")
        self.assertTrue(any("já existia e não foi o fluxo que criou" in m for m in msgs), msgs)
        self.disable()
        self.assertEqual(self.bin.read_text(), "#!/bin/sh\necho meu\n", "the disable only removes ours")

    def test_perfil_escolhido_e_link_no_lugar_da_config(self):
        self.config_pr.parent.mkdir(parents=True)
        os.symlink(self.tmp / "nao-existe", self.config_pr)
        code, msgs = self.enable(profile="empresa")
        self.assertEqual(code, 0, msgs)
        self.assertTrue(self.config_pr.is_symlink(), "a link is never followed nor replaced")
        os.unlink(self.config_pr)
        shutil.rmtree(self.cam.dados)
        self.bin.unlink()
        self.enable(profile="empresa")
        self.assertEqual(json.loads(self.config_pr.read_text())["profile"], "empresa")

    def test_recusa_link_no_lugar_do_clone_perfil_invalido_e_troca_de_clone(self):
        self.clone.parent.mkdir(parents=True)
        os.symlink(self.tmp / "nao-existe", self.clone)
        self.assertEqual(self.enable(), (1, [f"{self.clone} é um link; não mexo nele"]))
        os.unlink(self.clone)
        code, msgs = self.enable(profile="minha empresa")
        self.assertEqual(code, 1)
        self.assertIn("perfil 'minha empresa' inválido", msgs[0])
        self.assertFalse(self.clone.exists())
        self.enable()
        meu = self.tmp / "meu-clone"
        subprocess.run(["git", "clone", "-q", str(self.remoto.bare), str(meu)], check=True, capture_output=True)
        code, msgs = validate.enable(dict(self.cfg, validate={"dir": str(meu)}), self.cam, run=rodar_git, env={}, fonte=self.remoto.fonte())
        self.assertEqual(code, 1)
        self.assertIn("rode `fluxo validate disable` antes de trocar", msgs[0])
        self.assertIn(str(self.clone), self.bin.read_text(), "the command still points at the fluxo clone")

    def test_trocar_de_um_clone_seu_para_outro_passa_pelo_disable(self):
        a, b = self.tmp / "clone-a", self.tmp / "clone-b"
        for c in (a, b):
            subprocess.run(["git", "clone", "-q", str(self.remoto.bare), str(c)], check=True, capture_output=True)
        self.assertEqual(validate.enable(dict(self.cfg, validate={"dir": str(a)}), self.cam, run=rodar_git, env={})[0], 0)
        code, msgs = validate.enable(dict(self.cfg, validate={"dir": str(b)}), self.cam, run=rodar_git, env={})
        self.assertEqual(code, 1)
        self.assertIn(f"o validate está ligado com o seu clone em {a}: rode `fluxo validate disable` antes de trocar", msgs[0])
        self.assertIn(str(a), self.bin.read_text())

    def test_nao_mexe_no_clone_com_uma_validacao_rodando(self):
        self.enable()
        pasta = self.cam.estado / "validar" / "revisao" / "T1"
        pasta.mkdir(parents=True)
        (pasta / "fase.json").write_text(json.dumps({"pid": os.getpid(), "inicio": lock.inicio_do_pid(os.getpid()), "fase": "rodando"}))
        for acao in (self.enable, self.disable):
            code, msgs = acao()
            self.assertEqual(code, 1)
            self.assertIn("há uma validação rodando (revisao/T1)", msgs[0])
        self.assertTrue(self.clone.exists())
        (pasta / "fase.json").write_text(json.dumps({"pid": os.getpid(), "inicio": "outra hora", "fase": "rodando"}))
        self.assertEqual(self.disable()[0], 0, "a reused pid (after a reboot) is not the run")

    def test_disable_mantem_o_comando_que_voce_mudou(self):
        self.enable()
        self.bin.write_text(self.bin.read_text() + "# meu\n")
        code, msgs = self.disable()
        self.assertTrue(self.bin.exists())
        self.assertIn(f"{self.bin} mudou desde o enable; ficou como está", msgs)

    def test_recusa_uma_pasta_que_o_fluxo_nao_clonou(self):
        self.clone.mkdir(parents=True)
        (self.clone / "coisa").write_text("x")
        code, msgs = self.enable()
        self.assertEqual(code, 1)
        self.assertIn("não foi o fluxo que clonou", msgs[0])
        self.assertEqual((self.clone / "coisa").read_text(), "x")

    def test_clone_seu_em_validate_dir_nunca_e_mexido(self):
        meu = self.tmp / "meu-clone"
        subprocess.run(["git", "clone", "-q", str(self.remoto.bare), str(meu)], check=True, capture_output=True)
        cfg = dict(self.cfg, validate={"dir": str(meu)})
        code, msgs = validate.enable(cfg, self.cam, run=rodar_git, env={}, fonte=self.remoto.fonte())
        self.assertEqual(code, 0, msgs)
        self.assertEqual(msgs[0], f"usando o seu clone em {meu} (o fluxo nunca mexe nele)")
        self.assertFalse(self.clone.exists(), "nothing cloned")
        self.assertIn(str(meu / "bin" / "pr-review.ts"), self.bin.read_text())
        validate.disable(cfg, self.cam, run=rodar_git, env={})
        self.assertTrue((meu / "bin" / "pr-review.ts").exists())
        self.assertFalse(self.bin.exists())


class SemRede(unittest.TestCase):
    """Login, timeout e git ausente: mensagem clara, nada pela metade."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cam = escrever_config(self.tmp / "home", "claude", modules=["validate"])
        self.cfg = cfgmod.carregar(self.cam, ("validate",))

    def falso(self, clone):
        def run(argv, **kw):
            if argv[:2] == ["git", "config"]:
                return subprocess.CompletedProcess(argv, 1, "", "")
            return clone(argv, **kw)
        return run

    def test_erro_de_login_mostra_o_fim_do_stderr_e_as_dicas(self):
        run = self.falso(lambda argv, **kw: subprocess.CompletedProcess(argv, 128, "", "linha 1\ngit@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository."))
        code, msgs = validate.enable(self.cfg, self.cam, run=run, env={})
        self.assertEqual(code, 1)
        self.assertIn("Permission denied (publickey)", msgs[0])
        self.assertIn("gh auth setup-git", msgs[0])
        self.assertIn("insteadOf https://github.com/", msgs[0])
        self.assertFalse((self.cam.dados / "pr-reviewer").exists())

    def test_timeout_nao_deixa_pasta_pela_metade(self):
        def clone(argv, **kw):
            if "clone" not in argv:  # só o clone cria a pasta: outro git nunca vira pasta no cwd
                return subprocess.CompletedProcess(argv, 1, "", "")
            Path(argv[-1]).mkdir(parents=True)
            (Path(argv[-1]) / "pela-metade").write_text("x")
            raise subprocess.TimeoutExpired(argv, validate.TIMEOUT)
        code, msgs = validate.enable(self.cfg, self.cam, run=self.falso(clone), env={})
        self.assertEqual(code, 1)
        self.assertIn(f"passou de {validate.TIMEOUT} s", msgs[0])
        self.assertEqual([p.name for p in self.cam.dados.iterdir()], [], "no half clone left")

    def test_sem_git(self):
        def run(argv, **kw):
            raise FileNotFoundError("git")
        code, msgs = validate.enable(self.cfg, self.cam, run=run, env={})
        self.assertEqual((code, msgs), (1, ["sem git nesta máquina: o pr-reviewer vem por git clone"]))

    def test_ambiente_do_git_sem_pergunta_e_sem_repo_de_fora(self):
        vistos = []

        def run(argv, **kw):
            vistos.append((argv, kw))
            return subprocess.CompletedProcess(argv, 1, "", "")
        env = validate._ambiente({"GIT_DIR": "/outro/.git", "GIT_WORK_TREE": "/outro", "PATH": "/bin"}, run)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["GCM_INTERACTIVE"], "never")
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_WORK_TREE", env)
        self.assertEqual(env["GIT_SSH_COMMAND"], "ssh -o BatchMode=yes -o ConnectTimeout=15")
        self.assertEqual(vistos[0][1]["stdin"], subprocess.DEVNULL)
        proprio = validate._ambiente({"GIT_SSH_COMMAND": "ssh -i chave"}, run)
        self.assertEqual(proprio["GIT_SSH_COMMAND"], "ssh -i chave", "yours wins")

        def com_core(argv, **kw):
            return subprocess.CompletedProcess(argv, 0 if "--global" in argv else 1, "ssh -F meu\n", "")
        self.assertNotIn("GIT_SSH_COMMAND", validate._ambiente({}, com_core), "your global core.sshCommand too")
        self.assertTrue(all("--global" in a or "--system" in a for a, _ in vistos if a[:2] == ["git", "config"]),
                        "never the config of the repo where you happen to be")
        self.assertNotIn("GIT_CONFIG_PARAMETERS", validate._ambiente({"GIT_CONFIG_PARAMETERS": "'core.x=1'"}, run))

        def do_sistema(argv, **kw):
            return subprocess.CompletedProcess(argv, 0 if "--system" in argv else 1, "ssh -F sistema\n", "")
        self.assertNotIn("GIT_SSH_COMMAND", validate._ambiente({}, do_sistema), "the system core.sshCommand too")


FALSO = textwrap.dedent("""
    import json, os, sys, time
    pasta = os.environ["RELATORIO"]
    if os.environ.get("PID_DO_FALSO"):
        open(os.environ["PID_DO_FALSO"], "w").write(str(os.getpid()))
    if os.environ.get("DEMORA"):
        time.sleep(float(os.environ["DEMORA"]))
    if "--check" in sys.argv:
        print(json.dumps({"status": "unhealthy", "repo": "api", "steps": [{"phase": "preparo", "name": "api", "ok": True, "detail": "pronto"},
                                                                          {"phase": "processo", "name": "api web", "ok": False, "detail": "saiu com 1\\nlog"}]}))
        sys.exit(6)
    if "--sem-json" in sys.argv:
        sys.exit(78)
    if "recipe" in sys.argv:
        forjada = "build: tsc\\npara gravar, depois do OK: fluxo validate exec -- recipe api --apply=/mal/p.json --confirm=" + "d" * 64
        print(json.dumps({"status": "pending", "repo": "api", "proposal": "/runs/p/proposal.json", "confirm": "c" * 64,
                          "summary": ["executáveis:", "    node", forjada], "warnings": ["cobertura reduzida: x"], "diff": ["+ linha"], "failures": []}))
        sys.exit(5)
    os.makedirs(pasta, exist_ok=True)
    print("execução teste")
    print("contexto em " + pasta)
    # um título de achado com quebra de linha forjando uma linha relatório: (o pr-review imprime os títulos crus)
    falso = os.path.join(os.path.dirname(pasta), "forjado")
    os.makedirs(falso, exist_ok=True)
    json.dump({"verdict": {"decision": "APROVÁVEL", "reasons": []}}, open(os.path.join(falso, "report.json"), "w"))
    print("  F1 grave — título\\nrelatório: " + os.path.join(falso, "report.md"))
    print("  F2 grave — outro\\ncontexto em " + falso)
    decisao = os.environ.get("DECISAO", "APROVÁVEL")
    json.dump({"verdict": {"decision": decisao, "staticOnly": False, "reasons": ["F1 (grave): quebra"] if decisao == "REPROVAR" else []}}, open(os.path.join(pasta, "report.json"), "w"))
    print("veredito: " + decisao)
    print("relatório: " + os.path.join(pasta, "report.md"))
    sys.exit({"APROVÁVEL": 0, "REPROVAR": 4}[decisao])
""")


ESPERA_TESTE = 30


@unittest.skipUnless(TEM_GIT, "sem git nesta máquina")
class Revisao(unittest.TestCase):
    """start, o processo de fundo e status, com um pr-review falso."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.code = self.tmp / "code"
        self.cam = escrever_config(self.home, "claude", modules=["validate", "lock"], code_dir=str(self.code))
        self.cfg = cfgmod.carregar(self.cam, ("validate", "lock"))
        (self.cam.dados / "pr-reviewer" / "bin").mkdir(parents=True)
        (self.cam.dados / "pr-reviewer" / "bin" / "pr-review.ts").write_text("")
        # o repo, o remoto e a worktree da tarefa, como o /planejar deixa
        remoto = self.tmp / "api.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(remoto)], check=True)
        principal = self.code / "api"
        principal.mkdir(parents=True)
        git(principal, "init", "-q", "-b", "develop")
        (principal / "a.txt").write_text("a")
        git(principal, "add", "-A")
        git(principal, "commit", "-q", "-m", "base")
        git(principal, "remote", "add", "origin", str(remoto))
        git(principal, "push", "-q", "origin", "develop")
        self.wt = self.code / "api-T1"
        git(principal, "worktree", "add", "-q", "-b", "feat/T1", str(self.wt))
        planos = self.cam.dados / "planos"
        planos.mkdir(parents=True)
        self.plano = planos / "T1.md"
        self.plano.write_text("---\nstatus: em andamento\nrepos: [api]\nbase: develop\n---\n# T1\n")
        self.falso = self.tmp / "falso.py"
        self.falso.write_text(FALSO)

    def test_monta_um_local_por_repo_na_branch_da_worktree_e_a_base_do_plano(self):
        argv, avisos = validate.preparar_revisao(self.cfg, self.cam, "T1", "kiro", run=rodar_git)
        clone = self.cam.dados / "pr-reviewer" / "bin" / "pr-review.ts"
        self.assertEqual(argv, ["node", str(clone), "--local", "api:feat/T1", "--base", "origin/develop", "--plan", str(self.plano), "--backend", "kiro"])
        self.assertEqual(avisos, [])
        sem, _ = validate.preparar_revisao(self.cfg, self.cam, "T1", "copilot", run=rodar_git)
        self.assertNotIn("--backend", sem, "the pr-reviewer has no copilot adapter: its config decides")

    def test_base_e_repos_do_plano_sao_conferidos(self):
        self.plano.write_text("---\nrepos: [api]\nbase: main\n---\n")
        argv, avisos = validate.preparar_revisao(self.cfg, self.cam, "T1", None, run=rodar_git)
        self.assertEqual(argv[argv.index("--base") + 1], "origin/main", "the base of the plan")
        self.assertEqual(avisos, ["api: o git fetch origin main falhou; a revisão usa a origin/main que já está aqui"])
        for ruim, msg in ((["api"], "--upload-pack=x"), (["../x"], None), (["."], None), (["ação"], None)):
            base = msg or "develop"
            with self.subTest(repos=ruim, base=base):
                self.plano.write_text(f"---\nrepos: {json.dumps(ruim)}\nbase: {json.dumps(base)}\n---\n")
                with self.assertRaisesRegex(cfgmod.ErroConfig, "base .* inválida" if msg else "precisa de repos"):
                    validate.preparar_revisao(self.cfg, self.cam, "T1", None, run=rodar_git)
        self.plano.write_text("---\nrepos: [api]\nbase: develop\n---\n")
        git(self.wt, "checkout", "-q", "--detach")
        with self.assertRaisesRegex(cfgmod.ErroConfig, "não está numa branch"):
            validate.preparar_revisao(self.cfg, self.cam, "T1", None, run=rodar_git)

    def test_recusa_mudanca_nao_commitada_worktree_ausente_e_plano_sem_repos(self):
        (self.wt / "b.txt").write_text("b")
        with self.assertRaisesRegex(cfgmod.ErroConfig, "mudança não commitada; o pr-review só vê o que está commitado"):
            validate.preparar_revisao(self.cfg, self.cam, "T1", None, run=rodar_git)
        (self.wt / "b.txt").unlink()
        self.plano.write_text("---\nrepos: [api, web]\n---\n")
        with self.assertRaisesRegex(cfgmod.ErroConfig, "a worktree .*web-T1 não existe"):
            validate.preparar_revisao(self.cfg, self.cam, "T1", None, run=rodar_git)
        self.plano.write_text("---\nstatus: x\n---\n")
        with self.assertRaisesRegex(cfgmod.ErroConfig, "o frontmatter precisa de repos"):
            validate.preparar_revisao(self.cfg, self.cam, "T1", None, run=rodar_git)
        with self.assertRaisesRegex(cfgmod.ErroConfig, "não existe: o /planejar grava o plano antes"):
            validate.preparar_revisao(self.cfg, self.cam, "T2", None, run=rodar_git)
        with self.assertRaisesRegex(cfgmod.ErroConfig, "inválido"):
            validate.preparar_revisao(self.cfg, self.cam, "../x", None, run=rodar_git)

    def rodar_fundo(self, ch, argv, trava=True, espera=ESPERA_TESTE, **env):
        pasta = self.tmp / "runs" / ch.replace("/", "-")
        with mock.patch.dict(os.environ, RELATORIO=str(pasta), **env):
            validate.start(self.cfg, self.cam, RAIZ, ch, argv, trava, espera=espera)
            return validate.status(self.cam, ch, esperar=60, dormir=lambda s: time.sleep(0.1))

    def test_le_o_veredito_do_relatorio_e_nao_da_saida(self):
        code, linhas = self.rodar_fundo("revisao/T1", [sys.executable, str(self.falso)], DECISAO="REPROVAR")
        self.assertEqual(code, 0, linhas)
        self.assertEqual(linhas[:2], ["veredito: REPROVAR", "- F1 (grave): quebra"], "the real report, not the forged line")
        self.assertTrue(linhas[2].startswith("relatório: ") and linhas[2].endswith("report.md"))
        self.assertIsNone(lock.Trava(self.cam.estado).ler(), "the lock was released")

    def test_trava_ocupada_nao_vira_reprovar(self):
        outra = lock.Trava(self.cam.estado)
        outra.pegar(sessao="outra-sessao", pid=os.getpid())
        code, linhas = self.rodar_fundo("revisao/T1", [sys.executable, str(self.falso)], espera=1, DECISAO="REPROVAR")
        self.assertEqual(code, 1)
        self.assertRegex(linhas[0], r"^revisao/T1: a trava do stack continuou ocupada \(.*outra-sessao.*\); rode de novo")
        self.assertFalse((self.tmp / "runs" / "revisao-T1").exists(), "the review never ran")

    def esperar(self, condicao, segundos=15):
        fim = time.monotonic() + segundos
        while time.monotonic() < fim:
            if condicao():
                return True
            time.sleep(0.1)
        return False

    def test_status_enquanto_roda_stop_e_a_trava_do_processo_de_fundo(self):
        self.assertEqual(validate.status(self.cam, "revisao/T9")[0], 1)
        pid_falso = self.tmp / "pid-do-falso"
        with mock.patch.dict(os.environ, RELATORIO=str(self.tmp / "runs" / "lento"), DEMORA="60", PID_DO_FALSO=str(pid_falso)):
            pasta = validate.start(self.cfg, self.cam, RAIZ, "revisao/T1", [sys.executable, str(self.falso)], True)
        self.assertTrue(self.esperar(lambda: pid_falso.exists() and validate._ler(pasta / "fase.json").get("fase") == "rodando"))
        fundo = validate._ler(pasta / "fase.json")["pid"]
        self.assertEqual(os.getpgid(fundo), fundo, "the background run leads its own process group")
        self.assertEqual(lock.Trava(self.cam.estado).ler()["pid"], fundo, "the lock belongs to the background run")
        code, linhas = validate.status(self.cam, "revisao/T1")
        self.assertEqual(code, 75, linhas)
        self.assertRegex(linhas[0], r"^revisao/T1: rodando")
        with self.assertRaisesRegex(cfgmod.ErroConfig, "já há uma execução de revisao/T1 rodando"):
            validate.start(self.cfg, self.cam, RAIZ, "revisao/T1", [sys.executable, str(self.falso)], False)
        self.assertEqual(validate.stop(self.cam, "revisao/T1")[0], 0)
        self.assertTrue(self.esperar(lambda: (pasta / "fim.json").exists()))
        falso = int(pid_falso.read_text())
        self.assertTrue(self.esperar(lambda: not validate.lock.inicio_do_pid(falso)), "the pr-review died with it")
        self.assertIsNone(lock.Trava(self.cam.estado).ler(), "and only then the lock was released")
        code, linhas = validate.status(self.cam, "revisao/T1")
        self.assertEqual((code, linhas[0]), (1, "revisao/T1: parado pelo fluxo validate stop (saída 143)"))
        self.assertEqual(validate.stop(self.cam, "revisao/T1"), (1, "não há execução de revisao/T1 rodando"))

    def test_sinal_so_no_processo_de_fundo_tambem_para_o_pr_review(self):
        for sinal in (signal.SIGTERM, signal.SIGHUP):
            with self.subTest(sinal=sinal):
                pid_falso = self.tmp / f"pid-{sinal}"
                with mock.patch.dict(os.environ, RELATORIO=str(self.tmp / "runs" / str(sinal)), DEMORA="60", PID_DO_FALSO=str(pid_falso)):
                    pasta = validate.start(self.cfg, self.cam, RAIZ, "revisao/T1", [sys.executable, str(self.falso)], True)
                self.assertTrue(self.esperar(lambda: pid_falso.exists() and validate._ler(pasta / "fase.json").get("fase") == "rodando"))
                os.kill(validate._ler(pasta / "fase.json")["pid"], sinal)  # só nele, não no grupo
                self.assertTrue(self.esperar(lambda: (pasta / "fim.json").exists()))
                self.assertTrue(self.esperar(lambda: not lock.inicio_do_pid(int(pid_falso.read_text()))), "forwarded to the pr-review")
                self.assertEqual(validate._ler(pasta / "fim.json")["codigo"], 128 + sinal)
                self.assertIsNone(lock.Trava(self.cam.estado).ler())

    def test_stop_na_espera_da_trava_e_corrida_do_stop(self):
        outra = lock.Trava(self.cam.estado)
        outra.pegar(sessao="outra-sessao", pid=os.getpid())
        with mock.patch.dict(os.environ, RELATORIO=str(self.tmp / "runs" / "espera")):
            pasta = validate.start(self.cfg, self.cam, RAIZ, "revisao/T1", [sys.executable, str(self.falso)], True)
        self.assertTrue(self.esperar(lambda: validate._ler(pasta / "fase.json").get("fase") == "esperando a trava do stack"))
        self.assertEqual(validate.stop(self.cam, "revisao/T1")[0], 0)
        self.assertTrue(self.esperar(lambda: (pasta / "fim.json").exists()))
        self.assertEqual(validate.status(self.cam, "revisao/T1")[1][0], "revisao/T1: parado pelo fluxo validate stop (saída 143)")
        self.assertEqual(outra.ler()["sessao"], "outra-sessao", "the lock of the other session is untouched")
        pasta2 = self.cam.estado / "validar" / "revisao" / "T2"
        pasta2.mkdir(parents=True)
        (pasta2 / "fase.json").write_text(json.dumps({"pid": os.getpid(), "inicio": lock.inicio_do_pid(os.getpid()), "fase": "rodando"}))

        def sumiu(pid, sinal):
            raise ProcessLookupError()
        self.assertEqual(validate.stop(self.cam, "revisao/T2", matar=sumiu), (1, "a execução de revisao/T2 já tinha terminado"))

    def test_start_nao_sobrescreve_a_fase_que_o_filho_ja_gravou(self):
        class Filho:
            pid = os.getpid()

        def popen(argv, **kw):
            pasta = self.cam.estado / "validar" / "revisao" / "T1"
            (pasta / "fase.json").write_text(json.dumps({"pid": os.getpid(), "fase": "esperando a trava do stack"}))
            return Filho()
        validate.start(self.cfg, self.cam, RAIZ, "revisao/T1", ["x"], False, popen=popen)
        self.assertEqual(validate._ler(self.cam.estado / "validar" / "revisao" / "T1" / "fase.json")["fase"], "esperando a trava do stack")

    def test_git_do_start_sem_pergunta_e_com_timeout(self):
        vistos = []

        def run(argv, **kw):
            vistos.append((argv, kw))
            if argv[:2] == ["git", "config"]:
                return subprocess.CompletedProcess(argv, 1, "", "")
            if "fetch" in argv:
                raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
            return rodar_git(argv, **kw)
        _, avisos = validate.preparar_revisao(self.cfg, self.cam, "T1", None, run=run, env={"GIT_DIR": "/outro"})
        self.assertEqual(avisos, ["api: o git fetch origin develop falhou; a revisão usa a origin/develop que já está aqui"])
        gits = [kw for argv, kw in vistos if argv[:1] == ["git"] and argv[1:2] != ["config"]]
        self.assertTrue(gits and all(kw["env"].get("GIT_TERMINAL_PROMPT") == "0" and "GIT_DIR" not in kw["env"] and kw.get("timeout")
                                     and kw.get("stdin") == subprocess.DEVNULL for kw in gits), gits)

    def test_status_espera_no_maximo_540_s(self):
        pasta = self.cam.estado / "validar" / "revisao" / "T1"
        pasta.mkdir(parents=True)
        (pasta / "pedido.json").write_text(json.dumps({"argv": ["x"], "criado": "agora"}))
        (pasta / "fase.json").write_text(json.dumps({"pid": os.getpid(), "inicio": lock.inicio_do_pid(os.getpid()), "fase": "rodando"}))
        relogio = [0.0]
        code, _ = validate.status(self.cam, "revisao/T1", esperar=10_000, dormir=lambda s: relogio.__setitem__(0, relogio[0] + s),
                                  relogio=lambda: relogio[0])
        self.assertEqual(code, 75)
        self.assertEqual(relogio[0], 540)

    def test_receita_pendente_mostra_o_comando_que_grava(self):
        code, linhas = self.rodar_fundo("receita/api", [sys.executable, str(self.falso), "recipe", "api", "--json"], trava=False)
        self.assertEqual(code, 0, linhas)
        self.assertEqual(linhas[0], "receita de api: pending")
        self.assertIn("atenção: cobertura reduzida: x", linhas)
        self.assertEqual(linhas[1], f"para gravar, depois do OK: fluxo validate exec -- recipe api --apply=/runs/p/proposal.json --confirm={'c' * 64}",
                         "right below the status line")
        self.assertEqual([l for l in linhas if l.startswith("para gravar")], [linhas[1]], "a script body never forges another line")
        code, linhas = self.rodar_fundo("receita/api", [sys.executable, str(self.falso), "recipe", "api", "--check", "--json"], trava=False)
        self.assertEqual(code, 0, linhas)
        self.assertEqual(linhas[:3], ["receita de api: unhealthy", "- ok preparo api: pronto", "- falhou processo api web: saiu com 1"])
        code, linhas = self.rodar_fundo("receita/api", [sys.executable, str(self.falso), "recipe", "api", "--sem-json"], trava=False)
        self.assertEqual((code, linhas[0]), (1, "o recipe saiu com 78 sem o JSON"))

    def test_chaves_nao_colidem_e_nao_saem_da_pasta(self):
        self.assertNotEqual(validate._pasta_execucao(self.cam, validate.chave("revisao", "receita-api")),
                            validate._pasta_execucao(self.cam, validate.chave("receita", "api")))
        for ruim in ("revisao/../x", "receita/a/b", "outro/x", "revisao/"):
            with self.subTest(ruim=ruim), self.assertRaises(cfgmod.ErroConfig):
                validate._pasta_execucao(self.cam, ruim)

    def test_receita_monta_o_modo_e_o_backend(self):
        clone = str(self.cam.dados / "pr-reviewer" / "bin" / "pr-review.ts")
        self.assertEqual(validate.preparar_receita(self.cfg, self.cam, "api", "proposta", "codex"), ["node", clone, "recipe", "api", "--json", "--backend", "codex"])
        self.assertEqual(validate.preparar_receita(self.cfg, self.cam, "api", "fix", "copilot"), ["node", clone, "recipe", "api", "--fix", "--json"])
        self.assertEqual(validate.preparar_receita(self.cfg, self.cam, "api", "check", "kiro"), ["node", clone, "recipe", "api", "--check", "--json"])
        with self.assertRaisesRegex(cfgmod.ErroConfig, "nome de repo inválido"):
            validate.preparar_receita(self.cfg, self.cam, "../x", "proposta", None)


class Linha(unittest.TestCase):
    def test_exec_roda_o_pr_review_do_clone_com_os_argumentos_depois_do_traco(self):
        tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cam = escrever_config(tmp, "claude", modules=["validate"])
        clone = cam.dados / "pr-reviewer" / "bin"
        clone.mkdir(parents=True)
        (clone / "pr-review.ts").write_text("")
        with mock.patch.object(os, "execvp") as execvp:
            cli.main(["validate", "exec", "--home", str(tmp), "--", "recipe", "api", "--json"], RAIZ)
        execvp.assert_called_once_with("node", ["node", str(clone / "pr-review.ts"), "recipe", "api", "--json"])

    def test_exec_sem_o_clone_explica_o_enable(self):
        tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        escrever_config(tmp, "claude", modules=["validate"])
        with mock.patch.object(os, "execvp") as execvp, mock.patch("sys.stderr") as err:
            code = cli.main(["validate", "exec", "--home", str(tmp), "--", "doctor"], RAIZ)
        self.assertEqual(code, 2)
        execvp.assert_not_called()
        self.assertIn("rode `fluxo validate enable`", "".join(c.args[0] for c in err.write.call_args_list))

    def cli(self, *argv, modules=("validate", "lock"), clone=True):
        tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cam = escrever_config(tmp, "claude", modules=list(modules))
        if clone:
            (cam.dados / "pr-reviewer" / "bin").mkdir(parents=True)
            (cam.dados / "pr-reviewer" / "bin" / "pr-review.ts").write_text("")
        vistos = []
        with mock.patch.object(validate, "start", lambda cfg, c, raiz, ch, argv, trava, **kw: vistos.append((ch, argv[2:], trava))), \
             mock.patch.object(validate, "preparar_revisao", lambda cfg, c, id_, backend: (["node", "x", "--local", f"api:{id_}"], [])), \
             mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            code = cli.main(["validate", *argv, "--home", str(tmp)], RAIZ)
        return code, vistos, cam

    def test_linha_de_comando_do_start(self):
        self.assertEqual(self.cli("start", "T1")[:2], (0, [("revisao/T1", ["--local", "api:T1"], True)]), "the review takes the lock")
        self.assertEqual(self.cli("start", "T1", modules=("validate",))[1][0][2], False, "only with the lock module")
        self.assertEqual(self.cli("start", "--recipe", "api", "--backend", "kiro")[1], [("receita/api", ["recipe", "api", "--json", "--backend", "kiro"], False)])
        self.assertEqual(self.cli("start", "--recipe", "api", "--fix")[1], [("receita/api", ["recipe", "api", "--fix", "--json"], False)])
        self.assertEqual(self.cli("start", "--recipe", "api", "--check")[1], [("receita/api", ["recipe", "api", "--check", "--json"], True)], "only the check brings the stack up")
        for argv in (["start", "--recipe", "api", "--fix", "--check"], ["start", "T1", "--fix"], ["start"], ["start", "T1", "--recipe", "api"], ["status"]):
            with self.subTest(argv=argv):
                code, vistos, _ = self.cli(*argv)
                self.assertEqual((code, vistos), (2, []))

    def test_stop_pela_linha_de_comando(self):
        code, _, _ = self.cli("stop", "T1")
        self.assertEqual(code, 1, "nothing running")

    def test_disable_vale_com_o_modulo_desligado(self):
        code, _, cam = self.cli("disable", modules=(), clone=False)
        self.assertEqual(code, 0)

    def test_init_mantem_a_chave_validate(self):
        tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cam = escrever_config(tmp, "claude", modules=["validate"], validate={"dir": "~/meu-clone"})
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            cli.main(["init", "--force", "--tool", "claude", "--forge", "github", "--home", str(tmp)], RAIZ)
        self.assertEqual(json.loads(cam.config.read_text())["validate"], {"dir": "~/meu-clone"})

    def test_install_e_uninstall_avisam_pela_linha_de_comando(self):
        tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cam = escrever_config(tmp, "claude", modules=["validate"])
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            cli.main(["install", "--home", str(tmp)], RAIZ)
        self.assertIn("aviso: validate: rode `fluxo validate enable`", out.getvalue())
        cfgmod.criar_estado(cam.estado)
        (cam.estado / validate.ESTADO).write_text("{}")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            cli.main(["uninstall", "--home", str(tmp)], RAIZ)
        self.assertIn("aviso: validate: o clone do pr-reviewer, o comando pr-review e a config dele ficam", out.getvalue())

    def test_validate_dir_na_config(self):
        base = {"version": 1, "tools": ["claude"], "forge": "github", "os": "linux"}
        self.assertEqual(cfgmod.validar(base, ("validate",))["validate"], {"dir": None})
        cfgmod.validar(dict(base, validate={"dir": "~/code/pr-reviewer-core"}), ("validate",))
        for ruim in ({"dir": ""}, {"dir": 3}, {"pasta": "x"}, "x", {"dir": "clone/relativo"}):
            with self.subTest(ruim=ruim), self.assertRaisesRegex(cfgmod.ErroConfig, "validate"):
                cfgmod.validar(dict(base, validate=ruim), ("validate",))

    def test_install_avisa_o_enable_e_o_uninstall_avisa_o_que_fica(self):
        tmp = Path(tempfile.mkdtemp(prefix="fluxo-validate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        cam = escrever_config(tmp, "claude", modules=["validate"])
        cfg = cfgmod.carregar(cam, ("validate",))
        self.assertEqual(validate.avisos_do_install(cfg, cam), ["validate: rode `fluxo validate enable` para trazer o pr-reviewer (o install não usa rede)"])
        self.assertEqual(validate.avisos_do_uninstall(cam), [])
        cfgmod.criar_estado(cam.estado)
        (cam.estado / validate.ESTADO).write_text("{}")
        self.assertEqual(validate.avisos_do_install(cfg, cam), [])
        self.assertIn("rode `fluxo validate disable` antes", validate.avisos_do_uninstall(cam)[0])


if __name__ == "__main__":
    unittest.main()
