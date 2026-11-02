"""Daily loss guard and position sizing. All P&L maths is for SHORT option positions."""
from __future__ import annotations


class RiskGuard:
    def __init__(self, max_daily_loss: float, max_lots: int) -> None:
        self.max_daily_loss = float(max_daily_loss)
        self.max_lots = int(max_lots)
        self.realised = 0.0

    @staticmethod
    def open_pnl(entry: float, ltp: float, qty: int) -> float:
        return (entry - ltp) * qty          # we SOLD at entry; profit when price falls

    def total(self, open_pnl: float = 0.0) -> float:
        return self.realised + open_pnl

    @property
    def limited(self) -> bool:
        """False when the person set no daily loss limit (0): nothing is then stopped or sized by it."""
        return self.max_daily_loss > 0

    def breached(self, open_pnl: float = 0.0) -> bool:
        return self.limited and self.total(open_pnl) <= -self.max_daily_loss

    def budget_left(self) -> float:
        """How much more we may lose today if everything already booked stays booked."""
        return self.max_daily_loss + self.realised if self.limited else float("inf")

    def lots_for(self, risk_per_unit: float, lot_size: int, desired_lots: int) -> int:
        """Largest lot count (<= desired and <= max_lots) whose stop-loss risk fits the budget."""
        if risk_per_unit <= 0 or lot_size <= 0:
            return 0
        left = self.budget_left()
        fit = desired_lots if left == float("inf") else int(left // (risk_per_unit * lot_size))
        return max(0, min(desired_lots, self.max_lots, fit))
