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
from typing import Any

import requests

from ..models import OrderError
from ..trading.models import Funds, OrderRow, OrderTicket, PositionRow, TradeRow
from ..market.catalog import Catalog
from ..market.hub import QuoteHub
from ..market.models import Instrument

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
