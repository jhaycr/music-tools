"""Optional Apprise notification (an apprise-api /notify/<key> URL)."""
from __future__ import annotations

from .http import Http, HttpError


def notify(url: str, title: str, body: str, http: Http | None = None) -> None:
    if not url:
        return
    try:
        (http or Http(timeout=20)).request("POST", url, body={"title": title, "body": body})
    except HttpError as e:
        print(f"notify: {e}")
