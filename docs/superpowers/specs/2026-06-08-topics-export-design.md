# Дизайн: Экспорт по топикам форумных супергрупп

**Дата:** 2026-06-08
**Статус:** утверждён, готов к реализации
**Ветка:** `add-topics-export`
**Зависит от:** ничего нового; переиспользует паттерн «folder export» из `app.py`

## Цель

Дать пользователю экспортировать **форумные топики** супергрупп Telegram
(например группа «Happ» с топиками «Русский чат», «Bug reports», «Флудильня», …)
осмысленно:

- видеть, что выбранный чат — форум, и какие в нём топики;
- выбрать **конкретные топики** (мультиселект) и выгрузить только их;
- **либо** поставить «Все» и выгрузить все топики, каждый отдельно.

### Проблема сейчас

UI не различает форум и обычный чат. Для форума вызывается
`iter_messages(dialog)` без `reply_to` ([orchestrator.py:199](../../../tg_exporter/core/orchestrator.py))
→ Telethon отдаёт **все сообщения всех топиков вперемешку** в один файл.
Выбора топиков нет; не видно, что выгрузилось всё.

## Текущее состояние кода (что УЖЕ готово)

Нижние слои поддерживают экспорт **одного** топика по `topic_id` — нет только
доступа из UI и получения списка топиков:

| Слой | Статус | Где |
|---|---|---|
| `ExportTask.topic_id` / `topic_title` | ✅ | [export_task.py:72-74](../../../tg_exporter/models/export_task.py) |
| `iter_messages(reply_to=topic_id)` | ✅ | [orchestrator.py:176-178](../../../tg_exporter/core/orchestrator.py) |
| Имя папки/лейбл с топиком, подсчёт `_count_messages` | ✅ | [orchestrator.py:97-106, 346-365](../../../tg_exporter/core/orchestrator.py) |
| Вытаскивание топика из сообщения | ✅ | [converter.py:42-61](../../../tg_exporter/core/converter.py) |
| Запись топика в JSON/Markdown + тесты | ✅ | exporters, tests/ |

**Не хватает:** получить список топиков (нет `GetForumTopicsRequest` нигде),
детект форума, UI выбора, проброс `topic_id` из UI (сейчас всегда `None`,
[app.py:411-429](../../../tg_exporter/ui/app.py)), оркестрация нескольких топиков.

## Решения (зафиксированы с пользователем)

1. **A — структура вывода:** общая папка `<Форум>_<timestamp>/`, внутри —
   подпапка на каждый топик (orchestrator уже даёт суффикс `_topic_<title>`).
2. **B — «General»:** основной топик форума показываем в списке как обычный
   (его отдаёт `GetForumTopicsRequest`).
3. **C — «Все»:** все топики, **каждый в свой файл/папку** (не всё в один).
4. **Объём:** мультиселект топиков + «Все». Обычные опции (формат, даты,
   медиа, транскрипция) задаются один раз и применяются ко всем выбранным.
5. **YAGNI:** режим «весь форум одним файлом» отдельно НЕ добавляем — он уже
   существует как поведение по умолчанию (`topic_id=None`); не плодим опций.
   `converter`/exporters не трогаем.

## Архитектура

Новые/изменяемые единицы с чёткими границами:

| Компонент | Файл | Ответственность | Зависит от |
|---|---|---|---|
| Модель топика | `tg_exporter/models/forum_topic.py` (новый) | `@dataclass(frozen=True) ForumTopic{id:int(top_msg_id), title:str, closed:bool, count:Optional[int]}`. Чистые данные, без Telethon. | — |
| Сервис форумов | `tg_exporter/core/forum.py` (новый) | `is_forum(entity) -> bool` (по `getattr(entity,'forum',False)`); `get_forum_topics(client, entity) -> list[ForumTopic]` — `GetForumTopicsRequest` с пагинацией + конвертация. Всё знание Telethon про форумы — здесь. | Telethon, ForumTopic |
| Загрузка топиков (worker) | `tg_exporter/ui/app.py` (правка) | `_bg_load_topics(dialog)` в фоне → событие `topics_loaded`/`topics_load_failed`. | forum.py, client |
| Выбор топиков в UI | `tg_exporter/ui/views/export_modal.py` (правка) | Если чат — форум: секция со списком топиков (чекбоксы + скролл) и чекбоксом «Все»; статус «Загрузка топиков…» до прихода данных. `get_selected_topics() -> list[ForumTopic]`. | ForumTopic, theme |
| Пакетная оркестрация | `tg_exporter/ui/app.py` (правка) | Обобщить очередь folder-export в «пакет элементов»: элемент = `(dialog, topic_id, topic_title, label)`. Folder: `topic_id=None`. Topics: один dialog, разные `topic_id`. Последовательный прогон, события прогресса, общий итог. | orchestrator |
| Проброс в задачу | `tg_exporter/ui/app.py` (правка) | `start_export`/новый `start_topics_export` заполняют `ExportTask(topic_id, topic_title)`. | export_task |

