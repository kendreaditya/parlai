# Provider implementation notes

For each provider: where to find the auth state, what endpoints exist, what the response payload looks like, what's weird about it.

---

## chatgpt — `src/parlai/providers/chatgpt.py`

**Auth**: split JWT cookie at `chatgpt.com`. The session token was traditionally `__Secure-next-auth.session-token`, but ChatGPT now splits it across `.0` and `.1` suffixes when the JWT exceeds 4 KB. Both forms must be checked.

After cookies, an additional Bearer token must be fetched from `GET /api/auth/session` (returns `{accessToken: "..."}`). This token expires in ~1 hour — `_ensure_token()` handles it.

**Cloudflare gotcha**: the API rejects requests with non-Chrome User-Agent strings via Cloudflare's `__cf_bm` cookie. We send a real Chrome 147 UA + `Origin`/`Referer` headers + the Sec-Fetch-* trio. Without these, you get HTTP 403.

**List**: `GET /backend-api/conversations?offset=&limit=28&order=updated&is_archived=false&is_starred=false`. Response: `{items: [...], total, limit, offset}`. Page through `offset` until `len(items) < limit`.

**Get**: `GET /backend-api/conversation/{id}`. Returns `{title, create_time, update_time, mapping, current_node, ...}`. Messages are stored as a **tree** in `mapping` keyed by node id; walk from `current_node` up to the root via `parent` then reverse to chronological order. See `_walk_mapping()`.

**Search**: `GET /backend-api/conversations/search?query=&cursor=`. Returns `{items: [{title, snippet, conversation_id}, ...]}`. The response body is occasionally a JSON-encoded string (`'"{...}"'`); we double-parse if `isinstance(data, str)`.

---

## claude — `src/parlai/providers/claude.py`

**Auth**: single cookie `sessionKey` at `claude.ai`, prefix `sk-ant-sid01...`.

**Org discovery**: every endpoint is namespaced by org UUID. `_org_id()` calls `GET /api/organizations` (returns `[{uuid, name, ...}, ...]`) and caches the first one. For multi-org users, this picks the wrong one — would need explicit `--account` (deferred).

