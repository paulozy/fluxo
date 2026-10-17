"""doctor (com binários e rede falsos) e empacotar (zip levado a uma pasta sem git)."""
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from base import RAIZ, escrever_config
from fluxo import cli, config, doctor, empacotar, montar
from fluxo import validate as validate_mod
import json


class Resp:
    def __init__(self, code, out=""):
        self.returncode, self.stdout, self.stderr = code, out, ""


class Doctor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def checar(self, cenario, which=None, run=None, conectar=None, path=""):
        cam = escrever_config(self.home, cenario)
        cfg = config.carregar(cam)
        return doctor.checar(cam, RAIZ, cfg=cfg, which=which or (lambda b: f"/usr/bin/{b}"),
                             run=run or (lambda *a, **k: Resp(0, "1.0\n")),
                             conectar=conectar or (lambda *a: io.BytesIO()), versao=(3, 9),
                             env={"PATH": path})

    def niveis(self, resultados):
        return {texto.split(":")[0].split(" ")[0]: nivel for nivel, texto in resultados}

    def test_tudo_certo_menos_o_path(self):
        r = self.checar("claude", path=str(self.home / ".local" / "bin"))
        self.assertNotIn(doctor.FALHA, [n for n, _ in r])
        self.assertIn((doctor.AVISO, "nada instalado ainda: rode `fluxo install`"), r)

    def test_sem_a_ferramenta_e_falha(self):
        r = self.checar("copilot", which=lambda b: None if b == "copilot" else f"/usr/bin/{b}")
        self.assertIn((doctor.FALHA, "copilot não está no PATH"), r)

    def test_so_a_ferramenta_padrao_e_obrigatoria(self):
        cam = escrever_config(self.home, "copilot-claude")
        cfg = config.carregar(cam, montar.modulos_existentes(RAIZ))
        for falta, nivel in (("claude", doctor.AVISO), ("copilot", doctor.FALHA)):
            with self.subTest(falta=falta):
                r = doctor.checar(cam, RAIZ, cfg=cfg, which=lambda b: None if b == falta else f"/usr/bin/{b}",
                                  run=lambda *a, **k: Resp(0, "1.0\n"), conectar=lambda *a: io.BytesIO(),
                                  versao=(3, 9), env={"PATH": ""})
                self.assertIn((nivel, f"{falta} não está no PATH"), r)
                self.assertIn("copilot, claude (padrão: copilot)", r[1][1])

    def test_codex_home_e_agents_override(self):
        cam = escrever_config(self.home, "codex")
        cfg = config.carregar(cam, montar.modulos_existentes(RAIZ))

        def avisos(**env):
            r = doctor.checar(cam, RAIZ, cfg=cfg, which=lambda b: f"/usr/bin/{b}", run=lambda *a, **k: Resp(0, "1.0\n"),
                              conectar=lambda *a: io.BytesIO(), versao=(3, 9), env=dict(env, PATH=""))
            return [t for n, t in r if n == doctor.AVISO and ("CODEX_HOME" in t or "override" in t)]

        self.assertEqual(avisos(), [])
        self.assertEqual(avisos(CODEX_HOME="~/.codex/"), [])  # o mesmo lugar, escrito de outro jeito
        self.assertEqual(len(avisos(CODEX_HOME=str(self.home / "outro"))), 1)
        override = self.home / ".codex" / "AGENTS.override.md"
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text("\n")
        self.assertEqual(avisos(), [])  # vazio: o Codex segue para o AGENTS.md
        override.write_text("minhas regras\n")
        self.assertIn("não está vazio", avisos()[0])
        override.write_bytes("regras em latin-1: ação\n".encode("latin-1"))  # não pode derrubar o doctor
        self.assertIn("não está vazio", avisos()[0])
        # o override que conta é o do CODEX_HOME efetivo, e não o do ~/.codex
        outro = self.home / "outro"
        outro.mkdir()
        self.assertEqual([t for t in avisos(CODEX_HOME=str(outro)) if "override" in t], [])
        (outro / "AGENTS.override.md").write_text("do outro\n")
        self.assertIn(str(outro / "AGENTS.override.md"), " ".join(avisos(CODEX_HOME=str(outro))))
        # sem o codex em tools, nada disso é checado
        cam = escrever_config(self.home, "claude")
        r = doctor.checar(cam, RAIZ, cfg=config.carregar(cam), which=lambda b: f"/usr/bin/{b}",
                          run=lambda *a, **k: Resp(0, "1.0\n"), conectar=lambda *a: io.BytesIO(), versao=(3, 9),
                          env={"PATH": "", "CODEX_HOME": str(outro)})
        self.assertFalse([t for n, t in r if "CODEX_HOME" in t or "override" in t])

    def test_vpn_desligada_e_aviso(self):
        def recusa(*a):
            raise OSError("timed out")
        r = self.checar("copilot", conectar=recusa)
        self.assertTrue(any(n == doctor.AVISO and "VPN desligada?" in t for n, t in r))
        self.assertNotIn(doctor.FALHA, [n for n, _ in r])

    def test_kiro_sem_login_e_aviso(self):
        def run(argv, **k):
            return Resp(1, "error: You are not logged in, please log in with kiro-cli login") if "validate" in argv else Resp(0, "kiro-cli 2.28\n")
        cam = escrever_config(self.home, "kiro-v2")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["install", "--home", str(self.home)], RAIZ)
        r = doctor.checar(cam, RAIZ, cfg=config.carregar(cam), which=lambda b: f"/usr/bin/{b}", run=run,
                          conectar=lambda *a: io.BytesIO(), versao=(3, 9), env={"PATH": ""})
        self.assertIn((doctor.AVISO, "kiro-cli sem login: não deu para validar os agentes (kiro-cli login)"), r)

    def test_drift_do_manifesto(self):
        escrever_config(self.home, "claude")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["install", "--home", str(self.home)], RAIZ)
        (self.home / ".claude" / "rules" / "fluxo.md").write_text("mexi\n")
        (self.home / ".claude" / "skills" / "revisar" / "SKILL.md").unlink()
        r = self.checar("claude")
        texto = [t for n, t in r if "instalados" in t][0]
        self.assertIn("1 sumiram", texto)
        self.assertIn("1 editados", texto)

    def test_modulos_checam_o_que_precisam(self):
        cam = escrever_config(self.home, "copilot-tudo")
        r = doctor.checar(cam, RAIZ, cfg=config.carregar(cam, montar.modulos_existentes(RAIZ)),
                          which=lambda b: None if b in ("tmux", "pbcopy") else f"/usr/bin/{b}",
                          run=lambda *a, **k: Resp(0, "1.0\n"), conectar=lambda *a: io.BytesIO(), versao=(3, 9),
                          env={"PATH": ""})
        self.assertTrue(any(n == doctor.FALHA and t.startswith("tmux não está") for n, t in r))
        self.assertTrue(any(n == doctor.AVISO and t.startswith("sem pbcopy") for n, t in r))

    def test_so_o_lock_sem_tmux_e_aviso(self):
        cam = escrever_config(self.home, "copilot", modules=["lock"])
        r = doctor.checar(cam, RAIZ, cfg=config.carregar(cam, montar.modulos_existentes(RAIZ)),
                          which=lambda b: None if b == "tmux" else f"/usr/bin/{b}",
                          run=lambda *a, **k: Resp(0, "1.0\n"), conectar=lambda *a: io.BytesIO(), versao=(3, 9),
                          env={"PATH": ""})
        self.assertTrue(any(n == doctor.AVISO and t.startswith("tmux não está") for n, t in r))
        self.assertNotIn(doctor.FALHA, [n for n, _ in r])

    def validate(self, versao_node="v22.18.0", which=None, estado=None, conf=None, head=None):
        cam = escrever_config(self.home, "claude", modules=["validate"], code_dir=str(self.home / "code"))
        if estado is not None:
            config.criar_estado(cam.estado)
            (cam.estado / "pr-reviewer.json").write_text(json.dumps(estado))
        if conf is not None:
            destino = self.home / ".config" / "pr-reviewer" / "config.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(json.dumps(conf))

        def run(argv, **kw):  # um fake por argv: o de sempre devolve "1.0" para tudo
            if argv[:2] == ["node", "--version"]:
                return Resp(0, versao_node + "\n")
            if argv[:1] == ["git"] and argv[-2:] == ["rev-parse", "HEAD"]:
                return Resp(0, (head or validate_mod.PR_REVIEWER["sha"]) + "\n")
            return Resp(0, "1.0\n")
        r = doctor.checar(cam, RAIZ, cfg=config.carregar(cam, montar.modulos_existentes(RAIZ)),
                          which=which or (lambda b: f"/usr/bin/{b}"), run=run, conectar=lambda *a: io.BytesIO(),
                          versao=(3, 9), env={"PATH": ""})
        return [(n, t) for n, t in r if any(p in t for p in ("node", "docker", "pr-reviewer", "workspaceRoot", "validate"))]

    def test_validate_tudo_certo(self):
        r = self.validate(estado={"dir": str(self.home / ".local/share/fluxo/pr-reviewer"), "sha": validate_mod.PR_REVIEWER["sha"]},
                          conf={"workspaceRoot": str(self.home / "code")})
        self.assertNotIn(doctor.FALHA, [n for n, _ in r], r)
        self.assertNotIn(doctor.AVISO, [n for n, _ in r], r)
        self.assertIn((doctor.OK, "node v22.18.0"), r)
        self.assertTrue(any(t.startswith(f"pr-reviewer {validate_mod.PR_REVIEWER['tag']} em ") for _, t in r))
        self.assertIn((doctor.OK, "para conferir os agentes de verdade (custa): fluxo validate exec -- doctor"), r)

    def test_validate_node_velho_ou_ausente_e_falha(self):
        self.assertIn((doctor.FALHA, "node v22.17.1: o pr-reviewer precisa do 22.18 ou mais novo"), self.validate("v22.17.1"))
        self.assertIn((doctor.OK, "node v24.1.0"), self.validate("v24.1.0"))
        self.assertIn((doctor.OK, "node v23.0.0"), self.validate("v23.0.0"), "a newer major with a lower minor")
        sem = self.validate(which=lambda b: None if b == "node" else f"/usr/bin/{b}")
        self.assertIn((doctor.FALHA, "node não está no PATH: o pr-reviewer roda em Node 22.18 ou mais novo"), sem)

    def test_validate_avisa_o_que_falta_trazer_ou_acertar(self):
        r = self.validate(which=lambda b: None if b == "docker" else f"/usr/bin/{b}")
        self.assertIn((doctor.AVISO, "docker não está no PATH: o /validar fica só com a revisão estática"), r)
        self.assertIn((doctor.AVISO, "o pr-reviewer não foi trazido: rode `fluxo validate enable`"), r)
        self.assertTrue(any(n == doctor.AVISO and "não existe ou não é JSON" in t for n, t in r))
        velho = self.validate(estado={"dir": "x", "sha": "a" * 40}, head="b" * 40, conf={"workspaceRoot": "/outro"})
        self.assertTrue(any(n == doctor.AVISO and "não está na v" in t for n, t in velho), velho)
        self.assertTrue(any(n == doctor.AVISO and "o workspaceRoot do pr-reviewer (/outro) não é o code_dir" in t for n, t in velho))
        proprio = self.validate(estado={"dir": "/meu", "proprio": True}, conf={"workspaceRoot": str(self.home / "code")})
        self.assertIn((doctor.OK, "pr-reviewer: o seu clone em /meu"), proprio)

    def test_sem_config_e_falha(self):
        cam = config.Caminhos(home=self.home)
        r = doctor.checar(cam, RAIZ, cfg=None, erro_cfg="sem config", versao=(3, 9))
        self.assertEqual(r[-1], (doctor.FALHA, "config: sem config"))
        self.assertEqual(doctor.checar(cam, RAIZ, cfg=None, erro_cfg="x", versao=(3, 8))[0][0], doctor.FALHA)


