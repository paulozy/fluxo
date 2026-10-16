"""Leitura das PRs/MRs do usuário para o vigia: GitHub pelo `gh`, GitLab pelo `glab api`.

Cada forge devolve PRs normalizadas, e `eventos()` decide o que é novidade. Só leitura: o vigia nunca
faz merge, nunca comenta e nunca muda nada no forge.

PR normalizada:
    {ref, titulo, url, estado: aberta|mergeada|fechada, sha, ci: SUCCESS|FAILURE|PENDING|None,
     aprovacoes: [{autor, sha (ou None), quando}], falas: [{id, autor, quando, tipo, bot}],
     versao_em: ts da última versão (GitLab: para saber se a aprovação é do commit atual)}
"""
import json
import subprocess
from datetime import datetime
from urllib.parse import quote


class ErroForge(Exception):
    """Falha de rede, de login ou de resposta. O vigia registra uma vez e tenta no próximo tick."""


def iso(ts):
    """ISO 8601 do GitHub/GitLab para epoch. O fromisoformat do 3.9 não aceita o Z."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _rodar(run, argv, timeout=60):
    try:
        r = run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ErroForge(f"{argv[0]}: {exc}")
    if r.returncode:
        raise ErroForge(f"{argv[0]} saiu com {r.returncode}: {(r.stderr or r.stdout or '').strip()[:200]}")
    try:
        return json.loads(r.stdout or "null")
    except ValueError:
        raise ErroForge(f"{argv[0]}: resposta que não é JSON")


GH_QUERY = """query($after: String, $estados: [PullRequestState!]) { viewer { login pullRequests(first: 30,
  after: $after, states: $estados, orderBy: {field: UPDATED_AT, direction: DESC}) { pageInfo { hasNextPage endCursor }
  nodes { number title url state mergedAt updatedAt headRefOid repository { nameWithOwner isArchived }
    reviews(last: 30) { nodes { id state body submittedAt author { login __typename } commit { oid } } }
    reviewThreads(first: 50) { nodes { isResolved comments(last: 1) { nodes { id createdAt author { login __typename } } } } }
    comments(last: 30) { nodes { id createdAt author { login __typename } } }
    commits(last: 1) { nodes { commit { oid statusCheckRollup { state } } } } } } } }"""


def _bot(autor):
    return not autor or autor.get("__typename") == "Bot" or (autor.get("login") or "").endswith("[bot]")


class GitHub:
    def __init__(self, run=subprocess.run, paginas_abertas=5):
        self.run, self.paginas_abertas = run, paginas_abertas

    def _paginas(self, estado, maximo):
        depois, login = None, None
        for _ in range(maximo):
            argv = ["gh", "api", "graphql", "-f", f"query={GH_QUERY}", "-f", f"estados[]={estado}"] + \
                   (["-f", f"after={depois}"] if depois else [])
            dados = _rodar(self.run, argv)
            if not isinstance(dados, dict) or dados.get("errors") or not dados.get("data"):
                raise ErroForge(f"gh graphql: {str((dados or {}).get('errors'))[:200]}")
            viewer = dados["data"]["viewer"]
            login = viewer["login"]
            bloco = viewer["pullRequests"]
            yield login, bloco["nodes"]
            if not bloco["pageInfo"]["hasNextPage"]:
                return
            depois = bloco["pageInfo"]["endCursor"]

    def coletar(self):
        """(login, [PR normalizada]): todas as abertas (até 150) e as 30 mergeadas mais recentes. Numa
        consulta só, as mergeadas recentes empurravam para fora as abertas paradas há tempo."""
        login, vistas, prs = None, set(), []
        for estado, maximo in (("OPEN", self.paginas_abertas), ("MERGED", 1)):
            for login, nos in self._paginas(estado, maximo):
                for n in nos:
                    if (n.get("repository") or {}).get("isArchived"):
                        continue  # repo arquivado é só leitura: a PR nunca muda nem fecha, e só enchia a conta
                    pr = self._normalizar(n)
                    if pr["ref"] not in vistas:
                        vistas.add(pr["ref"])
                        prs.append(pr)
        return login, prs

    @staticmethod
    def _normalizar(n):
        ref = f"{n['repository']['nameWithOwner']}#{n['number']}"
        commit = ((n.get("commits") or {}).get("nodes") or [{}])[-1].get("commit") or {}
        rollup = commit.get("statusCheckRollup") or {}
        ci = {"SUCCESS": "SUCCESS", "FAILURE": "FAILURE", "ERROR": "FAILURE"}.get(rollup.get("state"),
                                                                                 "PENDING" if rollup else None)
        falas, aprovacoes = [], []
        for r in (n.get("reviews") or {}).get("nodes") or []:
            autor = r.get("author") or {}
            if r.get("state") == "APPROVED":
                # sem commit, a aprovação não é do head atual ("?" nunca casa com um sha)
                aprovacoes.append({"autor": autor.get("login"), "sha": (r.get("commit") or {}).get("oid") or "?",
                                   "quando": iso(r.get("submittedAt")), "bot": _bot(autor)})
            if r.get("state") == "CHANGES_REQUESTED" or (r.get("body") or "").strip():
                falas.append({"id": r["id"], "autor": autor.get("login"), "quando": iso(r.get("submittedAt")),
                              "tipo": "review", "bot": _bot(autor)})
        for t in (n.get("reviewThreads") or {}).get("nodes") or []:
            ultimo = ((t.get("comments") or {}).get("nodes") or [None])[-1]
            if ultimo and not t.get("isResolved"):
                autor = ultimo.get("author") or {}
                falas.append({"id": ultimo["id"], "autor": autor.get("login"), "quando": iso(ultimo.get("createdAt")),
                              "tipo": "thread", "bot": _bot(autor)})
        for c in (n.get("comments") or {}).get("nodes") or []:
            autor = c.get("author") or {}
            falas.append({"id": c["id"], "autor": autor.get("login"), "quando": iso(c.get("createdAt")),
                          "tipo": "comentario", "bot": _bot(autor)})
        estado = {"OPEN": "aberta", "MERGED": "mergeada"}.get(n.get("state"), "fechada")
        return {"ref": ref, "titulo": n.get("title") or "", "url": n.get("url"), "estado": estado,
                "merged_em": iso(n.get("mergedAt")), "sha": n.get("headRefOid"), "ci": ci,
                "aprovacoes": aprovacoes, "falas": falas, "versao_em": None}


class GitLab:
    """Pelo `glab api --hostname`: sem o hostname, fora de um repo git (o cwd do launchd é /), o glab
    iria para o gitlab.com."""

    def __init__(self, host, run=subprocess.run):
        if not host:
            raise ErroForge("gitlab.host não está na config (fluxo init --gitlab-host …)")
        self.host, self.run = host, run

    def api(self, caminho):
        return _rodar(self.run, ["glab", "api", "--hostname", self.host, caminho])

    def _todas(self, caminho, maximo=20):
        """Todas as páginas: as discussões vêm das mais velhas para as mais novas e incluem as notas de
        sistema (uma por push), então numa MR longa as novas ficam além da primeira página."""
        out = []
        for pagina in range(1, maximo + 1):
            lote = self.api(f"{caminho}&page={pagina}") or []
            out += lote
            if len(lote) < 100:
                break
        return out

    def coletar(self, abertas_antes=()):
        """(login, [MR normalizada]). `abertas_antes`: refs que o vigia viu abertas; a que sumiu da lista
        é buscada para saber se foi mergeada."""
        login = (self.api("user") or {}).get("username")
        # non_archived: no endpoint global o padrão é false, e MR de projeto arquivado nunca fecha
        lista = self.api("merge_requests?scope=created_by_me&state=opened&non_archived=true&per_page=50") or []
        mrs = [self._detalhar(m["project_id"], m["iid"]) for m in lista]
        vistas = {m["ref"] for m in mrs}
        for ref in abertas_antes:
            if ref not in vistas and "!" in ref:
                projeto, iid = ref.rsplit("!", 1)
                try:
                    mr = self._detalhar(projeto, iid, so_estado=True)
                    # sumiu da lista e continua aberta: o projeto foi arquivado (só leitura); sai da conta
                    if mr["estado"] == "aberta" and (self.api(f"projects/{quote(str(projeto), safe='')}") or {}).get("archived"):
                        continue
                    mrs.append(mr)
                except ErroForge:  # rede: o vigia guarda a ref e tenta de novo, senão o merge se perde
                    mrs.append({"ref": ref, "titulo": "", "url": None, "estado": "desconhecida", "merged_em": None,
                                "sha": None, "ci": None, "aprovacoes": [], "falas": [], "versao_em": None})
        return login, mrs

    def _detalhar(self, projeto, iid, so_estado=False):
        pid = quote(str(projeto), safe="")
        base = f"projects/{pid}/merge_requests/{iid}"
        mr = self.api(base) or {}
        ref = f"{(mr.get('references') or {}).get('full', '').split('!')[0] or projeto}!{iid}"
        estado = {"opened": "aberta", "merged": "mergeada"}.get(mr.get("state"), "fechada")
        norm = {"ref": ref, "titulo": mr.get("title") or "", "url": mr.get("web_url"), "estado": estado,
                "merged_em": iso(mr.get("merged_at")), "sha": mr.get("sha"), "ci": None, "aprovacoes": [],
                "falas": [], "versao_em": None}
        if so_estado:
            return norm
        pipeline = (mr.get("head_pipeline") or {})
        if pipeline.get("sha") == mr.get("sha"):
            norm["ci"] = {"success": "SUCCESS", "failed": "FAILURE"}.get(pipeline.get("status"), "PENDING")
        versoes = self.api(f"{base}/versions") or []
        if versoes:
            norm["versao_em"] = iso(versoes[0].get("created_at"))  # a mais nova vem primeiro
        for a in (self.api(f"{base}/approvals") or {}).get("approved_by") or []:
            u = a.get("user") or {}
            # a API não diz o commit da aprovação: vale se veio depois da última versão (o último push)
            norm["aprovacoes"].append({"autor": u.get("username"), "sha": None, "quando": iso(a.get("approved_at")),
                                       "bot": bool(u.get("bot"))})
        for d in self._todas(f"{base}/discussions?per_page=100"):
            for nota in d.get("notes") or []:
                if nota.get("system") or (nota.get("resolvable") and nota.get("resolved")):
                    continue
                autor = nota.get("author") or {}
                norm["falas"].append({"id": str(nota.get("id")), "autor": autor.get("username"),
                                      "quando": iso(nota.get("created_at")), "tipo": "discussao",
                                      "bot": bool(autor.get("bot"))})
        return norm


def eventos(prs, login, desde):
    """Os eventos de cada PR, cada um com uma chave estável: a mesma novidade nunca avisa duas vezes."""
    out = []
    for pr in prs:
        base = {"ref": pr["ref"], "titulo": pr["titulo"], "url": pr["url"]}
        if pr["estado"] == "mergeada":
            if (pr.get("merged_em") or 0) >= desde:
                out.append(dict(base, chave=f"merge:{pr['ref']}", tipo="merge", texto="mergeada"))
            continue
        if pr["estado"] != "aberta":
            continue
        for f in pr["falas"]:
            if f["autor"] == login or f["bot"] or (f.get("quando") or 0) < desde:
                continue
            out.append(dict(base, chave=f"review:{pr['ref']}:{f['id']}", tipo="review",
                            texto=f"{f['autor']} comentou ({f['tipo']})"))
        for a in pr["aprovacoes"]:
            if a["autor"] == login or a["bot"]:
                continue
            if a["sha"]:  # GitHub: o commit da aprovação
                atual = a["sha"] == pr["sha"]
            elif a.get("quando") is None:  # GitLab sem approved_at: avisa uma vez por sha (a chave leva o sha)
                atual = True
            else:  # GitLab: aprovada depois do último push
                atual = a["quando"] >= (pr.get("versao_em") or 0)
            if atual:
                out.append(dict(base, chave=f"aprovada:{pr['ref']}@{pr['sha']}:{a['autor']}", tipo="aprovada",
                                texto=f"aprovada por {a['autor']}"))
        if pr["ci"] in ("SUCCESS", "FAILURE"):
            out.append(dict(base, chave=f"ci:{pr['ref']}@{pr['sha']}:{pr['ci']}", tipo="ci", ci=pr["ci"],
                            texto="CI verde" if pr["ci"] == "SUCCESS" else "CI quebrou"))
    return out
