"""
AuthService — логика аутентификации в Telegram.

Полностью отделён от UI. Принимает callback для уведомлений о результате.
Все методы выполняются в фоновом потоке (там где живёт TelegramClient).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneNumberInvalidError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PasswordHashInvalidError,
    FloodWaitError,
    ApiIdInvalidError,
    AuthKeyInvalidError,
    AuthKeyUnregisteredError,
    SendCodeUnavailableError,
    AuthRestartError,
)

from .client import TelegramClientManager
from ..utils.logger import logger


class QrNeedsPassword(Exception):
    """
    Внутренний сигнал: QR-токен уже отсканирован на 2FA-аккаунте, Telegram
    требует пароль СРАЗУ (из qr_login/recreate, до wait). Это НЕ ошибка —
    нужно показать поле пароля 2FA, а не сообщение об ошибке.
    """


class AuthStep(Enum):
    CODE_SENT = auto()          # код отправлен
    PASSWORD_REQUIRED = auto()  # нужен пароль 2FA
    SUCCESS = auto()            # авторизован
    ERROR = auto()
    WAITING = auto()            # QR ещё не отсканирован (продолжаем поллинг)
    EXPIRED = auto()            # QR-токен истёк (нужно обновить)


@dataclass
class AuthResult:
    step: AuthStep
    error: Optional[str] = None

    @classmethod
    def ok(cls) -> "AuthResult":
        return cls(step=AuthStep.SUCCESS)

    @classmethod
    def code_sent(cls) -> "AuthResult":
        return cls(step=AuthStep.CODE_SENT)

    @classmethod
    def password_required(cls) -> "AuthResult":
        return cls(step=AuthStep.PASSWORD_REQUIRED)

    @classmethod
    def failure(cls, msg: str) -> "AuthResult":
        return cls(step=AuthStep.ERROR, error=msg)

    @classmethod
    def waiting(cls) -> "AuthResult":
        return cls(step=AuthStep.WAITING)

    @classmethod
    def expired(cls) -> "AuthResult":
        return cls(step=AuthStep.EXPIRED)


class AuthService:
    """
    Оркестратор процесса входа в Telegram.

    Состояние сессии (phone_hash, phone_number) хранится здесь,
    не в App и не в UI.
    """

    def __init__(self, client_manager: TelegramClientManager) -> None:
        self._client = client_manager
        self._phone_number: Optional[str] = None
        self._phone_hash: Optional[str] = None
        self._qr = None  # активный QRLogin (telethon) при входе по QR
        self._qr_pwd_pending = False  # 2FA-пароль ожидается (QR уже отсканирован)

    # ---- Public API ----

    def check_session(self) -> AuthResult:
        """
        Проверяет текущую сессию. Если авторизован — сохраняет и возвращает SUCCESS.
        Вызывать при старте приложения.
        """
        try:
            c = self._client.ensure_connected()
            if c.is_user_authorized():
                self._client.save_session()
                return AuthResult.ok()
            return AuthResult.failure("Требуется вход")
        except (AuthKeyInvalidError, AuthKeyUnregisteredError):
            return AuthResult.failure("Сессия устарела. Войдите заново.")
        except ApiIdInvalidError:
            return AuthResult.failure("Неверный API ID или API Hash. Проверьте настройки.")
        except Exception as exc:
            logger.error("check_session failed", exc=exc)
            return AuthResult.failure(_friendly(exc))

    def send_code(self, phone: str) -> AuthResult:
        """
        Отправляет код подтверждения на номер телефона.
        Запоминает phone_hash для последующего verify_code().
        """
        phone = (phone or "").strip()
        if not phone:
            return AuthResult.failure("Введите номер телефона.")
        try:
            c = self._client.ensure_connected()
            if c.is_user_authorized():
                self._client.save_session()
                return AuthResult.ok()
            sent = c.send_code_request(phone)
            self._phone_number = phone
            self._phone_hash = sent.phone_code_hash
            return AuthResult.code_sent()
        except PhoneNumberInvalidError:
            return AuthResult.failure("Неверный номер телефона.")
        except PhoneNumberBannedError:
            return AuthResult.failure("Этот номер заблокирован в Telegram.")
        except PhoneNumberFloodError:
            return AuthResult.failure("Слишком много попыток. Попробуйте позже.")
        except SendCodeUnavailableError:
            return AuthResult.failure("Не удалось отправить код. Попробуйте другой способ.")
        except FloodWaitError as exc:
            return AuthResult.failure(f"Слишком много запросов. Подождите {exc.seconds} сек.")
        except ApiIdInvalidError:
            return AuthResult.failure("Неверный API ID или API Hash. Проверьте настройки.")
        except Exception as exc:
            logger.error("send_code failed", exc=exc)
            return AuthResult.failure(_friendly(exc))

    def verify_code(self, code: str, password: str = "") -> AuthResult:
        """
        Верифицирует код из Telegram.
        Если включена 2FA и код верен — автоматически пробует password.
        """
        code = (code or "").strip()
        if not code:
            return AuthResult.failure("Введите код из Telegram.")
        if not self._phone_hash:
            return AuthResult.failure("Сначала нажмите «Получить код».")
        phone = self._phone_number
        if not phone:
            return AuthResult.failure("Введите номер телефона.")
        try:
            c = self._client.ensure_connected()
            c.sign_in(phone=phone, code=code, phone_code_hash=self._phone_hash)
            self._client.save_session()
            return AuthResult.ok()
        except SessionPasswordNeededError:
            if (password or "").strip():
                return self.verify_password(password)
            return AuthResult.password_required()
        except PhoneCodeInvalidError:
            return AuthResult.failure("Неверный код. Проверьте и попробуйте снова.")
        except PhoneCodeExpiredError:
            return AuthResult.failure("Код устарел. Запросите новый код.")
        except FloodWaitError as exc:
            return AuthResult.failure(f"Слишком много попыток. Подождите {exc.seconds} сек.")
        except Exception as exc:
            logger.error("verify_code failed", exc=exc)
            return AuthResult.failure(_friendly(exc))

    def verify_password(self, password: str) -> AuthResult:
        """Верифицирует пароль двухфакторной аутентификации."""
        password = (password or "").strip()
        if not password:
            return AuthResult.failure("Нужен пароль 2FA.")
        try:
            c = self._client.ensure_connected()
            c.sign_in(password=password)
            self._client.save_session()
            return AuthResult.ok()
        except PasswordHashInvalidError:
            return AuthResult.failure("Неверный пароль двухфакторной аутентификации.")
        except FloodWaitError as exc:
            return AuthResult.failure(f"Слишком много попыток. Подождите {exc.seconds} сек.")
        except Exception as exc:
            logger.error("verify_password failed", exc=exc)
            return AuthResult.failure(_friendly(exc))

    def verify_qr_password(self, password: str) -> AuthResult:
        """
        Пароль 2FA в QR-режиме. Отличие от verify_password: неверный пароль —
        НЕ терминальная ошибка, а PASSWORD_REQUIRED (остаёмся в 2FA, разрешаем
        повторить без перезапуска QR). ensure_connected — сокет мог упасть
        после 2FA-ошибки.
        """
        password = (password or "").strip()
        if not password:
            return AuthResult.failure("Нужен пароль 2FA.")
        try:
            c = self._client.ensure_connected()
            c.sign_in(password=password)
            self._client.save_session()
            self._qr = None
            self._qr_pwd_pending = False
            return AuthResult.ok()
        except PasswordHashInvalidError:
            # Остаёмся в 2FA — пользователь повторяет, без нового QR.
            self._qr_pwd_pending = True
            return AuthResult.password_required()
        except SessionPasswordNeededError:
            self._qr_pwd_pending = True
            return AuthResult.password_required()
        except FloodWaitError as exc:
            return AuthResult.failure(f"Слишком много попыток. Подождите {exc.seconds} сек.")
        except Exception as exc:
            logger.error("verify_qr_password failed", exc=exc)
            return AuthResult.failure(_friendly(exc))

    # ---- QR-вход ----

    def cancel_qr(self) -> None:
        """
        Сбрасывает незавершённый QR-вход (в т.ч. зависший на 2FA с неверным
        паролем). Пересоздаёт клиент начисто, чтобы вход другим способом
        (по номеру / другой QR) шёл с чистого листа. Сессию НЕ трогает —
        это не logout, аккаунт не разлогинивается.
        """
        self._qr = None
        self._qr_pwd_pending = False
        try:
            # НЕ просто destroy(): следующий _build_client восстановил бы ТОТ ЖЕ
            # auth_key, к которому Telegram привязал недоделанный 2FA-логин →
            # снова SESSION_PASSWORD_NEEDED без свежего QR. Форсируем чистый
            # auth_key (пустую сессию). Keyring-сессии не трогаем.
            self._client.use_fresh_session()
        except Exception:
            pass

    def start_qr(self) -> str:
        """
        Запускает QR-логин: создаёт QRLogin и возвращает его URL (содержимое
        для отрисовки в QR-код). Дальше — poll_qr() в цикле.

        Telegram при первом ExportLoginTokenRequest иногда отвечает
        AuthRestartError — по протоколу нужно перезапросить токен (qr_login
        заново). Делаем до 3 попыток.
        """
        c = self._client.ensure_connected()
        self._qr_pwd_pending = False
        last_exc = None
        for _ in range(3):
            try:
                self._qr = c.qr_login()
                return self._qr.url
            except AuthRestartError as exc:
                last_exc = exc
                continue
            except SessionPasswordNeededError:
                # qr_login() внутри шлёт ExportLoginTokenRequest; на 2FA-аккаунте
                # с уже отсканированным токеном Telegram сразу требует пароль.
                # Это НЕ ошибка — сигналим «нужен пароль 2FA».
                self._qr_pwd_pending = True
                raise QrNeedsPassword()
        # Если все попытки дали AuthRestartError — пробрасываем (обработает caller).
        raise last_exc if last_exc else RuntimeError("qr_login failed")

    def recreate_qr(self) -> str:
        """Пересоздаёт истёкший QR-токен (кнопка «Обновить»). Возвращает новый URL."""
        c = self._client.ensure_connected()
        if self._qr is None:
            return self.start_qr()
        # Обновление кода отменяет недоделанный 2FA.
        self._qr_pwd_pending = False
        # QRLogin.recreate — async-метод, НЕ синкифицирован telethon.sync.
        # Гоним корутину на loop клиента, иначе .url вернёт старый токен.
        try:
            c.loop.run_until_complete(self._qr.recreate())
            return self._qr.url
        except AuthRestartError:
            # Перезапрос токена с нуля.
            self._qr = None
            return self.start_qr()
        except SessionPasswordNeededError:
            # recreate тоже шлёт ExportLoginTokenRequest → на отсканированном
            # 2FA-аккаунте даёт пароль.
            self._qr_pwd_pending = True
            raise QrNeedsPassword()
        except ConnectionError:
            # Стейл QRLogin на мёртвом sender (после 2FA-ошибки клиент отвалился)
            # → берём свежий токен с нуля.
            self._qr = None
            return self.start_qr()

    def poll_qr(self, timeout: float = 5) -> AuthResult:
        """
        Один шаг ожидания сканирования QR. Вызывать в цикле из фонового потока.

        Возвращает:
          SUCCESS           — вошёл (сессия сохранена),
          PASSWORD_REQUIRED — включён 2FA (нужен пароль),
          EXPIRED           — токен истёк (предложить «Обновить»),
          WAITING           — ещё не отсканировали (продолжать поллинг),
          ERROR             — старт не выполнен / иная ошибка.
        """
        import asyncio

        if self._qr is None:
            return AuthResult.failure("QR-вход не запущен.")
        try:
            # QRLogin.wait — async-метод, НЕ синкифицирован telethon.sync
            # (в отличие от TelegramClient). БЕЗ run_until_complete вернётся
            # корутина (всегда truthy) → ложный SUCCESS без сканирования.
            # На успех wait() возвращает User; на таймаут бросает
            # asyncio.TimeoutError; при 2FA — SessionPasswordNeededError.
            c = self._client.ensure_connected()
            result = c.loop.run_until_complete(self._qr.wait(timeout=timeout))
            if result:
                self._client.save_session()
                self._qr = None
                return AuthResult.ok()
            # На практике сюда не попадаем (wait либо вернёт User, либо бросит),
            # но на всякий случай — проверка срока токена.
            return self._waiting_or_expired()
        except SessionPasswordNeededError:
            # 2FA: дальше пароль через verify_qr_password().
            logger.info("poll_qr: 2FA требуется (SessionPasswordNeededError)")
            self._qr_pwd_pending = True
            return AuthResult.password_required()
        except AuthRestartError:
            # Telegram просит перезапустить QR-токен → как «истёк»: пользователь
            # нажмёт «Обновить» (recreate_qr пересоздаст токен).
            return AuthResult.expired()
        except ConnectionError:
            # Соединение прервалось во время ожидания — не терминально, на след.
            # шаге ensure_connected() переподключит.
            logger.info("poll_qr: соединение прервано, переподключимся на след. шаге")
            return AuthResult.waiting()
        except asyncio.TimeoutError:
            # Никто не отсканировал за timeout — нормально, ждём дальше.
            return self._waiting_or_expired()
        except FloodWaitError as exc:
            return AuthResult.failure(f"Слишком много запросов. Подождите {exc.seconds} сек.")
        except Exception as exc:
            # Диагностика: telethon QRLogin.wait при 2FA может бросать не
            # SessionPasswordNeededError, а TypeError ('Login token response was
            # unexpected') — ловим этот случай и трактуем как 2FA.
            name = type(exc).__name__
            msg = str(exc)
            logger.error(f"poll_qr exception: {name}: {msg}")
            if "SESSION_PASSWORD_NEEDED" in msg or "password" in msg.lower():
                return AuthResult.password_required()
            return AuthResult.failure(_friendly(exc))

    def _waiting_or_expired(self) -> AuthResult:
        """WAITING, пока токен жив; EXPIRED, когда истёк (по qr.expires)."""
        import datetime
        expires = getattr(self._qr, "expires", None)
        if expires is not None:
            now = datetime.datetime.now(datetime.timezone.utc)
            # qr.expires — aware datetime в UTC. Сравниваем безопасно.
            try:
                if now >= expires:
                    return AuthResult.expired()
            except TypeError:
                # naive datetime — приводим к aware
                if now.replace(tzinfo=None) >= expires:
                    return AuthResult.expired()
        return AuthResult.waiting()

    def logout(self) -> None:

        """Выходит из аккаунта и уничтожает клиент."""
        try:
            c = self._client.get_client()
            c.log_out()
        except Exception:
            pass
        finally:
            self._client.destroy()
            self._qr = None
            self._qr_pwd_pending = False
            self._phone_number = None
            self._phone_hash = None


# ---- Helpers ----

def _friendly(exc: Exception) -> str:
    """Переводит необработанные исключения Telethon в читаемый русский текст."""
    msg = str(exc)
    if "The password" in msg and "is invalid" in msg:
        return "Неверный пароль двухфакторной аутентификации."
    if "Two-steps verification" in msg or "PASSWORD_HASH_INVALID" in msg:
        return "Неверный пароль двухфакторной аутентификации."
    if "PHONE_CODE_INVALID" in msg:
        return "Неверный код. Проверьте и попробуйте снова."
    if "PHONE_CODE_EXPIRED" in msg:
        return "Код устарел. Запросите новый код."
    if "PHONE_NUMBER_INVALID" in msg:
        return "Неверный номер телефона."
    if "PHONE_NUMBER_BANNED" in msg:
        return "Этот номер заблокирован в Telegram."
    if "API_ID_INVALID" in msg or "api_id" in msg.lower():
        return "Неверный API ID или API Hash. Проверьте настройки."
    if "AUTH_KEY_INVALID" in msg or "AUTH_KEY_UNREGISTERED" in msg:
        return "Сессия недействительна. Войдите заново."
    if "FLOOD_WAIT" in msg:
        return "Слишком много запросов. Подождите немного."
    if "network" in msg.lower() or "connect" in msg.lower():
        return "Ошибка соединения. Проверьте интернет."
    if "ResendCodeRequest" in msg or "options for this type" in msg:
        return "Все способы отправки кода исчерпаны. Попробуйте позже."
    return msg
