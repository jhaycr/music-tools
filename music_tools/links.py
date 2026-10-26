"""`links`: put an artist, album or song of one library category into another.

The library is <root>/<Category>/<Artist>/<Album>/<song> (MT_LIBRARY).

A link puts an artist, an album or a song that lives in one category into
another as well (an album of Soundtracks in Kids, an artist of Christian in
Mom), without a second copy. It is a relative symlink at the same place in the
other category, so it resolves the same on the host and in any container that
mounts the library at another path.

Nothing here writes anything but symlinks, and the plain folders a song link
needs above it. Removing a link never touches what it points at.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

AUDIO = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".alac", ".wma"}


class LinkError(Exception):
    """A request that can't be done, said so a person can act on it."""


@dataclass(frozen=True)
class Link:
    path: str  # where the link is, relative to the root: Kids/Various Artists/Frozen (2013)
    target: str  # what it points at, relative to the root; "" when it leaves the root
    kind: str  # artist | album | song
    broken: bool

    @property
    def category(self) -> str:
        return self.path.split("/", 1)[0]


@dataclass(frozen=True)
class Entry:
    name: str
    path: str
    is_dir: bool
    link: str | None  # what it points at, when the entry is itself a link


def _kind(parts: tuple[str, ...], is_dir: bool) -> str:
    if not is_dir:
        return "song"
    return "artist" if len(parts) == 2 else "album"


class Library:
    def __init__(self, root: Path | str, exclude: set[str] | frozenset[str] = frozenset()):
        self.root = Path(root)
        self.exclude = {e.casefold() for e in exclude}

    # ---- what there is

    def categories(self) -> list[str]:
        """Top-level folders that can hold links. Hidden folders, links and the
        excluded ones (another tool's output, such as parental-advisory's) are not."""
        try:
            ds = [d for d in self.root.iterdir()
                  if d.is_dir() and not d.is_symlink() and not d.name.startswith(".")
                  and d.name.casefold() not in self.exclude]
        except OSError as e:
            raise LinkError(f"the library at {self.root} can't be read: {e.strerror}") from None
        return sorted((d.name for d in ds), key=str.casefold)

    def resolve(self, rel: str) -> tuple[Path, tuple[str, ...]]:
        """A path given relative to the root, checked: inside the root, under a
        category it may use. Returns (absolute path, its parts)."""
        parts = tuple(p for p in rel.replace("\\", "/").split("/") if p not in ("", "."))
        if not parts or any(p == ".." or p.startswith(".") for p in parts):
            raise LinkError(f"{rel!r} is not a path in the library")
        if parts[0] not in self.categories():
            raise LinkError(f"{parts[0]!r} is not a library category this app manages")
        return self.root.joinpath(*parts), parts

    def browse(self, rel: str) -> list[Entry]:
        path, parts = self.resolve(rel)
        if not path.is_dir():
            raise LinkError(f"{rel} is not a folder")
        out = []
        for d in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
            if d.name.startswith("."):
                continue
            is_dir = d.is_dir()
            if not is_dir and d.suffix.lower() not in AUDIO:
                continue
            link = self._target(d) if d.is_symlink() else None
            out.append(Entry(d.name, "/".join(parts + (d.name,)), is_dir, link))
        return out

    def search(self, query: str, limit: int = 60) -> list[Entry]:
        """Artists and albums whose name holds the query, in every category."""
        q = query.casefold().strip()
        if len(q) < 2:
            return []
        out = []
        for cat in self.categories():
            for artist in sorted((self.root / cat).iterdir(), key=lambda p: p.name.casefold()):
                if artist.name.startswith(".") or not artist.is_dir():
                    continue
                if q in artist.name.casefold():
                    out.append(Entry(artist.name, f"{cat}/{artist.name}", True,
                                     self._target(artist) if artist.is_symlink() else None))
                if artist.is_symlink():
                    continue
                for album in sorted(artist.iterdir(), key=lambda p: p.name.casefold()):
                    if album.is_dir() and not album.name.startswith(".") and q in album.name.casefold():
                        out.append(Entry(album.name, f"{cat}/{artist.name}/{album.name}", True,
                                         self._target(album) if album.is_symlink() else None))
                if len(out) >= limit:
                    return out[:limit]
        return out

    def links(self) -> list[Link]:
        """Every link in the categories, made here or by hand, broken ones too."""
        out = []
        for cat in self.categories():
            for dirpath, dirnames, filenames in os.walk(self.root / cat):
                for name in dirnames + filenames:
                    p = Path(dirpath, name)
                    if p.is_symlink():
                        parts = p.relative_to(self.root).parts
                        out.append(Link("/".join(parts), self._target(p),
                                        _kind(parts, p.is_dir() or name in dirnames), not p.exists()))
                dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
        return sorted(out, key=lambda l: l.path.casefold())

    def _target(self, link: Path) -> str:
        real = Path(os.path.normpath(link.parent / os.readlink(link)))
        try:
            return "/".join(real.relative_to(self.root).parts)
        except ValueError:
            return ""

    # ---- changes

    def add(self, source: str, category: str) -> Link:
        """Link `source` (Category/Artist[/Album[/song]]) into `category`, at the
        same place: General/Artist/Album -> Kids/Artist/Album."""
        src, parts = self.resolve(source)
        if len(parts) < 2:
            raise LinkError("pick an artist, an album or a song, not a whole category")
        if category not in self.categories():
            raise LinkError(f"{category!r} is not a library category this app manages")
        if category == parts[0]:
            raise LinkError(f"{source} is already in {category}")
        if src.is_symlink():
            real = self._target(src)
            if not real or not src.exists():
                raise LinkError(f"{source} is itself a link, and what it points at is gone or outside the library")
            return self.add(real, category)
        if not src.exists():
            raise LinkError(f"{source} does not exist")
        if not src.is_dir() and src.suffix.lower() not in AUDIO:
            raise LinkError(f"{source} is not a song")
        dest = self.root.joinpath(category, *parts[1:])
        for anc in list(reversed(dest.parents))[len(self.root.parents) + 2:]:
            if anc.is_symlink():
                raise LinkError(f"{'/'.join(anc.relative_to(self.root).parts)} is already a link, "
                                "so everything under it is in that category already")
        if dest.is_symlink() or dest.exists():
            what = "a link" if dest.is_symlink() else "a folder" if dest.is_dir() else "a file"
            raise LinkError(f"{'/'.join(dest.relative_to(self.root).parts)} is already {what}"
                            + (": link the albums inside it one by one instead" if dest.is_dir() and not dest.is_symlink()
                               and src.is_dir() else ""))
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(os.path.relpath(src, dest.parent), dest)
        return Link("/".join(dest.relative_to(self.root).parts), "/".join(parts),
                    _kind(dest.relative_to(self.root).parts, src.is_dir()), False)

    def remove(self, link: str) -> str:
        """Remove a link (only a link), then the folders above it left empty, up
        to its category. Returns what it pointed at."""
        path, parts = self.resolve(link)
        if not path.is_symlink():
            raise LinkError(f"{link} is not a link; this app only removes links")
        target = self._target(path)
        path.unlink()
        top = self.root / parts[0]
        d = path.parent
        while d != top and d != self.root and not d.is_symlink():
            try:
                d.rmdir()  # only when empty
            except OSError:
                break
            d = d.parent
        return target
