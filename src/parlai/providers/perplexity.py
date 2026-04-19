"""Perplexity (www.perplexity.ai) provider — wraps internal /rest/thread endpoints."""

from __future__ import annotations

import base64
import json as _json
from typing import Iterator

import httpx

from parlai.auth import get_cookies
from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

DOMAIN = ".perplexity.ai"
BASE = "https://www.perplexity.ai"
REQUIRED_COOKIES = ["__Secure-next-auth.session-token"]


class PerplexityProvider:
    name = "perplexity"

    def __init__(self) -> None:
        self._cookies: dict[str, str] | None = None

    def _client(self) -> httpx.Client:
        cookies = self._cookies or get_cookies(self.name, DOMAIN, REQUIRED_COOKIES)
        self._cookies = cookies
        return httpx.Client(
            base_url=BASE,
            cookies=cookies,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 parlai",
                "Accept": "application/json",
                "x-app-apiclient": "default",
                "x-app-apiversion": "2.18",
                "Origin": BASE,
                "Referer": BASE + "/",
            },
            timeout=30.0,
        )

    def authed(self) -> bool:
        cookies = get_cookies(self.name, DOMAIN, REQUIRED_COOKIES)
        return any(
            k.startswith("__Secure-next-auth") or k == "next-auth.session-token"
            for k in cookies
        )

    def _post(self, c: httpx.Client, path: str, body: dict) -> dict:
        r = c.post(
            path + "?version=2.18&source=default",
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        return _maybe_b64_decode(data)

    def list(self, limit: int = 100, search_term: str = "") -> Iterator[dict]:
        page = min(limit, 20)
        offset = 0
        yielded = 0
        with self._client() as c:
            while yielded < limit:
                body = {
                    "limit": page,
                    "ascending": False,
                    "offset": offset,
                    "search_term": search_term,
                    "exclude_asi": False,
                }
                data = self._post(c, "/rest/thread/list_ask_threads", body)
                threads = data if isinstance(data, list) else (
                    data.get("threads") or data.get("entries") or []
                )
                if not threads:
                    return
                for t in threads:
                    cid = (
                        t.get("backend_uuid")
                        or t.get("uuid")
                        or t.get("frontend_uuid")
                    )
                    slug = t.get("slug") or t.get("thread_url_slug") or cid
                    yield {
                        "id": slug,
                        "title": t.get("title") or t.get("thread_title") or t.get("query_str"),
                        "updated_at": _iso_ms(
                            t.get("last_query_datetime")
                            or t.get("updated_datetime")
                        ),
                    }
                    yielded += 1
                    if yielded >= limit:
                        return
                if len(threads) < page:
                    return
                offset += len(threads)

    def get(self, conv_id: str) -> Conversation:
        with self._client() as c:
            r = c.get(f"/rest/thread/{conv_id}")
            r.raise_for_status()
            try:
                data = r.json()
            except _json.JSONDecodeError:
                # Body is plain base64 string
                data = _json.loads(base64.b64decode(r.text).decode("utf-8"))
        data = _maybe_b64_decode(data)

        title = None
        msgs: list[Message] = []
        idx = 0
        first_ts = None
        last_ts = None
        for entry in data.get("entries", []):
            title = entry.get("thread_title") or title
            ts = _iso_ms(entry.get("updated_datetime"))
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            q = entry.get("query_str")
            if q:
                msgs.append(Message(idx=idx, role="user", text=q, created_at=ts))
                idx += 1
            answer = _extract_answer(entry)
            if answer:
                msgs.append(Message(idx=idx, role="assistant", text=answer, created_at=ts))
                idx += 1

        return Conversation(
            provider=self.name,
            id=conv_id,
            title=title,
            url=self.url_for(conv_id),
            created_at=first_ts,
            updated_at=last_ts,
            messages=msgs,
            metadata={
                "related_queries": (data.get("entries") or [{}])[0].get("related_queries", [])
            },
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        results = list(self.list(limit=limit, search_term=query))
        return [
            SearchHit(
                provider=self.name,
                id=r["id"],
                title=r["title"],
                snippet=(r.get("title") or "")[:280],
                url=self.url_for(r["id"]),
            )
            for r in results
        ]

    def url_for(self, conv_id: str) -> str | None:
        if not conv_id:
            return None
        return f"https://www.perplexity.ai/search/{conv_id}"


def _iso_ms(s) -> int | None:
    if not isinstance(s, str):
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _maybe_b64_decode(value):
    """Perplexity sometimes returns the JSON payload base64-encoded (single-thread fetches)."""
    if isinstance(value, str):
        try:
            decoded = base64.b64decode(value).decode("utf-8")
            return _json.loads(decoded)
        except Exception:
            try:
                return _json.loads(value)
            except Exception:
                return {"raw": value}
    return value


def _extract_answer(entry: dict) -> str:
    """Pull the assistant's text out of one of several possible fields."""
    for key in ("display_answer", "answer", "text"):
        v = entry.get(key)
        if isinstance(v, str) and v.strip():
            return v
    # Sometimes the answer is in a nested 'web_results' structure or 'blocks'
    blocks = entry.get("blocks") or entry.get("web_results")
    if isinstance(blocks, list):
        parts: list[str] = []
        for b in blocks:
            if isinstance(b, dict):
                for k in ("text", "content", "summary"):
                    if isinstance(b.get(k), str):
                        parts.append(b[k])
        if parts:
            return "\n\n".join(parts)
    return ""
