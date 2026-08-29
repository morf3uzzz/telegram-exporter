"""
ChatsPage — страница списка чатов: таблица чатов (тип + название), фильтры, поиск.

Глобальные кнопки шапки (Инструкция / Выход / Настройки / Аккаунт)
вынесены в сайдбар. Здесь остаётся только список чатов и его фильтры.
Опции экспорта вынесены в ExportModal (открывается по кнопке).

Список — ttk.Treeview (две колонки: Тип | Название), стилизованный под тёмную
тему через ttk.Style (тема clam, иначе Windows-тема игнорирует цвета). Размер
шрифта и высота строки считаются под текущий масштаб окна (как CTk-виджеты).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

from ..theme import C, RADIUS, SPACING, WIDGET, font, font_display, scaled_font_px
from ..components.button import AppButton
from ..components.date_range_row import DateRangeRow
from ..components.entry import AppEntry
from ..components.tooltip import Tooltip
from ..modal_utils import AnchoredPopupController
from ...core.chat_kind import chat_kind, KIND_LABELS, KIND_ORDER
from ...utils.dates import parse_local_date

if TYPE_CHECKING:
    from ..app import App


_PERIOD_OPTIONS = ["Все время", "Неделя", "Месяц", "3 месяца", "Год", "Свой период"]
_PERIOD_DAYS: dict[str, int] = {
    "Все время": 0, "Неделя": 7, "Месяц": 30, "3 месяца": 90, "Год": 365
}


class ChatsPage(ctk.CTkFrame):
    """
    Страница со списком чатов внутри основного экрана с сайдбаром.

    Layout:
        [Header]     Чаты | Обновить
        [Toolbar]    Папка ▾  |  Период ▾  |  Экспортировать папку
        [DateRange]  (видима только при "Свой период")
        [Search]     🔍 Поиск чатов...   [Тип ▾]
        [Status]     Чатов: 42
        [List]       Таблица: Тип | Название
        [Export]     Экспортировать выбранный чат
    """

    def __init__(self, master, app: "App") -> None:
        super().__init__(master, fg_color="transparent")
        self._app = app
        self._dialogs: list = []
        self._dialog_map: dict[str, object] = {}
        self._folder_names: list[str] = ["Все чаты"]
        self._folder_var = tk.StringVar(value="Все чаты")
        self._period_var = tk.StringVar(value="Все время")
        self._date_from_var = tk.StringVar()
        self._date_to_var = tk.StringVar()
        # Фильтр по типу чата
        self._kind_vars: dict[str, tk.BooleanVar] = {
            k: tk.BooleanVar(value=True) for k in KIND_ORDER
        }
        self._build()

    # ---- Build ----

    def _build(self) -> None:
        # === HEADER страницы (заголовок + Обновить) ===
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=SPACING["xl"], pady=(SPACING["2xl"], SPACING["md"]))
        ctk.CTkLabel(header, text="Чаты", font=font_display(20, "bold"),
                     text_color=C["text"]).pack(side="left")
        AppButton(header, text="Обновить", variant="secondary", size="sm",
                  command=self._app.load_chats).pack(side="right")

        # === TOOLBAR (одна строка, на всю ширину; минимальная высота) ===
        _toolbar_h = WIDGET["entry_h_sm"] + SPACING["sm"] * 2  # 30 + 8 = 38
        toolbar = ctk.CTkFrame(self, fg_color=C["surface"], corner_radius=RADIUS["lg"],
                               height=_toolbar_h)
        toolbar.pack_propagate(False)
        toolbar.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["xs"]))

        _py = SPACING["xs"]

        # Папка
        ctk.CTkLabel(toolbar, text="Папка", font=font(12), text_color=C["text_sec"]).pack(
            side="left", padx=(SPACING["md"], SPACING["xs"]), pady=_py
        )
        self._folder_menu = ctk.CTkOptionMenu(
            toolbar,
            values=self._folder_names,
            variable=self._folder_var,
            command=self._on_folder_change,
            width=160, height=WIDGET["entry_h_sm"],
            font=font(12),
        )
        self._folder_menu.pack(side="left", pady=_py)

        # Разделитель
        ctk.CTkFrame(toolbar, width=1, fg_color=C["border"]).pack(
            side="left", fill="y", padx=SPACING["sm"], pady=SPACING["xs"]
        )

        # Период (применяется только при экспорте папки — для одного чата
        # период выбирается в модалке экспорта).
        ctk.CTkLabel(toolbar, text="Период (папка)", font=font(12), text_color=C["text_sec"]).pack(
            side="left", padx=(0, SPACING["xs"]), pady=_py
        )
        ctk.CTkOptionMenu(
            toolbar,
            values=_PERIOD_OPTIONS,
            variable=self._period_var,
            command=self._on_period_change,
            width=120, height=WIDGET["entry_h_sm"],
            font=font(12),
        ).pack(side="left", pady=_py)

        # Разделитель перед действием
        ctk.CTkFrame(toolbar, width=1, fg_color=C["border"]).pack(
            side="left", fill="y", padx=SPACING["sm"], pady=SPACING["xs"]
        )

        # Кнопка экспорта папки
        AppButton(toolbar, text="Экспортировать папку", variant="secondary", size="sm",
                  command=self._export_folder).pack(side="left", pady=_py)

        # Режим
        self._folder_mode_var = tk.StringVar(value="По чатам")
        ctk.CTkOptionMenu(
            toolbar,
            values=["По чатам", "Один .md на чат", "Один .md на папку"],
            variable=self._folder_mode_var,
            width=150, height=WIDGET["entry_h_sm"],
            font=font(12),
        ).pack(side="left", padx=(SPACING["sm"], 0), pady=_py)

        # Транскрипция (только для экспорта папки — для одного чата
        # настраивается отдельно в модалке экспорта).
        self._folder_transcribe_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            toolbar,
            text="Транскрипция (папка)",
            variable=self._folder_transcribe_var,
            font=font(12),
            text_color=C["text_sec"],
            checkbox_width=16, checkbox_height=16,
            corner_radius=4,
        ).pack(side="left", padx=(SPACING["sm"], SPACING["xs"]), pady=_py)

        hint_lbl = ctk.CTkLabel(
            toolbar,
            text="?",
            font=font(11, "bold"),
            text_color=C["text_sec"],
            cursor="hand2",
            width=16, height=16,
            fg_color=C["card"],
            corner_radius=8,
        )
        hint_lbl.pack(side="left", padx=(0, SPACING["md"]), pady=_py)
        Tooltip(
            hint_lbl,
            "Распознать речь в голосовых сообщениях и видеокружках для всех "
            "чатов выбранной папки. Текст транскрипции попадает в экспорт как "
            "обычное сообщение. Замедляет выгрузку — для больших папок может "
            "занять часы. Провайдер транскрипции и модель Whisper "
            "настраиваются в опциях экспорта одного чата.",
        )

        # === КАСТОМНЫЙ ДИАПАЗОН ДАТ === (скрыт до выбора «Свой период»)
        self._date_range_row = DateRangeRow(
            self,
            var_from=self._date_from_var,
            var_to=self._date_to_var,
            on_change=self._apply_custom_dates,
            leading_pad=SPACING["xl"],
        )

        # === ПОИСК + ФИЛЬТР ТИПА ===
        search_row = ctk.CTkFrame(self, fg_color="transparent")
        search_row.pack(fill="x", padx=SPACING["xl"], pady=(SPACING["md"], SPACING["xs"]))

        self._search_entry = AppEntry(search_row, placeholder_text="🔍  Поиск чатов...")
        self._search_entry.pack(side="left", fill="x", expand=True)
        self._search_entry.bind("<KeyRelease>", self._on_search)

        self._kind_btn = AppButton(
            search_row, text="Тип ▾", variant="secondary", size="md",
            command=self._open_kind_filter, width=92,
        )
        self._kind_btn.pack(side="left", padx=(SPACING["sm"], 0))

        # Popup-фильтр по типу: тот же контроллер, что у календаря, поэтому
        # бесплатно получает flip-вверх при нехватке места снизу и слежение
        # за окном-хостом (раньше фильтр за окном не следовал).
        self._kind_popup = AnchoredPopupController(
            self._kind_btn, self._build_kind_filter,
            fg_color=C["card"], follow_host=True,
        )

        # === СТАТУС ===
        self._status_lbl = ctk.CTkLabel(
            self, text="", font=font(12), text_color=C["text_sec"], anchor="w",
        )
        self._status_lbl.pack(fill="x", padx=SPACING["xl"] + SPACING["xs"], pady=(0, SPACING["xs"]))

        # === СПИСОК ЧАТОВ (ttk.Treeview, 2 колонки) ===
        list_frame = ctk.CTkFrame(self, fg_color="transparent")
        list_frame.pack(fill="both", expand=True, padx=SPACING["md"], pady=(0, SPACING["xs"]))

        # ttk.Style глобален на процесс; в приложении ttk используется ТОЛЬКО
        # здесь (Treeview + его скроллбар), поэтому смена темы на clam ни на что
        # больше не влияет. clam, в отличие от vista/xpnative, разрешает
        # переопределять фон/выделение — без него тёмная тема не применится.
        self._tree_style = ttk.Style()
        try:
            self._tree_style.theme_use("clam")
        except tk.TclError:
            pass

        self._tree = ttk.Treeview(
            list_frame,
            columns=("kind", "name"),
            show="headings",
            selectmode="browse",
            style="Chats.Treeview",
        )
        self._tree.heading("kind", text="Тип", anchor="w")
        self._tree.heading("name", text="Название", anchor="w")
        self._tree.column("kind", width=96, minwidth=64, stretch=False, anchor="w")
        self._tree.column("name", width=320, minwidth=140, stretch=True, anchor="w")

        vsb = ttk.Scrollbar(
            list_frame, orient="vertical", command=self._tree.yview,
            style="Chats.Vertical.TScrollbar",
        )
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<Return>", self._on_double_click)

        self._apply_tree_style()

        # === КНОПКА ЭКСПОРТА ===
        self._export_btn = AppButton(
            self, text="Экспортировать выбранный чат", variant="primary",
            command=self._export_selected,
        )
        self._export_btn.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["lg"]))

    # ---- Масштаб / тема таблицы ----

    def _tree_scale(self) -> float:
        try:
            return ctk.ScalingTracker.get_widget_scaling(self)
        except Exception:
            return 1.0

    def _apply_tree_style(self) -> None:
        """Конфигурирует ttk-стиль таблицы под тёмную тему и текущий масштаб."""
        scale = self._tree_scale()
        fam = font(14)[0]
        size_px = abs(scaled_font_px(scale))             # размер шрифта в пикселях
        rowheight = max(20, int(round(size_px * 2.0)))   # высота строки
        card = self._ctk_color(C["card"])
        text = self._ctk_color(C["text"])
        text_sec = self._ctk_color(C["text_sec"])
        primary = self._ctk_color(C["primary"])
        surface = self._ctk_color(C["surface"])
        border = self._ctk_color(C["border"])

        st = self._tree_style
        st.configure(
            "Chats.Treeview",
            background=card, fieldbackground=card, foreground=text,
            borderwidth=0, relief="flat", rowheight=rowheight, font=(fam, -size_px),
        )
        st.map(
            "Chats.Treeview",
            background=[("selected", primary)],
            foreground=[("selected", "#FFFFFF")],
        )
        st.configure(
            "Chats.Treeview.Heading",
            background=surface, foreground=text_sec, relief="flat",
            borderwidth=0, font=(fam, -size_px), padding=(SPACING["xs"], SPACING["xs"]),
        )
        st.map("Chats.Treeview.Heading", background=[("active", surface)])
        st.configure(
            "Chats.Vertical.TScrollbar",
            background=surface, troughcolor=card, bordercolor=card,
            arrowcolor=text_sec, borderwidth=0,
        )
        st.map("Chats.Vertical.TScrollbar", background=[("active", border)])
        try:
            self._tree.column("kind", width=int(round(96 * scale)))
        except Exception:
            pass

    def apply_scale(self) -> None:
        """Пересчитать стиль таблицы под новый масштаб (live-смена из настроек)."""
        try:
            self._apply_tree_style()
        except Exception:
            pass

    # ---- Public API ----

    def show_loading(self, text: str = "Загрузка чатов...") -> None:
        self._status_lbl.configure(text=text)
        self._tree.delete(*self._tree.get_children())

    def show_refreshing(self) -> None:
        """Мягкий «обновление» без чистки списка — старые чаты остаются
        видимыми, пока фон не вернёт новые. Иначе при медленной/висящей
        выгрузке у пользователя пустой экран и впечатление, что приложение
        зависло — тогда как `_all_dialogs` ещё содержит прошлый снапшот."""
        self._status_lbl.configure(text="Обновление списка чатов...")

    def render_chats(self, dialogs: list) -> None:
        self._dialogs = dialogs or []
        self._dialog_map = {}
        self._tree.delete(*self._tree.get_children())
        if not self._dialogs:
            self._status_lbl.configure(text="Ничего не найдено")
            return
        for i, d in enumerate(self._dialogs):
            iid = str(i)
            kind_label = KIND_LABELS.get(chat_kind(d), "")
            self._tree.insert(
                "", tk.END, iid=iid,
                values=(kind_label, d.name or "Без названия"),
            )
            self._dialog_map[iid] = d
        self._status_lbl.configure(text=f"Чатов: {len(self._dialogs)}")

    def set_folders(self, folder_names: list[str]) -> None:
        self._folder_names = ["Все чаты"] + (folder_names or [])
        self._folder_menu.configure(values=self._folder_names)
        if self._folder_var.get() not in self._folder_names:
            self._folder_var.set("Все чаты")

    def set_status(self, text: str) -> None:
        self._status_lbl.configure(text=text)

    def selected_dialog(self) -> Optional[object]:
        sel = self._tree.selection()
        if not sel:
            return None
        return self._dialog_map.get(sel[0])

    # ---- Handlers ----

    def _on_search(self, _event=None) -> None:
        self._app.filter_chats(self._search_entry.get().strip())

    def _on_folder_change(self, value: str) -> None:
        self._app.set_current_folder(value)
        self._app.filter_chats(self._search_entry.get().strip())

    def _on_period_change(self, value: str) -> None:
        if value == "Свой период":
            if not self._date_range_row.winfo_ismapped():
                self._date_range_row.pack(fill="x", pady=(0, SPACING["xs"]), after=self._folder_menu.master)
            self._app.set_date_period(0)
        else:
            if self._date_range_row.winfo_ismapped():
                self._date_range_row.pack_forget()
            self._app.set_custom_date_range(None, None)
            self._app.set_date_period(_PERIOD_DAYS.get(value, 0))

    def _apply_custom_dates(self, *_args) -> None:
        date_from = parse_local_date(self._date_from_var.get())
        date_to = parse_local_date(self._date_to_var.get())
        self._app.set_custom_date_range(date_from, date_to)

    def _export_selected(self) -> None:
        dialog = self.selected_dialog()
        if dialog is None:
            self._status_lbl.configure(text="Выберите чат из списка")
            return
        self._app.show_export_dialog(dialog)

    def _on_double_click(self, _event=None) -> None:
        self._export_selected()

    def _export_folder(self) -> None:
        self._app.export_current_folder(
            mode=self._folder_mode_var.get(),
            transcribe=self._folder_transcribe_var.get(),
        )

    # ---- Фильтр по типу чата (popup с галочками) ----

    def _open_kind_filter(self) -> None:
        # Контроллер сам делает toggle: повторный клик по кнопке закрывает popup.
        self._kind_popup.open()

    def _build_kind_filter(self, popup) -> None:
        inner = ctk.CTkFrame(popup, fg_color="transparent")
        inner.pack(padx=SPACING["sm"], pady=SPACING["sm"])
        ctk.CTkLabel(
            inner, text="Показывать типы", font=font(12, "bold"), text_color=C["text"],
        ).pack(anchor="w", pady=(0, SPACING["xs"]))
        for k in KIND_ORDER:
            ctk.CTkCheckBox(
                inner, text=KIND_LABELS[k], variable=self._kind_vars[k],
                command=self._on_kind_toggle, font=font(13), text_color=C["text"],
                checkbox_width=18, checkbox_height=18, corner_radius=4,
            ).pack(anchor="w", pady=2)

    def _on_kind_toggle(self) -> None:
        enabled = {k for k, v in self._kind_vars.items() if v.get()}
        self._app.set_kind_filter(enabled)
        self._app.filter_chats(self._search_entry.get().strip())
        n, total = len(enabled), len(KIND_ORDER)
        try:
            self._kind_btn.configure(text="Тип ▾" if n == total else f"Тип ({n}) ▾")
        except Exception:
            pass

    # ---- Helpers ----

    def _ctk_color(self, pair) -> str:
        """Возвращает строку цвета (light/dark) для нативных tk/ttk-виджетов."""
        return pair[0] if ctk.get_appearance_mode() == "Light" else pair[1]
