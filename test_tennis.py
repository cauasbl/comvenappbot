import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

import app
import corners
import oddspapi
from corners import estimate_cost, family_of, find_edges, market_meta, tennis_tournaments
from test_app import call
from test_appmode import srv2  # noqa: F401  (fixture do pytest)
from test_corners import NOW
from test_families import MARKETS as SOCCER_MARKETS
from test_venues import CFG as VCFG, book, fx

TENNIS_MARKETS = [
    {"marketId": 121, "marketName": "Winner", "handicap": 0.0,
     "outcomes": [{"outcomeId": 1211, "outcomeName": "1"}, {"outcomeId": 1212, "outcomeName": "2"}]},
    {"marketId": 123, "marketName": "First Set Winner", "handicap": 0.0,
     "outcomes": [{"outcomeId": 1231, "outcomeName": "1"}, {"outcomeId": 1232, "outcomeName": "2"}]},
    {"marketId": 12179, "marketName": "Game Handicap", "handicap": -2.5,
     "outcomes": [{"outcomeId": 121791, "outcomeName": "1"}, {"outcomeId": 121792, "outcomeName": "2"}]},
    {"marketId": 12150, "marketName": "Game Handicap", "handicap": -3.0,
     "outcomes": [{"outcomeId": 121501, "outcomeName": "1"}, {"outcomeId": 121502, "outcomeName": "2"}]},
    {"marketId": 1237, "marketName": "Total Games Over Under", "handicap": 22.5,
     "outcomes": [{"outcomeId": 12371, "outcomeName": "Over"}, {"outcomeId": 12372, "outcomeName": "Under"}]},
    {"marketId": 12231, "marketName": "Total Sets Over Under", "handicap": 2.5,
     "outcomes": [{"outcomeId": 122311, "outcomeName": "Over"}, {"outcomeId": 122312, "outcomeName": "Under"}]},
    {"marketId": 12404, "marketName": "Correct Score First Set", "handicap": 0.0,
     "outcomes": [{"outcomeId": 200 + i, "outcomeName": str(i)} for i in range(14)]},
    {"marketId": 12281, "marketName": "Total Aces", "handicap": 4.5,
     "outcomes": [{"outcomeId": 122811, "outcomeName": "Over"}, {"outcomeId": 122812, "outcomeName": "Under"}]},
]
META = market_meta(TENNIS_MARKETS, list(corners.TENNIS_FAMILIES))
CFG = {**VCFG, "venues": ["polymarket"], "fees": {"polymarket": 0.0}, "min_limit_usd": 0}


def tfixture(**venue_prices):
    f = fx({"121": {"1211": 1.50, "1212": 2.80},
            "12179": {"121791": 1.86, "121792": 2.03},
            "12150": {"121501": 1.90, "121502": 1.95},
            "1237": {"12371": 1.75, "12372": 2.16}}, **venue_prices)
    f.update(participant1Name="Alcaraz", participant2Name="Sinner")
    return f


@pytest.mark.parametrize("name,fam", [
    ("Winner", "tennis_winner"), ("First Set Winner", "tennis_winner"), ("Second Set Winner", "tennis_winner"),
    ("Total Games Over Under", "tennis_games"), ("Game Handicap", "tennis_games"),
    ("Total Games First Set", "tennis_games"), ("Total Sets Over Under", "tennis_sets"),
    ("Set Handicap", "tennis_sets"), ("Total Aces", None), ("Correct Score First Set", None),
    ("Participant 1 To Win a Set", None), ("Full Time Result", "result"), ("Over Under Full Time", "goals"),
])
def test_family_of_tennis(name, fam):
    assert family_of(name) == fam


def test_tennis_meta_keeps_two_way_markets_and_handicaps_but_not_exotics():
    assert set(META) == {"121", "123", "12179", "12150", "1237", "12231"}  # sem placar exato (14 resultados) e sem aces
    assert market_meta(TENNIS_MARKETS, ["tennis_winner"]).keys() == {"121", "123"}
    assert set(market_meta(SOCCER_MARKETS, list(corners.TENNIS_FAMILIES))) == set()  # futebol nunca vira tenis


