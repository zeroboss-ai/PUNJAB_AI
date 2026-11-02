"""Market-data value objects shared by the hub, the catalog, the API and the UI."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Instrument:
    key: str                    # "<segment>|<token>" - the ONE id used everywhere (feed, UI, watchlists)
    segment: str                # nse_cm | bse_cm | nse_fo | bse_fo | mcx_fo (commodities)
    token: str                  # pSymbol, or the index name for indices ("Nifty 50")
    symbol: str                 # trading symbol for orders: "RELIANCE-EQ", "NIFTY26OCT25000CE"
    name: str                   # underlying / display name: "RELIANCE", "NIFTY"
    kind: str                   # EQ | INDEX | CE | PE | FUT
    lot: int = 1
    tick: float = 0.05
    expiry: date | None = None
    strike: float | None = None
    desc: str = ""              # company name for cash instruments

    @property
    def is_derivative(self) -> bool:
        return self.kind in ("CE", "PE", "FUT")

    @property
    def is_commodity(self) -> bool:
        return self.segment == "mcx_fo"

    def label(self) -> str:
        n = self.desc if (self.segment == "mcx_fo" and self.desc) else self.name        # "Gold Mini", not "GOLDM"
        if self.kind in ("CE", "PE") and self.expiry and self.strike is not None:
            return f"{n} {self.expiry:%d %b} {self.strike:g} {self.kind}"
        if self.kind == "FUT" and self.expiry:
            return f"{n} {self.expiry:%d %b} FUT"
        return self.name

    def to_dict(self) -> dict:
        return {"key": self.key, "segment": self.segment, "token": self.token, "symbol": self.symbol,
                "name": self.name, "kind": self.kind, "lot": self.lot, "tick": self.tick,
                "expiry": self.expiry.isoformat() if self.expiry else None, "strike": self.strike,
                "desc": self.desc, "label": self.label()}


@dataclass
class Quote:
    key: str
    ltp: float
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: int | None = None
    oi: int | None = None
    ts: float = 0.0             # epoch seconds of the last update
    ver: int = 0                # bumped by the hub on every update (lets each client send only what changed)

    @property
    def change(self) -> float | None:
        return None if not self.prev_close else round(self.ltp - self.prev_close, 4)

    @property
    def change_pct(self) -> float | None:
        return None if not self.prev_close else round((self.ltp - self.prev_close) / self.prev_close * 100, 3)

    def to_wire(self) -> dict:
        return {"k": self.key, "l": self.ltp, "pc": self.prev_close, "c": self.change, "p": self.change_pct,
                "o": self.open, "h": self.high, "lo": self.low, "v": self.volume, "oi": self.oi, "t": self.ts}
