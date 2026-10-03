"""
MarkdownExporter — запись сообщений в Markdown с разбивкой по словам.

Особенности:
  - Разбивает на файлы по words_per_file слов
  - Поддерживает топики форумов (индекс + комментарии)
  - Собирает «популярные» сообщения по реакциям
  - Под сообщением с медиа пишет строку «📎 …» с путём к файлу; альбом
    (несколько фото/видео одним постом) выводится одним блоком: подпись + файлы
  - НЕТ UTF-8 BOM (был в оригинале — убираем, он не нужен для UTF-8)
"""

from __future__ import annotations

import re
import datetime
from typing import Optional

from .base import BaseExporter, sanitize_filename
from ..models.message import ExportMessage, MediaType
from ..models.config import MarkdownSettings


class MarkdownExporter(BaseExporter):
    """
    Создаёт файлы {chat_name}_part_1.md, _part_2.md, ...

    Дополнительно (опционально):
      - {chat_name}_popular.md  — сообщения с >= min_reactions реакций
    """

    def __init__(
        self,
        settings: Optional[MarkdownSettings] = None,
        popular_min_reactions: int = 0,  # 0 = отключено
    ) -> None:
        super().__init__()
        self._settings = settings or MarkdownSettings()
        self._popular_min = popular_min_reactions

        # Буферы
        self._current_chunk = ""
        self._current_words = 0
        self._chunks: list[str] = []           # готовые чанки
        self._popular: list[tuple[str, int]] = []  # (rendered, total_reactions)

        # Форумные топики
        self._topic_map: dict[str, str] = {}           # topic_id → title
        self._service_topic_by_id: dict[int, str] = {} # msg_id → title
        self._has_topics = False

        # Сообщения текущего альбома (общий grouped_id) — копятся до его конца.
        self._album: list[ExportMessage] = []

        self._md_prefix = ""

    def _open(self) -> None:
        self._md_prefix = _sanitize_md_filename(self._chat_name)
        self._current_chunk = ""
        self._current_words = 0
        self._chunks = []
        self._popular = []
        self._topic_map = {}
        self._service_topic_by_id = {}
        self._has_topics = False
        self._album = []

    def write(self, msg: ExportMessage) -> None:
        # Сервисные сообщения — обновляем топик-карту, не пишем в MD
        if msg.type == "service":
            if msg.topic_title and msg.topic_id is not None:
                tid = str(msg.topic_id)
                self._topic_map[tid] = msg.topic_title
                self._service_topic_by_id[msg.id] = msg.topic_title
                self._has_topics = True
            return

        # Альбом в Telegram — это несколько отдельных сообщений подряд; подпись
        # обычно только у одного. Выводим их одним блоком, иначе фото
        # «отрываются» от текста, к которому относятся.
        if msg.grouped_id is not None:
            if self._album and self._album[0].grouped_id != msg.grouped_id:
                self._flush_album()
            self._album.append(msg)
            return

        self._flush_album()
        self._write_block(msg, [msg])

    def _flush_album(self) -> None:
        if not self._album:
            return
        items, self._album = self._album, []
        # Заголовок, реакции и ссылки берём у сообщения с подписью.
        primary = next((m for m in items if m.text), items[0])
        self._write_block(primary, items)

    def _write_block(self, msg: ExportMessage, items: list[ExportMessage]) -> None:
        # Определяем топик сообщения
        topic_id_str = self._resolve_topic_id(msg)
        topic_comment = ""
        if self._has_topics and topic_id_str:
            topic_comment = _build_topic_comment(topic_id_str, self._topic_map)

        # Форматируем сообщение
        rendered = _format_message(msg, self._settings, items)
        if topic_comment:
            rendered = f"{topic_comment}{rendered}"

        if not rendered:
            return

        # Разбивка по словам
        msg_words = len(rendered.split())
        if (
            self._current_words + msg_words > self._settings.words_per_file
            and self._current_chunk.strip()
        ):
            self._flush_chunk()

        self._current_chunk += rendered + "\n\n"
        self._current_words += msg_words

        # Популярные сообщения
        if self._popular_min > 0 and msg.reactions:
            total_r = sum(r.count for r in msg.reactions)
            if total_r >= self._popular_min:
                self._popular.append((rendered, total_r))

    def finalize(self) -> list[str]:
        self._flush_album()
        # Сброс последнего чанка
        if self._current_chunk.strip():
            self._flush_chunk()

        # Формируем все файлы
        topics_index = _build_topics_index(self._topic_map) if self._has_topics else ""

        # Первый файл: индекс топиков + первый чанк
        first_parts = []
        if topics_index:
            first_parts.append(topics_index)
        if self._chunks:
            first_parts.append(self._chunks[0])
        if first_parts:
            self._write_md(1, "\n\n".join(first_parts).strip())

        # Остальные чанки
        for i, chunk in enumerate(self._chunks[1:], start=2):
            self._write_md(i, chunk)

        # Популярные
        if self._popular:
            self._write_popular()

        return self.output_files

    def close(self) -> None:
        """Отмена — ничего не пишем."""
        pass

    # ---- Internal ----

    def _flush_chunk(self) -> None:
        self._chunks.append(self._current_chunk.strip())
        self._current_chunk = ""
        self._current_words = 0

    def _write_md(self, index: int, content: str) -> None:
        if not content.strip():
            return
        path = self._path(f"{self._md_prefix}_part_{index}.md")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        with open(path, "w", encoding="utf-8") as f:
            f.write(normalized)
        self._register(path)

    def _write_popular(self) -> None:
        header = f"# Популярные сообщения (>= {self._popular_min} реакций)"
        blocks = [
            f"## Реакций: {count}\n\n{text}"
            for text, count in self._popular
        ]
        content = header + "\n\n" + "\n\n---\n\n".join(blocks) if blocks else header
        path = self._path(f"{self._md_prefix}_popular.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content.replace("\r\n", "\n").replace("\r", "\n"))
        self._register(path)

    def _resolve_topic_id(self, msg: ExportMessage) -> Optional[str]:
        raw = msg.topic_id
        if raw is None and msg.reply_to_message_id in self._service_topic_by_id:
            raw = msg.reply_to_message_id
        if raw is None:
            return None
        s = str(raw).strip()
        if s:
            if msg.topic_title:
                self._topic_map[s] = msg.topic_title
            return s
        return None


# ---- Форматирование сообщений ----

def _format_message(
    msg: ExportMessage,
    s: MarkdownSettings,
    items: Optional[list[ExportMessage]] = None,
) -> str:
    """`items` — сообщения одного блока (альбом); по умолчанию само `msg`."""
    items = items or [msg]
    parts = []

    if s.include_timestamps and msg.date:
        parts.append(f"[{_format_timestamp(msg.date, s.date_format)}]")

    if s.include_author and msg.from_name:
        name = msg.from_name
        if msg.from_username:
            name = f"{name} (@{msg.from_username})"
        parts.append(f"{name}:" if s.plain_text else f"**{name}**:")

    header = " ".join(parts).strip()

    body = _process_text("\n\n".join(m.text for m in items if m.text), s.plain_text)

    extras: list[str] = []

    media = _format_media(items, s.plain_text)
    if media:
        extras.append(media)

    if msg.links:
        link_lines = []
        for link in msg.links:
            if link.text and link.text != link.url:
                link_lines.append(f"[{link.text}]({link.url})")
            else:
                link_lines.append(link.url)
        if link_lines:
            has_text_links = any(lnk.text for lnk in msg.links)
            all_in_body = all(lnk.url in body for lnk in msg.links if not lnk.text)
            if has_text_links or not all_in_body:
                extras.append("🔗 " + " | ".join(link_lines))

    if s.include_polls and msg.poll:
        extras.append(_format_poll(msg.poll))

    if s.include_reactions and msg.reactions:
        parts_r = [
            f"{r.emoji}×{r.count}" if r.emoji else f"реакция×{r.count}"
            for r in msg.reactions
        ]
        extras.append(f"Реакции: {' · '.join(parts_r)}")

    if msg.views is not None or msg.forwards is not None:
        stats = []
        if msg.views is not None:
            stats.append(f"👁 {msg.views}")
        if msg.forwards is not None:
            stats.append(f"↗ {msg.forwards}")
        if stats:
            extras.append(" | ".join(stats))

    if s.include_forwarded and msg.forwarded_from:
        body = f"> Переслано от {msg.forwarded_from}\n{body}"

    if s.include_replies and msg.reply_to_message_id:
        body = f"↪ ответ на сообщение #{msg.reply_to_message_id}\n{body}"

    if msg.transcription:
        # Помечаем распознанный из голоса/видео текст: он сгенерирован
        # автоматически (Whisper/Deepgram) и может содержать ошибки —
        # пользователю важно отличать его от написанного человеком.
        extras.append(f"🎤 *Расшифровка голосового:* {msg.transcription}")

    combined = "\n\n".join([body, *extras]).strip()
    if header:
        return f"{header}\n{combined}".strip()
    return combined


_MEDIA_LABELS = {
    MediaType.PHOTO: "Фото",
    MediaType.VIDEO: "Видео",
    MediaType.VIDEO_NOTE: "Видеокружок",
    MediaType.VOICE: "Голосовое",
    MediaType.AUDIO: "Аудио",
    MediaType.DOCUMENT: "Документ",
    MediaType.ANIMATION: "GIF",
    MediaType.STICKER: "Стикер",
}


def _format_media(items: list[ExportMessage], plain_text: bool) -> str:
    """
    Строки «📎 Фото: media/photo/….jpg» — связь сообщения с его файлами.
    Для альбома — нумерованный список под общей строкой.
    """
    lines = [
        _media_entry(m, plain_text) for m in items if m.media_type is not None
    ]
    if not lines:
        return ""
    if len(lines) == 1:
        return f"📎 {lines[0]}"
    numbered = [f"{i}. {line}" for i, line in enumerate(lines, 1)]
    return "\n".join([f"📎 Альбом — вложений: {len(lines)}", *numbered])


def _media_entry(msg: ExportMessage, plain_text: bool) -> str:
    label = _MEDIA_LABELS.get(msg.media_type, "Файл")
    path = msg.media_path
    if not path:
        # Стикеры не скачиваются никогда — пометка «не скачан» была бы шумом.
        return label if msg.media_type == MediaType.STICKER else f"{label} (файл не скачан)"
    if plain_text:
        return f"{label}: {path}"
    return f"{label}: [{path.rsplit('/', 1)[-1]}]({path})"


def _format_timestamp(value: str, date_format: str) -> str:
    try:
        date_str = value.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(date_str)
        fmt_map = {
            "DD.MM.YYYY": "%d.%m.%Y",
            "YYYY-MM-DD": "%Y-%m-%d",
            "MM/DD/YYYY": "%m/%d/%Y",
        }
        fmt = fmt_map.get(date_format, "%d.%m.%Y")
        return dt.strftime(f"{fmt} %H:%M")
    except Exception:
        return value


def _process_text(value: str, plain_text: bool) -> str:
    if not plain_text:
        return value
    # Убираем markdown-разметку
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", value)
    text = text.replace("**", "").replace("*", "").replace("`", "")
    text = re.sub(r" {2,}", " ", text)
    return text


def _format_poll(poll) -> str:
    lines = [f"Опрос: {poll.question}" if poll.question else "Опрос"]
    for i, answer in enumerate(poll.answers, 1):
        lines.append(f"{i}. {answer.text} — {answer.voters}")
    lines.append(f"Всего голосов: {poll.total_voters}")
    return "\n".join(lines)


def _build_topic_comment(topic_id: str, topic_map: dict[str, str]) -> str:
    title = topic_map.get(topic_id, "")
    if not title:
        return f"<!-- topic_id={topic_id} -->\n"
    # Внутри HTML-комментария запрещена последовательность "--". Вместо
    # подмены em-dash'ем (искажает исходный текст) вставляем zero-width-space
    # между дефисами: визуально идентично, парсером HTML комментарий валиден.
    safe = str(title).replace("--", "-\u200b-").replace('"', '\\"')
    # Комментарий также не должен заканчиваться на "-"
    safe = safe.strip().rstrip("-") or "(без названия)"
    return f'<!-- topic_id={topic_id}; topic_title="{safe}" -->\n'


def _build_topics_index(topic_map: dict[str, str]) -> str:
    items = [
        (tid, title.strip())
        for tid, title in topic_map.items()
        if title and str(title).strip()
    ]
    if not items:
        return ""
    items.sort(key=lambda x: (int(x[0]) if x[0].isdigit() else 10**9, x[1]))
    lines = [
        f"{i}. {title} (topic_id={tid})"
        for i, (tid, title) in enumerate(items, 1)
    ]
    return "# Темы чата (" + str(len(items)) + ")\n\n" + "\n".join(lines)


def _sanitize_md_filename(value: str) -> str:
    cleaned = sanitize_filename(value).replace(" ", "_")
    return cleaned if cleaned else "Telegram_Chat"
