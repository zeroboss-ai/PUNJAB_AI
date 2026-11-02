import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from terminal.brokers.paper import PaperOrders
from terminal.brokers.replay import ReplayFeed, SyntheticInstruments
from terminal.config import load_config
from terminal.engine import Engine
from terminal.sim import build_day

DAY = date(2026, 10, 26)
ROOT = Path(__file__).resolve().parents[1]


def run_day(scenario: str, tmp_path, orders=None, **overrides) -> Engine:
    """Replay a synthetic day through the full engine (paper orders unless `orders=` is given).
    Overrides: `section__key=value` (e.g. risk__max_daily_loss=500) or a top-level `key=value`."""
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode = "replay"
    cfg.data_dir = str(tmp_path / "data")
    cfg.strategy.strike_window = 2
    cfg.risk.unclear_wait_s = 0.0                      # no real sleeping in the "broker reply unclear" paths
    for k, v in overrides.items():
        if "__" in k:
            section, key = k.split("__")
            setattr(getattr(cfg, section), key, v)
        else:
            setattr(cfg, k, v)
    eng = Engine(cfg, SyntheticInstruments(cfg, DAY), ReplayFeed(build_day(DAY, scenario)), orders or PaperOrders())
    eng.start()
    assert eng.finished.wait(120), "engine did not finish"
    return eng


@pytest.fixture
def day():
    return run_day


@pytest.fixture(autouse=True)
def _no_real_ip_lookup(monkeypatch):
    """No test may call a real 'what is my IP' service. Tests that need an answer replace terminal.netip.http_fetch."""
    import terminal.netip as netip
    monkeypatch.setattr(netip, "http_fetch", lambda fam: ("", "offline (test)"))
