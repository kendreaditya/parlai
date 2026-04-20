# Architecture

## One-paragraph summary

`parlai` is a CLI that talks to ~8 different AI products' internal web APIs (or, for local-only ones, reads JSONL files on disk) and unifies them behind one query interface. Each provider implements a 5-method protocol. Conversations and messages flow through provider-agnostic dataclasses into a single SQLite database that uses FTS5 for full-text search. The CLI layer (typer) routes commands, decides whether to hit the live API or the local cache, and renders results.

## Module dependency graph

```
                          cli.py  ──┐
                            │       │
            ┌───────────────┼───────┤
            ▼               ▼       ▼
        providers/       db.py   render.py
            │               │
   ┌────────┼────────┐      │
   ▼        ▼        ▼      ▼
 base    chatgpt    auth.py models.py
         claude
         gemini ──→ _gemini_internal.py
         …
                      ▲
                      │
                  paths.py + log.py (used everywhere)
```

- **`cli.py`** depends on everything below it. It's the only file that knows about typer, Rich rendering, or stdout.
- **`providers/`** depend only on `auth`, `models`, `log`. Each provider is independent — never imports another provider.
- **`db.py`** depends only on `models`, `paths`. It exposes `upsert_conversation()` and `search_local()`.
- **`auth.py`** depends only on `paths`, `log`. Optional `browser_cookie3` import is wrapped in `try/except`.
- **`_gemini_internal.py`** is private to `providers/gemini.py`. It exists because we don't want to depend on the upstream `gemini-webapi` library (see `design-decisions.md` § Gemini).

## The Provider Protocol

Every provider implements five methods. From `src/parlai/providers/base.py`:

```python
class Provider(Protocol):
    name: str

    def authed(self) -> bool: ...
    def list(self, limit: int = 100) -> Iterator[dict]: ...
    def get(self, conv_id: str) -> Conversation: ...
    def search(self, query: str, limit: int = 25) -> list[SearchHit]: ...
    def url_for(self, conv_id: str) -> str | None: ...
```

- `authed()` is cheap (cookie check, file glob). Used by `parlai status`.
- `list()` yields lightweight summaries (`{id, title, updated_at}`). Streaming for cheap providers, paginated for expensive ones.
- `get()` returns a fully-hydrated `Conversation`. This is where heavy work lives.
- `search()` returns native search hits. Empty list `[]` means "no native search" — the caller will fall back to local FTS5.
- `url_for()` returns the deep link to the conversation in the provider's web UI (or `None` for local-only providers).

Providers that have no remote search (`claude-code`, `codex-cli`, `codex-desktop`, `gemini` — wait, `gemini` has search now) just return `[]` from `search()`. The CLI's `_provider_search_with_fallback()` notices this and falls back to the local FTS5 index.

## Data flow: `parlai search "foo"`

```
user types `parlai search "foo"`
        │
        ▼
cli.search() — checks --local / --provider flags
        │
        ▼
_fanout_search(query) — ThreadPool over every authed provider
        │
        ▼ (per provider in parallel)
_provider_search_with_fallback("gemini", "foo", limit)
        │
        ├── try provider.search("foo")  ── live API call
        │
        └── if empty → db.search_local("foo", "gemini", limit)
                ▼
        sqlite SELECT ... FROM messages_fts WHERE messages_fts MATCH ?
        │
        ▼
_render_hits(rows, json_out=False)
        │
        ▼ (fzf-style stdout, or one JSON object per line if --json)
```

## Data flow: `parlai sync claude --full`

```
cli.sync("claude", full=True, limit=10**9)
        │
        ▼
provider.list(limit=10**9) — yields summaries one-by-one (uses cursor-paginated API)
        │
        ▼ (for each summary)
provider.get(cid) — returns Conversation with hydrated messages[]
        │
        ▼
write raw JSON to ~/.parlai/raw/<provider>/<cid>.json
        │
        ▼
db.upsert_conversation(conv, raw_path, synced_at)
        │
        ▼ (inside one transaction)
INSERT/UPDATE conversations row
DELETE old messages WHERE provider=? AND conv_id=?
INSERT new messages and matching messages_fts rows
        │
        ▼
update sync_state with new watermark
```

## Storage layout

```
~/.parlai/
├── db.sqlite                          ← single source of truth (FTS5 over messages.text)
├── raw/
│   ├── chatgpt/<cid>.json             ← verbatim payload as the API returned it
│   ├── claude/<cid>.json
│   └── …
└── credentials.json                   ← manual cookie fallback (if Chrome auto-detect fails)
```

- `db.sqlite` is the **searchable store**. Schema in `src/parlai/db.py` `SCHEMA`. Three tables: `conversations`, `messages`, virtual `messages_fts` (porter+unicode61 tokenizer), plus `sync_state` for watermarks.
- `raw/` is **forensic backup**. If a provider's parser changes or breaks, we can re-derive `messages` from the raw payload.
- `credentials.json` is **opt-in only**. The default code path goes through `browser_cookie3` reading Chrome's encrypted cookie store directly.

## Why a single SQLite DB instead of per-provider files?

- Cross-provider fan-out search becomes one SQL query.
- FTS5 is built into stdlib's sqlite3 — no extra dep, no service.
- Single file = easy backup, sync via Dropbox, or share across machines.
- Concurrent writes are rare (only `parlai sync`); WAL mode handles them fine.

## Why typer?

- Same author as FastAPI; identical decorator-based ergonomics.
- Type hints become argparse: `def list_cmd(provider: str, limit: int = 20)` becomes `parlai list <provider> --limit N` automatically.
- Pairs naturally with Rich for pretty output.
- Auto-generated `--help` is good enough that we don't write extra docs for it.

## Module sizes (rough)

| File | LOC | Purpose |
|---|---:|---|
| `cli.py` | ~250 | All command definitions + rendering helpers |
| `_gemini_internal.py` | ~250 | Self-hosted Gemini client (parser + RPC wrappers) |
| `db.py` | ~140 | Schema + FTS upsert/search |
| `providers/codex.py` | ~180 | Two-provider base for CLI + Desktop |
| `providers/chatgpt.py` | ~170 | Tree-walking parser for the mapping graph |
| `providers/claude.py` | ~150 | List/get/search REST |
| `providers/perplexity.py` | ~190 | Includes base64-decode for response bodies |
| `providers/claude_code.py` | ~140 | Local JSONL session reader |
| `providers/aistudio.py` | ~140 | Shells out to `gog drive` |
| `providers/gemini.py` | ~80 | Thin wrapper over `_gemini_internal` |
| `auth.py` | ~70 | Hybrid Chrome + manual cookies |
| `models.py` | ~25 | Conversation + Message dataclasses |
| `render.py` | ~20 | JSON → Markdown |

Total runtime code: ~1,800 LOC. Tests: ~150 LOC.
