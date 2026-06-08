# Экспорт по топикам форумных супергрупп — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать пользователю выбрать конкретные топики форум-супергруппы (или «Все») и выгрузить каждый отдельно.

**Architecture:** Нижние слои (orchestrator/converter/exporters) уже умеют экспортировать один топик по `ExportTask.topic_id`. Достраиваем: raw-API получение списка топиков (`core/forum.py`), чистый primitive очереди (`core/export_queue.py`), UI-выбор в `ExportModal`, и последовательную оркестрацию топиков в `App` (по образцу folder-export, но изолированно — folder с его спец-режимами не трогаем).

**Tech Stack:** Python 3.14, Telethon 1.43.2 (raw `functions.messages.GetForumTopicsRequest`), customtkinter 5.2.2, unittest + MagicMock.

**Спека:** `docs/superpowers/specs/2026-06-08-topics-export-design.md`

**Дока Telethon:** `docs.search_docs library=telethon version=1.43.2`; raw API — context7 `/websites/tl_telethon_dev`.

---

## File Structure

| Файл | Создать/Править | Ответственность |
|---|---|---|
| `tg_exporter/models/forum_topic.py` | создать | `ForumTopic` dataclass — чистые данные топика (id, title, closed, hidden) |
| `tg_exporter/core/forum.py` | создать | `is_forum(entity)`, `get_forum_topics(client, entity)` — единственное место с raw forum-API |
| `tg_exporter/core/export_queue.py` | создать | `BatchJob` + `ExportQueue` — чистый primitive последовательной очереди (без UI/сети) |
| `tests/test_forum.py` | создать | тесты forum.py (мок client) |
| `tests/test_export_queue.py` | создать | тесты ExportQueue/BatchJob |
| `tg_exporter/ui/views/export_modal.py` | править | секция выбора топиков (async-загрузка) + `get_selected_topics()` + ветка запуска |
| `tg_exporter/ui/app.py` | править | `load_forum_topics`, `_bg_load_topics`, события, `start_topics_export`, `_export_next_topic` |

Backend (`orchestrator.py`, `converter.py`, `exporters/`, `export_task.py`) — **НЕ трогаем**.

---

## Task 1: Модель ForumTopic

**Files:**
- Create: `tg_exporter/models/forum_topic.py`
- Test: `tests/test_forum_topic.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_forum_topic.py
import unittest
from tg_exporter.models.forum_topic import ForumTopic


class TestForumTopic(unittest.TestCase):
    def test_fields_and_defaults(self):
        t = ForumTopic(id=42, title="Bug reports")
        self.assertEqual(t.id, 42)
        self.assertEqual(t.title, "Bug reports")
        self.assertFalse(t.closed)
        self.assertFalse(t.hidden)

    def test_is_frozen(self):
        t = ForumTopic(id=1, title="x")
        with self.assertRaises(Exception):
            t.id = 2  # frozen dataclass


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_forum_topic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tg_exporter.models.forum_topic'`

- [ ] **Step 3: Реализация**

```python
# tg_exporter/models/forum_topic.py
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
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_forum_topic.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tg_exporter/models/forum_topic.py tests/test_forum_topic.py
git commit -m "feat(topics): модель ForumTopic"
```

---

## Task 2: forum.is_forum

**Files:**
- Create: `tg_exporter/core/forum.py`
- Test: `tests/test_forum.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_forum.py
import types
import unittest
from unittest.mock import MagicMock

from tg_exporter.core import forum


class TestIsForum(unittest.TestCase):
    def test_true_when_forum_flag_set(self):
        entity = types.SimpleNamespace(forum=True)
        self.assertTrue(forum.is_forum(entity))

    def test_false_when_flag_false(self):
        entity = types.SimpleNamespace(forum=False)
        self.assertFalse(forum.is_forum(entity))

    def test_false_when_attr_missing(self):
        entity = types.SimpleNamespace()  # обычный чат/юзер — нет .forum
        self.assertFalse(forum.is_forum(entity))

    def test_false_when_entity_none(self):
        self.assertFalse(forum.is_forum(None))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_forum.py -v`
Expected: FAIL — `ImportError`/`AttributeError: module 'tg_exporter.core.forum' has no attribute 'is_forum'`

