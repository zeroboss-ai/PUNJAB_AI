#!/usr/bin/env python3
"""
READ-ONLY check of NSE's free data against what terminal/nse.py expects. No login, no keys, no orders.

    python tools/check_nse.py

For each of FII/DII, the delivery report and the F&O bhavcopy it says whether the file could be fetched and whether
every column and number passed the terminal's checks, and shows the first lines so you can compare by eye.
Run it on the computer where you run the terminal (NSE treats servers and home connections differently), ideally in the
evening on a trading day. If something shows "FAILED", send me the output (it contains no secrets).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from terminal import nse  # noqa: E402

svc_dir = Path(tempfile.mkdtemp(prefix="nse-check-"))
client = nse.NseClient(svc_dir)
from terminal.store import Store  # noqa: E402

probe = nse.NseService(client, Store(":memory:"), "paper")


def section(t):
    print(f"\n=== {t} ===")


section("FII / DII  (https://www.nseindia.com/api/fiidiiTradeReact)")
try:
    raw = client.fii_dii()
    print("raw (first 2 entries):", str(raw[:2] if isinstance(raw, list) else raw)[:600])
    rows = nse.parse_fii_dii(raw)
    print(f"OK: {len(rows)} day(s) parsed and consistent; latest {rows[0]['day']}: FII net {rows[0]['fii']['net']}, DII net {rows[0]['dii']['net']}")
except Exception as e:
    print("FAILED:", e)

for name, fetch, parse in (("Delivery report (sec_bhavdata_full)", client.delivery_csv, nse.parse_delivery),
                           ("F&O bhavcopy (UDiFF)", client.fo_csv, nse.parse_fo)):
    section(name)
    try:
        d, text = probe._latest(fetch, None)
        print(f"file for {d} fetched, {len(text):,} characters. First lines:")
        print("\n".join(text.splitlines()[:3])[:700])
        out = parse(text, d) if parse is nse.parse_delivery else parse(text)
        print(f"OK: {len(out):,} rows parsed")
    except Exception as e:
        print("FAILED:", e)
print("\nDone. Nothing was written outside a temporary folder.")
