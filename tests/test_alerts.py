"""Telegram alerts: delivery, filtering, de-duplication, token secrecy, and that they never get in the way of trading."""
import json
import threading
import time

import httpx

from terminal.alerts import Alerts
from terminal.config import AlertsCfg

TOKEN = "123456:ABC-secret-token-xyz"


class Telegram:
    """A fake Telegram server behind httpx.MockTransport."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or [])
        self.gate = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.gate:
            self.gate.wait(5)
        self.calls.append((str(request.url), json.loads(request.content)))
        if self.replies:
            r = self.replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return httpx.Response(200, json={"ok": True, "result": {}})

    @property
    def texts(self):
        return [b["text"] for _, b in self.calls]


def make(tg, cfg=None, token=TOKEN, chat="42", mode="paper"):
    http = httpx.Client(transport=httpx.MockTransport(tg))
    return Alerts(token, chat, mode, cfg or AlertsCfg(), http=http, sleep=lambda s: None, spacing_s=0)


def test_message_is_sent_with_mode_prefix_and_plain_text():
    tg = Telegram()
    a = make(tg)
    assert a.notify("strategy", "ORB: In position <b>SOLD</b> 75 NIFTY & more")
    assert a.flush()
    url, body = tg.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert body["chat_id"] == "42"
    assert body["text"] == "[PAPER] ORB: In position <b>SOLD</b> 75 NIFTY & more"
    assert "parse_mode" not in body                       # plain text: odd characters cannot break the message
    assert a.status()["sent"] == 1 and a.status()["last_ok"]


def test_not_configured_or_disabled_sends_nothing():
    tg = Telegram()
    assert not make(tg, token="").notify("error", "x")
    assert not make(tg, chat="").notify("error", "x")
    assert not make(tg, cfg=AlertsCfg(enabled=False)).notify("error", "x")
    assert tg.calls == []


def test_event_switches_filter_but_errors_always_go_through():
    tg = Telegram()
    a = make(tg, cfg=AlertsCfg(strategy_events=False, warnings=False, orders=False, daily_summary=False))
    assert not a.notify("strategy", "s")
    assert not a.notify("warning", "w")
    assert not a.notify("order", "o")
    assert not a.notify("summary", "d")
    assert a.notify("error", "ACTION REQUIRED")
    assert a.flush()
    assert tg.texts == ["[PAPER] ACTION REQUIRED"]


def test_same_message_is_not_repeated_within_two_minutes():
    tg = Telegram()
    now = [1000.0]
    http = httpx.Client(transport=httpx.MockTransport(tg))
    a = Alerts(TOKEN, "42", "live", http=http, clock=lambda: now[0], sleep=lambda s: None, spacing_s=0)
    assert a.notify("warning", "feed quiet")
    assert not a.notify("warning", "feed quiet")
    assert a.notify("warning", "different")
    now[0] += 130
    assert a.notify("warning", "feed quiet")              # old enough: allowed again
    assert a.flush()
    assert len(tg.calls) == 3 and a.status()["suppressed"] == 1


def test_token_never_appears_in_errors_or_status():
    tg = Telegram([httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{TOKEN}/sendMessage")])
    a = make(tg)
    r = a.test()
    assert not r["ok"]
    assert TOKEN not in r["error"] and "secret-token" not in r["error"]
    assert TOKEN not in json.dumps(a.status())
    assert "secret-token" not in a.status()["last_error"]


def test_friendly_errors_for_bad_token_and_bad_chat():
    a = make(Telegram([httpx.Response(401, json={"ok": False, "description": "Unauthorized"})]))
    assert "bot token" in a.test()["error"]
    a = make(Telegram([httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})]))
    assert "press Start" in a.test()["error"]
    a = make(Telegram([httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"})]))
    assert "blocked" in a.test()["error"]


def test_rate_limit_reply_waits_and_retries_once():
    waits = []
    tg = Telegram([httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 2}}), httpx.Response(200, json={"ok": True})])
    http = httpx.Client(transport=httpx.MockTransport(tg))
    a = Alerts(TOKEN, "42", "paper", http=http, sleep=waits.append, spacing_s=0)
    assert a.test()["ok"]
    assert waits == [2.0] and len(tg.calls) == 2


def test_failed_send_is_recorded_and_does_not_raise():
    tg = Telegram([httpx.ConnectError("down")] * 3)
    a = make(tg)
    a.notify("error", "boom")
    assert a.flush()
    st = a.status()
    assert st["failed"] == 1 and "ConnectError" in st["last_error"] and st["sent"] == 0


def test_notify_never_blocks_even_when_telegram_hangs():
    tg = Telegram()
    tg.gate = threading.Event()                            # Telegram "hangs" until released
    a = make(tg)
    t0 = time.time()
    for i in range(200):
        a.notify("warning", f"message {i}")
    assert time.time() - t0 < 1.0                          # the caller was never held up
    assert a.status()["dropped"] > 0                       # the queue is bounded: oldest messages are dropped
    tg.gate.set()
    assert a.flush(10)


def test_test_button_reports_missing_setup():
    r = make(Telegram(), token="").test()
    assert not r["ok"] and "TELEGRAM_BOT_TOKEN" in r["error"]
