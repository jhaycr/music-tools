import json
import os

import pytest

from music_tools import arl as arlmod
from music_tools.config import Config
from music_tools.deemix import Deemix, stored_arl
from music_tools.deezer import CLIENT_ID, CLIENT_SECRET, Account, Catalogue, DeezerError, _md5, arl_from_login
from music_tools.follow import follow, pick
from music_tools.store import Store

GOOD = "a" * 192
NEW = "b" * 192


class World:
    """A fake Deezer and deemix that every FakeHttp talks to."""

    def __init__(self, tmp_path):
        self.valid = {GOOD}
        self.favs = [{"ART_ID": "1", "ART_NAME": "Old Band"}]
        self.albums = {1: [rel(10, "First", "album", "2020-01-01")]}
        self.login_file = tmp_path / "login.json"
        self.login_file.write_text(json.dumps({"arl": GOOD}))
        self.queued, self.calls, self.favorited = [], [], []
        self.token_ok = True
        self.issue = NEW
        self.deemix_saves = True


def rel(i, title, kind, day, explicit=False):
    return {"id": i, "title": title, "record_type": kind, "release_date": day, "explicit_lyrics": explicit}


class FakeHttp:
    def __init__(self, world, timeout=30):
        self.w = world
        self.cookies = {}
        self.authed = False

    def set_cookie(self, domain, name, value):
        self.cookies[name] = value

    def request(self, method, url, params=None, body=None, headers=None):
        w, p = self.w, params or {}
        w.calls.append((url.split("?")[0], p.get("method")))
        if url.endswith("gw-light.php"):
            m = p["method"]
            if m == "user.getArl":
                return {"error": {}, "results": w.issue if self.authed else None}
            if self.cookies.get("arl") not in w.valid:
                return {"error": {}, "results": {"USER": {"USER_ID": 0}, "checkForm": "x"}}
            if m == "deezer.getUserData":
                return {"error": [], "results": {"USER": {"USER_ID": 42, "BLOG_NAME": "me"}, "checkForm": "tok"}}
            assert p["api_token"] == "tok"
            if m == "deezer.pageProfile":
                return {"error": {}, "results": {"TAB": {"artists": {"data": w.favs}}}}
            if m == "artist.addFavorite":
                w.favorited.append(body["ART_ID"])
                return {"error": {}, "results": True}
        if url.endswith("/auth/token"):
            assert p["hash"] == _md5(CLIENT_ID + p["login"] + p["password"] + CLIENT_SECRET)
            ok = w.token_ok and p["password"] == _md5("pw")
            return {"access_token": "T"} if ok else {"error": {"type": "Exception", "message": "Invalid login"}}
        if "/platform/generic/" in url:
            self.authed = headers.get("Authorization") == "Bearer T"
            return None
        if "/artist/" in url and url.endswith("/albums"):
            aid = int(url.split("/artist/")[1].split("/")[0])
            return {"data": w.albums.get(aid, [])}
        if url.endswith("/artist/7"):
            return {"id": 7, "name": "K/DA"}
        if "/search/artist" in url:
            return {"data": [{"id": 7, "name": "K/DA", "nb_fan": 9}, {"id": 8, "name": "KDA Fans", "nb_fan": 1}]}
        if url.endswith("/api/loginArl"):
            ok = body["arl"] in w.valid
            self.deemix_session = ok
            w.deemix_logins = getattr(w, "deemix_logins", 0) + 1
            if ok and w.deemix_saves:
                w.login_file.write_text(json.dumps({"arl": body["arl"]}))
            if not ok:
                w.login_file.write_text(json.dumps({"arl": None}))  # deemix wipes it
            return {"status": 1 if ok else 0}
        if url.endswith("/api/addToQueue"):
            if not getattr(self, "deemix_session", False):  # deemix queues for a logged-in session only
                return {"result": False, "errid": "NotLoggedIn"}
            w.queued.append(body["url"])
            return {"result": True}
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def cfg(w, creds=True, tmp=None, keep=False):
    d = w.login_file.parent
    if creds:
        (d / "email").write_text("me@example.com\n")
        (d / "password").write_text("pw\n")
    return Config(deemix_url="http://deemix:6595", login_file=str(w.login_file), db=str(d / "db.sqlite"),
                  email_file=str(d / "email") if creds else "", password_file=str(d / "password") if creds else "",
                  apprise_url="", bitrate=None, max_queue=100, keep_file=str(d / "kept") if keep else "")


