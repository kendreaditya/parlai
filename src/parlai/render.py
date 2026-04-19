from parlai.models import Conversation


def to_markdown(conv: Conversation) -> str:
    lines: list[str] = []
    if conv.title:
        lines.append(f"# {conv.title}")
    lines.append(f"_{conv.provider} · {conv.id}_")
    if conv.url:
        lines.append(f"<{conv.url}>")
    lines.append("")
    for m in conv.messages:
        lines.append(f"## {m.role}")
        lines.append("")
        lines.append(m.text or "")
        lines.append("")
    return "\n".join(lines)
