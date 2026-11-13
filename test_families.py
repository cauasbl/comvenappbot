import pytest

import app
import corners
import oddspapi
from corners import enabled_families, family_of, find_edges, market_meta
from test_corners import NOW, fixture

CFG = {"sharp": "pinnacle", "soft": "betano", "min_ev": 0.02, "max_ev": 0.15, "min_odd": 1.5, "max_odd": 3.5,
       "max_overround": 1.08, "devig": "power", "bankroll": 1000, "kelly_fraction": 0.25,
       "max_stake_pct": 0.02, "min_kelly_pct": 0.005}


def mk(mid, name, handicap, outs):
    return {"marketId": mid, "marketName": name, "handicap": handicap,
            "outcomes": [{"outcomeId": k, "outcomeName": v} for k, v in outs.items()]}


MARKETS = [
    mk(1, "Corners - Over Under Full Time", 9.5, {11: "Over", 12: "Under"}),
    mk(2, "Bookings - Over Under Full Time", 3.5, {21: "Over", 22: "Under"}),
    mk(3, "Over Under Full Time", 2.5, {31: "Over", 32: "Under"}),
    mk(4, "Full Time Result", 0.0, {41: "1", 42: "X", 43: "2"}),
    mk(5, "Both Teams To Score", 0.0, {51: "Yes", 52: "No"}),
    mk(6, "Bookings - Handicap", 0.5, {61: "1", 62: "2"}),
    mk(7, "Correct Score Full Time", 0.0, {71: "1-0", 72: "0-0"}),
    mk(8, "Odd Even Full Time", 0.0, {81: "Odd", 82: "Even"}),
]


@pytest.mark.parametrize("name,fam", [
    ("Corners - Over Under Full Time", "corners"), ("Corners - 1X2", "corners"),
    ("Bookings - Over Under Team 1", "cards"), ("Bookings - 1X2", "cards"),
    ("Over Under First Half", "goals"), ("Over Under Team 2", "goals"),
    ("Full Time Result", "result"), ("First Half Result", "result"),
    ("Both Teams To Score", "btts"), ("Correct Score Full Time", None), ("Odd Even Full Time", None),
])
def test_family_of(name, fam):
    assert family_of(name) == fam


def test_market_meta_families_and_handicap():
    assert set(market_meta(MARKETS)) == {"1", "2", "3", "4", "5"}  # sem handicap, sem placar exato/impar-par
    assert set(market_meta(MARKETS, ["cards"])) == {"2"}
    assert set(market_meta(MARKETS, ["cards"], include_handicap=True)) == {"2", "6"}


def test_enabled_families_default_and_config():
    assert enabled_families({}) == list(corners.FAMILIES)
    assert enabled_families({"families": {"cards": True, "goals": False}}) == ["cards"]


def test_three_way_result_edge():
    meta = market_meta(MARKETS)
    f = fixture({"4": {"41": 2.00, "42": 3.40, "43": 3.80}}, {"4": {"41": 2.30, "42": 3.00, "43": 3.20}})
    edges = find_edges(f, meta, CFG, NOW)
    assert len(edges) == 1
    e = edges[0]
    assert e.market == "Resultado final (1X2)" and e.outcome == "A"  # time da casa, nao "1"
    assert len(e.pin_odds) == 3 and 0.05 < e.ev < 0.12


def test_cards_totals_edge_and_label():
    meta = market_meta(MARKETS)
    f = fixture({"2": {"21": 1.90, "22": 1.90}}, {"2": {"21": 2.10, "22": 1.70}})
    e = find_edges(f, meta, CFG, NOW)[0]
    assert e.market == "Cartoes - Total tempo normal 3.5" and e.outcome == "Over"
    assert e.ev == pytest.approx(0.05, abs=1e-3)


def test_btts_edge_uses_portuguese_labels():
    meta = market_meta(MARKETS)
    f = fixture({"5": {"51": 1.80, "52": 2.00}}, {"5": {"51": 2.00, "52": 1.70}})
    e = find_edges(f, meta, CFG, NOW)[0]
    assert e.market == "Ambas marcam" and e.outcome == "Sim"


def test_disabled_family_not_scanned():
    meta = market_meta(MARKETS, ["corners"])
    f = fixture({"2": {"21": 1.90, "22": 1.90}}, {"2": {"21": 2.10, "22": 1.70}})
    assert find_edges(f, meta, CFG, NOW) == []


# ---- app: liga/desliga familias ----
def test_apply_config_families():
    cfg = {"min_ev": 0.02, "max_ev": 0.15}
    assert app.apply_config(cfg, {"families": {"cards": False}}) == []
    assert cfg["families"] == {"corners": True, "cards": False, "goals": True, "result": True, "btts": True}
    before = dict(cfg["families"])
    assert app.apply_config(cfg, {"families": {"nada": True}})
    assert app.apply_config(cfg, {"families": {"corners": "sim"}})
    assert app.apply_config(cfg, {"families": {k: False for k in corners.FAMILIES}})  # precisa sobrar uma
    assert cfg["families"] == before


# ---- resiliencia da varredura ----
def test_client_fixtures_swallows_not_found_but_not_other_errors(tmp_path, monkeypatch):
    c = oddspapi.Client("k", cache_dir=str(tmp_path))

    def nf(self, path, **p):
        raise oddspapi.OddsPapiError("/fixtures: {'code': 'FIXTURE_NOT_FOUND'}")

    monkeypatch.setattr(oddspapi.Client, "_get", nf)
    assert c.fixtures(1) == []

    def other(self, path, **p):
        raise oddspapi.OddsPapiError("/fixtures: {'code': 'HTTP_402'}")

    monkeypatch.setattr(oddspapi.Client, "_get", other)
    with pytest.raises(oddspapi.OddsPapiError):
        c.fixtures(1)


