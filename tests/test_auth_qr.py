"""
Tests for AuthService QR-login methods (start_qr / poll_qr).

Telethon QRLogin замокан — сеть не трогаем. Проверяем ветки poll_qr:
SUCCESS / WAITING / PASSWORD_REQUIRED / EXPIRED.
"""

from __future__ import annotations

import datetime
import unittest


class _FakeQR:
    """
    Замена telethon QRLogin. ВАЖНО: wait/recreate — КОРУТИНЫ (async), как в
    реальном telethon (они НЕ синкифицированы). Это ловит баг, когда poll_qr
    забывает run_until_complete и получает всегда-truthy корутину.
    """
    def __init__(self, url="tg://login?token=abc", expires=None, wait_behavior=None):
        self.url = url
        self.expires = expires
        self._wait_behavior = wait_behavior  # значение-результат или Exception
        self.recreated = False

    async def wait(self, timeout=None):
        b = self._wait_behavior
        if isinstance(b, BaseException):
            raise b
        return b  # True (вошёл) / False / None

    async def recreate(self):
        self.recreated = True
        self.url = "tg://login?token=NEW"


class _FakeLoop:
    """Минимальный loop: реально выполняет корутину (как client.loop)."""
    def run_until_complete(self, coro):
        import asyncio
        return asyncio.new_event_loop().run_until_complete(coro)


class _FakeClient:
    def __init__(self, qr, qr_login_errors=0, qr_login_raises=None, sign_in_raises=None):
        self._qr = qr
        self.loop = _FakeLoop()
        self._qr_login_errors = qr_login_errors  # сколько раз бросить AuthRestartError
        self._qr_login_raises = qr_login_raises   # исключение из qr_login (напр. 2FA)
        self._sign_in_raises = sign_in_raises     # исключение из sign_in (напр. неверный пароль)
        self.qr_login_calls = 0
        self.sign_in_calls = 0

    def qr_login(self):
        self.qr_login_calls += 1
        if self._qr_login_errors > 0:
            self._qr_login_errors -= 1
            from telethon.errors import AuthRestartError
            raise AuthRestartError(request=None)
        if self._qr_login_raises is not None:
            exc = self._qr_login_raises
            self._qr_login_raises = None  # бросаем один раз
            raise exc
        return self._qr

    def sign_in(self, password=None):
        self.sign_in_calls += 1
        if self._sign_in_raises is not None:
            exc = self._sign_in_raises
            self._sign_in_raises = None  # один раз (повтор пройдёт)
            raise exc
        return object()  # User


class _FakeClientMgr:
    def __init__(self, client):
        self._client = client

    def ensure_connected(self):
        return self._client

    def get_client(self):
        return self._client

    def save_session(self):
        pass


def _future(seconds=60):
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)


def _past(seconds=60):
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)


