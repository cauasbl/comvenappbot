"""Gera o pacote de atualizacao do EV CANTOS: dist-update/evcantos-<versao>.zip + dist-update/update.json.

    python make_update.py --notes "o que mudou" [--url-base https://github.com/USUARIO/REPO/releases/latest/download/]

A versao vem de version.py (fonte unica: edite la antes de gerar). Publique os DOIS arquivos no endereco que os
usuarios configuraram na tela ATUALIZACOES. O pacote leva so o codigo do app e a pasta web/ - nunca config.json,
bot.db, secrets.json ou musicas (os dados de cada pessoa ficam na pasta de dados dela).
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import updater

LAUNCHER_MODULES = {"updater", "desktop"}  # fazem parte do .exe: nunca vao no pacote


def code_files(root: Path) -> tuple[str, ...]:
    """Modulos do app = tudo que o app.py importa (direta ou indiretamente, inclusive imports dentro de funcoes) e
    existe na pasta do projeto, menos o launcher. Descobrir pelos imports evita esquecer um modulo novo (foi o que
    quebrou a 0.4.x: cloud.py ficou de fora e o app novo nao abria)."""
    root, found, todo = Path(root), [], ["app", "version"]
    while todo:
        name = todo.pop()
        f = root / f"{name}.py"
        if name in found or name in LAUNCHER_MODULES or not f.is_file():
            continue
        found.append(name)
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Import):
                todo += [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                todo.append(node.module.split(".")[0])
    return tuple(f"{n}.py" for n in sorted(found))


CODE_FILES = code_files(Path(__file__).parent)


def smoke_test(pkg: Path) -> None:
    """Abre o pacote como o launcher abriria: so com o conteudo do zip + updater.py (que vem do .exe) e importa o app.
    Levanta RuntimeError se faltar modulo ou a importacao falhar."""
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(pkg) as z:
            z.extractall(td)
        shutil.copy(Path(__file__).parent / "updater.py", td)
        code = "import app, version; print(version.VERSION)"
        r = subprocess.run([sys.executable, "-c", code], cwd=td, capture_output=True, text=True, timeout=60,
                           env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
        if r.returncode != 0:
            raise RuntimeError("o pacote nao abre: " + (r.stderr.strip().splitlines() or ["?"])[-1])


def build(root: Path, out: Path, notes: str = "", url_base: str = "") -> tuple[Path, Path]:
    root, out = Path(root), Path(out)
    version = updater.read_version(root / "version.py")
    updater.parse_version(version)  # recusa versao fora do formato 1.2.3
    out.mkdir(parents=True, exist_ok=True)
    pkg = out / f"evcantos-{version}.zip"
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
        for name in code_files(root):
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
    out = Path(a.out)
    with tempfile.TemporaryDirectory() as td:  # gera num lugar temporario e so publica se o pacote abrir
        pkg, mf = build(Path(__file__).parent, Path(td), a.notes, a.url_base)
        try:
            smoke_test(pkg)
        except RuntimeError as e:
            sys.exit(f"PACOTE RECUSADO, nada foi gerado: {e}")
        out.mkdir(parents=True, exist_ok=True)
        pkg, mf = Path(shutil.copy(pkg, out / pkg.name)), Path(shutil.copy(mf, out / mf.name))
    print(f"pacote:    {pkg}  ({pkg.stat().st_size // 1024} KB)")
    print(f"manifesto: {mf}")
    print("Publique os dois arquivos no endereco de atualizacao (ex.: anexos de um release do GitHub).")


if __name__ == "__main__":
    main()
