from __future__ import annotations

from parlai.providers.aistudio import AIStudioProvider
from parlai.providers.base import Provider
from parlai.providers.chatgpt import ChatGPTProvider
from parlai.providers.claude import ClaudeProvider
from parlai.providers.claude_code import ClaudeCodeProvider
from parlai.providers.codex import CodexCLIProvider, CodexDesktopProvider
from parlai.providers.gemini import GeminiProvider
from parlai.providers.perplexity import PerplexityProvider

REGISTRY: dict[str, type[Provider]] = {
    "chatgpt": ChatGPTProvider,
    "claude": ClaudeProvider,
    "claude-code": ClaudeCodeProvider,
    "codex-cli": CodexCLIProvider,
    "codex-desktop": CodexDesktopProvider,
    "gemini": GeminiProvider,
    "aistudio": AIStudioProvider,
    "perplexity": PerplexityProvider,
}


def get(name: str) -> Provider:
    name = name.lower()
    if name not in REGISTRY:
        raise KeyError(f"Unknown provider: {name}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name]()


def all_names() -> list[str]:
    return sorted(REGISTRY)
