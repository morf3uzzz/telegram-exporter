"""
ExportProgressWidget — полоса прогресса с ETA, статусом и кнопкой отмены.

Показывает:
  - Название чата
  - count / total (или «X сообщений» бегущим счётчиком, если total неизвестен)
  - ETA в секундах / минутах
  - Статусную строку (например "Транскрипция...")
  - Кнопку отмены
"""

from __future__ import annotations

from typing import Callable, Optional
import customtkinter as ctk

from ..theme import C, RADIUS, WIDGET, SPACING, font


class ExportProgressWidget(ctk.CTkFrame):
    """
    Компактная полоса прогресса экспорта.

    Встраивается inline в ChatsPage или в ExportModal.
    """

    def __init__(self, master, on_cancel: Callable, **kwargs) -> None:
        kwargs.setdefault("fg_color", C["card"])
        kwargs.setdefault("corner_radius", RADIUS["lg"])
        super().__init__(master, **kwargs)
        self._on_cancel = on_cancel
        self._indeterminate = False  # режим бегущего бара (total неизвестен)

        # Строка 1: название + cancel
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=SPACING["md"], pady=(SPACING["sm"], 0))

        self._chat_lbl = ctk.CTkLabel(
            top, text="", font=font(13, "bold"), text_color=C["text"], anchor="w",
        )
        self._chat_lbl.pack(side="left", fill="x", expand=True)

        self._cancel_btn = ctk.CTkButton(
            top,
            text="✕",
            width=24,
            height=24,
            corner_radius=RADIUS["sm"],
            fg_color=C["card_hover"],
            hover_color=C["error"],
            text_color=C["text_sec"],
            font=font(11),
            command=self._on_cancel,
        )
        self._cancel_btn.pack(side="right")

        # Строка 2: прогресс-бар
        self._bar = ctk.CTkProgressBar(
            self,
            height=WIDGET["progress_h"],
            corner_radius=3,
            fg_color=C["border"],
            progress_color=C["primary"],
        )
        self._bar.pack(fill="x", padx=SPACING["md"], pady=(SPACING["xs"], 0))
        self._bar.set(0)

        # Строка 3: счётчик + ETA (в одну строку)
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=SPACING["md"], pady=(SPACING["xs"], 0))

        self._count_lbl = ctk.CTkLabel(
            bottom, text="", font=font(11), text_color=C["text_sec"], anchor="w",
        )
        self._count_lbl.pack(side="left")

        self._eta_lbl = ctk.CTkLabel(
            bottom, text="", font=font(11), text_color=C["text_dim"], anchor="e",
        )
        self._eta_lbl.pack(side="right")

        # Строка 4: статус (отдельно, на всю ширину — не конкурирует со счётчиком)
        self._status_lbl = ctk.CTkLabel(
            self, text="", font=font(11), text_color=C["text_dim"],
            anchor="w", justify="left", wraplength=0,
        )
        self._status_lbl.pack(
            fill="x", padx=SPACING["md"], pady=(SPACING["xs"], SPACING["sm"]),
        )

    # ---- Public API ----

    def start(self, chat_name: str, total: Optional[int]) -> None:
        """Показывает виджет и инициализирует начальное состояние."""
        self._chat_lbl.configure(text=chat_name)
        if total and total > 0:
            self._set_indeterminate(False)
            self._bar.set(0)
        else:
            # total неизвестен (напр. топик с фильтром по дате) — бегущий бар.
            self._set_indeterminate(True)
        self._count_lbl.configure(text=format_count_label(0, total))
        self._eta_lbl.configure(text="")
        self._status_lbl.configure(text="")
        # finish() прячет кнопку отмены — восстанавливаем для нового запуска.
        if not self._cancel_btn.winfo_ismapped():
            self._cancel_btn.pack(side="right")
        self._cancel_btn.configure(state="normal")
        self.pack_or_show()

    def update(
        self,
        count: int,
        total: Optional[int],
        eta_seconds: Optional[float] = None,
    ) -> None:
        """Обновляет счётчик, прогресс-бар и ETA."""
        if total and total > 0:
            self._set_indeterminate(False)
            ratio = min(count / total, 1.0)
            self._bar.set(ratio)
        else:
            # Неопределённый прогресс — анимированный «бегущий» бар.
            self._set_indeterminate(True)
        self._count_lbl.configure(text=format_count_label(count, total))

        if eta_seconds is not None and eta_seconds > 0:
            self._eta_lbl.configure(text=f"≈ {_format_eta(eta_seconds)}")
        else:
            self._eta_lbl.configure(text="")

    def _set_indeterminate(self, on: bool) -> None:
        """Переключает бар между обычным и «бегущим» режимом (идемпотентно)."""
        if on and not self._indeterminate:
            try:
                self._bar.set(0)  # сбрасываем прежнюю заливку перед анимацией
                self._bar.configure(mode="indeterminate")
                self._bar.start()
            except Exception:
                pass
            self._indeterminate = True
        elif not on and self._indeterminate:
            try:
                self._bar.stop()
                self._bar.configure(mode="determinate")
            except Exception:
                pass
            self._indeterminate = False

    def set_status(self, text: str) -> None:
        """Устанавливает текст статуса (напр. 'Транскрипция...')."""
        self._status_lbl.configure(text=text)

    def set_download_progress(self, ratio: float, text: str = "") -> None:
        """
        Показывает полосу как прогресс-бар скачивания модели (0..1).
        Используется в фазе preload — когда реальный экспорт ещё не начался.
        """
        self._set_indeterminate(False)
        r = max(0.0, min(1.0, ratio))
        self._bar.set(r)
        pct = int(r * 100)
        self._count_lbl.configure(text=f"{pct}%")
        self._eta_lbl.configure(text="")
        if text:
            self._status_lbl.configure(text=text)

    def finish(self) -> None:
        """Заполняет бар до 100%, чистит метки и убирает кнопку отмены."""
        self._set_indeterminate(False)
        self._bar.set(1.0)
        # Прячем кнопку, чтобы пользователь не пытался отменить уже завершённый
        # экспорт — disabled-вариант создавал ощущение «крестик не реагирует».
        try:
            self._cancel_btn.pack_forget()
        except Exception:
            pass
        # Если до finish() в _count_lbl было «100%» от скачивания модели
        # (set_download_progress) и не было ни одного export_progress —
        # счётчик остаётся со старым текстом. Чистим явно.
        self._count_lbl.configure(text="")
        self._eta_lbl.configure(text="")
        self._status_lbl.configure(text="")

    def hide(self) -> None:
        self._set_indeterminate(False)
        self.pack_forget()

    def pack_or_show(self) -> None:
        if not self.winfo_ismapped():
            self.pack(fill="x", padx=SPACING["xl"], pady=(0, SPACING["sm"]))


# ---- Helpers ----

def format_count_label(count: int, total: Optional[int]) -> str:
    """Текст счётчика: «X / Y» если знаменатель известен, иначе «X сообщений».

    Для топиков с фильтром по дате точного total у Telegram дёшево нет —
    тогда показываем честный бегущий счётчик без знаменателя.
    """
    if total and total > 0:
        return f"{count:,} / {total:,}"
    return f"{count:,} {_plural_messages(count)}"


def _plural_messages(n: int) -> str:
    """Русское склонение слова «сообщение» по числу."""
    n = abs(int(n))
    if 10 <= n % 100 <= 20:
        return "сообщений"
    tail = n % 10
    if tail == 1:
        return "сообщение"
    if 2 <= tail <= 4:
        return "сообщения"
    return "сообщений"


def _format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)} с"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m < 60:
        return f"{m} мин {s} с" if s else f"{m} мин"
    h = m // 60
    m2 = m % 60
    return f"{h} ч {m2} мин" if m2 else f"{h} ч"
