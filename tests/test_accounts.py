"""Saved Kotak accounts: encrypted store, validation, masking, the API, and connecting / switching accounts (with a fake Kotak)."""
import json
import os
import stat
import time

import pytest
from fastapi.testclient import TestClient

from conftest import ROOT
from terminal import services as services_mod
from terminal.accounts import ENV_ID, MAX_ACCOUNTS, AccountVault, VaultError, clean_mobile, scrub
from terminal.config import load_config
from terminal.services import Services
from terminal.web.server import create_app

PW = "correct-horse-battery"
GOOD = {"label": "My account", "consumer_key": "ck_1234567890abcdef", "mobile": "+919876543210", "ucc": "ab123", "mpin": "135790",
        "totp_secret": "", "static_ip": ""}
SECRET_TOTP = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def vault(tmp_path, env=None, pw=PW):
    v = AccountVault(tmp_path / "accounts.enc", env if env is not None else {})
    v.unlock(pw)
    return v


def acct(**kw):
    return {**GOOD, **kw}


# ------------------------------------------------------------------------------------------------ the vault
def test_add_list_masks_secrets_and_persists_encrypted(tmp_path):
    v = vault(tmp_path)
    aid = v.add(acct(totp_secret=SECRET_TOTP))
    (a,) = v.public()
    assert a["id"] == aid and a["label"] == "My account" and a["default"] and a["has_totp_secret"] and a["has_mpin"]
    blob = json.dumps(a)
    for secret in ("ck_1234567890abcdef", "135790", SECRET_TOTP, "9876543210", "AB123"):
        assert secret not in blob
    raw = (tmp_path / "accounts.enc").read_bytes()
    for secret in (b"ck_1234567890abcdef", b"135790", SECRET_TOTP.encode(), b"9876543210", b"AB123", b"My account"):
        assert secret not in raw
    assert stat.S_IMODE(os.stat(tmp_path / "accounts.enc").st_mode) == 0o600
    again = vault(tmp_path)
    assert again.creds(aid) == {"NEO_CONSUMER_KEY": "ck_1234567890abcdef", "NEO_MOBILE_NUMBER": "+919876543210", "NEO_UCC": "AB123",
                                "NEO_MPIN": "135790", "NEO_TOTP_SECRET": SECRET_TOTP}


def test_wrong_or_changed_dashboard_password_does_not_open_or_destroy_the_file(tmp_path):
    v = vault(tmp_path)
    v.add(acct())
    before = (tmp_path / "accounts.enc").read_bytes()
    other = vault(tmp_path, pw="another-password-1")
    assert not other.readable and "DASHBOARD_PASSWORD" in other.problem and other.public() == []
    with pytest.raises(VaultError):
        other.add(acct(label="x", ucc="zz9"))
    assert (tmp_path / "accounts.enc").read_bytes() == before                     # untouched
    assert vault(tmp_path).public()[0]["label"] == "My account"                   # the right password still opens it


def test_a_corrupt_file_is_reported_and_left_alone(tmp_path):
    (tmp_path / "accounts.enc").write_text("not json at all")
    v = vault(tmp_path)
    assert not v.readable and "could not be read" in v.problem
    assert (tmp_path / "accounts.enc").read_text() == "not json at all"


@pytest.mark.parametrize("over,frag", [
    ({"label": ""}, "name"), ({"label": "x" * 41}, "name"),
    ({"consumer_key": ""}, "API token"), ({"consumer_key": "short"}, "API token"), ({"consumer_key": "has space in the token"}, "API token"),
    ({"mobile": "12345"}, "mobile"), ({"mobile": "+911234567"}, "mobile"), ({"mobile": "abc"}, "mobile"), ({"mobile": ""}, "mobile"),
    ({"ucc": ""}, "client ID"), ({"ucc": "a b"}, "client ID"), ({"ucc": "x" * 30}, "client ID"),
    ({"mpin": ""}, "MPIN"), ({"mpin": "12"}, "MPIN"), ({"mpin": "12ab56"}, "MPIN"),
    ({"totp_secret": "123456"}, "TOTP"), ({"totp_secret": "!!!notbase32!!!!!!!!"}, "TOTP"),
    ({"static_ip": "999.1.1.1"}, "static IP"), ({"static_ip": "hello"}, "static IP"),
])
def test_bad_details_are_refused_with_a_readable_reason(tmp_path, over, frag):
    v = vault(tmp_path)
    with pytest.raises(VaultError) as e:
        v.add(acct(**over))
    assert frag.lower() in str(e.value).lower()
    assert v.public() == []