def test_tennis_edge_labels_and_signed_handicap():
    poly = book({"121": {"1211": 1.90, "1212": 2.80}, "12179": {"121791": 2.20, "121792": 1.50},
                 "1237": {"12371": 1.75, "12372": 2.16}})
    edges = {(e.market, e.outcome): e for e in find_edges(tfixture(polymarket=poly), META, CFG, NOW)}
    assert ("Vencedor", "Alcaraz") in edges                          # "Winner" traduzido, "1" vira o jogador 1
    e = edges[("Handicap de games", "Alcaraz -2.5")]                  # linha com sinal no lado do jogador 1
    assert e.venue == "polymarket" and e.ev > 0.05
    assert not any(k[0] == "Total de games 22.5" for k in edges)      # sem vantagem ali: nao vira sinal
    assert edges[("Vencedor", "Alcaraz")].market == "Vencedor"        # linha 0: sem numero no nome


def test_handicap_other_side_gets_the_opposite_sign():
    poly = book({"12179": {"121791": 1.50, "121792": 2.40}})
    e = [x for x in find_edges(tfixture(polymarket=poly), META, CFG, NOW) if x.market == "Handicap de games"][0]
    assert e.outcome == "Sinner +2.5"


def test_whole_number_handicap_lines_are_skipped():
    poly = book({"12150": {"121501": 3.0, "121502": 3.0}})  # preco absurdo, mas linha -3.0 pode devolver a aposta
    assert find_edges(tfixture(polymarket=poly), META, CFG, NOW) == []


# ---- escolha automatica dos torneios ----
TOURNS = [
    {"tournamentId": 1, "tournamentName": "ATP Stockholm Men Singles", "categoryName": "ATP", "upcomingFixtures": 3, "futureFixtures": 1},
    {"tournamentId": 2, "tournamentName": "ATP Stockholm Men Doubles", "categoryName": "ATP", "upcomingFixtures": 3, "futureFixtures": 1},
    {"tournamentId": 3, "tournamentName": "WTA Finals Women Singles", "categoryName": "WTA", "upcomingFixtures": 0, "futureFixtures": 12},
    {"tournamentId": 4, "tournamentName": "Wimbledon Mixed Doubles", "categoryName": "ATP", "upcomingFixtures": 5, "futureFixtures": 0},
    {"tournamentId": 5, "tournamentName": "Wimbledon", "categoryName": "ATP", "upcomingFixtures": 0, "futureFixtures": 0},
    {"tournamentId": 6, "tournamentName": "ATP Challenger Lima Men Singles", "categoryName": "Challenger", "upcomingFixtures": 4, "futureFixtures": 3},
    {"tournamentId": 7, "tournamentName": "ITF Monastir", "categoryName": "ITF Men", "upcomingFixtures": 9, "futureFixtures": 0},
    {"tournamentId": 8, "tournamentName": "ATP Metz Men Singles", "categoryName": "ATP", "upcomingFixtures": 1, "futureFixtures": 2},
]


def test_tennis_tournaments_filters_categories_doubles_and_empty_ones():
    cfg = {"tennis": {"enabled": True, "categories": ["ATP", "WTA"], "max_tournaments": 10}}
    got = tennis_tournaments(cfg, TOURNS)
    assert [i for _, i in got] == [1, 8, 3]  # mais jogos proximos primeiro; sem duplas, mistas, ITF, Challenger ou vazios
    assert got[0][0] == "ATP Stockholm Men Singles (ATP)"
    cfg["tennis"]["categories"] = ["Challenger"]
    assert [i for _, i in tennis_tournaments(cfg, TOURNS)] == [6]


