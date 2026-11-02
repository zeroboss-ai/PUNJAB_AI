from datetime import date, datetime, timedelta

import pytest

from terminal.brokers.paper import PaperOrders
from terminal.candles import CandleStore
from terminal.clock import IST, round_tick
from terminal.config import load_config
from terminal.models import Contract, OrderError, OrderRequest, Tick
from terminal.risk import RiskGuard
from conftest import ROOT


def ts(h, m, s=0):
    return datetime(2026, 10, 26, h, m, s, tzinfo=IST)


def test_candle_store_high_low_window():
    s = CandleStore()
    for sec, p in [(0, 100), (30, 110), (59, 105)]:
        s.on_tick("X", p, ts(9, 15, sec))
    s.on_tick("X", 90, ts(9, 16, 1))
    s.on_tick("X", 200, ts(9, 19, 0))               # outside [09:15, 09:19)
    assert s.high_low("X", ts(9, 15), ts(9, 19)) == (110, 90)
    assert s.candle("X", ts(9, 16)).h == 90
    assert s.high_low("NOPE", ts(9, 15), ts(9, 19)) is None


def test_risk_sizing_and_limits():
    r = RiskGuard(5000, max_lots=3)
    assert r.lots_for(11.0, 75, desired_lots=1) == 1
    assert r.lots_for(11.0, 75, desired_lots=10) == 3          # capped by max_lots
    assert r.lots_for(100.0, 75, desired_lots=1) == 0          # 7,500 risk > 5,000 budget
    r.realised = -4500
    assert r.budget_left() == 500
    assert r.lots_for(11.0, 75, 1) == 0
    assert RiskGuard(5000, 1).breached(-5000) and not RiskGuard(5000, 1).breached(-4999)
    assert RiskGuard.open_pnl(100, 90, 75) == 750               # short: price fell -> profit


def test_round_tick():
    assert round_tick(94.953) == 94.95 and round_tick(93.97) == 93.95


C = Contract("NIFTY25000PE", "t", "nse_fo", 25000, "PE", date(2026, 10, 27), 75)


def test_paper_sell_limit_fills_at_limit_and_tracks_position():
    p = PaperOrders()
    p.on_tick(Tick(C.symbol, 95.0, ts(9, 20)))
    oid = p.place(OrderRequest(C, "S", 75, "L", 94.0))
    st = p.status(oid)
    assert st.state == "COMPLETE" and st.avg_price == 94.0 and st.filled_qty == 75
    assert p.positions()[C.symbol] == -75


def test_paper_sell_limit_rests_until_price_reaches_it():
    p = PaperOrders()
    p.on_tick(Tick(C.symbol, 95.0, ts(9, 20)))
    oid = p.place(OrderRequest(C, "S", 75, "L", 96.0))          # above market -> rests
    assert p.status(oid).state == "OPEN"
    p.on_tick(Tick(C.symbol, 96.5, ts(9, 20, 1)))
    assert p.status(oid).state == "COMPLETE"


def test_paper_stop_limit_triggers_and_gap_through_limit_does_not_fill():
    p = PaperOrders()
    p.on_tick(Tick(C.symbol, 95.0, ts(9, 20)))
    ok = p.place(OrderRequest(C, "B", 75, "SL", price=110.0, trigger=105.0))
    gap = p.place(OrderRequest(C, "B", 75, "SL", price=110.0, trigger=105.0))
    assert p.status(ok).state == "TRIGGER_PENDING"
    p.on_tick(Tick(C.symbol, 104.0, ts(9, 21)))
    assert p.status(ok).state == "TRIGGER_PENDING"
    p.on_tick(Tick(C.symbol, 120.0, ts(9, 22)))                  # gaps straight through the limit
    assert p.status(ok).state == "OPEN" and p.status(gap).state == "OPEN"
    p.on_tick(Tick(C.symbol, 109.0, ts(9, 23)))
    assert p.status(ok).state == "COMPLETE" and p.status(ok).avg_price == 109.0


def test_paper_cancel_and_bad_qty():
    p = PaperOrders()
    oid = p.place(OrderRequest(C, "B", 75, "SL", price=110.0, trigger=105.0))
    p.cancel(oid)
    assert p.status(oid).state == "CANCELLED"
    with pytest.raises(OrderError):
        p.place(OrderRequest(C, "S", 0, "L", 90.0))


def test_config_rejects_typos():
    import yaml, tempfile, pathlib
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())
    raw["risk"]["max_dialy_loss"] = 1
    f = pathlib.Path(tempfile.mkdtemp()) / "c.yaml"
    f.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="Unknown key"):
        load_config(f)


def test_no_daily_loss_limit_means_nothing_is_stopped_or_sized_by_it():
    from terminal.risk import RiskGuard
    g = RiskGuard(0, 3)
    g.realised = -1_000_000
    assert not g.limited and not g.breached(-5_000_000)
    assert g.budget_left() == float("inf")
    assert g.lots_for(10.0, 75, 5) == 3                    # capped by max_lots only
    h = RiskGuard(5000, 3)
    h.realised = -4900
    assert h.limited and h.lots_for(10.0, 75, 5) == 0 and h.breached(-200)


def test_config_accepts_zero_daily_loss_but_not_negative(tmp_path):
    import yaml
    from conftest import ROOT
    from terminal.config import load_config
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())
    assert raw["risk"]["max_daily_loss"] == 0
    assert load_config(ROOT / "config.yaml").risk.max_daily_loss == 0
    raw["risk"]["max_daily_loss"] = -1
    f = tmp_path / "c.yaml"
    f.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        load_config(f)
