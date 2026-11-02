"""
Adapter tests built on the REAL shapes documented/implemented by the official SDK (kotakneoapi 3.0.9 /
GitHub Kotak-Neo/kotak-neo-python v3.0.10): URL-returning scrip_master, "dStrikePrice;" x100, NSE epoch
expiry, error-dicts instead of exceptions, SFeedIndex ticks with numeric token + name, etc.
What these tests CANNOT prove is that Kotak's live servers return exactly these values - run
`python tools/discover.py` with your own account for that.
"""
import struct
from datetime import date, datetime, timedelta, timezone

import pytest

from conftest import ROOT
from terminal.brokers import kotak
from terminal.brokers.base import Subscription
from terminal.brokers.kotak import (KotakFeed, KotakInstruments, KotakOrders, KotakSession, api_error,
                                    map_state, parse_expiry)
from terminal.clock import IST
from terminal.config import load_config
from terminal.models import INDEX, Contract, OrderError, OrderRequest, OrderUnknown

TODAY = datetime.now(IST).date()


def nse_epoch(d: date) -> int:
    """Encode `d` the way NSE does in pExpiryDate so that the SDK formula (1970 + n + 315511200s) gives `d`."""
    unix = datetime(d.year, d.month, d.day, 9, 0, tzinfo=timezone.utc).timestamp()      # mid-session on that day
    return int(unix - 315511200)


def unix_epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, 9, 0, tzinfo=timezone.utc).timestamp())


def make_csv(expiries, strikes=range(24500, 25600, 50), bse=False) -> bytes:
    enc = unix_epoch if bse else nse_epoch
    name = "SENSEX" if bse else "NIFTY"
    lines = ["pSymbol,pSymbolName,pTrdSymbol,pOptionType,pExpiryDate,dStrikePrice;,lLotSize"]
    for e in expiries:
        for k in strikes:
            for ot in ("CE", "PE"):
                lines.append(f"{50000 + k + (7 if ot == 'PE' else 0)},{name},{name}{e:%y%b}{k}{ot},{ot},{enc(e)},{k * 100},"
                             f"{20 if bse else 75}")
    e = expiries[0]
    lines.append(f"1,NIFTYNXT50,NIFTYNXT50{e:%y%b}7000CE,CE,{enc(e)},700000,25")        # look-alike underlying
    lines.append(f"2,BANKNIFTY,BANKNIFTY{e:%y%b}55000CE,CE,{enc(e)},5500000,35")
    lines.append(f"3,{name},{name}{e:%y%b}FUT,XX,{enc(e)},-100,{75}")                    # future, not an option
    return ("\n".join(lines) + "\n").encode()


class FakeClient:
    """Only the SDK methods the adapter uses, with the SDK's real return conventions."""

    def __init__(self):
        self.exp1, self.exp2 = TODAY + timedelta(days=2), TODAY + timedelta(days=9)
        self.placed = {}
        self.orders = {}
        self.pos_reply = {"data": []}

    def scrip_master(self, exchange_segment=""):
        return f"https://cdn.example/{exchange_segment}.csv"

    def place_order(self, exchange_segment, product, price, order_type, quantity, validity, trading_symbol,
                    transaction_type, amo="NO", disclosed_quantity="0", trigger_price="0", tag=None):
        self.placed = {k: v for k, v in locals().items() if k != "self"}
        return {"stat": "Ok", "nOrdNo": "26102600001", "stCode": 200}

    def cancel_order(self, order_id, amo="NO", isVerify=False):
        return {"stat": "Ok", "stCode": 200, "result": order_id}

    def order_report(self, order_id=None):
        rows = list(self.orders.values())
        if order_id is not None:
            rows = [r for r in rows if r["nOrdNo"] == str(order_id)]
            if not rows:
                return {"stat": "Not_Ok", "stCode": 5203, "emsg": "No data"}
        return {"stat": "Ok", "stCode": 200, "data": rows}

    def positions(self):
        return self.pos_reply


