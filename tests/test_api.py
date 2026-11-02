"""The whole HTTP/WebSocket API on the sample world: login security, search, watchlists, manual trading, risk."""
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import ROOT
from terminal.config import load_config
from terminal.services import Services
from terminal.web.server import create_app

PW = "correct-horse-battery"


@pytest.fixture
def env(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = "sample", str(tmp_path / "data")
    svc = Services(cfg)
    svc.start()
    app = create_app(svc, PW)
    c = TestClient(app)
    yield svc, c
    svc.shutdown()


def login(c):
    r = c.post("/api/auth/login", json={"password": PW}, headers={"x-terminal": "1"})
    assert r.status_code == 200
    c.headers["x-terminal"] = "1"


def wait_price(svc, key, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if svc.hub.ltp(key) is not None:
            return svc.hub.ltp(key)
        time.sleep(0.05)
    raise AssertionError(f"no price for {key}")


def first(svc, q, kind=None):
    res = [i for i in svc.catalog.search(q, 40) if kind is None or i.kind == kind]
    assert res, q
    return res[0]


# ----------------------------------------------------------------------------- security
def test_everything_needs_login_and_the_csrf_header(env):
    svc, c = env
    for path in ("/api/session", "/api/orders", "/api/positions", "/api/watchlists", "/api/search?q=nifty", "/api/algo"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/orders", json={}).status_code in (401, 403, 422)
    bad = c.post("/api/auth/login", json={"password": "nope"}, headers={"x-terminal": "1"})
    assert bad.status_code == 401
    assert c.post("/api/auth/login", json={"password": PW}).status_code == 403          # no X-Terminal header
    login(c)
    assert c.get("/api/session").status_code == 200
    h = dict(c.headers)
    c.headers.pop("x-terminal")
    assert c.post("/api/panic").status_code == 403                                      # logged in but no header
    c.headers["x-terminal"] = "1"
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/session").status_code == 401


def test_login_is_rate_limited(env):
    svc, c = env
    codes = [c.post("/api/auth/login", json={"password": f"wrong-{i}"}, headers={"x-terminal": "1"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert c.post("/api/auth/login", json={"password": PW}, headers={"x-terminal": "1"}).status_code == 429


def test_security_headers_present(env):
    svc, c = env
    r = c.get("/api/auth/status")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------------------- session / data
def test_session_search_and_quotes(env):
    svc, c = env
    login(c)
    s = c.get("/api/session").json()
    assert s["mode"] == "sample" and s["connected"] and len(s["indices"]) == 6
    res = c.get("/api/search", params={"q": "RELI"}).json()["results"]
    assert res and res[0]["name"] == "RELIANCE" and res[0]["kind"] == "EQ"
    opt = c.get("/api/search", params={"q": "NIFTY 24250 CE"}).json()["results"]
    assert opt and all(o["kind"] == "CE" and o["strike"] == 24250 for o in opt) and opt[0]["lot"] == 75
    key = res[0]["key"]
    svc.hub.want("test", [key])
    wait_price(svc, key)
    q = c.get("/api/quotes", params={"keys": key}).json()["quotes"]
    assert q and q[0]["k"] == key and q[0]["l"] > 0 and q[0]["pc"] > 0
    assert c.get("/api/quotes", params={"keys": "bad key;drop"}).json() == {"quotes": []}


def test_websocket_streams_only_subscribed_keys_and_checks_origin(env):
    svc, c = env
    login(c)
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws", headers={"origin": "http://evil.example"}):
            pass
    key = first(svc, "RELIANCE").key
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"op": "sub", "keys": [key]})
        msg = ws.receive_json()
        assert msg["t"] == "q" and {q["k"] for q in msg["d"]} == {key}
    anon = TestClient(c.app)
    with pytest.raises(WebSocketDisconnect):
        with anon.websocket_connect("/ws"):
            pass


# ----------------------------------------------------------------------------- watchlists
def test_watchlist_crud_and_default_list(env):
    svc, c = env
    login(c)
    lists = c.get("/api/watchlists").json()["lists"]
    assert len(lists) == 1 and len(lists[0]["items"]) >= 5
    wl = c.post("/api/watchlists", json={"name": "Options"}).json()["id"]
    inst = first(svc, "NIFTY 24250 CE")
    assert c.post(f"/api/watchlists/{wl}/items", json={"key": inst.key}).json()["ok"]
    assert c.post(f"/api/watchlists/{wl}/items", json={"key": "nse_fo|999999999"}).status_code == 400
    items = [l for l in c.get("/api/watchlists").json()["lists"] if l["id"] == wl][0]["items"]
    assert items[0]["key"] == inst.key and items[0]["lot"] == 75 and items[0]["kind"] == "CE"
    assert c.delete(f"/api/watchlists/{wl}/items", params={"key": inst.key}).json()["ok"]
    assert c.patch(f"/api/watchlists/{wl}", json={"name": "Renamed"}).json()["ok"]
    assert c.delete(f"/api/watchlists/{wl}").json()["ok"]


# ------------------------------------------------------------------------------- trading
def order(c, key, side, **kw):
    body = {"key": key, "side": side, "product": "MIS", "order_type": "MKT", **kw}
    return c.post("/api/orders", json=body)


def test_buy_then_sell_gives_position_orders_trades_and_pnl(env):
    svc, c = env
    login(c)
    eq = first(svc, "RELIANCE")
    svc.hub.want("test", [eq.key])
    wait_price(svc, eq.key)
    r = order(c, eq.key, "B", qty=10).json()
    assert r["ok"] and r["qty"] == 10
    pos = c.get("/api/positions").json()
    assert pos["ok"] and pos["rows"][0]["net_qty"] == 10 and pos["totals"]["open"] == 1
    assert order(c, eq.key, "S", qty=10).json()["ok"]
    pos = c.get("/api/positions").json()
    assert pos["rows"][0]["net_qty"] == 0 and pos["totals"]["open"] == 0
    orders = c.get("/api/orders").json()["rows"]
    trades = c.get("/api/trades").json()["rows"]
    assert len(orders) == 2 and all(o["status"] == "COMPLETE" for o in orders) and len(trades) == 2
    f = c.get("/api/funds").json()
    assert f["ok"] and f["funds"]["simulated"]


def test_option_orders_use_lots_and_server_side_lot_size(env):
    svc, c = env
    login(c)
    opt = first(svc, "NIFTY 24250 PE")
    svc.hub.want("test", [opt.key])
    px = wait_price(svc, opt.key)
    r = order(c, opt.key, "S", lots=2, product="NRML").json()
    assert r["ok"] and r["qty"] == 150                                  # 2 lots x 75, resolved on the server
    row = c.get("/api/positions").json()["rows"][0]
    assert row["net_qty"] == -150 and row["lot"] == 75 and row["sell_avg"] > 0


def test_ticket_validation(env):
    svc, c = env
    login(c)
    eq, idx = first(svc, "RELIANCE"), first(svc, "NIFTY 50", "INDEX")
    opt = first(svc, "NIFTY 24250 CE")
    wait_price(svc, eq.key) if svc.hub.want("t", [eq.key]) is None else None
    assert "Indices cannot be traded" in order(c, idx.key, "B", qty=1).json()["error"]
    assert "Unknown instrument" in order(c, "nse_cm|99999999", "B", qty=1).json()["error"]
    assert "at least 1 lot" in order(c, opt.key, "B", lots=0, product="NRML").json()["error"]
    assert "Max 20 lots" in order(c, opt.key, "B", lots=21, product="NRML").json()["error"]
    assert "Product must be" in order(c, opt.key, "B", lots=1, product="CNC").json()["error"]
    assert "Product must be" in order(c, eq.key, "B", qty=1, product="NRML").json()["error"]
    assert "tick size" in order(c, eq.key, "B", qty=1, order_type="L", price=100.03).json()["error"]
    assert "limit price" in order(c, eq.key, "B", qty=1, order_type="L", price=0).json()["error"]
    assert "above the per-order limit" in order(c, eq.key, "B", qty=100000).json()["error"]
    assert order(c, eq.key, "X", qty=1).status_code == 422                # malformed body is refused by the schema


def test_double_click_with_same_client_id_places_one_order(env):
    svc, c = env
    login(c)
    eq = first(svc, "RELIANCE")
    svc.hub.want("t", [eq.key])
    wait_price(svc, eq.key)
    a = order(c, eq.key, "B", qty=1, client_id="abc-123").json()
    b = order(c, eq.key, "B", qty=1, client_id="abc-123").json()
    assert a == b and len(c.get("/api/orders").json()["rows"]) == 1


def test_resting_limit_order_can_be_modified_and_cancelled(env):
    svc, c = env
    login(c)
    eq = first(svc, "RELIANCE")
    svc.hub.want("t", [eq.key])
    px = wait_price(svc, eq.key)
    far = round(round(px * 0.8 / 0.05) * 0.05, 2)                        # a buy limit far below the market: must rest
    oid = order(c, eq.key, "B", qty=5, order_type="L", price=far).json()["order_id"]
    row = c.get("/api/orders").json()["rows"][0]
    assert row["status"] == "OPEN" and row["open"]
    r = c.put(f"/api/orders/{oid}", json={"order_type": "L", "qty": 7, "price": far, "validity": "DAY"}).json()
    assert r["ok"] and c.get("/api/orders").json()["rows"][0]["qty"] == 7
    assert c.delete(f"/api/orders/{oid}").json()["ok"]
    assert c.get("/api/orders").json()["rows"][0]["status"] == "CANCELLED"
    assert "already" in c.delete(f"/api/orders/{oid}").json()["error"]


def test_daily_loss_limit_blocks_new_exposure_but_not_closing(env):
    svc, c = env
    login(c)
    svc.cfg.risk.max_daily_loss = 100.0
    eq = first(svc, "RELIANCE")
    svc.hub.want("t", [eq.key])
    wait_price(svc, eq.key)
    assert order(c, eq.key, "B", qty=100).json()["ok"]
    # simulate a market crash: the position loses far more than the 100 limit
    q = svc.hub.get(eq.key)
    from terminal.market.models import Quote
    svc.feed.stop()
    time.sleep(0.4)
    svc.hub.update(Quote(eq.key, round(q.ltp * 0.9, 2)))
    blocked = order(c, eq.key, "B", qty=1).json()
    assert not blocked["ok"] and "Daily loss limit" in blocked["error"]
    assert order(c, eq.key, "S", qty=100).json()["ok"]                    # closing is always allowed


def test_squareoff_preview_squareoff_and_panic(env):
    svc, c = env
    login(c)
    eq, opt = first(svc, "RELIANCE"), first(svc, "NIFTY 24250 CE")
    svc.hub.want("t", [eq.key, opt.key])
    wait_price(svc, eq.key)
    wait_price(svc, opt.key)
    order(c, eq.key, "B", qty=10)
    order(c, opt.key, "S", lots=1, product="NRML")
    pv = c.post("/api/squareoff/preview", json={}).json()["orders"]
    assert {(p["key"], p["side"]) for p in pv} == {(eq.key, "S"), (opt.key, "B")}
    assert all(p["price"] > 0 for p in pv)
    r = c.post("/api/squareoff", json={"keys": [eq.key]}).json()
    assert r["ok"] and r["results"][0]["ok"]
    rows = {p["key"]: p for p in c.get("/api/positions").json()["rows"]}
    assert rows[eq.key]["net_qty"] == 0 and rows[opt.key]["net_qty"] == -75
    far = round(round(svc.hub.ltp(eq.key) * 0.8 / 0.05) * 0.05, 2)
    order(c, eq.key, "B", qty=3, order_type="L", price=far)                # a resting order that panic must cancel
    p = c.post("/api/panic").json()
    assert p["ok"] and p["cancelled"] == 1
    assert all(r["net_qty"] == 0 for r in c.get("/api/positions").json()["rows"])


def test_algo_demo_day_runs_through_the_real_engine(env):
    svc, c = env
    login(c)
    assert c.post("/api/algo/start").json()["ok"]
    end = time.time() + 60
    while time.time() < end and not svc.algo.engine.finished.is_set():
        time.sleep(0.2)
    snap = c.get("/api/algo").json()
    assert snap["finished"] and snap["strategy"]["trades"] and snap["strategy"]["trades"][0]["side"] == "PE"


# ------------------------------------------------------------------- strategies + journal (release 3)
def _fixed_morning(svc):
    from datetime import datetime
    from terminal.clock import IST, now_ist
    d = now_ist().date()
    svc.rules.market.now = lambda: datetime(d.year, d.month, d.day, 9, 10, tzinfo=IST)


def _spec():
    from test_rules import tmpl
    return tmpl("straddle")


def test_strategy_endpoints_need_login_and_header(env):
    svc, c = env
    assert c.get("/api/strategies").status_code == 401
    assert c.get("/api/journal").status_code == 401
    login(c)
    h = c.headers.pop("x-terminal")
    assert c.post("/api/strategies", json={"spec": _spec()}).status_code == 403
    assert c.post("/api/strategies/1/start", json={}).status_code == 403
    assert c.put("/api/journal/notes/day:2026-10-26", json={"body": "x"}).status_code == 403
    c.headers["x-terminal"] = h


def test_strategy_crud_validate_and_plain_english(env):
    svc, c = env
    login(c)
    t = c.get("/api/strategy-templates").json()["templates"]
    assert {x["id"] for x in t} >= {"orb", "straddle", "blank"}
    bad = c.post("/api/strategies/validate", json={"spec": {"name": "", "entries": []}}).json()
    assert bad["ok"] is False and bad["errors"]
    good = c.post("/api/strategies/validate", json={"spec": _spec()}).json()
    assert good["ok"] and any("SELL the Call" in l for l in good["summary"]) and good["warnings"] is not None
    assert c.post("/api/strategies", json={"spec": {"name": "x"}}).json()["ok"] is False
    sid = c.post("/api/strategies", json={"spec": _spec()}).json()["id"]
    rows = c.get("/api/strategies").json()["strategies"]
    assert rows[0]["id"] == sid and rows[0]["valid"] and not rows[0]["running"]
    s2 = _spec()
    s2["name"] = "Renamed"
    assert c.put(f"/api/strategies/{sid}", json={"spec": s2}).json()["ok"]
    assert c.get(f"/api/strategies/{sid}").json()["strategy"]["name"] == "Renamed"
    assert c.get("/api/strategies/999").status_code == 404
    assert c.delete(f"/api/strategies/{sid}").json()["ok"]
    assert c.get("/api/strategies").json()["strategies"] == []


def test_strategy_lifecycle_and_stop_all(env):
    svc, c = env
    login(c)
    _fixed_morning(svc)
    sid = c.post("/api/strategies", json={"spec": _spec()}).json()["id"]
    r = c.post(f"/api/strategies/{sid}/start", json={}).json()
    assert r["ok"], r
    st = c.get(f"/api/strategies/{sid}/state").json()
    assert st["running"] and st["state"]["status"] in ("starting", "waiting")
    again = c.post(f"/api/strategies/{sid}/start", json={})
    assert again.status_code == 400 and "already running" in again.json()["error"]
    assert "Stop the strategy" in c.delete(f"/api/strategies/{sid}").json()["error"]
    assert "Stop the strategy" in c.put(f"/api/strategies/{sid}", json={"spec": _spec()}).json()["error"]
    assert c.post(f"/api/strategies/{sid}/pause", json={"paused": True}).json()["ok"]
    assert c.post("/api/panic").json()["ok"]
    state = c.get(f"/api/strategies/{sid}/state").json()
    assert state["state"]["status"] == "halted" and not state["running"] and "Stop-all" in state["state"]["halt"]
    assert c.post(f"/api/strategies/{sid}/pause", json={"paused": False}).status_code == 400      # not running any more


def test_journal_demo_notes_and_filters(env):
    svc, c = env
    login(c)
    j = c.get("/api/journal").json()
    assert j["ok"] and j["stats"]["n"] == 0
    assert c.post("/api/journal/demo").json()["added"] > 20
    j = c.get("/api/journal").json()
    assert j["stats"]["n"] > 10 and j["demo"] and len(j["equity"]) == j["stats"]["n"] and j["by_day"]
    first = j["trips"][0]
    assert c.put(f"/api/journal/notes/trip:{first['id']}", json={"body": "chased it", "tags": ["fomo"]}).json()["ok"]
    assert c.get("/api/journal").json()["trips"][0]["note"] == "chased it"
    assert c.put("/api/journal/notes/evil", json={"body": "x"}).status_code == 422
    tag = j["tags"][0]
    assert all(t["tag"] == tag for t in c.get(f"/api/journal?tag={tag}").json()["trips"])
    assert c.get("/api/journal?frm=garbage").status_code == 422
    assert c.delete("/api/journal/demo").json()["ok"]
    assert c.get("/api/journal").json()["stats"]["n"] == 0


def test_a_manual_trade_shows_up_in_the_journal_with_its_tag(env):
    svc, c = env
    login(c)
    inst = first(svc, "NIFTY 24250 PE")
    svc.hub.want("t", [inst.key])
    wait_price(svc, inst.key)
    c.post("/api/orders", json={"key": inst.key, "side": "S", "lots": 1, "product": "MIS", "order_type": "MKT", "client_id": "a1"})
    c.post("/api/orders", json={"key": inst.key, "side": "B", "lots": 1, "product": "MIS", "order_type": "MKT", "client_id": "a2"})
    j = c.get("/api/journal").json()
    assert j["stats"]["n"] == 1 and j["trips"][0]["tag"] == "manual" and j["trips"][0]["dir"] == "Short"


def test_alerts_endpoints_report_setup_and_never_leak_the_token(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = "sample", str(tmp_path / "data")
    svc = Services(cfg, env={"TELEGRAM_BOT_TOKEN": "999:super-secret", "TELEGRAM_CHAT_ID": ""})
    svc.start()
    c = TestClient(create_app(svc, PW))
    try:
        assert c.get("/api/alerts").status_code == 401                       # login required
        login(c)
        st = c.get("/api/alerts").json()
        assert st["ok"] and st["configured"] is False                         # chat id missing -> not configured
        assert "super-secret" not in c.get("/api/alerts").text
        t = c.post("/api/alerts/test").json()
        assert t["ok"] is False and "TELEGRAM" in t["error"]
        assert "super-secret" not in c.post("/api/alerts/test").text
        c2 = TestClient(create_app(svc, PW))
        c2.cookies.update(c.cookies)
        assert c2.post("/api/alerts/test").status_code in (401, 403)         # state-changing call needs the header
    finally:
        svc.shutdown()


def test_nse_and_alert_screens_work_in_sample_mode_and_validate_input(env):
    svc, c = env
    assert c.get("/api/nse/fii-dii").status_code == 401
    login(c)
    f = c.get("/api/nse/fii-dii").json()
    assert f["ok"] and f["source"] == "sample" and f["rows"]
    d = c.get("/api/nse/delivery?q=reliance").json()
    assert d["ok"] and d["lookup"][0]["symbol"] == "RELIANCE"
    o = c.get("/api/nse/fo").json()
    assert o["ok"] and "NIFTY" in o["options"]
    assert c.get("/api/nse/delivery?day=bad").status_code == 422
    assert c.get("/api/nse/delivery?q=%3Cscript%3E").status_code == 422
    assert c.get("/api/nse/fo?day=2020-01-01").json()["ok"] is False             # outside the 90-day window: a reason, not data


# ------------------------------------------------------------------- leg builder (release 6)
def _leg_spec(tid="leg_straddle"):
    from terminal.rules import legspec
    return next(t for t in legspec.templates() if t["id"] == tid)["spec"]


def test_templates_name_their_engine_and_leg_templates_are_fully_normalised(env):
    svc, c = env
    login(c)
    t = c.get("/api/strategy-templates").json()["templates"]
    engines = {x["engine"] for x in t}
    assert engines == {"legs", "rules"}
    ids = [x["id"] for x in t]
    assert len(ids) == len(set(ids))                                    # an id never means two templates
    legs = [x for x in t if x["engine"] == "legs"]
    assert len(legs) >= 5 and all(x["spec"]["version"] == 2 and x["spec"]["legs"] for x in legs)


def test_leg_strategy_validate_save_edit_and_plain_english(env):
    svc, c = env
    login(c)
    sp = _leg_spec()
    v = c.post("/api/strategies/validate", json={"spec": sp}).json()
    assert v["ok"] and v["summary"] and any("Sell" in l or "SELL" in l for l in v["summary"])
    bad = dict(sp, legs=[])
    r = c.post("/api/strategies/validate", json={"spec": bad}).json()
    assert r["ok"] is False and r["errors"]
    sid = c.post("/api/strategies", json={"spec": sp}).json()["id"]
    row = c.get(f"/api/strategies/{sid}").json()["strategy"]
    assert row["valid"] and row["spec"]["version"] == 2 and row["summary"]
    sp2 = dict(row["spec"], name="Renamed legs")
    assert c.put(f"/api/strategies/{sid}", json={"spec": sp2}).json()["ok"]
    assert c.get(f"/api/strategies/{sid}").json()["strategy"]["name"] == "Renamed legs"
    days = c.get("/api/backtest/days").json()["strategies"]
    assert any(d["id"] == sid and d["valid"] and d["underlying"] for d in days)
    assert c.delete(f"/api/strategies/{sid}").json()["ok"]


def test_a_leg_strategy_starts_shows_its_legs_and_stops(env):
    svc, c = env
    login(c)
    _fixed_morning(svc)
    sid = c.post("/api/strategies", json={"spec": _leg_spec()}).json()["id"]
    r = c.post(f"/api/strategies/{sid}/start", json={}).json()
    assert r["ok"], r
    st = c.get(f"/api/strategies/{sid}/state").json()
    assert st["running"] and st["state"]["kind"] == "legs" and st["state"]["legs"]
    assert c.post(f"/api/strategies/{sid}/stop").json()["ok"]
    end = time.time() + 5
    while time.time() < end and c.get(f"/api/strategies/{sid}/state").json()["running"]:
        time.sleep(0.1)                                                 # the runner closes up at its next step
    assert not c.get(f"/api/strategies/{sid}/state").json()["running"]


def test_old_rule_strategies_still_work_beside_leg_strategies(env):
    svc, c = env
    login(c)
    a = c.post("/api/strategies", json={"spec": _spec()}).json()["id"]
    b = c.post("/api/strategies", json={"spec": _leg_spec()}).json()["id"]
    rows = {r["id"]: r for r in c.get("/api/strategies").json()["strategies"]}
    assert rows[a]["spec"].get("version") != 2 and rows[b]["spec"]["version"] == 2 and rows[a]["valid"] and rows[b]["valid"]
