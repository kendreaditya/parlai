"""Claude Code local sessions: ~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

ROOT = Path.home() / ".claude" / "projects"


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
        title = None
        try:
            for line in path.read_text(errors="replace").splitlines():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "custom-title":
                    title = obj.get("customTitle") or title
        except OSError:
            return None
        return title

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
        title: str | None = None
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
                title = obj.get("customTitle") or title
                continue
            if t == "summary":
                title = title or obj.get("summary")
                continue
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
            title=title,
            url=None,
            created_at=first_ts or int(stat.st_birthtime * 1000) if hasattr(stat, "st_birthtime") else first_ts,
            updated_at=last_ts or int(stat.st_mtime * 1000),
            messages=messages,
            metadata={"cwd": cwd_decoded, "file": str(path)},
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        # No native search; CLI falls back to local FTS5
        return []

    def url_for(self, conv_id: str) -> str | None:
        return None


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
