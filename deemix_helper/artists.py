"""Telling artists apart: many share a name ("She" is at least four on
Deezer). An artist is named by an id, never a name:

- a Deezer artist id (or URL): what the account's favourites actually hold;
- a MusicBrainz artist id (MBID), as in the library's tags. It is resolved to
  Deezer by MusicBrainz's own Deezer link, then Wikidata's, and failing both
  by the release titles the two catalogues have in common.

`search` puts the evidence side by side for each Deezer match: its latest
releases, the MusicBrainz artist it lines up with (and a bio from there and
Wikipedia), and the library folder whose albums it has.
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse

from .deezer import Catalogue
from .follow import norm
from .http import Http, HttpError

MB = "https://musicbrainz.org/ws/2"
UA = "deemix-helper/0.1 ( https://github.com/jhaycr/deemix-helper )"
MBID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
DEEZER_ID = re.compile(r"^(?:https?://)?(?:www\.)?(?:deezer\.com/(?:[a-z]{2}/)?artist/)?(\d+)/?$")
_DEEZER_LINK = re.compile(r"deezer\.com/(?:[a-z]{2}/)?artist/(\d+)")
_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")
_EDITION = re.compile(r"\s*[\(\[](?:deluxe|remaster(?:ed)?|expanded|anniversary|edition|explicit|clean)[^\)\]]*[\)\]]", re.I)
SKIP_TOP = {"Family"}


class ArtistError(Exception):
    pass


def title_key(t: str) -> str:
    return norm(_EDITION.sub("", _YEAR.sub("", t)))


class MusicBrainz:
    """1 request a second, a named User-Agent: MusicBrainz's rules."""

    def __init__(self, http: Http | None = None, min_interval: float = 1.05):
        self.http = http or Http()
        self.min_interval = min_interval
        self._last = 0.0

    def get(self, path: str, params: dict) -> dict:
        for attempt in range(4):
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                return self.http.request("GET", MB + path, {**params, "fmt": "json"},
                                         headers={"User-Agent": UA}) or {}
            except HttpError as e:
                if "HTTP 503" not in str(e) or attempt == 3:  # 503: slow down
                    raise
                time.sleep(2 + 2 * attempt)
        return {}

    def search(self, name: str, limit: int = 10) -> list[dict]:
        q = 'artist:"' + name.replace('"', "") + '"'
        return self.get("/artist", {"query": q, "limit": limit}).get("artists") or []

    def artist(self, mbid: str) -> dict:
        return self.get(f"/artist/{mbid}", {"inc": "url-rels"})

    def titles(self, mbid: str) -> set[str]:
        out, offset = set(), 0
        while True:
            page = self.get("/release-group", {"artist": mbid, "limit": 100, "offset": offset})
            groups = page.get("release-groups") or []
            out |= {title_key(g["title"]) for g in groups}
            offset += len(groups)
            if not groups or offset >= (page.get("release-group-count") or 0):
                return out


def deezer_link(mb_artist: dict, http: Http | None = None) -> int | None:
    """The Deezer artist MusicBrainz links to, or Wikidata does."""
    urls = [r.get("url", {}).get("resource", "") for r in mb_artist.get("relations") or []]
    for u in urls:
        m = _DEEZER_LINK.search(u)
        if m:
            return int(m.group(1))
    q = next((u.rsplit("/", 1)[-1] for u in urls if "wikidata.org/wiki/Q" in u), None)
    if q:
        try:
            e = (http or Http()).request("GET", f"https://www.wikidata.org/wiki/Special:EntityData/{q}.json",
                                         headers={"User-Agent": UA})
            claims = e["entities"][q]["claims"].get("P2722") or []
            if claims:
                return int(claims[0]["mainsnak"]["datavalue"]["value"])
        except (HttpError, KeyError, TypeError, ValueError):
            pass
    return None


def wikipedia(mb_artist: dict, http: Http | None = None) -> str:
    """The first sentences of the English Wikipedia article, by way of Wikidata."""
    http = http or Http()
    q = next((r["url"]["resource"].rsplit("/", 1)[-1] for r in mb_artist.get("relations") or []
              if "wikidata.org/wiki/Q" in r.get("url", {}).get("resource", "")), None)
    if not q:
        return ""
    try:
        e = http.request("GET", f"https://www.wikidata.org/wiki/Special:EntityData/{q}.json",
                         headers={"User-Agent": UA})
        title = e["entities"][q]["sitelinks"]["enwiki"]["title"]
        s = http.request("GET", "https://en.wikipedia.org/api/rest_v1/page/summary/"
                         + urllib.parse.quote(title.replace(" ", "_"), safe=""), headers={"User-Agent": UA})
        text = (s or {}).get("extract") or ""
    except (HttpError, KeyError, TypeError):
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(sentences[:2])


