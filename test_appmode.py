import dataclasses
import io
import json
import re
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import pytest

import app
from bot import Bot, fmt_time
from store import Store
from test_app import base_cfg, call
from test_bot import FakeTg, make_edge


@pytest.fixture
def srv2(tmp_path, monkeypatch):
    # isola variaveis de ambiente e o arquivo de chaves (nada vaza para o computador real)
    monkeypatch.setenv("ODDSPAPI_KEY", "placeholder")
    monkeypatch.delenv("ODDSPAPI_KEY")
    cfg_path = tmp_path / "config.json"
    cfg = base_cfg()
    cfg_path.write_text(json.dumps(cfg))
    (tmp_path / "papi").mkdir()
    edges = []
    bot = Bot(FakeTg(), Store(str(tmp_path / "t.db")), cfg, scanner=lambda c: edges, cfg_path=str(cfg_path))
    bot.store.set("chat_id", "111")
    a = app.App(bot, "tok", cache_dir=str(tmp_path / "papi"), secrets_path=str(tmp_path / "secrets.json"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(a))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield a, bot, server.server_address[1], edges, tmp_path
    server.shutdown()
    server.server_close()


def wait_scan(bot, n=60):
    for _ in range(n):
        if bot.last_result and not bot.lock.locked():
            return
        time.sleep(0.05)


# ---- chaves dentro do app ----
def test_keys_saved_never_returned_and_applied(srv2, monkeypatch):
    a, bot, port, _, tmp = srv2
    monkeypatch.setattr(app.App, "start_polling", lambda self: None)  # sem rede
    bot.tg = None  # o app pode rodar sem Telegram
    secret = "abcdefgh-1234-5678"
    assert call(port, "/api/state")[1]["keys"] == {"oddspapi": False, "telegram": False}
    code, s = call(port, "/api/keys", {"oddspapi_key": secret})
    assert code == 200 and s["keys"]["oddspapi"] is True
    assert secret not in json.dumps(call(port, "/api/state")[1])  # a API nunca devolve a chave
    assert app.os.environ["ODDSPAPI_KEY"] == secret
    assert json.loads((tmp / "secrets.json").read_text())["oddspapi_key"] == secret
    token = "123456789:" + "A" * 35
    code, s = call(port, "/api/keys", {"telegram_token": token})
    assert code == 200 and s["keys"]["telegram"] is True and bot.tg is not None
    assert token not in json.dumps(s)
    code, s = call(port, "/api/keys", {"oddspapi_key": ""})  # string vazia = apagar
    assert s["keys"]["oddspapi"] is False and "oddspapi_key" not in json.loads((tmp / "secrets.json").read_text())


def test_keys_validation(srv2):
    a, bot, port, *_ = srv2
    assert call(port, "/api/keys", {"oddspapi_key": "curta"})[0] == 400
    assert call(port, "/api/keys", {"oddspapi_key": "tem espaco e ;"})[0] == 400
    assert call(port, "/api/keys", {"telegram_token": "nao-e-token"})[0] == 400
    assert call(port, "/api/keys", {"outra": "x"})[0] == 400
    assert call(port, "/api/keys", {"oddspapi_key": "abcdefgh1234"}, token="errado")[0] == 403


def test_build_reads_saved_key_without_overriding_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(base_cfg()))
    app.save_keys({"oddspapi_key": "doarquivo-12345"})
    monkeypatch.setenv("ODDSPAPI_KEY", "doambiente-12345")
    a = app.build()
    assert app.os.environ["ODDSPAPI_KEY"] == "doambiente-12345"  # variavel de ambiente vence
    a.bot.stop.set()
    monkeypatch.delenv("ODDSPAPI_KEY")
    a2 = app.build()
    assert app.os.environ["ODDSPAPI_KEY"] == "doarquivo-12345"
    a2.bot.stop.set()
    monkeypatch.delenv("ODDSPAPI_KEY")


