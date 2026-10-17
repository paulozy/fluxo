"""Módulo validate: o enable traz o pr-reviewer numa tag fixada, e nada que você mudou no clone se perde."""
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
from fluxo import cli, validate

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

    def test_nunca_descarta_o_que_voce_mudou_no_clone(self):
        casos = {
            "arquivo mudado": lambda c: (c / "bin" / "pr-review.ts").write_text("meu ajuste\n"),
            "arquivo novo": lambda c: (c / "notas.txt").write_text("x"),
            "arquivo ignorado": lambda c: ((c / "node_modules").mkdir(), (c / "node_modules" / "x").write_text("x")),
            "stash": lambda c: ((c / "bin" / "pr-review.ts").write_text("guardado\n"), git(c, "stash", "-q")),
            "branch local": lambda c: git(c, "branch", "meu-branch"),
            "outro commit": lambda c: ((c / "a.txt").write_text("a"), git(c, "add", "a.txt"), git(c, "commit", "-q", "-m", "meu")),
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
            if argv[:3] == ["git", "config", "--get"]:
                return subprocess.CompletedProcess(argv, 1, "", "")
            return clone(argv, **kw)
        return run

    def test_erro_de_login_mostra_o_fim_do_stderr_e_as_dicas(self):
        run = self.falso(lambda argv, **kw: subprocess.CompletedProcess(argv, 128, "", "linha 1\ngit@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository."))
        code, msgs = validate.enable(self.cfg, self.cam, run=run, env={})
        self.assertEqual(code, 1)
        self.assertIn("Permission denied (publickey)", msgs[0])
        self.assertIn("ssh -T git@github.com", msgs[0])
        self.assertFalse((self.cam.dados / "pr-reviewer").exists())

    def test_timeout_nao_deixa_pasta_pela_metade(self):
        def clone(argv, **kw):
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
            return subprocess.CompletedProcess(argv, 0, "ssh -F meu\n", "")
        self.assertNotIn("GIT_SSH_COMMAND", validate._ambiente({}, com_core), "your core.sshCommand too")


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

    def test_validate_dir_na_config(self):
        base = {"version": 1, "tools": ["claude"], "forge": "github", "os": "linux"}
        self.assertEqual(cfgmod.validar(base, ("validate",))["validate"], {"dir": None})
        cfgmod.validar(dict(base, validate={"dir": "~/code/pr-reviewer-core"}), ("validate",))
        for ruim in ({"dir": ""}, {"dir": 3}, {"pasta": "x"}, "x"):
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
