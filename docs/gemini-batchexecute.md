# Gemini's batchexecute protocol

This is a deep dive into how `src/parlai/providers/_gemini_internal.py` works. Read this if you're touching anything that talks to Google's web apps (Gemini, AI Studio, Bard).

## What `batchexecute` is

Google's web apps use a single generic RPC endpoint for almost every internal call:

```
POST https://gemini.google.com/_/BardChatUi/data/batchexecute
```

Different operations are dispatched via the `rpcids` query parameter (e.g. `MaZiqc` for "list chats"). The body is form-encoded, the response is a homebrew length-prefixed multi-frame format, and the length is in **UTF-16 code units**, not bytes or characters.

## Authentication

Two pieces:

1. **Cookies**: `__Secure-1PSID` and `__Secure-1PSIDTS` from `.google.com`. The first is a stable identity; the second is a rotating short-lived token that Google refreshes ~hourly. Both are required.
2. **Per-session token**: `SNlM0e`, scraped out of the HTML returned by `GET https://gemini.google.com/app`. This is the `at` form-encoded field on every batchexecute request. Without it, requests return 400.

Two more values get scraped from `/app` for completeness:

- `cfb2h` → the **build label** (`bl=` query param, e.g. `boq_assistant-bard-web-server_20260415.04_p1`). Rotates monthly. Optional but recommended.
- `FdrFJe` → the **session id** (`f.sid=` query param). Optional.
- `TuX5cc` → user's UI language (`hl=` query param).

The relevant scraping code:

```python
text = self._http.get("https://gemini.google.com/app").text
self._access_token = re.search(r'"SNlM0e":\s*"(.*?)"', text).group(1)
self._build_label  = re.search(r'"cfb2h":\s*"(.*?)"', text).group(1)
self._session_id   = re.search(r'"FdrFJe":\s*"(.*?)"', text).group(1)
```

## Request format

```
POST /_/BardChatUi/data/batchexecute?rpcids=MaZiqc&source-path=/app&f.sid=...&bl=...&hl=en&_reqid=100000&rt=c

Headers:
  Content-Type: application/x-www-form-urlencoded;charset=utf-8
  X-Same-Domain: 1
  Origin: https://gemini.google.com
  Referer: https://gemini.google.com/
  x-goog-ext-525001261-jspb: [1,null,null,null,null,null,null,null,[4]]
  x-goog-ext-73010989-jspb: [0]
  Cookie: __Secure-1PSID=...; __Secure-1PSIDTS=...

Body (form-encoded):
  f.req=[[["MaZiqc","[30,null,[1,null,1]]",null,"generic"]]]
  at=<SNlM0e value>
```

The `f.req` envelope is always:

```json
[[
  [
    "<rpcid>",
    "<payload-as-JSON-string>",
    null,
    "generic"
  ],
  ...  // can batch multiple RPCs in one call (but we always send one)
]]
```

Note that `<payload-as-JSON-string>` is a JSON-encoded string *inside* the outer JSON array. So you double-encode.

The two `x-goog-ext-*-jspb` headers are constants from the upstream `gemini-webapi` library (which got them from the live web client). They appear to encode some client capability flags.

## Response format

```
)]}'\n
\n
198656\n
[["wrb.fr","MaZiqc","<json-encoded payload string>",null,null,null,"generic"]]\n
57\n
[["di",549],["af.httprm",549,"...",17]]\n
28\n
[["e",4,null,null,208734]]\n
```

Three layers:

1. **The XSSI guard `)]}'`** at the very start. Strip it.
2. **Length-prefixed frames** — each `<length>\n<json>\n` block. The length is in UTF-16 code units (more on that below).
3. **Inside each frame, a JSON list of envelope items**. Items starting with `"wrb.fr"` are RPC results. Items like `"di"`, `"af.httprm"`, `"e"` are protocol-level metadata — skip them.

Each `wrb.fr` envelope is `["wrb.fr", "<rpcid>", "<inner-json-string>", null, null, null, "generic"]`. The inner JSON string is double-encoded — `json.loads()` it once to get the actual payload.

## The UTF-16 trap

Google's frame length is computed as JavaScript's `String.length` — which counts **UTF-16 code units**. A character with code point ≥ 0x10000 (emoji, some rare CJK, math symbols) takes **2** code units but only **1** Python character.

Naïve byte counting under-consumes by N bytes when the response has N non-BMP characters in it. Naïve Python char counting also under-consumes (because `len("🎉")` is 1 in Python but 2 in UTF-16). Either way, the chunk slice runs short, the JSON parser hits the next frame's leading bytes, and `json.loads` fails with `Extra data: line 2 column 1 (char N)`.

Fix: walk the string char-by-char, accumulating UTF-16 units, and stop when units consumed ≥ declared length:

