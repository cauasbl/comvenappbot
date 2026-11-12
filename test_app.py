import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import app
from bot import Bot
from store import Store
from test_bot import FakeTg, make_edge

TOURN = [
    {"tournamentId": 17, "tournamentName": "Premier League", "categoryName": "England", "futureFixtures": 290},
    {"tournamentId": 203, "tournamentName": "Premier League", "categoryName": "Russia", "futureFixtures": 0},
    {"tournamentId": 8, "tournamentName": "LaLiga", "categoryName": "Spain", "futureFixtures": 270},
]


def base_cfg():
    return {"bankroll": 1000.0, "kelly_fraction": 0.25, "max_stake_pct": 0.02, "min_ev": 0.02,
            "min_kelly_pct": 0.005, "max_ev": 0.15, "window_hours": 72, "max_fixtures": 10,
            "scan_interval_min": 0, "monthly_request_limit": 200,
            "leagues": {"A": {"id": 1, "enabled": True}, "B": 2}}


# ---- logica pura ----
def test_apply_config_valid_and_invalid():
    cfg = base_cfg()
    assert app.apply_config(cfg, {"max_fixtures": "25", "window_hours": 24, "scan_interval_min": 120}) == []
    assert (cfg["max_fixtures"], cfg["window_hours"], cfg["scan_interval_min"]) == (25, 24.0, 120)
    before = dict(cfg)
    assert app.apply_config(cfg, {"max_fixtures": -1})  # fora da faixa
    assert app.apply_config(cfg, {"window_hours": "abc"})
    assert app.apply_config(cfg, {"nao_existe": 1})
    assert app.apply_config(cfg, {"min_ev": 0.5})  # min_ev >= max_ev
    assert cfg == before  # nada aplicado quando ha erro


def test_apply_config_zero_means_all_and_off():
    cfg = base_cfg()
    assert app.apply_config(cfg, {"max_fixtures": 0, "scan_interval_min": 0}) == []


def test_league_ops():
    cfg = base_cfg()
    assert app.league_op(cfg, {"op": "toggle", "name": "A"}) is None
    assert cfg["leagues"]["A"]["enabled"] is False
    assert app.league_op(cfg, {"op": "toggle", "name": "B", "enabled": False}) is None  # forma antiga normalizada
    assert cfg["leagues"]["B"] == {"id": 2, "enabled": False}
    assert app.league_op(cfg, {"op": "add", "name": "C", "id": 3}) is None
    assert cfg["leagues"]["C"] == {"id": 3, "enabled": True}
    assert app.league_op(cfg, {"op": "add", "name": "D", "id": 3})  # id repetido
    assert app.league_op(cfg, {"op": "add", "name": "A", "id": 99})  # nome repetido
    assert app.league_op(cfg, {"op": "add", "name": "", "id": 5})
    assert app.league_op(cfg, {"op": "remove", "name": "C"}) is None and "C" not in cfg["leagues"]
    assert app.league_op(cfg, {"op": "remove", "name": "zzz"})


def test_search_leagues():
    r = app.search_leagues("premier", TOURN, {203})
    assert [x["id"] for x in r] == [17, 203]  # com jogos primeiro
    assert r[1]["added"] is True and r[0]["added"] is False
    assert app.search_leagues("premier russia", TOURN, set())[0]["id"] == 203
    assert app.search_leagues("a", TOURN, set()) == []


