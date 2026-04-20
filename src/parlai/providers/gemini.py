"""Gemini (gemini.google.com) provider — uses our own internal batchexecute client."""

from __future__ import annotations

from typing import Iterator

from parlai.auth import get_cookies
from parlai.models import Conversation, Message
from parlai.providers._gemini_internal import GeminiClient
from parlai.providers.base import SearchHit

DOMAIN = ".google.com"
REQUIRED_COOKIES = ["__Secure-1PSID", "__Secure-1PSIDTS"]


class GeminiProvider:
    name = "gemini"

    def __init__(self) -> None:
        self._client: GeminiClient | None = None
        self._cookies: dict[str, str] | None = None

    def _cookie_pair(self) -> tuple[str | None, str | None]:
        cookies = self._cookies or get_cookies(self.name, DOMAIN, REQUIRED_COOKIES)
        self._cookies = cookies
        return cookies.get("__Secure-1PSID"), cookies.get("__Secure-1PSIDTS")

    def authed(self) -> bool:
        psid, psidts = self._cookie_pair()
        return bool(psid and psidts)

    def _ensure(self) -> GeminiClient:
        if self._client is not None:
            return self._client
        psid, psidts = self._cookie_pair()
        if not psid:
            raise RuntimeError("Missing __Secure-1PSID cookie for Gemini")
        c = GeminiClient(psid, psidts)
        c.init()
        self._client = c
        return c

    def list(self, limit: int = 100) -> Iterator[dict]:
        client = self._ensure()
        for ci in client.list_chats(recent=min(limit, 50))[:limit]:
            yield {
                "id": ci.cid,
                "title": ci.title,
                "updated_at": int(ci.timestamp * 1000) if ci.timestamp else None,
            }

    def get(self, conv_id: str) -> Conversation:
        client = self._ensure()
        history = client.read_chat(conv_id, limit=200)
        if history is None:
            raise RuntimeError(f"No Gemini chat for cid {conv_id}")
        msgs: list[Message] = []
        # Upstream returns turns newest-first; reverse to chronological
        turns = list(reversed(history.turns))
        for i, t in enumerate(turns):
            text = t.text or ""
            if not text.strip():
                continue
            role = "assistant" if t.role == "model" else t.role
            msgs.append(Message(idx=i, role=role, text=text))
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
        client = self._ensure()
        results = client.search(query)
        hits: list[SearchHit] = []
        for r in results[:limit]:
            hits.append(
                SearchHit(
                    provider=self.name,
                    id=r.cid,
                    title=r.title,
                    snippet=(r.snippet or "")[:280],
                    url=self.url_for(r.cid),
                )
            )
        return hits

    def url_for(self, conv_id: str) -> str | None:
        if not conv_id:
            return None
        return f"https://gemini.google.com/app/{conv_id}"
