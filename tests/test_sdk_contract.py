"""Checks our calls against the REAL installed `kotakneoapi` SDK (signatures + its own validators).
Skipped when the SDK is not installed. If Kotak changes the SDK these fail loudly BEFORE market open."""
import inspect

import pytest

neo = pytest.importorskip("neo_api_client")
from neo_api_client import req_data_validation as V           # noqa: E402
from neo_api_client.websocket.feed import SFeedIndex, SFeedScrip, WsToken   # noqa: E402

from terminal.brokers.kotak import KotakOrders                  # noqa: E402
from terminal.models import Contract, OrderRequest              # noqa: E402
from conftest import ROOT                                       # noqa: E402
from terminal.config import load_config                         # noqa: E402
from terminal.brokers.kotak import KotakSession                 # noqa: E402
from datetime import date                                       # noqa: E402


def params(fn):
    return set(inspect.signature(fn).parameters)


def test_methods_and_parameters_we_call_exist():
    api = neo.NeoAPI
    assert {"consumer_key", "environment"} <= params(api.__init__)
    assert {"mobile_number", "ucc", "totp"} <= params(api.totp_login)
    assert {"mpin"} <= params(api.totp_validate)
    assert {"exchange_segment", "product", "price", "order_type", "quantity", "validity", "trading_symbol",
            "transaction_type", "amo", "disclosed_quantity", "trigger_price", "tag"} <= params(api.place_order)
    assert "order_id" in params(api.cancel_order) and "order_id" in params(api.order_report)
    assert "exchange_segment" in params(api.scrip_master)
    for name in ("positions", "whatsmyip", "create_websocket", "logout"):
        assert hasattr(api, name), name


def test_feed_message_fields_we_read_exist():
    for model in (SFeedScrip, SFeedIndex):
        assert {"exchange_segment", "instrument_token", "last_traded_price", "trading_symbol"} <= set(model.model_fields)
    assert "name" in SFeedIndex.model_fields
    assert WsToken("nse_fo", "75007").instrument_token == "75007"            # positional (segment, token)


def test_every_order_we_build_passes_the_sdks_own_validation():
    cfg = load_config(ROOT / "config.yaml")
    s = KotakSession()
    sent = []

    class Spy:
        def place_order(self, **kw):
            sent.append(kw)
            return {"stat": "Ok", "nOrdNo": "1", "stCode": 200}

    s.client = Spy()
    for seg, sym in (("nse_fo", "NIFTY26OCT25000PE"), ("bse_fo", "SENSEX26OCT80000PE")):
        c = Contract(sym, "1", seg, 25000, "PE", date.today(), 75)
        o = KotakOrders(s, cfg)
        o.place(OrderRequest(c, "S", 75, "L", 93.95, tag="orb-entry"))
        o.place(OrderRequest(c, "B", 75, "SL", 110.0, trigger=105.0, tag="orb-sl"))
        o.place(OrderRequest(c, "B", 75, "L", 96.5, tag="orb-exit"))
        o.place(OrderRequest(c, "B", 75, "MKT", tag="orb-exit"))
    assert len(sent) == 8
    for kw in sent:
        V.place_order_validation(kw["exchange_segment"], kw["product"], kw["price"], kw["order_type"], kw["quantity"],
                                 kw["validity"], kw["trading_symbol"], kw["transaction_type"], kw["amo"],
                                 kw["disclosed_quantity"], kw["trigger_price"], kw.get("tag"))