def factory(w):
    return lambda timeout=30: FakeHttp(w, timeout)


def account(w, arl=GOOD):
    a = Account(arl, FakeHttp(w))
    assert a.login()
    return a


def catalogue(w):
    return Catalogue(FakeHttp(w), min_interval=0)


# ---- pick

def test_pick_types_future_and_duplicates():
    rs = [
        {"id": 1, "title": "Song", "type": "single", "date": "2024-01-01", "explicit": False},
        {"id": 2, "title": "song ", "type": "single", "date": "2024-01-01", "explicit": True},
        {"id": 3, "title": "Hits", "type": "compile", "date": "2024-01-01", "explicit": False},
        {"id": 4, "title": "Soon", "type": "album", "date": "2099-01-01", "explicit": False},
        {"id": 5, "title": "Song", "type": "album", "date": "2024-02-01", "explicit": False},
    ]
    assert [r["id"] for r in pick(rs, "2026-10-20")] == [2, 5], "explicit wins; album != single; no compile or future"


# ---- follow

def test_first_run_baselines_then_new_release_and_new_artist(w):
    store = Store(":memory:")
    out = follow(store, account(w), catalogue(w), Deemix("http://deemix:6595", FakeHttp(w), arl=GOOD), today="2026-10-20",
                 log=lambda *_: None)
    assert out["queued"] == [] and w.queued == [] and store.release_state(10) == "baseline"
    # a new release from the old favourite, and a new favourite with a catalogue
    w.albums[1].append(rel(11, "Second", "ep", "2026-10-19"))
    w.favs.append({"ART_ID": "2", "ART_NAME": "New Band"})
    w.albums[2] = [rel(20, "Debut", "album", "2019-05-05"), rel(21, "Debut", "album", "2019-05-05", True),
                   rel(22, "Next", "single", "2099-01-01")]
    out = follow(store, account(w), catalogue(w), Deemix("http://deemix:6595", FakeHttp(w), arl=GOOD), today="2026-10-20",
                 log=lambda *_: None)
    assert w.queued == ["https://www.deezer.com/album/11", "https://www.deezer.com/album/21"]
    assert store.artist(2)["mode"] == "backfill" and store.release_state(22) is None
    follow(store, account(w), catalogue(w), Deemix("http://deemix:6595", FakeHttp(w), arl=GOOD), today="2026-10-20",
           log=lambda *_: None)
    assert len(w.queued) == 2, "nothing twice"


def test_dry_run_changes_nothing_and_limit_defers(w):
    store = Store(":memory:")
    store.set_meta("initialized", "x")  # not the first run: a new artist backfills
    w.albums[1] = [rel(i, f"T{i}", "single", "2020-01-01") for i in range(1, 6)]
    out = follow(store, account(w), catalogue(w), None, max_queue=3, dry_run=True, today="2026-10-20",
                 log=lambda *_: None)
    assert len(out["queued"]) == 3 and out["deferred"] == 2 and store.artist(1) is None and w.queued == []
    out = follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w), arl=GOOD), max_queue=3, today="2026-10-20",
                 log=lambda *_: None)
    assert len(w.queued) == 3 and out["deferred"] == 2
    follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w), arl=GOOD), max_queue=3, today="2026-10-20",
           log=lambda *_: None)
    assert len(w.queued) == 5


def test_a_failed_queue_is_retried(w):
    store = Store(":memory:")
    store.set_meta("initialized", "x")

    class Broken(Deemix):
        def queue(self, url, bitrate=None):
            from music_tools.deemix import DeemixError
            raise DeemixError("NotLoggedIn")
    out = follow(store, account(w), catalogue(w), Broken("x", FakeHttp(w)), today="2026-10-20", log=lambda *_: None)
    assert out["failed"] and store.release_state(10) == "failed"
    follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w), arl=GOOD), today="2026-10-20", log=lambda *_: None)
    assert w.queued == ["https://www.deezer.com/album/10"] and store.release_state(10) == "queued"


def test_a_later_listing_of_the_same_title_is_skipped(w):
    store = Store(":memory:")
    follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w), arl=GOOD), today="2026-10-20", log=lambda *_: None)
    w.albums[1].append(rel(12, "First", "album", "2026-10-01", True))  # an explicit version listed later
    follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w), arl=GOOD), today="2026-10-20", log=lambda *_: None)
    assert w.queued == [] and store.release_state(12) == "skipped"


