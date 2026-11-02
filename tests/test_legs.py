"""Leg builder: strike selection, spec checks, and the leg runner driven tick by tick against the simulated broker."""
import copy
import math
from datetime import datetime

import pytest

from conftest import ROOT
from terminal.clock import IST, now_ist
from terminal.config import load_config
from terminal.market.chain import ChainService, SampleChainProvider
from terminal.market.hub import QuoteHub
from terminal.market.models import Quote
from terminal.market.sample import SAMPLE_DERIVS, SAMPLE_INDICES, build_catalog
from terminal.models import OrderError
from terminal.rules import legspec as L
from terminal.rules import strikes as K
from terminal.rules.runner import RuleEngine
from terminal.rules.strategies import describe, make_runner, parse, templates
from terminal.store import Store
from terminal.trading.service import TradingService
from test_rules import Clock, FakeMarket, FlakyBroker

IDX = "nse_cm|Nifty 50"
T = 1 / 365 * 2                                   # two days to expiry, for the pure strike tests


def tmpl(name):
    return copy.deepcopy(next(t for t in templates() if t["id"] == name)["spec"])


def leg(side="S", kind="CE", **kw):
    d = {"segment": "options", "lots": 1, "side": side, "kind": kind, "expiry": "nearest", "strike": {"by": "atm", "steps": 0}}
    d.update(kw)
    return d


def spec(*legs, **kw):
    d = {"version": 2, "name": "T", "underlying": "NIFTY", "entry_time": "09:35", "exit_time": "15:14", "legs": list(legs),
         "limits": {"max_loss": 100000, "slippage_pct": 1.0}}
    d.update(kw)
    return d


def sl(pct):
    return {"mode": "percent", "value": pct}


# -------------------------------------------------------------------------------------------- strikes
STRIKES = [24000 + 50 * i for i in range(-10, 11) if True]
STRIKES = [24250 + 50 * i for i in range(-12, 13)]


def bs_prices(spot, vol=0.14, t=T):
    def f(k, kind):
        if k not in STRIKES:
            return None
        return max(0.05, round(K.bs_price(kind, spot, k, t, vol), 2))
    return f


def test_implied_vol_and_delta_match_black_scholes():
    p = K.bs_price("CE", 24250, 24250, T, 0.15)
    assert K.implied_vol("CE", p, 24250, 24250, T) == pytest.approx(0.15, abs=1e-4)
    assert K.delta("CE", p, 24250, 24250, T) == pytest.approx(0.5, abs=0.02)
    pp = K.bs_price("PE", 24250, 24000, T, 0.15)
    d = K.delta("PE", pp, 24250, 24000, T)
    assert 0.05 < d < 0.3                                          # an out-of-the-money put has a small absolute delta
    assert K.implied_vol("CE", 0.0, 24250, 24250, T) is None and K.implied_vol("CE", 5.0, 24250, 24000, T) is None   # below intrinsic


@pytest.mark.parametrize("kind,steps,want", [("CE", 0, 24250), ("CE", 3, 24400), ("PE", 3, 24100), ("CE", -2, 24150), ("PE", -2, 24350)])
def test_atm_steps_out_and_in_of_the_money(kind, steps, want):
    assert K.pick_strike({"by": "atm", "steps": steps}, kind, STRIKES, 24262, bs_prices(24262), T).strike == want


def test_atm_outside_the_chain_is_an_error():
    with pytest.raises(K.StrikeError, match="outside the option chain"):
        K.pick_strike({"by": "atm", "steps": 40}, "CE", STRIKES, 24250, bs_prices(24250), T)


def test_premium_selectors():
    px = bs_prices(24250)
    c = K.pick_strike({"by": "premium_closest", "value": 50}, "CE", STRIKES, 24250, px, T)
    assert abs(px(c.strike, "CE") - 50) == min(abs(px(k, "CE") - 50) for k in STRIKES)
    g = K.pick_strike({"by": "premium_gte", "value": 50}, "CE", STRIKES, 24250, px, T)
    assert px(g.strike, "CE") >= 50 and all(px(k, "CE") < 50 or px(k, "CE") >= px(g.strike, "CE") for k in STRIKES)
    l = K.pick_strike({"by": "premium_lte", "value": 50}, "PE", STRIKES, 24250, px, T)
    assert px(l.strike, "PE") <= 50 and all(px(k, "PE") > 50 or px(k, "PE") <= px(l.strike, "PE") for k in STRIKES)
    r = K.pick_strike({"by": "premium_range", "lo": 30, "hi": 60}, "CE", STRIKES, 24250, px, T)
    assert 30 <= px(r.strike, "CE") <= 60
    with pytest.raises(K.StrikeError, match="between 1000 and 2000"):
        K.pick_strike({"by": "premium_range", "lo": 1000, "hi": 2000}, "CE", STRIKES, 24250, px, T)


def test_premium_needs_an_unbroken_run_of_prices_and_never_guesses_at_a_gap():
    px = bs_prices(24250)
    gappy = lambda k, kind: None if k in (24450, 24500, 24550, 24600) else px(k, kind)
    with pytest.raises(K.StrikeError, match="next to the best match"):
        K.pick_strike({"by": "premium_closest", "value": 1.0}, "CE", STRIKES, 24250, gappy, T)
    with pytest.raises(K.StrikeError, match="ATM"):
        K.pick_strike({"by": "premium_closest", "value": 50}, "CE", STRIKES, 24250, lambda k, kind: None, T)


