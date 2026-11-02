"""
Static IP handling.

Kotak (under SEBI's rules for API trading) accepts orders only from the IP address you registered in its API dashboard.
The address has to be the same in two places: registered with Kotak, and the one Kotak actually sees when this terminal
connects. This module covers the terminal's side of that, in two ways the user can choose between:

  auto  The terminal finds the public IP address (IPv4 and IPv6) of this computer's internet connection and shows it, so
        you can register it in Kotak. You then press "I registered it" and the terminal remembers which address(es)
        you registered. This only works for as long as your provider keeps giving you the same address.
  own   You bought a static IP (or run the terminal on a server that has one). You type it in here (IPv4 and/or IPv6) and
        register the same one with Kotak.

The one fact that matters is what Kotak sees at login (`session.my_ip`). It is compared with the addresses you registered
here, in live mode, before any order can be sent. Finding the IP needs a third-party "what is my IP" service; it is only
used to show you the address and to notice when it changed - it can never make an order go out or be blocked by itself.

Everything here is best effort and never raises into the trading path.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

log = logging.getLogger("netip")

KEY = "network.ip"
MODES = ("auto", "own")
FAMILIES = ("v4", "v6")

# Plain-text "what is my IP" endpoints. Each family is asked over its own kind of connection, so a computer that has both
# IPv4 and IPv6 reports both. Any one answer per family is enough; the rest are fall-backs.
SOURCES = {
    "v4": ["https://api.ipify.org", "https://ipv4.icanhazip.com", "https://v4.ident.me"],
    "v6": ["https://api6.ipify.org", "https://ipv6.icanhazip.com", "https://v6.ident.me"],
}
TIMEOUT_S = 4.0
CACHE_S = 30.0


class NetIpError(ValueError):
    """A message meant for the person using the terminal."""


# ----------------------------------------------------------------------------------------------------- parsing
def to_ip(text) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse one IP address as typed or as Kotak reports it (brackets, a %zone, or ::ffff:1.2.3.4 are tolerated)."""
    s = str(text or "").strip().strip("[]").split("%", 1)[0]
    ip = ipaddress.ip_address(s)                       # ValueError if it is not an IP
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def norm(text) -> str:
    """Canonical text of an IP ('' if it is not one), so '2401:0db8::1' and '2401:db8::1' compare equal."""
    try:
        return str(to_ip(text))
    except ValueError:
        return ""


def same_ip(a, b) -> bool:
    na, nb = norm(a), norm(b)
    return bool(na) and na == nb


def family(ip: str) -> str:
    return "v4" if ":" not in ip else "v6"


def parse_ips(text, *, public_only: bool = False, what: str = "static IP") -> list[str]:
    """
    '49.36.1.2' or '49.36.1.2, 2401:4900:1::7' -> canonical list, at most one per family.
    public_only refuses addresses that cannot be an internet address (home-network 192.168.x.x, loopback, ...).
    """
    parts = [p for p in re.split(r"[,;\s]+", str(text or "").strip()) if p]
    out: list[str] = []
    for p in parts:
        try:
            ip = to_ip(p)
        except ValueError:
            raise NetIpError(f"'{p}' is not a valid IP address. The {what} looks like 49.36.10.20 (IPv4) or 2401:4900:1c2a::17 (IPv6).") from None
        if public_only and not ip.is_global:
            raise NetIpError(f"{ip} is not a public internet address ({_why_not_public(ip)}). Kotak needs the public {what} - "
                             f"the one the internet sees, not the address inside your home network.")
        s = str(ip)
        if family(s) in [family(o) for o in out]:
            raise NetIpError(f"Enter at most one IPv4 and one IPv6 address for the {what}.")
        out.append(s)
    return out


def _why_not_public(ip) -> str:
    if ip.is_loopback:
        return "this computer talking to itself"
    if ip.is_link_local:
        return "a link-local address"
    if ip.is_multicast:
        return "a multicast address"
    if ip.is_private:
        return "a private or reserved address that the internet cannot reach"
    return "reserved"


