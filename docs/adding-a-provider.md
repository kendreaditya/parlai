# Adding a new provider

Concrete walk-through of adding the 9th provider. Pretend we're adding **Pi** (`pi.ai`).

## Step 0: confirm the provider has a queryable conversation history

Some products (Meta.ai, OpenRouter) don't expose conversation history at all — they're stateless. Skip those. Verify by logging in via your browser and checking that you can see / search past chats in the UI.

## Step 1: capture a HAR

1. Log in via Chrome.
2. Open DevTools → Network tab → check **Preserve log**.
3. Reload the page, then perform every action you want parlai to support: list conversations, click into one, run a search.
4. Right-click any request → **Save all as HAR with content**.
5. Save to `~/Downloads/<provider>.har`.

## Step 2: extract endpoints

```bash
jq -r '.log.entries[]
  | select(.request.url | test("pi.ai"))
  | select(.request.url | test("\\.(png|jpg|css|woff|js|svg)$") | not)
  | "\(.request.method) \(.request.url)"
' ~/Downloads/pi.har | sed -E 's/\?.*$//' | sort -u
```

Look for:

- A `list`-shaped endpoint (`/conversations`, `/threads`, `/history`).
- A `get`-shaped endpoint that takes a conversation id.
- A `search` endpoint, OR a `?q=` query param on the list endpoint.

## Step 3: figure out the auth model

```bash
jq -r '.log.entries[]
  | select(.request.url | startswith("https://pi.ai"))
  | .request.headers[]
  | select(.name | test("auth|cookie|token|bearer|x-"; "i"))
  | "\(.name): \(.value | .[0:80])"
' ~/Downloads/pi.har | sort -u | head -20
```

Most products use one of:

- A session cookie (typical: `__Secure-next-auth.session-token` for Next.js apps, `sessionid` for Django, `_session` for Rails).
- A bearer JWT in `Authorization` (might come from `/api/auth/session` like ChatGPT).
- A custom header (rare).

## Step 4: probe the endpoints from a Python REPL

```python
import browser_cookie3, httpx, json
jar = browser_cookie3.chrome(domain_name="pi.ai")
cookies = {c.name: c.value for c in jar}

with httpx.Client(cookies=cookies, headers={
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Origin": "https://pi.ai",
    "Referer": "https://pi.ai/",
}) as c:
    r = c.get("https://pi.ai/api/threads")
    print(r.status_code, r.text[:300])
```

If 200 with JSON, you're good. If 403 / Cloudflare challenge, add the Sec-Fetch headers (see `chatgpt.py` for a reference set).

## Step 5: write the provider

Create `src/parlai/providers/pi.py`. Copy `claude.py` as a starting template — it's the simplest "just REST" provider.

Required: implement the 5 `Provider` Protocol methods.

```python
from __future__ import annotations
from typing import Iterator
import httpx
from parlai.auth import get_cookies
from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

DOMAIN = "pi.ai"
BASE = "https://pi.ai"
REQUIRED_COOKIES = ["__Secure-session-token"]  # ← from your HAR analysis


class PiProvider:
    name = "pi"

    def __init__(self):
        self._cookies = None

    def _client(self):
        cookies = self._cookies or get_cookies(self.name, DOMAIN, REQUIRED_COOKIES)
        self._cookies = cookies
        return httpx.Client(base_url=BASE, cookies=cookies, headers={
            "User-Agent": "Mozilla/5.0 ... Chrome/147.0.0.0 ...",
        }, timeout=30.0)

    def authed(self) -> bool:
        return any(k in get_cookies(self.name, DOMAIN, REQUIRED_COOKIES) for k in REQUIRED_COOKIES)

    def list(self, limit: int = 100) -> Iterator[dict]:
        with self._client() as c:
            r = c.get("/api/threads", params={"limit": limit})
            r.raise_for_status()
            for t in r.json().get("threads", []):
                yield {
                    "id": t["id"],
                    "title": t.get("title"),
                    "updated_at": t.get("updated_at"),
                }

    def get(self, conv_id: str) -> Conversation:
        with self._client() as c:
            r = c.get(f"/api/threads/{conv_id}")
            r.raise_for_status()
            data = r.json()
        msgs = [
            Message(idx=i, role=m["role"], text=m["text"])
            for i, m in enumerate(data["messages"])
        ]
        return Conversation(
            provider=self.name,
            id=conv_id,
            title=data.get("title"),
            url=self.url_for(conv_id),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            messages=msgs,
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        # If pi.ai has no native search, return [] — CLI falls back to local FTS.
        return []

    def url_for(self, conv_id: str) -> str | None:
        return f"https://pi.ai/threads/{conv_id}" if conv_id else None
```

## Step 6: register it

Add to `src/parlai/providers/__init__.py`:

```python
from parlai.providers.pi import PiProvider

REGISTRY: dict[str, type[Provider]] = {
    ...
    "pi": PiProvider,
}
```

## Step 7: test live

```bash
cd ~/workspace/parlai
uv run parlai status               # ← should show pi as ✓ if cookies present
uv run parlai list pi --remote --limit 3
uv run parlai get pi <id>
uv run parlai sync pi --limit 5
uv run parlai search "..." --local
```

## Step 8: write a parser test

Add a fixture-based test under `tests/test_parsers.py`. Capture one real response payload (sanitized of PII), commit it under `tests/fixtures/`, and write a unit test that asserts the parser produces the expected `Conversation`/`Message` shape. This protects against regressions when you tweak the parser later.

## Step 9: update the README + provider table in `docs/providers.md`

Add a row to the README table and a section under `docs/providers.md` describing the auth, endpoints, and any quirks you discovered.

## Common gotchas to watch for

- **Cloudflare anti-bot** — if you get 403, add `Origin`, `Referer`, and `Sec-Fetch-{Dest,Mode,Site}` headers matching what the live browser sends.
- **JSON-encoded JSON in responses** — if `r.json()` returns a string, `json.loads()` it again.
- **Base64-encoded response bodies** — Perplexity does this for single-thread fetches. `try/except` the decode.
- **Split session cookies** — `.0` and `.1` suffixes when JWTs exceed 4 KB. Pull the whole cookie jar, not just the named ones.
- **Tree-shaped conversations** — ChatGPT stores messages as a parent/children graph keyed by node id. Walk from `current_node` up via `parent` then reverse.
- **Pagination envelopes vary** — some return `{data, has_more}`, some `{items, total}`, some bare lists. Always check the response shape before assuming.
- **Watermark vs full re-sync** — incremental sync uses `updated_at > watermark`; the user can override with `--full` to re-fetch everything.

## When to give up

If after 2 hours you can't get a clean response shape from the API, the product is probably not worth integrating. Document the attempt in the README "considered but not supported" section so future contributors don't repeat the work.
