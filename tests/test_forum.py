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


if __name__ == "__main__":
    unittest.main()


def test_pagination_stops_if_offset_does_not_advance():
    """
    Страховка от бесконечного цикла: если сервер отдаёт полную страницу, но
    offset не двигается (аномальный id=0), пагинация обязана остановиться,
    а не крутиться вечно.
    """
    from tg_exporter.core import forum as forum_mod

    class _Stuck:
        id = 0
        title = None
        top_message = 0

    class _Res:
        def __init__(self):
            self.topics = [_Stuck(), _Stuck(), _Stuck()]
            self.count = 100

    calls = {"n": 0}

    def client(_req):
        calls["n"] += 1
        if calls["n"] > 50:
            raise AssertionError("пагинация зациклилась: offset не сдвигается")
        return _Res()

    forum_mod.get_forum_topics(client, object(), page_limit=3)
    assert calls["n"] <= 50
