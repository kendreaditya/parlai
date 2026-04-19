"""Google AI Studio provider — uses the Drive API via `gog drive` shell-out.

AI Studio prompts are stored in Drive as files with mime type
`application/vnd.google-makersuite.prompt`. Parsing logic adapted from
~/.claude/skills/gemini-convo/scripts/parse.py (skill is a script, not importable).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

from parlai.models import Conversation, Message
from parlai.providers.base import SearchHit

MIME = "application/vnd.google-makersuite.prompt"


class AIStudioProvider:
    name = "aistudio"

    def authed(self) -> bool:
        if not shutil.which("gog"):
            return False
        # Cheap check: try a tiny search and see if it succeeds.
        try:
            self._gog_json(
                ["drive", "search", f"mimeType='{MIME}'", "--raw-query", "--max", "1"]
            )
            return True
        except Exception:
            return False

    def _gog_json(self, args: list[str]) -> list | dict:
        cmd = ["gog", *args, "--json", "--results-only"]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=60)
        return json.loads(out) if out.strip() else []

    def list(self, limit: int = 100) -> Iterator[dict]:
        page_token: str | None = None
        yielded = 0
        while yielded < limit:
            args = [
                "drive",
                "search",
                f"mimeType='{MIME}'",
                "--raw-query",
                "--max",
                str(min(100, limit - yielded)),
            ]
            if page_token:
                args += ["--page", page_token]
            results = self._gog_json(args)
            if not isinstance(results, list) or not results:
                return
            for f in results:
                yield {
                    "id": f.get("id"),
                    "title": f.get("name"),
                    "updated_at": _iso_ms(f.get("modifiedTime")),
                }
                yielded += 1
                if yielded >= limit:
                    return
            # gog with --results-only drops nextPageToken; fetch without to paginate
            full = json.loads(
                subprocess.check_output(
                    [
                        "gog",
                        "drive",
                        "search",
                        f"mimeType='{MIME}'",
                        "--raw-query",
                        "--max",
                        str(min(100, limit - yielded)),
                        "--json",
                    ]
                    + (["--page", page_token] if page_token else []),
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                )
            )
            page_token = full.get("nextPageToken") if isinstance(full, dict) else None
            if not page_token:
                return

    def get(self, conv_id: str) -> Conversation:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            path = Path(tf.name)
        try:
            subprocess.check_output(
                ["gog", "drive", "download", conv_id, "--out", str(path)],
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
            data = json.loads(path.read_text())
        finally:
            path.unlink(missing_ok=True)

        meta = self._gog_json(["drive", "get", conv_id])
        if isinstance(meta, list):
            meta = meta[0] if meta else {}

        msgs = _parse_chunks(data)
        return Conversation(
            provider=self.name,
            id=conv_id,
            title=meta.get("name") if isinstance(meta, dict) else None,
            url=self.url_for(conv_id),
            created_at=_iso_ms(meta.get("createdTime")) if isinstance(meta, dict) else None,
            updated_at=_iso_ms(meta.get("modifiedTime")) if isinstance(meta, dict) else None,
            messages=msgs,
            metadata={
                "model": (data.get("runSettings") or {}).get("model"),
            },
        )

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        # Drive full-text search across these files
        escaped = query.replace("'", "\\'")
        args = [
            "drive",
            "search",
            f"mimeType='{MIME}' and fullText contains '{escaped}'",
            "--raw-query",
            "--max",
            str(limit),
        ]
        results = self._gog_json(args)
        if not isinstance(results, list):
            return []
        hits: list[SearchHit] = []
        for f in results:
            hits.append(
                SearchHit(
                    provider=self.name,
                    id=f.get("id"),
                    title=f.get("name"),
                    snippet="",
                    url=self.url_for(f.get("id")),
                )
            )
        return hits

    def url_for(self, conv_id: str) -> str | None:
        if not conv_id:
            return None
        return f"https://aistudio.google.com/app/prompts/{conv_id}"


def _iso_ms(s) -> int | None:
    if not isinstance(s, str):
        return None
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _parse_chunks(data: dict) -> list[Message]:
    """Parse the chunkedPrompt structure into ordered messages."""
    chunks = (data.get("chunkedPrompt") or {}).get("chunks", [])
    out: list[Message] = []
    idx = 0
    for chunk in chunks:
        role = chunk.get("role", "unknown")
        if chunk.get("isThought"):
            continue
        parts = chunk.get("parts", []) or []
        if parts and all(p.get("thoughtSignature") or p.get("thought") for p in parts):
            continue
        text = chunk.get("text") or ""
        if not text and parts:
            text = "".join(
                p.get("text", "")
                for p in parts
                if not p.get("thought") and not p.get("thoughtSignature")
            )
        if not text.strip():
            drive_doc = chunk.get("driveDocument") or {}
            if drive_doc.get("id"):
                text = f"[attached Drive document: {drive_doc['id']}]"
            else:
                continue
        out.append(Message(idx=idx, role="user" if role == "user" else "assistant", text=text))
        idx += 1
    return out
