import sqlite3

import pytest

import app
import corners
from bot import format_alert
from corners import find_edges, market_meta, venue_fee
from store import Store
from test_corners import NOW
from test_families import MARKETS

CFG = {"sharp": "pinnacle", "venues": ["kalshi", "polymarket"], "fees": {"kalshi": 0.07, "polymarket": 0.0},
       "min_ev": 0.02, "max_ev": 0.15, "min_odd": 1.1, "max_odd": 20.0, "max_overround": 1.08,
       "devig": "power", "bankroll": 1000, "kelly_fraction": 0.25, "min_kelly_pct": 0.0, "min_limit_usd": 50.0}


def book(prices, limit=500.0):
    """prices: {marketId: {outcomeId: preco decimal}}; limit = dolares disponiveis no melhor preco."""
    return {"bookmakerIsActive": True, "suspended": False, "markets": {
        mid: {"marketActive": True, "outcomes": {
            oid: {"players": {"0": {"active": True, "price": pr, "limit": limit,
                                    "changedAt": "2026-11-10T00:00:00.000Z"}}}
            for oid, pr in outs.items()}}
        for mid, outs in prices.items()}}


def fx(pin, **venues):
    return {"participant1Name": "A", "participant2Name": "B",
            "bookmakerOdds": {"pinnacle": book(pin, None), **{v: b for v, b in venues.items()}}}


PIN = {"3": {"31": 2.00, "32": 2.00}}  # Over/Under 2.5 sem juice: justa 50/50
META = market_meta(MARKETS, ["goals"])


def test_venue_fee_formula():
    assert venue_fee(CFG, "kalshi", 0.50) == pytest.approx(0.07 * 0.25)
    assert venue_fee(CFG, "polymarket", 0.50) == 0.0
    assert venue_fee({"fees": {}}, "kalshi", 0.5) == 0.0


def test_exchange_edge_includes_fee_in_ev_and_odd():
    # Kalshi paga 2.20 no Over (ask 45,45c); taxa 7% x P x (1-P) = 1,74c por contrato
    f = fx(PIN, kalshi=book({"3": {"31": 2.20, "32": 1.60}}))
    e = find_edges(f, META, CFG, NOW)[0]
    ask = 1 / 2.20
    fee = 0.07 * ask * (1 - ask)
    assert e.venue == "kalshi" and e.ask == pytest.approx(ask) and e.fee == pytest.approx(fee)
    assert e.betano_odd == pytest.approx(1 / (ask + fee))  # odd efetiva, ja com taxa
    assert e.ev == pytest.approx(0.5 * 1 / (ask + fee) - 1, abs=2e-3)  # EV depois da taxa (p ~ 0,5 apos devig)
    assert e.limit_usd == 500.0


def test_fee_can_erase_an_edge():
    f = fx(PIN, kalshi=book({"3": {"31": 2.06, "32": 1.60}}))  # EV bruto +3%
    assert find_edges(f, META, {**CFG, "fees": {"kalshi": 0.0}, "min_ev": 0.02}, NOW)
    assert find_edges(f, META, {**CFG, "min_ev": 0.02}, NOW) == []  # com taxa de 7% some


def test_two_venues_give_one_edge_each_and_min_book_filters():
    f = fx(PIN, kalshi=book({"3": {"31": 2.40, "32": 1.50}}, 500.0), polymarket=book({"3": {"31": 2.40, "32": 1.50}}, 10.0))
    assert {e.venue for e in find_edges(f, META, {**CFG, "min_limit_usd": 0}, NOW)} == {"kalshi", "polymarket"}
    assert {e.venue for e in find_edges(f, META, CFG, NOW)} == {"kalshi"}  # polymarket: so $10 no livro, minimo $50


def test_legacy_single_soft_book_still_works_without_venue_fields():
    cfg = {**CFG, "venues": None, "soft": "betano", "fees": {}}
    f = {"participant1Name": "A", "participant2Name": "B",
         "bookmakerOdds": {"pinnacle": book(PIN, None), "betano": book({"3": {"31": 2.30, "32": 1.50}}, None)}}
    e = find_edges(f, META, cfg, NOW)[0]
    assert e.venue == "" and e.betano_odd == 2.30 and e.fee == 0.0


