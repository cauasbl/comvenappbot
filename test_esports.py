import json
from datetime import datetime, timedelta, timezone

import pytest

import app
import corners
import oddspapi
from corners import ESPORT_GAMES, esport_tournaments, estimate_cost, family_of, find_edges, market_meta
from test_app import call
from test_appmode import srv2  # noqa: F401  (fixture do pytest)
from test_corners import NOW
from test_families import MARKETS as SOCCER_MARKETS
from test_tennis import TENNIS_MARKETS
from test_venues import CFG as VCFG, book, fx

CS = ESPORT_GAMES["cs"]
CS_MARKETS = [
    {"marketId": 171, "marketName": "Winner", "handicap": 0.0,
     "outcomes": [{"outcomeId": 1711, "outcomeName": "1"}, {"outcomeId": 1712, "outcomeName": "2"}]},
    {"marketId": 1747, "marketName": "First Map Winner (incl. overtime)", "handicap": 0.0,
     "outcomes": [{"outcomeId": 17471, "outcomeName": "1"}, {"outcomeId": 17472, "outcomeName": "2"}]},
    {"marketId": 1725, "marketName": "Maps Handicap", "handicap": -1.5,
     "outcomes": [{"outcomeId": 17251, "outcomeName": "1"}, {"outcomeId": 17252, "outcomeName": "2"}]},
    {"marketId": 173, "marketName": "Total Maps Over Under", "handicap": 2.5,
     "outcomes": [{"outcomeId": 1731, "outcomeName": "Over"}, {"outcomeId": 1732, "outcomeName": "Under"}]},
    {"marketId": 1790, "marketName": "Total Rounds First Map", "handicap": 26.5,
     "outcomes": [{"outcomeId": 17901, "outcomeName": "Over"}, {"outcomeId": 17902, "outcomeName": "Under"}]},
    {"marketId": 1799, "marketName": "Team 1 Total Rounds First Map", "handicap": 13.5,
     "outcomes": [{"outcomeId": 17991, "outcomeName": "Over"}, {"outcomeId": 17992, "outcomeName": "Under"}]},
    {"marketId": 1800, "marketName": "Correct Score", "handicap": 0.0,
     "outcomes": [{"outcomeId": 18000 + i, "outcomeName": str(i)} for i in range(6)]},
]
META = market_meta(CS_MARKETS, list(corners.ESPORTS_FAMILIES), sport=CS)
CFG = {**VCFG, "venues": ["polymarket"], "fees": {"polymarket": 0.0}, "min_limit_usd": 0}


def csfixture(pin_limit=75.0, **venues):
    """Pinnacle com o limite de aposta informado (em e-sports e baixo) + as plataformas dadas."""
    pin = book({"171": {"1711": 1.50, "1712": 2.80}, "1725": {"17251": 2.04, "17252": 1.79},
                "173": {"1731": 2.28, "1732": 1.64}}, pin_limit)
    f = fx({}, **venues)
    f["bookmakerOdds"]["pinnacle"] = pin
    f.update(participant1Name="Iberian Soul", participant2Name="Team Omega")
    return f


@pytest.mark.parametrize("name,fam", [
    ("Winner", "esports_winner"), ("First Map Winner (incl. overtime)", "esports_winner"),
    ("Third Map Winner (incl. overtime)", "esports_winner"), ("Maps Handicap", "esports_maps"),
    ("Total Maps Over Under", "esports_maps"), ("Total Rounds First Map", "esports_rounds"),
    ("Rounds Handicap Second Map", "esports_rounds"), ("Team 1 Total Rounds First Map", "esports_rounds"),
    ("Correct Score", None), ("Total Kills", None),
])
def test_family_of_esports(name, fam):
    assert family_of(name, CS) == fam


def test_sport_decides_the_family_of_winner():
    assert family_of("Winner", 12) == "tennis_winner" and family_of("Winner", CS) == "esports_winner"
    assert family_of("Over Under Full Time", 10) == "goals"  # futebol intacto


def test_esports_meta_includes_handicaps_and_drops_exotics():
    assert set(META) == {"171", "1747", "1725", "173", "1790", "1799"}  # sem "Correct Score" (6 resultados)
    assert set(market_meta(CS_MARKETS, ["esports_winner"], sport=CS)) == {"171", "1747"}
    assert market_meta(SOCCER_MARKETS, list(corners.ESPORTS_FAMILIES), sport=10) == {}


