"""Option chain + OI analysis: the arithmetic, the Kotak reply parsing, and the API on the sample world."""
import time

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from terminal.config import load_config
from terminal.market.chain import (ChainUnavailable, KotakChainProvider, Leg, RawChain, analyse, buildup, max_pain)
from terminal.market.hub import QuoteHub
from terminal.market.models import Quote
from terminal.services import Services
from terminal.web.server import create_app

PW = "correct-horse-battery"


# ------------------------------------------------------------------------------------ arithmetic
def test_max_pain_picks_the_strike_where_buyers_lose_most():
    strikes = [100.0, 110.0, 120.0]
    ce = {100.0: 10, 110: 50, 120: 10}
    pe = {100.0: 10, 110: 50, 120: 10}
    assert max_pain(strikes, ce, pe) == 110.0
    # heavy call OI at 100 and heavy put OI at 120 pulls the answer to the middle-ish strike with least total payout
    assert max_pain([100.0, 120.0], {100.0: 100}, {120.0: 100}) in (100.0, 120.0)
    assert max_pain([], {}, {}) is None


def test_buildup_labels():
    assert buildup(110, 100, 1200, 1000) == "Long build-up"
    assert buildup(90, 100, 1200, 1000) == "Short build-up"
    assert buildup(110, 100, 800, 1000) == "Short covering"
    assert buildup(90, 100, 800, 1000) == "Long unwinding"
    assert buildup(100, 100, 800, 1000) is None            # price unchanged: no label
    assert buildup(None, 100, 800, 1000) is None


def make_raw():
    raw = RawChain("NIFTY", "2026-11-03", 75, "nse_cm|Nifty 50", 24510.0, None)
    for k in (24400.0, 24500.0, 24600.0):
        raw.rows[k] = {
            "CE": Leg(f"nse_fo|{int(k)}1", f"N{int(k)}CE", 100.0, 90.0, 1000 + (k == 24600) * 3000, 1000, 10),
            "PE": Leg(f"nse_fo|{int(k)}2", f"N{int(k)}PE", 80.0, 100.0, 2000 + (k == 24400) * 4000, 1500, 10),
        }
    return raw


def test_analyse_summary_and_window_and_live_overlay():
    hub = QuoteHub()
    raw = make_raw()
    out = analyse(raw, hub, None)
    s = out["summary"]
    assert out["atm"] == 24500.0 and out["spot"] == 24510.0
    assert s["ce_oi"] == 1000 + 1000 + 4000 and s["pe_oi"] == 6000 + 2000 + 2000
    assert s["pcr"] == round(10000 / 6000, 3)
    assert s["ce_top"][0]["strike"] == 24600.0 and s["pe_top"][0]["strike"] == 24400.0
    assert s["straddle"] == 180.0
    assert out["rows"][0]["pe"]["buildup"] == "Short build-up"      # price 80 < prev 100 while OI 6000 > 1500
    # the live quote wins over the REST snapshot
    hub.update(Quote("nse_fo|245001", 120.0, prev_close=90.0, oi=1500))
    hub.update(Quote("nse_cm|Nifty 50", 24610.0))
    out = analyse(raw, hub, 0 or None)
    mid = [r for r in out["rows"] if r["strike"] == 24500.0][0]["ce"]
    assert mid["ltp"] == 120.0 and mid["oi"] == 1500 and mid["oi_chg"] == 500 and mid["buildup"] == "Long build-up"
    assert out["atm"] == 24600.0
    narrow = analyse(raw, hub, 1)
    assert [r["strike"] for r in narrow["rows"]] == [24500.0, 24600.0]
    assert narrow["strikes_total"] == 3


# ------------------------------------------------------------------------------- Kotak parsing
class FakeClient:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def expiries(self, **kw):
        self.calls.append(("expiries", kw))
        return {"expiries": ["2026-11-03", "2026-11-10"]}

    def option_chain(self, **kw):
        self.calls.append(("option_chain", kw))
        return self.reply


class FakeSession:
    def __init__(self, client):
        self.client, self.logged_in = client, True


