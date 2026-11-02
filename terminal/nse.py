"""
Free NSE public data: FII/DII cash-market flows and end-of-day files (delivery report, F&O bhavcopy).

Principles
  * NSE is not an API product. Its endpoints change and it blocks automated clients at times. So every value is
    CHECKED before it is shown (columns present, numbers parse, net = buy - sell, the file is for the day asked), and
    anything that fails the check becomes "unavailable, and why" - never a guess, never a half-filled table.
  * Network access is separated from interpretation: a provider fetches raw text, pure functions parse it. The
    sample provider writes files in the same formats, so sample mode exercises the same parsers.
  * Requests are slow and polite (one at a time, at least one second apart), cached on disk, and a missing file
    (404) is remembered for ten minutes instead of being asked for again.
  * Everything here is end-of-day information. It is not live and it is not advice.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import random
import re
import statistics
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .clock import now_ist
from .market.chain import buildup, max_pain

log = logging.getLogger("nse")

WWW = "https://www.nseindia.com"
ARCHIVE = "https://nsearchives.nseindia.com"
FII_DII_URL = f"{WWW}/api/fiidiiTradeReact"
COOKIE_PAGE = f"{WWW}/option-chain"
PUBLISH_AFTER = (18, 30)          # files are normally up in the evening; before this we start from the previous day
LOOKBACK_DAYS = 10
MIN_GAP_S = 1.0
INDEX_UNDERLYINGS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")


class NseError(Exception):
    """Something we can explain to the person in one sentence."""


class NseFileMissing(NseError):
    """404: not published (weekend, holiday, too early) - an expected, ordinary outcome."""


# ---------------------------------------------------------------------------------------- numbers & dates
def num(v: Any) -> float | None:
    if v is None:
        return None
    t = str(v).strip().replace(",", "").replace("₹", "")
    if t in ("", "-", "--", "NA", "N/A", "nan"):
        return None
    try:
        x = float(t)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def parse_day(v: Any) -> date | None:
    t = str(v or "").strip()
    for f in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            continue
    return None


def _need(cols: set[str], required: list[str], what: str) -> None:
    missing = [c for c in required if c not in cols]
    if missing:
        raise NseError(f"The {what} file does not have the columns this terminal expects ({', '.join(missing)} missing). "
                       f"NSE may have changed the format, so nothing is shown rather than wrong numbers.")


def _rows(text: str) -> tuple[list[dict], set[str]]:
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    rd.fieldnames = [(h or "").strip() for h in (rd.fieldnames or [])]
    out = []
    for r in rd:
        out.append({(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items()})
    return out, set(rd.fieldnames or [])


# ------------------------------------------------------------------------------------------------ FII / DII
def parse_fii_dii(payload: Any) -> list[dict]:
    """NSE's fiidiiTradeReact reply: a list of {category, date, buyValue, sellValue, netValue} (rupees crore),
    two entries (DII, FII/FPI) per day. Returns [{day, fii:{buy,sell,net}, dii:{...}}] newest first.
    Raises NseError unless EVERY entry is understood and consistent."""
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise NseError("NSE returned no FII/DII figures.")
    days: dict[date, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            raise NseError("NSE's FII/DII reply is in a shape this terminal does not understand.")
        cat = str(it.get("category", "")).upper()
        who = "fii" if ("FII" in cat or "FPI" in cat) else "dii" if "DII" in cat else None
        d = parse_day(it.get("date"))
        buy, sell, net = num(it.get("buyValue")), num(it.get("sellValue")), num(it.get("netValue"))
        if who is None or d is None or None in (buy, sell, net):
            raise NseError("NSE's FII/DII reply has fields this terminal does not understand, so it is not shown.")
        if abs((buy - sell) - net) > 1.0:
            raise NseError(f"NSE's FII/DII figures for {d.isoformat()} do not add up (buy - sell is not net), so they are not shown.")
        days.setdefault(d, {})[who] = {"buy": buy, "sell": sell, "net": net}
    out = [{"day": d.isoformat(), **v} for d, v in days.items() if "fii" in v and "dii" in v]
    if not out:
        raise NseError("NSE's FII/DII reply did not contain both FII and DII for any day.")
    return sorted(out, key=lambda r: r["day"], reverse=True)


# ------------------------------------------------------------------------------------------ delivery report
DELIVERY_REQUIRED = ["SYMBOL", "SERIES", "PREV_CLOSE", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "DELIV_QTY", "DELIV_PER"]
MIN_TURNOVER_CR = 10.0             # movers / delivery lists ignore stocks that traded less than ₹10 crore: tiny volumes mislead


def parse_delivery(text: str, day: date | None = None) -> list[dict]:
    rows, cols = _rows(text)
    _need(cols, DELIVERY_REQUIRED, "delivery report")
    out = []
    for r in rows:
        if r.get("SERIES") != "EQ":
            continue
        close, prev = num(r["CLOSE_PRICE"]), num(r["PREV_CLOSE"])
        turn, vol = num(r["TURNOVER_LACS"]), num(r["TTL_TRD_QNTY"])
        if None in (close, prev, turn, vol) or not prev:
            continue
        d = parse_day(r.get("DATE1"))
        if day and d and d != day:
            raise NseError(f"The delivery file says it is for {d.isoformat()}, not {day.isoformat()}. Not shown.")
        dq, dp = num(r["DELIV_QTY"]), num(r["DELIV_PER"])
        out.append({"symbol": r["SYMBOL"], "close": close, "prev": prev, "chg_pct": round((close - prev) / prev * 100, 2),
                    "volume": int(vol), "turnover_cr": round(turn / 100.0, 2),
                    "deliv_qty": int(dq) if dq is not None else None, "deliv_pct": dp})
    if not out:
        raise NseError("The delivery file had no usable equity rows.")
    return out


def delivery_view(rows: list[dict], q: str = "", top: int = 15) -> dict:
    liquid = [r for r in rows if r["turnover_cr"] >= MIN_TURNOVER_CR]
    by = lambda key, rev: sorted(liquid, key=lambda r: r[key], reverse=rev)[:top]
    with_d = [r for r in liquid if r["deliv_pct"] is not None]
    out = {"count": len(rows), "liquid": len(liquid), "min_turnover_cr": MIN_TURNOVER_CR,
           "gainers": by("chg_pct", True), "losers": by("chg_pct", False), "turnover": by("turnover_cr", True),
           "delivery": sorted(with_d, key=lambda r: r["deliv_pct"], reverse=True)[:top]}
    if q:
        qq = q.strip().upper()
        out["lookup"] = [r for r in rows if qq in r["symbol"]][:20]
    return out


# ---------------------------------------------------------------------------------------------- F&O bhavcopy
FO_REQUIRED = ["TckrSymb", "FinInstrmTp", "XpryDt", "ClsPric", "PrvsClsgPric", "OpnIntrst", "ChngInOpnIntrst"]
FO_OPT_REQUIRED = ["StrkPric", "OptnTp"]


def parse_fo(text: str) -> list[dict]:
    rows, cols = _rows(text)
    _need(cols, FO_REQUIRED, "F&O bhavcopy")
    has_opt = all(c in cols for c in FO_OPT_REQUIRED)
    out = []
    for r in rows:
        tp = r["FinInstrmTp"]
        if tp not in ("IDF", "STF", "IDO", "STO"):
            continue
        exp = parse_day(r["XpryDt"])
        close, prev, oi, chg = num(r["ClsPric"]), num(r["PrvsClsgPric"]), num(r["OpnIntrst"]), num(r["ChngInOpnIntrst"])
        if exp is None or None in (oi, chg):
            continue
        rec = {"symbol": r["TckrSymb"], "type": tp, "expiry": exp.isoformat(), "close": close, "prev": prev, "oi": oi, "oi_chg": chg,
               "under": num(r.get("UndrlygPric"))}
        if tp in ("IDO", "STO"):
            k, ot = num(r.get("StrkPric")) if has_opt else None, (r.get("OptnTp") or "") if has_opt else ""
            if k is None or ot not in ("CE", "PE"):
                continue
            rec["strike"], rec["opt"] = k, ot
        out.append(rec)
    if not out:
        raise NseError("The F&O bhavcopy had no usable rows.")
    return out


def _pct(a: float | None, b: float | None) -> float | None:
    return round((a - b) / b * 100, 2) if a is not None and b else None


def fo_view(rows: list[dict], top: int = 10, band: float = 0.08) -> dict:
    # ---- futures (nearest expiry of each symbol)
    fut: dict[str, dict] = {}
    for r in rows:
        if r["type"] in ("IDF", "STF") and (r["symbol"] not in fut or r["expiry"] < fut[r["symbol"]]["expiry"]):
            fut[r["symbol"]] = r
    flist = []
    for s, r in fut.items():
        oi_prev = r["oi"] - r["oi_chg"]
        flist.append({"symbol": s, "index": r["type"] == "IDF", "expiry": r["expiry"], "close": r["close"], "chg_pct": _pct(r["close"], r["prev"]),
                      "oi": r["oi"], "oi_chg": r["oi_chg"], "oi_chg_pct": _pct(r["oi"], oi_prev) if oi_prev > 0 else None,
                      "buildup": buildup(r["close"], r["prev"], r["oi"], oi_prev)})
    stocks = [f for f in flist if not f["index"] and f["oi_chg_pct"] is not None and f["buildup"]]
    groups = {}
    for name in ("Long build-up", "Short build-up", "Long unwinding", "Short covering"):
        groups[name] = sorted((f for f in stocks if f["buildup"] == name), key=lambda f: abs(f["oi_chg_pct"]), reverse=True)[:top]
    # ---- index options, nearest expiry
    opts: dict[str, Any] = {}
    for und in INDEX_UNDERLYINGS:
        o = [r for r in rows if r["type"] == "IDO" and r["symbol"] == und]
        if not o:
            continue
        exp = min(r["expiry"] for r in o)
        o = [r for r in o if r["expiry"] == exp]
        ce: dict[float, dict] = {r["strike"]: r for r in o if r["opt"] == "CE"}
        pe: dict[float, dict] = {r["strike"]: r for r in o if r["opt"] == "PE"}
        strikes = sorted(set(ce) | set(pe))
        ref = statistics.median([r["under"] for r in o if r["under"]] or [0]) or (fut.get(und) or {}).get("close")
        tce, tpe = sum(r["oi"] for r in ce.values()), sum(r["oi"] for r in pe.values())
        shown = strikes
        if ref:
            shown = sorted(sorted(strikes, key=lambda k: abs(k - ref))[:41])
            shown = [k for k in shown if abs(k - ref) <= ref * band] or shown[:21]
        opts[und] = {"expiry": exp, "ref": ref, "total_ce_oi": tce, "total_pe_oi": tpe, "pcr": round(tpe / tce, 2) if tce else None,
                     "max_pain": max_pain(strikes, {k: v["oi"] for k, v in ce.items()}, {k: v["oi"] for k, v in pe.items()}),
                     "top_ce": [{"strike": k, "oi": ce[k]["oi"]} for k in sorted(ce, key=lambda k: ce[k]["oi"], reverse=True)[:3]],
                     "top_pe": [{"strike": k, "oi": pe[k]["oi"]} for k in sorted(pe, key=lambda k: pe[k]["oi"], reverse=True)[:3]],
                     "strikes": [{"strike": k, "ce_oi": ce[k]["oi"] if k in ce else None, "ce_chg": ce[k]["oi_chg"] if k in ce else None,
                                  "pe_oi": pe[k]["oi"] if k in pe else None, "pe_chg": pe[k]["oi_chg"] if k in pe else None} for k in shown]}
    return {"index_futures": sorted((f for f in flist if f["index"]), key=lambda f: f["symbol"]), "buildup": groups,
            "options": opts, "stock_futures": len(stocks)}


# =================================================================================================== providers
class NseClient:
    """The real thing: talks to nseindia.com. Slow, polite, cached on disk."""

    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/118.0", "Accept": "*/*",
               "Accept-Language": "en-US,en;q=0.5", "Referer": f"{WWW}/get-quotes/equity?symbol=HDFCBANK"}
    name = "nse"

    def __init__(self, cache_dir: str | Path, http=None, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        import httpx
        self._http = http or httpx.Client(headers=self.HEADERS, timeout=20.0, follow_redirects=True)
        self._dir = Path(cache_dir)
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._last = 0.0
        self._cookies = False
        self._missing: dict[str, float] = {}

    # ---- plumbing
    def _get(self, url: str, accept_json: bool = False):
        import httpx
        with self._lock:                                                       # one request at a time, spaced out
            wait = MIN_GAP_S - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            try:
                if accept_json and not self._cookies:
                    self._http.get(COOKIE_PAGE)
                    self._cookies = True
                r = self._http.get(url)
                if r.status_code in (401, 403) and accept_json:                # cookies expired: refresh once
                    self._http.get(COOKIE_PAGE)
                    r = self._http.get(url)
            except httpx.HTTPError as e:
                raise NseError(f"Could not reach NSE ({type(e).__name__}). Check your internet and try again later.") from None
            finally:
                self._last = self._clock()
        if r.status_code == 404:
            raise NseFileMissing("NSE has not published this file (weekend, holiday, or too early in the evening).")
        if r.status_code in (401, 403):
            raise NseError("NSE refused the request (it blocks some automated access). Try again later; nothing is shown meanwhile.")
        if r.status_code == 429:
            raise NseError("NSE is rate-limiting this computer. Wait a few minutes and try again.")
        if r.status_code >= 400:
            raise NseError(f"NSE answered with an error ({r.status_code}). Try again later.")
        return r

    def _file(self, name: str, url: str, zipped: bool) -> str:
        path = self._dir / name
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
        if self._missing.get(name, 0) > self._clock():
            raise NseFileMissing("NSE has not published this file yet.")
        try:
            r = self._get(url)
        except NseFileMissing:
            self._missing[name] = self._clock() + 600
            raise
        data = r.content
        if zipped:
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    names = [n for n in z.namelist() if n.lower().endswith(".csv")]
                    if not names:
                        raise NseError("NSE's zip file had no CSV inside.")
                    data = z.read(names[0])
            except zipfile.BadZipFile:
                raise NseError("NSE sent something that is not a zip file (probably a block page). Try again later.") from None
        text = data.decode("utf-8", errors="replace")
        if "<html" in text[:300].lower():
            raise NseError("NSE sent a web page instead of the data file (probably a block page). Try again later.")
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text

    # ---- provider interface
    def fii_dii(self) -> Any:
        r = self._get(FII_DII_URL, accept_json=True)
        try:
            return r.json()
        except ValueError:
            raise NseError("NSE's FII/DII reply was not data (probably a block page). Try again later.") from None

    def delivery_csv(self, d: date) -> str:
        return self._file(f"sec_bhavdata_full_{d:%d%m%Y}.csv", f"{ARCHIVE}/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv", False)

    def fo_csv(self, d: date) -> str:
        return self._file(f"BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv", f"{ARCHIVE}/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip", True)


class SampleNse:
    """Invented data in NSE's file formats, so sample mode runs the same parsers. Never touches the network."""
    name = "sample"

    def __init__(self, today: Callable[[], date] = lambda: now_ist().date()) -> None:
        self._today = today

    def fii_dii(self) -> Any:
        rng, d, out = random.Random(77), self._today(), []
        while len(out) < 30:
            if d.weekday() < 5:
                for cat in ("DII", "FII/FPI "):
                    buy = round(rng.uniform(6000, 14000), 2)
                    sell = round(rng.uniform(6000, 14000), 2)
                    out.append({"category": cat, "date": d.strftime("%d-%b-%Y"), "buyValue": f"{buy:.2f}", "sellValue": f"{sell:.2f}",
                                "netValue": f"{buy - sell:.2f}"})
            d -= timedelta(days=1)
        return out

    def delivery_csv(self, d: date) -> str:
        from .market.sample import SAMPLE_EQUITIES
        rng = random.Random(d.toordinal())
        extra = [(f"DEMO{i:02d}", "", "", rng.uniform(80, 2400)) for i in range(1, 41)]
        lines = ["SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"]
        for sym, _, _, px in list(SAMPLE_EQUITIES) + extra:
            prev = round(px, 2)
            close = round(prev * (1 + rng.uniform(-0.04, 0.04)), 2)
            vol = int(rng.uniform(2e5, 9e6))
            dq = int(vol * rng.uniform(0.2, 0.8))
            lines.append(f"{sym}, EQ, {d:%d-%b-%Y}, {prev}, {prev}, {max(prev, close)}, {min(prev, close)}, {close}, {close}, {close}, "
                         f"{vol}, {vol * close / 1e5:.2f}, {int(vol / 40)}, {dq}, {dq / vol * 100:.2f}")
        return "\n".join(lines)

    def fo_csv(self, d: date) -> str:
        from .market.sample import SAMPLE_EQUITIES, SAMPLE_INDICES
        rng = random.Random(d.toordinal() + 1)
        hdr = "TradDt,BizDt,TckrSymb,FinInstrmTp,XpryDt,StrkPric,OptnTp,ClsPric,PrvsClsgPric,UndrlygPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol"
        lines = [hdr]
        exp = d + timedelta(days=(1 - d.weekday()) % 7 + 7)                       # an invented expiry
        spots = {"NIFTY": (24250.0, 50), "BANKNIFTY": (51850.0, 100), "FINNIFTY": (23180.0, 50), "MIDCPNIFTY": (12850.0, 25)}
        for und, (spot, step) in spots.items():
            prev_f = spot * 1.001
            lines.append(f"{d},{d},{und},IDF,{exp},,,{spot * (1 + rng.uniform(-.01, .01)):.2f},{prev_f:.2f},{spot},{int(rng.uniform(5e5, 3e6))},{int(rng.uniform(-8e4, 8e4))},1000")
            atm = round(spot / step) * step
            for i in range(-15, 16):
                k = atm + i * step
                for ot in ("CE", "PE"):
                    base = 4e5 * math.exp(-abs(i) / 6) * (1.3 if (ot == "CE") == (i > 0) else 0.8)
                    oi = int(base * rng.uniform(0.6, 1.4))
                    lines.append(f"{d},{d},{und},IDO,{exp},{k:.2f},{ot},{max(1.0, abs(spot - k) * 0.4 + 30):.2f},{max(1.0, abs(spot - k) * 0.4 + 28):.2f},{spot},{oi},{int(oi * rng.uniform(-.25, .3))},500")
        for sym, _, _, px in SAMPLE_EQUITIES:
            prev = px
            close = round(px * (1 + rng.uniform(-.03, .03)), 2)
            oi = int(rng.uniform(2e6, 2e7))
            lines.append(f"{d},{d},{sym},STF,{exp},,,{close},{prev},{px},{oi},{int(oi * rng.uniform(-.12, .12))},800")
        return "\n".join(lines)


