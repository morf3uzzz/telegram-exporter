"""
SettingsPage — страница настроек (левый сайдбар + страницы).

Объединяет на одной прокручиваемой странице:
  • API-ключи Telegram (api_id / api_hash) — раньше ApiKeysModal;
  • настройки транскрипции (провайдер / модель / язык / Deepgram ключ) —
    раньше SettingsModal.

Это ctk.CTkFrame (НЕ CTkToplevel) с прозрачным фоном — встраивается в
правую область главного окна.
"""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING
import customtkinter as ctk

from ..theme import C, SPACING, WIDGET, font, font_display
from ..components.button import AppButton
from ..components.entry import AppEntry
from ..modal_utils import setup_smooth_scroll
from ...models.config import UI_SCALES

if TYPE_CHECKING:
    from ..app import App

_PROVIDER_LABELS = {
    "local":    "Локальный Whisper",
    "deepgram": "Deepgram (облако)",
}

_LANG_LABELS = {
    "multi": "Авто (несколько языков)",
    "ru":    "Русский",
    "en":    "Английский",
    "de":    "Немецкий",
    "fr":    "Французский",
    "es":    "Испанский",
    "zh":    "Китайский",
    "ja":    "Японский",
}

_MODEL_LABELS = {
    "tiny":   "Tiny (быстро, ниже качество)",
    "base":   "Base (баланс)",
    "small":  "Small",
    "medium": "Medium",
    "large":  "Large (медленно, высокое качество)",
}


