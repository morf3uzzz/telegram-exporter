"""Tests for services and utilities:
  - AnalyticsCollector / render_top_authors / render_activity
  - ExportHistory
  - CancellationToken
  - BackgroundWorker / EventDispatcher
"""

import tempfile
import threading
import time
import unittest
from pathlib import Path

from tg_exporter.models.message import ExportMessage


def _msg(**kw) -> ExportMessage:
    defaults = dict(id=1, type="message", date="2024-06-15T10:00:00+00:00", text="Hello")
    defaults.update(kw)
    return ExportMessage(**defaults)


# ──────────────────────────────────────────────
# Analytics
# ──────────────────────────────────────────────

class TestAnalyticsCollector(unittest.TestCase):

    def setUp(self):
        from tg_exporter.services.analytics import AnalyticsCollector
        self.AnalyticsCollector = AnalyticsCollector

    def _collect(self, items):
        """items: list of (msg, is_outgoing)"""
        c = self.AnalyticsCollector()
        for msg, is_out in items:
            c.add(msg, msg.text or "", is_out)
        return c.result()

    def test_empty_result(self):
        result = self._collect([])
        self.assertEqual(result.authors, [])
        self.assertEqual(result.activity, {})

    def test_single_author_count(self):
        msgs = [(_msg(id=i, from_id=10, from_name="Alice"), False) for i in range(1, 4)]
        result = self._collect(msgs)
        self.assertEqual(len(result.authors), 1)
        self.assertEqual(result.authors[0].message_count, 3)
        self.assertEqual(result.authors[0].name, "Alice")

    def test_multiple_authors_sorted_by_count(self):
        items = (
            [(_msg(id=i, from_id=10, from_name="A"), False) for i in range(1, 6)] +
            [(_msg(id=i + 10, from_id=20, from_name="B"), False) for i in range(1, 3)]
        )
        result = self._collect(items)
        self.assertEqual(result.authors[0].user_id, 10)  # more messages
        self.assertEqual(result.authors[1].user_id, 20)

    def test_outgoing_messages_excluded_from_authors(self):
        items = [(_msg(id=1, from_id=10, from_name="Me"), True)]
        result = self._collect(items)
        self.assertEqual(result.authors, [])

    def test_activity_by_date(self):
        items = [
            (_msg(id=1, date="2024-06-15T10:00:00"), False),
            (_msg(id=2, date="2024-06-15T12:00:00"), False),
            (_msg(id=3, date="2024-06-16T08:00:00"), False),
        ]
        result = self._collect(items)
        self.assertEqual(result.activity.get("2024-06-15"), 2)
        self.assertEqual(result.activity.get("2024-06-16"), 1)

    def test_username_captured(self):
        msg = _msg(id=1, from_id=5, from_name="Bob", from_username="bob_tg")
        result = self._collect([(msg, False)])
        self.assertEqual(result.authors[0].username, "bob_tg")

    def test_messages_stored(self):
        msg = _msg(id=1, from_id=7, from_name="Eve", text="Test content")
        result = self._collect([(msg, False)])
        self.assertEqual(len(result.authors[0].messages), 1)
        self.assertIn("Test content", result.authors[0].messages[0])

    def test_none_from_id_skipped(self):
        msg = _msg(id=1, from_id=None, text="Channel post")
        result = self._collect([(msg, False)])
        self.assertEqual(result.authors, [])

    def test_activity_outgoing_still_counted(self):
        """Outgoing messages should still count for activity tracking."""
        msg = _msg(id=1, from_id=10, from_name="Me", date="2024-01-01T10:00:00")
        result = self._collect([(msg, True)])
        self.assertEqual(result.activity.get("2024-01-01"), 1)


class TestRenderTopAuthors(unittest.TestCase):

    def setUp(self):
        from tg_exporter.services.analytics import AnalyticsCollector, render_top_authors
        self._make = AnalyticsCollector
        self.render = render_top_authors

    def _result(self, msgs_by_author: dict):
        c = self._make()
        for author_id, (name, msgs) in msgs_by_author.items():
            for i, text in enumerate(msgs):
                msg = _msg(id=i, from_id=author_id, from_name=name, text=text)
                c.add(msg, text)
        return c.result()

    def test_returns_list_of_strings(self):
        result = self._result({1: ("Alice", ["msg1", "msg2"])})
        parts = self.render(result)
        self.assertIsInstance(parts, list)
        self.assertGreater(len(parts), 0)
        self.assertIsInstance(parts[0], str)

    def test_contains_author_name(self):
        result = self._result({1: ("Alice", ["hi"])})
        parts = self.render(result)
        combined = "".join(parts)
        self.assertIn("Alice", combined)

    def test_contains_message_count(self):
        result = self._result({1: ("Alice", ["a", "b", "c"])})
        parts = self.render(result)
        combined = "".join(parts)
        self.assertIn("3", combined)

    def test_empty_result_returns_empty_list(self):
        from tg_exporter.services.analytics import AnalyticsResult
        parts = self.render(AnalyticsResult())
        self.assertEqual(parts, [])

    def test_word_limit_splits_into_multiple_parts(self):
        """With tiny word limit, multiple authors should produce multiple parts."""
        result = self._result({
            1: ("Alice", ["word " * 20] * 5),
            2: ("Bob", ["word " * 20] * 5),
        })
        parts = self.render(result, words_per_file=30)
        self.assertGreater(len(parts), 1)