# ================================================================================================== service
class NseService:
    def __init__(self, provider, store, mode: str, today: Callable[[], datetime] = now_ist) -> None:
        self.p, self.store, self.mode, self._now = provider, store, mode, today
        self._lock = threading.Lock()
        self._fii_try = 0.0
        self._parsed: dict[tuple, Any] = {}

    @property
    def source(self) -> str:
        return self.p.name

    # ---- which day to ask for
    def candidate_days(self) -> list[date]:
        now = self._now()
        d = now.date()
        if now.hour * 60 + now.minute < PUBLISH_AFTER[0] * 60 + PUBLISH_AFTER[1]:
            d -= timedelta(days=1)
        out = []
        while len(out) < LOOKBACK_DAYS:
            if d.weekday() < 5:
                out.append(d)
            d -= timedelta(days=1)
        return out

    def _latest(self, fetch: Callable[[date], str], day: date | None) -> tuple[date, str]:
        if day is not None:
            return day, fetch(day)
        last: Exception | None = None
        for d in self.candidate_days():
            try:
                return d, fetch(d)
            except NseFileMissing as e:
                last = e
        raise NseError(f"No file found for the last {LOOKBACK_DAYS} weekdays ({last}).")

    def _check_day(self, day: str) -> date | None:
        if not day:
            return None
        d = parse_day(day)
        today = self._now().date()
        if d is None or d > today or d < today - timedelta(days=90):
            raise NseError("Pick a date within the last 90 days.")
        return d

    # ---- FII / DII
    def fii_dii(self, refresh_every_s: float = 600.0) -> dict:
        err = ""
        now = time.time()
        with self._lock:
            if now - self._fii_try >= refresh_every_s:
                self._fii_try = now
                try:
                    rows = parse_fii_dii(self.p.fii_dii())
                    self.store.add_fii_dii(self.mode, rows, now)
                except NseError as e:
                    err = str(e)
                except Exception as e:                                          # never let a surprise reach the screen as wrong data
                    log.exception("fii/dii")
                    err = f"Unexpected problem reading NSE's reply ({type(e).__name__})."
        rows = self.store.fii_dii(self.mode, 60)
        if not rows:
            return {"ok": False, "reason": "unavailable", "error": err or "No FII/DII figures yet."}
        return {"ok": True, "source": self.source, "rows": rows, "error": err,
                "note": "Rupees crore, cash market, provisional as published by NSE. History is kept from the days this terminal has fetched."}

    # ---- delivery report
    def delivery(self, day: str = "", q: str = "") -> dict:
        try:
            d = self._check_day(day)
            dd, text = self._latest(self.p.delivery_csv, d)
            key = (dd, len(text))
            if self._parsed.get("del", (None,))[0] != key:
                self._parsed["del"] = (key, parse_delivery(text, dd))
            return {"ok": True, "source": self.source, "day": dd.isoformat(), **delivery_view(self._parsed["del"][1], q)}
        except NseError as e:
            return {"ok": False, "reason": "unavailable", "error": str(e)}

    # ---- F&O bhavcopy
    def fo(self, day: str = "") -> dict:
        try:
            d = self._check_day(day)
            dd, text = self._latest(self.p.fo_csv, d)
            key = (dd, len(text))
            if self._parsed.get("fo", (None,))[0] != key:
                self._parsed["fo"] = (key, parse_fo(text))
            return {"ok": True, "source": self.source, "day": dd.isoformat(), **fo_view(self._parsed["fo"][1])}
        except NseError as e:
            return {"ok": False, "reason": "unavailable", "error": str(e)}
