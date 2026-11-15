import json
import time

from cloud import CloudError
from test_app import call
from test_appmode import srv2  # noqa: F401  (fixture do pytest)
from test_bot import make_edge
from test_cloud import KEY, URL, fake  # noqa: F401  (fake e fixture do pytest)


def setup_cloud(port, user="ana"):
    assert call(port, "/api/keys", {"supabase_url": URL, "supabase_key": KEY})[0] == 200
    code, s = call(port, "/api/config", {"cloud_user": user})
    assert code == 200
    return s


def test_cloud_off_by_default_and_check_explains(srv2, fake):  # noqa: F811
    a, bot, port, *_ = srv2
    s = call(port, "/api/state")[1]
    assert s["cloud"]["configured"] is False and s["keys"]["supabase"] is False
    assert call(port, "/api/cloud/check", {})[1]["ok"] is False
    assert call(port, "/api/cloud/sync", {})[1]["cloud"]["configured"] is False  # nao quebra sem nuvem


def test_configure_cloud_never_returns_the_key(srv2, fake):  # noqa: F811
    a, bot, port, _, tmp = srv2
    s = setup_cloud(port)
    assert s["cloud"]["configured"] is True and s["cloud"]["user"] == "ana" and s["keys"]["supabase"] is True
    assert KEY not in json.dumps(call(port, "/api/state")[1])
    assert json.loads((tmp / "secrets.json").read_text())["supabase_key"] == KEY
    assert call(port, "/api/cloud/check", {})[1] == {"ok": True}


def test_invalid_cloud_settings_rejected(srv2, fake):  # noqa: F811
    a, bot, port, *_ = srv2
    assert call(port, "/api/keys", {"supabase_url": "http://x.supabase.co"})[0] == 400
    assert call(port, "/api/keys", {"supabase_key": "curta"})[0] == 400
    assert call(port, "/api/config", {"cloud_user": "a"})[0] == 400
    assert call(port, "/api/config", {"cloud_user": "nome com espaco"})[0] == 400


def test_manual_sync_uploads_and_reports(srv2, fake):  # noqa: F811
    a, bot, port, *_ = srv2
    setup_cloud(port)
    bot.store.add_alert(make_edge())
    s = call(port, "/api/cloud/sync", {})[1]
    assert s["cloud"]["ok"] is True
    assert len(fake.t["alerts"]) == 1


def test_deciding_sends_the_decision_to_the_cloud(srv2, fake):  # noqa: F811
    a, bot, port, *_ = srv2
    setup_cloud(port)
    aid = bot.store.add_alert(make_edge())
    assert call(port, "/api/alerts/decide", {"id": aid, "status": "bet"})[0] == 200
    for _ in range(60):  # envio em segundo plano
        if fake.t["decisions"]:
            break
        time.sleep(0.05)
    assert list(fake.t["decisions"].values())[0]["status"] == "bet"


def _wants_ctx_scanner(got):
    def scanner(c, skip=None, on_game=None, stats=None):
        got.append(set(skip or ()))
        stats.update(listed=0, skipped=0, scanned=0, blocked=0, queries=0)
        return []
    scanner.wants_ctx = True
    return scanner


def test_scan_skips_games_seen_by_the_other_person(srv2, fake):  # noqa: F811
    a, bot, port, *_ = srv2
    setup_cloud(port, "ana")
    fake.t["scan_games"][("u1", "fx-do-beto")] = {"scan_uid": "u1", "fixture_id": "fx-do-beto", "user_name": "beto",
                                                  "seen_at": "2026-11-15T10:00:00+00:00"}
    got = []
    bot.scanner = _wants_ctx_scanner(got)
    bot.scan()
    assert got[0] == {"fx-do-beto"}


def test_cloud_failure_never_blocks_a_scan_or_local_use(srv2, fake):  # noqa: F811
    a, bot, port, *_ = srv2
    setup_cloud(port)
    fake.fail = CloudError("sem conexao com a nuvem (timeout)")
    got = []
    bot.scanner = _wants_ctx_scanner(got)
    assert bot.scan()["sent"] == 0 and got  # o scan roda normalmente
    aid = bot.store.add_alert(make_edge())
    assert call(port, "/api/alerts/decide", {"id": aid, "status": "ignored"})[0] == 200  # decidir tambem
    s = call(port, "/api/cloud/sync", {})[1]
    assert s["cloud"]["ok"] is False and "sem conexao" in s["cloud"]["error"]
    fake.fail = None
    s = call(port, "/api/cloud/sync", {})[1]  # voltou a internet: recupera tudo que ficou pendente
    assert s["cloud"]["ok"] is True and any(r["status"] == "ignored" for r in fake.t["decisions"].values())


def test_rest_url_is_saved_normalized(srv2, fake):  # noqa: F811
    a, bot, port, _, tmp = srv2
    assert call(port, "/api/keys", {"supabase_url": URL + "/rest/v1/", "supabase_key": KEY})[0] == 200
    assert json.loads((tmp / "secrets.json").read_text())["supabase_url"] == URL
