"""Commodities (MCX): names, hours, search, option chain, order rules, and a commodity leg strategy on the sample world."""
import copy
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from terminal.clock import IST
from terminal.config import load_config
from terminal.market import commodity as cm
from terminal.market.catalog import KotakCatalog, LazyCatalog
from terminal.market.chain import ChainService, ChainUnavailable, KotakChainProvider, SampleChainProvider
from terminal.market.hub import QuoteHub
from terminal.market.models import Instrument, Quote
from terminal.market.sample import SAMPLE_DERIVS, SAMPLE_INDICES, build_catalog
from terminal.models import OrderError
from terminal.rules import legspec as L
from terminal.services import Services
from terminal.trading.models import OrderTicket
from terminal.web.server import create_app

from test_chain import DOC_REPLY, FakeClient, FakeSession
from test_legs import LegWorld, leg, sl, spec


# ------------------------------------------------------------------------------------------ names
def test_typed_words_find_the_right_commodities():
    assert cm.resolve(["gold", "mini"]) == ["GOLDM"]
    assert cm.resolve(["crude"]) == ["CRUDEOIL", "CRUDEOILM"]
    assert cm.resolve(["natural", "gas"]) == ["NATURALGAS", "NATGASMINI"]
    assert "SILVERMIC" in cm.resolve(["silver", "micro"]) and "SILVER" not in cm.resolve(["silver", "micro"])
    assert cm.resolve(["zzz"]) == []
    assert cm.label_of("GOLDM") == "Gold Mini" and cm.label_of("NEWTHING") == "Newthing"


# ------------------------------------------------------------------------------------------ hours
@pytest.mark.parametrize("day,summer", [
    (date(2026, 7, 15), True), (date(2026, 1, 15), False),
    (date(2026, 3, 8), False), (date(2026, 3, 9), True),          # MCX switched the Monday after the US clocks changed
    (date(2026, 11, 1), True), (date(2026, 11, 2), False),        # 2 Nov 2026: 23:55 (broker bulletin)
])
def test_mcx_evening_close_follows_us_daylight_saving(day, summer):
    assert cm.us_summer_schedule(day) is summer
    assert cm.close_time(day).strftime("%H:%M") == ("23:30" if summer else "23:55")


def test_agri_and_override_hours():
    assert cm.close_time(date(2026, 11, 3), "COTTON").strftime("%H:%M") == "21:00"
    assert cm.close_time(date(2026, 11, 3), "CPO").strftime("%H:%M") == "17:00"
    assert cm.close_time(date(2026, 11, 3), "GOLD", "23:10").strftime("%H:%M") == "23:10"


def test_mcx_status_by_the_clock():
    def at(d, hm):
        h, m = hm.split(":")
        return datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=IST)
    mon = date(2026, 11, 2)
    assert cm.status(at(mon, "08:30"), "")["state"] == "closed"
    assert cm.status(at(mon, "09:00"), "")["state"] == "open"
    assert cm.status(at(mon, "23:40"), "")["state"] == "open"          # winter close is 23:55
    assert cm.status(at(mon, "23:56"), "")["state"] == "closed"
    assert cm.status(at(date(2026, 11, 7), "12:00"), "")["state"] == "weekend"
    assert cm.status(at(date(2026, 7, 6), "23:40"), "")["state"] == "closed"   # summer close is 23:30


# ------------------------------------------------------------------------------- sample world
def test_sample_search_finds_big_and_mini_and_shows_friendly_names():
    cat = build_catalog()
    gm = cat.search("gold mini", 20)
    assert gm and all(i.name == "GOLDM" and i.kind == "FUT" and i.segment == "mcx_fo" for i in gm)
    assert gm[0].label().startswith("Gold Mini ") and gm[0].label().endswith(" FUT")
    names = {i.name for i in cat.search("crude", 40)}
    assert {"CRUDEOIL", "CRUDEOILM"} <= names
    opts = cat.search("crude ce 5600", 20)
    assert opts and all(i.kind == "CE" and i.strike == 5600 for i in opts)
    assert cat.mcx_future("GOLD").kind == "FUT" and cat.mcx_loaded()