def test_tennis_tournaments_cap_and_off_switch():
    assert len(tennis_tournaments({"tennis": {"enabled": True, "max_tournaments": 2}}, TOURNS)) == 2
    assert tennis_tournaments({"tennis": {"enabled": False}}, TOURNS) == []
    assert tennis_tournaments({}, TOURNS) == []  # sem a chave na config: desligado (migracao liga)


def test_estimate_cost_counts_tennis_queries():
    soccer = {"leagues": {"A": {"id": 1, "enabled": True}, "B": {"id": 2, "enabled": True}}, "max_fixtures": 10}
    assert estimate_cost(soccer) == (2, 10)
    assert estimate_cost({**soccer, "tennis": {"enabled": True, "max_tournaments": 4}}) == (2 + 1 + 4, 10)


# ---- scan com os dois esportes ----
def test_run_live_scans_soccer_and_tennis_with_each_sports_markets(monkeypatch):
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    log = {"tournaments": [], "markets": [], "fixtures": [], "odds": []}

    class FC:
        session_requests = 0

        def __init__(self, key):
            pass

        def used_this_month(self):
            return 0

        def markets(self, sport):
            log["markets"].append(sport)
            return SOCCER_MARKETS if sport == 10 else TENNIS_MARKETS

        def tournaments(self, sport, max_age_h=None):
            log["tournaments"].append((sport, max_age_h))
            return TOURNS

        def fixtures(self, tid):
            log["fixtures"].append(tid)
            name = "soccer" if tid == 1000 else "tennis"
            return [{"fixtureId": f"{name}-{tid}", "startTime": start, "hasOdds": True,
                     "participant1Name": "A", "participant2Name": "B"}]

        def odds(self, fid, books):
            log["odds"].append(fid)
            if fid.startswith("soccer"):
                return fx({"3": {"31": 2.0, "32": 2.0}}, polymarket=book({"3": {"31": 2.40, "32": 1.50}}))
            return tfixture(polymarket=book({"121": {"1211": 1.90, "1212": 2.80}}))

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FC)
    cfg = {**CFG, "window_hours": 24, "max_fixtures": 10, "leagues": {"Liga": {"id": 1000, "enabled": True}},
           "tennis": {"enabled": True, "categories": ["ATP"], "max_tournaments": 2}}
    msgs = []
    edges = corners.run_live(cfg, progress=msgs.append)
    assert log["tournaments"] == [(12, 12)]                 # lista de torneios renovada a cada 12h
    assert sorted(log["markets"]) == [10, 12]
    assert log["fixtures"] == [1000, 1, 8]                  # futebol + os 2 torneios ATP de simples
    assert sorted(log["odds"]) == ["soccer-1000", "tennis-1", "tennis-8"]
    markets = {e.market for e in edges}
    assert "Vencedor" in markets and any(m.startswith("Total tempo normal") for m in markets)
    assert any("Tenis: 2 torneio(s)" in m for m in msgs)


def test_run_live_without_tennis_never_touches_tennis_endpoints(monkeypatch):
    seen = []

    class FC:
        session_requests = 0

        def __init__(self, key):
            pass

        def used_this_month(self):
            return 0

        def markets(self, sport):
            seen.append(("markets", sport))
            return SOCCER_MARKETS

        def tournaments(self, *a, **k):
            seen.append("tournaments")
            return []

        def fixtures(self, tid):
            return []

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FC)
    cfg = {**CFG, "window_hours": 24, "max_fixtures": 5, "leagues": {"L": {"id": 1, "enabled": True}}}
    corners.run_live(cfg, progress=lambda m: None)
    assert seen == [("markets", 10)]


def test_tennis_error_listing_tournaments_does_not_break_soccer(monkeypatch):
    class FC:
        session_requests = 0

        def __init__(self, key):
            pass

        def used_this_month(self):
            return 0

        def markets(self, sport):
            return SOCCER_MARKETS

        def tournaments(self, sport, max_age_h=None):
            raise oddspapi.OddsPapiError("/tournaments: HTTP 500")

        def fixtures(self, tid):
            return []

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FC)
    msgs = []
    cfg = {**CFG, "window_hours": 24, "max_fixtures": 5, "leagues": {"L": {"id": 1, "enabled": True}},
           "tennis": {"enabled": True}}
    assert corners.run_live(cfg, progress=msgs.append) == []
    assert any("Tenis: ERRO" in m for m in msgs)


