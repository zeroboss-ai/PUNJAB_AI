"""
HTTP + WebSocket API for the terminal UI.

Security model (single owner per instance, bound to localhost by default):
  * one password -> random session cookie (HttpOnly, SameSite=Strict); sessions are in memory only
  * every state-changing request must carry the custom header `X-Terminal: 1` (a cross-site page cannot send it)
  * the WebSocket checks the cookie AND that the Origin matches the Host (blocks cross-site WebSocket hijacking)
  * login attempts are rate limited; nothing secret (Kotak keys, MPIN, TOTP secret) is ever sent to the browser
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..market.chain import ChainUnavailable
from ..market.charts import TIMEFRAMES
from ..rules.strategies import describe, parse, templates
from ..models import OrderError
from ..services import Services, instrument_meta, market_status, mcx_status
from ..trading.models import OrderTicket

log = logging.getLogger("web")
DIST = Path(__file__).parent / "dist"
COOKIE = "nt_session"
SESSION_TTL = 12 * 3600
KEY_RE = re.compile(r"^[a-z_]{2,10}\|[A-Za-z0-9 &.\-_]{1,40}$")
MAX_WS_KEYS = 400


# ---------------------------------------------------------------------------------- bodies
class LoginIn(BaseModel):
    password: str = Field(max_length=200)


class ConnectIn(BaseModel):
    totp: str = Field("", max_length=12)
    account_id: str = Field("", max_length=20)
    confirm_live: bool = False


class AccountIn(BaseModel):
    label: str = Field("", max_length=40)
    consumer_key: str = Field("", max_length=300)
    mobile: str = Field("", max_length=24)
    ucc: str = Field("", max_length=30)
    mpin: str = Field("", max_length=12)
    totp_secret: str = Field("", max_length=80)
    clear_totp_secret: bool = False
    static_ip: str = Field("", max_length=64)


class NetworkIn(BaseModel):
    mode: Literal["auto", "own"]
    own_ips: str = Field("", max_length=120)


class NetworkConfirmIn(BaseModel):
    v4: bool = False
    v6: bool = False


class OrderIn(BaseModel):
    key: str = Field(max_length=80)
    side: Literal["B", "S"]
    lots: int = Field(0, ge=0, le=1000)
    qty: int = Field(0, ge=0, le=10_000_000)
    product: Literal["MIS", "NRML", "CNC"] = "MIS"
    order_type: Literal["L", "MKT", "SL", "SL-M"] = "L"
    price: float = Field(0, ge=0, le=10_000_000)
    trigger: float = Field(0, ge=0, le=10_000_000)
    validity: Literal["DAY", "IOC"] = "DAY"
    client_id: str = Field("", max_length=64)


class ModifyIn(BaseModel):
    order_type: Literal["L", "MKT", "SL", "SL-M"]
    lots: int = Field(0, ge=0, le=1000)
    qty: int = Field(0, ge=0, le=10_000_000)
    price: float = Field(0, ge=0, le=10_000_000)
    trigger: float = Field(0, ge=0, le=10_000_000)
    validity: Literal["DAY", "IOC"] = "DAY"


class ModeIn(BaseModel):
    mode: str = Field(max_length=8)
    confirm: str = Field(default="", max_length=16)


class DrawingsIn(BaseModel):
    drawings: list[dict] = Field(default_factory=list, max_length=60)


class NameIn(BaseModel):
    name: str = Field(max_length=40)


class KeyIn(BaseModel):
    key: str = Field(max_length=80)


class KeysIn(BaseModel):
    keys: list[str] | None = Field(None, max_length=200)


class OrderIdsIn(BaseModel):
    keys: list[str] = Field(max_length=MAX_WS_KEYS)


class PauseIn(BaseModel):
    paused: bool = True


class StrategyIn(BaseModel):
    spec: dict


class BacktestIn(BaseModel):
    strategy_id: int = Field(ge=1)
    days: list[str] = Field(min_length=1, max_length=60)
    start_time: str = Field("09:10", pattern=r"^\d{2}:\d{2}$")
    cost: float = Field(0, ge=0, le=100000)


class SampleDaysIn(BaseModel):
    days: int = Field(20, ge=3, le=40)


class StartIn(BaseModel):
    confirm_live: bool = False


class NoteIn(BaseModel):
    body: str = Field("", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=8)


# ------------------------------------------------------------------------------------ app
def create_app(svc: Services, password: str) -> FastAPI:
    if len(password) < 8:
        raise RuntimeError("DASHBOARD_PASSWORD must be set in .env and be at least 8 characters")
    svc.vault.unlock(password)
    app = FastAPI(title="PUNJAB AI", docs_url=None, redoc_url=None, openapi_url=None)
    sessions: dict[str, float] = {}
    fails: dict[str, list[float]] = {}
    recent_orders: dict[str, tuple[float, dict]] = {}
    order_lock = threading.Lock()

    # ------------------------------------------------------------------ security helpers
    def valid_token(tok: str | None) -> bool:
        if not tok:
            return False
        exp = sessions.get(tok)
        if exp is None:
            return False
        if exp < time.time():
            sessions.pop(tok, None)
            return False
        return True

    def auth(request: Request) -> None:
        if not valid_token(request.cookies.get(COOKIE)):
            raise HTTPException(401, "Not logged in")

    def guard(request: Request) -> None:
        auth(request)
        if request.headers.get("x-terminal") != "1":
            raise HTTPException(403, "Missing X-Terminal header")

    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                                   "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'none'")
        if request.url.path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def fail(msg: str, code: int = 400) -> JSONResponse:
        return JSONResponse({"ok": False, "error": msg}, status_code=code)

    def safe(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except OrderError as e:
            return fail(str(e))
        except HTTPException:
            raise
        except Exception as e:
            log.exception("api error")
            return fail(f"{type(e).__name__}: {str(e)[:200]}", 500)

    # ----------------------------------------------------------------------------- auth
    @app.get("/api/auth/status")
    def auth_status(request: Request):
        return {"logged_in": valid_token(request.cookies.get(COOKIE)), "mode": svc.mode}

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response):
        if request.headers.get("x-terminal") != "1":
            raise HTTPException(403, "Missing X-Terminal header")
        ip = request.client.host if request.client else "?"
        now = time.time()
        recent = [t for t in fails.get(ip, []) if now - t < 300]
        if len(recent) >= 5:
            return fail("Too many wrong passwords. Wait 5 minutes.", 429)
        if not hmac.compare_digest(body.password.encode(), password.encode()):
            fails[ip] = recent + [now]
            time.sleep(0.4)
            return fail("Wrong password", 401)
        fails.pop(ip, None)
        tok = secrets.token_urlsafe(32)
        sessions[tok] = now + SESSION_TTL
        response.set_cookie(COOKIE, tok, max_age=SESSION_TTL, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
        return {"ok": True}

    @app.post("/api/auth/logout", dependencies=[Depends(guard)])
    def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get(COOKIE, ""), None)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # --------------------------------------------------------------------------- session
    def activity() -> dict:
        """What is running right now; the phone's bottom bar shows it as small badges."""
        try:
            eng = svc.algo.engine
            return {"strategies": len(svc.rules.running()), "algo": bool(eng and not eng.finished.is_set())}
        except Exception:
            return {"strategies": 0, "algo": False}

    def ip_alert() -> str | None:
        """A warning line for the top of every page when a LIVE setup would have its orders rejected for the IP."""
        if svc.mode != "live":
            return None
        try:
            st = svc.network_status()
        except Exception:
            return None
        return st["message"] if st["bad"] or st["verdict"] == "unset" else None

    @app.get("/api/health")
    @app.get("/healthz")
    def health_check():
        return {"ok": True, "status": "healthy", "mode": svc.mode}

    @app.get("/api/session", dependencies=[Depends(auth)])
    def session_info():
        try:
            hub = svc.hub
            age = hub.age_s()
            feed = svc.feed
            lim = svc.trading.limits
            return {
                "mode": svc.mode, "needs_connect": svc.needs_connect, "connected": svc.connected,
                "my_ip": getattr(svc.session, "my_ip", None), "ip_alert": ip_alert(), "activity": activity(), "market": market_status(), "market_mcx": mcx_status(close_override=svc.cfg.mcx_close_time), "commodities": svc.commodity_cards(),
                "feed": {"connected": bool(getattr(feed, "connected", False)), "age_s": None if age is None else round(age, 1),
                         "ticks": hub.tick_count, "subscribed": len(hub.wanted())},
                "catalog_loading": svc.catalog.loading, "indices": svc.index_cards(), "account": svc.account,
                "limits": {"max_lots_per_order": lim.max_lots_per_order, "max_order_value": lim.max_order_value,
                           "block_after_loss": lim.block_after_loss, "max_daily_loss": svc.cfg.risk.max_daily_loss,
                           "squareoff_buffer_pct": lim.squareoff_buffer_pct},
            }
        except Exception as e:
            import traceback
            traceback.print_exc()
            log.exception("session_info failed")
            raise HTTPException(500, detail=str(e) + "\n" + traceback.format_exc())

    @app.get("/api/mode", dependencies=[Depends(auth)])
    def mode_get():
        return {"ok": True, **svc.mode_info()}

    @app.post("/api/mode", dependencies=[Depends(guard)])
    def mode_set(body: ModeIn):
        with order_lock:
            return guarded(lambda: {"ok": True, **svc.set_mode(body.mode, body.confirm)})

    @app.post("/api/connect", dependencies=[Depends(guard)])
    def connect(body: ConnectIn):
        def go():
            svc.connect(body.totp.strip() or None, body.account_id.strip(), body.confirm_live)
            return {"ok": True}
        return guarded(go)

    @app.post("/api/disconnect", dependencies=[Depends(guard)])
    def disconnect():
        def go():
            svc.disconnect()
            return {"ok": True}
        return guarded(go)

    # ------------------------------------------------------------------------ saved accounts
    def accounts_view() -> dict:
        v = svc.vault
        return {"ok": True, "mode": svc.mode, "can_connect": svc.mode != "sample", "connected": svc.connected,
                "active": svc.account["id"] if svc.account else None, "default": v.default_id(), "readable": v.readable, "problem": v.problem,
                "accounts": v.public(svc.account["id"] if svc.account else None)}

    @app.get("/api/accounts", dependencies=[Depends(auth)])
    def accounts_list():
        return accounts_view()

    @app.post("/api/accounts", dependencies=[Depends(guard)])
    def accounts_add(body: AccountIn):
        def go():
            aid = svc.vault.add(body.model_dump())
            return {**accounts_view(), "id": aid}
        return guarded(go)

    @app.put("/api/accounts/{aid}", dependencies=[Depends(guard)])
    def accounts_update(aid: str, body: AccountIn):
        def go():
            if svc.account and svc.account["id"] == aid:
                raise RuntimeError("This account is connected. Disconnect it before editing it.")
            svc.vault.update(aid, body.model_dump())
            return accounts_view()
        return guarded(go)

    @app.delete("/api/accounts/{aid}", dependencies=[Depends(guard)])
    def accounts_delete(aid: str):
        def go():
            if svc.account and svc.account["id"] == aid:
                raise RuntimeError("This account is connected. Disconnect it before deleting it.")
            svc.vault.delete(aid)
            return accounts_view()
        return guarded(go)

    @app.post("/api/accounts/{aid}/default", dependencies=[Depends(guard)])
    def accounts_default(aid: str):
        return guarded(lambda: (svc.vault.set_default(aid), accounts_view())[1])

    # --------------------------------------------------------------------------- static IP
    @app.get("/api/network", dependencies=[Depends(auth)])
    def network_get():
        return svc.network_status()

    @app.post("/api/network/detect", dependencies=[Depends(guard)])
    def network_detect():
        def go():
            svc.netip.detect(force=True)
            return svc.network_status()
        return guarded(go)

    @app.put("/api/network", dependencies=[Depends(guard)])
    def network_set(body: NetworkIn):
        def go():
            svc.netip.set_policy(body.mode, body.own_ips)
            return svc.network_status()
        return guarded(go)

    @app.post("/api/network/confirm", dependencies=[Depends(guard)])
    def network_confirm(body: NetworkConfirmIn):
        def go():
            svc.netip.confirm_registered(body.v4, body.v6)
            return svc.network_status()
        return guarded(go)

    # ---------------------------------------------------------------------- market data
    @app.get("/api/quotes", dependencies=[Depends(auth)])
    def quotes(keys: str = ""):
        ks = [k for k in keys.split(",") if KEY_RE.match(k)][:100]
        return {"quotes": [q.to_wire() for q in svc.hub.snapshot(ks)]}

    @app.get("/api/instrument", dependencies=[Depends(auth)])
    def instrument(key: str = ""):
        """One instrument's lot, tick and kind as the server knows it (the order ticket uses this for commodities)."""
        if not KEY_RE.match(key):
            raise HTTPException(422, "Bad key")
        i = svc.catalog.get(key)
        if i is None:
            raise HTTPException(404, "Unknown instrument")
        return {"instrument": i.to_dict()}

    # ---------------------------------------------------------------------- charts
    @app.get("/api/chart", dependencies=[Depends(auth)])
    def chart(key: str = "", tf: str = "5", limit: int = 600):
        if not KEY_RE.match(key):
            raise HTTPException(422, "Bad key")
        inst = svc.catalog.get(key)
        if inst is None:
            raise HTTPException(404, "Unknown instrument")
        if tf not in TIMEFRAMES:
            raise HTTPException(422, "Timeframe must be one of " + ", ".join(TIMEFRAMES))
        svc.charts.watch(key)
        data = svc.charts.candles(key, tf, limit)
        q = svc.hub.get(key)
        return {"ok": True, "key": key, "inst": inst.to_dict(), "invented": svc.mode == "sample",
                "last": None if q is None else {"ltp": q.ltp, "prev_close": q.prev_close}, **data}

    @app.get("/api/chart/recent", dependencies=[Depends(auth)])
    def chart_recent():
        out = []
        for k in svc.charts.recent():
            i = svc.catalog.get(k)
            out.append({"key": k, "label": i.label() if i else k})
        return {"recent": out}

    @app.get("/api/chart/drawings", dependencies=[Depends(auth)])
    def chart_drawings(key: str = ""):
        if not KEY_RE.match(key):
            raise HTTPException(422, "Bad key")
        return {"drawings": svc.charts.drawings(key)}

    @app.put("/api/chart/drawings", dependencies=[Depends(guard)])
    def chart_drawings_set(key: str, body: DrawingsIn):
        if not KEY_RE.match(key):
            raise HTTPException(422, "Bad key")
        return guarded(lambda: {"ok": True, "drawings": svc.charts.set_drawings(key, body.drawings)})

    @app.get("/api/search", dependencies=[Depends(auth)])
    def search(q: str = "", limit: int = 25):
        q = q.strip()[:60]
        res = svc.catalog.search(q, max(1, min(limit, 40))) if len(q) >= 1 else []
        return {"loading": svc.catalog.loading and not res, "results": [i.to_dict() for i in res]}

    # ---------------------------------------------------------------------- watchlists
    @app.get("/api/watchlists", dependencies=[Depends(auth)])
    def watchlists():
        return {"lists": svc.store.watchlists()}

    @app.post("/api/watchlists", dependencies=[Depends(guard)])
    def wl_create(body: NameIn):
        return safe(lambda: {"ok": True, "id": svc.store.create_watchlist(body.name)})

    @app.patch("/api/watchlists/{wl_id}", dependencies=[Depends(guard)])
    def wl_rename(wl_id: int, body: NameIn):
        return safe(lambda: (svc.store.rename_watchlist(wl_id, body.name), {"ok": True})[1])

    @app.delete("/api/watchlists/{wl_id}", dependencies=[Depends(guard)])
    def wl_delete(wl_id: int):
        return safe(lambda: (svc.store.delete_watchlist(wl_id), {"ok": True})[1])

    @app.post("/api/watchlists/{wl_id}/items", dependencies=[Depends(guard)])
    def wl_add(wl_id: int, body: KeyIn):
        def go():
            inst = svc.catalog.get(body.key)
            if inst is None:
                raise OrderError("Unknown instrument - search for it again")
            svc.store.add_item(wl_id, body.key, instrument_meta(inst))
            return {"ok": True}
        try:
            return safe(go)
        except ValueError as e:
            return fail(str(e))

    @app.delete("/api/watchlists/{wl_id}/items", dependencies=[Depends(guard)])
    def wl_remove(wl_id: int, key: str):
        return safe(lambda: (svc.store.remove_item(wl_id, key), {"ok": True})[1])

    @app.put("/api/watchlists/{wl_id}/order", dependencies=[Depends(guard)])
    def wl_order(wl_id: int, body: OrderIdsIn):
        return safe(lambda: (svc.store.reorder_items(wl_id, body.keys), {"ok": True})[1])

    # ---------------------------------------------------------------------------- trading
    def view(fn):
        def go():
            if svc.gateway is None:
                return {"ok": False, "rows": [], "reason": "not_connected"}
            return fn()
        return safe(go)

    @app.get("/api/orders", dependencies=[Depends(auth)])
    def orders():
        return view(svc.trading.orders_view)

    @app.get("/api/trades", dependencies=[Depends(auth)])
    def trades():
        return view(svc.trading.trades_view)

    @app.get("/api/positions", dependencies=[Depends(auth)])
    def positions():
        return view(svc.trading.positions_view)

    @app.get("/api/funds", dependencies=[Depends(auth)])
    def funds():
        def go():
            if svc.gateway is None:
                return {"ok": False, "reason": "not_connected"}
            f = svc.gateway.funds()
            return {"ok": f is not None, "funds": f.to_dict() if f else None}
        return safe(go)

    @app.post("/api/orders", dependencies=[Depends(guard)])
    def place(body: OrderIn):
        def go():
            now = time.time()
            for k in [k for k, (t, _) in recent_orders.items() if now - t > 120]:
                recent_orders.pop(k, None)
            if body.client_id and body.client_id in recent_orders:          # a double-click / retry: same answer, no 2nd order
                return recent_orders[body.client_id][1]
            with order_lock:
                res = svc.trading.place(OrderTicket(body.key, body.side, body.lots, body.qty, body.product, body.order_type,
                                                    body.price, body.trigger, body.validity))
            out = {"ok": True, **res}
            if body.client_id:
                recent_orders[body.client_id] = (now, out)
            return out
        return safe(go)

    @app.put("/api/orders/{order_id}", dependencies=[Depends(guard)])
    def modify(order_id: str, body: ModifyIn):
        def go():
            with order_lock:
                svc.trading.modify(order_id, order_type=body.order_type, lots=body.lots, qty=body.qty, price=body.price,
                                   trigger=body.trigger, validity=body.validity)
            return {"ok": True}
        return safe(go)

    @app.delete("/api/orders/{order_id}", dependencies=[Depends(guard)])
    def cancel(order_id: str):
        def go():
            with order_lock:
                svc.trading.cancel(order_id)
            return {"ok": True}
        return safe(go)

    @app.post("/api/squareoff/preview", dependencies=[Depends(guard)])
    def squareoff_preview(body: KeysIn):
        return safe(lambda: {"ok": True, "orders": svc.trading.squareoff_preview(body.keys)})

    @app.post("/api/squareoff", dependencies=[Depends(guard)])
    def squareoff(body: KeysIn):
        def go():
            with order_lock:
                return {"ok": True, "results": svc.trading.squareoff(body.keys)}
        return safe(go)

    @app.post("/api/panic", dependencies=[Depends(guard)])
    def panic():
        def go():
            with order_lock:
                return {"ok": True, **svc.trading.panic()}
        return safe(go)

    # ----------------------------------------------------------------------- option chain + OI
    UND_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")
    EXP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    def chain_call(fn, und: str = ""):
        try:
            if not svc.chain.ready:
                return {"ok": False, "underlying": und, "reason": "not_connected"}
            return fn()
        except ChainUnavailable as e:
            return {"ok": False, "underlying": und, "reason": "unavailable", "error": str(e)}
        except HTTPException:
            raise
        except Exception as e:
            log.exception("chain error")
            return {"ok": False, "underlying": und, "reason": "unavailable", "error": f"{type(e).__name__}: {str(e)[:160]}"}

    @app.get("/api/chain/underlyings", dependencies=[Depends(auth)])
    def chain_underlyings():
        return {"ok": svc.chain.ready, "underlyings": svc.chain.underlyings()}

    @app.get("/api/chain/search", dependencies=[Depends(auth)])
    def chain_search(q: str = "", limit: int = 12):
        """Find any index or F&O stock that has options, to open its chain."""
        q = q.strip()[:30]
        return chain_call(lambda: {"ok": True, "results": svc.chain.search(q, max(1, min(limit, 30))) if q else []})

    @app.get("/api/chain", dependencies=[Depends(auth)])
    def chain(underlying: str = "NIFTY", expiry: str = "", window: int = 12):
        und = underlying.strip().upper()
        if not UND_RE.match(und) or (expiry and not EXP_RE.match(expiry)):
            raise HTTPException(422, "Bad underlying or expiry")
        window = max(0, min(window, 60))
        return chain_call(lambda: {"ok": True, **svc.chain.get(und, expiry or None, window or None)}, und=und)

    @app.get("/api/chain/series", dependencies=[Depends(auth)])
    def chain_series(underlying: str, expiry: str):
        und = underlying.strip().upper()
        if not UND_RE.match(und) or not EXP_RE.match(expiry):
            raise HTTPException(422, "Bad underlying or expiry")
        return {"ok": True, "points": svc.chain.series(und, expiry)}

    # ------------------------------------------------------------------------------ algo
    @app.get("/api/algo", dependencies=[Depends(auth)])
    def algo_state():
        return svc.algo.snapshot()

    @app.post("/api/algo/start", dependencies=[Depends(guard)])
    def algo_start():
        return safe(lambda: (svc.algo.start(), {"ok": True})[1])

    @app.post("/api/algo/pause", dependencies=[Depends(guard)])
    def algo_pause(body: PauseIn):
        return safe(lambda: (svc.algo.pause(body.paused), {"ok": True})[1])

    @app.post("/api/algo/kill", dependencies=[Depends(guard)])
    def algo_kill():
        return safe(lambda: (svc.algo.kill(), {"ok": True})[1])

    # ------------------------------------------------------------------- rule strategies
    def guarded(fn):
        def go():
            try:
                return fn()
            except (ValueError, RuntimeError, KeyError) as e:
                return fail(str(e).strip("'") or type(e).__name__)
        return safe(go)

    def checked(spec: dict):
        st, errs, warns = parse(spec)
        return st, errs, warns

    def strategy_row(row: dict) -> dict:
        st, errs, warns = parse(row["spec"])
        snap = svc.rules.status(row["id"])
        return {"id": row["id"], "name": row["name"], "spec": row["spec"], "valid": st is not None, "errors": errs, "warnings": warns,
                "summary": describe(st) if st else [], "state": snap, "running": row["id"] in svc.rules.running(), "updated": row["updated"]}

    @app.get("/api/strategy-templates", dependencies=[Depends(auth)])
    def strategy_templates():
        out = []
        for t in templates():
            st, _, _ = parse(t["spec"])
            out.append({**t, "spec": st.to_json()})                    # fully normalised: every field present with its default
        return {"ok": True, "templates": out}

    @app.post("/api/strategies/validate", dependencies=[Depends(guard)])
    def strategies_validate(body: StrategyIn):
        st, errs, warns = checked(body.spec)
        return {"ok": st is not None, "errors": errs, "warnings": warns, "summary": describe(st) if st else []}

    @app.get("/api/strategies", dependencies=[Depends(auth)])
    def strategies_list():
        return {"ok": True, "mode": svc.mode, "connected": svc.connected, "strategies": [strategy_row(r) for r in svc.store.list_strategies()]}

    @app.get("/api/strategies/{sid}", dependencies=[Depends(auth)])
    def strategies_get(sid: int):
        row = svc.store.get_strategy(sid)
        if row is None:
            raise HTTPException(404, "No such strategy")
        return {"ok": True, "strategy": strategy_row(row)}

    @app.post("/api/strategies", dependencies=[Depends(guard)])
    def strategies_create(body: StrategyIn):
        def go():
            st, errs, warns = checked(body.spec)
            if st is None:
                return {"ok": False, "errors": errs, "warnings": warns}
            sid = svc.store.save_strategy(None, st.name, st.to_json())
            return {"ok": True, "id": sid, "warnings": warns}
        return guarded(go)

    @app.put("/api/strategies/{sid}", dependencies=[Depends(guard)])
    def strategies_update(sid: int, body: StrategyIn):
        def go():
            if sid in svc.rules.running():
                raise RuntimeError("Stop the strategy before editing it")
            st, errs, warns = checked(body.spec)
            if st is None:
                return {"ok": False, "errors": errs, "warnings": warns}
            svc.store.save_strategy(sid, st.name, st.to_json())
            return {"ok": True, "id": sid, "warnings": warns}
        return guarded(go)

    @app.delete("/api/strategies/{sid}", dependencies=[Depends(guard)])
    def strategies_delete(sid: int):
        def go():
            if sid in svc.rules.running():
                raise RuntimeError("Stop the strategy before deleting it")
            svc.store.delete_strategy(sid)
            return {"ok": True}
        return guarded(go)

    @app.get("/api/strategies/{sid}/state", dependencies=[Depends(auth)])
    def strategies_state(sid: int):
        return {"ok": True, "state": svc.rules.status(sid), "running": sid in svc.rules.running()}

    @app.post("/api/strategies/{sid}/start", dependencies=[Depends(guard)])
    def strategies_start(sid: int, body: StartIn):
        def go():
            if svc.mode == "live" and not body.confirm_live:
                raise RuntimeError("Live mode: confirm that this strategy may place REAL orders")
            with order_lock:
                svc.rules.start(sid)
            return {"ok": True}
        return guarded(go)

    @app.post("/api/strategies/{sid}/pause", dependencies=[Depends(guard)])
    def strategies_pause(sid: int, body: PauseIn):
        def go():
            (svc.rules.pause if body.paused else svc.rules.resume)(sid)
            return {"ok": True}
        return guarded(go)

    @app.post("/api/strategies/{sid}/stop", dependencies=[Depends(guard)])
    def strategies_stop(sid: int):
        def go():
            svc.rules.stop(sid)
            return {"ok": True}
        return guarded(go)

    # ------------------------------------------------------------------------------ journal
    DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    NOTE_RE = re.compile(r"^(trip:[0-9a-f]{12}|day:\d{4}-\d{2}-\d{2})$")

    @app.get("/api/journal", dependencies=[Depends(auth)])
    def journal(frm: str = "", to: str = "", tag: str = ""):
        if (frm and not DAY_RE.match(frm)) or (to and not DAY_RE.match(to)) or len(tag) > 40:
            raise HTTPException(422, "Bad filter")
        if svc.gateway is None:
            return {"ok": False, "reason": "not_connected"}
        return safe(lambda: {"ok": True, **svc.journal.report(frm or None, to or None, tag or None)})

    @app.put("/api/journal/notes/{nid}", dependencies=[Depends(guard)])
    def journal_note(nid: str, body: NoteIn):
        if not NOTE_RE.match(nid):
            raise HTTPException(422, "Bad note id")
        return safe(lambda: (svc.store.set_note(nid, body.body, body.tags), {"ok": True})[1])

    @app.post("/api/journal/demo", dependencies=[Depends(guard)])
    def journal_demo():
        return guarded(lambda: {"ok": True, "added": svc.journal.seed_demo()})

    @app.delete("/api/journal/demo", dependencies=[Depends(guard)])
    def journal_demo_clear():
        return guarded(lambda: (svc.journal.clear_demo(), {"ok": True})[1])

    # ------------------------------------------------------------------------------ back-test
    @app.get("/api/backtest/days", dependencies=[Depends(auth)])
    def bt_days():
        strategies = []
        for r in svc.store.list_strategies():
            spec, errs, _ = parse(r["spec"])
            strategies.append({"id": r["id"], "name": r["name"], "valid": spec is not None, "underlying": spec.underlying if spec else None})
        return {"ok": True, "src": svc.bt_src, "sample": svc.bt_src == "sample", "recorder": svc.recorder.status() if svc.recorder else None,
                "days": svc.history.days(svc.bt_src), "strategies": strategies, "running": svc.backtest.running()}

    @app.post("/api/backtest/sample-days", dependencies=[Depends(guard)])
    def bt_sample(body: SampleDaysIn):
        if svc.bt_src != "sample":
            raise HTTPException(400, "Invented days are only for sample mode. In paper and live mode the terminal records real days.")
        from datetime import timedelta
        from ..clock import now_ist
        from ..history import synth_day, weekdays_back
        end = now_ist().date() - timedelta(days=1)
        for d in weekdays_back(end, body.days):
            synth_day(svc.history, d)
        return {"ok": True, "days": len(svc.history.days("sample"))}

    @app.delete("/api/backtest/sample-days", dependencies=[Depends(guard)])
    def bt_sample_clear():
        if svc.bt_src != "sample":
            raise HTTPException(400, "Only for sample mode")
        svc.history.clear("sample")
        return {"ok": True}

    @app.post("/api/backtest/run", dependencies=[Depends(guard)])
    def bt_run(body: BacktestIn):
        if any(not DAY_RE.match(d) for d in body.days):
            raise HTTPException(422, "Bad day")
        row = svc.store.get_strategy(body.strategy_id)
        if row is None:
            raise HTTPException(404, "No such strategy")
        return guarded(lambda: {"ok": True, **svc.backtest.start(row["name"], row["spec"], svc.bt_src, body.days, body.start_time, body.cost)})

    @app.get("/api/backtest/job", dependencies=[Depends(auth)])
    def bt_job():
        return {"ok": True, "job": svc.backtest.status()}

    @app.post("/api/backtest/stop", dependencies=[Depends(guard)])
    def bt_stop():
        svc.backtest.stop()
        return {"ok": True}

    # ------------------------------------------------------------------------------ NSE public data
    @app.get("/api/nse/fii-dii", dependencies=[Depends(auth)])
    def nse_fii_dii():
        return safe(svc.nse.fii_dii)

    @app.get("/api/nse/delivery", dependencies=[Depends(auth)])
    def nse_delivery(day: str = "", q: str = ""):
        if (day and not DAY_RE.match(day)) or len(q) > 20 or not re.match(r"^[A-Za-z0-9&\- ]*$", q):
            raise HTTPException(422, "Bad request")
        return safe(svc.nse.delivery, day, q)

    @app.get("/api/nse/fo", dependencies=[Depends(auth)])
    def nse_fo(day: str = ""):
        if day and not DAY_RE.match(day):
            raise HTTPException(422, "Bad request")
        return safe(svc.nse.fo, day)

    # ------------------------------------------------------------------------------ alerts
    @app.get("/api/alerts", dependencies=[Depends(auth)])
    def alerts_status():
        return {"ok": True, **svc.alerts.status()}

    @app.post("/api/alerts/test", dependencies=[Depends(guard)])
    def alerts_test():
        return safe(svc.alerts.test)

    # -------------------------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        origin, host = websocket.headers.get("origin"), websocket.headers.get("host")
        if origin and urlparse(origin).netloc != host:
            await websocket.close(code=1008)
            return
        if not valid_token(websocket.cookies.get(COOKIE)):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        owner = f"ws{id(websocket)}"
        subs: set[str] = set()
        seen: dict[str, int] = {}

        async def reader():
            while True:
                msg = await websocket.receive_json()
                op, keys = msg.get("op"), [k for k in (msg.get("keys") or []) if isinstance(k, str) and KEY_RE.match(k)]
                if op == "sub":
                    new = [k for k in keys if k not in subs][: max(0, MAX_WS_KEYS - len(subs))]
                    subs.update(new)
                    for k in new:
                        seen.pop(k, None)
                    svc.hub.want(owner, new)
                elif op == "unsub":
                    subs.difference_update(keys)
                    svc.hub.release(owner, keys)

        async def writer():
            while True:
                changed = svc.hub.changed_since(subs, seen)
                if changed:
                    for q in changed:
                        seen[q.key] = q.ver
                    await websocket.send_json({"t": "q", "d": [q.to_wire() for q in changed]})
                await asyncio.sleep(0.2)

        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            svc.hub.release_owner(owner)

    # --------------------------------------------------------------------------- frontend
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or path == "ws":
            raise HTTPException(404)
        f = (DIST / path).resolve()
        if path and f.is_file() and DIST.resolve() in f.parents:
            return FileResponse(f)
        idx = DIST / "index.html"
        if idx.is_file():
            return FileResponse(idx)
        return HTMLResponse("<h2>Frontend not built</h2><p>Run <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>.</p>",
                            status_code=503)

    return app