**List**: `GET /api/organizations/{org}/chat_conversations_v2?limit=30&offset=0&consistency=eventual`. Response: `{data: [...], has_more: bool}` (NOT a bare list — first version of our parser missed this and 0-results'd).

**Get**: `GET /api/organizations/{org}/chat_conversations/{conv_id}?tree=True&rendering_mode=messages&render_all_tools=true`. Returns `{name, chat_messages: [{sender, content: [...], created_at}, ...]}`. Each message's `content[]` is a list of typed blocks (text, thinking, tool_use, tool_result).

**Search**: `GET /api/organizations/{org}/conversation/search?query=&n=10`. **Two response quirks**:

1. The body is a JSON-encoded string (the response is `"{\"chunks\":[...]}"` with the outer quotes), so `r.json()` returns a `str` and we have to `json.loads()` again.
2. Each chunk has `doc_uuid` (an internal index id, **not** the conversation UUID) and `extras.conversation_uuid` (the real one). Hitting `chat_conversations/{doc_uuid}` returns 404 — always use `extras.conversation_uuid`.

The response is RAG-style: chunks include the matched substring with start/end character offsets. We pass the snippet through to the user.

---

## gemini — `src/parlai/providers/gemini.py` + `_gemini_internal.py`

See **[gemini-batchexecute.md](gemini-batchexecute.md)** for the protocol details. In short:

- Cookies: `__Secure-1PSID` + `__Secure-1PSIDTS` at `.google.com`.
- Init: `GET https://gemini.google.com/app`, regex-scrape four JS variables out of the HTML: `SNlM0e` (access token), `cfb2h` (build label, rotates monthly), `FdrFJe` (session id), `TuX5cc` (language).
- One generic `POST /_/BardChatUi/data/batchexecute` endpoint for everything; the `rpcids` query param picks which RPC.
- RPCs we use: `MaZiqc` (list, two calls — one for recent, one for archived), `hNvQHb` (read single chat), `unqWSc` (search — **not exposed by the upstream `gemini-webapi` library**).
- Response uses Google's length-prefixed framing protocol with **UTF-16 code unit counts**, not byte or char counts.

We deliberately don't depend on `gemini-webapi` because it pulls in `orjson` (Rust build) which Homebrew rejects. The internal client is ~250 LOC of pure stdlib + httpx.

---

## aistudio — `src/parlai/providers/aistudio.py`

**Auth**: piggybacks on the `gog` CLI for Drive OAuth. We never touch tokens directly.

**Storage**: AI Studio prompts are stored in Google Drive as files with mime type `application/vnd.google-makersuite.prompt`. There is no "AI Studio backend" to hit — the entire feature is a Drive layer with a custom mime type.

**List**: `gog drive search "mimeType='application/vnd.google-makersuite.prompt'" --raw-query --json --results-only --max N`. Pagination: a separate call without `--results-only` to retrieve `nextPageToken`.

**Get**: `gog drive download {fileId} --out /tmp/file.json`, then parse the prompt JSON (see `_parse_chunks()`). Format: `{runSettings: {model, ...}, chunkedPrompt: {chunks: [{role, text|parts, isThought, ...}, ...]}}`.

**Search**: Drive's full-text search via the same `mimeType='...' and fullText contains 'query'` query string. No native AI-Studio search exists; this is good enough.

The chunk parser was adapted from `~/.claude/skills/gemini-convo/scripts/parse.py` (a script-only skill, can't be imported). We copied the parsing logic into `_parse_chunks()`.

---

## perplexity — `src/parlai/providers/perplexity.py`

**Auth**: session cookies at `.perplexity.ai`. Either `__Secure-next-auth.session-token` (single) or split `.0`/`.1`. The API accepts whichever is present.

**Cloudflare**: anti-bot is more aggressive here than ChatGPT. Required headers: `x-app-apiclient: default`, `x-app-apiversion: 2.18`, real Chrome UA, `Origin`/`Referer`. Without them you'll see Turnstile challenges in HTML responses.

**List**: `POST /rest/thread/list_ask_threads?version=2.18&source=default` with body `{"limit":20,"ascending":false,"offset":0,"search_term":"","exclude_asi":false}`. Response is a **bare list** of thread dicts (not envelope-wrapped).

**Get**: `GET /rest/thread/{slug-uuid}`. Response body is **base64-encoded JSON** (decoded inside `_maybe_b64_decode()`). After base64-decode, structure is `{status, entries: [...], background_entries, has_next_page, next_cursor, thread_metadata}`.

**Search**: same `list_ask_threads` endpoint with non-empty `search_term`. There is no separate `/search` endpoint.

Response IDs come in two flavors: `slug` (URL-friendly, used in user-facing URLs) and `backend_uuid`/`uuid` (machine ids). We use `slug` everywhere because the single-thread fetch endpoint accepts it.

---

## claude-code — `src/parlai/providers/claude_code.py`

**Auth**: none — reads files directly.

**Storage**: `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`. The encoded-cwd is the conversation's working directory with `/` replaced by `-` (e.g. `-Users-kendreaditya-Downloads`). We decode it back into the conversation's `metadata.cwd`.

**Format**: each line is a JSON event with `type`. Relevant types:
- `user`, `assistant` — message turns. `message.content` is either a string or a list of `{type, text}` blocks.
- `custom-title` — sets the conversation title (last one wins).
- `summary` — older format, may also set title.
- `queue-operation`, `last-prompt`, etc. — UI bookkeeping, skip.

**Title heuristic**: scan all `custom-title` events, use the last one. Falls back to `summary`. Falls back to None.

**No native search** — falls through to local FTS5.

---

## codex-cli + codex-desktop — `src/parlai/providers/codex.py`

**Auth**: none.

**Storage**: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Both CLI and Desktop write to this same path; the first event in each file is `session_meta` and its `payload.originator` field discriminates ("Codex CLI" vs "Codex Desktop").

**Provider split**: `_CodexBase` does the actual work; `CodexCLIProvider` and `CodexDesktopProvider` are thin subclasses with `originator_filter` set. This keeps the two filterable as separate surfaces in `parlai status` while sharing all parsing code.

**Format**: each line has `type` of:
- `session_meta` — first event, holds id/originator/cwd/timestamp.
- `event_msg` — engine status (task_started, etc.) — skip.
- `turn_context` — model context info — skip.
- `response_item` — actual content. Sub-types:
  - `message` (role: user/developer/assistant) — content blocks with text.
  - `reasoning` — encrypted thinking blob — skip text.
  - `function_call` / `function_call_output` — tool use; we summarize as `[function_call name] args`.
  - `local_shell_call` / `local_shell_call_output` — shell commands.
  - `custom_tool_call` (e.g. `apply_patch`) — capture name + input.

**Title heuristic**: first non-`<environment_context>` user message, truncated to 120 chars.

The "developer" role in Codex maps to "system" in our `Message.role` field for consistency with other providers.

---

## Common patterns

- **All HTTP responses with possible non-ASCII**: never use `r.json()` blindly when the API returns nested JSON-encoded strings. Always check `isinstance(data, str)` and `json.loads()` again.
- **Cookie domains matter**: `.google.com` (with leading dot) vs `gemini.google.com` (specific). Chrome stores them differently; we use the broadest reasonable scope per provider.
- **Pagination**: every list endpoint has its own pagination model. Always read the response envelope to find `has_more`/`next_cursor`/`offset` before assuming.
- **Error swallowing**: providers should never raise on a single bad conversation during sync. The CLI catches per-convo and prints `[red]get {id} failed:[/red]` while continuing.