def test_mobile_numbers_are_normalised():
    for raw in ("9876543210", "+91 98765 43210", "919876543210", "+91-9876543210", "(+91) 9876543210"):
        assert clean_mobile(raw) == "+919876543210"


def test_duplicates_and_limits(tmp_path):
    v = vault(tmp_path)
    v.add(acct())
    with pytest.raises(VaultError, match="already an account called"):
        v.add(acct(ucc="zz9"))
    with pytest.raises(VaultError, match="already saved"):
        v.add(acct(label="Other"))
    for i in range(MAX_ACCOUNTS - 1):
        v.add(acct(label=f"A{i}", ucc=f"U{i}"))
    with pytest.raises(VaultError, match="At most"):
        v.add(acct(label="one more", ucc="UNEW"))


def test_edit_keeps_blank_secrets_and_can_clear_the_totp_secret(tmp_path):
    v = vault(tmp_path)
    aid = v.add(acct(totp_secret=SECRET_TOTP))
    v.update(aid, {"label": "Renamed", "consumer_key": "", "mpin": "", "totp_secret": "", "mobile": "", "ucc": "", "static_ip": "203.0.113.7"})
    c = v.creds(aid)
    assert c["NEO_MPIN"] == "135790" and c["NEO_CONSUMER_KEY"] == "ck_1234567890abcdef" and c["NEO_TOTP_SECRET"] == SECRET_TOTP
    assert v.get(aid)["label"] == "Renamed" and v.get(aid)["static_ip"] == "203.0.113.7"
    v.update(aid, {"mpin": "246810"})
    assert v.creds(aid)["NEO_MPIN"] == "246810"
    v.update(aid, {"clear_totp_secret": True})
    assert v.creds(aid)["NEO_TOTP_SECRET"] == "" and not v.public()[0]["has_totp_secret"]
    with pytest.raises(VaultError):
        v.update("nope", {"label": "x"})


def test_default_account_rules_and_delete(tmp_path):
    v = vault(tmp_path)
    a = v.add(acct())
    assert v.default_id() == a
    b = v.add(acct(label="Second", ucc="XY9"))
    assert v.default_id() == a                                                  # the first stays the default
    v.set_default(b)
    assert v.default_id() == b and [x["default"] for x in v.public()] == [False, True]
    v.delete(b)
    assert v.default_id() == a                                                  # only one left: it is the default again
    v.delete(a)
    assert v.public() == [] and v.default_id() == ""
    with pytest.raises(VaultError):
        v.delete(a)


def test_two_accounts_and_no_default_means_the_person_must_choose(tmp_path):
    v = vault(tmp_path)
    a, b = v.add(acct()), v.add(acct(label="Second", ucc="XY9"))
    v._data["default"] = ""
    assert v.default_id() == ""


ENV = {"NEO_CONSUMER_KEY": "envkey_12345678", "NEO_MOBILE_NUMBER": "+919000000001", "NEO_UCC": "env1", "NEO_MPIN": "111222"}


def test_the_env_file_account_shows_up_read_only_and_keeps_the_old_journal(tmp_path):
    v = vault(tmp_path, env=ENV)
    (e,) = v.public()
    assert e["id"] == ENV_ID and e["label"] == ".env file" and not e["editable"] and e["default"]
    assert v.creds(ENV_ID)["NEO_MPIN"] == "111222"
    with pytest.raises(VaultError, match=".env"):
        v.update(ENV_ID, {"label": "x"})
    with pytest.raises(VaultError, match=".env"):
        v.delete(ENV_ID)
    assert v.journal_namespace(ENV_ID, "paper") == "paper"                       # the history from before accounts existed
    other = v.add(acct())
    assert v.journal_namespace(other, "paper") == "paper:AB123"
    with pytest.raises(VaultError, match="already saved as '.env file'"):        # one real client, one entry
        v.add(acct(label="Same client as .env", ucc="ENV1", consumer_key="another_key_123"))
    assert not any("envkey" in json.dumps(x) for x in v.public())


def test_incomplete_env_values_are_not_an_account(tmp_path):
    v = vault(tmp_path, env={"NEO_CONSUMER_KEY": "x" * 12, "NEO_UCC": "a1"})
    assert v.public() == []


def test_scrub_removes_secrets_from_error_text():
    c = {"NEO_CONSUMER_KEY": "ck_1234567890abcdef", "NEO_MPIN": "135790", "NEO_TOTP_SECRET": SECRET_TOTP}
    out = scrub(f"login failed for ck_1234567890abcdef with mpin 135790 secret {SECRET_TOTP}", c)
    assert "ck_123" not in out and "135790" not in out and SECRET_TOTP not in out and "••••" in out


