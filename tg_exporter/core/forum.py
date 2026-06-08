"""
Forum-топики Telegram (raw API). Единственный модуль, который знает про
GetForumTopicsRequest. Downstream работает с моделью ForumTopic.

Дока: context7 /websites/tl_telethon_dev — messages.GetForumTopicsRequest,
конструктор forumTopic#fcdad815, forumTopicDeleted#023f109b (только id).
"""

from __future__ import annotations

from typing import Optional

from telethon import functions

from ..models.forum_topic import ForumTopic
from ..utils.logger import logger


def is_forum(entity) -> bool:
    """True, если entity — форум-супергруппа (Channel с флагом forum)."""
    return bool(getattr(entity, "forum", False))
