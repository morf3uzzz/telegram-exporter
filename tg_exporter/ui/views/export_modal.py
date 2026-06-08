"""
ExportModal — диалог настройки экспорта + отображение прогресса.

Открывается когда пользователь нажимает "Экспортировать".
Содержит все опции экспорта и прогресс-бар.
После завершения показывает результат с кнопкой открыть папку.
"""

from __future__ import annotations

import os
import platform
import subprocess
from typing import TYPE_CHECKING, Optional
import tkinter as tk
import customtkinter as ctk

from ..theme import C, SPACING, WIDGET, font, font_display
from ..components.button import AppButton
from ..components.date_range_row import DateRangeRow
from ..components.entry import AppEntry
from ..components.progress_bar import ExportProgressWidget
from ..modal_utils import prepare_modal, show_modal, setup_smooth_scroll
from ...utils.dates import parse_local_date
from ...core import forum

if TYPE_CHECKING:
    from ..app import App


_WHISPER_MODELS = [
    ("tiny — быстро, ~1 GB RAM", "tiny"),
    ("base — быстро, ~1 GB RAM", "base"),
    ("small — ~2 GB RAM", "small"),
    ("medium — ~5 GB RAM", "medium"),
    ("large-v2 — ~10 GB, медленно", "large-v2"),
    ("large-v3 — ~10 GB, точнее v2", "large-v3"),
]

_PERIOD_OPTIONS = ["Все время", "Неделя", "Месяц", "3 месяца", "Год", "Свой период"]
_PERIOD_DAYS = {"Все время": 0, "Неделя": 7, "Месяц": 30, "3 месяца": 90, "Год": 365}


