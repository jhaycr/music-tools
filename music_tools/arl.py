"""Keep deemix logged in to Deezer.

deemix stores one ARL (a long-lived login cookie). When Deezer stops
accepting it, deemix fails every download with a login error until someone
pastes a new one. This checks the stored ARL against Deezer and, only when
Deezer says it no longer logs in, makes a new one from the account's email
and password and hands it to deemix, which saves it.
"""
from __future__ import annotations

from .config import Config
from .deemix import Deemix, DeemixError, stored_arl
from .deezer import Account, DeezerError, arl_from_login
from .http import Http, HttpError


class RenewError(Exception):
    pass


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


def ensure(cfg: Config, force: bool = False, http=None, log=print) -> tuple[Account, bool]:
    """A logged-in account, renewing deemix's ARL first if it has to. Returns (account, renewed)."""
    http = http or Http
    state, account = check(cfg, http)
    if state == "valid" and not force:
        return account, False
    if state == "unreachable":
        raise RenewError("Deezer can't be reached to check deemix's login; not renewing, trying next run")
    creds = cfg.credentials()
    if not creds:
        raise RenewError(f"deemix's Deezer login is {state}, and it can't be renewed: no email and password "
                         f"are configured ({cfg.credentials_problem()})")
    log(f"arl: deemix's login is {state}; logging in with the account's email and password")
    try:
        arl = arl_from_login(*creds, http=http())
        fresh = Account(arl, http())
        if not fresh.login():
            raise RenewError("Deezer gave a new ARL that doesn't log in; deemix left as it was")
        # Only now: a failed loginArl would wipe deemix's stored login.
        Deemix(cfg.deemix_url, http()).login(arl)
    except (DeezerError, DeemixError, HttpError) as e:
        raise RenewError(str(e)) from None
    if stored_arl(cfg.login_file) != arl:
        raise RenewError("deemix logged in with the new ARL but didn't save it to login.json")
    log("arl: renewed; deemix saved the new login")
    return fresh, True