# ---- scan ao abrir + trava de orcamento ----
def test_config_and_keys_saved_with_bom_still_load(tmp_path, monkeypatch):
    """Notepad/PowerShell gravam JSON com BOM; isso ja derrubou o app ao abrir e nao pode se repetir."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(base_cfg()).encode("utf-8"))
    (tmp_path / "secrets.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"oddspapi_key": "dobom-12345678"}).encode())
    monkeypatch.setenv("ODDSPAPI_KEY", "placeholder")
    monkeypatch.delenv("ODDSPAPI_KEY")
    a = app.build()
    try:
        assert a.bot.cfg["bankroll"] == base_cfg()["bankroll"]
        assert app.os.environ["ODDSPAPI_KEY"] == "dobom-12345678"
    finally:
        a.bot.stop.set()
        monkeypatch.delenv("ODDSPAPI_KEY", raising=False)


def test_scan_blocked_without_key_when_required(srv2):
    a, *_ = srv2
    a.key_required = True
    assert a.start_scan() == {"started": False, "reason": "nokey"}


def test_scan_is_never_blocked_by_a_local_counter(srv2):
    a, bot, port, edges, tmp = srv2
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    (tmp / "papi" / ".budget.json").write_text(json.dumps({month: 200}))  # contador antigo "estourado"
    assert a.start_scan() == {"started": True}  # antes: recusava com "limite mensal de 200 consultas atingido"
    wait_scan(bot)
    assert bot.last_result is not None and "error" not in bot.last_result
    assert "limit" not in call(port, "/api/state")[1]["budget"]  # a tela nao inventa um teto


def test_counter_is_per_key_so_a_new_key_starts_from_zero(tmp_path, monkeypatch):
    import oddspapi
    d = tmp_path / "papi"
    d.mkdir()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    (d / ".budget.json").write_text(json.dumps({month: 200}))  # formato antigo: nao pertence a chave nenhuma
    assert oddspapi.budget_used(str(d), "chave-nova-12345678") == 0
    a = oddspapi.Client("chave-A-12345678", cache_dir=str(d), min_interval=0)
    b = oddspapi.Client("chave-B-12345678", cache_dir=str(d), min_interval=0)
    for _ in range(3):
        a._count()
    b._count()
    assert oddspapi.budget_used(str(d), "chave-A-12345678") == 3 and a.used_this_month() == 3
    assert oddspapi.budget_used(str(d), "chave-B-12345678") == 1
    for _ in range(300):  # contagem enorme nunca bloqueia: o cliente continua fazendo a consulta
        a._count()

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

    monkeypatch.setattr(oddspapi.urllib.request, "urlopen", lambda url, timeout=0: Resp(b"[]"))
    assert a.fixtures(1) == [] and a.session_requests >= 300


def test_network_failure_is_a_normal_error_not_a_crash(tmp_path, monkeypatch):
    import oddspapi
    c = oddspapi.Client("chave-12345678", cache_dir=str(tmp_path), min_interval=0)

    def boom(url, timeout=0):
        raise oddspapi.urllib.error.URLError("sem internet")

    monkeypatch.setattr(oddspapi.urllib.request, "urlopen", boom)
    with pytest.raises(oddspapi.OddsPapiError, match="falha de rede"):
        c.odds("fx", ["pinnacle"])


def test_scan_only_starts_when_asked(srv2):
    a, bot, port, *_ = srv2
    assert bot.last_result is None  # abrir o app nao varre nada
    assert call(port, "/api/config", {"scan_on_open": True})[0] == 400  # a opcao de varrer ao abrir nao existe mais
    assert a.start_scan()["started"] is True
    wait_scan(bot)
    assert bot.last_result is not None


# ---- expirados: a data certa (UTC), sem depender do fuso do computador ----
def test_has_started_boundaries():
    row = {"start_time": "2026-11-12T19:45:00.000Z"}
    utc = timezone.utc
    assert app.has_started(row, datetime(2026, 11, 12, 19, 44, 59, tzinfo=utc)) is False  # 1s antes: ainda vale
    assert app.has_started(row, datetime(2026, 11, 12, 19, 45, 0, tzinfo=utc)) is True
    assert app.has_started({"start_time": "2026-11-12T19:45:00"}, datetime(2026, 11, 12, 20, 0, tzinfo=utc)) is True
    assert app.has_started({"start_time": None}) is False and app.has_started({"start_time": "lixo"}) is False


def test_expired_tab_separates_started_games(srv2):
    a, bot, port, *_ = srv2
    old = bot.store.add_alert(dataclasses.replace(make_edge(market_id="1"), start_time="2020-01-01T10:00:00.000Z"))
    new = bot.store.add_alert(dataclasses.replace(make_edge(market_id="2"), start_time="2999-01-01T10:00:00.000Z"))
    ids = lambda q: [x["id"] for x in call(port, "/api/alerts?status=" + q)[1]["alerts"]]  # noqa: E731
    assert ids("pending") == [new] and ids("expired") == [old] and sorted(ids("all")) == sorted([old, new])
    flags = {x["id"]: x["expired"] for x in call(port, "/api/alerts?status=all")[1]["alerts"]}
    assert flags == {old: True, new: False}
    assert call(port, "/api/state")[1]["counts"]["expired"] == 1
    assert call(port, "/api/state")[1]["counts"]["pending"] == 1


# ---- sair ----
def test_quit_endpoint(srv2):
    a, *rest = srv2
    port = rest[1]
    assert call(port, "/api/quit", {})[1] == {"closing": False}  # no navegador nao fecha nada
    called = threading.Event()
    a.on_quit = called.set
    assert call(port, "/api/quit", {})[1] == {"closing": True}
    assert called.wait(2)


# ---- horario local ----
def test_fmt_time_shows_local_and_utc():
    t = fmt_time("2026-11-12T19:45:00.000Z")
    assert t.endswith("(2026-11-12 19:45 UTC)") and re.match(r"^\d\d/\d\d \d\d:\d\d ", t)
    assert fmt_time(None) == "?" and fmt_time("lixo").endswith("UTC")
