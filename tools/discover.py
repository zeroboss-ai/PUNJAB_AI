#!/usr/bin/env python3
"""
READ-ONLY check of Kotak's real data formats against what terminal/brokers/kotak.py assumes.
It logs in, downloads the scrip master, listens to the index feed for a few messages, and reads your
positions and order report. It places NO orders.

Run it once while the market is open (ticks only arrive then), from the machine whose static IP
you registered:   python tools/discover.py

It writes discover_output.txt. REVIEW that file before sharing it - positions/orders may contain
account details. Never paste your .env.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from terminal.brokers.base import Subscription
from terminal.brokers.kotak import KotakFeed, KotakInstruments, KotakOrders, KotakSession
from terminal.clock import now_ist
from terminal.config import load_config

LINES: list[str] = []


def log(*a) -> None:
    line = " ".join(str(x) for x in a)
    print(line)
    LINES.append(line)


def mask(text: str) -> str:
    """Hide long digit strings (account / mobile numbers) in the saved report."""
    return re.sub(r"\b\d{8,}\b", lambda m: m.group(0)[:2] + "*" * (len(m.group(0)) - 4) + m.group(0)[-2:], text)


async def listen(session: KotakSession, cfg, subs: list[Subscription], seconds: int = 25) -> None:
    """Subscribe like the terminal does and show, for every message, what the adapter makes of it."""
    from neo_api_client.websocket.feed import WsToken
    feed = KotakFeed(session, cfg)
    mapped: list = []
    feed._on_tick = mapped.append
    feed.subscribe(subs)
    async with session.client.create_websocket() as ws:
        await ws.subscribe_scrips([WsToken(x.segment, x.token) for x in subs])
        it = ws.__aiter__()
        loop = asyncio.get_running_loop()
        end, shown = loop.time() + seconds, 0
        while loop.time() < end and shown < 12:
            try:
                msg = await asyncio.wait_for(it.__anext__(), timeout=5)
            except asyncio.TimeoutError:
                log("   (no message in 5s - is the market open?)")
                continue
            shown += 1
            before = len(mapped)
            feed._handle(msg)
            data = msg.model_dump() if hasattr(msg, "model_dump") else repr(msg)
            keep = {k: data[k] for k in ("exchange_segment", "instrument_token", "trading_symbol", "name",
                                         "last_traded_price") if isinstance(data, dict) and k in data}
            log(f"   {type(msg).__name__} {keep}  ->  " +
                (f"tick {mapped[-1].symbol} {mapped[-1].price}" if len(mapped) > before else "NOT MAPPED (ignored)"))
    log(f"   unmapped ticks seen: {feed.unknown}")


def main() -> None:
    load_dotenv()
    cfg = load_config()
    s = KotakSession()
    s.login(input("Enter the 6-digit TOTP from your authenticator app: ").strip())
    log(f"Logged in at {now_ist():%Y-%m-%d %H:%M:%S} IST")
    log(f"IP address Kotak sees: {s.my_ip}   (config.yaml static_ip: {cfg.static_ip or 'not set'})")
    if cfg.static_ip and s.my_ip != cfg.static_ip:
        log("  !! MISMATCH - live orders from this machine would be rejected under the SEBI static-IP rule")
    log("")

    ins = KotakInstruments(s, cfg)
    for name, icfg in cfg.instruments.items():
        log(f"=== {name}: scrip master for {icfg.option_segment} ===")
        try:
            df = ins._load(icfg.option_segment)
            log("columns:", list(df.columns))
            col = "psymbolname" if "psymbolname" in df.columns else df.columns[0]
            log(df[df[col].astype(str).str.upper() == icfg.underlying.upper()].head(4).to_string())
            ref = 25000.0 if name == "NIFTY" else 82000.0
            log(f"\ncandidate lookup around {ref:g}:")
            for k, (ce, pe) in ins.candidates(name, ref, 1, cfg.strategy.expiry_offset).items():
                log(f"  strike {k:g}: CE={ce.symbol} token={ce.token} lot={ce.lot_size} | "
                    f"PE={pe.symbol} token={pe.token} expiry={pe.expiry} ({pe.expiry:%A})")
        except Exception as e:
            log(f"  !! failed: {e!r}")
        log("")

    i = cfg.inst
    log(f"=== live feed: index {i.index_segment} / {i.index_token!r} + the ATM call and put ===")
    try:
        subs = [ins.index_subscription(cfg.instrument)]
        ref = 25000.0 if cfg.instrument == "NIFTY" else 82000.0
        ref = float(round(ref / i.strike_step) * i.strike_step)
        ce, pe = ins.candidates(cfg.instrument, ref, 0, cfg.strategy.expiry_offset)[ref]
        subs += [Subscription(c.segment, c.token, c.symbol) for c in (ce, pe)]
        asyncio.run(listen(s, cfg, subs))
    except Exception as e:
        log(f"  !! feed failed: {e!r}")

    log("\n=== positions() raw, then as the terminal reads it ===")
    try:
        log(str(s.client.positions())[:1500])
        log("parsed ->", KotakOrders(s, cfg).positions())
    except Exception as e:
        log(f"  !! {e!r}")
    log("\n=== order_report() raw (first 3 orders) - check fields nOrdNo / ordSt / fldQty / avgPrc / rejRsn ===")
    try:
        rep = s.client.order_report()
        rows = rep.get("data") if isinstance(rep, dict) else None
        log(str(rows[:3] if isinstance(rows, list) else rep)[:1800])
        if isinstance(rows, list) and rows:
            oid = str(rows[0].get("nOrdNo"))
            log(f"single-order lookup for {oid} ->", KotakOrders(s, cfg).status(oid))
    except Exception as e:
        log(f"  !! {e!r}")

    # ---- things the full terminal (dashboard, trade book, funds, OI pages) relies on
    log("\n=== dashboard index names: do these subscribe and map to ticks? ===")
    try:
        subs = [Subscription(d["segment"], d["token"], d["label"]) for d in cfg.dashboard_indices]
        asyncio.run(listen(s, cfg, subs, seconds=20))
        log("  Any index above that says NOT MAPPED, or never printed, needs its name fixed under dashboard_indices in config.yaml.")
    except Exception as e:
        log(f"  !! {e!r}")

    log("\n=== trade_report() raw (first 3) - the Trades tab reads this ===")
    try:
        rep = s.client.trade_report()
        rows = rep.get("data") if isinstance(rep, dict) else None
        log(str(rows[:3] if isinstance(rows, list) else rep)[:1500])
    except Exception as e:
        log(f"  !! {e!r}")

    log("\n=== limits() raw - the Funds panel reads this ===")
    try:
        log(str(s.client.limits())[:1500])
    except Exception as e:
        log(f"  !! {e!r}")

    log("\n=== expiries / option_chain / historical_data (for the next release: OI pages, backtests) ===")
    try:
        log("expiries:", str(s.client.expiries(exchange="nse_fo", underlying="NIFTY", instrument_type="option"))[:600])
        log("option_chain:", str(s.client.option_chain(exchange="nse_fo", underlying="NIFTY", count=10))[:900])
        from terminal.market.chain import KotakChainProvider
        raw = KotakChainProvider(s, None, strike_count=40).fetch("NIFTY", None)
        k = sorted(raw.rows)[len(raw.rows) // 2]
        log(f"chain parsed by the terminal: expiry {raw.expiry}, lot {raw.lot}, {len(raw.rows)} strikes, ATM flagged {raw.atm_hint}; strike {k:g}: {raw.rows[k]}")
        log("  -> Check that OI, previous OI and prices above are filled in (not None). If they are None, send me this file.")
    except Exception as e:
        log(f"  !! {e!r}")
    try:
        from datetime import timedelta
        today = now_ist().date()
        for sym in ("nse_cm|Nifty 50", "nse_cm|2885"):
            r = s.client.historical_data(neosymbol=sym, interval="5min", from_date=(today - timedelta(days=4)).isoformat(), to_date=today.isoformat())
            log(f"historical_data {sym}:", str(r)[:500])
    except Exception as e:
        log(f"  !! {e!r}")

    Path("discover_output.txt").write_text(mask("\n".join(LINES)))
    print("\nSaved discover_output.txt (account-like numbers masked). Review it before sharing.")
    s.logout()


if __name__ == "__main__":
    main()
