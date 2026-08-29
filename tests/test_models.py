"""Tests for tg_exporter.models — Config, ExportMessage, ExportTask."""

import dataclasses
import tempfile
import unittest
from pathlib import Path


class TestAppConfig(unittest.TestCase):

    def setUp(self):
        from tg_exporter.models.config import AppConfig, MarkdownSettings, ConfigValidationError
        self.AppConfig = AppConfig
        self.MarkdownSettings = MarkdownSettings
        self.ConfigValidationError = ConfigValidationError

    def test_defaults_are_valid(self):
        cfg = self.AppConfig()
        cfg.validate()  # no raise

    def test_api_id_int_strips_non_digits(self):
        cfg = self.AppConfig.from_dict({"api_id": " 12 34 "})
        self.assertEqual(cfg.api_id, " 12 34 ")  # raw stored as-is
        self.assertEqual(cfg.api_id_int, 1234)

    def test_with_api_id_strips_non_digits(self):
        cfg = self.AppConfig().with_api_id("abc-123-xyz")
        self.assertEqual(cfg.api_id, "123")

    def test_api_id_int_none_when_empty(self):
        cfg = self.AppConfig()
        self.assertIsNone(cfg.api_id_int)

    def test_validation_bad_provider(self):
        cfg = self.AppConfig(transcription_provider="unknown")
        with self.assertRaises(self.ConfigValidationError):
            cfg.validate()

    def test_validation_bad_model(self):
        cfg = self.AppConfig(local_whisper_model="gpt4")
        with self.assertRaises(self.ConfigValidationError):
            cfg.validate()

    def test_validation_bad_words_per_file(self):
        cfg = self.AppConfig(markdown=self.MarkdownSettings(words_per_file=100))
        with self.assertRaises(self.ConfigValidationError):
            cfg.validate()

    def test_to_dict_excludes_secrets(self):
        cfg = self.AppConfig(api_id="123", deepgram_api_key="secret")
        d = cfg.to_dict()
        self.assertNotIn("api_hash", d)
        self.assertNotIn("session", d)
        self.assertNotIn("deepgram_api_key", d)
        self.assertIn("api_id", d)

    def test_from_dict_ignores_secrets(self):
        cfg = self.AppConfig.from_dict({
            "api_id": "123",
            "api_hash": "should_be_ignored",
            "session": "should_be_ignored",
        })
        self.assertEqual(cfg.api_id, "123")
        self.assertEqual(cfg.deepgram_api_key, "")

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            import tg_exporter.models.config as cfg_mod
            orig_file = cfg_mod.CONFIG_FILE
            orig_dir = cfg_mod.CONFIG_DIR
            try:
                cfg_mod.CONFIG_FILE = Path(d) / "config.json"
                cfg_mod.CONFIG_DIR = Path(d)
                cfg = self.AppConfig(
                    api_id="99887",
                    transcription_provider="local",
                    local_whisper_model="small",
                )
                cfg.save()
                loaded = self.AppConfig.load()
                self.assertEqual(loaded.api_id, "99887")
                self.assertEqual(loaded.local_whisper_model, "small")
            finally:
                cfg_mod.CONFIG_FILE = orig_file
                cfg_mod.CONFIG_DIR = orig_dir

    def test_load_returns_default_when_no_file(self):
        import tg_exporter.models.config as cfg_mod
        orig_file = cfg_mod.CONFIG_FILE
        try:
            cfg_mod.CONFIG_FILE = Path("/nonexistent/path/config.json")
            cfg = self.AppConfig.load()
            self.assertEqual(cfg.api_id, "")
        finally:
            cfg_mod.CONFIG_FILE = orig_file

    def test_markdown_settings_roundtrip(self):
        from tg_exporter.models.config import MarkdownSettings
        s = MarkdownSettings(words_per_file=30_000, date_format="YYYY-MM-DD", plain_text=False)
        s2 = MarkdownSettings.from_dict(s.to_dict())
        self.assertEqual(s2.words_per_file, 30_000)
        self.assertEqual(s2.date_format, "YYYY-MM-DD")
        self.assertFalse(s2.plain_text)


