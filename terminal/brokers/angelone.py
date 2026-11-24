"""
Angel One SmartAPI broker adapter.

Handles authentication (SmartConnect session with TOTP) and live trading gateway
(place, modify, cancel orders, order book, trade book, open positions, funds).
Includes built-in native REST implementation so it works with or without external smartapi-python package.
"""
from __future__ import annotations

import logging
import math
import os
import re
import uuid
import json
import threading
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import requests

from ..clock import now_ist
from ..models import OrderError, Tick
from ..trading.models import Funds, OrderRow, OrderTicket, PositionRow, TradeRow
from ..market.catalog import Catalog
from ..market.hub import QuoteHub
from ..market.chain import INDEX_UNDERLYINGS, Leg, RawChain, ChainUnavailable
from ..market.models import Quote, Instrument
from ..market import commodity as cm

log = logging.getLogger("angelone")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return default if math.isnan(f) else f
    except (TypeError, ValueError):
        return default


def map_angel_order_status(raw: str) -> str:
    s = (raw or "").strip().lower()
    if "reject" in s:
        return "REJECTED"
    if "cancel" in s:
        return "CANCELLED"
    if "trigger" in s:
        return "TRIGGER_PENDING"
    if any(w in s for w in ("complete", "filled", "traded", "executed")):
        return "COMPLETE"
    if any(w in s for w in ("open", "pending", "put", "validation", "after market", "modif")):
        return "OPEN"
    return "UNKNOWN"


