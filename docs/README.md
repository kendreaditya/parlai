# parlai design docs

Welcome. These docs exist so a future contributor (or AI agent) can read for ~20 minutes and become productive on the codebase. The user-facing README at the repo root explains *what* `parlai` does and how to install it. These docs explain *why it's built the way it is* and *how to extend it*.

## Reading order

1. **[architecture.md](architecture.md)** — module layout, data flow, what each file does. Read this first.
2. **[design-decisions.md](design-decisions.md)** — every non-obvious choice and the constraint that drove it. Read this when you wonder "why didn't they just use X?"
3. **[providers.md](providers.md)** — per-provider implementation notes: endpoints, quirks, gotchas. Read the section for the provider you're working on.
4. **[gemini-batchexecute.md](gemini-batchexecute.md)** — deep dive into Google's `batchexecute` framing protocol. Read this if you're touching anything Google-flavoured (Gemini, AI Studio).
5. **[adding-a-provider.md](adding-a-provider.md)** — step-by-step guide for adding the 9th provider.

## Codebase at a glance

```
src/parlai/
├── cli.py              ← typer entry point — every user command lives here
├── auth.py             ← Chrome cookie extraction + manual paste fallback
├── db.py               ← SQLite schema + FTS5 setup + upsert/search
├── models.py           ← Conversation, Message dataclasses (provider-agnostic)
├── render.py           ← JSON → Markdown for `parlai get`
├── log.py              ← --verbose-aware warn() helper
├── paths.py            ← ~/.parlai/* path resolution
└── providers/
    ├── base.py             ← Provider Protocol (5 methods)
    ├── chatgpt.py          ← /backend-api/* endpoints
    ├── claude.py           ← /api/organizations/{org}/* endpoints
    ├── claude_code.py      ← reads ~/.claude/projects/*.jsonl (no auth)
    ├── codex.py            ← reads ~/.codex/sessions/**/*.jsonl (no auth, two filters)
    ├── gemini.py           ← thin wrapper over _gemini_internal
    ├── _gemini_internal.py ← own batchexecute client (replaces gemini-webapi dep)
    ├── perplexity.py       ← /rest/thread/* endpoints (base64-encoded responses!)
    └── aistudio.py         ← shells out to `gog drive` for Drive API
```

## Quick mental model

- **Every provider is a "scraper" of an internal web API**, except `claude-code`/`codex-*` which read local JSONL files.
- **Storage is a write-through cache**: live searches hit the API by default, but the raw payload is mirrored to disk and indexed in SQLite FTS5 for offline use.
- **Auth is hybrid**: try Chrome cookies first via `browser_cookie3`, fall back to manual paste stored at `~/.parlai/credentials.json`. No OAuth dance, no service registration.
- **The CLI is opinionated about defaults**: search defaults to *remote* (always-fresh), `--full` actually means full (no limit cap).
