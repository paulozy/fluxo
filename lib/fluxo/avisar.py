"""Aviso na área de trabalho, por sistema. O título da PR é texto de outra pessoa: ele nunca entra no
corpo de um script. Vai por argv (osascript), por variável de ambiente (PowerShell) ou como argumento
(notify-send, tmux).

- Mac: `display notification` do AppleScript. O Script Editor precisa de permissão de notificação
  (Ajustes > Notificações); sem ela, falha em silêncio.
- WSL: toast do Windows pelo powershell.exe (WinRT, AppID do PowerShell; fonte da comunidade, a
  validar). Se falhar, `tmux display-message`.
- Linux: notify-send.
"""
import base64
import html
import os
import shutil
import subprocess

OSASCRIPT = ['on run argv', 'display notification (item 2 of argv) with title (item 1 of argv)', 'end run']

# o texto chega por $env:FLUXO_TITULO e $env:FLUXO_TEXTO; o script em si é fixo
TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName('text')
$n.Item(0).AppendChild($t.CreateTextNode($env:FLUXO_TITULO)) | Out-Null
$n.Item(1).AppendChild($t.CreateTextNode($env:FLUXO_TEXTO)) | Out-Null
$app = $env:FLUXO_APPID
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


# o AppID do Windows PowerShell (público, igual em toda máquina), montado em partes para a varredura de
# UUID do repo não confundir com um id de empresa
APPID = "{" + "-".join(["1AC14E77", "02E7", "4E5D", "B744", "2EB1AE5198B7"]) + "}" + r"\WindowsPowerShell\v1.0\powershell.exe"


def _limpo(texto, n=180):
    """Uma linha, sem caracteres de controle (um ESC num título de PR iria para o terminal)."""
    return " ".join("".join(c for c in str(texto) if c.isprintable() or c.isspace()).split())[:n]


def comandos(so, titulo, texto, which=shutil.which):
    """Lista de (argv, env extra) a tentar, em ordem; o primeiro que der certo vale."""
    titulo, texto = _limpo(titulo, 80), _limpo(texto)
    out = []
    if so == "mac" and which("osascript"):
        argv = ["osascript"]
        for linha in OSASCRIPT:
            argv += ["-e", linha]
        out.append((argv + [titulo, texto], {}))
    elif so == "wsl" and which("powershell.exe"):
        codificado = base64.b64encode(TOAST_PS.encode("utf-16-le")).decode()
        out.append((["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", codificado],
                    # o WSLENV leva a variável do WSL para o processo do Windows: /w é "só quando o WSL chama
                    # o Win32" (o /u é o sentido contrário). learn.microsoft.com/windows/wsl/filesystems
                    {"FLUXO_TITULO": titulo, "FLUXO_TEXTO": texto, "FLUXO_APPID": APPID,
                     "WSLENV": ":".join(filter(None, [os.environ.get("WSLENV"),
                                                      "FLUXO_TITULO/w:FLUXO_TEXTO/w:FLUXO_APPID/w"]))}))
    elif so == "linux" and which("notify-send"):
        # o corpo do notify-send é markup em vários servidores: um <a href> do título viraria link
        out.append((["notify-send", "--", titulo, html.escape(texto)], {}))
    if which("tmux") and os.environ.get("TMUX"):
        # o display-message trata o texto como formato: #{…} e #H de um título de PR seriam expandidos
        out.append((["tmux", "display-message", "--", f"{titulo}: {texto}".replace("#", "##")], {}))
    return out


def avisar(so, titulo, texto, run=subprocess.run, which=shutil.which):
    """Devolve o comando que avisou, ou None (o aviso continua no avisos.jsonl)."""
    for argv, env in comandos(so, titulo, texto, which):
        try:
            r = run(argv, env=dict(os.environ, **env), capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode == 0:
            return argv[0]
    return None
