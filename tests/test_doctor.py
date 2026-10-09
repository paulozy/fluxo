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

    def test_so_a_trava_sem_tmux_e_aviso(self):
        cam = escrever_config(self.home, "copilot", modulos=["trava"])
        r = doctor.checar(cam, RAIZ, cfg=config.carregar(cam, montar.modulos_existentes(RAIZ)),
                          which=lambda b: None if b == "tmux" else f"/usr/bin/{b}",
                          run=lambda *a, **k: Resp(0, "1.0\n"), conectar=lambda *a: io.BytesIO(), versao=(3, 9),
                          env={"PATH": ""})
        self.assertTrue(any(n == doctor.AVISO and t.startswith("tmux não está") for n, t in r))
        self.assertNotIn(doctor.FALHA, [n for n, _ in r])

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
            for argv in (["init", "--ferramenta", "kiro", "--forge", "github", "--so", "wsl"], ["install"]):
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
