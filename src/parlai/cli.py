from __future__ import annotations

import json
import sys
import time
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from parlai import db, providers
from parlai.auth import manual_set
from parlai.paths import ensure, raw_path
from parlai.render import to_markdown

app = typer.Typer(no_args_is_help=True, help="Unified CLI for personal AI chat history.")
console = Console()


@app.callback()
def _root(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Print debug warnings (cookie failures, retries, etc.) to stderr"
    ),
) -> None:
    """Set global flags before any command runs."""
    from parlai import log
    log.VERBOSE = verbose


def _ensure_db() -> None:
    ensure()
    db.init()


@app.command()
def status() -> None:
    """Show which providers are authed and how many conversations are indexed."""
    _ensure_db()
    table = Table(title="parlai providers")
    table.add_column("provider")
    table.add_column("authed")
    table.add_column("conversations", justify="right")
    table.add_column("messages", justify="right")
    table.add_column("last sync")

    by_provider = {row["provider"]: row for row in db.stats()}
    for name in providers.all_names():
        try:
            authed = providers.get(name).authed()
        except Exception as e:
            authed = f"err: {e}"
        info = by_provider.get(name, {})
        last = info.get("last_sync")
        last_str = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(last / 1000))
            if last
            else "—"
        )
        table.add_row(
            name,
            "✓" if authed is True else ("✗" if authed is False else str(authed)),
            str(info.get("conversations", 0)),
            str(info.get("messages", 0)),
            last_str,
        )
    console.print(table)


@app.command(name="list")
def list_cmd(
    provider: str = typer.Argument(..., help="Provider name"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max conversations to show"),
    remote: bool = typer.Option(
        False, "--remote", help="Hit the provider API instead of the local DB"
    ),
) -> None:
    """List recent conversations from a provider."""
    _ensure_db()
    p = providers.get(provider)
    table = Table()
    table.add_column("id", overflow="fold")
    table.add_column("updated")
    table.add_column("title")
    if remote:
        rows = list(p.list(limit=limit))
    else:
        rows = db.list_conversations(provider, limit=limit)
    for row in rows:
        upd = row.get("updated_at")
        upd_str = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(upd / 1000)) if upd else "—"
        )
        table.add_row(row.get("id") or "?", upd_str, row.get("title") or "(untitled)")
    console.print(table)


@app.command()
def get(
    provider: str = typer.Argument(...),
    conv_id: str = typer.Argument(...),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Write to file instead of stdout"
    ),
    fmt: str = typer.Option(
        "md", "--format", "-f", help="md | json"
    ),
) -> None:
    """Fetch a single conversation."""
    _ensure_db()
    p = providers.get(provider)
    conv = p.get(conv_id)
    if fmt == "json":
        text = json.dumps(
            {
                "provider": conv.provider,
                "id": conv.id,
                "title": conv.title,
                "url": conv.url,
                "messages": [
                    {"idx": m.idx, "role": m.role, "text": m.text, "created_at": m.created_at}
                    for m in conv.messages
                ],
                "metadata": conv.metadata,
            },
            indent=2,
        )
    else:
        text = to_markdown(conv)
    if output:
        from pathlib import Path

        Path(output).write_text(text)
        console.print(f"[green]wrote[/green] {output} ({len(text)} bytes)")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="Restrict to one provider"
    ),
    local: bool = typer.Option(
        False,
        "--local",
        help="Use local FTS5 index instead of live provider APIs (faster, may be stale)",
    ),
    limit: int = typer.Option(25, "--limit", "-n"),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSONL (one result per line); machine-readable for LLMs/scripts"
    ),
    content: bool = typer.Option(
        False,
        "--content",
        "-c",
        help="Also fetch and include the full conversation body for each hit (deduplicated)",
    ),
) -> None:
    """Search conversations.

    Default: hits each provider's live search API. For providers without native
    search (claude-code, codex-*, gemini), silently falls back to the local index.
    Use --local to force the cached FTS5 index across all providers.
    """
    _ensure_db()
    if local:
        if not json_out:
            _warn_if_stale(provider)
        hits = db.search_local(query, provider=provider, limit=limit)
    elif provider:
        hits = _provider_search_with_fallback(provider, query, limit, quiet=json_out)
    else:
        hits = _fanout_search(query, limit=limit, quiet=json_out)
    if content:
        _render_hits_with_content(hits, json_out=json_out)
    else:
        _render_hits(hits, json_out=json_out)


