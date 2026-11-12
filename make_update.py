"""Gera o pacote de atualizacao do EV CANTOS: dist-update/evcantos-<versao>.zip + dist-update/update.json.

    python make_update.py --notes "o que mudou" [--url-base https://github.com/USUARIO/REPO/releases/latest/download/]

A versao vem de version.py (fonte unica: edite la antes de gerar). Publique os DOIS arquivos no endereco que os
usuarios configuraram na tela ATUALIZACOES. O pacote leva so o codigo do app e a pasta web/ - nunca config.json,
bot.db, secrets.json ou musicas (os dados de cada pessoa ficam na pasta de dados dela).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

import updater

CODE_FILES = ("app.py", "bot.py", "core.py", "corners.py", "oddspapi.py", "store.py", "tg.py", "version.py")


def build(root: Path, out: Path, notes: str = "", url_base: str = "") -> tuple[Path, Path]:
    root, out = Path(root), Path(out)
    version = updater.read_version(root / "version.py")
    updater.parse_version(version)  # recusa versao fora do formato 1.2.3
    out.mkdir(parents=True, exist_ok=True)
    pkg = out / f"evcantos-{version}.zip"
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
        for name in CODE_FILES:
            z.write(root / name, name)
        for p in sorted((root / "web").rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(root).as_posix())
    manifest = {"version": version, "url": f"{url_base}{pkg.name}",
                "sha256": hashlib.sha256(pkg.read_bytes()).hexdigest(), "notes": notes}
    mf = out / "update.json"
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return pkg, mf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", default="dist-update")
    ap.add_argument("--url-base", default="", help="prefixo do endereco do zip (termine com /); vazio = relativo ao update.json")
    a = ap.parse_args()
    pkg, mf = build(Path(__file__).parent, Path(a.out), a.notes, a.url_base)
    print(f"pacote:    {pkg}  ({pkg.stat().st_size // 1024} KB)")
    print(f"manifesto: {mf}")
    print("Publique os dois arquivos no endereco de atualizacao (ex.: anexos de um release do GitHub).")


if __name__ == "__main__":
    main()
