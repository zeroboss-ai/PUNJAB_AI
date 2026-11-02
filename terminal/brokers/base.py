"""The three things the engine needs. Kotak, paper and replay all plug into these."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..models import Contract, OrderRequest, OrderStatus, Tick


@dataclass(frozen=True)
class Subscription:
    segment: str
    token: str
    symbol: str          # internal key carried on every Tick (INDEX or the option trading symbol)


class Instruments(Protocol):
    def index_subscription(self, instrument: str) -> Subscription: ...

    def candidates(self, instrument: str, ref_price: float, window: int,
                   expiry_offset: int = 0) -> dict[float, tuple[Contract, Contract]]:
        """strike -> (CE contract, PE contract) for ATM +/- window strikes."""
        ...


class Feed(Protocol):
    def start(self, on_tick: Callable[[Tick], None]) -> None: ...
    def subscribe(self, subs: list[Subscription]) -> None: ...
    def stop(self) -> None: ...


class Orders(Protocol):
    def place(self, req: OrderRequest) -> str: ...
    def cancel(self, order_id: str) -> None: ...
    def status(self, order_id: str) -> OrderStatus: ...
    def positions(self) -> dict[str, int] | None:
        """symbol -> net quantity, or None if it could not be read."""
        ...