def covers(expected: list[str], seen) -> bool:
    return any(same_ip(seen, e) for e in expected)


# ------------------------------------------------------------------------------------------------------ detection
def http_fetch(fam: str) -> tuple[str, str]:
    """Ask the 'what is my IP' services over IPv4 or IPv6 only. Returns (ip, error); exactly one of them is non-empty."""
    import httpx
    bind = "0.0.0.0" if fam == "v4" else "::"
    last = ""
    for url in SOURCES[fam]:
        try:
            # A transport bound to one address family: an IPv6-only request cannot silently fall back to IPv4.
            with httpx.Client(transport=httpx.HTTPTransport(local_address=bind), timeout=TIMEOUT_S, follow_redirects=False) as c:
                r = c.get(url, headers={"User-Agent": "punjab-ai-terminal"})
            r.raise_for_status()
            ip = parse_public(r.text[:100], fam)
            if ip:
                return ip, ""
            last = "the service did not answer with an IP address"
        except Exception as e:                                         # offline, no IPv6, blocked, timeout ...
            last = _short_error(e)
    return "", last or "no answer"


def _short_error(e: Exception) -> str:
    t = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    low = t.lower()
    if "unreachable" in low or "no route" in low or "address family" in low or "cannot assign" in low or "network is down" in low:
        return "this connection has no " + ("IPv6" if "::" in t or "ipv6" in low else "such") + " route"
    if "timed out" in low or "timeout" in low:
        return "timed out"
    return t[:120]


def parse_public(text: str, fam: str) -> str:
    """The first line of a service's answer as a public IP of the wanted family, else ''."""
    try:
        ip = to_ip((text or "").strip().splitlines()[0])
    except (ValueError, IndexError):
        return ""
    if not ip.is_global or ("v4" if ip.version == 4 else "v6") != fam:
        return ""
    return str(ip)


