import json

import pytest

from bot import Bot, format_alert
from corners import Edge, active_leagues, corner_meta, find_edges
from store import Store
from test_corners import CFG, MARKETS, NOW, fixture


class FakeTg:
    def __init__(self):
        self.sent, self.edits, self.answers, self.markups = [], [], [], []

    def send(self, chat, text, buttons=None):
        self.sent.append((chat, text, buttons))
        return len(self.sent)

    def answer(self, cid, text=""):
        self.answers.append((cid, text))

    def edit_text(self, chat, mid, text, buttons=None):
        self.edits.append((chat, mid, text))

    def edit_markup(self, chat, mid, buttons):
        self.markups.append((chat, mid, buttons))


def make_edge(odd=2.30, suspicious=False, market_id="1", ev=0.09):
    return Edge(fixture="A x B", market="Corners - Over Under Full Time 9.5", outcome="Over",
                pin_odds=(2.0, 1.8), fair_prob=0.4737, betano_odd=odd, ev=ev, stake=9.9,
                pin_age_min=5.0, suspicious=suspicious, kelly_pct=0.0099, fixture_id="fx1",
                market_id=market_id, outcome_id="11", start_time="2999-01-01T19:45:00.000Z")  # futuro: o teste nao depende do relogio


@pytest.fixture
def env(tmp_path):
    cfg = {"bankroll": 1000.0, "min_ev": 0.02, "min_kelly_pct": 0.005, "max_ev": 0.15,
           "monthly_request_limit": 200, "scan_interval_min": 0,
           "leagues": {"Liga A": {"id": 1, "enabled": True}, "Liga B": {"id": 2, "enabled": False}}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    tg, store = FakeTg(), Store(str(tmp_path / "t.db"))
    edges = []
    bot = Bot(tg, store, cfg, scanner=lambda c: edges, cfg_path=str(path))
    return bot, tg, store, edges, path


def msg(text, chat=111):
    return {"update_id": 1, "message": {"chat": {"id": chat}, "text": text}}


def cb(data, chat=111, mid=5):
    return {"update_id": 2, "callback_query": {"id": "c1", "data": data,
                                               "message": {"chat": {"id": chat}, "message_id": mid}}}


# ---- filtros novos ----
def test_min_kelly_filter_blocks_small_stake():
    meta = corner_meta(MARKETS)
    f = fixture({"1": {"11": 2.00, "12": 1.80}}, {"1": {"11": 2.30, "12": 1.60}})
    assert len(find_edges(f, meta, CFG, NOW)) == 1
    assert find_edges(f, meta, {**CFG, "min_kelly_pct": 0.50}, NOW) == []  # exige >50% de 1/4 Kelly


def test_kelly_pct_and_ids_populated():
    meta = corner_meta(MARKETS)
    f = fixture({"1": {"11": 2.00, "12": 1.80}}, {"1": {"11": 2.30, "12": 1.60}})
    f.update(fixtureId="fx9", startTime="2026-11-12T19:45:00.000Z")
    e = find_edges(f, meta, CFG, NOW)[0]
    assert e.fixture_id == "fx9" and e.market_id == "1" and e.outcome_id == "11"
    assert e.kelly_pct == pytest.approx(0.25 * e.ev, abs=1e-9)


def test_active_leagues_respects_enabled_and_legacy():
    assert active_leagues({"leagues": {"A": {"id": 1, "enabled": True}, "B": {"id": 2, "enabled": False},
                                       "C": 3}}) == {"A": 1, "C": 3}


# ---- store ----
def test_store_dedupe_and_decide_once(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    aid = s.add_alert(make_edge())
    assert aid and s.add_alert(make_edge()) is None
    assert s.id_for(make_edge()) == aid and s.id_for(make_edge(odd=9.99)) is None
    assert s.add_alert(make_edge(odd=2.40)) is not None  # odd mudou -> novo sinal
    row = s.decide(aid, "bet")
    assert row["status"] == "bet" and row["bet_odd"] == 2.30 and row["bet_stake"] == 9.9
    assert s.decide(aid, "ignored") is None  # clique repetido nao sobrescreve
    assert s.counts() == {"bet": 1, "pending": 1}


# ---- formatacao ----
def test_format_alert_contents(tmp_path):
    s = Store(str(tmp_path / "f.db"))
    row = s.alert(s.add_alert(make_edge()))
    t = format_alert(row, "FOOT")
    assert "A x B" in t and "2.30" in t and "+9.0%" in t and "FOOT" in t and "2999-01-01 19:45 UTC" in t


# ---- bot ----
def test_start_registers_first_chat_only(env):
    bot, tg, store, *_ = env
    bot.handle(msg("/start", 111))
    assert store.get("chat_id") == "111"
    bot.handle(msg("/start", 222))
    assert store.get("chat_id") == "111" and all(c == "111" for c, *_ in tg.sent)


def test_unauthorized_chat_ignored(env):
    bot, tg, store, *_ = env
    bot.handle(msg("/start", 111))
    n = len(tg.sent)
    bot.handle(msg("/banca 99999", 222))
    bot.handle(cb("lg:0", chat=222))
    assert len(tg.sent) == n and bot.cfg["bankroll"] == 1000.0 and tg.markups == []


def test_scan_sends_new_skips_duplicates_and_suspicious(env):
    bot, tg, store, edges, _ = env
    bot.handle(msg("/start"))
    edges += [make_edge(), make_edge(odd=2.60, suspicious=True, ev=0.2)]
    bot.handle(msg("/scan"))
    alerts = [s for s in tg.sent if s[2] and s[2][0][0]["callback_data"].startswith("bet:")]
    assert len(alerts) == 1
    assert "1 novo(s) alerta(s), 0 repetido(s), 1 suspeito(s)" in tg.sent[-1][1]
    assert bot.last_result["all_ids"] == bot.last_result["ids"] and len(bot.last_result["ids"]) == 1
    bot.handle(msg("/scan"))
    assert "0 novo(s) alerta(s), 1 repetido(s)" in tg.sent[-1][1]
    # a varredura repetida continua listando o sinal (so nao reenvia ao Telegram)
    assert bot.last_result["ids"] == [] and len(bot.last_result["all_ids"]) == 1


def test_buttons_record_decision(env):
    bot, tg, store, edges, _ = env
    bot.handle(msg("/start"))
    edges.append(make_edge())
    bot.handle(msg("/scan"))
    bot.handle(cb("bet:1"))
    assert store.alert(1)["status"] == "bet"
    assert "APOSTEI" in tg.edits[-1][2]
    bot.handle(cb("ign:1"))
    assert store.alert(1)["status"] == "bet" and tg.answers[-1][1] == "Já registrado"


def test_league_toggle_persists(env):
    bot, tg, store, edges, path = env
    bot.handle(msg("/start"))
    bot.handle(cb("lg:1"))
    assert bot.cfg["leagues"]["Liga B"]["enabled"] is True
    assert json.loads(path.read_text())["leagues"]["Liga B"]["enabled"] is True
    assert tg.markups


def test_banca_and_auto_commands(env):
    bot, tg, store, edges, path = env
    bot.handle(msg("/start"))
    bot.handle(msg("/banca 2500,50"))
    bot.handle(msg("/auto 90"))
    saved = json.loads(path.read_text())
    assert saved["bankroll"] == 2500.5 and saved["scan_interval_min"] == 90
    bot.handle(msg("/banca abc"))
    assert "Use: /banca" in tg.sent[-1][1]


def test_scan_failure_reported_not_raised(env):
    bot, tg, store, edges, _ = env
    bot.handle(msg("/start"))
    bot.scanner = lambda c: (_ for _ in ()).throw(RuntimeError("limite mensal"))
    bot.handle(msg("/scan"))
    assert "Varredura falhou" in tg.sent[-1][1] and "limite mensal" in tg.sent[-1][1]


def test_real_client_get_updates_builds_request(monkeypatch):
    """Passa pelo cliente HTTP de verdade (urlopen falso): pegou o conflito do parametro 'timeout'."""
    import urllib.request
    from tg import Telegram
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps({"ok": True, "result": [{"update_id": 7}]}).encode()

    def fake_urlopen(req, timeout):
        seen["timeout"], seen["body"] = timeout, json.loads(req.data)
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert Telegram("TOKEN").get_updates(5, timeout=25) == [{"update_id": 7}]
    assert seen["timeout"] == 35 and seen["body"]["timeout"] == 25 and seen["body"]["offset"] == 5
