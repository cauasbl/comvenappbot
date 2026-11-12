import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import make_update
import updater

ROOT = Path(__file__).parent


def test_package_has_code_and_web_but_never_user_data(tmp_path):
    pkg, mf = make_update.build(ROOT, tmp_path)
    names = set(zipfile.ZipFile(pkg).namelist())
    assert set(make_update.CODE_FILES) <= names
    assert {"web/index.html", "web/static/audio.js", "web/static/bg.js"} <= names
    forbidden = {"config.json", "config.default.json", "bot.db", "secrets.json", "bot.log", "desktop.py", "updater.py"}
    assert not (names & forbidden)  # dados do usuario e o launcher nunca vao no pacote
    assert not any(n.startswith(("music/", "papi/", "updates/", "_pesquisa/")) or "__pycache__" in n for n in names)


def test_manifest_matches_package_and_version_file(tmp_path):
    pkg, mf = make_update.build(ROOT, tmp_path, notes="novidades", url_base="https://exemplo.test/rel/")
    m = json.loads(mf.read_text(encoding="utf-8"))
    assert m["version"] == updater.read_version(ROOT / "version.py") and m["notes"] == "novidades"
    assert m["url"] == f"https://exemplo.test/rel/{pkg.name}"
    assert m["sha256"] == hashlib.sha256(pkg.read_bytes()).hexdigest()


def test_generated_package_installs_through_the_real_updater(tmp_path, monkeypatch):
    """Ponta a ponta: o zip gerado aqui passa por check/stage do updater (o mesmo caminho do app)."""
    pkg, mf = make_update.build(ROOT, tmp_path / "out")
    served = {"manifest": mf.read_bytes(), "zip": pkg.read_bytes()}
    monkeypatch.setattr(updater, "_get", lambda url, limit, version="0", timeout=20:
                        served["manifest"] if url.endswith("update.json") else served["zip"])
    info = updater.check("https://exemplo.test/update.json", "0.0.1")
    assert info["available"] is True
    dest = updater.stage(info, tmp_path / "updates", "0.0.1")
    assert (dest / ".ok").is_file() and (dest / "web" / "index.html").is_file()
    assert updater.usable(tmp_path / "updates", "0.0.1") == dest


def test_invalid_version_is_refused(tmp_path):
    root = tmp_path / "proj"
    (root / "web").mkdir(parents=True)
    for name in make_update.CODE_FILES:
        (root / name).write_text("x = 1\n")
    (root / "version.py").write_text('VERSION = "banana"\n')
    with pytest.raises(updater.UpdateError):
        make_update.build(root, tmp_path / "o")