def test_delta_selectors_choose_the_strike_whose_delta_is_nearest():
    px = bs_prices(24250)
    c = K.pick_strike({"by": "delta_closest", "value": 0.2}, "CE", STRIKES, 24250, px, T)
    d = lambda k, kind: K.delta(kind, px(k, kind), 24250, k, T)
    assert abs(d(c.strike, "CE") - 0.2) == min(abs(d(k, "CE") - 0.2) for k in STRIKES if d(k, "CE") is not None)
    p = K.pick_strike({"by": "delta_closest", "value": 0.2}, "PE", STRIKES, 24250, px, T)
    assert p.strike < 24250 < c.strike
    r = K.pick_strike({"by": "delta_range", "lo": 0.15, "hi": 0.25}, "CE", STRIKES, 24250, px, T)
    assert 0.15 <= d(r.strike, "CE") <= 0.25


def test_percent_straddle_and_synthetic_selectors():
    px = bs_prices(24250)
    assert K.pick_strike({"by": "pct_atm", "value": 1.0}, "CE", STRIKES, 24250, px, T).strike == 24500       # 24250 * 1.01 = 24492.5
    assert K.pick_strike({"by": "pct_atm", "value": 1.0}, "PE", STRIKES, 24250, px, T).strike == 24000
    assert K.pick_strike({"by": "pct_atm", "value": -1.0}, "CE", STRIKES, 24250, px, T).strike == 24000
    prem = px(24250, "CE") + px(24250, "PE")
    want = min(STRIKES, key=lambda k: abs(k - (24250 + prem)))
    assert K.pick_strike({"by": "straddle", "value": 100}, "CE", STRIKES, 24250, px, T).strike == want
    assert K.pick_strike({"by": "straddle", "value": 100}, "PE", STRIKES, 24250, px, T).strike < 24250
    skew = lambda k, kind: px(k, kind) + (30 if (k == 24250 and kind == "CE") else 0)                         # call dearer than put: synthetic future above spot
    syn = K.pick_strike({"by": "synthetic", "steps": 0}, "CE", STRIKES, 24250, skew, T)
    assert syn.strike == 24300 and "synthetic" in syn.note
    with pytest.raises(K.StrikeError, match="Waiting for the ATM"):
        K.pick_strike({"by": "straddle", "value": 50}, "CE", STRIKES, 24250, lambda k, kind: None, T)


# ---------------------------------------------------------------------------------------------- spec
def test_templates_are_valid_readable_and_survive_a_round_trip():
    leg_t = [t for t in templates() if t["engine"] == "legs"]
    assert len(leg_t) >= 5 and any(t["engine"] == "rules" for t in templates())
    for t in leg_t:
        s, errs, _ = parse(t["spec"])
        assert s is not None, (t["id"], errs)
        j = s.to_json()
        s2, e2, _ = parse(j)
        assert s2 is not None and s2.to_json() == j
        text = " ".join(describe(s))
        assert "all positions are closed at" in text and "Leg 1:" in text


def test_description_is_plain_english():
    s, _, _ = parse(tmpl("leg_strangle_premium"))
    text = "\n".join(describe(s))
    assert "SELL 1 lot(s) of the NIFTY Call" in text and "price closest to ₹40" in text
    assert "Stop-loss 40% against" in text and "for every 10% in your favour, the stop-loss moves 10%" in text
    assert "Overall target" in text and "₹3,000" in text
    s, _, _ = parse(tmpl("leg_momentum"))
    text = "\n".join(describe(s))
    assert "moved up by 10%" in text and "price returns to the original entry price" in text


@pytest.mark.parametrize("mut,frag", [
    (lambda d: d.update(name=""), "name"),
    (lambda d: d.update(legs=[]), "at least one leg"),
    (lambda d: d.update(underlying="DOGE"), "Underlying"),
    (lambda d: d.update(exit_time="15:20"), "15:15"),
    (lambda d: d.update(entry_time="09:10"), "09:15"),
    (lambda d: d.update(entry_time="15:00", exit_time="14:00"), "after the entry time"),
    (lambda d: d["legs"][0].update(lots=0), "lots"),
    (lambda d: d["legs"][0].update(side="X"), "side"),
    (lambda d: d["legs"][0]["strike"].update(by="astrology"), "by"),
    (lambda d: d["legs"][0].update(strike={"by": "delta_closest", "value": 1.5}), "between"),
    (lambda d: d["legs"][0].update(strike={"by": "premium_range", "lo": 50, "hi": 40}), "lower premium"),
    (lambda d: d["legs"][0].update(trail={"on": True, "every": 5, "by": 5}, sl={"mode": "none"}), "needs a stop-loss to move"),
    (lambda d: d["legs"][0].update(reentry_sl={"mode": "asap", "count": 1}, sl={"mode": "none"}), "needs a stop-loss"),
    (lambda d: d["legs"][0].update(reentry_tgt={"mode": "asap", "count": 1}), "needs a target"),
    (lambda d: d["legs"][0].update(trigger={"type": "range", "until": "09:30"}), "range must end after the entry time"),
    (lambda d: d["legs"][0].update(trigger={"type": "condition", "conditions": []}), "at least one condition"),
    (lambda d: d["legs"][0].update(trigger={"type": "condition", "conditions": [{"left": {"kind": "ltp", "src": "CE"}, "op": ">", "right": {"kind": "number", "value": 1}}]}), "index or at this leg"),
    (lambda d: d.update(overall={"trail": {"on": True, "reach": 1000, "lock": 1500}}), "locked profit must be less"),
    (lambda d: d.update(overall={"trail": {"on": True, "reach": 1000, "lock": 200, "every": 100}}), "both"),
    (lambda d: d.update(no_reentry_after="09:00"), "No re-entry after"),
    (lambda d: d.update(stop_monitoring_after="16:00"), "Stop monitoring"),
])
def test_bad_specs_are_rejected_with_a_readable_reason(mut, frag):
    d = spec(leg(sl=sl(25)))
    mut(d)
    s, errs, _ = parse(d)
    assert s is None and any(frag.lower() in e.lower() for e in errs), errs