```python
def _utf16_advance(s: str, start: int, units: int) -> int:
    i = start
    consumed = 0
    n = len(s)
    while i < n and consumed < units:
        cp = ord(s[i])
        consumed += 2 if cp >= 0x10000 else 1
        i += 1
    return i
```

## The off-by-one in `body_start`

The frame header is `<digits>\n`. The length value **includes** the trailing `\n`. So if you advance the cursor to *after* the newline before counting, you'll under-consume by exactly one UTF-16 unit and end up with trailing junk.

Wrong:
```python
m = re.match(r"(\d+)\n", content, pos=pos)
body_start = m.end()                                # ← past the \n
body_end = _utf16_advance(content, body_start, length)
```

Right:
```python
m = re.match(r"(\d+)\n", content, pos=pos)
body_start = m.start() + len(m.group(1))           # ← right at the \n
body_end = _utf16_advance(content, body_start, length)
chunk = content[body_start:body_end].strip()       # ← .strip() removes the \n
```

The `.strip()` then removes the leading `\n` cleanly, AND any trailing `\n` from the next frame's leading whitespace. Both ends balance.

## RPCs we use

| Constant | rpcid | Purpose | Payload |
|----------|-------|---------|---------|
| `RPC_LIST_CHATS` | `MaZiqc` | list conversations | `[recent_count, null, [1, null, 1]]` for recent; `[..., [0, null, 1]]` for archived. Make both calls and dedupe. |
| `RPC_READ_CHAT` | `hNvQHb` | fetch single conversation | `[cid, limit, null, 1, [1], [4], null, 1]` |
| `RPC_SEARCH` | `unqWSc` | search across chats | `[query]` — extremely simple. Use `source-path=/search` instead of `/app`. |

The upstream `gemini-webapi` library does not expose `unqWSc` — we discovered it by diffing a HAR captured while typing in Gemini's search box.

## Response shapes

### `MaZiqc` (list)

```
inner[2] = [
  [cid, title, is_pinned, ?, ?, [seconds, nanos], ...],
  ...
]
```

The timestamp is split across two integers (seconds + nanoseconds). Combine: `seconds + nanos / 1e9`.

### `hNvQHb` (read)

```
inner[0] = [
  conv_turn,
  conv_turn,
  ...
]

conv_turn[2][0][0] = user message text
conv_turn[3][0]    = list of model candidates
conv_turn[3][0][N][1][0] = model response text (pull this out per candidate)
```

Order is **newest-first** in the response — we reverse to chronological in the public provider.

### `unqWSc` (search)

```
inner[0] = [
  result,
  result,
  ...
]

result[0]    = [cid, title]
result[2]    = [[snippet_type_int, "matched snippet text"], ...]
```

We pull the first snippet for the parlai `SearchHit` snippet field.

## Why we don't depend on `gemini-webapi`

The upstream library is excellent but has dependencies that break Homebrew installation:

- `orjson` — needs `maturin` (Rust toolchain) for source builds.
- `curl-cffi` — needs C build toolchain.
- `loguru`, `pydantic`, `pydantic-core`, `typing-inspection` — all transitive.

Our `_gemini_internal.py` re-implements just what we need (3 RPCs, no streaming generation, no images/videos/research) in ~250 LOC of pure stdlib + httpx. The framing parser is adapted from `gemini-webapi`'s `parsing.py` (BSD-licensed) — credit upstream.

Trade-off: when Google rotates the wire protocol, we have to fix it ourselves instead of upgrading a library version. So far this has been once a year, and the change is usually a renamed RPC.

## Debugging tips

- **Search returns 0 results** → check that you're using `source-path=/search` (not `/app`) and the payload is `[query]` (not `[[query]]`).
- **`Extra data` JSON error** → frame parser bug. Check the UTF-16 counting and the off-by-one in `body_start`.
- **`SNlM0e` regex returns None** → cookies are expired. Refresh by logging into gemini.google.com in Chrome.
- **HTTP 400** → the `at=` field is wrong (cookie/token mismatch). Re-init.
- **HTTP 401/302 redirect to login** → `__Secure-1PSIDTS` expired. Re-extract from Chrome.
- **Empty chats list** → the call to `MaZiqc` succeeded but with `recent_count=0`. Check that `recent` arg was passed.

## Capturing a fresh HAR

If endpoints rotate and parlai breaks:

1. Open `chrome://settings/cookies` and confirm you're logged into gemini.google.com.
2. Open DevTools → Network tab → check "Preserve log" → reload Gemini.
3. Click any conversation, scroll, and run a search.
4. Right-click any request → Save All as HAR with content.
5. Look for `batchexecute` requests; the `rpcids` query param tells you which RPCs the live UI uses now.

The reference HAR for the current release is captured at `/tmp/gemini-search-raw.txt` (full unqWSc response body).
