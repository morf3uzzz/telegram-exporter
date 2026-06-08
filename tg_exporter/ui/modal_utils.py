"""
Общие утилиты для модальных окон и popup'ов:
- prepare_modal/show_modal: появление модалки без вспышки, фон, transient, фокус.
- make_anchored_popup: overrideredirect-Toplevel (для тултипов/дропдаунов).
- setup_smooth_scroll: одинаковая скорость прокрутки колесом на всех платформах.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Optional

import customtkinter as ctk

from .theme import C


def prepare_modal(modal, parent, width: int, height: int, title: str) -> None:
    """
    ВЫЗЫВАТЬ ПЕРВЫМ ДЕЛОМ В __init__ модалки (сразу после super().__init__).

    - Прячет окно до построения UI (нет вспышки «CTkToplevel» в углу).
    - Сразу задаёт правильную геометрию и заголовок.
    - Заливает фон в цвет приложения (нет чёрной подложки).
    """
    modal.withdraw()  # ВАЖНО: до любых других вызовов — иначе видна вспышка.
    modal.title(title)
    modal.configure(fg_color=C["bg"])

    # Геометрия — задаём сразу, до отрисовки.
    parent.update_idletasks()
    px = parent.winfo_rootx()
    py = parent.winfo_rooty()
    pw = parent.winfo_width() or width
    ph = parent.winfo_height() or height
    x = px + max(0, (pw - width) // 2)
    y = py + max(0, (ph - height) // 2)
    modal.geometry(f"{width}x{height}+{x}+{y}")


def show_modal(modal, parent, resizable: tuple[bool, bool] = (False, False)) -> None:
    """
    ВЫЗЫВАТЬ В КОНЦЕ __init__ — после построения UI.

    - Делает окно видимым уже на правильной позиции и с готовым контентом.
    - Привязывает к родителю (не теряется за главным).
    - Включает grab + фокус.
    - Биндит «вспышку» при клике по заблокированному родителю.
    """
    modal.resizable(*resizable)
    try:
        modal.transient(parent)
    except Exception:
        pass
    modal.deiconify()
    modal.lift()
    modal.focus_force()
    modal.grab_set()
    _bind_parent_focus_hint(modal, parent)


def _bind_parent_focus_hint(modal, parent) -> None:
    """
    Когда пользователь кликает по заблокированному родителю — модалка
    мигает / лифтится, подсказывая «вот где взаимодействие».
    """
    flash_state = {"flashing": False}

    def _flash():
        if flash_state["flashing"] or not modal.winfo_exists():
            return
        flash_state["flashing"] = True
        try:
            modal.lift()
            modal.focus_force()
            # Кратковременная «вспышка» через bell + изменение alpha
            modal.bell()
            try:
                modal.attributes("-alpha", 0.7)
                modal.after(80, lambda: modal.winfo_exists() and modal.attributes("-alpha", 1.0))
            except Exception:
                pass
        finally:
            modal.after(200, lambda: flash_state.update(flashing=False))

    def _on_parent_click(_event):
        if modal.winfo_exists():
            _flash()

    # Биндим только на сам корневой Toplevel/Canvas, не на детей —
    # grab_set уже блокирует обычные виджеты.
    try:
        parent.bind("<Button-1>", _on_parent_click, add="+")

        def _cleanup(_e=None):
            try:
                parent.unbind("<Button-1>", _on_parent_click)  # noqa
            except Exception:
                pass

        modal.bind("<Destroy>", _cleanup, add="+")
    except Exception:
        pass


def make_anchored_popup(parent, x: int, y: int, fg_color=None) -> ctk.CTkToplevel:
    """Создаёт overrideredirect-Toplevel в координатах (x, y) экрана.

    Используется для тултипов и popup-дропдаунов: без рамки, всегда
    поверх, без записи в taskbar. wm_overrideredirect/attributes могут
    бросить TclError на нестандартных WM — поглощаем, окно всё равно
    останется рабочим.
    """
    popup = ctk.CTkToplevel(parent)
    try:
        popup.wm_overrideredirect(True)
    except tk.TclError:
        pass
    try:
        popup.attributes("-topmost", True)
    except tk.TclError:
        pass
    if fg_color is not None:
        popup.configure(fg_color=fg_color)
    popup.geometry(f"+{x}+{y}")
    return popup


def resolve_popup_position(
    anchor_left: int,
    anchor_top: int,
    anchor_bottom: int,
    popup_w: int,
    popup_h: int,
    screen_w: int,
    screen_h: int,
    margin: int = 4,
) -> tuple[int, int]:
    """Куда поставить popup (календарь/дропдаун) относительно кнопки-якоря.

    По горизонтали: левый край у якоря, но не вылезаем за края экрана.
    По вертикали: предпочтительно вниз под якорь; если снизу не помещается —
    разворачиваем вверх над якорем. Если popup выше обеих зон — выбираем
    сторону с большим запасом и прижимаем к краю.

    Допущение: один монитор. screen_w/screen_h — размеры ОСНОВНОГО экрана
    (winfo_screenwidth/height), а координаты якоря — виртуального рабочего
    стола. На мультимониторе слева/сверху от основного (отрицательные
    координаты) клэмп может прижать popup к краю основного экрана. Для
    текущего UI это приемлемо.

    Чистая функция (без Tk) — чтобы геометрию можно было покрыть тестами.
    """
    # X
    x = anchor_left
    if x + popup_w > screen_w - margin:
        x = screen_w - popup_w - margin
    if x < margin:
        x = margin

    # Y
    if anchor_bottom + margin + popup_h <= screen_h:
        y = anchor_bottom + margin
    else:
        above = anchor_top - margin - popup_h
        if above >= margin:
            y = above
        else:
            room_below = screen_h - (anchor_bottom + margin)
            room_above = anchor_top - margin
            y = margin if room_above >= room_below else (screen_h - popup_h - margin)
            if y < margin:
                y = margin
    return int(x), int(y)


def setup_smooth_scroll(modal, scrollable_frame) -> None:
    """
    Ускоряет колесо мыши на macOS (встроенный скролл CTkScrollableFrame там
    мелковат: использует -event.delta без множителя).

    ВАЖНО: НЕ используем `bind_all`/`unbind_all` — CustomTkinter запрещает их
    на своих виджетах (CTkBaseClass бросает AttributeError). Вместо этого
    биндим напрямую на внутренний `_parent_canvas` (это голый tkinter.Canvas,
    где bind разрешён). Возвращаем "break", чтобы прервать цепочку обработчиков
    и не задвоить со встроенным `bind_all`-обработчиком CTkScrollableFrame
    (widget-биндинг с "break" выполняется раньше тега `all` и прерывает его).

    На Win/Linux ничего не делаем — встроенная скорость там уже адекватная
    (event.delta/6), а кросс-тег "break" мог бы её сломать.

    Первый аргумент `modal` сохранён для совместимости вызовов, но не нужен.
    """
    if sys.platform != "darwin":
        return
    try:
        canvas = scrollable_frame._parent_canvas
    except AttributeError:
        return

    def _scroll_fn(event):
        # macOS: event.delta = ±1..±5 (мелкие тики). Множитель 3 даёт привычную
        # скорость трекпада/колеса.
        step = -event.delta * 3
        if step:
            canvas.yview_scroll(step, "units")
        return "break"

    canvas.bind("<MouseWheel>", _scroll_fn, add="+")
