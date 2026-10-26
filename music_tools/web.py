"""music-tools in a browser: the tools a person runs by hand. The scheduled ones
(`run`, `deemix run`, `deemix follow`) stay with cron.

  /links        browse and search the library; put an artist, album or song of one
                category into another as a link; see and remove every link
  /artists      tell same-named artists apart, and favourite the right one on Deezer
  /following    the artists followed and the latest releases queued
  /login        deemix's Deezer login: does it work, put the last working one back

Plain HTML forms that POST and redirect back; a JSON twin for links:

  GET    /api/links                       every link: path, target, kind, broken
  POST   /api/links  {"path", "category"} make a link
  DELETE /api/links?link=<path>           remove a link
  GET    /healthz

No sign-in of its own: run it behind one (Authentik, a VPN). A POST or DELETE
whose Origin names another site is refused, so another page can't drive it.
"""
from __future__ import annotations

import html
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from .artists import ArtistError, MusicBrainz, resolve, search as artist_search
from .config import Config
from .deezer import Catalogue, DeezerError
from .follow import add_favorite
from .http import HttpError
from .links import Library, LinkError
from .store import Store

TITLE = "music-tools"
NAV = [("/links", "Links"), ("/artists", "Artists"), ("/following", "Following"), ("/login", "Deezer login")]

CSS = """
:root { --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --accent:#2563eb; --bad:#b91c1c; --ok:#15803d; --card:#fff; }
@media (prefers-color-scheme: dark) { :root { --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --line:#44403c; --accent:#60a5fa; --bad:#f87171; --ok:#4ade80; --card:#292524; } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui,sans-serif }
main { max-width:900px; margin:0 auto; padding:16px }
a { color:var(--accent); text-decoration:none } a:hover { text-decoration:underline }
nav { display:flex; flex-wrap:wrap; gap:.3rem 1rem; align-items:baseline; margin:.2rem 0 1rem; border-bottom:1px solid var(--line); padding-bottom:.6rem }
nav b { font-size:1.15rem; margin-right:.6rem } nav a.on { color:var(--fg); font-weight:600 }
h2 { font-size:1.05rem; margin:1.4rem 0 .4rem }
.crumbs { color:var(--muted); margin-bottom:.6rem } .muted { color:var(--muted) } .small { font-size:.9rem }
ul.rows { list-style:none; padding:0; margin:0; border:1px solid var(--line); border-radius:8px; background:var(--card) }
ul.rows li { display:flex; flex-wrap:wrap; gap:.4rem .8rem; align-items:center; padding:.5rem .7rem; border-top:1px solid var(--line) }
ul.rows li:first-child { border-top:0 } .grow { flex:1 1 16rem; min-width:0; overflow-wrap:anywhere }
form.inline { display:flex; gap:.3rem; margin:0 } select,input,button { font:inherit; padding:.25rem .5rem; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg) }
button { cursor:pointer } button.del { color:var(--bad) }
.msg { padding:.6rem .8rem; border-radius:8px; margin-bottom:1rem; border:1px solid var(--line); background:var(--card) } .msg.err { border-color:var(--bad); color:var(--bad) }
.tag { font-size:.8rem; color:var(--muted); border:1px solid var(--line); border-radius:99px; padding:0 .45rem; white-space:nowrap }
.tag.bad { color:var(--bad); border-color:var(--bad) } .tag.ok { color:var(--ok); border-color:var(--ok) }
.search { display:flex; gap:.4rem; margin-bottom:1rem } .search input { flex:1 }
table { border-collapse:collapse; width:100% } td,th { text-align:left; padding:.3rem .5rem; border-top:1px solid var(--line); vertical-align:top } th { font-weight:600 }
.scroll { overflow-x:auto }
"""


def e(s) -> str:
    return html.escape(str(s), quote=True)


def _url(route: str, **kv) -> str:
    return route + ("?" + urlencode(kv) if kv else "")


def _with(url: str, **kv) -> str:
    return url + ("&" if "?" in url else "?") + urlencode(kv)


def _local(back: str) -> str:
    """Only redirect back within this app."""
    u = urlsplit(back or "/")
    if u.scheme or u.netloc or not u.path.startswith("/") or u.path.startswith("//"):
        return "/"
    return back


