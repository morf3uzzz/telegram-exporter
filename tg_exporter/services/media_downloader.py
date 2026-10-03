"""
MediaDownloader — скачивание медиафайлов и подготовка аудио к транскрипции.

Поддерживает CancellationToken — проверяет отмену перед каждым скачиванием.
Не знает про UI и очереди.
"""

from __future__ import annotations

import asyncio
import datetime
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from ..models.message import MediaType
from ..utils.cancellation import CancellationToken
from ..utils.logger import logger


_TRANSCRIBE_MAX_DURATION_SEC = 15 * 60  # 15 минут


@dataclass
class MediaDirs:
    """Пути к поддиректориям медиа внутри export_dir."""
    photo: str
    video: str
    audio: str
    documents: str

    @classmethod
    def create(cls, base: str) -> "MediaDirs":
        dirs = cls(
            photo=os.path.join(base, "photo"),
            video=os.path.join(base, "video"),
            audio=os.path.join(base, "audio"),
            documents=os.path.join(base, "documents"),
        )
        for d in (dirs.photo, dirs.video, dirs.audio, dirs.documents):
            os.makedirs(d, exist_ok=True)
        return dirs

    def for_media_type(self, media_type: Optional[MediaType]) -> Optional[str]:
        if media_type == MediaType.PHOTO:
            return self.photo
        if media_type in (MediaType.VIDEO, MediaType.VIDEO_NOTE, MediaType.ANIMATION):
            return self.video
        if media_type in (MediaType.VOICE, MediaType.AUDIO):
            return self.audio
        if media_type == MediaType.DOCUMENT:
            return self.documents
        return None


@dataclass
class AudioPrepResult:
    """Результат подготовки аудио к транскрипции."""
    audio_data: bytes
    content_type: str
    saved_path: Optional[str] = None  # путь куда сохранено (для video_note)


class MediaDownloader:
    """
    Скачивает медиафайлы и подготавливает аудио к транскрипции.

    Все методы вызываются из фонового потока с активным asyncio event loop
    (требование Telethon).
    """

    def download(
        self,
        msg,  # Telethon Message
        media_dirs: MediaDirs,
        token: Optional[CancellationToken] = None,
    ) -> Optional[str]:
        """
        Скачивает медиа из сообщения в нужную поддиректорию под именем
        `media_file_stem(msg)` — по нему файл однозначно связывается с сообщением.

        Returns:
            Путь к скачанному файлу, или None если медиа нет / произошла ошибка.
        """
        if token and token.is_cancelled:
            return None

        # Стикеры не скачиваем
        if getattr(msg, "sticker", None):
            return None

        if getattr(msg, "video_note", None):
            target = media_dirs.video
        elif getattr(msg, "photo", None):
            target = media_dirs.photo
        elif getattr(msg, "video", None):
            target = media_dirs.video
        elif getattr(msg, "voice", None) or getattr(msg, "audio", None):
            target = media_dirs.audio
        elif getattr(msg, "document", None):
            target = media_dirs.documents
        else:
            return None

        try:
            result = msg.download_media(
                file=os.path.join(target, media_file_stem(msg)),
                progress_callback=_make_progress_cb(token),
            )
            if asyncio.iscoroutine(result):
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(result)
                finally:
                    loop.close()
            path = result
            if token and token.is_cancelled:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                return None
            return path
        except _CancelledDuringDownload:
            return None
        except Exception as exc:
            from ..utils.logger import logger
            logger.error(f"download_media failed msg_id={getattr(msg,'id','?')}: {exc}")
            return None

    def prepare_audio(
        self,
        msg,  # Telethon Message
        token: Optional[CancellationToken] = None,
    ) -> Optional[AudioPrepResult]:
        """
        Скачивает и подготавливает аудио из голосового сообщения или видеокружка.

        Для voice: возвращает ogg байты.
        Для video_note: конвертирует в WAV через ffmpeg.

        Returns:
            AudioPrepResult или None если не применимо / отменено.
        """
        if token and token.is_cancelled:
            return None

        voice = getattr(msg, "voice", None)
        video_note = getattr(msg, "video_note", None)

        if not voice and not video_note:
            return None

        # Проверка длительности
        duration = getattr(voice or video_note, "duration", 0) or 0
        if duration > _TRANSCRIBE_MAX_DURATION_SEC:
            raise MediaTooLongError(
                f"Аудио слишком длинное ({duration // 60} мин), "
                f"лимит {_TRANSCRIBE_MAX_DURATION_SEC // 60} мин"
            )

        if voice:
            return self._prepare_voice(msg, token)
        else:
            return self._prepare_video_note(msg, token)

    # ---- Internal ----

    def _prepare_voice(
        self, msg, token: Optional[CancellationToken]
    ) -> Optional[AudioPrepResult]:
        tmp_path = None
        msg_id = getattr(msg, "id", "?")
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".ogg", prefix="tg_voice_"
            ) as tmp:
                tmp_path = tmp.name
            t0 = time.monotonic()
            logger.info(f"voice: download start msg_id={msg_id}")
            _run_download(msg.download_media(file=tmp_path))
            logger.info(
                f"voice: download done msg_id={msg_id} in {time.monotonic() - t0:.1f}s"
            )
            if token and token.is_cancelled:
                return None
            with open(tmp_path, "rb") as f:
                data = f.read()
            return AudioPrepResult(audio_data=data, content_type="audio/ogg")
        except Exception as exc:
            logger.error(f"voice: prepare failed msg_id={msg_id}: {exc}", exc=exc)
            return None
        finally:
            _try_remove(tmp_path)

    def _prepare_video_note(
        self, msg, token: Optional[CancellationToken]
    ) -> Optional[AudioPrepResult]:
        ffmpeg = _get_ffmpeg()
        if not ffmpeg:
            logger.error("video_note: ffmpeg not found")
            raise MediaProcessingError("Для видеокружков нужен ffmpeg")

        video_tmp = None
        wav_tmp = None
        msg_id = getattr(msg, "id", "?")
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".mp4", prefix="tg_vidnote_"
            ) as tmp:
                video_tmp = tmp.name
            t0 = time.monotonic()
            logger.info(f"video_note: download start msg_id={msg_id}")
            _run_download(msg.download_media(file=video_tmp))
            logger.info(
                f"video_note: download done msg_id={msg_id} in {time.monotonic() - t0:.1f}s"
            )
            if token and token.is_cancelled:
                return None

            t1 = time.monotonic()
            logger.info(f"video_note: ffmpeg extract start msg_id={msg_id}")
            wav_tmp = _extract_audio_to_wav(ffmpeg, video_tmp)
            logger.info(
                f"video_note: ffmpeg extract done msg_id={msg_id} in "
                f"{time.monotonic() - t1:.1f}s, success={wav_tmp is not None}"
            )
            if not wav_tmp:
                raise MediaProcessingError("Не удалось извлечь звук из видеокружка")

            if token and token.is_cancelled:
                return None

            with open(wav_tmp, "rb") as f:
                data = f.read()
            # Сохраняем WAV — он пойдёт и в media/audio и в транскрипцию
            saved_path = wav_tmp
            wav_tmp = None  # не удаляем в finally
            return AudioPrepResult(
                audio_data=data,
                content_type="audio/wav",
                saved_path=saved_path,
            )
        except (MediaProcessingError, MediaTooLongError):
            raise
        except Exception as exc:
            raise MediaProcessingError(str(exc)[:150]) from exc
        finally:
            _try_remove(video_tmp)
            if wav_tmp:
                _try_remove(wav_tmp)


