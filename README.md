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
deemix-helper favorite add ARTIST     favourite an artist (exact name, Deezer id or URL)
deemix-helper status                  artists followed, latest releases queued
```

`docker exec` does not work on a stopped one-shot container; use
`docker run --rm ... ghcr.io/jhaycr/deemix-helper status` with the same
volumes, or `docker compose run --rm deemix-helper status`.

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

Notifications: releases queued, a login renewed, and problems (each problem
once, not on every run while it lasts).

## Development

```
python -m pytest -q
```

Standard library only. The tests run against a fake Deezer and deemix.
