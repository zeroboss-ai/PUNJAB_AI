"""
Option chain + open-interest analysis.

  provider  gives a raw snapshot of one chain (strikes, call/put legs with price and open interest)
              - SampleChainProvider: invented, deterministic OI shape, prices move with the sample feed
              - KotakChainProvider:  Kotak's option_chain() / expiries() REST calls
  service   caches snapshots, overlays the LIVE prices/OI from the quote hub, and derives the analysis:
            ATM, PCR, max pain, largest call/put OI strikes, ATM straddle, per-leg OI build-up label.

Everything derived here is arithmetic on the numbers shown; it is not a trading signal.
"""
from __future__ import annotations

import logging
import math
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ..clock import now_ist
from . import commodity as cmd
from .hub import QuoteHub
from .models import Instrument

log = logging.getLogger("chain")

# name, label, kotak exchange segment, cash-segment spot key
INDEX_UNDERLYINGS = [
    {"name": "NIFTY", "label": "Nifty 50", "fo": "nse_fo", "spot_key": "nse_cm|Nifty 50", "step": 50},
    {"name": "BANKNIFTY", "label": "Nifty Bank", "fo": "nse_fo", "spot_key": "nse_cm|Nifty Bank", "step": 100},
    {"name": "SENSEX", "label": "Sensex", "fo": "bse_fo", "spot_key": "bse_cm|SENSEX", "step": 100},
    {"name": "FINNIFTY", "label": "Fin Service", "fo": "nse_fo", "spot_key": "nse_cm|Nifty Fin Service", "step": 50},
    {"name": "MIDCPNIFTY", "label": "Midcap Select", "fo": "nse_fo", "spot_key": "nse_cm|NIFTY MID SELECT", "step": 25},
]


@dataclass
class Leg:
    key: str
    symbol: str
    ltp: float | None
    prev_close: float | None
    oi: float | None
    oi_prev: float | None
    volume: float | None = None


@dataclass
class RawChain:
    underlying: str
    expiry: str                      # ISO date
    lot: int
    spot_key: str | None
    spot: float | None               # as of the REST snapshot (may be None)
    atm_hint: float | None           # strike Kotak flagged ATM, if any
    rows: dict[float, dict[str, Leg]] = field(default_factory=dict)      # strike -> {"CE": Leg, "PE": Leg}
    fetched: float = field(default_factory=time.time)


class ChainUnavailable(Exception):
    """Raised with a user-readable reason."""


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------- analysis
def max_pain(strikes: list[float], ce_oi: dict[float, float], pe_oi: dict[float, float]) -> float | None:
    """Strike at which option BUYERS together would lose the most (writers pay out the least) at expiry."""
    if not strikes:
        return None
    best, best_k = None, None
    for k in strikes:
        pay = 0.0
        for s in strikes:
            pay += ce_oi.get(s, 0.0) * max(k - s, 0.0) + pe_oi.get(s, 0.0) * max(s - k, 0.0)
        if best is None or pay < best:
            best, best_k = pay, k
    return best_k


def buildup(ltp: float | None, prev_close: float | None, oi: float | None, oi_prev: float | None) -> str | None:
    """Standard four-way reading of price direction vs OI direction. A description, not a recommendation."""
    if None in (ltp, prev_close, oi, oi_prev) or not prev_close or not oi_prev:
        return None
    dp, doi = ltp - prev_close, oi - oi_prev
    if abs(dp) < 1e-9 or abs(doi) < 1e-9:
        return None
    if dp > 0 and doi > 0:
        return "Long build-up"
    if dp < 0 and doi > 0:
        return "Short build-up"
    if dp > 0 and doi < 0:
        return "Short covering"
    return "Long unwinding"


def _leg_dict(leg: Leg, hub: QuoteHub) -> dict:
    q = hub.get(leg.key)
    ltp = q.ltp if q else leg.ltp
    pc = (q.prev_close if q and q.prev_close else None) or leg.prev_close
    oi = (q.oi if q and q.oi else None) or leg.oi
    vol = (q.volume if q and q.volume else None) or leg.volume
    prev = leg.oi_prev
    chg = None if oi is None or prev is None else oi - prev
    return {
        "key": leg.key, "symbol": leg.symbol, "ltp": ltp, "pc": pc,
        "chg_pct": None if not (ltp is not None and pc) else round((ltp - pc) / pc * 100, 2),
        "oi": oi, "oi_prev": prev, "oi_chg": chg,
        "oi_chg_pct": None if chg is None or not prev else round(chg / prev * 100, 2),
        "vol": vol, "buildup": buildup(ltp, pc, oi, prev),
    }


