"""Value objects for manual trading, order/trade books and positions (what the UI shows)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

OPEN_STATES = {"OPEN", "TRIGGER_PENDING"}


@dataclass
class OrderTicket:
    key: str
    side: str                      # "B" | "S"
    lots: int = 0                  # derivatives: number of lots
    qty: int = 0                   # cash: number of shares (derivatives: ignored, lots * lot is used)
    product: str = "MIS"           # MIS | NRML | CNC
    order_type: str = "L"          # L | MKT | SL | SL-M
    price: float = 0.0
    trigger: float = 0.0
    validity: str = "DAY"          # DAY | IOC
    tag: str = "manual"


@dataclass
class OrderRow:
    id: str
    key: str
    symbol: str
    label: str
    side: str
    qty: int                       # units (shares / contracts x lot)
    filled: int
    lot: int
    product: str
    order_type: str
    price: float
    trigger: float
    avg_price: float
    status: str                    # OPEN | TRIGGER_PENDING | COMPLETE | REJECTED | CANCELLED | UNKNOWN
    reason: str = ""
    time: str = ""
    validity: str = "DAY"
    tag: str = ""
    account_id: str = ""
    account_label: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATES

    def to_dict(self) -> dict:
        d = asdict(self)
        d["open"] = self.is_open
        return d


@dataclass
class TradeRow:
    order_id: str
    key: str
    symbol: str
    label: str
    side: str
    qty: int
    price: float
    product: str
    time: str
    trade_id: str = ""
    account_id: str = ""
    account_label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PositionRow:
    key: str
    symbol: str
    label: str
    segment: str
    product: str
    lot: int
    buy_qty: int
    sell_qty: int
    net_qty: int                   # units; negative = short
    buy_avg: float
    sell_avg: float
    avg_price: float               # of the open part (0 when flat)
    ltp: float | None
    realised: float
    unrealised: float
    expiry: str | None = None
    account_id: str = ""
    account_label: str = ""

    @property
    def pnl(self) -> float:
        return round(self.realised + self.unrealised, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pnl"] = self.pnl
        d["open"] = self.net_qty != 0
        return d


@dataclass
class Funds:
    available: float | None        # what you can still use
    used: float | None             # margin in use
    collateral: float | None = None
    realised: float | None = None
    unrealised: float | None = None
    simulated: bool = False
    note: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
