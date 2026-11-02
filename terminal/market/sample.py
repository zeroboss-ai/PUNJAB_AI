"""
Sample-data world: lets you click through the whole terminal with no login and no market hours.

  * indices, ~20 equities and an option chain for NIFTY / BANKNIFTY / SENSEX, all INVENTED prices
  * options are priced from the simulated index with Black-Scholes (rate 0, vol ~14%), so they move coherently
  * everything is clearly labelled "Sample data" in the UI - never use it to make a trading decision
"""
from __future__ import annotations

import math
import random
import threading
import time
from datetime import date, datetime, timedelta

from ..clock import IST, now_ist
from .catalog import Catalog
from .chain import INDEX_UNDERLYINGS, sample_oi_base
from .hub import QuoteHub
from .models import Instrument, Quote

# (label, segment, token name, base level, daily-ish volatility per tick)
SAMPLE_INDICES = [
    ("NIFTY 50", "nse_cm", "Nifty 50", 24250.0), ("NIFTY BANK", "nse_cm", "Nifty Bank", 51850.0),
    ("NIFTY FIN SERVICE", "nse_cm", "Nifty Fin Service", 23180.0), ("NIFTY MID SELECT", "nse_cm", "NIFTY MID SELECT", 12850.0),
    ("SENSEX", "bse_cm", "SENSEX", 79400.0), ("INDIA VIX", "nse_cm", "India VIX", 13.3),
]
SAMPLE_EQUITIES = [
    ("RELIANCE", "RELIANCE INDUSTRIES LTD", "2885", 1450.0), ("HDFCBANK", "HDFC BANK LTD", "1333", 1010.0),
    ("TCS", "TATA CONSULTANCY SERV LT", "11536", 3250.0), ("INFY", "INFOSYS LIMITED", "1594", 1620.0),
    ("ICICIBANK", "ICICI BANK LTD.", "4963", 1380.0), ("SBIN", "STATE BANK OF INDIA", "3045", 860.0),
    ("ITC", "ITC LTD", "1660", 415.0), ("LT", "LARSEN & TOUBRO LTD.", "11483", 3720.0),
    ("AXISBANK", "AXIS BANK LIMITED", "5900", 1140.0), ("BHARTIARTL", "BHARTI AIRTEL LIMITED", "10604", 1890.0),
    ("KOTAKBANK", "KOTAK MAHINDRA BANK LTD", "1922", 2080.0), ("HINDUNILVR", "HINDUSTAN UNILEVER LTD.", "1394", 2410.0),
    ("MARUTI", "MARUTI SUZUKI INDIA LTD.", "10999", 12400.0), ("SUNPHARMA", "SUN PHARMACEUTICAL IND L", "3351", 1730.0),
    ("TATASTEEL", "TATA STEEL LIMITED", "3499", 168.0), ("WIPRO", "WIPRO LTD", "3787", 265.0),
    ("YESBANK", "YES BANK LIMITED", "11915", 21.5), ("IDEA", "VODAFONE IDEA LIMITED", "14366", 9.4),
]
# underlying, spot key, strike step, lot, expiry weekday (0=Mon), segment, monthly-only
SAMPLE_DERIVS = [
    ("NIFTY", "nse_cm|Nifty 50", 50, 75, 1, "nse_fo", False),
    ("BANKNIFTY", "nse_cm|Nifty Bank", 100, 30, 1, "nse_fo", True),
    ("SENSEX", "bse_cm|SENSEX", 100, 20, 3, "bse_fo", False),
]
# F&O stocks in the sample world: underlying, cash token (see SAMPLE_EQUITIES), strike step, lot. Monthly expiries only.
SAMPLE_STOCK_DERIVS = [
    ("RELIANCE", "2885", 20, 500), ("HDFCBANK", "1333", 10, 550), ("TCS", "11536", 50, 175), ("INFY", "1594", 20, 400),
    ("ICICIBANK", "4963", 10, 700), ("SBIN", "3045", 10, 750), ("ITC", "1660", 5, 1600), ("AXISBANK", "5900", 10, 625),
    ("TATASTEEL", "3499", 2, 5500), ("MARUTI", "10999", 100, 50),
]
STOCK_UND = {sym: (f"nse_cm|{tok}", float(step), lot) for sym, tok, step, lot in SAMPLE_STOCK_DERIVS}
VOL = 0.14
VOL_STOCK = 0.26
# Commodities (MCX) in the sample world - ALL INVENTED. Price is per the usual MCX quote unit (Gold per 10 g, Silver per kg,
# Crude per barrel, Natural Gas per mmBtu); lot is in those price units so that price x quantity = contract value.
# name, price, tick, lot, strike step, vol, has options, expiry day of month
SAMPLE_COMMODITIES = [
    ("GOLD", 125000.0, 1.0, 100, 500.0, 0.16, True, 5), ("GOLDM", 125000.0, 1.0, 10, 500.0, 0.16, True, 5),
    ("GOLDPETAL", 12500.0, 1.0, 1, 100.0, 0.16, False, 28),
    ("SILVER", 150000.0, 1.0, 30, 1000.0, 0.26, True, 5), ("SILVERM", 150000.0, 1.0, 5, 1000.0, 0.26, True, 28),
    ("SILVERMIC", 150000.0, 1.0, 1, 1000.0, 0.26, False, 28),
    ("CRUDEOIL", 5600.0, 1.0, 100, 50.0, 0.38, True, 19), ("CRUDEOILM", 5600.0, 1.0, 10, 50.0, 0.38, True, 19),
    ("NATURALGAS", 290.0, 0.1, 1250, 5.0, 0.55, True, 25), ("NATGASMINI", 290.0, 0.1, 250, 5.0, 0.55, False, 25),
]
SAMPLE_CM = {n: dict(price=p, tick=t, lot=l, step=st, vol=v, options=o, day=d) for n, p, t, l, st, v, o, d in SAMPLE_COMMODITIES}


