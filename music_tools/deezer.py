"""Deezer: the account (gateway API, logged in by ARL), the public catalogue
(no login).

The ARL is a credential. It goes into a cookie and nowhere else: not into an
exception, a log line or a return value that gets printed.
"""
from __future__ import annotations

import re
import time

from .http import Http

GW = "https://www.deezer.com/ajax/gw-light.php"
API = "https://api.deezer.com"
ARL = re.compile(r"[0-9a-f]{64,}", re.I)


class DeezerError(Exception):
    pass


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

    def playlists(self) -> list[dict]:
        """The account's own playlists: id, title, how many songs."""
        res = self.gw("deezer.pageProfile", {"USER_ID": self.user["USER_ID"], "tab": "playlists", "nb": 2000})
        data = ((res or {}).get("TAB") or {}).get("playlists", {}).get("data") or []
        return [{"id": int(p["PLAYLIST_ID"]), "title": p.get("TITLE") or "", "songs": int(p.get("NB_SONG") or 0)}
                for p in data if str(p.get("PLAYLIST_ID") or "").isdigit()]

    def playlist_songs(self, playlist_id: int) -> list[dict]:
        """A playlist's songs in its order (private playlists too: the gateway, not the public API)."""
        res = self.gw("playlist.getSongs", {"PLAYLIST_ID": playlist_id, "nb": 2000, "start": 0})
        return [{"artist": s.get("ART_NAME") or "", "artists": [a.get("ART_NAME") for a in s.get("ARTISTS") or []],
                 "title": s.get("SNG_TITLE") or "", "version": s.get("VERSION") or "", "album": s.get("ALB_TITLE") or "",
                 "isrc": s.get("ISRC") or "", "seconds": int(s.get("DURATION") or 0),
                 "explicit": str(s.get("EXPLICIT_LYRICS") or "0") != "0", "deezer_id": int(s.get("SNG_ID") or 0)}
                for s in (res or {}).get("data") or []]


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