def test_esports_edge_labels_and_pin_limit_recorded():
    poly = book({"171": {"1711": 1.50, "1712": 3.40}, "1725": {"17251": 2.04, "17252": 1.79},
                 "173": {"1731": 2.28, "1732": 1.64}}, 900.0)
    edges = find_edges(csfixture(polymarket=poly), META, CFG, NOW)
    e = [x for x in edges if x.market == "Vencedor"][0]
    assert e.outcome == "Team Omega" and e.pin_limit == 75.0 and e.limit_usd == 900.0 and e.ev > 0.05
    poly2 = book({"1725": {"17251": 2.60, "17252": 1.79}}, 900.0)
    h = [x for x in find_edges(csfixture(polymarket=poly2), META, CFG, NOW) if "mapas" in x.market][0]
    assert h.market == "Handicap de mapas" and h.outcome == "Iberian Soul -1.5"


def test_pin_limit_filter_separates_reliable_lines():
    poly = book({"171": {"1711": 1.50, "1712": 3.40}}, 900.0)
    f = csfixture(pin_limit=75.0, polymarket=poly)
    assert find_edges(f, META, CFG, NOW)                                              # sem filtro: aparece
    assert find_edges(f, META, {**CFG, "min_pin_limit_usd": 200}, NOW) == []          # Pinnacle aceita so $75: barrado
    f2 = csfixture(pin_limit=5000.0, polymarket=poly)
    assert find_edges(f2, META, {**CFG, "min_pin_limit_usd": 200}, NOW)               # Pinnacle confiante: passa


def test_blocked_reason_explains_low_pinnacle_limit():
    e = corners.Edge("A x B", "m", "o", (2.0, 2.0), 0.5, 2.4, 0.2, 1.0, 1.0, False, 0.02, pin_limit=75.0)
    why = corners.blocked_reason(e, {**CFG, "min_pin_limit_usd": 200})
    assert "Pinnacle aceita so $75" in why and "pouco confiavel" in why
    assert corners.blocked_reason(e, CFG) is None


# ---- escolha de torneios e custo ----
TOURNS = [
    {"tournamentId": 1, "tournamentName": "CCT European Series", "categoryName": "CCT", "upcomingFixtures": 4, "futureFixtures": 2},
    {"tournamentId": 2, "tournamentName": "ESL Challenger League", "categoryName": "ESL", "upcomingFixtures": 3, "futureFixtures": 0},
    {"tournamentId": 3, "tournamentName": "Stake Ranked", "categoryName": "StarLadder", "upcomingFixtures": 0, "futureFixtures": 4},
    {"tournamentId": 4, "tournamentName": "Torneio parado", "categoryName": "X", "upcomingFixtures": 0, "futureFixtures": 0},
]


def test_esport_tournaments_picks_active_ones_nearest_first_with_per_game_cap():
    assert [i for _, i in esport_tournaments({"esports": {"max_tournaments": 2}}, TOURNS)] == [1, 2]
    assert [i for _, i in esport_tournaments({"esports": {"max_tournaments": 6}}, TOURNS)] == [1, 2, 3]  # sem o parado
    assert esport_tournaments({"esports": {"max_tournaments": 1}}, TOURNS)[0][0] == "CCT European Series (CCT)"


def test_estimate_cost_counts_esports_per_game():
    base = {"leagues": {}, "max_fixtures": 10}
    assert estimate_cost(base) == (0, 10)                                              # sem a chave: desligado
    assert estimate_cost({**base, "esports": {"enabled": True, "games": ["cs", "dota"], "max_tournaments": 2}}) == (6, 10)


# ---- scan ----
def test_run_live_routes_each_sport_to_its_own_markets(monkeypatch):
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
            return {10: SOCCER_MARKETS, 12: TENNIS_MARKETS}.get(sport, CS_MARKETS)

        def tournaments(self, sport, max_age_h=None):
            log["tournaments"].append((sport, max_age_h))
            return TOURNS

        def fixtures(self, tid):
            log["fixtures"].append(tid)
            return [{"fixtureId": f"fx-{tid}", "startTime": start, "hasOdds": True,
                     "participant1Name": "A", "participant2Name": "B"}]

        def odds(self, fid, books):
            log["odds"].append(fid)
            return csfixture(polymarket=book({"171": {"1711": 1.50, "1712": 3.40}}, 900.0))

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FC)
    cfg = {**CFG, "window_hours": 24, "max_fixtures": 10, "leagues": {},
           "esports": {"enabled": True, "games": ["cs", "dota"], "max_tournaments": 1}}
    msgs = []
    edges = corners.run_live(cfg, progress=msgs.append)
    assert log["tournaments"] == [(17, 12), (16, 12)]                     # 1 lista por jogo, renovada a cada 12h
    assert sorted(log["markets"]) == [10, 16, 17]                          # futebol + cada jogo de e-sports, uma vez
    assert log["fixtures"] == [1, 1] and len(log["odds"]) == 2             # 1 torneio por jogo
    assert {e.market for e in edges} == {"Vencedor"}
    assert any("E-sports CS: 1 torneio(s)" in m for m in msgs) and any("E-sports Dota 2" in m for m in msgs)


