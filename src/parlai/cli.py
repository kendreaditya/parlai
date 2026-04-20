from __future__ import annotations

import json
import sys
import time
import webbrowser
from typing import Iterator, Optional

import typer
from rich.console import Console
from rich.table import Table

from parlai import providers
from parlai.auth import manual_set
from parlai.dates import parse_date
from parlai.paths import ensure
from parlai.render import to_markdown

app = typer.Typer(no_args_is_help=True, help="Unified CLI for personal AI chat history.")
console = Console()


@app.callback()
def _root(
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Print debug warnings (cookie failures, retries, etc.) to stderr",
    ),
) -> None:
    from parlai import log
    log.VERBOSE = verbose


def _date_filter_iter(it: Iterator[dict], since: int | None, until: int | None):
    """Yield from `it` while `updated_at` is in [since, until]; break early when we
    drop past `since` (assumes iterator is newest-first)."""
    for row in it:
        upd = row.get("updated_at") or 0
        if until is not None and upd > until:
            continue
        if since is not None and upd and upd < since:
            return
        yield row


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
@app.command()
def status() -> None:
    """Show which providers are authed."""
    ensure()
    table = Table(title="parlai providers")
    table.add_column("provider")
    table.add_column("authed")
    for name in providers.all_names():
        try:
            ok = providers.get(name).authed()
            mark = "✓" if ok else "✗"
        except Exception as e:
            mark = f"err: {e}"
        table.add_row(name, mark)
    console.print(table)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
@app.command(name="list")
def list_cmd(
    provider: str = typer.Argument(..., help="Provider name"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max conversations to show"),
    since: Optional[str] = typer.Option(
        None, "--since",
        help="Only conversations on/after this date (ISO or relative: 7d, 2w, 1y)",
    ),
    until: Optional[str] = typer.Option(
        None, "--until", help="Only conversations on/before this date",
    ),
) -> None:
    """List recent conversations (always live — hits the provider API or local files)."""
    p = providers.get(provider)
    since_ms = parse_date(since)
    until_ms = parse_date(until)
    # If --since is old, cap internal pager generously so we can walk back
    page_cap = max(limit, 5000) if since_ms or until_ms else limit
    it = p.list(limit=page_cap)
    if since_ms or until_ms:
        it = _date_filter_iter(it, since_ms, until_ms)
    rows: list[dict] = []
    for r in it:
        rows.append(r)
        if len(rows) >= limit:
            break
    table = Table()
    table.add_column("id", overflow="fold")
    table.add_column("updated")
    table.add_column("title")
    for row in rows:
        upd = row.get("updated_at")
        upd_str = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(upd / 1000)) if upd else "—"
        )
        table.add_row(row.get("id") or "?", upd_str, row.get("title") or "(untitled)")
    console.print(table)


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------
@app.command()
def get(
    provider: str = typer.Argument(...),
    conv_id: str = typer.Argument(...),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write to file"),
    fmt: str = typer.Option("md", "--format", "-f", help="md | json"),
) -> None:
    """Fetch a single conversation (always live)."""
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


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    provider: Optional[str] = typer.Option(
        None, "--provider", "-p", help="Restrict to one provider",
    ),
    limit: int = typer.Option(25, "--limit", "-n"),
    json_out: bool = typer.Option(
        False, "--json", help="Emit JSONL (one result per line); machine-readable",
    ),
    content: bool = typer.Option(
        False, "--content", "-c",
        help="Also fetch full conversation bodies for each hit (deduplicated)",
    ),
    since: Optional[str] = typer.Option(
        None, "--since", help="Only results on/after this date",
    ),
    until: Optional[str] = typer.Option(
        None, "--until", help="Only results on/before this date",
    ),
) -> None:
    """Search conversations. Always hits the provider API or local files — no cache."""
    since_ms = parse_date(since)
    until_ms = parse_date(until)
    if provider:
        hits = _safe_search(provider, query, limit, quiet=json_out)
    else:
        hits = _fanout_search(query, limit=limit, quiet=json_out)
    if since_ms is not None or until_ms is not None:
        hits = [h for h in hits if _hit_in_range(h, since_ms, until_ms)]
    if content:
        _render_hits_with_content(hits, json_out=json_out)
    else:
        _render_hits(hits, json_out=json_out)


def _safe_search(name: str, query: str, limit: int, quiet: bool = False) -> list[dict]:
    try:
        return list(providers.get(name).search(query, limit=limit) or [])
    except Exception as e:
        if not quiet:
            console.print(f"[dim]{name} search error: {e}[/dim]")
        return []


