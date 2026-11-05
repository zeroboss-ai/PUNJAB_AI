"""One front door for both kinds of strategy: rule blocks (version 1) and the leg builder (version 2)."""
from __future__ import annotations

from typing import Any

from . import legspec, spec


def parse(raw: Any):
    """(strategy, errors, warnings) for either kind. The kind is decided by the spec itself (version 2 / a 'legs' list)."""
    return legspec.parse(raw) if legspec.is_leg_spec(raw) else spec.parse(raw)


def describe(st) -> list[str]:
    return legspec.describe(st) if isinstance(st, legspec.LegStrategy) else spec.describe(st)


def templates() -> list[dict]:
    return ([{**t, "engine": "legs"} for t in legspec.templates()] +
            [{**t, "engine": "rules", "title": t["title"] + " (rule blocks)"} for t in spec.templates()])


def make_runner(sid: int, st, env, account_id: str = ""):
    from .legrunner import LegRunner
    from .runner import Runner
    return LegRunner(sid, st, env, account_id=account_id) if isinstance(st, legspec.LegStrategy) else Runner(sid, st, env, account_id=account_id)
