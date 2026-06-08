"""
App — главное окно и контроллер приложения.

Владеет:
  - AppConfig (конфиг без секретов)
  - CredentialsManager (keyring)
  - TelegramClientManager (telegram client)
  - AuthService (login flow)
  - BackgroundWorker (фоновый поток + очередь событий)
  - EventDispatcher (роутинг событий к UI)
  - ExportOrchestrator (логика экспорта)

Навигация: show_login() ↔ show_chats()
"""

from __future__ import annotations

import datetime
import os
from typing import Optional
import customtkinter as ctk

from .theme import C, WINDOW
from .views.login_view import LoginView
from .views.chats_page import ChatsPage
from .views.accounts_page import AccountsPage
from .views.settings_page import SettingsPage
from .views.help_page import HelpPage
from .views.export_modal import ExportModal
from .components.sidebar import Sidebar

from ..models.config import AppConfig
from ..models.export_task import ExportTask, ExportProgress, ExportFormat, AuthorFilter
from ..core.credentials import CredentialsManager
from ..core.client import TelegramClientManager
from ..core.auth import AuthService, AuthStep, QrNeedsPassword
from ..core.orchestrator import ExportOrchestrator
from ..core.profiles import ProfileManager, Profile
from ..core import forum
from ..core.export_queue import ExportQueue, build_topic_jobs
from ..services.export_history import ExportHistory
from ..utils.cancellation import CancellationToken
from ..utils.dates import resolve_period_to_range
from ..utils.worker import BackgroundWorker, EventDispatcher
from ..utils.logger import logger

try:
    from telethon import functions
    from telethon.utils import get_peer_id
    _TELETHON_OK = True
except ImportError:
    _TELETHON_OK = False


