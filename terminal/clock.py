"""Time helpers. Everything in this project runs on Indian Standard Time."""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def parse_hm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def at(day: date, hm: time) -> datetime:
    return datetime.combine(day, hm, tzinfo=IST)


def minute_floor(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


def round_tick(price: float, tick: float = 0.05) -> float:
    """Round to the option tick size (Rs 0.05)."""
    return round(round(price / tick) * tick, 2)