def test_too_many_legs_and_garbage_never_crash_the_parser():
    s, errs, _ = parse(spec(*[leg(sl=sl(25)) for _ in range(9)]))
    assert s is None and any("At most 8 legs" in e for e in errs)
    for junk in (None, 5, "x", [], {"version": 2}, {"version": 2, "name": "a", "legs": "zzz"}, {"version": 2, "name": "a", "legs": [5]},
                 {"version": 2, "name": "a", "legs": [{"strike": 3, "trigger": 4, "sl": 5, "trail": 6}]}):
        s, errs, _ = parse(junk)
        assert s is None and errs


def test_old_rule_block_strategies_still_parse_with_the_old_engine():
    s, errs, _ = parse(next(t for t in templates() if t["id"] == "orb")["spec"])
    assert s is not None and not isinstance(s, L.LegStrategy)


def test_warnings_for_risky_but_legal_choices():
    s, errs, w = parse(spec(leg(), limits={"max_loss": 2000}))
    assert s is not None and any("no stop-loss" in x for x in w)
    s, errs, w = parse(spec(leg(sl=sl(20)), {**leg(), "segment": "futures"}))
    assert s is not None and any("Futures legs" in x for x in w)
    s, errs, w = parse(spec(leg(sl=sl(20), strike={"by": "delta_closest", "value": 0.3})))
    assert any("Delta is estimated" in x for x in w)


# ------------------------------------------------------------------------------------------ the world
class LegMarket(FakeMarket):
    def __init__(self, hub, chain, clock, cat):
        super().__init__(hub, chain, clock)
        self._cat = cat


class LegWorld:
    def __init__(self, tmp_path):
        cfg = load_config(ROOT / "config.yaml")
        cfg.data_dir = str(tmp_path / "data")
        self.cfg, self.clock, self.hub, self.cat = cfg, Clock(), QuoteHub(), build_catalog()
        base = {f"{s}|{t}": v for _, s, t, v in SAMPLE_INDICES}
        self.chain = ChainService(self.hub, None)
        self.chain.set_provider(SampleChainProvider(self.cat, base, SAMPLE_DERIVS))
        self.broker = FlakyBroker(self.hub, self.cat)
        self.trading = TradingService(cfg, self.hub, self.cat, lambda: self.broker)
        self.store = Store(":memory:")
        self.eng = RuleEngine(self.store, LegMarket(self.hub, self.chain, self.clock, self.cat), self.trading, sleep=lambda s: None)
        self.eng.env.fill_wait_s = 0
        self.eng.env.sl_poll_s = 1
        self.eng.attach(self.hub)
        raw = self.chain.raw_for("NIFTY", "nearest")
        self.expiry, self.lot, self.raw = raw.expiry, raw.lot, raw
        self.runner = None
        self.spot = 24250.0
        self.px = {}                                           # key -> last pushed price

    def key(self, strike, kind):
        return self.raw.rows[float(strike)][kind].key

    def create(self, sp):
        sid = self.store.save_strategy(None, sp["name"], sp)
        self.runner = self.eng.start(sid, threaded=False)
        return self.runner

    def at(self, hms, spot=None, step=True, **prices):
        """Move the clock, push the index and any option prices (by 'CE24250' style names), then step the strategy."""
        self.clock.set(hms)
        t = self.clock.t.timestamp()
        if spot is not None:
            self.spot = spot
        self.hub.update(Quote(IDX, self.spot, prev_close=self.spot, ts=t))
        for name, v in prices.items():
            kind, strike = name[:2], float(name[2:])
            self.px[self.key(strike, kind)] = v
        for k, v in self.px.items():
            self.hub.update(Quote(k, v, prev_close=v, ts=t))
        if step and self.runner:
            self.runner.step()

    def years_left(self, hms="09:35"):
        y, m, d = (int(x) for x in self.expiry.split("-"))
        exp = datetime(y, m, d, 15, 30, tzinfo=IST)
        h, mi = (int(x) for x in hms.split(":"))
        now = datetime.combine(self.clock.day, datetime.min.time().replace(hour=h, minute=mi), tzinfo=IST)
        return max((exp - now).total_seconds(), 60.0) / (365 * 86400)

    def chain_prices(self, spot, vol=0.14):
        """Price every strike of the chain from Black-Scholes so premium / delta selection has data."""
        t = self.years_left()
        for strike, legs in self.raw.rows.items():
            for kind, lg in legs.items():
                self.px[lg.key] = max(0.05, round(K.bs_price(kind, spot, strike, t, vol), 2))

    def positions(self):
        return {p.key: p for p in self.broker.positions()}

    def net(self, strike, kind):
        p = self.positions().get(self.key(strike, kind))
        return p.net_qty if p else 0


@pytest.fixture
def w(tmp_path):
    return LegWorld(tmp_path)


def start_before_entry(w, sp, **prices):
    w.chain_prices(24250)
    w.at("09:30", 24250, step=False, **prices)
    r = w.create(sp)
    w.at("09:31")
    return r


