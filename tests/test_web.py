import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

import pytest

from music_tools.artists import ArtistError
from music_tools.config import Config
from music_tools.store import Store
from music_tools.web import Tools, make_handler


@pytest.fixture
def cfg(tmp_path):
    for p in ("General/Nickel Creek/This Side (2002)/01 - Spit on a Stranger.flac",
              "Soundtracks/Various Artists/Frozen (2013)/01 - Let It Go.flac",
              "Kids/Various Artists/Disney Mandarin/01 - Into the Unknown.mp3",
              "Family/Nickel Creek/This Side (2002)/x.flac"):
        f = tmp_path / "music" / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"audio")
    return Config(deemix_url="http://deemix:6595", login_file=str(tmp_path / "login.json"), db=str(tmp_path / "db.sqlite"),
                  apprise_url="", bitrate=None, max_queue=100, library=str(tmp_path / "music"),
                  links_exclude=frozenset({"Family"}))


class FakeTools(Tools):
    """The pages' tools with Deezer and MusicBrainz replaced."""
    logged_in = True
    favourited: list = []

    def login(self):
        return ("valid", "me") if self.logged_in else ("invalid", "")

    def restore(self):
        return (True, "deemix's login works; nothing to put back") if self.logged_in else (False, "login is invalid; paste a fresh ARL")

    def search(self, name):
        return ([{"id": 7, "name": "K/DA", "fans": 9, "releases": 3, "favourite": False,
                  "latest": [{"title": "FIRESTARTER", "type": "ep", "date": "2026-10-22"}],
                  "mb": {"id": "x", "bio": "virtual group", "common": 3, "linked": True, "wikipedia": ""},
                  "library": [{"path": "General/K+DA", "albums": 3, "on_deezer": 3}]},
                 {"id": 8, "name": "KDA Fans", "fans": 1, "releases": 0, "favourite": True, "latest": [], "mb": None,
                  "library": []}], [], "")

    def deezer_account(self):
        if not self.logged_in:
            from music_tools.web import PlaylistError
            raise PlaylistError(503, "deemix's Deezer login is invalid; fix it under Deezer login first")

        class Account:
            def playlists(self):
                return [{"id": 15, "title": "Glow", "songs": 1}]

            def playlist_songs(self, pid):
                return [{"artist": "RAT BOY", "title": "Who's Ready for Tomorrow"}]
        return Account()

    def spotify_playlist(self, ref):
        from music_tools.spotify import playlist_id
        return {"id": playlist_id(ref), "title": "Glow", "owner": "me", "tracks": []}

    def favorite(self, ref):
        if ref != "7":
            raise ArtistError("names are not accepted")
        self.favourited.append(ref)
        return {"id": 7, "name": "K/DA", "already": False}


