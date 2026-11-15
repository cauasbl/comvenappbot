"""Persistencia SQLite: alertas enviados, decisao (apostei/ignorei) e configuracoes do bot.

Usada por varias threads (bot do Telegram, varredura automatica e servidor do app),
entao toda operacao passa por um lock.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,
  created_at TEXT NOT NULL,
  fixture_id TEXT, start_time TEXT, fixture TEXT,
  market_id TEXT, market TEXT, outcome_id TEXT, outcome TEXT,
  pin_odds TEXT, fair_prob REAL, betano_odd REAL, ev REAL, kelly_pct REAL, stake REAL,
  status TEXT NOT NULL DEFAULT 'pending',
  decided_at TEXT, bet_odd REAL, bet_stake REAL,
  closing_pin_odds TEXT, clv REAL, message_id INTEGER
);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS scans(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, trigger TEXT, params TEXT,
  listed INTEGER, skipped INTEGER, scanned INTEGER, signals INTEGER, blocked INTEGER, queries INTEGER,
  status TEXT NOT NULL DEFAULT 'running', error TEXT
);
CREATE TABLE IF NOT EXISTS scan_games(
  id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER NOT NULL, fixture_id TEXT NOT NULL, fixture TEXT,
  league TEXT, start_time TEXT, compared INTEGER, signals INTEGER, best_ev REAL, note TEXT, seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_games_fx ON scan_games(fixture_id);
CREATE INDEX IF NOT EXISTS scan_games_scan ON scan_games(scan_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str = "bot.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self.db.executescript(SCHEMA)
            have = {r["name"] for r in self.db.execute("PRAGMA table_info(alerts)")}
            for col, typ in (("venue", "TEXT"), ("ask", "REAL"), ("fee", "REAL"), ("limit_usd", "REAL")):
                if col not in have:  # bancos antigos ganham as colunas novas
                    self.db.execute(f"ALTER TABLE alerts ADD COLUMN {col} {typ}")
            self.db.commit()

    # ---- settings ----
    def get(self, k: str, default=None):
        with self._lock:
            r = self.db.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set(self, k: str, v) -> None:
        with self._lock:
            self.db.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                            (k, str(v)))
            self.db.commit()

    # ---- registro de scans ----
    def start_scan(self, trigger: str, params: dict) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO scans(started_at,trigger,params) VALUES(?,?,?)",
                                  (now_iso(), trigger, json.dumps(params, ensure_ascii=False)))
            self.db.commit()
            return cur.lastrowid

    def finish_scan(self, scan_id: int, status: str, signals: int = 0, error: str | None = None,
                    stats: dict | None = None) -> None:
        s = stats or {}
        with self._lock:
            self.db.execute(
                "UPDATE scans SET finished_at=?, status=?, error=?, signals=?, listed=?, skipped=?, scanned=?, "
                "blocked=?, queries=? WHERE id=?",
                (now_iso(), status, error, signals, s.get("listed"), s.get("skipped"), s.get("scanned"),
                 s.get("blocked"), s.get("queries"), scan_id))
            self.db.commit()

    def record_game(self, scan_id: int, fixture: dict, info: dict) -> None:
        title = f"{fixture.get('participant1Name', '?')} x {fixture.get('participant2Name', '?')}"
        with self._lock:
            self.db.execute(
                "INSERT INTO scan_games(scan_id,fixture_id,fixture,league,start_time,compared,signals,best_ev,note,seen_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (scan_id, fixture["fixtureId"], title, info.get("league"), fixture.get("startTime"),
                 info.get("compared"), info.get("signals"), info.get("best_ev"), info.get("note"), now_iso()))
            self.db.commit()

    def seen_ids(self) -> set[str]:
        """Jogos cujas odds ja foram lidas em algum scan."""
        with self._lock:
            return {r["fixture_id"] for r in self.db.execute("SELECT DISTINCT fixture_id FROM scan_games")}

    def list_scans(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (int(limit),))]

    def scan_games(self, scan_id: int) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM scan_games WHERE scan_id=? ORDER BY start_time, id", (int(scan_id),))]

    # ---- alerts ----
    @staticmethod
    def key_of(e) -> str:
        return f"{e.fixture_id}|{e.market_id}|{e.outcome_id}|{e.betano_odd}" + (f"|{e.venue}" if e.venue else "")

    def add_alert(self, e) -> int | None:
        """Grava o alerta. Devolve o id, ou None se esse mesmo sinal (jogo/mercado/lado/odd) ja foi enviado."""
        with self._lock:
            try:
                cur = self.db.execute(
                    "INSERT INTO alerts(key,created_at,fixture_id,start_time,fixture,market_id,market,outcome_id,"
                    "outcome,pin_odds,fair_prob,betano_odd,ev,kelly_pct,stake,venue,ask,fee,limit_usd) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (self.key_of(e), now_iso(), e.fixture_id, e.start_time, e.fixture, e.market_id, e.market,
                     e.outcome_id, e.outcome, json.dumps(list(e.pin_odds)), e.fair_prob, e.betano_odd, e.ev,
                     e.kelly_pct, e.stake, e.venue, e.ask, e.fee, e.limit_usd))
            except sqlite3.IntegrityError:
                # mesmo sinal ja gravado: atualiza os numeros se ainda estiver pendente (ex.: formula de stake mudou)
                self.db.execute("UPDATE alerts SET fair_prob=?, ev=?, kelly_pct=?, stake=? WHERE key=? AND status='pending'",
                                (e.fair_prob, e.ev, e.kelly_pct, e.stake, self.key_of(e)))
                self.db.commit()
                return None
            self.db.commit()
            return cur.lastrowid

    def id_for(self, e) -> int | None:
        """Id do alerta ja gravado para esse mesmo sinal (jogo/mercado/lado/odd), se existir."""
        with self._lock:
            r = self.db.execute("SELECT id FROM alerts WHERE key=?", (self.key_of(e),)).fetchone()
        return r["id"] if r else None

    def alert(self, aid: int) -> dict | None:
        with self._lock:
            r = self.db.execute("SELECT * FROM alerts WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None

    def list_alerts(self, status: str | None = None, ids: list[int] | None = None, limit: int = 100) -> list[dict]:
        sql, args, where = "SELECT * FROM alerts", [], []
        if status:
            where.append("status=?")
            args.append(status)
        if ids:
            where.append(f"id IN ({','.join('?' * len(ids))})")
            args += [int(i) for i in ids]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def set_message_id(self, aid: int, mid: int) -> None:
        with self._lock:
            self.db.execute("UPDATE alerts SET message_id=? WHERE id=?", (mid, aid))
            self.db.commit()

    def decide(self, aid: int, status: str, stake: float | None = None) -> dict | None:
        """status: 'bet' ou 'ignored'. So decide alerta pendente; devolve a linha atualizada ou None.
        Em 'bet', grava a stake informada (a da banca atual); sem ela, a gravada no alerta."""
        with self._lock:
            a = self.alert(aid)
            if not a or a["status"] != "pending":
                return None
            if status == "bet":
                self.db.execute("UPDATE alerts SET status='bet', decided_at=?, bet_odd=betano_odd, "
                                "bet_stake=COALESCE(?, stake) WHERE id=?", (now_iso(), stake, aid))
            else:
                self.db.execute("UPDATE alerts SET status='ignored', decided_at=? WHERE id=?", (now_iso(), aid))
            self.db.commit()
            return self.alert(aid)

    def counts(self) -> dict:
        with self._lock:
            rows = self.db.execute("SELECT status, COUNT(*) n FROM alerts GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}
