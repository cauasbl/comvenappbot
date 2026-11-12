import json
import sys
from pathlib import Path

import pytest

import desktop
from test_app import base_cfg


@pytest.fixture
def data(tmp_path, monkeypatch):
    """Pasta de dados isolada + modulos do app limpos (o launcher importa o app do zero)."""
    d = tmp_path / "data"
    d.mkdir()
    (d / "config.json").write_text(json.dumps(base_cfg()))
    monkeypatch.chdir(d)
    monkeypatch.setattr(desktop, "PORT", 0)  # porta qualquer: nao briga com o app real
    saved = {m: sys.modules.pop(m) for m in desktop.APP_MODULES if m in sys.modules}
    path_before = list(sys.path)
    yield d
    for m in desktop.APP_MODULES:
        sys.modules.pop(m, None)
    sys.modules.update(saved)
    sys.path[:] = path_before


def add_update(data, version, files):
    d = data / "updates" / version
    d.mkdir(parents=True)
    (d / ".ok").write_text("x")
    for name, text in files.items():
        (d / name).write_text(text)
    return d


def test_broken_update_is_marked_bad_and_builtin_version_starts(data):
    upd = add_update(data, "9.9.9", {"app.py": "raise RuntimeError('pacote quebrado')\n"})
    app, the_app, server = desktop.start_app(data)
    try:
        assert (upd / ".bad").exists()  # nao tenta de novo na proxima abertura
        assert str(upd) not in sys.path
        assert Path(app.__file__).resolve().parent == Path(desktop.BASE).resolve()  # voltou para a embutida
    finally:
        server.server_close()
        the_app.bot.stop.set()


def test_valid_newer_update_is_used_instead_of_builtin(data):
    stub = (
        "class LocalServer:\n"
        "    def __init__(self, addr, handler): self.addr = addr\n"
        "def make_handler(a): return None\n"
        "def build(path, key_required=False): return 'app-da-atualizacao'\n"
    )
    upd = add_update(data, "9.9.9", {"app.py": stub})
    app, the_app, server = desktop.start_app(data)
    assert Path(app.__file__).resolve().parent == upd.resolve()
    assert the_app == "app-da-atualizacao" and not (upd / ".bad").exists()


def test_broken_newest_update_falls_back_to_previous_good_update_not_builtin(data):
    stub = (
        "class LocalServer:\n"
        "    def __init__(self, addr, handler): pass\n"
        "def make_handler(a): return None\n"
        "def build(path, key_required=False): return 'versao-9.9.8'\n"
    )
    good = add_update(data, "9.9.8", {"app.py": stub})
    bad = add_update(data, "9.9.9", {"app.py": "raise RuntimeError('quebrada')\n"})
    app, the_app, server = desktop.start_app(data)
    assert Path(app.__file__).resolve().parent == good.resolve() and the_app == "versao-9.9.8"
    assert (bad / ".bad").exists() and not (good / ".bad").exists()


def test_update_not_newer_than_builtin_is_ignored(data):
    add_update(data, "0.0.1", {"app.py": "raise RuntimeError('nao deveria carregar')\n"})
    app, the_app, server = desktop.start_app(data)
    try:
        assert Path(app.__file__).resolve().parent == Path(desktop.BASE).resolve()
    finally:
        server.server_close()
        the_app.bot.stop.set()


def test_prepare_data_copies_default_config_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "novo"
    desktop.prepare_data(target)
    assert (target / "config.json").is_file() and Path.cwd().resolve() == target.resolve()
    (target / "config.json").write_text('{"meu": "ajuste"}')
    desktop.prepare_data(target)
    assert json.loads((target / "config.json").read_text()) == {"meu": "ajuste"}  # nao sobrescreve o do usuario


def test_data_dir_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("EVCANTOS_DATA", str(tmp_path / "x"))
    assert desktop.data_dir() == tmp_path / "x"
    monkeypatch.delenv("EVCANTOS_DATA")
    monkeypatch.setattr(desktop, "FROZEN", True)
    exe = tmp_path / "EvCantos" / "app" / "EvCantos.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert desktop.data_dir() == (tmp_path / "EvCantos" / "data").resolve()  # empacotado: data/ ao lado de app/
    monkeypatch.setattr(desktop, "FROZEN", False)
    assert desktop.data_dir() == Path(desktop.__file__).parent  # desenvolvimento: a propria pasta
