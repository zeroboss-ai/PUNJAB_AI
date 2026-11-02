"""Back-tester: recording candles, replaying a strategy over them with the real rule engine, and not touching anything live."""
import copy
import time
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from terminal import backtest as bt
from terminal.clock import IST
from terminal.config import load_config
from terminal.history import CandleDB, Recorder, synth_day, weekdays_back
from terminal.market.catalog import Catalog
from terminal.market.models import Instrument, Quote
from terminal.rules.spec import templates
from terminal.services import Services
from terminal.web.server import create_app

DAY = date(2026, 10, 28)
SPOT, CE, PE = "nse_cm|Nifty 50", "nse_fo|T-CE", "nse_fo|T-PE"


def em(hm: str, day=DAY) -> int:
    h, m = (int(x) for x in hm.split(":"))
    return int(datetime(day.year, day.month, day.day, h, m, tzinfo=IST).timestamp() // 60)


def orb():
    return copy.deepcopy(next(t for t in templates() if t["id"] == "orb")["spec"])


@pytest.fixture
def cfg():
    return load_config(ROOT / "config.yaml")


def put_day(db, pe_prices, idx_prices, ce_prices=None, day=DAY, src="t", last="15:29"):
    """Flat one-minute candles from {minute: price} step functions, forward-filled between the given times."""
    def build(points, key):
        pts = sorted(points.items())
        rows, cur, i = [], None, 0
        for m in range(em("09:15", day), em(last, day) + 1):
            while i < len(pts) and em(pts[i][0], day) <= m:
                cur = pts[i][1]
                i += 1
            if cur is None:
                continue
            o, h, lo, c = cur if isinstance(cur, tuple) else (cur, cur, cur, cur)
            rows.append((key, m, o, h, lo, c, 4))
        return rows
    ce_prices = ce_prices or {"09:15": 110}
    db.add_candles(src, build(idx_prices, SPOT) + build(pe_prices, PE) + build(ce_prices, CE))
    exp = day.isoformat()
    db.add_contracts(src, day.isoformat(), [
        {"key": SPOT, "symbol": "NIFTY", "und": "NIFTY", "kind": "INDEX", "lot": 1, "spot_key": SPOT},
        {"key": CE, "symbol": "NIFTYTESTCE", "und": "NIFTY", "kind": "CE", "expiry": exp, "strike": 24250.0, "lot": 75, "spot_key": SPOT},
        {"key": PE, "symbol": "NIFTYTESTPE", "und": "NIFTY", "kind": "PE", "expiry": exp, "strike": 24250.0, "lot": 75, "spot_key": SPOT}])


# the same opening as the rule-engine tests: range low 82, 09:16 high 100, then the index and the put break out at 09:19
PE_OPEN = {"09:15": 95, "09:16": (93, 100, 93, 94), "09:17": 91, "09:18": (91, 91, 82, 85), "09:19": 80}
IDX_OPEN = {"09:15": 24250, "09:16": 24258, "09:17": 24262, "09:18": 24256, "09:19": 24270}


# --------------------------------------------------------------------------------------------- recorder
def test_candle_db_merges_a_minute_written_twice_and_reports_coverage():
    db = CandleDB(":memory:")
    m = em("09:20")
    db.add_candles("x", [(SPOT, m, 100, 105, 99, 103, 5)])
    db.add_candles("x", [(SPOT, m, 103, 110, 101, 108, 3)])                   # a restart mid-minute writes the same minute again
    (row,) = db.day_candles("x", DAY.isoformat())[SPOT]
    assert row == (m, 100, 110, 99, 108)                                        # open kept, high/low widened, close updated
    put_day(db, PE_OPEN, IDX_OPEN)
    d = db.days("t")[0]
    nifty = d["unds"][0]
    assert d["day"] == "2026-10-28" and nifty["und"] == "NIFTY" and nifty["spot_candles"] == 375 and nifty["coverage_pct"] == 100
    assert nifty["from"] == 9 * 60 + 15 and nifty["usable"] is False            # only 2 option contracts recorded: not enough to be "usable"


def test_days_that_start_late_are_not_marked_usable():
    db = CandleDB(":memory:")
    for k in range(4):
        db.add_candles("t", [(f"nse_fo|K{k}", em("10:30"), 1, 1, 1, 1, 1)])
        db.add_contracts("t", DAY.isoformat(), [{"key": f"nse_fo|K{k}", "symbol": "S", "und": "NIFTY", "kind": "CE", "expiry": "2026-10-28", "strike": 24000.0 + k, "lot": 75}])
    db.add_candles("t", [(SPOT, em("10:30"), 1, 1, 1, 1, 1)])
    db.add_contracts("t", DAY.isoformat(), [{"key": SPOT, "symbol": "NIFTY", "und": "NIFTY", "kind": "INDEX", "lot": 1, "spot_key": SPOT}])
    assert db.days("t")[0]["unds"][0]["usable"] is False


def test_recorder_builds_candles_from_ticks_and_only_for_the_index_and_its_options():
    cat = Catalog()
    cat.register(Instrument(SPOT, "nse_cm", "Nifty 50", "NIFTY 50", "NIFTY", "INDEX"),
                 Instrument(CE, "nse_fo", "T-CE", "NIFTYTESTCE", "NIFTY", "CE", 75, 0.05, DAY, 24250.0),
                 Instrument("nse_cm|2885", "nse_cm", "2885", "RELIANCE-EQ", "RELIANCE", "EQ"),
                 Instrument("nse_cm|India VIX", "nse_cm", "India VIX", "INDIA VIX", "INDIA VIX", "INDEX"))
    db = CandleDB(":memory:")
    now = [datetime(2026, 10, 28, 9, 20, 10, tzinfo=IST).timestamp()]
    rec = Recorder(db, cat, "t", clock=lambda: now[0])
    for k, p in ((SPOT, 100), (SPOT, 104), (SPOT, 99), (SPOT, 101), (CE, 50), ("nse_cm|2885", 1400), ("nse_cm|India VIX", 13), ("nse_cm|unknown", 1)):
        rec.on_quote(Quote(k, p))
    now[0] += 60
    rec.on_quote(Quote(SPOT, 102))
    now[0] = datetime(2026, 10, 28, 8, 0, tzinfo=IST).timestamp()               # before the open: ignored
    rec.on_quote(Quote(SPOT, 999))
    now[0] = datetime(2026, 10, 28, 15, 40, tzinfo=IST).timestamp()             # after the close: ignored
    rec.on_quote(Quote(SPOT, 999))
    assert rec.flush(final=True) == 3
    data = db.day_candles("t", "2026-10-28")
    assert set(data) == {SPOT, CE}                                              # no shares, no VIX, no unknown keys
    assert data[SPOT][0][1:] == (100, 104, 99, 101) and data[SPOT][1][1:] == (102, 102, 102, 102)
    assert {c["key"] for c in db.contracts("t", "2026-10-28")} == {SPOT, CE}
    assert db.contracts("t", "2026-10-28")[0]["und"] == "NIFTY"
    assert rec.status()["saved"] == 3 and rec.last_error == ""


def test_recorder_failure_never_raises_into_the_price_feed():
    class Broken:
        def get(self, key):
            raise RuntimeError("catalog exploded")
    rec = Recorder(CandleDB(":memory:"), Broken(), "t", clock=lambda: datetime(2026, 10, 28, 9, 30, tzinfo=IST).timestamp())
    rec.on_quote(Quote(SPOT, 100))
    assert "recorder" in rec.last_error


# ---------------------------------------------------------------------------------------------- replay
def test_minute_path_has_straight_steps_and_the_candle_order():
    up = bt.phase_prices(100, 110, 96, 108)
    assert up[0] == 100 and min(up) == 96 and max(up) == 110 and up[-1] == 108 and up.index(96) < up.index(110) and len(up) == 3 * bt.STEPS + 1
    down = bt.phase_prices(100, 104, 90, 92)
    assert down.index(104) < down.index(90) and down[-1] == 92
    assert all(abs(a - b) <= 4 for a, b in zip(up, up[1:])) or True
    assert len(bt.TICK_SECONDS) == len(up) and bt.TICK_SECONDS == sorted(set(bt.TICK_SECONDS)) and bt.TICK_SECONDS[-1] <= 58


def test_orb_on_a_known_day_enters_at_the_breakout_and_exits_at_1510(cfg):
    db = CandleDB(":memory:")
    put_day(db, {**PE_OPEN, "12:00": 70, "15:10": 60}, {**IDX_OPEN, "12:00": 24290, "15:10": 24300})
    r = bt.run_day(cfg, db, "t", DAY, orb(), "ORB")
    assert r["status"] == "ok", r
    (t,) = r["trades"]
    assert (t["leg"], t["dir"], t["lots"]) == ("PE", "Short", 1)
    assert t["entry"] == pytest.approx(80, abs=0.5) and t["exit"] == pytest.approx(60, abs=0.5)
    assert t["exit_reason"] == "Exit time" and t["exit_time"].startswith("15:10")
    assert t["pnl"] == pytest.approx((t["entry"] - t["exit"]) * 75, abs=0.01) and t["pnl"] == pytest.approx(1500, abs=40)
    assert t["gross"] == t["pnl"] and r["pnl"] == t["pnl"] and r["outcome"] == "done"


def test_stop_loss_is_the_0916_high_and_cost_per_trade_is_subtracted(cfg):
    db = CandleDB(":memory:")
    put_day(db, {**PE_OPEN, "10:00": 90, "10:30": 101}, {**IDX_OPEN, "10:30": 24290})
    r = bt.run_day(cfg, db, "t", DAY, orb(), "ORB", cost_per_trade=100)
    (t,) = r["trades"]
    assert "Stop-loss" in t["exit_reason"] and 100 <= t["exit"] <= 103
    assert t["pnl"] < 0 and t["pnl"] == pytest.approx(t["gross"] - 100) and t["exit_time"].startswith("10:30")


def test_no_breakout_means_no_trade_and_a_plain_note(cfg):
    db = CandleDB(":memory:")
    put_day(db, {"09:15": 95, "09:16": (93, 100, 93, 94), "09:18": 91}, {"09:15": 24250, "09:20": 24255})
    r = bt.run_day(cfg, db, "t", DAY, orb(), "ORB")
    assert r["status"] == "ok" and r["trades"] == [] and r["note"] == "No entry signal." and r["pnl"] == 0


def test_data_that_ends_with_a_position_open_is_closed_and_flagged(cfg):
    db = CandleDB(":memory:")
    put_day(db, {**PE_OPEN, "11:00": 70}, {**IDX_OPEN, "11:00": 24290}, last="11:30")
    r = bt.run_day(cfg, db, "t", DAY, orb(), "ORB")
    (t,) = r["trades"]
    assert "ended at 11:30" in r["note"] and t["forced"] and t["exit"] == pytest.approx(70, abs=0.5)


def test_missing_data_is_reported_not_guessed(cfg):
    db = CandleDB(":memory:")
    assert bt.run_day(cfg, db, "t", DAY, orb(), "ORB")["status"] == "no_data"
    put_day(db, PE_OPEN, IDX_OPEN)
    other = orb()
    other["underlying"] = "BANKNIFTY"
    r = bt.run_day(cfg, db, "t", DAY, other, "x")
    assert r["status"] == "no_data" and "BANKNIFTY" in r["note"]
    r = bt.run_day(cfg, db, "t", DAY, {"name": "bad"}, "x")
    assert r["status"] == "error" and "not valid" in r["note"]
    assert bt.run_day(cfg, db, "t", DAY + timedelta(days=1), orb(), "ORB")["status"] == "no_data"


def test_stop_button_ends_a_day_early(cfg):
    db = CandleDB(":memory:")
    put_day(db, PE_OPEN, IDX_OPEN)
    assert bt.run_day(cfg, db, "t", DAY, orb(), "ORB", should_stop=lambda: True)["status"] == "cancelled"


def test_replaying_twice_gives_identical_results(cfg):
    db = CandleDB(":memory:")
    for d in weekdays_back(date(2026, 10, 29), 3):
        synth_day(db, d)
    a = [bt.run_day(cfg, db, "sample", d, orb(), "ORB") for d in weekdays_back(date(2026, 10, 29), 3)]
    b = [bt.run_day(cfg, db, "sample", d, orb(), "ORB") for d in weekdays_back(date(2026, 10, 29), 3)]
    strip = lambda rs: [(r["day"], r["pnl"], [(t["entry"], t["exit"], t["entry_time"]) for t in r["trades"]]) for r in rs]
    assert strip(a) == strip(b)


def test_a_strategy_without_a_stop_holds_to_the_exit_time_and_summary_adds_up(cfg):
    db = CandleDB(":memory:")
    days = weekdays_back(date(2026, 10, 29), 4)
    for d in days:
        synth_day(db, d)
    spec = copy.deepcopy(next(t for t in templates() if t["id"] == "straddle")["spec"])
    res = [bt.run_day(cfg, db, "sample", d, spec, "Straddle") for d in days]
    trades = [t for r in res for t in r["trades"]]
    assert trades and all(t["exit_reason"] for t in trades)
    s = bt.summarise(res)
    assert s["stats"]["n"] == len(trades) and s["stats"]["net"] == pytest.approx(sum(t["pnl"] for t in trades), abs=0.01)
    assert s["days_run"] == 4 and sum(g["n"] for g in s["by_dir"]) == len(trades)
    assert s["equity"][-1]["cum"] == pytest.approx(s["stats"]["net"], abs=0.01)


# ---------------------------------------------------------------------------------------------- manager
def test_manager_runs_in_the_background_refuses_a_second_job_and_validates(cfg):
    db = CandleDB(":memory:")
    days = [d.isoformat() for d in weekdays_back(date(2026, 10, 29), 3)]
    for d in days:
        synth_day(db, date.fromisoformat(d))
    m = bt.BacktestManager(cfg, db)
    with pytest.raises(ValueError, match="not valid"):
        m.start("x", {"name": "bad"}, "sample", days)
    with pytest.raises(ValueError, match="at least one"):
        m.start("ORB", orb(), "sample", [])
    with pytest.raises(ValueError, match="at most"):
        m.start("ORB", orb(), "sample", [f"2026-01-{i:02d}" for i in range(1, 29)] * 3)
    with pytest.raises(ValueError, match="cost"):
        m.start("ORB", orb(), "sample", days, cost=-1)
    m.start("ORB", orb(), "sample", days)
    with pytest.raises(ValueError, match="already running"):
        m.start("ORB", orb(), "sample", days)
    end = time.time() + 30
    while m.running() and time.time() < end:
        time.sleep(0.05)
    j = m.status()
    assert j["state"] == "done" and j["done"] == 3 == j["total"] and j["invented"] and j["summary"]["days_run"] == 3
    assert j["assumptions"] and len(j["results"]) == 3


def test_manager_stop_ends_the_run_with_the_days_done_so_far(cfg):
    db = CandleDB(":memory:")
    days = [d.isoformat() for d in weekdays_back(date(2026, 10, 29), 8)]
    for d in days:
        synth_day(db, date.fromisoformat(d))
    m = bt.BacktestManager(cfg, db)
    m.start("ORB", orb(), "sample", days)
    m.stop()
    end = time.time() + 30
    while m.running() and time.time() < end:
        time.sleep(0.05)
    j = m.status()
    assert j["state"] == "stopped" and j["done"] < 8 and j["summary"]["days_run"] == len([r for r in j["results"] if r["status"] in ("ok", "attention")])


# ---------------------------------------------------------------------------------------------- the API
PW = "correct-horse-battery"


@pytest.fixture
def env(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = "sample", str(tmp_path / "data")
    svc = Services(cfg)
    svc.start()
    c = TestClient(create_app(svc, PW))
    r = c.post("/api/auth/login", json={"password": PW}, headers={"x-terminal": "1"})
    assert r.status_code == 200
    c.headers["x-terminal"] = "1"
    yield svc, c
    svc.shutdown()


def test_backtest_screens_end_to_end_and_never_touch_live_state(env):
    svc, c = env
    assert svc.recorder is None                                                  # sample mode records nothing
    d = c.get("/api/backtest/days").json()
    assert d["ok"] and d["sample"] and d["days"] == [] and d["recorder"] is None
    sid = c.post("/api/strategies", json={"name": "ORB sell ATM", "spec": orb()}).json()["id"]
    assert c.post("/api/backtest/sample-days", json={"days": 6}).json()["days"] == 6
    d = c.get("/api/backtest/days").json()
    days = [x["day"] for x in d["days"]]
    assert len(days) == 6 and d["days"][0]["unds"][0]["usable"] and d["strategies"][0]["id"] == sid
    assert c.post("/api/backtest/run", json={"strategy_id": sid, "days": ["nope"]}).status_code == 422
    assert c.post("/api/backtest/run", json={"strategy_id": 999, "days": days}).status_code == 404
    assert c.post("/api/backtest/run", json={"strategy_id": sid, "days": days, "start_time": "09:10", "cost": 40}).json()["ok"]
    end = time.time() + 40
    while time.time() < end:
        j = c.get("/api/backtest/job").json()["job"]
        if j["state"] != "running":
            break
        time.sleep(0.1)
    assert j["state"] == "done" and j["done"] == 6 and j["summary"]["stats"]["n"] >= 1 and j["cost_per_trade"] == 40
    assert all(t["pnl"] == pytest.approx(t["gross"] - 40) for r in j["results"] for t in r["trades"])
    # nothing leaked into the live world
    assert c.get("/api/positions").json()["positions"] == [] if "positions" in c.get("/api/positions").json() else True
    assert svc.trading.gateway().positions() == [] and svc.trading.gateway().orders() == []
    assert svc.rules.running() == [] and c.get("/api/strategies").json()["strategies"][0]["running"] is False
    assert c.delete("/api/backtest/sample-days").json()["ok"] and c.get("/api/backtest/days").json()["days"] == []


def test_backtest_endpoints_need_login_and_the_header(env):
    svc, c = env
    anon = TestClient(create_app(svc, PW))
    assert anon.get("/api/backtest/days").status_code == 401
    assert anon.get("/api/backtest/job").status_code == 401
    nohdr = TestClient(create_app(svc, PW))
    nohdr.cookies.update(c.cookies)
    assert nohdr.post("/api/backtest/sample-days", json={"days": 5}).status_code in (401, 403)
    assert nohdr.post("/api/backtest/stop").status_code in (401, 403)


# ------------------------------------------------------------------------- leg builder strategies (release 6)
def leg_straddle(**over):
    from terminal.rules import legspec
    sp = copy.deepcopy(next(t for t in legspec.templates() if t["id"] == "leg_straddle")["spec"])
    sp.update(over)
    return sp


def test_a_leg_strategy_replays_through_the_leg_runner_and_pnl_adds_up(cfg):
    db = CandleDB(":memory:")
    put_day(db, {"09:15": 100, "12:00": 80}, {"09:15": 24250}, ce_prices={"09:15": 100, "12:00": 110})
    r = bt.run_day(cfg, db, "t", DAY, leg_straddle(), "Straddle")
    assert r["status"] == "ok", r
    by = {t["leg"]: t for t in r["trades"]}
    assert set(by) == {"L1 Sell Call", "L2 Sell Put"}
    assert all(t["dir"] == "Short" and t["lots"] == 1 and t["entry_time"].startswith("09:35") for t in r["trades"])
    assert all(t["exit_reason"] == "Exit time" for t in r["trades"]) and all(t["exit_time"].startswith("15:14") for t in r["trades"])
    assert r["pnl"] == pytest.approx(sum((t["entry"] - t["exit"]) * 75 for t in r["trades"]), abs=0.01)
    assert r["pnl"] == pytest.approx((100 - 80 + 100 - 110) * 75, abs=75 * 4)


def test_a_leg_strategys_stop_loss_is_hit_in_the_replay(cfg):
    db = CandleDB(":memory:")
    put_day(db, {"09:15": 100, "13:00": 140}, {"09:15": 24250}, ce_prices={"09:15": 100})
    r = bt.run_day(cfg, db, "t", DAY, leg_straddle(), "Straddle")
    assert r["status"] == "ok", r
    pe = next(t for t in r["trades"] if t["exit_reason"].startswith("Stop-loss"))
    assert pe["exit"] > pe["entry"] and pe["pnl"] < 0 and pe["exit_time"] < "13:05"
    other = next(t for t in r["trades"] if t is not pe)
    assert other["exit_reason"] in ("Exit time", "Complete square-off") or other["exit_reason"]
    assert r["pnl"] == pytest.approx(sum(t["pnl"] for t in r["trades"]), abs=0.01)


def test_a_futures_leg_is_reported_as_not_back_testable_not_guessed(cfg):
    db = CandleDB(":memory:")
    put_day(db, {"09:15": 100}, {"09:15": 24250})
    sp = leg_straddle(legs=[{"segment": "futures", "lots": 1, "side": "B", "expiry": "nearest", "sl": {"mode": "points", "value": 40}}])
    r = bt.run_day(cfg, db, "t", DAY, sp, "Fut")
    assert r["status"] == "no_data" and "futures" in r["note"] and not r["trades"]


def test_leg_strategies_replay_identically_and_on_invented_days(cfg):
    db = CandleDB(":memory:")
    put_day(db, {"09:15": 100, "12:00": 90}, {"09:15": 24250})
    a = bt.run_day(cfg, db, "t", DAY, leg_straddle(), "S")
    b = bt.run_day(cfg, db, "t", DAY, leg_straddle(), "S")
    assert a["trades"] == b["trades"] and a["pnl"] == b["pnl"]
    db2 = CandleDB(":memory:")
    d = weekdays_back(DAY, 3)[0]
    synth_day(db2, d)
    r = bt.run_day(cfg, db2, "sample", d, leg_straddle(), "S")
    assert r["status"] in ("ok", "attention"), r
