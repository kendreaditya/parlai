<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo-light.svg" alt="parlai" width="320">
  </picture>
</p>

<p align="center">
  <em>One CLI to search every AI chat you've ever had.</em>
</p>

---

## Why "parlai"

A **parley** is a conference between opposing sides — historically, the negotiation under a flag of truce before a battle resumes. Sailors parleyed, generals parleyed, and now you parley with **ai**: every chat with ChatGPT, Claude, Gemini, Perplexity is its own little negotiation. **parlai** is one place to keep the record of all of them.

## What it is

`parlai` is a single command-line tool that lets you list, fetch, and search your conversation history across every AI chat product you use — ChatGPT, Claude.ai, Gemini, Google AI Studio, Perplexity, Codex (CLI + Desktop), and Claude Code's local sessions — from one place.

There is no official API for any of this. `parlai` wraps each product's internal web endpoints (mapped from HAR captures) and falls back to local files where applicable.

## Install

```bash
brew tap kendreaditya/parlai https://github.com/kendreaditya/parlai
brew install kendreaditya/parlai/parlai
parlai status
```

Or from source with `uv`:

```bash
git clone https://github.com/kendreaditya/parlai ~/workspace/parlai
cd ~/workspace/parlai
uv tool install -e .
```

You're already authed for any provider whose web app you're logged into in Chrome — `parlai` reads cookies live from the browser via `browser_cookie3`. macOS, Linux, and Windows are all supported. If browser auto-detection fails (different browser, locked Keychain, etc.), fall back to manual cookie paste:

```bash
parlai login claude   # interactive — explains which cookie to copy
```

## How it works

```bash
parlai status                                # who's authed + indexed counts
parlai list claude [-n 20] [--remote]        # recent conversations (local DB or live API)
parlai get claude <id> [-f md|json] [-o]     # full conversation
parlai search "sombrero"                     # fan out remote search across every authed provider
parlai search -p claude "india"              # one provider's native search
parlai search "sombrero" --content --json    # search + fetch full bodies as JSONL (LLM-friendly)
parlai search "x" --local                    # cached FTS5 (faster; needs prior sync)
parlai sync claude --full                    # mirror every conversation into local SQLite
parlai open chatgpt <id>                     # open in browser
parlai login <provider>                      # interactive cookie capture (when Chrome auto-detect fails)
parlai --verbose <cmd>                       # log warnings to stderr
```

**Default = remote.** Live provider APIs are hit on every `search` so you don't need to sync first. For local-only providers (claude-code, codex-*) the search transparently falls back to the local FTS5 index. Pass `--local` to force the cache; pass `--content` to fetch full conversation bodies inline (also caches them to disk for next time).

Storage layout:

- `~/.parlai/db.sqlite` — conversations + messages, FTS5 over message text
- `~/.parlai/raw/<provider>/<id>.json` — verbatim payload per conversation
- `~/.parlai/credentials.json` — fallback cookie store (Chrome auto-discovery is preferred)

| Provider       | List API                                              | Native search                  | Auth                          |
|----------------|-------------------------------------------------------|--------------------------------|-------------------------------|
| `chatgpt`      | `/backend-api/conversations`                          | `/conversations/search`        | session cookies + Bearer      |
| `claude`       | `/api/organizations/{org_uuid}/chat_conversations_v2` | `/conversation/search` (chunked) | `sessionKey` cookie           |
| `gemini`       | `batchexecute` rpc `MaZiqc` (own client, no upstream lib) | rpc `unqWSc`               | `__Secure-1PSID` + `1PSIDTS`  |
| `aistudio`     | Drive API via `gog drive`                             | Drive `fullText contains`      | Drive OAuth via `gog`         |
| `perplexity`   | `/rest/thread/list_ask_threads`                       | same endpoint, `search_term`   | session cookies               |
| `claude-code`  | `~/.claude/projects/*.jsonl`                          | local FTS5 only                | none (local files)            |
| `codex-cli`    | `~/.codex/sessions/**/*.jsonl`                        | local FTS5 only                | none                          |
| `codex-desktop`| `~/.codex/sessions/**/*.jsonl`                        | local FTS5 only                | none                          |

## Contributing

Issues and PRs welcome at <https://github.com/kendreaditya/parlai>.

To add a new provider:

1. Capture a HAR from the provider's web app (DevTools → Network → right-click → Save All as HAR).
2. Map the list/get/search endpoints from the HAR.
3. Implement the `Provider` Protocol in `src/parlai/providers/base.py` (5 methods: `authed`, `list`, `get`, `search`, `url_for`).
4. Register the class in `src/parlai/providers/__init__.py`.
5. Add a fixture-based parser test under `tests/`.

Reverse-engineered endpoints break — when one does, recapture a fresh HAR. The Gemini provider lives at `src/parlai/providers/_gemini_internal.py` if you want a reference for handling Google's `batchexecute` framing protocol (length-prefixed JSON in UTF-16 code units).

## License

MIT.
