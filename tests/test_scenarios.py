"""End-to-end: whole engine + strategy + paper orders on synthetic days with known outcomes."""
from terminal.strategy import State


def test_put_sell_held_to_1510_profit(day, tmp_path):
    e = day("put_sell", tmp_path)
    t = e.strategy.trades
    assert len(t) == 1
    tr = t[0]
    assert tr["side"] == "PE" and tr["symbol"] == "NIFTY25000PE"
    assert tr["sl"] == 105.0                       # high of the 09:16 PUT candle
    assert tr["reason"] == "TIME"
    assert tr["entry_ts"] < tr["exit_ts"] and tr["exit_ts"].endswith("+05:30")
    assert tr["exit_ts"][11:16] == "15:10"
    assert tr["pnl"] > 0
    assert tr["qty"] == 75
    assert e.strategy.state == State.DONE
    assert not e.manual_action_required


def test_put_sl_hit_loss_is_bounded(day, tmp_path):
    e = day("put_sl", tmp_path)
    tr = e.strategy.trades[0]
    assert tr["side"] == "PE" and tr["reason"] in ("SL", "SL(exchange)")
    assert tr["exit"] >= 105.0 and tr["exit"] <= 110.0      # filled at/just above the trigger
    assert tr["pnl"] < 0 and tr["pnl"] > -5000
    assert len(e.strategy.trades) == 1                      # max 1 trade per day


def test_call_sell_mirror_image(day, tmp_path):
    e = day("call_sell", tmp_path)
    tr = e.strategy.trades[0]
    assert tr["side"] == "CE" and tr["symbol"] == "NIFTY25000CE"
    assert tr["sl"] == 100.0                       # high of the 09:16 CALL candle (not the 4-candle high of 105)
    assert tr["reason"] == "TIME" and tr["pnl"] > 0


def test_call_sl_hit(day, tmp_path):
    e = day("call_sl", tmp_path)
    tr = e.strategy.trades[0]
    assert tr["side"] == "CE" and tr["reason"] in ("SL", "SL(exchange)") and tr["pnl"] < 0


def test_no_trade_when_index_stays_inside_range(day, tmp_path):
    e = day("inside", tmp_path)
    assert e.strategy.trades == []
    assert e.strategy.state == State.DONE


def test_both_conditions_required(day, tmp_path):
    """Index breaks its high, but the put does not break its low -> must NOT sell."""
    e = day("unconfirmed", tmp_path)
    assert e.strategy.trades == []


def test_trade_skipped_when_risk_exceeds_daily_budget(day, tmp_path):
    # one lot risks ~ (105-94)*75 = ~830; a Rs 500 budget must refuse the trade
    e = day("put_sell", tmp_path, risk__max_daily_loss=500.0)
    assert e.strategy.trades == []
    assert any("skipped" in ev["msg"] for ev in e.events)


def test_range_and_sl_reference_values(day, tmp_path):
    e = day("put_sell", tmp_path)
    r = e.strategy.ranges
    assert r["INDEX"] == (25010.0, 24990.0)
    assert r["PE"] == (105.0, 95.0) and r["CE"] == (105.0, 95.0)
    assert e.strategy.sl_ref == {"CE": 100.0, "PE": 105.0}     # 09:16 candle highs only
    assert e.atm == 25000.0


def test_no_exchange_sl_mode_uses_software_stop(day, tmp_path):
    e = day("put_sl", tmp_path, risk__exchange_sl=False)
    tr = e.strategy.trades[0]
    assert tr["reason"] == "SL" and tr["pnl"] < 0