# ---- arl

def test_valid_login_is_left_alone(w):
    acct, renewed = arlmod.ensure(cfg(w), http=factory(w), log=lambda *_: None)
    assert not renewed and acct.user["USER_ID"] == 42
    assert not any(c[0].endswith("/auth/token") for c in w.calls)


def test_expired_login_is_renewed_and_saved(w):
    w.valid = {NEW}  # Deezer stopped accepting GOOD
    acct, renewed = arlmod.ensure(cfg(w), http=factory(w), log=lambda *_: None)
    assert renewed and stored_arl(str(w.login_file)) == NEW


def test_no_credentials_means_no_renewal(w):
    w.valid = {NEW}
    with pytest.raises(arlmod.RenewError, match="no email and password"):
        arlmod.ensure(cfg(w, creds=False), http=factory(w), log=lambda *_: None)
    assert stored_arl(str(w.login_file)) == GOOD


def test_a_bad_new_arl_never_reaches_deemix(w):
    w.valid = set()  # nothing logs in, not even what Deezer just issued
    with pytest.raises(arlmod.RenewError, match="doesn't log in"):
        arlmod.ensure(cfg(w), http=factory(w), log=lambda *_: None)
    assert not any(c[0].endswith("/api/loginArl") for c in w.calls)
    assert stored_arl(str(w.login_file)) == GOOD, "deemix's stored login untouched"


def test_refused_email_login_says_why_without_the_password(w):
    w.valid, w.token_ok = {NEW}, False
    with pytest.raises(arlmod.RenewError) as e:
        arlmod.ensure(cfg(w), http=factory(w), log=lambda *_: None)
    assert "Invalid login" in str(e.value) and "pw" not in str(e.value).replace("password", "")


def test_unreachable_deezer_is_not_a_reason_to_renew(w):
    from music_tools.http import HttpError

    class Down(FakeHttp):
        def request(self, *a, **k):
            raise HttpError("www.deezer.com: URLError")
    with pytest.raises(arlmod.RenewError, match="can't be reached"):
        arlmod.ensure(cfg(w), http=lambda timeout=30: Down(w), log=lambda *_: None)


def test_deemix_must_actually_save_it(w):
    w.valid, w.deemix_saves = {NEW}, False
    with pytest.raises(arlmod.RenewError, match="didn't save"):
        arlmod.ensure(cfg(w), http=factory(w), log=lambda *_: None)


def test_a_working_login_is_kept_owner_only(w):
    c = cfg(w, keep=True)
    arlmod.ensure(c, http=factory(w), log=lambda *_: None)
    assert arlmod.kept_arl(c) == GOOD
    assert (os.stat(c.keep_file).st_mode & 0o777) == 0o600


def test_a_wiped_login_is_restored_from_the_kept_one(w):
    c = cfg(w, keep=True)
    arlmod.ensure(c, http=factory(w), log=lambda *_: None)
    w.login_file.write_text(json.dumps({"arl": None}))  # a failed loginArl wiped it; the ARL still works
    acct, done = arlmod.ensure(c, http=factory(w), log=lambda *_: None)
    assert done == "restored" and acct.user["USER_ID"] == 42
    assert stored_arl(str(w.login_file)) == GOOD
    assert not any(c[0].endswith("/auth/token") for c in w.calls), "no email login needed"


def test_a_kept_login_that_no_longer_works_falls_back_to_renewal(w):
    c = cfg(w, keep=True)
    arlmod.ensure(c, http=factory(w), log=lambda *_: None)
    w.valid = {NEW}
    acct, done = arlmod.ensure(c, http=factory(w), log=lambda *_: None)
    assert done == "renewed" and stored_arl(str(w.login_file)) == NEW == arlmod.kept_arl(c)


def test_a_refused_email_login_says_how_to_fix_it(w):
    from music_tools.http import HttpError

    class Blocked(FakeHttp):
        def request(self, method, url, *a, **k):
            if url.endswith("/auth/token"):
                raise HttpError("api.deezer.com: HTTP 403")
            return super().request(method, url, *a, **k)
    w.valid = {NEW}
    with pytest.raises(arlmod.RenewError, match="HTTP 403; log deemix in with a fresh ARL"):
        arlmod.ensure(cfg(w), http=lambda timeout=30: Blocked(w), log=lambda *_: None)