def cm_expiries(today: date, day: int, n: int = 3) -> list[date]:
    """The next n monthly expiries on (or just before, if it is a weekend) `day` of the month. Invented schedule."""
    out: list[date] = []
    y, m = today.year, today.month
    while len(out) < n:
        d = date(y, m, day)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        if d > today:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def cm_option_expiry(fut_expiry: date) -> date:
    d = fut_expiry - timedelta(days=3)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def sample_und(name: str) -> tuple[str, float] | None:
    """(spot key, strike step) of a sample underlying, index or stock."""
    from .chain import INDEX_UNDERLYINGS
    u = next((u for u in INDEX_UNDERLYINGS if u["name"] == name), None)
    if u:
        return u["spot_key"], float(u["step"])
    st = STOCK_UND.get(name)
    return (st[0], st[1]) if st else None


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(spot: float, strike: float, t_years: float, sigma: float, kind: str) -> float:
    if t_years <= 1e-7 or sigma <= 0:
        return max(spot - strike, 0.0) if kind == "CE" else max(strike - spot, 0.0)
    sq = sigma * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * t_years) / sq
    d2 = d1 - sq
    call = spot * _cdf(d1) - strike * _cdf(d2)
    return call if kind == "CE" else call - spot + strike


def _next_weekday(d: date, wd: int) -> date:
    return d + timedelta(days=(wd - d.weekday()) % 7)


def _expiries(today: date, wd: int, monthly: bool, n: int = 3) -> list[date]:
    out: list[date] = []
    d = today
    now = now_ist()
    while len(out) < n and d < today + timedelta(days=130):
        d = _next_weekday(d, wd)
        is_last = (d + timedelta(days=7)).month != d.month
        alive = d > today or now.hour < 15 or (now.hour == 15 and now.minute < 30)
        if alive and (is_last or not monthly):
            out.append(d)
        d += timedelta(days=1)
    return out


