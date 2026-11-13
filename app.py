"""EV CANTOS: servidor local + bot + varredura (a tela retro roda no navegador ou na janela do app).

Modo navegador (PowerShell, dentro de ev_bot):
    python app.py          # chaves via variavel de ambiente OU cadastradas na propria tela
Modo aplicativo (janela propria + icone na barra de tarefas): veja desktop.py.
"""
from __future__ import annotations

import argparse
import collections
import hmac
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import bot as botmod
import corners
import updater
import version
from oddspapi import Client, budget_used
from store import Store
from tg import Telegram

log = logging.getLogger("app")
BASE = Path(__file__).resolve().parent  # onde este app.py esta (versao embutida OU pasta de uma atualizacao)
WEB = BASE / "web"
STATIC_DIR = WEB / "static"
LOCAL_HOSTS = ("127.0.0.1", "localhost")
SECRETS_FILE = "secrets.json"
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")

# campo -> (tipo, minimo, maximo)
NUMERIC = {
    "bankroll": (float, 1.0, 1e9),
    "min_ev": (float, 0.0, 1.0),
    "max_ev": (float, 0.01, 5.0),
    "min_odd": (float, 1.01, 100.0),  # faixa de odds da Betano que pode virar alerta
    "max_odd": (float, 1.01, 1000.0),
    "stake_cap_pct": (float, 0.0, 1.0),  # teto de stake (fracao da banca); 0 = sem teto
    "min_kelly_pct": (float, 0.0, 0.2),  # 1/4 Kelly minimo (fracao da banca; 0.005 = 0,5%)
    "window_hours": (float, 1.0, 336.0),
    "max_fixtures": (int, 0, 200),  # 0 = todos os jogos da janela
    "scan_interval_min": (int, 0, 10080),  # 0 = varredura automatica desligada
}
# som e visual (a tela SOM E VISUAL grava isto em config.json -> "ui")
UI_DEFAULTS = {
    "music": True, "music_volume": 0.5, "track": "neon-drive", "sfx": True, "sfx_volume": 0.7,
    "theme": "sunset", "bg": "synthwave", "bg_motion": True, "car": True, "scanlines": True, "vignette": True,
}
THEMES = ("sunset", "tron", "pacman", "vapor")
BGS = ("synthwave", "grid", "none")
BUILTIN_TRACKS = (  # musicas geradas pelo proprio app (ver web/static/audio.js); nenhuma e de terceiros
    {"id": "neon-drive", "name": "NEON DRIVE", "bpm": 108},
    {"id": "midnight-grid", "name": "MIDNIGHT GRID", "bpm": 96},
    {"id": "arcade-sunset", "name": "ARCADE SUNSET", "bpm": 118},
)
TRACK_RE = re.compile(r"^(neon-drive|midnight-grid|arcade-sunset|file:[^/\\]{1,150})$")
MUSIC_MIME = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".wav": "audio/wav", ".m4a": "audio/mp4",
              ".flac": "audio/flac"}
