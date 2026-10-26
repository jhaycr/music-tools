"""The one place requests leave. A cookie jar per instance, so each login is
its own session; tests swap the whole class for a fake.

Nothing here logs a URL, a header or a body: the ARL goes in a cookie.
"""
from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class HttpError(Exception):
    """A request failed: network, TLS or an HTTP error status. Says which host, never the URL."""


class Http:
    def __init__(self, timeout: float = 30):
        self.jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.timeout = timeout

    def set_cookie(self, domain: str, name: str, value: str) -> None:
        self.jar.set_cookie(http.cookiejar.Cookie(
            0, name, value, None, False, domain, True, domain.startswith("."), "/", True,
            True, None, False, None, None, {}))

    def request(self, method: str, url: str, params: dict | None = None,
                body: dict | None = None, headers: dict | None = None):
        """JSON in, JSON out (None when the answer isn't JSON). Retries twice on network errors."""
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        data = None if body is None else json.dumps(body).encode()
        h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
        if data is not None:
            h["Content-Type"] = "application/json"
        host = urllib.parse.urlsplit(url).netloc
        for attempt in range(3):
            req = urllib.request.Request(url, data=data, method=method, headers=h)
            try:
                with self._opener.open(req, timeout=self.timeout) as r:
                    raw = r.read()
                break
            except urllib.error.HTTPError as e:
                raise HttpError(f"{host}: HTTP {e.code}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == 2:
                    reason = getattr(e, "reason", e)
                    raise HttpError(f"{host}: {type(reason).__name__}") from None
                time.sleep(2 ** attempt)
        try:
            return json.loads(raw) if raw else None
        except ValueError:
            return None
