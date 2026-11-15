"""Sincronizacao com o banco na nuvem (Supabase, via REST/PostgREST). So biblioteca padrao.

O banco local (SQLite) continua sendo a fonte do app: tudo funciona sem internet. A nuvem serve para duas pessoas
em maquinas diferentes compartilharem:
  - sinais (alerts)            -> compartilhados: o sinal achado por um aparece para o outro
  - scans e jogos vistos       -> compartilhados: um jogo ja visto por um e pulado pelo outro (poupa consultas)
  - decisoes (apostei/ignorei) -> pessoais: cada um com as suas (tabela decisions, por usuario)

A URL e a chave ficam so no secrets.json local, nunca no GitHub nem devolvidas pela API do app.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?/?$")
LOCAL_RE = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d+)?/?$")
USER_RE = re.compile(r"^[A-Za-z0-9_-]{2,24}$")
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{20,500}$")
BATCH = 200
PAGE = 1000

ALERT_COLS = ("key", "created_at", "found_by", "fixture_id", "start_time", "fixture", "market_id", "market",
              "outcome_id", "outcome", "pin_odds", "fair_prob", "betano_odd", "ev", "kelly_pct", "venue", "ask",
              "fee", "limit_usd")
SCAN_COLS = ("uid", "user_name", "started_at", "finished_at", "trigger", "params", "listed", "skipped", "scanned",
             "signals", "blocked", "queries", "status", "error")
GAME_COLS = ("scan_uid", "fixture_id", "user_name", "fixture", "league", "start_time", "compared", "signals",
             "best_ev", "note", "seen_at")


class CloudError(RuntimeError):
    pass


def normalize_url(u) -> str:
    """Aceita a URL como o painel do Supabase a mostra: tira espacos, barra final e o sufixo /rest/v1."""
    u = (u or "").strip().rstrip("/") if isinstance(u, str) else ""
    if u.endswith("/rest/v1"):
        u = u[:-len("/rest/v1")]
    return u.rstrip("/")


def url_ok(u: str) -> bool:
    return isinstance(u, str) and bool(URL_RE.match(u) or LOCAL_RE.match(u))


def _http(method: str, url: str, headers: dict, body: bytes | None = None, timeout: float = 15.0):
    """Unico ponto que toca a rede (os testes trocam isto por um banco falso)."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise CloudError(f"sem conexao com a nuvem ({getattr(e, 'reason', e)})") from e


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Cloud:
    def __init__(self, url: str, key: str, user: str):
        url, key = normalize_url(url), (key or "").strip()
        if not url_ok(url):
            raise CloudError("URL invalida: use https://SEU-PROJETO.supabase.co")
        if not KEY_RE.match(key or ""):
            raise CloudError("chave invalida")
        if not USER_RE.match(user or ""):
            raise CloudError("nome de usuario invalido (2 a 24 letras, numeros, _ ou -)")
        self.base = url.rstrip("/") + "/rest/v1"
        self.key, self.user = key, user

    # ---- baixo nivel ----
    def _headers(self, prefer: str | None = None) -> dict:
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        if prefer:
            h["Prefer"] = prefer
        return h

    def _call(self, method: str, table: str, params: dict | None = None, body=None, prefer: str | None = None):
        qs = ("?" + urllib.parse.urlencode(params, safe=".,:()*")) if params else ""
        status, raw = _http(method, f"{self.base}/{table}{qs}", self._headers(prefer),
                            json.dumps(body).encode() if body is not None else None)
        if status >= 300:
            try:
                msg = json.loads(raw).get("message") or raw.decode("utf-8", "replace")
            except (ValueError, AttributeError):
                msg = raw.decode("utf-8", "replace")
            hint = " (rode o supabase_setup.sql no SQL Editor do projeto)" if status == 404 else ""
            raise CloudError(f"{table}: HTTP {status} {msg[:160]}{hint}")
        return json.loads(raw) if raw else []

    def upsert(self, table: str, rows: list[dict], conflict: str) -> None:
        for i in range(0, len(rows), BATCH):
            self._call("POST", table, {"on_conflict": conflict}, rows[i:i + BATCH],
                       "resolution=merge-duplicates,return=minimal")

    def select(self, table: str, params: dict) -> list[dict]:
        out: list[dict] = []
        while True:
            page = self._call("GET", table, {**params, "limit": PAGE, "offset": len(out)})
            out += page
            if len(page) < PAGE:
                return out

    # ---- verificacao ----
    def check(self) -> dict:
        """Confirma URL, chave e se as 4 tabelas existem. Levanta CloudError com a causa."""
        for t in ("alerts", "scans", "scan_games", "decisions"):
            self._call("GET", t, {"select": "*", "limit": 1})
        return {"ok": True}

    # ---- envio ----
    def push(self, store) -> dict:
        n = {"alerts": 0, "decisions": 0, "scans": 0, "games": 0}
        alerts = store.dirty_alerts()
        shared, decisions = [], []
        for a in alerts:
            mine = a.get("found_by") in (None, "", self.user)
            if mine:
                shared.append({**{c: a.get(c) for c in ALERT_COLS}, "found_by": self.user})
            if a["status"] in ("bet", "ignored"):
                decisions.append({"user_name": self.user, "alert_key": a["key"], "status": a["status"],
                                  "bet_odd": a.get("bet_odd"), "bet_stake": a.get("bet_stake"),
                                  "decided_at": a.get("decided_at")})
        if shared:
            self.upsert("alerts", shared, "key")
        if decisions:
            self.upsert("decisions", decisions, "user_name,alert_key")
        store.mark_synced("alerts", [(a["id"], a["status"]) for a in alerts], self.user)
        n["alerts"], n["decisions"] = len(shared), len(decisions)
        scans = [s for s in store.dirty_scans() if s["status"] != "running"]
        if scans:
            self.upsert("scans", [{**{c: s.get(c) for c in SCAN_COLS}, "user_name": self.user} for s in scans], "uid")
            store.mark_synced("scans", [s["id"] for s in scans])
        n["scans"] = len(scans)
        games = store.dirty_games()
        if games:
            self.upsert("scan_games", [{**{c: g.get(c) for c in GAME_COLS}, "user_name": self.user} for g in games],
                        "scan_uid,fixture_id")
            store.mark_synced("scan_games", [g["id"] for g in games])
        n["games"] = len(games)
        return n

    # ---- recebimento ----
    def pull_seen(self, store) -> int:
        """Jogos ja olhados por qualquer um (so os mais novos que a ultima leitura)."""
        wm = store.get("cloud_seen_wm", "")
        params = {"select": "fixture_id,seen_at", "order": "seen_at.asc"}
        if wm:
            params["seen_at"] = f"gte.{wm}"  # gte: nao perde linhas do mesmo segundo (inserir repetido e inofensivo)
        rows = self.select("scan_games", params)
        if rows:
            store.add_remote_seen([(r["fixture_id"], r["seen_at"]) for r in rows])
            store.set("cloud_seen_wm", rows[-1]["seen_at"])
        return len(rows)

    def pull_alerts(self, store) -> int:
        wm = store.get("cloud_alerts_wm", "")
        params = {"select": ",".join(ALERT_COLS), "order": "created_at.asc"}  # tudo: restaura os meus numa reinstalacao
        if wm:
            params["created_at"] = f"gte.{wm}"
        rows = self.select("alerts", params)
        added = store.merge_remote_alerts(rows) if rows else 0
        if rows:
            store.set("cloud_alerts_wm", rows[-1]["created_at"])
        return added

    def pull_my_decisions(self, store) -> int:
        """Restaura decisoes feitas por mim em outra instalacao (so afeta alertas ainda pendentes aqui)."""
        rows = self.select("decisions", {"select": "*", "user_name": f"eq.{self.user}", "order": "decided_at.asc"})
        return store.apply_remote_decisions(rows) if rows else 0

    def sync(self, store) -> dict:
        """Envia o que mudou e traz o que e novo. Devolve um resumo; erros viram CloudError."""
        out = {"at": _now()}
        out.update(self.push(store))
        out["new_alerts"] = self.pull_alerts(store)
        out["seen"] = self.pull_seen(store)
        out["restored"] = self.pull_my_decisions(store)
        return out
