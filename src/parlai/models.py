from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    idx: int
    role: str  # 'user' | 'assistant' | 'tool' | 'system'
    text: str
    created_at: int | None = None  # unix ms


@dataclass
class Conversation:
    provider: str
    id: str
    title: str | None
    url: str | None
    created_at: int | None  # unix ms
    updated_at: int | None
    messages: list[Message] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
