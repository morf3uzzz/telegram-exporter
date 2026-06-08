# tests/test_ui_scale.py
import unittest

from tg_exporter.models.config import (
    AppConfig, clamp_ui_scale, UI_SCALE_DEFAULT, UI_SCALE_MIN, UI_SCALE_MAX,
)


class TestClampUiScale(unittest.TestCase):
    def test_within_range_unchanged(self):
        self.assertEqual(clamp_ui_scale(1.0), 1.0)
        self.assertEqual(clamp_ui_scale(0.9), 0.9)

    def test_below_min_clamped(self):
        self.assertEqual(clamp_ui_scale(0.1), UI_SCALE_MIN)

    def test_above_max_clamped(self):
        self.assertEqual(clamp_ui_scale(99), UI_SCALE_MAX)

    def test_garbage_returns_default(self):
        self.assertEqual(clamp_ui_scale("nope"), UI_SCALE_DEFAULT)
        self.assertEqual(clamp_ui_scale(None), UI_SCALE_DEFAULT)


class TestAppConfigUiScale(unittest.TestCase):
    def test_default(self):
        self.assertEqual(AppConfig().ui_scale, UI_SCALE_DEFAULT)

    def test_to_dict_includes_ui_scale(self):
        d = AppConfig(ui_scale=1.1).to_dict()
        self.assertIn("ui_scale", d)
        self.assertEqual(d["ui_scale"], 1.1)

    def test_from_dict_reads_ui_scale(self):
        self.assertEqual(AppConfig.from_dict({"ui_scale": 1.25}).ui_scale, 1.25)

    def test_from_dict_missing_uses_default(self):
        self.assertEqual(AppConfig.from_dict({"api_id": "123"}).ui_scale, UI_SCALE_DEFAULT)

    def test_roundtrip(self):
        cfg = AppConfig(ui_scale=1.1)
        self.assertEqual(AppConfig.from_dict(cfg.to_dict()).ui_scale, 1.1)


class TestScaledFontPx(unittest.TestCase):
    def setUp(self):
        from tg_exporter.ui.theme import scaled_font_px
        self.fn = scaled_font_px

    def test_scale_1_is_14px_negative(self):
        self.assertEqual(self.fn(1.0), -14)

    def test_scale_0_9(self):
        self.assertEqual(self.fn(0.9), -13)

    def test_small_scale_clamped_to_min(self):
        self.assertEqual(self.fn(0.4), -8)

    def test_large_scale(self):
        self.assertEqual(self.fn(1.5), -21)

    def test_garbage_safe(self):
        self.assertEqual(self.fn("x"), -14)


if __name__ == "__main__":
    unittest.main()
