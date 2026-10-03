"""
Tests: привязка скачанных медиа к сообщениям.

Раньше файл скачивался ПОСЛЕ записи сообщения, путь выбрасывался, а имена
выбирал Telethon (`photo_2026-01-02_03-04-05 (1).jpg`) — по экспорту нельзя
было понять, к какому сообщению относится фото. Теперь имя содержит дату и id
сообщения, а путь попадает в JSON (`media_path`) и в Markdown.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from tg_exporter.core.orchestrator import ExportOrchestrator
from tg_exporter.models.config import AppConfig
from tg_exporter.models.export_task import ExportFormat, ExportTask
from tg_exporter.services.media_downloader import MediaDirs, MediaDownloader, media_file_stem
from tg_exporter.utils.cancellation import CancellationToken


_DATE = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)


def _named(msg_id=12, name=None, date=_DATE):
    return SimpleNamespace(id=msg_id, date=date, file=SimpleNamespace(name=name))


class TestMediaFileStem(unittest.TestCase):

    def test_stem_has_date_and_message_id(self):
        self.assertEqual(media_file_stem(_named(1234)), "2026-01-02_03-04-05_msg1234")

    def test_stem_without_original_name_has_no_dots(self):
        # Telethon дописывает расширение, только если в имени нет точки.
        self.assertNotIn(".", media_file_stem(_named(1234)))

    def test_document_keeps_original_name_and_extension(self):
        self.assertEqual(
            media_file_stem(_named(12, "Договор финал.pdf")),
            "2026-01-02_03-04-05_msg12_Договор_финал.pdf",
        )

    def test_unsafe_original_name_is_neutralised(self):
        stem = media_file_stem(_named(12, "../../evil report.v2.pdf"))
        self.assertEqual(stem, "2026-01-02_03-04-05_msg12_evil_report_v2.pdf")
        self.assertNotIn("/", stem)
        self.assertNotIn(" ", stem)

    def test_windows_separators_are_stripped(self):
        self.assertEqual(
            media_file_stem(_named(12, "C:\\\\Users\\\\me\\\\photo.JPG")),
            "2026-01-02_03-04-05_msg12_photo.JPG",
        )

    def test_long_original_name_is_truncated(self):
        stem = media_file_stem(_named(12, "a" * 300 + ".pdf"))
        self.assertLessEqual(len(stem), 100)
        self.assertTrue(stem.endswith(".pdf"))

    def test_suspicious_extension_is_dropped(self):
        stem = media_file_stem(_named(12, "archive.tar gz!"))
        self.assertNotIn(".", stem)

    def test_missing_date_falls_back_to_id(self):
        self.assertEqual(media_file_stem(_named(12, date=None)), "msg12")

    def test_mock_message_does_not_break_naming(self):
        m = MagicMock()
        m.id = 7
        self.assertEqual(media_file_stem(m), "msg7")


class _Photo:
    """Telethon-подобное сообщение с фото: download_media пишет файл на диск."""

    def __init__(self, msg_id, text="", grouped_id=None, fail=False):
        self.id = msg_id
        self.date = _DATE + datetime.timedelta(seconds=msg_id)
        self.action = None
        self.raw_text = text
        self.sender = None
        self.sender_id = 1
        self.photo = object()
        self.grouped_id = grouped_id
        self.file = SimpleNamespace(name=None)
        self.out = False
        self._fail = fail
        self.requested_file = None

    def download_media(self, file=None, progress_callback=None):
        self.requested_file = file
        if self._fail:
            raise RuntimeError("network")
        path = file + ".jpg"  # как Telethon: расширение дописывается к основе
        with open(path, "wb") as f:
            f.write(b"jpg")
        return path


class TestDownloaderNaming(unittest.TestCase):

    def test_download_asks_telethon_for_deterministic_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = MediaDirs.create(os.path.join(tmp, "media"))
            msg = _Photo(1234)
            path = MediaDownloader().download(msg, dirs, CancellationToken())
            self.assertEqual(os.path.dirname(msg.requested_file), dirs.photo)
            self.assertEqual(os.path.basename(path), media_file_stem(msg) + ".jpg")
            self.assertTrue(os.path.isfile(path))


class TestOrchestratorLinksMedia(unittest.TestCase):

    def _run(self, messages, fmt=ExportFormat.BOTH, download_media=True):
        conn = MagicMock()
        conn.iter_messages = lambda dialog, **kw: iter(messages)
        client = MagicMock()
        client.ensure_connected.return_value = conn

        orch = ExportOrchestrator.__new__(ExportOrchestrator)
        orch._client = client
        orch._config = AppConfig()
        orch._history = MagicMock()
        orch._deepgram_key = None
        orch._media = MediaDownloader()
        orch._count_messages = lambda c, dialog, task: len(messages)

        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        task = ExportTask(
            chat_id=1, chat_name="Chat", output_path=tmp,
            format=fmt, download_media=download_media,
        )
        dialog = MagicMock()
        dialog.name = "Chat"
        events = []
        orch._do_run(dialog, task, CancellationToken(), MagicMock(),
                     send=lambda etype, payload: events.append((etype, payload)))
        export_dir = next(p for e, p in events if e == "export_done")[0]
        return export_dir

    def _json(self, export_dir):
        with open(os.path.join(export_dir, "result.json"), encoding="utf-8") as f:
            return json.load(f)["messages"]

    def _md(self, export_dir):
        name = next(n for n in os.listdir(export_dir) if n.endswith(".md"))
        with open(os.path.join(export_dir, name), encoding="utf-8") as f:
            return f.read()

    def test_json_message_points_to_its_file(self):
        export_dir = self._run([_Photo(101, "Шаг 1")])
        msg = self._json(export_dir)[0]
        self.assertEqual(msg["media_type"], "photo")
        self.assertRegex(msg["media_path"], r"^media/photo/2026-01-02_03-05-46_msg101\.jpg$")
        self.assertTrue(os.path.isfile(os.path.join(export_dir, *msg["media_path"].split("/"))))

    def test_path_is_relative_posix(self):
        export_dir = self._run([_Photo(101)])
        path = self._json(export_dir)[0]["media_path"]
        self.assertFalse(os.path.isabs(path))
        self.assertNotIn("\\", path)
        self.assertNotIn(export_dir, path)

    def test_markdown_puts_each_stage_photos_under_its_caption(self):
        # Сценарий пользователя: «1 — начало прогрева» + фото, затем «2 …» + фото.
        stage1 = [_Photo(101, "1 — начало прогрева", grouped_id=900),
                  _Photo(102, grouped_id=900), _Photo(103, grouped_id=900)]
        stage2 = [_Photo(104, "2 — вовлечение", grouped_id=901), _Photo(105, grouped_id=901)]
        export_dir = self._run(stage1 + stage2)

        md = self._md(export_dir)
        i1, i2 = md.index("1 — начало прогрева"), md.index("2 — вовлечение")
        for msg_id in (101, 102, 103):
            self.assertTrue(i1 < md.index(f"_msg{msg_id}.jpg") < i2, msg_id)
        for msg_id in (104, 105):
            self.assertGreater(md.index(f"_msg{msg_id}.jpg"), i2)

        data = self._json(export_dir)
        self.assertEqual([m.get("grouped_id") for m in data], [900, 900, 900, 901, 901])
        self.assertTrue(all(m["media_path"].endswith(f"_msg{m['id']}.jpg") for m in data))

    def test_failed_download_does_not_lose_the_message(self):
        export_dir = self._run([_Photo(101, "Шаг 1", fail=True)])
        msg = self._json(export_dir)[0]
        self.assertEqual(msg["text"], "Шаг 1")
        self.assertNotIn("media_path", msg)
        self.assertIn("📎 Фото (файл не скачан)", self._md(export_dir))

    def test_without_media_download_nothing_is_fetched(self):
        photo = _Photo(101, "Шаг 1")
        export_dir = self._run([photo], download_media=False)
        self.assertIsNone(photo.requested_file)
        self.assertNotIn("media_path", self._json(export_dir)[0])
        self.assertFalse(os.path.exists(os.path.join(export_dir, "media")))


class TestConverterAlbumId(unittest.TestCase):

    def test_grouped_id_is_copied(self):
        from tg_exporter.core.converter import message_to_export
        self.assertEqual(message_to_export(_Photo(1, grouped_id=777)).grouped_id, 777)

    def test_no_album_no_grouped_id(self):
        from tg_exporter.core.converter import message_to_export
        self.assertIsNone(message_to_export(_Photo(1)).grouped_id)

    def test_non_integer_grouped_id_is_ignored(self):
        # MagicMock-сообщения в тестах оркестратора отдают mock на любой атрибут.
        from tg_exporter.core.converter import message_to_export
        m = _Photo(1)
        m.grouped_id = MagicMock()
        self.assertIsNone(message_to_export(m).grouped_id)


if __name__ == "__main__":
    unittest.main()