### Поток данных

```
chats_page [клик «Экспортировать выбранный чат»]
  → App.show_export_dialog(dialog)
  → ExportModal(dialog):
       if forum.is_forum(dialog.entity):
           показать секцию топиков со статусом «Загрузка топиков…»
           App.load_topics(dialog) → worker.submit(_bg_load_topics, dialog)
[worker]
  topics = forum.get_forum_topics(client, dialog.entity)   # GetForumTopicsRequest + пагинация
    → topics_loaded (modal, topics)   | topics_load_failed (modal, msg)
[UI каждые 80мс]
  topics_loaded → modal.set_topics(topics)  → чекбоксы + «Все»

ExportModal [клик «Экспортировать»]
  selected = modal.get_selected_topics()      # [] для обычного чата
  if selected:
      App.start_topics_export(dialog, out, modal, selected)
        → очередь [(dialog, t.id, t.title) for t in selected]
        → _export_next_in_batch():  ExportTask(topic_id, topic_title) → orchestrator.run
        → batch_progress / batch_done (как folder_progress/folder_done)
  else:
      App.start_export(...)   # текущий путь, без изменений
```

### Новые UIEvent

| event_type | payload | Источник |
|---|---|---|
| `topics_loaded` | `(modal, list[ForumTopic])` | `_bg_load_topics` |
| `topics_load_failed` | `(modal, str)` | `_bg_load_topics` |
| `batch_progress` | `(current, total, label)` | `_export_next_in_batch` (обобщённый folder_progress) |
| `batch_done` | `(total)` | конец очереди |

> Решение по обобщению: текущие `folder_progress`/`folder_done` и
> `_export_next_in_folder` обобщаются в `batch_*`/`_export_next_in_batch`.
> Поведение folder-export сохраняется (покрыто тестами); topics — частный
> случай той же очереди. Это устраняет дублирование, а не плодит вторую очередь.

## Telethon API (сверить с context7 перед кодом)

`GetForumTopicsRequest` (raw, `telethon.tl.functions.channels`) — точные имена
полей и структуру ответа `messages.ForumTopics` (`.topics[]`, `.count`,
`ForumTopic.id/.title/.closed`, `ForumTopicDeleted`) сверяю с
context7 `/websites/telethon_dev_en_stable` и `/websites/tl_telethon_dev` (1.x).
Пагинация — через `offset_date/offset_id/offset_topic` из последнего топика.
**Не изобретать поля — брать из спеки 1.x.**

## Тесты (TDD, RED → GREEN)

Чистая логика — юнит-тесты на моках, без сети:

1. `forum.get_forum_topics`: мок `client(GetForumTopicsRequest)` → fake
   `messages.ForumTopics` → корректный `list[ForumTopic]`; пропуск
   `ForumTopicDeleted`; пагинация (две страницы → склейка).
2. `forum.is_forum`: entity с `forum=True/False/отсутствует` → bool.
3. Пакетная очередь: N выбранных топиков → N `ExportTask` с верными
   `topic_id`/`topic_title`; folder-режим (topic_id=None) не сломан.
4. `ExportModal.get_selected_topics`: выбор подмножества → их id; «Все» → все id;
   обычный чат → `[]`.

Backend-тесты на сам экспорт топика (orchestrator/exporters) уже есть —
переиспользуем, не дублируем.

## Риски

- **Сигнатура `GetForumTopicsRequest`** — снимается сверкой с context7 (1.x).
- **Обобщение folder-export** трогает рабочий код — закрывается тестами на
  оба режима (folder + topics) до изменения.
- **Большие форумы** (>100 топиков) — обязательна пагинация (учтено в тестах).