# ---- cache de torneios com validade ----
def test_tournaments_cache_expires_only_when_asked(tmp_path, monkeypatch):
    c = oddspapi.Client("k" * 20, cache_dir=str(tmp_path))
    calls = []
    monkeypatch.setattr(oddspapi.Client, "_get", lambda self, path, **p: calls.append(path) or [{"tournamentId": 1}])
    assert c.tournaments(12, max_age_h=12) == [{"tournamentId": 1}] and len(calls) == 1
    c.tournaments(12, max_age_h=12)
    assert len(calls) == 1                                    # cache novo: nao consulta
    old = time.time() - 13 * 3600
    os.utime(tmp_path / "tournaments12.json", (old, old))
    c.tournaments(12, max_age_h=12)
    assert len(calls) == 2                                    # cache de 13h: renova
    os.utime(tmp_path / "tournaments12.json", (old, old))
    c.tournaments(12)
    assert len(calls) == 2                                    # sem validade (futebol): cache vale para sempre


# ---- configuracao ----
def test_migrate_tennis_is_idempotent_and_keeps_user_choices():
    cfg = {"families": {"goals": False, "result": True}}
    assert app.migrate_tennis(cfg) is True
    assert cfg["tennis"]["enabled"] is True and cfg["families"]["tennis_winner"] is True
    assert cfg["families"]["goals"] is False                   # escolha do usuario preservada
    snap = json.dumps(cfg, sort_keys=True)
    assert app.migrate_tennis(cfg) is False and json.dumps(cfg, sort_keys=True) == snap
    cfg2 = {"tennis": {"enabled": False}}
    app.migrate_tennis(cfg2)
    assert cfg2["tennis"]["enabled"] is False                  # quem desligou continua desligado


def test_apply_config_tennis_validation():
    cfg = {"min_ev": 0.02, "max_ev": 0.15, "tennis": dict(corners.NEW_TENNIS)}
    assert app.apply_config(cfg, {"tennis": {"categories": ["ATP", "Challenger"], "max_tournaments": 6}}) == []
    assert cfg["tennis"] == {"enabled": True, "categories": ["ATP", "Challenger"], "max_tournaments": 6}
    assert app.apply_config(cfg, {"tennis": {"enabled": False}}) == [] and cfg["tennis"]["enabled"] is False
    for bad in ({"categories": []}, {"categories": ["ITF Men"]}, {"max_tournaments": 0}, {"max_tournaments": 13},
                {"max_tournaments": True}, {"enabled": "sim"}, {"outro": 1}, "x"):
        assert app.apply_config(cfg, {"tennis": bad}), bad
    assert cfg["tennis"]["categories"] == ["ATP", "Challenger"]  # nada foi aplicado


def test_state_shows_tennis_settings_and_tournaments_from_cache_file(srv2):  # noqa: F811
    a, bot, port, _, tmp = srv2
    bot.cfg["tennis"] = dict(corners.NEW_TENNIS)
    s = call(port, "/api/state")[1]
    assert s["tennis"]["enabled"] is True and s["tennis"]["active"] == {"names": [], "cached_at": None}
    (tmp / "papi" / "tournaments12.json").write_text(json.dumps(TOURNS), encoding="utf-8")
    s = call(port, "/api/state")[1]
    assert s["tennis"]["active"]["names"] == ["ATP Stockholm Men Singles (ATP)", "ATP Metz Men Singles (ATP)",
                                              "WTA Finals Women Singles (WTA)"]
    assert call(port, "/api/config", {"tennis": {"categories": ["Challenger"]}})[0] == 200
    assert call(port, "/api/state")[1]["tennis"]["active"]["names"] == ["ATP Challenger Lima Men Singles (Challenger)"]
