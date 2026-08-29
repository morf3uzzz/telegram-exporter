"""
Общие утилиты для модальных окон и popup'ов:
- prepare_modal/show_modal: появление модалки без вспышки, фон, transient, фокус.
- make_anchored_popup: overrideredirect-Toplevel (для тултипов/дропдаунов).
- setup_smooth_scroll: одинаковая скорость прокрутки колесом на всех платформах.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable, Optional

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


def is_descendant(widget, ancestor) -> bool:
    """True, если widget — это сам ancestor или лежит в его поддереве.

    Идём вверх по цепочке .master. Используется, чтобы отличить клик по
    кнопке-якорю (или её внутренним подвиджетам CTk) от клика «снаружи»:
    по якорю popup закрывать не надо — его command сам сделает toggle.
    """
    while widget is not None:
        if widget is ancestor:
            return True
        widget = getattr(widget, "master", None)
    return False


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


class AnchoredPopupController:
    """Жизненный цикл одного «прицельного» popup'а под кнопкой-якорем
    (дропдаун, календарь и т.п.).

    Инкапсулирует общий для таких popup'ов сценарий:
      - создать overrideredirect-Toplevel под якорем (make_anchored_popup);
      - наполнить контентом (callback build_content);
      - измерить и поставить вниз под якорь либо вверх над ним, если снизу
        не помещается (resolve_popup_position);
      - закрыть по <Escape>, клику вне popup'а, <Destroy> якоря, прокрутке
        контента (<MouseWheel>) и уходе приложения на задний план (<FocusOut>
        popup'а с проверкой focus_get; <Deactivate> хоста как backup);
      - опционально следить за окном-хостом и двигать popup при его
        перемещении/ресайзе (<Configure>).

    Один контроллер обслуживает один popup за раз. Повторный open() при уже
    открытом popup'е его закрывает — это удобно вешать прямо на command
    кнопки-якоря: клик по кнопке = toggle.

    build_content получает готовый (но ещё скрытый) Toplevel и наполняет его;
    кнопки «Закрыть»/выбора значения внутри контента зовут close().
    """

    def __init__(
        self,
        anchor: tk.Misc,
        build_content: Callable[[ctk.CTkToplevel], None],
        *,
        fg_color=None,
        follow_host: bool = True,
        gap: int = 4,
    ) -> None:
        self._anchor = anchor
        self._build_content = build_content
        self._fg_color = fg_color
        self._follow_host = follow_host
        self._gap = gap

        self._popup: Optional[ctk.CTkToplevel] = None
        self._host: Optional[tk.Misc] = None
        self._host_binds: list[tuple[str, str]] = []   # (sequence, funcid)

        # Если якорь уничтожают, пока popup открыт — иначе popup останется
        # висеть отдельным окном.
        anchor.bind("<Destroy>", self._on_anchor_destroy, add="+")

    # ---- Public API ----

    @property
    def popup(self) -> Optional[ctk.CTkToplevel]:
        return self._popup

    def is_open(self) -> bool:
        return self._popup is not None and self._popup.winfo_exists()

    def open(self) -> None:
        """Открыть popup. Если уже открыт — закрыть (toggle)."""
        if self.is_open():
            self.close()
            return

        try:
            anchor_left = self._anchor.winfo_rootx()
            anchor_top = self._anchor.winfo_rooty()
            anchor_bottom = anchor_top + self._anchor.winfo_height()
        except tk.TclError:
            return

        # Создаём, прячем, наполняем — чтобы popup не мелькнул в промежуточной
        # позиции до измерения.
        popup = make_anchored_popup(
            self._anchor, anchor_left, anchor_bottom + self._gap,
            fg_color=self._fg_color,
        )
        try:
            popup.withdraw()
        except tk.TclError:
            pass

        self._build_content(popup)

        # Финальная позиция по фактическому размеру popup и размеру экрана.
        try:
            popup.update_idletasks()
            px, py = resolve_popup_position(
                anchor_left, anchor_top, anchor_bottom,
                popup.winfo_reqwidth(), popup.winfo_reqheight(),
                self._anchor.winfo_screenwidth(),
                self._anchor.winfo_screenheight(),
            )
            popup.geometry(f"+{px}+{py}")
        except tk.TclError:
            pass
        try:
            popup.deiconify()
        except tk.TclError:
            pass

        popup.bind("<Escape>", lambda _e: self.close())
        # На Windows для overrideredirect+transient <Deactivate> хоста при
        # alt-tab НЕ приходит, зато popup (он в фокусе) получает <FocusOut>.
        # Закрываемся, только если фокус ушёл из приложения / на чужой виджет.
        popup.bind("<FocusOut>", self._on_popup_focus_out, add="+")
        popup.after(50, popup.focus_set)
        self._popup = popup

        # Закрытие по клику вне popup'а биндим на Toplevel, в котором живёт
        # якорь (модалка/главное окно): popup — отдельный Toplevel, его клики
        # сюда не приходят, поэтому внутри него можно спокойно тыкать по
        # содержимому.
        try:
            host = self._anchor.winfo_toplevel()
        except tk.TclError:
            host = None
        if host is not None:
            self._host = host
            self._bind_host("<Button-1>", self._on_outside_click)
            # Popup — отдельное окно с абсолютными координатами, само за окном
            # не следует. Двигаем его за якорем при перемещении/ресайзе хоста.
            if self._follow_host:
                self._bind_host("<Configure>", self._on_host_configure)
            # Контент под popup'ом прокрутили — якорь уехал. Точно следовать за
            # скроллом внутри CTkScrollableFrame ненадёжно, поэтому закрываемся.
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self._bind_host(seq, self._on_scroll)
            # Приложение ушло на задний план (alt-tab/клик в другое окно):
            # topmost overrideredirect-popup иначе висит поверх чужого окна.
            self._bind_host("<Deactivate>", self._on_deactivate)

    def _bind_host(self, sequence: str, callback) -> None:
        funcid = self._host.bind(sequence, callback, add="+")
        self._host_binds.append((sequence, funcid))

    def close(self) -> None:
        if self._host is not None:
            for sequence, funcid in self._host_binds:
                try:
                    self._host.unbind(sequence, funcid)
                except tk.TclError:
                    pass
        self._host = None
        self._host_binds = []
        if self._popup is not None:
            try:
                if self._popup.winfo_exists():
                    self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None

    # ---- Internal ----

    def _on_anchor_destroy(self, _event=None) -> None:
        self.close()

    def _on_outside_click(self, event) -> None:
        if not self.is_open():
            return
        w = event.widget
        try:
            toplevel = w.winfo_toplevel()
        except tk.TclError:
            return
        # Клик внутри самого popup'а — игнор (обычно сюда не доходит: popup —
        # отдельный Toplevel).
        if toplevel is self._popup:
            return
        # Клик по якорю (или его внутренним подвиджетам CTk) — пусть сработает
        # его command и сам сделает toggle. Иначе будет close+reopen.
        if is_descendant(w, self._anchor):
            return
        self.close()

    def _on_host_configure(self, event=None) -> None:
        """Двигает открытый popup за якорем при сдвиге/ресайзе окна-хоста."""
        if not self.is_open():
            return
        # Только <Configure> самого окна-хоста, не его детей: bindtags Tk
        # прокидывают событие на родителя, иначе — шквал лишних вызовов.
        if event is not None and self._host is not None:
            if str(event.widget) != str(self._host):
                return
        try:
            anchor_left = self._anchor.winfo_rootx()
            anchor_top = self._anchor.winfo_rooty()
            anchor_bottom = anchor_top + self._anchor.winfo_height()
            pw = max(self._popup.winfo_width(), self._popup.winfo_reqwidth())
            ph = max(self._popup.winfo_height(), self._popup.winfo_reqheight())
            px, py = resolve_popup_position(
                anchor_left, anchor_top, anchor_bottom, pw, ph,
                self._anchor.winfo_screenwidth(),
                self._anchor.winfo_screenheight(),
            )
            self._popup.geometry(f"+{px}+{py}")
        except tk.TclError:
            pass

    def _on_scroll(self, _event=None) -> None:
        # Контент под popup'ом прокрутили: якорь уехал — закрываемся, иначе
        # popup «отклеивается» и висит на старом месте.
        if self.is_open():
            self.close()

    def _on_deactivate(self, event=None) -> None:
        # Приложение потеряло передний план (alt-tab): topmost-popup иначе
        # остаётся висеть поверх другого окна. На Windows для нашего popup'а
        # это событие обычно не приходит (основной путь — _on_popup_focus_out),
        # но оставляем как backup для других платформ.
        if event is not None and self._host is not None:
            if str(event.widget) != str(self._host):
                return
        if self.is_open():
            self.close()

    def _on_popup_focus_out(self, _event=None) -> None:
        # Откладываем проверку: к моменту обработки фокус ещё «переезжает» на
        # новый виджет/окно.
        popup = self._popup
        if popup is not None:
            try:
                popup.after_idle(self._close_if_focus_left)
            except tk.TclError:
                pass

    def _close_if_focus_left(self) -> None:
        if not self.is_open():
            return
        try:
            focused = self._popup.focus_get()
        except (KeyError, tk.TclError):
            return
        # focus_get() == None -> фокус ушёл из приложения (alt-tab) -> закрыть.
        if focused is None:
            self.close()
            return
        # Фокус внутри popup'а (клик по дню/стрелке) или на якоре (его command
        # сам сделает toggle) — не закрываем.
        if is_descendant(focused, self._popup) or is_descendant(focused, self._anchor):
            return
        self.close()


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
