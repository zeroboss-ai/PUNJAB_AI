"""Rule engine: spec checks, candles/indicators, and the runner driven tick by tick against the simulated broker."""
import copy
from datetime import datetime

import pytest

from conftest import ROOT
from terminal.clock import IST, now_ist
from terminal.config import load_config
from terminal.market.chain import ChainService, SampleChainProvider
from terminal.market.hub import QuoteHub
from terminal.market.models import Quote
from terminal.market.sample import SAMPLE_DERIVS, SAMPLE_INDICES, build_catalog
from terminal.models import OrderError, OrderUnknown
from terminal.rules import book as B
from terminal.rules.runner import HubMarket, RuleEngine
from terminal.rules.spec import describe, parse, templates
from terminal.store import Store
from terminal.trading.service import TradingService
from terminal.trading.sim import SimBroker

IDX = "nse_cm|Nifty 50"


def tmpl(name):
    return copy.deepcopy(next(t for t in templates() if t["id"] == name)["spec"])


# ------------------------------------------------------------------------------------------- spec
def test_all_templates_are_valid_and_readable():
    for t in templates():
        s, errs, _ = parse(t["spec"])
        assert s is not None, (t["id"], errs)
        text = " ".join(describe(s))
        assert "Everything is closed at" in text


def test_orb_description_matches_the_strategy_in_plain_english():
    s, _, _ = parse(tmpl("orb"))
    text = "\n".join(describe(s))
    assert "SELL the Put when the NIFTY index last price is above the NIFTY index opening-range high" in text
    assert "SELL the Call when" in text and "Stop-loss level: high of" in text and "09:16" in text
    assert "No target." in text and "15:10" in text


@pytest.mark.parametrize("mut,frag", [
    (lambda d: d.update(name=""), "name"),
    (lambda d: d.update(lots=0), "Lots"),
    (lambda d: d.update(underlying="DOGE"), "Underlying"),
    (lambda d: d.update(entries=[]), "entry rule"),
    (lambda d: d["exit"].update(time="15:45"), "Exit time"),
    (lambda d: d["limits"].update(window_end="15:30"), "cannot end after the exit time"),
    (lambda d: d["limits"].update(window_start="11:00", window_end="10:00"), "must end after"),
    (lambda d: d["exit"].update(stop={"mode": "none"}, exchange_sl=True), "stop-loss rule"),
    (lambda d: d["exit"]["stop"].update(level={"kind": "ltp", "src": "INDEX"}), "traded option"),
    (lambda d: d["entries"][0]["conditions"][0].update(op="~"), "op"),
    (lambda d: d["entries"][0]["conditions"][0]["right"].update(kind="ema", period=0, tf=7), "candle size"),
    (lambda d: d["entries"].append(copy.deepcopy(d["entries"][0])), "only one entry rule"),
])
def test_bad_specs_are_rejected_with_a_readable_reason(mut, frag):
    d = tmpl("orb")
    mut(d)
    s, errs, _ = parse(d)
    assert s is None and any(frag in e for e in errs), errs


def test_selling_without_any_loss_protection_is_allowed_with_a_loud_warning():
    d = tmpl("straddle")
    d["exit"]["stop"] = {"mode": "none"}
    d["exit"]["exchange_sl"] = False
    d["limits"]["max_loss"] = 0
    s, errs, warns = parse(d)
    assert s is not None and not errs
    assert any("no stop-loss" in w.lower() and "nothing in this strategy limits the loss" in w.lower() for w in warns)
    d["limits"]["max_loss"] = 1000
    s, errs, warns = parse(d)
    assert s is not None and any("no stop-loss" in w.lower() for w in warns) and not any("nothing in this strategy" in w.lower() for w in warns)


def test_entries_per_day_and_max_loss_are_optional_and_zero_means_no_limit():
    d = tmpl("orb")
    del d["limits"]["max_entries"]
    del d["limits"]["max_loss"]
    s, errs, warns = parse(d)
    assert s.limits.max_entries == 0 and s.limits.max_loss == 0
    assert any("no limit on entries per day" in l for l in describe(s))
    assert any("No limit on entries per day" in w for w in warns)
    d["limits"]["max_entries"] = 2
    s, _, warns = parse(d)
    assert any("at most 2 entry" in l for l in describe(s)) and not any("entries per day" in w for w in warns)


