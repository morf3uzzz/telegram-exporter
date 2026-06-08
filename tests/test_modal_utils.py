"""
Tests for ui/modal_utils.setup_smooth_scroll — регрессия на bind_all.

CustomTkinter запрещает bind_all/unbind_all на своих виджетах (бросает
AttributeError). Раньше setup_smooth_scroll звал modal.bind_all(...) на
<Enter>, что ломало скролл на страницах (CTkFrame). Эти тесты фиксируют, что
функция больше НЕ использует bind_all/unbind_all и не падает.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock


class _FakeCanvas:
    def __init__(self):
        self.binds = []

    def bind(self, seq, fn, add=None):
        self.binds.append(seq)

    def bind_all(self, *a, **k):
        raise AssertionError("bind_all не должен вызываться!")

    def unbind_all(self, *a, **k):
        raise AssertionError("unbind_all не должен вызываться!")

    def yview_scroll(self, *a, **k):
        pass


class _FakeScrollFrame:
    def __init__(self, canvas=None):
        if canvas is not None:
            self._parent_canvas = canvas

    def bind_all(self, *a, **k):
        raise AssertionError("bind_all не должен вызываться на frame!")

    def unbind_all(self, *a, **k):
        raise AssertionError("unbind_all не должен вызываться на frame!")


class _FakeModal:
    """modal-аргумент: если кто-то позовёт bind_all на нём — тест упадёт."""
    def bind_all(self, *a, **k):
        raise AssertionError("bind_all не должен вызываться на modal!")

    def unbind_all(self, *a, **k):
        raise AssertionError("unbind_all не должен вызываться на modal!")


class TestSetupSmoothScroll(unittest.TestCase):
    def setUp(self):
        from tg_exporter.ui.modal_utils import setup_smooth_scroll
        self.setup = setup_smooth_scroll

    def test_no_bind_all_on_macos(self):
        # На macOS биндим на canvas, но НИКОГДА bind_all (иначе AssertionError).
        canvas = _FakeCanvas()
        frame = _FakeScrollFrame(canvas)
        with mock.patch.object(sys, "platform", "darwin"):
            self.setup(_FakeModal(), frame)
        # должен забиндить колесо на canvas
        self.assertIn("<MouseWheel>", canvas.binds)

    def test_noop_on_non_macos(self):
        # На Win/Linux — no-op (встроенный скролл CTk достаточен).
        canvas = _FakeCanvas()
        frame = _FakeScrollFrame(canvas)
        with mock.patch.object(sys, "platform", "win32"):
            self.setup(_FakeModal(), frame)
        self.assertEqual(canvas.binds, [])

    def test_no_crash_without_parent_canvas(self):
        # Объект без _parent_canvas не должен ронять функцию.
        frame = _FakeScrollFrame(canvas=None)
        with mock.patch.object(sys, "platform", "darwin"):
            self.setup(_FakeModal(), frame)  # не должно бросить


class TestResolvePopupPosition(unittest.TestCase):
    SCREEN_W = 1920
    SCREEN_H = 1080

    def setUp(self):
        from tg_exporter.ui.modal_utils import resolve_popup_position
        self.resolve = resolve_popup_position

    def test_opens_below_when_room(self):
        x, y = self.resolve(
            anchor_left=100, anchor_top=90, anchor_bottom=120,
            popup_w=240, popup_h=300,
            screen_w=self.SCREEN_W, screen_h=self.SCREEN_H,
        )
        self.assertEqual(x, 100)
        self.assertEqual(y, 124)  # под якорем: 120 + margin 4

    def test_flips_up_when_no_room_below(self):
        # Якорь у нижнего края экрана: снизу не помещается → разворот вверх.
        x, y = self.resolve(
            anchor_left=100, anchor_top=900, anchor_bottom=930,
            popup_w=240, popup_h=300,
            screen_w=self.SCREEN_W, screen_h=self.SCREEN_H,
        )
        self.assertEqual(x, 100)
        self.assertEqual(y, 900 - 4 - 300)  # 596 — над якорем

    def test_clamps_to_right_edge(self):
        x, y = self.resolve(
            anchor_left=1850, anchor_top=90, anchor_bottom=120,
            popup_w=240, popup_h=300,
            screen_w=self.SCREEN_W, screen_h=self.SCREEN_H,
        )
        self.assertEqual(x, self.SCREEN_W - 240 - 4)  # 1676
        self.assertEqual(y, 124)

    def test_clamps_to_left_edge(self):
        x, _y = self.resolve(
            anchor_left=-50, anchor_top=90, anchor_bottom=120,
            popup_w=240, popup_h=300,
            screen_w=self.SCREEN_W, screen_h=self.SCREEN_H,
        )
        self.assertEqual(x, 4)

    def test_too_tall_for_either_side_picks_larger_gap(self):
        # popup выше обеих зон; сверху запас больше → прижать к верху.
        _x, y = self.resolve(
            anchor_left=100, anchor_top=400, anchor_bottom=430,
            popup_w=240, popup_h=480,
            screen_w=self.SCREEN_W, screen_h=500,
        )
        self.assertEqual(y, 4)


if __name__ == "__main__":
    unittest.main()