def test_commodity_chain_is_an_option_on_the_future_with_its_own_strike_step():
    cat, hub = build_catalog(), QuoteHub()
    chain = ChainService(hub, None)
    chain.set_provider(SampleChainProvider(cat, {}, SAMPLE_DERIVS))
    assert {"CRUDEOIL", "CRUDEOILM"} <= {h["name"] for h in chain.search("crude")}
    assert any(h["kind"] == "commodity" for h in chain.search("gold"))
    raw = chain.raw_for("CRUDEOIL", "nearest")
    fut = cat.get(raw.spot_key)
    assert fut.kind == "FUT" and fut.name == "CRUDEOIL" and fut.expiry >= date.fromisoformat(raw.expiry)
    ks = sorted(raw.rows)
    assert ks[1] - ks[0] == 50 and raw.lot == 100
    with pytest.raises(ChainUnavailable):
        chain.get("GOLDPETAL", None, 5)                                  # a future-only contract has no options
    hub.update(Quote(raw.spot_key, 5612.0, prev_close=5600.0, ts=1e10))
    out = chain.get("CRUDEOIL", raw.expiry, 5)
    assert out["spot_key"] == raw.spot_key and out["atm"] == 5600.0 and out["spot"] == 5612.0


def test_dashboard_commodity_cards_are_the_front_month_futures():
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode = "sample"
    svc = Services(cfg)
    svc.start()
    try:
        cards = svc.commodity_cards()
        assert [c["label"] for c in cards] == ["Gold", "Silver", "Crude Oil", "Natural Gas"]
        assert all(svc.catalog.get(c["key"]).kind == "FUT" for c in cards)
    finally:
        svc.shutdown()


# ------------------------------------------------------------------------ Kotak scrip master
def _mcx_frame():
    import pandas as pd
    return pd.DataFrame({
        "psymbol": [1, 2, 3, 4, 5, 6], "ptrdsymbol": ["GOLDM26DECFUT", "GOLDM26DEC120000CE", "GOLDM26DEC120000PE", "GOLD26DECFUT", "SILVERMIC26NOVFUT", "XYZ26DECFUT"],
        "psymbolname": ["GOLDM", "GOLDM", "GOLDM", "GOLD", "SILVERMIC", "XYZ"], "poptiontype": ["XX", "CE", "PE", "XX", "XX", "XX"],
        "pexpirydate": [1796376000 + 86400 * i for i in range(6)], "dstrikeprice": [0, 12000000, 12000000, 0, 0, 0],
        "llotsize": [100, 100, 100, 1000, 1, 5], "dticksize": [100, 100, 100, 100, 100, 100]})


def test_kotak_mcx_rows_become_futures_and_options_with_names_lots_and_ticks():
    class Ins:
        def _load(self, seg):
            return _mcx_frame() if seg == "mcx_fo" else _mcx_frame().iloc[0:0]
    cat = KotakCatalog(Ins())
    fut = cat.search("gold mini", 10)
    assert [i.kind for i in fut] == ["FUT"] and fut[0].lot == 100 and fut[0].tick == 1.0 and fut[0].label().startswith("Gold Mini")
    assert {i.name for i in cat.search("gold", 10)} == {"GOLD", "GOLDM"}
    opts = cat.search("goldm ce 120000", 10)
    assert [(i.kind, i.strike) for i in opts] == [("CE", 120000.0)]
    assert cat.search("xyz", 10)[0].name == "XYZ"                          # a symbol the table does not know is still searchable
    assert cat.mcx_names() == {"GOLDM": {"lot": 100, "name": "GOLDM", "label": "Gold Mini", "group": "Gold", "size": "mini"}}
    assert [u["name"] for u in cat.mcx_underlyings("gold")] == ["GOLDM"]   # only names that have options can open a chain
    assert cat.mcx_future("GOLD").name == "GOLD" and cat.mcx_future("NOPE") is None


