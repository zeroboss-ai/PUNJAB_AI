"""
Saved Kotak Neo accounts.

You can keep several accounts (your own, a family member's, a student's - each needs their own Kotak API access and their own
registered static IP) and choose which one the terminal connects to. One account is connected at a time.

Where the secrets live: `data/accounts.enc`, one file, encrypted (Fernet: AES-128-CBC + HMAC) with a key derived from your
DASHBOARD_PASSWORD (scrypt + a random salt). That protects the file if someone copies or backs up the data folder without
your .env. It does NOT protect against someone who has both the data folder and your .env, or who can run code on this
computer while the terminal runs - at that point they can read the keys from memory anyway. If you change DASHBOARD_PASSWORD,
the saved accounts cannot be opened any more and must be added again (the screen tells you so; nothing is deleted).

Secrets (API token, MPIN, TOTP secret) never leave this module in readable form except to the Kotak login itself: the API
returns only masked values and "saved" flags.

An account written in the .env file (NEO_CONSUMER_KEY ...) still works and shows up as a read-only account called ".env file".
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from .netip import NetIpError, parse_ips

MAX_ACCOUNTS = 10
ENV_ID = "env"
SECRET_FIELDS = ("consumer_key", "mpin", "totp_secret")
_ENV_NAMES = {"consumer_key": "NEO_CONSUMER_KEY", "mobile": "NEO_MOBILE_NUMBER", "ucc": "NEO_UCC", "mpin": "NEO_MPIN",
              "totp_secret": "NEO_TOTP_SECRET"}


class VaultError(ValueError):
    """A problem the person can fix; the message is shown to them."""


def _tail(s: str, n: int = 3) -> str:
    return ("•" * 4 + s[-n:]) if len(s) > n + 1 else "•" * len(s)


def mask_mobile(m: str) -> str:
    d = re.sub(r"\D", "", m)
    return f"+91 ••••• ••{d[-3:]}" if len(d) >= 10 else "•" * 6


def mask_ucc(u: str) -> str:
    return (u[:1] + "•" * max(2, len(u) - 3) + u[-2:]) if len(u) > 4 else "•" * len(u)


# ---------------------------------------------------------------------------------------------------- validation
def clean_mobile(raw: str) -> str:
    d = re.sub(r"[\s\-()]", "", raw or "")
    if d.startswith("+"):
        d = d[1:]
    if not d.isdigit():
        raise VaultError("The mobile number should contain digits only, like +919876543210.")
    if len(d) == 12 and d.startswith("91"):
        d = d[2:]
    if len(d) != 10 or d[0] not in "6789":
        raise VaultError("The mobile number must be a 10-digit Indian number (with or without +91).")
    return "+91" + d


def clean_fields(f: dict[str, Any], *, partial: bool, current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate and normalise. With partial=True, empty secret fields mean 'keep what is saved'."""
    cur = current or {}
    out: dict[str, Any] = {}
    label = str(f.get("label", "")).strip()
    if label or not partial:
        if not 1 <= len(label) <= 40:
            raise VaultError("Give the account a name (1 to 40 characters), for example 'My account'.")
        if any(ord(c) < 32 for c in label):
            raise VaultError("The account name has characters that are not allowed.")
        out["label"] = label
    else:
        out["label"] = cur.get("label", "")

    def need(name: str, text: str) -> str:
        v = str(f.get(name, "")).strip()
        if not v:
            if partial and cur.get(name):
                return cur[name]
            raise VaultError(f"{text} is required.")
        return v

    ck = need("consumer_key", "The API token (consumer key)")
    if not 8 <= len(ck) <= 300 or re.search(r"\s", ck):
        raise VaultError("The API token looks wrong: it should be one long piece of text without spaces. Copy it again from the Kotak Neo API dashboard.")
    out["consumer_key"] = ck
    out["mobile"] = clean_mobile(need("mobile", "The mobile number"))
    ucc = need("ucc", "The client ID (UCC)").upper()
    if not re.fullmatch(r"[A-Z0-9]{2,20}", ucc):
        raise VaultError("The client ID (UCC) should be letters and digits only, like AB123.")
    out["ucc"] = ucc
    mpin = need("mpin", "The MPIN")
    if not re.fullmatch(r"\d{4,8}", mpin):
        raise VaultError("The MPIN should be 4 to 8 digits (Kotak MPINs are 6 digits).")
    out["mpin"] = mpin

    ts = re.sub(r"\s", "", str(f.get("totp_secret", ""))).upper()
    if f.get("clear_totp_secret"):
        ts = ""
    elif not ts and partial:
        ts = cur.get("totp_secret", "")
    if ts:
        try:
            import pyotp
            pyotp.TOTP(ts).now()
        except Exception:
            raise VaultError("The TOTP secret is not valid. It is the long text (letters and digits 2-7) shown when you set up the authenticator, not the 6-digit code.")
        if len(ts) < 16:
            raise VaultError("The TOTP secret is too short. It is the long setup key, not the 6-digit code.")
    out["totp_secret"] = ts

    try:
        ip = ", ".join(parse_ips(f.get("static_ip", "")))
    except NetIpError:
        raise VaultError("The static IP is not a valid IP address. Use something like 49.36.10.20, or an IPv4 and an IPv6 address separated by a comma.") from None
    out["static_ip"] = ip
    return out