def test_run_live_skips_broken_league_and_continues(monkeypatch):
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()

    class FakeClient:
        session_requests = 0

        def __init__(self, key):
            pass

        def used_this_month(self):
            return 0

        def markets(self, sport):
            return MARKETS

        def fixtures(self, tid):
            if tid == 1:
                raise oddspapi.OddsPapiError("boom")
            return [{"fixtureId": "fx1", "startTime": start, "hasOdds": True,
                     "participant1Name": "A", "participant2Name": "B"}]

        def odds(self, fid, books):
            return fixture({"2": {"21": 1.90, "22": 1.90}}, {"2": {"21": 2.10, "22": 1.70}})

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FakeClient)
    cfg = {**CFG, "window_hours": 24, "monthly_request_limit": 200, "max_fixtures": 10,
           "leagues": {"ruim": {"id": 1, "enabled": True}, "boa": {"id": 2, "enabled": True}}}
    msgs = []
    edges = corners.run_live(cfg, progress=msgs.append)
    assert len(edges) == 1 and edges[0].market.startswith("Cartoes")
    assert any("ruim" in m and "ERRO" in m for m in msgs)
    assert any("1 linhas comparadas, melhor EV +5.0%" in m for m in msgs)


def _scan_with(monkeypatch, odds_fn):
    """Roda run_live com 5 jogos e uma funcao que decide o que cada consulta de odds devolve."""
    from datetime import datetime, timedelta, timezone
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
                     "participant1Name": f"A{i}", "participant2Name": f"B{i}"} for i in range(1, 6)]

        def odds(self, fid, books):
            calls.append(fid)
            return odds_fn(fid)

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FC)
    msgs = []
    cfg = {**CFG, "window_hours": 24, "max_fixtures": 10, "leagues": {"liga": {"id": 1, "enabled": True}}}
    return corners.run_live(cfg, progress=msgs.append), calls, msgs


def _good():
    return fixture({"2": {"21": 1.90, "22": 1.90}}, {"2": {"21": 2.10, "22": 1.70}})


def test_scan_keeps_found_signals_and_stops_after_three_errors_in_a_row(monkeypatch):
    def odds(fid):
        if fid == "fx1":
            return _good()
        raise oddspapi.OddsPapiError("/odds: cota esgotada")

    edges, calls, msgs = _scan_with(monkeypatch, odds)
    assert len(edges) == 1  # o sinal do 1o jogo NAO se perde
    assert calls == ["fx1", "fx2", "fx3", "fx4"]  # parou no 3o erro seguido; o fx5 nem foi consultado
    assert any("3 ERROS SEGUIDOS" in m for m in msgs)


def test_scan_keeps_found_signals_on_unexpected_crash(monkeypatch):
    def odds(fid):
        if fid == "fx1":
            return _good()
        raise ValueError("falha inesperada")

    edges, calls, msgs = _scan_with(monkeypatch, odds)
    assert len(edges) == 1 and calls == ["fx1", "fx2"]
    assert any("ERRO: falha inesperada" in m and "1 sinal" in m for m in msgs)


def test_scan_continues_after_an_isolated_error(monkeypatch):
    def odds(fid):
        if fid == "fx2":
            raise oddspapi.OddsPapiError("/odds: erro so neste jogo")
        return _good()

    edges, calls, msgs = _scan_with(monkeypatch, odds)
    assert len(edges) == 4 and calls == ["fx1", "fx2", "fx3", "fx4", "fx5"]  # um erro isolado so pula aquele jogo


# ---- filtros editaveis e log "BARRADO" ----
def test_blocked_reason_branches():
    e = corners.Edge(fixture="A x B", market="m", outcome="o", pin_odds=(2.0, 1.8), fair_prob=0.5,
                     betano_odd=4.4, ev=0.12, stake=1.0, pin_age_min=1.0, suspicious=False, kelly_pct=0.02)
    assert "odd fora de" in corners.blocked_reason(e, CFG)
    e.betano_odd = 2.2
    assert corners.blocked_reason(e, CFG) is None
    assert "EV abaixo" in corners.blocked_reason(e, {**CFG, "min_ev": 0.5})
    assert "Kelly" in corners.blocked_reason(e, {**CFG, "min_kelly_pct": 0.5})


def test_scan_logs_barrado_when_ev_high_but_odd_out_of_range(monkeypatch):
    edges, calls, msgs = _scan_with(
        monkeypatch, lambda fid: fixture({"2": {"21": 3.80, "22": 1.30}}, {"2": {"21": 4.40, "22": 1.20}}))
    assert edges == []
    assert any("BARRADO" in m and "odd fora de" in m for m in msgs)
    assert any("barrado pelos filtros" in m for m in msgs)


def test_apply_config_odd_and_kelly_filters():
    cfg = {"min_ev": 0.02, "max_ev": 0.15, "min_odd": 1.5, "max_odd": 3.5}
    assert app.apply_config(cfg, {"min_odd": 1.2, "max_odd": 5, "min_kelly_pct": 0.01}) == []
    assert (cfg["min_odd"], cfg["max_odd"], cfg["min_kelly_pct"]) == (1.2, 5, 0.01)
    assert app.apply_config(cfg, {"min_odd": 6})  # min >= max e rejeitado
    assert app.apply_config(cfg, {"min_kelly_pct": 0.9})  # fora do intervalo
    assert cfg["min_odd"] == 1.2