def test_kotak_commodity_chain_calls_mcx_fo_and_uses_the_future_as_spot():
    class Ins:
        def _load(self, seg):
            return _mcx_frame()
    cat = KotakCatalog(Ins())
    c = FakeClient(DOC_REPLY)
    p = KotakChainProvider(FakeSession(c), cat)
    p.check("GOLDM")
    with pytest.raises(ChainUnavailable, match="not in Kotak's F&O list|F&O"):
        p.check("NOPE")
    assert p._exchange("GOLDM") == "mcx_fo" and p._exchange("NIFTY") == "nse_fo"
    assert p._iso("2026-12-04", "mcx_fo") == "2026-12-04"
    assert p._iso("1796000000", "mcx_fo")                                    # Unix-seconds expiries are understood
    raw = p.fetch("GOLDM", "2026-12-04")
    assert c.calls[0][1]["exchange"] == "mcx_fo" and c.calls[0][1]["underlying"] == "GOLDM"
    assert raw.spot_key and raw.spot_key.startswith("mcx_fo|")


# ----------------------------------------------------------------------------------- order rules
@pytest.fixture
def world(tmp_path):
    return LegWorld(tmp_path)


def _inst(w, kind="FUT"):
    return next(i for i in w.cat.contracts if i.segment == "mcx_fo" and i.name == "GOLDM" and i.kind == kind)


def test_commodity_orders_are_day_only_and_never_delivery(world):
    i = _inst(world)
    world.hub.update(Quote(i.key, 125000.0, prev_close=125000.0, ts=1e10))
    t = OrderTicket(key=i.key, side="B", lots=1, product="MIS", order_type="L", price=125000.0, validity="IOC")
    with pytest.raises(OrderError, match="Day validity only"):
        world.trading.place(t)
    with pytest.raises(OrderError, match="Product must be"):
        world.trading.place(OrderTicket(key=i.key, side="B", lots=1, product="CNC", order_type="L", price=125000.0))
    res = world.trading.place(OrderTicket(key=i.key, side="B", lots=2, product="NRML", order_type="L", price=125000.0))
    assert res["qty"] == 2 * i.lot
    with pytest.raises(OrderError, match="tick size"):
        world.trading.place(OrderTicket(key=i.key, side="B", lots=1, product="MIS", order_type="L", price=125000.5))


# --------------------------------------------------------------------------------- strategy times
def cm_spec(und="CRUDEOIL", **kw):
    return spec(leg("S", "CE", sl=sl(25)), underlying=und, entry_time="18:00", exit_time="23:15", **kw)


def test_commodity_strategy_is_accepted_with_mcx_hours_and_nse_limits_do_not_apply():
    s, errs, _ = L.parse(cm_spec())
    assert not errs and s.underlying == "CRUDEOIL"
    s, errs, _ = L.parse(cm_spec(last_entry_time="22:00"))
    assert not errs
    raw = cm_spec()
    raw["entry_time"], raw["exit_time"] = "09:00", "23:15"
    assert not L.parse(raw)[1]                                             # 09:00 is a valid MCX start
    raw = spec(leg("S", "CE"), underlying="NIFTY", entry_time="09:05", exit_time="23:15")
    errs = L.parse(raw)[1]
    assert any("09:15 or later" in e for e in errs) and any("15:15 or earlier" in e for e in errs)


def test_commodity_exit_time_must_be_before_that_days_close(monkeypatch):
    raw = cm_spec()
    raw["exit_time"] = "23:50"
    monkeypatch.setattr(cm, "us_summer_schedule", lambda d: True)          # 23:30 close
    errs = L.parse(raw)[1]
    assert any("23:30 or earlier" in e for e in errs)
    monkeypatch.setattr(cm, "us_summer_schedule", lambda d: False)         # 23:55 close
    s, errs, warns = L.parse(raw)
    assert not errs and any("square off" in w for w in warns)             # inside the broker's square-off window: warned, your call
    raw["exit_time"] = "23:20"
    s, errs, warns = L.parse(raw)
    assert not errs and not any("square off intraday" in w for w in warns)


def test_commodity_defaults_when_times_are_left_out():
    raw = cm_spec()
    raw.pop("exit_time")
    raw.pop("entry_time")
    s, errs, _ = L.parse(raw)
    assert not errs and s.exit.time == "23:15" and s.last_entry_time == "22:45"


