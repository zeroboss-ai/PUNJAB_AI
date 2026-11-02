"""
Telegram alerts.

Rules this module keeps:
  * Alerts are best effort and can NEVER slow down or break trading: notify() only puts a message on a small queue;
    a background thread sends it. A full queue drops the oldest message. Every failure is swallowed and recorded.
  * The bot token never leaves this module: it is scrubbed from every error text, log line and API reply.
  * The same message is not sent twice within two minutes (a strategy retrying every second must not flood your phone).
  * Messages are plain text (no markup), so odd characters in a symbol or an error cannot break them.

Kinds: "error" (always sent), "warning", "strategy", "order", "summary", "system".
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

import httpx

from .config import AlertsCfg

log = logging.getLogger("alerts")

API = "https://api.telegram.org"
DEDUPE_S = 120.0
SPACING_S = 1.1            # Telegram allows about one message a second to one chat
MAX_TEXT = 3800            # Telegram's limit is 4096 characters
KIND_SWITCH = {"warning": "warnings", "strategy": "strategy_events", "order": "orders", "summary": "daily_summary"}


class Alerts:
    def __init__(self, token: str, chat_id: str, mode: str, cfg: AlertsCfg | None = None,
                 http: httpx.Client | None = None, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep, spacing_s: float = SPACING_S) -> None:
        self._token = (token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.mode = mode
        self.cfg = cfg or AlertsCfg()
        self._http = http or httpx.Client(timeout=10.0)
        self._clock, self._sleep, self._spacing = clock, sleep, spacing_s
        self._q: queue.Queue = queue.Queue(maxsize=50)
        self._recent: dict[tuple, float] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.sent = self.failed = self.suppressed = self.dropped = 0
        self.last_ok: float | None = None
        self.last_error: str = ""

    # ---------------------------------------------------------------- state
    @property
    def configured(self) -> bool:
        return bool(self._token and self.chat_id)

    @property
    def active(self) -> bool:
        return self.configured and self.cfg.enabled

    def status(self) -> dict:
        return {"configured": self.configured, "enabled": self.cfg.enabled, "sent": self.sent, "failed": self.failed,
                "suppressed": self.suppressed, "dropped": self.dropped, "queued": self._q.qsize(),
                "last_ok": self.last_ok, "last_error": self.last_error,
                "events": {"strategy_events": self.cfg.strategy_events, "warnings": self.cfg.warnings,
                           "orders": self.cfg.orders, "daily_summary": self.cfg.daily_summary}}

    def _scrub(self, text: str) -> str:
        t = str(text)
        if self._token:
            t = t.replace(self._token, "***")
            if ":" in self._token:                                   # also the part after "bot", in case it is split
                t = t.replace(self._token.split(":", 1)[1], "***")
        return t

    # ----------------------------------------------------------------- send
    def notify(self, kind: str, text: str) -> bool:
        """Queue a message. Returns True if it was queued. Never raises, never blocks."""
        try:
            if not self.active:
                return False
            if kind != "error":
                sw = KIND_SWITCH.get(kind)
                if sw is not None and not getattr(self.cfg, sw):
                    return False
            now = self._clock()
            key = (kind, text)
            with self._lock:
                for k in [k for k, t in self._recent.items() if now - t > DEDUPE_S]:
                    del self._recent[k]
                if key in self._recent:
                    self.suppressed += 1
                    return False
                self._recent[key] = now
            msg = f"[{self.mode.upper()}] {text}"[:MAX_TEXT]
            try:
                self._q.put_nowait(msg)
            except queue.Full:
                try:
                    self._q.get_nowait()
                    self._q.task_done()
                    self.dropped += 1
                    self._q.put_nowait(msg)
                except Exception:
                    return False
            self._ensure_worker()
            return True
        except Exception:
            log.exception("alert could not be queued")
            return False

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="alerts", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                msg = self._q.get(timeout=30)
            except queue.Empty:
                return                                              # worker exits when idle; notify() restarts it
            try:
                ok, err = self._send(msg)
                if not ok:
                    self.failed += 1
                    self.last_error = err
                    log.warning("telegram alert failed: %s", err)
                self._sleep(self._spacing)
            except Exception:
                log.exception("alert worker")
            finally:
                self._q.task_done()

    def _send(self, text: str) -> tuple[bool, str]:
        url = f"{API}/bot{self._token}/sendMessage"
        body = {"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True}
        for attempt in (1, 2):
            try:
                r = self._http.post(url, json=body)
            except Exception as e:                                  # network down, DNS, timeout ...
                return False, self._scrub(f"Could not reach Telegram ({type(e).__name__}). Check this computer's internet connection.")[:200]
            try:
                data = r.json()
            except Exception:
                data = {}
            if r.status_code == 200 and data.get("ok"):
                self.sent += 1
                self.last_ok = self._clock()
                self.last_error = ""
                return True, ""
            if r.status_code == 429 and attempt == 1:
                wait = (data.get("parameters") or {}).get("retry_after", 3)
                self._sleep(min(max(float(wait), 1.0), 30.0))
                continue
            return False, self._explain(r.status_code, str(data.get("description", "")))
        return False, "Telegram asked us to slow down"

    def _explain(self, code: int, desc: str) -> str:
        d = self._scrub(desc)
        if code == 401:
            return "Telegram rejected the bot token. Check TELEGRAM_BOT_TOKEN in .env."
        if code == 400 and "chat not found" in d.lower():
            return "Telegram cannot find that chat. Open your bot in Telegram, press Start, send it a message, and check TELEGRAM_CHAT_ID."
        if code == 403:
            return "The bot is blocked or was removed from the chat. Open the bot in Telegram and press Start."
        return f"Telegram error {code}: {d[:120]}"

    def test(self) -> dict:
        """Send one message right now (used by the Settings button) and say plainly whether it arrived."""
        if not self.configured:
            return {"ok": False, "error": "Telegram is not set up. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env and restart."}
        ok, err = self._send(f"[{self.mode.upper()}] Test message from PUNJAB AI. If you can read this, alerts work.")
        if not ok:
            self.failed += 1
            self.last_error = err
        return {"ok": ok, "error": err}

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every queued message has been handled (tests and shutdown). True if it finished in time."""
        end = time.time() + timeout
        while time.time() < end:
            if self._q.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return False
