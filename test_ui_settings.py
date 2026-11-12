import http.client
import json
import urllib.parse

import pytest

import app
from test_appmode import srv2  # noqa: F401  (fixture do pytest)
from test_app import call


def raw_get(port, path):
    """GET sem normalizar o caminho (urllib esconderia tentativas de '..')."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.getheader("Content-Type"), body


# ---- configuracao de som e visual ----
def test_ui_defaults_and_patch_persist(srv2):  # noqa: F811
    a, bot, port, _, tmp = srv2
    ui = call(port, "/api/state")[1]["ui"]
    assert ui == app.UI_DEFAULTS and ui["theme"] == "sunset" and ui["music"] is True
    code, s = call(port, "/api/config", {"ui": {"theme": "tron", "music_volume": 0.25, "sfx": False}})
    assert code == 200 and s["ui"]["theme"] == "tron" and s["ui"]["music_volume"] == 0.25 and s["ui"]["sfx"] is False
    assert s["ui"]["bg"] == "synthwave"  # o resto continua igual
    saved = json.loads((tmp / "config.json").read_text())["ui"]
    assert saved["theme"] == "tron" and saved["sfx_volume"] == 0.7


@pytest.mark.parametrize("patch", [
    {"theme": "nao-existe"}, {"bg": "xyz"}, {"music_volume": 1.5}, {"sfx_volume": -0.1}, {"music_volume": "alto"},
    {"music": "sim"}, {"track": "../../etc/passwd"}, {"track": "file:"}, {"campo": 1}, {"scanlines": 1},
])
def test_ui_rejects_invalid_values(srv2, patch):  # noqa: F811
    a, bot, port, *_ = srv2
    code, err = call(port, "/api/config", {"ui": patch})
    assert code == 400 and err["errors"]
    assert call(port, "/api/state")[1]["ui"] == app.UI_DEFAULTS  # nada foi aplicado


def test_ui_accepts_user_music_track_and_nonobject_is_rejected(srv2):  # noqa: F811
    a, bot, port, *_ = srv2
    assert call(port, "/api/config", {"ui": {"track": "file:Minha Música.mp3"}})[1]["ui"]["track"] == "file:Minha Música.mp3"
    assert call(port, "/api/config", {"ui": {"track": "midnight-grid"}})[0] == 200
    assert call(port, "/api/config", {"ui": "tron"})[0] == 400


# ---- arquivos estaticos (so de web/static, sem escapar) ----
def test_static_files_served_with_right_type(srv2):  # noqa: F811
    a, bot, port, *_ = srv2
    for name, ctype in (("audio.js", "text/javascript"), ("bg.js", "text/javascript")):
        status, ct, body = raw_get(port, f"/static/{name}")
        assert status == 200 and ct.startswith(ctype) and len(body) > 200


@pytest.mark.parametrize("path", [
    "/static/../app.py", "/static/..%2fapp.py", "/static/%2e%2e/app.py", "/static/..%5capp.py",
    "/static/audio.py", "/static/nao-existe.js", "/static/", "/static/%00.js",
])
def test_static_blocks_traversal_and_other_files(srv2, path):  # noqa: F811
    a, bot, port, *_ = srv2
    status, _, body = raw_get(port, path)
    assert status == 404 and b"def " not in body


# ---- musicas do usuario ----
def test_music_folder_listing_and_serving(srv2, monkeypatch):  # noqa: F811
    a, bot, port, _, tmp = srv2
    monkeypatch.chdir(tmp)
    d = tmp / "music"
    d.mkdir()
    (d / "Música Teste.mp3").write_bytes(b"ID3fake-audio-bytes")
    (d / "faixa.ogg").write_bytes(b"OggS")
    (d / "notas.txt").write_text("nao e musica")
    (tmp / "segredo.mp3").write_bytes(b"fora da pasta de musicas")
    code, m = call(port, "/api/music")
    assert code == 200 and m["files"] == ["Música Teste.mp3", "faixa.ogg"]  # .txt nao entra
    assert [t["id"] for t in m["builtin"]] == ["neon-drive", "midnight-grid", "arcade-sunset"]
    assert m["dir"].endswith("music")
    status, ct, body = raw_get(port, "/music/" + urllib.parse.quote("Música Teste.mp3"))
    assert status == 200 and ct == "audio/mpeg" and body == b"ID3fake-audio-bytes"
    for bad in ("/music/notas.txt", "/music/..%2fsegredo.mp3", "/music/..%5csegredo.mp3", "/music/nao-existe.mp3"):
        assert raw_get(port, bad)[0] == 404


def test_open_music_folder_endpoint(srv2, monkeypatch):  # noqa: F811
    a, bot, port, _, tmp = srv2
    monkeypatch.chdir(tmp)
    opened = []
    monkeypatch.setattr(app.os, "startfile", opened.append, raising=False)
    assert call(port, "/api/music/open", {})[1] == {"opened": True}
    assert opened and opened[0].endswith("music")
    assert call(port, "/api/music/open", {}, token="errado")[0] == 403
