"""
AccountSessions — привязка «профиль ↔ сохранённая сессия ↔ живой клиент».

Строка сессии Telethon — это только DC и auth-ключ; чей это аккаунт, знает лишь
сервер. Поэтому соответствие «профиль → аккаунт» нельзя принимать на веру:

  1. Сохранённая сессия профиля должна авторизовать именно его аккаунт —
     проверяется через get_me() при каждом применении сессии.
  2. Активным профиль становится только ПОСЛЕ проверки; при неудаче клиент
     возвращается к прежнему аккаунту.
  3. Интерактивный вход (код / QR) всегда идёт на чистом auth-ключе. Ключ, на
     котором уже входили, сервер привяжет к новому пользователю, и строка,
     сохранённая у старого профиля, начнёт открывать чужой аккаунт.
  4. Автовход не создаёт профили: удалённый аккаунт возвращается только через
     явный новый вход.

Не знает про UI. Все методы вызываются из фонового потока (там, где живёт
TelegramClient).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional

from telethon.errors import AuthKeyError

from .client import TelegramClientManager
from .credentials import CredentialsManager
from .profiles import Profile, ProfileManager, profile_matches
from ..utils.logger import logger


class Outcome(Enum):
    OK = auto()
    NO_SESSION = auto()     # у профиля нет сохранённой сессии
    UNAUTHORIZED = auto()   # сессия отозвана — снята, нужен повторный вход
    FOREIGN = auto()        # сессия авторизует другой аккаунт — снята
    FAILED = auto()         # сеть / прочее: сессия не тронута, можно повторить


@dataclass(frozen=True)
class Identity:
    user_id: int
    phone: str          # "+цифры" либо ""
    display_name: str


@dataclass(frozen=True)
class ApplyResult:
    outcome: Outcome
    profile: Optional[Profile] = None
    message: str = ""
    # FOREIGN: профиль настоящего владельца сессии, если он есть в списке.
    owner: Optional[Profile] = None

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK


class AccountSessions:

    def __init__(
        self,
        client_manager: TelegramClientManager,
        profiles: ProfileManager,
        credentials: CredentialsManager,
        get_api_id: Callable[[], str],
        set_api_id: Callable[[str], None],
    ) -> None:
        self._client = client_manager
        self._profiles = profiles
        self._credentials = credentials
        self._get_api_id = get_api_id
        self._set_api_id = set_api_id

    # ---- Запуск приложения ----

    def restore(self) -> ApplyResult:
        """
        Автовход: активный профиль, затем остальные с сохранённой сессией.

        Заодно лечит состояние, оставленное старыми версиями: сессия-«двойник»
        снимается с чужого профиля, активным становится тот аккаунт, который она
        реально авторизует. Профили не создаёт (кроме разовой миграции старого
        одиночного ключа, когда профилей ещё нет вовсе).
        """
        if self._profiles.is_empty():
            return self._restore_legacy()

        queue = self._startup_queue()
        tried: set[str] = set()
        message = ""
        while queue:
            profile = queue.pop(0)
            if profile.phone in tried:
                continue
            tried.add(profile.phone)

            res = self._apply(profile, allow_direct_fallback=True)
            if res.ok:
                self._profiles.set_active(profile.phone)
                self._forget_default_session()
                return res
            if res.outcome is Outcome.FAILED:
                # Сети нет — перебирать остальные бессмысленно (каждый ждал бы
                # таймаут). Сессия цела: пользователь повторит вход позже.
                return res
            if res.outcome is not Outcome.NO_SESSION:
                message = "Сохранённая сессия больше не действует. Войдите заново."
            if res.owner is not None:
                # Владельцу только что досталась сессия — пробуем его (снова).
                tried.discard(res.owner.phone)
                queue.insert(0, res.owner)

        self._client.use_fresh_session()
        return ApplyResult(Outcome.NO_SESSION, message=message)

    def _startup_queue(self) -> list[Profile]:
        active = self._profiles.active()
        rest = [p for p in self._profiles.list() if active is None or p.phone != active.phone]
        return ([active] if active is not None else []) + rest

    def _restore_legacy(self) -> ApplyResult:
        """Версии до мульти-аккаунтов хранили одну сессию под `{api_id}:session`."""
        api_id = self._get_api_id()
        session = self._credentials.load_session(api_id) if api_id else None
        if not session:
            return ApplyResult(Outcome.NO_SESSION)
        self._client.use_session(session)
        try:
            identity = self.identify()
        except Exception as exc:
            logger.error("accounts: автовход (legacy-сессия) не удался", exc=exc)
            return ApplyResult(Outcome.FAILED, message=_CONNECT_FAILED)
        if identity is None or not identity.phone:
            self._forget_default_session()
            self._client.use_fresh_session()
            return ApplyResult(
                Outcome.UNAUTHORIZED,
                message="Сохранённая сессия больше не действует. Войдите заново.",
            )
        profile = self._store(identity, session)
        return ApplyResult(Outcome.OK, profile=profile)

    # ---- Переключение ----

    def activate(self, profile: Profile) -> ApplyResult:
        """
        Переключает клиент на аккаунт профиля. Активным профиль становится
        только после проверки; иначе клиент возвращается к прежнему аккаунту.
        """
        previous = self._profiles.active()
        res = self._apply(profile)
        if res.ok:
            self._profiles.set_active(profile.phone)
            return res
        self._point_client_at(previous)
        return res

    # ---- Интерактивный вход ----

    def begin_phone_login(self, phone: str, proxy: str = "") -> Optional[ApplyResult]:
        """
        Вызывать перед отправкой кода с экрана входа. `proxy` — прокси,
        введённый на экране входа (если есть).

        Если для введённого номера есть рабочая сохранённая сессия — входит по
        ней (код не нужен) и возвращает результат. Во всех остальных случаях
        переводит клиент на чистый auth-ключ и возвращает None — можно
        отправлять код. Тупика «не удалось подключиться» здесь нет: если сети
        действительно нет, об этом скажет сама отправка кода.
        """
        profile = self._profiles.get(phone)
        if profile is not None and self._profiles.has_session(profile):
            res = self._apply(profile, allow_direct_fallback=True, extra_proxy=proxy)
            if res.ok:
                self._profiles.set_active(profile.phone)
                return res
        self._client.use_fresh_session()
        return None

    def begin_qr_login(self) -> None:
        """Вызывать перед показом QR с экрана входа: вход — на чистом ключе."""
        self._client.use_fresh_session()

    def remember_login(self) -> Optional[Profile]:
        """
        После успешного интерактивного входа: сохраняет сессию живого клиента в
        профиль того аккаунта, который реально вошёл, и делает его активным.
        """
        client = self._client.get_client()
        session = client.session.save() or ""
        identity = self.identify()
        if not session or identity is None or not identity.phone:
            return None
        return self._store(identity, session)

    # ---- Выход ----

    def sign_out_active(self, logout: Callable[[], None]) -> Optional[Profile]:
        """
        «Выход» и удаление активного аккаунта: завершает сессию на сервере
        (`logout` — best-effort), убирает профиль и его секреты, затем
        переключается на следующий аккаунт с рабочей сессией.

        Возвращает профиль, ставший активным, или None (показать экран входа).
        """
        target = self._profiles.active()
        try:
            identity = self.identify()
        except Exception:
            identity = None
        if identity is not None:
            # Выходим из того аккаунта, которым клиент является на самом деле.
            target = self._profiles.find_by_identity(identity.user_id, identity.phone)

        logout()
        if target is not None:
            self._profiles.remove(target.phone)
        self._forget_default_session()
        self._client.use_fresh_session()

        for profile in self._startup_queue():
            res = self._apply(profile)
            if res.ok:
                self._profiles.set_active(profile.phone)
                return profile
            if res.outcome is Outcome.FAILED:
                break
        self._client.use_fresh_session()
        return None

    # ---- Идентичность ----

    def identify(self) -> Optional[Identity]:
        """
        Кем авторизован живой клиент. None — сессия не авторизована (Telethon
        возвращает None только на 401). Сетевые ошибки пробрасываются: по ним
        нельзя судить, что сессия мертва.
        """
        client = self._client.ensure_connected()
        me = client.get_me()
        if me is None:
            return None
        digits = "".join(c for c in str(getattr(me, "phone", "") or "") if c.isdigit())
        name = " ".join(filter(None, [
            getattr(me, "first_name", "") or "",
            getattr(me, "last_name", "") or "",
        ])).strip()
        return Identity(
            user_id=int(getattr(me, "id", 0) or 0),
            phone=("+" + digits) if digits else "",
            display_name=name,
        )

    # ---- Internal ----

    def _apply(
        self,
        profile: Profile,
        allow_direct_fallback: bool = False,
        extra_proxy: str = "",
    ) -> ApplyResult:
        """
        Направляет клиент на сессию профиля и проверяет, чей это аккаунт.
        Активный профиль НЕ меняет и клиент при неудаче НЕ откатывает — это
        делают вызывающие.

        `extra_proxy` — прокси с экрана входа: пробуется после сохранённого и,
        если подключиться удалось только через него, становится прокси профиля.
        """
        name = profile.display_name or profile.phone
        session = self._profiles.load_session(profile)
        if not session:
            return ApplyResult(
                Outcome.NO_SESSION, profile=profile,
                message=f"Для аккаунта «{name}» нет сохранённой сессии. Нажмите «Войти» в его карточке.",
            )

        if profile.api_id and profile.api_id != self._get_api_id():
            self._set_api_id(profile.api_id)

        proxy = self._profiles.load_proxy(profile) or ""
        attempts = [proxy]
        if extra_proxy and extra_proxy != proxy:
            attempts.append(extra_proxy)
        # Прокси мог умереть, а Telegram быть доступен напрямую (VPN) — на
        # автовходе пробуем и без него.
        if allow_direct_fallback and "" not in attempts:
            attempts.append("")
        identity: Optional[Identity] = None
        connected = False
        used_proxy = proxy
        for attempt in attempts:
            self._client.use_proxy(attempt)
            self._client.use_session(session)
            try:
                identity = self.identify()
            except AuthKeyError as exc:
                # 406 (AUTH_KEY_DUPLICATED и т.п.): сервер аннулировал ключ
                # навсегда. Это не сбой сети — сессия мертва, как при 401.
                logger.warning(f"accounts: ключ {_tag(profile)} аннулирован ({type(exc).__name__})")
                identity = None
            except Exception as exc:
                logger.error(f"accounts: не удалось подключиться для {_tag(profile)}", exc=exc)
                continue
            connected = True
            used_proxy = attempt
            break
        if not connected:
            return ApplyResult(Outcome.FAILED, profile=profile, message=_CONNECT_FAILED)

        if identity is None:
            logger.warning(f"accounts: сессия {_tag(profile)} не действует — снимаю")
            self._profiles.drop_session(profile.phone)
            return ApplyResult(
                Outcome.UNAUTHORIZED, profile=profile,
                message=f"Сессия аккаунта «{name}» больше не действует. Нажмите «Войти» в его карточке.",
            )

        # Сверить не с чем (нет ни номера, ни user_id в профиле) — не считаем чужой.
        verifiable = bool(identity.phone) or bool(profile.user_id and identity.user_id)
        if verifiable and not profile_matches(profile, identity.user_id, identity.phone):
            return self._disarm_foreign(profile, identity, session, name)

        self._profiles.set_identity(profile.phone, identity.user_id, identity.display_name)
        self._profiles.drop_session_aliases(profile, session)
        if extra_proxy and used_proxy == extra_proxy:
            # Сохранённый прокси не сработал, а введённый на экране входа — да.
            self._profiles.set_proxy(profile.phone, extra_proxy)
        return ApplyResult(Outcome.OK, profile=self._profiles.get(profile.phone) or profile)

    def _disarm_foreign(
        self, profile: Profile, identity: Identity, session: str, name: str,
    ) -> ApplyResult:
        """Сессия профиля авторизует другой аккаунт: снимаем её с этого профиля."""
        logger.warning(f"accounts: сессия профиля {_tag(profile)} принадлежит другому аккаунту — снимаю")
        owner = self._profiles.find_by_identity(identity.user_id, identity.phone)
        if owner is not None and owner.phone == profile.phone:
            owner = None
        if owner is not None and not self._profiles.has_session(owner):
            # Настоящий владелец остался без сессии — она по праву его.
            self._profiles.add_or_update(
                phone=owner.phone, api_id=owner.api_id or self._get_api_id(),
                session_string=session, display_name=identity.display_name,
                set_active=False, user_id=identity.user_id,
            )
        self._profiles.drop_session(profile.phone)
        return ApplyResult(
            Outcome.FOREIGN, profile=profile, owner=owner,
            message=f"Сессия аккаунта «{name}» больше не действует. Нажмите «Войти» в его карточке.",
        )

    def _store(self, identity: Identity, session: str) -> Profile:
        profile = self._profiles.add_or_update(
            phone=identity.phone, api_id=self._get_api_id(),
            session_string=session, display_name=identity.display_name,
            set_active=True, user_id=identity.user_id,
        )
        # Вход шёл на «чистой» сессии — закрепляем за клиентом сохранённую.
        self._client.pin_session(session)
        self._forget_default_session()
        return profile

    def _point_client_at(self, profile: Optional[Profile]) -> None:
        """Возвращает клиент к сессии профиля без сетевых вызовов (ленивый коннект)."""
        session = self._profiles.load_session(profile) if profile is not None else None
        if not session:
            self._client.use_fresh_session()
            return
        if profile.api_id and profile.api_id != self._get_api_id():
            self._set_api_id(profile.api_id)
        self._client.use_proxy(self._profiles.load_proxy(profile) or "")
        self._client.use_session(session)

    def _forget_default_session(self) -> None:
        """
        Старый одиночный ключ `{api_id}:session` нужен только для миграции. Пока
        он лежит рядом с профилями, через него может «вернуться» аккаунт,
        который пользователь удалил.
        """
        api_id = self._get_api_id()
        if api_id:
            self._credentials.delete_session(api_id)


def _tag(profile: Profile) -> str:
    """Метка профиля для лога — без номера телефона."""
    return f"«…{profile.phone[-2:]}»"


_CONNECT_FAILED = (
    "Не удалось подключиться к Telegram. Проверьте интернет, VPN или прокси и попробуйте снова."
)
