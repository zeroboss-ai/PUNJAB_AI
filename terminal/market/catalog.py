"""
Instrument catalog: search by name / "NIFTY 25000 CE" / "RELIANCE", and resolve a key back to an Instrument.
The server always resolves the trading symbol and lot size from HERE - never from what the browser sends.
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import date
from typing import Any

from ..brokers.kotak import _f, parse_expiry
from ..clock import now_ist
from . import commodity as cm
from .models import Instrument

log = logging.getLogger("catalog")

CASH = ("nse_cm", "bse_cm")
DERIV = ("nse_fo", "bse_fo", "mcx_fo")
MCX = cm.SEGMENT


class Catalog:
    """Base class: registered instruments (indices etc.) + whatever a subclass can load."""

    def __init__(self) -> None:
        self._reg: dict[str, Instrument] = {}
        self._lock = threading.Lock()
        self.status = "ready"

    def register(self, *insts: Instrument) -> None:
        with self._lock:
            for i in insts:
                self._reg[i.key] = i

    def get(self, key: str) -> Instrument | None:
        with self._lock:
            hit = self._reg.get(key)
        if hit:
            return hit
        found = self._lookup(key)
        if found:
            self.register(found)                     # cache it: order/position pages look the same keys up repeatedly
        return found

    def _lookup(self, key: str) -> Instrument | None:            # subclasses
        return None

    def mcx_future(self, name: str, on_or_after: date | None = None) -> Instrument | None:   # subclasses with commodities
        return None

    def mcx_loaded(self) -> bool:
        return False

    def _search_loaded(self, toks: list[str], limit: int) -> list[Instrument]:   # subclasses
        return []

    def search(self, q: str, limit: int = 25) -> list[Instrument]:
        toks = [t for t in re.split(r"[\s,]+", q.strip().upper()) if t]
        if not toks:
            return []
        out: list[Instrument] = []
        with self._lock:
            regs = list(self._reg.values())
        word = toks[0]
        for i in regs:                                            # indices and other registered items
            hay = f"{i.name} {i.symbol}".upper()
            if all(t in hay for t in toks) and i.kind == "INDEX":
                out.append(i)
        out.extend(self._search_loaded(toks, limit))
        seen, uniq = set(), []
        for i in out:
            if i.key not in seen:
                seen.add(i.key)
                uniq.append(i)
        uniq.sort(key=lambda i: (0 if i.name == word else 1, 0 if i.kind in ("INDEX", "EQ") else 1,
                                 i.expiry or date.max, i.strike or 0, i.kind, i.name))
        return uniq[:limit]


# ----------------------------------------------------------------------------------- Kotak
def _kind_from_row(opt: str) -> str:
    o = str(opt or "").strip().upper()
    return o if o in ("CE", "PE") else "FUT"          # NSE/BSE futures carry "XX"; every MCX row that is not CE/PE is read as a future


class KotakCatalog(Catalog):
    """Reads Kotak's daily scrip-master CSVs (downloaded once per day per segment, then kept in memory)."""

    def __init__(self, instruments) -> None:           # KotakInstruments
        super().__init__()
        self.ins = instruments
        self._frames: dict[str, Any] = {}
        self._loading: set[str] = set()

    # background warm-up so the first search is instant
    def warm(self, segments=CASH + DERIV) -> None:
        def run():
            self.status = "loading"
            for seg in segments:
                try:
                    self._df(seg)
                except Exception as e:
                    log.warning("could not load %s scrip master: %r", seg, e)
            self.status = "ready"
        threading.Thread(target=run, name="catalog-warm", daemon=True).start()

    def _df(self, seg: str):
        df = self._frames.get(seg)
        if df is None:
            df = self.ins._load(seg)
            self._frames[seg] = df
        return df

    def _col(self, df, *names):
        for n in names:
            if n in df.columns:
                return n
        return None

    def _row_to_inst(self, seg: str, r, c: dict) -> Instrument:
        token = str(int(_f(r[c["tok"]])))
        tick = max(_f(r.get(c["tick"])) / 100.0, 0.01) if c["tick"] else 0.05
        lot = max(int(_f(r.get(c["lot"]), 1)), 1) if c["lot"] else 1
        if seg in CASH:
            return Instrument(f"{seg}|{token}", seg, token, str(r[c["trd"]]).strip(), str(r[c["name"]]).strip(), "EQ",
                              lot=1, tick=tick, desc=str(r.get(c["desc"]) or "") if c["desc"] else "")
        kind = _kind_from_row(r[c["opt"]])
        exp = parse_expiry(r[c["exp"]], now_ist().date(), seg)
        strike = _f(r[c["strike"]]) / 100.0 if kind in ("CE", "PE") else None
        name = str(r[c["name"]]).strip()
        desc = cm.label_of(name) if seg == MCX else ""
        return Instrument(f"{seg}|{token}", seg, token, str(r[c["trd"]]).strip(), name, kind,
                          lot=lot, tick=tick, expiry=exp, strike=strike, desc=desc)

    def _cols(self, df) -> dict:
        return {"tok": self._col(df, "psymbol"), "trd": self._col(df, "ptrdsymbol"), "name": self._col(df, "psymbolname"),
                "opt": self._col(df, "poptiontype"), "exp": self._col(df, "pexpirydate", "lexpirydate"),
                "strike": self._col(df, "dstrikeprice"), "lot": self._col(df, "llotsize", "ilotsize"),
                "tick": self._col(df, "dticksize"), "desc": self._col(df, "pdesc"), "grp": self._col(df, "pgroup")}

    def fo_names(self) -> dict[str, int]:
        """Every NSE underlying that has listed options (index or stock) -> its lot size. Read once from the F&O scrip master."""
        hit = getattr(self, "_fo_cache", None)
        if hit is not None:
            return hit
        df = self._df("nse_fo")
        c = self._cols(df)
        opt = df[c["opt"]].astype(str).str.upper()
        sub = df[opt.isin(["CE", "PE"])]
        names = sub[c["name"]].astype(str).str.strip().str.upper()
        lots = sub[c["lot"]] if c["lot"] else None
        out: dict[str, int] = {}
        for i, n in enumerate(names):
            if n and n not in out:
                out[n] = max(int(_f(lots.iloc[i], 1)), 1) if lots is not None else 1
        self._fo_cache = out
        return out

    def fo_underlyings(self, q: str, limit: int = 12) -> list[dict]:
        """F&O underlyings whose name starts with q, then those whose company name contains it. [{name, label, lot}]"""
        q = q.strip().upper()
        names = self.fo_names()
        desc: dict[str, str] = {}
        cm = self._frames.get("nse_cm")                              # company names, only if the cash list is already loaded
        if cm is not None:
            c = self._cols(cm)
            if c["name"] and c["desc"]:
                grp = cm[c["grp"]].astype(str) if c["grp"] else None
                sub = cm if grp is None else cm[grp.isin(["EQ", "BE"])]
                nm = sub[c["name"]].astype(str).str.strip().str.upper()
                for n, d in zip(nm, sub[c["desc"]].astype(str)):
                    if n in names and n not in desc:
                        desc[n] = d.strip()
        first = sorted(n for n in names if n.startswith(q))
        second = sorted(n for n in names if n not in first and q in desc.get(n, "").upper()) if len(q) >= 3 else []
        return [{"name": n, "label": desc.get(n, n), "lot": names[n]} for n in (first + second)[:limit]]

    def _lookup(self, key: str) -> Instrument | None:
        if "|" not in key:
            return None
        seg, token = key.split("|", 1)
        if seg not in CASH + DERIV or not token.isdigit():
            return None
        try:
            df = self._df(seg)
        except Exception:
            return None
        c = self._cols(df)
        hit = df[df[c["tok"]] == int(token)]
        return None if hit.empty else self._row_to_inst(seg, hit.iloc[0], c)

    def _search_loaded(self, toks: list[str], limit: int) -> list[Instrument]:
        want_opt = next((t for t in toks if t in ("CE", "PE", "FUT")), None)
        nums = [t for t in toks if re.fullmatch(r"\d+(\.\d+)?", t)]
        words = [t for t in toks if t not in ("CE", "PE", "FUT") and t not in nums]
        if not words:
            return []
        name, extra = words[0], words[1:]
        out: list[Instrument] = []
        out.extend(self._search_mcx(words, want_opt, nums))
        segs = tuple(x for x in (DERIV if (want_opt or nums) else CASH + DERIV) if x != MCX)
        for seg in segs:
            if seg not in self._frames and seg in DERIV and not (want_opt or nums) and len(name) < 3:
                continue                                        # don't download huge F&O files for a 1-2 letter search
            try:
                df = self._df(seg)
            except Exception as e:
                log.warning("search: %s unavailable: %r", seg, e)
                continue
            c = self._cols(df)
            names = df[c["name"]].astype(str).str.upper()
            m = names.str.startswith(name)
            if seg in CASH and c["desc"]:
                m |= df[c["desc"]].astype(str).str.upper().str.contains(re.escape(name), na=False)
            if seg in DERIV:
                opt = df[c["opt"]].astype(str).str.upper()
                m &= opt.isin(["CE", "PE", "XX"])
                if want_opt:
                    m &= opt.eq("XX" if want_opt == "FUT" else want_opt)
                if nums:
                    strikes = df[c["strike"]].astype(float) / 100.0
                    sm = False
                    for n in nums:
                        sm = sm | strikes.eq(float(n))
                    m &= sm
            for t in extra:
                m &= df[c["trd"]].astype(str).str.upper().str.contains(re.escape(t), na=False)
            sub = df[m]
            if seg in DERIV and not sub.empty:
                insts = [self._row_to_inst(seg, r, c) for _, r in sub.head(4000).iterrows()]
                today = now_ist().date()
                insts = [i for i in insts if i.expiry is None or i.expiry >= today]
                exps = sorted({i.expiry for i in insts if i.expiry})[:3]        # nearest three expiries only
                out.extend(i for i in insts if i.expiry in exps or i.expiry is None)
            else:
                if c["grp"] and seg == "nse_cm":
                    sub = sub[sub[c["grp"]].astype(str).isin(["EQ", "BE", "SM", "ST", "BZ", "IV"])]
                out.extend(self._row_to_inst(seg, r, c) for _, r in sub.head(60).iterrows())
        return out[: limit * 4]


    # ------------------------------------------------------------------------ commodities (MCX)
    def _mcx_df(self):
        try:
            return self._df(MCX)
        except Exception as e:
            log.warning("MCX scrip master unavailable: %r", e)
            return None

    def _mcx_names_in(self, df, c) -> set[str]:
        return set(df[c["name"]].astype(str).str.strip().str.upper())

    def _search_mcx(self, words: list[str], want_opt: str | None, nums: list[str]) -> list[Instrument]:
        """Commodity search: 'gold', 'gold mini', 'crude', 'naturalgas', 'silver 150000 ce'. Futures only unless CE/PE or a strike is typed."""
        if not words:
            return []
        hit = cm.resolve(words)
        if not hit and (MCX not in self._frames and len(words[0]) < 3):
            return []
        df = self._mcx_df() if (hit or MCX in self._frames or len(words[0]) >= 3) else None
        if df is None or df.empty:
            return []
        c = self._cols(df)
        names = df[c["name"]].astype(str).str.strip().str.upper()
        if hit:
            m = names.isin(hit)
        else:                                                     # a name the table does not know: still searchable
            m = names.str.startswith(words[0])
            for t in words[1:]:
                m &= df[c["trd"]].astype(str).str.upper().str.contains(re.escape(t), na=False)
        opt = df[c["opt"]].astype(str).str.strip().str.upper()
        is_opt = opt.isin(["CE", "PE"])
        if want_opt in ("CE", "PE"):
            m &= opt.eq(want_opt)
        elif nums:
            m &= is_opt
        elif want_opt == "FUT":
            m &= ~is_opt
        else:
            m &= ~is_opt                                          # plain name: the futures; options come with CE / PE / a strike
        if nums:
            strikes = df[c["strike"]].astype(float) / 100.0
            sm = False
            for n in nums:
                sm = sm | strikes.eq(float(n))
            m &= sm
        sub = df[m]
        if sub.empty:
            return []
        today = now_ist().date()
        insts = [self._row_to_inst(MCX, r, c) for _, r in sub.head(4000).iterrows()]
        insts = [i for i in insts if i.expiry is None or i.expiry >= today]
        if want_opt in ("CE", "PE") or nums:
            exps = sorted({i.expiry for i in insts if i.expiry})[:3]
            insts = [i for i in insts if i.expiry in exps or i.expiry is None]
        return insts

    def mcx_names(self) -> dict[str, dict]:
        """Every MCX underlying that has OPTIONS in Kotak's file -> {lot, label, group, size}. Read once."""
        hit = getattr(self, "_mcx_cache", None)
        if hit is not None:
            return hit
        df = self._df(MCX)
        c = self._cols(df)
        opt = df[c["opt"]].astype(str).str.strip().str.upper()
        sub = df[opt.isin(["CE", "PE"])]
        out: dict[str, dict] = {}
        lots = sub[c["lot"]] if c["lot"] else None
        for i, n in enumerate(sub[c["name"]].astype(str).str.strip().str.upper()):
            if n and n not in out:
                out[n] = {"lot": max(int(_f(lots.iloc[i], 1)), 1) if lots is not None else 1, **cm.info(n)}
        self._mcx_cache = out
        return out

    def mcx_underlyings(self, q: str, limit: int = 12) -> list[dict]:
        q = q.strip().upper()
        names = self.mcx_names()
        hit = set(cm.resolve(q.split())) | {n for n in names if n.startswith(q)}
        return [{"name": n, "label": names[n]["label"], "lot": names[n]["lot"], "size": names[n]["size"]}
                for n in sorted(hit) if n in names][:limit]

    def mcx_future(self, name: str, on_or_after: date | None = None) -> Instrument | None:
        """The future an option on `name` is written on: the nearest future that expires on/after the option's expiry
        (ASSUMPTION - MCX options are options on futures; Kotak's file does not say which one). Without a date: the front month."""
        df = self._mcx_df()
        if df is None:
            return None
        c = self._cols(df)
        names = df[c["name"]].astype(str).str.strip().str.upper()
        opt = df[c["opt"]].astype(str).str.strip().str.upper()
        sub = df[names.eq(name.upper()) & ~opt.isin(["CE", "PE"])]
        today = now_ist().date()
        futs = [self._row_to_inst(MCX, r, c) for _, r in sub.head(60).iterrows()]
        futs = sorted((i for i in futs if i.expiry and i.expiry >= (on_or_after or today)), key=lambda i: i.expiry)
        return futs[0] if futs else None

    def mcx_loaded(self) -> bool:
        return MCX in self._frames


class LazyCatalog(Catalog):
    """A stable object the rest of the app can hold while the real catalog (sample or Kotak) is created later."""

    def __init__(self) -> None:
        super().__init__()
        self.inner: Catalog | None = None

    def set(self, inner: Catalog | None) -> None:
        self.inner = inner

    def _lookup(self, key: str) -> Instrument | None:
        return self.inner.get(key) if self.inner else None

    def search(self, q: str, limit: int = 25) -> list[Instrument]:
        return self.inner.search(q, limit) if self.inner else []

    def mcx_future(self, name: str, on_or_after: date | None = None) -> Instrument | None:
        f = getattr(self.inner, "mcx_future", None)
        return f(name, on_or_after) if f else None

    def mcx_loaded(self) -> bool:
        f = getattr(self.inner, "mcx_loaded", None)
        return bool(f and f())

    @property
    def loading(self) -> bool:
        return getattr(self.inner, "status", "ready") == "loading"