@pytest.fixture
def parts(monkeypatch):
    cfg = load_config(ROOT / "config.yaml")
    s = KotakSession()
    s.client = FakeClient()
    monkeypatch.setattr(KotakInstruments, "_download", staticmethod(lambda url: make_csv([s.client.exp1, s.client.exp2])))
    return cfg, s


def contract():
    return Contract("NIFTY26OCT25000PE", "75007", "nse_fo", 25000, "PE", TODAY, 75)


# ----------------------------------------------------------------------------- scrip master
def test_candidates_use_url_download_real_columns_and_scale_strikes(parts):
    cfg, s = parts
    ch = KotakInstruments(s, cfg).candidates("NIFTY", 25012.0, window=2)
    assert sorted(ch) == [24900.0, 24950.0, 25000.0, 25050.0, 25100.0]
    ce, pe = ch[25000.0]
    assert (ce.option_type, pe.option_type) == ("CE", "PE")
    assert ce.expiry == s.client.exp1 and ce.lot_size == 75 and ce.segment == "nse_fo"
    assert ce.symbol == f"NIFTY{s.client.exp1:%y%b}25000CE" and ce.token == "75000"      # pTrdSymbol / pSymbol
    assert pe.token == "75007"


def test_candidates_ignore_lookalike_underlyings_and_futures(parts):
    cfg, s = parts
    ch = KotakInstruments(s, cfg).candidates("NIFTY", 25000.0, window=40)
    syms = {c.symbol for pair in ch.values() for c in pair}
    assert not any("NXT50" in x or "BANKNIFTY" in x or "FUT" in x for x in syms)
    assert 7000.0 not in ch and 55000.0 not in ch


def test_expiry_offset_and_unknown_underlying(parts):
    cfg, s = parts
    nxt = KotakInstruments(s, cfg).candidates("NIFTY", 25000.0, 1, expiry_offset=1)[25000.0][0]
    assert nxt.expiry == s.client.exp2
    with pytest.raises(RuntimeError, match="Expiry #5"):
        KotakInstruments(s, cfg).candidates("NIFTY", 25000.0, 1, expiry_offset=5)
    cfg.instruments["NIFTY"].underlying = "NOSUCH"
    with pytest.raises(RuntimeError, match="No NOSUCH"):
        KotakInstruments(s, cfg).candidates("NIFTY", 25000.0, 1)


def test_sensex_uses_plain_unix_expiry(monkeypatch):
    cfg = load_config(ROOT / "config.yaml")
    cfg.instrument = "SENSEX"
    s = KotakSession()
    s.client = FakeClient()
    e = TODAY + timedelta(days=3)
    monkeypatch.setattr(KotakInstruments, "_download", staticmethod(lambda url: make_csv([e], range(80000, 80600, 100), bse=True)))
    ce, pe = KotakInstruments(s, cfg).candidates("SENSEX", 80010.0, window=1)[80000.0]
    assert ce.expiry == e and ce.segment == "bse_fo" and ce.lot_size == 20


def test_scrip_master_failure_is_explained(parts):
    cfg, s = parts
    s.client.scrip_master = lambda exchange_segment="": {"Error": "boom"}
    with pytest.raises(RuntimeError, match="scrip_master failed: Error: boom"):
        KotakInstruments(s, cfg).candidates("NIFTY", 25000.0, 1)
    s.client.scrip_master = lambda exchange_segment="": None
    with pytest.raises(RuntimeError, match="did not return a file"):
        KotakInstruments(s, cfg).candidates("NIFTY", 25000.0, 1)


def test_expiry_decoding_matches_the_sdk_formula():
    d = TODAY + timedelta(days=5)
    assert parse_expiry(nse_epoch(d), TODAY, "nse_fo") == d
    assert parse_expiry(unix_epoch(d), TODAY, "bse_fo") == d
    assert parse_expiry(f"{d:%d%b%Y}", TODAY) == d
    assert parse_expiry("garbage", TODAY) is None


