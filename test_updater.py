import hashlib
import io
import json
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

import updater
import version
from updater import UpdateError
from test_appmode import srv2  # noqa: F401  (fixture do pytest)
from test_app import call


def make_zip(version="0.2.0", extra=None, omit=()):
    files = {"app.py": "# app", "bot.py": "# bot", "web/index.html": "<html>", "version.py": f'VERSION = "{version}"\n'}
    files.update(extra or {})
    for o in omit:
        files.pop(o)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


@pytest.fixture
def host(tmp_path):
    """Servidor HTTP local fingindo ser o endereco de atualizacao."""
    srv_dir = tmp_path / "srv"
    srv_dir.mkdir()

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(srv_dir)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def publish(version="0.2.0", zbytes=None, sha=None, notes="novidades", url=None):
        z = zbytes if zbytes is not None else make_zip(version)
        (srv_dir / f"evcantos-{version}.zip").write_bytes(z)
        m = {"version": version, "url": url or f"evcantos-{version}.zip",
             "sha256": sha or hashlib.sha256(z).hexdigest(), "notes": notes}
        (srv_dir / "update.json").write_text(json.dumps(m))
        return f"{base}/update.json"

    yield publish, tmp_path / "updates"
    server.shutdown()
    server.server_close()


# ---- versoes ----
def test_version_comparison_is_numeric_not_textual():
    assert updater.is_newer("0.10.0", "0.9.0") and updater.is_newer("v1.0", "0.9.9")
    assert not updater.is_newer("0.1.0", "0.1.0") and not updater.is_newer("0.1.0", "0.2.0")
    with pytest.raises(UpdateError):
        updater.parse_version("abc")


def test_read_version_without_importing(tmp_path):
    p = tmp_path / "version.py"
    p.write_text('VERSION = "3.4.5"\n')
    assert updater.read_version(p) == "3.4.5"
    p.write_text("x = 1\n")
    with pytest.raises(UpdateError):
        updater.read_version(p)


# ---- manifesto ----
def test_check_reports_available_and_resolves_relative_url(host):
    publish, _ = host
    info = updater.check(publish("0.2.0", notes="ficou mais rapido"), "0.1.0")
    assert info["available"] is True and info["version"] == "0.2.0" and info["notes"] == "ficou mais rapido"
    assert info["url"].endswith("/evcantos-0.2.0.zip") and info["url"].startswith("http://127.0.0.1:")
    assert updater.check(publish("0.2.0"), "0.2.0")["available"] is False  # ja esta na versao


def test_check_rejects_bad_manifests_and_insecure_urls(host, tmp_path):
    publish, _ = host
    with pytest.raises(UpdateError, match="https"):
        updater.check("http://example.com/update.json", "0.1.0")  # http fora do computador local
    with pytest.raises(UpdateError, match="configurado"):
        updater.check("", "0.1.0")
    with pytest.raises(UpdateError, match="sha256"):
        updater.check(publish("0.2.0", sha="nao-e-hash"), "0.1.0")


# ---- instalar ----
def test_stage_installs_verified_package(host):
    publish, root = host
    info = updater.check(publish("0.2.0"), "0.1.0")
    dest = updater.stage(info, root, "0.1.0")
    assert dest == root / "0.2.0" and (dest / ".ok").is_file() and (dest / "web" / "index.html").is_file()
    assert updater.usable(root, "0.1.0") == dest


def test_stage_refuses_tampered_package(host):
    publish, root = host
    info = updater.check(publish("0.2.0"), "0.1.0")
    info["sha256"] = "0" * 64
    with pytest.raises(UpdateError, match="SHA-256"):
        updater.stage(info, root, "0.1.0")
    assert not (root / "0.2.0").exists()