@pytest.fixture
def web(cfg):
    tools = FakeTools(cfg)
    tools.favourited = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tools))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)

    def call(method, path, data=None, headers=None, json_body=None):
        hdrs, body = dict(headers or {}), None
        if json_body is not None:
            body, hdrs["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
        try:
            r = opener.open(urllib.request.Request(base + path, data=body, method=method, headers=hdrs))
        except urllib.error.HTTPError as ex:
            r = ex
        return r.status, dict(r.headers), r.read().decode()
    call.tools = tools
    yield call
    httpd.shutdown()


def test_every_page_answers_and_the_root_goes_to_links(web):
    assert web("GET", "/")[1]["Location"] == "/links"
    for p in ("/links", "/links/browse?path=General", "/links/search?q=frozen", "/artists", "/artists?q=kda",
              "/following", "/login"):
        status, _, body = web("GET", p)
        assert status == 200, p
        assert 'href="/following"' in body, "the same navigation everywhere"
    assert web("GET", "/healthz")[2] == "ok\n"


def test_add_and_remove_a_link_through_the_forms(web, cfg):
    status, h, _ = web("POST", "/links/add", {"path": "Soundtracks/Various Artists/Frozen (2013)", "category": "Kids",
                                              "back": "/links/browse?path=Soundtracks"})
    assert status == 303 and h["Location"].startswith("/links/browse?path=Soundtracks&msg=Added")
    body = web("GET", "/links")[2]
    assert "Various Artists/Frozen (2013)" in body and "Remove" in body
    status, h, _ = web("POST", "/links/remove", {"link": "Kids/Various Artists/Frozen (2013)", "back": "/links"})
    assert status == 303 and web.tools.links.links() == []


def test_a_refused_link_comes_back_with_the_reason(web):
    _, h, _ = web("POST", "/links/add", {"path": "General/Nickel Creek", "category": "General", "back": "/links"})
    assert "err=" in h["Location"] and "already+in+General" in h["Location"]


def test_back_never_leaves_the_app(web):
    _, h, _ = web("POST", "/links/add", {"path": "General/Nickel Creek", "category": "Kids", "back": "https://evil.example/"})
    assert h["Location"].startswith("/?msg=")


def test_a_cross_site_post_is_refused(web):
    status, _, _ = web("POST", "/links/add", {"path": "General/Nickel Creek", "category": "Kids"},
                       headers={"Origin": "https://evil.example"})
    assert status == 403 and web.tools.links.links() == []
    status, _, _ = web("POST", "/artists/favorite", {"ref": "7"}, headers={"Origin": "https://evil.example"})
    assert status == 403 and web.tools.favourited == []


def test_the_links_api(web):
    status, _, body = web("POST", "/api/links", json_body={"path": "General/Nickel Creek", "category": "Kids"})
    assert status == 201 and json.loads(body)["path"] == "Kids/Nickel Creek"
    assert [l["path"] for l in json.loads(web("GET", "/api/links")[2])] == ["Kids/Nickel Creek"]
    status, _, body = web("POST", "/api/links", json_body={"path": "General/Nickel Creek", "category": "Kids"})
    assert status == 400 and "already a link" in json.loads(body)["error"]
    assert web("DELETE", "/api/links?" + urllib.parse.urlencode({"link": "Kids/Nickel Creek"}))[0] == 200


def test_excluded_categories_are_not_offered(web):
    body = web("GET", "/links")[2]
    assert "Family" not in body
    assert web("GET", "/links/browse?path=Family")[0] == 404


def test_artist_search_shows_the_evidence_and_favourites(web):
    body = web("GET", "/artists?q=kda")[2]
    assert "FIRESTARTER" in body and "virtual group" in body and "General/K+DA" in body
    assert body.count(">Favourite</button>") == 1, "the one already a favourite has no button"
    status, h, _ = web("POST", "/artists/favorite", {"ref": "7", "back": "/artists?q=kda"})
    assert status == 303 and "is+now+a+favourite" in h["Location"] and web.tools.favourited == ["7"]
    _, h, _ = web("POST", "/artists/favorite", {"ref": "K/DA", "back": "/artists?q=kda"})
    assert "err=names+are+not+accepted" in h["Location"]


def test_following_lists_what_the_store_holds(web, cfg):
    store = Store(cfg.db)
    store.add_artist(1, "Nickel Creek", "baseline")
    store.mark({"id": 9, "title": "Celebrants", "type": "album", "date": "2023-03-24"}, 1, "queued")
    store.mark({"id": 10, "title": "Strangers", "type": "single", "date": "2023-01-24"}, 1, "failed", "NotLoggedIn")
    body = web("GET", "/following")[2]
    assert "Nickel Creek" in body and "Celebrants" in body and "NotLoggedIn" in body


def test_login_page_and_restore(web):
    assert "working" in web("GET", "/login")[2]
    _, h, _ = web("POST", "/login/restore", {})
    assert h["Location"].startswith("/login?msg=")
    web.tools.logged_in = False
    assert "does not work" in web("GET", "/login")[2]
    _, h, _ = web("POST", "/login/restore", {})
    assert h["Location"].startswith("/login?err=")


def test_pages_escape_names(web, cfg):
    Path(cfg.library, "General", "<img src=x onerror=alert(1)>").mkdir()
    body = web("GET", "/links/browse?path=General")[2]
    assert "<img src=x" not in body and "&lt;img src=x onerror=alert(1)&gt;" in body


def test_the_playlists_api_reads_only_the_accounts_own(web):
    status, _, body = web("GET", "/api/deezer/playlists")
    assert status == 200 and json.loads(body) == [{"id": 15, "title": "Glow", "songs": 1}]
    status, _, body = web("GET", "/api/deezer/playlists/15")
    assert status == 200 and json.loads(body)["tracks"][0]["title"] == "Who's Ready for Tomorrow"
    assert web("GET", "/api/deezer/playlists/16")[0] == 404, "not the account's own"
    for bad in ("/api/deezer/playlists/1%2F2", "/api/deezer/playlists/-1", "/api/deezer/playlists/" + "9" * 21,
                "/api/deezer/other"):
        assert web("GET", bad)[0] == 404, bad
    assert web("GET", "/api/spotify/playlists/0a1B2c3D4e5F6g7H8i9J0k")[0] == 200
    assert web("GET", "/api/spotify/playlists/nope")[0] == 400
    web.tools.logged_in = False
    assert web("GET", "/api/deezer/playlists")[0] == 503
    web.tools.logged_in = True