class Empacotar(unittest.TestCase):
    def test_zip_instala_sem_git(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d).resolve()
            repo = d / "repo"  # sem .git: cai na listagem por pasta
            shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
            destino = empacotar.empacotar(repo, d / "dist")
            self.assertEqual(destino.name, "fluxo-dev.zip")
            with zipfile.ZipFile(destino) as z:
                nomes = set(z.namelist())
                self.assertIn("fluxo/fluxo", nomes)
                self.assertIn("fluxo/VERSAO", nomes)
                self.assertIn("fluxo/nucleo/skills/planejar/SKILL.md", nomes)
                z.extractall(d / "extraido")
            home = d / "casa"
            fluxo = d / "extraido" / "fluxo" / "fluxo"
            for argv in (["init", "--tool", "kiro", "--forge", "github", "--os", "wsl"], ["install"]):
                r = subprocess.run([sys.executable, str(fluxo), *argv, "--home", str(home)],
                                   capture_output=True, text=True, timeout=60)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue((home / ".kiro" / "steering" / "fluxo.md").exists())

    def test_nao_empacota_com_dado_proibido(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            shutil.copytree(RAIZ, repo, ignore=shutil.ignore_patterns(".git", "dist", "__pycache__"))
            uuid_falso = "-".join(["0123abcd", "0123", "4abc", "8abc", "0123456789ab"])
            (repo / "nota.md").write_text(f"um id {uuid_falso}\n")
            with self.assertRaisesRegex(RuntimeError, "varredura"):
                empacotar.empacotar(repo, Path(d) / "dist")
            self.assertFalse((Path(d) / "dist").exists())


if __name__ == "__main__":
    unittest.main()
