"""
TelegramClientManager — управление жизненным циклом TelegramClient.

Отвечает за:
- Создание клиента из credentials
- Переподключение при обрыве
- Один asyncio event loop на фоновый поток

Не содержит UI-логики и не знает про очереди.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

from .credentials import CredentialsManager
from ..models.config import AppConfig
from ..utils.logger import logger


# Маркер «форсировать брэнд-новую пустую сессию» для _session_override.
# Не может совпасть с реальной StringSession (та — base64 без NUL-префикса).
FRESH_SESSION = "\x00__fresh__"


class ClientNotConfiguredError(RuntimeError):
    """api_id или api_hash не заданы."""


class TelegramClientManager:
    """
    Держит один экземпляр TelegramClient на всё время жизни приложения.

    Создаётся один раз при старте App. Используется из фонового потока.
    Все методы Telethon должны вызываться из потока, где живёт event loop.
    """

    def __init__(self, config: AppConfig, credentials: CredentialsManager) -> None:
        self._config = config
        self._credentials = credentials
        self._client: Optional[TelegramClient] = None
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Явно заданная сессия (для профилей). Если None — берётся из Keyring
        # по `{api_id}:session`, как раньше.
        self._session_override: Optional[str] = None
        # Прокси активного профиля (URL-строка). None — без прокси.
        self._proxy_override: Optional[str] = None

    def update_config(self, config: AppConfig) -> None:
        """Обновляет конфиг. Если api_id изменился — сбрасывает клиент."""
        with self._lock:
            if self._config.api_id != config.api_id:
                self._destroy_client()
            self._config = config

    def use_session(self, session_string: Optional[str]) -> None:
        """
        Принудительно указывает сессию для клиента (для профилей).
        None — вернуться к дефолтной сессии из Keyring.
        Сбрасывает текущий клиент, чтобы следующий get_client() построил новый.
        """
        with self._lock:
            self._session_override = session_string or None
            self._destroy_client()

    def use_fresh_session(self) -> None:
        """
        Форсирует БРЭНД-НОВУЮ пустую сессию (новый MTProto auth_key) для
        следующего клиента. Нужно для перезапуска QR-входа: на старом auth_key
        Telegram держит привязанный недоделанный 2FA-логин и сразу отвечает
        SESSION_PASSWORD_NEEDED вместо свежего QR. use_session(None) НЕ годится —
        _build_client фолбэчит на load_session(); нужен явный пустой сеанс.
        НЕ logout — keyring-сессии не трогаются.
        """
        with self._lock:
            self._session_override = FRESH_SESSION
            self._destroy_client()

    def pin_session(self, session_string: str) -> None:
        """
        Запоминает сессию ЖИВОГО клиента, не пересоздавая его. Нужно после
        входа по коду/QR: он идёт на чистой сессии (use_fresh_session), и без
        этого любое пересоздание клиента (например, смена прокси) построило бы
        новый пустой ключ — приложение оказалось бы разлогинено.
        """
        with self._lock:
            self._session_override = session_string or None

    def use_proxy(self, proxy: Optional[str]) -> None:
        """
        Указывает прокси активного профиля (URL-строка) для клиента.
        None / "" — без прокси. Сбрасывает клиент, чтобы прокси применился
        при следующем get_client() (прокси задаётся при создании клиента).
        """
        with self._lock:
            self._proxy_override = proxy or None
            self._destroy_client()

    # ---- Event loop ----

    def ensure_event_loop(self) -> asyncio.AbstractEventLoop:
        """Гарантирует наличие event loop в текущем потоке."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
            return loop
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            return loop

    # ---- Client lifecycle ----

    def get_client(self) -> TelegramClient:
        """
        Возвращает готовый TelegramClient.
        Создаёт новый если ещё не создан.
        Raises ClientNotConfiguredError если api_id/api_hash не заданы.
        """
        self.ensure_event_loop()
        with self._lock:
            if self._client is None:
                self._client = self._build_client()
        return self._client

    def ensure_connected(self) -> TelegramClient:
        """Возвращает клиент, гарантируя что он подключён."""
        client = self.get_client()
        if not client.is_connected():
            client.connect()
        return client

    def disconnect(self) -> None:
        """Отключает клиент. Безопасен для вызова если клиент не создан."""
        with self._lock:
            if self._client is not None:
                try:
                    self._client.disconnect()
                except Exception:
                    pass

    def destroy(self) -> None:
        """Отключает и удаляет клиент. Следующий get_client() создаст новый."""
        with self._lock:
            self._destroy_client()

    def save_session(self) -> None:
        """Сохраняет текущую сессию в Keyring."""
        with self._lock:
            if self._client is None:
                return
            try:
                session_str = self._client.session.save()
                if session_str and self._config.api_id:
                    self._credentials.save_session(self._config.api_id, session_str)
            except Exception:
                pass

    @property
    def is_created(self) -> bool:
        return self._client is not None

    # ---- Internal ----

    def _build_client(self) -> TelegramClient:
        api_id = self._config.api_id_int
        if not api_id:
            raise ClientNotConfiguredError(
                "api_id не задан. Откройте настройки и введите API ID."
            )

        api_hash = self._credentials.load_api_hash(self._config.api_id)
        if not api_hash:
            raise ClientNotConfiguredError(
                "api_hash не найден. Откройте настройки и введите API Hash."
            )

        if self._session_override == FRESH_SESSION:
            # Чистый auth_key — без обращения к Keyring (перезапуск QR-входа).
            session = StringSession()
        else:
            session_str = self._session_override or self._credentials.load_session(self._config.api_id)
            session = StringSession(session_str) if session_str else StringSession()

        kwargs = self._proxy_kwargs()
        # Ограничиваем коннект: мёртвый/медленный прокси не должен вешать
        # worker-поток на минуты (telethon по умолчанию connection_retries=5).
        return TelegramClient(
            session, api_id, api_hash,
            timeout=10, connection_retries=1, retry_delay=0,
            **kwargs,
        )

    def test_proxy(self, proxy: str) -> tuple[bool, str]:
        """
        Проверяет работоспособность прокси: строит временный клиент и пытается
        подключиться к Telegram (без логина). Для кнопки «Тест связи» в UI.

        Возвращает (ok, message). Не трогает основной клиент/сессию.
        """
        from .proxy import parse_proxy, ProxyValidationError
        try:
            cfg = parse_proxy(proxy)
        except ProxyValidationError as exc:
            return False, str(exc)
        if cfg is None:
            return False, "Прокси не задан."
        return self._connect_test_client(cfg)

    def _connect_test_client(self, cfg) -> tuple[bool, str]:
        """Сетевой коннект временного клиента через прокси. Выделено для тестируемости."""
        api_id = self._config.api_id_int
        api_hash = self._credentials.load_api_hash(self._config.api_id)
        if not api_id or not api_hash:
            return False, "Сначала заполните API ID и API Hash в настройках."

        self.ensure_event_loop()
        tele = cfg.to_telethon()
        kwargs = {"proxy": tele["proxy"]}
        if tele["connection"] is not None:
            kwargs["connection"] = tele["connection"]

        client = TelegramClient(StringSession(), api_id, api_hash, timeout=10, **kwargs)
        try:
            client.connect()
            if client.is_connected():
                return True, f"Соединение через {cfg.describe()} установлено."
            return False, "Не удалось подключиться через прокси."
        except Exception as exc:
            # Текст исключения от telethon/python-socks может содержать
            # host/username/password из proxy-кортежа — НЕ логируем и НЕ
            # показываем его целиком. Описываем прокси через describe() (без
            # пароля/секрета) + только тип ошибки.
            logger.warning(f"client: тест прокси {cfg.describe()} не прошёл: {type(exc).__name__}")
            return False, f"Не удалось подключиться через {cfg.describe()}. Проверьте адрес, порт и доступность прокси."
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def _proxy_kwargs(self) -> dict:
        """
        Строит kwargs (proxy=/connection=) для TelegramClient из строки прокси
        активного профиля. Невалидная строка не блокирует вход — логируется,
        клиент строится без прокси.
        """
        if not self._proxy_override:
            return {}
        from .proxy import parse_proxy, ProxyValidationError
        try:
            cfg = parse_proxy(self._proxy_override)
        except ProxyValidationError as exc:
            logger.warning(f"client: некорректный прокси, игнорирую: {exc}")
            return {}
        if cfg is None:
            return {}
        tele = cfg.to_telethon()
        kwargs = {"proxy": tele["proxy"]}
        if tele["connection"] is not None:
            kwargs["connection"] = tele["connection"]
        logger.info(f"client: используется прокси {cfg.describe()}")
        return kwargs

    def _destroy_client(self) -> None:
        """Вызывать только под self._lock."""
        if self._client is not None:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