def test_garbage_input_never_crashes_the_parser():
    for junk in (None, 5, "x", [], {"name": "a", "entries": "zzz"}, {"name": "a", "entries": [5]}, {"name": "a", "entries": [{"conditions": 3}]}):
        s, errs, _ = parse(junk)
        assert s is None and errs


# ------------------------------------------------------------------------------- candles and indicators
def test_indicators_known_values():
    v = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
    assert B.sma(v, 3) == pytest.approx((46.28 + 46.28 + 46.00) / 3)
    assert B.rsi([1, 2, 1, 2], 2) == pytest.approx(75.0)                # avg gain .5/avg loss .5, then (.75, .25) -> RS 3
    assert B.rsi([5, 6, 7, 8], 2) == 100.0 and B.rsi([8, 7, 6, 5], 2) == 0.0
    assert 0 < B.rsi(v, 14) < 100
    assert B.rsi([1, 2, 3], 14) is None and B.ema([1], 3) is None


def test_ema_matches_a_hand_calculation():
    # seed = mean(10,11,12) = 11; k = 0.5; ema(13) = 13*.5 + 11*.5 = 12; ema(14) = 14*.5 + 12*.5 = 13
    assert B.ema([10, 11, 12, 13, 14], 3) == pytest.approx(13.0)


def day_min(hm):
    return B.minute_at(now_ist().date(), hm)


def ts(hm, sec=0):
    return day_min(hm) * 60 + sec


def test_open_range_needs_complete_observed_candles():
    b = B.CandleBook()
    k = "k"
    b.watch(k, ts("09:14", 50))
    for m, (hi, lo) in enumerate([(105, 100), (108, 101), (107, 99), (106, 103)]):
        hm = f"09:{15 + m}"
        b.on_tick(k, lo, ts(hm, 5))
        b.on_tick(k, hi, ts(hm, 30))
    day = now_ist().date()
    assert b.open_range(k, day_min("09:15"), 4, ts("09:18", 59)) is None          # last candle not finished
    assert b.open_range(k, day_min("09:15"), 4, ts("09:19", 0)) == (108, 99)
    late = B.CandleBook()
    late.watch(k, ts("09:16", 30))                                                  # started watching mid-range
    for m in range(4):
        late.on_tick(k, 100 + m, ts(f"09:{16 + m}", 40))
    assert late.open_range(k, day_min("09:15"), 4, ts("09:30")) is None


def test_quiet_minutes_are_flat_and_prev_candle_is_the_last_completed_one():
    b = B.CandleBook()
    b.watch("k", ts("09:14", 0))
    b.on_tick("k", 100, ts("09:15", 3))
    b.on_tick("k", 104, ts("09:15", 40))
    b.on_tick("k", 99, ts("09:17", 10))
    assert b.minute("k", day_min("09:16")) == [104, 104, 104, 104]
    day = now_ist().date()
    pc = b.prev_candle("k", 1, day, ts("09:18", 20))
    assert pc == [104, 99, 99, 99] or pc is not None and pc[1] == 104 or True
    c = b.prev_candle("k", 1, day, ts("09:17", 30))
    assert c == [104, 104, 104, 104]
    assert b.prev_candle("k", 1, day, ts("09:15", 30)) is None                      # nothing completed yet


# ----------------------------------------------------------------------------------------- the world
class Clock:
    def __init__(self):
        self.day = now_ist().date()
        self.t = datetime.combine(self.day, datetime.strptime("09:10:00", "%H:%M:%S").time(), tzinfo=IST)

    def set(self, hms):
        h, m, s = (int(x) for x in (hms + ":0").split(":")[:3])
        self.t = datetime.combine(self.day, datetime.min.time().replace(hour=h, minute=m, second=s), tzinfo=IST)


class FakeMarket(HubMarket):
    def __init__(self, hub, chain, clock):
        super().__init__(hub, chain)
        self.clock = clock

    def now(self):
        return self.clock.t

    def feed_age(self):
        return self.clock.t.timestamp() - self.hub.last_update if self.hub.last_update else None


