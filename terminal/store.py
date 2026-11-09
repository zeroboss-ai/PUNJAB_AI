"""Small SQLite store: watchlists and simple settings. One file per user (data/terminal.db)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlists (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, pos INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wl_items (wl_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    key TEXT NOT NULL, meta TEXT NOT NULL, pos INTEGER NOT NULL, PRIMARY KEY (wl_id, key));
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS oi_series (day TEXT NOT NULL, und TEXT NOT NULL, expiry TEXT NOT NULL, ts REAL NOT NULL,
    spot REAL, pcr REAL, ce_oi REAL, pe_oi REAL, max_pain REAL, straddle REAL, atm_ce REAL, atm_pe REAL);
CREATE INDEX IF NOT EXISTS oi_series_k ON oi_series (day, und, expiry, ts);
CREATE TABLE IF NOT EXISTS strategies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, spec TEXT NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS order_meta (order_id TEXT NOT NULL, mode TEXT NOT NULL, day TEXT NOT NULL, key TEXT NOT NULL,
    symbol TEXT NOT NULL, label TEXT NOT NULL, lot INTEGER NOT NULL, tag TEXT NOT NULL, PRIMARY KEY (order_id, mode, day));
CREATE TABLE IF NOT EXISTS fills (uid TEXT NOT NULL, mode TEXT NOT NULL, day TEXT NOT NULL, ts REAL NOT NULL, order_id TEXT NOT NULL,
    key TEXT NOT NULL, symbol TEXT NOT NULL, label TEXT NOT NULL, side TEXT NOT NULL, qty INTEGER NOT NULL, price REAL NOT NULL,
    product TEXT NOT NULL, lot INTEGER NOT NULL, tag TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY (uid, mode));
CREATE INDEX IF NOT EXISTS fills_k ON fills (mode, key, product, ts);
CREATE TABLE IF NOT EXISTS fii_dii (mode TEXT NOT NULL, day TEXT NOT NULL, fii_buy REAL NOT NULL, fii_sell REAL NOT NULL, fii_net REAL NOT NULL,
    dii_buy REAL NOT NULL, dii_sell REAL NOT NULL, dii_net REAL NOT NULL, fetched REAL NOT NULL, PRIMARY KEY (mode, day));
CREATE TABLE IF NOT EXISTS chart_candles (key TEXT NOT NULL, m INTEGER NOT NULL, o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL,
  v INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (key, m));
CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, body TEXT NOT NULL, tags TEXT NOT NULL, updated REAL NOT NULL);
"""

