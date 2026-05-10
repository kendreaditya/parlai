"""Claude Code local sessions: ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

ROOT = Path.home() / ".claude" / "projects"
DESKTOP_SESSION_ROOT = (
    Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
)


def _decode_cwd(encoded: str) -> str:
    """The directory name is cwd with '/' replaced by '-' (and a leading '-' for absolute paths)."""
    return encoded.replace("-", "/")


class ClaudeCodeProvider:
    name = "claude-code"

    def authed(self) -> bool:
        return ROOT.exists()

    def _files(self) -> list[Path]:
        if not ROOT.exists():
            return []
        return sorted(ROOT.glob("*/*.jsonl"))

    def list(self, limit: int = 100) -> Iterator[dict]:
        files = self._files()
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[:limit]:
            yield {
                "id": p.stem,
                "title": self._title_from_file(p),
                "updated_at": int(p.stat().st_mtime * 1000),
            }

    def _title_from_file(self, path: Path) -> str | None:
        custom_title = None
        summary = None
        slug = None
        first_user_title = None
        try:
            for line in path.read_text(errors="replace").splitlines():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "custom-title":
                    custom_title = obj.get("customTitle") or custom_title
                elif obj.get("type") == "summary":
                    summary = summary or obj.get("summary")
                if obj.get("slug"):
                    slug = obj.get("slug")
                if not first_user_title:
                    first_user_title = _title_from_event(obj)
        except OSError:
            return None
        return _resolve_title(path.stem, custom_title, summary, slug, first_user_title)

    def _find_file(self, conv_id: str) -> Path | None:
        for p in self._files():
            if p.stem == conv_id:
                return p
        return None

    def get(self, conv_id: str) -> Conversation:
        path = self._find_file(conv_id)
        if not path:
            raise FileNotFoundError(f"No claude-code session {conv_id}")
        return self._parse(path)

    def _parse(self, path: Path) -> Conversation:
        custom_title: str | None = None
        summary: str | None = None
        slug: str | None = None
        first_user_title: str | None = None
        messages: list[Message] = []
        idx = 0
        cwd_decoded = _decode_cwd(path.parent.name)
        first_ts: int | None = None
        last_ts: int | None = None

        for line in path.read_text(errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            if t == "custom-title":
                custom_title = obj.get("customTitle") or custom_title
                continue
            if t == "summary":
                summary = summary or obj.get("summary")
                continue
            if obj.get("slug"):
                slug = obj.get("slug")
            if not first_user_title:
                first_user_title = _title_from_event(obj)
            if t in ("user", "assistant"):
                msg = obj.get("message") or {}
                role = msg.get("role") or t
                content = msg.get("content")
                text = _content_to_text(content)
                ts_raw = obj.get("timestamp")
                ts_ms = _iso_to_ms(ts_raw)
                if ts_ms:
                    first_ts = first_ts or ts_ms
                    last_ts = ts_ms
                if text.strip():
                    messages.append(
                        Message(idx=idx, role=role, text=text, created_at=ts_ms)
                    )
                    idx += 1

        stat = path.stat()
        return Conversation(
            provider=self.name,
            id=path.stem,
            title=_resolve_title(path.stem, custom_title, summary, slug, first_user_title),
            url=None,
            created_at=first_ts or int(stat.st_birthtime * 1000) if hasattr(stat, "st_birthtime") else first_ts,
            updated_at=last_ts or int(stat.st_mtime * 1000),
            messages=messages,
            metadata={"cwd": cwd_decoded, "file": str(path)},
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        """Scan all session JSONL files for `query` (case-insensitive substring).
        Returns one hit per matching file with a small snippet around the match."""
        q = query.lower()
        hits: list[SearchHit] = []
        for p in self._files():
            title = self._title_from_file(p) or p.stem
            try:
                text = p.read_text(errors="replace")
            except OSError:
                continue
            low = text.lower()
            idx = low.find(q)
            title_match = q in title.lower()
            if idx < 0 and not title_match:
                continue
            if title_match:
                title_idx = title.lower().find(q)
                snippet = (
                    title[:title_idx]
                    + "<<"
                    + title[title_idx:title_idx + len(query)]
                    + ">>"
                    + title[title_idx + len(query):]
                )
            else:
                start = max(0, idx - 60)
                end = min(len(text), idx + len(q) + 180)
                snippet = text[start:end].replace("\n", " ")
                # wrap the match in FTS-style <<...>> so the renderer can highlight
                off = idx - start
                snippet = snippet[:off] + "<<" + snippet[off:off+len(q)] + ">>" + snippet[off+len(q):]
            hits.append(SearchHit(
                provider=self.name,
                id=p.stem,
                title=title,
                snippet=snippet[:280],
                url=None,
            ))
            if len(hits) >= limit:
                break
        return hits

    def url_for(self, conv_id: str) -> str | None:
        return None


def _resolve_title(
    session_id: str,
    custom_title: str | None = None,
    summary: str | None = None,
    slug: str | None = None,
    first_user_title: str | None = None,
) -> str | None:
    """Resolve Claude Code titles across CLI JSONL and Desktop sidecar metadata."""
    by_cli_session, by_plan_slug = _desktop_title_indexes()
    desktop_title = by_cli_session.get(session_id)
    if not desktop_title and slug:
        desktop_title = by_plan_slug.get(slug)
    return (
        _clean_title(custom_title)
        or desktop_title
        or _clean_title(summary)
        or _clean_title(first_user_title)
    )


@lru_cache(maxsize=1)
def _desktop_title_indexes() -> tuple[dict[str, str], dict[str, str]]:
    """Return title indexes from Claude Desktop's claude-code session sidecars.

    Recent Claude Desktop-backed Claude Code sessions keep UI titles outside the
    JSONL transcript, in:

      ~/Library/Application Support/Claude/claude-code-sessions/**/*.json

    The direct key is `cliSessionId`; resumed/continued transcripts can also be
    linked by the plan slug stored in `planPath`, which appears as `slug` on
    JSONL rows.
    """
    by_cli_session: dict[str, str] = {}
    by_plan_slug: dict[str, str] = {}
    if not DESKTOP_SESSION_ROOT.exists():
        return by_cli_session, by_plan_slug

    for path in DESKTOP_SESSION_ROOT.rglob("*.json"):
        try:
            data = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue

        title = _clean_title(data.get("title"))
        if not title:
            continue

        cli_session_id = data.get("cliSessionId")
        if isinstance(cli_session_id, str) and cli_session_id:
            by_cli_session[cli_session_id] = title

        plan_path = data.get("planPath")
        if isinstance(plan_path, str) and plan_path:
            by_plan_slug[Path(plan_path).stem] = title

    return by_cli_session, by_plan_slug


def _clean_title(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _title_from_event(obj: dict) -> str | None:
    if obj.get("type") != "user" or obj.get("isMeta"):
        return None
    msg = obj.get("message") or {}
    if msg.get("role") and msg.get("role") != "user":
        return None
    title = _content_to_text(msg.get("content"))
    return _clean_title(_truncate_title(_strip_command_wrappers(title)))


def _strip_command_wrappers(text: str) -> str:
    text = text.strip()
    command_args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
    if command_args:
        text = command_args.group(1).strip()
    text = re.sub(r"<command-(?:message|name)>.*?</command-(?:message|name)>", "", text, flags=re.S)
    text = re.sub(r"<local-command-.*?>.*?</local-command-.*?>", "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


def _truncate_title(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    truncated = text[:limit].rsplit(" ", 1)[0]
    return (truncated or text[:limit]).rstrip() + "…"


def _content_to_text(content) -> str:
    """Claude Code stores content as either a string or a list of {type, text} blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                parts.append(f"[tool: {block.get('name','?')}]")
            elif block.get("type") == "tool_result":
                tr = block.get("content", "")
                if isinstance(tr, list):
                    tr = _content_to_text(tr)
                parts.append(f"[tool_result] {tr}")
        return "\n".join(parts)
    return str(content)


def _iso_to_ms(s) -> int | None:
    if not isinstance(s, str):
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError):
        return None
