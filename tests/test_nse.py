"""NSE public data: parsers refuse anything they do not fully understand; the client is polite; the service walks back to the last published day."""
import io
import json
import zipfile
from datetime import date, datetime

import httpx
import pytest

from terminal import nse
from terminal.clock import IST
from terminal.nse import NseClient, NseError, NseFileMissing, NseService, SampleNse
from terminal.store import Store


# ------------------------------------------------------------------------------------------------ FII / DII
def fii(day="28-Oct-2026", fb="10000.50", fs="12000.25", fn="-1999.75", db="9000", ds="7000", dn="2000"):
    return [{"category": "DII", "date": day, "buyValue": db, "sellValue": ds, "netValue": dn},
            {"category": "FII/FPI ", "date": day, "buyValue": fb, "sellValue": fs, "netValue": fn}]


def test_fii_dii_parses_the_two_categories_per_day_newest_first():
    rows = nse.parse_fii_dii(fii() + fii("27-Oct-2026"))
    assert [r["day"] for r in rows] == ["2026-10-28", "2026-10-27"]
    assert rows[0]["fii"] == {"buy": 10000.5, "sell": 12000.25, "net": -1999.75}
    assert rows[0]["dii"]["net"] == 2000.0


def test_fii_dii_with_commas_and_a_data_wrapper():
    rows = nse.parse_fii_dii({"data": fii(fb="10,000.50", fs="12,000.25", fn="-1,999.75")})
    assert rows[0]["fii"]["buy"] == 10000.5


@pytest.mark.parametrize("payload", [None, [], {}, "x", [1, 2], [{"category": "X", "date": "28-Oct-2026", "buyValue": 1, "sellValue": 1, "netValue": 0}],
                                      fii(day="not a date"), fii(fb="abc"), fii(fn="500")])      # last: net does not equal buy - sell
def test_fii_dii_refuses_anything_it_does_not_understand_or_that_does_not_add_up(payload):
    with pytest.raises(NseError):
        nse.parse_fii_dii(payload)


def test_fii_dii_needs_both_sides_for_a_day():
    with pytest.raises(NseError):
        nse.parse_fii_dii(fii()[:1])


# --------------------------------------------------------------------------------------------- delivery file
DEL_HDR = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"


def del_row(sym, series="EQ", day="28-Oct-2026", prev=100, close=110, vol=1_000_000, turn=1100.0, dq="400000", dp="40.00"):
    return f"{sym}, {series}, {day}, {prev}, {prev}, {close}, {prev}, {close}, {close}, {close}, {vol}, {turn}, 100, {dq}, {dp}"


def test_delivery_parses_the_real_looking_file_with_spaces_and_dashes():
    text = "\n".join([DEL_HDR, del_row("AAA"), del_row("BBB", close=90), del_row("TINY", turn=50.0), del_row("NODEL", dq="-", dp="-"),
                       del_row("ETF1", series="BE"), del_row("BADROW", prev=0)])
    rows = nse.parse_delivery(text, date(2026, 10, 28))
    syms = {r["symbol"]: r for r in rows}
    assert set(syms) == {"AAA", "BBB", "TINY", "NODEL"}                          # BE series and a zero previous close are skipped
    assert syms["AAA"]["chg_pct"] == 10.0 and syms["BBB"]["chg_pct"] == -10.0
    assert syms["AAA"]["turnover_cr"] == 11.0                                    # lakh -> crore
    assert syms["NODEL"]["deliv_pct"] is None
    v = nse.delivery_view(rows, "aa")
    assert [r["symbol"] for r in v["gainers"]][0] == "AAA" and "TINY" not in [r["symbol"] for r in v["turnover"]]   # illiquid are left out
    assert [r["symbol"] for r in v["lookup"]] == ["AAA"] and "NODEL" not in [r["symbol"] for r in v["delivery"]]


def test_delivery_with_a_missing_column_or_wrong_date_is_refused_not_guessed():
    with pytest.raises(NseError, match="DELIV_PER"):
        nse.parse_delivery(DEL_HDR.replace(", DELIV_PER", "") + "\n" + "A, EQ, 28-Oct-2026, 1,1,1,1,1,1,1,1,1,1,1", date(2026, 10, 28))
    with pytest.raises(NseError, match="not 2026-10-28"):
        nse.parse_delivery(DEL_HDR + "\n" + del_row("AAA", day="27-Oct-2026"), date(2026, 10, 28))
    with pytest.raises(NseError):
        nse.parse_delivery(DEL_HDR + "\n", None)