class TestRenderActivity(unittest.TestCase):

    def setUp(self):
        from tg_exporter.services.analytics import render_activity, AnalyticsResult
        self.render = render_activity
        self.AnalyticsResult = AnalyticsResult

    def test_empty_returns_empty_string(self):
        result = self.AnalyticsResult()
        self.assertEqual(self.render(result), "")

    def test_contains_date(self):
        result = self.AnalyticsResult(activity={"2024-06-15": 5})
        out = self.render(result)
        self.assertIn("2024-06-15", out)
        self.assertIn("5", out)

    def test_sorted_dates(self):
        result = self.AnalyticsResult(activity={"2024-06-20": 2, "2024-06-10": 7})
        out = self.render(result)
        self.assertLess(out.index("2024-06-10"), out.index("2024-06-20"))

    def test_contains_weekday_name(self):
        result = self.AnalyticsResult(activity={"2024-06-17": 3})  # Monday
        out = self.render(result)
        self.assertIn("Понедельник", out)

    def test_hot_days_section(self):
        result = self.AnalyticsResult(activity={"2024-06-15": 100, "2024-06-16": 5})
        out = self.render(result)
        self.assertIn("горячие", out.lower())


# ──────────────────────────────────────────────
# ExportHistory
# ──────────────────────────────────────────────

class TestExportHistory(unittest.TestCase):

    def _make(self):
        from tg_exporter.services.export_history import ExportHistory
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "history.json"
            yield ExportHistory(path=path)

    def setUp(self):
        from tg_exporter.services.export_history import ExportHistory
        self._tmp = tempfile.mkdtemp()
        self.history_path = Path(self._tmp) / "history.json"
        self.ExportHistory = ExportHistory

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _new(self):
        return self.ExportHistory(path=self.history_path)

    def test_get_returns_none_for_unknown_chat(self):
        h = self._new()
        self.assertIsNone(h.get_last_id(12345))

    def test_set_and_get(self):
        h = self._new()
        h.set_last_id(100, 500)
        self.assertEqual(h.get_last_id(100), 500)

    def test_set_only_updates_if_greater(self):
        h = self._new()
        h.set_last_id(100, 500)
        h.set_last_id(100, 300)  # smaller — should be ignored
        self.assertEqual(h.get_last_id(100), 500)

    def test_set_updates_if_greater(self):
        h = self._new()
        h.set_last_id(100, 500)
        h.set_last_id(100, 700)
        self.assertEqual(h.get_last_id(100), 700)

    def test_persists_across_instances(self):
        h1 = self._new()
        h1.set_last_id(42, 1000)
        h2 = self._new()
        self.assertEqual(h2.get_last_id(42), 1000)

    def test_clear_removes_entry(self):
        h = self._new()
        h.set_last_id(10, 200)
        h.clear(10)
        self.assertIsNone(h.get_last_id(10))

    def test_multiple_chats_independent(self):
        h = self._new()
        h.set_last_id(1, 100)
        h.set_last_id(2, 200)
        self.assertEqual(h.get_last_id(1), 100)
        self.assertEqual(h.get_last_id(2), 200)

    def test_load_from_nonexistent_file_returns_defaults(self):
        path = Path(self._tmp) / "nonexistent" / "h.json"
        h = self.ExportHistory(path=path)
        self.assertIsNone(h.get_last_id(99))


# ──────────────────────────────────────────────
# CancellationToken
# ──────────────────────────────────────────────

class TestCancellationToken(unittest.TestCase):

    def setUp(self):
        from tg_exporter.utils.cancellation import CancellationToken, CancelledError
        self.CancellationToken = CancellationToken
        self.CancelledError = CancelledError

    def test_not_cancelled_by_default(self):
        t = self.CancellationToken()
        self.assertFalse(t.is_cancelled)

    def test_cancel_sets_flag(self):
        t = self.CancellationToken()
        t.cancel()
        self.assertTrue(t.is_cancelled)

    def test_raise_if_cancelled_raises(self):
        t = self.CancellationToken()
        t.cancel()
        with self.assertRaises(self.CancelledError):
            t.raise_if_cancelled()

    def test_raise_if_cancelled_no_raise_when_active(self):
        t = self.CancellationToken()
        t.raise_if_cancelled()  # no raise

    def test_cancel_is_idempotent(self):
        t = self.CancellationToken()
        t.cancel()
        t.cancel()  # no raise, no error
        self.assertTrue(t.is_cancelled)

    def test_reset_clears_flag(self):
        t = self.CancellationToken()
        t.cancel()
        t.reset()
        self.assertFalse(t.is_cancelled)
        t.raise_if_cancelled()  # should not raise

    def test_thread_safety(self):
        """Cancel from another thread should be visible in main thread."""
        t = self.CancellationToken()

        def canceller():
            time.sleep(0.01)
            t.cancel()

        th = threading.Thread(target=canceller)
        th.start()
        th.join(timeout=1.0)
        self.assertTrue(t.is_cancelled)

    def test_wait_for_cancel_returns_true_when_cancelled(self):
        t = self.CancellationToken()
        t.cancel()
        result = t.wait_for_cancel(timeout=0.1)
        self.assertTrue(result)

    def test_wait_for_cancel_returns_false_on_timeout(self):
        t = self.CancellationToken()
        result = t.wait_for_cancel(timeout=0.01)
        self.assertFalse(result)


