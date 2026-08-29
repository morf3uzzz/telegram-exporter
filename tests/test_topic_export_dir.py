"""
Имя директории экспорта топика должно быть уникальным.

Регрессия: два разных топика с одинаковым названием («Общее» в разных
ветках форума), экспортированные в одну и ту же секунду, давали одно и то
же имя папки — второй топик затирал первый. Пакетный экспорт топиков
делает этот случай штатным, поэтому в имя добавлен topic_id.
"""

from __future__ import annotations

from tg_exporter.core.orchestrator import build_export_dirname


def test_same_title_different_topics_give_different_dirs():
    ts = "2026-06-08_12-00-00"
    a = build_export_dirname("Форум", topic_id=1, topic_title="Общее", timestamp=ts)
    b = build_export_dirname("Форум", topic_id=2, topic_title="Общее", timestamp=ts)
    assert a != b


def test_topic_id_present_in_dirname():
    name = build_export_dirname("Форум", topic_id=42, topic_title="Общее",
                                timestamp="2026-06-08_12-00-00")
    assert "42" in name


def test_chat_without_topic_keeps_old_format():
    """Обычный чат (без топика) — имя как раньше, без суффикса topic."""
    name = build_export_dirname("Чат", topic_id=None, topic_title=None,
                                timestamp="2026-06-08_12-00-00")
    assert name == "Чат_2026-06-08_12-00-00"


def test_unsafe_title_is_sanitized():
    name = build_export_dirname("Чат", topic_id=7, topic_title="../../etc/passwd",
                                timestamp="2026-06-08_12-00-00")
    assert "/" not in name and ".." not in name


def test_title_missing_but_topic_id_set():
    """Топик без названия всё равно должен различаться по id."""
    a = build_export_dirname("Ф", topic_id=1, topic_title=None, timestamp="T")
    b = build_export_dirname("Ф", topic_id=2, topic_title=None, timestamp="T")
    assert a != b
