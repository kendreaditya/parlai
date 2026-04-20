"""Codex provider — reads ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.

Codex CLI and Codex Desktop share the same on-disk session format. The first event
in each file (`session_meta`) carries an `originator` field ("Codex CLI" vs "Codex Desktop")
that we use to split into two parlai providers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

ROOT = Path.home() / ".codex" / "sessions"


def _meta(path: Path) -> dict:
    """Read just the first JSON line and return its session_meta payload."""
    try:
        with path.open() as f:
            line = f.readline()
        obj = json.loads(line)
    except (OSError, json.JSONDecodeError):
        return {}
    if obj.get("type") != "session_meta":
        return {}
    return obj.get("payload") or {}


class _CodexBase:
    name: str = "codex"
    originator_filter: str | None = None  # set by subclasses

    def authed(self) -> bool:
        return ROOT.exists()

    def _files(self) -> list[Path]:
        if not ROOT.exists():
            return []
        return sorted(ROOT.glob("**/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)

    def _matches(self, path: Path) -> tuple[bool, dict]:
        meta = _meta(path)
        if not meta:
            return False, {}
        if self.originator_filter and meta.get("originator") != self.originator_filter:
            return False, meta
        return True, meta

    def list(self, limit: int = 100) -> Iterator[dict]:
        n = 0
        for p in self._files():
            ok, meta = self._matches(p)
            if not ok:
                continue
            yield {
                "id": meta.get("id") or p.stem,
                "title": _derive_title(p, fallback=meta.get("cwd")),
                "updated_at": int(p.stat().st_mtime * 1000),
            }
            n += 1
            if n >= limit:
                return

    def _find(self, conv_id: str) -> Path | None:
        for p in self._files():
            ok, meta = self._matches(p)
            if not ok:
                continue
            if meta.get("id") == conv_id or p.stem == conv_id or p.name.endswith(f"{conv_id}.jsonl"):
                return p
        return None

    def get(self, conv_id: str) -> Conversation:
        path = self._find(conv_id)
        if not path:
            raise FileNotFoundError(f"No {self.name} session {conv_id}")
        meta = _meta(path)
        title = _derive_title(path, fallback=meta.get("cwd"))
        msgs: list[Message] = []
        idx = 0
        first_ts: int | None = None
        last_ts: int | None = None

        for line in path.read_text(errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload") or {}
            ts_ms = _iso_ms(obj.get("timestamp"))
            if ts_ms:
                first_ts = first_ts or ts_ms
                last_ts = ts_ms

            text, role = _extract(payload)
            if not text or not text.strip():
                continue
            msgs.append(Message(idx=idx, role=role, text=text, created_at=ts_ms))
            idx += 1

        stat = path.stat()
        return Conversation(
            provider=self.name,
            id=meta.get("id") or path.stem,
            title=title,
            url=None,
            created_at=first_ts or int(getattr(stat, "st_birthtime", stat.st_mtime) * 1000),
            updated_at=last_ts or int(stat.st_mtime * 1000),
            messages=msgs,
            metadata={
                "originator": meta.get("originator"),
                "cwd": meta.get("cwd"),
                "model": meta.get("model_provider"),
                "file": str(path),
            },
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        """Scan all rollout JSONL files under this originator for `query`."""
        q = query.lower()
        hits: list[SearchHit] = []
        for p in self._files():
            ok, meta = self._matches(p)
            if not ok:
                continue
            try:
                text = p.read_text(errors="replace")
            except OSError:
                continue
            low = text.lower()
            idx = low.find(q)
            if idx < 0:
                continue
            start = max(0, idx - 60)
            end = min(len(text), idx + len(q) + 180)
            snippet = text[start:end].replace("\n", " ")
            off = idx - start
            snippet = snippet[:off] + "<<" + snippet[off:off+len(q)] + ">>" + snippet[off+len(q):]
            title = _derive_title(p, fallback=meta.get("cwd"))
            hits.append(SearchHit(
                provider=self.name,
                id=meta.get("id") or p.stem,
                title=title,
                snippet=snippet[:280],
                url=None,
            ))
            if len(hits) >= limit:
                break
        return hits

    def url_for(self, conv_id: str) -> str | None:
        return None


class CodexCLIProvider(_CodexBase):
    name = "codex-cli"
    originator_filter = "codex_cli_rs"  # adjust if your sessions use a different string


class CodexDesktopProvider(_CodexBase):
    name = "codex-desktop"
    originator_filter = "Codex Desktop"


def _derive_title(path: Path, fallback: str | None = None) -> str | None:
    """Pull title from the first non-environment user message; fall back to cwd."""
    try:
        for line in path.read_text(errors="replace").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "response_item":
                continue
            payload = obj.get("payload") or {}
            if payload.get("type") != "message" or payload.get("role") != "user":
                continue
            for c in payload.get("content") or []:
                t = c.get("text", "") if isinstance(c, dict) else ""
                if t and not t.strip().startswith("<environment_context>"):
                    return t.strip().splitlines()[0][:120]
    except OSError:
        pass
    if fallback:
        return f"({Path(fallback).name})"
    return None


def _extract(payload: dict) -> tuple[str, str]:
    """Convert one response_item payload into (text, role)."""
    t = payload.get("type")
    if t == "message":
        role = payload.get("role") or "user"
        if role == "developer":
            role = "system"
        parts: list[str] = []
        for c in payload.get("content") or []:
            if isinstance(c, dict):
                txt = c.get("text") or ""
                if txt and not txt.strip().startswith("<environment_context>"):
                    parts.append(txt)
        return "\n".join(parts), role
    if t == "reasoning":
        return "", "tool"  # encrypted blobs — skip text
    if t == "function_call":
        name = payload.get("name", "?")
        args = payload.get("arguments", "")
        return f"[function_call {name}] {args}"[:2000], "tool"
    if t == "function_call_output":
        out = payload.get("output", "")
        return f"[function_call_output] {out}"[:4000], "tool"
    if t == "custom_tool_call":
        return f"[{payload.get('name','?')}] {payload.get('input','')}"[:2000], "tool"
    if t == "custom_tool_call_output":
        return f"[tool_output] {payload.get('output','')}"[:4000], "tool"
    if t == "local_shell_call":
        action = payload.get("action") or {}
        cmd = action.get("command") or action
        return f"[shell] {json.dumps(cmd)[:1500]}", "tool"
    if t == "local_shell_call_output":
        return f"[shell_output] {(payload.get('output') or '')[:4000]}", "tool"
    return "", "tool"


def _iso_ms(s) -> int | None:
    if not isinstance(s, str):
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError):
        return None
