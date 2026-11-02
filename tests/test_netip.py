"""Static IP: detecting the public IPv4/IPv6, the two ways of setting it up (auto-detect / own), and the live connect check."""
import json

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from terminal.config import load_config
from terminal.netip import NetIp, NetIpError, covers, norm, parse_ips, parse_public, same_ip, problem_for_config
from terminal.services import Services
from terminal.store import Store
from terminal.web.server import create_app
from test_accounts import PW, acct, login, paper  # noqa: F401  (paper is a fixture)

V4, V6 = "49.36.10.20", "2401:4900:1c2a:77::17"
V4B, V6B = "49.36.10.99", "2401:4900:1c2a:77::99"


def fake(v4=V4, v6=V6, err6="this connection has no IPv6 route"):
    calls = []

    def fetch(fam):
        calls.append(fam)
        ip = v4 if fam == "v4" else v6
        return (ip, "") if ip else ("", err6 if fam == "v6" else "timed out")
    fetch.calls = calls
    return fetch


def mk(fetch=None, clock=None):
    return NetIp(Store(":memory:"), fetch or fake(), clock or (lambda: 1000.0))


# ------------------------------------------------------------------------------------------------ parsing
def test_ipv4_and_ipv6_compare_in_any_spelling():
    assert same_ip("2401:4900:1c2a:77::17", "2401:4900:1C2A:0077:0000:0000:0000:0017")
    assert same_ip("[2401:4900:1c2a:77::17]", V6)
    assert same_ip("::ffff:49.36.10.20", "49.36.10.20")                            # IPv4 shown as IPv6 by some servers
    assert same_ip("fe80::1%eth0", "fe80::1")
    assert not same_ip(V4, V4B) and not same_ip("", "") and not same_ip("nonsense", "nonsense")
    assert norm("49.036.10.20") == "" or norm("49.36.10.20") == "49.36.10.20"      # never raises
    assert covers([V4, V6], V6.upper()) and not covers([V4], V6) and not covers([], V4)


def test_parse_ips_accepts_one_of_each_and_explains_mistakes():
    assert parse_ips(f"{V4}, {V6}") == [V4, V6]
    assert parse_ips(f"  {V6}  ;  {V4}") == [V6, V4]
    assert parse_ips("") == []
    with pytest.raises(NetIpError, match="at most one IPv4"):
        parse_ips(f"{V4} {V4B}")
    with pytest.raises(NetIpError, match="not a valid IP"):
        parse_ips("49.36.10")
    with pytest.raises(NetIpError, match="not a valid IP"):
        parse_ips("hello")


@pytest.mark.parametrize("bad,why", [("192.168.1.5", "private"), ("10.0.0.7", "private"), ("127.0.0.1", "itself"),
                                     ("fe80::1", "link-local"), ("::1", "itself"), ("203.0.113.7", "reserved"), ("fd00::5", "private")])
def test_own_ip_must_be_a_public_internet_address(bad, why):
    with pytest.raises(NetIpError, match=why):
        parse_ips(bad, public_only=True)


def test_detection_answer_must_be_a_public_ip_of_the_right_kind():
    assert parse_public(V4 + "\n", "v4") == V4
    assert parse_public(V6, "v6") == V6
    assert parse_public(V6, "v4") == "" and parse_public(V4, "v6") == ""
    assert parse_public("192.168.0.4", "v4") == ""                                 # a service must not be able to feed us a private address
    assert parse_public("<html>error</html>", "v4") == "" and parse_public("", "v4") == ""


# ---------------------------------------------------------------------------------------------- detection
def test_detects_both_families_and_reports_what_is_missing():
    n = mk(fake(V4, ""))
    d = n.detect(force=True)
    assert d["v4"] == V4 and d["v6"] == "" and "IPv6" in d["err"]["v6"]
    n2 = mk(fake(V4, V6))
    d = n2.detect(force=True)
    assert (d["v4"], d["v6"]) == (V4, V6)


def test_detection_is_cached_for_a_short_while_and_never_raises():
    t = [1000.0]
    f = fake()
    n = mk(f, lambda: t[0])
    n.detect()
    n.detect()
    assert sorted(f.calls) == ["v4", "v6"]                                         # second call answered from the cache
    t[0] += 100
    n.detect()
    assert len(f.calls) == 4
    boom = NetIp(Store(":memory:"), lambda fam: (_ for _ in ()).throw(OSError("network is down")), lambda: 1.0)
    d = boom.detect(force=True)
    assert d["v4"] == d["v6"] == "" and d["err"]["v4"]