def test_arl_from_login_rejects_garbage(w):
    w.issue = "not-an-arl"
    with pytest.raises(DeezerError, match="no ARL"):
        arl_from_login("me@example.com", "pw", http=FakeHttp(w))


# ---- artists: ids, never names

class ArtistsHttp:
    """Deezer has three "She"; MusicBrainz knows one, with no Deezer link."""
    MB_SHE = "99e34e68-0320-4c3e-901d-0a307ed504a4"
    MB_LINKED = "11111111-2222-3333-4444-555555555555"

    def __init__(self, timeout=30):
        self.calls = []

    def set_cookie(self, *a):
        pass

    def request(self, method, url, params=None, body=None, headers=None):
        p = params or {}
        self.calls.append(url)
        if url.endswith("/search/artist"):
            return {"data": [{"id": 1, "name": "She", "nb_fan": 3}, {"id": 2, "name": "She", "nb_fan": 367},
                             {"id": 3, "name": "SHE", "nb_fan": 7}, {"id": 4, "name": "She & Him", "nb_fan": 9}]}
        if "/artist/" in url and url.endswith("/albums"):
            aid = int(url.split("/artist/")[1].split("/")[0])
            return {"data": {1: [rel(10, "Wants a Piece of You", "album", "2017-05-26")],
                             2: [rel(20, "Chiptek", "album", "2008-01-01"), rel(21, "Atomic", "album", "2009-01-01"),
                                 rel(22, "Abyss (Deluxe Edition)", "album", "2016-01-01"),
                                 rel(23, "Salvo", "single", "2025-05-02")],
                             3: [rel(30, "echo", "single", "2026-04-17")]}.get(aid, [])}
        if "api.deezer.com/artist/" in url:
            aid = int(url.rsplit("/", 1)[1])
            return {"id": aid, "name": {2: "She", 5: "Linked Band"}.get(aid, "Someone"), "nb_fan": 1}
        if url.endswith("/ws/2/artist"):
            assert headers and "music-tools" in headers["User-Agent"]
            return {"artists": [{"id": self.MB_SHE, "name": "she"}, {"id": "x", "name": "S.H.E"}]}
        if "/ws/2/artist/" in url:
            mbid = url.rsplit("/", 1)[1]
            if mbid == self.MB_LINKED:
                return {"name": "Linked Band", "relations": [{"url": {"resource": "https://www.deezer.com/artist/5"}}]}
            if mbid == self.MB_SHE:
                return {"name": "she", "disambiguation": "Polish electronic musician based in Sweden", "type": "Person",
                        "relations": [{"url": {"resource": "https://www.wikidata.org/wiki/Q20038335"}}]}
            return {}
        if url.endswith("/ws/2/release-group"):
            return {"release-groups": [{"title": t} for t in ("Chiptek", "Atomic", "Abyss", "Coloris")],
                    "release-group-count": 4}
        if "wikidata.org" in url:
            return {"entities": {"Q20038335": {"claims": {}, "sitelinks": {"enwiki": {"title": "She (musician)"}}}}}
        if "wikipedia.org" in url:
            assert url.endswith("She_%28musician%29")
            return {"extract": "she is a musical project formed in 2003. It makes chiptune. A third sentence."}
        raise AssertionError(url)


def artists_env():
    from music_tools.artists import MusicBrainz
    h = ArtistsHttp()
    return Catalogue(h, min_interval=0), MusicBrainz(h, min_interval=0), h


def test_search_tells_same_named_artists_apart(tmp_path):
    from music_tools.artists import search
    lib = tmp_path / "music"
    for d in ("General/she/Chiptek (2008)", "General/she/Abyss (2016)", "General/She/Trigger (2025)",
              "Family/she/Chiptek (2008)"):
        (lib / d).mkdir(parents=True)
    cat, mb, h = artists_env()
    cands, unmatched = search("She", cat, mb, str(lib), favourites={2}, http=h)
    assert [c["id"] for c in cands] == [1, 2, 3], "exact names only, case aside"
    she = cands[1]
    assert she["favourite"] and she["latest"][0]["title"] == "Salvo"
    assert she["mb"]["id"] == ArtistsHttp.MB_SHE and she["mb"]["common"] == 3 and not she["mb"]["linked"]
    assert she["mb"]["wikipedia"] == "she is a musical project formed in 2003. It makes chiptune."
    assert she["library"] == [{"path": "General/she", "albums": 2, "on_deezer": 2}]
    assert cands[0]["mb"] is None and unmatched == [{"path": "General/She", "albums": 1}], "Family is skipped"


