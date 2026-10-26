"""Settings, all from the environment."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    deemix_url: str
    login_file: str
    db: str
    apprise_url: str
    bitrate: str | None
    max_queue: int
    library: str = ""
    keep_file: str = ""
    links_exclude: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(
            deemix_url=e("DEEMIX_URL", "http://deemix:6595"),
            login_file=e("DEEMIX_LOGIN", "/deemix/login.json"),
            db=e("MT_DB", "/config/music-tools.db"),
            apprise_url=e("APPRISE_URL", ""),
            bitrate=e("MT_BITRATE") or None,
            max_queue=int(e("MT_MAX_QUEUE", "100")),
            library=e("MT_LIBRARY", "/music"),
            keep_file=e("MT_ARL_KEEP", "/config/last-good-arl"),
            links_exclude=frozenset(x.strip() for x in e("MT_LINKS_EXCLUDE", "").split(",") if x.strip()),
        )