- [ ] **Step 3: Реализация (только is_forum пока)**

```python
# tg_exporter/core/forum.py
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
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_forum.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tg_exporter/core/forum.py tests/test_forum.py
git commit -m "feat(topics): forum.is_forum — детект форум-супергруппы"
```

---

## Task 3: forum.get_forum_topics (raw API + пагинация + пропуск удалённых)

**Files:**
- Modify: `tg_exporter/core/forum.py`
- Test: `tests/test_forum.py`

Контекст API (сверено с tl.telethon.dev):
`client(functions.messages.GetForumTopicsRequest(peer=entity, offset_date=None, offset_id=0, offset_topic=0, limit=100, q=None))`
→ возвращает `messages.ForumTopics` с `.count:int` и `.topics: Vector<ForumTopic>`.
`ForumTopic` имеет `.id`, `.title`, `.closed`, `.hidden`. `ForumTopicDeleted` — только `.id` (нет `.title`) → пропускаем.

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_forum.py — ДОБАВИТЬ к существующему файлу

class _FakeTopics:
    """Имитация messages.ForumTopics из GetForumTopicsRequest."""

    def __init__(self, topics, count):
        self.topics = topics
        self.count = count


def _topic(tid, title, closed=False, hidden=False):
    return types.SimpleNamespace(id=tid, title=title, closed=closed, hidden=hidden)


def _deleted(tid):
    # ForumTopicDeleted: только id, без title
    return types.SimpleNamespace(id=tid)


class TestGetForumTopics(unittest.TestCase):
    def test_single_page(self):
        client = MagicMock()
        client.return_value = _FakeTopics(
            topics=[_topic(1, "General"), _topic(2, "Bug reports", closed=True)],
            count=2,
        )
        result = forum.get_forum_topics(client, entity="E")
        self.assertEqual([(t.id, t.title, t.closed) for t in result],
                         [(1, "General", False), (2, "Bug reports", True)])

    def test_skips_deleted(self):
        client = MagicMock()
        client.return_value = _FakeTopics(
            topics=[_topic(1, "General"), _deleted(99), _topic(2, "Флудильня")],
            count=3,
        )
        result = forum.get_forum_topics(client, entity="E")
        self.assertEqual([t.id for t in result], [1, 2])  # 99 пропущен

    def test_pagination_two_pages(self):
        client = MagicMock()
        page1 = _FakeTopics(topics=[_topic(i, f"t{i}") for i in range(1, 101)], count=150)
        page2 = _FakeTopics(topics=[_topic(i, f"t{i}") for i in range(101, 151)], count=150)
        client.side_effect = [page1, page2]
        result = forum.get_forum_topics(client, entity="E", page_limit=100)
        self.assertEqual(len(result), 150)
        # второй запрос должен сместиться: offset_topic = id последнего из page1 (100)
        second_request = client.call_args_list[1].args[0]
        self.assertEqual(second_request.offset_topic, 100)

    def test_stops_when_page_shorter_than_limit(self):
        client = MagicMock()
        client.return_value = _FakeTopics(topics=[_topic(1, "a"), _topic(2, "b")], count=999)
        result = forum.get_forum_topics(client, entity="E", page_limit=100)
        # вернулось 2 < 100 → пагинацию прекращаем, не зацикливаемся на count=999
        self.assertEqual(len(result), 2)
        self.assertEqual(client.call_count, 1)
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_forum.py -k GetForumTopics -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_forum_topics'`

- [ ] **Step 3: Реализация — добавить в forum.py**

```python
# tg_exporter/core/forum.py — ДОБАВИТЬ после is_forum

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
        offset_topic = getattr(last, "id", 0) or 0
        offset_id = getattr(last, "top_message", 0) or 0

    return collected
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_forum.py -v`
Expected: PASS (8 passed — 4 is_forum + 4 get_forum_topics)

- [ ] **Step 5: Commit**

```bash
git add tg_exporter/core/forum.py tests/test_forum.py
git commit -m "feat(topics): forum.get_forum_topics — raw API + пагинация + пропуск удалённых"
```

---

## Task 4: ExportQueue + BatchJob (чистый primitive очереди)

