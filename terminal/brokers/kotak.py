"""
Kotak Neo adapter (official `kotakneoapi` SDK, import name `neo_api_client`).

VERIFIED against the SDK source and docs: PyPI 3.0.9 and GitHub Kotak-Neo/kotak-neo-python HEAD
4843eb8 (v3.0.10, 2026-10-23). Things the SDK documents and this file relies on:
  * every SDK call returns a dict - it does NOT raise. Failures come back as {"Error": exc},
    {"Error Message": "..."}, {"error": [...]}, a non-"Ok" `stat`, or even None (network error).
  * index ticks arrive as `SFeedIndex` (numeric token + `name`), option ticks as `SFeedScrip`;
    prices are already scaled to rupees.
  * scrip_master(exchange_segment=...) returns a CSV *URL*; columns pSymbol, pTrdSymbol, pSymbolName,
    pOptionType, pExpiryDate, "dStrikePrice;" (strike x 100), lLotSize.
  * order_report(order_id=...) fetches one order; fields nOrdNo, ordSt, fldQty, avgPrc, rejRsn.
  * positions(): trdSym, flBuyQty/flSellQty/cfBuyQty/cfSellQty (in UNITS; divide by lotSz for lots).

What still cannot be known without a live account is marked  # LIVE-CHECK  and is exercised by
`python tools/discover.py` (read-only, places no orders).
"""
from __future__ import annotations

import asyncio
import inspect
import io
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

from ..clock import now_ist
from ..config import Config
from ..models import INDEX, Contract, OrderError, OrderRequest, OrderStatus, OrderUnknown, Tick
from .base import Subscription

log = logging.getLogger("kotak")

NSE_EXPIRY_OFFSET_S = 315511200            # SDK: scrip_search.py adds this for nse_fo expiry values
_ERR_KEYS = ("Error", "Error Message", "error", "Errors")


# scrip-master columns we use (lower-case, without the stray ';' Kotak puts on dStrikePrice)
_SCRIP_COLS = {"psymbol", "psymbolname", "ptrdsymbol", "poptiontype", "pexpirydate", "lexpirydate", "dstrikeprice",
               "llotsize", "ilotsize", "dticksize", "pdesc", "pgroup"}


# --------------------------------------------------------------------------- helpers
def _find(obj: Any, keys: tuple[str, ...]) -> Any:
    """First non-empty value for any of `keys` anywhere inside nested dict/list `obj` (case-insensitive)."""
    want = {k.lower() for k in keys}
    stack = [obj]
    while stack:
        cur = stack.pop(0)
        if isinstance(cur, dict):
            for k, v in cur.items():
                if str(k).lower() in want and v not in (None, ""):
                    return v
            stack.extend(cur.values())
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return None


def _rows(resp: Any) -> list[dict]:
    if isinstance(resp, dict):
        data = resp.get("data", resp.get("Data"))
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            return [data]
        if data is None and any(k in resp for k in ("nOrdNo", "trdSym")):
            return [resp]
        return []
    if isinstance(resp, list):
        return [r for r in resp if isinstance(r, dict)]
    return []


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fmt(price: float) -> str:
    return f"{price:.2f}"


def _short(v: Any, n: int = 240) -> str:
    """Human-readable error text from the odd shapes the SDK returns (never dumps tokens)."""
    if isinstance(v, BaseException):
        return f"{type(v).__name__}: {v}"[:n]
    if isinstance(v, list):
        parts = [str(x.get("message", x)) if isinstance(x, dict) else str(x) for x in v]
        return "; ".join(parts)[:n]
    return str(v)[:n]


