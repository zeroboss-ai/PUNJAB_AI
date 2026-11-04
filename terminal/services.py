"""
Services: builds and owns everything one terminal instance needs - hub, catalog, store, broker gateway, the
trading service and the algo controller - according to the mode (sample / paper / live).

  sample  no login. Invented prices, simulated orders. For trying the UI.
  paper   Kotak login. REAL prices, simulated orders.
  live    Kotak login. REAL prices, REAL orders.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from .accounts import AccountVault, scrub
from .alerts import Alerts
from .backtest import BacktestManager
from .history import CandleDB, Recorder
from .brokers.paper import PaperOrders
from .brokers.replay import ReplayFeed, SyntheticInstruments
from .clock import at, now_ist, parse_hm
from .config import Config
from .controller import Controller
from .engine import Engine
from .journal import Journal
from .market.catalog import LazyCatalog
from .market.charts import ChartService
from .market.chain import ChainService, KotakChainProvider, SampleChainProvider
from .market.hub import HubEngineFeed, QuoteHub
from .market.models import Instrument
from .netip import NetIp
from .nse import NseClient, NseService, SampleNse
from .market.sample import SAMPLE_DERIVS, SAMPLE_EQUITIES, SampleFeed, build_catalog
from .rules.runner import HubMarket, RuleEngine
from .sim import build_day
from .store import Store
from .trading.service import ManualLimits, TradingService
from .trading.sim import SimBroker

log = logging.getLogger("services")


def market_status(now: datetime | None = None) -> dict:
    """Open/closed by clock only - NSE holidays are NOT known to the terminal."""
    now = now or now_ist()
    if now.weekday() >= 5:
        return {"state": "weekend", "label": "Weekend"}
    d = now.date()
    if now < at(d, parse_hm("09:00")):
        return {"state": "closed", "label": "Pre-market"}
    if now < at(d, parse_hm("09:15")):
        return {"state": "preopen", "label": "Pre-open"}
    if now <= at(d, parse_hm("15:30")):
        return {"state": "open", "label": "Market open"}
    return {"state": "closed", "label": "Market closed"}


def mcx_status(now: datetime | None = None, close_override: str | None = None) -> dict:
    from .market import commodity
    return commodity.status(now or now_ist(), close_override)


class Services:
    def __init__(self, cfg: Config, env=os.environ) -> None:
        self.cfg, self.env = cfg, env
        self.mode = "sample" if cfg.mode == "replay" else cfg.mode
        self.started_as = self.mode
        self.hub = QuoteHub()
        self.catalog = LazyCatalog()
        self.store = Store(Path(cfg.data_dir) / "terminal.db")
        self.store.retag_sample_history()
        self.chain = ChainService(self.hub, self.store)
        self.charts = ChartService(self.hub, self.store, sample=self.mode == "sample")
        self.netip = NetIp(self.store)
        self.session = None                    # KotakSession (paper / live)
        self.vault = AccountVault(Path(cfg.data_dir) / "accounts.enc", env)
        self.accounts: dict[str, dict] = {}    # all connected accounts: aid -> {id, label, ucc}
        self.sessions: dict[str, Any] = {}     # aid -> KotakSession
        self.gateways: dict[str, Any] = {}     # aid -> KotakGateway / SimBroker
        self.active_account_id: str = ""
        self._fails: dict[str, tuple[int, float]] = {}
        self._journal_loop_on = False
        self._ip_monitor_on = False
        self.feed = None
        self._connected = False
        self._connect_lock = threading.Lock()
        self._kotak_ins = None
        self.alerts = Alerts(env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", ""), self.mode, cfg.alerts)
        m = cfg.manual
        self.trading = TradingService(
            cfg, self.hub, self.catalog, lambda aid=None: self.get_gateway(aid),
            ManualLimits(m.max_lots_per_order, m.max_order_value, m.block_after_loss, m.squareoff_buffer_pct),
            on_panic=self._on_panic,
            all_gateways_provider=lambda: self.gateways,
            account_labels_provider=lambda: {aid: a.get("label", aid) for aid, a in self.accounts.items()})
        if self.mode != "sample":
            from .brokers.kotak import KotakInstruments, KotakSession
            self._default_session = KotakSession(env)
            self._kotak_ins = KotakInstruments(self._default_session, cfg)
        else:
            self._default_session = None
        self.algo = Controller(cfg, self._build_engine, self.session, ip_check=self._ip_problem)
        self.rules = RuleEngine(self.store, HubMarket(self.hub, self.chain, self.catalog), self.trading, cfg.data_dir, notify=self.alerts.notify)
        self.rules.attach(self.hub)
        self._seed_default_strategies()
        self.journal = Journal(self.store, self.trading, self.mode)
        self.history = CandleDB(Path(cfg.data_dir) / "candles.db")
        self.backtest = BacktestManager(cfg, self.history)
        self.bt_src = "sample" if self.mode == "sample" else "market"
        self.recorder = None
        if self.mode != "sample":                                       # real prices only: sample mode never records invented ticks
            self.recorder = Recorder(self.history, self.catalog, self.bt_src)
            self.hub.add_listener(self.recorder.on_quote)
            self.recorder.start()
        self.nse = NseService(SampleNse() if self.mode == "sample" else NseClient(Path(cfg.data_dir) / "nse"), self.store, self.mode)
        self.trading.on_order = self._on_order
        self._stopping = threading.Event()
        self._indices = [dict(d) for d in cfg.dashboard_indices]
        from .market import commodity
        commodity.configure(cfg.mcx_close_time)

    # ---------------------------------------------------------------- lifecycle
    @property
    def account(self) -> dict | None:
        if self.active_account_id and self.active_account_id in self.accounts:
            return self.accounts[self.active_account_id]
        if self.accounts:
            return next(iter(self.accounts.values()))
        return None

    @account.setter
    def account(self, val):
        pass

    @property
    def connected(self) -> bool:
        if self.mode == "sample":
            return bool(getattr(self, "_connected", True))
        return bool(self.accounts)

    @connected.setter
    def connected(self, val: bool):
        self._connected = bool(val)

    def get_gateway(self, aid: str | None = None):
        if aid and aid in self.gateways:
            return self.gateways[aid]
        if self.active_account_id and self.active_account_id in self.gateways:
            return self.gateways[self.active_account_id]
        if self.gateways:
            return next(iter(self.gateways.values()))
        return None

    @property
    def gateway(self):
        return self.get_gateway()

    @gateway.setter
    def gateway(self, val):
        pass

    def get_session(self, aid: str | None = None):
        if aid and aid in self.sessions:
            return self.sessions[aid]
        if self.active_account_id and self.active_account_id in self.sessions:
            return self.sessions[self.active_account_id]
        if self.sessions:
            return next(iter(self.sessions.values()))
        return getattr(self, "_default_session", None)

    @property
    def session(self):
        return self.get_session()

    @session.setter
    def session(self, val):
        pass

    @property
    def needs_connect(self) -> bool:
        return self.mode != "sample"

    def index_cards(self) -> list[dict]:
        return [{"label": d["label"], "key": f"{d['segment']}|{d['token']}"} for d in self._indices]

    def commodity_cards(self) -> list[dict]:
        """Dashboard price cards: the front-month future of Gold, Silver, Crude Oil and Natural Gas.
        Empty until the commodity contract list is loaded (it loads in the background after login)."""
        from .market import commodity
        day = now_ist().date()
        hit = getattr(self, "_cm_cards", None)
        if hit and hit[0] == day:
            return hit[1]
        out = []
        for label, name in commodity.DASHBOARD:
            try:
                inst = self.catalog.mcx_future(name)
            except Exception:
                inst = None
            if inst:
                out.append({"label": label, "key": inst.key, "name": name, "contract": inst.label(),
                            "expiry": inst.expiry.isoformat() if inst.expiry else None})
            else:
                out.append({"label": label, "key": f"mcx_fo|{name}", "name": name, "contract": f"{label} FUT",
                            "expiry": None})
        if out and self.catalog.mcx_loaded():
            self._cm_cards = (day, out)
        return out

    def start(self) -> None:
        """Called once at app start. Sample mode comes up immediately; paper/live wait for the Kotak login."""
        if self.mode == "sample":
            cat = build_catalog()
            self.catalog.set(cat)
            feed = SampleFeed(self.hub, cat)
            self.feed = feed
            self.hub.attach_feed(feed)
            self.chain.set_provider(SampleChainProvider(cat, feed.base, SAMPLE_DERIVS, feed.eq_base))
            self.gateway = SimBroker(self.hub, self.catalog, self.cfg.manual.paper_capital, self.cfg.data_dir)
            self._seed_defaults(sample=True)
            self.charts.start()
            self.hub.want("dashboard", [c["key"] for c in self.index_cards()])
            feed.start()
            self.connected = True
            self._start_journal_loop()
        else:
            self._start_ip_monitor()

    def _on_order(self, oid, inst, ticket, qty, account_id=None) -> None:
        self.journal.note_order(oid, inst, ticket, qty)
        if (ticket.tag or "manual") == "manual":                      # strategies and the algo report their own orders
            kind = {"L": "limit", "MKT": "market", "SL": "SL", "SL-M": "SL-M"}.get(ticket.order_type, ticket.order_type)
            px = f" @ {ticket.price:g}" if ticket.order_type in ("L", "SL") and ticket.price else ""
            acct_lbl = self.accounts.get(account_id, {}).get("label") if account_id else (self.account.get("label") if self.account else "")
            tag = f" [{acct_lbl}]" if acct_lbl else ""
            self.alerts.notify("order", f"Order {oid}{tag}: {'BUY' if ticket.side == 'B' else 'SELL'} {qty} {inst.label()} {kind}{px}")

    def _on_panic(self) -> None:
        self.alerts.notify("error", "STOP ALL pressed: open orders cancelled, strategies stopped, positions being closed.")
        self._stop_algo()

    def _daily_summary(self) -> None:
        """One message after the close with today's round trips. Sent at most once a day."""
        now = now_ist()
        if now.weekday() >= 5 or now < at(now.date(), parse_hm("15:35")) or not self.alerts.active or not self.cfg.alerts.daily_summary:
            return
        day = now.date().isoformat()
        if self.store.get("alerts.summary_day") == day:
            return
        self.store.set("alerts.summary_day", day)
        r = self.journal.report(frm=day, to=day)
        st = r["stats"]
        if not st["n"]:
            return
        open_note = f" {len(r['open'])} position(s) still open." if r["open"] else ""
        self.alerts.notify("summary", f"Today: {st['n']} trade(s), {st['wins']} won, {st['losses']} lost, net ₹{st['net']:,.0f} "
                                      f"(before brokerage and taxes).{open_note}")

    def _start_journal_loop(self) -> None:
        if self._journal_loop_on:
            return
        self._journal_loop_on = True

        def loop():
            while not self._stopping.wait(20):
                self.journal.sync()
                try:
                    self._daily_summary()
                except Exception:
                    log.exception("daily summary")
        threading.Thread(target=loop, name="journal-sync", daemon=True).start()

    # ------------------------------------------------------------------ static IP
    def _account_ip(self) -> tuple[str, str]:
        """Static IP saved for the connected account (or '' if none / not connected) and its label."""
        if not self.account:
            return "", ""
        try:
            return self.vault.get(self.account["id"]).get("static_ip", ""), self.account["label"]
        except Exception:
            return "", ""

    def _ip_problem(self, seen) -> str | None:
        ip, label = self._account_ip()
        return self.netip.problem_for(seen, ip, self.cfg.static_ip, label)

    def network_status(self) -> dict:
        ip, label = self._account_ip()
        seen = getattr(self.session, "my_ip", None) if self.connected and self.session is not None else None
        return self.netip.status(self.mode, seen, ip, self.cfg.static_ip, label)

    def _start_ip_monitor(self) -> None:
        """Every few minutes: look up this computer's public IP, and tell the user (Telegram) when a live setup stops matching."""
        if self._ip_monitor_on:
            return
        self._ip_monitor_on = True
        def loop():
            while True:
                try:
                    self.netip.detect(force=True)
                    text = self.netip.alert_text(self.network_status(), self.mode)
                    if text:
                        self.alerts.notify("error", text)
                except Exception:
                    log.exception("IP monitor")
                if self._stopping.wait(300):
                    return
        threading.Thread(target=loop, name="ip-monitor", daemon=True).start()

    def connect(self, totp: str | None, account_id: str = "", confirm_live: bool = False) -> None:
        """Kotak login (paper/live) as one of the saved accounts, and start of the real feed. Safe to call again after a failed attempt."""
        if self.mode == "sample":
            return
        from .brokers.kotak import KotakFeed
        from .market.catalog import KotakCatalog
        from .trading.kotak_gateway import KotakGateway
        with self._connect_lock:
            aid = account_id or self.vault.default_id()
            if not aid:
                raise RuntimeError("No account to connect. Add one under Settings, in the Accounts box, or fill in the .env file.")
            if aid in self.accounts:
                log.info("Account '%s' is already connected", aid)
                return
            acct = self.vault.get(aid)
            if self.mode == "live" and not confirm_live:
                raise RuntimeError(f"LIVE mode: confirm that orders may be REAL on the account '{acct['label']}'.")
            n, last = self._fails.get(aid, (0, 0.0))
            if n >= 3 and time.time() - last < 90:
                raise RuntimeError(f"Three login attempts in a row failed for '{acct['label']}'. Wait a minute and check the details first: "
                                   f"Kotak can lock the MPIN after repeated wrong attempts.")
            creds = self.vault.creds(aid)
            from .brokers.kotak import KotakInstruments, KotakSession
            sess = KotakSession(creds)
            try:
                sess.login(totp)
            except Exception as e:
                self._fails[aid] = (n + 1, time.time())
                msg = scrub(str(e), creds)
                if n + 1 >= 2 and "TOTP" not in msg[:12]:
                    msg += " (Kotak can lock the MPIN after repeated wrong attempts: check the details before trying again.)"
                raise RuntimeError(msg) from None
            self._fails.pop(aid, None)
            seen = sess.my_ip
            problem = self.netip.problem_for(seen, acct.get("static_ip", ""), self.cfg.static_ip, acct["label"]) if self.mode == "live" else None
            if problem:
                sess.logout()
                raise RuntimeError(problem)

            if not self.feed:
                self._kotak_ins = KotakInstruments(sess, self.cfg)
                cat = KotakCatalog(self._kotak_ins)
                for c in self.index_cards():
                    seg, tok = c["key"].split("|", 1)
                    cat.register(Instrument(c["key"], seg, tok, c["label"], c["label"], "INDEX", tick=0.05))
                self.catalog.set(cat)
                feed = KotakFeed(sess, self.cfg)
                feed.on_quote = self.hub.update
                feed.start(lambda t: None)
                self.feed = feed
                self.hub.attach_feed(feed)
                self.hub.want("dashboard", [c["key"] for c in self.index_cards()])
                from .market.chain import KotakChainProvider
                self.chain.set_provider(KotakChainProvider(sess, self.catalog))
                self._seed_defaults()
                self.charts.start()
                cat.warm()

            if self.mode == "live":
                from .trading.kotak_gateway import KotakGateway
                gw = KotakGateway(sess, self.catalog, self.hub)
            else:
                gw = SimBroker(self.hub, self.catalog, self.cfg.manual.paper_capital, self.cfg.data_dir)

            self.sessions[aid] = sess
            self.gateways[aid] = gw
            self.accounts[aid] = {"id": aid, "label": acct["label"], "ucc": acct["ucc"][:1] + "•••" + acct["ucc"][-2:]}
            if not self.active_account_id:
                self.active_account_id = aid
            self.connected = True
            self.journal.ns = self.vault.journal_namespace(aid, self.mode)
            self.store.set("accounts.last", aid)
            self._start_journal_loop()
            log.info("connected to Kotak as '%s' in %s mode (total %d accounts connected)", acct["label"], self.mode, len(self.accounts))

    # ------------------------------------------------------------------ paper <-> real switch
    def _localhost(self) -> bool:
        allow_rem = (self.env.get("ALLOW_REMOTE") or os.environ.get("ALLOW_REMOTE") or "").strip().lower()
        if allow_rem in ("yes", "true", "1") or bool(os.environ.get("RENDER")) or bool(self.env.get("RENDER")):
            return True
        return self.cfg.host in ("127.0.0.1", "localhost")

    def mode_info(self) -> dict:
        """What the Settings 'Trading mode' box shows: the mode now, and for each mode why it can or cannot be chosen."""
        why: dict[str, str] = {}
        if self.rules.running() or (self.algo.engine and not self.algo.engine.finished.is_set()):
            busy = "Stop your running strategies and the Algo first."
            why = {"paper": busy, "live": busy}
        elif self.mode == "live" and self.connected and self._open_exposure():
            why["paper"] = "You still have open real positions or orders at Kotak. Close them first, otherwise they would be hidden from this screen."
        allow_live = (self.env.get("ALLOW_LIVE") or os.environ.get("ALLOW_LIVE") or "yes").strip().lower()
        if allow_live not in ("yes", "true", "1"):
            why.setdefault("live", "Real trading is locked. Add ALLOW_LIVE=yes to your .env file and restart once to unlock it.")
        elif not self._localhost():
            why.setdefault("live", "Real trading is only allowed when the terminal is open on this computer only (not exposed on a network).")
        return {"mode": self.mode, "started_as": self.started_as, "connected": self.connected,
                "can": {"paper": self.mode != "paper" and "paper" not in why, "live": self.mode != "live" and "live" not in why},
                "why": why, "account": self.account["label"] if self.account else None}

    def _leave_sample(self, mode: str) -> None:
        """Turn the invented-data demo into a paper / real terminal: stop the sample world, forget everything it made up,
        and wait for the Kotak login. Nothing invented stays behind on screen or in the saved price history."""
        from .brokers.kotak import KotakInstruments, KotakSession
        feed = self.feed
        if feed is not None:
            feed.stop()
            t = getattr(feed, "_thread", None)
            if t is not None:
                t.join(timeout=3)
        self.feed = None
        self.hub.reset()
        self.charts.set_sample(False)
        self.charts.forget_recent()
        self.chain.set_provider(None)
        self.chain._last_series.clear()
        self.catalog.set(None)
        self.gateway = None
        self.connected = False
        self.account = None
        self.journal.ns = None
        self.trading._pos_keys = set()
        self.store.drop_derivative_items()
        self.session = KotakSession(self.env)
        self._kotak_ins = KotakInstruments(self.session, self.cfg)
        self.algo.session = self.session
        self.bt_src = "market"
        if self.recorder is None:
            self.recorder = Recorder(self.history, self.catalog, self.bt_src)
            self.hub.add_listener(self.recorder.on_quote)
            self.recorder.start()
        self.nse = NseService(NseClient(Path(self.cfg.data_dir) / "nse"), self.store, mode)
        self.mode = self.cfg.mode = mode
        self.alerts.mode = mode
        self.journal.mode = mode
        self._start_ip_monitor()
        log.warning("left the sample world: now in %s mode, waiting for the Kotak login", mode)
        self.alerts.notify("info", f"Trading mode is now {'REAL: orders go to your Kotak account' if mode == 'live' else 'PAPER: simulated orders'} (waiting for the Kotak login).")

    def _open_exposure(self) -> bool:
        """True when the broker side has open positions or live orders (used before leaving real trading)."""
        gw = self.gateway
        if gw is None:
            return False
        pos, orders = gw.positions(), gw.orders()
        if pos is None or orders is None:
            return True                                                       # could not read the broker: do not assume it is flat
        return any(r.net_qty for r in pos) or any(o.status in ("OPEN", "TRIGGER_PENDING") for o in orders)

    def set_mode(self, mode: str, confirm: str = "") -> dict:
        """Switch the whole terminal between paper (real prices, simulated orders) and real trading, without a new login.
        The choice is for this run only: after a restart the terminal starts in the mode set in config.yaml / --mode."""
        from .trading.kotak_gateway import KotakGateway
        if mode not in ("paper", "live"):
            raise RuntimeError("Choose paper or real trading.")
        with self._connect_lock:
            info = self.mode_info()
            if self.mode == mode:
                return info
            if mode in info["why"]:
                raise RuntimeError(info["why"][mode])
            if self.mode == "sample":
                self._leave_sample(mode)
                return self.mode_info()
            if self.connected:
                try:
                    self.journal.sync()                                       # save what the old mode did, under its own name
                except Exception:
                    log.exception("journal sync before mode switch")
                if mode == "live":
                    acct = self.vault.get(self.account["id"]) if self.account else {}
                    problem = self.netip.problem_for(self.session.my_ip, acct.get("static_ip", ""), self.cfg.static_ip,
                                                     self.account["label"] if self.account else "")
                    if problem:
                        raise RuntimeError(problem)
                    gateway = KotakGateway(self.session, self.catalog, self.hub)
                else:
                    gateway = SimBroker(self.hub, self.catalog, self.cfg.manual.paper_capital, self.cfg.data_dir)
            old = self.mode
            self.mode = self.cfg.mode = mode
            self.alerts.mode = mode
            self.journal.mode = mode
            if self.connected:
                self.gateway = gateway
                self.journal.ns = self.vault.journal_namespace(self.account["id"], mode)
            log.warning("trading mode switched %s -> %s", old, mode)
            self.alerts.notify("info", f"Trading mode is now {'REAL: orders go to your Kotak account' if mode == 'live' else 'PAPER: simulated orders'}.")
            return self.mode_info()

    def disconnect(self, account_id: str | None = None) -> None:
        """Log out of Kotak so account(s) can be disconnected. Refused while anything is trading."""
        if self.mode == "sample":
            raise RuntimeError("Sample mode has no broker account.")
        with self._connect_lock:
            if not self.connected and not self.accounts:
                return
            if self.rules.running():
                raise RuntimeError("Stop your running strategies first (My strategies), then disconnect.")
            if self.algo.engine and not self.algo.engine.finished.is_set():
                raise RuntimeError("Stop the Algo first, then disconnect.")
            try:
                self.journal.sync()
            except Exception:
                log.exception("journal sync before disconnect")

            target_aids = [account_id] if account_id and account_id in self.accounts else list(self.accounts.keys())
            for aid in target_aids:
                sess = self.sessions.pop(aid, None)
                self.gateways.pop(aid, None)
                acct_info = self.accounts.pop(aid, None)
                lbl = acct_info.get("label", aid) if acct_info else aid
                if sess:
                    try:
                        sess.logout()
                    except Exception:
                        log.exception("logout %s", lbl)
                log.info("disconnected from Kotak ('%s')", lbl)

            if not self.accounts:
                try:
                    if self.feed:
                        self.feed.stop()
                except Exception:
                    log.exception("feed stop")
                self.connected = False
                self.feed = None
                self.active_account_id = ""
                self.journal.ns = None
            else:
                if self.active_account_id not in self.accounts:
                    self.active_account_id = next(iter(self.accounts.keys()))
                self.connected = True

    def shutdown(self) -> None:
        self._stopping.set()
        self.charts.stop()
        self.backtest.stop()
        if self.recorder:
            self.recorder.stop()
        self._stop_algo()
        try:
            if self.gateway is not None:
                self.journal.sync()
        except Exception:
            log.exception("journal sync at shutdown")
        try:
            if self.feed:
                self.feed.stop()
            if self.session and self.session.logged_in:
                self.session.logout()
        except Exception:
            log.exception("shutdown")

    def _stop_algo(self) -> None:
        try:
            self.rules.kill_all()
        except Exception:
            log.exception("could not stop the strategies")
        try:
            if self.algo.engine and not self.algo.engine.finished.is_set():
                self.algo.kill()
        except Exception:
            log.exception("could not stop the algo")

    # ------------------------------------------------------------------- algo
    def _build_engine(self) -> Engine:
        cfg = self.cfg
        if self.mode == "sample":                                   # replay a synthetic day through the real algo engine
            day = now_ist().date()
            ecfg = dataclasses.replace(cfg, mode="replay")
            ecfg.strategy = dataclasses.replace(cfg.strategy, strike_window=min(cfg.strategy.strike_window, 3))
            return Engine(ecfg, SyntheticInstruments(ecfg, day),
                          ReplayFeed(build_day(day, cfg.demo_scenario, underlying=cfg.inst.underlying)), PaperOrders())
        from .brokers.kotak import KotakOrders
        orders = PaperOrders() if self.mode == "paper" else KotakOrders(self.session, cfg)
        eng = Engine(cfg, self._kotak_ins, HubEngineFeed(self.hub), orders)
        eng.notify = self.alerts.notify
        return eng

    # --------------------------------------------------------------- defaults
    def _seed_default_strategies(self) -> None:
        try:
            if not self.store.list_strategies():
                from .rules import strategies
                tpl_map = {t["id"]: t for t in strategies.templates()}
                seeds = [
                    ("orb", "Opening-Range Breakout (ORB)"),
                    ("leg_straddle", "Short Straddle 09:20"),
                    ("leg_strangle_delta", "Short Strangle by Delta"),
                    ("leg_condor", "Iron Condor with Hedges"),
                ]
                for tid, custom_name in seeds:
                    if tid in tpl_map:
                        t = tpl_map[tid]
                        st, _, _ = strategies.parse(t["spec"])
                        if st:
                            d = st.to_json()
                            d["name"] = custom_name
                            self.store.save_strategy(None, custom_name, d)
                            log.info("Seeded pre-built strategy: %s", custom_name)
        except Exception as e:
            log.warning("could not seed default strategies: %r", e)

    def _seed_defaults(self, sample: bool = False) -> None:
        if self.store.watchlists():
            return
        wl = self.store.create_watchlist("Watchlist 1")
        if sample:
            picks = [f"{d['segment']}|{d['token']}" for d in self._indices[:3]] + [f"nse_cm|{t}" for _, _, t, _ in SAMPLE_EQUITIES[:8]]
        else:
            picks = [f"{d['segment']}|{d['token']}" for d in self._indices[:3]]
        for k in picks:
            inst = self.catalog.get(k)
            if inst:
                self.store.add_item(wl, k, instrument_meta(inst))


def instrument_meta(i: Instrument) -> dict:
    d = i.to_dict()
    d.pop("key", None)
    return d