class TestExportMessage(unittest.TestCase):

    def _make(self, **kw):
        from tg_exporter.models.message import ExportMessage
        defaults = dict(id=1, type="message", date="2024-06-15T10:00:00+00:00", text="Hello")
        defaults.update(kw)
        return ExportMessage(**defaults)

    def test_to_dict_minimal(self):
        msg = self._make()
        d = msg.to_dict()
        self.assertEqual(d["id"], 1)
        self.assertEqual(d["text"], "Hello")
        self.assertEqual(d["date"], "2024-06-15T10:00:00+00:00")
        self.assertNotIn("links", d)
        self.assertNotIn("reactions", d)

    def test_to_dict_omits_none_fields(self):
        msg = self._make(views=None, forwards=None)
        d = msg.to_dict()
        self.assertNotIn("views", d)
        self.assertNotIn("forwards", d)

    def test_to_dict_includes_views_when_set(self):
        msg = self._make(views=500, forwards=10)
        d = msg.to_dict()
        self.assertEqual(d["views"], 500)
        self.assertEqual(d["forwards"], 10)

    def test_with_transcription_immutable(self):
        msg = self._make()
        msg2 = msg.with_transcription("Привет мир")
        self.assertIsNone(msg.transcription)
        self.assertEqual(msg2.transcription, "Привет мир")

    def test_with_media_immutable(self):
        from tg_exporter.models.message import MediaType
        msg = self._make()
        msg2 = msg.with_media("/path/file.ogg", MediaType.VOICE, "audio/ogg")
        self.assertIsNone(msg.media_path)
        self.assertEqual(msg2.media_path, "/path/file.ogg")
        self.assertEqual(msg2.media_type, MediaType.VOICE)

    def test_frozen_prevents_mutation(self):
        msg = self._make()
        with self.assertRaises((dataclasses.FrozenInstanceError, TypeError, AttributeError)):
            msg.text = "modified"  # type: ignore[misc]

    def test_reactions_in_to_dict(self):
        from tg_exporter.models.message import ExportMessage, ReactionItem
        msg = ExportMessage(
            id=2, type="message", date="2024-01-01T00:00:00",
            reactions=(ReactionItem(emoji="👍", count=5),),
        )
        d = msg.to_dict()
        self.assertEqual(d["reactions"], [{"emoji": "👍", "count": 5}])

    def test_poll_in_to_dict(self):
        from tg_exporter.models.message import ExportMessage, PollData, PollAnswer
        poll = PollData(
            question="Что лучше?",
            answers=(PollAnswer(text="A", voters=10), PollAnswer(text="B", voters=5)),
            total_voters=15,
        )
        msg = ExportMessage(id=3, type="message", date="2024-01-01T00:00:00", poll=poll)
        d = msg.to_dict()
        self.assertEqual(d["poll"]["question"], "Что лучше?")
        self.assertEqual(d["poll"]["total_voters"], 15)


class TestExportTask(unittest.TestCase):

    def test_author_filter_empty_matches_all(self):
        from tg_exporter.models.export_task import AuthorFilter
        af = AuthorFilter()
        self.assertTrue(af.matches(123))
        self.assertTrue(af.matches(None))
        self.assertTrue(af.is_empty())

    def test_author_filter_with_ids(self):
        from tg_exporter.models.export_task import AuthorFilter
        af = AuthorFilter.from_ids([10, 20, 30])
        self.assertTrue(af.matches(10))
        self.assertFalse(af.matches(99))
        self.assertFalse(af.is_empty())

    def test_export_progress_lifecycle(self):
        import time
        from tg_exporter.models.export_task import ExportProgress, ExportStatus
        p = ExportProgress()
        self.assertEqual(p.status, ExportStatus.PENDING)
        self.assertIsNone(p.progress_ratio)

        p.start()
        self.assertEqual(p.status, ExportStatus.RUNNING)
        self.assertIsNotNone(p.started_at)

        p.total_messages = 100
        p.processed_messages = 25
        self.assertAlmostEqual(p.progress_ratio, 0.25)

        time.sleep(0.01)
        self.assertIsNotNone(p.elapsed_seconds)
        self.assertGreater(p.elapsed_seconds, 0)

        p.finish()
        self.assertEqual(p.status, ExportStatus.DONE)
        self.assertIsNotNone(p.finished_at)

    def test_export_progress_cancel(self):
        from tg_exporter.models.export_task import ExportProgress, ExportStatus
        p = ExportProgress()
        p.start()
        p.cancel()
        self.assertEqual(p.status, ExportStatus.CANCELLED)

    def test_export_progress_fail(self):
        from tg_exporter.models.export_task import ExportProgress, ExportStatus
        p = ExportProgress()
        p.start()
        p.fail("network error")
        self.assertEqual(p.status, ExportStatus.ERROR)
        self.assertEqual(p.error, "network error")

    def test_export_progress_ratio_capped_at_1(self):
        from tg_exporter.models.export_task import ExportProgress
        p = ExportProgress()
        p.total_messages = 10
        p.processed_messages = 15  # больше total
        self.assertEqual(p.progress_ratio, 1.0)

    def test_export_progress_eta(self):
        import time
        from tg_exporter.models.export_task import ExportProgress
        p = ExportProgress()
        p.start()
        p.total_messages = 100
        p.processed_messages = 50
        time.sleep(0.02)
        eta = p.eta_seconds
        self.assertIsNotNone(eta)
        self.assertGreater(eta, 0)

    def test_export_task_immutable(self):
        from tg_exporter.models.export_task import ExportTask
        task = ExportTask(chat_id=1, chat_name="Test", output_path="/tmp")
        task2 = task.with_last_id(500)
        self.assertIsNone(task.last_exported_id)
        self.assertEqual(task2.last_exported_id, 500)

    def test_export_task_incremental_flag(self):
        from tg_exporter.models.export_task import ExportTask
        t1 = ExportTask(chat_id=1, chat_name="C", output_path="/tmp", incremental=True)
        self.assertFalse(t1.is_incremental_with_offset)  # нет last_id

        t2 = t1.with_last_id(100)
        self.assertTrue(t2.is_incremental_with_offset)


if __name__ == "__main__":
    unittest.main()
