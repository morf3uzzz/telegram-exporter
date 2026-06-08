"""DatePickerButton — кнопка-календарь со встроенным popup-выбором даты.

Раньше использовали tkcalendar, но он на ttk и плохо тематизируется под
CustomTkinter (тонкие чёрно-белые стрелки, мелкий шрифт), плюс лицензирован
под GPLv3, что тянет лицензию итогового бандла.

Сейчас — свой простой календарь на CTkButton: нативно выглядит в светлой
и тёмной теме, без внешних зависимостей.
"""

from __future__ import annotations

import datetime
import tkinter as tk
import customtkinter as ctk
from typing import Callable, Optional

from .button import AppButton
from ..modal_utils import AnchoredPopupController
from ..theme import C, RADIUS, SPACING, font


_WEEKDAY_LABELS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
_MONTH_LABELS = (
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
)


class DatePickerButton(AppButton):
    """
    Маленькая кнопка с иконкой календаря. По клику открывает popup
    с выбором года/месяца/дня; выбранная дата в формате YYYY-MM-DD
    пишется в переданный StringVar.

    Текстовое поле даты остаётся независимым — пользователь может либо
    набрать дату руками, либо выбрать кликом. После выбора дополнительно
    вызывается опциональный on_pick — для случаев, когда родитель
    биндится на FocusOut/Return текстового поля и не получает уведомление
    при программной установке переменной.
    """

    def __init__(
        self,
        master,
        target_var: tk.StringVar,
        on_pick: Optional[Callable[[], None]] = None,
        size: str = "sm",
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            text="📅",
            variant="ghost",
            size=size,
            command=self._open_picker,
            width=30,
            **kwargs,
        )
        self._target = target_var
        self._on_pick = on_pick
        # Общий контроллер popup'а: создание/позиционирование (с flip-вверх),
        # закрытие по Escape, клику вне и <Destroy> кнопки, слежение за
        # окном-хостом (двигает календарь при перемещении/ресайзе окна).
        self._date_popup = AnchoredPopupController(
            self, self._build_calendar, fg_color=C["card"], follow_host=True,
        )

    # ---- Internal ----

    def _open_picker(self) -> None:
        # Контроллер сам делает toggle: повторный клик по иконке закрывает
        # уже открытый popup.
        self._date_popup.open()

    def _build_calendar(self, popup) -> None:
        initial = datetime.date.today()
        raw = (self._target.get() or "").strip()
        if raw:
            try:
                initial = datetime.date.fromisoformat(raw[:10])
            except ValueError:
                pass
        _CalendarFrame(popup, initial=initial, on_pick=self._commit).pack(
            padx=SPACING["sm"], pady=(SPACING["sm"], SPACING["xs"]),
        )
        AppButton(
            popup, text="Закрыть", variant="ghost", size="sm",
            command=self._date_popup.close,
        ).pack(padx=SPACING["sm"], pady=(0, SPACING["sm"]), fill="x")

    def _commit(self, value: str) -> None:
        self._target.set(value)
        if self._on_pick is not None:
            self._on_pick()
        self._date_popup.close()


class _CalendarFrame(ctk.CTkFrame):
    """Сетка с выбором дня + навигация по месяцам/годам.

    Кнопки ячеек создаются один раз, рендер меняет им только текст,
    цвет и команду — без пересоздания виджетов.
    """

    def __init__(
        self,
        master,
        initial: datetime.date,
        on_pick: Callable[[str], None],
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._year = initial.year
        self._month = initial.month
        self._selected = initial
        self._on_pick = on_pick
        self._day_buttons: list[ctk.CTkButton] = []
        self._build()
        self._render()

    def _build(self) -> None:
        # Навигация: ◀◀ ◀  Месяц Год  ▶ ▶▶
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill="x", pady=(0, SPACING["sm"]))
        AppButton(
            nav, text="«", variant="ghost", size="sm", width=28,
            command=lambda: self._shift_year(-1),
        ).pack(side="left")
        AppButton(
            nav, text="‹", variant="ghost", size="sm", width=24,
            command=lambda: self._shift_month(-1),
        ).pack(side="left", padx=(SPACING["xs"], 0))
        self._title_lbl = ctk.CTkLabel(
            nav, text="", font=font(13, "bold"), text_color=C["text"],
        )
        self._title_lbl.pack(side="left", expand=True)
        # Зеркально левой стороне: год — снаружи, месяц — внутри.
        # Итог симметричен: « ‹  Месяц Год  › »
        AppButton(
            nav, text="»", variant="ghost", size="sm", width=28,
            command=lambda: self._shift_year(1),
        ).pack(side="right")
        AppButton(
            nav, text="›", variant="ghost", size="sm", width=24,
            command=lambda: self._shift_month(1),
        ).pack(side="right", padx=(0, SPACING["xs"]))

        # Заголовки дней недели
        headers = ctk.CTkFrame(self, fg_color="transparent")
        headers.pack(fill="x", pady=(0, SPACING["xs"]))
        for i, label in enumerate(_WEEKDAY_LABELS):
            color = C["primary"] if i >= 5 else C["text_sec"]
            ctk.CTkLabel(
                headers, text=label, font=font(11, "bold"),
                text_color=color, width=32,
            ).grid(row=0, column=i, padx=1)

        # Сетка 6×7 кнопок-дней
        grid_frame = ctk.CTkFrame(self, fg_color="transparent")
        grid_frame.pack()
        for row in range(6):
            for col in range(7):
                btn = ctk.CTkButton(
                    grid_frame, text="",
                    width=32, height=28,
                    corner_radius=RADIUS["sm"],
                    font=font(12),
                    border_width=0,
                )
                btn.grid(row=row, column=col, padx=1, pady=1)
                self._day_buttons.append(btn)

    # ---- Navigation ----

    def _shift_month(self, delta: int) -> None:
        m = self._month + delta
        y = self._year
        while m > 12:
            m -= 12
            y += 1
        while m < 1:
            m += 12
            y -= 1
        self._month = m
        self._year = y
        self._render()

    def _shift_year(self, delta: int) -> None:
        self._year += delta
        self._render()

    # ---- Render ----

    def _render(self) -> None:
        self._title_lbl.configure(
            text=f"{_MONTH_LABELS[self._month - 1]} {self._year}",
        )

        first = datetime.date(self._year, self._month, 1)
        start = first - datetime.timedelta(days=first.weekday())
        today = datetime.date.today()

        for i, btn in enumerate(self._day_buttons):
            d = start + datetime.timedelta(days=i)
            in_month = (d.month == self._month)
            is_selected = (d == self._selected)
            is_today = (d == today)

            if is_selected:
                fg = C["primary"]
                text_color = C["primary_text"]
                hover = C["primary_h"]
            else:
                fg = "transparent"
                text_color = C["text"] if in_month else C["text_dim"]
                hover = C["card_hover"]

            btn.configure(
                text=str(d.day),
                fg_color=fg,
                text_color=text_color,
                hover_color=hover,
                border_width=1 if is_today and not is_selected else 0,
                border_color=C["primary"] if is_today else C["card"],
                command=lambda d=d: self._on_pick(d.isoformat()),
            )