class Tools:
    """What the pages do, apart from rendering. Tests swap the network-bound parts."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.links = Library(cfg.library, cfg.links_exclude)

    def login(self) -> tuple[str, str]:
        from .arl import check
        state, account = check(self.cfg)
        who = (account.user.get("BLOG_NAME") or str(account.user["USER_ID"])) if account else ""
        return state, who

    def restore(self) -> tuple[bool, str]:
        from .arl import LoginError, ensure
        said = []
        try:
            _, done = ensure(self.cfg, log=said.append)
        except LoginError as ex:
            return False, str(ex)
        return True, ("deemix's login was put back: the last one that worked" if done == "restored"
                      else "deemix's login works; nothing to put back")

    def search(self, name: str) -> tuple[list, list, str]:
        from .arl import check
        state, account = check(self.cfg)
        favs = {f["id"] for f in account.favorite_artists()} if account else set()
        note = "" if account else f"deemix's Deezer login is {state}: favourites can't be marked or added"
        cands, unmatched = artist_search(name, Catalogue(), MusicBrainz(), self.cfg.library, favs)
        return cands, unmatched, note

    def favorite(self, ref: str) -> dict:
        from .arl import check
        state, account = check(self.cfg)
        if not account:
            raise ArtistError(f"deemix's Deezer login is {state}; fix it under Deezer login first")
        cat = Catalogue()
        return add_favorite(account, cat, resolve(ref, cat, MusicBrainz()))

    def following(self) -> dict:
        store = Store(self.cfg.db)
        rows = [dict(r) | {"counts": store.counts(r["id"])} for r in store.artists()]
        return {"since": store.get_meta("initialized"), "artists": rows, "recent": [dict(r) for r in store.recent(30)]}


# ---- pages

def page(route: str, title: str, body: str, msg: str = "", err: str = "") -> bytes:
    nav = "".join(f'<a href="{h}"{" class=on" if route.startswith(h) else ""}>{e(t)}</a>' for h, t in NAV)
    note = (f'<div class="msg err">{e(err)}</div>' if err else "") + (f'<div class="msg">{e(msg)}</div>' if msg else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(title)}</title>
<style>{CSS}</style></head><body><main>
<nav><b>{e(TITLE)}</b>{nav}</nav>{note}{body}</main></body></html>""".encode()


def _add_form(lib: Library, path: str, back: str) -> str:
    here = path.split("/", 1)[0]
    opts = "".join(f"<option>{e(c)}</option>" for c in lib.categories() if c != here)
    return (f'<form class="inline" method="post" action="/links/add"><input type="hidden" name="path" value="{e(path)}">'
            f'<input type="hidden" name="back" value="{e(back)}"><select name="category" aria-label="Category" required>'
            f'<option value="" selected disabled>Add to…</option>{opts}</select>'
            f'<button>Add</button></form>')


def _remove_form(link: str, back: str) -> str:
    return (f'<form class="inline" method="post" action="/links/remove"><input type="hidden" name="link" value="{e(link)}">'
            f'<input type="hidden" name="back" value="{e(back)}"><button class="del">Remove</button></form>')


def _browse_url(path: str) -> str:
    return _url("/links/browse", path=path)


def _links_search_box(q: str = "") -> str:
    return (f'<form class="search" action="/links/search" method="get"><input name="q" value="{e(q)}" '
            'placeholder="Find an artist or album in the library" aria-label="Find an artist or album"><button>Search</button></form>')


def links_home(lib: Library) -> str:
    links = lib.links()
    out = [_links_search_box(),
           '<p class="muted small">Put an artist, album or song of one category into another, without a copy: '
           'browse to it and pick the category.</p><h2>Browse</h2><ul class="rows">']
    out += [f'<li><a class="grow" href="{e(_browse_url(c))}">{e(c)}</a></li>' for c in lib.categories()]
    out.append(f"</ul><h2>Links ({len(links)})</h2>")
    if not links:
        out.append('<p class="muted">None yet.</p>')
    by_cat: dict[str, list] = {}
    for l in links:
        by_cat.setdefault(l.category, []).append(l)
    for cat, ls in by_cat.items():
        out.append(f'<h2>{e(cat)}</h2><ul class="rows">')
        for l in ls:
            shown = l.path.split("/", 1)[1]
            src = (f'from <a href="{e(_browse_url(l.target.rsplit("/", 1)[0] if l.kind == "song" else l.target))}">'
                   f'{e(l.target.split("/", 1)[0])}</a>') if l.target else "points outside the library"
            bad = ' <span class="tag bad">broken</span>' if l.broken else ""
            out.append(f'<li><span class="grow">{e(shown)} <span class="tag">{e(l.kind)}</span>{bad}<br>'
                       f'<span class="muted small">{src}</span></span>{_remove_form(l.path, "/links")}</li>')
        out.append("</ul>")
    return "".join(out)


