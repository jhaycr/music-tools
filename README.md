# deemix-helper

Small tools around a [deemix](https://github.com/bambanah/deemix) server,
each a one-shot command meant for cron: it does its job, logs what it did,
and exits. There is no UI.

- **Follow favourite artists.** Every album, EP and single that an artist you
  favourite on Deezer releases is queued in deemix on the day it comes out.
  A replacement for Lidarr's "monitor artist", driven by the Deezer account
  deemix is already logged in to.
- **Keep deemix logged in.** deemix stores one Deezer login cookie (the ARL).
  When Deezer stops accepting it, every download fails until someone pastes a
  new one. deemix-helper notices, logs in with the account's email and
  password, and hands deemix the new ARL.

> **Personal project, no support.** Built largely with AI code-generation tools
> to scratch my own itch. Shared in case it's useful to you too. Issues and
> feature requests are welcome and I read them all, but responses and fixes
> happen on hobby-project time. Review and test before relying on it.

## Quick start

deemix-helper talks to deemix over its web API and reads deemix's config
folder for the stored login. Run it next to deemix, on the same Docker
network, in single-user mode (deemix's default).

```yaml
services:
  deemix-helper:
    image: ghcr.io/jhaycr/deemix-helper:latest
    user: 1000:1000
    restart: "no"            # a one-shot: cron starts it (docker start -a deemix-helper)
    volumes:
      - ./deemix-helper:/config                  # its state (SQLite)
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

Then, for example from the host's crontab, every hour:

```
17 * * * * docker start -a deemix-helper
```

The email and password are optional: without them, everything works except
renewing an expired login (you are told it has expired instead).

## Commands

```
deemix-helper run                     arl renew if needed, then follow (the default)
deemix-helper follow [--dry-run]      queue new releases of the favourite artists
deemix-helper arl check               does deemix's stored login still work?
deemix-helper arl renew [--force]     new ARL from the email and password, given to deemix
deemix-helper artist search NAME      Deezer artists of that name, told apart (below)
deemix-helper favorite add ID         favourite an artist: Deezer artist id or URL, or a
                                      MusicBrainz artist id. Never a name
deemix-helper status                  artists followed, latest releases queued
```

`docker exec` does not work on a stopped one-shot container; use
`docker run --rm ... ghcr.io/jhaycr/deemix-helper status` with the same
volumes, or `docker compose run --rm deemix-helper status`.

## Telling artists apart

Many artists share a name: Deezer has at least four called "She". So
deemix-helper never takes a name for an artist, only an id:

- a **Deezer artist id** (or `deezer.com/artist/...` URL), which is what the
  account's favourites hold;
- a **MusicBrainz artist id**, as in a library tagged by Picard or beets. It is
  resolved by MusicBrainz's own Deezer link, then Wikidata's, then by the
  release titles both catalogues share, and refused when no match is clear.

`artist search` shows the evidence for each match:

```
$ deemix-helper artist search she

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
(`DH_LIBRARY`), laid out `<Category>/<Artist>/<Album (Year)>`; folders are
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
- At most 100 releases are queued a run (`DH_MAX_QUEUE`); the rest wait for
  the next run. A release deemix would not queue is tried again next run.
- Unfavouriting an artist stops it being followed. Its downloads stay.

Downloads land wherever deemix's settings say, as if you had queued them in
the web UI. Filing them into a library is another tool's job (for example
[music-sort](https://github.com/jhaycr/music-sort)).

## How the login renewal works

1. Each run checks deemix's stored ARL (`login.json`) against Deezer.
2. If Deezer can't be reached, nothing happens: that is not an expired login.
3. If Deezer says the ARL no longer logs in, deemix-helper logs in with the
   email and password the way deemix's own email login does, and gets a new
   ARL.
4. It checks the new ARL logs in, and only then gives it to deemix
   (`/api/loginArl`). In single-user mode deemix saves it. Giving deemix an ARL
   that fails would make deemix forget its stored login, which is why step 4
   checks first.
5. It confirms deemix saved it.

An account with two-factor sign-in, or one Deezer asks to solve a captcha,
can't log in this way; renewal then fails and says why.

The ARL, the password and the access token are never logged or printed. The
email and password are read from files, so `docker inspect` doesn't show them.

## Settings

| Variable | Default | |
|---|---|---|
| `DEEMIX_URL` | `http://deemix:6595` | deemix's web server |
| `DEEMIX_LOGIN` | `/deemix/login.json` | deemix's stored login |
| `DH_DB` | `/config/deemix-helper.db` | state |
| `DEEZER_EMAIL_FILE`, `DEEZER_PASSWORD_FILE` | unset | the account, for renewal |
| `DH_BITRATE` | deemix's own | e.g. `9` FLAC, `3` MP3 320 |
| `DH_MAX_QUEUE` | `100` | releases queued per run |
| `APPRISE_URL` | unset | an apprise-api `/notify/<key>` URL |
| `DH_LIBRARY` | `/music` | the library, read-only, for `artist search` |

Notifications: releases queued, a login renewed, and problems (each problem
once, not on every run while it lasts).

## Development

```
python -m pytest -q
```

Standard library only. The tests run against a fake Deezer and deemix.