def test_resolve_takes_ids_and_refuses_names():
    from music_tools.artists import ArtistError, resolve
    cat, mb, h = artists_env()
    assert resolve("https://www.deezer.com/en/artist/2", cat, mb, h)["id"] == 2
    assert resolve("2", cat, mb, h)["id"] == 2
    assert resolve(ArtistsHttp.MB_LINKED, cat, mb, h)["id"] == 5, "MusicBrainz's own Deezer link"
    assert resolve(f"https://musicbrainz.org/artist/{ArtistsHttp.MB_SHE}", cat, mb, h)["id"] == 2, "by titles"
    with pytest.raises(ArtistError, match="artist search She"):
        resolve("She", cat, mb, h)


# ---- favourites

def test_favorite_adds_once(w):
    from music_tools.follow import add_favorite
    r = add_favorite(account(w), catalogue(w), {"id": 7, "name": "K/DA"})
    assert r == {"id": 7, "name": "K/DA", "already": False} and w.favorited == [7]
    w.favs.append({"ART_ID": "7", "ART_NAME": "K/DA"})
    assert add_favorite(account(w), catalogue(w), {"id": 7, "name": "K/DA"})["already"]


# ---- failures: loud in the log, and never stopping the other tools

@pytest.fixture
def cli(w, monkeypatch):
    """main() as cron runs it, against the fake world; returns (run, notifications)."""
    import music_tools.arl, music_tools.artists, music_tools.cli, music_tools.deezer
    for mod in (music_tools.arl, music_tools.cli, music_tools.deezer, music_tools.artists):
        monkeypatch.setattr(mod, "Http", factory(w))
    sent = []
    monkeypatch.setattr(music_tools.cli, "notify", lambda url, title, body: sent.append(body))
    d = w.login_file.parent
    monkeypatch.setenv("DEEMIX_LOGIN", str(w.login_file))
    monkeypatch.setenv("MT_DB", str(d / "state.db"))
    monkeypatch.setenv("APPRISE_URL", "http://apprise/notify/x")
    monkeypatch.setenv("MT_LIBRARY", str(d / "no-library"))
    monkeypatch.setenv("MT_ARL_KEEP", str(d / "kept"))
    for k in ("DEEZER_EMAIL_FILE", "DEEZER_PASSWORD_FILE"):
        monkeypatch.delenv(k, raising=False)

    def creds(email="me@example.com", password="pw"):
        (d / "email").write_text(email)
        (d / "password").write_text(password)
        monkeypatch.setenv("DEEZER_EMAIL_FILE", str(d / "email"))
        monkeypatch.setenv("DEEZER_PASSWORD_FILE", str(d / "password"))

    def run(*argv):
        from music_tools.cli import main
        return main(list(argv))
    run.creds = creds
    return run, sent


def errors(out):
    return [l for l in out.splitlines() if " ERROR " in l]


def test_expired_login_and_no_credentials_is_an_error_alerted_once(cli, w, capsys):
    run, sent = cli
    w.valid = {NEW}  # Deezer no longer takes the stored ARL
    assert run("run") == 1
    errs = errors(capsys.readouterr().out)
    assert any("deemix: login: deemix's Deezer login is invalid" in e and "DEEZER_EMAIL_FILE is not set" in e
               for e in errs), errs
    assert any("run: failed: deemix" in e for e in errs)
    assert len(sent) == 1 and run("run") == 1 and len(sent) == 1, "alerted once while it lasts"
    assert stored_arl(str(w.login_file)) == GOOD


def test_an_empty_password_file_is_named(cli, w, capsys):
    run, _ = cli
    w.valid = {NEW}
    run.creds(password="")
    assert run("deemix", "run") == 1
    assert "password (DEEZER_PASSWORD_FILE) is empty or unreadable" in capsys.readouterr().out