def links_browse(lib: Library, path: str) -> str:
    entries = lib.browse(path)
    parts = path.split("/")
    crumbs = " / ".join(f'<a href="{e(_browse_url("/".join(parts[:i + 1])))}">{e(p)}</a>' for i, p in enumerate(parts))
    back = _browse_url(path)
    out = [_links_search_box(), f'<div class="crumbs"><a href="/links">Links</a> / {crumbs}</div>']
    if len(parts) >= 2:
        out.append(f'<ul class="rows"><li><span class="grow">All of <b>{e(parts[-1])}</b></span>{_add_form(lib, path, back)}</li></ul><h2>Inside</h2>')
    out.append('<ul class="rows">')
    if not entries:
        out.append('<li class="muted">Empty</li>')
    for en in entries:
        name = f'<a href="{e(_browse_url(en.path))}">{e(en.name)}</a>' if en.is_dir else e(en.name)
        if en.link is not None:
            out.append(f'<li><span class="grow">{name} <span class="tag">link</span><br>'
                       f'<span class="muted small">to {e(en.link or "outside the library")}</span></span>{_remove_form(en.path, back)}</li>')
        else:
            out.append(f'<li><span class="grow">{name}</span>{_add_form(lib, en.path, back)}</li>')
    out.append("</ul>")
    return "".join(out)


def links_search(lib: Library, q: str) -> str:
    hits = lib.search(q)
    out = [_links_search_box(q)]
    if not hits:
        return out[0] + f'<p class="muted">Nothing in the library named like “{e(q)}”.</p>'
    back = _url("/links/search", q=q)
    out.append('<ul class="rows">')
    for en in hits:
        where = e(en.path.rsplit("/", 1)[0])
        if en.link is not None:
            out.append(f'<li><span class="grow">{e(en.name)} <span class="tag">link</span><br><span class="muted small">{where}</span></span>'
                       f'{_remove_form(en.path, back)}</li>')
        else:
            out.append(f'<li><span class="grow"><a href="{e(_browse_url(en.path))}">{e(en.name)}</a><br>'
                       f'<span class="muted small">{where}</span></span>{_add_form(lib, en.path, back)}</li>')
    out.append("</ul>")
    return "".join(out)


def artists_page(tools: Tools, q: str) -> str:
    box = (f'<form class="search" action="/artists" method="get"><input name="q" value="{e(q)}" '
           'placeholder="Artist name, as on Deezer" aria-label="Artist name"><button>Search</button></form>'
           '<p class="muted small">Many artists share a name. Each Deezer match comes with its latest releases, the '
           'MusicBrainz artist it lines up with and the library folder that is theirs. MusicBrainz allows one request a '
           'second, so a search takes a few seconds. Favouriting downloads the artist’s releases from today on.</p>')
    if not q.strip():
        return box
    cands, unmatched, note = tools.search(q)
    out = [box]
    if note:
        out.append(f'<div class="msg err">{e(note)}</div>')
    if not cands:
        out.append(f'<p class="muted">No Deezer artist named like “{e(q)}”.</p>')
    out.append('<ul class="rows">')
    for c in cands:
        fav = '<span class="tag ok">favourite</span>' if c["favourite"] else (
            f'<form class="inline" method="post" action="/artists/favorite"><input type="hidden" name="ref" value="{e(c["id"])}">'
            f'<input type="hidden" name="back" value="{e(_url("/artists", q=q))}"><button>Favourite</button></form>')
        lines = [f'<b><a href="https://www.deezer.com/artist/{e(c["id"])}">{e(c["name"])}</a></b> '
                 f'<span class="muted small">Deezer {e(c["id"])} · {e(c.get("fans", 0))} fans · {e(c["releases"])} releases</span>']
        if c.get("latest"):
            lines.append("latest: " + "; ".join(f'{e(r["title"])} ({e(r["type"])}, {e(r["date"])})' for r in c["latest"]))
        m = c.get("mb")
        if m:
            how = "links to this Deezer artist" if m["linked"] else f'{m["common"]} release titles in common'
            wiki = f' · <a href="{e(m["wikipedia"])}">Wikipedia</a>' if m.get("wikipedia") else ""
            lines.append(f'MusicBrainz: {e(m["bio"] or "no details")} ({e(how)}){wiki}')
        elif c.get("mb_error"):
            lines.append(f'MusicBrainz not reached: {e(c["mb_error"])}')
        for f in c.get("library", []):
            lines.append(f'in your library: {e(f["path"])} ({e(f["on_deezer"])} of its {e(f["albums"])} albums are this artist’s)')
        out.append(f'<li><span class="grow">{"<br>".join(lines)}</span>{fav}</li>')
    out.append("</ul>")
    for f in unmatched:
        out.append(f'<p class="muted small">In your library but none of these: {e(f["path"])} ({e(f["albums"])} albums)</p>')
    return "".join(out)