def analyse(raw: RawChain, hub: QuoteHub, window: int | None) -> dict:
    spot = hub.ltp(raw.spot_key) if raw.spot_key else None
    spot = spot or raw.spot
    strikes = sorted(raw.rows)
    atm = None
    if spot and strikes:
        atm = min(strikes, key=lambda k: abs(k - spot))
    elif raw.atm_hint in strikes:
        atm = raw.atm_hint

    rows, ce_oi, pe_oi = [], {}, {}
    for k in strikes:
        legs = raw.rows[k]
        ce = _leg_dict(legs["CE"], hub) if "CE" in legs else None
        pe = _leg_dict(legs["PE"], hub) if "PE" in legs else None
        if ce and ce["oi"] is not None:
            ce_oi[k] = ce["oi"]
        if pe and pe["oi"] is not None:
            pe_oi[k] = pe["oi"]
        rows.append({"strike": k, "ce": ce, "pe": pe})

    tot_ce, tot_pe = sum(ce_oi.values()), sum(pe_oi.values())
    chg_ce = sum((r["ce"]["oi_chg"] or 0) for r in rows if r["ce"])
    chg_pe = sum((r["pe"]["oi_chg"] or 0) for r in rows if r["pe"])
    prev_ce = sum((r["ce"]["oi_prev"] or 0) for r in rows if r["ce"])
    prev_pe = sum((r["pe"]["oi_prev"] or 0) for r in rows if r["pe"])

    def top(d: dict[float, float], n: int = 3) -> list[dict]:
        return [{"strike": k, "oi": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]

    straddle = None
    atm_ce = None
    atm_pe = None
    if atm is not None:
        r = next((r for r in rows if r["strike"] == atm), None)
        if r and r["ce"] and r["pe"]:
            atm_ce = r["ce"]["ltp"]
            atm_pe = r["pe"]["ltp"]
            if atm_ce is not None and atm_pe is not None:
                straddle = round(atm_ce + atm_pe, 2)

    # trim the displayed window around ATM (the analysis above always uses every strike we have)
    shown = rows
    if window and atm is not None:
        i = strikes.index(atm)
        shown = rows[max(0, i - window): i + window + 1]

    return {
        "spot": spot, "atm": atm, "rows": shown, "strikes_total": len(rows),
        "summary": {
            "pcr": round(tot_pe / tot_ce, 3) if tot_ce else None,
            "pcr_prev": round(prev_pe / prev_ce, 3) if prev_ce else None,
            "ce_oi": tot_ce, "pe_oi": tot_pe, "ce_oi_chg": chg_ce, "pe_oi_chg": chg_pe,
            "max_pain": max_pain(strikes, ce_oi, pe_oi),
            "ce_top": top(ce_oi), "pe_top": top(pe_oi),
            "straddle": straddle,
            "straddle_pct": round(straddle / spot * 100, 2) if straddle and spot else None,
            "atm_ce": atm_ce,
            "atm_pe": atm_pe,
        },
    }


# ---------------------------------------------------------------------------------------- sample
def sample_oi_base(strike: float, kind: str, spot: float, step: float, seed_key: str) -> float:
    """Invented but plausible OI shape: calls pile up above the market, puts below, with round-number spikes."""
    rng = random.Random(f"{seed_key}|{strike}|{kind}")
    d = (strike - spot) / step
    centre = 4.0 if kind == "CE" else -4.0
    hump = math.exp(-((d - centre) / 5.5) ** 2)
    round_bonus = 1.7 if int(strike) % (step * 4) == 0 else 1.0
    return round(max(5000, (150_000 + 3_800_000 * hump) * round_bonus * rng.uniform(0.8, 1.2)) / 75) * 75


class SampleChainProvider:
    source = "sample"

    def __init__(self, catalog, spots: dict[str, float], derivs: list[tuple], eq_spots: dict[str, float] | None = None) -> None:
        self.cat, self.spots, self.derivs = catalog, spots, derivs
        self.eq_spots = eq_spots or {}                      # stock prices (sample world), for the first snapshot's spot

    def underlyings(self) -> list[dict]:
        names = {d[0] for d in self.derivs}
        return [u for u in INDEX_UNDERLYINGS if u["name"] in names]

    def _stock_names(self) -> dict[str, tuple[str, int]]:
        out: dict[str, tuple[str, int]] = {}
        for i in self.cat.contracts:
            if i.segment == cmd.SEGMENT:
                continue
            if i.kind in ("CE", "PE") and i.name not in out and not any(u["name"] == i.name for u in INDEX_UNDERLYINGS):
                eq = next((e for e in self.cat.contracts if e.kind == "EQ" and e.name == i.name), None)
                out[i.name] = (eq.desc if eq and eq.desc else i.name, i.lot)
        return out

    def _cm_names(self) -> dict[str, int]:
        return {i.name: i.lot for i in self.cat.contracts if i.segment == cmd.SEGMENT and i.kind in ("CE", "PE")}

    def search(self, q: str, limit: int = 12) -> list[dict]:
        q = q.strip().upper()
        hits = [{"name": u["name"], "label": u["label"], "lot": None, "kind": "index"} for u in self.underlyings()
                if q in u["name"] or q in u["label"].upper()]
        cms = self._cm_names()
        pick = set(cmd.resolve(q.split())) | {n for n in cms if n.startswith(q)}
        hits += [{"name": n, "label": cmd.label_of(n), "lot": cms[n], "kind": "commodity", "size": cmd.info(n)["size"]}
                 for n in sorted(pick) if n in cms]
        for n, (desc, lot) in sorted(self._stock_names().items()):
            if n.startswith(q) or q in desc.upper():
                hits.append({"name": n, "label": desc, "lot": lot, "kind": "stock"})
        return hits[:limit]

    def check(self, und: str) -> None:
        if not any(u["name"] == und for u in INDEX_UNDERLYINGS) and und not in self._stock_names() and und not in self._cm_names():
            raise ChainUnavailable(f"{und} has no options in the sample world. Search for RELIANCE, TCS, SBIN, GOLD, CRUDE ...")

    def _contracts(self, und: str, expiry: date | None = None):
        return [i for i in self.cat.contracts if i.kind in ("CE", "PE") and i.name == und and (expiry is None or i.expiry == expiry)]

    def expiries(self, und: str) -> list[str]:
        return sorted({i.expiry.isoformat() for i in self._contracts(und) if i.expiry})

    def fetch(self, und: str, expiry: str | None) -> RawChain:
        exps = self.expiries(und)
        if not exps:
            raise ChainUnavailable(f"No sample contracts for {und}")
        expiry = expiry if expiry in exps else exps[0]
        from .sample import sample_und, SAMPLE_CM
        if und in SAMPLE_CM:                                                         # commodity: the option is on the matching future
            fut = self.cat.mcx_future(und, date.fromisoformat(expiry))
            spot_key, step = (fut.key if fut else None), SAMPLE_CM[und]["step"]
            base = SAMPLE_CM[und]["price"]
        else:
            spot_key, step = sample_und(und)
            base = self.spots.get(spot_key) or self.eq_spots.get(spot_key, 0.0)
        contracts = self._contracts(und, date.fromisoformat(expiry))
        raw = RawChain(und, expiry, contracts[0].lot, spot_key, base, None)
        for i in contracts:
            oi = sample_oi_base(i.strike, i.kind, base, step, f"{und}{expiry}")
            prev = round(oi * random.Random(f"p|{i.key}").uniform(0.88, 1.1) / 75) * 75
            raw.rows.setdefault(i.strike, {})[i.kind] = Leg(i.key, i.symbol, None, None, oi, prev)
        return raw


COMMODITY_ALIASES = {
    # Typo / abbreviation mappings to official MCX underlying symbols:
    "CRUDEM": "CRUDEOILM",
    "CRUDEMINI": "CRUDEOILM",
    "CRUDEOILM": "CRUDEOILM",
    "CRUDE": "CRUDEOIL",
    "NATGAS": "NATURALGAS",
    "NATGASM": "NATGASMINI",
    "NATGASMINI": "NATGASMINI",
    "NATURALGASM": "NATGASMINI",
    "SILVERMINI": "SILVERM",
    "GOLDMINI": "GOLDM",
}


# ----------------------------------------------------------------------------------------- Kotak
class KotakChainProvider:
    source = "kotak"

    FULL_COUNTS = (200, 100)            # asked first: the whole chain. Kotak wants a multiple of 10 (SDK docs); its default is 40.

    def __init__(self, session, catalog, strike_count: int = 40) -> None:
        self.s, self.cat, self.count = session, catalog, strike_count
        self._counts = [c for c in self.FULL_COUNTS if c > strike_count] + [strike_count]
        self.count_used: int | None = None      # the first count Kotak accepted; remembered so a refusal is not retried every refresh

    def _get_session(self):
        # 1. Callable getter (e.g. svc.get_valid_kotak_session)
        if callable(self.s):
            try:
                res = self.s()
                if res and getattr(res, "logged_in", False) and getattr(res, "client", None) is not None:
                    return res
            except Exception:
                pass
        # 2. Object with session getter or sessions attribute
        if hasattr(self.s, "get_valid_kotak_session"):
            try:
                res = self.s.get_valid_kotak_session()
                if res and getattr(res, "logged_in", False) and getattr(res, "client", None) is not None:
                    return res
            except Exception:
                pass
        if hasattr(self.s, "get_session"):
            try:
                res = self.s.get_session()
                if res and getattr(res, "logged_in", False) and getattr(res, "client", None) is not None:
                    return res
            except Exception:
                pass
        if hasattr(self.s, "sessions") and isinstance(self.s.sessions, dict):
            for s in self.s.sessions.values():
                if s and getattr(s, "logged_in", False) and getattr(s, "client", None) is not None:
                    return s
        # 3. Direct session instance
        if getattr(self.s, "logged_in", False) and getattr(self.s, "client", None) is not None:
            return self.s
        # 4. Try from catalog's instrument session
        cat = getattr(self.cat, "inner", self.cat)
        ins = getattr(cat, "ins", None)
        sess = getattr(ins, "s", None)
        if sess:
            cand = sess() if callable(sess) else sess
            if cand and getattr(cand, "logged_in", False) and getattr(cand, "client", None) is not None:
                return cand
        return None

    def _und(self, und: str) -> str:
        u = und.strip().upper()
        return COMMODITY_ALIASES.get(u, u)

    def underlyings(self) -> list[dict]:
        return INDEX_UNDERLYINGS

    def _fo(self):
        """The Kotak catalog behind the lazy wrapper, or None if it cannot list F&O names."""
        cat = getattr(self.cat, "inner", self.cat)
        return cat if hasattr(cat, "fo_underlyings") else None

    def _mcx(self):
        cat = getattr(self.cat, "inner", self.cat)
        return cat if hasattr(cat, "mcx_underlyings") else None

    def _mcx_known(self, und: str) -> bool:
        cat = self._mcx()
        if cat is None:
            return False
        try:
            return und in cat.mcx_names()
        except Exception:
            return False

    def search(self, q: str, limit: int = 12) -> list[dict]:
        q = q.strip().upper()
        hits = [{"name": u["name"], "label": u["label"], "lot": None, "kind": "index"} for u in INDEX_UNDERLYINGS
                if q in u["name"] or q in u["label"].upper()]
        mc = self._mcx()
        if mc is not None:
            try:
                hits += [{**c, "kind": "commodity"} for c in mc.mcx_underlyings(q, limit)]
            except Exception as e:
                log.warning("commodity search failed: %r", e)
        cat = self._fo()
        if cat is None:
            return hits[:limit]
        try:
            stocks = cat.fo_underlyings(q, limit)
        except Exception as e:
            raise ChainUnavailable(f"The F&O instrument list is not available yet ({type(e).__name__}). Try again in a few seconds.") from None
        seen = {h["name"] for h in hits}
        hits += [{**s, "kind": "stock"} for s in stocks if s["name"] not in seen]
        return hits[:limit]

    def check(self, und: str) -> None:
        target = self._und(und)
        if any(u["name"] == target for u in INDEX_UNDERLYINGS) or self._mcx_known(target) or cmd.is_commodity(target):
            return
        cat = self._fo()
        if cat is None:
            return
        try:
            known = cat.fo_names()
        except Exception:
            return                                            # cannot check: let Kotak answer
        if target not in known:
            raise ChainUnavailable(f"{target} is not in Kotak's F&O list (no options are listed for it). Use the search box to find the exact name.")

    def _client(self):
        sess = self._get_session()
        if not sess or not getattr(sess, "logged_in", False) or getattr(sess, "client", None) is None:
            raise ChainUnavailable("Not connected to Kotak yet.")
        return sess.client

    @staticmethod
    def _check(resp: Any, what: str) -> dict:
        if not isinstance(resp, dict):
            raise ChainUnavailable(f"Kotak returned an unreadable {what} reply.")
        if resp.get("rateLimit") is not None:
            raise ChainUnavailable("Kotak is rate-limiting option-chain requests. Wait a few seconds.")
        err = resp.get("error") or resp.get("Error")
        if err:
            msg = err[0].get("message") if isinstance(err, list) and err and isinstance(err[0], dict) else str(err)
            raise ChainUnavailable(f"Kotak could not return the {what}: {str(msg)[:160]}")
        return resp

    def _exchange(self, und: str) -> str:
        target = self._und(und)
        u = next((u for u in INDEX_UNDERLYINGS if u["name"] == target), None)
        if u:
            return u["fo"]
        return cmd.SEGMENT if (self._mcx_known(target) or cmd.is_commodity(target)) else "nse_fo"

    def expiries(self, und: str) -> list[str]:
        target = self._und(und)
        ex = self._exchange(target)
        r = self._check(self._client().expiries(exchange=ex, underlying=target, instrument_type="option"), "expiry list")
        out = r.get("expiries") or (r.get("data") or {}).get("expiries") or []
        return sorted(d for d in (self._iso(x, ex) for x in out) if d)

    @staticmethod
    def _iso(x: Any, segment: str) -> str | None:
        """An expiry as YYYY-MM-DD. Kotak's date format is not documented for every segment (MCX/BSE may send Unix seconds), so be tolerant."""
        t = str(x).strip()
        if len(t) >= 10 and t[4:5] == "-" and t[7:8] == "-":
            return t[:10]
        from ..brokers.kotak import parse_expiry
        d = parse_expiry(t, now_ist().date(), segment)
        return d.isoformat() if d else None

    def _spot_key(self, und: str, expiry: str | None = None) -> str | None:
        target = self._und(und)
        u = next((u for u in INDEX_UNDERLYINGS if u["name"] == target), None)
        if u:
            return u["spot_key"]
        if self._mcx_known(target) or cmd.is_commodity(target):                              # an option on a future: the future is the "spot"
            try:
                mc = self._mcx()
                if mc is not None:
                    fut = mc.mcx_future(target, date.fromisoformat(expiry[:10]) if expiry else None)
                    return fut.key if fut else None
            except Exception:
                return None
            return None
        try:                                                  # a stock: its cash-market instrument
            hit = next((i for i in self.cat.search(target, 5) if i.kind == "EQ" and i.name == target and i.segment == "nse_cm"), None)
            return hit.key if hit else None
        except Exception:
            return None

    def _option_chain(self, und: str, expiry: str | None) -> dict:
        """option_chain() for as many strikes as Kotak will give: 200, then 100, then 40. Only a refusal moves to the next size."""
        target = self._und(und)
        kw: dict[str, Any] = {"exchange": self._exchange(target), "underlying": target, "instrument_type": "option"}
        if expiry:
            kw["expiry"] = expiry
        counts = [self.count_used] if self.count_used else self._counts
        last: ChainUnavailable | None = None
        for n in counts:
            try:
                r = self._check(self._client().option_chain(**kw, count=n), "option chain")
            except ChainUnavailable as e:
                if "rate-limiting" in str(e) or "Not connected" in str(e):
                    raise
                last = e
                continue
            if n == counts[0] or self.count_used is None:
                self.count_used = n
            return r
        raise last or ChainUnavailable(f"Kotak returned no option chain for {target}.")

    def fetch(self, und: str, expiry: str | None) -> RawChain:
        target = self._und(und)
        r = self._option_chain(target, expiry)
        log.info("Kotak option_chain reply for %s: %s", target, str(r)[:300])
        d = r.get("data") if isinstance(r.get("data"), dict) else r
        common = d.get("common_data") or {}
        lot = int(_f(common.get("mktLot")) or 0)
        if lot <= 0:
            mc = self._mcx()
            if mc is not None:
                try:
                    lot = int(mc.mcx_names().get(target, {}).get("lot") or 0)
                except Exception:
                    lot = 0
            if lot <= 0:
                lot = 10 if target in ("CRUDEOILM", "CRUDEM", "CRUDEMINI", "GOLDM") else (5 if target in ("SILVERM", "SILVERMINI") else 1)
        if target in ("CRUDEOILM", "CRUDEM", "CRUDEMINI") and lot != 10:
            lot = 10
        if target in ("SILVERM", "SILVERMINI") and lot > 5:
            lot = 5
        exp_iso = (self._iso(common.get("expiryDt"), self._exchange(target)) if common.get("expiryDt") else None) or (expiry or "")[:10]
        spot_dict = d.get("spot") or {}
        fut_dict = d.get("future") or {}
        spot_val = _f(spot_dict.get("ltp")) or _f(fut_dict.get("ltp"))
        raw = RawChain(target, exp_iso, lot, self._spot_key(target, exp_iso or expiry), spot_val, None)
        for side, kind in (("call", "CE"), ("put", "PE")):
            for it in d.get(side) or []:
                ins = it.get("inst") or it.get("instrument") or {}
                q = it.get("quote") or {}
                oi = it.get("oi") or it.get("openInterest") or {}
                strike = _f(ins.get("strkPrc")) or _f(ins.get("strikePrice"))
                key = ins.get("neoSymbol") or ins.get("token")
                if strike is None or not key:
                    continue
                key_str = str(key)
                if "|" not in key_str:
                    key_str = f"{self._exchange(target)}|{key_str}"
                if str(ins.get("moneyness", "")).upper() == "ATM":
                    raw.atm_hint = strike
                raw.rows.setdefault(strike, {})[kind] = Leg(
                    key_str,
                    str(ins.get("symbol") or ""),
                    _f(q.get("ltp")),
                    _f(q.get("pc")) or _f(q.get("prevClose")),
                    _f(oi.get("cur")) or _f(oi.get("current")),
                    _f(oi.get("prev")) or _f(oi.get("previous")),
                    _f(q.get("vol")) or _f(q.get("volume")),
                )
        if not raw.rows:
            raise ChainUnavailable("Kotak returned an empty option chain. The market may be closed or the name may be wrong.")
        return raw


# --------------------------------------------------------------------------------------- service
class ChainService:
    OWNER = "chain"

    def __init__(self, hub: QuoteHub, store=None, ttl: float = 20.0) -> None:
        self.hub, self.store, self.ttl = hub, store, ttl
        self.provider = None
        self._raw: dict[tuple[str, str], RawChain] = {}
        self._exp: dict[str, tuple[float, list[str]]] = {}
        self._lock = threading.Lock()
        self._followed: set[str] = set()
        self._last_series: dict[tuple[str, str], float] = {}

    def set_provider(self, p) -> None:
        with self._lock:
            self.provider = p
            self._raw.clear()
            self._exp.clear()

    @property
    def ready(self) -> bool:
        return self.provider is not None

    def underlyings(self) -> list[dict]:
        return self.provider.underlyings() if self.provider else []

    def search(self, q: str, limit: int = 12) -> list[dict]:
        """Any underlying that has options (indices and F&O stocks) whose name starts with, or whose company name contains, q."""
        if not self.provider:
            raise ChainUnavailable("Not connected to Kotak yet.")
        return self.provider.search(q, limit) if q.strip() and hasattr(self.provider, "search") else []

    def expiries(self, und: str) -> list[str]:
        if not self.provider:
            raise ChainUnavailable("Not connected to Kotak yet.")
        now = time.time()
        hit = self._exp.get(und)
        if hit and now - hit[0] < 600:
            return hit[1]
        exps = [e for e in self.provider.expiries(und) if e >= now_ist().date().isoformat()]
        self._exp[und] = (now, exps)
        return exps

    def _raw_chain(self, und: str, expiry: str) -> RawChain:
        k = (und, expiry)
        hit = self._raw.get(k)
        if hit and time.time() - hit.fetched < self.ttl:
            return hit
        with self._lock:
            hit = self._raw.get(k)
            if hit and time.time() - hit.fetched < self.ttl:
                return hit
            try:
                raw = self.provider.fetch(und, expiry)
            except ChainUnavailable:
                if hit:                                       # serve the last good snapshot rather than a blank page
                    return hit
                raise
            self._raw[k] = raw
            return raw

    def raw_for(self, und: str, sel: str) -> RawChain:
        """The raw chain for strategies: sel is 'nearest', 'next' or an ISO expiry date."""
        if not self.provider:
            raise ChainUnavailable("Not connected to Kotak yet.")
        exps = self.expiries(und.upper())
        if not exps:
            raise ChainUnavailable(f"No expiries found for {und}.")
        if sel == "nearest":
            pick = exps[0]
        elif sel == "next":
            if len(exps) < 2:
                raise ChainUnavailable(f"Only one expiry is listed for {und}.")
            pick = exps[1]
        elif sel in exps:
            pick = sel
        else:
            raise ChainUnavailable(f"{und} has no expiry on {sel}.")
        return self._raw_chain(und.upper(), pick)

    FOLLOW_STRIKES = 60

    def _follow(self, raw: RawChain) -> bool:
        """Subscribe to live prices for the strikes nearest the money only; far strikes show the chain snapshot."""
        ref = self.hub.ltp(raw.spot_key) if raw.spot_key else None
        ref = ref or raw.spot or raw.atm_hint
        ks = sorted(raw.rows)
        if ref:
            ks = sorted(ks, key=lambda k: abs(k - ref))
        elif len(ks) > self.FOLLOW_STRIKES:
            mid = len(ks) // 2
            ks = ks[max(0, mid - self.FOLLOW_STRIKES // 2): mid + self.FOLLOW_STRIKES // 2]
        keys = {l.key for k in ks[: self.FOLLOW_STRIKES] for l in raw.rows[k].values()}
        if raw.spot_key:
            keys.add(raw.spot_key)
        new, gone = keys - self._followed, self._followed - keys
        if new:
            self.hub.want(self.OWNER, sorted(new))
        if gone:
            self.hub.release(self.OWNER, sorted(gone))
        self._followed = keys
        return bool(new)

    def get(self, und: str, expiry: str | None, window: int | None) -> dict:
        if not self.provider:
            raise ChainUnavailable("Not connected to Kotak yet.")
        und = und.upper()
        if hasattr(self.provider, "check"):
            self.provider.check(und)
        exps = self.expiries(und)
        if not exps:
            raise ChainUnavailable(f"No expiries found for {und}.")
        expiry = expiry if expiry in exps else exps[0]
        raw = self._raw_chain(und, expiry)
        fresh = self._follow(raw)
        if fresh and self.provider.source == "sample":
            time.sleep(0.6)                                   # let the sample feed publish a first price for the new strikes
        out = analyse(raw, self.hub, window)
        age = time.time() - raw.fetched
        out.update({"underlying": und, "expiry": raw.expiry or expiry, "expiries": exps, "lot": raw.lot,
                    "spot_key": raw.spot_key, "source": self.provider.source, "snapshot_age_s": round(age, 1),
                    "stale": age > max(self.ttl * 3, 60)})
        self._record(und, raw.expiry or expiry, out)
        return out

    def _ns(self, und: str) -> str:
        """Invented (sample) chains are saved under their own name, never next to real ones."""
        return "SAMPLE:" + und if self.provider is not None and self.provider.source == "sample" else und

    # ---- intraday series (recorded while the terminal runs and somebody is looking at this chain)
    def _record(self, und: str, expiry: str, out: dict) -> None:
        if not self.store or self.provider is None:
            return
        now = time.time()
        k = (und, expiry)
        if now - self._last_series.get(k, 0) < 60:
            return
        s = out["summary"]
        if s["pcr"] is None or not out["spot"]:
            return
        self._last_series[k] = now
        try:
            self.store.add_oi_point(now_ist().date().isoformat(), self._ns(und), expiry, now, out["spot"],
                                    s["pcr"], s["ce_oi"], s["pe_oi"], s["max_pain"],
                                    s.get("straddle"), s.get("atm_ce"), s.get("atm_pe"))
        except Exception:
            log.exception("could not record OI point")

    def series(self, und: str, expiry: str) -> list[dict]:
        return self.store.oi_series(now_ist().date().isoformat(), self._ns(und.upper()), expiry) if self.store else []
