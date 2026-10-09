"""O que o fluxo precisa do tmux: abrir janela em segundo plano e saber quais panes estão vivos.

No lugar do /proc do agent-tree (que o Mac não tem), a sessão viva é o pane vivo: a janela roda a
ferramenta direto e morre junto com ela. O pane é comparado pelo par (pane_id, pane_pid), porque os
ids %N recomeçam quando o servidor do tmux reinicia.
"""
import subprocess

FORMATO_JANELA = "#{window_id} #{pane_id} #{pane_pid}"


class ErroTmux(Exception):
    pass


def servidor_de_pe(run=subprocess.run):
    try:
        return run(["tmux", "list-sessions"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def panes_vivos(run=subprocess.run):
    """{(pane_id, pane_pid)} dos panes vivos; vazio se não há tmux ou servidor."""
    try:
        r = run(["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{pane_dead}"],
                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return set()
    if r.returncode:
        return set()
    vivos = set()
    for linha in r.stdout.splitlines():
        partes = linha.split()
        if len(partes) == 3 and partes[2] != "1":
            vivos.add((partes[0], partes[1]))
    return vivos


def pane_vivo(pane, pid, vivos):
    return bool(pane) and (pane, str(pid)) in vivos


def argv_janela(nome, pasta, comando, alvo=None):
    # -d: segundo plano, sem roubar o foco; -P/-F: devolve janela, pane e pid para o registro.
    # O comando vai em argumentos separados: o tmux executa direto, sem passar pelo default-shell (com
    # uma string só, um prompt com aspas viraria comando num shell que não segue as regras do sh, ex.: fish)
    return (["tmux", "new-window", "-d", "-P", "-F", FORMATO_JANELA] + (["-t", alvo] if alvo else [])
            + ["-n", nome, "-c", str(pasta), "--", *comando])


def abrir_janela(nome, pasta, comando, alvo=None, run=subprocess.run):
    """Abre e devolve (window_id, pane_id, pane_pid). Levanta ErroTmux se o tmux recusar."""
    r = run(argv_janela(nome, pasta, comando, alvo), capture_output=True, text=True, timeout=10)
    if r.returncode:
        raise ErroTmux(f"o tmux recusou: {(r.stderr or '').strip()}")
    partes = r.stdout.split()
    if len(partes) != 3:
        raise ErroTmux(f"resposta inesperada do tmux: {r.stdout.strip()!r}")
    return tuple(partes)
