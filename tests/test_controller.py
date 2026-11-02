import pytest

from conftest import ROOT
from terminal.config import load_config
from terminal.controller import Controller


class Sess:
    logged_in = True
    my_ip = "203.0.113.7"


def test_start_refused_when_kotak_sees_a_different_ip_than_config():
    cfg = load_config(ROOT / "config.yaml")
    cfg.static_ip = "198.51.100.9"
    c = Controller(cfg, lambda: pytest.fail("engine must not be built"), Sess())
    with pytest.raises(RuntimeError, match="203.0.113.7.*198.51.100.9"):
        c.start()
    assert c.snapshot()["my_ip"] == "203.0.113.7"


def test_matching_or_unset_static_ip_is_allowed():
    cfg = load_config(ROOT / "config.yaml")
    built = []

    class Eng:
        finished = type("E", (), {"is_set": staticmethod(lambda: False)})()
        def start(self): built.append(1)

    for want in ("", "203.0.113.7"):
        cfg.static_ip = want
        c = Controller(cfg, Eng, Sess())
        c.start()
    assert built == [1, 1]