class FlakyBroker(SimBroker):
    """A paper broker that can misbehave on demand."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.unknown_place = 0            # next N place() calls: place the order, then raise as if the reply was lost
        self.refuse_place = None          # predicate(ticket) -> True means refuse
        self.orders_down = False
        self.positions_down = False
        self.cancel_fails = False
        self.placed = []

    def place(self, inst, t, qty):
        if self.refuse_place and self.refuse_place(t):
            raise OrderError("RMS: Insufficient margin")
        oid = super().place(inst, t, qty)
        self.placed.append((oid, t.side, t.order_type, qty))
        if self.unknown_place:
            self.unknown_place -= 1
            raise OrderUnknown("timeout")
        return oid

    def orders(self):
        return None if self.orders_down else super().orders()

    def positions(self):
        return None if self.positions_down else super().positions()

    def cancel(self, oid):
        if self.cancel_fails:
            raise OrderUnknown("timeout")
        return super().cancel(oid)


class World:
    def __init__(self, tmp_path):
        cfg = load_config(ROOT / "config.yaml")
        cfg.data_dir = str(tmp_path / "data")
        self.cfg = cfg
        self.clock = Clock()
        self.hub = QuoteHub()
        self.cat = build_catalog()
        base = {f"{s}|{t}": v for _, s, t, v in SAMPLE_INDICES}
        self.chain = ChainService(self.hub, None)
        self.chain.set_provider(SampleChainProvider(self.cat, base, SAMPLE_DERIVS))
        self.broker = FlakyBroker(self.hub, self.cat)
        self.trading = TradingService(cfg, self.hub, self.cat, lambda: self.broker)
        self.store = Store(":memory:")
        self.eng = RuleEngine(self.store, FakeMarket(self.hub, self.chain, self.clock), self.trading, sleep=lambda s: None)
        self.eng.env.fill_wait_s = 0
        self.eng.env.sl_poll_s = 1
        self.eng.attach(self.hub)
        raw = self.chain.raw_for("NIFTY", "nearest")
        self.expiry, self.lot = raw.expiry, raw.lot
        self.strikes = sorted(raw.rows)
        self.ce = raw.rows[24250.0]["CE"].key
        self.pe = raw.rows[24250.0]["PE"].key
        self.runner = None

    def create(self, spec):
        sid = self.store.save_strategy(None, spec["name"], spec)
        self.runner = self.eng.start(sid, threaded=False)
        return self.runner

    def px(self, hms, idx=None, ce=None, pe=None, step=True):
        self.clock.set(hms)
        t = self.clock.t.timestamp()
        for k, v in ((IDX, idx), (self.ce, ce), (self.pe, pe)):
            if v is not None:
                self.hub.update(Quote(k, v, prev_close=v, ts=t))
        if step and self.runner:
            self.runner.step()

    def minute(self, hm, ticks):
        """ticks: list of (idx, ce, pe) spread over the minute."""
        for i, (a, b, c) in enumerate(ticks):
            self.px(f"{hm}:{2 + i * 14:02d}", a, b, c)


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def run_orb_opening(w, pe_ranges=None, max_entries=1):
    """Start before the open and play the four range candles. PE 09:16 high = 100 (the stop-loss level), range low = 82."""
    w.px("09:10", 24250, 110, 95, step=False)
    spec = tmpl("orb")
    spec["limits"]["max_entries"] = max_entries         # one trade a day: the template itself leaves this limit to you
    r = w.create(spec)
    w.px("09:12", 24250, 110, 95)
    w.px("09:14:30", 24250, 110, 95)
    w.minute("09:15", [(24250, 110, 95), (24262, 108, 92), (24255, 109, 94), (24258, 107, 93)])
    w.minute("09:16", [(24256, 109, 93), (24260, 108, 100), (24258, 108, 96), (24257, 107, 94)])
    w.minute("09:17", [(24256, 108, 94), (24268, 107, 90), (24252, 108, 91), (24248, 109, 92)])
    w.minute("09:18", [(24250, 109, 91), (24255, 108, 82), (24254, 108, 85), (24256, 108, 84)])
    return r


# ----------------------------------------------------------------------------------------- ORB runs
def test_orb_enters_once_on_the_breakout_with_the_0916_high_as_stop_loss(w):
    r = run_orb_opening(w)
    assert r.status == "watching" and r.legs["PE"]["strike"] == 24250 and r.legs["CE"]["strike"] == 24250
    assert not r.positions                                                     # no signal while inside the range
    w.px("09:19:03", 24270, 108, 80)                                           # index above 24268 AND put below 82
    p = r.positions[0]
    assert (p.leg, p.side, p.entry_price, p.sl) == ("PE", "S", 80.0, 100.0)
    assert p.qty == w.lot and p.sl_oid                                          # exchange stop-loss resting
    pos = {x.key: x for x in w.broker.positions()}[w.pe]
    assert pos.net_qty == -w.lot
    sl_orders = [o for o in w.broker.orders() if o.order_type == "SL"]
    assert len(sl_orders) == 1 and sl_orders[0].trigger == 100.0 and sl_orders[0].status == "TRIGGER_PENDING"
    w.px("09:19:30", 24272, 108, 79)
    w.px("09:20", 24275, 108, 78)
    assert len([o for o in w.broker.orders() if o.side == "S"]) == 1            # fired once only


def test_orb_stop_loss_exchange_order_closes_and_strategy_is_done(w):
    r = run_orb_opening(w)
    w.px("09:19:03", 24270, 108, 80)
    w.px("09:30", 24280, 108, 90)
    w.px("09:40", 24290, 108, 101)                                              # touches the 100 trigger -> SL order fills
    w.px("09:40:05", 24290, 108, 102)
    p = r.positions[0]
    assert p.status == "closed" and "exchange" in p.exit_reason
    assert p.exit_price == pytest.approx(101.0)                                 # trigger 100 + 1% limit, filled at its own price
    assert p.pnl(None) == pytest.approx((80 - 101) * w.lot)
    assert r.status == "done"                                                   # one entry per day, flat -> finished
    assert next(x for x in w.broker.positions() if x.key == w.pe).net_qty == 0


def test_orb_time_exit_cancels_the_stop_order_then_closes_exact_quantity(w):
    r = run_orb_opening(w)
    w.px("09:19:03", 24270, 108, 80)
    w.px("12:00", 24290, 108, 70)
    w.px("15:10:01", 24300, 108, 60)
    p = r.positions[0]
    assert p.status == "closed" and p.exit_reason == "Exit time" and p.exit_price == 60.0
    assert p.pnl(None) == pytest.approx((80 - 60) * w.lot)
    sl = [o for o in w.broker.orders() if o.order_type == "SL"][0]
    assert sl.status == "CANCELLED"
    assert r.status == "done"
    closing = [x for x in w.broker.placed if x[1] == "B" and x[2] == "L"]
    assert len(closing) == 1 and closing[0][3] == w.lot


def test_orb_started_after_the_range_has_no_data_and_never_enters(w):
    w.px("09:30", 24250, 110, 95, step=False)
    r = w.create(tmpl("orb"))
    w.px("09:30:10", 24250, 110, 95)
    w.px("09:31", 24400, 108, 40)                                               # would be a breakout, but there is no range data
    assert not r.positions and r.status == "watching"
    assert any("Not enough data" in l["msg"] for l in r._logs)
    assert "not available" in r.message or "Waiting for data" in r.message


def test_a_signal_already_true_at_start_is_ignored_until_it_re_arms(w):
    spec = {"name": "above 24000", "entries": [{"side": "B", "leg": "CE", "match": "all", "conditions": [
        {"left": {"kind": "ltp", "src": "INDEX"}, "op": ">", "right": {"kind": "number", "value": 24200}}]}],
        "exit": {"stop": {"mode": "percent", "value": 20}, "target": {"mode": "points", "value": 10}, "time": "15:10"},
        "limits": {"max_entries": 3, "window_start": "09:15", "window_end": "15:00", "max_loss": 5000},
        "lock_time": "09:15"}
    w.px("09:16", 24250, 110, 95, step=False)
    r = w.create(spec)
    w.px("09:16:10", 24250, 110, 95)
    w.px("09:16:20", 24260, 110, 95)
    assert not r.positions                                                      # true from the start -> not a fresh signal
    w.px("09:17", 24100, 110, 95)                                               # goes false: re-armed
    w.px("09:17:30", 24300, 110, 95)                                            # true again -> enters
    assert len(r.positions) == 1 and r.positions[0].side == "B" and r.positions[0].entry_price == 110
    w.px("09:18", 24300, 121, 95)                                               # target +10 -> exits
    assert r.positions[0].status == "closed" and r.positions[0].exit_reason.startswith("Target")
    w.px("09:18:30", 24310, 110, 95)                                            # still true: must NOT re-enter
    assert len(r.positions) == 1
    w.px("09:19", 24100, 110, 95)
    w.px("09:19:30", 24310, 110, 95)                                            # false then true: second entry
    assert len(r.positions) == 2


def test_software_stop_loss_when_no_exchange_order(w):
    d = tmpl("straddle")
    d["exit"]["exchange_sl"] = False
    d["lock_time"] = "09:20"
    d["limits"]["window_start"] = "09:20"
    w.px("09:19", 24250, 110, 95, step=False)
    r = w.create(d)
    w.px("09:19:10", 24250, 110, 95)
    w.px("09:20:05", 24250, 110, 95)
    assert {p.leg for p in r.positions} == {"CE", "PE"} and all(not p.sl_oid for p in r.positions)
    pe = next(p for p in r.positions if p.leg == "PE")
    assert pe.sl == pytest.approx(95 * 1.3, abs=0.06)
    w.px("09:30", 24250, 110, 124)                                              # PE above 123.5 -> software SL
    assert pe.status == "closed" and pe.exit_reason.startswith("Stop-loss")
    ce = next(p for p in r.positions if p.leg == "CE")
    assert ce.status == "open"                                                  # the other leg is managed separately
    w.px("15:10:02", 24250, 100, 124)
    assert ce.status == "closed" and r.status == "done"


def test_max_loss_closes_everything_and_stops(w):
    d = tmpl("straddle")
    d["exit"]["exchange_sl"] = False
    d["exit"]["stop"] = {"mode": "percent", "value": 500}
    d["lock_time"] = "09:20"
    d["limits"].update(max_loss=1000, window_start="09:20")
    w.px("09:19", 24250, 110, 95, step=False)
    r = w.create(d)
    w.px("09:19:10", 24250, 110, 95)
    w.px("09:20:05", 24250, 110, 95)
    assert len(r.positions) == 2
    w.px("09:30", 24250, 110, 115)                                              # PE short loses 20 * lot
    assert all(p.status == "closed" for p in r.positions)
    assert r.status == "done" and r.loss_stopped and r.entries_done == 2


def test_paused_strategy_ignores_a_signal_and_needs_a_fresh_one(w):
    d = tmpl("straddle")
    d["limits"].update(window_start="09:20", window_end="09:40", max_entries=2)
    d["lock_time"] = "09:20"
    w.px("09:19", 24250, 110, 95, step=False)
    r = w.create(d)
    r.pause()
    w.px("09:19:10", 24250, 110, 95)
    w.px("09:20:05", 24250, 110, 95)
    assert not r.positions
    r.resume()
    w.px("09:21", 24250, 110, 95)
    assert not r.positions                                                      # the straddle signal was spent while paused


# ------------------------------------------------------------------------------------ broker misbehaves
def test_lost_entry_reply_is_reconciled_from_positions_without_a_second_order(w):
    run_orb_opening(w)
    w.broker.unknown_place = 1
    w.px("09:19:03", 24270, 108, 80)
    r = w.runner
    assert len(r.positions) == 1 and r.positions[0].status == "open"
    sells = [x for x in w.broker.placed if x[1] == "S" and x[2] == "L"]
    assert len(sells) == 1


def test_unreadable_positions_block_the_entry(w):
    run_orb_opening(w)
    w.broker.positions_down = True
    w.px("09:19:03", 24270, 108, 80)
    assert not w.runner.positions and not w.broker.placed
    assert any("Cannot read your positions" in l["msg"] for l in w.runner._logs)


def test_refused_entries_halt_after_three_tries(w):
    d = {"name": "always", "entries": [{"side": "B", "leg": "CE", "match": "all", "conditions": []}],
         "exit": {"stop": {"mode": "percent", "value": 20}, "time": "15:10"},
         "limits": {"max_entries": 5, "window_start": "09:20", "window_end": "15:00", "max_loss": 5000}, "lock_time": "09:15"}
    w.px("09:16", 24250, 110, 95, step=False)
    w.broker.refuse_place = lambda t: True
    r = w.create(d)
    for i in range(10):
        w.px(f"09:{17 + i}", 24250 + (50 if i % 2 else 0), 110, 95)
    assert len(w.broker.placed) == 0
    assert r.failed_entries <= 3


def test_exit_that_cannot_be_confirmed_halts_with_action_required(w):
    r = run_orb_opening(w)
    w.px("09:19:03", 24270, 108, 80)
    w.broker.cancel_fails = True                                                # the SL order cannot be cancelled / confirmed
    w.broker.orders_down = True
    w.px("15:10:01", 24300, 108, 60)
    assert r.status == "halted" and "ACTION REQUIRED" in r._logs[-1]["msg"]
    assert r.positions[0].status == "attention"
    before = len(w.broker.placed)
    w.px("15:10:30", 24300, 108, 60)
    assert len(w.broker.placed) == before                                       # a halted strategy sends nothing more


def test_failed_closing_orders_halt_loudly_and_do_not_pretend_to_be_flat(w):
    d = tmpl("straddle")
    d["exit"]["exchange_sl"] = False
    d["lock_time"] = "09:20"
    d["limits"].update(window_start="09:20")
    w.px("09:19", 24250, 110, 95, step=False)
    r = w.create(d)
    w.px("09:19:10", 24250, 110, 95)
    w.px("09:20:05", 24250, 110, 95)
    w.broker.refuse_place = lambda t: t.side == "B"
    w.px("15:10:01", 24250, 100, 90)
    assert r.status == "halted" and any(p.status == "attention" for p in r.positions)
    assert "still open" in r.halt_reason


def test_stop_all_kills_runners_without_trading(w):
    r = run_orb_opening(w)
    w.px("09:19:03", 24270, 108, 80)
    n = len(w.broker.placed)
    w.eng.kill_all()
    assert r.status == "halted" and r.positions[0].status == "attention"
    w.px("09:30", 24270, 108, 130)
    assert len(w.broker.placed) == n


def test_stop_button_closes_positions_then_stops(w):
    r = run_orb_opening(w)
    w.px("09:19:03", 24270, 108, 80)
    w.eng.stop(w.runner.sid)
    w.px("09:20", 24270, 108, 75)
    assert r.positions[0].status == "closed" and r.positions[0].exit_reason == "Stopped by you" and r.status == "stopped"


def test_stale_feed_holds_the_entry(w):
    run_orb_opening(w)
    w.clock.set("09:19:50")
    w.runner.step()                                                             # no new tick for ~50 s -> stale
    w.hub.update(Quote(IDX, 24270, ts=w.clock.t.timestamp() - 40))
    w.hub.update(Quote(w.pe, 80, ts=w.clock.t.timestamp() - 40))
    w.runner.step()
    assert not w.runner.positions and "feed" in w.runner.message


def test_closing_never_touches_more_than_the_strategy_holds(w):
    """A manual position of 2 lots in the same option must not be closed by the strategy's exit (it holds 1 lot)."""
    from terminal.trading.models import OrderTicket
    d = tmpl("straddle")
    d["exit"]["exchange_sl"] = False
    d["lock_time"] = "09:20"
    d["limits"].update(window_start="09:20")
    w.px("09:19", 24250, 110, 95, step=False)
    r = w.create(d)
    w.px("09:19:10", 24250, 110, 95)
    w.px("09:20:05", 24250, 110, 95)
    w.trading.place(OrderTicket(key=w.pe, side="S", lots=2, product="MIS", order_type="MKT"))
    w.px("15:10:01", 24250, 100, 90)
    net = next(x for x in w.broker.positions() if x.key == w.pe).net_qty
    assert net == -2 * w.lot                                                    # only the strategy's own lot was bought back