STATIC_MIME = {".js": "text/javascript", ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml"}


def effective_ui(cfg: dict) -> dict:
    return {**UI_DEFAULTS, **(cfg.get("ui") or {})}


def validate_ui(cfg: dict, patch) -> tuple[list[str], dict | None]:
    """Valida um pedaco de configuracao de som/visual. Devolve (erros, ui completo ja mesclado)."""
    if not isinstance(patch, dict):
        return ["ui: use um objeto"], None
    out, errors = effective_ui(cfg), []
    for k, v in patch.items():
        if k not in UI_DEFAULTS:
            errors.append(f"ui.{k}: campo desconhecido")
        elif isinstance(UI_DEFAULTS[k], bool):
            if isinstance(v, bool):
                out[k] = v
            else:
                errors.append(f"ui.{k}: use true ou false")
        elif k.endswith("_volume"):
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1:
                out[k] = float(v)
            else:
                errors.append(f"ui.{k}: use um numero de 0 a 1")
        elif k == "theme" and v in THEMES or k == "bg" and v in BGS or (
                k == "track" and isinstance(v, str) and TRACK_RE.match(v)):
            out[k] = v
        else:
            errors.append(f"ui.{k}: valor invalido")
    return errors, out


def url_ok(u: str) -> bool:
    p = urlparse(u.strip())
    return bool(p.hostname) and (p.scheme == "https" or (p.scheme == "http" and p.hostname in LOCAL_HOSTS))


# ---------- logica pura (testavel sem HTTP) ----------
def apply_config(cfg: dict, patch: dict) -> list[str]:
    """Valida e aplica campos numericos/booleanos e familias. Devolve a lista de erros (vazia = aplicado)."""
    clean, errors = {}, []
    patch = dict(patch)
    fam_patch = patch.pop("families", None)
    ui_patch = patch.pop("ui", None)
    if "update_url" in patch:
        u = patch.pop("update_url")
        if isinstance(u, str) and (not u.strip() or url_ok(u)):
            clean["update_url"] = u.strip()
        else:
            errors.append("update_url: use um endereco https://...")
    for k, v in patch.items():
        if k not in NUMERIC:
            errors.append(f"campo desconhecido: {k}")
            continue
        typ, lo, hi = NUMERIC[k]
        try:
            if isinstance(v, bool):
                raise ValueError
            x = typ(v)
        except (TypeError, ValueError):
            errors.append(f"{k}: valor invalido")
            continue
        if not lo <= x <= hi:
            errors.append(f"{k}: precisa estar entre {lo:g} e {hi:g}")
            continue
        clean[k] = x
    merged = {**cfg, **clean}
    if not errors and merged["min_ev"] >= merged["max_ev"]:
        errors.append("min_ev precisa ser menor que max_ev")
    if not errors and merged.get("min_odd", 1.5) >= merged.get("max_odd", 3.5):
        errors.append("min_odd precisa ser menor que max_odd")
    if fam_patch is not None:
        if (not isinstance(fam_patch, dict) or any(k not in corners.FAMILIES for k in fam_patch)
                or any(not isinstance(v, bool) for v in fam_patch.values())):
            errors.append("families: use {familia: true/false} com familias conhecidas")
        else:
            new_fam = {k: k in corners.enabled_families(cfg) for k in corners.FAMILIES}
            new_fam.update(fam_patch)
            if not any(new_fam.values()):
                errors.append("deixe pelo menos uma familia de mercado ligada")
            else:
                clean["families"] = new_fam
    if ui_patch is not None:
        ui_errors, merged_ui = validate_ui(cfg, ui_patch)
        errors += ui_errors
        if not ui_errors:
            clean["ui"] = merged_ui
    if not errors:
        cfg.update(clean)
    return errors


def _norm(v) -> dict:
    return v if isinstance(v, dict) else {"id": v, "enabled": True}


def league_op(cfg: dict, op: dict) -> str | None:
    """toggle / add / remove em cfg['leagues']. Devolve mensagem de erro ou None."""
    leagues = cfg["leagues"]
    for k in list(leagues):
        leagues[k] = _norm(leagues[k])
    kind, name = op.get("op"), str(op.get("name", "")).strip()
    if kind == "toggle":
        if name not in leagues:
            return "liga inexistente"
        leagues[name]["enabled"] = bool(op["enabled"]) if "enabled" in op else not leagues[name]["enabled"]
    elif kind == "remove":
        if name not in leagues:
            return "liga inexistente"
        del leagues[name]
    elif kind == "add":
        try:
            tid = int(op.get("id"))
        except (TypeError, ValueError):
            return "id invalido"
        if tid <= 0 or not name:
            return "nome e id sao obrigatorios"
        name = name[:80]
        if any(v["id"] == tid for v in leagues.values()):
            return "liga ja cadastrada"
        if name in leagues:
            return "ja existe uma liga com esse nome"
        leagues[name] = {"id": tid, "enabled": True}
    else:
        return "operacao desconhecida"
    return None


def search_leagues(q: str, tournaments: list[dict], existing_ids: set, limit: int = 20) -> list[dict]:
    words = q.strip().lower().split()
    if not words or sum(len(w) for w in words) < 2:
        return []
    out = []
    for t in tournaments:
        text = f"{t.get('tournamentName', '')} {t.get('categoryName', '')}".lower()
        if all(w in text for w in words):
            n = int(t.get("futureFixtures", 0)) + int(t.get("upcomingFixtures", 0)) + int(t.get("liveFixtures", 0))
            out.append({"id": t["tournamentId"], "name": t["tournamentName"], "country": t.get("categoryName", ""),
                        "fixtures": n, "added": t["tournamentId"] in existing_ids})
    out.sort(key=lambda x: (x["fixtures"] == 0, -x["fixtures"], x["name"]))
    return out[:limit]


def has_started(row: dict, now: datetime | None = None) -> bool:
    """True se o jogo do alerta ja comecou (compara em UTC, entao o fuso do computador nao importa)."""
    st = row.get("start_time")
    if not st:
        return False
    try:
        t = datetime.fromisoformat(st.replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t <= (now or datetime.now(timezone.utc))


def present(row: dict, cfg: dict | None = None) -> dict:
    """Linha pronta para a tela. Stake: pendente = banca ATUAL; apostado = a gravada ao apostar."""
    r = dict(row)
    r["pin_odds"] = json.loads(r["pin_odds"]) if r.get("pin_odds") else []
    r["fair_odd"] = round(1 / r["fair_prob"], 3) if r.get("fair_prob") else None
    r["stake_at_alert"] = r.get("stake")
    r["expired"] = r.get("status") == "pending" and has_started(r)
    if r.get("status") == "bet" and r.get("bet_stake") is not None:
        r["stake"] = r["bet_stake"]
    elif cfg is not None and r.get("status") == "pending" and r.get("kelly_pct") is not None:
        r["stake"] = botmod.current_stake(r, cfg)
    return r


# ---------- chaves (ficam num arquivo local do usuario; a API nunca as devolve) ----------
def load_keys(path: str = SECRETS_FILE) -> dict:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_keys(keys: dict, path: str = SECRETS_FILE) -> None:
    tmp = Path(path + ".tmp")
    tmp.write_text(json.dumps(keys), encoding="utf-8")
    tmp.replace(path)


def validate_keys(patch: dict) -> list[str]:
    errors = [f"campo desconhecido: {k}" for k in patch if k not in ("oddspapi_key", "telegram_token")]
    k, t = patch.get("oddspapi_key"), patch.get("telegram_token")
    if k not in (None, "") and not (isinstance(k, str) and KEY_RE.match(k)):
        errors.append("oddspapi_key: formato invalido")
    if t not in (None, "") and not (isinstance(t, str) and TOKEN_RE.match(t)):
        errors.append("telegram_token: formato invalido (123456789:ABC...)")
    return errors


# ---------- aplicacao ----------
class App:
    def __init__(self, bot: botmod.Bot | None, token: str, cache_dir: str = "papi",
                 secrets_path: str = SECRETS_FILE, key_required: bool = False):
        self.bot, self.token, self.cache_dir = bot, token, cache_dir
        self.secrets_path, self.key_required = secrets_path, key_required
        self.logs: collections.deque = collections.deque(maxlen=300)
        self.on_quit = None  # o launcher da janela define: fecha o aplicativo
        self.on_restart = None  # idem: reinicia o aplicativo (aplica a atualizacao baixada)
        self.update_info: dict | None = None
        self.update_staged: str | None = None
        self._tourn: list[dict] | None = None
        self._polling = False

    def log(self, msg: str) -> None:
        self.logs.append({"t": time.strftime("%H:%M:%S"), "m": msg})
        log.info(msg)

    @staticmethod
    def has_key() -> bool:
        return bool(os.environ.get("ODDSPAPI_KEY"))

    def start_polling(self) -> None:
        """Liga o bot do Telegram (opcional) em segundo plano, se houver token."""
        if self._polling or not self.bot.tg:
            return
        self._polling = True
        threading.Thread(target=botmod.run, args=(self.bot,), daemon=True).start()

    def set_keys(self, patch: dict) -> list[str]:
        errs = validate_keys(patch)
        if errs:
            return errs
        keys = load_keys(self.secrets_path)
        if "oddspapi_key" in patch:
            if patch["oddspapi_key"]:
                keys["oddspapi_key"] = patch["oddspapi_key"]
                os.environ["ODDSPAPI_KEY"] = patch["oddspapi_key"]
            else:
                keys.pop("oddspapi_key", None)
                os.environ.pop("ODDSPAPI_KEY", None)
        if "telegram_token" in patch:
            if patch["telegram_token"]:
                keys["telegram_token"] = patch["telegram_token"]
                self.bot.tg = Telegram(patch["telegram_token"])
                self.start_polling()
            else:
                keys.pop("telegram_token", None)
                self.bot.tg = None
        save_keys(keys, self.secrets_path)
        return []

    def tournaments(self) -> list[dict]:
        if self._tourn is None:
            p = Path(self.cache_dir) / "tournaments10.json"
            if p.exists():
                self._tourn = json.loads(p.read_text(encoding="utf-8"))
            else:
                key = os.environ.get("ODDSPAPI_KEY")
                if not key:
                    raise RuntimeError("cadastre a chave do OddsPapi para buscar campeonatos")
                self._tourn = Client(key, self.cache_dir).tournaments(10)
        return self._tourn

    def state(self) -> dict:
        b, cfg = self.bot, self.bot.cfg
        lists, cap = corners.estimate_cost(cfg)
        iv = int(cfg.get("scan_interval_min", 0) or 0)
        nxt = b.next_scan_at
        leagues = [{"name": n, "id": _norm(v)["id"], "enabled": _norm(v).get("enabled", True)}
                   for n, v in cfg["leagues"].items()]
        counts = dict(b.store.counts())
        pend = b.store.list_alerts("pending", limit=5000)
        counts["expired"] = sum(1 for r in pend if has_started(r))
        counts["pending"] = len(pend) - counts["expired"]
        return {
            "config": {k: cfg.get(k) for k in NUMERIC},
            "leagues": leagues,
            "families": [{"key": k, "label": lbl, "enabled": k in corners.enabled_families(cfg)}
                         for k, lbl in corners.FAMILIES.items()],
            "budget": {"used": budget_used(self.cache_dir)},  # so informativo: consultas feitas pelo app, com a chave atual
            "cost": {"lists": lists, "cap": cap},
            "auto": {"interval": iv, "next_in": max(0, int(nxt - time.time())) if iv and nxt else None},
            "telegram": {"enabled": bool(b.tg), "linked": bool(b.chat)},
            "keys": {"oddspapi": self.has_key(), "telegram": bool(b.tg)},
            "scan": {"running": b.lock.locked(), "log": list(self.logs)[-120:], "result": b.last_result},
            "counts": counts,
            "ui": effective_ui(cfg),
            "version": version.VERSION,
            "update_url": cfg.get("update_url", ""),
            "update": {**(self.update_info or {"checked": False}), "staged": self.update_staged},
        }

    def alerts(self, status: str | None, ids: list[int] | None, limit: int = 100) -> list[dict]:
        store = self.bot.store
        if status in ("pending", "expired"):  # pendente = jogo ainda nao comecou; expirado = ja comecou
            rows = [r for r in store.list_alerts("pending", ids, 5000)
                    if has_started(r) == (status == "expired")][:limit]
        else:
            rows = store.list_alerts(status, ids, limit)
        return [present(r, self.bot.cfg) for r in rows]

    def start_scan(self) -> dict:
        """Inicia uma varredura em segundo plano (so quando o usuario pede ou o agendador manda).
        Devolve {started, reason?}. O app NAO recusa varredura por conta propria (nao adivinha o saldo do plano):
        se a cota da OddsPapi acabar, a propria API devolve erro e a varredura mantem o que ja achou."""
        if self.bot.lock.locked():
            return {"started": False, "reason": "running"}
        if self.key_required and not self.has_key():
            return {"started": False, "reason": "nokey"}
        threading.Thread(target=self._scan_thread, daemon=True).start()
        return {"started": True}

    def _scan_thread(self) -> None:
        self.logs.clear()
        self.log("INICIANDO VARREDURA")
        try:
            self.bot.run_scan(self.bot.chat)
        except Exception as e:  # noqa: BLE001 - nada pode derrubar a thread em silencio
            self.log(f"ERRO: {e}")
        else:
            r = self.bot.last_result or {}
            self.log(f"FIM: {r['error']}" if "error" in r else
                     f"FIM: {r.get('sent', 0)} NOVOS, {r.get('dup', 0)} JA ENVIADOS, {r.get('suspicious', 0)} SUSPEITOS")

    # ---- musicas do usuario ----
    @staticmethod
    def music_dir() -> Path:
        d = Path("music")
        d.mkdir(exist_ok=True)
        return d

    def music_files(self) -> list[str]:
        return sorted(p.name for p in self.music_dir().iterdir()
                      if p.is_file() and p.suffix.lower() in MUSIC_MIME)

    # ---- atualizacao ----
    def check_update(self) -> dict:
        """Le o manifesto do endereco configurado (so um GET pequeno; nao gasta consulta da API de odds)."""
        try:
            info = {**updater.check(self.bot.cfg.get("update_url", ""), version.VERSION), "error": None}
        except updater.UpdateError as e:
            info = {"available": False, "error": str(e)}
        self.update_info = {**info, "checked": True, "checked_at": time.time()}
        return self.update_info

    def install_update(self) -> str:
        """Baixa e prepara o pacote verificado. Quem aplica e o launcher, na proxima abertura."""
        info = self.update_info
        if not info or not info.get("available"):
            raise updater.UpdateError("nao ha atualizacao verificada para instalar")
        dest = updater.stage(info, "updates", version.VERSION)
        self.update_staged = info["version"]
        return str(dest)

    def restart(self) -> bool:
        if not self.on_restart:
            return False
        threading.Timer(0.3, self.on_restart).start()
        return True

    def save(self) -> None:
        self.bot.save_cfg()


class LocalServer(ThreadingHTTPServer):
    """No Windows, SO_REUSEADDR deixa duas copias escutarem a mesma porta (e a antiga responde no lugar da nova).
    Desligado: a segunda copia falha ao abrir, o que o launcher usa para avisar 'ja esta aberto'."""
    allow_reuse_address = False


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EvCantos"

        def log_message(self, *a):  # silencia o log padrao
            pass

        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            text = ctype.startswith(("text/", "application/json"))
            self.send_header("Content-Type", f"{ctype}; charset=utf-8" if text else ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "").split(":")[0] in LOCAL_HOSTS

        def _file(self, base: Path, rel: str, mimes: dict) -> None:
            """Serve um arquivo de dentro de `base` (so extensoes permitidas; sem escapar da pasta)."""
            try:
                base = base.resolve()
                p = (base / rel).resolve()
                ok = base in p.parents and p.suffix.lower() in mimes and p.is_file()
            except (OSError, ValueError):
                ok = False
            if not ok:
                return self._send(404, {"error": "nao encontrado"})
            self._send(200, p.read_bytes(), mimes[p.suffix.lower()])

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "host nao permitido"})
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    page = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                    return self._send(200, page.encode(), "text/html")
                if u.path.startswith("/static/"):
                    return self._file(STATIC_DIR, unquote(u.path[len("/static/"):]), STATIC_MIME)
                if u.path.startswith("/music/"):
                    return self._file(app.music_dir(), unquote(u.path[len("/music/"):]), MUSIC_MIME)
                if u.path == "/api/music":
                    return self._send(200, {"builtin": list(BUILTIN_TRACKS), "files": app.music_files(),
                                            "dir": str(app.music_dir().resolve())})
                if u.path == "/api/state":
                    return self._send(200, app.state())
                if u.path == "/api/alerts":
                    status = q.get("status", [None])[0]
                    status = None if status in (None, "", "all") else status
                    ids = [int(x) for x in q.get("ids", [""])[0].split(",") if x.strip().isdigit()] or None
                    return self._send(200, {"alerts": app.alerts(status, ids, int(q.get("limit", ["100"])[0]))})
                if u.path == "/api/leagues/search":
                    existing = {_norm(v)["id"] for v in app.bot.cfg["leagues"].values()}
                    return self._send(200, {"results": search_leagues(q.get("q", [""])[0], app.tournaments(),
                                                                    existing)})
                return self._send(404, {"error": "nao encontrado"})
            except Exception as e:  # noqa: BLE001
                log.exception("erro em GET %s", u.path)
                return self._send(500, {"error": str(e)})

        def do_POST(self):
            # le o corpo ANTES de qualquer resposta: responder (ex.: 403) sem le-lo derruba a conexao no Windows
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > 65536:
                self.close_connection = True
                return self._send(413, {"error": "corpo invalido ou grande demais"})
            raw = self.rfile.read(n)
            if not self._host_ok() or not hmac.compare_digest(self.headers.get("X-Token") or "", app.token):
                return self._send(403, {"error": "acesso negado"})
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in LOCAL_HOSTS:
                return self._send(403, {"error": "origem nao permitida"})
            try:
                body = json.loads(raw or b"{}")
            except (ValueError, TypeError):
                return self._send(400, {"error": "JSON invalido"})
            path, cfg = urlparse(self.path).path, app.bot.cfg
            try:
                if path == "/api/config":
                    errs = apply_config(cfg, body)
                    if errs:
                        return self._send(400, {"errors": errs})
                    app.save()
                    return self._send(200, app.state())
                if path == "/api/league":
                    err = league_op(cfg, body)
                    if err:
                        return self._send(400, {"errors": [err]})
                    app.save()
                    return self._send(200, app.state())
                if path == "/api/keys":
                    errs = app.set_keys(body)
                    if errs:
                        return self._send(400, {"errors": errs})
                    return self._send(200, app.state())
                if path == "/api/scan":
                    return self._send(200, app.start_scan())
                if path == "/api/music/open":
                    opener = getattr(os, "startfile", None)  # so existe no Windows
                    if opener:
                        opener(str(app.music_dir().resolve()))
                    return self._send(200, {"opened": bool(opener)})
                if path == "/api/update/check":
                    app.check_update()
                    return self._send(200, app.state())
                if path == "/api/update/install":
                    try:
                        app.install_update()
                    except updater.UpdateError as e:
                        return self._send(400, {"errors": [str(e)]})
                    return self._send(200, app.state())
                if path == "/api/update/restart":
                    return self._send(200, {"restarting": app.restart()})
                if path == "/api/quit":
                    if app.on_quit:
                        threading.Timer(0.3, app.on_quit).start()
                    return self._send(200, {"closing": bool(app.on_quit)})
                if path == "/api/alerts/decide":
                    status = body.get("status")
                    if status not in ("bet", "ignored") or not isinstance(body.get("id"), int):
                        return self._send(400, {"errors": ["use id (numero) e status 'bet' ou 'ignored'"]})
                    row = app.bot.decide_alert(body["id"], status)
                    if row is None:
                        return self._send(409, {"errors": ["alerta inexistente ou ja decidido"]})
                    return self._send(200, {"alert": present(row)})
                return self._send(404, {"error": "nao encontrado"})
            except Exception as e:  # noqa: BLE001
                log.exception("erro em POST %s", path)
                return self._send(500, {"error": str(e)})

    return Handler