class NetIp:
    """Remembers the user's choice (in the small settings table) and the last detected addresses (in memory)."""

    def __init__(self, store, fetch: Callable[[str], tuple[str, str]] | None = None, clock: Callable[[], float] = time.time) -> None:
        self.store, self._clock = store, clock
        self._fetch = fetch or (lambda fam: http_fetch(fam))           # looked up at call time, so tests can replace http_fetch
        self._lock = threading.Lock()
        self._detected: dict = {"at": 0.0, "v4": "", "v6": "", "err": {"v4": "", "v6": ""}}
        self._alerted = ""

    # ------------------------------------------------------------------ saved choice
    def policy(self) -> dict:
        raw = self.store.get(KEY) or {}
        mode = raw.get("mode") if raw.get("mode") in MODES else "auto"
        own = {f: norm((raw.get("own") or {}).get(f)) for f in FAMILIES}
        reg = raw.get("registered") or {}
        return {"mode": mode, "own": own,
                "registered": {"v4": norm(reg.get("v4")), "v6": norm(reg.get("v6")), "at": float(reg.get("at") or 0)}}

    def _save(self, pol: dict) -> None:
        self.store.set(KEY, pol)

    def set_policy(self, mode: str, own_text: str = "") -> dict:
        if mode not in MODES:
            raise NetIpError("Choose either 'detect my IP automatically' or 'I have my own static IP'.")
        pol = self.policy()
        if mode == "own":
            ips = parse_ips(own_text, public_only=True)
            if not ips:
                raise NetIpError("Enter the static IP you bought: an IPv4 address, an IPv6 address, or both.")
            pol["own"] = {"v4": next((i for i in ips if family(i) == "v4"), ""), "v6": next((i for i in ips if family(i) == "v6"), "")}
        pol["mode"] = mode
        self._save(pol)
        self._alerted = ""
        return pol

    def confirm_registered(self, v4: bool, v6: bool) -> dict:
        """The user says they registered the address(es) shown. The address recorded is the one detected right now, by the terminal itself."""
        if not (v4 or v6):
            raise NetIpError("Tick the address(es) you registered in Kotak.")
        d = self.detect(force=True)
        pol = self.policy()
        reg = {"v4": "", "v6": "", "at": self._clock()}
        for f, wanted in (("v4", v4), ("v6", v6)):
            if not wanted:
                continue
            if not d[f]:
                raise NetIpError(f"This computer's {'IPv4' if f == 'v4' else 'IPv6'} address could not be found right now"
                                 f"{' (' + d['err'][f] + ')' if d['err'][f] else ''}, so there is nothing to confirm. Press 'Detect again' first.")
            reg[f] = d[f]
        pol["registered"] = reg
        pol["mode"] = "auto"
        self._save(pol)
        self._alerted = ""
        return pol

    # --------------------------------------------------------------------- detection
    def detect(self, force: bool = False) -> dict:
        with self._lock:
            now = self._clock()
            if not force and self._detected["at"] and now - self._detected["at"] < CACHE_S:
                return self._detected
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {f: ex.submit(self._safe_fetch, f) for f in FAMILIES}
            res = {f: fu.result() for f, fu in futs.items()}
        with self._lock:
            self._detected = {"at": self._clock(), "v4": res["v4"][0], "v6": res["v6"][0],
                              "err": {"v4": res["v4"][1], "v6": res["v6"][1]}}
            return self._detected

    def _safe_fetch(self, fam: str) -> tuple[str, str]:
        try:
            ip, err = self._fetch(fam)
            ip = norm(ip)
            if ip and family(ip) != fam:
                return "", "wrong kind of address"
            return ip, ("" if ip else (err or "no answer"))
        except Exception as e:
            log.warning("IP detection (%s) failed: %s", fam, e)
            return "", _short_error(e)

    def last(self) -> dict:
        with self._lock:
            return dict(self._detected)

    # ------------------------------------------------------------------------- policy
    def expected(self, account_ip: str = "", config_ip: str = "") -> tuple[list[str], str]:
        """The addresses Kotak must see, and where they come from: 'account', 'terminal' or 'config' ('' = nothing set)."""
        ips = _loose(account_ip)
        if ips:
            return ips, "account"
        pol = self.policy()
        if pol["mode"] == "own":
            ips = [i for i in (pol["own"]["v4"], pol["own"]["v6"]) if i]
        else:
            ips = [i for i in (pol["registered"]["v4"], pol["registered"]["v6"]) if i]
        if ips:
            return ips, "terminal"
        ips = _loose(config_ip)
        return (ips, "config") if ips else ([], "")

    def problem_for(self, seen, account_ip: str = "", config_ip: str = "", label: str = "") -> str | None:
        """Text explaining why an order-sending session must not start, or None. Used at live connect and before the algo starts."""
        seen = (seen or "").strip() if isinstance(seen, str) else seen
        if not seen:
            return None                                            # Kotak did not tell us; Kotak itself will reject a wrong IP
        exp, src = self.expected(account_ip, config_ip)
        if not exp or covers(exp, seen):
            return None
        where = {"account": f"the static IP saved for '{label}'" if label else "the static IP saved for this account",
                 "terminal": "the IP you registered in Settings, Static IP", "config": "static_ip in config.yaml"}[src]
        shown = " or ".join(exp)
        base = f"Kotak sees this computer as {seen}, but {where} is {shown}. Orders from a different IP are rejected by Kotak. "
        d = self.last()
        if src == "terminal" and self.policy()["mode"] == "auto" and any(same_ip(seen, d[f]) for f in FAMILIES):
            return base + "Your internet address has changed since you registered it. Register the new one in Kotak (Trade API, API Dashboard) and press 'I registered it' in Settings, Static IP."
        if src == "terminal" and self.policy()["mode"] == "own":
            return base + "This computer is not going out through your static IP. Run the terminal from the computer or server that has it."
        return base + "Run the terminal from the registered computer, or update the IP."

    # --------------------------------------------------------------------------- view
    def status(self, mode: str, seen=None, account_ip: str = "", config_ip: str = "", account_label: str = "") -> dict:
        """Everything the Settings panel shows. Contains no secrets."""
        pol, d = self.policy(), self.last()
        exp, src = self.expected(account_ip, config_ip)
        verdict, message = self._verdict(pol, d, exp, src, seen, mode)
        return {
            "ok": True, "app_mode": mode, "mode": pol["mode"],
            "detected": {"at": d["at"] or None, "v4": d["v4"], "v6": d["v6"], "errors": d["err"]},
            "own": pol["own"], "registered": pol["registered"] if pol["registered"]["at"] else None,
            "expected": exp, "source": src, "account_label": account_label if src == "account" else "",
            "kotak_sees": norm(seen) or None, "verdict": verdict, "message": message,
            "bad": verdict in ("changed", "mismatch"),
        }

    def _verdict(self, pol: dict, d: dict, exp: list[str], src: str, seen, mode: str) -> tuple[str, str]:
        seen_n = norm(seen)
        if not exp:
            if mode == "live":
                return "unset", "No static IP is set up. Kotak rejects orders from an address you have not registered with it. Set one up below before you trade."
            return "unset", "No static IP is set up yet. Do this before you go live: pick one of the two options below."
        who = {"account": "the account's saved IP", "terminal": "your registered IP", "config": "the IP in config.yaml"}[src]
        if seen_n:                                                  # the one test that counts: what Kotak itself sees
            if covers(exp, seen_n):
                return "ok", f"Kotak sees this computer as {seen_n}, which is {who}."
            return "mismatch", f"Kotak sees this computer as {seen_n}, but {who} is {' or '.join(exp)}. Orders would be rejected."
        found = [d[f] for f in FAMILIES if d[f]]
        if not found:
            return "unchecked", "Could not find this computer's public IP just now (offline, or the lookup services are blocked). Kotak's own check happens when you connect."
        bad = [ip for ip in found if any(family(e) == family(ip) for e in exp) and not covers(exp, ip)]
        if bad:
            if src == "terminal" and pol["mode"] == "auto":
                return "changed", f"This connection's IP is now {' and '.join(bad)}, not {' or '.join(exp)} as registered. Register the new address in Kotak and confirm it here, or orders will be rejected."
            return "mismatch", f"This connection's IP is {' and '.join(bad)}, but {who} is {' or '.join(exp)}. Orders would be rejected until the terminal runs from the registered address."
        extra = [ip for ip in found if not any(family(e) == family(ip) for e in exp)]
        if extra:
            kind = "IPv6" if family(extra[0]) == "v6" else "IPv4"
            return "partial", (f"This computer also has the {kind} address {extra[0]}, which is not registered. If Kotak reaches you over {kind}, "
                               f"it will see that address instead. Register it too, or connect once: Kotak then shows the exact address it sees.")
        return "ok", "The IP this connection uses matches what you registered."

    def alert_text(self, st: dict, mode: str) -> str | None:
        """Text to send to Telegram once, when a live setup turns bad. None when nothing new needs saying."""
        if mode != "live" or not st["bad"]:
            self._alerted = ""
            return None
        sig = f"{st['verdict']}|{st['detected']['v4']}|{st['detected']['v6']}|{','.join(st['expected'])}"
        if sig == self._alerted:
            return None
        self._alerted = sig
        return f"ACTION REQUIRED: {st['message']} (Settings, Static IP)"


def _loose(text) -> list[str]:
    """Saved account / config values: anything that parses is kept (no public-address test, so old settings keep working)."""
    try:
        return parse_ips(text)
    except NetIpError:
        return []


def problem_for_config(config_ip: str, seen) -> str | None:
    """Stand-alone check used when no Services object exists (the plain algo controller): config.yaml's static_ip against what Kotak sees."""
    exp = _loose(config_ip)
    if not exp or not seen or covers(exp, seen):
        return None
    return (f"Kotak sees this computer as {seen}, but config.yaml static_ip is {' or '.join(exp)}. "
            f"Orders from a different IP are rejected - run the terminal from the registered server.")
