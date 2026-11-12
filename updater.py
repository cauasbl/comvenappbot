"""Atualizacao do EV CANTOS por pacote de codigo.

Fluxo: o usuario aponta um endereco HTTPS (ex.: um release do GitHub) com um 'manifesto' JSON
    {"version": "0.2.0", "url": "evcantos-0.2.0.zip", "sha256": "<64 hex>", "notes": "o que mudou"}
check() le o manifesto; stage() baixa o zip, confere o SHA-256 e extrai em updates/<versao>/.
Na proxima abertura, o launcher (desktop.py) usa a pasta mais nova (usable()). Nada e baixado sem o usuario
clicar, e este modulo nunca executa o codigo baixado.

Este arquivo faz parte do launcher: mudar ele exige reinstalar o .exe (o resto atualiza por pacote).
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

MAX_ZIP = 40 * 1024 * 1024
MAX_UNZIPPED = 150 * 1024 * 1024
REQUIRED = ("app.py", "bot.py", "version.py", "web/index.html")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_LINE = re.compile(r"""^VERSION\s*=\s*["']([^"']+)["']""", re.M)


class UpdateError(RuntimeError):
    pass


def parse_version(s: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(s).strip().lstrip("v").split("."))
    except ValueError:
        raise UpdateError(f"versao invalida: {s!r}") from None


def is_newer(a: str, b: str) -> bool:
    return parse_version(a) > parse_version(b)


def read_version(version_py: Path) -> str:
    """Le VERSION de um version.py sem importa-lo (importar deixaria a versao embutida no cache de modulos)."""
    m = VERSION_LINE.search(Path(version_py).read_text(encoding="utf-8"))
    if not m:
        raise UpdateError(f"sem VERSION em {version_py}")
    return m.group(1)


def _check_url(url: str) -> None:
    u = urllib.parse.urlparse(url)
    if not u.hostname:
        raise UpdateError("endereco invalido")
    local = u.hostname in ("127.0.0.1", "localhost")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise UpdateError("o endereco de atualizacao precisa ser https")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Segue redirecionamentos (releases do GitHub usam), mas nunca rebaixa de https para http."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, limit: int, version: str = "0", timeout: int = 20) -> bytes:
    _check_url(url)
    opener = urllib.request.build_opener(_SafeRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": f"EvCantos/{version}"})
    try:
        with opener.open(req, timeout=timeout) as r:
            data = r.read(limit + 1)
    except UpdateError:
        raise
    except Exception as e:  # noqa: BLE001 - rede/HTTP viram uma mensagem so
        raise UpdateError(f"nao consegui baixar ({type(e).__name__})") from None
    if len(data) > limit:
        raise UpdateError("arquivo grande demais")
    return data


def check(update_url: str, current: str) -> dict:
    """Le o manifesto (sem baixar o pacote). Devolve {available, version, current, notes, url, sha256}."""
    if not update_url:
        raise UpdateError("nenhum endereco de atualizacao configurado")
    try:
        m = json.loads(_get(update_url, 64 * 1024, current))
    except ValueError:
        raise UpdateError("manifesto nao e um JSON valido") from None
    if not isinstance(m, dict):
        raise UpdateError("manifesto invalido")
    for k in ("version", "url", "sha256"):
        if not isinstance(m.get(k), str) or not m[k]:
            raise UpdateError(f"manifesto sem '{k}'")
    sha = m["sha256"].lower()
    if not SHA_RE.match(sha):
        raise UpdateError("sha256 do manifesto invalido")
    parse_version(m["version"])
    return {"available": is_newer(m["version"], current), "version": m["version"], "current": current,
            "notes": str(m.get("notes", ""))[:2000], "url": urllib.parse.urljoin(update_url, m["url"]), "sha256": sha}


def stage(info: dict, root: str | Path = "updates", current: str = "0") -> Path:
    """Baixa o pacote, confere o hash, extrai com seguranca em root/<versao>/ e valida o conteudo."""
    root = Path(root)
    data = _get(info["url"], MAX_ZIP, current, timeout=60)
    if hashlib.sha256(data).hexdigest() != info["sha256"]:
        raise UpdateError("o pacote nao confere com o SHA-256 do manifesto")
    root.mkdir(parents=True, exist_ok=True)
    tmp, dest = root / f".tmp-{info['version']}", root / info["version"]
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            base, total = tmp.resolve(), 0
            for zi in z.infolist():
                target = (tmp / zi.filename).resolve()
                if Path(zi.filename).is_absolute() or (target != base and base not in target.parents):
                    raise UpdateError("pacote com caminho perigoso")
                total += zi.file_size
                if total > MAX_UNZIPPED:
                    raise UpdateError("pacote grande demais depois de extraido")
            z.extractall(tmp)
        missing = [f for f in REQUIRED if not (tmp / f).is_file()]
        if missing:
            raise UpdateError("pacote incompleto: faltam " + ", ".join(missing))
        if read_version(tmp / "version.py") != info["version"]:
            raise UpdateError("a versao dentro do pacote nao bate com o manifesto")
        (tmp / ".ok").write_text(info["sha256"], encoding="utf-8")
        shutil.rmtree(dest, ignore_errors=True)
        tmp.replace(dest)
    except zipfile.BadZipFile:
        raise UpdateError("o pacote nao e um zip valido") from None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    prune(root)
    return dest


def candidates(root: str | Path, builtin: str) -> list[Path]:
    """Atualizacoes completas, nao marcadas como ruins e mais novas que a embutida, da mais nova para a mais antiga.
    O launcher tenta uma por uma: se a mais nova falhar, cai na proxima, e so por ultimo na versao embutida."""
    root, found = Path(root), []
    if root.is_dir():
        for d in root.iterdir():
            if d.name.startswith(".") or not (d / ".ok").is_file() or (d / ".bad").exists():
                continue
            try:
                v = parse_version(d.name)
            except UpdateError:
                continue
            if v > parse_version(builtin):
                found.append((v, d))
    return [d for _, d in sorted(found, reverse=True)]


def usable(root: str | Path, builtin: str) -> Path | None:
    """A atualizacao mais nova utilizavel (ou None)."""
    c = candidates(root, builtin)
    return c[0] if c else None


def mark_bad(folder: Path) -> None:
    """Marca uma atualizacao que falhou ao iniciar, para o launcher voltar a versao anterior."""
    (Path(folder) / ".bad").write_text("falhou ao iniciar", encoding="utf-8")


def prune(root: str | Path, keep: int = 2) -> None:
    """Mantem so as `keep` versoes mais novas (a anterior fica para o caso de voltar atras)."""
    root = Path(root)
    dirs = []
    for d in root.iterdir() if root.is_dir() else []:
        try:
            dirs.append((parse_version(d.name), d))
        except UpdateError:
            continue
    for _, d in sorted(dirs, reverse=True)[keep:]:
        shutil.rmtree(d, ignore_errors=True)