def _fanout_search(query: str, limit: int, quiet: bool = False) -> list[dict]:
    """Run native search against every authed provider in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    targets = []
    for name in providers.all_names():
        try:
            if providers.get(name).authed():
                targets.append(name)
        except Exception:
            continue
    out: list[dict] = []
    if not targets:
        return out
    with ThreadPoolExecutor(max_workers=max(1, len(targets))) as ex:
        futs = {ex.submit(_safe_search, name, query, limit, True): name for name in targets}
        for fut in as_completed(futs):
            try:
                out.extend(fut.result(timeout=120) or [])
            except Exception as e:
                if not quiet:
                    console.print(f"[dim]{futs[fut]}: {e}[/dim]")
    return out


def _hit_in_range(hit: dict, since_ms: int | None, until_ms: int | None) -> bool:
    upd = hit.get("updated_at")
    if upd is None:
        return True  # no timestamp → don't drop
    if since_ms is not None and upd < since_ms:
        return False
    if until_ms is not None and upd > until_ms:
        return False
    return True


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------
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
    for h in hits:
        provider = h.get("provider", "?")
        cid = h.get("conv_id") or h.get("id") or "?"
        title = h.get("title") or "(untitled)"
        snip = (h.get("snip") or h.get("snippet") or "").replace("\n", " ").strip()
        snip = snip.replace("<<", "[bold yellow]").replace(">>", "[/bold yellow]")
        console.print(
            f"[dim cyan]\\[{provider}][/dim cyan] "
            f"[dim]{cid}[/dim]  [bold]{title}[/bold]"
        )
        if snip:
            console.print(f"  [dim]{snip}[/dim]")


def _render_hits_with_content(hits: list[dict], json_out: bool = False) -> None:
    """Deduplicate hits by (provider, id), fetch each conversation, emit with full body.
    No caching — each `get()` hits the provider API / filesystem fresh."""
    seen: dict[tuple[str, str], dict] = {}
    for h in hits:
        provider = h.get("provider") or "?"
        cid = h.get("conv_id") or h.get("id") or "?"
        key = (provider, cid)
        if key in seen:
            seen[key]["snippets"].append(h.get("snip") or h.get("snippet") or "")
            continue
        seen[key] = {
            "provider": provider, "id": cid,
            "title": h.get("title"), "url": h.get("url"),
            "snippets": [h.get("snip") or h.get("snippet") or ""],
        }
    for entry in seen.values():
        provider, cid = entry["provider"], entry["id"]
        try:
            conv = providers.get(provider).get(cid)
            entry["title"] = conv.title or entry["title"]
            entry["url"] = conv.url or entry["url"]
            entry["messages"] = [
                {"idx": m.idx, "role": m.role, "text": m.text, "created_at": m.created_at}
                for m in conv.messages
            ]
        except Exception as e:
            entry["messages"] = []
            entry["fetch_error"] = str(e)

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
            console.print(f"[bold]## {m['role']}[/bold]\n{m['text'] or ''}\n")
        console.print("[dim]" + "─" * 60 + "[/dim]\n")


# ---------------------------------------------------------------------------
# open / login
# ---------------------------------------------------------------------------
@app.command(name="open")
def open_cmd(provider: str = typer.Argument(...), conv_id: str = typer.Argument(...)) -> None:
    """Open the conversation in your default browser."""
    url = providers.get(provider).url_for(conv_id)
    if not url:
        console.print(f"[yellow]{provider} has no web URL for {conv_id}[/yellow]")
        raise typer.Exit(1)
    console.print(f"[cyan]→[/cyan] {url}")
    webbrowser.open(url)


_LOGIN_HINTS: dict[str, dict[str, str]] = {
    "chatgpt":       {"url": "https://chatgpt.com",          "needed": "__Secure-next-auth.session-token (or .0 / .1 split)"},
    "claude":        {"url": "https://claude.ai",            "needed": "sessionKey (starts with sk-ant-sid01...)"},
    "gemini":        {"url": "https://gemini.google.com",    "needed": "__Secure-1PSID and __Secure-1PSIDTS"},
    "perplexity":    {"url": "https://www.perplexity.ai",    "needed": "__Secure-next-auth.session-token"},
    "aistudio":      {"url": "https://aistudio.google.com",  "needed": "use `gog` CLI — run `gog auth login` first"},
    "claude-code":   {"url": None,                           "needed": "no auth — reads ~/.claude/projects/*.jsonl"},
    "codex-cli":     {"url": None,                           "needed": "no auth — reads ~/.codex/sessions/**/*.jsonl"},
    "codex-desktop": {"url": None,                           "needed": "no auth — reads ~/.codex/sessions/**/*.jsonl"},
}


@app.command()
def login(
    provider: str = typer.Argument(..., help="Which provider to authenticate"),
    cookie: Optional[str] = typer.Option(
        None, "--cookie", "-c", help="Raw 'name1=value1; name2=value2' Cookie header",
    ),
) -> None:
    """Authenticate to a provider (interactive cookie paste if --cookie not given)."""
    hint = _LOGIN_HINTS.get(provider, {})
    if cookie is None:
        console.print(f"[bold]Login for {provider}[/bold]")
        if hint.get("url"):
            console.print(f"  1. Open [cyan]{hint['url']}[/cyan] in Chrome and sign in")
            console.print("  2. DevTools → Application → Cookies → pick the domain")
            console.print(f"     Need: [yellow]{hint.get('needed','session cookies')}[/yellow]")
            console.print("  3. Copy the full Cookie header from any XHR")
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
    for chunk in (cookie or "").split(";"):
        chunk = chunk.strip()
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            pairs[k.strip()] = v.strip()
    if not pairs:
        console.print("[red]no cookies parsed — expected name=value; name=value...[/red]")
        raise typer.Exit(1)
    manual_set(provider, pairs)
    console.print(f"[green]✓[/green] stored {len(pairs)} cookies for {provider}")
    try:
        ok = providers.get(provider).authed()
        console.print(f"  authed: {'✓' if ok else '✗ (cookies may be wrong or expired)'}")
    except Exception as e:
        console.print(f"  [yellow]could not verify: {e}[/yellow]")
