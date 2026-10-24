"""Keep deemix logged in to Deezer.

deemix stores one ARL (a long-lived login cookie). When Deezer stops
accepting it, deemix fails every download with a login error until someone
pastes a new one. This checks the stored ARL against Deezer and, only when
it no longer logs in, puts a working one back: first the last ARL that
worked, kept by music-tools, then a new one made from the account's email
and password. deemix is handed it and saves it.

The kept copy matters because any failed /api/loginArl, even a passing
network error inside deemix, wipes deemix's stored login while the ARL
itself is still good.
"""
from __future__ import annotations

import os

from .config import Config
from .deemix import Deemix, DeemixError, stored_arl
from .deezer import ARL, Account, DeezerError, arl_from_login
from .http import Http, HttpError

FIX = "log deemix in with a fresh ARL from a browser (the arl cookie on deezer.com)"


class RenewError(Exception):
    pass


def kept_arl(cfg: Config) -> str | None:
    """The last ARL that logged in, as music-tools kept it, or None."""
    if not cfg.keep_file:
        return None
    try:
        with open(cfg.keep_file, encoding="utf-8") as f:
            arl = f.read().strip()
    except OSError:
        return None
    return arl if ARL.fullmatch(arl) else None


def keep(cfg: Config, arl: str) -> None:
    """Remember an ARL that just logged in. Owner-only, written whole or not at all."""
    if not cfg.keep_file or kept_arl(cfg) == arl:
        return
    tmp = cfg.keep_file + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(arl + "\n")
    os.replace(tmp, cfg.keep_file)


def check(cfg: Config, http=None) -> tuple[str, Account | None]:
    """('valid', account) | ('invalid', None) | ('missing', None) | ('unreachable', None)."""
    http = http or Http
    arl = stored_arl(cfg.login_file)
    if not arl:
        return "missing", None
    account = Account(arl, http())
    try:
        ok = account.login()
    except (DeezerError, HttpError):
        return "unreachable", None
    return ("valid", account) if ok else ("invalid", None)


def _hand_to_deemix(cfg: Config, arl: str, http) -> None:
    """Log deemix in with an ARL that has just logged in to Deezer, and confirm it saved it."""
    Deemix(cfg.deemix_url, http()).login(arl)
    if stored_arl(cfg.login_file) != arl:
        raise RenewError("deemix logged in with the new ARL but didn't save it to login.json")


def _restore(cfg: Config, state: str, http, log) -> Account | None:
    """deemix logged back in with the kept ARL, or None when there is none or it no longer logs in."""
    arl = kept_arl(cfg)
    if not arl or arl == stored_arl(cfg.login_file):
        return None
    account = Account(arl, http())
    try:
        if not account.login():
            return None
    except (DeezerError, HttpError):
        raise RenewError("Deezer can't be reached to check the kept login; trying next run") from None
    log(f"arl: deemix's login is {state}; the last one that worked still does, giving it back to deemix")
    try:
        _hand_to_deemix(cfg, arl, http)
    except (DeemixError, HttpError) as e:
        raise RenewError(f"deemix would not take back the login that worked: {e}") from None
    log("arl: restored; deemix saved the login again")
    return account


def ensure(cfg: Config, force: bool = False, http=None, log=print) -> tuple[Account, str]:
    """A logged-in account, putting a working ARL back in deemix first if it has to.
    Returns (account, what was done: '' | 'restored' | 'renewed')."""
    http = http or Http
    state, account = check(cfg, http)
    if state == "valid" and not force:
        keep(cfg, stored_arl(cfg.login_file))
        return account, ""
    if state == "unreachable":
        raise RenewError("Deezer can't be reached to check deemix's login; not renewing, trying next run")
    if not force:
        restored = _restore(cfg, state, http, log)
        if restored:
            return restored, "restored"
    creds = cfg.credentials()
    if not creds:
        raise RenewError(f"deemix's Deezer login is {state}, and it can't be renewed: no email and password "
                         f"are configured ({cfg.credentials_problem()}); {FIX}")
    log(f"arl: deemix's login is {state}; logging in with the account's email and password")
    try:
        arl = arl_from_login(*creds, http=http())
        fresh = Account(arl, http())
        if not fresh.login():
            raise RenewError(f"Deezer gave a new ARL that doesn't log in; deemix left as it was; {FIX}")
        # Only now: a failed loginArl would wipe deemix's stored login.
        _hand_to_deemix(cfg, arl, http)
    except (DeezerError, DeemixError, HttpError) as e:
        raise RenewError(f"{e}; {FIX}") from None
    keep(cfg, arl)
    log("arl: renewed; deemix saved the new login")
    return fresh, "renewed"
