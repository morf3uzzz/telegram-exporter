"""
Tests for AccountSessions — привязка «профиль ↔ сохранённая сессия ↔ живой клиент».

Регрессия бага мульти-аккаунтов: после «Выход» + входа под другим номером
новый вход шёл на ТОМ ЖЕ auth-ключе, строка сессии совпадала с сохранённой у
старого профиля, и оба профиля открывали один аккаунт. Симптомы: «переключил
аккаунт — чаты прежние», «после перезапуска активен прошлый аккаунт»,
«удалённый аккаунт возвращается».

Telegram подменён фейковым сервером, который (как настоящий) помнит только
«auth-ключ → пользователь»: строка сессии сама не знает, чей это аккаунт.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, key: str, value: str) -> None:
        self.store[(service, key)] = value

    def get_password(self, service: str, key: str):
        return self.store.get((service, key))

    def delete_password(self, service: str, key: str) -> None:
        self.store.pop((service, key), None)


class _FakeTelegram:
    """Сервер: auth-ключ → пользователь (None — ключ жив, но не авторизован)."""

    def __init__(self) -> None:
        self.keys: dict[str, object] = {}
        self.offline = False
        self.dead_proxies: set[str] = set()
        self.direct_blocked = False          # без прокси Telegram недоступен
        self.duplicated_keys: set[str] = set()  # AUTH_KEY_DUPLICATED
        self._seq = 0

    def new_key(self) -> str:
        self._seq += 1
        key = f"key-{self._seq}"
        self.keys[key] = None
        return key

    def user(self, user_id: int, phone: str, first_name: str = "User"):
        return SimpleNamespace(id=user_id, phone=phone, first_name=first_name, last_name="")


class _FakeClient:
    def __init__(self, server: _FakeTelegram, key: str) -> None:
        self._server = server
        self._key = key
        self.session = SimpleNamespace(save=lambda: self._key)

    def get_me(self):
        if self._key in self._server.duplicated_keys:
            from telethon.errors import AuthKeyDuplicatedError
            raise AuthKeyDuplicatedError(request=None)
        # Telethon: get_me() → None, если ключ не авторизован.
        return self._server.keys.get(self._key)

    def log_out(self):
        # auth.logOut отвязывает пользователя, но ключ остаётся рабочим —
        # именно поэтому повторный вход на нём опасен.
        self._server.keys[self._key] = None
        return True

    def sign_in_as(self, user) -> None:
        """Тест-хелпер: интерактивный вход (код/QR) на этом клиенте."""
        self._server.keys[self._key] = user


_FRESH = object()


class _FakeClientManager:
    """Повторяет контракт TelegramClientManager, нужный AccountSessions."""

    def __init__(self, server: _FakeTelegram) -> None:
        self._server = server
        self._session = _FRESH
        self.proxy = None
        self._client = None

    def use_session(self, session_string) -> None:
        self._session = session_string or _FRESH
        self._client = None

    def use_fresh_session(self) -> None:
        self._session = _FRESH
        self._client = None

    def use_proxy(self, proxy) -> None:
        self.proxy = proxy or None
        self._client = None

    def pin_session(self, session_string) -> None:
        # Как в TelegramClientManager: запоминает сессию, живой клиент не трогает.
        self._session = session_string or _FRESH

    def get_client(self) -> _FakeClient:
        if self._client is None:
            key = self._server.new_key() if self._session is _FRESH else self._session
            self._client = _FakeClient(self._server, key)
        return self._client

    def ensure_connected(self) -> _FakeClient:
        if (
            self._server.offline
            or (self.proxy and self.proxy in self._server.dead_proxies)
            or (self._server.direct_blocked and not self.proxy)
        ):
            raise ConnectionError("Connection to Telegram failed")
        return self.get_client()

    def destroy(self) -> None:
        self._client = None

    def live_user(self):
        return self.get_client().get_me()


class _Base(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        self.kr = _FakeKeyring()
        sys.modules["keyring"] = self.kr  # type: ignore[assignment]
        self.addCleanup(lambda: sys.modules.pop("keyring", None))

        from tg_exporter.core import profiles as profiles_mod
        from tg_exporter.core import credentials as credentials_mod
        from tg_exporter.core.credentials import CredentialsManager
        from tg_exporter.core.profiles import ProfileManager
        from tg_exporter.core.account_session import AccountSessions, Outcome

        orig_path = profiles_mod._PROFILES_FILE
        profiles_mod._PROFILES_FILE = Path(self._tmp.name) / "profiles.json"
        self.addCleanup(lambda: setattr(profiles_mod, "_PROFILES_FILE", orig_path))

        # CredentialsManager держит ссылку на модуль keyring с момента импорта.
        orig_kr = credentials_mod.keyring
        orig_avail = credentials_mod._KEYRING_AVAILABLE
        credentials_mod.keyring = self.kr
        credentials_mod._KEYRING_AVAILABLE = True
        self.addCleanup(lambda: setattr(credentials_mod, "keyring", orig_kr))
        self.addCleanup(lambda: setattr(credentials_mod, "_KEYRING_AVAILABLE", orig_avail))

        self.Outcome = Outcome
        self.ProfileManager = ProfileManager
        self.creds = CredentialsManager()
        self.creds._require_keyring = lambda: True  # type: ignore[method-assign]
        self.pm = ProfileManager(self.creds)
        self.tg = _FakeTelegram()
        self.mgr = _FakeClientManager(self.tg)
        self.api_id = "42"
        self.accounts = AccountSessions(
            self.mgr, self.pm, self.creds,
            get_api_id=lambda: self.api_id,
            set_api_id=lambda v: setattr(self, "api_id", v),
        )

        self.alice = self.tg.user(1, "71111111111", "Alice")
        self.bob = self.tg.user(2, "72222222222", "Bob")
        self.carol = self.tg.user(3, "73333333333", "Carol")

    # ---- helpers ----

    def _login(self, user):
        """Интерактивный вход на живом клиенте + сохранение профиля (как в App)."""
        self.mgr.get_client().sign_in_as(user)
        return self.accounts.remember_login()

    def _add_via_modal(self, user):
        """«+ Добавить аккаунт»: отдельный клиент с чистой сессией."""
        key = self.tg.new_key()
        self.tg.keys[key] = user
        return self.pm.add_or_update(
            phone="+" + user.phone, api_id=self.api_id, session_string=key,
            display_name=user.first_name, set_active=False, user_id=user.id,
        )

    def _restart(self):
        """Новый запуск приложения: память чистая, диск/Keyring те же."""
        from tg_exporter.core.account_session import AccountSessions
        self.pm = self.ProfileManager(self.creds)
        self.mgr = _FakeClientManager(self.tg)
        self.accounts = AccountSessions(
            self.mgr, self.pm, self.creds,
            get_api_id=lambda: self.api_id,
            set_api_id=lambda v: setattr(self, "api_id", v),
        )
        return self.accounts.restore()

    def _phones(self):
        return sorted(p.phone for p in self.pm.list())

    def _session_of(self, phone):
        return self.pm.load_session(self.pm.get(phone))


class TestSwitch(_Base):

    def test_switch_changes_live_account(self):
        self._login(self.alice)
        bob = self._add_via_modal(self.bob)

        res = self.accounts.activate(bob)

        self.assertTrue(res.ok)
        self.assertEqual(self.mgr.live_user().id, self.bob.id)
        self.assertEqual(self.pm.active_phone(), "+72222222222")

    def test_switch_survives_restart(self):
        self._login(self.alice)
        self.accounts.activate(self._add_via_modal(self.bob))

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertEqual(self.pm.active_phone(), "+72222222222")
        self.assertEqual(self.mgr.live_user().id, self.bob.id)

    def test_switch_to_foreign_session_is_refused_and_rolled_back(self):
        # Профиль Alice хранит строку, которая на деле авторизует Bob («двойник»).
        self._login(self.bob)
        bob_session = self._session_of("+72222222222")
        self.pm.add_or_update(
            phone="+71111111111", api_id=self.api_id, session_string="tmp",
            display_name="Alice", set_active=False,
        )
        self.kr.store[("tg_exporter", "42:session:+71111111111")] = bob_session

        res = self.accounts.activate(self.pm.get("+71111111111"))

        self.assertFalse(res.ok)
        self.assertIs(res.outcome, self.Outcome.FOREIGN)
        # Активным остался настоящий аккаунт, клиент — он же.
        self.assertEqual(self.pm.active_phone(), "+72222222222")
        self.assertEqual(self.mgr.live_user().id, self.bob.id)
        # «Двойник» обезврежен: сессия снята, у владельца — на месте.
        self.assertIsNone(self._session_of("+71111111111"))
        self.assertEqual(self._session_of("+72222222222"), bob_session)

    def test_switch_to_revoked_session_drops_it_and_rolls_back(self):
        self._login(self.alice)
        bob = self._add_via_modal(self.bob)
        self.tg.keys[self._session_of("+72222222222")] = None  # отозвана с другого устройства

        res = self.accounts.activate(bob)

        self.assertIs(res.outcome, self.Outcome.UNAUTHORIZED)
        self.assertEqual(self.pm.active_phone(), "+71111111111")
        self.assertEqual(self.mgr.live_user().id, self.alice.id)
        self.assertIsNone(self._session_of("+72222222222"))
        self.assertEqual(len(self.pm.list()), 2)  # профиль остался — «нужен вход»

    def test_switch_network_failure_keeps_session_and_active(self):
        self._login(self.alice)
        bob = self._add_via_modal(self.bob)
        bob_session = self._session_of("+72222222222")
        self.tg.offline = True

        res = self.accounts.activate(bob)

        self.assertIs(res.outcome, self.Outcome.FAILED)
        self.assertEqual(self.pm.active_phone(), "+71111111111")
        self.assertEqual(self._session_of("+72222222222"), bob_session)
        self.tg.offline = False
        self.assertEqual(self.mgr.live_user().id, self.alice.id)

    def test_switch_without_stored_session(self):
        self._login(self.alice)
        bob = self._add_via_modal(self.bob)
        self.pm.drop_session(bob.phone)

        res = self.accounts.activate(bob)

        self.assertIs(res.outcome, self.Outcome.NO_SESSION)
        self.assertEqual(self.pm.active_phone(), "+71111111111")
        self.assertEqual(self.mgr.live_user().id, self.alice.id)

    def test_failed_switch_restores_api_id(self):
        self._login(self.alice)
        key = self.tg.new_key()
        self.tg.keys[key] = self.bob
        self.pm.add_or_update(
            phone="+72222222222", api_id="77", session_string=key, set_active=False,
        )
        self.tg.offline = True

        self.accounts.activate(self.pm.get("+72222222222"))

        self.assertEqual(self.api_id, "42")

    def test_switch_backfills_user_id(self):
        self._login(self.alice)
        key = self.tg.new_key()
        self.tg.keys[key] = self.bob
        # Профиль из старой версии: user_id ещё не записан.
        self.pm.add_or_update(
            phone="+72222222222", api_id=self.api_id, session_string=key, set_active=False,
        )

        self.accounts.activate(self.pm.get("+72222222222"))

        self.assertEqual(self.pm.get("+72222222222").user_id, self.bob.id)


class TestLogoutThenLoginAnotherAccount(_Base):
    """Первопричина: вход под другим аккаунтом после «Выход»."""

    def test_new_login_never_reuses_a_stored_auth_key(self):
        self._login(self.alice)
        alice_key = self._session_of("+71111111111")
        self._add_via_modal(self.carol)

        self.accounts.sign_out_active(lambda: self.mgr.get_client().log_out())
        self.accounts.begin_phone_login("+72222222222")
        self._login(self.bob)

        bob_key = self._session_of("+72222222222")
        self.assertNotEqual(bob_key, alice_key)
        self.assertNotEqual(bob_key, self._session_of("+73333333333"))
        self.assertEqual(self.mgr.live_user().id, self.bob.id)

    def test_login_after_failed_autologin_does_not_alias_profiles(self):
        # Старое состояние: «Выход» не убрал профиль; сессия Alice мертва, но
        # лежит в Keyring и после неудачного автовхода остаётся в клиенте.
        self._login(self.alice)
        alice_key = self._session_of("+71111111111")
        self.tg.keys[alice_key] = None
        self._restart()

        self.accounts.begin_phone_login("+72222222222")
        self._login(self.bob)

        self.assertNotEqual(self._session_of("+72222222222"), alice_key)
        self.assertIsNone(self._session_of("+71111111111"))
        # Переключиться «на Alice» и получить чаты Bob больше нельзя.
        res = self.accounts.activate(self.pm.get("+71111111111"))
        self.assertFalse(res.ok)
        self.assertEqual(self.mgr.live_user().id, self.bob.id)

    def test_qr_login_starts_on_fresh_key(self):
        # Автовход упал по сети → в клиенте осталась ЖИВАЯ сессия Alice.
        # Сканирование QR другим аккаунтом не должно перепривязать её ключ.
        self._login(self.alice)
        alice_key = self._session_of("+71111111111")
        self.tg.offline = True
        self._restart()
        self.tg.offline = False

        self.accounts.begin_qr_login()
        self._login(self.bob)

        self.assertNotEqual(self._session_of("+72222222222"), alice_key)
        self.assertEqual(self._session_of("+71111111111"), alice_key)
        self.assertIs(self.tg.keys[alice_key], self.alice)
        self.assertTrue(self.accounts.activate(self.pm.get("+71111111111")).ok)
        self.assertEqual(self.mgr.live_user().id, self.alice.id)

    def test_typed_phone_of_saved_account_restores_without_code(self):
        self._login(self.alice)
        self.tg.offline = True
        self.assertIs(self._restart().outcome, self.Outcome.FAILED)
        self.tg.offline = False

        res = self.accounts.begin_phone_login("+7 111 111-11-11")

        self.assertIsNotNone(res)
        self.assertTrue(res.ok)
        self.assertEqual(self.mgr.live_user().id, self.alice.id)

    def test_typed_phone_of_other_account_does_not_enter_stored_one(self):
        # Автовход не удался (нет сети), пользователь вводит ДРУГОЙ номер —
        # нельзя молча войти в сохранённый аккаунт.
        self._login(self.alice)
        self.tg.offline = True
        self._restart()
        self.tg.offline = False

        res = self.accounts.begin_phone_login("+72222222222")

        self.assertIsNone(res)
        self.assertIsNone(self.mgr.live_user())  # чистый ключ, никто не вошёл


class TestAfterInteractiveLogin(_Base):

    def test_client_rebuild_after_login_keeps_the_account(self):
        # Сохранение прокси активного аккаунта пересоздаёт клиент (use_proxy).
        # После входа по коду клиент не должен «забывать» сессию и строиться
        # на новом пустом ключе.
        self.accounts.begin_phone_login("+71111111111")
        self._login(self.alice)

        self.mgr.use_proxy("")

        self.assertEqual(self.mgr.live_user().id, self.alice.id)

    def test_client_rebuild_after_qr_login_keeps_the_account(self):
        self.accounts.begin_qr_login()
        self._login(self.alice)

        self.mgr.use_proxy("")

        self.assertEqual(self.mgr.live_user().id, self.alice.id)

    def test_client_rebuild_after_legacy_migration_keeps_the_account(self):
        key = self.tg.new_key()
        self.tg.keys[key] = self.alice
        self.kr.store[("tg_exporter", "42:session")] = key
        self._restart()

        self.mgr.use_proxy("")

        self.assertEqual(self.mgr.live_user().id, self.alice.id)


class TestLoginScreenProxy(_Base):
    """РФ без VPN: сохранённый прокси умер, новый введён на экране входа."""

    def _saved_account_behind_dead_proxy(self):
        self._login(self.alice)
        self.pm.set_proxy("+71111111111", "socks5://old:1080")
        self.tg.dead_proxies.add("socks5://old:1080")
        self.tg.direct_blocked = True
        self.assertIs(self._restart().outcome, self.Outcome.FAILED)

    def test_typed_phone_restores_through_login_screen_proxy(self):
        self._saved_account_behind_dead_proxy()

        res = self.accounts.begin_phone_login("+71111111111", proxy="socks5://new:1080")

        self.assertIsNotNone(res)
        self.assertTrue(res.ok)
        self.assertEqual(self.mgr.proxy, "socks5://new:1080")
        self.assertEqual(self.mgr.live_user().id, self.alice.id)
        # Рабочий прокси становится прокси профиля — следующий запуск пройдёт сам.
        self.assertEqual(self.pm.load_proxy(self.pm.get("+71111111111")), "socks5://new:1080")
        self.assertTrue(self._restart().ok)

    def test_working_stored_proxy_is_not_replaced(self):
        self._login(self.alice)
        self.pm.set_proxy("+71111111111", "socks5://old:1080")
        self.tg.dead_proxies.add("socks5://bad:1080")
        self.tg.offline = True
        self._restart()
        self.tg.offline = False

        res = self.accounts.begin_phone_login("+71111111111", proxy="socks5://bad:1080")

        self.assertTrue(res.ok)
        self.assertEqual(self.pm.load_proxy(self.pm.get("+71111111111")), "socks5://old:1080")

    def test_unreachable_saved_account_falls_back_to_code_login(self):
        # Раньше — тупик: «не удалось подключиться» и код не отправлялся никогда.
        self._login(self.alice)
        key = self._session_of("+71111111111")
        self.tg.offline = True
        self._restart()

        res = self.accounts.begin_phone_login("+71111111111")

        self.assertIsNone(res)                                  # можно слать код
        self.assertEqual(self._session_of("+71111111111"), key)  # сессия не тронута
        self.tg.offline = False
        self.assertIsNone(self.mgr.live_user())                 # вход — на чистом ключе


class TestDeadAuthKey(_Base):

    def test_duplicated_auth_key_is_treated_as_dead(self):
        # AUTH_KEY_DUPLICATED — ключ аннулирован сервером навсегда. Это не «нет
        # сети»: автовход должен перейти к следующему аккаунту, а не застрять.
        self._login(self.alice)
        self._add_via_modal(self.bob)
        self.tg.duplicated_keys.add(self._session_of("+71111111111"))

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertEqual(self.pm.active_phone(), "+72222222222")
        self.assertIsNone(self._session_of("+71111111111"))

    def test_duplicated_auth_key_does_not_block_code_login(self):
        self._login(self.alice)
        self.tg.duplicated_keys.add(self._session_of("+71111111111"))
        self._restart()

        self.assertIsNone(self.accounts.begin_phone_login("+71111111111"))
        self._login(self.alice)
        self.assertEqual(self.mgr.live_user().id, self.alice.id)


class TestSignOut(_Base):

    def test_sign_out_removes_profile_and_secrets(self):
        self._login(self.alice)
        alice_key = self._session_of("+71111111111")
        self.kr.store[("tg_exporter", "42:session")] = alice_key  # legacy-копия

        nxt = self.accounts.sign_out_active(lambda: self.mgr.get_client().log_out())

        self.assertIsNone(nxt)
        self.assertEqual(self.pm.list(), [])
        self.assertNotIn(("tg_exporter", "42:session:+71111111111"), self.kr.store)
        self.assertNotIn(("tg_exporter", "42:session"), self.kr.store)
        self.assertIsNone(self.tg.keys[alice_key])  # сессия завершена на сервере
        self.assertIsNone(self.mgr.live_user())

    def test_sign_out_switches_to_next_account(self):
        self._login(self.alice)
        self._add_via_modal(self.bob)

        nxt = self.accounts.sign_out_active(lambda: self.mgr.get_client().log_out())

        self.assertEqual(nxt.phone, "+72222222222")
        self.assertEqual(self.pm.active_phone(), "+72222222222")
        self.assertEqual(self.mgr.live_user().id, self.bob.id)
        self.assertEqual(self._phones(), ["+72222222222"])

    def test_sign_out_removes_profile_of_the_live_account(self):
        # Живой клиент — Bob, а активным (по ошибке) числится Alice:
        # выходим из Bob, значит и убирать надо Bob, а не Alice.
        self._login(self.alice)
        bob = self._add_via_modal(self.bob)
        self.mgr.use_session(self.pm.load_session(bob))

        self.accounts.sign_out_active(lambda: self.mgr.get_client().log_out())

        self.assertEqual(self._phones(), ["+71111111111"])
        self.assertIsNotNone(self._session_of("+71111111111"))

    def test_signed_out_account_does_not_come_back_after_restart(self):
        self._login(self.alice)
        self._add_via_modal(self.bob)
        self.accounts.sign_out_active(lambda: self.mgr.get_client().log_out())

        self._restart()

        self.assertEqual(self._phones(), ["+72222222222"])


class TestRestore(_Base):

    def test_restore_active_profile(self):
        self._login(self.alice)

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertEqual(res.profile.phone, "+71111111111")
        self.assertEqual(self.mgr.live_user().id, self.alice.id)

    def test_restore_does_not_resurrect_removed_account(self):
        # S3: удалили Bob (неактивный) — после перезапуска он не возвращается,
        # даже если в legacy-ключе осталась его сессия.
        self._login(self.alice)
        bob = self._add_via_modal(self.bob)
        self.kr.store[("tg_exporter", "42:session")] = self.pm.load_session(bob)
        self.pm.remove(bob.phone)

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertEqual(self._phones(), ["+71111111111"])
        self.assertEqual(self.mgr.live_user().id, self.alice.id)
        self.assertNotIn(("tg_exporter", "42:session"), self.kr.store)

    def test_restore_heals_aliased_profiles(self):
        # Состояние, оставленное багом: два профиля и legacy-ключ с ОДНОЙ
        # строкой, которая авторизует Bob. Активным числится Alice.
        self._login(self.bob)
        shared = self._session_of("+72222222222")
        self.pm.add_or_update(
            phone="+71111111111", api_id=self.api_id, session_string="tmp",
            display_name="Alice", set_active=True,
        )
        self.kr.store[("tg_exporter", "42:session:+71111111111")] = shared
        self.kr.store[("tg_exporter", "42:session")] = shared

        res = self._restart()

        self.assertTrue(res.ok)
        # Активен тот аккаунт, который реально авторизован.
        self.assertEqual(res.profile.phone, "+72222222222")
        self.assertEqual(self.pm.active_phone(), "+72222222222")
        self.assertEqual(self.mgr.live_user().id, self.bob.id)
        # Профиль-двойник остался в списке, но без сессии (нужен вход).
        self.assertEqual(self._phones(), ["+71111111111", "+72222222222"])
        self.assertIsNone(self._session_of("+71111111111"))
        self.assertEqual(self._session_of("+72222222222"), shared)

    def test_restore_drops_alias_copy_when_active_is_the_real_owner(self):
        self._login(self.alice)
        shared = self._session_of("+71111111111")
        self.pm.add_or_update(
            phone="+72222222222", api_id=self.api_id, session_string="tmp",
            display_name="Bob", set_active=False,
        )
        self.kr.store[("tg_exporter", "42:session:+72222222222")] = shared

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertEqual(self.pm.active_phone(), "+71111111111")
        self.assertIsNone(self._session_of("+72222222222"))

    def test_restore_orphan_session_is_not_turned_into_a_profile(self):
        # Профиль Alice хранит сессию Bob, а профиль Bob пользователь удалил.
        self._login(self.bob)
        shared = self._session_of("+72222222222")
        self.pm.add_or_update(
            phone="+71111111111", api_id=self.api_id, session_string="tmp",
            display_name="Alice", set_active=True,
        )
        self.kr.store[("tg_exporter", "42:session:+71111111111")] = shared
        self.pm.remove("+72222222222")

        res = self._restart()

        self.assertFalse(res.ok)
        self.assertEqual(self._phones(), ["+71111111111"])
        self.assertIsNone(self._session_of("+71111111111"))
        self.assertIsNone(self.mgr.live_user())

    def test_restore_falls_through_to_next_profile_when_active_is_dead(self):
        self._login(self.alice)
        self._add_via_modal(self.bob)
        self.tg.keys[self._session_of("+71111111111")] = None

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertEqual(self.pm.active_phone(), "+72222222222")
        self.assertEqual(self.mgr.live_user().id, self.bob.id)

    def test_restore_network_failure_keeps_everything(self):
        self._login(self.alice)
        key = self._session_of("+71111111111")
        self.tg.offline = True

        res = self._restart()

        self.assertIs(res.outcome, self.Outcome.FAILED)
        self.assertTrue(res.message)
        self.assertEqual(self._session_of("+71111111111"), key)
        self.assertEqual(self.pm.active_phone(), "+71111111111")

    def test_restore_retries_without_dead_proxy(self):
        self._login(self.alice)
        self.pm.set_proxy("+71111111111", "socks5://1.2.3.4:1080")
        self.tg.dead_proxies.add("socks5://1.2.3.4:1080")

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertIsNone(self.mgr.proxy)

    def test_restore_migrates_legacy_single_session(self):
        key = self.tg.new_key()
        self.tg.keys[key] = self.alice
        self.kr.store[("tg_exporter", "42:session")] = key

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertEqual(self._phones(), ["+71111111111"])
        self.assertEqual(self._session_of("+71111111111"), key)
        self.assertNotIn(("tg_exporter", "42:session"), self.kr.store)

    def test_restore_hands_session_to_owner_tried_earlier(self):
        # Активный Bob без сессии (пробуется первым), а его сессия лежит у Alice.
        self._login(self.bob)
        shared = self._session_of("+72222222222")
        self.pm.add_or_update(
            phone="+71111111111", api_id=self.api_id, session_string="tmp",
            display_name="Alice", set_active=False,
        )
        self.pm.drop_session("+72222222222")
        self.kr.store[("tg_exporter", "42:session:+71111111111")] = shared

        res = self._restart()

        self.assertTrue(res.ok)
        self.assertEqual(res.profile.phone, "+72222222222")
        self.assertEqual(self.mgr.live_user().id, self.bob.id)

    def test_dead_legacy_session_is_forgotten(self):
        key = self.tg.new_key()  # ключ есть, но никем не авторизован
        self.kr.store[("tg_exporter", "42:session")] = key

        res = self._restart()

        self.assertFalse(res.ok)
        self.assertNotIn(("tg_exporter", "42:session"), self.kr.store)
        self.assertEqual(self.pm.list(), [])

    def test_restore_with_nothing_saved_is_silent(self):
        res = self._restart()

        self.assertFalse(res.ok)
        self.assertEqual(res.message, "")

    def test_restore_does_not_rewrite_sessions(self):
        # Лишняя запись в Keychain на каждом старте — риск потерять сессию
        # (macOS set = delete + add). Автовход должен только читать.
        self._login(self.alice)
        writes = []
        orig = self.kr.set_password
        self.kr.set_password = lambda s, k, v: (writes.append(k), orig(s, k, v))[1]

        self._restart()

        self.assertEqual(writes, [])


class TestRememberLogin(_Base):

    def test_remember_creates_active_profile(self):
        profile = self._login(self.alice)

        self.assertEqual(profile.phone, "+71111111111")
        self.assertEqual(profile.user_id, self.alice.id)
        self.assertEqual(profile.display_name, "Alice")
        self.assertEqual(self.pm.active_phone(), "+71111111111")

    def test_remember_same_account_twice_does_not_duplicate(self):
        self._login(self.alice)
        self.accounts.begin_phone_login("+79990000000")
        self._login(self.alice)

        self.assertEqual(self._phones(), ["+71111111111"])

    def test_remember_clears_legacy_default_key(self):
        self.kr.store[("tg_exporter", "42:session")] = "whatever"

        self._login(self.alice)

        self.assertNotIn(("tg_exporter", "42:session"), self.kr.store)

    def test_remember_when_not_authorized_returns_none(self):
        self.assertIsNone(self.accounts.remember_login())
        self.assertEqual(self.pm.list(), [])


if __name__ == "__main__":
    unittest.main()