MAX_LISTS, MAX_ITEMS = 20, 200


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        for col in ("straddle REAL", "atm_ce REAL", "atm_pe REAL"):
            try:
                self._db.execute(f"ALTER TABLE oi_series ADD COLUMN {col}")
            except Exception:
                pass
        self._lock = threading.Lock()

    # ---- watchlists
    def watchlists(self) -> list[dict]:
        with self._lock:
            lists = self._db.execute("SELECT id, name FROM watchlists ORDER BY pos, id").fetchall()
            out = []
            for w in lists:
                items = self._db.execute("SELECT key, meta FROM wl_items WHERE wl_id=? ORDER BY pos", (w["id"],)).fetchall()
                out.append({"id": w["id"], "name": w["name"],
                            "items": [{"key": i["key"], **json.loads(i["meta"])} for i in items]})
            return out

    def create_watchlist(self, name: str) -> int:
        name = (name or "").strip()[:40] or "Watchlist"
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0]
            if n >= MAX_LISTS:
                raise ValueError(f"At most {MAX_LISTS} watchlists")
            cur = self._db.execute("INSERT INTO watchlists (name, pos) VALUES (?, ?)", (name, n))
            self._db.commit()
            return int(cur.lastrowid)

    def rename_watchlist(self, wl_id: int, name: str) -> None:
        with self._lock:
            self._db.execute("UPDATE watchlists SET name=? WHERE id=?", ((name or "").strip()[:40] or "Watchlist", wl_id))
            self._db.commit()

    def delete_watchlist(self, wl_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM watchlists WHERE id=?", (wl_id,))
            self._db.commit()

    def add_item(self, wl_id: int, key: str, meta: dict) -> None:
        with self._lock:
            if not self._db.execute("SELECT 1 FROM watchlists WHERE id=?", (wl_id,)).fetchone():
                raise ValueError("No such watchlist")
            n = self._db.execute("SELECT COUNT(*) FROM wl_items WHERE wl_id=?", (wl_id,)).fetchone()[0]
            if n >= MAX_ITEMS:
                raise ValueError(f"At most {MAX_ITEMS} instruments per watchlist")
            self._db.execute("INSERT OR IGNORE INTO wl_items (wl_id, key, meta, pos) VALUES (?,?,?,?)",
                             (wl_id, key, json.dumps(meta), n))
            self._db.commit()

    def remove_item(self, wl_id: int, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM wl_items WHERE wl_id=? AND key=?", (wl_id, key))
            self._db.commit()

    def reorder_items(self, wl_id: int, keys: list[str]) -> None:
        with self._lock:
            for pos, k in enumerate(keys):
                self._db.execute("UPDATE wl_items SET pos=? WHERE wl_id=? AND key=?", (pos, wl_id, k))
            self._db.commit()

    def all_watch_keys(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT DISTINCT key FROM wl_items")]

    # ---- settings
    def get(self, k: str, default=None):
        with self._lock:
            r = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, k: str, v) -> None:
        with self._lock:
            self._db.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))
            self._db.commit()

    # ---- chart candles (1-minute, built from live prices)
    def put_candles(self, rows: list[tuple]) -> None:
        """rows: (key, epoch minute, o, h, l, c, v). A minute that already exists keeps its open, widens high/low, takes the new close."""
        with self._lock:
            self._db.executemany(
                "INSERT INTO chart_candles (key, m, o, h, l, c, v) VALUES (?,?,?,?,?,?,?) ON CONFLICT(key, m) DO UPDATE SET "
                "h=MAX(h, excluded.h), l=MIN(l, excluded.l), c=excluded.c, v=MAX(v, excluded.v)", rows)
            self._db.commit()

    def candles(self, key: str, since_min: int = 0) -> list[tuple]:
        with self._lock:
            return [tuple(r) for r in self._db.execute(
                "SELECT m, o, h, l, c, v FROM chart_candles WHERE key=? AND m>=? ORDER BY m", (key, since_min))]

    def prune_candles(self, before_min: float) -> None:
        with self._lock:
            self._db.execute("DELETE FROM chart_candles WHERE m<?", (int(before_min),))
            self._db.commit()

    def retag_sample_history(self) -> None:
        """Older versions saved invented sample candles and OI points next to real ones. If this database has never connected
        to a broker, everything in them is invented: move it into the sample namespace (nothing is deleted)."""
        if self.get("history.ns_v2"):
            return
        if not self.get("accounts.last"):
            with self._lock:
                self._db.execute("UPDATE OR IGNORE chart_candles SET key='sample:'||key WHERE key NOT LIKE 'sample:%'")
                self._db.execute("UPDATE oi_series SET und='SAMPLE:'||und WHERE und NOT LIKE 'SAMPLE:%'")
                self._db.commit()
        self.set("history.ns_v2", 1)

    def drop_derivative_items(self) -> int:
        """Remove futures / options / commodity rows from every watchlist (sample contract numbers mean nothing for real prices)."""
        n = 0
        for w in self.watchlists():
            for it in w["items"]:
                if str(it["key"]).split("|", 1)[0] in ("nse_fo", "bse_fo", "cde_fo", "mcx_fo"):
                    self.remove_item(w["id"], it["key"])
                    n += 1
        return n

    # ---- intraday open-interest series (recorded while the terminal runs)
    def add_oi_point(self, day: str, und: str, expiry: str, ts: float, spot, pcr, ce_oi, pe_oi, max_pain, straddle=None, atm_ce=None, atm_pe=None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO oi_series (day, und, expiry, ts, spot, pcr, ce_oi, pe_oi, max_pain, straddle, atm_ce, atm_pe) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (day, und, expiry, ts, spot, pcr, ce_oi, pe_oi, max_pain, straddle, atm_ce, atm_pe))
            self._db.execute("DELETE FROM oi_series WHERE day < date(?, '-7 day')", (day,))
            self._db.commit()

    def oi_series(self, day: str, und: str, expiry: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT ts, spot, pcr, ce_oi, pe_oi, max_pain, straddle, atm_ce, atm_pe FROM oi_series WHERE day=? AND und=? AND expiry=? ORDER BY ts",
                                    (day, und, expiry)).fetchall()
        return [dict(r) for r in rows]

    # ---- strategies (rule builder)
    def list_strategies(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, name, spec, created, updated FROM strategies ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"], "spec": json.loads(r["spec"]), "created": r["created"], "updated": r["updated"]} for r in rows]

    def get_strategy(self, sid: int) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT id, name, spec, created, updated FROM strategies WHERE id=?", (sid,)).fetchone()
        return None if r is None else {"id": r["id"], "name": r["name"], "spec": json.loads(r["spec"]), "created": r["created"], "updated": r["updated"]}

    def save_strategy(self, sid: int | None, name: str, spec: dict) -> int:
        now = time.time()
        with self._lock:
            if sid is None:
                if self._db.execute("SELECT COUNT(*) FROM strategies").fetchone()[0] >= 50:
                    raise ValueError("At most 50 saved strategies")
                cur = self._db.execute("INSERT INTO strategies (name, spec, created, updated) VALUES (?,?,?,?)", (name, json.dumps(spec), now, now))
                self._db.commit()
                return int(cur.lastrowid)
            cur = self._db.execute("UPDATE strategies SET name=?, spec=?, updated=? WHERE id=?", (name, json.dumps(spec), now, sid))
            self._db.commit()
            if cur.rowcount == 0:
                raise ValueError("No such strategy")
            return sid

    def delete_strategy(self, sid: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM strategies WHERE id=?", (sid,))
            self._db.commit()

    # ---- order tags + fills + notes (trade journal)
    def add_order_meta(self, order_id: str, mode: str, day: str, key: str, symbol: str, label: str, lot: int, tag: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO order_meta VALUES (?,?,?,?,?,?,?,?)", (order_id, mode, day, key, symbol, label, lot, tag))
            self._db.commit()

    def order_meta(self, mode: str, day: str) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM order_meta WHERE mode=? AND day=?", (mode, day)).fetchall()
        return {r["order_id"]: dict(r) for r in rows}

    def add_fills(self, rows: list[dict]) -> int:
        n = 0
        with self._lock:
            for r in rows:
                cur = self._db.execute(
                    "INSERT OR IGNORE INTO fills (uid, mode, day, ts, order_id, key, symbol, label, side, qty, price, product, lot, tag, name) "
                    "VALUES (:uid,:mode,:day,:ts,:order_id,:key,:symbol,:label,:side,:qty,:price,:product,:lot,:tag,:name)", r)
                n += cur.rowcount
            self._db.commit()
        return n

    def fills(self, mode: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM fills WHERE mode=? ORDER BY ts, rowid", (mode,)).fetchall()
        return [dict(r) for r in rows]

    def delete_fills(self, mode: str, order_prefix: str | None = None) -> None:
        with self._lock:
            if order_prefix:
                self._db.execute("DELETE FROM fills WHERE mode=? AND order_id LIKE ?", (mode, order_prefix + "%"))
            else:
                self._db.execute("DELETE FROM fills WHERE mode=?", (mode,))
            self._db.commit()

    def notes(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, body, tags FROM notes").fetchall()
        return {r["id"]: {"body": r["body"], "tags": json.loads(r["tags"])} for r in rows}

    def set_note(self, nid: str, body: str, tags: list[str]) -> None:
        body, tags = body.strip()[:2000], [t.strip()[:24] for t in tags if t.strip()][:8]
        with self._lock:
            if not body and not tags:
                self._db.execute("DELETE FROM notes WHERE id=?", (nid,))
            else:
                self._db.execute("INSERT INTO notes (id, body, tags, updated) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                                 "body=excluded.body, tags=excluded.tags, updated=excluded.updated", (nid, body, json.dumps(tags), time.time()))
            self._db.commit()

    # ---- FII / DII daily flows (history accumulates from the days the terminal fetched; per mode so sample data never mixes)
    def add_fii_dii(self, mode: str, rows: list[dict], ts: float) -> None:
        with self._lock:
            for r in rows:
                f, d = r["fii"], r["dii"]
                self._db.execute("INSERT OR REPLACE INTO fii_dii VALUES (?,?,?,?,?,?,?,?,?)",
                                 (mode, r["day"], f["buy"], f["sell"], f["net"], d["buy"], d["sell"], d["net"], ts))
            self._db.commit()

    def fii_dii(self, mode: str, limit: int = 60) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM fii_dii WHERE mode=? ORDER BY day DESC LIMIT ?", (mode, limit)).fetchall()
        return [{"day": r["day"], "fii": {"buy": r["fii_buy"], "sell": r["fii_sell"], "net": r["fii_net"]},
                 "dii": {"buy": r["dii_buy"], "sell": r["dii_sell"], "net": r["dii_net"]}} for r in rows]