# --------------------------------------------------------------------------------- orders
def test_place_sends_sdk_valid_string_params_and_returns_order_number(parts):
    cfg, s = parts
    oid = KotakOrders(s, cfg).place(OrderRequest(contract(), "S", 75, "L", 93.95, tag="orb-entry"))
    assert oid == "26102600001"
    p = s.client.placed
    assert (p["price"], p["quantity"], p["product"], p["transaction_type"], p["order_type"]) == ("93.95", "75", "MIS", "S", "L")
    assert (p["exchange_segment"], p["validity"], p["amo"], p["trigger_price"], p["tag"]) == ("nse_fo", "DAY", "NO", "0", "orb-entry")


def test_stop_order_and_market_order_params(parts):
    cfg, s = parts
    KotakOrders(s, cfg).place(OrderRequest(contract(), "B", 75, "SL", 110.0, trigger=105.0, tag="orb-sl"))
    assert (s.client.placed["order_type"], s.client.placed["price"], s.client.placed["trigger_price"]) == ("SL", "110.00", "105.00")
    KotakOrders(s, cfg).place(OrderRequest(contract(), "B", 75, "MKT"))
    assert (s.client.placed["order_type"], s.client.placed["price"]) == ("MKT", "0")


@pytest.mark.parametrize("reply, exc", [
    ({"stat": "Not_Ok", "emsg": "Insufficient margin", "stCode": 1004}, OrderError),            # Kotak refused
    ({"Error Message": "Complete the 2fa process before accessing this application"}, OrderError),
    ({"Error": ValueError("Invalid product")}, OrderError),                                      # SDK validation
    ({"Error": type("ApiException", (Exception,), {"status": 400})()}, OrderError),             # HTTP 4xx
    ({"Error": TimeoutError("read timed out")}, OrderUnknown),                                   # may have been sent
    ({"Error": type("ApiException", (Exception,), {"status": 502})()}, OrderUnknown),
    (None, OrderUnknown),
    ({"stat": "Ok", "stCode": 200}, OrderError),                                                 # ok but no order number
])
def test_place_error_replies(parts, reply, exc):
    cfg, s = parts
    s.client.place_order = lambda **kw: reply
    with pytest.raises(exc) as ei:
        KotakOrders(s, cfg).place(OrderRequest(contract(), "S", 75, "L", 90.0))
    assert (type(ei.value) is OrderUnknown) == (exc is OrderUnknown)


def test_cancel_treats_already_complete_as_fine_and_other_errors_as_errors(parts):
    cfg, s = parts
    o = KotakOrders(s, cfg)
    s.client.cancel_order = lambda order_id, **k: {"stCode": 1021, "errMsg": "order is completed"}
    o.cancel("1")
    s.client.cancel_order = lambda order_id, **k: {"Error": TimeoutError("x")}
    with pytest.raises(OrderError):
        o.cancel("1")
    s.client.cancel_order = lambda order_id, **k: None
    with pytest.raises(OrderError):
        o.cancel("1")


def test_status_reads_single_order_report_and_maps_fields(parts):
    cfg, s = parts
    s.client.orders = {"1": {"nOrdNo": "1", "ordSt": "complete", "fldQty": "75", "avgPrc": "93.95", "rejRsn": "--"},
                       "2": {"nOrdNo": "2", "ordSt": "rejected", "fldQty": "0", "avgPrc": "0", "rejRsn": "RMS:Margin Exceeds"},
                       "3": {"nOrdNo": "3", "ordSt": "trigger pending", "fldQty": "0"},
                       "4": {"nOrdNo": "4", "ordSt": "open", "fldQty": "25", "avgPrc": "94"}}
    o = KotakOrders(s, cfg)
    assert (o.status("1").state, o.status("1").filled_qty, o.status("1").avg_price, o.status("1").reason) == ("COMPLETE", 75, 93.95, "")
    assert (o.status("2").state, o.status("2").reason) == ("REJECTED", "RMS:Margin Exceeds")
    assert o.status("3").state == "TRIGGER_PENDING"
    assert (o.status("4").state, o.status("4").filled_qty) == ("OPEN", 25)


