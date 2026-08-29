"""
AppConfig — единственный источник истины для настроек приложения.

Секреты (api_hash, session) НЕ хранятся в конфиге — только в Keyring.
Конфиг-файл содержит только несекретные настройки.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


CONFIG_DIR = Path(os.path.expanduser("~/.tg_exporter"))
CONFIG_FILE = CONFIG_DIR / "config.json"

WHISPER_MODELS = ("tiny", "base", "small", "medium", "large", "large-v2", "large-v3")
TRANSCRIPTION_PROVIDERS = ("local", "deepgram")
TRANSCRIPTION_LANGUAGES = ("multi", "ru", "en", "de", "fr", "es", "zh", "ja")
DATE_FORMATS = ("DD.MM.YYYY", "YYYY-MM-DD", "MM/DD/YYYY")

# Масштаб интерфейса — множитель CTk widget-scaling. 1.0 = дефолт CTk;
# 0.9 — наш базовый «поджатый» вид. Пользователь меняет в Настройках.
UI_SCALE_DEFAULT = 0.9
UI_SCALE_MIN = 0.7
UI_SCALE_MAX = 1.6
# Пресеты для выпадашки. Намеренно ⊂ [MIN, MAX]: clamp шире набора пресетов,
# чтобы терпеть значения из вручную отредактированного config.json.
UI_SCALES = (0.8, 0.9, 1.0, 1.1, 1.25, 1.5)


def clamp_ui_scale(value) -> float:
    """Приводит масштаб к допустимому диапазону; нечисловой мусор → дефолт."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return UI_SCALE_DEFAULT
    return max(UI_SCALE_MIN, min(UI_SCALE_MAX, v))


class ConfigValidationError(ValueError):
    pass


@dataclass
class MarkdownSettings:
    words_per_file: int = 50_000
    date_format: str = "DD.MM.YYYY"
    include_timestamps: bool = True
    include_author: bool = True
    include_replies: bool = True
    include_reactions: bool = False
    include_polls: bool = False
    include_forwarded: bool = True
    plain_text: bool = True

    def validate(self) -> None:
        if self.words_per_file < 1000:
            raise ConfigValidationError("words_per_file must be >= 1000")
        if self.date_format not in DATE_FORMATS:
            raise ConfigValidationError(
                f"date_format must be one of {DATE_FORMATS}, got {self.date_format!r}"
            )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MarkdownSettings":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class AppConfig:
    # Telegram API — только несекретная часть
    api_id: str = ""

    # Транскрипция
    transcription_provider: str = "local"
    transcription_language: str = "multi"
    local_whisper_model: str = "base"
    deepgram_api_key: str = ""

    # Интерфейс
    include_private_chats: bool = False
    ui_scale: float = UI_SCALE_DEFAULT

    # Настройки Markdown
    markdown: MarkdownSettings = field(default_factory=MarkdownSettings)

    # ---- Валидация ----

    def validate(self) -> None:
        if self.api_id:
            digits = "".join(c for c in self.api_id if c.isdigit())
            if not digits:
                raise ConfigValidationError("api_id must contain digits")

        if self.transcription_provider not in TRANSCRIPTION_PROVIDERS:
            raise ConfigValidationError(
                f"transcription_provider must be one of {TRANSCRIPTION_PROVIDERS}"
            )

        if self.transcription_language not in TRANSCRIPTION_LANGUAGES:
            raise ConfigValidationError(
                f"transcription_language must be one of {TRANSCRIPTION_LANGUAGES}"
            )

        if self.local_whisper_model not in WHISPER_MODELS:
            raise ConfigValidationError(
                f"local_whisper_model must be one of {WHISPER_MODELS}"
            )

        if not (UI_SCALE_MIN <= self.ui_scale <= UI_SCALE_MAX):
            raise ConfigValidationError(
                f"ui_scale must be within [{UI_SCALE_MIN}, {UI_SCALE_MAX}], got {self.ui_scale!r}"
            )

        self.markdown.validate()

    @property
    def api_id_int(self) -> Optional[int]:
        """Возвращает api_id как int, или None если не задан / невалиден."""
        digits = "".join(c for c in self.api_id if c.isdigit())
        return int(digits) if digits else None

    # ---- Сериализация ----

    def to_dict(self) -> dict:
        """Только несекретные поля — safe для записи в файл."""
        return {
            "api_id": self.api_id,
            "transcription_provider": self.transcription_provider,
            "transcription_language": self.transcription_language,
            "local_whisper_model": self.local_whisper_model,
            # deepgram_api_key намеренно исключён — хранится в Keyring
            "include_private_chats": self.include_private_chats,
            "ui_scale": self.ui_scale,
            "markdown": self.markdown.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppConfig":
        md_data = data.pop("markdown", {})
        known = {f for f in cls.__dataclass_fields__ if f != "markdown"}
        # deepgram_api_key не читаем из файла — только из Keyring
        filtered = {k: v for k, v in data.items() if k in known and k != "deepgram_api_key"}
        obj = cls(**filtered)
        if md_data:
            obj.markdown = MarkdownSettings.from_dict(md_data)
        return obj

    # ---- Персистентность ----

    @classmethod
    def load(cls) -> "AppConfig":
        """Загружает конфиг из файла. Возвращает дефолтный если файл не существует.

        При повреждении файла делает бэкап config.json.broken.{timestamp},
        чтобы пользователь мог восстановить настройки вручную, а не получал
        молчаливый сброс.
        """
        if not CONFIG_FILE.exists():
            return cls()
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            raw.pop("api_hash", None)
            raw.pop("session", None)
            return cls.from_dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            try:
                import datetime as _dt
                ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = CONFIG_FILE.with_suffix(f".broken.{ts}.json")
                CONFIG_FILE.rename(backup)
            except OSError:
                pass
            return cls()

    def save(self) -> None:
        """Сохраняет только несекретные поля. Атомарно + права 0o600."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, CONFIG_FILE)
        _secure_permissions(CONFIG_FILE)

    def with_api_id(self, api_id: str) -> "AppConfig":
        """Возвращает новый экземпляр с обновлённым api_id."""
        digits = "".join(c for c in (api_id or "") if c.isdigit())
        import dataclasses
        return dataclasses.replace(self, api_id=digits)


def _secure_permissions(path: Path) -> None:
    import platform
    if platform.system() == "Windows":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
