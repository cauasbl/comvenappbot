import pytest

from core import consensus, devig_power, devig_proportional, ev, kelly_full, kelly_stake


def test_proportional_example():
    p = devig_proportional([1.80, 2.00])
    assert p[0] == pytest.approx(0.5263, abs=1e-3)
    assert 1 / p[0] == pytest.approx(1.90, abs=0.01)
    assert sum(p) == pytest.approx(1.0)


def test_power_sums_to_one_and_close_to_proportional_on_even_market():
    p = devig_power([1.80, 2.00])
    assert sum(p) == pytest.approx(1.0, abs=1e-8)
    assert 1 / p[0] == pytest.approx(1.90, abs=0.03)


def test_power_removes_less_from_favourite_more_from_longshot():
    odds = [1.20, 5.50]
    prop = devig_proportional(odds)
    pw = devig_power(odds)
    assert pw[1] < prop[1]


def test_ev_and_kelly_user_example():
    p = 1 / 1.90
    assert ev(p, 2.00) == pytest.approx(0.0526, abs=1e-3)
    assert kelly_full(p, 2.00) == pytest.approx(0.0526, abs=1e-3)
    # 1/4 Kelly = ~1.32% da banca
    assert kelly_stake(p, 2.00, 1000) == pytest.approx(13.16, abs=0.05)


def test_kelly_zero_when_negative_ev():
    assert kelly_full(0.40, 2.00) == 0.0


def test_stake_cap():
    assert kelly_stake(0.9, 3.0, 1000, fraction=1.0, max_pct=0.02) == 20.0


def test_consensus_renormalizes_missing_sources():
    w = {"pinnacle": 0.7, "polymarket": 0.3}
    assert consensus({"pinnacle": 0.5, "polymarket": 0.6}, w) == pytest.approx(0.53)
    assert consensus({"pinnacle": 0.5}, w) == pytest.approx(0.5)