def library_folders(root: str, name: str) -> list[dict]:
    """Artist folders in the library spelled like `name` (any case), with their album titles."""
    out = []
    if not root or not os.path.isdir(root):
        return out
    key = norm(name)
    for cat in sorted(os.listdir(root)):
        cdir = os.path.join(root, cat)
        if cat.startswith(".") or cat in SKIP_TOP or not os.path.isdir(cdir) or os.path.islink(cdir):
            continue
        for art in os.listdir(cdir):
            adir = os.path.join(cdir, art)
            if norm(art) == key and os.path.isdir(adir) and not os.path.islink(adir):
                albums = {title_key(a) for a in os.listdir(adir)
                          if os.path.isdir(os.path.join(adir, a)) and not a.startswith(".")}
                out.append({"path": f"{cat}/{art}", "albums": albums})
    return out


def _bio(mb: dict) -> str:
    bits = [mb.get("disambiguation"), mb.get("type"), (mb.get("area") or {}).get("name")]
    begin = (mb.get("life-span") or {}).get("begin")
    if begin:
        bits.append(f"from {begin[:4]}")
    return ", ".join(b for b in bits if b)


def search(query: str, catalogue: Catalogue, mb: MusicBrainz, library: str = "",
           favourites: set[int] | None = None, limit: int = 6, http: Http | None = None) -> tuple[list, list]:
    """Deezer artists matching `query`, each with the evidence to tell them apart, and
    the library folders of that name that none of them accounts for."""
    hits = catalogue.search_artists(query)
    exact = [h for h in hits if norm(h["name"]) == norm(query)]
    cands = (exact or hits)[:limit]
    for c in cands:
        rels = sorted(catalogue.releases(c["id"]), key=lambda r: r["date"], reverse=True)
        c["releases"] = len(rels)
        c["latest"] = rels[:4]
        c["titles"] = {title_key(r["title"]) for r in rels}
        c["favourite"] = c["id"] in (favourites or set())
        c["mb"], c["library"] = None, []
    # MusicBrainz artists of that name, matched to a Deezer candidate by link or shared titles
    try:
        mbs = [a for a in mb.search(query) if norm(a["name"]) == norm(query)][:limit]
        for a in mbs:
            full = mb.artist(a["id"])
            link = deezer_link(full, http)
            titles = mb.titles(a["id"])
            best = None
            for c in cands:
                if link == c["id"]:
                    best = (c, 99)
                    break
                common = len(titles & c["titles"])
                if common >= 2 and (best is None or common > best[1]):
                    best = (c, common)
            if best and (best[0]["mb"] is None or best[1] > best[0]["mb"]["common"]):
                best[0]["mb"] = {"id": a["id"], "bio": _bio(full), "common": best[1],
                                 "linked": link == best[0]["id"], "wikipedia": wikipedia(full, http)}
    except (HttpError, KeyError, TypeError) as e:
        for c in cands:
            c["mb_error"] = str(e)
    unmatched = []
    for f in library_folders(library, query):
        scored = [(len(f["albums"] & c["titles"]), c) for c in cands]
        n, c = max(scored, key=lambda x: x[0], default=(0, None))
        if c and n:
            c["library"].append({"path": f["path"], "albums": len(f["albums"]), "on_deezer": n})
        else:
            unmatched.append({"path": f["path"], "albums": len(f["albums"])})
    return cands, unmatched


def resolve(ref: str, catalogue: Catalogue, mb: MusicBrainz, http: Http | None = None) -> dict:
    """A Deezer artist from a Deezer id/URL or a MusicBrainz id. Names are refused."""
    ref = ref.strip()
    m = DEEZER_ID.match(ref)
    if m:
        return catalogue.artist(int(m.group(1)))
    m = MBID.search(ref)
    if not m:
        raise ArtistError(f"{ref!r} is not a Deezer artist id or a MusicBrainz id. "
                          f"`deemix-helper artist search {ref}` shows the candidates and their ids")
    try:
        full = mb.artist(m.group(0))
    except HttpError as e:
        raise ArtistError(f"MusicBrainz: {e}") from None
    if not full.get("name"):
        raise ArtistError(f"MusicBrainz has no artist {m.group(0)}")
    link = deezer_link(full, http)
    if link:
        return catalogue.artist(link)
    # No link: the Deezer artist of that name sharing the most release titles, if it's clear.
    titles = mb.titles(m.group(0))
    scored = []
    for c in [h for h in catalogue.search_artists(full["name"]) if norm(h["name"]) == norm(full["name"])][:8]:
        common = len(titles & {title_key(r["title"]) for r in catalogue.releases(c["id"])})
        scored.append((common, c))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] >= 3 and (len(scored) == 1 or scored[0][0] >= 2 * scored[1][0]):
        return scored[0][1]
    near = ", ".join(f"{c['name']} {c['id']} ({n} titles in common)" for n, c in scored[:4]) or "none"
    raise ArtistError(f"MusicBrainz's {full['name']} ({_bio(full) or 'no details'}) has no Deezer link and no "
                      f"clear match by titles: {near}. Pick one with `artist search` and pass its Deezer id")
