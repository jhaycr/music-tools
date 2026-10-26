"""Download what the account's favourite artists release.

An artist is followed from the day it is first seen as a favourite: everything
it has already released is recorded as 'baseline', and only its new releases
download. That holds for the first run's favourites and for any favourited
later. Albums, EPs and singles; not compilations, and nothing dated in
the future (it is picked up on the day).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from .deemix import Deemix, DeemixError
from .deezer import Account, Catalogue, DeezerError
from .http import HttpError
from .store import Store, now

TYPES = ("album", "ep", "single")


def norm(title: str) -> str:
    t = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"\s+", " ", t).strip()


def pick(releases: list[dict], today: str) -> list[dict]:
    """Out, of a type we want, and one per title and type: Deezer often lists the
    explicit and clean versions of a release separately. The explicit one wins
    (the original), then the earliest."""
    keep: dict[tuple, dict] = {}
    for r in releases:
        if r["type"] not in TYPES or not r["date"] or r["date"] > today:
            continue
        k = (norm(r["title"]), r["type"])
        cur = keep.get(k)
        if cur is None or (not r["explicit"], r["date"], r["id"]) < (not cur["explicit"], cur["date"], cur["id"]):
            keep[k] = r
    return sorted(keep.values(), key=lambda r: (r["date"], r["id"]))


def follow(store: Store, account: Account, catalogue: Catalogue, deemix: Deemix | None,
           max_queue: int = 100, bitrate: str | None = None, dry_run: bool = False,
           today: str | None = None, log=print, err=None) -> dict:
    err = err or log
    today = today or date.today().isoformat()
    favs = account.favorite_artists()
    out = {"artists": len(favs), "queued": [], "failed": [], "deferred": 0, "new_artists": []}
    budget = max_queue
    for a in favs:
        row = store.artist(a["id"])
        new = row is None
        # Artists followed before 2026-10-26 may be 'backfill' (whole catalogue);
        # theirs is queued already, so they now behave the same.
        mode = "baseline" if new else row["mode"]
        if new:
            out["new_artists"].append(a["name"])
            log(f"{a['name']}: new releases from today")
            if not dry_run:
                store.add_artist(a["id"], a["name"], mode)
        try:
            releases = pick(catalogue.releases(a["id"]), today)
        except (DeezerError, HttpError) as e:
            err(f"{a['name']}: can't read releases ({e}); next run")
            out["failed"].append(f"{a['name']}: {e}")
            continue
        have = {(norm(r["title"]), r["type"]) for r in store.titles(a["id"])}
        for r in releases:
            if store.release_state(r["id"]) in ("baseline", "queued", "skipped"):
                continue
            if new and mode == "baseline":
                if not dry_run:
                    store.mark(r, a["id"], "baseline")
                continue
            if (norm(r["title"]), r["type"]) in have:
                if not dry_run:
                    store.mark(r, a["id"], "skipped", "same title and type as one already handled")
                continue
            if budget <= 0:
                out["deferred"] += 1
                continue
            budget -= 1
            label = f"{a['name']} - {r['title']} ({r['type']}, {r['date']})"
            if dry_run:
                log(f"would queue {label}")
                out["queued"].append(label)
                continue
            try:
                deemix.queue(f"https://www.deezer.com/album/{r['id']}", bitrate)
            except (DeemixError, HttpError) as e:
                store.mark(r, a["id"], "failed", str(e))
                err(f"FAILED to queue {label}: {e}")
                out["failed"].append(f"{label}: {e}")
                continue
            store.mark(r, a["id"], "queued")
            have.add((norm(r["title"]), r["type"]))
            log(f"queued {label}")
            out["queued"].append(label)
    if not dry_run:
        store.set_active({a["id"] for a in favs})
        if store.get_meta("initialized") is None:
            store.set_meta("initialized", now())
    if out["deferred"]:
        log(f"{out['deferred']} more over the limit of {max_queue} a run; next run")
    return out


def add_favorite(account: Account, catalogue: Catalogue, artist: dict) -> dict:
    if any(f["id"] == artist["id"] for f in account.favorite_artists()):
        return {**artist, "already": True}
    account.add_favorite_artist(artist["id"])
    return {**artist, "already": False}
