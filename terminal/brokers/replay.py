"""Offline data source: replays ticks from a CSV (timestamp,symbol,price) or an in-memory list."""
from __future__ import annotations

import csv
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from ..config import Config
from ..models import INDEX, Contract, Tick
from .base import Subscription


def load_ticks_csv(path: str | Path) -> list[Tick]:
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            out.append(Tick(row["symbol"], float(row["price"]), datetime.fromisoformat(row["timestamp"])))
    out.sort(key=lambda t: t.ts)
    return out


class ReplayFeed:
    """speed=0 -> as fast as possible. speed=60 -> one market minute per real second."""

    def __init__(self, ticks: Iterable[Tick], speed: float = 0.0) -> None:
        self._ticks = list(ticks)
        self.speed = speed
        self.on_eof: Callable[[], None] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_tick: Callable[[Tick], None]) -> None:
        def run() -> None:
            prev = None
            for t in self._ticks:
                if self._stop.is_set():
                    return
                if self.speed > 0 and prev is not None:
                    time.sleep(max(0.0, (t.ts - prev).total_seconds() / self.speed))
                prev = t.ts
                on_tick(t)
            if self.on_eof:
                self.on_eof()

        self._thread = threading.Thread(target=run, name="replay-feed", daemon=True)
        self._thread.start()

    def subscribe(self, subs) -> None:        # replay already contains every symbol
        pass

    def stop(self) -> None:
        self._stop.set()


class SyntheticInstruments:
    """Builds contract objects from a naming scheme so replay needs no broker login."""

    def __init__(self, cfg: Config, day: date) -> None:
        self.cfg, self.day = cfg, day

    def index_subscription(self, instrument: str) -> Subscription:
        i = self.cfg.instruments[instrument]
        return Subscription(i.index_segment, i.index_token, INDEX)

    def candidates(self, instrument, ref_price, window, expiry_offset=0):
        i = self.cfg.instruments[instrument]
        atm = round(ref_price / i.strike_step) * i.strike_step
        out = {}
        for k in range(-window, window + 1):
            strike = float(atm + k * i.strike_step)
            mk = lambda ot: Contract(f"{i.underlying}{int(strike)}{ot}", f"{int(strike)}{ot}",
                                     i.option_segment, strike, ot, self.day, i.paper_lot_size)
            out[strike] = (mk("CE"), mk("PE"))
        return out