class App(ctk.CTk):
    """
    Главное окно приложения.

    Обязанности:
    - Инициализация Phase 1/2 объектов
    - Навигация между экранами
    - Обработка UIEvent очереди (polling каждые 80 мс)
    - Делегирование логики сервисам

    НЕ содержит: бизнес-логику, прямые Telegram-вызовы, export код.
    """

    # ---- Init ----

    def __init__(self) -> None:
        super().__init__()
        self._setup_window()

        # Phase 1: config и credentials
        self.config = AppConfig.load()
        self.credentials = CredentialsManager()
        self._migrate_legacy_config()

        # Phase 2: сервисы
        self._client_mgr = TelegramClientManager(self.config, self.credentials)
        self._profiles = ProfileManager(self.credentials)
        self._auth = AuthService(self._client_mgr)
        self._history = ExportHistory()
        self._worker = BackgroundWorker()
        self._dispatcher = EventDispatcher()
        self._token = CancellationToken()

        # Состояние
        self._all_dialogs: list = []
        self._folder_peers: dict = {}
        self._folder_filters: dict = {}
        self._folder_excludes: dict = {}
        self._current_folder: str = "Все чаты"
        self._date_period_days: int = 0
        self._custom_date_from: Optional[datetime.datetime] = None
        self._custom_date_to: Optional[datetime.datetime] = None
        self._active_export_modal: Optional[ExportModal] = None
        # Временный прокси для входа (до создания профиля). Применяется к клиенту
        # на send_code и переносится в профиль после успешного логина.
        self._pending_proxy: str = ""
        # Активен ли цикл поллинга QR-входа (выключается при уходе с QR-режима).
        self._qr_active: bool = False
        self._folder_active: bool = False
        self._folder_mode: str = "По чатам"   # "По чатам" | "Один .md на чат" | "Один .md на папку"
        self._folder_transcribe: bool = False
        self._folder_queue: list = []
        self._folder_index: int = 0
        self._folder_export_base: Optional[str] = None
        self._folder_log: list[str] = []
        # Период из шапки фиксируется в момент запуска экспорта папки, чтобы
        # все чаты пакета выгружались с одинаковым диапазоном дат.
        self._folder_dates: tuple[Optional[datetime.datetime], Optional[datetime.datetime]] = (None, None)

        # Состояние пакетного экспорта топиков форума (аналог folder, но изолирован).
        self._topic_queue: Optional[ExportQueue] = None
        self._topic_active: bool = False
        self._topic_base: Optional[str] = None
        self._topic_options: Optional[dict] = None

        # Views / Shell
        self._container = ctk.CTkFrame(self, fg_color="transparent")
        self._container.pack(fill="both", expand=True)

        # LOGIN-режим: login_view на всё окно
        self.login_view = LoginView(self._container, self)

        # SHELL-режим: sidebar + контейнер страниц
        self._shell = ctk.CTkFrame(self._container, fg_color="transparent")
        self.sidebar = Sidebar(self._shell, self, on_select=self._show_page)
        self.sidebar.pack(side="left", fill="y")
        self._page_container = ctk.CTkFrame(self._shell, fg_color="transparent")
        self._page_container.pack(side="left", fill="both", expand=True)
        # Все страницы — в ОДНОЙ grid-ячейке (оверлап). Переключение через
        # tkraise() мгновенно, без pack/forget (перепаковка = видимая задержка).
        self._page_container.grid_rowconfigure(0, weight=1)
        self._page_container.grid_columnconfigure(0, weight=1)

        self.chats_page = ChatsPage(self._page_container, self)
        self.accounts_page = AccountsPage(self._page_container, self)
        self.settings_page = SettingsPage(self._page_container, self)
        self.help_page = HelpPage(self._page_container, self)
        self._pages = {
            "chats": self.chats_page, "accounts": self.accounts_page,
            "settings": self.settings_page, "help": self.help_page,
        }
        for page in self._pages.values():
            page.grid(row=0, column=0, sticky="nsew")
        self._current_page = None
        self._current_view = None

        # Регистрация обработчиков событий
        self._register_handlers()

        # Запуск
        self._worker.start()
        self.show_login()
        self.after(80, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_window(self) -> None:
        self.title(WINDOW["title"])
        self.geometry(WINDOW["size"])
        self.minsize(*WINDOW["min_size"])
        self.configure(fg_color=C["bg"])
        # Win11 + Tk + Python 3.14 теряет окно при разворачивании из таскбара,
        # если последняя сохранённая Tk-геометрия попала в координаты вне
        # подключённых мониторов (отключённый второй экран, undock ноута).
        # MainThread штатно ждёт WM_*, окно «не отвечает» — спасает только
        # принудительный SetWindowPos. Здесь страхуемся через <Map>.
        self.update_idletasks()
        self._ensure_visible()
        self.bind("<Map>", self._on_map)

    def _on_map(self, event) -> None:
        # <Map> на toplevel ловит события ВСЕХ дочерних виджетов: путь "."
        # входит в bindtags каждого потомка, поэтому при старте/открытии модалок
        # хендлер дёргается сотнями. Нас интересует только разворачивание
        # самого окна (deiconify из таскбара) — остальное отсекаем по пути.
        if str(event.widget) == str(self):
            self._ensure_visible()

    def _ensure_visible(self) -> None:
        """Если окно вне видимой области primary-монитора — центрируем его."""
        try:
            self.update_idletasks()
            x, y = self.winfo_x(), self.winfo_y()
            w, h = self.winfo_width(), self.winfo_height()
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            # Полностью вне primary screen — переносим в центр.
            if x + w <= 0 or y + h <= 0 or x >= sw or y >= sh:
                new_x = max(0, (sw - w) // 2)
                new_y = max(0, (sh - h) // 2)
                self.geometry(f"{w}x{h}+{new_x}+{new_y}")
        except Exception:
            pass  # _ensure_visible не должен ронять UI

    # ---- Navigation ----

    def show_login(self) -> None:
        # LOGIN-режим: прячем shell, показываем login на всё окно.
        self._qr_active = False
        self._shell.pack_forget()
        self.login_view.pack(fill="both", expand=True)
        self._current_view = self.login_view
        # Сброс возможного залипшего QR-режима (напр. после logout из QR).
        self.login_view.reset_to_phone_mode()
        self.login_view.refresh_state()
        if self.has_api_creds() and self._has_any_session():
            self._worker.submit(self._bg_check_session)

    def _has_any_session(self) -> bool:
        """Есть ли хоть одна сохранённая сессия — активный профиль или дефолтная."""
        active = self._profiles.active()
        if active and self._profiles.load_session(active):
            return True
        return bool(self.credentials.load_session(self.config.api_id))

    def _show_shell(self) -> None:
        """SHELL-режим: login скрыт, видны sidebar + страницы."""
        self.login_view.pack_forget()
        self._shell.pack(fill="both", expand=True)
        self._current_view = self._shell

    def _show_page(self, name: str) -> None:
        """Переключает активную страницу в контейнере + подсветку sidebar."""
        page = self._pages.get(name)
        if page is None:
            return
        # tkraise — мгновенно, без перепаковки (страница уже в grid-ячейке).
        page.tkraise()
        self._current_page = page
        self.sidebar.set_active(name)
        # Обновление данных — ПОСЛЕ отрисовки (after), чтобы не задерживать
        # появление страницы (refresh перечитывает config / рисует карточки).
        if name in ("accounts", "settings"):
            self.after(0, page.refresh)

    def show_chats(self) -> None:
        self._show_shell()
        self._show_page("chats")
        self.load_chats()

    def show_settings(self) -> None:
        self._show_shell()
        self._show_page("settings")

    def show_api_keys(self) -> None:
        # API-ключи теперь часть страницы настроек.
        self._show_shell()
        self._show_page("settings")

    def show_help(self) -> None:
        self._show_shell()
        self._show_page("help")

    def show_add_account(self) -> None:
        from .views.add_account_modal import AddAccountModal
        AddAccountModal(self)

    def show_proxy_settings(self, phone: str) -> None:
        from .views.proxy_modal import ProxyModal
        ProxyModal(self, phone)

    def show_login_proxy(self) -> None:
        """Прокси для входа (до создания профиля) — открывает модалку без phone."""
        from .views.proxy_modal import ProxyModal
        ProxyModal(self, None)

    @property
    def pending_proxy(self) -> str:
        return self._pending_proxy

    def set_pending_proxy(self, proxy_url: str) -> None:
        """Сохраняет временный прокси входа и применяет к клиенту до логина."""
        self._pending_proxy = proxy_url or ""
        self._worker.submit(self._client_mgr.use_proxy, self._pending_proxy)

    def test_account_proxy(self, modal, proxy_url: str) -> None:
        """Проверяет прокси в фоне (кнопка «Тест связи» в ProxyModal)."""
        self._worker.submit(self._bg_test_proxy, modal, proxy_url)

    def apply_active_proxy(self, proxy_url: str) -> None:
        """
        Применяет прокси к клиенту активного профиля. use_proxy() рвёт и
        пересоздаёт клиент (disconnect → asyncio), поэтому выполняем в worker-
        потоке: нельзя трогать клиент из UI-потока (§10.6) и нужно сериализовать
        с возможным активным экспортом, который идёт в том же worker.
        """
        self._worker.submit(self._client_mgr.use_proxy, proxy_url)

    def show_export_dialog(self, dialog) -> None:
        modal = ExportModal(self, dialog)
        self._active_export_modal = modal

    def load_forum_topics(self, dialog, modal) -> None:
        """Грузит список топиков форума в фоне для модалки экспорта."""
        self._worker.submit(self._bg_load_topics, dialog, modal)

    def _bg_load_topics(self, dialog, modal) -> None:
        try:
            c = self._client_mgr.ensure_connected()
            topics = forum.get_forum_topics(c, dialog.entity)
            self._worker.put_event("topics_loaded", (modal, topics))
        except Exception as exc:
            logger.error("load_forum_topics failed", exc=exc)
            self._worker.put_event("topics_load_failed", (modal, str(exc)))

    # ---- Auth actions ----

    def send_code(self, phone: str) -> None:
        self._worker.submit(self._bg_send_code, phone)

    def verify_code(self, code: str, password: str = "") -> None:
        self._worker.submit(self._bg_verify_code, code, password)

    def logout(self) -> None:
        self._worker.submit(self._bg_logout)

    # ---- QR-вход ----

    def start_qr_login(self) -> None:
        """Запускает вход по QR: фоновый цикл поллинга + событие qr_ready."""
        self._qr_active = True
        self._worker.submit(self._bg_qr_login)

    def stop_qr_login(self) -> None:
        """
        Останавливает поллинг QR и СБРАСЫВАЕТ незавершённый QR-вход (в т.ч.
        зависший на 2FA). Вызывается при переключении на вход по номеру —
        чтобы войти другим способом/номером с чистого листа. Сессию не трогает.
        """
        self._qr_active = False
        self._worker.submit(self._auth.cancel_qr)

    def refresh_qr(self) -> None:
        """Кнопка «Обновить»: пересоздаёт истёкший QR-токен и продолжает поллинг."""
        self._qr_active = True
        self._worker.submit(self._bg_qr_refresh)

    def verify_qr_password(self, password: str) -> None:
        """Пароль 2FA после сканирования QR."""
        self._worker.submit(self._bg_qr_password, password)

    # ---- Config actions ----

    def has_api_creds(self) -> bool:
        if not self.config.api_id:
            return False
        return bool(self.credentials.load_api_hash(self.config.api_id))

    def save_config(self, api_id: str, api_hash: str) -> None:
        old_id = self.config.api_id
        self.config = self.config.with_api_id(api_id)

        # Если api_id изменился — удаляем старые секреты
        if old_id and old_id != self.config.api_id:
            self.credentials.delete_all(old_id)
            self._client_mgr.destroy()

        try:
            self.credentials.save_api_hash(self.config.api_id, api_hash)
        except Exception as exc:
            logger.error("save_api_hash failed", exc=exc)

        self.config.save()
        self._client_mgr.update_config(self.config)
        self.login_view.refresh_state()

    def clear_api_creds(self) -> None:
        if self.config.api_id:
            self.credentials.delete_all(self.config.api_id)
        self._client_mgr.destroy()
        self.config = AppConfig()
        self.config.save()
        self.login_view.refresh_state()

    # ---- Chat list actions ----

    def load_chats(self) -> None:
        # На рефреше (когда уже есть чаты) держим список видимым — иначе при
        # медленном/завивающем get_dialogs() пользователь видит пустой
        # «Загрузка чатов...» и думает, что всё сломалось.
        if self._all_dialogs:
            self.chats_page.show_refreshing()
        else:
            self.chats_page.show_loading()
        self._worker.submit(self._bg_load_chats)

    def filter_chats(self, query: str = "") -> None:
        dialogs = self._get_folder_dialogs(self._current_folder)
        if query:
            q = query.lower()
            dialogs = [d for d in dialogs if q in (d.name or "").lower()]
        self.chats_page.render_chats(dialogs)

    def set_current_folder(self, folder_name: str) -> None:
        self._current_folder = folder_name or "Все чаты"

    def set_date_period(self, days: int) -> None:
        self._date_period_days = max(0, int(days))

    def set_custom_date_range(
        self,
        date_from: Optional[datetime.datetime],
        date_to: Optional[datetime.datetime],
    ) -> None:
        self._custom_date_from = date_from
        self._custom_date_to = date_to

    # ---- Export actions ----

    def start_export(self, dialog, output_path: str, modal: ExportModal) -> None:
        self._token = CancellationToken()
        self._active_export_modal = modal
        options = modal.get_export_options()

        # Инкрементальный offset
        last_id: Optional[int] = None
        if options.get("incremental"):
            try:
                peer_id = get_peer_id(dialog.entity)
                last_id = self._history.get_last_id(peer_id)
            except Exception:
                pass

        # Период берём только из модалки экспорта одного чата. Период
        # из шапки относится к экспорту папки и сюда не подмешивается —
        # иначе модалкин выбор «Все время» молча перекрывался шапкой.
        date_from = options.get("date_from")
        date_to = options.get("date_to")

        # Обновляем MarkdownSettings
        words = options.get("words_per_file", 50_000)
        import dataclasses
        self.config = dataclasses.replace(
            self.config,
            markdown=dataclasses.replace(self.config.markdown, words_per_file=words),
        )

        task = ExportTask(
            chat_id=getattr(dialog, "id", 0),
            chat_name=getattr(dialog, "name", "Chat") or "Chat",
            output_path=output_path,
            format=options.get("format", ExportFormat.BOTH),
            date_from=date_from,
            date_to=date_to,
            download_media=options.get("download_media", False),
            collect_analytics=options.get("collect_analytics", False),
            transcribe_audio=options.get("transcribe_audio", False),
            transcription_provider=self.config.transcription_provider,
            transcription_language=self.config.transcription_language,
            local_whisper_model=self.config.local_whisper_model,
            deepgram_api_key=self.credentials.load_deepgram_key() or "",
            author_filter=AuthorFilter(),
            incremental=options.get("incremental", False),
            last_exported_id=last_id,
            words_per_file=words,
        )

        progress = ExportProgress()
        deepgram_key = self.credentials.load_deepgram_key()
        orch = ExportOrchestrator(self._client_mgr, self.config, self._history, deepgram_key)
        token = self._token

        self._worker.submit(
            orch.run, dialog, task, token, progress,
            lambda etype, payload: self._worker.put_event(etype, payload),
        )

    def start_topics_export(self, dialog, output_path: str, modal, topics) -> None:
        """Запускает последовательный экспорт выбранных топиков форума."""
        import datetime, os
        from ..exporters.base import sanitize_filename

        if not topics:
            self._worker.put_event("error", "Не выбран ни один топик.")
            return

        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        base = os.path.join(output_path, f"{sanitize_filename(dialog.name or 'forum')}_{ts}")
        os.makedirs(base, exist_ok=True)

        self._topic_queue = ExportQueue(build_topic_jobs(dialog, topics))
        self._topic_active = True
        self._topic_base = base
        self._topic_options = modal.get_export_options()
        self._active_export_modal = modal
        self._export_next_topic()

    def _export_next_topic(self) -> None:
        q = self._topic_queue
        if not self._topic_active or q is None or not q.has_next():
            if q is not None:
                self._worker.put_event("topic_done", (self._topic_base, q.ok, q.failed))
            self._topic_active = False
            self._topic_queue = None
            return

        job = q.next()
        self._worker.put_event("topic_progress", (q.current_index, q.total, job.topic_title))

        opts = self._topic_options or {}
        self._token = CancellationToken()
        task = ExportTask(
            chat_id=getattr(job.dialog, "id", 0),
            chat_name=job.dialog.name or "Chat",
            output_path=self._topic_base,
            format=opts.get("format", ExportFormat.BOTH),
            date_from=opts.get("date_from"),
            date_to=opts.get("date_to"),
            topic_id=job.topic_id,
            topic_title=job.topic_title,
            download_media=opts.get("download_media", False),
            collect_analytics=opts.get("collect_analytics", False),
            transcribe_audio=opts.get("transcribe_audio", False),
            transcription_provider=self.config.transcription_provider,
            transcription_language=self.config.transcription_language,
            local_whisper_model=self.config.local_whisper_model,
            deepgram_api_key=self.credentials.load_deepgram_key() or "",
            author_filter=AuthorFilter(),
            words_per_file=opts.get("words_per_file", 50_000),
        )
        progress = ExportProgress()
        deepgram_key = self.credentials.load_deepgram_key()
        orch = ExportOrchestrator(self._client_mgr, self.config, self._history, deepgram_key)
        token = self._token
        self._worker.submit(
            orch.run, job.dialog, task, token, progress,
            lambda etype, payload: self._worker.put_event(etype, payload),
        )

    def cancel_export(self) -> None:
        self._token.cancel()
        self._folder_active = False
        self._topic_active = False
        self._topic_queue = None

    def export_current_folder(self, mode: str = "По чатам", transcribe: bool = False) -> None:
        folder = self._current_folder
        if not folder or folder == "Все чаты":
            self._worker.put_event("error", "Выберите папку для экспорта.")
            return
        dialogs = self._get_folder_dialogs(folder)
        if not dialogs:
            self._worker.put_event("error", "В выбранной папке нет чатов.")
            return
        import tkinter.filedialog as fd
        path = fd.askdirectory(title="Куда сохранить экспорт папки?")
        if not path:
            return
        import os
        from ..exporters.base import sanitize_filename
        base = os.path.join(path, sanitize_filename(folder))
        os.makedirs(base, exist_ok=True)
        self._folder_queue = list(dialogs)
        self._folder_index = 0
        self._folder_export_base = base
        self._folder_mode = mode
        self._folder_transcribe = transcribe
        self._folder_log = []
        self._folder_active = True
        # Фиксируем период один раз — все чаты пакета пойдут с одинаковыми датами
        self._folder_dates = self._resolve_folder_dates()
        self._worker.put_event("folder_progress", (0, len(dialogs), folder))
        self._export_next_in_folder()

    def _resolve_folder_dates(self) -> tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
        """Снимок шапочного периода для ExportTask. Логика — в utils.dates."""
        return resolve_period_to_range(
            self._date_period_days,
            self._custom_date_from,
            self._custom_date_to,
        )

    # ---- Profiles ----

    def profiles(self) -> list[Profile]:
        return self._profiles.list()

    def active_profile(self) -> Optional[Profile]:
        return self._profiles.active()

    def switch_profile(self, phone: str) -> None:
        """Переключается на указанный профиль — без повторного ввода кода."""
        profile = self._profiles.get(phone)
        if profile is None:
            return
        self._worker.submit(self._bg_switch_profile, profile)

    def remove_profile(self, phone: str) -> None:
        """Удаляет профиль. Если это был активный — переключает на первый из оставшихся или показывает логин."""
        was_active = (self._profiles.active_phone() == phone)
        self._profiles.remove(phone)
        if not was_active:
            return
        next_profile = self._profiles.active()
        if next_profile is None:
            # Последний профиль — полный logout
            self._worker.submit(self._bg_logout)
        else:
            self._worker.submit(self._bg_switch_profile, next_profile)

    def save_active_profile_session(self, phone: Optional[str] = None, display_name: str = "") -> None:
        """Сохраняет текущую сессию клиента в профиль (вызывается после успешного логина)."""
        try:
            client = self._client_mgr.get_client()
            session_str = client.session.save() or ""
            if not session_str:
                return
            phone = phone or ""
            if not phone:
                try:
                    me = client.get_me()
                    phone = "+" + str(getattr(me, "phone", "") or "")
                    if not display_name:
                        display_name = " ".join(filter(None, [
                            getattr(me, "first_name", "") or "",
                            getattr(me, "last_name", "") or "",
                        ])).strip()
                except Exception:
                    pass
            if not phone or phone == "+":
                return
            self._profiles.add_or_update(
                phone=phone, api_id=self.config.api_id,
                session_string=session_str,
                display_name=display_name, set_active=True,
            )
            # Переносим временный прокси входа в созданный профиль, чтобы он
            # сохранился между запусками (а не жил только в памяти сессии).
            if self._pending_proxy:
                self._profiles.set_proxy(phone, self._pending_proxy)
                self._pending_proxy = ""
        except Exception as exc:
            logger.error("save_active_profile_session failed", exc=exc)

    # ---- Transcription settings ----

    def set_transcription_provider(self, provider: str) -> None:
        self.config = _update_config(self.config, transcription_provider=provider)
        self.config.save()

    def set_local_whisper_model(self, model: str) -> None:
        self.config = _update_config(self.config, local_whisper_model=model or "base")
        self.config.save()

    # ---- Background tasks ----

    def _bg_check_session(self) -> None:
        """
        Автовход: восстанавливает сохранённую сессию активного профиля.
        Устойчив к сбоям прокси — если коннект через прокси не прошёл, но
        сессия валидна, пробует БЕЗ прокси (юзер мог включить VPN/выйти из РФ).
        Любая ошибка не «съедается» молча — пользователь получает сообщение.
        """
        try:
            active = self._profiles.active()
            session_str = None
            proxy_url = ""
            if active:
                proxy_url = self._profiles.load_proxy(active) or ""
                session_str = self._profiles.load_session(active)
                if session_str and active.api_id and active.api_id != self.config.api_id:
                    self.config = self.config.with_api_id(active.api_id)
                    self.config.save()
                    self._client_mgr.update_config(self.config)

            # Попытка 1: с прокси профиля (если задан).
            self._client_mgr.use_proxy(proxy_url)
            if session_str:
                self._client_mgr.use_session(session_str)
            result = self._auth.check_session()
            if result.step == AuthStep.SUCCESS:
                self._worker.put_event("login_success", None)
                return

            # Попытка 2 (fallback): если был прокси и сессия валидна — пробуем
            # без прокси (прокси мог умереть, а Telegram доступен напрямую).
            if proxy_url and session_str:
                self._client_mgr.use_proxy("")
                self._client_mgr.use_session(session_str)
                result2 = self._auth.check_session()
                if result2.step == AuthStep.SUCCESS:
                    self._worker.put_event("login_success", None)
                    return
                # Не вышло и без прокси — сообщаем, но не блокируем вход.
                self._worker.put_event(
                    "login_error",
                    "Автовход не удался (прокси недоступен?). Войдите заново или проверьте прокси.",
                )
            # Без прокси — остаёмся на экране входа (сессия устарела/нет).
        except Exception as exc:
            logger.error("check_session (автовход) failed", exc=exc)
            self._worker.put_event("login_error", "Автовход не удался. Войдите заново.")

    def _bg_send_code(self, phone: str) -> None:
        result = self._auth.send_code(phone)
        if result.step == AuthStep.SUCCESS:
            self._worker.put_event("login_success", None)
        elif result.step == AuthStep.CODE_SENT:
            self._worker.put_event("code_sent", None)
        else:
            self._worker.put_event("login_error", result.error or "Ошибка")

    def _bg_verify_code(self, code: str, password: str) -> None:
        result = self._auth.verify_code(code, password)
        if result.step == AuthStep.SUCCESS:
            self._worker.put_event("login_success", None)
        elif result.step == AuthStep.PASSWORD_REQUIRED:
            self._worker.put_event("login_2fa", None)
        else:
            self._worker.put_event("login_error", result.error or "Ошибка")

    def _bg_qr_login(self) -> None:
        """Фоновый цикл QR-входа: показать QR → поллить до входа/2FA/истечения."""
        try:
            # Прокси для входа (как и для входа по номеру) — до создания профиля.
            self._client_mgr.use_proxy(self._pending_proxy)
            url = self._auth.start_qr()
            self._worker.put_event("qr_ready", url)
            self._qr_poll_loop()
        except QrNeedsPassword:
            # 2FA-аккаунт, токен уже отсканирован → сразу поле пароля, без poll.
            self._qr_active = False
            self._worker.put_event("qr_2fa", None)
        except Exception as exc:
            logger.error("qr_login failed", exc=exc)
            self._qr_active = False
            self._worker.put_event("qr_error", str(exc))

    def _bg_qr_refresh(self) -> None:
        """Пересоздаёт истёкший QR и продолжает поллинг."""
        try:
            url = self._auth.recreate_qr()
            self._worker.put_event("qr_ready", url)
            self._qr_poll_loop()
        except QrNeedsPassword:
            self._qr_active = False
            self._worker.put_event("qr_2fa", None)
        except Exception as exc:
            logger.error("qr_refresh failed", exc=exc)
            self._qr_active = False
            self._worker.put_event("qr_error", str(exc))

    def _qr_poll_loop(self) -> None:
        """Поллит poll_qr, пока активен QR-режим. Эмитит события по результату."""
        while self._qr_active:
            result = self._auth.poll_qr(timeout=5)
            # Пользователь мог уйти с QR-режима, пока мы блокировались в wait(5).
            # Не эмитим устаревшие события (включая ложную навигацию на чаты).
            if not self._qr_active:
                return
            if result.step == AuthStep.SUCCESS:
                self._qr_active = False
                self._worker.put_event("login_success", None)
                return
            if result.step == AuthStep.PASSWORD_REQUIRED:
                self._qr_active = False
                self._worker.put_event("qr_2fa", None)
                return
            if result.step == AuthStep.EXPIRED:
                self._qr_active = False
                self._worker.put_event("qr_expired", None)
                return
            if result.step == AuthStep.ERROR:
                self._qr_active = False
                self._worker.put_event("qr_error", result.error or "Ошибка QR-входа")
                return
            # WAITING → продолжаем цикл

    def _bg_qr_password(self, password: str) -> None:
        """Пароль 2FA после QR-сканирования. Неверный пароль — даём повторить."""
        result = self._auth.verify_qr_password(password)
        if result.step == AuthStep.SUCCESS:
            self._worker.put_event("login_success", None)
        elif result.step == AuthStep.PASSWORD_REQUIRED:
            # Неверный/пустой пароль — остаёмся в 2FA, разрешаем повторить.
            self._worker.put_event("qr_2fa_retry", "Неверный пароль 2FA. Попробуйте ещё раз.")
        else:
            self._worker.put_event("qr_error", result.error or "Ошибка входа")

    def _bg_logout(self) -> None:
        self._qr_active = False
        self._auth.logout()
        if self.config.api_id:
            self.credentials.delete_session(self.config.api_id)
        self._worker.put_event("logout_done", None)

    def _bg_switch_profile(self, profile: Profile) -> None:
        """Переключает активную сессию клиента на профиль (в фоне)."""
        try:
            self._client_mgr.disconnect()
            session_str = self._profiles.load_session(profile)
            if not session_str:
                self._worker.put_event("error", f"Сессия профиля {profile.phone} не найдена. Войдите заново.")
                return
            self._profiles.set_active(profile.phone)
            # Убеждаемся, что api_id клиента соответствует профилю.
            if profile.api_id and profile.api_id != self.config.api_id:
                self.config = self.config.with_api_id(profile.api_id)
                self.config.save()
                self._client_mgr.update_config(self.config)
            self._client_mgr.use_proxy(self._profiles.load_proxy(profile))
            self._client_mgr.use_session(session_str)
            client = self._client_mgr.ensure_connected()
            if not client.is_user_authorized():
                self._worker.put_event("error", f"Сессия {profile.phone} устарела. Удалите профиль и войдите заново.")
                return
            self._worker.put_event("profile_switched", profile)
        except Exception as exc:
            logger.error("switch_profile failed", exc=exc)
            self._worker.put_event("error", f"Не удалось переключиться: {exc}")

    def _bg_test_proxy(self, modal, proxy_url: str) -> None:
        """Проверка прокси через временный клиент. Не трогает активную сессию."""
        try:
            ok, message = self._client_mgr.test_proxy(proxy_url)
        except Exception as exc:
            ok, message = False, f"Ошибка проверки: {exc}"
        self._worker.put_event("proxy_test_result", (modal, (ok, message)))

    def _bg_load_chats(self) -> None:
        try:
            c = self._client_mgr.ensure_connected()
            dialogs = c.get_dialogs()
            self._all_dialogs = dialogs
            self._worker.put_event("chats_loaded", dialogs)
            # Папки
            try:
                filters = c(functions.messages.GetDialogFiltersRequest())
                if hasattr(filters, "filters"):
                    filters = filters.filters
            except Exception:
                filters = []
            self._process_filters(filters or [])
        except Exception as exc:
            # На неудаче — пробрасываем error (модалка) И отдельным событием
            # просим UI восстановить статус, иначе на рефреше «Обновление
            # списка чатов...» останется висеть навсегда.
            logger.error("load_chats failed", exc=exc)
            self._worker.put_event("chats_load_failed", None)
            self._worker.put_event("error", str(exc))

    def _process_filters(self, filters) -> None:
        from telethon.utils import get_peer_id
        folder_peers: dict = {}
        folder_filters: dict = {}
        folder_excludes: dict = {}
        names: list[str] = []
        for f in filters:
            title = _normalize(getattr(f, "title", None))
            if not title:
                continue
            include_peers = getattr(f, "include_peers", None) or []
            pinned_peers = getattr(f, "pinned_peers", None) or []
            exclude_peers = getattr(f, "exclude_peers", None) or []
            peer_ids: set[int] = set()
            for p in list(include_peers) + list(pinned_peers):
                try:
                    peer_ids.add(get_peer_id(p))
                except Exception:
                    pass
            exclude_ids: set[int] = set()
            for p in exclude_peers:
                try:
                    exclude_ids.add(get_peer_id(p))
                except Exception:
                    pass
            has_flags = any(getattr(f, a, False) for a in ("contacts", "non_contacts", "groups", "broadcasts", "bots"))
            if peer_ids or has_flags or exclude_ids:
                folder_peers[title] = peer_ids
                folder_filters[title] = f
                folder_excludes[title] = exclude_ids
                names.append(title)
        self._folder_peers = folder_peers
        self._folder_filters = folder_filters
        self._folder_excludes = folder_excludes
        self._worker.put_event("folders_loaded", names)

    # ---- Folder export ----

    def _export_next_in_folder(self) -> None:
        if self._folder_index >= len(self._folder_queue):
            self._folder_active = False
            self._worker.put_event("folder_done", len(self._folder_queue))
            return
        if self._token.is_cancelled:
            self._folder_active = False
            self._worker.put_event("export_cancelled", None)
            return
        dialog = self._folder_queue[self._folder_index]
        self._folder_index += 1
        name = getattr(dialog, "name", "Чат") or "Чат"
        self._worker.put_event("folder_progress", (self._folder_index, len(self._folder_queue), name))
        self._token = CancellationToken()
        progress = ExportProgress()
        deepgram_key = self.credentials.load_deepgram_key()
        orch = ExportOrchestrator(self._client_mgr, self.config, self._history, deepgram_key)

        flat = self._folder_mode in ("Один .md на чат", "Один .md на папку")
        date_from, date_to = self._folder_dates
        task = ExportTask(
            chat_id=getattr(dialog, "id", 0),
            chat_name=name,
            output_path=self._folder_export_base,
            format=ExportFormat.MARKDOWN if flat else ExportFormat.BOTH,
            transcribe_audio=self._folder_transcribe,
            date_from=date_from,
            date_to=date_to,
        )
        token = self._token
        self._worker.submit(
            orch.run, dialog, task, token, progress,
            lambda etype, payload: self._worker.put_event(etype, payload),
        )

    # ---- Event registration ----

    def _register_handlers(self) -> None:
        d = self._dispatcher
        d.on("login_success",    self._on_login_success)
        d.on("code_sent",        lambda _: self.login_view.show_code_input())
        d.on("login_error",      lambda msg: self.login_view.set_error(msg or "Ошибка"))
        d.on("login_2fa",        lambda _: self.login_view.show_code_input())
        d.on("qr_ready",         lambda url: self.login_view.on_qr_ready(url))
        d.on("qr_2fa",           lambda _: self.login_view.on_qr_2fa())
        d.on("qr_2fa_retry",     lambda msg: self.login_view.on_qr_2fa_retry(msg or "Неверный пароль 2FA"))
        d.on("qr_expired",       lambda _: self.login_view.on_qr_expired())
        d.on("qr_error",         lambda msg: self.login_view.on_qr_error(msg or "Ошибка QR"))
        d.on("logout_done",      lambda _: self.show_login())
        d.on("profile_switched", self._on_profile_switched)

        d.on("add_account_code_sent", self._on_add_account_code_sent)
        d.on("add_account_2fa",       self._on_add_account_2fa)
        d.on("add_account_done",      self._on_add_account_done)
        d.on("add_account_error",     self._on_add_account_error)
        d.on("add_account_qr_ready",   self._on_add_account_qr_ready)
        d.on("add_account_qr_2fa",     self._on_add_account_qr_2fa)
        d.on("add_account_qr_expired", self._on_add_account_qr_expired)
        d.on("add_account_qr_error",   self._on_add_account_qr_error)
        d.on("proxy_test_result",     self._on_proxy_test_result)

        d.on("topics_loaded",      self._on_topics_loaded)
        d.on("topics_load_failed", self._on_topics_load_failed)
        d.on("topic_progress",     self._on_topic_progress)
        d.on("topic_done",         self._on_topic_done)

        d.on("chats_loaded",     self._on_chats_loaded)
        d.on("chats_load_failed", self._on_chats_load_failed)
        d.on("folders_loaded",   lambda names: self.chats_page.set_folders(names))
        d.on("error",            self._on_error)
        d.on("info",             self._on_info)
        d.on("worker_error",     lambda tb: logger.error(f"Worker error:\n{tb}"))

        d.on("export_start",     self._on_export_start)
        d.on("export_progress",  self._on_export_progress)
        d.on("export_status",    self._on_export_status)
        d.on("model_download_progress", self._on_model_download_progress)
        d.on("export_done",      self._on_export_done)
        d.on("export_error",     self._on_export_error)
        d.on("export_cancelled", self._on_export_cancelled)

        d.on("folder_progress",  self._on_folder_progress)
        d.on("folder_done",      self._on_folder_done)

    # ---- Event handlers ----

    def _on_login_success(self, _payload) -> None:
        # Сохраняем успешный логин в профили (миграция + новые аккаунты)
        self._worker.submit(self.save_active_profile_session)
        self.show_chats()

    def _on_add_account_code_sent(self, payload) -> None:
        modal, _ = payload
        try:
            modal.on_code_sent()
        except Exception:
            pass

    def _on_add_account_2fa(self, payload) -> None:
        modal, _ = payload
        try:
            modal.on_2fa_required()
        except Exception:
            pass

    def _on_add_account_done(self, payload) -> None:
        modal, phone = payload
        try:
            modal.on_done(phone)
        except Exception:
            pass
        profile = self._profiles.get(phone)
        if profile is not None:
            self._worker.submit(self._bg_switch_profile, profile)

    def _on_add_account_error(self, payload) -> None:
        modal, message = payload
        try:
            modal.on_error(message or "Ошибка")
        except Exception:
            pass

    def _on_add_account_qr_ready(self, payload) -> None:
        modal, url = payload
        try:
            modal.on_qr_ready(url)
        except Exception:
            pass

    def _on_add_account_qr_2fa(self, payload) -> None:
        modal, data = payload
        try:
            # data == "wrong" → повторная попытка после неверного пароля.
            modal.on_qr_2fa(retry=(data == "wrong"))
        except Exception:
            pass

    def _on_add_account_qr_expired(self, payload) -> None:
        modal, _ = payload
        try:
            modal.on_qr_expired()
        except Exception:
            pass

    def _on_add_account_qr_error(self, payload) -> None:
        modal, message = payload
        try:
            modal.on_qr_error(message or "Ошибка QR")
        except Exception:
            pass

    def _on_proxy_test_result(self, payload) -> None:
        modal, (ok, message) = payload
        try:
            modal.on_test_result(ok, message)
        except Exception:
            pass

    def _on_topics_loaded(self, payload) -> None:
        modal, topics = payload
        try:
            modal.set_topics(topics)
        except Exception:
            pass

    def _on_topics_load_failed(self, payload) -> None:
        modal, message = payload
        try:
            modal.set_topics_error(message or "Не удалось загрузить топики")
        except Exception:
            pass

    def _on_topic_progress(self, payload) -> None:
        current, total, title = payload
        if self._active_export_modal:
            self._active_export_modal.on_topic_progress(current, total, title)

    def _on_topic_done(self, payload) -> None:
        export_dir, ok, failed = payload
        if self._active_export_modal:
            self._active_export_modal.on_topic_batch_done(export_dir, ok, failed)

    def _on_profile_switched(self, profile: Profile) -> None:
        # Обновляем список чатов под новый аккаунт + карточки аккаунтов
        # (пометка «активный»), затем показываем страницу «Чаты».
        self._all_dialogs = []
        self._folder_peers = {}
        self._folder_filters = {}
        self._folder_excludes = {}
        self.accounts_page.refresh()
        self.show_chats()

    def _on_chats_loaded(self, dialogs) -> None:
        self._all_dialogs = dialogs
        self.filter_chats(
            self.chats_page._search_entry.get().strip()
            if hasattr(self.chats_page, "_search_entry") else ""
        )

    def _on_chats_load_failed(self, _payload) -> None:
        # Возвращаем статус «Чатов: N» вместо застрявшего «Обновление...».
        # Если ничего не было загружено раньше — пусто.
        if self._all_dialogs:
            self.chats_page.set_status(f"Чатов: {len(self._all_dialogs)}")
        else:
            self.chats_page.set_status("Не удалось загрузить чаты")

    def _on_error(self, msg: str) -> None:
        import tkinter.messagebox as mb
        if "database is locked" in (msg or "").lower():
            mb.showerror("Ошибка", "База сессии занята.\nЗакройте все копии приложения.")
        else:
            mb.showerror("Ошибка", msg)

    def _on_info(self, msg: str) -> None:
        import tkinter.messagebox as mb
        mb.showinfo("Информация", msg)

    def _on_export_start(self, payload) -> None:
        chat_name, total = payload
        if self._active_export_modal:
            self._active_export_modal.on_export_start(chat_name, total)

    def _on_export_progress(self, payload) -> None:
        count, total = payload
        if self._active_export_modal:
            self._active_export_modal.on_export_progress(count, total)

    def _on_export_status(self, text: str) -> None:
        if self._active_export_modal:
            self._active_export_modal.on_export_status(text or "")

    def _on_model_download_progress(self, payload) -> None:
        ratio, text = payload
        if self._active_export_modal:
            self._active_export_modal.on_model_download_progress(ratio, text)

    def _on_export_done(self, payload) -> None:
        import os, shutil
        export_dir, files = payload
        # Топик-пакет: ведём к следующему топику, финал покажет topic_done.
        if self._topic_active:
            self._topic_queue.record(True)
            self._export_next_topic()
            return
        if self._active_export_modal:
            self._active_export_modal.on_export_done(export_dir, files)
        if self._folder_active:
            chat_name = getattr(self._folder_queue[self._folder_index - 1], "name", "chat") or "chat"
            if self._folder_mode in ("Один .md на чат", "Один .md на папку") and self._folder_export_base:
                from ..exporters.base import sanitize_filename
                safe_name = sanitize_filename(chat_name)
                for f in files:
                    if f.endswith(".md") and os.path.exists(f):
                        dest = os.path.join(self._folder_export_base, f"{safe_name}.md")
                        if os.path.exists(dest):
                            base_, ext_ = os.path.splitext(dest)
                            dest = f"{base_}_2{ext_}"
                        try:
                            shutil.move(f, dest)
                        except Exception:
                            pass
                try:
                    if os.path.isdir(export_dir):
                        shutil.rmtree(export_dir, ignore_errors=True)
                except Exception:
                    pass
            self._folder_log.append(f"OK: {chat_name}")
            self._export_next_in_folder()

    def _on_export_error(self, msg: str) -> None:
        if self._topic_active:
            self._topic_queue.record(False)
            self._export_next_topic()
            return
        if self._active_export_modal:
            self._active_export_modal.on_export_error(msg)
        if self._folder_active:
            self._folder_log.append(f"ERR: {getattr(self._folder_queue[self._folder_index - 1], 'name', '?')}")
            self._export_next_in_folder()

    def _on_export_cancelled(self, _) -> None:
        if self._active_export_modal:
            self._active_export_modal.on_export_cancelled()
        self._folder_active = False
        self._topic_active = False
        self._topic_queue = None

    def _on_folder_progress(self, payload) -> None:
        current, total, label = payload
        self.chats_page.set_status(f"Папка: {current}/{total} — {label}")

    def _on_folder_done(self, total: int) -> None:
        import os, glob as _glob
        ok = sum(1 for l in self._folder_log if l.startswith("OK"))
        err = total - ok

        if self._folder_mode == "Один .md на папку" and self._folder_export_base:
            # Объединяем все .md в один файл
            md_files = sorted(_glob.glob(os.path.join(self._folder_export_base, "*.md")))
            if md_files:
                merged_path = os.path.join(self._folder_export_base, "_все_чаты.md")
                try:
                    with open(merged_path, "w", encoding="utf-8") as out:
                        for md_path in md_files:
                            chat_title = os.path.splitext(os.path.basename(md_path))[0]
                            out.write(f"# {chat_title}\n\n")
                            with open(md_path, encoding="utf-8") as inp:
                                out.write(inp.read())
                            out.write("\n\n---\n\n")
                    # Удаляем отдельные файлы, оставляем только merged
                    for md_path in md_files:
                        try:
                            os.remove(md_path)
                        except Exception:
                            pass
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Merge error: {e}")

        msg = f"Папка готова: {ok} успешно"
        if err:
            msg += f", {err} ошибок"
        self.chats_page.set_status(msg)

    # ---- Polling ----

    def _poll(self) -> None:
        for event_type, payload in self._worker.poll_events():
            self._dispatcher.dispatch(event_type, payload)
        self.after(80, self._poll)

    # ---- Folder dialogs helper ----

    def _get_folder_dialogs(self, folder_name: str) -> list:
        if not folder_name or folder_name == "Все чаты":
            return list(self._all_dialogs)
        peers = self._folder_peers.get(folder_name)
        f_obj = self._folder_filters.get(folder_name)
        excludes = self._folder_excludes.get(folder_name, set())
        dialogs = []
        for d in self._all_dialogs:
            try:
                pid = get_peer_id(d.entity)
            except Exception:
                pid = d.id
            if pid in excludes:
                continue
            if peers is not None and pid in peers:
                dialogs.append(d)
                continue
            if f_obj is not None:
                flags = any(getattr(f_obj, a, False) for a in ("contacts", "non_contacts", "groups", "broadcasts", "bots"))
                if flags:
                    dialogs.append(d)
        return dialogs

    # ---- Migration ----

    def _migrate_legacy_config(self) -> None:
        """
        Переносит api_hash / session из старого config.json в Keyring.
        ВАЖНО: секреты стираются из файла ТОЛЬКО если миграция прошла успешно.
        """
        import json
        legacy_path = os.path.expanduser("~/.tg_exporter/config.json")
        try:
            if not os.path.exists(legacy_path):
                return
            with open(legacy_path, "r") as f:
                raw = json.load(f)
            api_id = (raw.get("api_id") or "").strip()
            api_hash = raw.get("api_hash") or ""
            session = raw.get("session") or ""
            if not (api_hash or session):
                return
            if not api_id:
                # Нет api_id — мигрировать некуда, но плэйнтекст-секреты оставляем
                return
            ok = self.credentials.migrate_from_plaintext(
                api_id, api_hash or None, session or None
            )
            if not ok:
                # Keyring недоступен — НЕ стираем плэйнтекст, чтобы не потерять авторизацию
                return
            raw.pop("api_hash", None)
            raw.pop("session", None)
            # Атомарная запись: tmp + os.replace
            tmp_path = legacy_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(raw, f, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, legacy_path)
        except Exception:
            pass

    # ---- Close ----

    def _on_close(self) -> None:
        self.cancel_export()
        self._worker.shutdown(timeout=1.0)
        self.destroy()


# ---- Helpers ----

def _normalize(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if hasattr(value, "text"):
        return str(value.text)
    return str(value)


def _update_config(config: AppConfig, **kwargs) -> AppConfig:
    import dataclasses
    return dataclasses.replace(config, **kwargs)
