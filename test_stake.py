import pytest

import app
from bot import current_stake
from store import Store
from test_app import call, srv  # noqa: F401  (srv e fixture do pytest)
from test_bot import make_edge  # noqa: F401


def test_current_stake_uses_current_bankroll_and_cap():
    row = {"kelly_pct": 0.0099}
    assert current_stake(row, {"bankroll": 1000, "max_stake_pct": 0.02}) == 9.9
    assert current_stake(row, {"bankroll": 5000, "max_stake_pct": 0.02}) == 49.5
    assert current_stake({"kelly_pct": 0.05}, {"bankroll": 5000, "stake_cap_pct": 0.02}) == 100.0  # teto opcional de 2%
    assert current_stake({"kelly_pct": 0.05}, {"bankroll": 5000}) == 250.0  # sem teto por padrao


def test_stake_is_quarter_of_ev_example_from_screenshot():
    from core import stake_pct
    p = 1 / 1.75  # odd justa 1.75, Betano 1.90 -> EV +8.57%
    assert stake_pct(p, 1.90) == pytest.approx(0.25 * 0.0857, abs=1e-4)  # 2,14% da banca, nao 2,37%
    assert current_stake({"kelly_pct": stake_pct(p, 1.90)}, {"bankroll": 5000}) == pytest.approx(107.14, abs=0.05)
    assert stake_pct(p, 1.90, mode="classic") == pytest.approx(0.0238, abs=1e-4)  # formula antiga, opcional


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
    assert pend[old]["stake"] == pytest.approx(49.5) and pend[old]["stake_at_alert"] == 9.9

    code, d = call(port, "/api/alerts/decide", {"id": old, "status": "bet"})
    assert code == 200 and d["alert"]["bet_stake"] == pytest.approx(49.5)  # grava a stake de AGORA

    bot.cfg["bankroll"] = 100000.0  # mudar a banca depois nao reescreve o que ja foi apostado
    bets = call(port, "/api/alerts?status=bet")[1]["alerts"]
    assert bets[0]["stake"] == pytest.approx(49.5)
    still = {x["id"]: x for x in call(port, "/api/alerts?status=pending")[1]["alerts"]}
    assert still[other]["stake"] == pytest.approx(app.botmod.current_stake({"kelly_pct": 0.0099}, bot.cfg))


def test_telegram_button_records_current_stake(srv):  # noqa: F811
    a, bot, port, edges, _ = srv
    aid = bot.store.add_alert(make_edge())
    bot.cfg.update(bankroll=5000.0, max_stake_pct=0.02)
    bot.handle({"update_id": 1, "callback_query": {"id": "c", "data": f"bet:{aid}",
                                                   "message": {"chat": {"id": 111}, "message_id": 5}}})
    assert bot.store.alert(aid)["bet_stake"] == pytest.approx(49.5)


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