def build(config_path: str = "config.json", key_required: bool = False) -> App:
    """Monta bot + app (sem abrir porta). Chaves: variavel de ambiente tem prioridade; senao secrets.json."""
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))  # -sig: aceita o BOM que o Bloco de Notas/PowerShell colocam
    keys = load_keys()
    if keys.get("oddspapi_key") and not os.environ.get("ODDSPAPI_KEY"):
        os.environ["ODDSPAPI_KEY"] = keys["oddspapi_key"]
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or keys.get("telegram_token")
    the_app = App(None, secrets.token_urlsafe(24), key_required=key_required)
    bot = botmod.Bot(Telegram(token) if token else None, Store("bot.db"), cfg,
                     scanner=lambda c: corners.run_live(c, progress=the_app.log), cfg_path=config_path)
    the_app.bot = bot
    threading.Thread(target=botmod.scheduler, args=(bot,), daemon=True).start()
    the_app.start_polling()
    if cfg.get("update_url"):  # verificacao silenciosa ao abrir (so le o manifesto; nada e baixado)
        threading.Thread(target=the_app.check_update, daemon=True).start()
    return the_app


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    botmod.setup_logging()
    the_app = build(args.config, key_required=True)
    try:
        server = LocalServer(("127.0.0.1", args.port), make_handler(the_app))
    except OSError:
        sys.exit(f"A porta {args.port} ja esta em uso: o EV CANTOS provavelmente ja esta aberto.")
    url = f"http://127.0.0.1:{args.port}/"
    print(f"EV CANTOS no ar: {url}  (Ctrl+C para sair)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        the_app.bot.stop.set()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
