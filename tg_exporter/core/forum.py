"""
Forum-топики Telegram (raw API). Единственный модуль, который знает про
GetForumTopicsRequest. Downstream работает с моделью ForumTopic.

Дока: context7 /websites/tl_telethon_dev — messages.GetForumTopicsRequest,
конструктор forumTopic#fcdad815, forumTopicDeleted#023f109b (только id).
"""

from __future__ import annotations

from telethon import functions

from ..models.forum_topic import ForumTopic


def is_forum(entity) -> bool:
    """True, если entity — форум-супергруппа (Channel с флагом forum)."""
    return bool(getattr(entity, "forum", False))


def get_forum_topics(client, entity, *, page_limit: int = 100) -> list[ForumTopic]:
    """
    Список топиков форума через raw API с пагинацией. Удалённые топики
    (ForumTopicDeleted — без .title) пропускаются. Выполнять в worker-потоке.
    """
    collected: list[ForumTopic] = []
    offset_date = None
    offset_id = 0
    offset_topic = 0

    while True:
        result = client(functions.messages.GetForumTopicsRequest(
            peer=entity,
            offset_date=offset_date,
            offset_id=offset_id,
            offset_topic=offset_topic,
            limit=page_limit,
            q=None,
        ))
        batch = list(getattr(result, "topics", None) or [])
        if not batch:
            break

        for raw in batch:
            title = getattr(raw, "title", None)
            if title is None:
                continue  # ForumTopicDeleted — пропускаем
            collected.append(ForumTopic(
                id=raw.id,
                title=str(title),
                closed=bool(getattr(raw, "closed", False)),
                hidden=bool(getattr(raw, "hidden", False)),
            ))

        total = getattr(result, "count", None)
        if len(batch) < page_limit:
            break
        if total is not None and len(collected) >= total:
            break

        last = batch[-1]
        next_topic = getattr(last, "id", 0) or 0
        next_id = getattr(last, "top_message", 0) or 0
        # Если offset не сдвинулся, сервер будет отдавать ту же страницу
        # бесконечно — выходим, вместо того чтобы крутиться вечно.
        if next_topic == offset_topic and next_id == offset_id:
            break
        offset_topic = next_topic
        offset_id = next_id

    return collected