def api_error(resp: Any) -> str | None:
    """None if `resp` looks like a successful order/portfolio reply, else a short reason."""
    if resp is None:
        return "no response from Kotak (network error?)"
    if not isinstance(resp, dict):
        return f"unexpected reply: {str(resp)[:160]}"
    for k in _ERR_KEYS:
        if resp.get(k):
            return f"{k}: {_short(resp[k])}"
    code = resp.get("stCode")
    stat = str(resp.get("stat", "")).strip().lower()
    if stat and stat != "ok":
        msg = resp.get("emsg") or resp.get("errMsg") or resp.get("message") or resp.get("rejRsn") or resp.get("stat")
        extra = f": {resp['rejRsn']}" if resp.get("rejRsn") else ""
        return f"{_short(msg)}{extra} (stCode {code})"
    if code not in (None, 200, "200"):
        msg = resp.get("errMsg") or resp.get("emsg") or resp.get("rejRsn") or resp.get("message") or "error"
        extra = f": {resp['rejRsn']}" if resp.get("rejRsn") else ""
        return f"{_short(msg)}{extra} (stCode {code})"
    return None


def map_state(raw: str) -> str:
    s = (raw or "").strip().lower()
    if "reject" in s:
        return "REJECTED"
    if "cancel" in s:
        return "CANCELLED"
    if "trigger" in s:
        return "TRIGGER_PENDING"
    if any(w in s for w in ("complete", "filled", "traded", "executed")):
        return "COMPLETE"
    # SDK OrderStatus: "open pending", "open"; Kotak also uses "put order req received", "validation pending"
    if any(w in s for w in ("open", "pending", "put", "validation", "after market", "modif")):
        return "OPEN"
    return "UNKNOWN"


