from datetime import datetime, timedelta, timezone

import pytest

import corners
import oddspapi
from bot import Bot
from store import Store
from test_app import call, srv  # noqa: F401  (srv e fixture do pytest)
from test_bot import FakeTg, make_edge
from test_families import CFG as FCFG, MARKETS, fixture


def _run(monkeypatch, skip=None, cap=10, games=5):
    start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    calls = []

    class FC:
        session_requests = 0

        def __init__(self, key):
            pass

        def used_this_month(self):
            return 0

        def markets(self, sport):
            return MARKETS

        def fixtures(self, tid):
            return [{"fixtureId": f"fx{i}", "startTime": start, "hasOdds": True,
                     "participant1Name": f"A{i}", "participant2Name": f"B{i}"} for i in range(1, games + 1)]

        def odds(self, fid, books):
            calls.append(fid)
            return fixture({"2": {"21": 1.90, "22": 1.90}}, {"2": {"21": 2.10, "22": 1.70}})

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FC)
    cfg = {**FCFG, "window_hours": 24, "max_fixtures": cap, "leagues": {"Liga X": {"id": 1, "enabled": True}}}
    seen, stats = [], {}
    edges = corners.run_live(cfg, progress=lambda m: None, skip=skip, stats=stats,
                             on_game=lambda f, i: seen.append((f["fixtureId"], i)))
    return edges, calls, seen, stats


def test_run_live_reports_each_game_and_stats(monkeypatch):
    edges, calls, seen, stats = _run(monkeypatch, games=3)
    assert calls == ["fx1", "fx2", "fx3"] and [s[0] for s in seen] == calls
    info = seen[0][1]
    assert info["league"] == "Liga X" and info["signals"] == 1 and info["compared"] == 1
    assert info["best_ev"] == pytest.approx(0.05, abs=1e-3)
    assert stats["listed"] == 3 and stats["skipped"] == 0 and stats["scanned"] == 3


def test_skip_removes_seen_games_before_the_cap(monkeypatch):
    # 5 jogos, 2 ja vistos, teto 2: o teto deve contar so jogos novos (fx3 e fx4), nao gastar com fx1/fx2
    edges, calls, seen, stats = _run(monkeypatch, skip={"fx1", "fx2"}, cap=2)
    assert calls == ["fx3", "fx4"]
    assert stats["listed"] == 5 and stats["skipped"] == 2 and stats["scanned"] == 2


# ---- Bot: registro e opcao de repetir ----
def make_bot(tmp_path, **cfg_extra):
    cfg = {"bankroll": 1000.0, "min_ev": 0.02, "min_kelly_pct": 0.005, "max_ev": 0.15, "kelly_fraction": 0.25,
           "leagues": {"Liga A": {"id": 1, "enabled": True}}, **cfg_extra}
    got = []

    def scanner(c, skip=None, on_game=None, stats=None):
        got.append(set(skip) if skip else set())
        for fid in ("g1", "g2"):
            if not skip or fid not in skip:
                on_game({"fixtureId": fid, "participant1Name": "A", "participant2Name": "B", "startTime": "x"},
                        {"league": "Liga A", "compared": 3, "signals": 0, "best_ev": 0.01, "note": ""})
        stats.update(listed=2, skipped=len(skip or ()), scanned=2 - len(skip or ()), blocked=0, queries=4)
        return [make_edge()]

    scanner.wants_ctx = True
    bot = Bot(FakeTg(), Store(str(tmp_path / "b.db")), cfg, scanner, cfg_path=str(tmp_path / "c.json"))
    return bot, got


def test_every_scan_is_registered_with_games_and_stats(tmp_path):
    bot, _ = make_bot(tmp_path)
    r = bot.scan()
    s = bot.store.list_scans()[0]
    assert s["id"] == r["scan_id"] and s["status"] == "done" and s["trigger"] == "manual"
    assert (s["listed"], s["skipped"], s["scanned"], s["queries"], s["signals"]) == (2, 0, 2, 4, 1)
    assert [g["fixture_id"] for g in bot.store.scan_games(s["id"])] == ["g1", "g2"]


def test_second_scan_skips_seen_games_unless_repeat_or_disabled(tmp_path):
    bot, got = make_bot(tmp_path)
    bot.scan()
    assert got[-1] == set()                      # 1o scan: nada visto ainda
    r = bot.scan()
    assert got[-1] == {"g1", "g2"} and r["skipped"] == 2   # 2o: pula os vistos
    bot.scan(repeat=True)
    assert got[-1] == set()                      # repetir: nao pula nada
    bot.cfg["skip_seen"] = False
    bot.scan()
    assert got[-1] == set()                      # opcao desligada: tambem nao pula
    assert len(bot.store.list_scans()) == 4


def test_failed_scan_is_registered_as_error_and_lock_released(tmp_path):
    bot, _ = make_bot(tmp_path)

    def boom(c, **kw):
        raise RuntimeError("cota esgotada")

    boom.wants_ctx = True
    bot.scanner = boom
    with pytest.raises(RuntimeError):
        bot.scan(trigger="auto")
    s = bot.store.list_scans()[0]
    assert s["status"] == "error" and "cota esgotada" in s["error"] and s["trigger"] == "auto"
    assert bot.lock.acquire(blocking=False)  # lock liberado
    bot.lock.release()


def test_scanner_without_ctx_still_works_and_is_registered(tmp_path):
    bot, _ = make_bot(tmp_path)
    bot.scanner = lambda c: [make_edge()]
    assert bot.scan()["sent"] == 1
    assert bot.store.list_scans()[0]["status"] == "done"


# ---- API ----
def test_scan_api_endpoints(srv):  # noqa: F811
    a, bot, port, edges, _ = srv
    sid = bot.store.start_scan("manual", {})
    bot.store.record_game(sid, {"fixtureId": "f1", "participant1Name": "A", "participant2Name": "B"},
                          {"league": "L", "compared": 2, "signals": 0, "best_ev": 0.0, "note": ""})
    bot.store.finish_scan(sid, "done", 0, stats={"listed": 1, "skipped": 0, "scanned": 1, "blocked": 0, "queries": 1})
    code, d = call(port, "/api/scans")
    assert code == 200 and d["scans"][0]["id"] == sid
    code, d = call(port, f"/api/scans/games?id={sid}")
    assert code == 200 and d["games"][0]["fixture"] == "A x B"
    assert call(port, "/api/scans/games?id=abc")[0] == 400


def test_config_skip_seen_validation():
    import app
    cfg = {"min_ev": 0.02, "max_ev": 0.15}
    assert app.apply_config(cfg, {"skip_seen": False}) == [] and cfg["skip_seen"] is False
    assert app.apply_config(cfg, {"skip_seen": "sim"}) and cfg["skip_seen"] is False
