# tests/test_progress_label.py
import unittest

from tg_exporter.ui.components.progress_bar import format_count_label


class TestFormatCountLabel(unittest.TestCase):
    def test_known_total_shows_x_of_y(self):
        self.assertEqual(format_count_label(5, 100), "5 / 100")

    def test_known_total_thousands_separator(self):
        self.assertEqual(format_count_label(1500, 20000), "1,500 / 20,000")

    def test_zero_total_falls_back_to_count(self):
        # total=0 трактуем как «неизвестно» → счётчик без знаменателя.
        self.assertEqual(format_count_label(1, 0), "1 сообщение")

    def test_no_total_plural_one(self):
        self.assertEqual(format_count_label(1, None), "1 сообщение")

    def test_no_total_plural_few(self):
        self.assertEqual(format_count_label(3, None), "3 сообщения")

    def test_no_total_plural_many(self):
        self.assertEqual(format_count_label(5, None), "5 сообщений")

    def test_no_total_teens_are_genitive(self):
        self.assertEqual(format_count_label(11, None), "11 сообщений")
        self.assertEqual(format_count_label(14, None), "14 сообщений")

    def test_no_total_21_is_singular(self):
        self.assertEqual(format_count_label(21, None), "21 сообщение")

    def test_no_total_zero(self):
        self.assertEqual(format_count_label(0, None), "0 сообщений")

    def test_no_total_thousands_separator(self):
        self.assertEqual(format_count_label(1500, None), "1,500 сообщений")


if __name__ == "__main__":
    unittest.main()
