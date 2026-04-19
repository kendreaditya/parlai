"""Gemini provider — wraps HanaokaYuzu/Gemini-API (gemini-webapi)."""

from __future__ import annotations

import asyncio
from typing import Iterator

from parlai.auth import get_cookies
from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

DOMAIN = ".google.com"
REQUIRED_COOKIES = ["__Secure-1PSID", "__Secure-1PSIDTS"]


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        self._cookies: dict[str, str] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client = None

    def _cookie_pair(self) -> tuple[str | None, str | None]:
        cookies = self._cookies or get_cookies(self.name, DOMAIN, REQUIRED_COOKIES)
        self._cookies = cookies
        return cookies.get("__Secure-1PSID"), cookies.get("__Secure-1PSIDTS")

    def authed(self) -> bool:
        psid, psidts = self._cookie_pair()
        return bool(psid and psidts)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        # Reuse one loop across all calls so gemini-webapi's auto-refresh task survives.
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    async def _ensure_client(self):
        if self._client is not None:
            return self._client
        from gemini_webapi import GeminiClient

        psid, psidts = self._cookie_pair()
        if not psid:
            raise RuntimeError("Missing __Secure-1PSID cookie for Gemini")
        client = GeminiClient(psid, psidts)
        # auto_refresh=False so we don't leak background tasks
        await client.init(verbose=False, auto_refresh=False)
        self._client = client
        return client

    def _run(self, coro):
        return self._ensure_loop().run_until_complete(coro)

    def list(self, limit: int = 100) -> Iterator[dict]:
        async def go():
            client = await self._ensure_client()
            chats = client.list_chats() or []
            return chats[:limit]

        chats = self._run(go())
        for ci in chats:
            yield {
                "id": ci.cid,
                "title": ci.title,
                "updated_at": _ts_to_ms(ci.timestamp),
            }

    def get(self, conv_id: str) -> Conversation:
        async def go():
            client = await self._ensure_client()
            # Retry once if the server says the chat is still streaming
            for _ in range(2):
                hist = await client.read_chat(conv_id, limit=200)
                if hist is not None:
                    return hist
                await asyncio.sleep(1.5)
            return None

        history = self._run(go())
        if history is None:
            raise RuntimeError(f"No Gemini chat for cid {conv_id}")
        msgs: list[Message] = []
        idx = 0
        for turn in history.turns or []:
            text = turn.text or ""
            if not text.strip():
                continue
            msgs.append(Message(idx=idx, role=turn.role or "assistant", text=text))
            idx += 1
        return Conversation(
            provider=self.name,
            id=conv_id,
            title=None,
            url=self.url_for(conv_id),
            created_at=None,
            updated_at=None,
            messages=msgs,
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        # Native search not exposed by the library yet; fall back to local FTS.
        return []

    def url_for(self, conv_id: str) -> str | None:
        if not conv_id:
            return None
        return f"https://gemini.google.com/app/{conv_id}"


def _ts_to_ms(ts) -> int | None:
    if ts is None:
        return None
    try:
        return int(ts.timestamp() * 1000)
    except AttributeError:
        try:
            return int(float(ts) * 1000)
        except (TypeError, ValueError):
            return None
