"""Smoke tests for provider response parsers using fixture payloads.

Each fixture is a real HAR-derived response shape, stripped of any user PII.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

from parlai.providers.aistudio import _parse_chunks
from parlai.providers.chatgpt import _walk_mapping
from parlai.providers.perplexity import _maybe_b64_decode

FIX = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# ChatGPT mapping walker
# ---------------------------------------------------------------------------
def test_chatgpt_walk_mapping_orders_messages_root_to_leaf():
    payload = {
        "current_node": "c",
        "mapping": {
            "a": {
                "id": "a",
                "parent": None,
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["hello"]},
                    "create_time": 1700000000,
                },
            },
            "b": {
                "id": "b",
                "parent": "a",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["hi back"]},
                    "create_time": 1700000001,
                },
            },
            "c": {
                "id": "c",
                "parent": "b",
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["thanks"]},
                    "create_time": 1700000002,
                },
            },
        },
    }
    msgs = _walk_mapping(payload)
    assert [m.role for m in msgs] == ["user", "assistant", "user"]
    assert [m.text for m in msgs] == ["hello", "hi back", "thanks"]
    assert [m.idx for m in msgs] == [0, 1, 2]


def test_chatgpt_skips_empty_and_editable_context():
    payload = {
        "current_node": "b",
        "mapping": {
            "a": {
                "id": "a",
                "parent": None,
                "message": {
                    "author": {"role": "system"},
                    "content": {"content_type": "model_editable_context"},
                },
            },
            "b": {
                "id": "b",
                "parent": "a",
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["only me"]},
                },
            },
        },
    }
    msgs = _walk_mapping(payload)
    assert len(msgs) == 1 and msgs[0].text == "only me"


# ---------------------------------------------------------------------------
# Perplexity base64 envelope
# ---------------------------------------------------------------------------
def test_perplexity_base64_decodes_to_dict():
    inner = {"status": "success", "entries": [{"thread_title": "x"}]}
    encoded = base64.b64encode(json.dumps(inner).encode()).decode()
    out = _maybe_b64_decode(encoded)
    assert out == inner


def test_perplexity_passes_through_dicts_unchanged():
    obj = {"status": "ok", "entries": []}
    assert _maybe_b64_decode(obj) is obj


# ---------------------------------------------------------------------------
# AI Studio chunk parser (from gemini-convo logic)
# ---------------------------------------------------------------------------
def test_aistudio_parse_chunks_user_then_model():
    data = {
        "chunkedPrompt": {
            "chunks": [
                {"role": "user", "text": "hi"},
                {"role": "model", "text": "hello"},
                {"role": "model", "isThought": True, "text": "(thinking)"},
                {"role": "model", "parts": [{"text": "answer"}]},
            ]
        }
    }
    msgs = _parse_chunks(data)
    assert len(msgs) == 3
    assert msgs[0].role == "user" and msgs[0].text == "hi"
    assert msgs[1].role == "assistant" and msgs[1].text == "hello"
    # thinking is skipped
    assert msgs[2].text == "answer"


def test_aistudio_drive_doc_attachment_becomes_placeholder():
    data = {
        "chunkedPrompt": {
            "chunks": [
                {"role": "user", "driveDocument": {"id": "abc123"}},
                {"role": "model", "text": "got it"},
            ]
        }
    }
    msgs = _parse_chunks(data)
    assert "[attached Drive document: abc123]" in msgs[0].text


# ---------------------------------------------------------------------------
# Claude Code JSONL parser (uses real file shape)
# ---------------------------------------------------------------------------
def test_claude_code_parses_jsonl_session(tmp_path):
    from parlai.providers.claude_code import ClaudeCodeProvider

    sess_dir = tmp_path / "-Users-test-Downloads"
    sess_dir.mkdir()
    sess_file = sess_dir / "abc-123.jsonl"
    sess_file.write_text(
        "\n".join(
            [
                json.dumps({"type": "custom-title", "customTitle": "Test conv"}),
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-04-19T20:00:00.000Z",
                        "message": {"role": "user", "content": "hello"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-04-19T20:00:01.000Z",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "hi back"}],
                        },
                    }
                ),
            ]
        )
    )

    with patch("parlai.providers.claude_code.ROOT", tmp_path):
        p = ClaudeCodeProvider()
        conv = p.get("abc-123")
    assert conv.title == "Test conv"
    assert len(conv.messages) == 2
    assert conv.messages[0].text == "hello"
    assert conv.messages[1].text == "hi back"
