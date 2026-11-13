import pytest

import app
from bot import current_stake
from store import Store
from test_app import call, srv  # noqa: F401  (srv e fixture do pytest)
from test_bot import make_edge  # noqa: F401

# stake dos alertas de make_edge (p=0.4737, odd 2.30) com banca 5000, Kelly classico 1/4
EXP5000 = 0.25 * (0.4737 * 2.30 - 1) / 1.30 * 5000


def test_current_stake_uses_current_bankroll_and_cap():
    row = {"kelly_pct": 0.0099}
    assert current_stake(row, {"bankroll": 1000, "max_stake_pct": 0.02}) == 9.9
    assert current_stake(row, {"bankroll": 5000, "max_stake_pct": 0.02}) == 49.5
    assert current_stake({"kelly_pct": 0.05}, {"bankroll": 5000, "stake_cap_pct": 0.02}) == 100.0  # teto opcional de 2%
    assert current_stake({"kelly_pct": 0.05}, {"bankroll": 5000}) == 250.0  # sem teto por padrao


def test_stake_is_quarter_of_ev_example_from_screenshot():
    from core import stake_pct
    p = 1 / 1.75  # odd justa 1.75, Betano 1.90 -> EV +8.57%
    assert stake_pct(p, 1.90, mode="ev") == pytest.approx(0.25 * 0.0857, abs=1e-4)  # 2,14% da banca, nao 2,37%
    assert current_stake({"kelly_pct": 0, "fair_prob": p, "betano_odd": 1.90}, {"bankroll": 5000, "kelly_mode": "ev"}) == pytest.approx(107.14, abs=0.05)
    assert stake_pct(p, 1.90) == pytest.approx(0.0238, abs=1e-4)  # classico e o padrao


def test_store_decide_records_given_stake_else_alert_stake(tmp_path):
    s = Store(str(tmp_path / "s.db"))
    a1, a2 = s.add_alert(make_edge()), s.add_alert(make_edge(odd=2.40))
    assert s.decide(a1, "bet", 49.5)["bet_stake"] == 49.5
    assert s.decide(a2, "bet")["bet_stake"] == 9.9  # sem stake informada: a gravada no alerta


def test_pending_alerts_follow_current_bankroll_but_decided_keep_theirs(srv):  # noqa: F811
    a, bot, port, edges, _ = srv
    s = bot.store
    old = s.add_alert(make_edge(odd=2.30))   # nasceu com stake 9.9 (banca de 1000)
    other = s.add_alert(make_edge(odd=2.40))
    bot.cfg.update(bankroll=5000.0, max_stake_pct=0.02)

    pend = {x["id"]: x for x in call(port, "/api/alerts?status=pending")[1]["alerts"]}
    assert pend[old]["stake"] == pytest.approx(EXP5000, abs=0.01) and pend[old]["stake_at_alert"] == 9.9

    code, d = call(port, "/api/alerts/decide", {"id": old, "status": "bet"})
    assert code == 200 and d["alert"]["bet_stake"] == pytest.approx(EXP5000, abs=0.01)  # grava a stake de AGORA

    bot.cfg["bankroll"] = 100000.0  # mudar a banca depois nao reescreve o que ja foi apostado
    bets = call(port, "/api/alerts?status=bet")[1]["alerts"]
    assert bets[0]["stake"] == pytest.approx(EXP5000, abs=0.01)
    still = {x["id"]: x for x in call(port, "/api/alerts?status=pending")[1]["alerts"]}
    assert still[other]["stake"] == pytest.approx(app.botmod.current_stake(bot.store.alert(other), bot.cfg))


def test_telegram_button_records_current_stake(srv):  # noqa: F811
    a, bot, port, edges, _ = srv
    aid = bot.store.add_alert(make_edge())
    bot.cfg.update(bankroll=5000.0, max_stake_pct=0.02)
    bot.handle({"update_id": 1, "callback_query": {"id": "c", "data": f"bet:{aid}",
                                                   "message": {"chat": {"id": 111}, "message_id": 5}}})
    assert bot.store.alert(aid)["bet_stake"] == pytest.approx(EXP5000, abs=0.01)


def test_rescan_refreshes_pending_numbers_but_not_decided(tmp_path):
    s = Store(str(tmp_path / "r.db"))
    aid = s.add_alert(make_edge(ev=0.09))
    e2 = make_edge(ev=0.09)
    e2.kelly_pct, e2.stake = 0.0225, 22.5
    assert s.add_alert(e2) is None and s.alert(aid)["kelly_pct"] == 0.0225  # pendente: atualizado
    s.decide(aid, "bet")
    e3 = make_edge(ev=0.09)
    e3.kelly_pct = 0.5
    s.add_alert(e3)
    assert s.alert(aid)["kelly_pct"] == 0.0225  # ja apostado: preservado


def test_kelly_mode_validation_and_pending_recalc():
    cfg = {"min_ev": 0.02, "max_ev": 0.15}
    assert app.apply_config(cfg, {"kelly_mode": "ev"}) == [] and cfg["kelly_mode"] == "ev"
    assert app.apply_config(cfg, {"kelly_mode": "xyz"}) and cfg["kelly_mode"] == "ev"
    row = {"status": "pending", "pin_odds": "[]", "fair_prob": 1 / 1.75, "betano_odd": 1.90, "kelly_pct": 0.0,
           "start_time": "2999-01-01T19:45:00.000Z", "stake": 1.0}
    base = {"bankroll": 5000, "kelly_fraction": 0.25}
    assert app.present(row, {**base, "kelly_mode": "ev"})["stake"] == pytest.approx(107.14, abs=0.05)
    assert app.present(row, {**base, "kelly_mode": "classic"})["stake"] == pytest.approx(119.0, abs=0.5)