def test_status_falls_back_to_full_book_and_reports_unknown_when_unreadable(parts):
    cfg, s = parts
    s.client.orders = {"1": {"nOrdNo": "1", "ordSt": "complete", "fldQty": "75", "avgPrc": "90"}}
    full_only = s.client.order_report
    s.client.order_report = lambda order_id=None: ({"stat": "Not_Ok", "emsg": "bad arg"} if order_id else full_only())
    assert KotakOrders(s, cfg).status("1").state == "COMPLETE"
    s.client.order_report = lambda order_id=None: None
    assert KotakOrders(s, cfg).status("1").state == "UNKNOWN"          # never guessed
    s.client.order_report = lambda order_id=None: {"Error": TimeoutError("x")}
    assert KotakOrders(s, cfg).status("1").state == "UNKNOWN"


def test_positions_net_units_and_unreadable_is_none_not_empty(parts):
    cfg, s = parts
    o = KotakOrders(s, cfg)
    s.client.pos_reply = {"stat": "Ok", "data": [
        {"trdSym": "NIFTY26OCT25000PE", "flSellQty": "75", "flBuyQty": "0", "cfBuyQty": "0", "cfSellQty": "0", "lotSz": "75"},
        {"trdSym": "NIFTY26OCT25000CE", "flSellQty": "150", "flBuyQty": "75", "cfBuyQty": "0", "cfSellQty": "0"}]}
    assert o.positions() == {"NIFTY26OCT25000PE": -75, "NIFTY26OCT25000CE": -75}
    s.client.pos_reply = {"data": []}
    assert o.positions() == {}
    s.client.pos_reply = {"stat": "Not_Ok", "stCode": 5203, "emsg": "No Data"}
    assert o.positions() == {}                                            # Kotak's "no positions" wording
    for bad in (None, {"Error": TimeoutError("x")}, {"Error Message": "2fa"}, {"stat": "Not_Ok", "emsg": "Session expired"}):
        s.client.pos_reply = bad
        assert o.positions() is None


def test_api_error_and_map_state():
    assert api_error({"stat": "Ok", "nOrdNo": "1", "stCode": 200}) is None
    assert "Insufficient" in api_error({"stat": "Not_Ok", "emsg": "Insufficient margin"})
    assert api_error(None) and api_error("<html>")
    assert map_state("complete") == "COMPLETE" and map_state("rejected") == "REJECTED"
    assert map_state("open pending") == "OPEN" and map_state("put order req received") == "OPEN"
    assert map_state("trigger pending") == "TRIGGER_PENDING" and map_state("Cancelled") == "CANCELLED"
    assert map_state("???") == "UNKNOWN"


# ------------------------------------------------------------------------------- login
class FakeNeo:
    script = {}

    def __init__(self, consumer_key, environment="prod", **kw):
        assert environment == "prod"

    def totp_login(self, mobile_number, ucc, totp):
        return self.script["login"]

    def totp_validate(self, mpin):
        return self.script["validate"]

    def whatsmyip(self):
        return {"data": [{"ip": "203.0.113.7"}]}


ENV = {"NEO_CONSUMER_KEY": "k", "NEO_MOBILE_NUMBER": "+919999999999", "NEO_UCC": "AB123", "NEO_MPIN": "123456"}


@pytest.fixture
def fake_neo(monkeypatch):
    import neo_api_client
    monkeypatch.setattr(neo_api_client, "NeoAPI", FakeNeo)
    return FakeNeo


def test_login_success_records_ip(fake_neo):
    fake_neo.script = {"login": {"data": {"token": "t", "status": "success", "kType": "View"}},
                       "validate": {"data": {"token": "t2", "status": "success", "kType": "Trade"}}}
    s = KotakSession(ENV)
    s.login("123456")
    assert s.logged_in and s.my_ip == "203.0.113.7"


