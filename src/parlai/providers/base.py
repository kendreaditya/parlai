from __future__ import annotations

from typing import Iterator, Protocol

from parlai.models import Conversation


class SearchHit(dict):
    """{provider, id, title, url, snippet, updated_at}"""


class Provider(Protocol):
    name: str

    def authed(self) -> bool: ...

    def list(self, limit: int = 100) -> Iterator[dict]:
        """Yield lightweight conversation summaries (id, title, updated_at)."""
        ...

    def get(self, conv_id: str) -> Conversation:
        """Fetch a full conversation including messages."""
        ...

    def search(self, query: str, limit: int = 25) -> list[SearchHit]:
        """Provider-native search. Empty list if not supported."""
        ...

    def url_for(self, conv_id: str) -> str | None:
        """Deep link to the conversation in the provider's web UI."""
        ...
