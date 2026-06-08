"""
Тесты для AnchoredPopupController и хелпера is_descendant из ui/modal_utils.

Контроллер сильно завязан на Tk, но его *решения* (закрывать ли popup по
клику, репозиционировать ли при <Configure>, что отвязать при закрытии)
проверяемы без реального дисплея: подменяем make_anchored_popup на фейк-окно
и используем фейк-виджеты для якоря/хоста/popup'а (стиль существующих
test_modal_utils._FakeCanvas).
"""

from __future__ import annotations

import tkinter as tk
import unittest
from unittest import mock

MOD = "tg_exporter.ui.modal_utils"


class TestIsDescendant(unittest.TestCase):
    def setUp(self):
        from tg_exporter.ui.modal_utils import is_descendant
        self.is_descendant = is_descendant

    def test_widget_is_ancestor_itself(self):
        a = object()
        self.assertTrue(self.is_descendant(a, a))

    def test_direct_child(self):
        anc = _Node(None)
        child = _Node(anc)
        self.assertTrue(self.is_descendant(child, anc))

    def test_deep_descendant(self):
        anc = _Node(None)
        mid = _Node(anc)
        leaf = _Node(mid)
        self.assertTrue(self.is_descendant(leaf, anc))

    def test_unrelated_widget(self):
        anc = _Node(None)
        other = _Node(_Node(None))
        self.assertFalse(self.is_descendant(other, anc))

    def test_none_widget(self):
        anc = _Node(None)
        self.assertFalse(self.is_descendant(None, anc))


class _Node:
    """Минимальный «виджет» с цепочкой .master для is_descendant."""

    def __init__(self, master):
        self.master = master


class _FakeWidget:
    """Фейк Tk-виджета: записывает bind/unbind/geometry/destroy, отдаёт
    заранее заданную геометрию. Служит и якорем, и хостом, и popup'ом."""

    def __init__(self, name="w", master=None, toplevel=None):
        self._name = name
        self.master = master
        self._toplevel = toplevel          # None → winfo_toplevel вернёт self
        self._exists = True
        self.raise_toplevel = False        # имитировать TclError в winfo_toplevel
        self.binds: list[tuple] = []       # (seq, fn, add)
        self.unbinds: list[tuple] = []     # (seq, funcid)
        self.geometries: list[str] = []
        self.after_calls: list[tuple] = []
        self.destroyed = False
        self._bind_n = 0
        # Геометрия (значения произвольны, важны лишь как числа).
        self.rootx, self.rooty = 100, 120
        self.height, self.width = 300, 240
        self.reqheight, self.reqwidth = 300, 240
        self.screenwidth, self.screenheight = 1920, 1080
        self.focus_target = None        # что вернёт focus_get()

    def bind(self, seq, fn, add=None):
        self._bind_n += 1
        self.binds.append((seq, fn, add))
        return f"{self._name}:{seq}:{self._bind_n}"

    def unbind(self, seq, funcid=None):
        self.unbinds.append((seq, funcid))

    def winfo_toplevel(self):
        if self.raise_toplevel:
            raise tk.TclError("no toplevel")
        return self._toplevel if self._toplevel is not None else self

    def winfo_exists(self):
        return self._exists

    def winfo_rootx(self):
        return self.rootx

    def winfo_rooty(self):
        return self.rooty

    def winfo_height(self):
        return self.height

    def winfo_width(self):
        return self.width

    def winfo_reqwidth(self):
        return self.reqwidth

    def winfo_reqheight(self):
        return self.reqheight

    def winfo_screenwidth(self):
        return self.screenwidth

    def winfo_screenheight(self):
        return self.screenheight

    def withdraw(self):
        pass

    def deiconify(self):
        pass

    def update_idletasks(self):
        pass

    def focus_set(self):
        pass

    def configure(self, **kwargs):
        pass

    def geometry(self, spec):
        self.geometries.append(spec)

    def after(self, ms, fn=None):
        # НЕ вызываем fn: focus_set нам в тесте не нужен.
        self.after_calls.append((ms, fn))

    def after_idle(self, fn, *a):
        return fn(*a)                   # синхронно для тестов

    def focus_get(self):
        return self.focus_target

    def destroy(self):
        self.destroyed = True
        self._exists = False

    def __str__(self):
        return self._name