**Files:**
- Create: `tg_exporter/core/export_queue.py`
- Test: `tests/test_export_queue.py`

- [ ] **Step 1: Написать падающие тесты**

```python
# tests/test_export_queue.py
import types
import unittest

from tg_exporter.core.export_queue import BatchJob, ExportQueue, build_topic_jobs
from tg_exporter.models.forum_topic import ForumTopic


class TestBuildTopicJobs(unittest.TestCase):
    def test_one_job_per_topic(self):
        dialog = types.SimpleNamespace(id=10, name="Happ")
        topics = [ForumTopic(1, "General"), ForumTopic(2, "Bug reports")]
        jobs = build_topic_jobs(dialog, topics)
        self.assertEqual(len(jobs), 2)
        self.assertEqual([(j.topic_id, j.topic_title) for j in jobs],
                         [(1, "General"), (2, "Bug reports")])
        self.assertIs(jobs[0].dialog, dialog)
        self.assertEqual(jobs[0].label, "General")


class TestExportQueue(unittest.TestCase):
    def _jobs(self, n):
        return [BatchJob(dialog=None, topic_id=i, topic_title=f"t{i}", label=f"t{i}")
                for i in range(n)]

    def test_empty_has_no_next(self):
        q = ExportQueue([])
        self.assertFalse(q.has_next())
        self.assertEqual(q.total, 0)

    def test_next_advances_index(self):
        q = ExportQueue(self._jobs(2))
        self.assertTrue(q.has_next())
        j0 = q.next()
        self.assertEqual(j0.topic_id, 0)
        self.assertEqual(q.current_index, 1)
        j1 = q.next()
        self.assertEqual(j1.topic_id, 1)
        self.assertFalse(q.has_next())

    def test_record_counts_and_summary(self):
        q = ExportQueue(self._jobs(3))
        q.record(True)
        q.record(False)
        q.record(True)
        self.assertEqual(q.ok, 2)
        self.assertEqual(q.failed, 1)
        self.assertIn("2", q.summary())
        self.assertIn("1", q.summary())

    def test_summary_no_errors(self):
        q = ExportQueue(self._jobs(1))
        q.record(True)
        self.assertNotIn("ошиб", q.summary().lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_export_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tg_exporter.core.export_queue'`

- [ ] **Step 3: Реализация**

```python
# tg_exporter/core/export_queue.py
"""
Чистый primitive последовательного пакетного экспорта: очередь задач +
счётчики. Без UI, сети и Telethon — легко тестируется. Используется для
экспорта нескольких топиков (а потенциально и папок).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class BatchJob:
    """Одна задача пакета: какой диалог и (опц.) топик экспортировать."""

    dialog: object
    topic_id: Optional[int]
    topic_title: Optional[str]
    label: str


def build_topic_jobs(dialog, topics) -> list[BatchJob]:
    """Список задач: по одной на каждый выбранный топик одного форума."""
    return [
        BatchJob(dialog=dialog, topic_id=t.id, topic_title=t.title, label=t.title)
        for t in topics
    ]


class ExportQueue:
    """Последовательная очередь BatchJob со счётчиками успехов/ошибок."""

    def __init__(self, jobs: list[BatchJob]) -> None:
        self._jobs = list(jobs)
        self._index = 0
        self.ok = 0
        self.failed = 0

    @property
    def total(self) -> int:
        return len(self._jobs)

    @property
    def current_index(self) -> int:
        return self._index

    def has_next(self) -> bool:
        return self._index < len(self._jobs)

    def next(self) -> BatchJob:
        job = self._jobs[self._index]
        self._index += 1
        return job

    def record(self, success: bool) -> None:
        if success:
            self.ok += 1
        else:
            self.failed += 1

    def summary(self) -> str:
        msg = f"Готово: {self.ok} успешно"
        if self.failed:
            msg += f", {self.failed} ошибок"
        return msg
```

