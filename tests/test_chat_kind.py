# tests/test_chat_kind.py
import types
import unittest

from tg_exporter.core.chat_kind import chat_kind, KIND_LABELS, KIND_ORDER


def _ent(**flags):
    return types.SimpleNamespace(**flags)


def _dlg(entity=None, **dialog_flags):
    return types.SimpleNamespace(entity=entity, **dialog_flags)


class TestChatKind(unittest.TestCase):
    def test_bot(self):
        self.assertEqual(chat_kind(_dlg(_ent(bot=True))), "bot")

    def test_broadcast_channel(self):
        self.assertEqual(chat_kind(_dlg(_ent(broadcast=True, megagroup=False))), "channel")

    def test_forum(self):
        self.assertEqual(chat_kind(_dlg(_ent(megagroup=True, forum=True))), "forum")

    def test_supergroup_is_group(self):
        self.assertEqual(chat_kind(_dlg(_ent(megagroup=True, forum=False))), "group")

    def test_basic_group_via_dialog_flag(self):
        self.assertEqual(chat_kind(_dlg(_ent(title="Team"), is_group=True)), "group")

    def test_user_via_dialog_flag(self):
        self.assertEqual(chat_kind(_dlg(_ent(first_name="Bob"), is_user=True)), "user")

    def test_user_entity_only(self):
        self.assertEqual(chat_kind(_dlg(_ent(first_name="Bob"))), "user")

    def test_channel_not_forum_when_broadcast(self):
        self.assertEqual(chat_kind(_dlg(_ent(broadcast=True, forum=False))), "channel")

    def test_entity_passed_directly(self):
        self.assertEqual(chat_kind(_ent(broadcast=True)), "channel")

    def test_labels_cover_all_kinds(self):
        for k in KIND_ORDER:
            self.assertIn(k, KIND_LABELS)


if __name__ == "__main__":
    unittest.main()