# ------------------------------------------------------------------------------------------------------- the vault
class AccountVault:
    def __init__(self, path: str | Path, env=os.environ) -> None:
        self.path = Path(path)
        self.env = env
        self._lock = threading.RLock()
        self._key: bytes | None = None
        self._data: dict[str, Any] = {"accounts": [], "default": ""}
        self.readable = True
        self.problem = ""

    # ---- opening
    def unlock(self, password: str) -> None:
        """Derive the key from the dashboard password and read the file. Never raises: a problem is reported in .problem."""
        with self._lock:
            self._pw = password
            self.readable, self.problem = True, ""
            if not self.path.exists():
                self._salt = secrets.token_bytes(16)
                self._key = self._derive(password, self._salt)
                return
            try:
                blob = json.loads(self.path.read_text())
                self._salt = base64.b64decode(blob["salt"])
                self._key = self._derive(password, self._salt)
                from cryptography.fernet import Fernet, InvalidToken
                try:
                    raw = Fernet(self._key).decrypt(blob["token"].encode())
                except InvalidToken:
                    self.readable = False
                    self.problem = ("The saved accounts cannot be opened with the current DASHBOARD_PASSWORD (it was probably changed). "
                                    "Use the old password again, or add the accounts again. Nothing has been deleted.")
                    return
                self._data = json.loads(raw)
                self._data.setdefault("accounts", [])
                self._data.setdefault("default", "")
            except Exception as e:
                self.readable = False
                self.problem = f"The saved accounts file could not be read ({type(e).__name__}). It was left untouched."

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        import hashlib
        return base64.urlsafe_b64encode(hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32))

    def _need_open(self) -> None:
        if self._key is None:
            raise VaultError("The account store is not unlocked.")
        if not self.readable:
            raise VaultError(self.problem)

    def _save(self) -> None:
        from cryptography.fernet import Fernet
        token = Fernet(self._key).encrypt(json.dumps(self._data).encode()).decode()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"v": 1, "salt": base64.b64encode(self._salt).decode(), "token": token}, fh)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # ---- the account(s) that may be written in .env or environment variables
    def _env_accounts(self) -> list[dict[str, Any]]:
        e = self.env
        out = []
        vals1 = {k: str(e.get(n, "") or "").strip() for k, n in _ENV_NAMES.items()}
        if all(vals1[k] for k in ("consumer_key", "mobile", "ucc", "mpin")):
            out.append({"id": ENV_ID, "label": ".env file", **vals1, "ucc": vals1["ucc"].upper(), "static_ip": "", "source": "env"})

        for idx in range(2, 10):
            for pfx in (f"NEO_{idx}_", f"NEO_ACCOUNT_{idx}_", f"ACCOUNT_{idx}_"):
                ck = str(e.get(f"{pfx}CONSUMER_KEY", "") or "").strip()
                mob = str(e.get(f"{pfx}MOBILE_NUMBER", "") or e.get(f"{pfx}MOBILE", "") or "").strip()
                ucc = str(e.get(f"{pfx}UCC", "") or "").strip()
                mpin = str(e.get(f"{pfx}MPIN", "") or "").strip()
                totp_sec = str(e.get(f"{pfx}TOTP_SECRET", "") or "").strip()
                lbl = str(e.get(f"{pfx}LABEL", "") or f"Account {idx} (.env)").strip()
                if ck and mob and ucc and mpin:
                    out.append({
                        "id": f"env_{idx}",
                        "label": lbl,
                        "consumer_key": ck,
                        "mobile": mob,
                        "ucc": ucc.upper(),
                        "mpin": mpin,
                        "totp_secret": totp_sec,
                        "static_ip": "",
                        "source": "env"
                    })
                    break
        return out

    def _env_account(self) -> dict[str, Any] | None:
        accts = self._env_accounts()
        return accts[0] if accts else None

    def _all(self) -> list[dict[str, Any]]:
        accts = [dict(a, source="saved") for a in self._data["accounts"]] if self.readable else []
        for env_acct in self._env_accounts():
            if not any(a["id"] == env_acct["id"] for a in accts):
                accts.append(env_acct)
        return accts

    # ---- reading
    def default_id(self) -> str:
        accts = self._all()
        ids = {a["id"] for a in accts}
        d = self._data.get("default", "")
        if d in ids:
            return d
        return accts[0]["id"] if len(accts) == 1 else ""

    def public(self, connected_id: str | None = None) -> list[dict[str, Any]]:
        d = self.default_id()
        out = []
        for a in self._all():
            out.append({"id": a["id"], "label": a["label"], "source": a["source"], "ucc": mask_ucc(a["ucc"]), "mobile": mask_mobile(a["mobile"]),
                        "token": _tail(a["consumer_key"], 4), "has_mpin": bool(a["mpin"]), "has_totp_secret": bool(a.get("totp_secret")),
                        "static_ip": a.get("static_ip", ""), "default": a["id"] == d, "connected": a["id"] == connected_id,
                        "editable": a["source"] == "saved"})
        return out

    def get(self, account_id: str) -> dict[str, Any]:
        for a in self._all():
            if a["id"] == account_id:
                return a
        raise VaultError("That account does not exist (it may have been deleted).")

    def creds(self, account_id: str) -> dict[str, str]:
        """The values the Kotak login reads (same names as the .env file)."""
        a = self.get(account_id)
        return {"NEO_CONSUMER_KEY": a["consumer_key"], "NEO_MOBILE_NUMBER": a["mobile"], "NEO_UCC": a["ucc"], "NEO_MPIN": a["mpin"],
                "NEO_TOTP_SECRET": a.get("totp_secret", "")}

    def journal_namespace(self, account_id: str, mode: str) -> str:
        """The journal is kept per real Kotak client ID, so the same account always shows the same history however its keys are stored."""
        env = self._env_account()
        ucc = self.get(account_id)["ucc"]
        return mode if (env is not None and env["ucc"] == ucc) else f"{mode}:{ucc}"

    # ---- changing
    def add(self, fields: dict[str, Any]) -> str:
        with self._lock:
            self._need_open()
            if len(self._data["accounts"]) >= MAX_ACCOUNTS:
                raise VaultError(f"At most {MAX_ACCOUNTS} accounts can be saved.")
            f = clean_fields(fields, partial=False)
            self._check_unique(f)
            aid = secrets.token_hex(4)
            self._data["accounts"].append({"id": aid, **f, "created": time.time()})
            if not self._data.get("default"):
                self._data["default"] = aid
            self._save()
            return aid

    def update(self, account_id: str, fields: dict[str, Any]) -> None:
        with self._lock:
            self._need_open()
            cur = self._find(account_id)
            f = clean_fields(fields, partial=True, current=cur)
            self._check_unique(f, skip=account_id)
            cur.update(f)
            self._save()

    def delete(self, account_id: str) -> None:
        with self._lock:
            self._need_open()
            cur = self._find(account_id)
            self._data["accounts"].remove(cur)
            if self._data.get("default") == account_id:
                self._data["default"] = ""
            self._save()

    def set_default(self, account_id: str) -> None:
        with self._lock:
            self._need_open()
            self.get(account_id)
            self._data["default"] = account_id
            self._save()

    def _find(self, account_id: str) -> dict[str, Any]:
        if account_id == ENV_ID:
            raise VaultError("The account from the .env file is changed by editing the .env file.")
        for a in self._data["accounts"]:
            if a["id"] == account_id:
                return a
        raise VaultError("That account does not exist (it may have been deleted).")

    def _check_unique(self, f: dict[str, Any], skip: str = "") -> None:
        for a in self._all():
            if a["id"] == skip:
                continue
            if a["label"].lower() == f["label"].lower():
                raise VaultError(f"There is already an account called '{a['label']}'. Use a different name.")
            if a["ucc"] == f["ucc"]:
                raise VaultError(f"The client ID {mask_ucc(f['ucc'])} is already saved as '{a['label']}'. Edit that account instead.")


def scrub(text: str, creds: dict[str, str]) -> str:
    """Remove any secret that a broker or library error message might repeat."""
    for k in ("NEO_CONSUMER_KEY", "NEO_MPIN", "NEO_TOTP_SECRET"):
        v = creds.get(k, "")
        if v and len(v) >= 4:
            text = text.replace(v, "••••")
    return text