# ------------------------------------------------------------------------------------------------- F&O file
FO_HDR = "TradDt,BizDt,TckrSymb,FinInstrmTp,XpryDt,StrkPric,OptnTp,ClsPric,PrvsClsgPric,UndrlygPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol"


def fo_row(sym, tp, exp, k="", ot="", close=100.0, prev=100.0, under="", oi=1000, chg=0):
    return f"2026-10-28,2026-10-28,{sym},{tp},{exp},{k},{ot},{close},{prev},{under},{oi},{chg},10"


def test_fo_futures_buildup_and_index_option_summary():
    rows = [FO_HDR,
            fo_row("AAA", "STF", "2026-10-27", close=110, prev=100, oi=1200, chg=200),         # price up, OI up -> long build-up
            fo_row("AAA", "STF", "2026-11-24", close=999, prev=1, oi=5, chg=1),                # later expiry is ignored
            fo_row("BBB", "STF", "2026-10-27", close=90, prev=100, oi=1200, chg=200),          # price down, OI up -> short build-up
            fo_row("CCC", "STF", "2026-10-27", close=90, prev=100, oi=800, chg=-200),          # long unwinding
            fo_row("DDD", "STF", "2026-10-27", close=110, prev=100, oi=800, chg=-200),         # short covering
            fo_row("NIFTY", "IDF", "2026-10-27", close=24300, prev=24200, oi=5000, chg=100),
            fo_row("NIFTY", "IDO", "2026-10-27", 24200, "CE", under=24250, oi=100),
            fo_row("NIFTY", "IDO", "2026-10-27", 24300, "CE", under=24250, oi=300, chg=50),
            fo_row("NIFTY", "IDO", "2026-10-27", 24200, "PE", under=24250, oi=400, chg=-20),
            fo_row("NIFTY", "IDO", "2026-10-27", 24300, "PE", under=24250, oi=100),
            fo_row("NIFTY", "IDO", "2026-11-03", 24300, "PE", under=24250, oi=999999),         # later expiry ignored
            fo_row("XYZ", "OPTSTK", "2026-10-27")]                                              # unknown type skipped
    v = nse.fo_view(nse.parse_fo("\n".join(rows)))
    b = v["buildup"]
    assert [f["symbol"] for f in b["Long build-up"]] == ["AAA"] and b["Long build-up"][0]["oi_chg_pct"] == 20.0
    assert [f["symbol"] for f in b["Short build-up"]] == ["BBB"]
    assert [f["symbol"] for f in b["Long unwinding"]] == ["CCC"] and [f["symbol"] for f in b["Short covering"]] == ["DDD"]
    assert v["index_futures"][0]["symbol"] == "NIFTY" and v["index_futures"][0]["buildup"] == "Long build-up"
    o = v["options"]["NIFTY"]
    assert o["expiry"] == "2026-10-27" and o["total_ce_oi"] == 400 and o["total_pe_oi"] == 500 and o["pcr"] == 1.25
    assert o["max_pain"] == 24200.0                                                   # tie at 10,000 either way; the lower strike wins
    assert o["top_ce"][0] == {"strike": 24300.0, "oi": 300} and o["top_pe"][0]["strike"] == 24200.0
    assert [s["strike"] for s in o["strikes"]] == [24200.0, 24300.0]


def test_fo_with_missing_columns_is_refused():
    with pytest.raises(NseError, match="OpnIntrst"):
        nse.parse_fo(FO_HDR.replace("OpnIntrst", "OI") + "\n")
    with pytest.raises(NseError):
        nse.parse_fo(FO_HDR + "\n")


# ------------------------------------------------------------------------------------------------ the client
class Server:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(str(req.url))
        for frag, resp in self.routes:
            if frag in str(req.url):
                r = resp.pop(0) if isinstance(resp, list) and len(resp) > 1 else (resp[0] if isinstance(resp, list) else resp)
                return r() if callable(r) else r
        return httpx.Response(404)