# ──────────────────────────────────────────────
# BackgroundWorker
# ──────────────────────────────────────────────

class TestBackgroundWorker(unittest.TestCase):

    def setUp(self):
        from tg_exporter.utils.worker import BackgroundWorker
        self.worker = BackgroundWorker()
        self.worker.start()

    def tearDown(self):
        self.worker.shutdown(timeout=1.0)

    def test_submit_runs_task(self):
        done = threading.Event()
        self.worker.submit(lambda: done.set())
        self.assertTrue(done.wait(timeout=2.0))

    def test_put_event_visible_in_poll(self):
        self.worker.put_event("test_event", {"data": 42})
        # poll immediately
        events = []
        deadline = time.time() + 2.0
        while time.time() < deadline and not events:
            events = self.worker.poll_events()
        self.assertTrue(any(e[0] == "test_event" for e in events))

    def test_poll_returns_empty_when_no_events(self):
        events = self.worker.poll_events()
        self.assertIsInstance(events, list)
        self.assertEqual(events, [])

    def test_submit_sends_event_from_task(self):
        self.worker.submit(self.worker.put_event, "from_bg", "payload")
        events = []
        deadline = time.time() + 2.0
        while time.time() < deadline:
            events.extend(self.worker.poll_events())
            if any(e[0] == "from_bg" for e in events):
                break
            time.sleep(0.01)
        self.assertTrue(any(e[0] == "from_bg" for e in events))

    def test_task_exception_produces_worker_error_event(self):
        self.worker.submit(lambda: 1 / 0)
        events = []
        deadline = time.time() + 2.0
        while time.time() < deadline:
            events.extend(self.worker.poll_events())
            if any(e[0] == "worker_error" for e in events):
                break
            time.sleep(0.01)
        error_events = [e for e in events if e[0] == "worker_error"]
        self.assertEqual(len(error_events), 1)
        self.assertIn("ZeroDivision", error_events[0][1])

    def test_start_twice_is_safe(self):
        self.worker.start()  # second start should not raise

    def test_poll_max_events_limit(self):
        for i in range(30):
            self.worker.put_event("ev", i)
        time.sleep(0.05)
        events = self.worker.poll_events(max_events=10)
        self.assertLessEqual(len(events), 10)


# ──────────────────────────────────────────────
# EventDispatcher
# ──────────────────────────────────────────────

class TestEventDispatcher(unittest.TestCase):

    def setUp(self):
        from tg_exporter.utils.worker import EventDispatcher
        self.EventDispatcher = EventDispatcher

    def test_on_and_dispatch(self):
        d = self.EventDispatcher()
        received = []
        d.on("ping", lambda p: received.append(p))
        d.dispatch("ping", "pong")
        self.assertEqual(received, ["pong"])

    def test_dispatch_unknown_event_is_silent(self):
        d = self.EventDispatcher()
        d.dispatch("no_handler", None)  # no raise

    def test_multiple_handlers_for_same_event(self):
        d = self.EventDispatcher()
        results = []
        d.on("ev", lambda p: results.append("A"))
        d.on("ev", lambda p: results.append("B"))
        d.dispatch("ev", None)
        self.assertEqual(sorted(results), ["A", "B"])

    def test_off_removes_handler(self):
        d = self.EventDispatcher()
        received = []

        def handler(p):
            received.append(p)

        d.on("ev", handler)
        d.off("ev", handler)
        d.dispatch("ev", 1)
        self.assertEqual(received, [])

    def test_handler_exception_does_not_stop_others(self):
        d = self.EventDispatcher()
        results = []
        d.on("ev", lambda p: (_ for _ in ()).throw(RuntimeError("oops")))
        d.on("ev", lambda p: results.append("ok"))
        d.dispatch("ev", None)
        self.assertEqual(results, ["ok"])

    def test_dispatch_event_tuple(self):
        d = self.EventDispatcher()
        received = []
        d.on("tick", lambda p: received.append(p))
        d.dispatch_event(("tick", 42))
        self.assertEqual(received, [42])


if __name__ == "__main__":
    unittest.main()