class TestAuthQR(unittest.TestCase):
    def setUp(self):
        from tg_exporter.core.auth import AuthService, AuthStep
        self.AuthService = AuthService
        self.AuthStep = AuthStep

    def _service(self, qr, qr_login_errors=0, qr_login_raises=None, sign_in_raises=None):
        client = _FakeClient(qr, qr_login_errors, qr_login_raises, sign_in_raises)
        svc = self.AuthService(_FakeClientMgr(client))
        svc._fake_client = client  # доступ к счётчикам в тестах
        return svc

    def test_start_qr_returns_url(self):
        qr = _FakeQR(url="tg://login?token=XYZ", expires=_future())
        svc = self._service(qr)
        url = svc.start_qr()
        self.assertEqual(url, "tg://login?token=XYZ")

    def test_start_qr_retries_on_auth_restart(self):
        # Telegram бросает AuthRestartError на первый qr_login → ретраим.
        qr = _FakeQR(url="tg://login?token=AFTER_RETRY", expires=_future())
        svc = self._service(qr, qr_login_errors=1)
        url = svc.start_qr()
        self.assertEqual(url, "tg://login?token=AFTER_RETRY")

    def test_start_qr_gives_up_after_3_auth_restart(self):
        from telethon.errors import AuthRestartError
        qr = _FakeQR(expires=_future())
        svc = self._service(qr, qr_login_errors=5)  # всегда падает
        with self.assertRaises(AuthRestartError):
            svc.start_qr()

    def test_recreate_qr_awaits_and_returns_new_url(self):
        # recreate() должен реально выполниться (корутина), url обновиться.
        qr = _FakeQR(url="tg://login?token=OLD", expires=_future())
        svc = self._service(qr)
        svc.start_qr()
        new_url = svc.recreate_qr()
        self.assertTrue(qr.recreated, "recreate() не была выполнена")
        self.assertEqual(new_url, "tg://login?token=NEW")

    def test_poll_success(self):
        qr = _FakeQR(expires=_future(), wait_behavior=True)  # вошёл
        svc = self._service(qr)
        svc.start_qr()
        res = svc.poll_qr(timeout=1)
        self.assertEqual(res.step, self.AuthStep.SUCCESS)

    def test_poll_waiting(self):
        # wait вернул без логина, токен ещё жив → WAITING
        qr = _FakeQR(expires=_future(), wait_behavior=False)
        svc = self._service(qr)
        svc.start_qr()
        res = svc.poll_qr(timeout=1)
        self.assertEqual(res.step, self.AuthStep.WAITING)

    def test_poll_expired(self):
        # wait вернул без логина, токен протух → EXPIRED
        qr = _FakeQR(expires=_past(), wait_behavior=False)
        svc = self._service(qr)
        svc.start_qr()
        res = svc.poll_qr(timeout=1)
        self.assertEqual(res.step, self.AuthStep.EXPIRED)

    def test_poll_2fa(self):
        from telethon.errors import SessionPasswordNeededError
        # SessionPasswordNeededError требует аргумент request — передаём заглушку.
        exc = SessionPasswordNeededError(request=None)
        qr = _FakeQR(expires=_future(), wait_behavior=exc)
        svc = self._service(qr)
        svc.start_qr()
        res = svc.poll_qr(timeout=1)
        self.assertEqual(res.step, self.AuthStep.PASSWORD_REQUIRED)

    def test_poll_timeout_treated_as_waiting(self):
        # wait бросил asyncio.TimeoutError (никто не отсканировал за timeout) —
        # это нормальный «ждём дальше», токен жив → WAITING.
        import asyncio
        qr = _FakeQR(expires=_future(), wait_behavior=asyncio.TimeoutError())
        svc = self._service(qr)
        svc.start_qr()
        res = svc.poll_qr(timeout=1)
        self.assertEqual(res.step, self.AuthStep.WAITING)

    def test_poll_without_start_errors(self):
        qr = _FakeQR()
        svc = self._service(qr)
        res = svc.poll_qr(timeout=1)
        self.assertEqual(res.step, self.AuthStep.ERROR)

    # --- QR + 2FA edge cases (баги из реального лога) ---

    def test_start_qr_2fa_raises_needs_password(self):
        # 2FA-аккаунт, токен уже отсканирован: qr_login() бросает
        # SessionPasswordNeededError → start_qr должен поднять QrNeedsPassword
        # (это не ошибка, а «покажи поле пароля»).
        from telethon.errors import SessionPasswordNeededError
        from tg_exporter.core.auth import QrNeedsPassword
        qr = _FakeQR()
        svc = self._service(qr, qr_login_raises=SessionPasswordNeededError(request=None))
        with self.assertRaises(QrNeedsPassword):
            svc.start_qr()

    def test_verify_qr_password_success(self):
        qr = _FakeQR(expires=_future())
        svc = self._service(qr)
        svc.start_qr()
        res = svc.verify_qr_password("correct")
        self.assertEqual(res.step, self.AuthStep.SUCCESS)

    def test_verify_qr_password_wrong_is_retryable(self):
        # Неверный пароль 2FA — НЕ терминальная ошибка: остаёмся в 2FA (повтор).
        from telethon.errors import PasswordHashInvalidError
        qr = _FakeQR(expires=_future())
        svc = self._service(qr, sign_in_raises=PasswordHashInvalidError(request=None))
        svc.start_qr()
        res = svc.verify_qr_password("wrong")
        self.assertEqual(res.step, self.AuthStep.PASSWORD_REQUIRED)
        # повтор с верным паролем (sign_in_raises бросает один раз) → успех
        res2 = svc.verify_qr_password("correct")
        self.assertEqual(res2.step, self.AuthStep.SUCCESS)

    def test_verify_qr_password_empty(self):
        qr = _FakeQR(expires=_future())
        svc = self._service(qr)
        svc.start_qr()
        res = svc.verify_qr_password("")
        self.assertEqual(res.step, self.AuthStep.ERROR)

    def test_cancel_qr_resets_state(self):
        # cancel_qr сбрасывает _qr/_qr_pwd_pending и ФОРСИРУЕТ свежую сессию
        # (новый auth_key) — иначе Telegram держит привязанный 2FA-логин и
        # снова просит пароль вместо нового QR.
        qr = _FakeQR(expires=_future())
        svc = self._service(qr)
        svc.start_qr()
        svc._qr_pwd_pending = True  # имитируем застрявший 2FA
        called = {"fresh": False}
        svc._client.use_fresh_session = lambda: called.__setitem__("fresh", True)
        svc.cancel_qr()
        self.assertIsNone(svc._qr)
        self.assertFalse(svc._qr_pwd_pending)
        self.assertTrue(called["fresh"], "cancel_qr должен форсировать свежую сессию")

    def test_recreate_qr_on_connection_error_starts_fresh(self):
        # recreate на «мёртвом» QR → ConnectionError → берём свежий токен с нуля.
        class _DeadQR(_FakeQR):
            async def recreate(self):
                raise ConnectionError("Cannot send requests while disconnected")
        dead = _DeadQR(url="tg://login?token=OLD", expires=_future())
        # после старта _qr=dead; recreate упадёт → start_qr вернёт новый qr.url
        fresh = _FakeQR(url="tg://login?token=FRESH", expires=_future())
        svc = self._service(fresh)
        svc._qr = dead  # имитируем уже запущенный (мёртвый) QR
        url = svc.recreate_qr()
        self.assertEqual(url, "tg://login?token=FRESH")


if __name__ == "__main__":
    unittest.main()
