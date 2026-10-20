"""Settings, all from the environment. Secrets come from files, never from
an environment variable's value, so `docker inspect` doesn't show them."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _read(path: str) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


@dataclass
class Config:
    deemix_url: str
    login_file: str
    db: str
    email_file: str
    password_file: str
    apprise_url: str
    bitrate: str | None
    max_queue: int

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(
            deemix_url=e("DEEMIX_URL", "http://deemix:6595"),
            login_file=e("DEEMIX_LOGIN", "/deemix/login.json"),
            db=e("DH_DB", "/config/deemix-helper.db"),
            email_file=e("DEEZER_EMAIL_FILE", ""),
            password_file=e("DEEZER_PASSWORD_FILE", ""),
            apprise_url=e("APPRISE_URL", ""),
            bitrate=e("DH_BITRATE") or None,
            max_queue=int(e("DH_MAX_QUEUE", "100")),
        )

    def credentials(self) -> tuple[str, str] | None:
        email, password = _read(self.email_file), _read(self.password_file)
        return (email, password) if email and password else None
