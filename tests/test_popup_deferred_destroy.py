"""
Popup нельзя разрушать синхронно внутри Tk-обработчика события.

Регрессия (краш SIGSEGV в TkMacOSXGetHostToplevel / TkWmDeadWindow):
close() вызывался прямо из биндингов (<Button-1> хоста, <Deactivate>,
<MouseWheel>, <Escape>, <Destroy> якоря) и звал popup.destroy(). Tk в этот
момент ещё идёт по цепочке биндингов уничтожаемого окна и после destroy
читает освобождённую память.

Разрушение откладывается через after_idle: обработчик успевает вернуться,
Tk доигрывает событие, и только потом окно удаляется.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from tg_exporter.ui.modal_utils import AnchoredPopupController


class _FakePopup:
    def __init__(self):
        self.destroyed = False
        self.idle_calls = []

    def winfo_exists(self):
        return not self.destroyed

    def destroy(self):
        self.destroyed = True

    def after_idle(self, fn):
        self.idle_calls.append(fn)

    def bind(self, *_a, **_k):
        return "id"


def _controller_with_popup():
    c = AnchoredPopupController.__new__(AnchoredPopupController)
    c._popup = _FakePopup()
    c._host = None
    c._host_binds = []
    return c


def test_close_defers_destroy_to_idle():
    """close() не разрушает окно прямо в обработчике."""
    c = _controller_with_popup()
    popup = c._popup
    c.close()
    assert popup.destroyed is False, "destroy() выполнен синхронно — это и есть краш"
    assert popup.idle_calls, "разрушение не запланировано через after_idle"


def test_deferred_destroy_actually_runs():
    """Отложенный вызов действительно уничтожает окно."""
    c = _controller_with_popup()
    popup = c._popup
    c.close()
    for fn in list(popup.idle_calls):
        fn()
    assert popup.destroyed is True


def test_close_clears_state_immediately():
    """Ссылка на popup снимается сразу — is_open() больше не врёт."""
    c = _controller_with_popup()
    c.close()
    assert c._popup is None


def test_double_close_is_safe():
    c = _controller_with_popup()
    popup = c._popup
    c.close()
    c.close()
    for fn in list(popup.idle_calls):
        fn()
    assert popup.destroyed is True