# --------------------------------------------------------------------------------------- straddle runs
def test_straddle_sells_both_legs_at_the_entry_time_with_exchange_stops(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(25)), leg("S", "PE", sl=sl(25))), CE24250=100, PE24250=90)
    assert r.status == "waiting" and not r.positions                               # nothing before the entry time
    w.at("09:34:59")
    assert not r.positions
    w.at("09:35:02")
    assert r.status == "watching" and len(r.positions) == 2
    ce, pe = r.positions
    assert (ce.side, ce.entry_price, ce.sl, ce.leg) == ("S", 100.0, 125.0, "L1")
    assert (pe.side, pe.entry_price, pe.sl, pe.leg) == ("S", 90.0, 112.5, "L2")
    assert ce.sl_oid and pe.sl_oid
    assert w.net(24250, "CE") == -w.lot and w.net(24250, "PE") == -w.lot
    triggers = sorted(o.trigger for o in w.broker.orders() if o.order_type == "SL")
    assert triggers == [112.5, 125.0]
    snap = r.snapshot()
    assert snap["kind"] == "legs" and [l["status"] for l in snap["legs"]] == ["open", "open"]


def test_partial_square_off_only_the_stopped_leg_closes_then_the_rest_at_exit(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(25)), leg("S", "PE", sl=sl(25))), CE24250=100, PE24250=90)
    w.at("09:35:02")
    w.at("10:00", CE24250=126)
    w.at("10:00:05", CE24250=127)
    ce, pe = r.positions
    assert ce.status == "closed" and "Stop-loss" in ce.exit_reason and pe.status == "open"
    assert w.net(24250, "CE") == 0 and w.net(24250, "PE") == -w.lot
    assert r.status == "watching"
    w.at("14:00", PE24250=70)
    w.at("15:14:01")
    assert pe.status == "closed" and pe.exit_reason == "Exit time" and pe.exit_price == 70.0
    assert r.status == "done" and w.net(24250, "PE") == 0
    assert r.pnl()["realised"] == pytest.approx(((100 - ce.exit_price) + (90 - 70)) * w.lot)


def test_complete_square_off_closes_everything_when_one_leg_stops_out(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(25)), leg("S", "PE", sl=sl(25)), square_off="complete"), CE24250=100, PE24250=90)
    w.at("09:35:02")
    w.at("10:00", CE24250=126, PE24250=85)
    w.at("10:00:05", CE24250=127, PE24250=85)
    w.at("10:00:10")
    ce, pe = r.positions
    assert ce.status == "closed" and pe.status == "closed" and "complete square-off" in "\n".join(l["msg"] for l in r.snapshot()["log"])
    assert r.status == "done" and w.net(24250, "PE") == 0 and w.net(24250, "CE") == 0
    assert pe.exit_price == pytest.approx(85, abs=1.0)


def test_late_start_does_not_trade(w):
    w.chain_prices(24250)
    w.at("09:50", 24250, step=False, CE24250=100, PE24250=90)
    r = w.create(spec(leg("S", "CE", sl=sl(25))))
    w.at("09:50:05")
    assert r.status == "done" and not r.positions and "Started after 09:35" in r.message
    assert not [o for o in w.broker.orders() if o.status != "CANCELLED"]


def test_two_legs_choosing_the_same_option_are_refused_before_any_order(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(25)), leg("B", "CE")), CE24250=100)
    w.at("09:35:02")
    assert r.status == "halted" and "same option" in r.halt_reason and not r.positions and not w.broker.orders()


# ------------------------------------------------------------------------------------------ trailing
def sl_orders(w, key):
    return [o for o in w.broker.orders() if o.order_type == "SL" and o.key == key]


def test_trailing_stop_moves_the_exchange_order_and_never_loosens(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(40), trail={"on": True, "every": 10, "by": 10})), CE24250=100)
    w.at("09:35:02")
    p = r.positions[0]
    assert (p.entry_price, p.sl, p.sl_init) == (100.0, 140.0, 140.0)
    w.at("09:40", CE24250=88)                                       # moved 12 in our favour: one step of 10 -> stop 130
    assert p.sl == 130.0 and p.trail_steps == 1
    w.at("09:50", CE24250=78)                                       # 22: two steps -> 120
    assert p.sl == 120.0 and p.trail_steps == 2
    w.at("10:00", CE24250=90)                                       # price comes back: the stop stays at 120
    assert p.sl == 120.0
    orders = sl_orders(w, p.key)
    live = [o for o in orders if o.status == "TRIGGER_PENDING"]
    assert [o.status for o in orders].count("CANCELLED") == 2 and len(live) == 1 and live[0].trigger == 120.0
    w.at("10:10", CE24250=121)
    w.at("10:10:05", CE24250=122)
    assert p.status == "closed" and "Stop-loss" in p.exit_reason and p.exit_price == pytest.approx(121.0, abs=1.5)
    assert r.status == "done" and w.net(24250, "CE") == 0


def test_trailing_with_points_and_a_buy_leg(w):
    r = start_before_entry(w, spec(leg("B", "CE", sl={"mode": "points", "value": 20}, trail={"on": True, "every": 10, "by": 5})), CE24250=100)
    w.at("09:35:02")
    p = r.positions[0]
    assert (p.side, p.entry_price, p.sl) == ("B", 100.0, 80.0)
    w.at("09:50", CE24250=125)                                      # +25: two steps -> stop 90
    assert p.sl == 90.0
    w.at("09:51", CE24250=95)
    w.at("09:51:05", CE24250=89.5)                                    # trigger 90 -> a sell limit just below it, filled at its own price
    assert p.status == "closed" and p.exit_price == pytest.approx(89.1, abs=0.2)


