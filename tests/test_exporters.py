"""Tests for tg_exporter.exporters — JsonExporter and MarkdownExporter."""

import json
import os
import tempfile
import unittest

from tg_exporter.models.message import (
    ExportMessage, LinkItem, MediaType, ReactionItem, PollData, PollAnswer,
)
from tg_exporter.models.config import MarkdownSettings


def _msg(**kw) -> ExportMessage:
    defaults = dict(id=1, type="message", date="2024-06-15T10:00:00+00:00", text="Hello")
    defaults.update(kw)
    return ExportMessage(**defaults)


class TestJsonExporter(unittest.TestCase):

    def setUp(self):
        from tg_exporter.exporters.json_exporter import JsonExporter
        self.JsonExporter = JsonExporter
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, messages, **kw) -> dict:
        exp = self.JsonExporter(**kw)
        exp.open(self._tmpdir, "Test Chat")
        for msg in messages:
            exp.write(msg)
        files = exp.finalize()
        self.assertEqual(len(files), 1)
        with open(files[0], encoding="utf-8") as f:
            return json.load(f)

    def test_empty_export_is_valid_json(self):
        data = self._run([])
        self.assertEqual(data["name"], "Test Chat")
        self.assertEqual(data["messages"], [])

    def test_single_message_roundtrip(self):
        data = self._run([_msg(id=42, text="Привет")])
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["id"], 42)
        self.assertEqual(data["messages"][0]["text"], "Привет")

    def test_multiple_messages_order(self):
        msgs = [_msg(id=i, text=f"msg{i}") for i in range(1, 6)]
        data = self._run(msgs)
        ids = [m["id"] for m in data["messages"]]
        self.assertEqual(ids, [1, 2, 3, 4, 5])

    def test_topic_in_header(self):
        exp = self.JsonExporter()
        exp.open(self._tmpdir, "Forum", "General")
        exp.write(_msg(id=1))
        files = exp.finalize()
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["topic"], "General")

    def test_include_views_true(self):
        data = self._run([_msg(id=1, views=100, forwards=5)], include_views=True)
        self.assertEqual(data["messages"][0]["views"], 100)
        self.assertEqual(data["messages"][0]["forwards"], 5)

    def test_include_views_false_strips_stats(self):
        data = self._run([_msg(id=1, views=100, forwards=5)], include_views=False)
        self.assertNotIn("views", data["messages"][0])
        self.assertNotIn("forwards", data["messages"][0])

    def test_no_utf8_bom(self):
        exp = self.JsonExporter()
        exp.open(self._tmpdir, "Chat")
        exp.write(_msg())
        files = exp.finalize()
        with open(files[0], "rb") as f:
            raw = f.read(3)
        self.assertNotEqual(raw, b"\xef\xbb\xbf")

    def test_unicode_preserved(self):
        data = self._run([_msg(text="Тест: 日本語 🎉")])
        self.assertEqual(data["messages"][0]["text"], "Тест: 日本語 🎉")

    def test_reactions_serialised(self):
        msg = _msg(id=1, reactions=(ReactionItem(emoji="👍", count=3),))
        data = self._run([msg])
        self.assertEqual(data["messages"][0]["reactions"][0]["emoji"], "👍")

    def test_media_path_and_album_id_serialised(self):
        msg = _msg(grouped_id=42).with_media("media/photo/a_msg1.jpg", MediaType.PHOTO)
        data = self._run([msg])
        self.assertEqual(data["messages"][0]["media_path"], "media/photo/a_msg1.jpg")
        self.assertEqual(data["messages"][0]["grouped_id"], 42)

    def test_finalize_returns_registered_path(self):
        exp = self.JsonExporter()
        exp.open(self._tmpdir, "C")
        exp.write(_msg())
        files = exp.finalize()
        self.assertTrue(os.path.isfile(files[0]))

    def test_close_without_finalize_leaves_no_crash(self):
        """close() should not raise even without finalize."""
        exp = self.JsonExporter()
        exp.open(self._tmpdir, "C")
        exp.write(_msg())
        exp.close()  # no raise

    def test_context_manager_finalizes_on_success(self):
        exp = self.JsonExporter()
        with exp:
            exp.open(self._tmpdir, "Chat")
            exp.write(_msg())
        self.assertEqual(len(exp.output_files), 1)
        self.assertTrue(os.path.isfile(exp.output_files[0]))

    def test_context_manager_calls_close_on_error(self):
        """On exception, __exit__ calls close() not finalize()."""
        exp = self.JsonExporter()
        try:
            with exp:
                exp.open(self._tmpdir, "Chat")
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # File should exist but be in invalid state (no finalize was written)
        # The key check: no exception from the exporter itself
        self.assertIsNone(exp._file)