class ExportModal(ctk.CTkToplevel):
    """
    Диалог с:
    - Секцией опций экспорта (формат, транскрипция, медиа, аналитика)
    - Кнопкой запуска
    - Прогресс-баром после запуска
    - Результатом
    """

    def __init__(self, app: "App", dialog) -> None:
        super().__init__(app)
        prepare_modal(self, app, 600, 680, f"Экспорт: {getattr(dialog, 'name', 'Чат')}")
        self._app = app
        self._dialog = dialog
        self._exporting = False
        self._export_dir: Optional[str] = None
        self._build()
        show_modal(self, app, resizable=(False, True))
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self.after(100, lambda: setup_smooth_scroll(self, self._scroll))

    # ---- Build ----

    def _build(self) -> None:
        cfg = self._app.config
        pad = SPACING["xl"]

        # Скроллируемый контейнер для опций
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=C["bg"])
        scroll = self._scroll
        scroll.pack(fill="both", expand=True, padx=0, pady=0)

        # Заголовок
        name = getattr(self._dialog, "name", "Чат") or "Чат"
        ctk.CTkLabel(
            scroll, text=name, font=font_display(16, "bold"), text_color=C["text"], anchor="w",
        ).pack(padx=pad, pady=(pad, SPACING["xl"]), fill="x")

        # ---- Топики форума (только для форум-супергрупп) ----
        self._topic_vars: dict[int, tk.BooleanVar] = {}
        self._topics: list = []
        self._topics_section = ctk.CTkFrame(scroll, fg_color="transparent")
        entity = getattr(self._dialog, "entity", None)
        if forum.is_forum(entity):
            self._topics_section.pack(fill="x", padx=0, pady=0)
            self._add_section(self._topics_section, "Топики форума")
            self._topics_all_var = tk.BooleanVar(value=True)
            self._topics_all_cb = ctk.CTkCheckBox(
                self._topics_section, text="Все топики", variable=self._topics_all_var,
                font=font(13, "bold"), text_color=C["text"], command=self._on_topics_all,
            )
            self._topics_all_cb.pack(anchor="w", padx=SPACING["xl"], pady=(0, SPACING["xs"]))
            self._topics_list_frame = ctk.CTkFrame(self._topics_section, fg_color="transparent")
            self._topics_list_frame.pack(fill="x", padx=SPACING["xl"])
            self._topics_status = ctk.CTkLabel(
                self._topics_list_frame, text="Загрузка топиков…",
                font=font(12), text_color=C["text_sec"], anchor="w",
            )
            self._topics_status.pack(anchor="w")
            self._app.load_forum_topics(self._dialog, self)

        # ---- Период ----
        self._period_var = tk.StringVar(value="Все время")
        self._add_section(scroll, "Период")
        self._period_row = ctk.CTkFrame(scroll, fg_color="transparent")
        self._period_row.pack(fill="x", padx=pad, pady=(0, SPACING["md"]))
        ctk.CTkOptionMenu(
            self._period_row,
            values=_PERIOD_OPTIONS,
            variable=self._period_var,
            width=200, height=WIDGET["entry_h_sm"],
            command=self._on_period_change,
        ).pack(side="left")

        self._date_from_var = tk.StringVar()
        self._date_to_var = tk.StringVar()
        self._date_row = DateRangeRow(
            scroll,
            var_from=self._date_from_var,
            var_to=self._date_to_var,
            hint="Формат: YYYY-MM-DD, локальное время (например 2025-01-15)",
        )

        # ---- Формат ----
        self._add_section(scroll, "Формат вывода")
        self._format_var = tk.StringVar(value="Оба формата")
        fmt_row = ctk.CTkFrame(scroll, fg_color="transparent")
        fmt_row.pack(fill="x", padx=pad, pady=(0, SPACING["sm"]))
        for label in ("JSON", "Markdown", "Оба формата"):
            ctk.CTkRadioButton(
                fmt_row, text=label, variable=self._format_var, value=label,
                font=font(13), text_color=C["text"],
            ).pack(side="left", padx=(0, SPACING["lg"]))

        # Слайдер слов
        self._words_var = tk.IntVar(value=50)
        words_row = ctk.CTkFrame(scroll, fg_color="transparent")
        words_row.pack(fill="x", padx=pad, pady=(SPACING["xs"], 0))
        ctk.CTkLabel(words_row, text="Разбивка Markdown (слов):", font=font(12), text_color=C["text_sec"]).pack(side="left")
        self._words_lbl = ctk.CTkLabel(words_row, text="50 000", font=font(12), text_color=C["text"])
        self._words_lbl.pack(side="left", padx=(SPACING["sm"], 0))
        ctk.CTkSlider(
            scroll, from_=10, to=500, number_of_steps=49,
            variable=self._words_var, command=self._on_words,
            height=8, width=340,
        ).pack(padx=pad, pady=(SPACING["xs"], SPACING["md"]), anchor="w")

        # ---- Контент ----
        self._add_section(scroll, "Контент")
        self._popular_var = tk.BooleanVar(value=False)
        self._popular_min_var = tk.StringVar(value="5")
        self._analytics_var = tk.BooleanVar(value=False)
        self._views_var = tk.BooleanVar(value=False)
        self._incremental_var = tk.BooleanVar(value=False)

        for var, text in [
            (self._popular_var, "Популярные сообщения (по реакциям)"),
            (self._analytics_var, "Аналитика: топ авторов и активность"),
            (self._views_var, "Просмотры и пересылки"),
            (self._incremental_var, "Только новые (инкрементальный)"),
        ]:
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", padx=pad, pady=(0, SPACING["xs"]))
            ctk.CTkCheckBox(row, text=text, variable=var, font=font(13), text_color=C["text"]).pack(side="left")

        # Порог популярных
        pop_thresh = ctk.CTkFrame(scroll, fg_color="transparent")
        pop_thresh.pack(fill="x", padx=pad + SPACING["xl"], pady=(0, SPACING["sm"]))
        ctk.CTkLabel(pop_thresh, text="Мин. реакций:", font=font(12), text_color=C["text_sec"]).pack(side="left")
        AppEntry(pop_thresh, placeholder_text="5", width=60, size="sm",
                 textvariable=self._popular_min_var).pack(side="left", padx=(SPACING["sm"], 0))

        # ---- Медиа ----
        self._add_section(scroll, "Медиа")
        self._media_var = tk.BooleanVar(value=False)
        self._transcribe_var = tk.BooleanVar(value=False)
        media_row = ctk.CTkFrame(scroll, fg_color="transparent")
        media_row.pack(fill="x", padx=pad, pady=(0, SPACING["xs"]))
        ctk.CTkCheckBox(media_row, text="Скачивать медиа (фото, видео, документы)",
                        variable=self._media_var, font=font(13), text_color=C["text"]).pack(side="left")

        tr_row = ctk.CTkFrame(scroll, fg_color="transparent")
        tr_row.pack(fill="x", padx=pad, pady=(0, SPACING["xs"]))
        ctk.CTkCheckBox(tr_row, text="Транскрипция голосовых и видеокружков",
                        variable=self._transcribe_var, font=font(13), text_color=C["text"],
                        command=self._on_transcribe_toggle).pack(side="left")

        # Провайдер транскрипции
        self._transcribe_options_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        self._provider_var = tk.StringVar(
            value="Deepgram (облако)" if cfg.transcription_provider == "deepgram" else "Локальный Whisper"
        )
        prov_row = ctk.CTkFrame(self._transcribe_options_frame, fg_color="transparent")
        prov_row.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["xs"]))
        ctk.CTkLabel(prov_row, text="Провайдер:", font=font(12), text_color=C["text_sec"]).pack(side="left")
        ctk.CTkOptionMenu(
            prov_row,
            values=["Локальный Whisper", "Deepgram (облако)"],
            variable=self._provider_var,
            width=300, height=WIDGET["entry_h_sm"],
            command=self._on_provider_change,
        ).pack(side="left", padx=(SPACING["sm"], 0))

        # Модель (только для локальной)
        self._model_frame = ctk.CTkFrame(self._transcribe_options_frame, fg_color="transparent")
        cur_model_id = cfg.local_whisper_model or "base"
        cur_display = next((d for d, m in _WHISPER_MODELS if m == cur_model_id), _WHISPER_MODELS[1][0])
        self._model_var = tk.StringVar(value=cur_display)
        model_row = ctk.CTkFrame(self._model_frame, fg_color="transparent")
        model_row.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["xs"]))
        ctk.CTkLabel(model_row, text="Модель:", font=font(12), text_color=C["text_sec"]).pack(side="left")
        ctk.CTkOptionMenu(
            model_row,
            values=[d for d, _ in _WHISPER_MODELS],
            variable=self._model_var,
            width=380, height=WIDGET["entry_h_sm"],
            command=self._on_model_change,
        ).pack(side="left", padx=(SPACING["sm"], 0))
        self._model_frame.pack(fill="x")

        # Deepgram API ключ (только для Deepgram)
        self._deepgram_frame = ctk.CTkFrame(self._transcribe_options_frame, fg_color="transparent")
        dg_row = ctk.CTkFrame(self._deepgram_frame, fg_color="transparent")
        dg_row.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["xs"]))
        ctk.CTkLabel(dg_row, text="Deepgram API ключ:", font=font(12), text_color=C["text_sec"]).pack(side="left")
        existing_key = self._app.credentials.load_deepgram_key() or ""
        self._deepgram_key_entry = AppEntry(
            dg_row, placeholder_text="Вставьте API ключ Deepgram", size="sm",
            show="•",
        )
        self._deepgram_key_entry.pack(side="left", fill="x", expand=True, padx=(SPACING["sm"], 0))
        if existing_key:
            self._deepgram_key_entry.set_text(existing_key)
        dg_link = ctk.CTkLabel(
            self._deepgram_frame, text="🔗 console.deepgram.com",
            font=font(11), text_color=C["primary"], cursor="hand2",
        )
        dg_link.pack(padx=SPACING["xl"] + SPACING["sm"], anchor="w")
        dg_link.bind("<Button-1>", lambda e: __import__("webbrowser").open("https://console.deepgram.com/"))

        self._transcribe_options_frame.pack(fill="x", padx=pad, pady=(0, SPACING["md"]))
        self._transcribe_options_frame.pack_forget()
        # Показываем нужный суб-фрейм по текущему провайдеру
        self._update_provider_ui()

        # ---- Прогресс (скрыт до запуска) ----
        self._progress = ExportProgressWidget(self, on_cancel=self._on_cancel)
        self._progress.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["sm"]))
        self._progress.pack_forget()

        # ---- Статус/результат ----
        self._result_lbl = ctk.CTkLabel(
            self, text="", font=font(12), text_color=C["text_sec"], wraplength=540,
        )
        self._result_lbl.pack(padx=pad, fill="x")

        # ---- Кнопки ----
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=pad, pady=(SPACING["sm"], pad))

        self._close_btn = AppButton(btn_frame, text="Закрыть", variant="secondary", command=self._on_close_window)
        self._close_btn.pack(side="left", expand=True, fill="x", padx=(0, SPACING["sm"]))

        self._open_btn = AppButton(btn_frame, text="Открыть папку", variant="secondary",
                                   command=self._open_folder, state="disabled")
        self._open_btn.pack(side="left", expand=True, fill="x", padx=(0, SPACING["sm"]))

        self._start_btn = AppButton(btn_frame, text="Экспортировать", variant="primary",
                                    command=self._on_start)
        self._start_btn.pack(side="left", expand=True, fill="x")

    def _add_section(self, parent, title: str) -> None:
        ctk.CTkLabel(
            parent, text=title, font=font(12, "bold"), text_color=C["text_sec"], anchor="w",
        ).pack(fill="x", padx=SPACING["xl"], pady=(SPACING["md"], SPACING["xs"]))

    # ---- Event handlers ----

    def _on_period_change(self, value: str) -> None:
        if value == "Свой период":
            if not self._date_row.winfo_ismapped():
                # Сразу под дропдауном «Период», а не в конце модалки.
                self._date_row.pack(
                    fill="x", padx=SPACING["xl"], pady=(0, SPACING["sm"]),
                    after=self._period_row,
                )
        else:
            if self._date_row.winfo_ismapped():
                self._date_row.pack_forget()

    def _on_words(self, value) -> None:
        rounded = max(10, int(round(float(value) / 10) * 10))
        self._words_var.set(rounded)
        self._words_lbl.configure(text=f"{rounded * 1000:,}".replace(",", " "))

    def _on_transcribe_toggle(self) -> None:
        if self._transcribe_var.get():
            self._transcribe_options_frame.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["md"]))
        else:
            self._transcribe_options_frame.pack_forget()

    def _on_provider_change(self, value: str) -> None:
        self._update_provider_ui()
        is_local = "Whisper" in value
        self._app.set_transcription_provider("local" if is_local else "deepgram")
        self._app.set_local_whisper_model(self._current_model_id())

    def _update_provider_ui(self) -> None:
        is_local = "Whisper" in self._provider_var.get()
        if is_local:
            self._model_frame.pack(fill="x")
            self._deepgram_frame.pack_forget()
        else:
            self._model_frame.pack_forget()
            self._deepgram_frame.pack(fill="x")

    def _on_model_change(self, display: str) -> None:
        model_id = next((m for d, m in _WHISPER_MODELS if d == display), "base")
        self._app.set_local_whisper_model(model_id)

    def _on_start(self) -> None:
        # Сохраняем Deepgram ключ если введён
        if "Deepgram" in self._provider_var.get():
            key = self._deepgram_key_entry.get().strip()
            if key:
                self._app.credentials.save_deepgram_key(key)
        import tkinter.filedialog as fd
        path = fd.askdirectory(title="Куда сохранить экспорт?")
        if not path:
            return
        self._exporting = True
        self._start_btn.configure(state="disabled")
        self._progress.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["sm"]))
        self._result_lbl.configure(text="")
        self._open_btn.configure(state="disabled")
        selected = self.get_selected_topics()
        if selected:
            self._progress.start(f"Топиков: {len(selected)}", None)
            self._app.start_topics_export(self._dialog, path, self, selected)
        else:
            self._progress.start(getattr(self._dialog, "name", "Чат"), None)
            self._app.start_export(self._dialog, path, self)

    def _on_cancel(self) -> None:
        self._app.cancel_export()

    def _on_close_window(self) -> None:
        # Закрытие окна (крестик/«Закрыть») во время экспорта — отменяем работу,
        # иначе воркер молча доедет до конца, а события полетят в мёртвый виджет.
        if self._exporting:
            self._app.cancel_export()
        self._app.detach_export_modal(self)
        self.destroy()

    def _open_folder(self) -> None:
        if self._export_dir and os.path.isdir(self._export_dir):
            _open_directory(self._export_dir)

    # ---- Called by App ----

    def on_export_start(self, chat_name: str, total: Optional[int]) -> None:
        self._progress.start(chat_name, total)

    def on_export_progress(self, count: int, total: Optional[int], eta: Optional[float] = None) -> None:
        self._progress.update(count, total, eta)

    def on_export_status(self, text: str) -> None:
        self._progress.set_status(text)

    def on_model_download_progress(self, ratio: float, text: str) -> None:
        """Показывает прогресс скачивания модели транскрипции на месте прогресс-бара экспорта."""
        self._progress.set_download_progress(ratio, text)

    def on_export_done(self, export_dir: str, files: list[str]) -> None:
        self._export_dir = export_dir
        self._exporting = False
        self._progress.finish()
        n = len(files)
        self._result_lbl.configure(
            text=f"✓ Готово — {n} {'файл' if n == 1 else 'файла' if 2 <= n <= 4 else 'файлов'}\n{export_dir}",
            text_color=C["success"],
        )
        self._start_btn.configure(state="normal", text="Экспортировать ещё")
        self._open_btn.configure(state="normal")

    def on_export_error(self, message: str) -> None:
        self._exporting = False
        self._progress.hide()
        self._result_lbl.configure(text=f"Ошибка: {message}", text_color=C["error"])
        self._start_btn.configure(state="normal")

    def on_export_cancelled(self) -> None:
        self._exporting = False
        self._progress.hide()
        self._result_lbl.configure(text="Экспорт отменён", text_color=C["text_sec"])
        self._start_btn.configure(state="normal")

    # ---- Топики форума ----

    def set_topics(self, topics) -> None:
        """Отрисовывает чекбоксы топиков (вызывается из App после загрузки)."""
        self._topics = list(topics)
        for w in self._topics_list_frame.winfo_children():
            w.destroy()
        self._topic_vars = {}
        if not self._topics:
            ctk.CTkLabel(self._topics_list_frame, text="Топиков не найдено",
                         font=font(12), text_color=C["text_sec"]).pack(anchor="w")
            return
        for t in self._topics:
            var = tk.BooleanVar(value=True)
            self._topic_vars[t.id] = var
            label = t.title + ("  🔒" if getattr(t, "closed", False) else "")
            ctk.CTkCheckBox(
                self._topics_list_frame, text=label, variable=var,
                font=font(12), text_color=C["text"],
                command=self._on_topic_toggle, checkbox_width=16, checkbox_height=16,
            ).pack(anchor="w", pady=1)

    def set_topics_error(self, message: str) -> None:
        for w in self._topics_list_frame.winfo_children():
            w.destroy()
        ctk.CTkLabel(self._topics_list_frame, text=f"Ошибка загрузки топиков: {message}",
                     font=font(12), text_color=C["error"], wraplength=520,
                     anchor="w").pack(anchor="w")

    def _on_topics_all(self) -> None:
        val = self._topics_all_var.get()
        for var in self._topic_vars.values():
            var.set(val)

    def _on_topic_toggle(self) -> None:
        # Если сняли хоть один — «Все» гаснет; если все стоят — «Все» зажигается.
        if self._topic_vars:
            all_on = all(v.get() for v in self._topic_vars.values())
            self._topics_all_var.set(all_on)

    def get_selected_topics(self) -> list:
        """Выбранные ForumTopic (для форума). Для обычного чата — []."""
        if not self._topic_vars:
            return []
        chosen = {tid for tid, v in self._topic_vars.items() if v.get()}
        return [t for t in self._topics if t.id in chosen]

    def on_topic_progress(self, current: int, total: int, title: str) -> None:
        self._progress.start(f"Топик {current}/{total}: {title}", None)

    def on_topic_batch_done(self, export_dir: str, ok: int, failed: int) -> None:
        self._export_dir = export_dir
        self._exporting = False
        self._progress.finish()
        msg = f"✓ Топиков: {ok} успешно"
        if failed:
            msg += f", {failed} ошибок"
        self._result_lbl.configure(text=f"{msg}\n{export_dir}", text_color=C["success"])
        self._start_btn.configure(state="normal", text="Экспортировать ещё")
        self._open_btn.configure(state="normal")

    # ---- Getters for App ----

    def _current_model_id(self) -> str:
        display = self._model_var.get()
        return next((m for d, m in _WHISPER_MODELS if d == display), "base")

    def get_export_options(self) -> dict:
        """Возвращает словарь опций для ExportTask."""
        period = self._period_var.get()
        date_from = date_to = None
        if period == "Свой период":
            date_from = parse_local_date(self._date_from_var.get())
            date_to = parse_local_date(self._date_to_var.get())
        elif period in _PERIOD_DAYS and _PERIOD_DAYS[period] > 0:
            import datetime as dt
            date_from = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=_PERIOD_DAYS[period])

        fmt_str = self._format_var.get()
        from ...models.export_task import ExportFormat
        fmt = {
            "JSON": ExportFormat.JSON,
            "Markdown": ExportFormat.MARKDOWN,
        }.get(fmt_str, ExportFormat.BOTH)

        return {
            "format": fmt,
            "date_from": date_from,
            "date_to": date_to,
            "download_media": self._media_var.get(),
            "transcribe_audio": self._transcribe_var.get(),
            "collect_analytics": self._analytics_var.get(),
            "words_per_file": self._words_var.get() * 1000,
            "incremental": self._incremental_var.get(),
        }


# ---- Helpers ----

def _open_directory(path: str) -> None:
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass
