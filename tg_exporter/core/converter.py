"""
Конвертер Telethon-объектов в типизированные модели.

Единственный модуль, который знает про Telethon.
Все остальные сервисы работают только с ExportMessage.
"""

from __future__ import annotations

from typing import Optional

from telethon.utils import get_display_name

from ..models.message import (
    ExportMessage,
    LinkItem,
    ReactionItem,
    PollAnswer,
    PollData,
    MediaType,
)


def message_to_export(message) -> ExportMessage:
    """
    Конвертирует Telethon Message в ExportMessage.

    Все данные извлекаются здесь — downstream не видит Telethon.
    """
    msg_type = "service" if getattr(message, "action", None) else "message"

    from_name: Optional[str] = None
    from_username: Optional[str] = None
    if getattr(message, "sender", None):
        from_name = get_display_name(message.sender) or None
        from_username = getattr(message.sender, "username", None)

    raw_text = getattr(message, "raw_text", None)
    text_value = raw_text if raw_text is not None else getattr(message, "message", "")
    text = _normalize(text_value)

    # Топик/форум
    topic_id: Optional[int] = None
    is_topic_message = False
    is_forum_topic: Optional[bool] = None
    reply_to = getattr(message, "reply_to", None)
    if reply_to:
        top_id = (
            getattr(reply_to, "top_msg_id", None)
            or getattr(reply_to, "reply_to_top_id", None)
        )
        if top_id:
            topic_id = top_id
            is_topic_message = True
        forum_flag = getattr(reply_to, "forum_topic", None)
        if forum_flag is not None:
            is_forum_topic = bool(forum_flag)

    topic_title: Optional[str] = None
    if message.action and hasattr(message.action, "title"):
        topic_title = _normalize(getattr(message.action, "title", "")) or None

    # Реакции
    reactions = _build_reactions(message)

    # Опрос
    poll = _build_poll(message)

    # Ссылки
    links = _extract_links(message)

    # Медиа-тип (определяем без скачивания)
    media_type = _detect_media_type(message)

    # message.date иногда может быть None (редкие сервисные сообщения
        # или некорректные записи в old chats). Не роняем конвертацию.
    msg_date = getattr(message, "date", None)
    date_str = msg_date.isoformat() if msg_date is not None else ""

    return ExportMessage(
        id=message.id,
        type=msg_type,
        date=date_str,
        from_name=from_name,
        from_username=from_username,
        from_id=getattr(message, "sender_id", None),
        text=text,
        links=tuple(links),
        views=getattr(message, "views", None),
        forwards=getattr(message, "forwards", None),
        reply_to_message_id=getattr(message, "reply_to_msg_id", None),
        topic_id=topic_id,
        is_topic_message=is_topic_message,
        is_forum_topic=is_forum_topic,
        topic_title=topic_title,
        forwarded_from=_build_forwarded_from(getattr(message, "fwd_from", None)),
        reactions=tuple(reactions),
        poll=poll,
        media_type=media_type,
    )


# ---- Internal helpers ----

def _normalize(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "text"):
        return str(value.text)
    return str(value)


def _build_forwarded_from(fwd_from) -> Optional[str]:
    if not fwd_from:
        return None
    if getattr(fwd_from, "from_name", None):
        return fwd_from.from_name
    if getattr(fwd_from, "from_id", None):
        return f"from_id:{fwd_from.from_id}"
    if getattr(fwd_from, "channel_post", None):
        return f"channel_post:{fwd_from.channel_post}"
    return None


def _build_reactions(message) -> list[ReactionItem]:
    reactions_obj = getattr(message, "reactions", None)
    if not reactions_obj or not getattr(reactions_obj, "results", None):
        return []
    items = []
    for result in reactions_obj.results:
        reaction = result.reaction
        emoji = getattr(reaction, "emoticon", None) or str(reaction)
        items.append(ReactionItem(emoji=emoji, count=result.count))
    return items


def _build_poll(message) -> Optional[PollData]:
    media_poll = getattr(message, "poll", None)
    if not media_poll:
        return None
    poll = getattr(media_poll, "poll", None)
    if not poll:
        return None

    results_obj = getattr(media_poll, "results", None)
    answers = []
    for answer in getattr(poll, "answers", []) or []:
        count: Optional[int] = None
        if results_obj and getattr(results_obj, "results", None):
            for res in results_obj.results:
                if res.option == answer.option:
                    count = res.voters
                    break
        answers.append(PollAnswer(text=_normalize(answer.text), voters=count))

    total_voters: Optional[int] = None
    if results_obj and getattr(results_obj, "total_voters", None) is not None:
        total_voters = results_obj.total_voters

    return PollData(
        question=_normalize(poll.question),
        answers=tuple(answers),
        total_voters=total_voters,
    )


def _extract_links(message) -> list[LinkItem]:
    entities = getattr(message, "entities", None) or []
    raw = getattr(message, "raw_text", "") or ""
    links: list[LinkItem] = []
    seen: set[str] = set()

    for ent in entities:
        cls_name = type(ent).__name__
        if cls_name == "MessageEntityTextUrl":
            url = getattr(ent, "url", None)
            if url and url not in seen:
                label = (
                    raw[ent.offset : ent.offset + ent.length]
                    if ent.offset + ent.length <= len(raw)
                    else ""
                )
                links.append(
                    LinkItem(url=url, text=label if label and label != url else None)
                )
                seen.add(url)
        elif cls_name == "MessageEntityUrl":
            url = (
                raw[ent.offset : ent.offset + ent.length]
                if ent.offset + ent.length <= len(raw)
                else ""
            )
            if url and url not in seen:
                links.append(LinkItem(url=url))
                seen.add(url)

    return links


def _detect_media_type(message) -> Optional[MediaType]:
    if getattr(message, "sticker", None):
        return MediaType.STICKER
    if getattr(message, "photo", None):
        return MediaType.PHOTO
    if getattr(message, "voice", None):
        return MediaType.VOICE
    if getattr(message, "video_note", None):
        return MediaType.VIDEO_NOTE
    if getattr(message, "video", None):
        return MediaType.VIDEO
    if getattr(message, "audio", None):
        return MediaType.AUDIO
    if getattr(message, "gif", None):
        return MediaType.ANIMATION
    if getattr(message, "document", None):
        return MediaType.DOCUMENT
    return None