def test_a_service_answering_with_the_wrong_family_is_ignored():
    n = mk(lambda fam: (V6 if fam == "v4" else V4, ""))
    d = n.detect(force=True)
    assert d["v4"] == "" and d["v6"] == ""


# ----------------------------------------------------------------------------------------- the two options
def test_option_one_register_what_was_detected_then_it_is_the_expected_address():
    n = mk()
    assert n.expected()[0] == [] and n.status("live")["verdict"] == "unset"
    n.detect(force=True)
    assert n.status("live")["verdict"] == "unset"                                  # detected but not yet registered with Kotak
    n.confirm_registered(True, True)
    assert n.expected() == ([V4, V6], "terminal")
    st = n.status("live")
    assert st["verdict"] == "ok" and st["mode"] == "auto" and st["registered"]["v4"] == V4 and not st["bad"]


def test_confirming_only_ipv4_leaves_a_heads_up_about_the_ipv6_address():
    n = mk()
    n.confirm_registered(True, False)
    st = n.status("live")
    assert n.expected()[0] == [V4]
    assert st["verdict"] == "partial" and V6 in st["message"] and not st["bad"]


def test_the_address_recorded_is_the_one_the_terminal_found_not_one_sent_by_the_browser():
    n = mk()
    n.confirm_registered(True, False)
    assert n.policy()["registered"]["v4"] == V4
    nothing = mk(fake("", ""))
    with pytest.raises(NetIpError, match="nothing to confirm"):
        nothing.confirm_registered(True, False)
    with pytest.raises(NetIpError, match="Tick"):
        mk().confirm_registered(False, False)


def test_auto_mode_notices_when_the_provider_changes_the_address():
    cur = {"v4": V4, "v6": V6}
    n = NetIp(Store(":memory:"), lambda fam: (cur[fam], ""), lambda: 5.0)
    n.confirm_registered(True, True)
    cur["v4"] = V4B
    n.detect(force=True)
    st = n.status("live")
    assert st["verdict"] == "changed" and st["bad"] and V4B in st["message"] and V4 in st["message"]
    txt = n.alert_text(st, "live")
    assert txt.startswith("ACTION REQUIRED") and V4B in txt
    assert n.alert_text(st, "live") is None                                        # told once, not every five minutes
    cur["v4"] = V4
    n.detect(force=True)
    assert n.alert_text(n.status("live"), "live") is None                          # fixed: nothing to say, alert re-armed
    cur["v4"] = V4B
    n.detect(force=True)
    assert n.alert_text(n.status("live"), "live") is not None
    assert n.alert_text(n.status("paper"), "paper") is None                        # paper mode sends no real orders: no Telegram noise


def test_option_two_own_static_ip_v4_v6_or_both():
    n = mk()
    n.set_policy("own", f"{V4}, {V6}")
    assert n.expected() == ([V4, V6], "terminal") and n.policy()["mode"] == "own"
    n.set_policy("own", V6)
    assert n.expected() == ([V6], "terminal")
    n.set_policy("own", V4)
    assert n.expected() == ([V4], "terminal")
    with pytest.raises(NetIpError, match="Enter the static IP"):
        n.set_policy("own", "")
    with pytest.raises(NetIpError, match="public"):
        n.set_policy("own", "192.168.1.20")
    assert n.expected() == ([V4], "terminal")                                      # a refused entry changes nothing
    with pytest.raises(NetIpError):
        n.set_policy("sideways", "")


def test_own_ip_that_this_connection_is_not_using_is_flagged_before_you_trade():
    n = mk(fake(V4B, ""))
    n.set_policy("own", V4)
    n.detect(force=True)
    st = n.status("live")
    assert st["verdict"] == "mismatch" and st["bad"] and V4B in st["message"] and V4 in st["message"]
    ok = mk(fake(V4, ""))
    ok.set_policy("own", V4)
    ok.detect(force=True)
    assert ok.status("live")["verdict"] == "ok"


