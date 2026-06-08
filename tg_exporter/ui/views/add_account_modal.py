"""
AddAccountModal — модалка добавления нового Telegram-аккаунта.

Использует отдельный TelegramClient поверх StringSession, не трогая текущий
активный клиент приложения. После успеха:
  - сохраняет session string в профиль (через App.save_active_profile_session
    логика вызывается с параметрами нового аккаунта)
  - переключает активный аккаунт

Два режима добавления (переключатель сверху):
  • «По номеру» — phone → code → (опц. 2FA) → success.
  • «QR-код»    — qr_login на ТОМ ЖЕ временном клиенте → скан → (опц. 2FA) →
    success. QR-логика гонится напрямую через telethon на временном клиенте
    (НЕ через App._auth/_client_mgr — они трогают активную сессию). Это зеркало
    core.auth.AuthService.{start_qr,poll_qr,recreate_qr,verify_qr_password},
    но привязанное к self._client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
import customtkinter as ctk

from ..theme import C, RADIUS, SPACING, WIDGET, font, font_display
from ..components.button import AppButton
from ..components.entry import AppEntry
from ..components.qr_widget import QRCodeWidget
from ..modal_utils import prepare_modal, show_modal

if TYPE_CHECKING:
    from ..app import App


class AddAccountModal(ctk.CTkToplevel):
    """Мини-флоу логина в отдельной модалке для второго+ аккаунта."""

    def __init__(self, app: "App") -> None:
        super().__init__(app)
        prepare_modal(self, app, 440, 560, "Добавить аккаунт")
        self._app = app
        self._client = None
        self._phone_hash: Optional[str] = None
        self._phone: str = ""
        self._step: str = "phone"  # phone | code | done
        # QR-вход (на том же временном клиенте, что и phone-флоу)
        self._mode: str = "phone"  # phone | qr
        self._qr_active: bool = False  # флаг работы поллинг-цикла QR
        self._qr = None  # активный telethon QRLogin на временном клиенте
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        show_modal(self, app)

    # ---------------------------------------------------------- build

    def _build(self) -> None:
        pad = SPACING["2xl"]

        ctk.CTkLabel(
            self, text="Добавить аккаунт",
            font=font_display(16, "bold"), text_color=C["text"],
        ).pack(pady=(pad, SPACING["xs"]))

        ctk.CTkLabel(
            self, text="Текущий аккаунт не выйдет — просто сохранится.",
            font=font(11), text_color=C["text_sec"],
            wraplength=360, justify="center",
        ).pack(pady=(0, SPACING["md"]))

        # Переключатель режима добавления: по номеру / QR-код
        self._mode_var = ctk.StringVar(value="По номеру")
        self._mode_seg = ctk.CTkSegmentedButton(
            self, values=["По номеру", "QR-код"],
            variable=self._mode_var, command=self._on_mode_change,
        )
        self._mode_seg.pack(padx=pad, pady=(0, SPACING["md"]))

        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.pack(fill="x", padx=pad)

        # Phone
        self._phone_frame = ctk.CTkFrame(self._body, fg_color="transparent")
        ctk.CTkLabel(
            self._phone_frame, text="Номер телефона",
            font=font(12), text_color=C["text_sec"], anchor="w",
        ).pack(fill="x")
        self._phone_entry = AppEntry(self._phone_frame, placeholder_text="+79991234567", size="md")
        self._phone_entry.pack(fill="x", pady=(SPACING["xs"], 0))
        self._phone_entry.bind("<Return>", lambda _e: self._on_send_code())

        # Code / 2FA
        self._code_frame = ctk.CTkFrame(self._body, fg_color="transparent")
        ctk.CTkLabel(
            self._code_frame, text="Код из Telegram",
            font=font(12), text_color=C["text_sec"], anchor="w",
        ).pack(fill="x")
        self._code_entry = AppEntry(self._code_frame, placeholder_text="12345", size="md")
        self._code_entry.pack(fill="x", pady=(SPACING["xs"], SPACING["sm"]))
        self._code_entry.bind("<Return>", lambda _e: self._on_submit_code())

        ctk.CTkLabel(
            self._code_frame, text="Пароль 2FA (если включён)",
            font=font(12), text_color=C["text_sec"], anchor="w",
        ).pack(fill="x")
        self._password_entry = AppEntry(
            self._code_frame, placeholder_text="пароль или пусто",
            show="•", size="md",
        )
        self._password_entry.pack(fill="x", pady=(SPACING["xs"], 0))
        self._password_entry.bind("<Return>", lambda _e: self._on_submit_code())

        # QR (скрыт по умолчанию, не пакуется)
        self._qr_frame = ctk.CTkFrame(self._body, fg_color="transparent")
        self._qr_widget = QRCodeWidget(self._qr_frame, size_px=200)
        self._qr_widget.pack(pady=(0, SPACING["sm"]))
        self._qr_status = ctk.CTkLabel(
            self._qr_frame, text="Запрашиваю код…",
            font=font(12), text_color=C["text_sec"],
            wraplength=320, justify="center",
        )
        self._qr_status.pack(pady=(0, SPACING["sm"]))
        # Поле пароля 2FA в QR-режиме (показывается по on_qr_2fa)
        self._qr_pwd_frame = ctk.CTkFrame(
            self._qr_frame, fg_color=C["surface"],
            border_width=1, border_color=C["border"],
            corner_radius=RADIUS["md"],
        )
        self._qr_pwd_entry = AppEntry(
            self._qr_pwd_frame, placeholder_text="Пароль 2FA", show="•",
            border_width=0, fg_color="transparent",
        )
        self._qr_pwd_entry.pack(side="left", fill="x", expand=True,
                                padx=(SPACING["xs"], 0))
        self._qr_pwd_entry.bind("<Return>", lambda _e: self._on_qr_2fa_submit())
        # Кнопка «Обновить код» (показывается при истечении/ошибке)
        self._qr_refresh_btn = AppButton(
            self._qr_frame, text="Обновить код", variant="secondary", size="sm",
            command=self._on_qr_refresh,
        )
        # Кнопка «Войти» (показывается в 2FA-состоянии)
        self._qr_2fa_btn = AppButton(
            self._qr_frame, text="Войти", size="sm",
            command=self._on_qr_2fa_submit,
        )

        # Status
        self._status_lbl = ctk.CTkLabel(
            self, text="", font=font(11),
            text_color=C["error"], wraplength=360, justify="left", anchor="w",
        )
        self._status_lbl.pack(fill="x", padx=pad, pady=(SPACING["sm"], 0))

        # Buttons — фикс-высота ряда чтобы кнопки не сжимались при packing
        btn_h = WIDGET["btn_h"]
        btn_row = ctk.CTkFrame(self, fg_color="transparent", height=btn_h)
        btn_row.pack(side="bottom", fill="x", padx=pad, pady=(SPACING["md"], pad))
        btn_row.pack_propagate(False)
        AppButton(btn_row, text="Отмена", variant="secondary", size="md",
                  command=self._on_cancel).pack(
            side="left", expand=True, fill="both", padx=(0, SPACING["sm"]),
        )
        self._primary_btn = AppButton(
            btn_row, text="Получить код", variant="primary", size="md",
            command=self._on_send_code,
        )
        self._primary_btn.pack(side="left", expand=True, fill="both")

        self._phone_frame.pack(fill="x")

    # ---------------------------------------------------------- actions

    def _on_send_code(self) -> None:
        phone = self._phone_entry.get().strip()
        if not phone:
            self._set_error("Введите номер телефона.")
            return
        if not phone.startswith("+"):
            phone = "+" + "".join(c for c in phone if c.isdigit())
        self._phone = phone
        self._set_error("")
        self._primary_btn.configure(state="disabled", text="Отправка кода...")
        self._app._worker.submit(self._bg_send_code, phone)

    def _on_submit_code(self) -> None:
        code = self._code_entry.get().strip()
        password = self._password_entry.get().strip()
        if not code:
            self._set_error("Введите код из Telegram.")
            return
        self._set_error("")
        self._primary_btn.configure(state="disabled", text="Проверка...")
        self._app._worker.submit(self._bg_verify_code, code, password)

    def _on_cancel(self) -> None:
        # Временный клиент подключён на worker-потоке (его event loop) —
        # disconnect ОБЯЗАТЕЛЬНО на worker, иначе из UI-потока он молча
        # не выполнится и клиент/сокет утечёт (CLAUDE.md §10.6). Окно
        # закрываем сразу на UI-потоке.
        self._qr_active = False
        self._app._worker.submit(self._dispose_client)
        self.destroy()

    # ---------------------------------------------------------- mode switch

    def _on_mode_change(self, value: str) -> None:
        if value == "QR-код":
            self._mode = "qr"
            # Скрываем виджеты входа по номеру + нижнюю primary-кнопку.
            self._phone_frame.pack_forget()
            self._code_frame.pack_forget()
            self._primary_btn.pack_forget()
            # Сбрасываем состояние QR-блока перед стартом.
            self._set_error("")
            self._qr_widget.clear()
            self._qr_pwd_entry.clear()
            self._hide(self._qr_pwd_frame)
            self._hide(self._qr_2fa_btn)
            self._hide(self._qr_refresh_btn)
            self._qr_status.configure(text="Запрашиваю код…", text_color=C["text_sec"])
            self._qr_frame.pack(fill="x")
            # Отключаем старый клиент и стартуем QR — ОБА на worker-потоке
            # (disconnect из UI-потока молча не выполнится → утечка). Worker
            # последовательный: dispose отработает до _bg_qr_start.
            self._qr_active = True
            self._app._worker.submit(self._dispose_client)
            self._app._worker.submit(self._bg_qr_start)
        else:
            self._mode = "phone"
            # Останавливаем поллинг и отключаем временный клиент.
            self._qr_active = False
            self._app._worker.submit(self._dispose_client)
            self._qr_frame.pack_forget()
            self._hide(self._qr_pwd_frame)
            self._hide(self._qr_2fa_btn)
            self._hide(self._qr_refresh_btn)
            self._qr_pwd_entry.clear()
            self._qr_widget.clear()
            self._set_error("")
            self._step = "phone"
            # Возвращаем поле телефона + primary-кнопку.
            self._phone_frame.pack(fill="x")
            if not self._primary_btn.winfo_ismapped():
                self._primary_btn.pack(side="left", expand=True, fill="both")
            self._primary_btn.configure(
                state="normal", text="Получить код", command=self._on_send_code,
            )

    def _on_qr_refresh(self) -> None:
        """Кнопка «Обновить код»: пересоздаёт истёкший QR-токен."""
        self._set_error("")
        self._hide(self._qr_refresh_btn)
        self._qr_status.configure(text="Обновляю код…", text_color=C["text_sec"])
        self._qr_active = True
        self._app._worker.submit(self._bg_qr_refresh)

    def _on_qr_2fa_submit(self) -> None:
        pwd = self._qr_pwd_entry.get().strip()
        if not pwd:
            self._qr_status.configure(text="Введите пароль 2FA", text_color=C["error"])
            return
        self._qr_status.configure(text="Проверяю пароль…", text_color=C["text_sec"])
        self._qr_2fa_btn.configure(state="disabled")
        self._app._worker.submit(self._bg_qr_password, pwd)

    # ---------------------------------------------------------- background

    def _bg_send_code(self, phone: str) -> None:
        try:
            self._app._client_mgr.ensure_event_loop()
            client = self._make_client()
            if client is None:
                self._emit("add_account_error", "Сначала войдите в первый аккаунт — нужны API ID и Hash.")
                return
            self._client = client
            client.connect()
            sent = client.send_code_request(phone)
            self._phone_hash = sent.phone_code_hash
            self._emit("add_account_code_sent", None)
        except Exception as exc:
            self._emit("add_account_error", _friendly(exc))

    def _bg_verify_code(self, code: str, password: str) -> None:
        try:
            client = self._client
            if client is None or not self._phone_hash:
                self._emit("add_account_error", "Сначала запросите код.")
                return
            try:
                client.sign_in(phone=self._phone, code=code, phone_code_hash=self._phone_hash)
            except Exception as exc:
                if "SessionPasswordNeededError" in type(exc).__name__ or "SESSION_PASSWORD" in str(exc):
                    if not password:
                        self._emit("add_account_2fa", None)
                        return
                    client.sign_in(password=password)
                else:
                    raise
            self._finish_success(client)
        except Exception as exc:
            self._emit("add_account_error", _friendly(exc))

    def _finish_success(self, client) -> None:
        """
        Общий успех-путь для phone- и QR-флоу: сохраняет сессию в профиль,
        отключает временный клиент, эмитит add_account_done. Вызывается из
        фонового потока.
        """
        session_str = client.session.save()
        me = client.get_me()
        phone = "+" + str(getattr(me, "phone", "") or "").lstrip("+")
        if phone == "+":
            phone = self._phone
        display_name = " ".join(filter(None, [
            getattr(me, "first_name", "") or "",
            getattr(me, "last_name", "") or "",
        ])).strip() or phone
        self._app._profiles.add_or_update(
            phone=phone,
            api_id=self._app.config.api_id,
            session_string=session_str,
            display_name=display_name,
            set_active=True,
        )
        try:
            client.disconnect()
        except Exception:
            pass
        self._client = None
        self._emit("add_account_done", phone)

    # ---------------------------------------------------------- QR background

    def _bg_qr_start(self) -> None:
        """
        Создаёт временный клиент, коннектится, запускает qr_login (с ретраем
        AuthRestartError до 3 раз), эмитит qr_ready, затем входит в поллинг.
        Гоняется в worker-потоке; QR-логика — на self._client (не на App._auth).
        """
        from telethon.errors import (
            SessionPasswordNeededError, AuthRestartError,
        )
        try:
            self._app._client_mgr.ensure_event_loop()
            client = self._make_client()
            if client is None:
                self._qr_active = False
                self._emit(
                    "add_account_qr_error",
                    "Сначала войдите в первый аккаунт — нужны API ID и Hash.",
                )
                return
            self._client = client
            client.connect()
            last_exc = None
            for _ in range(3):
                try:
                    self._qr = client.qr_login()
                    break
                except AuthRestartError as exc:
                    last_exc = exc
                    self._qr = None
                    continue
                except SessionPasswordNeededError:
                    # 2FA-аккаунт, токен уже привязан → сразу пароль.
                    self._emit("add_account_qr_2fa", None)
                    self._qr_active = False
                    return
            if self._qr is None:
                raise last_exc if last_exc else RuntimeError("qr_login failed")
            if not self._qr_active:
                return  # пользователь ушёл, пока коннектились
            self._emit("add_account_qr_ready", self._qr.url)
            self._bg_qr_poll_loop()
        except Exception as exc:
            self._qr_active = False
            self._emit("add_account_qr_error", _friendly(exc))

    def _bg_qr_poll_loop(self) -> None:
        """
        Поллит QRLogin.wait в цикле, пока self._qr_active. Зеркало
        AuthService.poll_qr, но на self._client.loop.run_until_complete
        (wait — async, не синкифицирован telethon.sync).
        """
        import asyncio
        from telethon.errors import SessionPasswordNeededError, AuthRestartError

        client = self._client
        if client is None or self._qr is None:
            return
        while self._qr_active:
            try:
                result = client.loop.run_until_complete(self._qr.wait(timeout=5))
            except asyncio.TimeoutError:
                # Никто не отсканировал за timeout — проверяем срок токена.
                if not self._qr_active:
                    return
                if self._qr_expired():
                    self._qr_active = False
                    self._emit("add_account_qr_expired", None)
                    return
                continue
            except SessionPasswordNeededError:
                self._qr_active = False
                self._emit("add_account_qr_2fa", None)
                return
            except AuthRestartError:
                # Telegram просит перезапросить токен → как «истёк».
                self._qr_active = False
                self._emit("add_account_qr_expired", None)
                return
            except ConnectionError:
                # Соединение прервалось — не терминально, продолжаем.
                if not self._qr_active:
                    return
                continue
            except Exception as exc:
                msg = str(exc)
                if "SESSION_PASSWORD_NEEDED" in msg or "password" in msg.lower():
                    self._qr_active = False
                    self._emit("add_account_qr_2fa", None)
                    return
                self._qr_active = False
                self._emit("add_account_qr_error", _friendly(exc))
                return
            # Пользователь мог уйти, пока блокировались в wait(5).
            if not self._qr_active:
                return
            if result:
                # Вошёл — сохраняем профиль и закрываем.
                self._qr_active = False
                self._qr = None
                try:
                    self._finish_success(client)
                except Exception as exc:
                    self._emit("add_account_qr_error", _friendly(exc))
                return
            # result пустой (на практике сюда не попадаем) — проверяем срок.
            if self._qr_expired():
                self._qr_active = False
                self._emit("add_account_qr_expired", None)
                return

    def _qr_expired(self) -> bool:
        """True, если QR-токен истёк (по qr.expires)."""
        import datetime
        expires = getattr(self._qr, "expires", None)
        if expires is None:
            return False
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            return now >= expires
        except TypeError:
            return now.replace(tzinfo=None) >= expires

    def _bg_qr_refresh(self) -> None:
        """Пересоздаёт истёкший QR-токен (кнопка «Обновить») и продолжает поллинг."""
        from telethon.errors import (
            SessionPasswordNeededError, AuthRestartError,
        )
        client = self._client
        try:
            if client is None or self._qr is None:
                # Клиент отвалился — стартуем заново.
                self._app._worker.submit(self._bg_qr_start)
                return
            try:
                client.loop.run_until_complete(self._qr.recreate())
            except AuthRestartError:
                self._qr = None
                self._bg_qr_start()
                return
            except SessionPasswordNeededError:
                self._qr_active = False
                self._emit("add_account_qr_2fa", None)
                return
            except ConnectionError:
                self._qr = None
                self._bg_qr_start()
                return
            if not self._qr_active:
                return
            self._emit("add_account_qr_ready", self._qr.url)
            self._bg_qr_poll_loop()
        except Exception as exc:
            self._qr_active = False
            self._emit("add_account_qr_error", _friendly(exc))

    def _bg_qr_password(self, password: str) -> None:
        """
        Пароль 2FA после QR-сканирования. Неверный пароль — НЕ терминальная
        ошибка: даём повторить (add_account_qr_2fa). Зеркало
        AuthService.verify_qr_password, но на self._client.
        """
        from telethon.errors import (
            PasswordHashInvalidError, SessionPasswordNeededError,
        )
        client = self._client
        try:
            if client is None:
                self._emit("add_account_qr_error", "QR-вход не запущен.")
                return
            try:
                client.sign_in(password=password)
            except PasswordHashInvalidError:
                self._emit("add_account_qr_2fa", "wrong")
                return
            except SessionPasswordNeededError:
                self._emit("add_account_qr_2fa", "wrong")
                return
            self._qr = None
            self._finish_success(client)
        except Exception as exc:
            self._emit("add_account_qr_error", _friendly(exc))

    def _make_client(self):
        """Собирает отдельный TelegramClient — не трогает активный `_client_mgr._client`."""
        from telethon.sync import TelegramClient
        from telethon.sessions import StringSession
        api_id_int = self._app.config.api_id_int
        api_hash = self._app.credentials.load_api_hash(self._app.config.api_id)
        if not api_id_int or not api_hash:
            return None
        # Прокси: временный прокси входа (если задан в этой модалке) или прокси
        # активного профиля. Без него добавить 2-й аккаунт за прокси нельзя —
        # временный клиент бы шёл напрямую и таймаутил у РФ-юзеров без VPN.
        kwargs = self._proxy_kwargs()
        return TelegramClient(StringSession(), api_id_int, api_hash, **kwargs)

    def _proxy_kwargs(self) -> dict:
        """proxy=/connection= для временного клиента. Невалидный прокси игнорируется."""
        proxy_url = (self._app.pending_proxy or "").strip()
        if not proxy_url:
            active = self._app.active_profile()
            if active:
                proxy_url = (self._app._profiles.load_proxy(active) or "").strip()
        if not proxy_url:
            return {}
        from ...core.proxy import parse_proxy, ProxyValidationError
        try:
            cfg = parse_proxy(proxy_url)
        except ProxyValidationError:
            return {}
        if cfg is None:
            return {}
        tele = cfg.to_telethon()
        kwargs = {"proxy": tele["proxy"]}
        if tele["connection"] is not None:
            kwargs["connection"] = tele["connection"]
        return kwargs

    def _dispose_client(self) -> None:
        # Только отключает временный клиент. Флагом _qr_active управляют
        # вызывающие явно (иначе при submit(dispose)+submit(start) dispose
        # сбросил бы флаг и поллинг сразу бы вышел). Вызывать на worker-потоке.
        self._qr = None
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

    def _emit(self, event: str, payload) -> None:
        # Модалка сама регистрируется в App.on(...) через колбэки в handle_event
        self._app._worker.put_event(event, (self, payload))

    # ---------------------------------------------------------- ui updates (вызываются из App)

    def on_code_sent(self) -> None:
        self._step = "code"
        self._phone_frame.pack_forget()
        self._code_frame.pack(fill="x")
        self._primary_btn.configure(state="normal", text="Войти", command=self._on_submit_code)
        self._code_entry.focus_set()
        self._set_error("Код отправлен в Telegram.", color=C["text_sec"])

    def on_2fa_required(self) -> None:
        self._primary_btn.configure(state="normal", text="Войти", command=self._on_submit_code)
        self._set_error("Требуется пароль 2FA.", color=C["text_sec"])
        self._password_entry.focus_set()

    def on_done(self, phone: str) -> None:
        self.destroy()

    def on_error(self, message: str) -> None:
        if self._mode == "qr":
            # QR-режим: ошибки показываем в статусе QR-блока + кнопка «Обновить».
            self.on_qr_error(message)
            return
        self._primary_btn.configure(
            state="normal",
            text="Войти" if self._step == "code" else "Получить код",
        )
        self._set_error(message, color=C["error"])

    # ---- QR ui updates (вызываются из App) ----

    def on_qr_ready(self, url: str) -> None:
        """Получен токен входа — рисуем QR и просим отсканировать."""
        self._hide(self._qr_pwd_frame)
        self._hide(self._qr_2fa_btn)
        self._hide(self._qr_refresh_btn)
        self._qr_widget.set_data(url)
        self._qr_status.configure(
            text="Отсканируйте код в приложении Telegram",
            text_color=C["text_sec"],
        )

    def on_qr_2fa(self, retry: bool = False) -> None:
        """QR-вход требует пароль 2FA — показываем поле пароля и кнопку «Войти»."""
        self._hide(self._qr_refresh_btn)
        if retry:
            self._qr_status.configure(
                text="Неверный пароль 2FA. Попробуйте ещё раз.",
                text_color=C["error"],
            )
            self._qr_pwd_entry.clear()
        else:
            self._qr_status.configure(text="Введите пароль 2FA", text_color=C["text_sec"])
        if not self._qr_pwd_frame.winfo_ismapped():
            self._qr_pwd_frame.pack(fill="x", pady=(0, SPACING["sm"]))
        if not self._qr_2fa_btn.winfo_ismapped():
            self._qr_2fa_btn.pack(pady=(0, SPACING["sm"]))
        self._qr_2fa_btn.configure(state="normal")
        self._qr_pwd_entry.focus_set()

    def on_qr_expired(self) -> None:
        """Токен устарел — предлагаем обновить код."""
        self._qr_status.configure(
            text="Код устарел. Нажмите «Обновить код».",
            text_color=C["warning"],
        )
        if not self._qr_refresh_btn.winfo_ismapped():
            self._qr_refresh_btn.pack(pady=(0, SPACING["sm"]))

    def on_qr_error(self, message: str) -> None:
        """Ошибка QR-входа — показываем сообщение и кнопку обновления."""
        self._qr_2fa_btn.configure(state="normal")
        self._qr_status.configure(text=message or "Ошибка QR-входа", text_color=C["error"])
        if not self._qr_refresh_btn.winfo_ismapped():
            self._qr_refresh_btn.pack(pady=(0, SPACING["sm"]))

    def _set_error(self, text: str, color=None) -> None:
        self._status_lbl.configure(text=text, text_color=color or C["error"])

    def _hide(self, widget) -> None:
        if widget.winfo_ismapped():
            widget.pack_forget()


def _friendly(exc: Exception) -> str:
    msg = str(exc)
    if "PHONE_CODE_INVALID" in msg:
        return "Неверный код."
    if "PHONE_CODE_EXPIRED" in msg:
        return "Код устарел. Запросите новый."
    if "PHONE_NUMBER_INVALID" in msg:
        return "Неверный номер."
    if "PHONE_NUMBER_BANNED" in msg:
        return "Номер заблокирован."
    if "PASSWORD_HASH_INVALID" in msg or ("password" in msg.lower() and "invalid" in msg.lower()):
        return "Неверный пароль 2FA."
    if "API_ID_INVALID" in msg:
        return "Неверный API ID/Hash."
    if "FLOOD_WAIT" in msg:
        return "Слишком много попыток. Подождите."
    if "network" in msg.lower() or "connect" in msg.lower():
        return "Ошибка сети."
    return msg[:200]
