from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

INDEX = "INDEX"  # internal key for the index feed


@dataclass(frozen=True)
class Tick:
    symbol: str          # INDEX or an option trading symbol
    price: float
    ts: datetime         # tz-aware IST


@dataclass(frozen=True)
class Contract:
    symbol: str          # trading symbol used for orders
    token: str           # feed token
    segment: str         # nse_fo / bse_fo
    strike: float
    option_type: str     # "CE" | "PE"
    expiry: date
    lot_size: int


@dataclass
class OrderRequest:
    contract: Contract
    side: str            # "B" | "S"
    qty: int             # units (lots * lot_size)
    order_type: str      # "L" | "SL" | "MKT"
    price: float = 0.0
    trigger: float = 0.0
    tag: str = ""


@dataclass
class OrderStatus:
    order_id: str
    state: str           # OPEN | TRIGGER_PENDING | COMPLETE | REJECTED | CANCELLED | UNKNOWN
    filled_qty: int = 0
    avg_price: float = 0.0
    reason: str = ""


TERMINAL_STATES = {"COMPLETE", "REJECTED", "CANCELLED"}


class OrderError(Exception):
    """Broker (or the SDK's own validation) definitely rejected the order call. Nothing was placed."""


class OrderUnknown(OrderError):
    """The call failed in a way that does NOT tell us whether the order reached the exchange
    (timeout, dropped connection, unreadable reply). The engine must check positions before acting."""
