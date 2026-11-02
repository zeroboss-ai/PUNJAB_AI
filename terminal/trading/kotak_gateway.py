"""
Manual-trading gateway on Kotak Neo (official SDK). Field names come from the SDK docs for order_report,
trade_report, positions and limits - see tools/discover.py to confirm them on your own account.

Every reader returns None (not an empty list) when Kotak's answer is missing or unreadable, so the UI can say
"could not load" instead of showing a false "no positions".
"""
from __future__ import annotations

import logging

from ..brokers.kotak import _f, api_error, map_state, modify_order, send_order
from ..market.catalog import Catalog
from ..market.hub import QuoteHub
from ..market.models import Instrument
from ..models import OrderError
from .models import Funds, OrderRow, OrderTicket, PositionRow, TradeRow

log = logging.getLogger("kotak-gateway")


def _rows(resp) -> list[dict] | None:
    if not isinstance(resp, dict) or api_error(resp):
        return None
    data = resp.get("data")
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def _is_empty_reply(resp) -> bool:
    """Kotak answers 'no data' with an error-looking reply when the book is simply empty."""
    if not isinstance(resp, dict) or "Error" in resp or "Error Message" in resp:
        return False                                                   # an exception/timeout is NOT an empty book
    text = str(resp).lower()
    return any(w in text for w in ("no data", "no orders", "no trades", "no position"))