def test_run_live_esports_off_never_touches_them(monkeypatch):
    seen = []

    class FC:
        session_requests = 0

        def __init__(self, key):
            pass

        def used_this_month(self):
            return 0

        def markets(self, sport):
            seen.append(sport)
            return SOCCER_MARKETS

        def tournaments(self, *a, **k):
            seen.append("t")
            return []

        def fixtures(self, tid):
            return []

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FC)
    corners.run_live({**CFG, "window_hours": 24, "max_fixtures": 5, "leagues": {"L": {"id": 1, "enabled": True}},
                      "esports": {"enabled": False}}, progress=lambda m: None)
    assert seen == [10]


def test_one_game_failing_does_not_stop_the_others(monkeypatch):
    class FC:
        session_requests = 0

        def __init__(self, key):
            pass

        def used_this_month(self):
            return 0

        def markets(self, sport):
            return CS_MARKETS

        def tournaments(self, sport, max_age_h=None):
            if sport == ESPORT_GAMES["lol"]:
                raise oddspapi.OddsPapiError("/tournaments: HTTP 500")
            return TOURNS

        def fixtures(self, tid):
            return []

    monkeypatch.setenv("ODDSPAPI_KEY", "x")
    monkeypatch.setattr(oddspapi, "Client", FC)
    msgs = []
    cfg = {**CFG, "window_hours": 24, "max_fixtures": 5, "leagues": {},
           "esports": {"enabled": True, "games": ["lol", "cs"], "max_tournaments": 1}}
    corners.run_live(cfg, progress=msgs.append)
    assert any("E-sports LoL: ERRO" in m for m in msgs) and any("E-sports CS: 1 torneio(s)" in m for m in msgs)


# ---- configuracao e tela ----
def test_migrate_esports_idempotent_and_respects_choices():
    cfg = {"families": {"goals": False}}
    assert app.migrate_esports(cfg) is True
    assert cfg["esports"]["enabled"] is True and cfg["families"]["esports_winner"] is True
    assert cfg["families"]["goals"] is False
    snap = json.dumps(cfg, sort_keys=True)
    assert app.migrate_esports(cfg) is False and json.dumps(cfg, sort_keys=True) == snap
    off = {"esports": {"enabled": False}}
    app.migrate_esports(off)
    assert off["esports"]["enabled"] is False


def test_apply_config_esports_and_pin_limit_validation():
    cfg = {"min_ev": 0.02, "max_ev": 0.15, "esports": dict(corners.NEW_ESPORTS)}
    assert app.apply_config(cfg, {"esports": {"games": ["cs"], "max_tournaments": 3}, "min_pin_limit_usd": 200}) == []
    assert cfg["esports"]["games"] == ["cs"] and cfg["esports"]["max_tournaments"] == 3 and cfg["min_pin_limit_usd"] == 200
    for bad in ({"games": []}, {"games": ["fifa"]}, {"max_tournaments": 0}, {"max_tournaments": 7},
                {"enabled": "sim"}, {"x": 1}, "cs"):
        assert app.apply_config(cfg, {"esports": bad}), bad
    assert app.apply_config(cfg, {"min_pin_limit_usd": -1})
    assert cfg["esports"]["games"] == ["cs"]


def test_state_lists_esports_tournaments_per_game_from_cache(srv2):  # noqa: F811
    a, bot, port, _, tmp = srv2
    bot.cfg["esports"] = {"enabled": True, "games": ["cs", "dota"], "max_tournaments": 1}
    s = call(port, "/api/state")[1]
    assert s["esports"]["games"] == ["cs", "dota"] and s["esports"]["active"]["cs"] == {"names": [], "cached_at": None}
    assert [g["key"] for g in s["esports"]["all_games"]] == ["cs", "lol", "dota", "valorant"]
    (tmp / "papi" / "tournaments17.json").write_text(json.dumps(TOURNS), encoding="utf-8")
    s = call(port, "/api/state")[1]
    assert s["esports"]["active"]["cs"]["names"] == ["CCT European Series (CCT)"]
    assert call(port, "/api/config", {"esports": {"max_tournaments": 2}})[0] == 200
    assert len(call(port, "/api/state")[1]["esports"]["active"]["cs"]["names"]) == 2