def test_what_kotak_sees_decides_when_it_is_known():
    n = mk(fake(V4B, V6B))
    n.set_policy("own", V4)
    n.detect(force=True)                                                           # this connection looks different...
    assert n.status("live", seen=V4)["verdict"] == "ok"                            # ...but Kotak sees the registered one: that wins
    assert n.status("live", seen="49.36.10.77")["verdict"] == "mismatch"
    assert n.status("live", seen=V6)["verdict"] == "mismatch"
    assert n.status("live", seen="::ffff:49.36.10.20")["verdict"] == "ok"
    assert n.status("live", seen=V4)["kotak_sees"] == V4


def test_lookup_failure_is_reported_as_unchecked_not_as_a_wrong_ip():
    n = mk(fake("", "", ""))
    n.set_policy("own", V4)
    n.detect(force=True)
    assert n.status("live")["verdict"] == "unchecked" and not n.status("live")["bad"]


def test_precedence_account_then_terminal_then_config():
    n = mk()
    n.set_policy("own", V4)
    assert n.expected("", "198.51.100.9") == ([V4], "terminal")
    assert n.expected(f"{V6B}", "198.51.100.9") == ([V6B], "account")
    assert n.expected(f"{V4B}, {V6B}", "") == ([V4B, V6B], "account")
    empty = mk()
    assert empty.expected("", "198.51.100.9") == (["198.51.100.9"], "config")
    assert empty.expected("", "garbage") == ([], "")


def test_problem_text_says_what_to_do():
    n = mk()
    n.set_policy("own", V4)
    assert n.problem_for(V4, "", "") is None
    assert n.problem_for("", "", "") is None                                       # Kotak did not say: its own check still applies
    p = n.problem_for(V6B, "", "")
    assert V6B in p and V4 in p and "static IP" in p
    pa = n.problem_for(V4B, V6, "", "Main")
    assert "'Main'" in pa and V6 in pa
    assert problem_for_config("198.51.100.9", "203.0.113.7").count("198.51.100.9") == 1
    assert problem_for_config("", "203.0.113.7") is None and problem_for_config("198.51.100.9", "198.51.100.9") is None


# ------------------------------------------------------------------------------- live connect (fake Kotak)
def test_live_connect_with_terminal_static_ip_accepts_ipv4_or_ipv6_and_refuses_others(paper, monkeypatch):
    svc, a, b = paper
    svc.mode = "live"
    import terminal.trading.kotak_gateway as kg
    monkeypatch.setattr(kg, "KotakGateway", lambda *x: object())
    svc.netip.set_policy("own", f"{V4}, {V6}")

    def login_as(ip):
        def login(totp=None):
            svc.session.my_ip, svc.session.logged_in = ip, True
        svc.session.login = login
    login_as(V4B)
    with pytest.raises(RuntimeError, match="Settings, Static IP"):
        svc.connect("111111", a, confirm_live=True)
    assert not svc.connected and svc.session.logged_in is False                    # logged out again, nothing started
    login_as(V6.upper())
    svc.connect("111111", a, confirm_live=True)                                    # IPv6 as Kotak reports it, other spelling
    assert svc.connected
    assert svc.network_status()["verdict"] == "ok" and svc.network_status()["kotak_sees"] == V6
    svc.disconnect()
    login_as(V4)
    svc.connect("111111", a, confirm_live=True)
    assert svc.connected


def test_live_connect_without_any_static_ip_is_allowed_but_flagged(paper, monkeypatch):
    svc, a, b = paper
    svc.mode = "live"
    import terminal.trading.kotak_gateway as kg
    monkeypatch.setattr(kg, "KotakGateway", lambda *x: object())
    svc.connect("111111", a, confirm_live=True)
    st = svc.network_status()
    assert svc.connected and st["verdict"] == "unset" and "Kotak rejects" in st["message"]


def test_paper_mode_never_refuses_for_the_ip(paper):
    svc, a, b = paper
    svc.netip.set_policy("own", V4B)                                               # Kotak sees 203.0.113.7: would be a mismatch in live
    svc.connect("111111", a)
    assert svc.connected


def test_the_algo_will_not_start_when_kotak_sees_another_ip(paper):
    svc, a, b = paper
    svc.connect("111111", a)
    svc.netip.set_policy("own", V4)
    with pytest.raises(RuntimeError, match=V4):
        svc.algo.start()