class _Event:
    def __init__(self, widget):
        self.widget = widget


def _handler(widget, seq):
    for s, fn, _add in widget.binds:
        if s == seq:
            return fn
    return None


class TestAnchoredPopupController(unittest.TestCase):
    def _make(self, *, follow_host=True):
        """Создаёт контроллер с фейками и открывает popup.

        Возвращает (ctl, anchor, host, popup)."""
        from tg_exporter.ui.modal_utils import AnchoredPopupController

        host = _FakeWidget("host")
        anchor = _FakeWidget("anchor", toplevel=host)
        popup = _FakeWidget("popup")
        self._built = []
        with mock.patch(MOD + ".make_anchored_popup", return_value=popup):
            ctl = AnchoredPopupController(
                anchor, lambda p: self._built.append(p), follow_host=follow_host,
            )
            ctl.open()
        return ctl, anchor, host, popup

    # ---- open ----

    def test_open_builds_positions_and_binds(self):
        ctl, anchor, host, popup = self._make()
        self.assertTrue(ctl.is_open())
        self.assertIs(ctl.popup, popup)
        self.assertEqual(self._built, [popup])           # контент наполнен
        self.assertTrue(popup.geometries)                # позиционирован
        seqs = {s for s, _fn, _add in host.binds}
        self.assertIn("<Button-1>", seqs)
        self.assertIn("<Configure>", seqs)               # follow_host=True
        self.assertIn("<MouseWheel>", seqs)              # закрытие при скролле
        self.assertIn("<Deactivate>", seqs)              # закрытие при alt-tab

    def test_open_when_open_toggles_closed(self):
        ctl, anchor, host, popup = self._make()
        with mock.patch(MOD + ".make_anchored_popup", return_value=_FakeWidget("p2")):
            ctl.open()                                    # повторный open = close
        self.assertFalse(ctl.is_open())
        self.assertTrue(popup.destroyed)

    def test_follow_host_false_skips_configure_bind(self):
        ctl, anchor, host, popup = self._make(follow_host=False)
        seqs = {s for s, _fn, _add in host.binds}
        self.assertIn("<Button-1>", seqs)
        self.assertNotIn("<Configure>", seqs)

    # ---- outside click ----

    def test_click_inside_popup_keeps_open(self):
        ctl, anchor, host, popup = self._make()
        inside = _FakeWidget("inside", toplevel=popup)
        _handler(host, "<Button-1>")(_Event(inside))
        self.assertTrue(ctl.is_open())
        self.assertFalse(popup.destroyed)

    def test_click_on_anchor_keeps_open(self):
        ctl, anchor, host, popup = self._make()
        child = _FakeWidget("btnchild", master=anchor, toplevel=host)
        _handler(host, "<Button-1>")(_Event(child))
        self.assertTrue(ctl.is_open())            # command кнопки сам сделает toggle
        self.assertFalse(popup.destroyed)

    def test_click_outside_closes(self):
        ctl, anchor, host, popup = self._make()
        outside = _FakeWidget("outside", toplevel=host)
        _handler(host, "<Button-1>")(_Event(outside))
        self.assertFalse(ctl.is_open())
        self.assertTrue(popup.destroyed)

    def test_click_swallows_tclerror(self):
        ctl, anchor, host, popup = self._make()
        bad = _FakeWidget("bad")
        bad.raise_toplevel = True
        _handler(host, "<Button-1>")(_Event(bad))   # не должно бросить
        self.assertTrue(ctl.is_open())

    # ---- follow host ----

    def test_configure_of_host_repositions(self):
        ctl, anchor, host, popup = self._make()
        before = len(popup.geometries)
        _handler(host, "<Configure>")(_Event(host))
        self.assertEqual(len(popup.geometries), before + 1)

    def test_configure_of_child_ignored(self):
        ctl, anchor, host, popup = self._make()
        before = len(popup.geometries)
        child = _FakeWidget("child")          # str(child) != str(host)
        _handler(host, "<Configure>")(_Event(child))
        self.assertEqual(len(popup.geometries), before)

    # ---- scroll / app-deactivate ----

    def test_scroll_closes(self):
        # Прокрутка контента под popup'ом: якорь уезжает -> закрываемся,
        # а не висим «отклеенными».
        ctl, anchor, host, popup = self._make()
        _handler(host, "<MouseWheel>")(_Event(_FakeWidget("scrolled")))
        self.assertFalse(ctl.is_open())
        self.assertTrue(popup.destroyed)

    def test_deactivate_closes(self):
        # Приложение ушло на задний план (alt-tab): topmost-popup иначе висит
        # поверх чужого окна.
        ctl, anchor, host, popup = self._make()
        _handler(host, "<Deactivate>")(_Event(host))
        self.assertFalse(ctl.is_open())
        self.assertTrue(popup.destroyed)

    def test_deactivate_of_child_ignored(self):
        ctl, anchor, host, popup = self._make()
        _handler(host, "<Deactivate>")(_Event(_FakeWidget("child")))
        self.assertTrue(ctl.is_open())

    # ---- focus-out (alt-tab / потеря фокуса приложением) ----
    # На Windows для overrideredirect+transient <Deactivate> не приходит, зато
    # popup (он в фокусе) получает <FocusOut>. Закрываемся, только если фокус
    # ушёл из приложения или на чужой виджет — не на день/стрелку/якорь.

    def test_focusout_bound_on_popup(self):
        ctl, anchor, host, popup = self._make()
        seqs = {s for s, _fn, _add in popup.binds}
        self.assertIn("<FocusOut>", seqs)

    def test_focus_left_app_closes(self):
        ctl, anchor, host, popup = self._make()
        popup.focus_target = None                 # фокус ушёл из приложения (alt-tab)
        ctl._close_if_focus_left()
        self.assertFalse(ctl.is_open())
        self.assertTrue(popup.destroyed)

    def test_focus_inside_popup_keeps_open(self):
        ctl, anchor, host, popup = self._make()
        popup.focus_target = _FakeWidget("daybtn", master=popup)
        ctl._close_if_focus_left()
        self.assertTrue(ctl.is_open())            # клик по дню/стрелке не закрывает

    def test_focus_on_anchor_keeps_open(self):
        ctl, anchor, host, popup = self._make()
        popup.focus_target = anchor               # клик по якорю — command сам сделает toggle
        ctl._close_if_focus_left()
        self.assertTrue(ctl.is_open())

    def test_focus_to_other_widget_closes(self):
        ctl, anchor, host, popup = self._make()
        popup.focus_target = _FakeWidget("modalwidget", master=host)
        ctl._close_if_focus_left()
        self.assertFalse(ctl.is_open())

    # ---- close ----

    def test_close_unbinds_everything(self):
        ctl, anchor, host, popup = self._make()
        ctl.close()
        unbound = {s for s, _id in host.unbinds}
        self.assertEqual(unbound, {"<Button-1>", "<Configure>", "<MouseWheel>",
                                   "<Button-4>", "<Button-5>", "<Deactivate>"})
        self.assertTrue(popup.destroyed)
        self.assertFalse(ctl.is_open())
        self.assertIsNone(ctl.popup)

    def test_close_followhost_false_unbinds_all_but_configure(self):
        ctl, anchor, host, popup = self._make(follow_host=False)
        ctl.close()
        unbound = {s for s, _id in host.unbinds}
        self.assertEqual(unbound, {"<Button-1>", "<MouseWheel>",
                                   "<Button-4>", "<Button-5>", "<Deactivate>"})

    def test_close_is_idempotent(self):
        ctl, anchor, host, popup = self._make()
        ctl.close()
        ctl.close()                                # второй раз — без исключений
        self.assertFalse(ctl.is_open())

    # ---- lifecycle wiring ----

    def test_anchor_destroy_closes_popup(self):
        ctl, anchor, host, popup = self._make()
        _handler(anchor, "<Destroy>")(_Event(anchor))
        self.assertTrue(popup.destroyed)
        self.assertFalse(ctl.is_open())

    def test_escape_closes_popup(self):
        ctl, anchor, host, popup = self._make()
        _handler(popup, "<Escape>")(_Event(popup))
        self.assertTrue(popup.destroyed)
        self.assertFalse(ctl.is_open())


if __name__ == "__main__":
    unittest.main()