@pytest.mark.parametrize("zbytes,msg", [
    (make_zip(extra={"../evil.txt": "x"}), "perigoso"),
    (make_zip(omit=("web/index.html",)), "incompleto"),
    (make_zip(version="9.9.9"), "nao bate"),
    (b"isto nao e um zip", "zip"),
])
def test_stage_rejects_unsafe_or_broken_packages(host, zbytes, msg):
    publish, root = host
    info = updater.check(publish("0.2.0", zbytes=zbytes), "0.1.0")
    with pytest.raises(UpdateError, match=msg):
        updater.stage(info, root, "0.1.0")
    assert not (root / "0.2.0").exists() and not list(root.glob(".tmp-*"))  # nada fica pela metade


# ---- escolha da versao na abertura ----
def test_usable_picks_newest_ignores_bad_and_incomplete(tmp_path):
    root = tmp_path / "updates"
    for v, ok in (("0.2.0", True), ("0.3.0", True), ("0.4.0", False), ("0.0.9", True)):
        (root / v).mkdir(parents=True)
        if ok:
            (root / v / ".ok").write_text("x")
    assert updater.usable(root, "0.1.0") == root / "0.3.0"  # 0.4.0 esta incompleta; 0.0.9 e mais velha
    updater.mark_bad(root / "0.3.0")  # falhou ao iniciar: volta para a anterior
    assert updater.usable(root, "0.1.0") == root / "0.2.0"
    assert updater.usable(root, "0.2.0") is None  # nada mais novo que a embutida
    assert updater.usable(tmp_path / "nao-existe", "0.1.0") is None


def test_candidates_are_ordered_newest_first_and_skip_bad(tmp_path):
    root = tmp_path / "updates"
    for v in ("0.2.0", "0.10.0", "0.3.0", "0.1.0"):
        (root / v).mkdir(parents=True)
        (root / v / ".ok").write_text("x")
    updater.mark_bad(root / "0.3.0")
    assert [d.name for d in updater.candidates(root, "0.1.0")] == ["0.10.0", "0.2.0"]  # 0.3.0 ruim; 0.1.0 nao e mais nova
    assert updater.candidates(tmp_path / "nao-existe", "0.1.0") == []


def test_prune_keeps_two_newest(host):
    publish, root = host
    for v in ("0.2.0", "0.3.0", "0.4.0"):
        updater.stage(updater.check(publish(v), "0.1.0"), root, "0.1.0")
    assert sorted(p.name for p in root.iterdir()) == ["0.3.0", "0.4.0"]


# ---- endpoints do app ----
def test_update_endpoints_end_to_end(srv2, host, monkeypatch):  # noqa: F811
    a, bot, port, _, tmp = srv2
    publish, _root = host
    monkeypatch.chdir(tmp)  # o app grava em ./updates (relativo)
    url = publish("0.2.0", notes="musicas novas")
    assert call(port, "/api/config", {"update_url": "http://example.com/u.json"})[0] == 400
    assert call(port, "/api/update/install", {})[0] == 400  # nada verificado ainda
    assert call(port, "/api/config", {"update_url": url})[1]["update_url"] == url
    code, s = call(port, "/api/update/check", {})
    assert code == 200 and s["update"]["available"] is True and s["update"]["notes"] == "musicas novas"
    assert s["version"] == version.VERSION and s["update"]["staged"] is None
    code, s = call(port, "/api/update/install", {})
    assert code == 200 and s["update"]["staged"] == "0.2.0" and (tmp / "updates" / "0.2.0" / ".ok").is_file()
    assert call(port, "/api/update/restart", {})[1] == {"restarting": False}  # sem launcher (modo navegador)
    called = threading.Event()
    a.on_restart = called.set
    assert call(port, "/api/update/restart", {})[1] == {"restarting": True} and called.wait(2)


def test_update_check_error_is_reported_not_raised(srv2):  # noqa: F811
    a, bot, port, *_ = srv2
    code, s = call(port, "/api/update/check", {})  # sem endereco configurado
    assert code == 200 and s["update"]["available"] is False and "configurado" in s["update"]["error"]