class SampleCatalog(Catalog):
    def __init__(self) -> None:
        super().__init__()
        self.contracts: list[Instrument] = []

    def mcx_loaded(self) -> bool:
        return True

    def mcx_future(self, name: str, on_or_after: date | None = None) -> Instrument | None:
        today = now_ist().date()
        futs = sorted((i for i in self.contracts if i.segment == "mcx_fo" and i.kind == "FUT" and i.name == name.upper()
                       and i.expiry and i.expiry >= (on_or_after or today)), key=lambda i: i.expiry)
        return futs[0] if futs else None

    def _search_loaded(self, toks: list[str], limit: int) -> list[Instrument]:
        want_opt = next((t for t in toks if t in ("CE", "PE", "FUT")), None)
        nums = [t for t in toks if t.replace(".", "", 1).isdigit()]
        words = [t for t in toks if t not in ("CE", "PE", "FUT") and t not in nums]
        if not words:
            return []
        name, extra = words[0], words[1:]
        out = [i for i in self.contracts if i.kind == "EQ" and (i.name.startswith(name) or name in i.desc.upper())]
        from . import commodity as cmd
        hit = set(cmd.resolve(words))
        for i in self.contracts:                                           # commodities: futures unless CE / PE / a strike is typed
            if i.segment == "mcx_fo" and i.name in hit and (i.kind in ("CE", "PE")) == bool(nums or want_opt in ("CE", "PE")) \
                    and (not want_opt or i.kind == want_opt) and (not nums or any(i.strike == float(n) for n in nums)):
                out.append(i)
        if not extra or True:
            for i in self.contracts:
                if i.kind == "EQ" or i.segment == "mcx_fo" or not i.name.startswith(name):
                    continue
                if want_opt and i.kind != want_opt:
                    continue
                if nums and not any(i.strike == float(n) for n in nums):
                    continue
                if all(t in i.symbol.upper() for t in extra):
                    out.append(i)
        return out


def build_catalog(today: date | None = None) -> SampleCatalog:
    cat = SampleCatalog()
    for label, seg, tok, _ in SAMPLE_INDICES:
        cat.register(Instrument(f"{seg}|{tok}", seg, tok, label, label, "INDEX", tick=0.05))
    for sym, desc, tok, _ in SAMPLE_EQUITIES:
        cat.contracts.append(Instrument(f"nse_cm|{tok}", "nse_cm", tok, f"{sym}-EQ", sym, "EQ", tick=0.05, desc=desc))
    today = today or now_ist().date()
    base = {k: v for _, s, t, v in SAMPLE_INDICES for k in [f"{s}|{t}"]}
    tok = 100000
    for und, spot_key, step, lot, wd, seg, monthly in SAMPLE_DERIVS:
        atm = round(base[spot_key] / step) * step
        for exp in _expiries(today, wd, monthly):
            for k in range(-30, 31):
                strike = float(atm + k * step)
                for kind in ("CE", "PE"):
                    tok += 1
                    sym = f"{und}{exp:%y%b}{int(strike)}{kind}".upper()
                    cat.contracts.append(Instrument(f"{seg}|{tok}", seg, str(tok), sym, und, kind, lot=lot, tick=0.05,
                                                    expiry=exp, strike=strike))
    for und, spot_key, step, lot, wd, seg, monthly in SAMPLE_DERIVS:                    # index futures: the last weekday-expiry of the month
        for exp in _expiries(today, wd, True, 2):
            tok += 1
            cat.contracts.append(Instrument(f"{seg}|{tok}", seg, str(tok), f"{und}{exp:%y%b}FUT".upper(), und, "FUT", lot=lot, tick=0.05, expiry=exp))
    px = {tok: v for _, _, tok, v in SAMPLE_EQUITIES}
    for und, ctok, step, lot in SAMPLE_STOCK_DERIVS:                                     # stock options: monthly (last Tuesday)
        atm = round(px[ctok] / step) * step
        for exp in _expiries(today, 1, True):
            for k in range(-30, 31):
                strike = float(atm + k * step)
                if strike <= 0:
                    continue
                for kind in ("CE", "PE"):
                    tok += 1
                    cat.contracts.append(Instrument(f"nse_fo|{tok}", "nse_fo", str(tok), f"{und}{exp:%y%b}{strike:g}{kind}".upper(), und, kind,
                                                    lot=lot, tick=0.05, expiry=exp, strike=strike))
    from . import commodity as cmd
    for name, c in SAMPLE_CM.items():                                                    # commodities
        label = cmd.label_of(name)
        for fexp in cm_expiries(today, c["day"]):
            tok += 1
            cat.contracts.append(Instrument(f"mcx_fo|{tok}", "mcx_fo", str(tok), f"{name}{fexp:%y%b}FUT".upper(), name, "FUT",
                                            lot=c["lot"], tick=c["tick"], expiry=fexp, desc=label))
            if not c["options"]:
                continue
            oexp = cm_option_expiry(fexp)
            atm = round(c["price"] / c["step"]) * c["step"]
            for k in range(-30, 31):
                strike = float(atm + k * c["step"])
                if strike <= 0:
                    continue
                for kind in ("CE", "PE"):
                    tok += 1
                    cat.contracts.append(Instrument(f"mcx_fo|{tok}", "mcx_fo", str(tok), f"{name}{oexp:%y%b}{strike:g}{kind}".upper(), name, kind,
                                                    lot=c["lot"], tick=c["tick"], expiry=oexp, strike=strike, desc=label))
    for i in cat.contracts:
        cat._reg[i.key] = i
    return cat