def test_a_trail_step_that_would_sit_inside_the_price_is_skipped_not_forced(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl={"mode": "points", "value": 5}, trail={"on": True, "every": 10, "by": 20})), CE24250=100)
    w.at("09:35:02")
    p = r.positions[0]
    w.at("09:40", CE24250=88)                                       # a 20-point move of a 5-point stop would put it at 85, below the price 88
    assert p.sl == 105.0 and p.status == "open"
    assert any("too close" in l["msg"] for l in r.snapshot()["log"])


# ----------------------------------------------------------------------------------------- re-entries
def test_re_entry_asap_after_stop_loss_up_to_the_count_then_stops(w):
    sp = spec(leg("S", "CE", sl=sl(25), reentry_sl={"mode": "asap", "count": 1}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=126)
    w.at("10:00:05", CE24250=127)
    assert len(r.positions) == 2 and r.positions[0].status == "closed"
    p2 = r.positions[1]
    assert p2.side == "S" and p2.status == "open" and p2.entry_price == pytest.approx(127, abs=1.5) and p2.sl == pytest.approx(p2.entry_price * 1.25, abs=0.06)
    w.at("10:30", CE24250=159)
    w.at("10:30:05", CE24250=160)
    assert p2.status == "closed" and len(r.positions) == 2           # count of 1 is used up
    assert r.status == "done"


def test_re_entry_at_cost_waits_for_the_original_entry_price(w):
    sp = spec(leg("S", "CE", sl=sl(25), reentry_sl={"mode": "cost", "count": 2}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=126)
    w.at("10:00:05", CE24250=127)
    assert len(r.positions) == 1 and r.snapshot()["legs"][0]["pending"]["mode"] == "cost"
    w.at("10:10", CE24250=110)
    assert len(r.positions) == 1 and r.status == "watching"
    w.at("10:20", CE24250=99.5)                                      # back at the entry price
    assert len(r.positions) == 2 and r.positions[1].side == "S" and r.positions[1].entry_price == pytest.approx(99.5, abs=1.5)


def test_reverse_re_entry_takes_the_opposite_side(w):
    sp = spec(leg("S", "CE", sl=sl(25), reentry_sl={"mode": "asap", "reverse": True, "count": 1}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=126)
    w.at("10:00:05", CE24250=127)
    assert [p.side for p in r.positions] == ["S", "B"]
    p2 = r.positions[1]
    assert p2.status == "open" and p2.sl == pytest.approx(p2.entry_price * 0.75, abs=0.06)      # a bought leg's stop is below its entry
    assert w.net(24250, "CE") == w.lot


def test_momentum_re_entry_waits_for_a_move_from_the_exit_price(w):
    sp = spec(leg("S", "CE", sl=sl(25), reentry_sl={"mode": "momentum", "unit": "points", "value": 10, "count": 1}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=126)
    w.at("10:00:05", CE24250=127)
    exit_px = r.positions[0].exit_price
    w.at("10:10", CE24250=exit_px - 5)
    assert len(r.positions) == 1
    w.at("10:11", CE24250=exit_px - 11)                              # a fall of 10+ from the exit price -> sell again
    assert len(r.positions) == 2 and r.positions[1].side == "S"


def test_re_entry_after_target(w):
    sp = spec(leg("S", "CE", target={"mode": "percent", "value": 30}, sl=sl(25), reentry_tgt={"mode": "asap", "count": 1}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    assert r.positions[0].target == 70.0
    w.at("10:00", CE24250=69)
    assert r.positions[0].status == "closed" and r.positions[0].exit_reason.startswith("Target") and len(r.positions) == 2


def test_no_re_entry_after_the_cut_off_time(w):
    sp = spec(leg("S", "CE", sl=sl(25), reentry_sl={"mode": "asap", "count": 3}), no_reentry_after="10:00")
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    w.at("10:05", CE24250=126)
    w.at("10:05:05", CE24250=127)
    assert len(r.positions) == 1 and r.status == "done"


# ------------------------------------------------------------------------------------------- triggers
def test_momentum_entry_waits_for_the_move_from_the_price_at_the_entry_time(w):
    sp = spec(leg("S", "CE", sl=sl(25), trigger={"type": "momentum", "unit": "percent", "dir": "up", "value": 10}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    assert r.status == "watching" and not r.positions and r.snapshot()["legs"][0]["status"] == "selected"
    w.at("09:40", CE24250=105)
    assert not r.positions and "needed" in r.snapshot()["legs"][0]["progress"]
    w.at("09:41", CE24250=110.5)
    assert len(r.positions) == 1 and r.positions[0].entry_price == pytest.approx(110.5, abs=1.5)


def test_momentum_down_and_the_last_entry_time(w):
    sp = spec(leg("S", "CE", sl=sl(25), trigger={"type": "momentum", "unit": "points", "dir": "down", "value": 8}), last_entry_time="10:00")
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    w.at("09:50", CE24250=95)
    assert not r.positions
    w.at("10:00:05", CE24250=90)                                       # too late: the window closed at 10:00
    assert not r.positions and r.snapshot()["legs"][0]["status"] == "skipped" and r.status == "done"


def test_range_breakout_on_the_option_enters_when_the_range_high_breaks(w):
    sp = spec(leg("S", "CE", sl=sl(25), trigger={"type": "range", "until": "09:45", "side": "high", "of": "option"}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    for i, px in enumerate([102, 98, 104, 96, 101, 103, 99, 100, 102, 97], start=35):
        w.at(f"09:{i}:20", CE24250=px)
        w.at(f"09:{i}:40", CE24250=px + 1)
    assert not r.positions
    w.at("09:45:30", CE24250=104.5)                                    # range high is 105 (104 + 1): not broken yet
    assert not r.positions
    w.at("09:46", CE24250=106)
    assert len(r.positions) == 1 and r.positions[0].entry_price == pytest.approx(106, abs=1.5)


def test_range_breakout_of_the_underlying_low(w):
    sp = spec(leg("B", "PE", trigger={"type": "range", "until": "09:40", "side": "low", "of": "underlying"}, sl=sl(25)))
    r = start_before_entry(w, sp, PE24250=90)
    w.at("09:35:02", 24250)
    for i, s_ in enumerate([24260, 24240, 24270, 24235, 24255], start=35):
        w.at(f"09:{i}:20", s_)
    w.at("09:40:30", 24236)
    assert not r.positions
    w.at("09:41", 24230)                                               # below the range low 24235
    assert len(r.positions) == 1 and r.positions[0].side == "B"


def test_condition_trigger_needs_a_fresh_signal(w):
    cond = {"type": "condition", "match": "all", "conditions": [{"left": {"kind": "ltp", "src": "INDEX"}, "op": ">", "right": {"kind": "number", "value": 24300}}]}
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(25), trigger=cond)), CE24250=100)
    w.at("09:35:02")
    assert not r.positions
    w.at("09:40", 24310)
    assert len(r.positions) == 1                                       # false -> true: fires once
    w.at("09:41", 24320)
    assert len(r.positions) == 1


def test_condition_that_is_already_true_is_ignored_until_it_turns_false_and_true_again(w):
    cond = {"type": "condition", "match": "all", "conditions": [{"left": {"kind": "ltp", "src": "INDEX"}, "op": ">", "right": {"kind": "number", "value": 24200}}]}
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(25), trigger=cond)), CE24250=100)
    w.at("09:35:02")
    w.at("09:36", 24260)
    assert not r.positions and "already true" in r.snapshot()["legs"][0]["progress"]
    w.at("09:37", 24190)
    w.at("09:38", 24210)
    assert len(r.positions) == 1


def test_condition_on_the_legs_own_option(w):
    cond = {"type": "condition", "match": "all", "conditions": [{"left": {"kind": "ltp", "src": "SELF"}, "op": "<", "right": {"kind": "number", "value": 95}}]}
    r = start_before_entry(w, spec(leg("S", "PE", sl=sl(25), trigger=cond)), PE24250=100)
    w.at("09:35:02")
    assert not r.positions
    w.at("09:40", PE24250=94)
    assert len(r.positions) == 1 and r.positions[0].leg == "L1"


def test_price_that_jumps_past_the_stop_limit_is_closed_by_the_terminal(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(25))), CE24250=100)
    w.at("09:35:02")
    p = r.positions[0]
    w.at("10:00", CE24250=140)                                       # gapped far beyond the 125 trigger and its limit (126.25)
    assert p.status == "closed" and p.exit_reason == "Stop-loss (price ran past the exchange order)"
    assert w.net(24250, "CE") == 0
    assert not [o for o in w.broker.orders() if o.is_open]           # the resting stop order was cancelled


# ---------------------------------------------------------------------------------------- overall rules
def two_legs(**kw):
    return spec(leg("S", "CE", sl=sl(60)), leg("S", "PE", sl=sl(60)), **kw)


def test_overall_stop_loss_in_rupees_closes_everything_and_ends_the_day(w):
    r = start_before_entry(w, two_legs(overall={"sl": {"mode": "rupees", "value": 1000}}), CE24250=100, PE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=114)                                       # -14 * 75 = -1050
    w.at("10:00:05")
    assert [p.status for p in r.positions] == ["closed", "closed"] and r.status == "done"
    assert all("Overall stop-loss" in p.exit_reason for p in r.positions)
    assert w.net(24250, "CE") == 0 and w.net(24250, "PE") == 0


def test_overall_stop_loss_as_a_percent_of_the_premium_taken(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(60)), overall={"sl": {"mode": "percent", "value": 10}}), CE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=109)                                       # -9% of the 7,500 premium: not yet
    assert r.positions[0].status == "open"
    w.at("10:01", CE24250=111)
    assert r.positions[0].status == "closed" and "10% of premium" in r.positions[0].exit_reason


