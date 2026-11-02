"""
Kotak's SDK never raises: a timeout comes back as {"Error": ...} and a lost reply looks exactly like a
rejected order. These tests make the paper broker lose replies / hide statuses / hide positions and check
that the engine falls back to the broker's POSITIONS instead of guessing - no naked shorts, no double buys.
"""
from conftest import DAY, ROOT
from terminal.brokers.paper import PaperOrders
from terminal.brokers.replay import ReplayFeed, SyntheticInstruments
from terminal.config import load_config
from terminal.engine import Engine
from terminal.models import OrderUnknown
from terminal.sim import build_day
from terminal.strategy import State


class FlakyOrders(PaperOrders):
    def __init__(self):
        super().__init__()
        self.lose_reply: dict[str, int] = {}       # tag -> how many replies to lose AFTER the order executed
        self.drop_order: dict[str, int] = {}       # tag -> how many orders to raise on BEFORE executing
        self.hide_status = False
        self.hide_positions = False
        self.placed: list[tuple[str, str]] = []    # (tag, side)

    def place(self, req):
        if self.drop_order.get(req.tag, 0) > 0:
            self.drop_order[req.tag] -= 1
            raise OrderUnknown("timeout before the order left")
        oid = super().place(req)
        self.placed.append((req.tag, req.side))
        if self.lose_reply.get(req.tag, 0) > 0:
            self.lose_reply[req.tag] -= 1
            raise OrderUnknown("timeout after the order was sent")
        return oid

    def status(self, order_id):
        st = super().status(order_id)
        if self.hide_status:
            st.state, st.filled_qty, st.avg_price, st.reason = "UNKNOWN", 0, 0.0, "order report unreadable"
        return st

    def positions(self):
        return None if self.hide_positions else super().positions()


def net_total(o: PaperOrders) -> int:
    return sum(o._net.values())


def test_lost_entry_reply_is_recovered_from_positions(day, tmp_path):
    o = FlakyOrders()
    o.lose_reply["orb-entry"] = 1
    e = day("put_sell", tmp_path, orders=o)
    assert len(e.strategy.trades) == 1 and e.strategy.trades[0]["reason"] == "TIME"
    assert not e.manual_action_required
    assert net_total(o) == 0                                   # flat at the end
    assert [t for t in o.placed if t[0] == "orb-entry"] == [("orb-entry", "S")]     # never sold twice
    assert any(t[0] == "orb-sl" for t in o.placed)             # the protective stop was still placed
    assert any("treating the entry as filled" in ev["msg"] for ev in e.events)


def test_entry_that_never_left_halts_instead_of_guessing(day, tmp_path):
    o = FlakyOrders()
    o.drop_order["orb-entry"] = 1
    e = day("put_sell", tmp_path, orders=o)
    assert e.strategy.trades == [] and o.placed == []
    assert e.manual_action_required and e.strategy.state == State.HALTED
    assert any("may still be resting" in ev["msg"] for ev in e.events)


def test_unreadable_order_status_falls_back_to_positions_all_day(day, tmp_path):
    o = FlakyOrders()
    o.hide_status = True
    e = day("put_sell", tmp_path, orders=o)
    assert len(e.strategy.trades) == 1 and not e.manual_action_required
    assert net_total(o) == 0
    assert [t for t in o.placed if t[0] == "orb-exit"] == [("orb-exit", "B")]       # bought back exactly once


def test_unreadable_status_and_positions_halts_with_alert(day, tmp_path):
    o = FlakyOrders()
    o.hide_status = True
    o.hide_positions = True
    e = day("put_sell", tmp_path, orders=o)
    assert e.manual_action_required and e.strategy.state == State.HALTED
    assert any("NO stop-loss" in ev["msg"] for ev in e.events)
    assert not any(t[0] == "orb-sl" for t in o.placed)         # no blind follow-up orders


def test_lost_exit_reply_does_not_double_buy(day, tmp_path):
    o = FlakyOrders()
    o.lose_reply["orb-exit"] = 1
    e = day("put_sell", tmp_path, orders=o)
    assert len(e.strategy.trades) == 1 and not e.manual_action_required
    assert [t for t in o.placed if t[0] == "orb-exit"] == [("orb-exit", "B")]
    assert net_total(o) == 0


def test_lost_exit_reply_on_stoploss_exit(day, tmp_path):
    o = FlakyOrders()
    o.lose_reply["orb-exit"] = 1
    e = day("put_sl", tmp_path, orders=o, risk__exchange_sl=False)
    assert len(e.strategy.trades) == 1 and e.strategy.trades[0]["pnl"] < 0
    assert net_total(o) == 0 and not e.manual_action_required


def test_accidental_double_buy_is_detected(day, tmp_path):
    """If a buy-back somehow executed twice the broker is LONG; the engine must stop and shout."""
    o = FlakyOrders()
    o.lose_reply["orb-exit"] = 1
    real_positions = o.positions
    o.positions = lambda: {k: v + 75 for k, v in real_positions().items()}   # pretend an extra buy happened
    e = day("put_sell", tmp_path, orders=o)
    assert e.manual_action_required and any("LONG" in ev["msg"] for ev in e.events)


def _live_engine(tmp_path, orders):
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode = "live"
    cfg.data_dir = str(tmp_path / "data")
    cfg.risk.unclear_wait_s = 0.0
    return Engine(cfg, SyntheticInstruments(cfg, DAY), ReplayFeed(build_day(DAY)), orders)


def test_live_start_is_blocked_when_positions_cannot_be_read(tmp_path):
    o = FlakyOrders()
    o.hide_positions = True
    e = _live_engine(tmp_path, o)
    e.start()
    assert e.finished.wait(10)
    assert e.phase == "BLOCKED" and e.manual_action_required
    assert any("will not trade blind" in ev["msg"] for ev in e.events)


def test_live_start_is_blocked_by_an_existing_position(tmp_path):
    o = FlakyOrders()
    o._net["NIFTY25OCT25000PE"] = -75
    e = _live_engine(tmp_path, o)
    e.start()
    assert e.finished.wait(10)
    assert e.phase == "BLOCKED"