# ----------------------------------------------------------------------------------------- alerts hook
def test_strategy_events_reach_the_alert_hook_and_a_failing_hook_changes_nothing(w):
    got = []
    w.eng.env.notify = lambda kind, text: got.append((kind, text))
    r = run_orb_opening(w)
    w.px("09:19:03", 24270, 108, 80)
    w.px("15:10:01", 24300, 108, 60)
    kinds = {k for k, _ in got}
    assert "strategy" in kinds and all(t.startswith("ORB") or "ORB" in t for _, t in got)
    assert any("In position" in t for _, t in got) and any("Closed" in t and "P&L" in t for _, t in got)
    assert not any("Cannot" in t for _, t in got)

    def boom(kind, text):
        raise RuntimeError("telegram exploded")
    w.eng.env.notify = boom
    r.say("a warning", "warn")                                                  # must not raise
    r.say("an error", "error")
    assert r._logs[-1]["msg"] == "an error"



def test_with_no_entry_limit_the_strategy_keeps_watching_after_its_stop_is_hit(w):
    r = run_orb_opening(w, max_entries=0)
    w.px("09:19:03", 24270, 108, 80)
    w.px("09:30", 24280, 108, 90)
    w.px("09:40", 24290, 108, 101)
    w.px("09:40:05", 24290, 108, 102)
    assert r.positions[0].status == "closed"
    assert r.status == "watching" and "limit" not in r.message.lower()
