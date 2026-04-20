"""Minimal sync Gemini web client — replaces the gemini-webapi dependency.

Implements just what parlai needs: list_chats, read_chat, search.
Auth via __Secure-1PSID + __Secure-1PSIDTS cookies. No streaming, no writes.

Frame parser is adapted from gemini-webapi's parsing.py (BSD-licensed).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from parlai import log

BASE = "https://gemini.google.com"
APP = f"{BASE}/app"
BATCHEXEC = f"{BASE}/_/BardChatUi/data/batchexecute"

# RPC IDs (from HAR + gemini-webapi constants)
RPC_LIST_CHATS = "MaZiqc"
RPC_READ_CHAT = "hNvQHb"
RPC_SEARCH = "unqWSc"

_LENGTH_LINE = re.compile(r"(\d+)\n")

# Constant headers Gemini's web client sends
_BATCH_HEADERS = {
    "x-goog-ext-525001261-jspb": "[1,null,null,null,null,null,null,null,[4]]",
    "x-goog-ext-73010989-jspb": "[0]",
    "X-Same-Domain": "1",
    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    "Origin": BASE,
    "Referer": f"{BASE}/",
}


@dataclass
class ChatInfo:
    cid: str
    title: str
    timestamp: float = 0.0  # unix seconds


@dataclass
class ChatTurn:
    role: str
    text: str


@dataclass
class ChatHistory:
    cid: str
    turns: list[ChatTurn] = field(default_factory=list)


@dataclass
class SearchResult:
    cid: str
    title: str
    snippet: str = ""


class GeminiClient:
    def __init__(self, psid: str, psidts: str, *, timeout: float = 30.0) -> None:
        if not psid:
            raise ValueError("Missing __Secure-1PSID cookie")
        self._cookies = {"__Secure-1PSID": psid}
        if psidts:
            self._cookies["__Secure-1PSIDTS"] = psidts
        self._http = httpx.Client(
            cookies=self._cookies,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
                ),
            },
            timeout=timeout,
            follow_redirects=True,
        )
        self._access_token: str | None = None
        self._build_label: str | None = None
        self._session_id: str | None = None
        self._language: str = "en"
        self._reqid = 100000

    def init(self) -> None:
        """Scrape the per-session tokens from /app's HTML."""
        r = self._http.get(APP)
        r.raise_for_status()
        text = r.text
        self._access_token = _re1(r'"SNlM0e":\s*"(.*?)"', text)
        self._build_label = _re1(r'"cfb2h":\s*"(.*?)"', text)
        self._session_id = _re1(r'"FdrFJe":\s*"(.*?)"', text)
        lang = _re1(r'"TuX5cc":\s*"(.*?)"', text)
        if lang:
            self._language = lang
        if not self._access_token:
            raise RuntimeError(
                "Could not find SNlM0e token in /app — cookies may be expired"
            )
        log.warn(f"gemini init ok (bl={self._build_label!r} sid={self._session_id!r})")

    def _ensure_init(self) -> None:
        if self._access_token is None:
            self.init()

    def _batch_execute(
        self, rpcid: str, payload: list, *, source_path: str = "/app"
    ) -> list:
        """Invoke one RPC and return parsed top-level frames."""
        self._ensure_init()
        self._reqid += 100000
        params = {
            "rpcids": rpcid,
            "source-path": source_path,
            "f.sid": self._session_id or "",
            "bl": self._build_label or "",
            "hl": self._language,
            "_reqid": self._reqid,
            "rt": "c",
        }
        body = {
            "f.req": json.dumps([[[rpcid, json.dumps(payload), None, "generic"]]]),
            "at": self._access_token,
        }
        r = self._http.post(
            BATCHEXEC, params=params, data=body, headers=_BATCH_HEADERS
        )
        if r.status_code != 200:
            raise RuntimeError(f"Gemini {rpcid} returned HTTP {r.status_code}")
        return _parse_frames(r.text)

    # -------- High-level operations --------

    def list_chats(self, recent: int = 30) -> list[ChatInfo]:
        """Two-call list (matches upstream): one for recent, one for archived."""
        out: dict[str, ChatInfo] = {}
        for flag in ([1, None, 1], [0, None, 1]):
            try:
                frames = self._batch_execute(RPC_LIST_CHATS, [recent, None, flag])
            except Exception as e:
                log.warn(f"list_chats RPC failed: {e}")
                continue
            for f in frames:
                if not (isinstance(f, list) and f and f[0] == "wrb.fr"):
                    continue
                inner = json.loads(f[2])
                chats = _nested(inner, [2])
                if not isinstance(chats, list):
                    continue
                for entry in chats:
                    if not (isinstance(entry, list) and len(entry) > 1):
                        continue
                    cid = entry[0]
                    title = entry[1] or ""
                    ts_raw = _nested(entry, [5])
                    ts = 0.0
                    if isinstance(ts_raw, list) and len(ts_raw) >= 2:
                        ts = float(ts_raw[0]) + float(ts_raw[1]) / 1e9
                    if cid and cid not in out:
                        out[cid] = ChatInfo(cid=cid, title=title, timestamp=ts)
        # Most recent first
        return sorted(out.values(), key=lambda c: c.timestamp, reverse=True)

    def read_chat(self, cid: str, limit: int = 200) -> ChatHistory | None:
        """Fetch full conversation history (newest turn first per upstream)."""
        try:
            frames = self._batch_execute(
                RPC_READ_CHAT, [cid, limit, None, 1, [1], [4], None, 1]
            )
        except Exception as e:
            log.warn(f"read_chat RPC failed: {e}")
            return None

        for f in frames:
            if not (isinstance(f, list) and f and f[0] == "wrb.fr"):
                continue
            inner = json.loads(f[2])
            turns_data = _nested(inner, [0])
            if not isinstance(turns_data, list):
                continue
            history = ChatHistory(cid=cid)
            for conv_turn in turns_data:
                # Model turn first (newest format puts model output before user)
                candidates = _nested(conv_turn, [3, 0])
                if isinstance(candidates, list):
                    text_parts: list[str] = []
                    for cand in candidates:
                        text = _extract_candidate_text(cand)
                        if text:
                            text_parts.append(text)
                    if text_parts:
                        history.turns.append(
                            ChatTurn(role="model", text="\n".join(text_parts))
                        )
                # User turn
                user_text = _nested(conv_turn, [2, 0, 0])
                if isinstance(user_text, str) and user_text:
                    history.turns.append(ChatTurn(role="user", text=user_text))
            return history
        return None

    def search(self, query: str) -> list[SearchResult]:
        """Search across all conversations (Gemini's `unqWSc` rpc)."""
        try:
            frames = self._batch_execute(
                RPC_SEARCH, [query], source_path="/search"
            )
        except Exception as e:
            log.warn(f"search RPC failed: {e}")
            return []
        out: list[SearchResult] = []
        for f in frames:
            if not (isinstance(f, list) and f and f[0] == "wrb.fr"):
                continue
            inner = json.loads(f[2])
            results = inner[0] if isinstance(inner, list) and inner else []
            if not isinstance(results, list):
                continue
            for entry in results:
                head = _nested(entry, [0])
                if not (isinstance(head, list) and len(head) >= 2):
                    continue
                cid, title = head[0], head[1]
                snippet = ""
                # entry[2] sometimes holds [[type_int, "snippet text"], ...]
                chunks = _nested(entry, [2])
                if isinstance(chunks, list):
                    for ch in chunks:
                        if isinstance(ch, list) and len(ch) >= 2 and isinstance(ch[1], str):
                            snippet = ch[1]
                            break
                out.append(SearchResult(cid=cid, title=title or "", snippet=snippet))
        return out

    def close(self) -> None:
        self._http.close()