# ------------------------------------------------------------------------------------------------------ the API
@pytest.fixture
def env(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = "sample", str(tmp_path / "data")
    svc = Services(cfg, env={})
    svc.start()
    c = TestClient(create_app(svc, PW))
    yield svc, c
    svc.shutdown()


def login(c):
    assert c.post("/api/auth/login", json={"password": PW}, headers={"x-terminal": "1"}).status_code == 200
    c.headers["x-terminal"] = "1"


def test_account_endpoints_need_login_and_the_csrf_header(env):
    svc, c = env
    assert c.get("/api/accounts").status_code == 401
    login(c)
    h = c.headers.pop("x-terminal")
    assert c.post("/api/accounts", json=GOOD).status_code == 403
    assert c.delete("/api/accounts/x").status_code == 403
    assert c.post("/api/disconnect").status_code == 403
    c.headers["x-terminal"] = h


def test_add_edit_default_delete_over_http_never_return_secrets(env):
    svc, c = env
    login(c)
    r = c.post("/api/accounts", json=GOOD).json()
    assert r["ok"] and len(r["accounts"]) == 1 and r["can_connect"] is False
    aid = r["id"]
    for text in (json.dumps(r), c.get("/api/accounts").text):
        for secret in ("ck_1234567890abcdef", "135790", "9876543210"):
            assert secret not in text
    bad = c.post("/api/accounts", json=acct(label="B", ucc="Q1", mpin="12"))
    assert bad.status_code == 400 and "MPIN" in bad.json()["error"]
    r2 = c.post("/api/accounts", json=acct(label="Second", ucc="XY9")).json()
    assert [a["default"] for a in r2["accounts"]] == [True, False]
    assert c.post(f"/api/accounts/{r2['id']}/default").json()["default"] == r2["id"]
    ed = c.put(f"/api/accounts/{aid}", json={"label": "Renamed"}).json()
    assert ed["ok"] and ed["accounts"][0]["label"] == "Renamed"
    assert c.put("/api/accounts/nope", json={"label": "x"}).status_code == 400
    assert c.delete(f"/api/accounts/{aid}").json()["ok"]
    assert len(c.get("/api/accounts").json()["accounts"]) == 1
    assert vault(svc.cfg.data_dir and __import__("pathlib").Path(svc.cfg.data_dir)).public()[0]["label"] == "Second"      # it was really saved


def test_sample_mode_has_nothing_to_connect_or_disconnect(env):
    svc, c = env
    login(c)
    assert c.post("/api/connect", json={"totp": ""}).json()["ok"]
    assert c.post("/api/disconnect").status_code == 400
    assert c.get("/api/session").json()["account"] is None


# ----------------------------------------------------------------------- connecting, switching (fake Kotak)
class FakeFeed:
    def __init__(self, session, cfg):
        self.connected, self.stopped, self.on_quote = True, False, None

    def start(self, cb):
        pass

    def stop(self):
        self.stopped = True

    def subscribe_keys(self, keys):
        pass


class FakeCatalog:
    def __init__(self, ins):
        self.items = {}

    def register(self, inst):
        self.items[inst.key] = inst

    def get(self, key):
        return self.items.get(key)

    def search(self, q, limit=25):
        return []

    def warm(self):
        pass

    status = "ready"


@pytest.fixture
def paper(tmp_path, monkeypatch):
    import terminal.brokers.kotak as kotak
    import terminal.market.catalog as catalog
    cfg = load_config(ROOT / "config.yaml")
    cfg.mode, cfg.data_dir = "paper", str(tmp_path / "data")
    svc = Services(cfg, env={})
    svc.vault.unlock(PW)
    monkeypatch.setattr(kotak, "KotakFeed", FakeFeed)
    monkeypatch.setattr(catalog, "KotakCatalog", FakeCatalog)
    monkeypatch.setattr(services_mod, "KotakChainProvider", lambda s, c: object())
    svc.logins = []

    def login(totp=None):
        svc.logins.append((dict(svc.session.env), totp))
        svc.session.my_ip = "203.0.113.7"
        svc.session.logged_in = True
    svc.session.login = login
    svc.session.logout = lambda: setattr(svc.session, "logged_in", False)
    svc._kotak_ins = object()
    a = svc.vault.add(acct())
    b = svc.vault.add(acct(label="Second", ucc="XY9", mpin="999888", consumer_key="second_key_123456"))
    yield svc, a, b
    svc.shutdown()


def test_connect_uses_the_chosen_accounts_own_details_and_names_it(paper):
    svc, a, b = paper
    svc.connect("123456", b)
    creds, totp = svc.logins[-1]
    assert creds["NEO_UCC"] == "XY9" and creds["NEO_MPIN"] == "999888" and totp == "123456"
    assert svc.connected and svc.account["id"] == b and svc.account["label"] == "Second"
    assert "XY9" not in json.dumps(svc.account)                                  # the client ID is masked even here
    assert svc.journal.ns == "paper:XY9"
    assert svc.store.get("accounts.last") == b


def test_connect_without_a_choice_uses_the_default_and_refuses_when_there_is_none(paper):
    svc, a, b = paper
    svc.connect("111111")
    assert svc.account["id"] == a
    svc.disconnect()
    svc.vault._data["default"] = ""
    with pytest.raises(RuntimeError, match="No account|Choose"):
        svc.connect("111111")


def test_a_second_account_cannot_be_connected_over_the_first_and_connecting_the_same_again_is_harmless(paper):
    svc, a, b = paper
    svc.connect("111111", a)
    svc.connect("222222", a)
    assert len(svc.logins) == 1
    with pytest.raises(RuntimeError, match="Disconnect first"):
        svc.connect("333333", b)
    assert svc.account["id"] == a


def test_switching_accounts_after_a_disconnect_keeps_each_journal_apart(paper):
    svc, a, b = paper
    svc.connect("111111", a)
    first_feed = svc.feed
    assert svc.journal.ns == "paper:AB123"
    svc.disconnect()
    assert not svc.connected and svc.gateway is None and svc.account is None and svc.feed is None and first_feed.stopped
    assert svc.journal.ns is None
    svc.connect("222222", b)
    assert svc.account["id"] == b and svc.journal.ns == "paper:XY9" and svc.feed is not first_feed
    assert svc.logins[0][0]["NEO_UCC"] == "AB123" and svc.logins[1][0]["NEO_UCC"] == "XY9"


def test_disconnect_is_refused_while_a_strategy_or_the_algo_is_running(paper):
    svc, a, b = paper
    svc.connect("111111", a)
    svc.rules.running = lambda: [1]
    with pytest.raises(RuntimeError, match="Stop your running strategies"):
        svc.disconnect()
    assert svc.connected
    svc.rules.running = lambda: []
    class Eng:
        class finished:
            @staticmethod
            def is_set():
                return False
    svc.algo.engine = Eng()
    with pytest.raises(RuntimeError, match="Stop the Algo"):
        svc.disconnect()
    svc.algo.engine = None
    svc.disconnect()
    assert not svc.connected


def test_live_mode_needs_an_explicit_confirmation_naming_the_account(paper):
    svc, a, b = paper
    svc.mode = "live"
    with pytest.raises(RuntimeError, match="LIVE mode.*'Second'"):
        svc.connect("111111", b)
    assert not svc.logins


def test_live_connection_checks_the_accounts_own_static_ip(paper, monkeypatch):
    svc, a, b = paper
    svc.mode = "live"
    svc.vault.update(b, {"static_ip": "198.51.100.9"})
    import terminal.trading.kotak_gateway as kg
    monkeypatch.setattr(kg, "KotakGateway", lambda *x: object())
    with pytest.raises(RuntimeError, match="198.51.100.9"):
        svc.connect("111111", b, confirm_live=True)
    assert not svc.connected and svc.session.logged_in is False                  # logged out again


def test_failed_logins_never_leak_secrets_and_repeated_ones_are_paused(paper):
    svc, a, b = paper
    def bad(totp=None):
        svc.logins.append(1)
        raise RuntimeError(f"MPIN validation failed for key {svc.session.env['NEO_CONSUMER_KEY']} mpin {svc.session.env['NEO_MPIN']}")
    svc.session.login = bad
    msgs = []
    for _ in range(3):
        with pytest.raises(RuntimeError) as e:
            svc.connect("123456", a)
        msgs.append(str(e.value))
    assert all("ck_1234567890abcdef" not in m and "135790" not in m for m in msgs)
    assert "lock the MPIN" in msgs[1]
    n = len(svc.logins)
    with pytest.raises(RuntimeError, match="Wait a minute"):
        svc.connect("123456", a)
    assert len(svc.logins) == n                                                  # Kotak was not asked a fourth time
    with pytest.raises(RuntimeError):                                            # the other account is not affected
        svc.connect("123456", b)
    assert len(svc.logins) == n + 1
