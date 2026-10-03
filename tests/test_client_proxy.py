"""
Tests for TelegramClientManager proxy integration (use_proxy + _build_client).

Проверяем, что строка прокси активного профиля доходит до конструктора
TelegramClient в правильном виде, а невалидная строка не роняет вход
(клиент строится без прокси).
"""

from __future__ import annotations

import unittest
import warnings


class _FakeCreds:
    """Лёгкая замена CredentialsManager: отдаёт api_hash/session без Keyring."""

    def __init__(self, api_hash="0123456789abcdef0123456789abcdef", session=None):
        self._api_hash = api_hash
        self._session = session

    def load_api_hash(self, api_id):
        return self._api_hash

    def load_session(self, api_id):
        return self._session


def _make_manager(proxy=None):
    from tg_exporter.core.client import TelegramClientManager
    from tg_exporter.models.config import AppConfig

    config = AppConfig(api_id="12345")
    mgr = TelegramClientManager(config, _FakeCreds())
    if proxy is not None:
        mgr.use_proxy(proxy)
    return mgr


class TestUseProxy(unittest.TestCase):

    def test_no_proxy_builds_client_without_proxy(self):
        mgr = _make_manager()
        client = mgr._build_client()
        self.assertIsNone(client._proxy)

    def test_socks5_proxy_reaches_client(self):
        mgr = _make_manager("socks5://user:pass@1.2.3.4:1080")
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # «proxy ignored» стало бы ошибкой
            client = mgr._build_client()
        self.assertEqual(client._proxy["proxy_type"], "socks5")
        self.assertEqual(client._proxy["addr"], "1.2.3.4")
        self.assertEqual(client._proxy["port"], 1080)
        self.assertEqual(client._proxy["username"], "user")

    def test_http_proxy_reaches_client(self):
        mgr = _make_manager("http://1.2.3.4:8080")
        client = mgr._build_client()
        self.assertEqual(client._proxy["proxy_type"], "http")

    def test_mtproto_proxy_sets_connection(self):
        from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
        mgr = _make_manager("mtproto://1.2.3.4:443?secret=00112233445566778899aabbccddeeff")
        client = mgr._build_client()
        self.assertEqual(client._proxy, ("1.2.3.4", 443, "00112233445566778899aabbccddeeff"))
        self.assertTrue(issubclass(client._connection, ConnectionTcpMTProxyRandomizedIntermediate)
                        or client._connection is ConnectionTcpMTProxyRandomizedIntermediate)

    def test_invalid_proxy_does_not_crash_builds_without_proxy(self):
        # Опечатка в прокси не должна блокировать вход — строим клиент без прокси.
        mgr = _make_manager("ftp://garbage")
        client = mgr._build_client()
        self.assertIsNone(client._proxy)

    def test_use_proxy_none_clears(self):
        mgr = _make_manager("socks5://1.2.3.4:1080")
        mgr.use_proxy(None)
        client = mgr._build_client()
        self.assertIsNone(client._proxy)

    def test_use_proxy_empty_string_clears(self):
        mgr = _make_manager("socks5://1.2.3.4:1080")
        mgr.use_proxy("")
        client = mgr._build_client()
        self.assertIsNone(client._proxy)


class TestProxyConnectionTest(unittest.TestCase):
    """test_proxy() — проверка соединения для кнопки «Тест связи» в UI."""

    def test_invalid_proxy_returns_false_without_network(self):
        mgr = _make_manager()
        ok, msg = mgr.test_proxy("ftp://garbage")
        self.assertFalse(ok)
        self.assertTrue(msg)  # есть понятное сообщение

    def test_empty_proxy_returns_false(self):
        mgr = _make_manager()
        ok, msg = mgr.test_proxy("")
        self.assertFalse(ok)

    def test_valid_proxy_connects_via_temp_client(self):
        # Подменяем _connect_test_client фейком, чтобы не ходить в сеть:
        # проверяем, что валидная строка доходит до попытки коннекта и
        # успешный коннект → (True, ...).
        mgr = _make_manager()

        called = {}

        def fake_connect(proxy_cfg):
            called["kind"] = proxy_cfg.kind
            return True, "OK"

        mgr._connect_test_client = fake_connect  # type: ignore[assignment]
        ok, msg = mgr.test_proxy("socks5://1.2.3.4:1080")
        self.assertTrue(ok)
        self.assertEqual(called["kind"], "socks5")

    def test_connect_failure_returns_false(self):
        mgr = _make_manager()

        def fake_connect(proxy_cfg):
            return False, "Не удалось подключиться"

        mgr._connect_test_client = fake_connect  # type: ignore[assignment]
        ok, msg = mgr.test_proxy("socks5://1.2.3.4:1080")
        self.assertFalse(ok)
        self.assertIn("подключиться", msg)


class TestFreshSession(unittest.TestCase):
    """use_fresh_session — чистый auth_key для перезапуска QR (игнор Keyring)."""

    def test_fresh_session_ignores_keyring(self):
        from tg_exporter.core.client import TelegramClientManager
        from tg_exporter.models.config import AppConfig
        # creds с НЕпустой сохранённой сессией — fresh должен её проигнорировать
        creds = _FakeCreds(session="OLD_SESSION_SHOULD_BE_IGNORED")
        mgr = TelegramClientManager(AppConfig(api_id="12345"), creds)
        mgr.use_fresh_session()
        client = mgr._build_client()
        saved = client.session.save()
        self.assertNotEqual(saved, "OLD_SESSION_SHOULD_BE_IGNORED")

    def test_normal_session_still_used_after_fresh(self):
        # use_session перекрывает FRESH (другие потоки логина не ломаются)
        from tg_exporter.core.client import TelegramClientManager
        from tg_exporter.models.config import AppConfig
        creds = _FakeCreds(session=None)
        mgr = TelegramClientManager(AppConfig(api_id="12345"), creds)
        mgr.use_fresh_session()
        mgr.use_session("REAL_SESSION")  # перекрывает sentinel
        self.assertEqual(mgr._session_override, "REAL_SESSION")


class TestPinSession(unittest.TestCase):
    """pin_session: клиент после входа помнит свою сессию при пересоздании."""

    def test_pin_replaces_fresh_marker_without_dropping_client(self):
        from tg_exporter.core.client import FRESH_SESSION
        mgr = _make_manager()
        mgr.use_fresh_session()
        client = mgr.get_client()
        self.assertEqual(mgr._session_override, FRESH_SESSION)

        mgr.pin_session(client.session.save() or "pinned")

        self.assertNotEqual(mgr._session_override, FRESH_SESSION)
        self.assertIs(mgr.get_client(), client)  # живой клиент не пересоздан


if __name__ == "__main__":
    unittest.main()
