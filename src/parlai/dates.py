"""Date parsing for --since/--until flags.

Accepts:
- ISO dates: 2026-04-19, 2026-04-19T16:30:00, 2026-04
- Relative: 7d, 2w, 3mo, 1y, 24h, 90m
- Special: today, yesterday
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

_REL_UNITS = {
    "m": 60,
    "h": 3600,
    "d": 86_400,
    "w": 7 * 86_400,
    "mo": 30 * 86_400,
    "y": 365 * 86_400,
}
_REL_RE = re.compile(r"^(\d+)\s*(mo|m|h|d|w|y)$", re.IGNORECASE)


def parse_date(s: str | None) -> int | None:
    """Return unix milliseconds for `s`, or None if `s` is falsy.

    Raises ValueError if `s` is non-empty but unparseable.
    """
    if not s:
        return None
    s = s.strip().lower()

    if s == "today":
        d = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(d.timestamp() * 1000)
    if s == "yesterday":
        d = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        return int(d.timestamp() * 1000)

    m = _REL_RE.match(s)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        secs = amount * _REL_UNITS[unit]
        return int((time.time() - secs) * 1000)

    # ISO formats — try a few common shapes
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m"):
        try:
            d = datetime.strptime(s, fmt)
            return int(d.replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue

    raise ValueError(
        f"Could not parse date {s!r}. Try ISO (2026-04-19) or relative (7d, 2w, 3mo)."
    )
