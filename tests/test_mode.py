"""Settings -> Trading mode: switching the whole terminal between paper and real trading, and every refusal on the way."""
import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from test_accounts import PW, FakeCatalog, FakeFeed, acct
from terminal import services as services_mod
from terminal.config import load_config
from terminal.services import Services
from terminal.trading.models import OrderRow, PositionRow
from terminal.trading.sim import SimBroker
from terminal.web.server import create_app


def fake_kotak(svc, monkeypatch):
    """Replace everything that would talk to Kotak (login, price feed, scrip master) with harmless fakes."""
    import terminal.brokers.kotak as kotak
    import terminal.market.catalog as catalog
    monkeypatch.setattr(kotak, "KotakFeed", FakeFeed)
    monkeypatch.setattr(catalog, "KotakCatalog", FakeCatalog)
    monkeypatch.setattr(services_mod, "KotakChainProvider", lambda s, c: object())

    def login(totp=None):
        svc.session.my_ip = "203.0.113.7"
        svc.session.logged_in = True
    if svc.session is not None:
        svc.session.login = login
        svc.session.logout = lambda: setattr(svc.session, "logged_in", False)
    svc._kotak_ins = object()
    svc._fake_login = login


def make(tmp_path, monkeypatch, mode="paper", env=None, host=None):
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = mode, str(tmp_path / "data")
    if host:
        cfg.host = host
    svc = Services(cfg, env={"ALLOW_LIVE": "yes"} if env is None else env)
    svc.vault.unlock(PW)
    fake_kotak(svc, monkeypatch)
    aid = svc.vault.add(acct())
    if mode == "sample":
        svc.start()
    return svc, aid


class FakeLive:
    """Stands in for KotakGateway: what the broker side currently holds."""
    def __init__(self, *a, net=0, orders=(), none=False):
        self.net, self._orders, self.none = net, list(orders), none

    def positions(self):
        if self.none:
            return None
        return [PositionRow(key="nse_fo|1", symbol="X", label="X", segment="nse_fo", product="MIS", lot=1, buy_qty=0, sell_qty=0,
                            net_qty=self.net, buy_avg=0, sell_avg=0, avg_price=0, ltp=1, realised=0, unrealised=0)] if self.net else []

    def orders(self):
        return None if self.none else self._orders


@pytest.fixture
def paper(tmp_path, monkeypatch):
    svc, aid = make(tmp_path, monkeypatch)
    yield svc, aid
    svc.shutdown()


def fake_live(monkeypatch, **kw):
    import terminal.trading.kotak_gateway as kg
    holder = {}
    monkeypatch.setattr(kg, "KotakGateway", lambda *a: holder.setdefault("g", FakeLive(**kw)))
    return holder


def test_leaving_the_sample_demo_forgets_every_invented_thing_and_waits_for_the_login(tmp_path, monkeypatch):
    svc, aid = make(tmp_path, monkeypatch, mode="sample")
    try:
        assert svc.connected and svc.mode == "sample" and svc.mode_info()["can"] == {"paper": True, "live": True}
        wl = svc.store.watchlists()[0]["id"]
        svc.store.add_item(wl, "nse_fo|99001", {"label": "invented option"})
        svc.charts.watch("nse_cm|2885")
        deadline = __import__("time").time() + 5
        while svc.hub.ltp("nse_cm|2885") is None and __import__("time").time() < deadline:
            __import__("time").sleep(0.1)
        svc.charts.candles("nse_cm|2885", "5")                                   # lays down invented sample candles
        assert svc.store.candles("sample:nse_cm|2885") and not svc.store.candles("nse_cm|2885")
        keys_before = {i["key"] for i in svc.store.watchlists()[0]["items"]}
        feed = svc.feed
        svc.set_mode("paper")
        assert svc.mode == svc.cfg.mode == svc.alerts.mode == svc.journal.mode == "paper"
        assert svc.needs_connect and not svc.connected and svc.gateway is None and svc.account is None
        assert feed._stop.is_set() and svc.feed is None
        assert svc.hub.wanted() == [] and svc.hub.get("nse_cm|2885") is None and svc.hub.tick_count == 0
        assert svc.catalog.inner is None and svc.chain.provider is None and svc.charts.sample is False
        assert svc.session is not None and svc.algo.session is svc.session and svc.recorder is not None and svc.bt_src == "market"
        items = {i["key"] for i in svc.store.watchlists()[0]["items"]}
        assert "nse_fo|99001" not in items and items == keys_before - {"nse_fo|99001"}      # real shares and indices stay
        assert svc.charts.recent() == []
        assert not svc.store.candles("nse_cm|2885")                              # sample candles never became "real" history
        fake_kotak(svc, monkeypatch)
        svc.connect("123456", aid)                                               # the normal login now works in paper mode
        assert svc.connected and isinstance(svc.gateway, SimBroker) and svc.journal.ns == "paper:AB123"
        assert svc.charts.sample is False and svc.mode == "paper"
    finally:
        svc.shutdown()