def test_commodities_cannot_be_back_tested():
    from terminal.backtest import BacktestManager
    bm = BacktestManager.__new__(BacktestManager)
    with pytest.raises(ValueError, match="not available"):
        BacktestManager.start(bm, "x", cm_spec(), "sample", ["2026-10-26"])


# ------------------------------------------------------------------ a commodity strategy, tick by tick
def test_crude_strategy_sells_the_atm_call_off_the_future_with_the_contracts_tick_and_hours(world):
    w = world
    raw = w.chain.raw_for("CRUDEOIL", "nearest")
    fut = raw.spot_key
    ce = raw.rows[5600.0]["CE"].key
    assert w.cat.get(ce).tick == 1.0

    def push(hms, fut_px=5603.0, ce_px=90.0):
        w.clock.set(hms)
        t = w.clock.t.timestamp()
        w.hub.update(Quote(fut, fut_px, prev_close=fut_px, ts=t))
        for k, leg_ in raw.rows.items():
            for kind, lg in leg_.items():
                w.hub.update(Quote(lg.key, ce_px if lg.key == ce else 60.0, prev_close=60.0, ts=t))
        if w.runner:
            w.runner.step()

    push("17:40")
    sid = w.store.save_strategy(None, "Crude", spec(leg("S", "CE", sl=sl(25)), underlying="CRUDEOIL", entry_time="18:00", exit_time="23:15"))
    r = w.runner = w.eng.start(sid, threaded=False)
    assert r.spot_key == fut                                               # the front-month future is the reference price
    assert r._hours() == ("09:00", cm.session_hm("CRUDEOIL", w.clock.day)[1])
    push("17:55")
    assert not r.positions
    push("18:00:02")
    assert r.status == "watching" and len(r.positions) == 1
    p = r.positions[0]
    assert p.key == ce and p.side == "S" and p.entry_price == 90.0
    assert p.sl == 112.0 or p.sl == 113.0                                  # 90 + 25% = 112.5, moved onto the 1-rupee tick grid
    assert w.broker.positions()[0].net_qty == -100                         # one lot of crude = 100 barrels in the sample world
    push("23:15:02")
    assert r.status in ("done", "stopped") and w.broker.positions()[0].net_qty == 0   # closed at the exit time you set


# --------------------------------------------------------------------------------------- the API
@pytest.fixture
def api(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = "sample", str(tmp_path / "data")
    svc = Services(cfg)
    svc.start()
    c = TestClient(create_app(svc, "correct-horse-battery"))
    assert c.post("/api/auth/login", json={"password": "correct-horse-battery"}, headers={"x-terminal": "1"}).status_code == 200
    c.headers["x-terminal"] = "1"
    yield svc, c
    svc.shutdown()


def test_api_session_search_instrument_and_chain_for_commodities(api):
    svc, c = api
    s = c.get("/api/session").json()
    assert s["market_mcx"]["state"] in ("open", "closed", "weekend") and s["market_mcx"]["close"] in ("23:30", "23:55")
    assert [x["label"] for x in s["commodities"]] == ["Gold", "Silver", "Crude Oil", "Natural Gas"]
    hit = c.get("/api/search", params={"q": "gold mini"}).json()["results"]
    assert hit and hit[0]["segment"] == "mcx_fo" and hit[0]["label"].startswith("Gold Mini")
    ins = c.get("/api/instrument", params={"key": hit[0]["key"]}).json()["instrument"]
    assert ins["lot"] == 10 and ins["tick"] == 1.0
    assert c.get("/api/instrument", params={"key": "mcx_fo|0"}).status_code == 404
    r = c.get("/api/chain", params={"underlying": "CRUDEOIL"}).json()
    assert r["ok"] and r["spot_key"].startswith("mcx_fo|") and r["lot"] == 100 and r["rows"]
    found = c.get("/api/chain/search", params={"q": "crude"}).json()["results"]
    assert {"CRUDEOIL", "CRUDEOILM"} <= {x["name"] for x in found}
    r = c.post("/api/orders", json={"key": hit[0]["key"], "side": "B", "lots": 1, "product": "MIS", "order_type": "MKT", "validity": "IOC"})
    assert r.status_code == 400 and "Day validity only" in r.text
