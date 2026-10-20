"""The deemix web server's API (bambanah/deemix v4): log a session in, queue
downloads. Downloads land wherever deemix's own settings say.

In single-user mode a SUCCESSFUL /api/loginArl also saves that ARL as
deemix's stored login, and a FAILED one wipes the stored login. So only ever
hand it an ARL that has just logged in to Deezer directly.
"""
from __future__ import annotations

import json

from .http import Http

LOGGED_IN = {1, 2, 3}  # SUCCESS, ALREADY_LOGGED, FORCED_SUCCESS


class DeemixError(Exception):
    pass


def stored_arl(login_file: str) -> str | None:
    """The ARL deemix keeps in its config folder (login.json), or None."""
    try:
        with open(login_file, encoding="utf-8") as f:
            arl = json.load(f).get("arl")
    except (OSError, ValueError, AttributeError):
        return None
    return arl.strip() if isinstance(arl, str) and arl.strip() else None


class Deemix:
    def __init__(self, url: str, http: Http | None = None):
        self.url = url.rstrip("/")
        self.http = http or Http(timeout=120)

    def login(self, arl: str) -> None:
        r = self.http.request("POST", self.url + "/api/loginArl", body={"arl": arl})
        status = r.get("status") if isinstance(r, dict) else None
        if status not in LOGGED_IN:
            raise DeemixError(f"deemix did not log in (status {status}"
                              + (f", {r.get('error')}" if isinstance(r, dict) and r.get("error") else "") + ")")

    def queue(self, url: str, bitrate: str | None = None) -> None:
        r = self.http.request("POST", self.url + "/api/addToQueue",
                              body={"url": url, "bitrate": bitrate or "null"})
        if not (isinstance(r, dict) and r.get("result")):
            raise DeemixError(f"deemix would not queue it: {(r or {}).get('errid') if isinstance(r, dict) else 'no answer'}")