def parse_expiry(v: Any, today: date, segment: str | None = None) -> date | None:
    """Convert a scrip-master pExpiryDate to a date, exactly like the SDK's search_scrip does:
         nse_fo            : 1970-01-01 + n seconds + 315511200 s   (NSE epoch, 1980-based)
         bse_fo / mcx_fo   : 1970-01-01 + n seconds                 (plain Unix epoch)
       With no `segment` (or if that lands outside a sane window) both encodings are tried."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        s = str(v).strip()
        for fmt in ("%d%b%Y", "%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d/%m/%Y", "%d%b%y"):
            try:
                return datetime.strptime(s.title(), fmt).date()
            except ValueError:
                pass
        return None
    unix = datetime(1970, 1, 1) + timedelta(seconds=n)
    nse = unix + timedelta(seconds=NSE_EXPIRY_OFFSET_S)
    order = [unix, nse] if segment in ("bse_fo", "mcx_fo") else [nse, unix]
    lo, hi = today - timedelta(days=1), today + timedelta(days=800)
    for cand in order:
        if lo <= cand.date() <= hi:
            return cand.date()
    return None


def _token(v: Any) -> str:
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v).strip()


# --------------------------------------------------------------------------- session
def _login_problem(resp: Any) -> str:
    if isinstance(resp, dict):
        for k in ("error", "Error", "Error Message", "emsg", "errMsg", "message"):
            if resp.get(k):
                return _short(resp[k])
        data = resp.get("data")
        if isinstance(data, dict) and data.get("status"):
            return f"status={data['status']}"
    return "no valid reply from Kotak" if resp in (None, {}) else "unrecognised reply"


def _check_login(resp: Any, step: str, need_trade: bool) -> None:
    data = resp.get("data") if isinstance(resp, dict) else None
    if isinstance(data, dict) and str(data.get("status", "")).lower() == "success":
        if need_trade and str(data.get("kType", "")).lower() != "trade":
            raise RuntimeError(f"{step}: logged in but without trading access (kType={data.get('kType')})")
        return
    raise RuntimeError(f"{step} failed: {_login_problem(resp)}")


class KotakSession:
    def __init__(self, env=os.environ) -> None:
        self.env = env
        self.client = None
        self.logged_in = False
        self.my_ip: str | None = None          # IP Kotak sees - must equal the static IP you registered

    def _need(self, name: str) -> str:
        v = self.env.get(name, "").strip()
        if not v:
            raise RuntimeError(f"{name} is missing for this account. Add it under Settings, in the Accounts box (or in the .env file).")
        return v

    def login(self, totp: str | None = None) -> None:
        from neo_api_client import NeoAPI          # imported late so tests run without the SDK
        if not totp:
            secret = self.env.get("NEO_TOTP_SECRET", "").strip()
            if not secret:
                raise RuntimeError("Enter the 6-digit TOTP from your authenticator app")
            import pyotp
            totp = pyotp.TOTP(secret).now()
        self.logged_in = False
        client = NeoAPI(consumer_key=self._need("NEO_CONSUMER_KEY").strip(), environment="prod")
        mob = self._need("NEO_MOBILE_NUMBER").strip()
        ucc = self._need("NEO_UCC").strip().upper()
        clean_digits = re.sub(r"\D", "", mob)
        if len(clean_digits) == 12 and clean_digits.startswith("91"):
            clean_digits = clean_digits[2:]

        candidates = [mob]
        if clean_digits:
            for alt in (clean_digits, f"+91{clean_digits}", f"91{clean_digits}"):
                if alt not in candidates:
                    candidates.append(alt)

        r1 = None
        for m in candidates:
            r1 = client.totp_login(mobile_number=m, ucc=ucc, totp=str(totp).strip())
            data = r1.get("data") if isinstance(r1, dict) else None
            if isinstance(data, dict) and str(data.get("status", "")).lower() == "success":
                break
            prob = _login_problem(r1).lower()
            if "not found" not in prob and "mobile" not in prob:
                break  # another error like wrong TOTP, don't keep trying phone formats

        _check_login(r1, "TOTP login", need_trade=False)
        r2 = client.totp_validate(mpin=self._need("NEO_MPIN").strip())
        _check_login(r2, "MPIN validation", need_trade=True)
        self.client, self.logged_in = client, True
        try:
            self.my_ip = str(client.whatsmyip()["data"][0]["ip"])
        except Exception:
            self.my_ip = None
        log.info("Kotak login OK (outbound IP seen by Kotak: %s)", self.my_ip)

    def logout(self) -> None:
        try:
            if self.client:
                self.client.logout()
        finally:
            self.logged_in = False


# ------------------------------------------------------------------------- instruments
class KotakInstruments:
    def __init__(self, session: KotakSession, cfg: Config) -> None:
        self.s, self.cfg = session, cfg
        self._cache: dict[tuple[str, date], Any] = {}

    def index_subscription(self, instrument: str) -> Subscription:
        i = self.cfg.instruments[instrument]
        return Subscription(i.index_segment, i.index_token, INDEX)

    @staticmethod
    def _download(url: str) -> bytes:
        import httpx                               # installed with the SDK
        r = httpx.get(url, timeout=60, follow_redirects=True)
        r.raise_for_status()
        return r.content

    @staticmethod
    def _csv_url(resp: Any, segment: str) -> str:
        if isinstance(resp, str):
            return resp
        if isinstance(resp, dict):
            err = api_error(resp)
            if err:
                raise RuntimeError(f"scrip_master failed: {err}")
            for p in resp.get("filesPaths") or []:
                base = str(p).rsplit("/", 1)[-1].lower()
                if base == f"{segment}.csv" or base.startswith(f"{segment}-"):
                    return str(p)
        raise RuntimeError(f"scrip_master() did not return a file for {segment}: {str(resp)[:200]}")

    def _load(self, segment: str):
        import pandas as pd
        key = (segment, now_ist().date())
        if key in self._cache:
            return self._cache[key]
        url = self._csv_url(self.s.client.scrip_master(exchange_segment=segment), segment)
        df = pd.read_csv(io.BytesIO(self._download(url)), low_memory=False,
                         usecols=lambda c: str(c).strip().rstrip(";").strip().lower() in _SCRIP_COLS)   # keep memory small
        df.columns = [str(c).strip().rstrip(";").strip().lower() for c in df.columns]   # "dStrikePrice;" -> dstrikeprice
        self._cache = {key: df}
        return df

    def candidates(self, instrument: str, ref_price: float, window: int, expiry_offset: int = 0):
        icfg = self.cfg.instruments[instrument]
        seg = icfg.option_segment
        df = self._load(seg)
        cols = set(df.columns)

        def pick(*names: str) -> str:
            for n in names:
                if n in cols:
                    return n
            raise RuntimeError(f"scrip master for {seg} has none of the columns {names}; it has {sorted(cols)[:30]}")

        c_name, c_trd, c_tok = pick("psymbolname"), pick("ptrdsymbol"), pick("psymbol")
        c_opt, c_strike = pick("poptiontype"), pick("dstrikeprice")
        c_exp, c_lot = pick("pexpirydate", "lexpirydate"), pick("llotsize", "ilotsize")

        und = icfg.underlying.upper()
        is_opt = df[c_opt].astype(str).str.upper().isin(["CE", "PE"])
        sub = df[is_opt & (df[c_name].astype(str).str.strip().str.upper() == und)].copy()
        if sub.empty:                              # fallback: trading symbol like NIFTY26OCT25000CE
            ts = df[c_trd].astype(str).str.strip().str.upper()
            sub = df[is_opt & ts.str.match(rf"^{und}\d")].copy()
            if not sub.empty:
                log.warning("matched %s options by trading-symbol prefix (pSymbolName did not equal %s)", und, und)
        if sub.empty:
            raise RuntimeError(f"No {icfg.underlying} CE/PE rows in the {seg} scrip master")

        today = now_ist().date()
        sub["_expiry"] = sub[c_exp].map(lambda v: parse_expiry(v, today, seg))
        sub["_strike"] = sub[c_strike].astype(float) / 100.0        # SDK: strikes are stored x100
        sub["_opt"] = sub[c_opt].astype(str).str.upper()
        expiries = sorted({d for d in sub["_expiry"].dropna() if d >= today})
        if len(expiries) <= expiry_offset:
            raise RuntimeError(f"Expiry #{expiry_offset} not found; available: {expiries[:5]}")
        expiry = expiries[expiry_offset]
        sub = sub[sub["_expiry"] == expiry]
        strikes = sorted(sub["_strike"].unique())
        atm_ref = min(strikes, key=lambda s: abs(s - ref_price))
        i = strikes.index(atm_ref)
        out = {}
        for k in strikes[max(0, i - window): i + window + 1]:
            pair = []
            for ot in ("CE", "PE"):
                r = sub[(sub["_strike"] == k) & (sub["_opt"] == ot)]
                if r.empty:
                    break
                r = r.iloc[0]
                lot = int(_f(r[c_lot]))
                if lot < 1:
                    raise RuntimeError(f"Bad lot size {r[c_lot]!r} for {r[c_trd]}")
                pair.append(Contract(str(r[c_trd]).strip(), _token(r[c_tok]), seg, float(k), ot, expiry, lot))
            if len(pair) == 2:
                out[float(k)] = (pair[0], pair[1])
        if not out:
            raise RuntimeError("No usable strikes found around the index level")
        return out


# ------------------------------------------------------------------------------- feed
class KotakFeed:
    """SFeed websocket (async) run in its own thread; ticks are pushed to the engine's queue.

    Index ticks come back as `SFeedIndex` carrying a NUMERIC token and a `name` ("Nifty 50");
    option ticks come back as `SFeedScrip` carrying the numeric pSymbol we subscribed with.
    """

    def __init__(self, session: KotakSession, cfg: Config) -> None:
        self.s, self.cfg = session, cfg
        self._subs: dict[str, Subscription] = {}
        self._by_key: dict[tuple[str, str], str] = {}          # (segment, token) -> internal symbol
        self._name_map: dict[tuple[str, str], str] = {}        # (segment, normalised index NAME) -> internal symbol
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: asyncio.Queue | None = None
        self._on_tick: Callable[[Tick], None] | None = None
        self.on_quote: Callable[[Any], None] | None = None     # full quote (OHLC / volume / OI) for the shared hub
        self.unknown = 0
        self.connected = False
        i = cfg.inst
        self._idx_seg = i.index_segment.lower()
        self._idx_names = {a.lower() for a in [*i.index_aliases, i.index_token]}

    def start(self, on_tick: Callable[[Tick], None]) -> None:
        self._on_tick = on_tick
        threading.Thread(target=lambda: asyncio.run(self._main()), name="kotak-feed", daemon=True).start()

    def subscribe(self, subs: list[Subscription]) -> None:
        with self._lock:
            for s in subs:
                self._subs[s.symbol] = s
                seg = s.segment.lower().strip()
                tok = str(s.token).strip()
                self._by_key[(seg, tok)] = s.symbol
                if not tok.isdigit():                       # indices are subscribed by NAME
                    self._name_map[(seg, _norm(tok))] = s.symbol
        if self._loop and self._pending is not None:
            asyncio.run_coroutine_threadsafe(self._pending.put(list(subs)), self._loop)

    def subscribe_keys(self, keys: list[str]) -> None:
        """Hub entry point: key is "<segment>|<token>" and doubles as the internal symbol."""
        self.subscribe([Subscription(k.split("|", 1)[0], k.split("|", 1)[1], k) for k in keys])

    def stop(self) -> None:
        self._stop.set()

    async def _do_subscribe(self, ws, tokens: list[Subscription], WsToken) -> None:
        idx_tokens = [WsToken(x.segment.lower().strip(), str(x.token).strip()) for x in tokens if not str(x.token).strip().isdigit()]
        scrip_tokens = [WsToken(x.segment.lower().strip(), str(x.token).strip()) for x in tokens if str(x.token).strip().isdigit()]
        if idx_tokens:
            try:
                await ws.subscribe_index(idx_tokens)
            except Exception as e:
                log.warning("subscribe_index error: %r", e)
        if scrip_tokens:
            try:
                await ws.subscribe_scrips(scrip_tokens)
            except Exception as e:
                log.warning("subscribe_scrips error: %r", e)

    async def _main(self) -> None:
        from neo_api_client.websocket.feed import WsToken
        self._loop = asyncio.get_running_loop()
        self._pending = asyncio.Queue()
        while not self._stop.is_set():
            try:
                async with self.s.client.create_websocket() as ws:
                    self.connected = True
                    with self._lock:
                        known = list(self._subs.values())
                    if known:
                        await self._do_subscribe(ws, known, WsToken)
                    pump = asyncio.create_task(self._pump(ws, WsToken))
                    try:
                        async for message in ws:
                            if self._stop.is_set():
                                break
                            self._handle(message)
                    finally:
                        pump.cancel()
            except Exception as e:                                         # reconnect and resubscribe everything
                log.warning("feed error: %r - reconnecting in 3s", e)
            self.connected = False
            await asyncio.sleep(3)

    async def _pump(self, ws, WsToken) -> None:
        while True:
            batch = await self._pending.get()
            await self._do_subscribe(ws, batch, WsToken)

    def _handle(self, message: Any) -> None:
        """Duck-typed on purpose: SFeedScrip, SFeedScripLite and SFeedIndex all carry last_traded_price;
        market-status / CAS messages do not and are ignored."""
        ltp = getattr(message, "last_traded_price", None)
        if ltp is None:
            return
        price = _f(ltp, 0.0)
        if price <= 0:
            return
        seg = str(getattr(message, "exchange_segment", "") or "").lower().strip()
        tok = str(getattr(message, "instrument_token", "") or "").strip()
        name = str(getattr(message, "name", "") or "").lower().strip()               # SFeedIndex only
        tsym = str(getattr(message, "trading_symbol", "") or "").lower().strip()
        sym = self._by_key.get((seg, tok))
        if sym is None:
            for (s_seg, s_tok), v in self._by_key.items():
                if s_tok == tok and (s_seg == seg or s_seg.startswith(seg) or seg.startswith(s_seg)):
                    sym = v
                    break
        if sym is None and getattr(message, "type", "") == "index":
            sym = self._name_map.get((seg, _norm(name))) or self._name_map.get((seg, _norm(tsym.split("-")[0])))
            if sym is None:
                on_seg = [v for (sg, _), v in self._name_map.items() if sg == seg]
                if len(set(on_seg)) == 1:                       # exactly one index subscribed on this segment: it is that one
                    sym = on_seg[0]
        if sym is None and seg == self._idx_seg and not self._name_map:
            # legacy single-index algo path (no hub): any index tick on the index segment is our index
            if (getattr(message, "type", "") == "index" or name in self._idx_names
                    or tsym in self._idx_names or tok.lower() in self._idx_names):
                sym = INDEX
        if sym is None:
            self.unknown += 1
            if self.unknown <= 5:
                log.warning("tick for unmapped instrument segment=%r token=%r name=%r symbol=%r",
                            seg, tok, name, tsym)
            return
        if self._on_tick:
            self._on_tick(Tick(sym, price, now_ist()))
        if self.on_quote:
            self.on_quote(quote_from_message(sym, message, price))


def _norm(v: Any) -> str:
    return "".join(str(v or "").lower().split())


def quote_from_message(key: str, m: Any, price: float):
    """SFeedScrip / SFeedScripLite / SFeedIndex -> hub Quote (fields a message lacks stay None)."""
    from ..market.models import Quote

    def num(attr, cast=float):
        v = getattr(m, attr, None)
        try:
            return cast(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def pos(v):
        return v if v and v > 0 else None            # a 0 open/high/low/close means "not set yet"

    return Quote(key=key, ltp=price, prev_close=pos(num("close_price")), open=pos(num("open_price")),
                 high=pos(num("high_price")), low=pos(num("low_price")), volume=num("volume_traded_today", int),
                 oi=num("open_interest", int), ts=time.time())


# ----------------------------------------------------------------------------- orders
def _definite(resp: dict) -> bool:
    """True if an {"Error": ...} reply is a local validation problem or an HTTP 4xx (nothing was placed)."""
    if "Error Message" in resp:                                   # "Complete the 2fa process ..."
        return True
    err = resp.get("Error")
    if isinstance(err, (ValueError, TypeError)):                 # SDK ApiValueError/ApiTypeError (validation)
        return True
    status = getattr(err, "status", None)                        # SDK ApiException: Kotak answered with HTTP status
    return isinstance(status, int) and 400 <= status < 500       # 4xx = refused; 5xx/timeouts stay "unknown"


def send_order(client, *, segment: str, symbol: str, side: str, qty: int, order_type: str, price: float = 0.0,
               trigger: float = 0.0, product: str = "MIS", validity: str = "DAY", tag: str = "") -> str:
    """Place one order through the SDK and return Kotak's order number.
    Raises OrderError when Kotak/the SDK definitely refused it, OrderUnknown when we cannot tell."""
    fn = client.place_order
    mkt = order_type in ("MKT", "SL-M")
    kw = dict(exchange_segment=segment, product=product, price="0" if mkt else _fmt(price), order_type=order_type,
              quantity=str(int(qty)), validity=validity, trading_symbol=symbol, transaction_type=side, amo="NO",
              disclosed_quantity="0", trigger_price=_fmt(trigger) if trigger else "0")
    if tag and tag != "manual" and "tag" in inspect.signature(fn).parameters:        # `tag` exists in 3.0.9+
        kw["tag"] = tag[:20]
    resp = fn(**kw)
    oid = _find(resp, ("nOrdNo",)) if isinstance(resp, dict) else None
    err = api_error(resp)
    if err is None and oid is not None:
        return str(oid)
    log.warning("place_order failed. payload=%s response=%s", kw, resp)
    text = f"place_order: {err or 'reply had no order number'} (resp: {resp})"
    if resp is None or (isinstance(resp, dict) and ("Error" in resp or "Error Message" in resp) and not _definite(resp)):
        raise OrderUnknown(text)                                 # timeout etc.: may or may not have gone through
    raise OrderError(text)


def modify_order(client, order_id: str, *, order_type: str, qty: int, price: float, trigger: float = 0.0,
                 validity: str = "DAY") -> None:
    mkt = order_type in ("MKT", "SL-M")
    resp = client.modify_order(order_id=str(order_id), price="0" if mkt else _fmt(price), order_type=order_type,
                               quantity=str(int(qty)), validity=validity, trigger_price=_fmt(trigger) if trigger else "0")
    err = api_error(resp)
    if err:
        if resp is None or (isinstance(resp, dict) and ("Error" in resp) and not _definite(resp)):
            raise OrderUnknown(f"modify_order: {err}")
        raise OrderError(f"modify_order: {err}")


class KotakOrders:
    def __init__(self, session: KotakSession, cfg: Config) -> None:
        self.s, self.cfg = session, cfg

    def place(self, req: OrderRequest) -> str:
        c = req.contract
        return send_order(self.s.client, segment=c.segment, symbol=c.symbol, side=req.side, qty=req.qty,
                          order_type=req.order_type, price=req.price, trigger=req.trigger, tag=req.tag)

    def cancel(self, order_id: str) -> None:
        resp = self.s.client.cancel_order(order_id=str(order_id))
        if isinstance(resp, dict) and str(resp.get("stCode")) == "1021":   # SDK: ORDER_ALREADY_COMPLETE_ST_CODE
            return                                                         # already traded - the status check decides
        err = api_error(resp)
        if err:
            raise OrderError(f"cancel_order: {err}")

    def status(self, order_id: str) -> OrderStatus:
        oid = str(order_id)
        rows: list[dict] = []
        reasons: list[str] = []
        for call in (lambda: self.s.client.order_report(order_id=oid), lambda: self.s.client.order_report()):
            try:
                rep = call()
            except Exception as e:
                reasons.append(repr(e))
                continue
            err = api_error(rep)
            if err:
                reasons.append(err)
                continue
            rows = [r for r in _rows(rep) if str(r.get("nOrdNo", "")) == oid]
            if rows:
                break
        if not rows:
            return OrderStatus(oid, "UNKNOWN", 0, 0.0, "; ".join(reasons) or "order not in order report yet")
        r = max(rows, key=lambda x: _f(x.get("updRecvTm")))             # newest if history rows are returned
        rej = str(r.get("rejRsn") or "").strip()
        rej = "" if rej in ("--", "-", "NA") else rej
        return OrderStatus(oid, map_state(str(r.get("ordSt") or r.get("stat") or "")),
                           int(_f(r.get("fldQty"))), _f(r.get("avgPrc")), rej)

    def positions(self) -> dict[str, int] | None:
        """symbol -> net quantity in UNITS, or None if the position book could not be read."""
        try:
            resp = self.s.client.positions()
        except Exception as e:
            log.warning("positions() failed: %r", e)
            return None
        if resp is None or not isinstance(resp, dict):
            return None
        if isinstance(resp.get("data"), list):
            out: dict[str, int] = {}
            for r in resp["data"]:
                sym = str(r.get("trdSym") or "").strip()
                if not sym:
                    continue
                buy = _f(r.get("flBuyQty")) + _f(r.get("cfBuyQty"))
                sell = _f(r.get("flSellQty")) + _f(r.get("cfSellQty"))
                out[sym] = out.get(sym, 0) + int(buy - sell)
            return out
        text = json.dumps(resp, default=str).lower()
        if "no data" in text or "no position" in text or "not found" in text:
            return {}                                                   # genuinely empty book
        if api_error(resp) is None and "data" not in resp:
            return {}
        log.warning("positions() unreadable: %s", api_error(resp) or text[:200])
        return None