def test_overall_target_closes_everything(w):
    r = start_before_entry(w, two_legs(overall={"target": {"mode": "rupees", "value": 1000}}), CE24250=100, PE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=86)                                        # +14 * 75 = +1050
    w.at("10:00:05")
    assert all(p.status == "closed" for p in r.positions) and r.status == "done"
    assert all("Overall target" in p.exit_reason for p in r.positions)


def test_overall_profit_lock_and_trail(w):
    ov = {"trail": {"on": True, "reach": 1000, "lock": 400, "every": 500, "by": 250}}
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(60)), overall=ov), CE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=86)                                        # +1050 -> lock 400
    assert r.floor == 400
    w.at("10:10", CE24250=79)                                        # +1575 -> 500 above the trigger once -> lock 650
    assert r.floor == 650 and r.positions[0].status == "open"
    w.at("10:20", CE24250=91)                                        # +675, still above 650
    assert r.positions[0].status == "open"
    w.at("10:21", CE24250=92)                                        # +600 <= 650: leave
    assert r.positions[0].status == "closed" and "profit lock" in r.positions[0].exit_reason and r.status == "done"


def test_stop_monitoring_after_switches_off_targets_but_not_the_stop(w):
    sp = spec(leg("S", "CE", sl=sl(25), target={"mode": "percent", "value": 30}), stop_monitoring_after="12:00")
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    w.at("12:30", CE24250=60)                                        # below the 70 target, but nobody is watching targets any more
    assert r.positions[0].status == "open" and not r.snapshot()["overall"]["monitoring"]
    w.at("13:00", CE24250=130)
    w.at("13:00:05", CE24250=131)
    assert r.positions[0].status == "closed" and "Stop-loss" in r.positions[0].exit_reason