# -------- Helpers --------

def _re1(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _nested(obj: Any, path: list, default: Any = None) -> Any:
    """Walk a deeply nested list/dict by path; return default on miss."""
    cur = obj
    for key in path:
        try:
            cur = cur[key]
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def _extract_candidate_text(cand: Any) -> str:
    """Pull plain text from a Gemini candidate node."""
    text = _nested(cand, [1, 0])
    if isinstance(text, str):
        return text
    parts = _nested(cand, [1])
    if isinstance(parts, list):
        out: list[str] = []
        for p in parts:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, list) and p and isinstance(p[0], str):
                out.append(p[0])
        if out:
            return "\n".join(out)
    return ""


def _parse_frames(text: str) -> list:
    """Decode Google's length-prefixed framing protocol.

    Format: `)]}'\n\n<length>\n<json>\n<length>\n<json>...`
    Length is in UTF-16 code units (JavaScript `String.length`).
    """
    content = text
    if content.startswith(")]}'"):
        content = content[4:]
    pos = 0
    n = len(content)
    out: list = []
    while pos < n:
        while pos < n and content[pos].isspace():
            pos += 1
        if pos >= n:
            break
        m = _LENGTH_LINE.match(content, pos=pos)
        if not m:
            break
        length = int(m.group(1))
        # Important: start counting from immediately AFTER the digits (before the \n).
        # Google's length count includes that newline, and .strip() below removes it.
        body_start = m.start() + len(m.group(1))
        body_end = _utf16_advance(content, body_start, length)
        chunk = content[body_start:body_end].strip()
        pos = body_end
        if not chunk:
            continue
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, list):
                out.extend(parsed)
            else:
                out.append(parsed)
        except json.JSONDecodeError as e:
            log.warn(f"frame parse failed: {e}")
    return out


def _utf16_advance(s: str, start: int, units: int) -> int:
    """Advance through `s` from `start` until we've consumed `units` UTF-16 code units."""
    i = start
    consumed = 0
    n = len(s)
    while i < n and consumed < units:
        cp = ord(s[i])
        consumed += 2 if cp >= 0x10000 else 1
        i += 1
    return i
