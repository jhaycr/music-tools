"""deemix-helper: small tools around a deemix server, one-shot and cron-friendly.

  run                      arl ensure, then follow: what cron runs
  follow [--dry-run]       queue new releases of the account's favourite artists
  arl check                does deemix's stored Deezer login still work?
  arl renew [--force]      make a new ARL from the email and password, hand it to deemix
  artist search NAME       Deezer artists of that name, told apart: latest releases,
                           MusicBrainz match and bio, the library folder that is theirs
  favorite add ID          favourite an artist: a Deezer artist id or URL, or a
                           MusicBrainz artist id (never a name)
  status                   artists followed and the latest releases queued
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
from .store import Store, now


def _alert(cfg: Config, store: Store, title: str, body: str) -> None:
    """Notify about a problem once, not on every cron pass while it lasts."""
    if store.get_meta("last_alert") != body:
        notify(cfg.apprise_url, title, body)
        store.set_meta("last_alert", body)


def _clear_alert(store: Store) -> None:
    if store.get_meta("last_alert"):
        store.set_meta("last_alert", "")


def cmd_arl(a, cfg: Config) -> int:
    if a.action == "check":
        state, account = check(cfg)
        who = f" as {account.user.get('BLOG_NAME') or account.user['USER_ID']}" if account else ""
        print(f"arl: deemix's Deezer login is {state}{who}")
        return 0 if state == "valid" else 2
    store = Store(cfg.db)
    try:
        _, renewed = ensure(cfg, force=a.force)
    except RenewError as e:
        print(f"arl: NOT renewed: {e}")
        _alert(cfg, store, "deemix-helper: deemix's Deezer login is broken", str(e))
        return 1
    if renewed:
        notify(cfg.apprise_url, "deemix-helper", "deemix's Deezer login was renewed from the account's email")
    else:
        print("arl: deemix's login works; nothing to do (--force renews anyway)")
    return 0


def _follow(cfg: Config, store: Store, account, dry_run: bool) -> int:
    out = follow(store, account, Catalogue(), None if dry_run else Deemix(cfg.deemix_url, Http(timeout=120)),
                 max_queue=cfg.max_queue, bitrate=cfg.bitrate, dry_run=dry_run)
    verb = "would queue" if dry_run else "queued"
    print(f"follow: {out['artists']} favourite artists, {verb} {len(out['queued'])}, "
          f"{len(out['failed'])} failed, {out['deferred']} left for the next run")
    if out["queued"] and not dry_run:
        notify(cfg.apprise_url, f"deemix-helper: {len(out['queued'])} new releases downloading",
               "\n".join(out["queued"][:40]) + (f"\n... and {len(out['queued']) - 40} more" if len(out["queued"]) > 40 else ""))
    if out["failed"] and not dry_run:
        _alert(cfg, store, "deemix-helper: some releases did not queue", "\n".join(out["failed"][:20]))
    return 1 if out["failed"] else 0


def cmd_follow(a, cfg: Config) -> int:
    store = Store(cfg.db)
    state, account = check(cfg)
    if state != "valid":
        print(f"follow: deemix's Deezer login is {state}; `arl renew` (or `run`) fixes that")
        return 1
    return _follow(cfg, store, account, a.dry_run)


def cmd_run(a, cfg: Config) -> int:
    print(f"deemix-helper run {now()}")
    store = Store(cfg.db)
    try:
        account, renewed = ensure(cfg)
    except RenewError as e:
        print(f"arl: NOT renewed: {e}")
        _alert(cfg, store, "deemix-helper: deemix's Deezer login is broken", str(e))
        return 1
    if renewed:
        notify(cfg.apprise_url, "deemix-helper", "deemix's Deezer login was renewed from the account's email")
    try:
        rc = _follow(cfg, store, account, dry_run=False)
    except (DeezerError, HttpError) as e:
        print(f"follow: {e}")
        _alert(cfg, store, "deemix-helper: follow failed", str(e))
        return 1
    if rc == 0:
        _clear_alert(store)
    return rc


def cmd_favorite(a, cfg: Config) -> int:
    state, account = check(cfg)
    if state != "valid":
        print(f"favorite: deemix's Deezer login is {state}; `arl renew` fixes that")
        return 1
    try:
        cat = Catalogue()
        r = add_favorite(account, cat, resolve(a.artist, cat, MusicBrainz()))
    except (ArtistError, DeezerError, HttpError) as e:
        print(f"favorite: {e}")
        return 1
    print(f"favorite: {r['name']} ({r['id']}) " + ("was already a favourite" if r["already"]
                                                   else "is now a favourite; the next run downloads its catalogue"))
    return 0


def cmd_artist(a, cfg: Config) -> int:
    favourites = set()
    state, account = check(cfg)
    if state == "valid":
        favourites = {f["id"] for f in account.favorite_artists()}
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
    print("\nfavourite one with: deemix-helper favorite add <Deezer id>")
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
    p = argparse.ArgumentParser(prog="deemix-helper", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="arl ensure, then follow")
    s = sub.add_parser("follow", help="queue new releases of favourite artists")
    s.add_argument("--dry-run", action="store_true", help="say what would be queued; change nothing")
    s = sub.add_parser("arl", help="check or renew deemix's Deezer login")
    s.add_argument("action", choices=["check", "renew"])
    s.add_argument("--force", action="store_true", help="renew even though the login works")
    s = sub.add_parser("favorite", help="favourite an artist on the account")
    s.add_argument("action", choices=["add"])
    s.add_argument("artist", help="Deezer artist id or URL, or MusicBrainz artist id")
    s = sub.add_parser("artist", help="find an artist's ids")
    s.add_argument("action", choices=["search"])
    s.add_argument("name")
    sub.add_parser("status", help="artists followed, latest releases")
    a = p.parse_args(argv)
    cfg = Config.from_env()
    try:
        return {"run": cmd_run, "follow": cmd_follow, "arl": cmd_arl, "favorite": cmd_favorite, "artist": cmd_artist,
                "status": cmd_status}[a.cmd](a, cfg)
    except (DeemixError, DeezerError, HttpError) as e:
        print(f"{a.cmd}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
