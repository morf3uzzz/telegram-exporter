"""
Классификация чата по типу — для колонки «Тип» и фильтров в списке чатов.

Тип определяется по флагам Telethon-сущности (entity) диалога. Модуль чистый
(без импортов Telethon) — работает через getattr, поэтому легко тестируется
на моках и не тянет сеть.

Типы:
  user    — личный чат (User, не бот)
  bot     — бот (User с bot=True)
  group   — группа (basic Chat или супергруппа-megagroup без forum)
  forum   — форум-супергруппа (Channel megagroup с forum=True)
  channel — канал (Channel broadcast)
"""

from __future__ import annotations

KIND_USER = "user"
KIND_BOT = "bot"
KIND_GROUP = "group"
KIND_FORUM = "forum"
KIND_CHANNEL = "channel"

# Порядок — он же порядок чекбоксов в фильтре.
KIND_ORDER = (KIND_USER, KIND_BOT, KIND_GROUP, KIND_FORUM, KIND_CHANNEL)

KIND_LABELS = {
    KIND_USER: "Личный",
    KIND_BOT: "Бот",
    KIND_GROUP: "Группа",
    KIND_FORUM: "Форум",
    KIND_CHANNEL: "Канал",
}


def chat_kind(dialog) -> str:
    """Тип чата по диалогу (или напрямую по entity). Один из KIND_ORDER.

    Принимает как Telethon Dialog (есть .entity и .is_user/.is_group/.is_channel),
    так и саму entity. Порядок проверок важен: бот → канал → форум → группа,
    т.к. forum — частный случай megagroup, а bot — частный случай User.
    """
    entity = getattr(dialog, "entity", None)
    if entity is None:
        entity = dialog

    # Бот — это User с флагом bot.
    if getattr(entity, "bot", False):
        return KIND_BOT

    # Channel: broadcast → канал; megagroup+forum → форум; megagroup → группа.
    if getattr(entity, "broadcast", False):
        return KIND_CHANNEL
    if getattr(entity, "forum", False):
        return KIND_FORUM
    if getattr(entity, "megagroup", False) or getattr(entity, "gigagroup", False):
        return KIND_GROUP

    # Подсказки от Telethon Dialog, если флагов сущности не хватило.
    if getattr(dialog, "is_user", False):
        return KIND_USER
    if getattr(dialog, "is_channel", False) or getattr(dialog, "is_group", False):
        return KIND_GROUP

    # Только entity, без обёртки Dialog: User имеет first_name/phone, Chat — title.
    if getattr(entity, "first_name", None) is not None or getattr(entity, "phone", None) is not None:
        return KIND_USER
    if getattr(entity, "title", None) is not None:
        return KIND_GROUP
    return KIND_USER
