"""
The ORB template built from rule blocks, replayed on the SAME synthetic days as the built-in algo.
Not bit-identical (different order handling), but the decisions must agree: whether to trade, which option,
the stop-loss level, how the trade ended, and roughly where it entered and exited.
"""
import pytest

from conftest import DAY, run_day
from terminal.market.models import Quote
from terminal.models import INDEX
from terminal.sim import build_day
from test_rules import IDX, World, tmpl

SHIFT = 750          # the sample option chain is centred on 24250; the synthetic day on 25000


def replay(w, scenario):
    spec = tmpl("orb")
    spec["limits"]["max_entries"] = 1                      # the built-in algo trades once a day; the template leaves the limit to you
    w.clock.set("09:00:00")
    r = w.create(spec)
    cur = None
    for t in build_day(w.clock.day, scenario):
        if t.ts != cur:
            if cur is not None:
                r.step()
            cur = t.ts
            w.clock.t = t.ts
        stamp = t.ts.timestamp()
        if t.symbol == INDEX:
            w.hub.update(Quote(IDX, t.price - SHIFT, ts=stamp))
        elif t.symbol == "NIFTY25000CE":
            w.hub.update(Quote(w.ce, t.price, ts=stamp))
        elif t.symbol == "NIFTY25000PE":
            w.hub.update(Quote(w.pe, t.price, ts=stamp))
    r.step()
    return r


@pytest.mark.parametrize("scenario", ["put_sell", "put_sl", "call_sell", "call_sl", "inside", "unconfirmed"])
def test_rule_orb_agrees_with_the_builtin_algo(scenario, tmp_path):
    built = run_day(scenario, tmp_path).strategy.trades
    w = World(tmp_path / "w")
    r = replay(w, scenario)
    assert len(r.positions) == len(built), (scenario, [l["msg"] for l in r._logs][-6:])
    if not built:
        return
    b, p = built[0], r.positions[0]
    assert p.leg == b["side"] and p.side == "S"
    assert p.sl == pytest.approx(b["sl"])
    assert p.status == "closed"
    assert (p.exit_reason == "Exit time") == (b["reason"] == "TIME"), (p.exit_reason, b["reason"])
    assert abs(p.entry_price - b["entry"]) <= 2.0
    assert abs(p.exit_price - b["exit"]) <= 6.0
    assert (p.pnl(None) > 0) == (b["pnl"] > 0)
