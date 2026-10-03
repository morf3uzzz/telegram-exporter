"""
ProfileManager — управление несколькими Telegram-аккаунтами.

Метаданные профилей (несекретные): ~/.tg_exporter/profiles.json.
Сессии (секретные) хранятся в Keyring под ключом `{api_id}:session:{phone}`.

Формат profiles.json:
{
  "active_phone": "+7999...",
  "profiles": [
    {"phone": "+7999...", "display_name": "Max", "api_id": "123", "user_id": 111},
    ...
  ]
}

Инвариант: сохранённая сессия профиля авторизует именно аккаунт этого профиля.
Одна и та же строка сессии не может принадлежать двум профилям (auth-ключ
привязан ровно к одному пользователю) — см. add_or_update().
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .credentials import CredentialsManager, keyring_set_with_retry
from ..utils.logger import logger


_PROFILES_FILE = Path(os.path.expanduser("~/.tg_exporter/profiles.json"))


@dataclass
class Profile:
    phone: str
    display_name: str = ""
    api_id: str = ""
    # URL-строка прокси для этого аккаунта ("" — без прокси). Несекретная,
    # хранится в profiles.json. Формат разбирается в core/proxy.py.
    proxy: str = ""
    # Telegram user id владельца — настоящая идентичность аккаунта (номер может
    # смениться или быть записан с «+» и без). 0 — ещё не известен (старый файл).
    user_id: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def _session_key(api_id: str, phone: str) -> str:
    return f"{api_id}:session:{phone}"


def _proxy_key(api_id: str, phone: str) -> str:
    return f"{api_id}:proxy:{phone}"


class ProfileManager:
    """
    CRUD над списком профилей + активным профилем.

    Thread-safe: внутренний lock защищает загрузку/сохранение файла.
    Секреты (session string) всегда идут через CredentialsManager/Keyring.
    """

    def __init__(self, credentials: CredentialsManager) -> None:
        self._credentials = credentials
        self._lock = threading.Lock()
        self._profiles: list[Profile] = []
        self._active_phone: Optional[str] = None
        self._load()

    # ---------------------------------------------------------- persistence

    def _load(self) -> None:
        if not _PROFILES_FILE.exists():
            return
        try:
            with _PROFILES_FILE.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            self._active_phone = raw.get("active_phone") or None
            self._profiles = [
                Profile.from_dict(p) for p in (raw.get("profiles") or [])
                if isinstance(p, dict) and p.get("phone")
            ]
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            logger.warning(f"profiles: load failed: {exc}")

    def _save(self) -> None:
        _PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active_phone": self._active_phone,
            "profiles": [p.to_dict() for p in self._profiles],
        }
        tmp = _PROFILES_FILE.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, _PROFILES_FILE)
        _secure_permissions(_PROFILES_FILE)

    # ---------------------------------------------------------- queries

    def list(self) -> list[Profile]:
        with self._lock:
            return list(self._profiles)

    def active(self) -> Optional[Profile]:
        with self._lock:
            if not self._active_phone:
                return None
            return next((p for p in self._profiles if p.phone == self._active_phone), None)

    def active_phone(self) -> Optional[str]:
        with self._lock:
            return self._active_phone

    def get(self, phone: str) -> Optional[Profile]:
        with self._lock:
            return self._find(phone)

    def find_by_identity(self, user_id: int, phone: str) -> Optional[Profile]:
        """
        Профиль, которому принадлежит аккаунт (user_id, phone). Сначала по
        user_id, затем по номеру — но только если user_id профиля ещё не известен
        или совпадает (см. profile_matches).
        """
        with self._lock:
            if user_id:
                found = next((p for p in self._profiles if p.user_id == user_id), None)
                if found is not None:
                    return found
            return next(
                (p for p in self._profiles if profile_matches(p, user_id, phone)), None
            )

    def with_session(self) -> list[Profile]:
        """Профили, в которые можно войти без кода (сессия есть в Keyring)."""
        return [p for p in self.list() if self.has_session(p)]

    def _find(self, phone: str) -> Optional[Profile]:
        """Вызывать под self._lock. Номера сравниваются по цифрам («+» не важен)."""
        digits = _digits(phone)
        if not digits:
            return None
        return next((p for p in self._profiles if _digits(p.phone) == digits), None)

    def is_empty(self) -> bool:
        with self._lock:
            return not self._profiles

    # ---------------------------------------------------------- mutations

    def add_or_update(
        self,
        phone: str,
        api_id: str,
        session_string: str,
        display_name: str = "",
        set_active: bool = True,
        user_id: int = 0,
    ) -> Profile:
        """
        Добавляет новый профиль или обновляет существующий — того же аккаунта
        (по user_id) либо с тем же номером.

        Если такая же строка сессии уже записана у ДРУГОГО профиля, там она
        удаляется: один auth-ключ авторизует ровно одного пользователя, значит
        чужая копия — устаревший «двойник», через который переключение
        открывало бы не тот аккаунт.
        """
        phone = _normalize_phone(phone)
        if not phone:
            raise ValueError("phone required")
        if not api_id:
            raise ValueError("api_id required")
        with self._lock:
            existing = self._find_owner(user_id, phone)
            key_phone = existing.phone if existing is not None else phone
        if session_string:
            self._credentials._require_keyring()  # type: ignore[attr-defined]
            keyring_set_with_retry("tg_exporter", _session_key(api_id, key_phone), session_string)
        with self._lock:
            existing = self._find_owner(user_id, phone)
            if existing is not None:
                existing.api_id = api_id
                if display_name:
                    existing.display_name = display_name
                if user_id:
                    existing.user_id = user_id
                profile = existing
            else:
                profile = Profile(
                    phone=phone, display_name=display_name or phone,
                    api_id=api_id, user_id=user_id,
                )
                self._profiles.append(profile)
            if set_active or self._active_phone is None:
                self._active_phone = profile.phone
            self._save()
        self.drop_session_aliases(profile, session_string)
        return profile

    def _find_owner(self, user_id: int, phone: str) -> Optional[Profile]:
        """Вызывать под self._lock. Профиль аккаунта: по user_id, иначе по номеру."""
        if user_id:
            found = next((p for p in self._profiles if p.user_id == user_id), None)
            if found is not None:
                return found
        return self._find(phone)

    def set_active(self, phone: str) -> Optional[Profile]:
        with self._lock:
            profile = self._find(phone)
            if profile is None:
                return None
            self._active_phone = profile.phone
            self._save()
            return profile

    def remove(self, phone: str) -> bool:
        """Удаляет профиль и его сессию из Keyring."""
        with self._lock:
            profile = self._find(phone)
            if profile is None:
                return False
            self._profiles = [p for p in self._profiles if p is not profile]
            if self._active_phone == profile.phone:
                self._active_phone = self._profiles[0].phone if self._profiles else None
            self._save()
        self._delete_session(profile.api_id, profile.phone)
        self._delete_proxy(profile.api_id, profile.phone)
        return True

    def drop_session(self, phone: str) -> bool:
        """
        Удаляет из Keyring только сессию профиля; сам профиль остаётся в списке
        («нужен повторный вход»). Для мёртвых сессий и сессий-«двойников».
        """
        with self._lock:
            profile = self._find(phone)
        if profile is None:
            return False
        self._delete_session(profile.api_id, profile.phone)
        return True

    def drop_session_aliases(self, owner: Profile, session_string: str) -> None:
        """Снимает такую же строку сессии со всех профилей, кроме владельца."""
        if not session_string:
            return
        for other in self.list():
            if _digits(other.phone) == _digits(owner.phone):
                continue
            if self.load_session(other) == session_string:
                logger.warning(
                    "profiles: одна сессия у двух профилей — снимаю копию у прежнего владельца"
                )
                self._delete_session(other.api_id, other.phone)

    def set_identity(self, phone: str, user_id: int, display_name: str = "") -> bool:
        """Запоминает user_id (и имя) аккаунта после проверки сессии."""
        with self._lock:
            profile = self._find(phone)
            if profile is None:
                return False
            changed = False
            if user_id and profile.user_id != user_id:
                profile.user_id = user_id
                changed = True
            if display_name and profile.display_name != display_name:
                profile.display_name = display_name
                changed = True
            if changed:
                self._save()
            return True

    def rename(self, phone: str, display_name: str) -> bool:
        with self._lock:
            profile = self._find(phone)
            if profile is None:
                return False
            profile.display_name = display_name or profile.phone
            self._save()
            return True

    def set_proxy(self, phone: str, proxy: str) -> bool:
        """
        Задаёт/очищает прокси для аккаунта. "" — убрать прокси.

        Секрет (пароль/MTProto-секрет) хранится в Keyring под `{api_id}:proxy:
        {phone}`; в profiles.json пишется только несекретная часть (public_url:
        тип/host/port/username) — для UI-метки и предзаполнения полей.
        """
        with self._lock:
            profile = self._find(phone)
            if profile is None:
                return False
            phone = profile.phone
            full = (proxy or "").strip()
            if not full:
                profile.proxy = ""
                self._save()
                api_id = profile.api_id
                self._delete_proxy(api_id, phone)
                return True
            # Разбираем, чтобы отделить публичную часть от секрета.
            from .proxy import parse_proxy, ProxyValidationError
            try:
                cfg = parse_proxy(full)
            except ProxyValidationError:
                cfg = None
            if cfg is not None:
                profile.proxy = cfg.public_url()
            else:
                # Не распарсилось — кладём как есть (без секрета отделить нельзя);
                # это не должно случаться, т.к. UI валидирует перед сохранением.
                profile.proxy = full
            self._save()
            api_id = profile.api_id
        # Полный URL (с секретом) — в Keyring.
        self._save_proxy_secret(api_id, phone, full)
        return True

    # ---------------------------------------------------------- proxy I/O

    def load_proxy(self, profile: Profile) -> str:
        """
        Полная строка прокси (с секретом) из Keyring. Фолбэк на profile.proxy
        (public, из файла) для профилей без сохранённого в Keyring секрета.
        """
        if not profile.api_id or not profile.phone:
            return profile.proxy or ""
        try:
            import keyring
            full = keyring.get_password("tg_exporter", _proxy_key(profile.api_id, profile.phone))
            if full:
                return full
        except Exception:
            pass
        return profile.proxy or ""

    def _save_proxy_secret(self, api_id: str, phone: str, full_url: str) -> None:
        if not api_id or not phone or not full_url:
            return
        try:
            keyring_set_with_retry("tg_exporter", _proxy_key(api_id, phone), full_url)
        except Exception as exc:
            logger.error(f"profiles: save proxy failed: {exc}")

    def _delete_proxy(self, api_id: str, phone: str) -> None:
        if not api_id or not phone:
            return
        try:
            import keyring
            key = _proxy_key(api_id, phone)
            if keyring.get_password("tg_exporter", key):
                keyring.delete_password("tg_exporter", key)
        except Exception:
            pass

    # ---------------------------------------------------------- session I/O

    def load_session(self, profile: Profile) -> Optional[str]:
        if not profile.api_id or not profile.phone:
            return None
        try:
            import keyring
            return keyring.get_password("tg_exporter", _session_key(profile.api_id, profile.phone)) or None
        except Exception:
            return None

    def has_session(self, profile: Profile) -> bool:
        return bool(self.load_session(profile))

    def save_session(self, profile: Profile, session_string: str) -> None:
        if not session_string or not profile.api_id or not profile.phone:
            return
        try:
            keyring_set_with_retry("tg_exporter", _session_key(profile.api_id, profile.phone), session_string)
        except Exception as exc:
            logger.error(f"profiles: save_session failed: {exc}")

    def _delete_session(self, api_id: str, phone: str) -> None:
        if not api_id or not phone:
            return
        try:
            import keyring
            key = _session_key(api_id, phone)
            if keyring.get_password("tg_exporter", key):
                keyring.delete_password("tg_exporter", key)
        except Exception:
            pass


def _digits(phone: str) -> str:
    return "".join(c for c in (phone or "") if c.isdigit())


def profile_matches(profile: Profile, user_id: int, phone: str) -> bool:
    """
    Принадлежит ли профиль аккаунту (user_id, phone).

    user_id — истина, когда он известен обеим сторонам. Номер сравнивается
    только если user_id профиля ещё не записан (профиль из старой версии).
    """
    if user_id and profile.user_id:
        return profile.user_id == user_id
    digits = _digits(phone)
    return bool(digits) and _digits(profile.phone) == digits


def _normalize_phone(phone: str) -> str:
    phone = (phone or "").strip()
    if not phone:
        return ""
    # Оставляем только `+` в начале и цифры
    digits = "".join(c for c in phone if c.isdigit())
    if phone.startswith("+"):
        return "+" + digits
    return digits


def _secure_permissions(path: Path) -> None:
    import platform
    if platform.system() == "Windows":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
