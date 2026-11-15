import json
import urllib.parse

import pytest

import cloud
from cloud import Cloud, CloudError
from store import Store
from test_bot import make_edge

KEY = "k" * 40
URL = "https://abc.supabase.co"
PKS = {"alerts": ("key",), "scans": ("uid",), "scan_games": ("scan_uid", "fixture_id"),
       "decisions": ("user_name", "alert_key")}


class FakeSupabase:
    """Minimo de PostgREST: upsert por on_conflict e GET com eq/neq/gt/gte, order, limit, offset."""

    def __init__(self):
        self.t = {n: {} for n in PKS}
        self.calls = []
        self.fail = None

    def __call__(self, method, url, headers, body=None, timeout=15.0):
        if self.fail:
            raise self.fail
        assert headers["apikey"] == KEY and headers["Authorization"] == f"Bearer {KEY}"
        u = urllib.parse.urlparse(url)
        table = u.path.rsplit("/", 1)[1]
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        self.calls.append((method, table, q))
        if table not in self.t:
            return 404, json.dumps({"message": f"relation {table} does not exist"}).encode()
        if method == "POST":
            keys = tuple(q["on_conflict"].split(","))
            assert keys == PKS[table]
            for row in json.loads(body):
                k = tuple(row[c] for c in keys)
                self.t[table][k] = {**self.t[table].get(k, {}), **row}
            return 201, b""
        rows = list(self.t[table].values())
        for col, cond in q.items():
            if col in ("select", "order", "limit", "offset", "on_conflict"):
                continue
            op, val = cond.split(".", 1)
            rows = [r for r in rows if {"eq": r.get(col) == val, "neq": r.get(col) != val,
                                        "gt": str(r.get(col)) > val, "gte": str(r.get(col)) >= val}[op]]
        if "order" in q:
            col, d = q["order"].split(".")
            rows.sort(key=lambda r: str(r.get(col)), reverse=d == "desc")
        off, lim = int(q.get("offset", 0)), int(q.get("limit", 1000))
        rows = rows[off:off + lim]
        if q.get("select", "*") != "*":
            cols = q["select"].split(",")
            rows = [{c: r.get(c) for c in cols} for r in rows]
        return 200, json.dumps(rows).encode()


@pytest.fixture
def fake(monkeypatch):
    f = FakeSupabase()
    monkeypatch.setattr(cloud, "_http", f)
    return f


def person(tmp_path, name):
    return Store(str(tmp_path / f"{name}.db")), Cloud(URL, KEY, name)


def do_scan(store, fixture_ids=("g1", "g2"), signals=1):
    sid = store.start_scan("manual", {"x": 1})
    for fid in fixture_ids:
        store.record_game(sid, {"fixtureId": fid, "participant1Name": "A", "participant2Name": "B", "startTime": "t"},
                          {"league": "L", "compared": 3, "signals": 0, "best_ev": 0.01, "note": ""})
    store.finish_scan(sid, "done", signals, stats={"listed": 2, "skipped": 0, "scanned": 2, "blocked": 0, "queries": 3})
    return sid


# ---- validacao ----
@pytest.mark.parametrize("url,key,user", [("http://abc.supabase.co", KEY, "ana"), ("ftp://x", KEY, "ana"),
                                          (URL, "curta", "ana"), (URL, KEY, "a"), (URL, KEY, "nome com espaco")])
def test_invalid_credentials_rejected(url, key, user):
    with pytest.raises(CloudError):
        Cloud(url, key, user)


def test_localhost_http_allowed_for_tests_only():
    Cloud("http://127.0.0.1:9", KEY, "ana")
    with pytest.raises(CloudError):
        Cloud("http://example.com", KEY, "ana")


def test_check_reports_missing_tables_with_hint(fake):
    c = Cloud(URL, KEY, "ana")
    assert c.check() == {"ok": True}
    del fake.t["decisions"]
    with pytest.raises(CloudError, match="supabase_setup.sql"):
        c.check()


def test_network_error_is_cloud_error_and_local_data_untouched(fake, tmp_path):
    s, c = person(tmp_path, "ana")
    s.add_alert(make_edge())
    fake.fail = CloudError("sem conexao com a nuvem (timeout)")
    with pytest.raises(CloudError):
        c.sync(s)
    assert len(s.dirty_alerts()) == 1  # nada foi marcado como enviado


