"""One line per event, `<UTC time> <LEVEL> <tool>: <message>`, on stdout so
`docker logs` keeps the order. ERROR lines are what to alert on."""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone


def _line(level: str, tool: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, part in enumerate(str(msg).splitlines() or [""]):
        print(f"{ts} {level:5s} {tool}: {part}" if i == 0 else f"{ts} {level:5s} {tool}:   {part}", flush=True)


def info(tool: str, msg: str) -> None:
    _line("INFO", tool, msg)


def warn(tool: str, msg: str) -> None:
    _line("WARN", tool, msg)


def error(tool: str, msg: str, exc: bool = False) -> None:
    _line("ERROR", tool, msg)
    if exc:
        _line("ERROR", tool, "".join(traceback.format_exception(*sys.exc_info())).rstrip())
