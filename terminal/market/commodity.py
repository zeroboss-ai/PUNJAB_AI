"""
Commodities (MCX): names, friendly labels, trading hours.

What is KNOWN here comes from public exchange / broker pages (hours, lot sizes in the sample world).
What is ASSUMED is marked: the live contract list, lot sizes and tick sizes always come from Kotak's own scrip master
(segment mcx_fo), never from this file. This file only gives names a human label and groups big / mini / micro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

SEGMENT = "mcx_fo"


@dataclass(frozen=True)
class Commodity:
    name: str            # MCX symbol as it appears in the scrip master (pSymbolName)
    label: str
    group: str           # Gold | Silver | Crude Oil | Natural Gas | Base metals | Agri
    size: str            # big | mini | micro
    aliases: str = ""    # extra words people type
    agri: str = ""       # "" = non-agri, "21" = closes 21:00, "17" = closes 17:00


# The symbols below are the ones MCX is known to list; anything else Kotak lists is still searchable under its own name.
COMMODITIES: list[Commodity] = [
    Commodity("GOLD", "Gold", "Gold", "big", "yellow metal"),
    Commodity("GOLDM", "Gold Mini", "Gold", "mini"),
    Commodity("GOLDGUINEA", "Gold Guinea", "Gold", "micro"),
    Commodity("GOLDPETAL", "Gold Petal", "Gold", "micro"),
    Commodity("GOLDTEN", "Gold Ten", "Gold", "micro"),
    Commodity("SILVER", "Silver", "Silver", "big", "chandi"),
    Commodity("SILVERM", "Silver Mini", "Silver", "mini"),
    Commodity("SILVERMIC", "Silver Micro", "Silver", "micro"),
    Commodity("CRUDEOIL", "Crude Oil", "Crude Oil", "big", "crude brent wti"),
    Commodity("CRUDEOILM", "Crude Oil Mini", "Crude Oil", "mini", "crude brent wti"),
    Commodity("NATURALGAS", "Natural Gas", "Natural Gas", "big", "natgas ng"),
    Commodity("NATGASMINI", "Natural Gas Mini", "Natural Gas", "mini", "natgas ng"),
    Commodity("COPPER", "Copper", "Base metals", "big"),
    Commodity("COPPERM", "Copper Mini", "Base metals", "mini"),
    Commodity("ZINC", "Zinc", "Base metals", "big"),
    Commodity("ZINCMINI", "Zinc Mini", "Base metals", "mini"),
    Commodity("LEAD", "Lead", "Base metals", "big"),
    Commodity("LEADMINI", "Lead Mini", "Base metals", "mini"),
    Commodity("ALUMINIUM", "Aluminium", "Base metals", "big", "aluminum"),
    Commodity("ALUMINI", "Aluminium Mini", "Base metals", "mini", "aluminum"),
    Commodity("NICKEL", "Nickel", "Base metals", "big"),
    Commodity("NICKELM", "Nickel Mini", "Base metals", "mini"),
    Commodity("MENTHAOIL", "Mentha Oil", "Agri", "big", "mentha", agri="17"),
    Commodity("CPO", "Palm Oil (CPO)", "Agri", "big", "palm", agri="17"),
    Commodity("COTTON", "Cotton", "Agri", "big", agri="21"),
    Commodity("KAPAS", "Kapas", "Agri", "big", "cotton", agri="21"),
    Commodity("COTTONCNDY", "Cotton Candy", "Agri", "big", "cotton", agri="21"),
]
BY_NAME = {c.name: c for c in COMMODITIES}

# Dashboard cards: the front-month future of each (label, MCX name)
DASHBOARD = [("Gold", "GOLD"), ("Silver", "SILVER"), ("Crude Oil", "CRUDEOIL"), ("Natural Gas", "NATURALGAS")]

SIZE_LABEL = {"big": "Big", "mini": "Mini", "micro": "Micro"}


def label_of(name: str) -> str:
    c = BY_NAME.get(name.upper())
    return c.label if c else name.title()


def info(name: str) -> dict:
    c = BY_NAME.get(name.upper())
    return {"name": name.upper(), "label": c.label if c else name.title(), "group": c.group if c else "",
            "size": c.size if c else ""}


def _hay(c: Commodity) -> str:
    return f"{c.name} {c.label} {c.group} {c.aliases} {c.size}".upper()


def resolve(words: list[str]) -> list[str]:
    """MCX names whose name / label / alias text contains EVERY typed word: 'gold mini' -> GOLDM, 'crude' -> CRUDEOIL, CRUDEOILM."""
    ws = [w.upper() for w in words if w]
    if not ws:
        return []
    return [c.name for c in COMMODITIES if all(w in _hay(c) for w in ws)]


# ---------------------------------------------------------------------------------------- hours
def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)           # first Sunday
    return d + timedelta(days=7 * (n - 1))


def us_summer_schedule(d: date) -> bool:
    """True while MCX runs its 'US daylight-saving' schedule (evening close 23:30).
    MCX has moved on the Monday after the US clocks change: the Monday after the 2nd Sunday of March (-> 23:30)
    and the Monday after the 1st Sunday of November (-> 23:55). Source: MCX circulars as reported by brokers (Nov 2025, Mar 2026, Nov 2026)."""
    start = _nth_sunday(d.year, 3, 2) + timedelta(days=1)
    end = _nth_sunday(d.year, 11, 1)                      # last day of the summer schedule
    return start <= d <= end


def close_time(d: date, name: str | None = None, override: str = "") -> time:
    """Evening close of a commodity on day d. `override` ("HH:MM") wins for non-agri contracts."""
    c = BY_NAME.get((name or "").upper())
    if c and c.agri == "17":
        return time(17, 0)
    if c and c.agri == "21":
        return time(21, 0)
    if override:
        h, m = override.strip().split(":")
        return time(int(h), int(m))
    return time(23, 30) if us_summer_schedule(d) else time(23, 55)


OPEN = time(9, 0)
_override = ""            # config.yaml mcx_close_time, set once at start


def configure(close_override: str = "") -> None:
    global _override
    _override = (close_override or "").strip()


def is_commodity(name: str) -> bool:
    return (name or "").upper() in BY_NAME


def session_hm(name: str, day: date) -> tuple[str, str]:
    """(open, close) as HH:MM for a commodity on `day`."""
    c = close_time(day, name, _override)
    return f"{OPEN:%H:%M}", f"{c:%H:%M}"


BROKER_SQUAREOFF_MIN = 10   # brokers square intraday positions off about this long before the close (Zerodha bulletin, Nov 2026); Kotak's own time may differ


def status(now: datetime, override: str | None = None) -> dict:
    """Open/closed by the clock only (non-agri hours). Exchange holidays and special sessions are NOT known to the terminal."""
    d = now.date()
    close = close_time(d, None, _override if override is None else override)
    base = {"close": f"{close:%H:%M}", "open": f"{OPEN:%H:%M}"}
    if now.weekday() >= 5:
        return {**base, "state": "weekend", "label": "MCX weekend"}
    t = now.timetz().replace(tzinfo=None)
    if t < OPEN:
        return {**base, "state": "closed", "label": "MCX opens 09:00"}
    if t <= close:
        return {**base, "state": "open", "label": f"MCX open · till {close:%H:%M}"}
    return {**base, "state": "closed", "label": "MCX closed"}


_MCX_KEY = re.compile(r"^mcx_fo\|")


def is_mcx_key(key: str) -> bool:
    return bool(_MCX_KEY.match(key or ""))