class TestMarkdownExporter(unittest.TestCase):

    def setUp(self):
        from tg_exporter.exporters.markdown_exporter import MarkdownExporter, _format_message
        self.MarkdownExporter = MarkdownExporter
        self._fmt = _format_message
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, messages, settings=None, popular_min=0) -> list[str]:
        exp = self.MarkdownExporter(settings=settings, popular_min_reactions=popular_min)
        exp.open(self._tmpdir, "Test Chat")
        for msg in messages:
            exp.write(msg)
        return exp.finalize()

    def _read(self, path: str) -> str:
        with open(path, encoding="utf-8") as f:
            return f.read()

    # --- Format helpers ---

    def test_format_message_text_only(self):
        s = MarkdownSettings(include_timestamps=False, include_author=False)
        result = self._fmt(_msg(text="Hello"), s)
        self.assertEqual(result, "Hello")

    def test_format_message_with_author(self):
        s = MarkdownSettings(include_timestamps=False, include_author=True)
        result = self._fmt(_msg(text="Hi", from_name="Alice"), s)
        self.assertIn("Alice", result)
        self.assertIn("Hi", result)

    def test_format_message_plain_text_strips_markdown(self):
        s = MarkdownSettings(plain_text=True, include_timestamps=False, include_author=False)
        result = self._fmt(_msg(text="**Bold** and `code`"), s)
        self.assertNotIn("**", result)
        self.assertNotIn("`", result)
        self.assertIn("Bold", result)

    def test_format_message_with_timestamp(self):
        s = MarkdownSettings(include_timestamps=True, include_author=False, date_format="YYYY-MM-DD")
        result = self._fmt(_msg(date="2024-06-15T10:00:00+00:00", text="Hi"), s)
        self.assertIn("2024-06-15", result)

    def test_format_message_with_reactions(self):
        s = MarkdownSettings(include_reactions=True, include_timestamps=False, include_author=False)
        msg = _msg(text="Hi", reactions=(ReactionItem(emoji="👍", count=5),))
        result = self._fmt(msg, s)
        self.assertIn("👍", result)
        self.assertIn("5", result)

    def test_format_message_with_poll(self):
        s = MarkdownSettings(include_polls=True, include_timestamps=False, include_author=False)
        poll = PollData(
            question="Что выбрать?",
            answers=(PollAnswer(text="A", voters=2), PollAnswer(text="B", voters=3)),
            total_voters=5,
        )
        result = self._fmt(_msg(text="", poll=poll), s)
        self.assertIn("Что выбрать?", result)
        self.assertIn("5", result)

    def test_format_message_with_forwarded(self):
        s = MarkdownSettings(include_forwarded=True, include_timestamps=False, include_author=False)
        msg = _msg(text="Hi", forwarded_from="Bob")
        result = self._fmt(msg, s)
        self.assertIn("Bob", result)

    def test_format_message_with_transcription(self):
        s = MarkdownSettings(include_timestamps=False, include_author=False)
        msg = _msg(text="", transcription="Привет мир")
        result = self._fmt(msg, s)
        # Распознанный текст помечается 🎤 + курсивом, чтобы отличать его
        # от написанного человеком (может содержать ошибки распознавания).
        self.assertIn("🎤", result)
        self.assertIn("*Расшифровка голосового:*", result)
        self.assertIn("Привет мир", result)

    # --- Медиа: привязка файла к сообщению ---

    def _plain(self, **kw):
        return MarkdownSettings(include_timestamps=False, include_author=False, **kw)

    def test_media_line_points_to_file(self):
        msg = _msg(text="Шаг 1").with_media("media/photo/2024-06-15_10-00-00_msg1.jpg", MediaType.PHOTO)
        result = self._fmt(msg, self._plain())
        self.assertIn("Шаг 1", result)
        self.assertIn("📎 Фото: media/photo/2024-06-15_10-00-00_msg1.jpg", result)

    def test_media_line_is_a_link_in_markdown_mode(self):
        msg = _msg(text="Шаг 1").with_media("media/video/v_msg1.mp4", MediaType.VIDEO)
        result = self._fmt(msg, self._plain(plain_text=False))
        self.assertIn("📎 Видео: [v_msg1.mp4](media/video/v_msg1.mp4)", result)

    def test_media_without_file_is_still_mentioned(self):
        # Медиа не скачивали (или не удалось) — читателю всё равно важно знать,
        # что в сообщении было вложение.
        result = self._fmt(_msg(text="", media_type=MediaType.PHOTO), self._plain())
        self.assertEqual(result, "📎 Фото (файл не скачан)")

    def test_sticker_is_mentioned_without_download_note(self):
        result = self._fmt(_msg(text="", media_type=MediaType.STICKER), self._plain())
        self.assertEqual(result, "📎 Стикер")

    def test_media_path_survives_plain_text_cleanup(self):
        # plain_text вычищает `*` и обратные кавычки из текста — путь трогать нельзя.
        msg = _msg(text="**жирный**").with_media("media/documents/d_msg1_a_b.pdf", MediaType.DOCUMENT)
        result = self._fmt(msg, self._plain())
        self.assertIn("media/documents/d_msg1_a_b.pdf", result)

    def _album(self, group=500, start=10, count=3, caption="1 — начало прогрева", caption_at=0):
        items = []
        for i in range(count):
            m = _msg(
                id=start + i, grouped_id=group, from_name="Автор",
                text=caption if i == caption_at else "",
            )
            items.append(m.with_media(f"media/photo/p_msg{start + i}.jpg", MediaType.PHOTO))
        return items

    def test_album_is_one_block_with_caption_and_all_files(self):
        s = MarkdownSettings(include_timestamps=True, include_author=True)
        content = self._read(self._run(self._album(), settings=s)[0])
        self.assertEqual(content.count("Автор"), 1)          # один заголовок на альбом
        self.assertEqual(content.count("1 — начало прогрева"), 1)
        self.assertIn("📎 Альбом — вложений: 3", content)
        for i in (10, 11, 12):
            self.assertIn(f"media/photo/p_msg{i}.jpg", content)
        # Файлы идут сразу под подписью, в порядке сообщений.
        self.assertLess(content.index("1 — начало прогрева"), content.index("p_msg10.jpg"))
        self.assertLess(content.index("p_msg10.jpg"), content.index("p_msg12.jpg"))

    def test_album_caption_on_later_item_is_kept(self):
        content = self._read(self._run(self._album(caption_at=2))[0])
        self.assertEqual(content.count("1 — начало прогрева"), 1)
        self.assertIn("📎 Альбом — вложений: 3", content)

    def test_album_keeps_links_of_every_item(self):
        items = self._album(count=2)
        import dataclasses
        items[1] = dataclasses.replace(
            items[1], links=(LinkItem(url="https://example.com/b", text="вторая"),),
        )
        content = self._read(self._run(items)[0])
        self.assertIn("https://example.com/b", content)

    def test_two_albums_stay_separate(self):
        first = self._album(group=500, start=10, caption="1 — начало прогрева")
        second = self._album(group=501, start=20, caption="2 — вовлечение")
        content = self._read(self._run(first + second)[0])
        self.assertEqual(content.count("📎 Альбом — вложений: 3"), 2)
        # Фото первого этапа стоят до подписи второго, фото второго — после неё.
        self.assertLess(content.index("p_msg12.jpg"), content.index("2 — вовлечение"))
        self.assertLess(content.index("2 — вовлечение"), content.index("p_msg20.jpg"))

    def test_message_after_album_keeps_order(self):
        content = self._read(self._run(self._album() + [_msg(id=99, text="после альбома")])[0])
        self.assertLess(content.index("p_msg12.jpg"), content.index("после альбома"))

    def test_trailing_album_is_not_lost(self):
        content = self._read(self._run([_msg(id=1, text="до")] + self._album())[0])
        self.assertIn("p_msg12.jpg", content)

    def test_album_is_not_split_between_part_files(self):
        s = MarkdownSettings(words_per_file=1000)
        filler = [_msg(id=i, text="слово " * 400) for i in range(1, 3)]
        files = self._run(filler + self._album(count=5) + [_msg(id=90, text="слово " * 700)], settings=s)
        holders = [f for f in files if "p_msg1" in self._read(f)]
        self.assertEqual(len(holders), 1)
        self.assertEqual(self._read(holders[0]).count("media/photo/p_msg"), 5)

    def test_single_item_album_renders_as_plain_media(self):
        content = self._read(self._run(self._album(count=1))[0])
        self.assertNotIn("Альбом", content)
        self.assertIn("📎 Фото: media/photo/p_msg10.jpg", content)

    # --- Exporter integration ---

    def test_single_message_creates_file(self):
        files = self._run([_msg(text="Hello world")])
        self.assertEqual(len(files), 1)
        content = self._read(files[0])
        self.assertIn("Hello world", content)

    def test_no_utf8_bom(self):
        files = self._run([_msg(text="test")])
        with open(files[0], "rb") as f:
            raw = f.read(3)
        self.assertNotEqual(raw, b"\xef\xbb\xbf")

    def test_word_limit_creates_multiple_files(self):
        # 5 words per file, 3 messages each with 4 words → should split
        settings = MarkdownSettings(words_per_file=5, include_timestamps=False, include_author=False)
        msgs = [_msg(id=i, text="one two three four") for i in range(1, 4)]
        files = self._run(msgs, settings=settings)
        self.assertGreater(len(files), 1)

    def test_service_message_not_written(self):
        """Service messages should be skipped in output."""
        msgs = [
            ExportMessage(id=1, type="service", date="2024-01-01T00:00:00", text="User joined"),
            _msg(id=2, text="Actual content"),
        ]
        files = self._run(msgs)
        content = self._read(files[0])
        self.assertNotIn("User joined", content)
        self.assertIn("Actual content", content)

    def test_popular_messages_file_created(self):
        msg = _msg(id=1, text="Viral", reactions=(ReactionItem(emoji="🔥", count=10),))
        files = self._run([msg], popular_min=5)
        popular_files = [f for f in files if "popular" in os.path.basename(f)]
        self.assertEqual(len(popular_files), 1)
        content = self._read(popular_files[0])
        self.assertIn("Viral", content)

    def test_no_popular_file_when_below_threshold(self):
        msg = _msg(id=1, text="Low", reactions=(ReactionItem(emoji="👍", count=2),))
        files = self._run([msg], popular_min=5)
        popular_files = [f for f in files if "popular" in os.path.basename(f)]
        self.assertEqual(len(popular_files), 0)

    def test_empty_export_returns_empty_list(self):
        files = self._run([])
        self.assertEqual(files, [])

    def test_filename_contains_chat_name(self):
        files = self._run([_msg(text="x")])
        basename = os.path.basename(files[0])
        self.assertIn("Test_Chat", basename)

    def test_finalize_idempotent_on_empty_chunks(self):
        """finalize() should not crash if content is empty after write."""
        exp = self.MarkdownExporter()
        exp.open(self._tmpdir, "Empty")
        exp.write(ExportMessage(id=1, type="service", date="2024-01-01T00:00:00", text=""))
        files = exp.finalize()
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