# ---- fluxo entre duas pessoas ----
def test_two_people_share_signals_and_seen_games_but_not_decisions(fake, tmp_path):
    a, ca = person(tmp_path, "ana")
    b, cb = person(tmp_path, "beto")
    aid = a.add_alert(make_edge())
    do_scan(a)
    r = ca.sync(a)
    assert (r["alerts"], r["scans"], r["games"], r["decisions"]) == (1, 1, 2, 0)
    assert len(fake.t["alerts"]) == 1 and len(fake.t["scans"]) == 1 and len(fake.t["scan_games"]) == 2

    r = cb.sync(b)  # beto recebe o sinal da ana e os jogos que ela ja viu
    assert r["new_alerts"] == 1 and r["seen"] == 2
    assert b.seen_ids() == {"g1", "g2"}
    got = b.list_alerts()[0]
    assert got["found_by"] == "ana" and got["status"] == "pending" and got["synced"] == 1

    b.decide(got["id"], "bet", 25.0)  # decisao do beto
    r = cb.sync(b)
    assert r["decisions"] == 1 and r["alerts"] == 0  # nao reenvia o sinal que nao e dele
    assert list(fake.t["decisions"]) == [("beto", got["key"])]
    ca.sync(a)
    assert a.alert(aid)["status"] == "pending"  # a decisao do beto nao vira decisao da ana
    assert a.seen_ids() == {"g1", "g2"}


def test_second_sync_sends_nothing_new(fake, tmp_path):
    a, ca = person(tmp_path, "ana")
    a.add_alert(make_edge())
    do_scan(a)
    ca.sync(a)
    r = ca.sync(a)
    assert r["alerts"] == r["scans"] == r["games"] == r["decisions"] == 0


def test_running_scan_waits_until_finished(fake, tmp_path):
    a, ca = person(tmp_path, "ana")
    sid = a.start_scan("manual", {})
    a.record_game(sid, {"fixtureId": "g9", "participant1Name": "A", "participant2Name": "B"}, {"league": "L"})
    r = ca.sync(a)
    assert r["scans"] == 0 and r["games"] == 0
    a.finish_scan(sid, "done", 0, stats={})
    r = ca.sync(a)
    assert r["scans"] == 1 and r["games"] == 1


def test_decision_made_during_push_stays_dirty(tmp_path):
    s = Store(str(tmp_path / "r.db"))
    aid = s.add_alert(make_edge())
    snapshot = [(a["id"], a["status"]) for a in s.dirty_alerts()]   # o que foi enviado: pendente
    s.decide(aid, "bet", 10.0)                                      # usuario decide no meio do envio
    s.mark_synced("alerts", snapshot, "ana")
    assert [a["id"] for a in s.dirty_alerts()] == [aid]             # ainda precisa enviar a decisao


def test_reinstall_restores_own_alerts_and_decisions(fake, tmp_path):
    a, ca = person(tmp_path, "ana")
    aid = a.add_alert(make_edge())
    a.decide(aid, "bet", 33.0)
    ca.sync(a)
    fresh = Store(str(tmp_path / "novo_pc.db"))
    r = Cloud(URL, KEY, "ana").sync(fresh)  # mesmo usuario, banco local vazio
    assert r["new_alerts"] == 1 and r["restored"] == 1
    row = fresh.list_alerts()[0]
    assert row["status"] == "bet" and row["bet_stake"] == 33.0


def test_incremental_pull_uses_watermark_and_is_idempotent(fake, tmp_path):
    a, ca = person(tmp_path, "ana")
    b, cb = person(tmp_path, "beto")
    a.add_alert(make_edge(odd=2.30))
    ca.sync(a)
    cb.sync(b)
    a.add_alert(make_edge(odd=2.40))
    ca.sync(a)
    r = cb.sync(b)
    assert r["new_alerts"] == 1  # so o novo conta (o do mesmo segundo da marca d'agua nao duplica)
    assert len(b.list_alerts()) == 2
    sent = [q for m, t, q in fake.calls if m == "GET" and t == "alerts" and "created_at" in q]
    assert sent and sent[-1]["created_at"].startswith("gte.")


def test_scans_get_a_uid_and_games_carry_it(tmp_path):
    s = Store(str(tmp_path / "m.db"))
    do_scan(s)
    assert s.dirty_scans()[0]["uid"] and s.dirty_games()[0]["scan_uid"] == s.dirty_scans()[0]["uid"]


@pytest.mark.parametrize("raw", ["https://abc.supabase.co", "https://abc.supabase.co/", "https://abc.supabase.co/rest/v1",
                                 "https://abc.supabase.co/rest/v1/", "  https://abc.supabase.co/rest/v1/  "])
def test_url_pasted_from_the_dashboard_is_normalized(raw):
    assert cloud.normalize_url(raw) == "https://abc.supabase.co"
    assert Cloud(raw, KEY, "ana").base == "https://abc.supabase.co/rest/v1"  # sem /rest/v1 duplicado
