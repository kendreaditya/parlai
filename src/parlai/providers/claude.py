"""claude.ai web app provider — uses internal REST endpoints."""

from __future__ import annotations

from typing import Iterator

import httpx

from parlai.auth import get_cookies
from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

DOMAIN = "claude.ai"
BASE = "https://claude.ai"
REQUIRED_COOKIES = ["sessionKey"]


class ClaudeProvider:
    name = "claude"

    def __init__(self) -> None:
        self._cookies: dict[str, str] | None = None
        self._org: str | None = None

    def _client(self) -> httpx.Client:
        cookies = self._cookies or get_cookies(self.name, DOMAIN, REQUIRED_COOKIES)
        self._cookies = cookies
        return httpx.Client(
            base_url=BASE,
            cookies=cookies,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 parlai",
                "Accept": "application/json",
                "Anthropic-Client-Platform": "web_claude_ai",
            },
            timeout=30.0,
        )

    def authed(self) -> bool:
        cookies = get_cookies(self.name, DOMAIN, REQUIRED_COOKIES)
        return "sessionKey" in cookies

    def _org_id(self) -> str:
        if self._org:
            return self._org
        with self._client() as c:
            r = c.get("/api/organizations")
            r.raise_for_status()
            orgs = r.json()
        if not orgs:
            raise RuntimeError("No Claude organizations available for this session")
        # Prefer the first non-archived personal org
        self._org = orgs[0]["uuid"]
        return self._org

    def list(self, limit: int = 100) -> Iterator[dict]:
        org = self._org_id()
        offset = 0
        page = min(limit, 30)
        yielded = 0
        with self._client() as c:
            while yielded < limit:
                r = c.get(
                    f"/api/organizations/{org}/chat_conversations_v2",
                    params={"limit": page, "offset": offset, "consistency": "eventual"},
                )
                r.raise_for_status()
                payload = r.json()
                items = payload.get("data") if isinstance(payload, dict) else payload
                if not items:
                    return
                for it in items:
                    yield {
                        "id": it.get("uuid"),
                        "title": it.get("name"),
                        "updated_at": _iso_ms(it.get("updated_at")),
                    }
                    yielded += 1
                    if yielded >= limit:
                        return
                if isinstance(payload, dict) and not payload.get("has_more"):
                    return
                if len(items) < page:
                    return
                offset += len(items)

    def get(self, conv_id: str) -> Conversation:
        org = self._org_id()
        with self._client() as c:
            r = c.get(
                f"/api/organizations/{org}/chat_conversations/{conv_id}",
                params={"tree": "True", "rendering_mode": "messages", "render_all_tools": "true"},
            )
            r.raise_for_status()
            data = r.json()
        msgs: list[Message] = []
        for i, m in enumerate(data.get("chat_messages", [])):
            text = _claude_message_text(m)
            msgs.append(
                Message(
                    idx=i,
                    role=m.get("sender") or "assistant",
                    text=text,
                    created_at=_iso_ms(m.get("created_at")),
                )
            )
        return Conversation(
            provider=self.name,
            id=conv_id,
            title=data.get("name"),
            url=self.url_for(conv_id),
            created_at=_iso_ms(data.get("created_at")),
            updated_at=_iso_ms(data.get("updated_at")),
            messages=msgs,
            metadata={"org": org},
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        import json as _json

        org = self._org_id()
        with self._client() as c:
            r = c.get(
                f"/api/organizations/{org}/conversation/search",
                params={"query": query, "n": limit},
            )
            r.raise_for_status()
            data = r.json()
            # Response body is sometimes a JSON-encoded string ("{...}") instead of a dict
            if isinstance(data, str):
                data = _json.loads(data)
        hits: list[SearchHit] = []
        for chunk in data.get("chunks", []):
            extras = chunk.get("extras") or {}
            # The real conversation UUID is in extras; doc_uuid is an internal index id.
            conv_uuid = extras.get("conversation_uuid") or chunk.get("doc_uuid")
            title = extras.get("conversation_title") or chunk.get("name")
            hits.append(
                SearchHit(
                    provider=self.name,
                    id=conv_uuid,
                    title=title,
                    snippet=(chunk.get("text") or "")[:280],
                    url=self.url_for(conv_uuid),
                )
            )
        return hits

    def url_for(self, conv_id: str) -> str | None:
        if not conv_id:
            return None
        return f"https://claude.ai/chat/{conv_id}"


def _iso_ms(s) -> int | None:
    if not isinstance(s, str):
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _claude_message_text(msg: dict) -> str:
    parts: list[str] = []
    for block in msg.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "text":
            parts.append(block.get("text", ""))
        elif t == "thinking":
            continue
        elif t == "tool_use":
            parts.append(f"[tool: {block.get('name','?')}]")
        elif t == "tool_result":
            content = block.get("content", "")
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
            parts.append(f"[tool_result] {content}")
    if not parts:
        # fallback to plaintext field if present
        text = msg.get("text")
        if text:
            parts.append(text)
    return "\n".join(parts)