- [ ] **Step 4: Запустить — убедиться, что проходит**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_export_queue.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add tg_exporter/core/export_queue.py tests/test_export_queue.py
git commit -m "feat(topics): ExportQueue + BatchJob — primitive пакетной очереди"
```

---

## Task 5: App — загрузка топиков в фоне + события

**Files:**
- Modify: `tg_exporter/ui/app.py`

Импорты, фоновую загрузку и обработчики событий. UI-glue — проверяется запуском (Task 8).

- [ ] **Step 1: Добавить импорты**

В `tg_exporter/ui/app.py` после строки `from ..core.profiles import ProfileManager, Profile` добавить:

```python
from ..core import forum
from ..core.export_queue import ExportQueue, build_topic_jobs
```

- [ ] **Step 2: Инициализировать состояние топик-пакета**

В `App.__init__`, рядом с блоком folder-состояния (после `self._folder_dates = ...`), добавить:

```python
        # Состояние пакетного экспорта топиков форума (аналог folder, но изолирован).
        self._topic_queue: Optional[ExportQueue] = None
        self._topic_active: bool = False
        self._topic_base: Optional[str] = None
        self._topic_options: Optional[dict] = None
```

- [ ] **Step 3: Публичный метод загрузки топиков + фоновая задача**

Добавить методы в класс `App` (рядом с `show_export_dialog`):

```python
    def load_forum_topics(self, dialog, modal) -> None:
        """Грузит список топиков форума в фоне для модалки экспорта."""
        self._worker.submit(self._bg_load_topics, dialog, modal)

    def _bg_load_topics(self, dialog, modal) -> None:
        try:
            c = self._client_mgr.ensure_connected()
            topics = forum.get_forum_topics(c, dialog.entity)
            self._worker.put_event("topics_loaded", (modal, topics))
        except Exception as exc:
            logger.error("load_forum_topics failed", exc=exc)
            self._worker.put_event("topics_load_failed", (modal, str(exc)))
```

- [ ] **Step 4: Зарегистрировать события**

В `_register_handlers`, после строки `d.on("proxy_test_result", self._on_proxy_test_result)` добавить:

```python
        d.on("topics_loaded",      self._on_topics_loaded)
        d.on("topics_load_failed", self._on_topics_load_failed)
        d.on("topic_progress",     self._on_topic_progress)
        d.on("topic_done",         self._on_topic_done)
```

- [ ] **Step 5: Обработчики событий**

Добавить методы в `App` (рядом с другими `_on_*`):

```python
    def _on_topics_loaded(self, payload) -> None:
        modal, topics = payload
        try:
            modal.set_topics(topics)
        except Exception:
            pass

    def _on_topics_load_failed(self, payload) -> None:
        modal, message = payload
        try:
            modal.set_topics_error(message or "Не удалось загрузить топики")
        except Exception:
            pass

    def _on_topic_progress(self, payload) -> None:
        current, total, title = payload
        if self._active_export_modal:
            self._active_export_modal.on_topic_progress(current, total, title)

    def _on_topic_done(self, payload) -> None:
        export_dir, ok, failed = payload
        if self._active_export_modal:
            self._active_export_modal.on_topic_batch_done(export_dir, ok, failed)
```

- [ ] **Step 6: Проверить, что приложение импортируется без ошибок**

Run: `./.venv/Scripts/python.exe -c "import tg_exporter.ui.app"`
Expected: без ошибок (exit 0)

- [ ] **Step 7: Commit**

```bash
git add tg_exporter/ui/app.py
git commit -m "feat(topics): App — фоновая загрузка топиков и события"
```

---

## Task 6: App — оркестрация экспорта топиков

**Files:**
- Modify: `tg_exporter/ui/app.py`

- [ ] **Step 1: Метод запуска пакета топиков**

Добавить в `App`:

```python
    def start_topics_export(self, dialog, output_path: str, modal, topics) -> None:
        """Запускает последовательный экспорт выбранных топиков форума."""
        import datetime, os
        from ..exporters.base import sanitize_filename

        if not topics:
            self._worker.put_event("error", "Не выбран ни один топик.")
            return

        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base = os.path.join(output_path, f"{sanitize_filename(dialog.name or 'forum')}_{ts}")
        os.makedirs(base, exist_ok=True)

        self._topic_queue = ExportQueue(build_topic_jobs(dialog, topics))
        self._topic_active = True
        self._topic_base = base
        self._topic_options = modal.get_export_options()
        self._active_export_modal = modal
        self._export_next_topic()
