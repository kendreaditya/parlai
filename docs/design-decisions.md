# Design decisions

Each section is a non-obvious choice. The format is: **decision** → **constraint that drove it** → **what we considered instead**.

## Why Python (not Go, Rust, Node)

**Constraint**: matches the user's existing stack — every other personal CLI lives in `~/workspace/.venv` or as a Python skill under `~/.claude/skills/`. Reusing the same toolchain means no new dependencies for editing, no new release pipeline.

**Considered**: Go (great for static binary distribution via Homebrew, no Python interpreter dep — but every provider would need bespoke HTTP/cookie/JSON code instead of leveraging the rich Python ecosystem). Rust (overkill for HTTP scraping; fights with rapid-iteration mental model needed for reverse-engineering web APIs).

## Why `uv` (not pip, poetry, hatch alone)

**Constraint**: needed deterministic, fast installs both for local development and Homebrew's `virtualenv_install_with_resources`. `uv sync` is 10–100× faster than pip; `uv tool install -e .` exposes the binary on PATH without polluting the user's other venvs.

**Considered**: poetry (slow, heavy, opinionated about workflow). pipx (one-shot install only, no lockfile). plain pip + requirements.txt (no lockfile, slow resolution).

## Why hybrid Chrome cookies + manual paste (not OAuth, not pure cookie)

**Constraint**: none of these products has an OAuth flow for "read my own conversations." The only pragmatic auth is the same session cookie the browser uses. Chrome auto-detection via `browser_cookie3` is zero-config when it works; manual paste covers Safari/Firefox/locked-Keychain edge cases.

**Considered**:
- Pure manual cookie paste — too much friction for the 95% case.
- mitmproxy / browser extension — adds an install step and a constant background process.
- Selenium/Playwright sessions — heavy, slow, requires re-login periodically.

## Why SQLite + FTS5 (not Elasticsearch, Meilisearch, no DB)

**Constraint**: zero-service install. SQLite ships with Python; FTS5 is built in. One file = trivial backup, easy to sync via Dropbox, sufficient for ~100k conversations indexed.

**Considered**:
- DuckDB — better analytics, but no built-in FTS5 (would need extension).
- Meilisearch / Typesense — separate process, defeats the "one binary" install.
- Just files + ripgrep — no JOIN with conversation metadata, no ranking.

## Why FTS5 highlight markers (`<<` / `>>`) instead of CSS classes

**Constraint**: the snippet is consumed by both the CLI (Rich ANSI) and `--json` output (LLM piped). A neutral marker that round-trips through JSON is simpler than CSS spans.

## Why default search is *remote*, not local

**Constraint**: the original default was local (cached FTS5). A user ran `parlai search -p claude "india"`, got "no results", and was confused — because they hadn't synced yet. Defaulting to remote means **searching always works without sync**. Local is now opt-in via `--local` for users who want offline / fast-paths.

The cost: remote is slower (network call) and rate-limited. Mitigation: local-only providers (claude-code, codex-*) silently fall back to local FTS, so fan-out search always returns *something* per provider.

## Why `--full` *truly* means full (no `--limit` cap)

**Constraint**: the user would type `parlai sync claude --full` expecting an archival backup, but get only the 500-most-recent because `--limit` defaulted to 500. That's a footgun. New behavior: `--full` ignores `--limit` entirely (sets effective limit to 10⁹).

## Why we wrote our own Gemini client (dropping `gemini-webapi`)

**Constraint**: `gemini-webapi` transitively depends on `orjson`, which requires `maturin` (Rust toolchain) to build. Homebrew refuses Rust source builds in formula installs. Result: `brew install` would error during Python wheel building. We had three options:

1. Mark `gemini-webapi` optional, lose Gemini support out-of-the-box.
2. Pre-build a binary wheel and ship it.
3. Re-implement the protocol natively, dropping the dep.

We chose (3) because (a) we only need 3 RPCs (list / read / search), (b) the upstream library is 5,897 LOC vs. our 250 LOC, (c) we wanted native search via `unqWSc` which the upstream library doesn't expose. See `gemini-batchexecute.md` for the protocol details.

## Why no multi-account in v1

**Constraint**: would require a non-trivial schema change (account becomes part of every primary key, cookie storage namespaced per `provider:account`, CLI gains `--account`). Discussed and explicitly deferred — single-account covers 95% of use cases.

When it's added: see the discussion in commit `c4f8318` (initial commit message + issue tracker).

## Why we chose Homebrew over PyPI for distribution

**Constraint**: PyPI has a name conflict — `parlai` is taken by Facebook's [ParlAI](https://github.com/facebookresearch/ParlAI), a famous dialog research library. We'd have to publish as `parlai-cli` on PyPI but the binary is still `parlai`, which is confusing. Homebrew namespaces by tap (`kendreaditya/parlai/parlai`), avoiding the collision.

Trade-off: macOS-only distribution (Linux users can `pip install -e .` from source).

## Why fan-out search uses `concurrent.futures.ThreadPoolExecutor`, not asyncio

**Constraint**: providers are a mix of sync (httpx) and would-be-async (gemini). Threads handle both transparently. `asyncio.gather` would force every provider to be async, including ones that don't benefit (claude-code does a file glob — no IO concurrency to win).

**Considered**: making everything async (boilerplate spreads across all 8 providers). Sequential (slow when one provider has high latency).

## Why the Gemini frame parser counts UTF-16 code units

**Constraint**: Google's `batchexecute` length prefix is in JavaScript `String.length`, which counts UTF-16 code units. A non-BMP character (emoji, certain CJK) takes 2 units but 1 Python char. Naïve byte counting / Python char counting under-consumes by N bytes when there are N non-BMP chars in the response. See `gemini-batchexecute.md` for the exact algorithm.

We hit this exactly — chunks were ending mid-frame and `json.loads` failed with "extra data". The fix was to walk char-by-char and add `2 if ord(c) > 0xFFFF else 1` to the consumed counter.

## Why we keep raw JSON dumps in `~/.parlai/raw/`

**Constraint**: the parsers are best-effort (each provider's payload shape changes without notice). When `read_chat()` returns garbled output, we want the raw response on disk for diffing, replaying through a fixed parser, and writing regression tests. ~30 KB per conversation is cheap.

## Why we don't ship a daemon / sync-on-cron

**Constraint**: scope. `parlai sync --full` is one command in your dotfiles; if you want it daily, you cron it yourself. Building a launchd / systemd integration would 2× the surface area for marginal value.

## Why `cli.py` has all command definitions in one file

**Constraint**: 8 commands × maybe 10 lines each = ~250 LOC total. Splitting into `commands/list.py`, `commands/search.py` etc. would force more file-jumping for less code per file. Single-file remains fast to read.

If/when commands grow past ~600 LOC, we'll split.

## Why we chose `Provider` as a `Protocol`, not an ABC

**Constraint**: structural typing means a provider doesn't have to inherit from anything — it just has to have the 5 methods. Easier to test (mock objects don't need a base class), easier to add providers from outside the codebase.

## Why the codex provider is split into `codex-cli` and `codex-desktop`

**Constraint**: both write to `~/.codex/sessions/**/*.jsonl` but the `session_meta.payload.originator` field distinguishes them ("Codex CLI" vs "Codex Desktop"). The user wanted them as separate searchable surfaces. Implementation: one base class `_CodexBase`, two thin subclasses each setting `originator_filter`.