def test_the_demo_can_go_straight_to_real_only_with_the_lock_and_the_typed_word(tmp_path, monkeypatch):
    svc, aid = make(tmp_path, monkeypatch, mode="sample", env={})
    try:
        with pytest.raises(RuntimeError, match="ALLOW_LIVE"):
            svc.set_mode("live", "REAL")
        assert svc.mode == "sample" and svc.connected
    finally:
        svc.shutdown()
    svc, aid = make(tmp_path / "b", monkeypatch, mode="sample")
    try:
        with pytest.raises(RuntimeError, match="Type REAL"):
            svc.set_mode("live", "")
        assert svc.mode == "sample" and svc.connected and svc.feed is not None
        svc.set_mode("live", "real")
        assert svc.mode == "live" and not svc.connected
        fake_kotak(svc, monkeypatch)
        with pytest.raises(RuntimeError, match="LIVE mode.*confirm"):
            svc.connect("123456", aid)                                           # the login still asks its own confirmation
    finally:
        svc.shutdown()


def test_old_sample_history_is_moved_aside_but_real_history_is_left_alone(tmp_path):
    from terminal.store import Store
    st = Store(tmp_path / "a.db")
    st.put_candles([("nse_cm|2885", 100, 1, 1, 1, 1, 1)])
    st.add_oi_point("2026-11-02", "NIFTY", "2026-11-03", 1.0, 1, 1, 1, 1, 1)
    st.retag_sample_history()
    assert st.candles("sample:nse_cm|2885") and not st.candles("nse_cm|2885")
    assert st.oi_series("2026-11-02", "SAMPLE:NIFTY", "2026-11-03") and not st.oi_series("2026-11-02", "NIFTY", "2026-11-03")
    st2 = Store(tmp_path / "b.db")
    st2.set("accounts.last", "x")                                               # a broker was connected here once: cannot tell, so leave it
    st2.put_candles([("nse_cm|2885", 100, 1, 1, 1, 1, 1)])
    st2.retag_sample_history()
    assert st2.candles("nse_cm|2885")


def test_real_trading_stays_locked_without_the_env_flag(tmp_path, monkeypatch):
    svc, aid = make(tmp_path, monkeypatch, env={})
    try:
        info = svc.mode_info()
        assert info["can"] == {"paper": False, "live": False} and "ALLOW_LIVE=yes" in info["why"]["live"]
        with pytest.raises(RuntimeError, match="ALLOW_LIVE"):
            svc.set_mode("live", "REAL")
        assert svc.mode == "paper"
    finally:
        svc.shutdown()


def test_real_trading_is_refused_when_the_terminal_is_exposed_on_a_network(tmp_path, monkeypatch):
    svc, aid = make(tmp_path, monkeypatch, host="0.0.0.0")
    try:
        with pytest.raises(RuntimeError, match="this computer only"):
            svc.set_mode("live", "REAL")
    finally:
        svc.shutdown()


def test_real_needs_the_typed_word_and_a_wrong_mode_name_is_refused(paper):
    svc, aid = paper
    for bad in ("", "yes", "real trading"):
        with pytest.raises(RuntimeError, match="Type REAL"):
            svc.set_mode("live", bad)
    assert svc.mode == "paper" and svc.cfg.mode == "paper"
    with pytest.raises(RuntimeError, match="paper or real"):
        svc.set_mode("sample")
    with pytest.raises(RuntimeError, match="paper or real"):
        svc.set_mode("replay", "REAL")