@pytest.mark.parametrize("login, validate, msg", [
    ({"error": [{"message": "Invalid TOTP"}]}, None, "TOTP login failed: Invalid TOTP"),
    (None, None, "TOTP login failed"),
    ({"data": {"status": "success", "kType": "View"}}, {"error": [{"message": "Invalid MPIN"}]}, "MPIN validation failed: Invalid MPIN"),
    ({"data": {"status": "success", "kType": "View"}}, {"data": {"status": "success", "kType": "View"}}, "without trading access"),
])
def test_login_failures_are_not_swallowed(fake_neo, login, validate, msg):
    fake_neo.script = {"login": login, "validate": validate}
    s = KotakSession(ENV)
    with pytest.raises(RuntimeError, match=msg):
        s.login("123456")
    assert not s.logged_in


# --------------------------------------------------------------------------------- feed
def feed_for(instrument="NIFTY"):
    cfg = load_config(ROOT / "config.yaml")
    cfg.instrument = instrument
    f = KotakFeed(KotakSession(), cfg)
    got = []
    f._on_tick = got.append
    return f, got


def index_packet(token=26000, value=2501234, name=b"Nifty 50", exchange_id=1):
    from neo_api_client.websocket.feed import protocol as P
    body = P._INDEX_BODY.pack(token, 2500000, 2490000, 2510000, 2480000, value, 123456, 2600000, 2200000, 12, 0.0, 2, 100, name)
    header = P._HEADER.pack(P.HEADER_SIZE + len(body), P.MSG_INDEX, exchange_id, 4, 0, 1, 0)
    return header + body


def test_real_index_packet_decodes_to_an_INDEX_tick():
    pytest.importorskip("neo_api_client")
    from neo_api_client.websocket.feed import protocol as P
    f, got = feed_for()
    f.subscribe([Subscription("nse_cm", "Nifty 50", INDEX)])              # we subscribe by NAME ...
    msg = P.decode_packet(index_packet(), {1: 100})                         # ... Kotak answers with numeric token + name
    assert type(msg).__name__ == "SFeedIndex" and msg.instrument_token == "26000" and msg.name == "Nifty 50"
    f._handle(msg)
    assert [(t.symbol, t.price) for t in got] == [(INDEX, 25012.34)]


def test_index_tick_with_unexpected_name_still_maps_because_only_one_index_is_subscribed():
    from neo_api_client.websocket.feed import protocol as P
    f, got = feed_for()
    f._handle(P.decode_packet(index_packet(token=99, name=b"Something Else"), {1: 100}))
    assert [t.symbol for t in got] == [INDEX]
    f2, got2 = feed_for()
    f2._handle(P.decode_packet(index_packet(exchange_id=5), {5: 100}))      # a BSE index tick must NOT feed a NIFTY session
    assert got2 == []


def test_option_ticks_map_by_segment_and_token_and_junk_is_ignored():
    from neo_api_client.websocket.feed import SFeedMarketStatus, SFeedScrip
    f, got = feed_for()
    f.subscribe([Subscription("nse_fo", "75007", "NIFTY26OCT25000PE")])
    base = dict(level=4, open_price=1, high_price=1, low_price=1, close_price=1, average_trade_price=1, last_trade_time=1,
                last_update_time=1, last_trade_qty=1, total_buy_quantity=1, total_sell_quantity=1, volume_traded_today=1,
                open_interest=1, net_change=0, net_change_percent=0, upper_circuit_limit=0, lower_circuit_limit=0,
                yearly_high=0, yearly_low=0, total_traded_value=0, market_lot=75, precision=2, multiplier=1)
    f._handle(SFeedScrip(exchange_segment="nse_fo", instrument_token="75007", last_traded_price=93.95, **base))
    f._handle(SFeedScrip(exchange_segment="nse_fo", instrument_token="99999", last_traded_price=10.0, **base))   # not ours
    f._handle(SFeedScrip(exchange_segment="nse_fo", instrument_token="75007", last_traded_price=0.0, **base))     # no price
    f._handle(SFeedMarketStatus(exchange_segment="nse_fo", status_code=1, status="Market open"))
    assert [(t.symbol, t.price) for t in got] == [("NIFTY26OCT25000PE", 93.95)]
    assert f.unknown == 1
