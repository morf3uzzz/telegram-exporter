"""
ExportMessage — иммутабельное представление одного сообщения Telegram.

Используется как промежуточный формат между Telethon-объектом и экспортёрами.
Все поля — базовые Python-типы, нет зависимостей от Telethon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MediaType(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"
    AUDIO = "audio"
    VOICE = "voice"
    VIDEO_NOTE = "video_note"
    DOCUMENT = "document"
    STICKER = "sticker"
    ANIMATION = "animation"


@dataclass(frozen=True)
class ReactionItem:
    emoji: str
    count: int

    def to_dict(self) -> dict:
        return {"emoji": self.emoji, "count": self.count}


@dataclass(frozen=True)
class LinkItem:
    url: str
    text: Optional[str] = None

    def to_dict(self) -> dict:
        d: dict = {"url": self.url}
        if self.text and self.text != self.url:
            d["text"] = self.text
        return d


@dataclass(frozen=True)
class PollAnswer:
    text: str
    voters: Optional[int]

    def to_dict(self) -> dict:
        return {"text": self.text, "voters": self.voters}


@dataclass(frozen=True)
class PollData:
    question: str
    answers: tuple[PollAnswer, ...]
    total_voters: Optional[int]

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answers": [a.to_dict() for a in self.answers],
            "total_voters": self.total_voters,
        }


@dataclass(frozen=True)
class ExportMessage:
    # Обязательные поля
    id: int
    type: str                       # "message" | "service"
    date: str                       # ISO 8601

    # Отправитель
    from_name: Optional[str] = None
    from_username: Optional[str] = None
    from_id: Optional[int] = None

    # Контент
    text: str = ""

    # Метаданные
    links: tuple[LinkItem, ...] = field(default_factory=tuple)
    views: Optional[int] = None
    forwards: Optional[int] = None

    # Треды и топики
    reply_to_message_id: Optional[int] = None
    topic_id: Optional[int] = None
    is_topic_message: bool = False
    is_forum_topic: Optional[bool] = None
    topic_title: Optional[str] = None

    # Форвард
    forwarded_from: Optional[str] = None

    # Реакции и опросы
    reactions: tuple[ReactionItem, ...] = field(default_factory=tuple)
    poll: Optional[PollData] = None

    # Медиа (заполняется после загрузки)
    media_type: Optional[MediaType] = None
    media_path: Optional[str] = None   # локальный путь после скачивания
    media_mime: Optional[str] = None

    # Транскрипция (заполняется после обработки)
    transcription: Optional[str] = None

    def to_dict(self) -> dict:
        """Сериализует в dict, совместимый с текущим JSON-форматом экспорта."""
        d: dict = {
            "id": self.id,
            "type": self.type,
            "date": self.date,
        }

        if self.from_name is not None:
            d["from"] = self.from_name
        if self.from_username is not None:
            d["from_username"] = self.from_username
        if self.from_id is not None:
            d["from_id"] = self.from_id

        d["text"] = self.text

        if self.links:
            d["links"] = [lnk.to_dict() for lnk in self.links]
        if self.views is not None:
            d["views"] = self.views
        if self.forwards is not None:
            d["forwards"] = self.forwards
        if self.reply_to_message_id is not None:
            d["reply_to_message_id"] = self.reply_to_message_id
        if self.topic_id is not None:
            d["topic_id"] = self.topic_id
        if self.is_topic_message:
            d["is_topic_message"] = True
        if self.is_forum_topic is not None:
            d["is_forum_topic"] = self.is_forum_topic
        if self.topic_title:
            d["topic_title"] = self.topic_title
        if self.forwarded_from:
            d["forwarded_from"] = self.forwarded_from
        if self.reactions:
            d["reactions"] = [r.to_dict() for r in self.reactions]
        if self.poll is not None:
            d["poll"] = self.poll.to_dict()
        if self.media_type is not None:
            d["media_type"] = self.media_type.value
        if self.media_path:
            d["media_path"] = self.media_path
        if self.transcription:
            d["transcription"] = self.transcription

        return d

    def with_media(self, path: str, media_type: MediaType, mime: Optional[str] = None) -> "ExportMessage":
        """Возвращает новый экземпляр с заполненными медиа-полями."""
        import dataclasses
        return dataclasses.replace(self, media_path=path, media_type=media_type, media_mime=mime)

    def with_transcription(self, text: str) -> "ExportMessage":
        """Возвращает новый экземпляр с транскрипцией."""
        import dataclasses
        return dataclasses.replace(self, transcription=text)