class SampleFeed:
    """Writes simulated quotes into the hub at ~4 Hz for every instrument somebody is looking at."""

    def __init__(self, hub: QuoteHub, catalog: SampleCatalog, seed: int | None = None) -> None:
        self.hub, self.cat = hub, catalog
        self.rng = random.Random(seed)
        self._stop = threading.Event()
        self.spots = {f"{s}|{t}": v for _, s, t, v in SAMPLE_INDICES}
        self.base = dict(self.spots)
        self.prev = {k: round(v * (1 + self.rng.uniform(-0.004, 0.004)), 2) for k, v in self.spots.items()}
        self.state: dict[str, dict] = {}
        self.eq = {f"nse_cm|{tok}": px for _, _, tok, px in SAMPLE_EQUITIES}
        self.eq_base = dict(self.eq)
        self.eq_prev = {k: round(v * (1 + self.rng.uniform(-0.01, 0.01)), 2) for k, v in self.eq.items()}
        self.cm = {n: c["price"] for n, c in SAMPLE_CM.items()}                      # commodity prices (front-month level)
        self.cm_base = dict(self.cm)
        self.cm_prev = {n: round(v * (1 + self.rng.uniform(-0.01, 0.01)), 2) for n, v in self.cm.items()}
        self._thread: threading.Thread | None = None
        self.connected = True

    def subscribe_keys(self, keys: list[str]) -> None:       # the loop reads hub.wanted() itself
        pass

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="sample-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _hl(self, key: str, px: float, prev: float, oi0: float | None = None) -> dict:
        s = self.state.setdefault(key, {"o": px, "h": px, "l": px, "v": self.rng.randint(10_000, 400_000),
                                        "oi": oi0 if oi0 is not None else self.rng.randint(20_000, 2_000_000), "prev": prev})
        s["h"], s["l"] = max(s["h"], px), min(s["l"], px)
        s["v"] += self.rng.randint(0, 900)
        if self.rng.random() < 0.2:
            s["oi"] = max(0, s["oi"] + self.rng.randint(-1, 1) * 75 * self.rng.randint(1, 6))
        return s

    def _publish(self, key: str, px: float, prev: float, with_oi: bool = False, oi0: float | None = None) -> None:
        s = self._hl(key, px, prev, oi0)
        self.hub.update(Quote(key, round(px, 2), prev_close=round(prev, 2), open=round(s["o"], 2), high=round(s["h"], 2),
                              low=round(s["l"], 2), volume=s["v"], oi=s["oi"] if with_oi else None, ts=time.time()))

    def cm_future_px(self, name: str, fexp: date, today: date) -> float:
        c = SAMPLE_CM[name]
        months = max((fexp - today).days, 0) / 30.0
        return round(round(self.cm[name] * (1 + 0.003 * months) / c["tick"]) * c["tick"], 2)

    def _run_commodity(self, k: str, inst: Instrument, now) -> None:
        c = SAMPLE_CM.get(inst.name)
        if not c:
            return
        today = now.date()
        if inst.kind == "FUT":
            self._publish(k, self.cm_future_px(inst.name, inst.expiry, today), self.cm_prev[inst.name])
            return
        if inst.kind not in ("CE", "PE"):
            return
        fut_exp = next((f for f in cm_expiries(today - timedelta(days=1), c["day"], 4) if cm_option_expiry(f) == inst.expiry), None)
        if fut_exp is None:
            return
        fpx = self.cm_future_px(inst.name, fut_exp, today)
        t = max((datetime(inst.expiry.year, inst.expiry.month, inst.expiry.day, 23, 30, tzinfo=IST) - now).total_seconds() / (365 * 86400), 1e-6)
        px = max(c["tick"], bs_price(fpx, inst.strike, t, c["vol"], inst.kind) * (1 + self.rng.gauss(0, 0.0015)))
        px = round(round(px / c["tick"]) * c["tick"], 2)
        prev_f = fpx * self.cm_prev[inst.name] / max(self.cm[inst.name], 1e-9)
        prev_px = max(c["tick"], bs_price(prev_f, inst.strike, t + 1 / 365, c["vol"], inst.kind))
        oi0 = sample_oi_base(inst.strike, inst.kind, c["price"], c["step"], f"{inst.name}{inst.expiry.isoformat()}")
        self._publish(k, px, prev_px, with_oi=True, oi0=oi0)

    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            wanted = set(self.hub.wanted()) | set(self.spots)
            for k in self.spots:                                   # indices always move (options are priced from them)
                b = self.base[k]
                step = (0.00012 if k.endswith("VIX") is False else 0.0006) * self.rng.gauss(0, 1)
                self.spots[k] = max(0.01, self.spots[k] * (1 + step) + (b - self.spots[k]) * 0.002)
                if k in wanted:
                    self._publish(k, self.spots[k], self.prev[k])
            for k, px in list(self.eq.items()):
                if k in wanted:
                    self.eq[k] = max(0.5, px * (1 + 0.00035 * self.rng.gauss(0, 1) + (self.eq_base[k] - px) / px * 0.002))
                    self._publish(k, self.eq[k], self.eq_prev[k])
            for n, px in list(self.cm.items()):                                        # commodities always move (their options are priced from them)
                self.cm[n] = max(0.1, px * (1 + 0.0004 * self.rng.gauss(0, 1) + (self.cm_base[n] - px) / px * 0.002))
            now = now_ist()
            for k in wanted:
                inst = self.cat.get(k)
                if inst and inst.segment == "mcx_fo" and inst.expiry is not None:
                    self._run_commodity(k, inst, now)
                    continue
                if inst and inst.kind == "FUT" and inst.expiry is not None:
                    fk = next((sk for u, sk, *_ in SAMPLE_DERIVS if u == inst.name), None)
                    sp = self.spots.get(fk or "")
                    if sp:
                        days = max((inst.expiry - now.date()).days, 0)
                        self._publish(k, round(round(sp * (1 + 0.0004 * days / 7) / 0.05) * 0.05, 2), self.prev[fk])
                    continue
                if not inst or inst.kind not in ("CE", "PE") or inst.expiry is None:
                    continue
                info = sample_und(inst.name)
                if not info:
                    continue
                und_key, ustep = info
                stock = inst.name in STOCK_UND
                spot = self.eq.get(und_key) if stock else self.spots.get(und_key)
                if not spot:
                    continue
                sigma = VOL_STOCK if stock else VOL
                exp_dt = datetime(inst.expiry.year, inst.expiry.month, inst.expiry.day, 15, 30, tzinfo=IST)
                t = max((exp_dt - now).total_seconds() / (365 * 86400), 1e-6)
                px = max(0.05, bs_price(spot, inst.strike, t, sigma, inst.kind) * (1 + self.rng.gauss(0, 0.0015)))
                px = round(round(px / 0.05) * 0.05, 2)
                prev_spot = self.eq_prev[und_key] if stock else self.prev[und_key]
                prev_px = max(0.05, bs_price(prev_spot, inst.strike, t + 1 / 365, sigma, inst.kind))
                base = self.eq_base[und_key] if stock else self.base[und_key]
                oi0 = sample_oi_base(inst.strike, inst.kind, base, ustep, f"{inst.name}{inst.expiry.isoformat()}")
                self._publish(k, px, prev_px, with_oi=True, oi0=oi0)
            self._stop.wait(max(0.0, 0.25 - (time.time() - t0)))
