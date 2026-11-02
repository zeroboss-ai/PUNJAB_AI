"""Trade journal: FIFO round trips (long, short, partial, flip), statistics, tags, notes, idempotent sync."""
import pytest

from terminal.clock import now_ist
from terminal.journal import Journal, equity, stats
from terminal.store import Store
from terminal.trading.models import OrderTicket


def fill(uid, oid, side, qty, price, ts, key="k1", tag="manual", day="2026-10-26", lot=75, product="MIS"):
    return {"uid": uid, "mode": "sample", "day": day, "ts": ts, "order_id": oid, "key": key, "symbol": "S", "label": "NIFTY 24250 PE",
            "side": side, "qty": qty, "price": price, "product": product, "lot": lot, "tag": tag, "name": "NIFTY"}


def journal(*fills):
    st = Store(":memory:")
    st.add_fills(list(fills))
    return Journal(st, None, "sample"), st


def trips_of(j):
    return j.trips()


def test_short_round_trip_pnl_and_direction():
    j, _ = journal(fill("a", "o1", "S", 75, 100.0, 1000), fill("b", "o2", "B", 75, 80.0, 2000))
    t, held = trips_of(j)
    assert len(t) == 1 and not held
    assert t[0]["dir"] == "Short" and t[0]["pnl"] == pytest.approx(20 * 75) and t[0]["lots"] == 1 and t[0]["hold_s"] == 1000


def test_long_round_trip_loss():
    j, _ = journal(fill("a", "o1", "B", 150, 50.0, 1000), fill("b", "o2", "S", 150, 40.0, 2000))
    t, _ = trips_of(j)
    assert t[0]["dir"] == "Long" and t[0]["pnl"] == pytest.approx(-1500)


def test_fifo_partial_exits_and_averaging():
    j, _ = journal(fill("a", "o1", "B", 75, 100.0, 1), fill("b", "o2", "B", 75, 110.0, 2),
                   fill("c", "o3", "S", 100, 120.0, 3), fill("d", "o4", "S", 50, 90.0, 4))
    t, held = trips_of(j)
    by = {(x["entry"], x["qty"]): x for x in t}
    # 100 sold at 120: 75 against the 100-lot, 25 against the 110-lot; then 50 at 90 against the rest of the 110-lot
    assert sum(x["qty"] for x in t) == 150 and not held
    assert sum(x["pnl"] for x in t) == pytest.approx(75 * 20 + 25 * 10 + 50 * -20)


def test_leftover_is_reported_as_open_and_not_in_stats():
    j, _ = journal(fill("a", "o1", "S", 150, 100.0, 1), fill("b", "o2", "B", 75, 90.0, 2))
    t, held = trips_of(j)
    assert len(t) == 1 and held[0]["qty"] == 75 and held[0]["side"] == "Short"


def test_flip_through_zero_opens_the_opposite_side():
    j, _ = journal(fill("a", "o1", "B", 75, 100.0, 1), fill("b", "o2", "S", 150, 110.0, 2), fill("c", "o3", "B", 75, 100.0, 3))
    t, held = trips_of(j)
    assert [x["dir"] for x in t] == ["Long", "Short"] and t[0]["pnl"] == pytest.approx(750) and t[1]["pnl"] == pytest.approx(750)
    assert not held


def test_positions_carry_across_days_and_products_do_not_mix():
    j, _ = journal(fill("a", "o1", "S", 75, 100.0, 1, day="2026-10-26", product="NRML"),
                   fill("b", "o2", "B", 75, 70.0, 2, day="2026-10-27", product="MIS"),
                   fill("c", "o3", "B", 75, 90.0, 3, day="2026-10-27", product="NRML"))
    t, held = trips_of(j)
    assert len(t) == 1 and t[0]["product"] == "NRML" and t[0]["day"] == "2026-10-27" and t[0]["pnl"] == pytest.approx(750)
    assert held and held[0]["product"] == "MIS"


def test_stats_numbers():
    ts = [{"pnl": p, "day": "d", "hold_s": 60, "exit_ts": i} for i, p in enumerate([100, -50, -50, -50, 200, -25])]
    s = stats(ts)
    assert s["n"] == 6 and s["wins"] == 2 and s["losses"] == 4 and s["win_rate"] == pytest.approx(33.3)
    assert s["net"] == 125 and s["avg_win"] == 150 and s["avg_loss"] == pytest.approx(-43.75)
    assert s["profit_factor"] == pytest.approx(300 / 175, abs=0.01) and s["expectancy"] == pytest.approx(125 / 6, abs=0.01)
    assert s["max_drawdown"] == 150 and s["max_losing_streak"] == 3 and s["best"] == 200 and s["worst"] == -50
    assert stats([])["n"] == 0 and stats([])["win_rate"] is None
    assert [e["cum"] for e in equity(ts)] == [100, 50, 0, -50, 150, 125]


def test_report_filters_by_tag_and_day_and_attaches_notes():
    j, st = journal(fill("a", "o1", "S", 75, 100, 1, tag="ORB", day="2026-10-26"), fill("b", "o2", "B", 75, 90, 2, tag="ORB", day="2026-10-26"),
                    fill("c", "o3", "B", 75, 50, 3, tag="manual", day="2026-10-27"), fill("d", "o4", "S", 75, 60, 4, tag="manual", day="2026-10-27"))
    r = j.report()
    assert r["stats"]["n"] == 2 and r["tags"] == ["ORB", "manual"]
    assert j.report(tag="ORB")["stats"]["n"] == 1
    assert j.report(frm="2026-10-27")["stats"]["net"] == pytest.approx(750)
    tid = r["trips"][0]["id"]
    st.set_note(f"trip:{tid}", "late entry", ["fomo"])
    assert j.report()["trips"][0]["note"] == "late entry"
    st.set_note(f"trip:{tid}", "", [])
    assert j.report()["trips"][0]["note"] == ""


def test_demo_data_is_removable_and_sample_only():
    j, st = journal()
    assert j.seed_demo() > 20 and j.report()["demo"]
    st.add_fills([fill("real", "REAL1", "B", 75, 10, 1)])
    j.clear_demo()
    assert not j.report()["demo"] and len(st.fills("sample")) == 1
    with pytest.raises(ValueError):
        Journal(st, None, "live").seed_demo()


def test_sync_from_the_broker_is_idempotent_and_carries_tags(tmp_path):
    from test_rules import World
    w = World(tmp_path)
    j = Journal(w.store, w.trading, "sample")
    w.trading.on_order = j.note_order
    from terminal.market.models import Quote
    w.hub.update(Quote(w.pe, 100.0, prev_close=100.0))
    w.trading.place(OrderTicket(key=w.pe, side="S", lots=1, product="MIS", order_type="MKT", tag="manual"))
    w.hub.update(Quote(w.pe, 90.0, prev_close=100.0))
    w.trading.place(OrderTicket(key=w.pe, side="B", lots=1, product="MIS", order_type="MKT", tag="my_algo"))
    assert j.sync() == 2 and j.sync() == 0
    r = j.report()
    t = r["trips"][0]
    assert t["pnl"] == pytest.approx(10 * w.lot) and t["tag"] == "manual" and t["dir"] == "Short"
    assert t["label"].startswith("NIFTY") and r["stats"]["n"] == 1
