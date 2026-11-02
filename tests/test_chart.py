"""Charts: candles built from live prices, timeframe alignment, drawings, and the API."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from terminal.clock import IST
from terminal.config import load_config
from terminal.market import charts as ch
from terminal.market.hub import QuoteHub
from terminal.market.models import Quote
from terminal.services import Services
from terminal.store import Store
from terminal.web.server import create_app

NSE, MCX = "nse_cm|2885", "mcx_fo|99999"


def mins(y, mo, d, h, mi):
    """Epoch minute of an Indian clock time."""
    return int(datetime(y, mo, d, h, mi, tzinfo=IST).timestamp() // 60)


def svc_for(sample=False):
    hub, store = QuoteHub(), Store(":memory:")
    return hub, store, ch.ChartService(hub, store, sample=sample)


def tick(s, key, ltp, m, sec=5, vol=None):
    s.on_quote(Quote(key=key, ltp=ltp, volume=vol, ts=m * 60 + sec))


# ------------------------------------------------------------------------------------ alignment
def test_buckets_align_to_the_market_open():
    m = mins(2026, 10, 30, 9, 15)
    assert ch.bucket_start(m, 5, NSE) == m
    assert ch.bucket_start(m + 4, 5, NSE) == m
    assert ch.bucket_start(m + 5, 5, NSE) == m + 5
    assert ch.bucket_start(m + 44, 60, NSE) == m                      # the first hour runs 09:15-10:15, not 09:00-10:00
    assert ch.bucket_start(mins(2026, 10, 30, 10, 15), 60, NSE) == m + 60
    mm = mins(2026, 10, 30, 9, 0)
    assert ch.bucket_start(mm + 59, 60, MCX) == mm                    # commodities open at 09:00
    assert ch.bucket_start(mins(2026, 10, 30, 23, 10), 15, MCX) == mins(2026, 10, 30, 23, 0)


def test_daily_candle_is_the_indian_calendar_day():
    a, b = mins(2026, 10, 30, 0, 5), mins(2026, 10, 30, 23, 50)
    assert ch.bucket_start(a, 1440, MCX) == ch.bucket_start(b, 1440, MCX) == mins(2026, 10, 30, 0, 0)
    assert ch.bucket_start(b + 20, 1440, MCX) == mins(2026, 10, 31, 0, 0)


def test_aggregate_merges_open_high_low_close_volume():
    m = mins(2026, 10, 30, 9, 15)
    rows = [(m, 10, 12, 9, 11, 100), (m + 1, 11, 15, 10, 14, 50), (m + 2, 14, 14, 8, 9, 25), (m + 5, 9, 9.5, 9, 9.2, 5)]
    out = ch.aggregate(rows, 5, NSE)
    assert out == [[m, 10, 15, 8, 9, 175], [m + 5, 9, 9.5, 9, 9.2, 5]]
    assert ch.aggregate(rows, 1, NSE)[1] == [m + 1, 11, 15, 10, 14, 50]


# ------------------------------------------------------------------------------------ recording
def test_only_watched_instruments_are_recorded_and_candles_form_from_ticks():
    hub, store, s = svc_for()
    m = mins(2026, 10, 30, 10, 0)
    tick(s, NSE, 100, m)                                              # nobody asked for this chart yet
    s.flush()
    assert store.candles(NSE) == []
    s.watch(NSE)
    for px, mm, sec in [(100, m, 1), (103, m, 20), (99, m, 40), (101, m, 55), (102, m + 1, 3)]:
        tick(s, NSE, px, mm, sec)
    s.flush()
    rows = store.candles(NSE)
    assert rows[0][:5] == (m, 100, 103, 99, 101)
    assert rows[1][:5] == (m + 1, 102, 102, 102, 102)
    assert hub.wanted() == [NSE]


def test_volume_is_the_difference_of_the_running_total():
    hub, store, s = svc_for()
    s.watch(NSE)
    m = mins(2026, 10, 30, 10, 0)
    tick(s, NSE, 100, m, 1, vol=1000)
    tick(s, NSE, 101, m, 30, vol=1300)
    tick(s, NSE, 102, m + 1, 2, vol=1500)
    tick(s, NSE, 102, m + 1, 30, vol=1500)
    tick(s, NSE, 103, m + 2, 5, vol=100)                              # total went DOWN (new day / reset): counted as 0, not negative
    s.flush()
    v = [r[5] for r in store.candles(NSE)]
    assert v == [300, 200, 0]


def test_saving_a_minute_twice_keeps_the_open_and_widens_the_range():
    store = Store(":memory:")
    store.put_candles([(NSE, 10, 100, 105, 99, 102, 50)])
    store.put_candles([(NSE, 10, 101, 107, 98, 103, 70)])
    assert store.candles(NSE) == [(10, 100, 107, 98, 103, 70)]


def test_zero_or_missing_prices_are_ignored():
    hub, store, s = svc_for()
    s.watch(NSE)
    m = mins(2026, 10, 30, 10, 0)
    tick(s, NSE, 0, m)
    tick(s, NSE, -5, m)
    s.flush()
    assert store.candles(NSE) == []


def test_only_the_30_most_recent_charts_are_kept():
    hub, store, s = svc_for()
    for i in range(35):
        s.watch(f"nse_cm|{i}")
    rec = s.recent()
    assert len(rec) == 30 and rec[0] == "nse_cm|34" and "nse_cm|0" not in rec
    assert "nse_cm|0" not in hub.wanted() and "nse_cm|34" in hub.wanted()


def test_recording_resumes_after_a_restart_and_old_minutes_are_pruned():
    hub, store, s = svc_for()
    s.watch(NSE)
    old = int(datetime.now().timestamp() // 60) - (ch.KEEP_DAYS + 2) * 1440
    store.put_candles([(NSE, old, 1, 1, 1, 1, 1)])
    hub2 = QuoteHub()
    s2 = ch.ChartService(hub2, store)
    s2.start()
    try:
        assert hub2.wanted() == [NSE]
        assert store.candles(NSE) == []                               # the old minute was dropped
    finally:
        s2.stop()


def test_candles_reply_shape_and_ist_shift():
    hub, store, s = svc_for()
    m = mins(2026, 10, 30, 9, 15)
    store.put_candles([(NSE, m + i, 100 + i, 101 + i, 99 + i, 100.5 + i, 10) for i in range(12)])
    r = s.candles(NSE, "5", 600)
    assert r["tf"] == "5" and r["minutes_recorded"] == 12
    assert len(r["candles"]) == 3
    t, o, h, l, c, v = r["candles"][0]
    assert t == m * 60 + ch.IST_SHIFT and (o, h, l, c, v) == (100, 105, 99, 104.5, 50)
    with pytest.raises(ValueError):
        s.candles(NSE, "7")


# ------------------------------------------------------------------------------------ drawings
def test_drawings_are_validated():
    s = svc_for()[2]
    good = [{"id": "a", "type": "h", "price": 101.5}, {"id": "b", "type": "t", "t1": 1, "p1": 2, "t2": 3, "p2": 4, "junk": "x"}]
    out = s.set_drawings(NSE, good)
    assert out[1] == {"id": "b", "type": "t", "t1": 1.0, "p1": 2.0, "t2": 3.0, "p2": 4.0}
    assert s.drawings(NSE) == out
    for bad in ([{"type": "circle"}], [{"type": "h", "price": "9"}], [{"type": "h", "price": True}],
                [{"type": "h", "price": float("nan")}], [{"type": "h"}], ["x"], "x",
                [{"type": "h", "price": 1}] * (ch.MAX_DRAWINGS + 1)):
        with pytest.raises(ValueError):
            s.clean_drawings(bad)
    assert s.drawings(NSE) == out                                     # a rejected save changes nothing


# ------------------------------------------------------------------------------------ sample mode
def test_sample_backfill_is_only_for_sample_mode_and_never_overwrites_real_history():
    hub, store, s = svc_for(sample=True)
    hub.seed(NSE, ltp=1450.0)
    r = s.candles(NSE, "5", 600)
    assert r["minutes_recorded"] > 100
    s2 = svc_for(sample=False)[2]
    assert s2.candles(NSE, "5")["minutes_recorded"] == 0              # live mode never invents anything
    hub3, store3, s3 = svc_for(sample=True)
    hub3.seed(NSE, ltp=1450.0)
    store3.put_candles([("sample:" + NSE, 1000 + i, 1, 1, 1, 1, 1) for i in range(100)])
    n = len(store3.candles("sample:" + NSE))
    s3.candles(NSE, "5")
    assert len(store3.candles("sample:" + NSE)) == n
    assert store3.candles(NSE) == []                                  # nothing invented is ever saved as a real candle


# ------------------------------------------------------------------------------------ API
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


def test_api_chart_candles_recent_and_errors(api):
    svc, c = api
    key = c.get("/api/search", params={"q": "reliance"}).json()["results"][0]["key"]
    r = c.get("/api/chart", params={"key": key, "tf": "15"})
    assert r.status_code == 200
    j = r.json()
    assert j["invented"] is True and j["tf"] == "15" and len(j["candles"]) > 10
    assert len(j["candles"][0]) == 6 and j["inst"]["key"] == key
    assert c.get("/api/chart", params={"key": key, "tf": "7"}).status_code == 422
    assert c.get("/api/chart", params={"key": "bad key!", "tf": "5"}).status_code == 422
    assert c.get("/api/chart", params={"key": "nse_cm|0", "tf": "5"}).status_code == 404
    rec = c.get("/api/chart/recent").json()["recent"]
    assert rec and rec[0]["key"] == key


def test_api_drawings_roundtrip_and_validation(api):
    svc, c = api
    key = c.get("/api/search", params={"q": "reliance"}).json()["results"][0]["key"]
    assert c.get("/api/chart/drawings", params={"key": key}).json() == {"drawings": []}
    body = {"drawings": [{"id": "x1", "type": "h", "price": 1500.5}]}
    assert c.put("/api/chart/drawings", params={"key": key}, json=body).json()["drawings"][0]["price"] == 1500.5
    assert c.get("/api/chart/drawings", params={"key": key}).json()["drawings"][0]["id"] == "x1"
    assert c.put("/api/chart/drawings", params={"key": key}, json={"drawings": [{"type": "zz"}]}).status_code in (400, 422)
    assert c.get("/api/chart/drawings", params={"key": key}).json()["drawings"][0]["id"] == "x1"
    assert c.put("/api/chart/drawings", params={"key": key}, json={"drawings": [{"type": "h", "price": 1}] * 61}).status_code == 422
    nocsrf = TestClient(create_app(svc, "correct-horse-battery"))
    assert nocsrf.put("/api/chart/drawings", params={"key": key}, json=body).status_code in (401, 403)