def client(tmp_path, routes, sleeps=None):
    srv = Server(routes)
    http = httpx.Client(transport=httpx.MockTransport(srv), follow_redirects=True)
    t = [0.0]
    return NseClient(tmp_path / "nse", http=http, clock=lambda: t[0], sleep=(sleeps.append if sleeps is not None else lambda s: None)), srv


def test_client_gets_cookies_first_then_the_json_and_is_polite(tmp_path):
    sleeps = []
    c, srv = client(tmp_path, [("option-chain", httpx.Response(200, text="ok")), ("fiidii", httpx.Response(200, json=fii()))], sleeps)
    assert c.fii_dii()[0]["category"] == "DII"
    assert "option-chain" in srv.calls[0] and "fiidii" in srv.calls[1]
    c.fii_dii()
    assert sleeps and all(s <= nse.MIN_GAP_S for s in sleeps)                      # it waits between requests


def test_client_refreshes_cookies_once_on_403_then_reports_a_block(tmp_path):
    c, srv = client(tmp_path, [("option-chain", httpx.Response(200)), ("fiidii", httpx.Response(403))])
    with pytest.raises(NseError, match="refused"):
        c.fii_dii()
    assert sum("option-chain" in u for u in srv.calls) == 2 and sum("fiidii" in u for u in srv.calls) == 2


def test_client_block_page_instead_of_json_or_file_is_an_error(tmp_path):
    c, _ = client(tmp_path, [("option-chain", httpx.Response(200)), ("fiidii", httpx.Response(200, text="<html>Access denied</html>")),
                              ("sec_bhav", httpx.Response(200, text="<html><body>blocked</body></html>"))])
    with pytest.raises(NseError, match="not data"):
        c.fii_dii()
    with pytest.raises(NseError, match="block page"):
        c.delivery_csv(date(2026, 10, 28))
    assert not list((tmp_path / "nse").glob("*"))                                  # a block page is never cached as a data file


def test_client_caches_files_and_remembers_missing_ones(tmp_path):
    c, srv = client(tmp_path, [("sec_bhavdata_full_28102026", httpx.Response(200, text="A,B\n1,2")), ("sec_bhavdata_full_29102026", httpx.Response(404))])
    assert c.delivery_csv(date(2026, 10, 28)) == "A,B\n1,2" and c.delivery_csv(date(2026, 10, 28)) == "A,B\n1,2"
    assert len(srv.calls) == 1                                                       # second read came from disk
    for _ in range(3):
        with pytest.raises(NseFileMissing):
            c.delivery_csv(date(2026, 10, 29))
    assert len(srv.calls) == 2                                                       # the 404 was asked about once, then remembered


