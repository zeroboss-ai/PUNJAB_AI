#!/usr/bin/env python3
"""
Entry point.

  python run.py                       # uses `mode:` from config.yaml (default: sample - invented data, no login)
  python run.py --mode sample         # try every page with invented data, nothing to log in to
  python run.py --mode paper          # REAL Kotak prices, SIMULATED orders
  python run.py --mode live           # REAL orders (needs ALLOW_LIVE=yes in .env as a second lock)
  python run.py --mode replay --scenario put_sl --headless     # algo test on a synthetic day, prints the result
"""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from terminal.brokers.paper import PaperOrders
from terminal.brokers.replay import ReplayFeed, SyntheticInstruments, load_ticks_csv
from terminal.clock import now_ist
from terminal.config import load_config
from terminal.engine import Engine
from terminal.sim import SCENARIOS, build_day


def setup_logging(log_dir: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(log_dir) / f"terminal-{now_ist():%Y%m%d}.log"),
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mode", choices=["sample", "paper", "live", "replay"])
    ap.add_argument("--instrument", choices=["NIFTY", "SENSEX"])
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="put_sell")
    ap.add_argument("--ticks", help="CSV of ticks for replay mode (timestamp,symbol,price)")
    ap.add_argument("--speed", type=float, default=0.0, help="replay speed multiplier; 0 = instant")
    ap.add_argument("--headless", action="store_true", help="replay only: run the algo, print summary, exit (no UI)")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()

    load_dotenv()
    cfg = load_config(args.config)
    if args.mode:
        cfg.mode = args.mode
    if args.instrument:
        cfg.instrument = args.instrument
    if args.host:
        cfg.host = args.host
    elif os.environ.get("HOST"):
        cfg.host = os.environ["HOST"]

    if args.port:
        cfg.port = args.port
    elif os.environ.get("PORT"):
        try:
            cfg.port = int(os.environ["PORT"])
        except ValueError:
            pass
    setup_logging(cfg.log_dir)

    if args.headless:
        if cfg.mode != "replay":
            print("--headless is only for replay mode")
            return 2
        day = date.today()
        ticks = load_ticks_csv(args.ticks) if args.ticks else build_day(day, args.scenario, underlying=cfg.inst.underlying)
        if args.ticks:
            day = ticks[0].ts.date()
        cfg.strategy.strike_window = min(cfg.strategy.strike_window, 3)
        eng = Engine(cfg, SyntheticInstruments(cfg, day), ReplayFeed(ticks, args.speed), PaperOrders())
        eng.start()
        eng.finished.wait()
        for e in eng.events:
            print(f"{e['ts']} [{e['level']:<8}] {e['msg']}")
        print(f"\nTrades: {len(eng.strategy.trades)} | Day P&L: {eng.risk.realised:+,.2f}")
        return 0

    if cfg.mode == "live" and os.environ.get("ALLOW_LIVE", "").strip().lower() != "yes":
        print("Refusing to start in LIVE mode: set ALLOW_LIVE=yes in your .env file to confirm real-money trading.")
        return 2

    allow_remote = os.environ.get("ALLOW_REMOTE", "").strip().lower() in ("yes", "true", "1") or bool(os.environ.get("RENDER"))
    if cfg.mode in ("live", "paper") and cfg.host not in ("127.0.0.1", "localhost") and not allow_remote:
        print(f"Refusing {cfg.mode.upper()} mode with the terminal exposed beyond localhost. Set ALLOW_REMOTE=yes in your environment if hosting on cloud.")
        return 2

    pw = os.environ.get("DASHBOARD_PASSWORD", "")
    shown_pw = None
    if (cfg.host not in ("127.0.0.1", "localhost") or allow_remote) and len(pw) < 8:
        print("Set DASHBOARD_PASSWORD (8+ characters) in your environment before exposing the terminal.")
        return 2
    if cfg.mode in ("sample", "replay") and len(pw) < 8:
        pw = shown_pw = "demo-" + secrets.token_hex(3)               # sample mode on localhost: a throw-away password

    import uvicorn
    from terminal.services import Services
    from terminal.web.server import create_app

    svc = Services(cfg)
    svc.start()
    app = create_app(svc, pw)
    print(f"\nTerminal: http://{cfg.host}:{cfg.port}   (mode={svc.mode})")
    if shown_pw:
        print(f"Sample-mode password for this run: {shown_pw}")
    print()
    try:
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning")
    finally:
        svc.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