def test_switching_before_login_changes_everything_that_reads_the_mode(paper):
    svc, aid = paper
    svc.set_mode("live", " real ")
    assert svc.mode == svc.cfg.mode == svc.alerts.mode == svc.journal.mode == "live"
    assert svc.mode_info()["can"] == {"paper": True, "live": False}
    svc.set_mode("paper")
    assert svc.mode == svc.cfg.mode == svc.alerts.mode == svc.journal.mode == "paper"
    assert svc.set_mode("paper")["mode"] == "paper"                                 # choosing the same mode again is harmless


def test_hot_switch_while_logged_in_swaps_the_broker_and_keeps_journals_apart(paper, monkeypatch):
    svc, aid = paper
    svc.connect("123456", aid)
    assert isinstance(svc.gateway, SimBroker) and svc.journal.ns == "paper:AB123"
    holder = fake_live(monkeypatch)
    svc.set_mode("live", "REAL")
    assert svc.gateway is holder["g"] and svc.mode == "live" and svc.journal.ns == "live:AB123" and svc.connected
    svc.set_mode("paper")
    assert isinstance(svc.gateway, SimBroker) and svc.journal.ns == "paper:AB123" and svc.connected


def test_going_real_checks_the_static_ip_and_changes_nothing_when_it_fails(paper, monkeypatch):
    svc, aid = paper
    svc.vault.update(aid, {"static_ip": "198.51.100.9"})
    svc.connect("123456", aid)
    before = svc.gateway
    fake_live(monkeypatch)
    with pytest.raises(RuntimeError, match="198.51.100.9"):
        svc.set_mode("live", "REAL")
    assert svc.mode == "paper" and svc.cfg.mode == "paper" and svc.gateway is before and svc.journal.ns == "paper:AB123"


@pytest.mark.parametrize("kw,why", [({"net": -75}, "open real positions"),
                                    ({"orders": [OrderRow(id="1", key="k", symbol="s", label="l", side="B", qty=1, filled=0, lot=1, product="MIS",
                                                          order_type="L", price=1, trigger=0, avg_price=0, status="OPEN")]}, "open real positions"),
                                    ({"none": True}, "open real positions")])
def test_leaving_real_is_refused_while_anything_is_open_or_unknown(paper, monkeypatch, kw, why):
    svc, aid = paper
    svc.connect("123456", aid)
    holder = fake_live(monkeypatch, **kw)
    svc.set_mode("live", "REAL")
    assert not svc.mode_info()["can"]["paper"]
    with pytest.raises(RuntimeError, match=why):
        svc.set_mode("paper")
    assert svc.mode == "live" and svc.gateway is holder["g"]                         # still real, still showing the real account
    holder["g"].net, holder["g"]._orders, holder["g"].none = 0, [], False             # flat now
    svc.set_mode("paper")
    assert svc.mode == "paper"


def test_running_strategies_or_the_algo_block_any_switch(paper, monkeypatch):
    svc, aid = paper
    monkeypatch.setattr(svc.rules, "running", lambda: [7])
    info = svc.mode_info()
    assert info["can"] == {"paper": False, "live": False}
    with pytest.raises(RuntimeError, match="Stop your running strategies"):
        svc.set_mode("live", "REAL")


def test_api_get_and_post_need_login_and_the_csrf_header(paper, monkeypatch):
    svc, aid = paper
    app = create_app(svc, PW)
    anon = TestClient(app)
    assert anon.get("/api/mode").status_code == 401
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"password": PW}, headers={"x-terminal": "1"}).status_code == 200
    assert c.post("/api/mode", json={"mode": "live", "confirm": "REAL"}).status_code in (401, 403)     # no CSRF header
    c.headers["x-terminal"] = "1"
    j = c.get("/api/mode").json()
    assert j["mode"] == "paper" and j["can"]["live"] is True and j["started_as"] == "paper"
    r = c.post("/api/mode", json={"mode": "live", "confirm": ""})
    assert r.status_code == 400 and "Type REAL" in r.json()["error"]
    assert c.get("/api/session").json()["mode"] == "paper"
    r = c.post("/api/mode", json={"mode": "live", "confirm": "REAL"})
    assert r.status_code == 200 and r.json()["mode"] == "live"
    assert c.get("/api/session").json()["mode"] == "live"
    assert c.post("/api/mode", json={"mode": "paper"}).json()["mode"] == "paper"
    assert c.post("/api/mode", json={"mode": "sample"}).status_code == 400
    assert c.post("/api/mode", json={"mode": "x" * 40}).status_code == 422