def _provider_search_with_fallback(
    name: str, query: str, limit: int, quiet: bool = False
) -> list[dict]:
    p = providers.get(name)
    try:
        hits = p.search(query, limit=limit) or []
    except Exception as e:
        if not quiet:
            console.print(f"[dim]{name} remote search failed: {e}; using local[/dim]")
        hits = []
    if hits:
        return list(hits)
    # remote returned empty (either no results or no native search) — try local
    if not quiet:
        _warn_if_stale(name)
    return db.search_local(query, provider=name, limit=limit)


def _warn_if_stale(provider: Optional[str]) -> None:
    """Print a warning if the local index hasn't been synced in >7 days."""
    threshold_ms = 7 * 24 * 3600 * 1000
    rows = db.stats()
    now = int(time.time() * 1000)
    for r in rows:
        if provider and r["provider"] != provider:
            continue
        last = r.get("last_sync")
        if not last:
            continue
        age_days = (now - last) / 86_400_000
        if age_days > 7:
            console.print(
                f"[yellow]⚠ {r['provider']} local index is {age_days:.0f} days stale; run "
                f"'parlai sync {r['provider']} --full'[/yellow]"
            )


def _fanout_search(query: str, limit: int, quiet: bool = False) -> list[dict]:
    """Run native search against every authed provider in parallel.

    For providers whose remote search returns nothing (no native API or no results),
    silently fall back to local FTS for that provider so the user sees a unified set.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    targets = []
    for name in providers.all_names():
        try:
            p = providers.get(name)
            if p.authed():
                targets.append((name, p))
        except Exception:
            continue
    out: list[dict] = []
    if not targets:
        return out
    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
        futs = {
            ex.submit(_provider_search_with_fallback, name, query, limit, True): name
            for name, _ in targets
        }
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                hits = fut.result(timeout=60) or []
                out.extend(hits)
            except Exception as e:
                if not quiet:
                    console.print(f"[dim]{name} search error: {e}[/dim]")
    return out


@app.command(name="open")
def open_cmd(
    provider: str = typer.Argument(...),
    conv_id: str = typer.Argument(...),
) -> None:
    """Open the conversation in your default browser."""
    p = providers.get(provider)
    url = p.url_for(conv_id)
    if not url:
        console.print(f"[yellow]{provider} has no web URL for {conv_id}[/yellow]")
        raise typer.Exit(1)
    import webbrowser

    console.print(f"[cyan]→[/cyan] {url}")
    webbrowser.open(url)


@app.command()
def stats() -> None:
    """Show storage stats per provider."""
    _ensure_db()
    rows = db.stats()
    if not rows:
        console.print("[dim]no data yet — try `parlai sync`[/dim]")
        return
    table = Table(title="local index")
    table.add_column("provider")
    table.add_column("conversations", justify="right")
    table.add_column("messages", justify="right")
    table.add_column("last sync")
    for r in rows:
        last = r.get("last_sync")
        last_str = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(last / 1000))
            if last
            else "—"
        )
        table.add_row(
            r["provider"], str(r["conversations"]), str(r["messages"]), last_str
        )
    console.print(table)


def _render_hits_with_content(hits: list[dict], json_out: bool = False) -> None:
    """Deduplicate hits by (provider, id), fetch each conversation, then emit
    each result with its full message body."""
    # Group snippets by (provider, id), preserving rank order
    seen: dict[tuple[str, str], dict] = {}
    for h in hits:
        provider = h.get("provider") or "?"
        cid = h.get("conv_id") or h.get("id") or "?"
        key = (provider, cid)
        if key in seen:
            seen[key]["snippets"].append(h.get("snip") or h.get("snippet") or "")
            continue
        seen[key] = {
            "provider": provider,
            "id": cid,
            "title": h.get("title"),
            "url": h.get("url"),
            "snippets": [h.get("snip") or h.get("snippet") or ""],
        }

    for entry in seen.values():
        provider, cid = entry["provider"], entry["id"]
        try:
            conv = providers.get(provider).get(cid)
            messages = [
                {"idx": m.idx, "role": m.role, "text": m.text, "created_at": m.created_at}
                for m in conv.messages
            ]
            entry["title"] = conv.title or entry["title"]
            entry["url"] = conv.url or entry["url"]
            # Cache: write through to local DB + raw mirror so future searches hit it
            try:
                rp = raw_path(provider, cid)
                rp.write_text(
                    json.dumps(
                        {
                            "id": conv.id,
                            "title": conv.title,
                            "url": conv.url,
                            "created_at": conv.created_at,
                            "updated_at": conv.updated_at,
                            "metadata": conv.metadata,
                            "messages": messages,
                        },
                        indent=2,
                    )
                )
                db.upsert_conversation(conv, str(rp), int(time.time() * 1000))
            except Exception:
                pass  # cache failures shouldn't break the user-facing output
        except Exception as e:
            messages = []
            entry["fetch_error"] = str(e)
        entry["messages"] = messages

    if json_out:
        for e in seen.values():
            sys.stdout.write(json.dumps(e, ensure_ascii=False) + "\n")
        return

    if not seen:
        console.print("[dim]no results[/dim]")
        return
    for e in seen.values():
        console.print(
            f"[dim cyan]\\[{e['provider']}][/dim cyan] "
            f"[dim]{e['id']}[/dim]  [bold]{e.get('title') or '(untitled)'}[/bold]"
        )
        for snip in e["snippets"][:3]:
            snip = snip.replace("\n", " ").strip()
            snip = snip.replace("<<", "[bold yellow]").replace(">>", "[/bold yellow]")
            if snip:
                console.print(f"  [dim]› {snip}[/dim]")
        if e.get("fetch_error"):
            console.print(f"  [red]fetch failed: {e['fetch_error']}[/red]")
            continue
        console.print()
        for m in e["messages"]:
            role = m["role"]
            text = m["text"] or ""
            console.print(f"[bold]## {role}[/bold]\n{text}\n")
        console.print("[dim]" + "─" * 60 + "[/dim]\n")


def _render_hits(hits: list[dict], json_out: bool = False) -> None:
    if json_out:
        for h in hits:
            payload = {
                "provider": h.get("provider"),
                "id": h.get("conv_id") or h.get("id"),
                "title": h.get("title"),
                "url": h.get("url"),
                "snippet": h.get("snip") or h.get("snippet"),
                "updated_at": h.get("updated_at"),
            }
            sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return
    if not hits:
        console.print("[dim]no results[/dim]")
        return
    # fzf-style: header line + indented snippet
    for h in hits:
        provider = h.get("provider", "?")
        cid = h.get("conv_id") or h.get("id") or "?"
        title = h.get("title") or "(untitled)"
        snip = (h.get("snip") or h.get("snippet") or "").replace("\n", " ").strip()
        # Highlight matched terms (FTS5 wraps them in <<...>> by default)
        snip = snip.replace("<<", "[bold yellow]").replace(">>", "[/bold yellow]")
        console.print(
            f"[dim cyan]\\[{provider}][/dim cyan] "
            f"[dim]{cid}[/dim]  "
            f"[bold]{title}[/bold]"
        )
        if snip:
            console.print(f"  [dim]{snip}[/dim]")


@app.command()
def sync(
    provider: Optional[str] = typer.Argument(
        None, help="Provider to sync; omit to sync all authed providers"
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Re-fetch every conversation, ignoring watermark and limit (true backup)",
    ),
    limit: int = typer.Option(
        500,
        "--limit",
        help="Max conversations to enumerate per provider (ignored with --full)",
    ),
) -> None:
    """Pull conversations from provider(s) into the local DB."""
    _ensure_db()
    targets = [provider] if provider else providers.all_names()
    # --full means *truly* full: no limit cap, no watermark
    effective_limit = 10**9 if full else limit
    for name in targets:
        try:
            p = providers.get(name)
        except KeyError:
            console.print(f"[red]unknown provider:[/red] {name}")
            continue
        if not p.authed():
            console.print(f"[yellow]skip {name}: not authed[/yellow]")
            continue
        watermark = None if full else db.get_watermark(name)
        new_watermark = watermark or 0
        count = 0
        console.print(f"[cyan]{name}:[/cyan] listing…")
        for summary in p.list(limit=effective_limit):
            upd = summary.get("updated_at") or 0
            cid = summary["id"]
            if watermark and upd <= watermark:
                continue
            try:
                conv = p.get(cid)
            except Exception as e:
                console.print(f"  [red]get {cid} failed:[/red] {e}")
                continue
            rp = raw_path(name, cid)
            rp.write_text(
                json.dumps(
                    {
                        "id": conv.id,
                        "title": conv.title,
                        "url": conv.url,
                        "created_at": conv.created_at,
                        "updated_at": conv.updated_at,
                        "metadata": conv.metadata,
                        "messages": [
                            {"idx": m.idx, "role": m.role, "text": m.text, "created_at": m.created_at}
                            for m in conv.messages
                        ],
                    },
                    indent=2,
                )
            )
            db.upsert_conversation(conv, str(rp), int(time.time() * 1000))
            count += 1
            if upd > new_watermark:
                new_watermark = upd
        db.set_sync_state(name, int(time.time() * 1000), new_watermark or None)
        console.print(f"[green]{name}:[/green] synced {count} conversations")


_LOGIN_HINTS: dict[str, dict[str, str]] = {
    "chatgpt": {
        "domain": "chatgpt.com",
        "url": "https://chatgpt.com",
        "needed": "__Secure-next-auth.session-token (or .0 / .1 split)",
    },
    "claude": {
        "domain": "claude.ai",
        "url": "https://claude.ai",
        "needed": "sessionKey (starts with sk-ant-sid01...)",
    },
    "gemini": {
        "domain": ".google.com",
        "url": "https://gemini.google.com",
        "needed": "__Secure-1PSID and __Secure-1PSIDTS",
    },
    "perplexity": {
        "domain": ".perplexity.ai",
        "url": "https://www.perplexity.ai",
        "needed": "__Secure-next-auth.session-token (or next-auth.session-token)",
    },
    "aistudio": {
        "domain": "google",
        "url": "https://aistudio.google.com",
        "needed": "uses `gog` CLI for auth — run `gog auth login` first",
    },
    "claude-code": {
        "domain": "(local)",
        "url": "n/a",
        "needed": "no auth — reads ~/.claude/projects/*.jsonl",
    },
    "codex-cli": {
        "domain": "(local)",
        "url": "n/a",
        "needed": "no auth — reads ~/.codex/sessions/**/*.jsonl",
    },
    "codex-desktop": {
        "domain": "(local)",
        "url": "n/a",
        "needed": "no auth — reads ~/.codex/sessions/**/*.jsonl",
    },
}


@app.command()
def login(
    provider: str = typer.Argument(..., help="Which provider to authenticate"),
    cookie: Optional[str] = typer.Option(
        None, "--cookie", "-c", help="Raw 'name1=value1; name2=value2' Cookie header"
    ),
) -> None:
    """Authenticate to a provider.

    With no --cookie, prints instructions and prompts you to paste a Cookie header
    pulled from your browser's DevTools.
    """
    hint = _LOGIN_HINTS.get(provider, {})
    if cookie is None:
        console.print(f"[bold]Login for {provider}[/bold]")
        if hint.get("url"):
            console.print(f"  1. Open [cyan]{hint['url']}[/cyan] in Chrome and sign in")
        if hint.get("domain") != "(local)":
            console.print("  2. Open DevTools → Application → Cookies → select the domain")
            console.print(f"     Need: [yellow]{hint.get('needed','session cookies')}[/yellow]")
            console.print("  3. Copy the full Cookie request header from any XHR (Network tab → request → Headers)")
            console.print("  4. Paste below.\n")
            try:
                cookie = typer.prompt("Cookie header", hide_input=True)
            except (EOFError, KeyboardInterrupt):
                console.print("[yellow]aborted[/yellow]")
                raise typer.Exit(1)
        else:
            console.print(f"  [green]{provider} needs no auth — {hint.get('needed','')}[/green]")
            return
    pairs: dict[str, str] = {}
    for chunk in cookie.split(";"):
        chunk = chunk.strip()
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            pairs[k.strip()] = v.strip()
    if not pairs:
        console.print("[red]no cookies parsed — expected name=value; name=value...[/red]")
        raise typer.Exit(1)
    manual_set(provider, pairs)
    console.print(f"[green]✓[/green] stored {len(pairs)} cookies for {provider}")
    # immediate verification
    try:
        ok = providers.get(provider).authed()
        console.print(f"  authed: {'✓' if ok else '✗ (cookies may be wrong or expired)'}")
    except Exception as e:
        console.print(f"  [yellow]could not verify: {e}[/yellow]")
