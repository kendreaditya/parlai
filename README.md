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

```
parlai status                          # which providers are authed + indexed
parlai list   claude                    # list recent conversations
parlai search "sombrero"                # remote search, fans out across all providers
parlai search -p chatgpt "react hooks"  # one provider's native search
parlai search -p claude "x" --local     # local FTS5 (faster; needs prior sync)
parlai get    claude <id>               # full conversation as Markdown
parlai sync   claude --full             # mirror everything to local SQLite
parlai open   chatgpt <id>              # open in browser
```

Storage:

- `~/.parlai/db.sqlite` — conversations + messages, FTS5 over message text
- `~/.parlai/raw/<provider>/<id>.json` — verbatim payload per conversation
- `~/.parlai/credentials.json` — fallback cookie store (Chrome auto-discovery is preferred)

| Provider       | List API                                   | Search                | Auth                            |
|----------------|--------------------------------------------|-----------------------|---------------------------------|
| `chatgpt`      | `/backend-api/conversations`               | `/conversations/search` | session cookies + Bearer       |
| `claude`       | `/api/organizations/{org_uuid}/chat_conversations_v2` | `/conversation/search` | `sessionKey` cookie         |
| `gemini`       | `gemini-webapi` (HanaokaYuzu)              | local only             | `__Secure-1PSID` cookies        |
| `aistudio`     | Drive API via `gog drive`                  | Drive `fullText`       | Drive OAuth                     |
| `perplexity`   | `/rest/thread/list_ask_threads`            | same endpoint, `search_term` | session cookies                 |
| `claude-code`  | `~/.claude/projects/*.jsonl`               | local only             | none (local files)              |
| `codex-cli`    | `~/.codex/sessions/**/*.jsonl`             | local only             | none                            |
| `codex-desktop`| `~/.codex/sessions/**/*.jsonl`             | local only             | none                            |

## Contributing

Issues and PRs welcome at <https://github.com/kendreaditya/parlai>.

To add a new provider:

1. Subclass nothing — implement the small `Provider` Protocol in `src/parlai/providers/base.py` (5 methods: `authed`, `list`, `get`, `search`, `url_for`).
2. Capture a HAR from the provider's web app (DevTools → Network → right-click → Save All as HAR).
3. Map the list/get/search endpoints.
4. Register the class in `src/parlai/providers/__init__.py`.

Reverse-engineered endpoints break — when one does, regenerate from a fresh HAR.

## License

MIT.
