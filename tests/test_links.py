import os

import pytest

from music_tools.links import Library, LinkError


@pytest.fixture
def lib(tmp_path):
    """General, Kids, Soundtracks and Family (another tool's output, excluded)."""
    for p in ("General/Nickel Creek/This Side (2002)/01 - Spit on a Stranger.flac",
              "General/Nickel Creek/This Side (2002)/cover.jpg",
              "Soundtracks/Various Artists/Frozen (2013)/01 - Let It Go.flac",
              "Kids/Various Artists/Disney Mandarin/01 - Into the Unknown.mp3",
              "Family/Nickel Creek/This Side (2002)/x.flac"):
        f = tmp_path / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"audio")
    (tmp_path / ".trash").mkdir()
    return Library(tmp_path, {"Family"})


def test_categories_leave_out_the_excluded_and_hidden(lib):
    assert lib.categories() == ["General", "Kids", "Soundtracks"]


def test_an_album_link_is_relative_and_shows_the_same_files(lib):
    link = lib.add("Soundtracks/Various Artists/Frozen (2013)", "Kids")
    p = lib.root / "Kids/Various Artists/Frozen (2013)"
    assert link.path == "Kids/Various Artists/Frozen (2013)" and link.kind == "album"
    assert os.readlink(p) == "../../Soundtracks/Various Artists/Frozen (2013)"
    assert (p / "01 - Let It Go.flac").read_bytes() == b"audio"
    assert lib.links() == [link]


def test_an_artist_link_and_a_song_link(lib):
    a = lib.add("General/Nickel Creek", "Kids")
    assert a.kind == "artist" and os.readlink(lib.root / "Kids/Nickel Creek") == "../General/Nickel Creek"
    lib.remove(a.path)
    s = lib.add("General/Nickel Creek/This Side (2002)/01 - Spit on a Stranger.flac", "Soundtracks")
    assert s.kind == "song"
    p = lib.root / s.path
    assert p.is_symlink() and not p.parent.is_symlink(), "the album folder above a song link is a real one"
    assert p.read_bytes() == b"audio"


def test_removing_a_song_link_removes_the_folders_it_made(lib):
    s = lib.add("General/Nickel Creek/This Side (2002)/01 - Spit on a Stranger.flac", "Soundtracks")
    assert lib.remove(s.path) == "General/Nickel Creek/This Side (2002)/01 - Spit on a Stranger.flac"
    assert not (lib.root / "Soundtracks/Nickel Creek").exists()
    assert (lib.root / "Soundtracks").is_dir(), "never the category itself"
    assert (lib.root / "General/Nickel Creek/This Side (2002)/01 - Spit on a Stranger.flac").exists()


def test_removing_an_album_link_keeps_a_folder_that_holds_other_things(lib):
    lib.add("Soundtracks/Various Artists/Frozen (2013)", "Kids")
    lib.remove("Kids/Various Artists/Frozen (2013)")
    assert (lib.root / "Kids/Various Artists/Disney Mandarin").is_dir()
    assert (lib.root / "Soundtracks/Various Artists/Frozen (2013)/01 - Let It Go.flac").exists()


@pytest.mark.parametrize("source,category,why", [
    ("General", "Kids", "not a whole category"),
    ("General/Nickel Creek", "General", "already in General"),
    ("General/Nobody", "Kids", "does not exist"),
    ("General/Nickel Creek/This Side (2002)/cover.jpg", "Kids", "not a song"),
    ("General/Nickel Creek", "Family", "not a library category"),
    ("Family/Nickel Creek", "Kids", "not a library category"),
    ("General/../Family/Nickel Creek", "Kids", "not a path"),
    ("General/Nickel Creek", "Nowhere", "not a library category"),
])
def test_refusals(lib, source, category, why):
    with pytest.raises(LinkError, match=why):
        lib.add(source, category)


def test_a_real_folder_in_the_way_is_never_replaced(lib):
    (lib.root / "Kids/Nickel Creek").mkdir()
    with pytest.raises(LinkError, match="already a folder: link the albums inside it one by one"):
        lib.add("General/Nickel Creek", "Kids")
    lib.add("General/Nickel Creek/This Side (2002)", "Kids")  # the album, one by one, works


def test_nothing_is_linked_under_an_existing_link(lib):
    lib.add("General/Nickel Creek", "Kids")
    with pytest.raises(LinkError, match="Kids/Nickel Creek is already a link"):
        lib.add("General/Nickel Creek/This Side (2002)", "Kids")


def test_linking_a_link_links_what_it_points_at(lib):
    lib.add("Soundtracks/Various Artists/Frozen (2013)", "Kids")
    link = lib.add("Kids/Various Artists/Frozen (2013)", "General")
    assert link.target == "Soundtracks/Various Artists/Frozen (2013)"
    assert os.readlink(lib.root / link.path) == "../../Soundtracks/Various Artists/Frozen (2013)"


def test_only_links_are_removed(lib):
    with pytest.raises(LinkError, match="not a link"):
        lib.remove("General/Nickel Creek")
    assert (lib.root / "General/Nickel Creek").is_dir()


def test_links_made_by_hand_and_broken_ones_are_listed(lib):
    os.symlink("../General/Gone", lib.root / "Kids/Gone")
    os.symlink("../../../elsewhere", lib.root / "Kids/Various Artists/Out")
    [gone, out] = lib.links()
    assert (gone.path, gone.target, gone.broken) == ("Kids/Gone", "General/Gone", True)
    assert out.target == "" and out.broken
    lib.remove("Kids/Gone")  # a broken one can be removed


def test_search_finds_artists_and_albums_everywhere(lib):
    lib.add("Soundtracks/Various Artists/Frozen (2013)", "Kids")
    hits = {(h.path, h.link) for h in lib.search("froz")}
    assert hits == {("Soundtracks/Various Artists/Frozen (2013)", None),
                    ("Kids/Various Artists/Frozen (2013)", "Soundtracks/Various Artists/Frozen (2013)")}
    assert [h.path for h in lib.search("nickel")] == ["General/Nickel Creek"]
