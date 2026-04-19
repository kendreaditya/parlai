"""ChatGPT (chatgpt.com) provider — wraps internal /backend-api endpoints."""

from __future__ import annotations

import json as _json
from typing import Iterator

import httpx

from parlai.auth import get_cookies
from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

DOMAIN = "chatgpt.com"
BASE = "https://chatgpt.com"
# ChatGPT may split the session JWT across .0/.1 chunks; either single or split is valid.
REQUIRED_COOKIES = [
    "__Secure-next-auth.session-token",
    "__Secure-next-auth.session-token.0",
]


class ChatGPTProvider:
    name = "chatgpt"

    def __init__(self) -> None:
        self._cookies: dict[str, str] | None = None
        self._token: str | None = None

    def _client(self, with_bearer: bool = True) -> httpx.Client:
        cookies = self._cookies or _all_chatgpt_cookies()
        self._cookies = cookies
        # Matching real Chrome UA + headers — Cloudflare blocks anything that smells like a bot.
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": BASE,
            "Referer": BASE + "/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        if with_bearer and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return httpx.Client(
            base_url=BASE, cookies=cookies, headers=headers, timeout=30.0
        )

    def authed(self) -> bool:
        cookies = _all_chatgpt_cookies()
        return any(k in cookies for k in REQUIRED_COOKIES)

    def _ensure_token(self) -> None:
        if self._token:
            return
        with self._client(with_bearer=False) as c:
            r = c.get("/api/auth/session")
            r.raise_for_status()
            data = r.json()
        self._token = data.get("accessToken")
        if not self._token:
            raise RuntimeError("ChatGPT /api/auth/session returned no accessToken")

    def list(self, limit: int = 100) -> Iterator[dict]:
        self._ensure_token()
        page = min(limit, 28)
        offset = 0
        yielded = 0
        with self._client() as c:
            while yielded < limit:
                r = c.get(
                    "/backend-api/conversations",
                    params={
                        "offset": offset,
                        "limit": page,
                        "order": "updated",
                        "is_archived": "false",
                        "is_starred": "false",
                    },
                )
                r.raise_for_status()
                data = r.json()
                items = data.get("items", [])
                if not items:
                    return
                for it in items:
                    yield {
                        "id": it.get("id"),
                        "title": it.get("title"),
                        "updated_at": _ts_to_ms(it.get("update_time")),
                    }
                    yielded += 1
                    if yielded >= limit:
                        return
                if len(items) < page:
                    return
                offset += len(items)

    def get(self, conv_id: str) -> Conversation:
        self._ensure_token()
        with self._client() as c:
            r = c.get(f"/backend-api/conversation/{conv_id}")
            r.raise_for_status()
            data = r.json()

        msgs = _walk_mapping(data)
        return Conversation(
            provider=self.name,
            id=conv_id,
            title=data.get("title"),
            url=self.url_for(conv_id),
            created_at=_ts_to_ms(data.get("create_time")),
            updated_at=_ts_to_ms(data.get("update_time")),
            messages=msgs,
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        self._ensure_token()
        with self._client() as c:
            r = c.get(
                "/backend-api/conversations/search",
                params={"query": query, "cursor": ""},
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, str):
                data = _json.loads(data)
        hits: list[SearchHit] = []
        for it in (data.get("items") or [])[:limit]:
            cid = it.get("conversation_id") or it.get("id")
            hits.append(
                SearchHit(
                    provider=self.name,
                    id=cid,
                    title=it.get("title"),
                    snippet=(it.get("snippet") or it.get("summary") or "")[:280],
                    url=self.url_for(cid),
                )
            )
        return hits

    def url_for(self, conv_id: str) -> str | None:
        if not conv_id:
            return None
        return f"https://chatgpt.com/c/{conv_id}"


def _all_chatgpt_cookies() -> dict[str, str]:
    """Return ALL cookies for chatgpt.com. We need the whole jar (CF challenge cookies, _puid, etc.)
    because /backend-api requests are gated on Cloudflare's __cf_bm + the split session-token chunks."""
    try:
        import browser_cookie3
        jar = browser_cookie3.chrome(domain_name=DOMAIN)
        cookies = {c.name: c.value for c in jar}
    except Exception:
        cookies = {}
    # Merge in any manually-stored cookies as override
    from parlai.auth import manual_get
    cookies.update(manual_get("chatgpt"))
    return cookies


def _ts_to_ms(t) -> int | None:
    if t is None:
        return None
    try:
        return int(float(t) * 1000)
    except (TypeError, ValueError):
        return None


def _walk_mapping(data: dict) -> list[Message]:
    """ChatGPT stores messages in a parent/children tree. Walk from current_node up to root, then reverse."""
    mapping = data.get("mapping") or {}
    cur = data.get("current_node")
    chain: list[dict] = []
    seen: set[str] = set()
    while cur and cur not in seen:
        seen.add(cur)
        node = mapping.get(cur) or {}
        msg = node.get("message")
        if msg:
            chain.append(msg)
        cur = node.get("parent")
    chain.reverse()
    out: list[Message] = []
    idx = 0
    for msg in chain:
        author = (msg.get("author") or {}).get("role") or "unknown"
        text = _msg_text(msg)
        if not text.strip():
            continue
        out.append(
            Message(
                idx=idx,
                role=author,
                text=text,
                created_at=_ts_to_ms(msg.get("create_time")),
            )
        )
        idx += 1
    return out


def _msg_text(msg: dict) -> str:
    content = msg.get("content") or {}
    ctype = content.get("content_type")
    parts = content.get("parts") or []
    if ctype == "text":
        return "\n".join(p for p in parts if isinstance(p, str))
    if ctype == "code":
        return f"```\n{content.get('text', '')}\n```"
    if ctype == "multimodal_text":
        out: list[str] = []
        for p in parts:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                t = p.get("content_type")
                if t == "image_asset_pointer":
                    out.append(f"[image: {p.get('asset_pointer','?')}]")
                elif t == "audio_asset_pointer":
                    out.append("[audio]")
                else:
                    out.append(f"[{t}]")
        return "\n".join(out)
    if ctype == "model_editable_context":
        return ""
    if ctype == "user_editable_context":
        return content.get("user_profile") or ""
    return _json.dumps(content)[:500]