# ---- HTTP ----
@pytest.fixture
def srv(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg = base_cfg()
    cfg_path.write_text(json.dumps(cfg))
    (tmp_path / "papi").mkdir()
    (tmp_path / "papi" / "tournaments10.json").write_text(json.dumps(TOURN))
    edges = []
    bot = Bot(FakeTg(), Store(str(tmp_path / "t.db")), cfg, scanner=lambda c: edges, cfg_path=str(cfg_path))
    bot.store.set("chat_id", "111")
    a = app.App(bot, "tok", cache_dir=str(tmp_path / "papi"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(a))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield a, bot, server.server_address[1], edges, cfg_path
    server.shutdown()
    server.server_close()


def call(port, path, body=None, token="tok", host=None):
    headers = {"X-Token": token, "Content-Type": "application/json"}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers,
                                 data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_state_shape(srv):
    a, bot, port, *_ = srv
    code, s = call(port, "/api/state")
    assert code == 200
    assert {"config", "leagues", "budget", "cost", "auto", "telegram", "keys", "scan", "counts"} <= set(s)
    assert s["cost"] == {"lists": 2, "cap": 10}  # A (ativa) e B (formato antigo = ativa)
    assert s["telegram"] == {"enabled": True, "linked": True}


def test_post_requires_token_and_local_host(srv):
    a, bot, port, *_ = srv
    assert call(port, "/api/config", {"max_fixtures": 5}, token="errado")[0] == 403
    assert call(port, "/api/config", {"max_fixtures": 5}, host="evil.example.com")[0] == 403
    assert bot.cfg["max_fixtures"] == 10


def test_page_served_with_token(srv):
    a, bot, port, *_ = srv
    req = urllib.request.Request(f"http://127.0.0.1:{port}/")
    html = urllib.request.urlopen(req, timeout=5).read().decode()
    assert "tok" in html and "__TOKEN__" not in html and "EV CANTOS" in html


def test_config_persists_and_rejects_bad(srv):
    a, bot, port, edges, cfg_path = srv
    code, s = call(port, "/api/config", {"max_fixtures": 25, "window_hours": 12, "scan_interval_min": 60})
    assert code == 200 and s["config"]["max_fixtures"] == 25 and s["auto"]["interval"] == 60
    saved = json.loads(cfg_path.read_text())
    assert saved["max_fixtures"] == 25 and saved["window_hours"] == 12
    code, err = call(port, "/api/config", {"max_fixtures": -3})
    assert code == 400 and err["errors"]
    assert bot.cfg["max_fixtures"] == 25


def test_league_endpoints(srv):
    a, bot, port, edges, cfg_path = srv
    assert call(port, "/api/league", {"op": "toggle", "name": "B"})[0] == 200
    assert json.loads(cfg_path.read_text())["leagues"]["B"] == {"id": 2, "enabled": False}  # estava ativa
    assert call(port, "/api/league", {"op": "add", "name": "Premier League (England)", "id": 17})[0] == 200
    assert call(port, "/api/league", {"op": "add", "name": "Outra", "id": 17})[0] == 400
    code, s = call(port, "/api/league", {"op": "remove", "name": "A"})
    assert code == 200 and [x["name"] for x in s["leagues"]] == ["B", "Premier League (England)"]


def test_league_search_endpoint(srv):
    a, bot, port, *_ = srv
    code, r = call(port, "/api/leagues/search?q=premier")
    assert code == 200 and r["results"][0]["id"] == 17


def test_scan_then_list_and_decide(srv):
    a, bot, port, edges, _ = srv
    edges.append(make_edge())
    assert call(port, "/api/scan", {})[1]["started"] is True
    for _ in range(50):
        if bot.last_result and not bot.lock.locked():
            break
        time.sleep(0.1)
    assert bot.last_result["sent"] == 1
    code, r = call(port, "/api/alerts?status=pending")
    assert code == 200 and len(r["alerts"]) == 1
    al = r["alerts"][0]
    assert al["pin_odds"] == [2.0, 1.8] and al["fair_odd"] == pytest.approx(2.111, abs=1e-3)
    code, d = call(port, "/api/alerts/decide", {"id": al["id"], "status": "bet"})
    assert code == 200 and d["alert"]["status"] == "bet" and d["alert"]["bet_odd"] == 2.30
    assert call(port, "/api/alerts/decide", {"id": al["id"], "status": "ignored"})[0] == 409
    assert call(port, "/api/alerts/decide", {"id": "x", "status": "bet"})[0] == 400
    assert call(port, "/api/alerts?status=bet")[1]["alerts"][0]["id"] == al["id"]


def test_scan_rejected_while_running(srv):
    a, bot, port, *_ = srv
    bot.lock.acquire()
    try:
        assert call(port, "/api/scan", {})[1] == {"started": False, "reason": "running"}
    finally:
        bot.lock.release()
