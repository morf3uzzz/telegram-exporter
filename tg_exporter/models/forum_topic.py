"""ForumTopic — топик форум-супергруппы. Чистые данные, без Telethon."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ForumTopic:
    """Один топик форума. `id` — это top_msg_id, используется как reply_to."""

    id: int
    title: str
    closed: bool = False
    hidden: bool = False