def test_wrong_password_says_what_deezer_said(cli, w, capsys):
    run, _ = cli
    w.valid = {NEW}
    run.creds(password="wrong")
    assert run("run") == 1
    out = capsys.readouterr().out
    assert "ERROR deemix: login: Deezer refused the email login: Invalid login" in out and "wrong" not in out
    assert stored_arl(str(w.login_file)) == GOOD


def test_a_working_login_without_credentials_still_follows_with_a_warning(cli, w, capsys):
    run, _ = cli
    assert run("run") == 0
    out = capsys.readouterr().out
    assert "WARN  deemix: login works, but can't be renewed when it expires: DEEZER_EMAIL_FILE is not set" in out
    assert "INFO  deemix: 1 favourite artists" in out and not errors(out)


def test_one_tool_crashing_does_not_stop_the_others(cli, monkeypatch, capsys):
    import music_tools.cli as c
    run, _ = cli
    ran = []

    def boom(a, cfg):
        raise RuntimeError("a bug")
    monkeypatch.setattr(c, "JOBS", [("first", boom), ("second", lambda a, cfg: ran.append(1) or 0)])
    assert run("run") == 1 and ran == [1]
    out = capsys.readouterr().out
    assert "ERROR first: crashed" in out and "RuntimeError: a bug" in out and "run: failed: first (the others ran)" in out


def test_a_missing_login_does_not_stop_artist_search(cli, w, monkeypatch, capsys):
    import music_tools.cli as c
    run, _ = cli
    w.login_file.unlink()
    monkeypatch.setattr(c, "Catalogue", lambda: Catalogue(FakeHttp(w), min_interval=0))
    from music_tools.artists import MusicBrainz
    monkeypatch.setattr(c, "MusicBrainz", lambda: MusicBrainz(ArtistsHttp(), min_interval=0))
    w.favs = []
    monkeypatch.setattr(FakeHttp, "request", lambda self, m, url, params=None, body=None, headers=None:
                        ArtistsHttp().request(m, url, params, body, headers))
    assert run("artist", "search", "She") == 0
    out = capsys.readouterr().out
    assert "WARN  artist: deemix's Deezer login is missing: favourites are not marked" in out
    assert "She  Deezer 2" in out


def test_follow_and_favorite_refuse_cleanly_without_a_login(cli, w, capsys):
    run, _ = cli
    w.valid = set()
    assert run("deemix", "follow") == 1 and run("deemix", "favorite", "add", "7") == 1
    errs = errors(capsys.readouterr().out)
    assert len(errs) == 2 and all("login is invalid" in e for e in errs)


def test_run_logs_deemix_in_before_queueing_and_only_once(cli, w, capsys):
    run, _ = cli
    assert run("run") == 0  # the first run: a baseline
    w.favs.append({"ART_ID": "2", "ART_NAME": "New Band"})
    w.albums[2] = [rel(20, "Debut", "album", "2019-05-05"), rel(21, "Next", "single", "2020-01-01")]
    assert run("run") == 0, capsys.readouterr().out
    assert w.queued == ["https://www.deezer.com/album/20", "https://www.deezer.com/album/21"]
    assert w.deemix_logins == 1, "one deemix login per run, only when something is queued"
    assert stored_arl(str(w.login_file)) == GOOD


def test_a_deemix_login_that_fails_once_heals_on_the_next_run(cli, w, capsys):
    """2026-10-22: deemix's loginArl failed during a run, wiping its stored login while the
    ARL still worked, and the new release was never downloaded."""
    run, sent = cli
    assert run("run") == 0  # baseline, and the working ARL is kept
    w.albums[1].append(rel(11, "Second", "single", "2020-02-02"))
    flaky = {"left": 1}
    real = FakeHttp.request

    def once(self, method, url, params=None, body=None, headers=None):
        if url.endswith("/api/loginArl") and flaky["left"]:
            flaky["left"] -= 1
            w.login_file.write_text(json.dumps({"arl": None}))  # deemix wipes it
            return {"status": 0}
        return real(self, method, url, params, body, headers)
    FakeHttp.request = once
    try:
        assert run("run") == 1 and w.queued == []
        assert stored_arl(str(w.login_file)) is None
        assert run("run") == 0, capsys.readouterr().out
    finally:
        FakeHttp.request = real
    assert w.queued == ["https://www.deezer.com/album/11"]
    assert stored_arl(str(w.login_file)) == GOOD
    assert any("last one that worked was put back" in m for m in sent)
