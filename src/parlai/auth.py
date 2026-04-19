"""Cookie acquisition: try Chrome first, fall back to manual paste stored on disk."""

from __future__ import annotations

import json
from typing import Iterable

from parlai.paths import CREDS_PATH, ensure

try:
    import browser_cookie3  # type: ignore
except ImportError:  # pragma: no cover
    browser_cookie3 = None  # type: ignore


def _load_disk() -> dict:
    if not CREDS_PATH.exists():
        return {}
    try:
        return json.loads(CREDS_PATH.read_text())
    except json.JSONDecodeError:
        return {}


def _save_disk(data: dict) -> None:
    ensure()
    CREDS_PATH.write_text(json.dumps(data, indent=2))
    CREDS_PATH.chmod(0o600)


def manual_set(provider: str, cookies: dict[str, str]) -> None:
    data = _load_disk()
    data[provider] = cookies
    _save_disk(data)


def manual_get(provider: str) -> dict[str, str]:
    return _load_disk().get(provider, {})


def chrome_cookies(domain: str, names: Iterable[str] | None = None) -> dict[str, str]:
    """Pull cookies for a domain from the local Chrome cookie store.

    Works on macOS, Linux, and Windows (browser_cookie3 handles platform differences).
    Returns {} if Chrome isn't installed or cookies can't be decrypted.
    """
    from parlai import log

    if browser_cookie3 is None:
        log.warn("browser_cookie3 not installed; manual login only")
        return {}
    try:
        jar = browser_cookie3.chrome(domain_name=domain)
    except Exception as e:
        log.warn(f"chrome cookies for {domain} failed: {e}")
        return {}
    out: dict[str, str] = {}
    name_set = set(names) if names else None
    for c in jar:
        if name_set is None or c.name in name_set:
            out[c.name] = c.value
    return out


def get_cookies(
    provider: str, domain: str, required: list[str]
) -> dict[str, str]:
    """Hybrid: try Chrome, fall back to manually-stored cookies. Returns dict (possibly empty)."""
    cookies = chrome_cookies(domain, required)
    if all(k in cookies for k in required):
        return cookies
    manual = manual_get(provider)
    merged = {**cookies, **manual}
    return merged
