"""music-tools: small one-shot tools for a self-hosted music library, cron-friendly.

  run                          every scheduled tool, each on its own (now: deemix run)
  deemix run                   deemix arl renew if needed, then deemix follow
  deemix follow [--dry-run]    queue new releases of the Deezer account's favourite artists
  deemix arl check             does deemix's stored Deezer login still work?
  deemix arl renew [--force]   make a new ARL from the email and password, hand it to deemix
  deemix favorite add ID       favourite an artist: a Deezer artist id or URL, or a
                               MusicBrainz artist id (never a name)
  deemix status                artists followed and the latest releases queued
  artist search NAME           Deezer artists of that name, told apart: latest releases,
                               MusicBrainz match and bio, the library folder that is theirs
"""
from __future__ import annotations

import argparse
import sys

from .arl import RenewError, check, ensure
from .artists import ArtistError, MusicBrainz, resolve, search
from .config import Config
from .deemix import Deemix, DeemixError
from .deezer import Catalogue, DeezerError
from .follow import add_favorite, follow
from .http import Http, HttpError
from .notify import notify
from . import log
from .store import Store


def _alert(cfg: Config, store: Store, title: str, body: str) -> None:
    """Notify about a problem once, not on every cron pass while it lasts."""
    if store.get_meta("last_alert") != body:
        notify(cfg.apprise_url, title, body)
        store.set_meta("last_alert", body)


def _clear_alert(store: Store) -> None:
    if store.get_meta("last_alert"):
        store.set_meta("last_alert", "")


def _login(cfg: Config, store: Store, force: bool = False):
    """deemix's Deezer account, renewing the login if it must. None (logged as an ERROR) when it can't."""
    try:
        account, renewed = ensure(cfg, force=force, log=lambda m: log.info("deemix", m))
    except RenewError as e:
        log.error("deemix", f"login: {e}")
        _alert(cfg, store, "music-tools: deemix's Deezer login is broken", str(e))
        return None
    if renewed:
        notify(cfg.apprise_url, "music-tools", "deemix's Deezer login was renewed from the account's email")
    elif cfg.credentials_problem():
        log.warn("deemix", f"login works, but can't be renewed when it expires: {cfg.credentials_problem()}")
    return account


def cmd_arl(a, cfg: Config) -> int:
    if a.action == "check":
        state, account = check(cfg)
        who = f" as {account.user.get('BLOG_NAME') or account.user['USER_ID']}" if account else ""
        (log.info if state == "valid" else log.error)("deemix", f"deemix's Deezer login is {state}{who}")
        problem = cfg.credentials_problem()
        if problem:
            log.warn("deemix", f"renewal not possible: {problem}")
        return 0 if state == "valid" else 2
    store = Store(cfg.db)
    if _login(cfg, store, force=a.force) is None:
        return 1
    if not a.force:
        log.info("deemix", "deemix's login works; nothing to renew (--force renews anyway)")
    return 0


def _follow(cfg: Config, store: Store, account, dry_run: bool) -> int:
    out = follow(store, account, Catalogue(), None if dry_run else Deemix(cfg.deemix_url, Http(timeout=120)),
                 max_queue=cfg.max_queue, bitrate=cfg.bitrate, dry_run=dry_run,
                 log=lambda m: log.info("deemix", m), err=lambda m: log.error("deemix", m))
    verb = "would queue" if dry_run else "queued"
    log.info("deemix", f"{out['artists']} favourite artists, {verb} {len(out['queued'])}, "
                       f"{len(out['failed'])} failed, {out['deferred']} left for the next run")
    if out["queued"] and not dry_run:
        notify(cfg.apprise_url, f"music-tools: {len(out['queued'])} new releases downloading",
               "\n".join(out["queued"][:40]) + (f"\n... and {len(out['queued']) - 40} more" if len(out["queued"]) > 40 else ""))
    if out["failed"] and not dry_run:
        _alert(cfg, store, "music-tools: some releases did not queue", "\n".join(out["failed"][:20]))
    return 1 if out["failed"] else 0


def cmd_follow(a, cfg: Config) -> int:
    store = Store(cfg.db)
    state, account = check(cfg)
    if state != "valid":
        log.error("deemix", f"deemix's Deezer login is {state}; `music-tools deemix arl renew` fixes that")
        return 1
    return _follow(cfg, store, account, a.dry_run)


def cmd_deemix_run(a, cfg: Config) -> int:
    store = Store(cfg.db)
    account = _login(cfg, store)
    if account is None:
        return 1
    try:
        rc = _follow(cfg, store, account, dry_run=False)
    except (DeezerError, DeemixError, HttpError) as e:
        log.error("deemix", f"follow: {e}")
        _alert(cfg, store, "music-tools: follow failed", str(e))
        return 1
    if rc == 0:
        _clear_alert(store)
    return rc


# What `music-tools run` (the container's default) runs, each on its own: one
# failing or crashing is logged and the rest still run.
JOBS = [("deemix", cmd_deemix_run)]


def cmd_run(a, cfg: Config) -> int:
    log.info("run", f"music-tools run: {', '.join(n for n, _ in JOBS)}")
    failed = []
    for name, job in JOBS:
        try:
            rc = job(a, cfg)
        except Exception:  # a bug in one tool must not stop the others
            log.error(name, "crashed", exc=True)
            rc = 1
        if rc:
            failed.append(name)
    if failed:
        log.error("run", f"failed: {', '.join(failed)} (the others ran)")
        return 1
    log.info("run", "all ok")
    return 0


