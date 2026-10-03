"""Tests for ProfileManager — хранение, CRUD, активный профиль, сессии в keyring."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


class _FakeKeyring:
    """In-memory замена keyring для изоляции тестов от системного хранилища."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, key: str, value: str) -> None:
        self.store[(service, key)] = value

    def get_password(self, service: str, key: str):
        return self.store.get((service, key))

    def delete_password(self, service: str, key: str) -> None:
        self.store.pop((service, key), None)


class TestProfileManager(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._fake_home = Path(self._tmp.name)

        # Подмена keyring на in-memory fake (до импорта ProfileManager)
        self._fake_kr = _FakeKeyring()
        sys.modules["keyring"] = self._fake_kr  # type: ignore[assignment]
        self.addCleanup(lambda: sys.modules.pop("keyring", None))

        # Импортируем и патчим путь к profiles.json
        from tg_exporter.core import profiles as profiles_mod
        from tg_exporter.core.credentials import CredentialsManager
        from tg_exporter.core.profiles import ProfileManager

        self._profiles_mod = profiles_mod
        self._orig_path = profiles_mod._PROFILES_FILE
        profiles_mod._PROFILES_FILE = self._fake_home / "profiles.json"
        self.addCleanup(lambda: setattr(profiles_mod, "_PROFILES_FILE", self._orig_path))

        self._creds = CredentialsManager()
        # CredentialsManager._require_keyring() должен возвращать True с фейком
        self._creds._require_keyring = lambda: True  # type: ignore[method-assign]
        self.pm = ProfileManager(self._creds)

    def test_empty_initial_state(self):
        self.assertTrue(self.pm.is_empty())
        self.assertIsNone(self.pm.active())
        self.assertEqual(self.pm.list(), [])

    def test_add_and_list(self):
        p = self.pm.add_or_update(
            phone="+79991112233", api_id="42",
            session_string="session-A", display_name="Max",
        )
        self.assertEqual(p.phone, "+79991112233")
        self.assertEqual(p.display_name, "Max")
        self.assertFalse(self.pm.is_empty())
        self.assertEqual(self.pm.active_phone(), "+79991112233")
        self.assertEqual(len(self.pm.list()), 1)

    def test_session_stored_in_keyring(self):
        self.pm.add_or_update(
            phone="+79991112233", api_id="42",
            session_string="session-A",
        )
        key = ("tg_exporter", "42:session:+79991112233")
        self.assertEqual(self._fake_kr.store.get(key), "session-A")

    def test_add_second_profile_preserves_active(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.pm.add_or_update(
            phone="+72222222222", api_id="42",
            session_string="s2", set_active=False,
        )
        self.assertEqual(len(self.pm.list()), 2)
        self.assertEqual(self.pm.active_phone(), "+71111111111")

    def test_set_active_switches(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.pm.add_or_update(
            phone="+72222222222", api_id="42",
            session_string="s2", set_active=False,
        )
        result = self.pm.set_active("+72222222222")
        self.assertIsNotNone(result)
        self.assertEqual(self.pm.active_phone(), "+72222222222")

    def test_set_active_unknown_returns_none(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.assertIsNone(self.pm.set_active("+70000000000"))
        self.assertEqual(self.pm.active_phone(), "+71111111111")

    def test_remove_deletes_session(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.pm.add_or_update(
            phone="+72222222222", api_id="42",
            session_string="s2", set_active=False,
        )
        self.assertTrue(self.pm.remove("+71111111111"))
        # Активный должен переключиться на оставшийся
        self.assertEqual(self.pm.active_phone(), "+72222222222")
        # Сессия удалена из keyring
        self.assertNotIn(("tg_exporter", "42:session:+71111111111"), self._fake_kr.store)

    def test_remove_last_clears_active(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.pm.remove("+71111111111")
        self.assertIsNone(self.pm.active_phone())
        self.assertTrue(self.pm.is_empty())

    def test_remove_unknown_returns_false(self):
        self.assertFalse(self.pm.remove("+70000000000"))

    def test_rename(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.assertTrue(self.pm.rename("+71111111111", "Работа"))
        self.assertEqual(self.pm.get("+71111111111").display_name, "Работа")

    def test_load_session_roundtrip(self):
        p = self.pm.add_or_update(
            phone="+71111111111", api_id="42",
            session_string="my-session-string",
        )
        self.assertEqual(self.pm.load_session(p), "my-session-string")

    def test_persistence_across_instances(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.pm.add_or_update(
            phone="+72222222222", api_id="42",
            session_string="s2", set_active=False,
        )
        self.pm.set_active("+72222222222")

        from tg_exporter.core.profiles import ProfileManager
        pm2 = ProfileManager(self._creds)
        self.assertEqual(pm2.active_phone(), "+72222222222")
        self.assertEqual(len(pm2.list()), 2)

    def test_phone_normalization(self):
        p = self.pm.add_or_update(
            phone="7 999 111-22-33", api_id="42", session_string="s",
        )
        self.assertEqual(p.phone, "79991112233")

    def test_empty_phone_raises(self):
        with self.assertRaises(ValueError):
            self.pm.add_or_update(phone="   ", api_id="42", session_string="s")

    def test_empty_api_id_raises(self):
        with self.assertRaises(ValueError):
            self.pm.add_or_update(phone="+71111111111", api_id="", session_string="s")

    def test_update_existing_keeps_phone(self):
        self.pm.add_or_update(
            phone="+71111111111", api_id="42",
            session_string="v1", display_name="Old",
        )
        updated = self.pm.add_or_update(
            phone="+71111111111", api_id="42",
            session_string="v2", display_name="New",
        )
        self.assertEqual(updated.display_name, "New")
        self.assertEqual(len(self.pm.list()), 1)
        key = ("tg_exporter", "42:session:+71111111111")
        self.assertEqual(self._fake_kr.store.get(key), "v2")

    def test_file_has_no_session_secrets(self):
        self.pm.add_or_update(
            phone="+71111111111", api_id="42",
            session_string="super-secret-session",
        )
        path = self._fake_home / "profiles.json"
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("super-secret-session", raw)
        data = json.loads(raw)
        self.assertEqual(data["active_phone"], "+71111111111")
        self.assertEqual(len(data["profiles"]), 1)

    # --- прокси на аккаунт ---

    def test_proxy_default_empty(self):
        p = self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.assertEqual(p.proxy, "")

    def test_set_proxy(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.assertTrue(self.pm.set_proxy("+71111111111", "socks5://1.2.3.4:1080"))
        # без кредов public == full
        self.assertEqual(self.pm.get("+71111111111").proxy, "socks5://1.2.3.4:1080")

    def test_set_proxy_unknown_returns_false(self):
        self.assertFalse(self.pm.set_proxy("+70000000000", "socks5://1.2.3.4:1080"))

    def test_set_proxy_clear(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy("+71111111111", "socks5://1.2.3.4:1080")
        self.assertTrue(self.pm.set_proxy("+71111111111", ""))
        self.assertEqual(self.pm.get("+71111111111").proxy, "")

    def test_proxy_persisted_to_file(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy("+71111111111", "socks5://1.2.3.4:1080")
        path = self._fake_home / "profiles.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["profiles"][0]["proxy"], "socks5://1.2.3.4:1080")

    def test_proxy_persists_across_instances(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy("+71111111111", "http://1.2.3.4:8080")
        from tg_exporter.core.profiles import ProfileManager
        pm2 = ProfileManager(self._creds)
        self.assertEqual(pm2.get("+71111111111").proxy, "http://1.2.3.4:8080")

    # --- секрет прокси в Keyring, не в файле ---

    def test_proxy_password_not_in_file(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy("+71111111111", "socks5://user:topsecret@1.2.3.4:1080")
        path = self._fake_home / "profiles.json"
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("topsecret", raw)
        # username/host остаются (несекретны) для UI-метки
        self.assertIn("1.2.3.4", raw)

    def test_mtproto_secret_not_in_file(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy(
            "+71111111111",
            "mtproto://1.2.3.4:443?secret=00112233445566778899aabbccddeeff",
        )
        raw = (self._fake_home / "profiles.json").read_text(encoding="utf-8")
        self.assertNotIn("00112233445566778899aabbccddeeff", raw)

    def test_load_proxy_returns_full_url_with_secret(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        full = "socks5://user:topsecret@1.2.3.4:1080"
        self.pm.set_proxy("+71111111111", full)
        p = self.pm.get("+71111111111")
        self.assertEqual(self.pm.load_proxy(p), full)

    def test_load_proxy_full_persists_across_instances(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        full = "socks5://user:topsecret@1.2.3.4:1080"
        self.pm.set_proxy("+71111111111", full)
        from tg_exporter.core.profiles import ProfileManager
        pm2 = ProfileManager(self._creds)
        self.assertEqual(pm2.load_proxy(pm2.get("+71111111111")), full)

    def test_proxy_secret_in_keyring(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy("+71111111111", "socks5://user:topsecret@1.2.3.4:1080")
        key = ("tg_exporter", "42:proxy:+71111111111")
        self.assertIn("topsecret", self._fake_kr.store.get(key, ""))

    def test_clear_proxy_removes_keyring_secret(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy("+71111111111", "socks5://user:topsecret@1.2.3.4:1080")
        self.pm.set_proxy("+71111111111", "")
        key = ("tg_exporter", "42:proxy:+71111111111")
        self.assertNotIn(key, self._fake_kr.store)

    def test_remove_profile_deletes_proxy_secret(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy("+71111111111", "socks5://user:topsecret@1.2.3.4:1080")
        self.pm.remove("+71111111111")
        key = ("tg_exporter", "42:proxy:+71111111111")
        self.assertNotIn(key, self._fake_kr.store)

    def test_load_proxy_falls_back_to_public_when_no_keyring(self):
        # Старый профиль: proxy в файле, но в Keyring секрета нет (миграция).
        # load_proxy должен вернуть хотя бы public-строку из файла.
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s")
        self.pm.set_proxy("+71111111111", "socks5://1.2.3.4:1080")
        # вручную стираем keyring-ключ прокси
        self._fake_kr.store.pop(("tg_exporter", "42:proxy:+71111111111"), None)
        p = self.pm.get("+71111111111")
        self.assertEqual(self.pm.load_proxy(p), "socks5://1.2.3.4:1080")

    def test_old_file_without_proxy_loads(self):
        # Обратная совместимость: profiles.json от старой версии без поля proxy.
        path = self._fake_home / "profiles.json"
        path.write_text(json.dumps({
            "active_phone": "+71111111111",
            "profiles": [{"phone": "+71111111111", "display_name": "Max", "api_id": "42"}],
        }), encoding="utf-8")
        from tg_exporter.core.profiles import ProfileManager
        pm2 = ProfileManager(self._creds)
        p = pm2.get("+71111111111")
        self.assertEqual(p.display_name, "Max")
        self.assertEqual(p.proxy, "")  # дефолт для отсутствующего поля

    # --- идентичность профиля и защита от «склейки» сессий ---

    def test_same_session_cannot_belong_to_two_profiles(self):
        # Баг: после «Выход» + входа под другим аккаунтом на том же auth_key
        # строка сессии совпадала с сохранённой у старого профиля → оба профиля
        # открывали один аккаунт. Auth-ключ привязан ровно к одному пользователю,
        # поэтому у прежнего владельца строки сессия должна быть снята.
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="shared")
        self.pm.add_or_update(phone="+72222222222", api_id="42", session_string="shared")
        a = self.pm.get("+71111111111")
        b = self.pm.get("+72222222222")
        self.assertIsNone(self.pm.load_session(a))
        self.assertEqual(self.pm.load_session(b), "shared")
        # Профиль-«двойник» остаётся в списке (нужен повторный вход), не исчезает.
        self.assertEqual(len(self.pm.list()), 2)

    def test_distinct_sessions_are_left_alone(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.pm.add_or_update(phone="+72222222222", api_id="42", session_string="s2")
        self.assertEqual(self.pm.load_session(self.pm.get("+71111111111")), "s1")
        self.assertEqual(self.pm.load_session(self.pm.get("+72222222222")), "s2")

    def test_drop_session_keeps_profile(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.assertTrue(self.pm.drop_session("+71111111111"))
        p = self.pm.get("+71111111111")
        self.assertIsNotNone(p)
        self.assertIsNone(self.pm.load_session(p))
        self.assertFalse(self.pm.has_session(p))

    def test_has_session(self):
        p = self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.assertTrue(self.pm.has_session(p))

    def test_user_id_persisted(self):
        self.pm.add_or_update(
            phone="+71111111111", api_id="42", session_string="s1", user_id=777,
        )
        from tg_exporter.core.profiles import ProfileManager
        pm2 = ProfileManager(self._creds)
        self.assertEqual(pm2.get("+71111111111").user_id, 777)

    def test_same_user_id_updates_existing_profile_not_duplicate(self):
        # Тот же аккаунт (user_id) под иначе записанным номером не должен
        # плодить второй профиль — иначе «удалённый» аккаунт возвращается.
        self.pm.add_or_update(
            phone="79991112233", api_id="42", session_string="v1", user_id=777,
        )
        self.pm.add_or_update(
            phone="+79991112233", api_id="42", session_string="v2", user_id=777,
        )
        self.assertEqual(len(self.pm.list()), 1)
        p = self.pm.list()[0]
        self.assertEqual(self.pm.load_session(p), "v2")

    def test_lookup_ignores_plus_prefix(self):
        self.pm.add_or_update(phone="79991112233", api_id="42", session_string="s")
        self.assertIsNotNone(self.pm.get("+79991112233"))
        self.pm.add_or_update(phone="+79991112233", api_id="42", session_string="s2")
        self.assertEqual(len(self.pm.list()), 1)

    def test_find_by_identity_prefers_user_id(self):
        self.pm.add_or_update(
            phone="+71111111111", api_id="42", session_string="s1", user_id=1,
        )
        self.pm.add_or_update(
            phone="+72222222222", api_id="42", session_string="s2", user_id=2,
        )
        found = self.pm.find_by_identity(user_id=2, phone="+71111111111")
        self.assertEqual(found.phone, "+72222222222")
        # Без user_id — по номеру.
        self.assertEqual(self.pm.find_by_identity(user_id=0, phone="71111111111").phone,
                         "+71111111111")
        self.assertIsNone(self.pm.find_by_identity(user_id=99, phone="+70000000000"))

    def test_user_id_mismatch_is_not_matched_by_phone(self):
        # Номер совпал, но user_id у профиля уже известен и он другой —
        # это другой аккаунт (номер переехал), совпадением не считаем.
        self.pm.add_or_update(
            phone="+71111111111", api_id="42", session_string="s1", user_id=1,
        )
        self.assertIsNone(self.pm.find_by_identity(user_id=5, phone="+71111111111"))

    def test_with_session_lists_only_loginable_profiles(self):
        self.pm.add_or_update(phone="+71111111111", api_id="42", session_string="s1")
        self.pm.add_or_update(phone="+72222222222", api_id="42", session_string="s2")
        self.pm.drop_session("+71111111111")
        self.assertEqual([p.phone for p in self.pm.with_session()], ["+72222222222"])

    def test_old_file_without_user_id_loads(self):
        path = self._fake_home / "profiles.json"
        path.write_text(json.dumps({
            "active_phone": "+71111111111",
            "profiles": [{"phone": "+71111111111", "display_name": "Max", "api_id": "42"}],
        }), encoding="utf-8")
        from tg_exporter.core.profiles import ProfileManager
        pm2 = ProfileManager(self._creds)
        self.assertEqual(pm2.get("+71111111111").user_id, 0)


if __name__ == "__main__":
    unittest.main()
