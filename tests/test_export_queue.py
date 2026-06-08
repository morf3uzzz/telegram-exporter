# tests/test_export_queue.py
import types
import unittest

from tg_exporter.core.export_queue import BatchJob, ExportQueue, build_topic_jobs
from tg_exporter.models.forum_topic import ForumTopic


class TestBuildTopicJobs(unittest.TestCase):
    def test_one_job_per_topic(self):
        dialog = types.SimpleNamespace(id=10, name="Happ")
        topics = [ForumTopic(1, "General"), ForumTopic(2, "Bug reports")]
        jobs = build_topic_jobs(dialog, topics)
        self.assertEqual(len(jobs), 2)
        self.assertEqual([(j.topic_id, j.topic_title) for j in jobs],
                         [(1, "General"), (2, "Bug reports")])
        self.assertIs(jobs[0].dialog, dialog)
        self.assertEqual(jobs[0].label, "General")


class TestExportQueue(unittest.TestCase):
    def _jobs(self, n):
        return [BatchJob(dialog=None, topic_id=i, topic_title=f"t{i}", label=f"t{i}")
                for i in range(n)]

    def test_empty_has_no_next(self):
        q = ExportQueue([])
        self.assertFalse(q.has_next())
        self.assertEqual(q.total, 0)

    def test_next_advances_index(self):
        q = ExportQueue(self._jobs(2))
        self.assertTrue(q.has_next())
        j0 = q.next()
        self.assertEqual(j0.topic_id, 0)
        self.assertEqual(q.current_index, 1)
        j1 = q.next()
        self.assertEqual(j1.topic_id, 1)
        self.assertFalse(q.has_next())

    def test_record_counts_and_summary(self):
        q = ExportQueue(self._jobs(3))
        q.record(True)
        q.record(False)
        q.record(True)
        self.assertEqual(q.ok, 2)
        self.assertEqual(q.failed, 1)
        self.assertIn("2", q.summary())
        self.assertIn("1", q.summary())

    def test_summary_no_errors(self):
        q = ExportQueue(self._jobs(1))
        q.record(True)
        self.assertNotIn("ошиб", q.summary().lower())


if __name__ == "__main__":
    unittest.main()
