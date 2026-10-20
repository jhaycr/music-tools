"""Deezer: the account (gateway API, logged in by ARL), the public catalogue
(no login), and the email login that mints a fresh ARL.

The ARL is a credential. It goes into a cookie and nowhere else: not into an
exception, a log line or a return value that gets printed.
"""
from __future__ import annotations

import hashlib
import re
import time

from .http import Http

GW = "https://www.deezer.com/ajax/gw-light.php"
API = "https://api.deezer.com"
# The app deemix itself uses for its email login (deemix,
# packages/deemix/src/utils/deezer.ts): Deezer's own published client.
CLIENT_ID = "172365"
CLIENT_SECRET = "fb0bec7ccc063dab0417eb7b0d847f34"
ARL = re.compile(r"[0-9a-f]{64,}", re.I)


class DeezerError(Exception):
    pass


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


class Account:
    """One logged-in Deezer session, by ARL."""

    def __init__(self, arl: str, http: Http | None = None):
        self.http = http or Http()
        self.http.set_cookie(".deezer.com", "arl", arl)
        self.token = "null"
        self.user: dict | None = None

    def gw(self, method: str, body: dict | None = None):
        r = self.http.request("POST", GW, {"method": method, "input": "3", "api_version": "1.0",
                                           "api_token": self.token}, body or {})
        if not isinstance(r, dict):
            raise DeezerError(f"{method}: no answer")
        if r.get("error"):  # [] or {} when fine
            raise DeezerError(f"{method}: {r['error']}")
        return r.get("results")

    def login(self) -> bool:
        """True when the ARL logs in. Also fetches the token later calls need."""
        res = self.gw("deezer.getUserData") or {}
        self.token = res.get("checkForm") or "null"
        user = res.get("USER") or {}
        self.user = user if int(user.get("USER_ID") or 0) else None
        return self.user is not None

    def favorite_artists(self) -> list[dict]:
        res = self.gw("deezer.pageProfile", {"USER_ID": self.user["USER_ID"], "tab": "artists", "nb": 10000})
        data = ((res or {}).get("TAB") or {}).get("artists", {}).get("data") or []
        return [{"id": int(a["ART_ID"]), "name": a["ART_NAME"]} for a in data]

    def add_favorite_artist(self, artist_id: int) -> None:
        self.gw("artist.addFavorite", {"ART_ID": artist_id})


def arl_from_login(email: str, password: str, http: Http | None = None) -> str:
    """Email and password to a new ARL, the way deemix's own email login does it."""
    http = http or Http()
    pw = _md5(password)
    r = http.request("GET", API + "/auth/token", {
        "app_id": CLIENT_ID, "login": email, "password": pw,
        "hash": _md5(CLIENT_ID + email + pw + CLIENT_SECRET)})
    token = (r or {}).get("access_token") if isinstance(r, dict) else None
    if not token:
        err = (r or {}).get("error") if isinstance(r, dict) else None
        why = err.get("message") or err.get("type") if isinstance(err, dict) else "no access token"
        raise DeezerError(f"Deezer refused the email login: {why}")
    # Any authorised call sets the session cookie that user.getArl reads.
    http.request("GET", API + "/platform/generic/track/3135556", headers={"Authorization": "Bearer " + token})
    r = http.request("GET", GW, {"method": "user.getArl", "input": "3", "api_version": "1.0", "api_token": "null"})
    arl = r.get("results") if isinstance(r, dict) else None
    if not isinstance(arl, str) or not ARL.fullmatch(arl):
        raise DeezerError("Deezer accepted the login but gave no ARL")
    return arl


class Catalogue:
    """The public API: 50 requests per 5 seconds, no login."""

    def __init__(self, http: Http | None = None, min_interval: float = 0.12):
        self.http = http or Http()
        self.min_interval = min_interval
        self._last = 0.0

    def get(self, path: str, params: dict | None = None) -> dict:
        for _ in range(5):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.http.request("GET", API + path, params)
            err = r.get("error") if isinstance(r, dict) else None
            if not err:
                return r or {}
            if err.get("code") == 4:  # quota
                time.sleep(5)
                continue
            raise DeezerError(f"{path}: {err.get('message') or err}")
        raise DeezerError(f"{path}: over quota")

    def releases(self, artist_id: int) -> list[dict]:
        out, index = [], 0
        while True:
            page = self.get(f"/artist/{artist_id}/albums", {"limit": 100, "index": index})
            data = page.get("data") or []
            out += [{"id": int(a["id"]), "title": a.get("title") or "", "type": a.get("record_type") or "",
                     "date": a.get("release_date") or "", "explicit": bool(a.get("explicit_lyrics"))}
                    for a in data]
            index += len(data)
            if not data or "next" not in page:
                return out

    def artist(self, artist_id: int) -> dict:
        a = self.get(f"/artist/{artist_id}")
        return {"id": int(a["id"]), "name": a["name"], "fans": a.get("nb_fan") or 0,
                "albums": a.get("nb_album") or 0}

    def search_artists(self, query: str, limit: int = 25) -> list[dict]:
        hits = self.get("/search/artist", {"q": query, "limit": limit}).get("data") or []
        return [{"id": int(h["id"]), "name": h["name"], "fans": h.get("nb_fan") or 0,
                 "albums": h.get("nb_album") or 0} for h in hits]