def cmd_favorite(a, cfg: Config) -> int:
    state, account = check(cfg)
    if state != "valid":
        log.error("deemix", f"can't favourite: deemix's Deezer login is {state}; "
                            "`music-tools deemix arl renew` fixes that")
        return 1
    try:
        cat = Catalogue()
        r = add_favorite(account, cat, resolve(a.artist, cat, MusicBrainz()))
    except (ArtistError, DeezerError, HttpError) as e:
        log.error("deemix", f"favorite: {e}")
        return 1
    log.info("deemix", f"{r['name']} ({r['id']}) " + ("was already a favourite" if r["already"]
                                                     else "is now a favourite; the next run downloads its catalogue"))
    return 0


def cmd_artist(a, cfg: Config) -> int:
    favourites = set()
    state, account = check(cfg)
    if state == "valid":
        favourites = {f["id"] for f in account.favorite_artists()}
    else:  # the search needs no login; it just can't mark favourites
        log.warn("artist", f"deemix's Deezer login is {state}: favourites are not marked")
    print(f"searching Deezer and MusicBrainz for {a.name!r} (MusicBrainz allows one request a second)...")
    cands, unmatched = search(a.name, Catalogue(), MusicBrainz(), cfg.library, favourites)
    if not cands:
        print("no Deezer artist by that name")
        return 1
    for c in cands:
        star = "  * a favourite" if c["favourite"] else ""
        print(f"\n{c['name']}  Deezer {c['id']}{star}\n  {c['fans']} fans, {c['releases']} releases")
        if c["latest"]:
            print("  latest: " + "; ".join(f"{r['title']} ({r['type']}, {r['date']})" for r in c["latest"]))
        m = c.get("mb")
        if m:
            how = "links to this Deezer artist" if m["linked"] else f"{m['common']} release titles in common"
            print(f"  MusicBrainz {m['id']}: {m['bio'] or 'no details'} ({how})")
            if m["wikipedia"]:
                print(f"  Wikipedia: {m['wikipedia']}")
        elif c.get("mb_error"):
            print(f"  MusicBrainz: not reached ({c['mb_error']})")
        for f in c["library"]:
            print(f"  in your library: {f['path']} ({f['on_deezer']} of its {f['albums']} albums are this artist's)")
    for f in unmatched:
        print(f"\nin your library but none of these: {f['path']} ({f['albums']} albums)")
    print("\nfavourite one with: music-tools deemix favorite add <Deezer id>")
    return 0


def cmd_status(a, cfg: Config) -> int:
    store = Store(cfg.db)
    first = store.get_meta("initialized")
    print(f"following since {first}" if first else "not run yet: the first run takes today's favourites as the baseline")
    for r in store.artists():
        c = store.counts(r["id"])
        flag = "" if r["active"] else "  (no longer a favourite)"
        print(f"  {r['name']:32s} {'new only' if r['mode'] == 'baseline' else 'catalogue':9s} "
              f"queued {c.get('queued', 0):4d}  failed {c.get('failed', 0):3d}  since {r['first_seen'][:10]}{flag}")
    recent = store.recent()
    if recent:
        print("latest:")
        for r in recent:
            err = f"  {r['error']}" if r["state"] == "failed" else ""
            print(f"  {r['at'][:16]} {r['state']:7s} {r['artist']} - {r['title']} ({r['type']}, {r['date']}){err}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="music-tools", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    tools = p.add_subparsers(dest="tool", required=True)
    tools.add_parser("run", help="every scheduled tool, each on its own: what cron runs")
    dz = tools.add_parser("deemix", help="follow favourite artists; keep deemix logged in")
    sub = dz.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="arl renew if needed, then follow")
    s = sub.add_parser("follow", help="queue new releases of favourite artists")
    s.add_argument("--dry-run", action="store_true", help="say what would be queued; change nothing")
    s = sub.add_parser("arl", help="check or renew deemix's Deezer login")
    s.add_argument("action", choices=["check", "renew"])
    s.add_argument("--force", action="store_true", help="renew even though the login works")
    s = sub.add_parser("favorite", help="favourite an artist on the account")
    s.add_argument("action", choices=["add"])
    s.add_argument("artist", help="Deezer artist id or URL, or MusicBrainz artist id")
    sub.add_parser("status", help="artists followed, latest releases")
    ar = tools.add_parser("artist", help="find an artist's ids")
    sub = ar.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="Deezer artists of that name, told apart")
    s.add_argument("name")
    a = p.parse_args(argv)
    cfg = Config.from_env()
    handlers = {("run", None): cmd_run, ("deemix", "run"): cmd_deemix_run, ("deemix", "follow"): cmd_follow, ("deemix", "arl"): cmd_arl,
                ("deemix", "favorite"): cmd_favorite, ("deemix", "status"): cmd_status,
                ("artist", "search"): cmd_artist}
    try:
        return handlers[(a.tool, getattr(a, "cmd", None))](a, cfg)
    except (DeemixError, DeezerError, HttpError, ArtistError) as e:
        log.error(a.tool, str(e))
        return 1
    except Exception:
        log.error(a.tool, "crashed", exc=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
