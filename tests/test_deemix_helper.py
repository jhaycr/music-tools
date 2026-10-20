import json

import pytest

from deemix_helper import arl as arlmod
from deemix_helper.config import Config
from deemix_helper.deemix import Deemix, stored_arl
from deemix_helper.deezer import CLIENT_ID, CLIENT_SECRET, Account, Catalogue, DeezerError, _md5, arl_from_login
from deemix_helper.follow import follow, pick
from deemix_helper.store import Store

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
            if ok and w.deemix_saves:
                w.login_file.write_text(json.dumps({"arl": body["arl"]}))
            if not ok:
                w.login_file.write_text(json.dumps({"arl": None}))  # deemix wipes it
            return {"status": 1 if ok else 0}
        if url.endswith("/api/addToQueue"):
            w.queued.append(body["url"])
            return {"result": True}
        raise AssertionError(f"unexpected {method} {url}")


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def cfg(w, creds=True, tmp=None):
    d = w.login_file.parent
    if creds:
        (d / "email").write_text("me@example.com\n")
        (d / "password").write_text("pw\n")
    return Config(deemix_url="http://deemix:6595", login_file=str(w.login_file), db=str(d / "db.sqlite"),
                  email_file=str(d / "email") if creds else "", password_file=str(d / "password") if creds else "",
                  apprise_url="", bitrate=None, max_queue=100)


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
    out = follow(store, account(w), catalogue(w), Deemix("http://deemix:6595", FakeHttp(w)), today="2026-10-20",
                 log=lambda *_: None)
    assert out["queued"] == [] and w.queued == [] and store.release_state(10) == "baseline"
    # a new release from the old favourite, and a new favourite with a catalogue
    w.albums[1].append(rel(11, "Second", "ep", "2026-10-19"))
    w.favs.append({"ART_ID": "2", "ART_NAME": "New Band"})
    w.albums[2] = [rel(20, "Debut", "album", "2019-05-05"), rel(21, "Debut", "album", "2019-05-05", True),
                   rel(22, "Next", "single", "2099-01-01")]
    out = follow(store, account(w), catalogue(w), Deemix("http://deemix:6595", FakeHttp(w)), today="2026-10-20",
                 log=lambda *_: None)
    assert w.queued == ["https://www.deezer.com/album/11", "https://www.deezer.com/album/21"]
    assert store.artist(2)["mode"] == "backfill" and store.release_state(22) is None
    follow(store, account(w), catalogue(w), Deemix("http://deemix:6595", FakeHttp(w)), today="2026-10-20",
           log=lambda *_: None)
    assert len(w.queued) == 2, "nothing twice"


def test_dry_run_changes_nothing_and_limit_defers(w):
    store = Store(":memory:")
    store.set_meta("initialized", "x")  # not the first run: a new artist backfills
    w.albums[1] = [rel(i, f"T{i}", "single", "2020-01-01") for i in range(1, 6)]
    out = follow(store, account(w), catalogue(w), None, max_queue=3, dry_run=True, today="2026-10-20",
                 log=lambda *_: None)
    assert len(out["queued"]) == 3 and out["deferred"] == 2 and store.artist(1) is None and w.queued == []
    out = follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w)), max_queue=3, today="2026-10-20",
                 log=lambda *_: None)
    assert len(w.queued) == 3 and out["deferred"] == 2
    follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w)), max_queue=3, today="2026-10-20",
           log=lambda *_: None)
    assert len(w.queued) == 5


def test_a_failed_queue_is_retried(w):
    store = Store(":memory:")
    store.set_meta("initialized", "x")

    class Broken(Deemix):
        def queue(self, url, bitrate=None):
            from deemix_helper.deemix import DeemixError
            raise DeemixError("NotLoggedIn")
    out = follow(store, account(w), catalogue(w), Broken("x", FakeHttp(w)), today="2026-10-20", log=lambda *_: None)
    assert out["failed"] and store.release_state(10) == "failed"
    follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w)), today="2026-10-20", log=lambda *_: None)
    assert w.queued == ["https://www.deezer.com/album/10"] and store.release_state(10) == "queued"


def test_a_later_listing_of_the_same_title_is_skipped(w):
    store = Store(":memory:")
    follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w)), today="2026-10-20", log=lambda *_: None)
    w.albums[1].append(rel(12, "First", "album", "2026-10-01", True))  # an explicit version listed later
    follow(store, account(w), catalogue(w), Deemix("x", FakeHttp(w)), today="2026-10-20", log=lambda *_: None)
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
    from deemix_helper.http import HttpError

    class Down(FakeHttp):
        def request(self, *a, **k):
            raise HttpError("www.deezer.com: URLError")
    with pytest.raises(arlmod.RenewError, match="can't be reached"):
        arlmod.ensure(cfg(w), http=lambda timeout=30: Down(w), log=lambda *_: None)


def test_deemix_must_actually_save_it(w):
    w.valid, w.deemix_saves = {NEW}, False
    with pytest.raises(arlmod.RenewError, match="didn't save"):
        arlmod.ensure(cfg(w), http=factory(w), log=lambda *_: None)


def test_arl_from_login_rejects_garbage(w):
    w.issue = "not-an-arl"
    with pytest.raises(DeezerError, match="no ARL"):
        arl_from_login("me@example.com", "pw", http=FakeHttp(w))


# ---- favourites

def test_favorite_by_exact_name(w):
    from deemix_helper.follow import add_favorite
    r = add_favorite(account(w), catalogue(w), "k/da")
    assert r == {"id": 7, "name": "K/DA", "already": False} and w.favorited == [7]
    w.favs.append({"ART_ID": "7", "ART_NAME": "K/DA"})
    assert add_favorite(account(w), catalogue(w), "https://www.deezer.com/en/artist/7")["already"]


def test_no_exact_name_lists_the_closest(w):
    with pytest.raises(DeezerError, match="closest: K/DA"):
        catalogue(w).find_artist("KDA")
