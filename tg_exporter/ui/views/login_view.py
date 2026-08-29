"""
LoginView — экран авторизации.

Состояния: phone → code (+2fa) → loading → (переход к чатам через App)
Inline ошибки под полем вместо messagebox.

Карточка разбита на две вкладки сверху:
  • «Вход»        — выбор «По номеру / QR» + поля телефона/кода/2FA + QR.
  • «Подключение» — inline-форма API-ключей (api_id/api_hash) + прокси + сброс.
Вкладка «Подключение» заменяет старый сворачиваемый блок «Настройки
подключения» и больше НЕ уводит в shell настроек (раньше «Настроить API
ключи» вызывала App.show_api_keys и навигировала в SettingsPage до входа).
"""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING
import customtkinter as ctk

from ..theme import C, RADIUS, SPACING, font, font_display
from ..components.button import AppButton
from ..components.entry import AppEntry
from ..components.qr_widget import QRCodeWidget

if TYPE_CHECKING:
    from ..app import App

_TAB_LOGIN = "Вход"
_TAB_CONN = "Подключение"


class LoginView(ctk.CTkFrame):
    """Центральная карточка авторизации с вкладками «Вход» / «Подключение»."""

    def __init__(self, master, app: "App") -> None:
        super().__init__(master, fg_color="transparent")
        self._app = app
        self._state = "phone"  # "phone" | "code" | "loading"
        self._build()

    # ---- Build ----

    def _build(self) -> None:
        # Карточка по центру
        self._card = ctk.CTkFrame(
            self,
            fg_color=C["card"],
            corner_radius=RADIUS["2xl"],
            border_width=1,
            border_color=C["border"],
        )
        # Высота фиксирована — вмещает обе вкладки, ввод кода и QR-картинку.
        # Не меняем при переключении вкладок/режимов, чтобы не было «прыжка».
        self._card.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.42, relheight=0.84)

        pad = SPACING["3xl"]

        # Заголовок
        ctk.CTkLabel(
            self._card,
            text="Telegram Exporter",
            font=font_display(22, "bold"),
            text_color=C["text"],
        ).pack(pady=(pad, SPACING["xs"]))

        ctk.CTkLabel(
            self._card,
            text="Вход в аккаунт",
            font=font(13),
            text_color=C["text_sec"],
        ).pack(pady=(0, SPACING["lg"]))

        # ---- Верхние вкладки: «Вход» / «Подключение» ----
        self._tab_var = ctk.StringVar(value=_TAB_LOGIN)
        self._tab_seg = ctk.CTkSegmentedButton(
            self._card,
            values=[_TAB_LOGIN, _TAB_CONN],
            variable=self._tab_var,
            command=self._on_tab_change,
        )
        self._tab_seg.pack(padx=pad, pady=(0, SPACING["md"]))

        # Контейнер вкладок: оба фрейма в ОДНОЙ grid-ячейке (оверлап),
        # переключение через tkraise() — без pack/forget, без моргания.
        self._tabs_holder = ctk.CTkFrame(self._card, fg_color="transparent")
        self._tabs_holder.pack(fill="both", expand=True)
        self._tabs_holder.grid_rowconfigure(0, weight=1)
        self._tabs_holder.grid_columnconfigure(0, weight=1)

        self._login_tab = ctk.CTkFrame(self._tabs_holder, fg_color="transparent")
        self._conn_tab = ctk.CTkFrame(self._tabs_holder, fg_color="transparent")
        self._login_tab.grid(row=0, column=0, sticky="nsew")
        self._conn_tab.grid(row=0, column=0, sticky="nsew")

        self._build_login_tab(pad)
        self._build_conn_tab(pad)

        # По умолчанию активна вкладка «Вход» (refresh_state может переключить).
        self._login_tab.tkraise()

    def _build_login_tab(self, pad: int) -> None:
        """Вкладка «Вход»: выбор режима + поля телефона/кода/2FA + QR."""
        t = self._login_tab

        # Переключатель режима входа: по номеру / QR-код
        self._mode_var = ctk.StringVar(value="По номеру")
        self._mode_seg = ctk.CTkSegmentedButton(
            t,
            values=["По номеру", "QR-код"],
            variable=self._mode_var,
            command=self._on_mode_change,
        )
        self._mode_seg.pack(pady=(0, SPACING["md"]))

        # Телефон
        self._phone_entry = AppEntry(t, placeholder_text="+7 900 000-00-00")
        self._phone_entry.pack(padx=pad, fill="x", pady=(0, SPACING["xs"]))
        self._phone_entry.bind("<Return>", lambda _: self._on_action())

        # Код (скрыт)
        self._code_entry = AppEntry(t, placeholder_text="Код из Telegram")
        self._code_entry.bind("<Return>", lambda _: self._on_action())

        # 2FA пароль (скрыт) + кнопка показа
        self._pwd_frame = ctk.CTkFrame(
            t,
            fg_color=C["surface"],
            border_width=1,
            border_color=C["border"],
            corner_radius=RADIUS["md"],
        )
        self._pwd_entry = AppEntry(
            self._pwd_frame, placeholder_text="Пароль 2FA", show="•",
            border_width=0, fg_color="transparent",
        )
        self._pwd_entry.pack(side="left", fill="x", expand=True, padx=(SPACING["xs"], 0))
        self._pwd_entry.bind("<Return>", lambda _: self._on_action())
        self._pwd_visible = False
        self._eye_btn = ctk.CTkButton(
            self._pwd_frame, text="👁", width=28, height=28,
            fg_color="transparent", hover_color=C["border"],
            text_color=C["text_sec"], font=font(13),
            corner_radius=RADIUS["sm"],
            command=self._toggle_pwd_visibility,
        )
        self._eye_btn.pack(side="left", padx=(0, SPACING["xs"]))

        # Inline ошибка
        self._error_lbl = ctk.CTkLabel(
            t, text="", font=font(12), text_color=C["error"],
            wraplength=300,
        )
        self._error_lbl.pack(padx=pad, pady=(0, SPACING["sm"]))

        # Главная кнопка действия
        self._action_btn = AppButton(
            t, text="Получить код", command=self._on_action,
        )
        self._action_btn.pack(padx=pad, fill="x", pady=(0, SPACING["lg"]))

        # ---- QR-режим (скрыт по умолчанию, не пакуется) ----
        self._qr_frame = ctk.CTkFrame(t, fg_color="transparent")
        self._qr_widget = QRCodeWidget(self._qr_frame, size_px=220)
        self._qr_widget.pack(pady=(0, SPACING["sm"]))
        self._qr_status = ctk.CTkLabel(
            self._qr_frame,
            text="Отсканируйте код в приложении Telegram",
            font=font(12),
            text_color=C["text_sec"],
            wraplength=300,
        )
        self._qr_status.pack(pady=(0, SPACING["sm"]))
        # Кнопка обновления кода — показывается только после истечения/ошибки.
        self._qr_refresh_btn = AppButton(
            self._qr_frame, text="Обновить код", variant="secondary", size="sm",
            command=self._app.refresh_qr,
        )
        # Кнопка подтверждения пароля 2FA в QR-режиме — показывается по on_qr_2fa.
        self._qr_2fa_btn = AppButton(
            self._qr_frame, text="Войти", size="sm",
            command=self._on_qr_2fa_submit,
        )
        # «Войти другим способом» — сброс QR/2FA, возврат ко входу по номеру.
        # Показывается в 2FA-состоянии (когда застрял с паролем/другой аккаунт).
        self._qr_cancel_btn = AppButton(
            self._qr_frame, text="← Войти другим способом", variant="ghost", size="sm",
            command=self._cancel_qr_to_phone,
        )

    def _cancel_qr_to_phone(self) -> None:
        """Сбрасывает QR/2FA и возвращает на вход по номеру."""
        self._mode_var.set("По номеру")
        self._on_mode_change("По номеру")

    def _build_conn_tab(self, pad: int) -> None:
        """
        Вкладка «Подключение»: две секции-карточки — API-ключи и прокси.
        Всё выровнено по левому краю, сброс ключей — внутри секции API.
        """
        t = self._conn_tab
        ip = SPACING["lg"]  # внутренний отступ секций

        # ── Секция «API-ключи» ────────────────────────────────────────────
        api_sec = ctk.CTkFrame(t, fg_color=C["surface"], corner_radius=RADIUS["lg"],
                               border_width=1, border_color=C["border"])
        api_sec.pack(padx=pad, fill="x", pady=(0, SPACING["md"]))

        head = ctk.CTkFrame(api_sec, fg_color="transparent")
        head.pack(fill="x", padx=ip, pady=(ip, SPACING["sm"]))
        ctk.CTkLabel(head, text="🔑  API-ключи Telegram", font=font(13, "bold"),
                     text_color=C["text"], anchor="w").pack(side="left")
        # Бейдж статуса (обновляется в refresh_state).
        self._api_lbl = ctk.CTkLabel(head, text="", font=font(11),
                                     text_color=C["success"], anchor="e")
        self._api_lbl.pack(side="right")

        link = ctk.CTkLabel(
            api_sec, text="Получите на my.telegram.org → API development tools",
            font=font(11), text_color=C["primary"], anchor="w", cursor="hand2",
            wraplength=300, justify="left",
        )
        link.pack(fill="x", padx=ip, pady=(0, SPACING["md"]))
        link.bind("<Button-1>", lambda _e: webbrowser.open("https://my.telegram.org"))

        ctk.CTkLabel(api_sec, text="API ID", font=font(11), text_color=C["text_sec"],
                     anchor="w").pack(fill="x", padx=ip)
        self._api_id_entry = AppEntry(api_sec, placeholder_text="например, 1234567", size="md")
        self._api_id_entry.pack(fill="x", padx=ip, pady=(SPACING["xs"], SPACING["sm"]))

        ctk.CTkLabel(api_sec, text="API Hash", font=font(11), text_color=C["text_sec"],
                     anchor="w").pack(fill="x", padx=ip)
        self._api_hash_entry = AppEntry(api_sec, placeholder_text="32 символа",
                                        show="•", size="md")
        self._api_hash_entry.pack(fill="x", padx=ip, pady=(SPACING["xs"], SPACING["md"]))

        self._save_api_btn = AppButton(api_sec, text="Сохранить", variant="primary",
                                       size="md", command=self._save_api)
        self._save_api_btn.pack(fill="x", padx=ip, pady=(0, SPACING["xs"]))

        # Статус сохранения / ошибки валидации (полная строка под кнопкой).
        self._save_status = ctk.CTkLabel(api_sec, text="", font=font(11),
                                         text_color=C["text_sec"], anchor="w",
                                         wraplength=300, justify="left")
        self._save_status.pack(fill="x", padx=ip, pady=(0, SPACING["sm"]))

        # Сброс — внутри секции API (логически относится к ключам).
        self._clear_api_btn = AppButton(api_sec, text="Сбросить ключи",
                                        variant="ghost", size="sm",
                                        command=self._on_clear_api)
        self._clear_api_btn.pack(anchor="w", padx=ip, pady=(0, ip))

        # ── Секция «Прокси» ───────────────────────────────────────────────
        proxy_sec = ctk.CTkFrame(t, fg_color=C["surface"], corner_radius=RADIUS["lg"],
                                 border_width=1, border_color=C["border"])
        proxy_sec.pack(padx=pad, fill="x")
        prow = ctk.CTkFrame(proxy_sec, fg_color="transparent")
        prow.pack(fill="x", padx=ip, pady=ip)
        desc = ctk.CTkFrame(prow, fg_color="transparent")
        desc.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(desc, text="🌐  Прокси", font=font(13, "bold"),
                     text_color=C["text"], anchor="w").pack(fill="x")
        ctk.CTkLabel(desc, text="Вход без VPN (SOCKS5 / MTProto)", font=font(11),
                     text_color=C["text_sec"], anchor="w").pack(fill="x")
        self._proxy_btn = AppButton(prow, text="Настроить", variant="secondary",
                                    size="sm", command=self._app.show_login_proxy)
        self._proxy_btn.pack(side="right")

    # ---- Tabs ----

    def _on_tab_change(self, value: str) -> None:
        """Переключает вкладку «Вход» / «Подключение»."""
        # Без API-ключей вкладка «Вход» недоступна — возвращаем на «Подключение».
        # (Защита на случай, если визуальный дизейбл сегмента обойдён.)
        if value == _TAB_LOGIN and not self._app.has_api_creds():
            self._tab_var.set(_TAB_CONN)
            self._show_conn_tab()
            self.set_error("Сначала настройте подключение (API-ключи).")
            return
        if value == _TAB_CONN:
            self._show_conn_tab()
        else:
            # tkraise — без перепаковки, без моргания. Высота карточки фиксирована
            # (см. _build), поэтому смены relheight (прыжка) нет.
            self._login_tab.tkraise()

    def _show_conn_tab(self) -> None:
        self._conn_tab.tkraise()

    def _set_login_tab_enabled(self, enabled: bool) -> None:
        """Визуально (раз)блокирует сегмент «Вход» в переключателе вкладок."""
        try:
            btn = self._tab_seg._buttons_dict.get(_TAB_LOGIN)
            if btn is not None:
                btn.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass  # внутренности CTk могли измениться — перехват в _on_tab_change страхует

    def _switch_tab(self, tab: str) -> None:
        """Программно активирует вкладку (обновляет переменную + раскладку)."""
        self._tab_var.set(tab)
        self._on_tab_change(tab)

    # ---- Public API ----

    def reset_to_phone_mode(self) -> None:
        """
        Сбрасывает экран входа в режим «По номеру» (вызывать при показе login,
        напр. после logout из QR-режима — иначе остаётся залипший QR-вид).
        """
        if self._mode_var.get() != "По номеру":
            self._mode_var.set("По номеру")
            self._on_mode_change("По номеру")

    def refresh_state(self) -> None:
        """Вызывается App после изменения config / credentials."""
        has_creds = self._app.has_api_creds()
        # Префилл полей API-ключей из config + Keyring.
        cfg = self._app.config
        if cfg.api_id:
            self._api_id_entry.set_text(cfg.api_id)
            api_hash = self._app.credentials.load_api_hash(cfg.api_id) or ""
            if api_hash:
                self._api_hash_entry.set_text(api_hash)
            else:
                self._api_hash_entry.clear()
        else:
            self._api_id_entry.clear()
            self._api_hash_entry.clear()

        # ВАЖНО: значения вкладок НЕ меняем динамически (метка приходит в
        # command _on_tab_change как value — добавление «✓» к тексту сломало бы
        # сравнение value == _TAB_CONN, и вкладка «Подключение» не открывалась бы).
        # Статус «ключи заданы» показываем через _api_lbl на самой вкладке.
        if has_creds:
            # Короткий бейдж справа от заголовка секции «API-ключи».
            self._api_lbl.configure(text="✓ настроены", text_color=C["success"])
            self._phone_entry.configure(state="normal")
            self._action_btn.configure(state="normal")
            self._set_login_tab_enabled(True)
            # По умолчанию — вкладка «Вход».
            if self._tab_var.get() != _TAB_LOGIN:
                self._switch_tab(_TAB_LOGIN)
        else:
            # Без ключей вход невозможен — блокируем вкладку «Вход» и открываем
            # «Подключение», чтобы пользователь сразу ввёл ключи.
            self._api_lbl.configure(text="не заданы", text_color=C["error"])
            self._phone_entry.configure(state="disabled")
            self._action_btn.configure(state="disabled")
            # Сначала уводим на «Подключение», ПОТОМ дизейблим «Вход»
            # (дизейбл активного сегмента визуально некорректен).
            self._switch_tab(_TAB_CONN)
            self._set_login_tab_enabled(False)
        self.clear_error()

    def show_code_input(self) -> None:
        """Переключается в состояние ввода кода."""
        self._state = "code"
        self._phone_entry.configure(state="disabled")
        self._show_widget(self._code_entry, padx=SPACING["3xl"], fill="x",
                          pady=(0, SPACING["xs"]), before=self._error_lbl)
        self._show_widget(self._pwd_frame, padx=SPACING["3xl"], fill="x",
                          pady=(0, SPACING["xs"]), before=self._error_lbl)
        self._action_btn.set_idle_text("Войти")
        self._action_btn.set_loading(False)
        self._code_entry.focus()

    def set_loading(self, loading: bool) -> None:
        self._state = "loading" if loading else self._state
        self._action_btn.set_loading(loading, "Подключение...")
        self._phone_entry.configure(state="disabled" if loading else "normal")

    def set_error(self, msg: str) -> None:
        self._error_lbl.configure(text=msg)
        self._action_btn.set_loading(False)
        self._state = "phone" if self._state == "loading" else self._state

    def clear_error(self) -> None:
        self._error_lbl.configure(text="")

    @property
    def phone(self) -> str:
        return self._phone_entry.get().strip()

    @property
    def code(self) -> str:
        return self._code_entry.get().strip()

    @property
    def password(self) -> str:
        return self._pwd_entry.get().strip()

    # ---- QR-события (вызываются App через EventDispatcher) ----

    def on_qr_ready(self, url: str) -> None:
        """Получен токен входа — рисуем QR и просим отсканировать."""
        self._qr_widget.set_data(url)
        self._qr_status.configure(
            text="Отсканируйте код в приложении Telegram",
            text_color=C["text_sec"],
        )
        self._hide_widget(self._qr_refresh_btn)
        self._hide_widget(self._qr_cancel_btn)

    def on_qr_2fa(self) -> None:
        """QR-вход требует пароль 2FA — показываем поле пароля и кнопку «Войти»."""
        self._hide_widget(self._qr_refresh_btn)
        self._qr_status.configure(text="Введите пароль 2FA", text_color=C["text_sec"])
        # _pwd_frame — ребёнок вкладки входа; показываем его перед строкой ошибки.
        self._show_widget(self._pwd_frame, padx=SPACING["3xl"], fill="x",
                          pady=(0, SPACING["sm"]), before=self._error_lbl)
        # Кнопка «Войти» — ребёнок _qr_frame, под статусом.
        self._show_widget(self._qr_2fa_btn, pady=(0, SPACING["sm"]))
        # «Войти другим способом» — выход из застрявшего 2FA / смена аккаунта.
        self._show_widget(self._qr_cancel_btn)
        self._pwd_entry.focus()

    def on_qr_2fa_retry(self, msg: str) -> None:
        """Неверный пароль 2FA — остаёмся в поле пароля, даём повторить."""
        # Поле пароля и кнопка уже показаны (on_qr_2fa); просто сообщаем об
        # ошибке и чистим ввод для повторной попытки.
        if not self._pwd_frame.winfo_ismapped():
            self.on_qr_2fa()
        self._qr_status.configure(text=msg, text_color=C["error"])
        self._qr_2fa_btn.set_loading(False)
        self._pwd_entry.clear()
        self._pwd_entry.focus()

    def on_qr_expired(self) -> None:
        """Токен устарел — предлагаем обновить код."""
        self._qr_status.configure(
            text="Код устарел. Нажмите «Обновить».",
            text_color=C["warning"],
        )
        self._show_widget(self._qr_refresh_btn, pady=(0, SPACING["sm"]))

    def on_qr_error(self, msg: str) -> None:
        """Ошибка QR-входа — показываем сообщение и кнопку обновления."""
        self._qr_status.configure(text=msg, text_color=C["error"])
        self._show_widget(self._qr_refresh_btn, pady=(0, SPACING["sm"]))

    # ---- Handlers ----

    def _on_mode_change(self, value: str) -> None:
        if value == "QR-код":
            # QR-вход требует API-кредов (как и вход по номеру).
            if not self._app.has_api_creds():
                self.set_error("Сначала укажите API ID и API Hash")
                self._mode_var.set("По номеру")
                return
            # Скрываем виджеты входа по номеру.
            self._hide_widget(self._phone_entry)
            self._hide_widget(self._code_entry)
            self._hide_widget(self._pwd_frame)
            self._hide_widget(self._action_btn)
            # Показываем QR-блок. Очищаем старую картинку/поле пароля, чтобы
            # при повторном входе по QR не висело предыдущее состояние.
            self.clear_error()
            self._qr_widget.clear()
            self._pwd_entry.clear()
            self._hide_widget(self._qr_refresh_btn)
            self._hide_widget(self._qr_2fa_btn)
            self._hide_widget(self._qr_cancel_btn)
            self._hide_widget(self._pwd_frame)
            self._qr_status.configure(text="Запрашиваю код…", text_color=C["text_sec"])
            self._show_widget(self._qr_frame, pady=(0, SPACING["md"]),
                              before=self._error_lbl)
            self._app.start_qr_login()
        else:
            # Возврат в режим входа по номеру.
            self._app.stop_qr_login()
            self._hide_widget(self._qr_frame)
            self._hide_widget(self._qr_refresh_btn)
            self._hide_widget(self._qr_2fa_btn)
            self._hide_widget(self._qr_cancel_btn)
            self._hide_widget(self._pwd_frame)  # мог остаться от QR-2FA
            self._code_entry.clear()
            self._pwd_entry.clear()
            self._qr_widget.clear()
            self._state = "phone"
            # Сначала возвращаем поле телефона в packing-порядок (до refresh_state,
            # который ссылается на него через before=), затем кнопку действия.
            self._show_widget(self._phone_entry, padx=SPACING["3xl"], fill="x",
                              pady=(0, SPACING["xs"]), before=self._error_lbl)
            self._show_widget(self._action_btn, padx=SPACING["3xl"], fill="x",
                              pady=(0, SPACING["lg"]))
            self._action_btn.set_idle_text("Получить код")
            self.refresh_state()

    def _on_qr_2fa_submit(self) -> None:
        self.clear_error()
        pwd = self._pwd_entry.get().strip()
        if not pwd:
            self._qr_status.configure(text="Введите пароль 2FA", text_color=C["error"])
            return
        self._qr_status.configure(text="Проверяю пароль…", text_color=C["text_sec"])
        self._app.verify_qr_password(pwd)

    def _on_action(self) -> None:
        self.clear_error()
        if self._state == "phone":
            phone = self.phone
            if not phone:
                self.set_error("Введите номер телефона")
                return
            self.set_loading(True)
            self._app.send_code(phone)
        elif self._state in ("code", "loading"):
            code = self.code
            if not code:
                self.set_error("Введите код из Telegram")
                return
            self.set_loading(True)
            self._app.verify_code(code, self.password)

    def _save_api(self) -> None:
        """Inline-сохранение API-ключей (без навигации в shell настроек)."""
        api_id = self._api_id_entry.get().strip()
        api_hash = self._api_hash_entry.get().strip()
        if not api_id or not api_id.isdigit():
            self._save_status.configure(text="API ID должен быть числом.", text_color=C["error"])
            return
        if not api_hash or len(api_hash) < 10:
            self._save_status.configure(text="API Hash выглядит некорректно.", text_color=C["error"])
            return
        try:
            # save_config сам вызывает refresh_state() в конце (переключит на «Вход»).
            self._app.save_config(api_id, api_hash)
        except Exception as exc:
            self._save_status.configure(text=f"Ошибка сохранения: {exc}", text_color=C["error"])
            return
        # refresh_state уже переключил на вкладку «Вход» и проставил бейдж ✓.
        self._save_status.configure(text="Сохранено ✓", text_color=C["success"])

    def _toggle_pwd_visibility(self) -> None:
        self._pwd_visible = not self._pwd_visible
        self._pwd_entry.set_show("" if self._pwd_visible else "•")
        self._eye_btn.configure(text="🙈" if self._pwd_visible else "👁")

    def _on_clear_api(self) -> None:
        import tkinter.messagebox as mb
        if mb.askyesno(
            "Сбросить API ключи",
            "Удалить API ID/Hash и сессию? После этого нужно будет ввести ключи заново.",
        ):
            self._app.clear_api_creds()

    # ---- Layout helpers ----

    def _show_widget(self, widget, **pack_kw) -> None:
        if not widget.winfo_ismapped():
            widget.pack(**pack_kw)

    def _hide_widget(self, widget) -> None:
        if widget.winfo_ismapped():
            widget.pack_forget()
