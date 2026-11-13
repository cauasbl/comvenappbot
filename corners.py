"""Scanner de escanteios: odd justa da Pinnacle (sem juice) contra a Betano.

Dry-run: so imprime no console. Nao aposta, nao envia nada.

Uso:
    $env:ODDSPAPI_KEY = "..."
    python corners.py                      # ao vivo, usa config.json
    python corners.py --offline papi/nl_*.json   # sem gastar consultas
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core import devig, ev, stake_pct


def _capped(pct: float, cfg: dict) -> float:
    """Aplica o teto opcional de stake (stake_cap_pct; 0 ou ausente = sem teto)."""
    cap = cfg.get("stake_cap_pct", 0.0)
    return min(pct, cap) if cap else pct


@dataclass
class Edge:
    fixture: str
    market: str
    outcome: str
    pin_odds: tuple
    fair_prob: float
    betano_odd: float
    ev: float
    stake: float
    pin_age_min: float | None
    suspicious: bool
    kelly_pct: float = 0.0
    fixture_id: str = ""
    market_id: str = ""
    outcome_id: str = ""
    start_time: str | None = None

    @property
    def fair_odd(self) -> float:
        return 1.0 / self.fair_prob


# ---------- extracao ----------
FAMILIES = {
    "corners": "Escanteios",
    "cards": "Cartoes",
    "goals": "Gols (mais/menos)",
    "result": "Resultado 1X2",
    "btts": "Ambas marcam",
}


def family_of(name: str) -> str | None:
    n = name.lower()
    if "corner" in n:
        return "corners"
    if "booking" in n or "card" in n:
        return "cards"
    if n.startswith("over under"):
        return "goals"
    if n in ("full time result", "first half result"):
        return "result"
    if n.startswith("both teams to score"):
        return "btts"
    return None


def enabled_families(cfg: dict) -> list[str]:
    """Familias ligadas no config. Sem a chave 'families', todas."""
    fam = cfg.get("families")
    return list(FAMILIES) if fam is None else [k for k in FAMILIES if fam.get(k)]


def market_meta(markets: list[dict], families=None, include_handicap: bool = False) -> dict[str, dict]:
    """marketId -> meta dos mercados de 2 ou 3 vias das familias ligadas (ids sao globais entre casas)."""
    on = set(FAMILIES) if families is None else set(families)
    out = {}
    for m in markets:
        name = m.get("marketName", "")
        fam = family_of(name)
        if fam not in on or len(m.get("outcomes", [])) not in (2, 3):
            continue
        if "handicap" in name.lower() and not include_handicap:
            continue
        out[str(m["marketId"])] = {**m, "family": fam}
    return out


def corner_meta(markets: list[dict], include_handicap: bool = False) -> dict[str, dict]:
    """Atalho: so escanteios."""
    return market_meta(markets, ["corners"], include_handicap)


def is_half_line(handicap: float) -> bool:
    """So linhas x.5 (sem push e sem quarter line)."""
    x = abs(handicap) * 2
    return abs(x - round(x)) < 1e-9 and round(x) % 2 == 1


def _price(outcome: dict):
    for p in outcome.get("players", {}).values():
        if p.get("active", True) and p.get("price") and p["price"] > 1:
            return p["price"], p.get("changedAt")
    return None


def book_prices(book: dict | None, meta: dict[str, dict]) -> dict[str, dict[str, tuple]]:
    if not book or book.get("suspended") or not book.get("bookmakerIsActive", True):
        return {}
    out = {}
    for mid, m in book.get("markets", {}).items():
        if mid not in meta or not m.get("marketActive", True):
            continue
        prices = {}
        for oid, o in m.get("outcomes", {}).items():
            p = _price(o)
            if p:
                prices[oid] = p
        out[mid] = prices
    return out


def _age_min(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (now - t).total_seconds() / 60


LABELS = {"Full Time Result": "Resultado final (1X2)", "First Half Result": "Resultado 1o tempo (1X2)",
          "Both Teams To Score": "Ambas marcam", "Both Teams To Score First Half": "Ambas marcam 1o tempo"}
WORDS = (("Bookings", "Cartoes"), ("Corners", "Escanteios"), ("Over Under", "Total"),
         ("Full Time", "tempo normal"), ("First Half", "1o tempo"))


def _label(name: str, fixture: dict) -> str:
    p1 = fixture.get("participant1Name") or "Time 1"
    p2 = fixture.get("participant2Name") or "Time 2"
    if name in LABELS:
        return LABELS[name]
    for a, b in WORDS:
        name = name.replace(a, b)
    return name.replace("Team 1", p1).replace("Team 2", p2)


def _outcome_label(name: str, fixture: dict) -> str:
    return {"1": fixture.get("participant1Name") or "Casa", "2": fixture.get("participant2Name") or "Fora",
            "X": "Empate", "Yes": "Sim", "No": "Nao"}.get(name, name)


# ---------- nucleo ----------
def find_edges(fixture: dict, meta: dict[str, dict], cfg: dict, now: datetime | None = None) -> list[Edge]:
    now = now or datetime.now(timezone.utc)
    books = fixture.get("bookmakerOdds", {})
    sharp = book_prices(books.get(cfg["sharp"]), meta)
    soft = book_prices(books.get(cfg["soft"]), meta)
    title = f"{fixture.get('participant1Name', '?')} x {fixture.get('participant2Name', '?')}"
    edges: list[Edge] = []
    for mid, spr in sharp.items():
        m = meta[mid]
        h = m.get("handicap", 0.0)
        if h and not is_half_line(h):  # linha inteira/quarto pode devolver a aposta; 1X2 e ambas-marcam nao tem linha
            continue
        oids = [str(o["outcomeId"]) for o in m["outcomes"]]
        names = {str(o["outcomeId"]): o["outcomeName"] for o in m["outcomes"]}
        if any(o not in spr for o in oids):
            continue
        odds = [spr[o][0] for o in oids]
        if sum(1 / x for x in odds) > cfg.get("max_overround", 1.08):
            continue
        probs = devig(odds, cfg.get("devig", "power"))
        for oid, p in zip(oids, probs):
            sp = soft.get(mid, {}).get(oid)
            if not sp:
                continue
            odd = sp[0]
            if not (cfg["min_odd"] <= odd <= cfg["max_odd"]):
                continue
            e = ev(p, odd)
            if e < cfg["min_ev"]:
                continue
            kpct = stake_pct(p, odd, cfg["kelly_fraction"], cfg.get("kelly_mode", "classic"))
            if kpct < cfg.get("min_kelly_pct", 0.0):
                continue
            edges.append(Edge(
                fixture=title,
                market=f"{_label(m['marketName'], fixture)} {abs(h):g}" if h else _label(m["marketName"], fixture),
                outcome=_outcome_label(names[oid], fixture),
                pin_odds=tuple(odds),
                fair_prob=p,
                betano_odd=odd,
                ev=e,
                stake=round(_capped(kpct, cfg) * cfg["bankroll"], 2),
                pin_age_min=_age_min(spr[oid][1], now),
                suspicious=e > cfg["max_ev"],
                kelly_pct=kpct,
                fixture_id=fixture.get("fixtureId", ""),
                market_id=mid,
                outcome_id=oid,
                start_time=fixture.get("startTime"),
            ))
    return sorted(edges, key=lambda x: -x.ev)


# ---------- saida ----------
def print_edges(edges: list[Edge]) -> None:
    ok = [e for e in edges if not e.suspicious]
    sus = [e for e in edges if e.suspicious]
    if not edges:
        print("Nenhum +EV dentro dos filtros.")
        return
    for title, group in (("+EV", ok), ("SUSPEITAS (EV acima do teto, provavel erro de linha)", sus)):
        if not group:
            continue
        print(f"\n== {title}: {len(group)} ==")
        for e in group:
            age = f"{e.pin_age_min:.0f}min" if e.pin_age_min is not None else "?"
            print(f"{e.fixture} | {e.market} | {e.outcome}\n"
                  f"   Pinnacle {'/'.join(f'{x:.3f}' for x in e.pin_odds)} -> justa {e.fair_odd:.3f} (p={e.fair_prob:.3f})"
                  f" | Betano {e.betano_odd:.2f} | EV {e.ev * 100:+.1f}% | stake {e.stake:.2f}"
                  f" | ultima mudanca Pinnacle ha {age}")


# ---------- execucao ----------
def run_offline(files: list[str], meta: dict, cfg: dict) -> list[Edge]:
    edges = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            edges += find_edges(json.load(fh), meta, cfg)
    return sorted(edges, key=lambda x: -x.ev)


def active_leagues(cfg: dict) -> dict[str, int]:
    """Ligas marcadas para varredura. Aceita {"nome": id} ou {"nome": {"id": n, "enabled": bool}}."""
    out = {}
    for name, v in cfg["leagues"].items():
        if isinstance(v, dict):
            if v.get("enabled", True):
                out[name] = v["id"]
        else:
            out[name] = v
    return out


def estimate_cost(cfg: dict) -> tuple[int, int]:
    """(consultas de lista = 1 por liga ativa, teto de jogos). Teto 0 = sem limite (custo imprevisivel)."""
    return len(active_leagues(cfg)), int(cfg.get("max_fixtures", 10) or 0)


def blocked_reason(e: Edge, cfg: dict) -> str | None:
    """Por que um candidato (EV calculado sem filtros) nao virou alerta; None = passou. Mesma ordem do find_edges."""
    if not cfg["min_odd"] <= e.betano_odd <= cfg["max_odd"]:
        return f"odd fora de {cfg['min_odd']:g}-{cfg['max_odd']:g}"
    if e.ev < cfg["min_ev"]:
        return f"EV abaixo do minimo de {cfg['min_ev']:.1%}"
    if e.kelly_pct < cfg.get("min_kelly_pct", 0.0):
        return f"1/4 Kelly {e.kelly_pct:.2%} abaixo do minimo de {cfg.get('min_kelly_pct', 0.0):.2%}"
    return None


def run_live(cfg: dict, max_fixtures: int | None = None, progress=print) -> list[Edge]:
    """Varre as ligas ativas. O teto de jogos vale para TODAS as ligas juntas, pelos jogos mais proximos."""
    from oddspapi import Client, OddsPapiError
    key = os.environ.get("ODDSPAPI_KEY")
    if not key:
        raise RuntimeError("Defina ODDSPAPI_KEY")
    c = Client(key)
    cap = int(cfg.get("max_fixtures", 10) or 0) if max_fixtures is None else max_fixtures
    meta = market_meta(c.markets(10), enabled_families(cfg), cfg.get("include_handicap", False))
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=cfg["window_hours"])
    progress(f"Consultas ja feitas pelo app neste mes (esta chave): {c.used_this_month()}")
    pool = []
    for league, tid in active_leagues(cfg).items():
        try:
            listed = c.fixtures(tid)
        except OddsPapiError as e:  # uma liga com problema nao derruba a varredura inteira
            progress(f"{league}: ERRO ao listar jogos ({e}); pulando")
            continue
        fx = [x for x in listed if x.get("hasOdds")
              and now < datetime.fromisoformat(x["startTime"].replace("Z", "+00:00")) <= horizon]
        progress(f"{league}: {len(fx)} jogos na janela de {cfg['window_hours']}h")
        pool += [(x["startTime"], x) for x in fx]
    pool.sort(key=lambda t: t[0])
    if cap:
        pool = pool[:cap]
    progress(f"Olhando {len(pool)} jogo(s), os mais proximos primeiro")
    edges, failures, blocked = [], 0, 0
    try:
        for i, (_, f) in enumerate(pool, 1):
            try:
                data = c.odds(f["fixtureId"], [cfg["sharp"], cfg["soft"]])
            except OddsPapiError as e:
                failures += 1
                progress(f"[{i}/{len(pool)}] ERRO nas odds ({e}); pulando")
                if failures >= 3:  # provavelmente cota da API acabou/chave invalida: nao adianta insistir
                    progress("3 ERROS SEGUIDOS: parei aqui. Os sinais ja encontrados foram mantidos.")
                    break
                continue
            failures = 0
            data.update({k: f[k] for k in ("participant1Name", "participant2Name") if k in f})
            found = find_edges(data, meta, cfg)
            edges += found
            # transparencia: quantas linhas deu para comparar e o melhor EV visto, mesmo abaixo dos filtros
            seen = find_edges(data, meta, {**cfg, "min_ev": -1.0, "min_kelly_pct": -1.0, "min_odd": 1.0,
                                           "max_odd": 1000.0, "max_ev": 1e9})
            top = max(seen, key=lambda e: e.ev) if seen else None
            best = f"{top.ev:+.1%}" if top else "-"
            note = ""
            if top and top.ev >= cfg["min_ev"] and (top.market_id, top.outcome_id) not in {
                    (e.market_id, e.outcome_id) for e in found}:
                why = blocked_reason(top, cfg)
                if why:  # o melhor EV e calculado SEM filtros: aqui diz qual linha foi barrada e por que
                    blocked += 1
                    note = f" [BARRADO: {top.market} {top.outcome} @ {top.betano_odd:.2f} - {why}]"
            progress(f"[{i}/{len(pool)}] {f.get('participant1Name', '?')} x {f.get('participant2Name', '?')}"
                     f" -> {len(found)} sinal(is) | {len({e.market_id for e in seen})} linhas comparadas,"
                     f" melhor EV {best}{note}")
    except Exception as e:  # noqa: BLE001 - qualquer falha no meio nunca pode jogar fora o que ja foi achado
        progress(f"ERRO: {e} - os {len(edges)} sinal(is) ja encontrado(s) foram mantidos")
    if blocked:
        progress(f"{blocked} jogo(s) tinham candidato com EV acima do minimo, barrado pelos filtros "
                 f"(veja as linhas BARRADO; ajuste em CONFIGURACOES > BANCA E FILTROS).")
    progress(f"Consultas nesta execucao: {c.session_requests} | neste mes (esta chave): {c.used_this_month()}")
    return sorted(edges, key=lambda x: -x.ev)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--offline", nargs="+", help="arquivos de odds salvos (glob aceito)")
    ap.add_argument("--markets", default="papi/markets10.json")
    ap.add_argument("--max-fixtures", type=int, default=10)
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if args.offline:
        files = [f for pat in args.offline for f in glob.glob(pat)]
        with open(args.markets, encoding="utf-8") as fh:
            meta = market_meta(json.load(fh), enabled_families(cfg), cfg.get("include_handicap", False))
        print_edges(run_offline(files, meta, cfg))
    else:
        try:
            print_edges(run_live(cfg, args.max_fixtures))
        except RuntimeError as e:
            sys.exit(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
