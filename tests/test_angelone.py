"""
Unit tests for Angel One SmartAPI session, gateway, and vault integration.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from terminal.accounts import AccountVault, VaultError
from terminal.brokers.angelone import (
    AngelOneGateway,
    AngelOneSession,
    map_angel_order_status,
)
from terminal.market.models import Instrument
from terminal.models import OrderError
from terminal.trading.models import OrderTicket


# --------------------------------------------------------------------------- Vault & Accounts
def test_vault_can_save_and_retrieve_angelone_account(tmp_path):
    v = AccountVault(tmp_path / "accounts.enc", env={})
    v.unlock("test-dashboard-password")

    aid = v.add({
        "broker": "angelone",
        "label": "My Angel One",
        "api_key": "smartapi_key_12345",
        "client_code": "A123456",
        "pin": "1234",
        "totp_secret": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP",
        "static_ip": ""
    })

    (a,) = v.public()
    assert a["id"] == aid
    assert a["broker"] == "angelone"
    assert a["label"] == "My Angel One"
    assert a["token"] == "••••2345"
    assert a["has_mpin"] is True
    assert a["has_totp_secret"] is True
    assert "smartapi_key" not in json.dumps(a)

    creds = v.creds(aid)
    assert creds["broker"] == "angelone"
    assert creds["ANGEL_API_KEY"] == "smartapi_key_12345"
    assert creds["ANGEL_CLIENT_CODE"] == "A123456"
    assert creds["ANGEL_PIN"] == "1234"
    assert creds["ANGEL_TOTP_SECRET"] == "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def test_vault_reads_angelone_from_env(tmp_path):
    fake_env = {
        "ANGEL_API_KEY": "env_api_key_9999",
        "ANGEL_CLIENT_CODE": "B987654",
        "ANGEL_PIN": "4321",
        "ANGEL_TOTP_SECRET": "",
        "ANGEL_LABEL": "Primary Angel One"
    }
    v = AccountVault(tmp_path / "accounts.enc", env=fake_env)
    v.unlock("test-dashboard-password")

    pub = v.public()
    assert len(pub) == 1
    a = pub[0]
    assert a["id"] == "env_angel"
    assert a["broker"] == "angelone"
    assert a["label"] == "Primary Angel One"
    assert a["source"] == "env"


def test_vault_validates_angelone_fields(tmp_path):
    v = AccountVault(tmp_path / "accounts.enc", env={})
    v.unlock("test-dashboard-password")

    with pytest.raises(VaultError, match="Angel One API Key is required"):
        v.add({"broker": "angelone", "label": "Bad", "api_key": "", "client_code": "A123", "pin": "1234"})

    with pytest.raises(VaultError, match="Angel One Client Code is required"):
        v.add({"broker": "angelone", "label": "Bad", "api_key": "valid_key_123", "client_code": "", "pin": "1234"})

    with pytest.raises(VaultError, match="trading PIN is required"):
        v.add({"broker": "angelone", "label": "Bad", "api_key": "valid_key_123", "client_code": "A1234", "pin": ""})


# --------------------------------------------------------------------------- AngelOneSession
def test_angelone_session_login_success():
    creds = {
        "ANGEL_API_KEY": "test_api_key_123",
        "ANGEL_CLIENT_CODE": "A111222",
        "ANGEL_PIN": "1234",
        "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    }
    sess = AngelOneSession(creds)

    mock_client = MagicMock()
    mock_client.clientPublicIp = "103.20.15.5"
    mock_client.generateSession.return_value = {
        "status": True,
        "message": "SUCCESS",
        "errorcode": "",
        "data": {
            "jwtToken": "mock_jwt_token",
            "refreshToken": "mock_refresh_token",
            "feedToken": "mock_feed_token"
        }
    }

    with patch("SmartApi.SmartConnect", return_value=mock_client):
        sess.login(totp="654321")
        assert sess.logged_in is True
        assert sess.jwt_token == "mock_jwt_token"
        assert sess.feed_token == "mock_feed_token"
        assert sess.my_ip == "103.20.15.5"
        mock_client.generateSession.assert_called_once_with("A111222", "1234", "654321")


def test_angelone_session_login_failure():
    creds = {
        "ANGEL_API_KEY": "test_api_key_123",
        "ANGEL_CLIENT_CODE": "A111222",
        "ANGEL_PIN": "1234",
    }
    sess = AngelOneSession(creds)

    mock_client = MagicMock()
    mock_client.generateSession.return_value = {
        "status": False,
        "message": "Invalid credentials",
        "errorcode": "AB1004"
    }

    with patch("SmartApi.SmartConnect", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Invalid credentials"):
            sess.login(totp="111111")
        assert sess.logged_in is False


# --------------------------------------------------------------------------- AngelOneGateway
@pytest.fixture
def mock_gw():
    sess = MagicMock(spec=AngelOneSession)
    mock_client = MagicMock()
    sess.client = mock_client
    cat = MagicMock()
    hub = MagicMock()
    hub.ltp.return_value = 100.5
    gw = AngelOneGateway(sess, cat, hub)
    return gw, mock_client


def test_gateway_place_limit_order(mock_gw):
    gw, client = mock_gw
    client.placeOrderFullResponse.return_value = {
        "status": True,
        "data": {"orderid": "24112400001"}
    }

    inst = Instrument("nse_fo|49214", "nse_fo", "49214", "NIFTY28NOV2424000CE", "NIFTY 24000 CE", "OPT")
    ticket = OrderTicket(key="nse_fo|49214", side="B", product="MIS", order_type="L", price=125.5, trigger=0.0, tag="my-tag")
    oid = gw.place(inst, ticket, qty=50)

    assert oid == "24112400001"
    client.placeOrderFullResponse.assert_called_once()
    args = client.placeOrderFullResponse.call_args[0][0]
    assert args["tradingsymbol"] == "NIFTY28NOV2424000CE"
    assert args["symboltoken"] == "49214"
    assert args["transactiontype"] == "BUY"
    assert args["ordertype"] == "LIMIT"
    assert args["producttype"] == "INTRADAY"
    assert args["price"] == "125.50"
    assert args["quantity"] == "50"


def test_gateway_place_market_order(mock_gw):
    gw, client = mock_gw
    client.placeOrderFullResponse.return_value = {
        "status": True,
        "data": {"orderid": "24112400002"}
    }

    inst = Instrument("nse_fo|49214", "nse_fo", "49214", "NIFTY28NOV2424000CE", "NIFTY 24000 CE", "OPT")
    ticket = OrderTicket(key="nse_fo|49214", side="S", product="NRML", order_type="MKT", price=0.0, trigger=0.0)
    oid = gw.place(inst, ticket, qty=25)

    assert oid == "24112400002"
    args = client.placeOrderFullResponse.call_args[0][0]
    assert args["transactiontype"] == "SELL"
    assert args["ordertype"] == "MARKET"
    assert args["producttype"] == "CARRYFORWARD"
    assert args["price"] == "0"


def test_gateway_order_placement_rejected(mock_gw):
    gw, client = mock_gw
    client.placeOrderFullResponse.return_value = {
        "status": False,
        "message": "Insufficient margin",
        "errorcode": "AB2001"
    }

    inst = Instrument("nse_fo|49214", "nse_fo", "49214", "NIFTY28NOV2424000CE", "NIFTY 24000 CE", "OPT")
    ticket = OrderTicket(key="nse_fo|49214", side="B", product="MIS", order_type="L", price=100.0)

    with pytest.raises(OrderError, match="Insufficient margin"):
        gw.place(inst, ticket, qty=50)


def test_gateway_orders_mapping(mock_gw):
    gw, client = mock_gw
    client.orderBook.return_value = {
        "status": True,
        "data": [
            {
                "orderid": "241124001",
                "symboltoken": "49214",
                "exchange": "NFO",
                "tradingsymbol": "NIFTY28NOV2424000CE",
                "transactiontype": "BUY",
                "quantity": 50,
                "filledshares": 50,
                "price": 120.0,
                "averageprice": 120.0,
                "triggerprice": 0.0,
                "orderstatus": "complete",
                "producttype": "INTRADAY",
                "ordertype": "LIMIT",
                "lotsize": "50",
                "updatetime": "24-Nov-2024 10:15:00",
                "text": ""
            }
        ]
    }

    orders = gw.orders()
    assert orders is not None
    assert len(orders) == 1
    o = orders[0]
    assert o.id == "241124001"
    assert o.key == "nse_fo|49214"
    assert o.side == "B"
    assert o.qty == 50
    assert o.filled == 50
    assert o.status == "COMPLETE"
    assert o.product == "MIS"
    assert o.order_type == "L"


def test_gateway_positions_mapping(mock_gw):
    gw, client = mock_gw
    client.position.return_value = {
        "status": True,
        "data": [
            {
                "symboltoken": "49214",
                "exchange": "NFO",
                "tradingsymbol": "NIFTY28NOV2424000CE",
                "producttype": "INTRADAY",
                "buyqty": 50,
                "sellqty": 0,
                "netqty": 50,
                "buyavgprice": 120.0,
                "sellavgprice": 0.0,
                "netprice": 120.0,
                "realised": 0.0,
                "unrealised": 250.0,
                "ltp": 125.0,
                "lotsize": "50"
            }
        ]
    }

    positions = gw.positions()
    assert positions is not None
    assert len(positions) == 1
    p = positions[0]
    assert p.key == "nse_fo|49214"
    assert p.net_qty == 50
    assert p.buy_qty == 50
    assert p.buy_avg == 120.0
    assert p.product == "MIS"
    assert p.realised == 0.0


def test_gateway_funds_mapping(mock_gw):
    gw, client = mock_gw
    client.rmsLimit.return_value = {
        "status": True,
        "data": {
            "net": 150000.0,
            "availablecash": 120000.0,
            "utiliseddebits": 30000.0,
            "collateral": 50000.0,
            "m2mrealized": 1500.0,
            "m2munrealized": 500.0
        }
    }

    f = gw.funds()
    assert f is not None
    assert f.available == 150000.0
    assert f.used == 30000.0
    assert f.collateral == 50000.0
    assert f.realised == 1500.0
    assert f.unrealised == 500.0
    assert f.simulated is False


def test_status_mapping():
    assert map_angel_order_status("complete") == "COMPLETE"
    assert map_angel_order_status("rejected") == "REJECTED"
    assert map_angel_order_status("cancelled") == "CANCELLED"
    assert map_angel_order_status("trigger pending") == "TRIGGER_PENDING"
    assert map_angel_order_status("open") == "OPEN"


# --------------------------------------------------------------------------- Catalog & Feed & Chain
def test_angelone_catalog_indexing(tmp_path):
    from terminal.brokers.angelone import AngelOneCatalog

    cat = AngelOneCatalog(data_dir=tmp_path)
    # Check default index cards pre-registered
    nifty = cat.get("nse_cm|Nifty 50")
    assert nifty is not None
    assert nifty.name == "NIFTY"
    assert nifty.token == "99926000"

    sensex = cat.get("bse_cm|SENSEX")
    assert sensex is not None
    assert sensex.name == "SENSEX"
    assert sensex.token == "99919000"

    # Index sample data
    from datetime import date
    sample_data = [
        {
            "token": "35352", "symbol": "NIFTY28NOV2424000CE", "name": "NIFTY",
            "expiry": "28NOV2026", "strike": "2400000.000000", "lotsize": "65",
            "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.0"
        },
        {
            "token": "35353", "symbol": "NIFTY28NOV2424000PE", "name": "NIFTY",
            "expiry": "28NOV2026", "strike": "2400000.000000", "lotsize": "65",
            "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.0"
        },
        {
            "token": "556625", "symbol": "CRUDEOIL18DEC26FUT", "name": "CRUDEOIL",
            "expiry": "18DEC2026", "strike": "0.0", "lotsize": "100",
            "instrumenttype": "FUTCOM", "exch_seg": "MCX", "tick_size": "100.0"
        }
    ]
    cat._index_master(sample_data)

    ce = cat.get("nse_fo|35352")
    assert ce is not None
    assert ce.symbol == "NIFTY28NOV2424000CE"
    assert ce.strike == 24000.0
    assert ce.kind == "CE"
    assert ce.lot == 65

    exps = cat.expiries_for("NIFTY")
    assert "2026-11-28" in exps

    opts = cat.options_for("NIFTY", "2026-11-28")
    assert len(opts) == 2

    crude_fut = cat.mcx_future("CRUDEOIL")
    assert crude_fut is not None
    assert crude_fut.token == "556625"
    assert crude_fut.name == "CRUDEOIL"
    assert crude_fut.kind == "FUT"


def test_angelone_chain_provider_and_feed(tmp_path):
    from terminal.brokers.angelone import AngelOneCatalog, AngelOneFeed, AngelOneChainProvider
    from terminal.market.hub import QuoteHub
    from terminal.market.chain import analyse

    hub = QuoteHub()
    cat = AngelOneCatalog(data_dir=tmp_path)
    sample_data = [
        {
            "token": "35352", "symbol": "NIFTY28NOV2624000CE", "name": "NIFTY",
            "expiry": "28NOV2026", "strike": "2400000.000000", "lotsize": "65",
            "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.0"
        },
        {
            "token": "35353", "symbol": "NIFTY28NOV2624000PE", "name": "NIFTY",
            "expiry": "28NOV2026", "strike": "2400000.000000", "lotsize": "65",
            "instrumenttype": "OPTIDX", "exch_seg": "NFO", "tick_size": "5.0"
        }
    ]
    cat._index_master(sample_data)

    mock_client = MagicMock()
    mock_sess = MagicMock()
    mock_sess.client = mock_client
    mock_sess.logged_in = True

    provider = AngelOneChainProvider(lambda: mock_sess, cat, hub)
    unds = provider.underlyings()
    assert any(u["name"] == "NIFTY" for u in unds)

    exps = provider.expiries("NIFTY")
    assert "2026-11-28" in exps

    # Simulate hub spot
    from terminal.market.models import Quote
    hub.update(Quote("nse_cm|Nifty 50", ltp=24050.0))

    raw = provider.fetch("NIFTY", "2026-11-28")
    assert raw.underlying == "NIFTY"
    assert raw.expiry == "2026-11-28"
    assert raw.lot == 65
    assert raw.spot == 24050.0
    assert 24000.0 in raw.rows
    assert "CE" in raw.rows[24000.0]
    assert "PE" in raw.rows[24000.0]

    # Run analyse
    ans = analyse(raw, hub, window=12)
    assert ans["spot"] == 24050.0
    assert ans["atm"] == 24000.0
    assert len(ans["rows"]) == 1

    # Test feed pushing quotes
    feed = AngelOneFeed(lambda: mock_sess, MagicMock(), catalog=cat)
    feed.on_quote = hub.update
    feed.subscribe_keys(["nse_cm|Nifty 50", "nse_fo|35352"])

    mock_client.getMarketData.return_value = {
        "status": True,
        "data": {
            "fetched": [
                {
                    "exchange": "NSE",
                    "symbolToken": "99926000",
                    "ltp": 24100.5,
                    "close": 24000.0,
                    "open": 24020.0,
                    "high": 24120.0,
                    "low": 23990.0
                },
                {
                    "exchange": "NFO",
                    "symbolToken": "35352",
                    "ltp": 150.25,
                    "close": 120.0,
                    "open": 130.0,
                    "high": 160.0,
                    "low": 115.0,
                    "tradeVolume": 5000,
                    "opnInterest": 25000
                }
            ]
        }
    }

    # Run one pass of feed poll
    with patch("time.sleep"):
        feed._poll_once()

    # Verify hub received both quotes
    q_idx = hub.get("nse_cm|Nifty 50")
    assert q_idx is not None
    assert q_idx.ltp == 24100.5

    q_opt = hub.get("nse_fo|35352")
    assert q_opt is not None
    assert q_opt.ltp == 150.25
    assert q_opt.volume == 5000
    assert q_opt.oi == 25000
