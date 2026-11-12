"""Cliente minimo da OddsPapi v4 com ritmo por endpoint, cache de mercados e orcamento mensal."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.oddspapi.io/v4"


def budget_used(cache_dir: str = "papi") -> int:
    """Consultas feitas neste mes pelo programa (le papi/.budget.json)."""
    try:
        data = json.loads((Path(cache_dir) / ".budget.json").read_text())
    except (OSError, ValueError):
        data = {}
    return int(data.get(datetime.now(timezone.utc).strftime("%Y-%m"), 0))


class OddsPapiError(RuntimeError):
    pass


class BudgetExceeded(OddsPapiError):
    pass


class Client:
    def __init__(self, api_key: str, cache_dir: str = "papi", monthly_limit: int = 200,
                 min_interval: float = 1.3):
        self.key = api_key
        self.dir = Path(cache_dir)
        self.dir.mkdir(exist_ok=True)
        self.limit = monthly_limit
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self.session_requests = 0

    # ---- orcamento (plano gratis = 250 consultas/mes) ----
    def _budget_file(self) -> Path:
        return self.dir / ".budget.json"

    def used_this_month(self) -> int:
        return budget_used(str(self.dir))

    def _count(self) -> None:
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        try:
            data = json.loads(self._budget_file().read_text())
        except (OSError, ValueError):
            data = {}
        data[month] = int(data.get(month, 0)) + 1
        self._budget_file().write_text(json.dumps(data))
        self.session_requests += 1

    # ---- HTTP ----
    def _get(self, path: str, **params):
        if self.used_this_month() >= self.limit:
            raise BudgetExceeded(f"limite mensal de {self.limit} consultas atingido")
        params["apiKey"] = self.key
        url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last.get(path, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last[path] = time.monotonic()
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    body = json.load(r)
            except urllib.error.HTTPError as e:
                try:
                    body = json.loads(e.read().decode())
                except ValueError:
                    body = {"error": {"code": f"HTTP_{e.code}", "message": str(e)}}
            if isinstance(body, dict) and "error" in body:
                err = body["error"]
                code = err.get("code") if isinstance(err, dict) else str(err)
                if code == "RATE_LIMITED" and attempt < 3:
                    time.sleep(2.0)
                    continue
                raise OddsPapiError(f"{path}: {err}")
            self._count()
            return body
        raise OddsPapiError(f"{path}: limite de ritmo persistente")

    # ---- endpoints ----
    def markets(self, sport_id: int = 10) -> list[dict]:
        cache = self.dir / f"markets{sport_id}.json"
        if cache.exists():
            return json.loads(cache.read_text())
        data = self._get("/markets", sportId=sport_id)
        cache.write_text(json.dumps(data))
        return data

    def tournaments(self, sport_id: int = 10) -> list[dict]:
        cache = self.dir / f"tournaments{sport_id}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        data = self._get("/tournaments", sportId=sport_id)
        cache.write_text(json.dumps(data), encoding="utf-8")
        return data

    def fixtures(self, tournament_id: int) -> list[dict]:
        try:
            return self._get("/fixtures", tournamentId=tournament_id)
        except BudgetExceeded:
            raise
        except OddsPapiError as e:
            if "FIXTURE_NOT_FOUND" in str(e):  # campeonato sem jogos: a API devolve erro em vez de lista vazia
                return []
            raise

    def odds(self, fixture_id: str, bookmakers: list[str]) -> dict:
        return self._get("/odds", fixtureId=fixture_id, bookmakers=",".join(bookmakers))