def test_blocked_reason_for_shallow_book():
    e = corners.Edge("A x B", "m", "o", (2.0, 2.0), 0.5, 2.4, 0.2, 1.0, 1.0, False, 0.02, limit_usd=7.0)
    assert "so ha $7" in corners.blocked_reason(e, CFG)
    assert corners.blocked_reason(e, {**CFG, "min_limit_usd": 5.0}) is None


# ---- config ----
def test_migrate_legacy_config_to_venues_and_is_idempotent():
    cfg = {"soft": "betano", "min_odd": 1.5, "max_odd": 3.5, "families": {"corners": True, "cards": True, "goals": True}}
    assert app.migrate_venues(cfg) is True
    assert cfg["venues"] == ["kalshi", "polymarket"] and cfg["fees"]["kalshi"] == 0.07
    assert cfg["families"]["corners"] is False and cfg["families"]["cards"] is False and cfg["families"]["goals"] is True
    assert cfg["min_odd"] == 1.1 and cfg["max_odd"] == 20.0
    snap = dict(cfg)
    assert app.migrate_venues(cfg) is False and cfg == snap


def test_apply_config_venues_and_fees_validation():
    cfg = {"min_ev": 0.02, "max_ev": 0.15, "venues": ["kalshi"], "fees": {"kalshi": 0.07}}
    assert app.apply_config(cfg, {"venues": ["kalshi", "polymarket"], "min_limit_usd": 100}) == []
    assert cfg["venues"] == ["kalshi", "polymarket"] and cfg["min_limit_usd"] == 100
    assert app.apply_config(cfg, {"venues": []}) and app.apply_config(cfg, {"venues": ["betano"]})
    assert app.apply_config(cfg, {"fees": {"kalshi": 0.02}}) == [] and cfg["fees"]["kalshi"] == 0.02
    assert cfg["fees"]["polymarket"] == app.DEFAULT_FEES["polymarket"]
    assert app.apply_config(cfg, {"fees": {"kalshi": 0.9}}) and app.apply_config(cfg, {"fees": {"betano": 0.01}})
    assert cfg["fees"]["kalshi"] == 0.02


# ---- banco e alerta ----
def _edge(venue="kalshi", odd=2.1):
    return corners.Edge("A x B", "Total tempo normal 2.5", "Over", (2.0, 2.0), 0.5, odd, 0.05, 10.0, 1.0, False,
                        0.0125, fixture_id="fx1", market_id="3", outcome_id="31", venue=venue, ask=0.4545,
                        fee=0.0174, limit_usd=500.0)


def test_store_keeps_venue_fields_and_dedupes_per_venue(tmp_path):
    s = Store(str(tmp_path / "v.db"))
    a, b = s.add_alert(_edge("kalshi")), s.add_alert(_edge("polymarket"))
    assert a and b and a != b  # mesma odd em casas diferentes = sinais diferentes
    assert s.add_alert(_edge("kalshi")) is None
    r = s.alert(a)
    assert (r["venue"], r["ask"], r["fee"], r["limit_usd"]) == ("kalshi", 0.4545, 0.0174, 500.0)


def test_old_database_gets_new_columns(tmp_path):
    p = str(tmp_path / "old.db")
    db = sqlite3.connect(p)
    db.executescript("CREATE TABLE alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE NOT NULL, "
                     "created_at TEXT NOT NULL, fixture_id TEXT, start_time TEXT, fixture TEXT, market_id TEXT, "
                     "market TEXT, outcome_id TEXT, outcome TEXT, pin_odds TEXT, fair_prob REAL, betano_odd REAL, "
                     "ev REAL, kelly_pct REAL, stake REAL, status TEXT NOT NULL DEFAULT 'pending', decided_at TEXT, "
                     "bet_odd REAL, bet_stake REAL, closing_pin_odds TEXT, clv REAL, message_id INTEGER);"
                     "INSERT INTO alerts(key,created_at,fixture) VALUES('k','2026-01-01T00:00:00','velho');")
    db.commit()
    db.close()
    s = Store(p)
    assert s.list_alerts()[0]["venue"] is None  # linha antiga preservada
    assert s.add_alert(_edge()) is not None


def test_format_alert_shows_venue_price_fee_and_book(tmp_path):
    s = Store(str(tmp_path / "f.db"))
    t = format_alert(s.alert(s.add_alert(_edge())))
    assert "Kalshi" in t and "45¢" in t and "1.7¢ taxa" in t and "livro $500" in t and "Betano" not in t