def following_page(tools: Tools) -> str:
    d = tools.following()
    out = [f'<p class="muted small">{"Following since " + e(d["since"][:10]) if d["since"] else "Not run yet."} '
           'New releases of each favourite queue in deemix every hour. Favourite more under Artists.</p>',
           '<h2>Artists</h2><div class="scroll"><table><tr><th>Artist</th><th>Queued</th><th>Failed</th><th>Since</th></tr>']
    for a in d["artists"]:
        flag = ' <span class="tag">no longer a favourite</span>' if not a["active"] else ""
        out.append(f'<tr><td>{e(a["name"])}{flag}</td><td>{e(a["counts"].get("queued", 0))}</td>'
                   f'<td>{e(a["counts"].get("failed", 0))}</td><td>{e(a["first_seen"][:10])}</td></tr>')
    out.append('</table></div><h2>Latest</h2><div class="scroll"><table><tr><th>When</th><th>Release</th><th></th></tr>')
    for r in d["recent"]:
        state = f'<span class="tag{" bad" if r["state"] == "failed" else ""}">{e(r["state"])}</span>'
        err = f'<br><span class="muted small">{e(r["error"])}</span>' if r["state"] == "failed" and r.get("error") else ""
        out.append(f'<tr><td class="small">{e(r["at"][:16].replace("T", " "))}</td><td>{e(r["artist"])} – {e(r["title"])} '
                   f'<span class="muted small">({e(r["type"])}, {e(r["date"])})</span>{err}</td><td>{state}</td></tr>')
    out.append("</table></div>")
    return "".join(out)


def login_page(tools: Tools) -> str:
    state, who = tools.login()
    ok = state == "valid"
    head = (f'<p><span class="tag ok">working</span> deemix is logged in to Deezer as {e(who)}.</p>' if ok else
            f'<p><span class="tag bad">{e(state)}</span> deemix’s Deezer login does not work.</p>')
    return head + (
        '<p class="muted small">Every run keeps the last login that worked and gives it back when deemix loses it. '
        'When that one has expired too, log deemix in with a fresh ARL from a browser (the <code>arl</code> cookie on '
        'deezer.com); the next run keeps it.</p>'
        '<form method="post" action="/login/restore"><button>Put back the last working login</button></form>')