class MediaTooLongError(Exception):
    """Аудио длиннее допустимого лимита."""


class MediaProcessingError(Exception):
    """Не удалось обработать медиафайл."""


# ---- Helpers ----

_UNSAFE_NAME_CHARS = re.compile(r"[^\w-]+")
_PLAIN_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,8}")
_ORIGINAL_NAME_MAX = 60


def media_file_stem(msg) -> str:
    """
    Имя файла медиа (без каталога): `{дата}_{время}_msg{id}`, для документов —
    плюс исходное имя: `2026-01-02_03-04-05_msg1234_Договор.pdf`.

    Дата — как в сообщении (UTC, то же время печатает Markdown), id — номер
    сообщения в чате: по имени видно, к какому сообщению и дню относится файл.

    Расширение дописывает Telethon, но только если в имени нет точки
    (`os.path.splitext`), поэтому в основе точек быть не должно.
    """
    stem = f"msg{getattr(msg, 'id', 0) or 0}"
    date = getattr(msg, "date", None)
    if isinstance(date, datetime.datetime):
        stem = f"{date:%Y-%m-%d_%H-%M-%S}_{stem}"

    original = getattr(getattr(msg, "file", None), "name", None)
    if not isinstance(original, str) or not original.strip():
        return stem
    # Имя приходит от отправителя: берём только последний сегмент пути и
    # оставляем буквы/цифры/«_»/«-» — без пробелов, точек и разделителей.
    base, ext = os.path.splitext(original.replace("\\", "/").rsplit("/", 1)[-1])
    base = _UNSAFE_NAME_CHARS.sub("_", base).strip("_")[:_ORIGINAL_NAME_MAX].rstrip("_")
    if base:
        stem = f"{stem}_{base}"
    if _PLAIN_EXTENSION.fullmatch(ext):
        stem += ext
    return stem


class _CancelledDuringDownload(Exception):
    """Внутреннее исключение — отмена во время progress_callback."""


def _make_progress_cb(token: Optional[CancellationToken]):
    """Возвращает progress_callback для download_media, который проверяет токен."""
    if token is None:
        return None

    def _cb(_received: int, _total: int) -> None:
        if token.is_cancelled:
            raise _CancelledDuringDownload()

    return _cb


def _run_download(result) -> None:
    """Запускает download_media корутину если telethon.sync вернул её вместо результата."""
    if asyncio.iscoroutine(result):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(result)
        finally:
            loop.close()



def _get_ffmpeg() -> Optional[str]:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    return shutil.which("ffmpeg")


def _extract_audio_to_wav(ffmpeg: str, video_path: str) -> Optional[str]:
    try:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="tg_audio_")
        os.close(fd)
        subprocess.run(
            [
                ffmpeg, "-y", "-i", video_path,
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                wav_path,
            ],
            capture_output=True,
            timeout=600,
            check=True,
        )
        return wav_path
    except Exception:
        return None


def _try_remove(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