class SettingsPage(ctk.CTkFrame):
    """Страница «Настройки»: API-ключи + транскрипция."""

    def __init__(self, master, app: "App") -> None:
        super().__init__(master, fg_color="transparent")
        self._app = app
        self._build()
        self._load()
        self.after(100, lambda: setup_smooth_scroll(self, self._scroll))

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        pad = SPACING["2xl"]

        ctk.CTkLabel(
            self, text="Настройки",
            font=font_display(18, "bold"), text_color=C["text"], anchor="w",
        ).pack(fill="x", padx=pad, pady=(pad, SPACING["lg"]))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color=C["bg"])
        self._scroll.pack(fill="both", expand=True, padx=pad)
        s = self._scroll

        # ── Секция API ────────────────────────────────────────────────────
        self._build_api_section(s)

        # ── Разделитель ───────────────────────────────────────────────────
        ctk.CTkFrame(s, height=1, fg_color=C["border"]).pack(
            fill="x", pady=(SPACING["lg"], SPACING["md"]),
        )

        # ── Секция Транскрипция ───────────────────────────────────────────
        self._build_transcription_section(s)

        # ── Разделитель ───────────────────────────────────────────────────
        ctk.CTkFrame(s, height=1, fg_color=C["border"]).pack(
            fill="x", pady=(SPACING["lg"], SPACING["md"]),
        )

        # ── Секция Экспорт по умолчанию ───────────────────────────────────
        self._build_export_section(s)

        # ── Разделитель ───────────────────────────────────────────────────
        ctk.CTkFrame(s, height=1, fg_color=C["border"]).pack(
            fill="x", pady=(SPACING["lg"], SPACING["md"]),
        )

        # ── Секция Интерфейс ──────────────────────────────────────────────
        self._build_interface_section(s)

        # ── Низ страницы: статус + Сохранить ──────────────────────────────
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=pad, pady=(SPACING["md"], pad))

        self._status_lbl = ctk.CTkLabel(
            btn_row, text="", font=font(12),
            text_color=C["success"], anchor="w",
        )
        self._status_lbl.pack(side="left", fill="x", expand=True)

        AppButton(
            btn_row, text="Сохранить", variant="primary", size="md",
            command=self._save,
        ).pack(side="right")

    def _build_api_section(self, s) -> None:
        """API ID + API Hash (+ ссылка на my.telegram.org)."""
        self._section(s, "API-ключи Telegram")

        ctk.CTkLabel(
            s,
            text="Получите их на my.telegram.org → API development tools.",
            font=font(12), text_color=C["text_sec"],
            wraplength=420, justify="left", anchor="w",
        ).pack(fill="x", pady=(0, SPACING["sm"]))

        AppButton(
            s, text="Открыть my.telegram.org", variant="ghost", size="sm",
            command=lambda: webbrowser.open("https://my.telegram.org"),
        ).pack(anchor="w", pady=(0, SPACING["md"]))

        # api_id
        self._row_label(s, "API ID")
        self._api_id_entry = AppEntry(s, placeholder_text="например, 1234567", size="md")
        self._api_id_entry.pack(fill="x", pady=(SPACING["xs"], SPACING["md"]))

        # api_hash
        self._row_label(s, "API Hash")
        self._api_hash_entry = AppEntry(
            s, placeholder_text="32 символа", show="•", size="md",
        )
        self._api_hash_entry.pack(fill="x", pady=(SPACING["xs"], 0))

    def _build_transcription_section(self, s) -> None:
        """Провайдер / модель Whisper / Deepgram ключ / язык."""
        self._section(s, "Транскрипция голосовых и видеокружков")

        self._row_label(s, "Провайдер")
        self._provider_var = ctk.StringVar(value="local")
        self._provider_menu = ctk.CTkOptionMenu(
            s,
            values=list(_PROVIDER_LABELS.values()),
            command=self._on_provider_change,
            height=WIDGET["entry_h_sm"], font=font(13),
        )
        self._provider_menu.pack(fill="x", pady=(SPACING["xs"], SPACING["sm"]))

        # Единый контейнер — внутри показываем либо Whisper, либо Deepgram блок
        self._provider_options = ctk.CTkFrame(s, fg_color="transparent")
        self._provider_options.pack(fill="x")

        # Блок Whisper модели
        self._model_block = ctk.CTkFrame(self._provider_options, fg_color="transparent")
        self._row_label(self._model_block, "Модель Whisper")
        self._model_var = ctk.StringVar(value="base")
        self._model_menu = ctk.CTkOptionMenu(
            self._model_block,
            values=list(_MODEL_LABELS.values()),
            command=self._on_model_change,
            height=WIDGET["entry_h_sm"], font=font(13),
        )
        self._model_menu.pack(fill="x", pady=(SPACING["xs"], SPACING["sm"]))
        self._model_block.pack(fill="x")  # показан по умолчанию

        # Блок Deepgram ключа
        self._deepgram_block = ctk.CTkFrame(self._provider_options, fg_color="transparent")
        self._row_label(self._deepgram_block, "Deepgram API Key")
        self._deepgram_entry = AppEntry(
            self._deepgram_block, placeholder_text="Вставьте ключ", show="•", size="sm",
        )
        self._deepgram_entry.pack(fill="x", pady=(SPACING["xs"], 0))
        dg_link = ctk.CTkLabel(
            self._deepgram_block, text="🔗 console.deepgram.com",
            font=font(11), text_color=C["primary"], cursor="hand2",
        )
        dg_link.pack(anchor="w", pady=(2, SPACING["sm"]))
        dg_link.bind("<Button-1>", lambda _: webbrowser.open("https://console.deepgram.com/"))

        # Язык
        self._row_label(s, "Язык распознавания")
        self._lang_var = ctk.StringVar(value="multi")
        self._lang_menu = ctk.CTkOptionMenu(
            s,
            values=list(_LANG_LABELS.values()),
            command=self._on_lang_change,
            height=WIDGET["entry_h_sm"], font=font(13),
        )
        self._lang_menu.pack(fill="x", pady=(SPACING["xs"], 0))

    def _build_export_section(self, s) -> None:
        """Параметры Markdown по умолчанию (автор / временные метки)."""
        self._section(s, "Экспорт по умолчанию")

        self._row_label(s, "Включать автора сообщения")
        self._include_author_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            s, text="", variable=self._include_author_var,
            onvalue=True, offvalue=False,
        ).pack(anchor="w", pady=(SPACING["xs"], SPACING["sm"]))

        self._row_label(s, "Включать временные метки")
        self._include_ts_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            s, text="", variable=self._include_ts_var,
            onvalue=True, offvalue=False,
        ).pack(anchor="w", pady=(SPACING["xs"], SPACING["sm"]))

    def _build_interface_section(self, s) -> None:
        """Масштаб интерфейса — применяется сразу, без кнопки «Сохранить»."""
        self._section(s, "Интерфейс")
        self._row_label(s, "Масштаб интерфейса")
        self._scale_menu = ctk.CTkOptionMenu(
            s,
            values=[f"{int(v * 100)}%" for v in UI_SCALES],
            command=self._on_scale_change,
            height=WIDGET["entry_h_sm"], font=font(13),
        )
        self._scale_menu.pack(fill="x", pady=(SPACING["xs"], SPACING["xs"]))
        ctk.CTkLabel(
            s, text="Размер шрифтов и элементов интерфейса. Применяется сразу.",
            font=font(11), text_color=C["text_dim"],
            wraplength=420, justify="left", anchor="w",
        ).pack(fill="x")

    def _on_scale_change(self, display_val: str) -> None:
        try:
            value = int(display_val.rstrip("%")) / 100.0
        except ValueError:
            return
        self._app.set_ui_scale(value)

    # ------------------------------------------------------------------ helpers

    def _section(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text,
            font=font(13, "bold"), text_color=C["text"], anchor="w",
        ).pack(fill="x", pady=(0, SPACING["xs"]))

    def _row_label(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text,
            font=font(12), text_color=C["text_sec"], anchor="w",
        ).pack(fill="x")

    def _on_provider_change(self, display_val: str) -> None:
        key = next((k for k, v in _PROVIDER_LABELS.items() if v == display_val), "local")
        self._provider_var.set(key)
        self._model_block.pack_forget()
        self._deepgram_block.pack_forget()
        if key == "deepgram":
            self._deepgram_block.pack(fill="x", in_=self._provider_options)
        else:
            self._model_block.pack(fill="x", in_=self._provider_options)

    def _on_model_change(self, display_val: str) -> None:
        key = next((k for k, v in _MODEL_LABELS.items() if v == display_val), "base")
        self._model_var.set(key)

    def _on_lang_change(self, display_val: str) -> None:
        key = next((k for k, v in _LANG_LABELS.items() if v == display_val), "multi")
        self._lang_var.set(key)

    # ------------------------------------------------------------------ load/save

    def _load(self) -> None:
        """Заполняет поля текущими значениями из config + Keyring."""
        cfg = self._app.config

        # API-ключи
        if cfg.api_id:
            self._api_id_entry.set_text(cfg.api_id)
        else:
            self._api_id_entry.clear()
        api_hash = self._app.credentials.load_api_hash(cfg.api_id) if cfg.api_id else ""
        if api_hash:
            self._api_hash_entry.set_text(api_hash)
        else:
            self._api_hash_entry.clear()

        # Транскрипция
        provider = cfg.transcription_provider
        self._provider_var.set(provider)
        self._provider_menu.set(_PROVIDER_LABELS.get(provider, _PROVIDER_LABELS["local"]))

        if provider == "deepgram":
            self._model_block.pack_forget()
            self._deepgram_block.pack(fill="x", in_=self._provider_options)
            dg_key = self._app.credentials.load_deepgram_key() or ""
            if dg_key:
                self._deepgram_entry.set_text(dg_key)
        else:
            self._deepgram_block.pack_forget()
            self._model_block.pack(fill="x", in_=self._provider_options)

        model = cfg.local_whisper_model
        self._model_var.set(model)
        self._model_menu.set(_MODEL_LABELS.get(model, _MODEL_LABELS["base"]))

        lang = cfg.transcription_language
        self._lang_var.set(lang)
        self._lang_menu.set(_LANG_LABELS.get(lang, _LANG_LABELS["multi"]))

        # Экспорт по умолчанию (Markdown)
        md_cfg = cfg.markdown
        self._include_author_var.set(getattr(md_cfg, "include_author", True))
        self._include_ts_var.set(getattr(md_cfg, "include_timestamps", True))

        # Масштаб интерфейса
        self._scale_menu.set(f"{int(round(cfg.ui_scale * 100))}%")

    def _save(self) -> None:
        import dataclasses

        # ── API-ключи ─────────────────────────────────────────────────────
        api_id = self._api_id_entry.get().strip()
        api_hash = self._api_hash_entry.get().strip()
        if not api_id or not api_id.isdigit():
            self._status_lbl.configure(text="API ID должен быть числом.", text_color=C["error"])
            return
        if not api_hash or len(api_hash) < 10:
            self._status_lbl.configure(text="API Hash выглядит некорректно.", text_color=C["error"])
            return
        try:
            self._app.save_config(api_id, api_hash)
        except Exception as exc:
            self._status_lbl.configure(text=f"Ошибка сохранения: {exc}", text_color=C["error"])
            return

        # ── Транскрипция ──────────────────────────────────────────────────
        provider = self._provider_var.get()
        self._app.set_transcription_provider(provider)
        self._app.set_local_whisper_model(self._model_var.get())

        if provider == "deepgram":
            dg_key = self._deepgram_entry.get().strip()
            if dg_key:
                self._app.credentials.save_deepgram_key(dg_key)

        lang = self._lang_var.get()
        cfg = self._app.config
        md_cfg = dataclasses.replace(
            cfg.markdown,
            include_author=self._include_author_var.get(),
            include_timestamps=self._include_ts_var.get(),
        )
        new_cfg = dataclasses.replace(cfg, transcription_language=lang, markdown=md_cfg)
        self._app.config = new_cfg
        new_cfg.save()

        self._status_lbl.configure(text="Сохранено ✓", text_color=C["success"])

    # ------------------------------------------------------------------ public

    def refresh(self) -> None:
        """Перечитать актуальные значения (вызывается при показе страницы)."""
        self._load()
