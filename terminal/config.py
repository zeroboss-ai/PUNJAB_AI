from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .clock import parse_hm


@dataclass
class StrategyCfg:
    range_start: str = "09:15"        # first candle of the opening range
    range_candles: int = 4            # 9:15, 9:16, 9:17, 9:18  -> range closes at 09:19
    sl_candle: str = "09:16"          # SL = HIGH of this option candle
    entry_cutoff: str = "15:00"       # no new entries after this
    exit_time: str = "15:10"          # square off everything
    max_trades_per_day: int = 1
    lots: int = 1
    entry_limit_buffer: float = 1.0   # sell limit placed this many Rs below LTP so it fills
    max_tick_age_s: float = 10.0      # ignore a signal if any of the 3 prices is older than this
    strike_window: int = 8            # subscribe ATM +/- this many strikes before the open
    expiry_offset: int = 0            # 0 = nearest expiry (today on expiry day), 1 = next expiry
    min_lead_s: float = 5.0           # option feed must be subscribed this long before the open


@dataclass
class RiskCfg:
    max_daily_loss: float = 0.0       # rupees; 0 = no limit (you set your own rule)
    max_lots: int = 1
    exchange_sl: bool = True          # also keep a stop-loss order resting at the exchange
    sl_limit_buffer: float = 5.0      # exchange SL-L limit price = trigger + this
    entry_timeout_s: float = 5.0
    exit_timeout_s: float = 3.0
    exit_retries: int = 5
    exit_buffer: float = 1.0          # buy-back limit = LTP + this (grows with each retry)
    feed_stale_s: float = 30.0        # in a trade with no ticks this long -> square off
    sl_poll_s: float = 3.0            # how often to check the exchange SL order status
    unclear_retries: int = 5          # order status unreadable: re-check status/positions this many times
    unclear_wait_s: float = 1.0       #   ...waiting this long between checks, then halt and alert you


@dataclass
class ManualCfg:
    max_lots_per_order: int = 20          # a single manual order may not exceed this many lots
    max_order_value: float = 2_500_000.0  # ... or this value in rupees (qty x price)
    block_after_loss: bool = True         # after the daily loss limit, only closing orders are accepted
    squareoff_buffer_pct: float = 3.0     # square-off sends a limit this % through the last price so it fills
    paper_capital: float = 1_000_000.0    # starting balance of the simulated account (sample / paper mode)


@dataclass
class AlertsCfg:
    enabled: bool = True              # master switch; alerts also need TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
    strategy_events: bool = True      # strategy / algo started, signals, entries, exits, finished
    warnings: bool = True             # warnings (errors and ACTION REQUIRED are always sent)
    orders: bool = True               # orders you place by hand
    daily_summary: bool = True        # one message after the close with today's round trips


@dataclass
class InstrumentCfg:
    underlying: str
    index_segment: str
    index_token: str
    option_segment: str
    strike_step: float
    paper_lot_size: int               # used ONLY by replay/synthetic data; live reads the scrip master
    index_aliases: list[str] = field(default_factory=list)


@dataclass
class Config:
    mode: str = "paper"               # sample | paper | live | replay
    instrument: str = "NIFTY"
    strategy: StrategyCfg = field(default_factory=StrategyCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    manual: ManualCfg = field(default_factory=ManualCfg)
    alerts: AlertsCfg = field(default_factory=AlertsCfg)
    instruments: dict = field(default_factory=dict)
    dashboard_indices: list = field(default_factory=lambda: [
        {"label": "NIFTY 50", "segment": "nse_cm", "token": "Nifty 50"},
        {"label": "NIFTY BANK", "segment": "nse_cm", "token": "Nifty Bank"},
        {"label": "SENSEX", "segment": "bse_cm", "token": "SENSEX"},
        {"label": "NIFTY FIN SERVICE", "segment": "nse_cm", "token": "Nifty Fin Service"},
        {"label": "NIFTY MID SELECT", "segment": "nse_cm", "token": "NIFTY MID SELECT"},
        {"label": "INDIA VIX", "segment": "nse_cm", "token": "India VIX"}])
    demo_scenario: str = "put_sell"   # sample mode: which synthetic day the "Run demo" button replays
    poll_interval_s: float = 0.5
    mcx_close_time: str = ""          # commodities: "" = automatic by season (23:30 in US summer time, 23:55 in winter); or "HH:MM"
    static_ip: str = ""               # live only: the static IP you registered with Kotak; refuse to start if different
    data_dir: str = "data"
    log_dir: str = "logs"
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def inst(self) -> InstrumentCfg:
        return self.instruments[self.instrument]


def _build(cls, raw, where: str):
    raw = raw or {}
    names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(raw) - names
    if unknown:
        raise ValueError(f"Unknown key(s) in {where}: {sorted(unknown)} (typo in config.yaml?)")
    return cls(**raw)


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    strat = _build(StrategyCfg, raw.pop("strategy", None), "strategy")
    risk = _build(RiskCfg, raw.pop("risk", None), "risk")
    manual = _build(ManualCfg, raw.pop("manual", None), "manual")
    alerts = _build(AlertsCfg, raw.pop("alerts", None), "alerts")
    insts = {
        k.upper(): _build(InstrumentCfg, v, f"instruments.{k}")
        for k, v in (raw.pop("instruments", {}) or {}).items()
    }
    cfg = _build(Config, {**raw, "strategy": strat, "risk": risk, "manual": manual, "alerts": alerts, "instruments": insts}, "top level")
    cfg.instrument = str(cfg.instrument).upper()
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.mode not in {"sample", "paper", "live", "replay"}:
        raise ValueError("mode must be sample, paper, live or replay")
    for d in cfg.dashboard_indices:
        if not isinstance(d, dict) or not {"label", "segment", "token"} <= set(d):
            raise ValueError("each dashboard_indices entry needs label, segment and token")
    if cfg.mcx_close_time:
        parse_hm(cfg.mcx_close_time)
    if cfg.instrument not in cfg.instruments:
        raise ValueError(f"instrument {cfg.instrument} is not defined under instruments:")
    s, r = cfg.strategy, cfg.risk
    for name in ("range_start", "sl_candle", "entry_cutoff", "exit_time"):
        parse_hm(getattr(s, name))
    if s.range_candles < 1 or s.lots < 1 or s.max_trades_per_day < 1:
        raise ValueError("range_candles, lots and max_trades_per_day must be >= 1")
    if r.max_daily_loss < 0 or r.max_lots < 1:
        raise ValueError("risk.max_daily_loss must be 0 (no limit) or more, and risk.max_lots >= 1")
    if not (parse_hm(s.range_start) <= parse_hm(s.sl_candle)):
        raise ValueError("sl_candle must not be before range_start")
    if not (parse_hm(s.entry_cutoff) <= parse_hm(s.exit_time)):
        raise ValueError("entry_cutoff must not be after exit_time")
