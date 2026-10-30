"""Spotify, public playlists only, with no login: the embed page every shared
playlist has carries its track list. A private playlist, or the songs its owner
hid, are not there; nothing here can see them.
"""
from __future__ import annotations

import json
import re

from .http import Http, HttpError

EMBED = "https://open.spotify.com/embed/playlist/{id}"
PLAYLIST_ID = re.compile(r"\A[0-9A-Za-z]{22}\Z")
NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


class SpotifyError(Exception):
    pass


def playlist_id(ref: str) -> str:
    """The id from an id or a share link (open.spotify.com/playlist/<id>?si=...)."""
    m = re.search(r"playlist[/:]([0-9A-Za-z]{22})", ref or "")
    pid = m.group(1) if m else (ref or "").strip()
    if not PLAYLIST_ID.match(pid):
        raise SpotifyError("a Spotify playlist id is 22 letters and digits, or give its share link")
    return pid


def parse_embed(page: str) -> dict:
    m = NEXT_DATA.search(page)
    if not m:
        raise SpotifyError("Spotify's page has no track list (a private or removed playlist?)")
    try:
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (ValueError, KeyError, TypeError):
        raise SpotifyError("Spotify's page has changed shape; its track list could not be read") from None
    tracks = []
    for t in entity.get("trackList") or []:
        uri = str(t.get("uri") or "")
        tracks.append({"title": t.get("title") or "", "artist": (t.get("subtitle") or "").split(",")[0].strip(),
                       "artists": [a.strip() for a in (t.get("subtitle") or "").split(",") if a.strip()],
                       "seconds": int((t.get("duration") or 0) / 1000), "explicit": bool(t.get("isExplicit")),
                       "spotify_id": uri.rsplit(":", 1)[-1] if uri.startswith("spotify:track:") else ""})
    return {"title": entity.get("name") or entity.get("title") or "", "owner": entity.get("subtitle") or "",
            "tracks": tracks}


def public_playlist(ref: str, http: Http | None = None) -> dict:
    pid = playlist_id(ref)
    try:
        page = (http or Http()).text(EMBED.format(id=pid))
    except HttpError as e:
        raise SpotifyError(f"Spotify didn't answer: {e}") from None
    return {"id": pid, **parse_embed(page)}
