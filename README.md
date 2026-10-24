# music-tools

Small one-shot tools for a self-hosted music library, each meant for cron:
it does its job, logs what it did, and exits. No UI, no daemon, standard
library only.

- **`deemix`: follow favourite artists.** Every album, EP and single that an
  artist favourited on Deezer releases is queued in a
  [deemix](https://github.com/bambanah/deemix) server on the day it comes out.
  A replacement for Lidarr's "monitor artist", driven by the Deezer account
  deemix is already logged in to.
- **`deemix`: keep deemix logged in.** deemix stores one Deezer login cookie
  (the ARL). When Deezer stops accepting it, every download fails until
  someone pastes a new one. music-tools notices, logs in with the account's
  email and password, and hands deemix the new ARL.
- **`artist`: tell artists apart.** Many artists share a name. Artists are
  always named by id, and `artist search` shows the evidence to pick the right
  one.

Filing downloads into a library is
[music-sort](https://github.com/jhaycr/music-sort)'s job (to fold in here
later). For Bandcamp purchases, [BandcampSync](https://github.com/meeb/bandcampsync)
already does the job well and can drop them in the same place.

> **Personal project, no support.** Built largely with AI code-generation tools
> to scratch my own itch. Shared in case it's useful to you too. Issues and
> feature requests are welcome and I read them all, but responses and fixes
> happen on hobby-project time. Review and test before relying on it.

## Quick start

music-tools talks to deemix over its web API and reads deemix's config
folder for the stored login. Run it next to deemix, on the same Docker
network, with deemix in single-user mode (its default).

```yaml
services:
  music-tools:
    image: ghcr.io/jhaycr/music-tools:latest
    user: 1000:1000
    restart: "no"            # a one-shot: cron starts it (docker start -a music-tools)
    volumes:
      - ./music-tools:/config                    # its state (SQLite) and the kept login
      - ./deemix/config:/deemix:ro               # deemix's config folder, for login.json
      - ./music:/music:ro                        # optional: the library, for artist search
      - ./secrets/deezer-email:/run/secrets/deezer-email:ro
      - ./secrets/deezer-password:/run/secrets/deezer-password:ro
    environment:
      DEEMIX_URL: http://deemix:6595
      DEEZER_EMAIL_FILE: /run/secrets/deezer-email
      DEEZER_PASSWORD_FILE: /run/secrets/deezer-password
      # APPRISE_URL: http://apprise:8000/notify/<key>   # optional
```

The default command is `run`: every scheduled tool, each on its own (today,
`deemix run`). From the host's crontab, every hour:

```
17 * * * * docker start -a music-tools
```

The email and password are optional: without them, everything works except
renewing an expired login (you are told it has expired instead).

## Commands

```
music-tools run                           every scheduled tool, each on its own (the default)
music-tools deemix run                    deemix arl renew if needed, then deemix follow
music-tools deemix follow [--dry-run]     queue new releases of the favourite artists
music-tools deemix arl check              does deemix's stored login still work?
music-tools deemix arl renew [--force]    new ARL from the email and password, given to deemix
music-tools deemix favorite add ID        favourite an artist: Deezer artist id or URL, or a
                                          MusicBrainz artist id. Never a name
music-tools deemix status                 artists followed, latest releases queued
music-tools artist search NAME            Deezer artists of that name, told apart (below)
```

`docker exec` does not work on a stopped one-shot container; use
`docker compose run --rm music-tools artist search she`.

## When something is wrong

Every event is one line in the log, `<UTC time> <INFO|WARN|ERROR> <tool>: <message>`;
alert on `ERROR`. `run` runs each tool on its own: one failing, or crashing
outright (logged with its traceback), never stops the others, and `run` exits
1 naming the ones that failed.

| Situation | What happens |
|---|---|
| deemix's login works, email/password not configured | `WARN` each run naming the missing setting; following carries on |
| login expired or missing, no email/password | `ERROR` naming what is missing (the variable, or the empty file); nothing is queued; deemix's stored login is left alone |
| login expired, wrong password | `ERROR` with Deezer's own reason; deemix left alone |
| Deezer can't be reached | `ERROR`; no renewal attempted (an outage is not an expired login) |
| a release deemix won't queue | `ERROR` for that release; the rest queue; it is tried again next run |
| `artist search` without a working login | `WARN`; the search runs, favourites just aren't marked |

Each problem is also notified once (with `APPRISE_URL`), not on every run
while it lasts. Credentials are never printed: messages name the setting or
the file, never its contents.

## Telling artists apart

Many artists share a name: Deezer has at least four called "She". So
music-tools never takes a name for an artist, only an id:

- a **Deezer artist id** (or `deezer.com/artist/...` URL), which is what the
  account's favourites hold;
- a **MusicBrainz artist id**, as in a library tagged by Picard or beets. It is
  resolved by MusicBrainz's own Deezer link, then Wikidata's, then by the
  release titles both catalogues share, and refused when no match is clear.

`artist search` shows the evidence for each match:

```
$ music-tools artist search she

She  Deezer 117840782  * a favourite
  367 fans, 23 releases
  latest: Salvo (single, 2025-05-02); Chiptronica (single, 2025-03-10); ...
  MusicBrainz 99e34e68-0320-4c3e-901d-0a307ed504a4: Polish electronic musician
    based in Sweden, Person, Sweden, from 1983 (22 release titles in common)
  Wikipedia: she is a musical project formed in 2003 by ...
  in your library: General/she (18 of its 30 albums are this artist's)

SHE  Deezer 365804512
  7 fans, 6 releases
  latest: Rain All Over Me (single, 2026-08-14); ...
```

The library line needs the library mounted read-only at `/music`
(`MT_LIBRARY`), laid out `<Category>/<Artist>/<Album (Year)>`. Folders are
matched by album titles, and a folder of that name that none of the matches
accounts for is listed too.

## How following works

- **The first run** takes the favourites as they stand and records
  everything they have already released as a baseline. Nothing downloads:
  you most likely have those already. From then on, only new releases do.
- **An artist favourited later** gets its whole catalogue once, then new
  releases.
- Albums, EPs and singles; not compilations. Deezer often lists the explicit
  and clean versions of a release separately: the explicit one is taken. A
  release dated in the future waits for its day.
- At most 100 releases are queued a run (`MT_MAX_QUEUE`); the rest wait for
  the next run. A release deemix would not queue is tried again next run.
- Unfavouriting an artist stops it being followed. Its downloads stay.

Downloads land wherever deemix's settings say, as if you had queued them in
the web UI.

## How the login renewal works

1. Each run checks deemix's stored ARL (`login.json`) against Deezer. One
   that logs in is kept in `MT_ARL_KEEP` (owner-only).
2. If Deezer can't be reached, nothing happens: that is not an expired login.
3. If deemix's login is gone or no longer logs in, but the kept ARL still
   does, deemix is given the kept one back. Any failed `/api/loginArl`, even
   a passing network error inside deemix, makes deemix forget its stored
   login while the ARL itself is still good; this undoes that on the next run.
4. Otherwise music-tools logs in with the email and password the way deemix's
   own email login does, and gets a new ARL.
5. It checks the new ARL logs in, and only then gives it to deemix
   (`/api/loginArl`). In single-user mode deemix saves it. Giving deemix an ARL
   that fails would make deemix forget its stored login, which is why step 5
   checks first.
6. It confirms deemix saved it.

An account with two-factor sign-in, or one Deezer asks to solve a captcha,
can't log in this way, and Deezer sometimes refuses it outright (HTTP 403).
Renewal then fails and says why: log deemix in with a fresh ARL from a
browser (the `arl` cookie on deezer.com), and the next run keeps that one.

The ARL, the password and the access token are never logged or printed. The
email and password are read from files, so `docker inspect` doesn't show them.

## Settings

| Variable | Default | |
|---|---|---|
| `DEEMIX_URL` | `http://deemix:6595` | deemix's web server |
| `DEEMIX_LOGIN` | `/deemix/login.json` | deemix's stored login |
| `MT_DB` | `/config/music-tools.db` | state |
| `MT_ARL_KEEP` | `/config/last-good-arl` | the last ARL that logged in, to restore a wiped login |
| `DEEZER_EMAIL_FILE`, `DEEZER_PASSWORD_FILE` | unset | the account, for renewal |
| `MT_BITRATE` | deemix's own | e.g. `9` FLAC, `3` MP3 320 |
| `MT_MAX_QUEUE` | `100` | releases queued per run |
| `MT_LIBRARY` | `/music` | the library, read-only, for `artist search` |
| `APPRISE_URL` | unset | an apprise-api `/notify/<key>` URL |

Notifications: releases queued, a login renewed, and problems (each problem
once, not on every run while it lasts).

## Development

```
python -m pytest -q
```

Standard library only. The tests run against a fake Deezer, MusicBrainz and
deemix.
