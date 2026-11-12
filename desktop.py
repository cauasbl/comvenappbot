"""EV CANTOS como aplicativo de Windows: janela propria (WebView2) + icone na barra de tarefas.

Desenvolvimento:  python desktop.py
Empacotado:       EvCantos.exe (veja build.ps1)

Onde ficam os dados (config.json, bot.db, secrets.json com as chaves, cache da API, bot.log, updates/, music/):
  - empacotado:      <raiz>\\data, ao lado da pasta app\\ do programa (sobrevive a atualizacoes)
  - desenvolvimento: a propria pasta do projeto (ou a definida em EVCANTOS_DATA)

Atualizacao: se existir updates/<versao>/ mais novo que a versao embutida (baixado pela tela ATUALIZACOES),
o app e carregado dele; se falhar ao iniciar, a pasta e marcada como ruim e o app volta para a versao anterior.
Este arquivo e o updater.py fazem parte do launcher: mudar eles exige reinstalar o .exe.
"""
from __future__ import annotations

import ctypes
import importlib
import os
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

APP_ID = "EvCantos.App"  # agrupa a janela sob o icone do EV CANTOS na barra de tarefas (nao sob o do Python)
PORT = 8765
FROZEN = getattr(sys, "frozen", False)
BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
APP_MODULES = ("app", "bot", "core", "corners", "oddspapi", "store", "tg", "version")  # atualizaveis por pacote


def data_dir() -> Path:
    env = os.environ.get("EVCANTOS_DATA")
    if env:
        return Path(env)
    if FROZEN:  # <raiz>/app/EvCantos.exe -> <raiz>/data (fora do AppData, que o Windows pode virtualizar/redirecionar)
        return Path(sys.executable).resolve().parent.parent / "data"
    return Path(__file__).parent


def prepare_data(data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    os.chdir(data)  # o resto do programa usa caminhos relativos (config.json, bot.db, papi/, secrets.json)
    if not (data / "config.json").exists():
        shutil.copy(BASE / "config.json", data / "config.json")  # config padrao empacotada


def already_running(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def message(title: str, text: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    else:
        print(f"{title}: {text}")


def start_app(data: Path):
    """Importa e monta o app: da atualizacao mais nova (se houver e funcionar) ou da versao embutida.
    Devolve (modulo app, App montado, servidor HTTP)."""
    import updater  # parte do launcher: nunca vem de pacote de atualizacao
    # da atualizacao mais nova para a mais antiga; None (a versao embutida) e sempre a ultima tentativa
    tries = updater.candidates(data / "updates", updater.read_version(BASE / "version.py")) + [None]
    for code in tries:
        try:
            if code:
                sys.path.insert(0, str(code))
            app = importlib.import_module("app")
            import bot as botmod
            botmod.setup_logging()
            the_app = app.build("config.json", key_required=True)
            server = app.LocalServer(("127.0.0.1", PORT), app.make_handler(the_app))
            return app, the_app, server
        except OSError:
            raise  # porta ocupada etc.: nao e culpa da atualizacao
        except Exception:  # noqa: BLE001 - qualquer falha ao iniciar a atualizacao => volta para a anterior
            if code is None:
                raise
            updater.mark_bad(code)
            sys.path.remove(str(code))
            for m in APP_MODULES:
                sys.modules.pop(m, None)
    raise RuntimeError("nao foi possivel iniciar")  # inalcancavel


def relaunch() -> None:
    """Abre outra copia do app depois que esta fechar (a porta precisa ser liberada primeiro)."""
    cmd = subprocess.list2cmdline([sys.executable] + ([] if FROZEN else [str(Path(__file__).resolve())]))
    subprocess.Popen(f'cmd /c ping -n 3 127.0.0.1 >nul & start "" {cmd}', shell=True,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def main() -> int:
    if sys.platform == "win32" and not FROZEN:  # empacotado, o Windows agrupa pelo proprio .exe (casa com o atalho fixado)
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:  # noqa: BLE001 - so cosmetico (agrupamento do icone)
            pass
    if already_running(PORT):
        message("EV CANTOS", "O EV CANTOS ja esta aberto.")
        return 0
    data = data_dir()
    prepare_data(data)
    if (data / "code").is_dir():  # codigo do app empacotado como arquivos (permite sobrepor por atualizacao)
        sys.path.insert(0, str(data / "code"))
    elif (BASE / "code").is_dir():
        sys.path.insert(0, str(BASE / "code"))
    try:
        app, the_app, server = start_app(data)
    except OSError:
        message("EV CANTOS", "Nao consegui abrir: a porta local esta em uso por outro programa.")
        return 1
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/"

    try:
        import webview
    except ImportError:  # sem pywebview: cai para o navegador, para ainda funcionar
        webbrowser.open(url)
        threading.Event().wait()
        return 0

    window = webview.create_window("EV CANTOS", url, width=1100, height=820, min_size=(760, 560),
                                   background_color="#05020f")
    the_app.on_quit = window.destroy  # menu SAIR fecha a janela

    def restart() -> None:  # tela ATUALIZACOES -> REINICIAR AGORA
        relaunch()
        window.destroy()

    the_app.on_restart = restart
    webview.start()
    the_app.bot.stop.set()
    server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