DOC_REPLY = {"data": {
    "common_data": {"mktLot": "65", "multiplier": "1", "unlSymbol": "NIFTY", "exSeg": "nse_fo", "expiryDt": "2026-11-03"},
    "call": [{"instrument": {"neoSymbol": "nse_fo|71472", "symbol": "NIFTY26NOV24500CE", "optionType": "CE", "strikePrice": "24500", "moneyness": "ATM"},
              "quote": {"ltp": "166.7500", "prevClose": "99.7000", "volume": 225431505},
              "openInterest": {"current": 10715645, "previous": 13647630, "change": -2931985, "changePct": -21.48}}],
    "put": [{"instrument": {"neoSymbol": "nse_fo|71473", "symbol": "NIFTY26NOV24500PE", "optionType": "PE", "strikePrice": "24500", "moneyness": "ATM"},
             "quote": {"ltp": "80.5", "prevClose": "90", "volume": 1000},
             "openInterest": {"current": "5000", "previous": "4000"}}]}}


def test_kotak_option_chain_reply_is_parsed_per_the_sdk_docs():
    c = FakeClient(DOC_REPLY)
    p = KotakChainProvider(FakeSession(c), catalog=None, strike_count=40)
    raw = p.fetch("NIFTY", "2026-11-03")
    assert c.calls[0][1] == {"exchange": "nse_fo", "underlying": "NIFTY", "instrument_type": "option", "expiry": "2026-11-03", "count": 200}
    assert raw.lot == 65 and raw.atm_hint == 24500.0 and raw.spot_key == "nse_cm|Nifty 50"
    ce, pe = raw.rows[24500.0]["CE"], raw.rows[24500.0]["PE"]
    assert (ce.key, ce.ltp, ce.prev_close, ce.oi, ce.oi_prev) == ("nse_fo|71472", 166.75, 99.7, 10715645, 13647630)
    assert pe.oi == 5000 and pe.oi_prev == 4000
    assert p.expiries("NIFTY") == ["2026-11-03", "2026-11-10"]
    assert FakeClient(None) and c.calls[-1][1]["exchange"] == "nse_fo"
    assert KotakChainProvider(FakeSession(FakeClient(DOC_REPLY)), None)._exchange("SENSEX") == "bse_fo"


@pytest.mark.parametrize("reply,needle", [
    ({"error": [{"message": "Invalid session"}]}, "Invalid session"),
    ({"rateLimit": {"Retry-After": "60"}}, "rate-limiting"),
    ({"data": {"common_data": {}, "call": [], "put": []}}, "empty option chain"),
    ("garbage", "unreadable"),
])
def test_kotak_errors_become_readable_messages(reply, needle):
    p = KotakChainProvider(FakeSession(FakeClient(reply)), None)
    with pytest.raises(ChainUnavailable) as e:
        p.fetch("NIFTY", None)
    assert needle in str(e.value)


def test_not_logged_in_is_reported_not_crashed():
    s = FakeSession(FakeClient(DOC_REPLY))
    s.logged_in = False
    with pytest.raises(ChainUnavailable, match="Not connected"):
        KotakChainProvider(s, None).fetch("NIFTY", None)


