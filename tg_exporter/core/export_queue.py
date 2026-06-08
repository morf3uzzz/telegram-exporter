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