class KotakGateway:
    simulated = False

    def __init__(self, session, catalog: Catalog, hub: QuoteHub) -> None:
        self.s, self.cat, self.hub = session, catalog, hub

    @property
    def client(self):
        return self.s.client

    # ------------------------------------------------------------------ orders
    def place(self, inst: Instrument, t: OrderTicket, qty: int) -> str:
        return send_order(self.client, segment=inst.segment, symbol=inst.symbol, side=t.side, qty=qty, order_type=t.order_type,
                          price=t.price, trigger=t.trigger, product=t.product, validity=t.validity, tag=t.tag)

    def modify(self, order_id: str, *, order_type: str, qty: int, price: float, trigger: float, validity: str) -> None:
        modify_order(self.client, order_id, order_type=order_type, qty=qty, price=price, trigger=trigger, validity=validity)

    def cancel(self, order_id: str) -> None:
        resp = self.client.cancel_order(order_id=str(order_id))
        if isinstance(resp, dict) and str(resp.get("stCode")) == "1021":
            raise OrderError("That order is already completed")
        err = api_error(resp)
        if err:
            raise OrderError(f"cancel_order: {err}")

    def orders(self) -> list[OrderRow] | None:
        resp = self.client.order_report()
        rows = _rows(resp)
        if rows is None:
            return [] if _is_empty_reply(resp) else None
        out = []
        for r in rows:
            seg, tok = str(r.get("exSeg") or "").lower(), str(r.get("tok") or "").strip()
            key = f"{seg}|{tok}" if tok else ""
            lot = int(_f(r.get("lotSz") or r.get("brdLtQty"), 1)) or 1
            rej = str(r.get("rejRsn") or "").strip()
            sym = str(r.get("trdSym") or "")
            out.append(OrderRow(
                id=str(r.get("nOrdNo") or ""), key=key, symbol=sym, label=sym, side=str(r.get("trnsTp") or ""),
                qty=int(_f(r.get("qty"))), filled=int(_f(r.get("fldQty"))), lot=lot, product=str(r.get("prod") or ""),
                order_type=str(r.get("prcTp") or ""), price=_f(r.get("prc")), trigger=_f(r.get("trgPrc")),
                avg_price=_f(r.get("avgPrc")), status=map_state(str(r.get("ordSt") or r.get("stat") or "")),
                reason="" if rej in ("", "--", "-", "NA") else rej, time=str(r.get("ordDtTm") or ""),
                validity=str(r.get("vldt") or "DAY"), tag=str(r.get("GuiOrdId") or "")))
        out.sort(key=lambda o: o.id, reverse=True)
        return out

    def trades(self) -> list[TradeRow] | None:
        resp = self.client.trade_report()
        rows = _rows(resp)
        if rows is None:
            return [] if _is_empty_reply(resp) else None
        keys = {}
        book = self.orders()                                           # trade_report has no token: borrow it from the order book
        for o in book or []:
            keys[o.id] = o.key
        out = []
        for r in rows:
            oid = str(r.get("nOrdNo") or "")
            sym = str(r.get("trdSym") or "")
            out.append(TradeRow(order_id=oid, key=keys.get(oid, ""), symbol=sym, label=sym, side=str(r.get("trnsTp") or ""),
                                qty=int(_f(r.get("fldQty"))), price=_f(r.get("avgPrc")), product=str(r.get("prod") or ""),
                                time=str(r.get("exTm") or r.get("flDt") or ""), trade_id=str(r.get("flId") or "")))
        return out

    # --------------------------------------------------------------- positions
    def positions(self) -> list[PositionRow] | None:
        resp = self.client.positions()
        rows = _rows(resp)
        if rows is None:
            return [] if _is_empty_reply(resp) else None
        out = []
        for r in rows:
            seg, tok = str(r.get("exSeg") or "").lower(), str(r.get("tok") or "").strip()
            key = f"{seg}|{tok}"
            bq = int(_f(r.get("cfBuyQty")) + _f(r.get("flBuyQty")))
            sq = int(_f(r.get("cfSellQty")) + _f(r.get("flSellQty")))
            ba, sa = _f(r.get("cfBuyAmt")) + _f(r.get("buyAmt")), _f(r.get("cfSellAmt")) + _f(r.get("sellAmt"))
            # SDK docs: factor = multiplier * (genNum/genDen) * (prcNum/prcDen)
            mult = _f(r.get("multiplier"), 1) or 1
            gn, gd = _f(r.get("genNum"), 1) or 1, _f(r.get("genDen"), 1) or 1
            pn, pd = _f(r.get("prcNum"), 1) or 1, _f(r.get("prcDen"), 1) or 1
            f = mult * (gn / gd) * (pn / pd)
            net = bq - sq
            ltp = self.hub.ltp(key)
            if ltp is None:
                ltp = _f(r.get("ltp")) or None
            buy_avg = ba / (bq * f) if bq else 0.0
            sell_avg = sa / (sq * f) if sq else 0.0
            closed = min(bq, sq)
            realised = closed * (sell_avg - buy_avg) * f
            total = (sa - ba) + (net * (ltp or 0.0) * f if ltp is not None else 0.0)
            unreal = total - realised if ltp is not None else 0.0
            lot = int(_f(r.get("lotSz") or r.get("brdLtQty"), 1)) or 1
            sym = str(r.get("trdSym") or "")
            out.append(PositionRow(key=key, symbol=sym, label=sym, segment=seg, product=str(r.get("prod") or ""), lot=lot,
                                   buy_qty=bq, sell_qty=sq, net_qty=net, buy_avg=round(buy_avg, 2), sell_avg=round(sell_avg, 2),
                                   avg_price=round(buy_avg if net > 0 else sell_avg, 2) if net else 0.0, ltp=ltp,
                                   realised=round(realised, 2), unrealised=round(unreal, 2), expiry=str(r.get("expDt") or "") or None))
        return out

    def funds(self) -> Funds | None:
        resp = self.client.limits()
        if not isinstance(resp, dict) or api_error(resp):
            return None
        d = resp.get("data") if isinstance(resp.get("data"), dict) else resp
        return Funds(available=_f(d.get("Net")), used=_f(d.get("MarginUsed")), collateral=_f(d.get("CollateralValue")),
                     realised=_f(d.get("RealizedMtomPrsnt")), unrealised=_f(d.get("UnrealizedMtomPrsnt")),
                     simulated=False, note="From Kotak limits(): 'Net' = available, 'MarginUsed' = used.",
                     extra={k: d.get(k) for k in ("AdhocMargin", "NotionalCash", "PremiumPrsnt", "SpanMarginPrsnt",
                                                  "ExposureMarginPrsnt", "RmsPayInAmt", "RmsPayOutAmt") if k in d})