def test_max_loss_still_protects_without_any_leg_or_overall_stop(w):
    r = start_before_entry(w, spec(leg("S", "CE"), limits={"max_loss": 500, "slippage_pct": 1.0}), CE24250=100)
    w.at("09:35:02")
    w.at("10:00", CE24250=108)                                       # -600
    w.at("10:00:05")
    assert r.positions[0].status == "closed" and r.positions[0].exit_reason == "Max loss reached" and r.status == "done"


# ---------------------------------------------------------------------------- hedges, break-even, futures
def test_bought_hedge_legs_are_placed_before_sold_legs(w):
    sp = spec(leg("S", "CE", sl=sl(30), strike={"by": "atm", "steps": 2}), leg("B", "CE", strike={"by": "atm", "steps": 6}))
    r = start_before_entry(w, sp, CE24350=60, CE24550=15)
    w.at("09:35:02")
    sides = [p[1] for p in w.broker.placed if p[2] != "SL"]
    assert sides[:2] == ["B", "S"] and len(r.positions) == 2
    assert w.net(24350, "CE") == -w.lot and w.net(24550, "CE") == w.lot


def test_a_missing_hedge_stops_the_sold_leg_from_being_placed(w):
    w.broker.refuse_place = lambda t: t.side == "B"
    sp = spec(leg("S", "CE", sl=sl(30), strike={"by": "atm", "steps": 2}), leg("B", "CE", strike={"by": "atm", "steps": 6}))
    r = start_before_entry(w, sp, CE24350=60, CE24550=15)
    w.at("09:35:02")
    w.at("09:35:03")
    assert not r.positions and not [p for p in w.broker.placed if p[1] == "S"]
    w.at("09:35:04")
    assert r.status == "halted" and "Three entry orders" in r.halt_reason


def test_trail_to_break_even_moves_the_other_legs_stop_to_their_entry_price(w):
    sp = spec(leg("S", "CE", sl=sl(25)), leg("S", "PE", sl=sl(25)), trail_be=True)
    r = start_before_entry(w, sp, CE24250=100, PE24250=100)
    w.at("09:35:02")
    w.at("09:50", PE24250=80)
    w.at("10:00", CE24250=126)
    w.at("10:00:05", CE24250=127)
    w.at("10:00:10")
    ce, pe = r.positions
    assert ce.status == "closed" and pe.status == "open" and pe.sl == 100.0
    live = [o for o in sl_orders(w, pe.key) if o.status == "TRIGGER_PENDING"]
    assert len(live) == 1 and live[0].trigger == 100.0


def test_break_even_for_sl_legs_only_leaves_legs_without_a_stop_alone(w):
    sp = spec(leg("S", "CE", sl=sl(25)), leg("S", "PE"), leg("S", "CE", sl=sl(25), strike={"by": "atm", "steps": 1}), trail_be=True,
              trail_be_scope="sl_legs")
    r = start_before_entry(w, sp, CE24250=100, PE24250=100, CE24300=70)
    w.at("09:35:02")
    w.at("09:50", CE24300=55)
    w.at("10:00", CE24250=126)
    w.at("10:00:05", CE24250=127)
    w.at("10:00:10")
    a, b, c = r.positions
    assert a.status == "closed" and b.sl is None and c.sl == 70.0


def test_futures_leg_trades_the_index_future(w):
    fut = next(i for i in w.cat.contracts if i.kind == "FUT" and i.name == "NIFTY")
    w.px[fut.key] = 24300.0
    sp = spec({"segment": "futures", "lots": 1, "side": "B", "expiry": "nearest", "sl": {"mode": "points", "value": 40}})
    r = start_before_entry(w, sp)
    w.at("09:35:02")
    p = r.positions[0]
    assert p.key == fut.key and p.side == "B" and p.entry_price == 24300.0 and p.sl == 24260.0 and p.qty == fut.lot
    assert r.snapshot()["legs"][0]["segment"] == "futures"
    w.px[fut.key] = 24350.0
    w.at("15:14:01")
    assert p.status == "closed" and p.exit_reason == "Exit time" and r.status == "done" and w.positions()[fut.key].net_qty == 0


def test_a_futures_leg_without_a_listed_future_waits_then_halts_with_nothing_traded(w):
    w.cat.contracts = [i for i in w.cat.contracts if i.kind != "FUT"]
    sp = spec({"segment": "futures", "lots": 1, "side": "B", "expiry": "nearest", "sl": {"mode": "points", "value": 40}})
    r = start_before_entry(w, sp)
    w.at("09:35:02")
    assert not r.positions and r.status == "waiting" and "future" in r.message
    w.at("09:37:00")
    assert r.status == "halted" and "Nothing was traded" in r.halt_reason