class NativeSmartConnect:
    """Built-in pure REST client for Angel One SmartAPI."""
    _rootUrl = "https://apiconnect.angelone.in"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or ""
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.feed_token: str | None = None
        self.userId: str | None = None
        self.root = self._rootUrl
        self.clientPublicIp = "106.193.147.98"
        try:
            r = requests.get("https://api.ipify.org", timeout=2)
            if r.status_code == 200 and r.text.strip():
                self.clientPublicIp = r.text.strip()
        except Exception:
            pass
        try:
            self.clientMacAddress = ':'.join(re.findall('..', '%012x' % uuid.getnode()))
        except Exception:
            self.clientMacAddress = "fe80::1"

    def requestHeaders(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": self.clientPublicIp,
            "X-MACAddress": self.clientMacAddress,
            "X-PrivateKey": self.api_key,
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    def generateSession(self, clientCode: str, password: str, totp: str) -> dict:
        url = f"{self.root}/rest/auth/angelbroking/user/v1/loginByPassword"
        payload = {"clientcode": clientCode, "password": password, "totp": str(totp).strip()}
        try:
            res = requests.post(url, json=payload, headers=self.requestHeaders(), timeout=12)
            data = res.json()
        except Exception as e:
            return {"status": False, "message": f"Connection error: {e}"}

        if isinstance(data, dict) and data.get("status") is True and data.get("data"):
            ud = data["data"]
            self.access_token = ud.get("jwtToken")
            self.feed_token = ud.get("feedToken")
            self.refresh_token = ud.get("refreshToken")
            self.userId = clientCode
        return data

    def terminateSession(self, clientCode: str) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/user/v1/logout"
        try:
            res = requests.post(url, json={"clientcode": clientCode}, headers=self.requestHeaders(), timeout=8)
            return res.json()
        except Exception:
            return {"status": True}

    def placeOrder(self, orderparams: dict) -> str | None:
        resp = self.placeOrderFullResponse(orderparams)
        if isinstance(resp, dict) and resp.get("status") is True:
            data = resp.get("data")
            if isinstance(data, dict) and data.get("orderid"):
                return str(data["orderid"])
        return None

    def placeOrderFullResponse(self, orderparams: dict) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/order/v1/placeOrder"
        res = requests.post(url, json=orderparams, headers=self.requestHeaders(), timeout=12)
        return res.json()

    def modifyOrder(self, orderparams: dict) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/order/v1/modifyOrder"
        res = requests.post(url, json=orderparams, headers=self.requestHeaders(), timeout=12)
        return res.json()

    def cancelOrder(self, order_id: str, variety: str = "NORMAL") -> dict:
        url = f"{self.root}/rest/secure/angelbroking/order/v1/cancelOrder"
        res = requests.post(url, json={"orderid": str(order_id), "variety": variety}, headers=self.requestHeaders(), timeout=12)
        return res.json()

    def orderBook(self) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/order/v1/getOrderBook"
        res = requests.get(url, headers=self.requestHeaders(), timeout=12)
        return res.json()

    def tradeBook(self) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/order/v1/getTradeBook"
        res = requests.get(url, headers=self.requestHeaders(), timeout=12)
        return res.json()

    def position(self) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/order/v1/getPosition"
        res = requests.get(url, headers=self.requestHeaders(), timeout=12)
        return res.json()

    def rmsLimit(self) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/user/v1/getRMS"
        res = requests.get(url, headers=self.requestHeaders(), timeout=12)
        return res.json()

    def getMarketData(self, mode: str, exchangeTokens: dict) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/market/v1/quote/"
        payload = {"mode": mode, "exchangeTokens": exchangeTokens}
        try:
            res = requests.post(url, json=payload, headers=self.requestHeaders(), timeout=10)
            return res.json()
        except Exception as e:
            return {"status": False, "message": str(e), "data": {"fetched": [], "unfetched": []}}

    def ltpData(self, exchange: str, tradingsymbol: str, symboltoken: str) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/order/v1/getLtpData"
        payload = {"exchange": exchange, "tradingsymbol": tradingsymbol, "symboltoken": str(symboltoken)}
        try:
            res = requests.post(url, json=payload, headers=self.requestHeaders(), timeout=8)
            return res.json()
        except Exception as e:
            return {"status": False, "message": str(e)}

    def getOptionGreek(self, name: str, expirydate: str) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/marketData/v1/optionGreek"
        payload = {"name": name, "expirydate": expirydate}
        try:
            res = requests.post(url, json=payload, headers=self.requestHeaders(), timeout=8)
            return res.json()
        except Exception as e:
            return {"status": False, "message": str(e)}

    def searchScrip(self, exchange: str, searchscrip: str) -> dict:
        url = f"{self.root}/rest/secure/angelbroking/order/v1/searchScrip"
        payload = {"exchange": exchange, "searchscrip": searchscrip}
        try:
            res = requests.post(url, json=payload, headers=self.requestHeaders(), timeout=8)
            return res.json()
        except Exception as e:
            return {"status": False, "message": str(e)}


def _get_smart_client(api_key: str):
    """Return SmartConnect from smartapi-python or fallback to built-in NativeSmartConnect."""
    try:
        from SmartApi import SmartConnect
        return SmartConnect(api_key=api_key)
    except Exception:
        return NativeSmartConnect(api_key=api_key)


class AngelOneSession:
    def __init__(self, creds: dict[str, str] | os._Environ = os.environ) -> None:
        self.creds = creds
        self.client = None
        self.logged_in = False
        self.my_ip: str | None = None
        self.jwt_token: str | None = None
        self.feed_token: str | None = None
        self.refresh_token: str | None = None

    def _need(self, name: str) -> str:
        v = str(self.creds.get(name, "") or "").strip()
        if not v:
            raise RuntimeError(f"{name} is missing for this Angel One account. Check Settings -> Accounts.")
        return v

    def login(self, totp: str | None = None) -> None:
        api_key = self._need("ANGEL_API_KEY")
        client_code = self._need("ANGEL_CLIENT_CODE").upper()
        pin = self._need("ANGEL_PIN")

        if not totp:
            secret = str(self.creds.get("ANGEL_TOTP_SECRET", "") or "").strip()
            if not secret:
                raise RuntimeError("Enter the 6-digit TOTP from your authenticator app")
            import pyotp
            totp = pyotp.TOTP(secret).now()

        client = _get_smart_client(api_key=api_key)
        data = client.generateSession(client_code, pin, str(totp).strip())

        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected reply from Angel One login: {data}")

        if data.get("status") is False:
            msg = data.get("message") or data.get("errorcode") or "Angel One login failed"
            raise RuntimeError(f"Angel One login failed: {msg}")

        user_data = data.get("data") or {}
        self.api_key = api_key
        self.client_code = client_code
        self.pin = pin
        self.jwt_token = user_data.get("jwtToken")
        self.feed_token = user_data.get("feedToken")
        self.refresh_token = user_data.get("refreshToken")
        self.client = client
        self.logged_in = True

        try:
            self.my_ip = getattr(client, "clientPublicIp", None) or None
        except Exception:
            self.my_ip = None

        log.info("Angel One login OK for client %s (IP: %s)", client_code, self.my_ip)

    def logout(self) -> None:
        if self.client and self.logged_in:
            try:
                client_code = str(self.creds.get("ANGEL_CLIENT_CODE", "")).upper()
                self.client.terminateSession(client_code)
            except Exception:
                pass
            self.logged_in = False


class AngelOneGateway:
    simulated = False

    def __init__(self, session: AngelOneSession, catalog: Catalog, hub: QuoteHub) -> None:
        self.s, self.cat, self.hub = session, catalog, hub
        self._order_tags: dict[str, str] = {}

    @property
    def client(self):
        return self.s.client

    def _resolve_exchange(self, segment: str) -> str:
        seg = (segment or "").lower()
        if seg == "nse_fo":
            return "NFO"
        if seg == "bse_fo":
            return "BFO"
        if seg == "mcx_fo":
            return "MCX"
        if seg == "bse_cm":
            return "BSE"
        return "NSE"

    def place(self, inst: Instrument, t: OrderTicket, qty: int) -> str:
        exchange = self._resolve_exchange(inst.segment)
        trans_type = "BUY" if t.side == "B" else "SELL"

        ord_type_map = {
            "L": "LIMIT",
            "MKT": "MARKET",
            "SL": "STOPLOSS_LIMIT",
            "SL-M": "STOPLOSS_MARKET"
        }
        ord_type = ord_type_map.get(t.order_type, "LIMIT")

        prod_type_map = {
            "MIS": "INTRADAY",
            "NRML": "CARRYFORWARD",
            "CNC": "DELIVERY"
        }
        prod_type = prod_type_map.get(t.product, "INTRADAY")

        variety = "STOPLOSS" if t.order_type in ("SL", "SL-M") else "NORMAL"
        price_val = f"{t.price:.2f}" if t.order_type in ("L", "SL") and t.price > 0 else "0"
        trigger_val = f"{t.trigger:.2f}" if t.order_type in ("SL", "SL-M") and t.trigger > 0 else "0"

        orderparams = {
            "variety": variety,
            "tradingsymbol": inst.symbol,
            "symboltoken": str(inst.token),
            "transactiontype": trans_type,
            "exchange": exchange,
            "ordertype": ord_type,
            "producttype": prod_type,
            "duration": "DAY",
            "price": price_val,
            "triggerprice": trigger_val,
            "quantity": str(qty)
        }

        resp = None
        if hasattr(self.client, "placeOrderFullResponse"):
            try:
                resp = self.client.placeOrderFullResponse(orderparams)
            except Exception as e:
                raise OrderError(f"Angel One placeOrder: {e}")
        else:
            try:
                resp = self.client.placeOrder(orderparams)
            except Exception as e:
                raise OrderError(f"Angel One placeOrder: {e}")

        if resp is None:
            raise OrderError("No reply from Angel One for order placement")

        if isinstance(resp, dict):
            if resp.get("status") is False:
                msg = resp.get("message") or resp.get("errorcode") or "Order placement rejected"
                raise OrderError(f"Angel One: {msg}")
            data = resp.get("data")
            if isinstance(data, dict) and data.get("orderid"):
                oid = str(data["orderid"])
                if t.tag:
                    self._order_tags[oid] = t.tag
                return oid
            if resp.get("orderid"):
                oid = str(resp["orderid"])
                if t.tag:
                    self._order_tags[oid] = t.tag
                return oid

        if isinstance(resp, (str, int)):
            oid = str(resp)
            if t.tag:
                self._order_tags[oid] = t.tag
            return oid

        raise OrderError(f"Failed to place order at Angel One: {resp}")

    def modify(self, order_id: str, *, order_type: str, qty: int, price: float, trigger: float, validity: str) -> None:
        ord_type_map = {
            "L": "LIMIT",
            "MKT": "MARKET",
            "SL": "STOPLOSS_LIMIT",
            "SL-M": "STOPLOSS_MARKET"
        }
        variety = "STOPLOSS" if order_type in ("SL", "SL-M") else "NORMAL"
        price_val = f"{price:.2f}" if order_type in ("L", "SL") and price > 0 else "0"
        trigger_val = f"{trigger:.2f}" if order_type in ("SL", "SL-M") and trigger > 0 else "0"

        orderparams = {
            "variety": variety,
            "orderid": str(order_id),
            "ordertype": ord_type_map.get(order_type, "LIMIT"),
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": price_val,
            "quantity": str(qty),
            "triggerprice": trigger_val
        }
        try:
            resp = self.client.modifyOrder(orderparams)
            if isinstance(resp, dict) and resp.get("status") is False:
                msg = resp.get("message") or resp.get("errorcode") or "Order modify rejected"
                raise OrderError(f"Angel One modifyOrder: {msg}")
        except Exception as e:
            if not isinstance(e, OrderError):
                raise OrderError(f"Angel One modifyOrder: {e}")
            raise

    def cancel(self, order_id: str) -> None:
        try:
            resp = self.client.cancelOrder(str(order_id), "NORMAL")
            if isinstance(resp, dict) and resp.get("status") is False:
                resp2 = self.client.cancelOrder(str(order_id), "STOPLOSS")
                if isinstance(resp2, dict) and resp2.get("status") is False:
                    msg = resp.get("message") or resp.get("errorcode") or "Order cancel failed"
                    raise OrderError(f"Angel One cancelOrder: {msg}")
        except Exception as e:
            if not isinstance(e, OrderError):
                raise OrderError(f"Angel One cancelOrder: {e}")
            raise

    def orders(self) -> list[OrderRow] | None:
        try:
            resp = self.client.orderBook()
        except Exception as e:
            log.warning("Angel One orderBook error: %s", e)
            return None

        if not isinstance(resp, dict):
            return None
        if resp.get("status") is False:
            if "no data" in str(resp.get("message", "")).lower():
                return []
            return None
        data = resp.get("data")
        if not isinstance(data, list):
            return []

        out = []
        for r in data:
            if not isinstance(r, dict):
                continue
            oid = str(r.get("orderid") or "")
            tok = str(r.get("symboltoken") or "").strip()
            exch = str(r.get("exchange") or "NSE").lower()
            seg = "nse_fo" if exch == "nfo" else "bse_fo" if exch == "bfo" else "mcx_fo" if exch == "mcx" else f"{exch}_cm"
            key = f"{seg}|{tok}" if tok else ""
            sym = str(r.get("tradingsymbol") or "")
            side = "B" if str(r.get("transactiontype") or "").upper() == "BUY" else "S"
            qty = int(_f(r.get("quantity")))
            filled = int(_f(r.get("filledshares")))
            price = _f(r.get("price"))
            avg_p = _f(r.get("averageprice"))
            trg = _f(r.get("triggerprice"))
            status = map_angel_order_status(str(r.get("orderstatus") or r.get("status") or ""))
            reason = str(r.get("text") or r.get("rejectionreason") or "").strip()
            time_str = str(r.get("updatetime") or r.get("ordertimestamp") or "")

            raw_ot = str(r.get("ordertype") or "").upper()
            ot = "MKT" if "MARKET" in raw_ot and "STOPLOSS" not in raw_ot else "SL" if raw_ot == "STOPLOSS_LIMIT" else "SL-M" if raw_ot == "STOPLOSS_MARKET" else "L"

            raw_pt = str(r.get("producttype") or "").upper()
            prod = "MIS" if "INTRADAY" in raw_pt else "NRML" if "CARRYFORWARD" in raw_pt else "CNC"
            lot = int(_f(r.get("lotsize"), 1)) or 1

            out.append(OrderRow(
                id=oid, key=key, symbol=sym, label=sym, side=side,
                qty=qty, filled=filled, lot=lot, product=prod,
                order_type=ot, price=price, trigger=trg, avg_price=avg_p,
                status=status, reason=reason, time=time_str, validity=str(r.get("duration") or "DAY"),
                tag=self._order_tags.get(oid, "")
            ))
        out.sort(key=lambda o: o.id, reverse=True)
        return out

    def trades(self) -> list[TradeRow] | None:
        try:
            resp = self.client.tradeBook()
        except Exception as e:
            log.warning("Angel One tradeBook error: %s", e)
            return None

        if not isinstance(resp, dict):
            return None
        if resp.get("status") is False:
            if "no data" in str(resp.get("message", "")).lower():
                return []
            return None
        data = resp.get("data")
        if not isinstance(data, list):
            return []

        out = []
        for r in data:
            if not isinstance(r, dict):
                continue
            oid = str(r.get("orderid") or "")
            sym = str(r.get("tradingsymbol") or "")
            tok = str(r.get("symboltoken") or "").strip()
            exch = str(r.get("exchange") or "NSE").lower()
            seg = "nse_fo" if exch == "nfo" else "bse_fo" if exch == "bfo" else "mcx_fo" if exch == "mcx" else f"{exch}_cm"
            key = f"{seg}|{tok}" if tok else ""
            side = "B" if str(r.get("transactiontype") or "").upper() == "BUY" else "S"
            qty = int(_f(r.get("fillshares") or r.get("quantity")))
            price = _f(r.get("fillprice") or r.get("price"))
            raw_pt = str(r.get("producttype") or "").upper()
            prod = "MIS" if "INTRADAY" in raw_pt else "NRML" if "CARRYFORWARD" in raw_pt else "CNC"
            time_str = str(r.get("filltime") or r.get("tradevalue") or "")
            tid = str(r.get("tradeid") or "")

            out.append(TradeRow(
                order_id=oid, key=key, symbol=sym, label=sym, side=side,
                qty=qty, price=price, product=prod, time=time_str, trade_id=tid
            ))
        return out

    def positions(self) -> list[PositionRow] | None:
        try:
            resp = self.client.position()
        except Exception as e:
            log.warning("Angel One position error: %s", e)
            return None

        if not isinstance(resp, dict):
            return None
        if resp.get("status") is False:
            if "no data" in str(resp.get("message", "")).lower():
                return []
            return None
        data = resp.get("data")
        if not isinstance(data, list):
            return []

        out = []
        for r in data:
            if not isinstance(r, dict):
                continue
            tok = str(r.get("symboltoken") or "").strip()
            exch = str(r.get("exchange") or "NSE").lower()
            seg = "nse_fo" if exch == "nfo" else "bse_fo" if exch == "bfo" else "mcx_fo" if exch == "mcx" else f"{exch}_cm"
            key = f"{seg}|{tok}"
            sym = str(r.get("tradingsymbol") or "")
            bq = int(_f(r.get("buyqty") or r.get("totalbuyqty")))
            sq = int(_f(r.get("sellqty") or r.get("totalsellqty")))
            net = int(_f(r.get("netqty")))
            buy_avg = _f(r.get("buyavgprice"))
            sell_avg = _f(r.get("sellavgprice"))
            net_avg = _f(r.get("netprice"))
            ltp = self.hub.ltp(key) or _f(r.get("ltp"))
            realised = _f(r.get("realised"))
            unreal = _f(r.get("unrealised"))
            raw_pt = str(r.get("producttype") or "").upper()
            prod = "MIS" if "INTRADAY" in raw_pt else "NRML" if "CARRYFORWARD" in raw_pt else "CNC"
            lot = int(_f(r.get("lotsize"), 1)) or 1

            out.append(PositionRow(
                key=key, symbol=sym, label=sym, segment=seg, product=prod, lot=lot,
                buy_qty=bq, sell_qty=sq, net_qty=net, buy_avg=round(buy_avg, 2), sell_avg=round(sell_avg, 2),
                avg_price=round(net_avg, 2), ltp=ltp, realised=round(realised, 2), unrealised=round(unreal, 2)
            ))
        return out

    def funds(self) -> Funds | None:
        try:
            resp = self.client.rmsLimit()
        except Exception as e:
            log.warning("Angel One rmsLimit error: %s", e)
            return None

        if not isinstance(resp, dict) or resp.get("status") is False:
            return None
        d = resp.get("data") if isinstance(resp.get("data"), dict) else resp
        avail = _f(d.get("net") or d.get("availablecash"))
        used = _f(d.get("utiliseddebits") or d.get("utilizedamount"))
        coll = _f(d.get("collateral"))
        realised = _f(d.get("m2mrealized"))
        unreal = _f(d.get("m2munrealized"))
        return Funds(
            available=avail, used=used, collateral=coll,
            realised=realised, unrealised=unreal, simulated=False,
            note="From Angel One RMS limits", extra=d
        )


# ------------------------------------------------------------------------- Instruments & Feed
SEGMENT_MAP = {
    "NFO": "nse_fo",
    "BFO": "bse_fo",
    "MCX": "mcx_fo",
    "NSE": "nse_cm",
    "BSE": "bse_cm",
}
REV_EXCHANGE_MAP = {
    "nse_fo": "NFO",
    "bse_fo": "BFO",
    "mcx_fo": "MCX",
    "nse_cm": "NSE",
    "bse_cm": "BSE",
}

ANGEL_INDEX_CARDS = [
    {"label": "NIFTY 50", "key": "nse_cm|Nifty 50", "token": "99926000", "exchange": "NSE", "name": "NIFTY", "lot": 65},
    {"label": "NIFTY BANK", "key": "nse_cm|Nifty Bank", "token": "99926009", "exchange": "NSE", "name": "BANKNIFTY", "lot": 30},
    {"label": "SENSEX", "key": "bse_cm|SENSEX", "token": "99919000", "exchange": "BSE", "name": "SENSEX", "lot": 20},
    {"label": "NIFTY FIN SERVICE", "key": "nse_cm|Nifty Fin Service", "token": "99926037", "exchange": "NSE", "name": "FINNIFTY", "lot": 40},
    {"label": "NIFTY MID SELECT", "key": "nse_cm|NIFTY MID SELECT", "token": "99926074", "exchange": "NSE", "name": "MIDCPNIFTY", "lot": 50},
    {"label": "INDIA VIX", "key": "nse_cm|India VIX", "token": "99926004", "exchange": "NSE", "name": "INDIA VIX", "lot": 1},
]


def parse_angel_expiry(s: str) -> date | None:
    if not s:
        return None
    raw = str(s).strip()
    for fmt in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


class AngelOneCatalog(Catalog):
    """Reads Angel One instruments from OpenAPIScripMaster.json."""

    SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

    def __init__(self, data_dir: str | Path = "data") -> None:
        super().__init__()
        self.data_dir = Path(data_dir)
        self.master_path = self.data_dir / "angel_scrip_master.json"
        self._by_key: dict[str, Instrument] = {}
        self._by_symbol: dict[str, Instrument] = {}
        self._options_by_und_exp: dict[tuple[str, str], list[Instrument]] = {}
        self._expiries_by_und: dict[str, set[str]] = {}
        self._lots: dict[str, int] = {}
        self._fo_underlyings: set[str] = set()
        self._mcx_futures: dict[str, list[Instrument]] = {}
        self._mcx_names: dict[str, dict] = {}
        self._mcx_loaded = False
        self.status = "ready"

        # Pre-register default index cards immediately
        for c in ANGEL_INDEX_CARDS:
            seg = c["key"].split("|", 1)[0]
            tok = c["token"]
            inst = Instrument(c["key"], seg, tok, c["label"], c["name"], "INDEX", lot=c["lot"], tick=0.05)
            self.register(inst)
            self._by_key[c["key"]] = inst
            self._by_key[f"{seg}|{tok}"] = inst
            self._by_symbol[c["label"]] = inst

    def warm_background(self) -> None:
        threading.Thread(target=self._load_master, name="angel-catalog-loader", daemon=True).start()

    def _load_master(self) -> None:
        try:
            raw_data = None
            if self.master_path.exists():
                mtime = self.master_path.stat().st_mtime
                if time.time() - mtime < 86400:  # Fresh within 24 hours
                    try:
                        with open(self.master_path, "r", encoding="utf-8") as f:
                            raw_data = json.load(f)
                    except Exception as e:
                        log.warning("failed reading local angel_scrip_master.json: %s", e)

            if raw_data is None:
                self.status = "loading"
                log.info("Downloading Angel One scrip master from %s ...", self.SCRIP_MASTER_URL)
                req = urllib.request.Request(self.SCRIP_MASTER_URL, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=35) as res:
                    raw_bytes = res.read()
                raw_data = json.loads(raw_bytes.decode("utf-8"))
                self.data_dir.mkdir(parents=True, exist_ok=True)
                with open(self.master_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(raw_data))

            self._index_master(raw_data)
        except Exception as e:
            log.exception("AngelOneCatalog load error: %s", e)
            self.status = "error"

    def _index_master(self, raw_data: list[dict]) -> None:
        opts_map: dict[tuple[str, str], list[Instrument]] = {}
        exps_map: dict[str, set[str]] = {}
        fo_unds: set[str] = set()
        mcx_futs: dict[str, list[Instrument]] = {}
        mcx_nms: dict[str, dict] = {}
        by_key: dict[str, Instrument] = dict(self._by_key)
        by_sym: dict[str, Instrument] = dict(self._by_symbol)
        lots: dict[str, int] = {}

        for row in raw_data:
            tok = str(row.get("token") or "").strip()
            sym = str(row.get("symbol") or "").strip()
            name = str(row.get("name") or "").strip().upper()
            exch = str(row.get("exch_seg") or "").upper()
            itype = str(row.get("instrumenttype") or "").upper()

            seg = SEGMENT_MAP.get(exch, "nse_cm")
            key = f"{seg}|{tok}"
            lot = max(1, int(_f(row.get("lotsize"), 1)))
            tick_raw = _f(row.get("tick_size"), 0.05)
            tick = tick_raw / 100.0 if tick_raw > 1 else max(0.05, tick_raw)
            exp_date = parse_angel_expiry(row.get("expiry") or "")
            raw_strike = _f(row.get("strike"), 0.0)
            strike = (raw_strike / 100.0) if raw_strike > 0 else None

            if itype == "AMXIDX":
                kind = "INDEX"
            elif itype in ("OPTIDX", "OPTSTK", "OPTFUT", "OPTCOM") or sym.endswith(("CE", "PE")):
                kind = "CE" if sym.endswith("CE") else "PE"
            elif itype in ("FUTIDX", "FUTSTK", "FUTCOM", "FUT") or (exch == "MCX" and not sym.endswith(("CE", "PE"))):
                kind = "FUT"
            else:
                kind = "EQ" if exch in ("NSE", "BSE") and not exp_date else "OTHER"

            inst = Instrument(
                key=key, segment=seg, token=tok, symbol=sym, name=name,
                kind=kind, lot=lot, tick=tick, expiry=exp_date, strike=strike, desc=sym
            )
            by_key[key] = inst
            by_sym[sym] = inst

            if kind in ("CE", "PE") and exp_date:
                iso = exp_date.isoformat()
                opts_map.setdefault((name, iso), []).append(inst)
                exps_map.setdefault(name, set()).add(iso)
                fo_unds.add(name)
                lots[name] = lot

            if exch == "MCX":
                if kind == "FUT":
                    mcx_futs.setdefault(name, []).append(inst)
                mcx_nms[name] = {"lot": lot, **cm.info(name)}

        with self._lock:
            self._by_key = by_key
            self._by_symbol = by_sym
            self._options_by_und_exp = opts_map
            self._expiries_by_und = exps_map
            self._fo_underlyings = fo_unds
            self._mcx_futures = mcx_futs
            self._mcx_names = mcx_nms
            self._lots = lots
            self._mcx_loaded = True
            self.status = "ready"
        log.info("AngelOneCatalog indexing complete: %d instruments, %d option buckets", len(by_key), len(opts_map))

    def _lookup(self, key: str) -> Instrument | None:
        with self._lock:
            hit = self._by_key.get(key)
        if hit:
            return hit
        if "|" in key:
            seg, tok = key.split("|", 1)
            with self._lock:
                hit = self._by_symbol.get(tok)
            if hit:
                return hit
        return None

    def search(self, q: str, limit: int = 25) -> list[Instrument]:
        toks = [t for t in re.split(r"[\s,]+", q.strip().upper()) if t]
        if not toks:
            return []
        out: list[Instrument] = []
        with self._lock:
            regs = list(self._reg.values())
            all_insts = list(self._by_key.values())

        for i in regs:
            hay = f"{i.name} {i.symbol}".upper()
            if all(t in hay for t in toks) and i.kind == "INDEX":
                out.append(i)

        for i in all_insts:
            hay = f"{i.name} {i.symbol}".upper()
            if all(t in hay for t in toks):
                out.append(i)
                if len(out) >= limit * 2:
                    break

        seen, uniq = set(), []
        for i in out:
            if i.key not in seen:
                seen.add(i.key)
                uniq.append(i)
        return uniq[:limit]

    def fo_names(self) -> set[str]:
        with self._lock:
            return set(self._fo_underlyings) | {u["name"] for u in INDEX_UNDERLYINGS}

    def fo_underlyings(self, q: str, limit: int = 12) -> list[dict]:
        q = q.strip().upper()
        with self._lock:
            names = sorted(self._fo_underlyings)
        hits = [n for n in names if q in n]
        return [{"name": n, "label": n, "lot": self._lots.get(n, 1), "kind": "stock"} for n in hits[:limit]]

    def expiries_for(self, und: str) -> list[str]:
        target = und.strip().upper()
        today = now_ist().date().isoformat()
        with self._lock:
            exps = self._expiries_by_und.get(target, set())
        return sorted(e for e in exps if e >= today)

    def options_for(self, und: str, expiry: str) -> list[Instrument]:
        with self._lock:
            return sorted(self._options_by_und_exp.get((und.strip().upper(), expiry), []),
                          key=lambda i: (i.strike or 0, i.kind))

    def lot_for(self, und: str, expiry: str = "") -> int:
        target = und.strip().upper()
        with self._lock:
            if (target, expiry) in self._options_by_und_exp:
                opts = self._options_by_und_exp[(target, expiry)]
                if opts:
                    return opts[0].lot
            return self._lots.get(target, 50 if "NIFTY" in target else 1)

    def mcx_names(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._mcx_names)

    def mcx_underlyings(self, q: str, limit: int = 12) -> list[dict]:
        q = q.strip().upper()
        names = self.mcx_names()
        hit = set(cm.resolve(q.split())) | {n for n in names if n.startswith(q)}
        return [{"name": n, "label": names[n].get("label", n), "lot": names[n].get("lot", 1), "size": names[n].get("size", "big")}
                for n in sorted(hit) if n in names][:limit]

    def mcx_future(self, name: str, on_or_after: date | None = None) -> Instrument | None:
        target = name.strip().upper()
        today = now_ist().date()
        target_date = on_or_after or today
        with self._lock:
            futs = self._mcx_futures.get(target, [])
        valid = [f for f in futs if f.expiry and f.expiry >= target_date]
        if not valid:
            valid = [f for f in futs if f.expiry and f.expiry >= today]
        if not valid:
            return None
        valid.sort(key=lambda f: f.expiry)
        return valid[0]

    def mcx_loaded(self) -> bool:
        return self._mcx_loaded


class AngelOneFeed:
    """Live market quote feed for Angel One using REST quote polling and WebSocket."""

    def __init__(self, session_getter, cfg, catalog: AngelOneCatalog | None = None) -> None:
        self.s = session_getter
        self.cfg = cfg
        self.cat = catalog
        self._keys: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.on_quote: Callable[[Quote], None] | None = None
        self._on_tick: Callable[[Tick], None] | None = None
        self.connected = False
        self._ws = None

    def start(self, on_tick: Callable[[Tick], None] | None = None) -> None:
        self._on_tick = on_tick
        threading.Thread(target=self._poll_loop, name="angelone-feed-poll", daemon=True).start()
        threading.Thread(target=self._ws_loop, name="angelone-feed-ws", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            try:
                self._ws.close_connection()
            except Exception:
                pass

    def subscribe_keys(self, keys: list[str]) -> None:
        with self._lock:
            self._keys.update(keys)

    def _poll_once(self) -> None:
        sess = self.s() if callable(self.s) else self.s
        if not sess or not getattr(sess, "logged_in", False) or getattr(sess, "client", None) is None:
            self.connected = False
            return

        with self._lock:
            keys = set(self._keys)

        for c in ANGEL_INDEX_CARDS:
            keys.add(c["key"])

        exchange_tokens: dict[str, set[str]] = {}
        token_to_keys: dict[tuple[str, str], list[str]] = {}

        for k in keys:
            idx = next((c for c in ANGEL_INDEX_CARDS if c["key"] == k), None)
            if idx:
                exch = idx["exchange"]
                tok = idx["token"]
                exchange_tokens.setdefault(exch, set()).add(tok)
                token_to_keys.setdefault((exch, tok), []).append(k)
                token_to_keys.setdefault((exch, tok), []).append(f"{idx['key'].split('|', 1)[0]}|{tok}")
                continue

            if "|" in k:
                seg, tok = k.split("|", 1)
                exch = REV_EXCHANGE_MAP.get(seg.lower(), "NSE")
                if tok.isdigit():
                    exchange_tokens.setdefault(exch, set()).add(tok)
                    token_to_keys.setdefault((exch, tok), []).append(k)

        all_pairs = []
        for exch, toks in exchange_tokens.items():
            for t in toks:
                all_pairs.append((exch, t))

        chunk_size = 50
        for i in range(0, len(all_pairs), chunk_size):
            if self._stop.is_set():
                break
            chunk = all_pairs[i:i + chunk_size]
            chunk_dict: dict[str, list[str]] = {}
            for exch, t in chunk:
                chunk_dict.setdefault(exch, []).append(t)

            try:
                res = sess.client.getMarketData(mode="FULL", exchangeTokens=chunk_dict)
                if isinstance(res, dict) and res.get("status") is True and isinstance(res.get("data"), dict):
                    fetched = res["data"].get("fetched") or []
                    for item in fetched:
                        exch = str(item.get("exchange") or "").upper()
                        tok = str(item.get("symbolToken") or item.get("symboltoken") or "").strip()
                        ltp = _f(item.get("ltp") or item.get("lastPrice"))
                        if ltp <= 0:
                            continue
                        close_p = _f(item.get("close") or item.get("prevClose") or item.get("previousClose"))
                        open_p = _f(item.get("open"))
                        high_p = _f(item.get("high"))
                        low_p = _f(item.get("low"))
                        vol = int(_f(item.get("tradeVolume") or item.get("volume") or 0))
                        oi = int(_f(item.get("opnInterest") or item.get("openInterest") or item.get("oi") or 0))

                        target_keys = token_to_keys.get((exch, tok), [f"{SEGMENT_MAP.get(exch, 'nse_cm')}|{tok}"])
                        for tk in set(target_keys):
                            q = Quote(
                                key=tk,
                                ltp=ltp,
                                prev_close=close_p if close_p > 0 else None,
                                open=open_p if open_p > 0 else None,
                                high=high_p if high_p > 0 else None,
                                low=low_p if low_p > 0 else None,
                                volume=vol if vol > 0 else None,
                                oi=oi if oi > 0 else None
                            )
                            if self.on_quote:
                                self.on_quote(q)
                            if self._on_tick:
                                try:
                                    self._on_tick(Tick(tk, ltp, high=high_p, low=low_p, close=close_p))
                                except Exception:
                                    pass
                    self.connected = True
            except Exception as e:
                log.debug("Angel One market quote poll error: %s", e)

            time.sleep(0.15)

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            if self._stop.wait(1.0):
                break

    def _ws_loop(self) -> None:
        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        except Exception:
            return

        while not self._stop.is_set():
            sess = self.s() if callable(self.s) else self.s
            if not sess or not getattr(sess, "logged_in", False) or not getattr(sess, "jwt_token", None):
                if self._stop.wait(3.0):
                    break
                continue

            try:
                auth_token = sess.jwt_token
                api_key = getattr(sess, "api_key", "") or sess.creds.get("ANGEL_API_KEY", "")
                client_code = getattr(sess, "client_code", "") or sess.creds.get("ANGEL_CLIENT_CODE", "")
                feed_token = getattr(sess, "feed_token", "") or sess.jwt_token

                if not (auth_token and api_key and client_code and feed_token):
                    if self._stop.wait(3.0):
                        break
                    continue

                ws = SmartWebSocketV2(auth_token, api_key, client_code, feed_token)
                self._ws = ws

                def on_data(wsapp, data):
                    if not isinstance(data, dict):
                        return
                    tok = str(data.get("token") or "").strip()
                    ltp_raw = _f(data.get("last_traded_price"))
                    ltp = ltp_raw / 100.0 if ltp_raw > 100000 else ltp_raw
                    if ltp <= 0:
                        return
                    close_raw = _f(data.get("closed_price"))
                    close_p = close_raw / 100.0 if close_raw > 100000 else close_raw
                    open_raw = _f(data.get("open_price_of_the_day"))
                    open_p = open_raw / 100.0 if open_raw > 100000 else open_raw
                    high_raw = _f(data.get("high_price_of_the_day"))
                    high_p = high_raw / 100.0 if high_raw > 100000 else high_raw
                    low_raw = _f(data.get("low_price_of_the_day"))
                    low_p = low_raw / 100.0 if low_raw > 100000 else low_raw
                    vol = int(_f(data.get("volume_trade_for_the_day") or 0))
                    oi = int(_f(data.get("open_interest") or 0))

                    # Map index cards
                    idx = next((c for c in ANGEL_INDEX_CARDS if c["token"] == tok), None)
                    keys = [f"nse_cm|{tok}", f"bse_cm|{tok}", f"nse_fo|{tok}", f"bse_fo|{tok}"]
                    if idx:
                        keys = [idx["key"], f"{idx['key'].split('|', 1)[0]}|{tok}"]

                    for tk in keys:
                        q = Quote(
                            key=tk,
                            ltp=ltp,
                            prev_close=close_p if close_p > 0 else None,
                            open=open_p if open_p > 0 else None,
                            high=high_p if high_p > 0 else None,
                            low=low_p if low_p > 0 else None,
                            volume=vol if vol > 0 else None,
                            oi=oi if oi > 0 else None
                        )
                        if self.on_quote:
                            self.on_quote(q)
                    self.connected = True

                def on_open(wsapp):
                    log.info("Angel One SmartWebSocketV2 connected")
                    self.connected = True
                    # Subscribe tokens
                    with self._lock:
                        keys = set(self._keys)
                    for c in ANGEL_INDEX_CARDS:
                        keys.add(c["key"])
                    tokens_by_seg: dict[int, list[str]] = {}
                    for k in keys:
                        idx = next((c for c in ANGEL_INDEX_CARDS if c["key"] == k), None)
                        if idx:
                            exch_type = 1 if idx["exchange"] == "NSE" else 3
                            tokens_by_seg.setdefault(exch_type, []).append(idx["token"])
                        elif "|" in k:
                            seg, tok = k.split("|", 1)
                            if tok.isdigit():
                                exch_type = 2 if "fo" in seg else 1
                                tokens_by_seg.setdefault(exch_type, []).append(tok)
                    token_list = [{"exchangeType": ex_type, "tokens": toks} for ex_type, toks in tokens_by_seg.items()]
                    if token_list:
                        try:
                            ws.subscribe("quote_sub", 2, token_list)
                        except Exception:
                            pass

                ws.on_data = on_data
                ws.on_open = on_open
                ws.connect()
            except Exception as e:
                log.debug("SmartWebSocketV2 connection loop error: %s", e)

            if self._stop.wait(5.0):
                break


class AngelOneChainProvider:
    source = "angelone"

    def __init__(self, session_getter, catalog: AngelOneCatalog, hub: QuoteHub) -> None:
        self.s = session_getter
        self.cat = catalog
        self.hub = hub

    def underlyings(self) -> list[dict]:
        return INDEX_UNDERLYINGS

    def search(self, q: str, limit: int = 12) -> list[dict]:
        q = q.strip().upper()
        hits = [{"name": u["name"], "label": u["label"], "lot": None, "kind": "index"}
                for u in INDEX_UNDERLYINGS if q in u["name"] or q in u["label"].upper()]
        if hasattr(self.cat, "mcx_underlyings"):
            try:
                hits += [{**c, "kind": "commodity"} for c in self.cat.mcx_underlyings(q, limit)]
            except Exception:
                pass
        if hasattr(self.cat, "fo_underlyings"):
            try:
                stocks = self.cat.fo_underlyings(q, limit)
                seen = {h["name"] for h in hits}
                hits += [{**s, "kind": "stock"} for s in stocks if s["name"] not in seen]
            except Exception:
                pass
        return hits[:limit]

    def check(self, und: str) -> None:
        target = und.strip().upper()
        if any(u["name"] == target for u in INDEX_UNDERLYINGS):
            return
        if hasattr(self.cat, "fo_names") and target in self.cat.fo_names():
            return
        if hasattr(self.cat, "mcx_names") and target in self.cat.mcx_names():
            return
        if getattr(self.cat, "status", "") == "loading":
            return
        raise ChainUnavailable(f"{target} has no options listed at Angel One.")

    def expiries(self, und: str) -> list[str]:
        target = und.strip().upper()
        exps = self.cat.expiries_for(target)
        if not exps:
            if getattr(self.cat, "status", "") == "loading":
                raise ChainUnavailable("Angel One contract master is loading. Please wait a few seconds and try again.")
            raise ChainUnavailable(f"No expiries listed for {target} at Angel One.")
        return exps

    def fetch(self, und: str, expiry: str | None) -> RawChain:
        target = und.strip().upper()
        exps = self.expiries(target)
        if not exps:
            raise ChainUnavailable(f"No option contracts found for {target} at Angel One.")
        pick_exp = expiry if expiry and expiry in exps else exps[0]
        options = self.cat.options_for(target, pick_exp)
        if not options:
            raise ChainUnavailable(f"No strikes found for {target} on expiry {pick_exp}.")

        spot_key = next((u["spot_key"] for u in INDEX_UNDERLYINGS if u["name"] == target), f"nse_cm|{target}")
        spot_val = self.hub.ltp(spot_key)

        # If spot is not in hub yet, try quick LTP lookup via Angel One REST API
        if spot_val is None:
            sess = self.s() if callable(self.s) else self.s
            if sess and getattr(sess, "logged_in", False) and getattr(sess, "client", None) is not None:
                idx_info = next((c for c in ANGEL_INDEX_CARDS if c["name"] == target), None)
                if idx_info:
                    try:
                        res = sess.client.getMarketData(mode="LTP", exchangeTokens={idx_info["exchange"]: [idx_info["token"]]})
                        if isinstance(res, dict) and res.get("status") is True:
                            fetched = (res.get("data") or {}).get("fetched") or []
                            if fetched:
                                spot_val = _f(fetched[0].get("ltp"))
                                if spot_val > 0:
                                    self.hub.update(Quote(key=spot_key, ltp=spot_val))
                    except Exception:
                        pass

        lot = self.cat.lot_for(target, pick_exp)
        raw = RawChain(target, pick_exp, lot, spot_key, spot_val, None)

        for inst in options:
            strike = inst.strike
            if strike is None:
                continue
            kind = inst.kind
            q = self.hub.get(inst.key)
            ltp = q.ltp if q else None
            pc = q.prev_close if q else None
            oi = q.oi if q else None
            vol = q.volume if q else None
            raw.rows.setdefault(strike, {})[kind] = Leg(
                key=inst.key,
                symbol=inst.symbol,
                ltp=ltp,
                prev_close=pc,
                oi=oi,
                oi_prev=None,
                volume=vol
            )

        if spot_val and raw.rows:
            raw.atm_hint = min(raw.rows.keys(), key=lambda k: abs(k - spot_val))

        return raw