```

- [ ] **Step 2: Шаг очереди**

Добавить в `App`:

```python
    def _export_next_topic(self) -> None:
        q = self._topic_queue
        if not self._topic_active or q is None or not q.has_next():
            if q is not None:
                self._worker.put_event("topic_done", (self._topic_base, q.ok, q.failed))
            self._topic_active = False
            self._topic_queue = None
            return

        job = q.next()
        self._worker.put_event("topic_progress", (q.current_index, q.total, job.topic_title))

        opts = self._topic_options or {}
        self._token = CancellationToken()
        task = ExportTask(
            chat_id=getattr(job.dialog, "id", 0),
            chat_name=job.dialog.name or "Chat",
            output_path=self._topic_base,
            format=opts.get("format", ExportFormat.BOTH),
            date_from=opts.get("date_from"),
            date_to=opts.get("date_to"),
            topic_id=job.topic_id,
            topic_title=job.topic_title,
            download_media=opts.get("download_media", False),
            collect_analytics=opts.get("collect_analytics", False),
            transcribe_audio=opts.get("transcribe_audio", False),
            transcription_provider=self.config.transcription_provider,
            transcription_language=self.config.transcription_language,
            local_whisper_model=self.config.local_whisper_model,
            deepgram_api_key=self.credentials.load_deepgram_key() or "",
            author_filter=AuthorFilter(),
            words_per_file=opts.get("words_per_file", 50_000),
        )
        progress = ExportProgress()
        deepgram_key = self.credentials.load_deepgram_key()
        orch = ExportOrchestrator(self._client_mgr, self.config, self._history, deepgram_key)
        token = self._token
        self._worker.submit(
            orch.run, job.dialog, task, token, progress,
            lambda etype, payload: self._worker.put_event(etype, payload),
        )
```

- [ ] **Step 2b: Проверить, что нужные имена импортированы**

`ExportTask`, `ExportProgress`, `ExportFormat`, `AuthorFilter` импортируются в app.py строкой `from ..models.export_task import ...` (проверить наличие `AuthorFilter` — он уже импортируется), `ExportOrchestrator` и `CancellationToken` тоже. Запустить:

Run: `./.venv/Scripts/python.exe -c "import tg_exporter.ui.app"`
Expected: без ошибок

- [ ] **Step 3: Встроить продолжение очереди в `_on_export_done`**

Заменить начало метода `_on_export_done` так, чтобы топик-пакет вёл к следующему топику и НЕ показывал модалке «готово» после каждого:

Найти:
```python
    def _on_export_done(self, payload) -> None:
        import os, shutil
        export_dir, files = payload
        if self._active_export_modal:
            self._active_export_modal.on_export_done(export_dir, files)
```
Заменить на:
```python
    def _on_export_done(self, payload) -> None:
        import os, shutil
        export_dir, files = payload
        # Топик-пакет: ведём к следующему топику, финал покажет topic_done.
        if self._topic_active:
            self._topic_queue.record(True)
            self._export_next_topic()
            return
        if self._active_export_modal:
            self._active_export_modal.on_export_done(export_dir, files)
```

- [ ] **Step 4: Встроить продолжение в `_on_export_error`**

Найти:
```python
    def _on_export_error(self, msg: str) -> None:
        if self._active_export_modal:
            self._active_export_modal.on_export_error(msg)
```
Заменить на:
```python
    def _on_export_error(self, msg: str) -> None:
        if self._topic_active:
            self._topic_queue.record(False)
            self._export_next_topic()
            return
        if self._active_export_modal:
            self._active_export_modal.on_export_error(msg)
```

- [ ] **Step 5: Отмена пакета в `cancel_export` и `_on_export_cancelled`**

Найти:
```python
    def cancel_export(self) -> None:
        self._token.cancel()
        self._folder_active = False
```
Заменить на:
```python
    def cancel_export(self) -> None:
        self._token.cancel()
        self._folder_active = False
        self._topic_active = False
        self._topic_queue = None
```

Найти:
```python
    def _on_export_cancelled(self, _) -> None:
        if self._active_export_modal:
            self._active_export_modal.on_export_cancelled()
        self._folder_active = False