def test_account_static_ip_accepts_ipv4_and_ipv6_together(paper):
    svc, a, b = paper
    svc.vault.update(a, {"static_ip": f"{V4}, {V6}"})
    assert svc.vault.get(a)["static_ip"] == f"{V4}, {V6}"
    with pytest.raises(Exception, match="static IP"):
        svc.vault.update(a, {"static_ip": f"{V4} {V4B}"})


# ----------------------------------------------------------------------------------------------- the API
@pytest.fixture
def api(tmp_path, monkeypatch):
    import terminal.netip as netip
    monkeypatch.setattr(netip, "http_fetch", lambda fam: (V4, "") if fam == "v4" else (V6, ""))
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = "sample", str(tmp_path / "data")
    svc = Services(cfg, env={})
    svc.start()
    c = TestClient(create_app(svc, PW))
    yield svc, c
    svc.shutdown()


def test_network_endpoints_need_login_and_csrf_header(api):
    svc, c = api
    assert c.get("/api/network").status_code == 401
    login(c)
    h = c.headers.pop("x-terminal")
    for call in (lambda: c.post("/api/network/detect"), lambda: c.put("/api/network", json={"mode": "auto"}),
                 lambda: c.post("/api/network/confirm", json={"v4": True})):
        assert call().status_code == 403
    c.headers["x-terminal"] = h


def test_api_detect_then_register_then_switch_to_own_ip(api):
    svc, c = api
    login(c)
    st = c.get("/api/network").json()
    assert st["verdict"] == "unset" and st["detected"]["v4"] == ""                 # nothing looked up until asked
    st = c.post("/api/network/detect").json()
    assert st["detected"]["v4"] == V4 and st["detected"]["v6"] == V6 and st["mode"] == "auto"
    r = c.post("/api/network/confirm", json={"v4": True, "v6": True}).json()
    assert r["verdict"] == "ok" and r["expected"] == [V4, V6] and r["registered"]["v4"] == V4
    r = c.put("/api/network", json={"mode": "own", "own_ips": V4B}).json()
    assert r["mode"] == "own" and r["expected"] == [V4B] and r["own"]["v4"] == V4B and r["own"]["v6"] == ""
    assert r["registered"]["v4"] == V4                                             # the earlier registration is kept if you switch back
    r = c.put("/api/network", json={"mode": "own", "own_ips": f"{V4B}, {V6B}"}).json()
    assert r["expected"] == [V4B, V6B]


def test_api_refuses_private_and_malformed_addresses_with_a_readable_message(api):
    svc, c = api
    login(c)
    for bad, text in (("192.168.1.10", "public"), ("12.34.56", "not a valid"), ("", "Enter the static IP"), (f"{V4} {V4B}", "at most one")):
        r = c.put("/api/network", json={"mode": "own", "own_ips": bad})
        assert r.status_code == 400 and text in r.json()["error"], (bad, r.text)
    assert c.put("/api/network", json={"mode": "bogus"}).status_code == 422
    assert c.post("/api/network/confirm", json={}).status_code == 400


def test_session_carries_an_ip_alert_only_for_live(api, tmp_path):
    svc, c = api
    login(c)
    assert c.get("/api/session").json()["ip_alert"] is None                        # sample mode: nothing to warn about
    svc.mode = "live"
    assert "Kotak rejects" in c.get("/api/session").json()["ip_alert"]             # live with nothing set up
    svc.netip.set_policy("own", V4)
    svc.netip.detect(force=True)                                                   # connection uses V4/V6 from the fake: V4 matches, V6 is extra
    assert c.get("/api/session").json()["ip_alert"] is None
    svc.netip.set_policy("own", V4B)
    assert V4B in c.get("/api/session").json()["ip_alert"]
    svc.mode = "sample"


def test_status_never_contains_secrets(paper):
    svc, a, b = paper
    svc.connect("111111", a)
    blob = json.dumps(svc.network_status())
    for secret in ("ck_1234567890abcdef", "135790", "AB123", "9876543210"):
        assert secret not in blob


def test_session_reports_what_is_running_for_the_phone_badges(api):
    svc, c = api
    login(c)
    assert c.get("/api/session").json()["activity"] == {"strategies": 0, "algo": False}
