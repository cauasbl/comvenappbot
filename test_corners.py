from datetime import datetime, timezone

import pytest

from corners import corner_meta, find_edges, is_half_line

CFG = {"sharp": "pinnacle", "soft": "betano", "min_ev": 0.03, "max_ev": 0.15, "min_odd": 1.5,
       "max_odd": 3.5, "max_overround": 1.08, "devig": "power", "bankroll": 1000,
       "kelly_fraction": 0.25, "max_stake_pct": 0.02}

MARKETS = [
    {"marketId": 1, "marketName": "Corners - Over Under Full Time", "handicap": 9.5,
     "outcomes": [{"outcomeId": 11, "outcomeName": "Over"}, {"outcomeId": 12, "outcomeName": "Under"}]},
    {"marketId": 2, "marketName": "Corners - Over Under Full Time", "handicap": 10.0,
     "outcomes": [{"outcomeId": 21, "outcomeName": "Over"}, {"outcomeId": 22, "outcomeName": "Under"}]},
    {"marketId": 3, "marketName": "Corners - Handicap", "handicap": 2.5,
     "outcomes": [{"outcomeId": 31, "outcomeName": "1"}, {"outcomeId": 32, "outcomeName": "2"}]},
    {"marketId": 9, "marketName": "Full Time Result", "handicap": 0.0,
     "outcomes": [{"outcomeId": 91, "outcomeName": "1"}, {"outcomeId": 92, "outcomeName": "2"}]},
]

NOW = datetime(2026, 11, 10, 1, 0, tzinfo=timezone.utc)


def _book(prices_by_market, active=True):
    return {"bookmakerIsActive": True, "suspended": False, "markets": {
        mid: {"marketActive": active, "outcomes": {
            oid: {"players": {"0": {"active": True, "price": pr, "changedAt": "2026-11-10T00:00:00.000Z"}}}
            for oid, pr in outs.items()}}
        for mid, outs in prices_by_market.items()}}


def fixture(pin, bet, bet_active=True):
    return {"participant1Name": "A", "participant2Name": "B",
            "bookmakerOdds": {"pinnacle": _book(pin), "betano": _book(bet, bet_active)}}


def test_corner_meta_filters_non_corners_and_handicap():
    assert set(corner_meta(MARKETS)) == {"1", "2"}
    assert set(corner_meta(MARKETS, include_handicap=True)) == {"1", "2", "3"}


@pytest.mark.parametrize("h,ok", [(9.5, True), (10.0, False), (9.75, False), (-2.5, True), (3.0, False)])
def test_half_line(h, ok):
    assert is_half_line(h) is ok


def test_no_edge_when_betano_equals_fair():
    meta = corner_meta(MARKETS)
    f = fixture({"1": {"11": 1.90, "12": 1.90}}, {"1": {"11": 2.00, "12": 1.80}})
    assert find_edges(f, meta, CFG, NOW) == []  # fair = 0.5, odd 2.00 -> EV 0


def test_finds_value_over_and_stake():
    # Pinnacle 2.00/1.80 -> fair over ~2.11; Betano 2.30 -> EV ~ +9%
    meta = corner_meta(MARKETS)
    f = fixture({"1": {"11": 2.00, "12": 1.80}}, {"1": {"11": 2.30, "12": 1.60}})
    edges = find_edges(f, meta, CFG, NOW)
    assert len(edges) == 1
    e = edges[0]
    assert e.outcome == "Over" and e.betano_odd == 2.30
    assert 0.07 < e.ev < 0.11
    assert 0 < e.stake <= 20.0  # teto de 2% da banca
    assert not e.suspicious
    assert e.pin_age_min == pytest.approx(60.0)


def test_push_line_ignored():
    meta = corner_meta(MARKETS)
    f = fixture({"2": {"21": 1.90, "22": 1.90}}, {"2": {"21": 2.50, "22": 1.60}})
    assert find_edges(f, meta, CFG, NOW) == []


def test_inactive_soft_market_ignored():
    meta = corner_meta(MARKETS)
    f = fixture({"1": {"11": 1.90, "12": 1.90}}, {"1": {"11": 2.50, "12": 1.60}}, bet_active=False)
    assert find_edges(f, meta, CFG, NOW) == []


def test_missing_pinnacle_side_skipped():
    meta = corner_meta(MARKETS)
    f = fixture({"1": {"11": 1.90}}, {"1": {"11": 2.50, "12": 1.60}})
    assert find_edges(f, meta, CFG, NOW) == []


def test_huge_ev_flagged_suspicious():
    meta = corner_meta(MARKETS)
    f = fixture({"1": {"11": 1.90, "12": 1.90}}, {"1": {"11": 2.60, "12": 1.50}})
    e = find_edges(f, meta, CFG, NOW)[0]
    assert e.suspicious


def test_high_overround_skipped():
    meta = corner_meta(MARKETS)
    f = fixture({"1": {"11": 1.50, "12": 1.50}}, {"1": {"11": 2.50, "12": 1.60}})
    assert find_edges(f, meta, CFG, NOW) == []
