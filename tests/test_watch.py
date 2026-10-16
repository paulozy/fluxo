"""fluxo watch: normalização do GitHub e do GitLab, eventos, linha de base, sem rede, estado e avisos."""
import json
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from base import RAIZ, escrever_config
from fluxo import avisar, config, forge, montar, watch

T0 = 1_791_000_000  # um instante qualquer de out/2026


def iso(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pr_gh(n=1, sha="a1", state="OPEN", reviews=(), threads=(), comments=(), ci="SUCCESS", merged=None, arquivado=False):
    return {"number": n, "title": f"PR {n}", "url": f"https://github.com/o/r/pull/{n}", "state": state,
            "mergedAt": merged, "updatedAt": iso(T0), "headRefOid": sha,
            "repository": {"nameWithOwner": "o/r", "isArchived": arquivado},
            "reviews": {"nodes": list(reviews)}, "reviewThreads": {"nodes": list(threads)},
            "comments": {"nodes": list(comments)},
            "commits": {"nodes": [{"commit": {"oid": sha, "statusCheckRollup": {"state": ci} if ci else None}}]}}


def resposta_gh(*prs, login="eu"):
    return json.dumps({"data": {"viewer": {"login": login, "pullRequests": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": list(prs)}}}})


class GhFalso:
    def __init__(self, *prs):
        self.saida, self.falha = resposta_gh(*prs), None

    def __call__(self, argv, **kw):
        assert argv[:3] == ["gh", "api", "graphql"], argv
        if self.falha:
            return subprocess.CompletedProcess(argv, 1, "", self.falha)
        return subprocess.CompletedProcess(argv, 0, self.saida, "")


def pessoa(login):
    return {"login": login, "__typename": "User"}


class Normalizar(unittest.TestCase):
    def test_github(self):
        bot = {"login": "ci[bot]", "__typename": "Bot"}
        n = pr_gh(sha="b2", ci="ERROR",
                  reviews=[{"id": "R1", "state": "APPROVED", "body": "", "submittedAt": iso(T0), "author": pessoa("ana"),
                            "commit": {"oid": "b2"}},
                           {"id": "R2", "state": "COMMENTED", "body": "  ", "submittedAt": iso(T0), "author": pessoa("ana"),
                            "commit": {"oid": "b2"}},
                           {"id": "R3", "state": "CHANGES_REQUESTED", "body": "", "submittedAt": iso(T0),
                            "author": pessoa("rui"), "commit": {"oid": "a1"}}],
                  threads=[{"isResolved": False, "comments": {"nodes": [{"id": "C1", "createdAt": iso(T0), "author": pessoa("rui")}]}},
                           {"isResolved": True, "comments": {"nodes": [{"id": "C2", "createdAt": iso(T0), "author": pessoa("rui")}]}}],
                  comments=[{"id": "I1", "createdAt": iso(T0), "author": bot}])
        p = forge.GitHub._normalizar(n)
        self.assertEqual((p["ref"], p["estado"], p["sha"], p["ci"]), ("o/r#1", "aberta", "b2", "FAILURE"))
        self.assertEqual([a["autor"] for a in p["aprovacoes"]], ["ana"])
        self.assertEqual([(f["id"], f["tipo"], f["bot"]) for f in p["falas"]],
                         [("R3", "review", False), ("C1", "thread", False), ("I1", "comentario", True)])

    def test_gitlab(self):
        respostas = {
            "user": {"username": "eu"},
            "merge_requests?scope=created_by_me&state=opened&non_archived=true&per_page=50": [{"project_id": 7, "iid": 3}],
            "projects/7/merge_requests/3": {"title": "MR 3", "web_url": "https://g/x/-/merge_requests/3", "state": "opened",
                                            "sha": "c3", "references": {"full": "grupo/proj!3"},
                                            "head_pipeline": {"sha": "c3", "status": "failed"}},
            "projects/7/merge_requests/3/versions": [{"created_at": iso(T0 + 100)}, {"created_at": iso(T0)}],
            "projects/7/merge_requests/3/approvals": {"approved_by": [
                {"user": {"username": "ana"}, "approved_at": iso(T0 + 50)},
                {"user": {"username": "rui"}, "approved_at": iso(T0 + 200)}]},
            "projects/7/merge_requests/3/discussions?per_page=100&page=1": [
                {"notes": [{"id": 11, "system": True, "author": {"username": "ana"}, "created_at": iso(T0)}]},
                {"notes": [{"id": 12, "resolvable": True, "resolved": False, "author": {"username": "ana"}, "created_at": iso(T0)}]},
                {"notes": [{"id": 13, "resolvable": True, "resolved": True, "author": {"username": "ana"}, "created_at": iso(T0)}]}],
            "projects/grupo%2Fvelha/merge_requests/9": {"state": "merged", "title": "velha", "merged_at": iso(T0 + 300),
                                                         "references": {"full": "grupo/velha!9"}, "sha": "z"},
        }
        chamadas = []

        def run(argv, **kw):
            chamadas.append(argv)
            self.assertEqual(argv[:4], ["glab", "api", "--hostname", "g.exemplo.com"])
            return subprocess.CompletedProcess(argv, 0, json.dumps(respostas[argv[4]]), "")

        login, mrs = forge.GitLab("g.exemplo.com", run=run).coletar(abertas_antes=["grupo/proj!3", "grupo/velha!9"])
        self.assertEqual(login, "eu")
        mr, velha = mrs
        self.assertEqual((mr["ref"], mr["ci"], mr["versao_em"]), ("grupo/proj!3", "FAILURE", T0 + 100))
        self.assertEqual([f["id"] for f in mr["falas"]], ["12"])  # sem nota de sistema nem thread resolvida
        self.assertEqual((velha["ref"], velha["estado"]), ("grupo/velha!9", "mergeada"))
        evs = {e["chave"] for e in forge.eventos(mrs, "eu", T0)}
        self.assertIn("aprovada:grupo/proj!3@c3:rui", evs)  # depois do último push
        self.assertNotIn("aprovada:grupo/proj!3@c3:ana", evs)  # antes do último push: é de um commit velho
        self.assertIn("merge:grupo/velha!9", evs)

    def test_gitlab_sem_host(self):
        with self.assertRaisesRegex(forge.ErroForge, "gitlab.host"):
            forge.GitLab("")


class Consultas(unittest.TestCase):
    def test_abertas_e_mergeadas_em_separado(self):
        """Numa consulta só, as mergeadas recentes empurravam para fora as abertas paradas há tempo."""
        pedidos = []

        def run(argv, **kw):
            estado = [a for a in argv if a.startswith("estados[]=")][0].split("=")[1]
            pedidos.append(estado)
            pr = pr_gh(n=1 if estado == "OPEN" else 2, state=estado, merged=None if estado == "OPEN" else iso(T0))
            return subprocess.CompletedProcess(argv, 0, resposta_gh(pr), "")

        login, prs = forge.GitHub(run=run).coletar()
        self.assertEqual(pedidos, ["OPEN", "MERGED"])
        self.assertEqual([(p["ref"], p["estado"]) for p in prs], [("o/r#1", "aberta"), ("o/r#2", "mergeada")])


class Arquivados(unittest.TestCase):
    """Repo/projeto arquivado é só leitura: a PR/MR nunca muda nem fecha, e só enchia a conta."""

    def test_github_filtra_e_pede_o_campo(self):
        consultas = []

        def run(argv, **kw):
            consultas.append([a for a in argv if a.startswith("query=")][0])
            return subprocess.CompletedProcess(argv, 0, resposta_gh(pr_gh(n=5, arquivado=True), pr_gh(n=6)), "")

        _, prs = forge.GitHub(run=run).coletar()
        self.assertEqual([p["ref"] for p in prs], ["o/r#6"])
        self.assertTrue(all("isArchived" in q for q in consultas))  # sem o campo na query, o filtro não vê nada

    def test_gitlab_pede_non_archived_e_descarta_o_que_foi_arquivado(self):
        caminhos = []

        def run(argv, **kw):
            caminho = argv[4]
            caminhos.append(caminho)
            respostas = {
                "user": {"username": "eu"},
                "projects/g%2Fvelho/merge_requests/2": {"state": "opened", "title": "x", "sha": "s",
                                                        "references": {"full": "g/velho!2"}},
                "projects/g%2Fvelho": {"archived": True},
            }
            if caminho.startswith("merge_requests?"):
                return subprocess.CompletedProcess(argv, 0, "[]", "")
            return subprocess.CompletedProcess(argv, 0, json.dumps(respostas[caminho]), "")

        _, mrs = forge.GitLab("h", run=run).coletar(abertas_antes=["g/velho!2"])
        self.assertEqual(mrs, [])  # o projeto foi arquivado com a MR aberta: sai de vez
        self.assertIn("non_archived=true", [c for c in caminhos if c.startswith("merge_requests?")][0])

    def test_gitlab_mr_aberta_de_projeto_ativo_continua(self):
        """Ela some da lista quando há mais de 50 abertas (a lista é uma página): não pode sair da conta."""
        def run(argv, **kw):
            respostas = {"user": {"username": "eu"},
                         "projects/g%2Fativo/merge_requests/2": {"state": "opened", "title": "x", "sha": "s",
                                                                 "references": {"full": "g/ativo!2"}},
                         "projects/g%2Fativo": {"archived": False}}
            caminho = argv[4]
            if caminho.startswith("merge_requests?"):
                return subprocess.CompletedProcess(argv, 0, "[]", "")
            return subprocess.CompletedProcess(argv, 0, json.dumps(respostas[caminho]), "")

        _, mrs = forge.GitLab("h", run=run).coletar(abertas_antes=["g/ativo!2"])
        self.assertEqual([(m["ref"], m["estado"]) for m in mrs], [("g/ativo!2", "aberta")])


class Eventos(unittest.TestCase):
    def pr(self, **kw):
        base = {"ref": "o/r#1", "titulo": "t", "url": "u", "estado": "aberta", "merged_em": None, "sha": "s1",
                "ci": None, "aprovacoes": [], "falas": [], "versao_em": None}
        base.update(kw)
        return base

    def test_quem_conta(self):
        falas = [{"id": "1", "autor": "eu", "quando": T0, "tipo": "thread", "bot": False},
                 {"id": "2", "autor": "ana", "quando": T0, "tipo": "thread", "bot": False},
                 {"id": "3", "autor": "ci", "quando": T0, "tipo": "comentario", "bot": True},
                 {"id": "4", "autor": "ana", "quando": T0 - 10, "tipo": "review", "bot": False}]
        aprov = [{"autor": "ana", "sha": "s1", "quando": T0, "bot": False},
                 {"autor": "rui", "sha": "s0", "quando": T0, "bot": False}]
        evs = forge.eventos([self.pr(falas=falas, aprovacoes=aprov, ci="PENDING")], "eu", T0 - 5)
        self.assertEqual(sorted(e["chave"] for e in evs), ["aprovada:o/r#1@s1:ana", "review:o/r#1:2"])

    def test_ci_e_merge(self):
        evs = forge.eventos([self.pr(ci="FAILURE"), self.pr(ref="o/r#2", estado="mergeada", merged_em=T0),
                             self.pr(ref="o/r#3", estado="mergeada", merged_em=T0 - 100)], "eu", T0 - 50)
        self.assertEqual(sorted(e["chave"] for e in evs), ["ci:o/r#1@s1:FAILURE", "merge:o/r#2"])


class Tick(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.cam = escrever_config(self.home, "claude")
        self.cfg = config.carregar(self.cam)
        self.avisos = []

    def tearDown(self):
        self.tmp.cleanup()

    def tick(self, run, agora):
        return watch.tick(self.cfg, self.cam, run=run, avisar_fn=lambda t, x: self.avisos.append((t, x)), agora=agora)

    def test_linha_de_base_depois_novidade_uma_vez(self):
        run = GhFalso(pr_gh(ci="SUCCESS", threads=[
            {"isResolved": False, "comments": {"nodes": [{"id": "C1", "createdAt": iso(T0), "author": pessoa("ana")}]}}]))
        code, resumo = self.tick(run, T0 + 10)
        self.assertEqual(code, 0)
        self.assertIn("linha de base", resumo)
        self.assertEqual(self.avisos, [])  # o que já existia não avisa
        run.saida = resposta_gh(pr_gh(ci="SUCCESS", threads=[
            {"isResolved": False, "comments": {"nodes": [{"id": "C2", "createdAt": iso(T0 + 20), "author": pessoa("ana")}]}}]))
        self.assertEqual(self.tick(run, T0 + 30), (0, "1 novidade(s)"))
        self.assertEqual(self.avisos, [("o/r#1: ana comentou (thread)", "PR 1")])
        self.assertEqual(self.tick(run, T0 + 40), (0, "0 novidade(s)"))  # a mesma não avisa de novo
        linhas = (self.cam.estado / "avisos.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(linhas[-1])["ref"], "o/r#1")

    def test_leva_grande_vira_um_aviso(self):
        run = GhFalso(pr_gh())
        self.tick(run, T0)
        threads = [{"isResolved": False, "comments": {"nodes": [{"id": f"C{i}", "createdAt": iso(T0 + 5), "author": pessoa("ana")}]}}
                   for i in range(6)]
        run.saida = resposta_gh(pr_gh(threads=threads))
        self.tick(run, T0 + 10)
        self.assertEqual(len(self.avisos), 1)
        self.assertIn("6 novidades", self.avisos[0][1])

    def test_sem_rede_registra_uma_vez(self):
        run = GhFalso(pr_gh())
        self.tick(run, T0)
        run.falha = "dial tcp: i/o timeout"
        for i in range(3):
            code, resumo = self.tick(run, T0 + 10 + i)
            self.assertEqual(code, 0)
        erros = [l for l in (self.cam.estado / "avisos.jsonl").read_text().splitlines() if '"erro"' in l]
        self.assertEqual(len(erros), 1)
        self.assertIn("erro: gh saiu com 1", watch.status(self.cam, agora=T0 + 60))
        run.falha = None
        self.tick(run, T0 + 100)
        self.assertNotIn("erro:", watch.status(self.cam, agora=T0 + 160))

    def test_estado_corrompido_recomeca_pela_base(self):
        self.cam.estado.mkdir(parents=True)
        (self.cam.estado / "vigia.json").write_text("{quebrado")
        code, resumo = self.tick(GhFalso(pr_gh()), T0)
        self.assertEqual(code, 0)
        self.assertIn("linha de base", resumo)
        self.assertEqual(len(list(self.cam.estado.glob("vigia.corrupt-*.json"))), 1)
        self.assertIn("corrompido", self.avisos[0][1])

    def test_tick_concorrente_sai(self):
        import fcntl
        self.cam.estado.mkdir(parents=True)
        with open(self.cam.estado / "vigia.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            self.assertEqual(self.tick(GhFalso(pr_gh()), T0), (0, "outro tick está rodando"))


class Avisar(unittest.TestCase):
    def test_formato_do_tmux_e_markup_do_notify(self):
        import os
        antes = os.environ.get("TMUX")
        os.environ["TMUX"] = "/tmp/falso,1,0"  # nenhum tmux de verdade é chamado: só o argv é conferido
        try:
            cmds = avisar.comandos("linux", "#{session_name} #H", "<a href='x'>clique</a>\x1b[2J",
                                   which=lambda b: b)
        finally:
            os.environ.pop("TMUX") if antes is None else os.environ.__setitem__("TMUX", antes)
        (notify, _), (tmux_, _) = cmds
        self.assertEqual(notify[-1], "&lt;a href=&#x27;x&#x27;&gt;clique&lt;/a&gt;[2J")  # sem ESC e sem markup
        self.assertTrue(tmux_[-1].startswith("##{session_name} ##H"))

    def test_texto_de_terceiro_nunca_entra_no_script(self):
        titulo, texto = 'x" & do shell script "rm -rf ~', "$(touch /tmp/pwn) `id`"
        (argv, env), = avisar.comandos("mac", titulo, texto, which=lambda b: b if b == "osascript" else None)
        self.assertEqual(argv[-2:], [titulo, texto])  # por argv
        self.assertTrue(all(titulo not in a for a in argv[:-2]))
        (argv, env), = avisar.comandos("wsl", titulo, texto, which=lambda b: b if b == "powershell.exe" else None)
        self.assertNotIn(titulo, " ".join(argv))  # por variável de ambiente
        self.assertEqual((env["FLUXO_TITULO"], env["FLUXO_TEXTO"]), (titulo, texto))
        self.assertIn("FLUXO_TEXTO/w", env["WSLENV"])  # /w: do WSL para o Windows (o /u é o contrário)
        self.assertNotIn("/u", env["WSLENV"])

    def test_tenta_em_ordem(self):
        tentados = []

        def run(argv, **kw):
            tentados.append(argv[0])
            return subprocess.CompletedProcess(argv, 1 if argv[0] == "notify-send" else 0, b"", b"")

        import os
        antes = os.environ.get("TMUX")
        os.environ["TMUX"] = "/tmp/falso,1,0"  # só para o aviso cair no tmux: nenhum tmux de verdade é chamado
        try:
            self.assertEqual(avisar.avisar("linux", "t", "x", run=run, which=lambda b: b), "tmux")
        finally:
            os.environ.pop("TMUX") if antes is None else os.environ.__setitem__("TMUX", antes)
        self.assertEqual(tentados, ["notify-send", "tmux"])


class Revisao(unittest.TestCase):
    """Os casos da revisão de 09/10: reaviso em 30 dias, merge perdido, paginação, aprovação sem data."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.cam = escrever_config(self.home, "claude")
        self.cfg = config.carregar(self.cam)
        self.avisos = []

    def tearDown(self):
        self.tmp.cleanup()

    def tick(self, run, agora):
        return watch.tick(self.cfg, self.cam, run=run, avisar_fn=lambda t, x: self.avisos.append((t, x)), agora=agora)

    def test_nao_reavisa_depois_de_30_dias(self):
        run = GhFalso(pr_gh())
        self.tick(run, T0)
        run.saida = resposta_gh(pr_gh(ci="FAILURE"))
        self.tick(run, T0 + 60)
        self.assertEqual(len(self.avisos), 1)
        (ci,) = [a for a in watch.ler_avisos(self.cam.estado) if a.get("tipo") == "ci"]
        self.assertEqual(ci["ci"], "FAILURE")  # o estado vai junto, e não só o texto (o alerta do panel usa)
        for dia in range(1, 70, 5):  # a mesma PR com o mesmo CI, todo dia, por mais de dois meses
            self.tick(run, T0 + dia * 86400)
        self.assertEqual(len(self.avisos), 1)

    def test_merge_antigo_que_volta_a_lista_nao_reavisa(self):
        run = GhFalso(pr_gh())
        self.tick(run, T0)
        velho = pr_gh(n=9, state="MERGED", merged=iso(T0 + 100))
        run.saida = resposta_gh(velho)
        self.tick(run, T0 + 200)  # avisa o merge
        run.saida = resposta_gh(pr_gh())
        self.tick(run, T0 + 40 * 86400)  # some da lista e a chave é esquecida
        run.saida = resposta_gh(velho)
        self.tick(run, T0 + 41 * 86400)  # volta (alguém comentou): é de 41 dias atrás
        self.assertEqual([a for a in self.avisos if "mergeada" in a[0]], [("o/r#9: mergeada", "PR 9")])

    def test_aprovacao_do_github_sem_commit_nao_e_do_head(self):
        n = pr_gh(sha="b2", reviews=[{"id": "R1", "state": "APPROVED", "body": "", "submittedAt": iso(T0),
                                      "author": pessoa("ana"), "commit": None}])
        tipos = [e["tipo"] for e in forge.eventos([forge.GitHub._normalizar(n)], "eu", T0 - 1)]
        self.assertNotIn("aprovada", tipos)
        self.assertIn("ci", tipos)  # o resto da PR continua valendo

    def test_aprovacao_do_gitlab_sem_data_avisa_uma_vez_por_sha(self):
        pr = {"ref": "g/p!1", "titulo": "t", "url": "u", "estado": "aberta", "merged_em": None, "sha": "c1", "ci": None,
              "aprovacoes": [{"autor": "ana", "sha": None, "quando": None, "bot": False}], "falas": [], "versao_em": T0}
        self.assertEqual([e["chave"] for e in forge.eventos([pr], "eu", T0)], ["aprovada:g/p!1@c1:ana"])

    def test_merge_do_gitlab_com_erro_passageiro_nao_se_perde(self):
        cfg = config.validar({"version": 1, "tools": ["copilot"], "forge": "gitlab", "os": "mac",
                              "gitlab": {"host": "g.exemplo.com"}})
        estado = {"falha": False, "aberta": True}

        def run(argv, **kw):
            caminho = argv[4]
            if caminho == "user":
                return subprocess.CompletedProcess(argv, 0, json.dumps({"username": "eu"}), "")
            if caminho.startswith("merge_requests?"):
                lista = [{"project_id": 7, "iid": 3}] if estado["aberta"] else []
                return subprocess.CompletedProcess(argv, 0, json.dumps(lista), "")
            if estado["falha"] and not estado["aberta"]:
                return subprocess.CompletedProcess(argv, 1, "", "502 Bad Gateway")
            mr = {"title": "MR 3", "web_url": "u", "state": "opened" if estado["aberta"] else "merged", "sha": "c3",
                  "merged_at": None if estado["aberta"] else iso(T0 + 500), "references": {"full": "g/p!3"}}
            respostas = {"projects/7/merge_requests/3": mr, "projects/g%2Fp/merge_requests/3": mr}
            return subprocess.CompletedProcess(argv, 0, json.dumps(respostas.get(caminho, [])), "")

        tick = lambda agora: watch.tick(cfg, self.cam, run=run, avisar_fn=lambda t, x: self.avisos.append(t), agora=agora)
        tick(T0)
        estado.update(aberta=False, falha=True)
        tick(T0 + 300)  # saiu da lista, e o detalhe deu 502
        self.assertEqual(self.avisos, [])
        estado["falha"] = False
        tick(T0 + 600)  # a rede voltou
        self.assertEqual(self.avisos, ["g/p!3: mergeada"])

    def test_discussoes_do_gitlab_paginadas(self):
        paginas = []

        def run(argv, **kw):
            caminho = argv[4]
            paginas.append(caminho)
            n = int(caminho.rsplit("page=", 1)[1])
            lote = [{"notes": [{"id": n * 1000 + i, "system": True}]} for i in range(100 if n < 3 else 7)]
            return subprocess.CompletedProcess(argv, 0, json.dumps(lote), "")

        todas = forge.GitLab("h", run=run)._todas("x?per_page=100")
        self.assertEqual((len(todas), len(paginas)), (207, 3))

    def test_loop_sobrevive_a_um_tick_com_bug(self):
        chamadas = []

        def tick_quebrado(cfg, cam):
            chamadas.append(1)
            raise TypeError("vigia.json com tipo errado")

        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            watch.loop(self.cfg, self.cam, 1, dormir=lambda s: None, voltas=3, tick=tick_quebrado)
        self.assertEqual(len(chamadas), 3)
        self.assertIn("erro no tick", out.getvalue())

    def test_ligar_no_wsl_registra_e_nao_duplica(self):
        cfg = config.validar({"version": 1, "tools": ["kiro"], "forge": "github", "os": "wsl", "modules": ["watch"]},
                             montar.modulos_existentes(RAIZ))
        abertas, vivos = [], set()

        def run(argv, **kw):
            if argv[:2] == ["tmux", "new-window"]:
                abertas.append(argv)
                vivos.add(("%4", "44"))
                return subprocess.CompletedProcess(argv, 0, "@4 %4 44\n", "")
            if argv[:2] == ["tmux", "list-panes"]:
                return subprocess.CompletedProcess(argv, 0, "".join(f"{p} {q} 0\n" for p, q in vivos), "")
            if argv[:2] == ["tmux", "kill-pane"]:
                vivos.discard(("%4", "44"))
                return subprocess.CompletedProcess(argv, 0, "", "")
            raise AssertionError(argv)

        self.assertFalse(self.cam.estado.exists())  # o install não cria: o ligar tem de criar antes de abrir
        self.assertEqual(watch.ligar(cfg, self.cam, run=run, dentro_do_tmux=True)[0], 0)
        code, msg = watch.ligar(cfg, self.cam, run=run, dentro_do_tmux=True)
        self.assertIn("já está ligado", msg)
        self.assertEqual(len(abertas), 1)
        self.assertEqual(watch.desligar(cfg, self.cam, run=run)[0], 0)
        self.assertEqual(vivos, set())


class Agendamento(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def extras(self, cenario, **cfg):
        cam = escrever_config(self.home, cenario, modules=["watch"], **cfg)
        c = config.carregar(cam, montar.modulos_existentes(RAIZ))
        return watch.arquivos_extras(c, montar.Arquivo)

    def test_mac_linux_wsl(self):
        (plist,), _ = self.extras("copilot", watch={"interval": 600})
        dados = plistlib.loads(plist.conteudo)
        self.assertEqual(dados["StartInterval"], 600)
        self.assertEqual(dados["ProgramArguments"][-1], 'exec "$HOME/.local/bin/fluxo" watch tick')
        unidades, _ = self.extras("claude")
        self.assertEqual([a.rel for a in unidades], [".config/systemd/user/fluxo-watch.service",
                                                     ".config/systemd/user/fluxo-watch.timer"])
        self.assertIn("%h/.local/bin/fluxo watch tick", unidades[0].conteudo.decode())
        nada, avisos = self.extras("kiro-v2")
        self.assertEqual(nada, [])
        self.assertIn("janela do tmux", avisos[0])


if __name__ == "__main__":
    unittest.main()