def make_handler(tools: Tools):
    lib = tools.links

    class Handler(BaseHTTPRequestHandler):
        server_version = "music-tools"

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

        def send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
            self.send_header("Referrer-Policy", "same-origin")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, status: int, obj) -> None:
            self.send(status, json.dumps(obj, indent=1).encode(), "application/json")

        def redirect(self, url: str) -> None:
            self.send(HTTPStatus.SEE_OTHER, b"", headers={"Location": url})

        def query(self) -> dict[str, str]:
            return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

        def form(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024:
                raise ValueError("request too large")
            raw = self.rfile.read(n).decode("utf-8", "replace")
            if (self.headers.get("Content-Type") or "").startswith("application/json"):
                body = json.loads(raw or "{}")
                return body if isinstance(body, dict) else {}
            return {k: v[0] for k, v in parse_qs(raw).items()}

        def cross_site(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return False
            return origin == "null" or urlsplit(origin).netloc != (self.headers.get("Host") or "")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            route, q = urlsplit(self.path).path, self.query()
            msg, err = q.get("msg", ""), q.get("err", "")
            try:
                if route == "/healthz":
                    lib.categories()
                    return self.send(200, b"ok\n", "text/plain")
                if route == "/":
                    return self.redirect("/links")
                if route == "/api/links":
                    return self.json(200, [l.__dict__ for l in lib.links()])
                pages = {
                    "/links": lambda: ("Links", links_home(lib)),
                    "/links/browse": lambda: (q.get("path", ""), links_browse(lib, q.get("path", ""))),
                    "/links/search": lambda: ("Search", links_search(lib, q.get("q", ""))),
                    "/artists": lambda: ("Artists", artists_page(tools, q.get("q", ""))),
                    "/following": lambda: ("Following", following_page(tools)),
                    "/login": lambda: ("Deezer login", login_page(tools)),
                }
                if route in pages:
                    title, body = pages[route]()
                    return self.send(200, page(route, f"{title} – {TITLE}", body, msg, err))
            except LinkError as ex:
                return self.send(404, page(route, TITLE, '<p><a href="/links">Back to Links</a></p>', err=str(ex)))
            except (DeezerError, HttpError, ArtistError) as ex:
                return self.send(502, page(route, TITLE, "", err=f"Deezer or MusicBrainz didn't answer: {ex}"))
            self.send(404, page(route, TITLE, '<p><a href="/links">Back to Links</a></p>', err="No such page"))

        def do_POST(self):
            route = urlsplit(self.path).path
            api = route == "/api/links"
            if self.cross_site():
                return self.json(403, {"error": "cross-site request refused"}) if api else \
                    self.send(403, page(route, TITLE, "", err="Refused: that request came from another site."))
            try:
                f = self.form()
            except ValueError as ex:
                return self.json(400, {"error": str(ex)})
            back = _local(str(f.get("back", "/links")))
            try:
                if api or route == "/links/add":
                    link = lib.add(str(f.get("path", "")), str(f.get("category", "")))
                    if api:
                        return self.json(201, link.__dict__)
                    return self.redirect(_with(back, msg=f"Added: {link.path}"))
                if route == "/links/remove":
                    lib.remove(str(f.get("link", "")))
                    return self.redirect(_with(back, msg=f"Removed: {f.get('link')}"))
                if route == "/artists/favorite":
                    r = tools.favorite(str(f.get("ref", "")))
                    said = "was already a favourite" if r["already"] else "is now a favourite; its releases from today on download"
                    return self.redirect(_with(back, msg=f"{r['name']} {said}"))
                if route == "/login/restore":
                    ok, said = tools.restore()
                    return self.redirect(_with("/login", **({"msg": said} if ok else {"err": said})))
            except LinkError as ex:
                return self.json(400, {"error": str(ex)}) if api else self.redirect(_with(back, err=str(ex)))
            except (ArtistError, DeezerError, HttpError) as ex:
                return self.redirect(_with(back, err=str(ex)))
            except OSError as ex:
                said = f"the library refused the change: {ex.strerror}"
                return self.json(500, {"error": said}) if api else self.redirect(_with(back, err=said))
            self.send(404, page(route, TITLE, "", err="No such page"))

        def do_DELETE(self):
            if urlsplit(self.path).path != "/api/links":
                return self.json(404, {"error": "no such page"})
            if self.cross_site():
                return self.json(403, {"error": "cross-site request refused"})
            link = self.query().get("link", "")
            try:
                target = lib.remove(link)
            except LinkError as ex:
                return self.json(400, {"error": str(ex)})
            self.json(200, {"removed": link, "target": target})

    return Handler


def serve(cfg: Config, host: str, port: int) -> None:
    tools = Tools(cfg)
    httpd = ThreadingHTTPServer((host, port), make_handler(tools))
    print(f"music-tools web on http://{host}:{port}/ for {cfg.library}", flush=True)
    httpd.serve_forever()