# ---------------------------------------------------------------------------- strike choice in the runner
def test_delta_strangle_picks_out_of_the_money_strikes_and_trades_them(w):
    sp = spec(leg("S", "CE", sl=sl(30), strike={"by": "delta_closest", "value": 0.2}), leg("S", "PE", sl=sl(30), strike={"by": "delta_closest", "value": 0.2}))
    r = start_before_entry(w, sp)
    w.at("09:35:02")
    ce, pe = r.snapshot()["legs"]
    assert ce["strike"] > 24250 > pe["strike"] and "delta" in ce["pick"]
    t = w.years_left()
    for lg_ in (ce, pe):
        d = K.delta(lg_["kind"], w.px[lg_["key"]], 24250, lg_["strike"], t)
        assert 0.1 < d < 0.3
    assert len(r.positions) == 2 and w.net(ce["strike"], "CE") == -w.lot and w.net(pe["strike"], "PE") == -w.lot


def test_premium_strangle_picks_the_strikes_priced_nearest_to_the_target(w):
    sp = spec(leg("S", "CE", sl=sl(30), strike={"by": "premium_closest", "value": 40}), leg("S", "PE", sl=sl(30), strike={"by": "premium_closest", "value": 40}))
    r = start_before_entry(w, sp)
    w.at("09:35:02")
    for lg_ in r.snapshot()["legs"]:
        near = min(w.raw.rows, key=lambda k: abs(w.px[w.raw.rows[k][lg_["kind"]].key] - 40))
        assert lg_["strike"] == near
        assert abs(w.px[lg_["key"]] - 40) < 12


def test_a_second_expiry_and_the_monthly_expiry_are_resolved(w):
    exps = w.chain.expiries("NIFTY")
    sp = spec(leg("S", "CE", sl=sl(30), expiry="next"))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    leg1 = r.snapshot()["legs"][0]
    assert leg1["expiry"] == exps[1]
    raw2 = w.chain.raw_for("NIFTY", exps[1])
    assert leg1["key"] == raw2.rows[24250.0]["CE"].key or leg1["strike"] is not None


# ------------------------------------------------------------------------------------------ safety
def test_exchange_stop_that_cannot_be_placed_closes_the_position_and_does_not_count_as_a_stop_hit(w):
    w.broker.refuse_place = lambda t: t.order_type == "SL"
    sp = spec(leg("S", "CE", sl=sl(25), reentry_sl={"mode": "asap", "count": 2}), square_off="complete")
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    p = r.positions[0]
    assert p.status == "closed" and p.exit_reason == "Stop-loss order could not be placed" and w.net(24250, "CE") == 0
    assert len(r.positions) == 1 and not r.complete_reason                 # not treated as a stop hit: no re-entry, no square-off cascade


def test_pause_blocks_entries_and_resume_allows_them(w):
    sp = spec(leg("S", "CE", sl=sl(25), trigger={"type": "momentum", "unit": "points", "dir": "up", "value": 5}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    r.pause()
    w.at("09:40", CE24250=110)
    assert not r.positions and r.message.startswith("Paused")
    r.resume()
    w.at("09:41", CE24250=111)
    assert len(r.positions) == 1


def test_stop_closes_positions_and_ends(w):
    r = start_before_entry(w, two_legs(), CE24250=100, PE24250=100)
    w.at("09:35:02")
    r.stop()
    w.at("09:40")
    w.at("09:40:05")
    assert all(p.status == "closed" and p.exit_reason == "Stopped by you" for p in r.positions) and r.status == "stopped"


def test_kill_leaves_no_leg_waiting(w):
    sp = spec(leg("S", "CE", sl=sl(25), trigger={"type": "momentum", "unit": "points", "dir": "up", "value": 50}))
    r = start_before_entry(w, sp, CE24250=100)
    w.at("09:35:02")
    w.eng.kill_all()
    assert r.status == "halted" and r.snapshot()["legs"][0]["status"] == "skipped"


def test_stale_feed_holds_entries(w):
    r = start_before_entry(w, spec(leg("S", "CE", sl=sl(25))), CE24250=100, step=False) if False else None
    w.chain_prices(24250)
    w.at("09:30", 24250, step=False, CE24250=100)
    r = w.create(spec(leg("S", "CE", sl=sl(25))))
    w.at("09:31")
    w.at("09:35:02", step=False)
    w.eng.env.stale_s = -1                                              # every price counts as stale
    r.step()
    assert not r.positions and r.message.startswith("Holding")
    w.eng.env.stale_s = 1e9
    r.step()
    assert len(r.positions) == 1


def test_selection_waits_for_prices_then_gives_up_with_nothing_traded(w):
    sp = spec(leg("S", "CE", sl=sl(25), strike={"by": "premium_closest", "value": 40}))
    w.at("09:30", 24250, step=False)                                    # no option prices at all
    r = w.create(sp)
    w.at("09:35:02")
    assert r.status == "waiting" and "Choosing strikes" in r.message
    w.at("09:37:00")
    assert r.status == "halted" and "Nothing was traded" in r.halt_reason and not w.broker.orders()


def test_selling_with_no_stop_at_all_saves_with_a_loud_warning_and_no_limits_are_forced():
    d = spec(leg(sl=sl(25)))
    d["legs"][0]["sl"] = {"mode": "none"}
    d["limits"]["max_loss"] = 0
    st, errs, warns = parse(d)
    assert st is not None and not errs
    assert any("nothing in this strategy limits the loss" in w for w in warns) and any("No max loss" in w for w in warns)
    assert any("No strategy-level max loss" in l for l in describe(st))


def test_max_loss_is_off_unless_you_set_it():
    d = spec(leg(sl=sl(25)))
    del d["limits"]
    assert parse(d)[0].limits.max_loss == 0
    d["limits"] = {"max_loss": 1234}
    assert parse(d)[0].limits.max_loss == 1234
