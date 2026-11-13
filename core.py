"""Nucleo matematico: de-vig, consenso, EV e Kelly fracionado."""
from __future__ import annotations

from typing import Mapping, Sequence


def implied(odds: Sequence[float]) -> list[float]:
    return [1.0 / o for o in odds]


def devig_proportional(odds: Sequence[float]) -> list[float]:
    """Probabilidades justas dividindo pelo overround."""
    q = implied(odds)
    s = sum(q)
    return [x / s for x in q]


def devig_power(odds: Sequence[float], tol: float = 1e-10) -> list[float]:
    """Metodo da potencia: acha k tal que sum(q_i ** k) == 1.

    Tira mais margem das odds longas que o proporcional.
    """
    q = implied(odds)
    if abs(sum(q) - 1.0) < tol:
        return q
    f = lambda k: sum(x ** k for x in q) - 1.0
    # overround > 1 pede k > 1; overround < 1 pede k < 1
    if f(1.0) > 0:
        lo, hi = 1.0, 2.0
        while f(hi) > 0:
            hi *= 2
    else:
        lo, hi = 0.5, 1.0
        while f(lo) < 0:
            lo /= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    k = (lo + hi) / 2
    return [x ** k for x in q]


def devig(odds: Sequence[float], method: str = "power") -> list[float]:
    if method == "power":
        return devig_power(odds)
    if method == "proportional":
        return devig_proportional(odds)
    raise ValueError(f"metodo desconhecido: {method}")


def consensus(probs: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Media ponderada das probabilidades; renormaliza pesos das fontes presentes."""
    used = {k: weights[k] for k in probs if weights.get(k, 0) > 0}
    if not used:
        raise ValueError("nenhuma fonte com peso")
    total = sum(used.values())
    return sum(probs[k] * w for k, w in used.items()) / total


def ev(p_fair: float, odd: float) -> float:
    return p_fair * odd - 1.0


def kelly_full(p_fair: float, odd: float) -> float:
    if odd <= 1.0:
        return 0.0
    return max(0.0, (p_fair * odd - 1.0) / (odd - 1.0))


def stake_pct(p_fair: float, odd: float, fraction: float = 0.25, mode: str = "classic") -> float:
    """% da banca. 'classic' (padrao): fracao x Kelly cheio (EV/(odd-1)). 'ev': fracao x EV (EV 8% -> 2%)."""
    if mode == "ev":
        return max(0.0, ev(p_fair, odd)) * fraction
    return kelly_full(p_fair, odd) * fraction


def kelly_stake(
    p_fair: float,
    odd: float,
    bankroll: float,
    fraction: float = 0.25,
    max_pct: float = 0.02,
) -> float:
    """Stake em valor absoluto: fracao do Kelly, limitada a max_pct da banca."""
    pct = min(kelly_full(p_fair, odd) * fraction, max_pct)
    return round(pct * bankroll, 2)
