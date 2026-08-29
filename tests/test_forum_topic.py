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