def test_client_unzips_the_fo_file_and_survives_a_bad_zip(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("BhavCopy_NSE_FO_0_0_0_20261028_F_0000.csv", FO_HDR + "\nrow")
    c, _ = client(tmp_path, [("BhavCopy_NSE_FO_0_0_0_20261028", httpx.Response(200, content=buf.getvalue())),
                              ("BhavCopy_NSE_FO_0_0_0_20261027", httpx.Response(200, content=b"not a zip"))])
    assert c.fo_csv(date(2026, 10, 28)).startswith("TradDt")
    with pytest.raises(NseError, match="not a zip"):
        c.fo_csv(date(2026, 10, 27))


def test_client_network_failure_is_a_plain_message(tmp_path):
    def boom():
        raise httpx.ConnectError("dns")
    c, _ = client(tmp_path, [("option-chain", boom), ("sec_bhav", boom)])
    with pytest.raises(NseError, match="Could not reach NSE"):
        c.delivery_csv(date(2026, 10, 28))


# ------------------------------------------------------------------------------------------------ the service
class Fake:
    name = "fake"

    def __init__(self, published=(), fii_reply=None):
        self.published, self.fii_reply, self.asked = set(published), fii_reply, []

    def fii_dii(self):
        if isinstance(self.fii_reply, Exception):
            raise self.fii_reply
        return self.fii_reply

    def delivery_csv(self, d):
        self.asked.append(d)
        if d not in self.published:
            raise NseFileMissing("no")
        return DEL_HDR + "\n" + del_row("AAA", day=d.strftime("%d-%b-%Y"))

    fo_csv = delivery_csv


def svc_at(provider, y, mo, d, h, mi=0):
    return NseService(provider, Store(":memory:"), "paper", today=lambda: datetime(y, mo, d, h, mi, tzinfo=IST))


def test_candidate_days_wait_for_the_evening_and_skip_weekends():
    s = svc_at(Fake(), 2026, 10, 29, 10)                       # Thursday morning: start from Wednesday
    assert s.candidate_days()[:3] == [date(2026, 10, 28), date(2026, 10, 27), date(2026, 10, 26)]
    s = svc_at(Fake(), 2026, 10, 29, 19)                       # after 18:30: today is expected
    assert s.candidate_days()[0] == date(2026, 10, 29)
    s = svc_at(Fake(), 2026, 11, 2, 10)                        # Monday morning: Friday, not the weekend
    assert s.candidate_days()[:2] == [date(2026, 10, 30), date(2026, 10, 29)]
    assert all(d.weekday() < 5 for d in s.candidate_days())


def test_service_walks_back_to_the_last_published_day_and_says_which():
    prov = Fake(published=[date(2026, 10, 27)])
    r = svc_at(prov, 2026, 10, 29, 19).delivery()
    assert r["ok"] and r["day"] == "2026-10-27" and prov.asked[:3] == [date(2026, 10, 29), date(2026, 10, 28), date(2026, 10, 27)]


def test_service_gives_up_with_a_reason_and_never_partial_data():
    r = svc_at(Fake(published=[]), 2026, 10, 29, 19).delivery()
    assert r == {"ok": False, "reason": "unavailable", "error": r["error"]} and "No file found" in r["error"]


def test_service_validates_the_day_the_person_asks_for():
    s = svc_at(Fake(published=[date(2026, 10, 28)]), 2026, 10, 29, 19)
    assert s.delivery("2026-10-28")["ok"]
    for bad in ("2026-11-30", "2020-01-01", "2026-13-45"):
        r = s.delivery(bad)
        assert not r["ok"] and "90 days" in r["error"]
    assert not s.delivery("2026-10-27")["ok"]                  # asked for a specific day that was not published: no silent substitute


def test_fii_dii_history_accumulates_and_a_failure_keeps_the_stored_rows_visible():
    prov = Fake(fii_reply=fii("27-Oct-2026"))
    s = svc_at(prov, 2026, 10, 29, 19)
    assert [r["day"] for r in s.fii_dii(refresh_every_s=0)["rows"]] == ["2026-10-27"]
    prov.fii_reply = fii("28-Oct-2026")
    assert [r["day"] for r in s.fii_dii(refresh_every_s=0)["rows"]] == ["2026-10-28", "2026-10-27"]
    prov.fii_reply = NseError("NSE refused the request")
    r = s.fii_dii(refresh_every_s=0)
    assert r["ok"] and len(r["rows"]) == 2 and "refused" in r["error"]            # old rows stay, with the reason shown beside them
    prov.fii_reply = fii(fn="1")                                                   # does not add up: stored rows untouched
    r = s.fii_dii(refresh_every_s=0)
    assert len(r["rows"]) == 2 and "do not add up" in r["error"]


def test_fii_dii_with_nothing_stored_and_a_failure_is_unavailable():
    r = svc_at(Fake(fii_reply=NseError("blocked")), 2026, 10, 29, 19).fii_dii(refresh_every_s=0)
    assert r["ok"] is False and r["error"] == "blocked"


def test_fii_dii_is_not_refetched_inside_the_refresh_window():
    prov = Fake(fii_reply=fii())
    s = svc_at(prov, 2026, 10, 29, 19)
    s.fii_dii()
    prov.fii_reply = NseError("should not be asked")
    assert s.fii_dii()["error"] == ""


def test_sample_provider_goes_through_the_same_parsers_and_is_consistent():
    s = NseService(SampleNse(), Store(":memory:"), "sample")
    f = s.fii_dii()
    assert f["ok"] and f["source"] == "sample" and len(f["rows"]) >= 15
    for r in f["rows"]:
        assert abs(r["fii"]["buy"] - r["fii"]["sell"] - r["fii"]["net"]) < 1.0
    d = s.delivery()
    assert d["ok"] and d["gainers"] and "lookup" not in d
    o = s.fo()
    assert o["ok"] and set(o["options"]) == set(nse.INDEX_UNDERLYINGS) and o["options"]["NIFTY"]["strikes"]
    assert s.delivery(q="RELIANCE")["lookup"][0]["symbol"] == "RELIANCE"