# ------------------------------------------------------------------------------------------ API
@pytest.fixture
def env(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = "sample", str(tmp_path / "data")
    svc = Services(cfg)
    svc.start()
    c = TestClient(create_app(svc, PW))
    assert c.post("/api/auth/login", json={"password": PW}, headers={"x-terminal": "1"}).status_code == 200
    c.headers["x-terminal"] = "1"
    yield svc, c
    svc.shutdown()


def test_chain_endpoints_need_login(env):
    svc, c = env
    anon = TestClient(c.app)
    for path in ("/api/chain", "/api/chain/underlyings", "/api/chain/series?underlying=NIFTY&expiry=2026-11-03"):
        assert anon.get(path).status_code == 401


def test_chain_on_sample_world(env):
    svc, c = env
    u = c.get("/api/chain/underlyings").json()
    assert u["ok"] and {x["name"] for x in u["underlyings"]} == {"NIFTY", "BANKNIFTY", "SENSEX"}
    r = c.get("/api/chain", params={"underlying": "nifty", "window": 6}).json()
    assert r["ok"] and r["underlying"] == "NIFTY" and r["source"] == "sample" and len(r["expiries"]) == 3
    assert r["expiry"] == r["expiries"][0] and r["lot"] == 75
    assert len(r["rows"]) == 13 and r["atm"] in [x["strike"] for x in r["rows"]]
    s = r["summary"]
    assert s["pcr"] and s["max_pain"] and s["ce_top"] and s["pe_top"] and s["straddle"]
    row = r["rows"][6]
    assert row["ce"]["key"].startswith("nse_fo|") and row["ce"]["ltp"] and row["ce"]["oi"] and row["pe"]["ltp"]
    # a second expiry and another underlying
    r2 = c.get("/api/chain", params={"underlying": "NIFTY", "expiry": r["expiries"][1], "window": 3}).json()
    assert r2["expiry"] == r["expiries"][1] and len(r2["rows"]) == 7
    assert c.get("/api/chain", params={"underlying": "SENSEX"}).json()["lot"] == 20
    # unknown expiry falls back to the nearest; unknown underlying is reported, not a crash
    assert c.get("/api/chain", params={"underlying": "NIFTY", "expiry": "2030-01-01"}).json()["expiry"] == r["expiries"][0]
    bad = c.get("/api/chain", params={"underlying": "NOPE"}).json()
    assert bad["ok"] is False and bad["error"]


def test_chain_input_validation(env):
    svc, c = env
    assert c.get("/api/chain", params={"underlying": "NIFTY; DROP"}).status_code == 422
    assert c.get("/api/chain", params={"underlying": "NIFTY", "expiry": "tomorrow"}).status_code == 422
    assert c.get("/api/chain/series", params={"underlying": "NIFTY", "expiry": "x"}).status_code == 422


def test_oi_series_is_recorded_and_returned(env):
    svc, c = env
    r = c.get("/api/chain", params={"underlying": "NIFTY"}).json()
    time.sleep(0.2)
    pts = c.get("/api/chain/series", params={"underlying": "NIFTY", "expiry": r["expiry"]}).json()["points"]
    assert len(pts) == 1 and pts[0]["pcr"] and pts[0]["spot"] and pts[0]["ce_oi"]
    c.get("/api/chain", params={"underlying": "NIFTY"})          # within a minute: not recorded again
    assert len(c.get("/api/chain/series", params={"underlying": "NIFTY", "expiry": r["expiry"]}).json()["points"]) == 1


def test_chain_prices_are_live(env):
    svc, c = env
    a = c.get("/api/chain", params={"underlying": "NIFTY", "window": 2}).json()
    time.sleep(1.2)
    b = c.get("/api/chain", params={"underlying": "NIFTY", "window": 2}).json()
    atm_a = [x for x in a["rows"] if x["strike"] == a["atm"]][0]["ce"]["ltp"]
    atm_b = [x for x in b["rows"] if x["strike"] == b["atm"]][0]["ce"]["ltp"]
    assert atm_a and atm_b and a["spot"] != b["spot"]


# ------------------------------------------------------------------------------- any F&O stock (not only indices)
def test_sample_world_finds_fo_stocks_and_indices_by_name(env):
    svc, c = env
    r = c.get("/api/chain/search", params={"q": "rel"}).json()
    assert r["ok"] and [x["name"] for x in r["results"]] == ["RELIANCE"] and r["results"][0]["kind"] == "stock" and r["results"][0]["lot"] == 500
    assert r["results"][0]["label"].startswith("RELIANCE INDUSTRIES")
    assert {x["name"] for x in c.get("/api/chain/search", params={"q": "tata"}).json()["results"]} == {"TATASTEEL", "TCS"}   # name starts with it, or the company name contains it
    nifty = c.get("/api/chain/search", params={"q": "nif"}).json()["results"]
    assert "NIFTY" in {x["name"] for x in nifty} and all(x["kind"] == "index" for x in nifty)
    assert c.get("/api/chain/search", params={"q": "zzzz"}).json()["results"] == []
    assert c.get("/api/chain/search", params={"q": ""}).json()["results"] == []
    assert TestClient(c.app).get("/api/chain/search", params={"q": "rel"}).status_code == 401


def test_stock_option_chain_opens_with_its_own_spot_lot_and_monthly_expiries(env):
    svc, c = env
    r = c.get("/api/chain", params={"underlying": "reliance", "window": 6}).json()
    assert r["ok"] and r["underlying"] == "RELIANCE" and r["lot"] == 500 and r["spot_key"] == "nse_cm|2885"
    assert 1300 < r["spot"] < 1600 and len(r["expiries"]) == 3
    assert len(r["rows"]) == 13 and r["atm"] % 20 == 0 and abs(r["atm"] - r["spot"]) <= 20
    row = r["rows"][6]
    assert row["ce"]["ltp"] and row["pe"]["ltp"] and row["ce"]["oi"] and row["pe"]["oi"]
    assert r["summary"]["pcr"] and r["summary"]["straddle"] and 0 < r["summary"]["straddle_pct"] < 20
    # the legs are real catalog instruments, so the order ticket can trade them
    inst = svc.catalog.get(row["ce"]["key"])
    assert inst and inst.name == "RELIANCE" and inst.kind == "CE" and inst.lot == 500 and inst.strike == row["strike"]
    # prices follow the stock: the lowest-strike call is deep in the money (dear), the highest-strike call far out of the money (cheap)
    far = c.get("/api/chain", params={"underlying": "RELIANCE", "window": 14}).json()["rows"]
    assert far[0]["ce"]["ltp"] > far[len(far) // 2]["ce"]["ltp"] > far[-1]["ce"]["ltp"]


def test_a_name_without_options_is_refused_with_a_message(env):
    svc, c = env
    r = c.get("/api/chain", params={"underlying": "WIPRO"}).json()
    assert r["ok"] is False and "no options" in r["error"] and "WIPRO" in r["error"]


def test_kotak_fo_list_search_and_check(monkeypatch):
    import pandas as pd
    from terminal.market.catalog import KotakCatalog, LazyCatalog
    fo = pd.DataFrame({
        "psymbol": [1, 2, 3, 4, 5, 6], "ptrdsymbol": ["RELIANCE26NOV1200CE", "RELIANCE26NOV1200PE", "RELIANCE26NOVFUT", "M&M26NOV3000CE", "TCS26NOV3000CE", "NIFTY26NOV24500CE"],
        "psymbolname": ["RELIANCE", "RELIANCE", "RELIANCE", "M&M", "TCS", "NIFTY"], "poptiontype": ["CE", "PE", "XX", "CE", "CE", "CE"],
        "pexpirydate": [0] * 6, "dstrikeprice": [120000, 120000, 0, 300000, 300000, 2450000], "llotsize": [500, 500, 500, 350, 175, 75], "dticksize": [5] * 6})
    cm = pd.DataFrame({"psymbol": [10, 11, 12], "ptrdsymbol": ["RELIANCE-EQ", "TCS-EQ", "M&M-EQ"], "psymbolname": ["RELIANCE", "TCS", "M&M"],
                       "pdesc": ["RELIANCE INDUSTRIES LTD", "TATA CONSULTANCY SERV LT", "MAHINDRA & MAHINDRA LTD"], "pgroup": ["EQ"] * 3, "dticksize": [5] * 3})

    class Ins:
        def _load(self, seg):
            return {"nse_fo": fo, "nse_cm": cm}[seg]
    cat = KotakCatalog(Ins())
    assert cat.fo_names() == {"RELIANCE": 500, "M&M": 350, "TCS": 175, "NIFTY": 75}       # futures-only rows are not an options underlying
    cat._df("nse_cm")                                                                      # the cash list is loaded: company names appear
    assert cat.fo_underlyings("rel") == [{"name": "RELIANCE", "label": "RELIANCE INDUSTRIES LTD", "lot": 500}]
    assert [x["name"] for x in cat.fo_underlyings("mahindra")] == ["M&M"]                  # by company name
    assert cat.fo_underlyings("zz") == []
    p = KotakChainProvider(FakeSession(FakeClient(DOC_REPLY)), LazyCatalog())
    lazy = p.cat
    lazy.set(cat)
    out = p.search("rel")
    assert [(x["name"], x["kind"]) for x in out] == [("RELIANCE", "stock")]
    nif = p.search("nif")
    assert nif[0]["name"] == "NIFTY" and nif[0]["kind"] == "index" and len([x for x in nif if x["name"] == "NIFTY"]) == 1   # an index is listed once
    p.check("RELIANCE")
    p.check("BANKNIFTY")
    with pytest.raises(ChainUnavailable, match="not in Kotak's F&O list"):
        p.check("WIPRO")
    # the stock's chain call goes to the F&O segment with the stock's name, and its spot is the cash instrument
    c = FakeClient(DOC_REPLY)
    sp = KotakChainProvider(FakeSession(c), cat)
    cat.register(__import__("terminal.market.models", fromlist=["Instrument"]).Instrument("nse_cm|2885", "nse_cm", "2885", "RELIANCE-EQ", "RELIANCE", "EQ"))
    raw = sp.fetch("RELIANCE", None)
    assert c.calls[0][1]["underlying"] == "RELIANCE" and c.calls[0][1]["exchange"] == "nse_fo" and c.calls[0][1]["instrument_type"] == "option"


# ------------------------------------------------------------------------------------ every strike, not just 40
class PickyClient(FakeClient):
    """Refuses big counts the way a backend with a smaller limit might."""
    def __init__(self, reply, max_count):
        super().__init__(reply)
        self.max_count = max_count

    def option_chain(self, **kw):
        self.calls.append(("option_chain", kw))
        if kw["count"] > self.max_count:
            return {"error": [{"message": "count too large"}]}
        return self.reply


def test_kotak_is_asked_for_the_whole_chain_and_falls_back_to_smaller_counts():
    c = PickyClient(DOC_REPLY, 100)
    p = KotakChainProvider(FakeSession(c), catalog=None)
    p.fetch("NIFTY", "2026-11-03")
    assert [x[1]["count"] for x in c.calls if x[0] == "option_chain"] == [200, 100]      # 200 refused, 100 accepted
    p.fetch("NIFTY", "2026-11-03")
    assert [x[1]["count"] for x in c.calls if x[0] == "option_chain"][-1] == 100 and len(c.calls) == 3   # remembered: no retry of 200
    low = PickyClient(DOC_REPLY, 40)
    KotakChainProvider(FakeSession(low), catalog=None).fetch("NIFTY", None)
    assert [x[1]["count"] for x in low.calls] == [200, 100, 40]
    never = PickyClient(DOC_REPLY, 0)
    with pytest.raises(ChainUnavailable, match="count too large"):
        KotakChainProvider(FakeSession(never), catalog=None).fetch("NIFTY", None)


def test_a_rate_limit_is_not_mistaken_for_a_count_problem():
    c = FakeClient({"rateLimit": {"x": 1}})
    p = KotakChainProvider(FakeSession(c), catalog=None)
    with pytest.raises(ChainUnavailable, match="rate-limiting"):
        p.fetch("NIFTY", None)
    assert len(c.calls) == 1                                                            # stopped at once, no smaller retries


def test_window_trims_only_the_display_while_the_numbers_use_every_strike(env):
    svc, c = env
    full = c.get("/api/chain", params={"underlying": "NIFTY", "window": 0}).json()
    small = c.get("/api/chain", params={"underlying": "NIFTY", "window": 6}).json()
    assert full["strikes_total"] == 61 and len(full["rows"]) == 61 and len(small["rows"]) == 13 and small["strikes_total"] == 61
    assert full["summary"]["pcr"] == small["summary"]["pcr"] and full["summary"]["max_pain"] == small["summary"]["max_pain"]
    assert [r["strike"] for r in full["rows"]] == sorted(r["strike"] for r in full["rows"])
    stock = c.get("/api/chain", params={"underlying": "RELIANCE", "window": 0}).json()
    assert stock["strikes_total"] == 61 and stock["rows"][0]["strike"] < stock["atm"] < stock["rows"][-1]["strike"]


def test_live_prices_are_followed_for_the_strikes_nearest_the_money_only():
    from terminal.market.chain import ChainService
    hub = QuoteHub()
    svc = ChainService(hub)
    raw = RawChain("NIFTY", "2026-11-03", 75, "nse_cm|Nifty 50", 24500.0, None)
    for i in range(-80, 81):
        k = 24500.0 + 50 * i
        raw.rows[k] = {"CE": Leg(f"nse_fo|{i + 1000}1", "c", 1.0, 1.0, 1, 1), "PE": Leg(f"nse_fo|{i + 1000}2", "p", 1.0, 1.0, 1, 1)}
    svc._follow(raw)
    followed = svc._followed
    assert len(followed) == 60 * 2 + 1 and "nse_cm|Nifty 50" in followed
    assert "nse_fo|10001" in followed and "nse_fo|9201" not in followed and "nse_fo|10802" not in followed      # ATM in, far strikes out
