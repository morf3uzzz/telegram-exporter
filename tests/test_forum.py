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