```
Заменить на:
```python
    def _on_export_cancelled(self, _) -> None:
        if self._active_export_modal:
            self._active_export_modal.on_export_cancelled()
        self._folder_active = False
        self._topic_active = False
        self._topic_queue = None
```

- [ ] **Step 6: Проверить импорт**

Run: `./.venv/Scripts/python.exe -c "import tg_exporter.ui.app"`
Expected: без ошибок

- [ ] **Step 7: Commit**

```bash
git add tg_exporter/ui/app.py
git commit -m "feat(topics): App — последовательная оркестрация экспорта топиков"
```

---

## Task 7: ExportModal — секция выбора топиков

**Files:**
- Modify: `tg_exporter/ui/views/export_modal.py`

- [ ] **Step 1: Импорт forum + детект в конце `_build`**

В начало файла добавить импорт (рядом с другими `from ...`):
```python
from ...core import forum
```

В конце метода `_build`, перед строкой `self._add_section(scroll, "Период")` (т.е. сразу после заголовка-имени чата), вставить секцию топиков:

```python
        # ---- Топики форума (только для форум-супергрупп) ----
        self._topic_vars: dict[int, tk.BooleanVar] = {}
        self._topics: list = []
        self._topics_section = ctk.CTkFrame(scroll, fg_color="transparent")
        entity = getattr(self._dialog, "entity", None)
        if forum.is_forum(entity):
            self._topics_section.pack(fill="x", padx=0, pady=0)
            self._add_section(self._topics_section, "Топики форума")
            self._topics_all_var = tk.BooleanVar(value=True)
            self._topics_all_cb = ctk.CTkCheckBox(
                self._topics_section, text="Все топики", variable=self._topics_all_var,
                font=font(13, "bold"), text_color=C["text"], command=self._on_topics_all,
            )
            self._topics_all_cb.pack(anchor="w", padx=SPACING["xl"], pady=(0, SPACING["xs"]))
            self._topics_list_frame = ctk.CTkFrame(self._topics_section, fg_color="transparent")
            self._topics_list_frame.pack(fill="x", padx=SPACING["xl"])
            self._topics_status = ctk.CTkLabel(
                self._topics_list_frame, text="Загрузка топиков…",
                font=font(12), text_color=C["text_sec"], anchor="w",
            )
            self._topics_status.pack(anchor="w")
            self._app.load_forum_topics(self._dialog, self)
```

- [ ] **Step 2: Методы управления списком топиков**

Добавить в класс `ExportModal` (в секцию `# ---- Called by App ----`):

```python
    def set_topics(self, topics) -> None:
        """Отрисовывает чекбоксы топиков (вызывается из App после загрузки)."""
        self._topics = list(topics)
        for w in self._topics_list_frame.winfo_children():
            w.destroy()
        self._topic_vars = {}
        if not self._topics:
            ctk.CTkLabel(self._topics_list_frame, text="Топиков не найдено",
                         font=font(12), text_color=C["text_sec"]).pack(anchor="w")
            return
        for t in self._topics:
            var = tk.BooleanVar(value=True)
            self._topic_vars[t.id] = var
            label = t.title + ("  🔒" if getattr(t, "closed", False) else "")
            ctk.CTkCheckBox(
                self._topics_list_frame, text=label, variable=var,
                font=font(12), text_color=C["text"],
                command=self._on_topic_toggle, checkbox_width=16, checkbox_height=16,
            ).pack(anchor="w", pady=1)

    def set_topics_error(self, message: str) -> None:
        for w in self._topics_list_frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(self._topics_list_frame, text=f"Ошибка загрузки топиков: {message}",
                     font=font(12), text_color=C["error"], wraplength=520,
                     anchor="w").pack(anchor="w")

    def _on_topics_all(self) -> None:
        val = self._topics_all_var.get()
        for var in self._topic_vars.values():
            var.set(val)

    def _on_topic_toggle(self) -> None:
        # Если сняли хоть один — «Все» гаснет; если все стоят — «Все» зажигается.
        if self._topic_vars:
            all_on = all(v.get() for v in self._topic_vars.values())
            self._topics_all_var.set(all_on)

    def get_selected_topics(self) -> list:
        """Выбранные ForumTopic (для форума). Для обычного чата — []."""
        if not self._topic_vars:
            return []
        chosen = {tid for tid, v in self._topic_vars.items() if v.get()}
        return [t for t in self._topics if t.id in chosen]
```

