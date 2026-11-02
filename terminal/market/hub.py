"""
QuoteHub: the one place live prices live.

  feed thread --update()--> hub --> (a) UI websockets (poll by version)   (b) HubEngineFeed -> the algo engine
                              ^
              want()/release() from the UI and the algo decide which instruments the feed must subscribe to.

There is exactly ONE connection to Kotak's feed no matter how many pages / browser tabs / algos are open.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Callable, Protocol

from ..brokers.base import Subscription
from ..clock import now_ist
from ..models import Tick
from .models import Quote

log = logging.getLogger("hub")


class KeyFeed(Protocol):
    def subscribe_keys(self, keys: list[str]) -> None: ...


class QuoteHub:
    def __init__(self) -> None:
        self._q: dict[str, Quote] = {}
        self._lock = threading.RLock()
        self._ver = 0
        self._owners: dict[str, set[str]] = defaultdict(set)       # key -> owners that want it
        self._listeners: list[Callable[[Quote], None]] = []
        self._feed: KeyFeed | None = None
        self.tick_count = 0
        self.last_update = 0.0

    # ---- feed side
    def attach_feed(self, feed: KeyFeed) -> None:
        with self._lock:
            self._feed = feed
            keys = list(self._owners)
        if keys:
            feed.subscribe_keys(keys)

    def reset(self) -> None:
        """Forget every price, subscription and the feed (used when the terminal leaves the sample world for real prices)."""
        with self._lock:
            self._q.clear()
            self._owners.clear()
            self._feed = None
            self.tick_count = 0
            self.last_update = 0.0

    def update(self, q: Quote) -> None:
        """Merge a tick/quote. Fields the feed did not carry (None) keep their previous value."""
        with self._lock:
            old = self._q.get(q.key)
            if old is not None:
                for f in ("prev_close", "open", "high", "low", "volume", "oi"):
                    if getattr(q, f) is None:
                        setattr(q, f, getattr(old, f))
            self._ver += 1
            q.ver = self._ver
            q.ts = q.ts or time.time()
            self._q[q.key] = q
            self.tick_count += 1
            self.last_update = q.ts
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(q)
            except Exception:
                log.exception("quote listener failed")

    def seed(self, key: str, **fields) -> None:
        """Fill in reference data (previous close, OHLC) without disturbing a live ltp."""
        with self._lock:
            cur = self._q.get(key)
            if cur is None:
                ltp = fields.pop("ltp", None)
                if ltp is None:
                    return
                self.update(Quote(key, ltp, **fields))
                return
            for k, v in fields.items():
                if v is not None and k != "ltp" and getattr(cur, k, None) is None:
                    setattr(cur, k, v)

    # ---- reading
    def get(self, key: str) -> Quote | None:
        with self._lock:
            return self._q.get(key)

    def ltp(self, key: str) -> float | None:
        q = self.get(key)
        return q.ltp if q else None

    def snapshot(self, keys: list[str]) -> list[Quote]:
        with self._lock:
            return [self._q[k] for k in keys if k in self._q]

    def changed_since(self, keys: set[str], seen: dict[str, int]) -> list[Quote]:
        with self._lock:
            return [q for k in keys if (q := self._q.get(k)) is not None and q.ver > seen.get(k, 0)]

    def add_listener(self, fn: Callable[[Quote], None]) -> None:
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[Quote], None]) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    # ---- subscription bookkeeping (reference counted by owner)
    def want(self, owner: str, keys: list[str]) -> None:
        new: list[str] = []
        with self._lock:
            for k in keys:
                if not self._owners[k]:
                    new.append(k)
                self._owners[k].add(owner)
            feed = self._feed
        if new and feed:
            feed.subscribe_keys(new)

    def release(self, owner: str, keys: list[str]) -> None:
        with self._lock:
            for k in keys:
                s = self._owners.get(k)
                if s:
                    s.discard(owner)
                    if not s:
                        del self._owners[k]

    def release_owner(self, owner: str) -> None:
        with self._lock:
            for k in list(self._owners):
                self._owners[k].discard(owner)
                if not self._owners[k]:
                    del self._owners[k]

    def wanted(self) -> list[str]:
        with self._lock:
            return list(self._owners)

    def age_s(self) -> float | None:
        return None if not self.last_update else time.time() - self.last_update


class HubEngineFeed:
    """Lets the algo Engine (which speaks Subscription/Tick) read from the shared hub."""

    OWNER = "algo"

    def __init__(self, hub: QuoteHub) -> None:
        self.hub = hub
        self._map: dict[str, str] = {}              # hub key -> engine symbol (INDEX or option trading symbol)
        self._cb: Callable[[Tick], None] | None = None

    def start(self, on_tick: Callable[[Tick], None]) -> None:
        self._cb = on_tick
        self.hub.add_listener(self._on_quote)

    def subscribe(self, subs: list[Subscription]) -> None:
        for s in subs:
            self._map[f"{s.segment}|{s.token}"] = s.symbol
        self.hub.want(self.OWNER, list(self._map))

    def stop(self) -> None:
        self.hub.remove_listener(self._on_quote)
        self.hub.release_owner(self.OWNER)
        self._cb = None

    def _on_quote(self, q: Quote) -> None:
        sym = self._map.get(q.key)
        cb = self._cb
        if sym and cb and q.ltp > 0:
            cb(Tick(sym, q.ltp, now_ist()))