- [ ] **Step 3: Прогресс/финал пакета топиков**

Добавить в класс `ExportModal`:

```python
    def on_topic_progress(self, current: int, total: int, title: str) -> None:
        self._progress.start(f"Топик {current}/{total}: {title}", None)

    def on_topic_batch_done(self, export_dir: str, ok: int, failed: int) -> None:
        self._export_dir = export_dir
        self._exporting = False
        self._progress.finish()
        msg = f"✓ Топиков: {ok} успешно"
        if failed:
            msg += f", {failed} ошибок"
        self._result_lbl.configure(text=f"{msg}\n{export_dir}", text_color=C["success"])
        self._start_btn.configure(state="normal", text="Экспортировать ещё")
        self._open_btn.configure(state="normal")
```

- [ ] **Step 4: Ветка запуска в `_on_start`**

Найти в `_on_start` конец метода:
```python
        self._progress.start(getattr(self._dialog, "name", "Чат"), None)
        self._result_lbl.configure(text="")
        self._open_btn.configure(state="disabled")
        self._app.start_export(self._dialog, path, self)
```
Заменить на:
```python
        self._result_lbl.configure(text="")
        self._open_btn.configure(state="disabled")
        selected = self.get_selected_topics()
        if selected:
            self._progress.start(f"Топиков: {len(selected)}", None)
            self._app.start_topics_export(self._dialog, path, self, selected)
        else:
            self._progress.start(getattr(self._dialog, "name", "Чат"), None)
            self._app.start_export(self._dialog, path, self)
```

- [ ] **Step 5: Проверить импорт модалки**

Run: `./.venv/Scripts/python.exe -c "import tg_exporter.ui.views.export_modal"`
Expected: без ошибок

- [ ] **Step 6: Commit**

```bash
git add tg_exporter/ui/views/export_modal.py
git commit -m "feat(topics): ExportModal — выбор топиков (чекбоксы + «Все»)"
```

---

## Task 8: Прогон всех тестов + ручная верификация в приложении

**Files:** —

- [ ] **Step 1: Весь автотест-набор зелёный**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: все тесты PASS (новые + существующие; регрессий нет)

- [ ] **Step 2: Ручная верификация (форум-чат)**

Запустить приложение, войти, открыть экспорт форум-супергруппы (напр. «Happ»):

Run: `./.venv/Scripts/python.exe main.py`

Проверить вручную:
1. В модалке экспорта форума появляется секция «Топики форума» со списком и чекбоксом «Все».
2. Снять «Все», выбрать 2 топика → «Экспортировать» → выбрать папку.
3. Прогресс идёт по топикам («Топик 1/2: …», «Топик 2/2: …»).
4. По завершении — папка `<Форум>_<timestamp>/`, внутри подпапки на каждый выбранный топик с непустыми JSON/MD.
5. Обычный (не форум) чат: секции топиков нет, экспорт работает как раньше.

- [ ] **Step 3: Verify-скилл (опционально, но рекомендуется)**

Использовать skill `verify` для прогона приложения и снятия скриншота модалки с топиками как доказательства.

- [ ] **Step 4: Финальный commit (если были правки по итогам верификации)**

```bash
git add -A
git commit -m "test(topics): верификация экспорта топиков в приложении"
```

---

## Заметки для исполнителя

- **Запуск Python всегда через venv:** `./.venv/Scripts/python.exe` (в системном нет customtkinter).
- **Не трогать** `orchestrator.py`/`converter.py`/`exporters/` — они уже поддерживают `topic_id`.
- **Folder-export не трогаем** — у него спец-режимы (merge .md), изолирован намеренно; топики используют отдельный `ExportQueue`.
- **Raw API**: при сомнениях в полях `GetForumTopicsRequest`/`ForumTopic` — context7 `/websites/tl_telethon_dev`, не выдумывать.
