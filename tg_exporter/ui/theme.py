"""
Единая система дизайна.

Все цвета, шрифты, отступы — строго отсюда.
Нигде в UI нет хардкода "#2563EB" или font=("SF Pro Text", 13).
"""

from __future__ import annotations

import platform
import customtkinter as ctk

from ..models.config import UI_SCALE_DEFAULT

# ---- Режим темы ----

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

# Дефолтный масштаб виджетов до загрузки конфига (App затем применяет
# config.ui_scale). Берём из единой точки правды UI_SCALE_DEFAULT, чтобы не
# дублировать магическое число. После DPI-фикса (System-DPI-aware в main.py —
# чинит сворачивание окна на Win11) шрифты следуют масштабу Windows; этот
# множитель их поджимает, DPI-осознанность при этом не трогаем.
ctk.set_widget_scaling(UI_SCALE_DEFAULT)


# ---- Шрифты по платформе ----

_OS = platform.system()

if _OS == "Windows":
    FONT_UI      = "Segoe UI"
    FONT_DISPLAY = "Segoe UI"
elif _OS == "Darwin":
    FONT_UI      = "SF Pro Text"
    FONT_DISPLAY = "SF Pro Display"
else:
    FONT_UI      = "Helvetica"
    FONT_DISPLAY = "Helvetica"


def font(size: int = 13, weight: str = "normal") -> tuple:
    return (FONT_UI, size, weight) if weight != "normal" else (FONT_UI, size)

def font_display(size: int = 20, weight: str = "bold") -> tuple:
    return (FONT_DISPLAY, size, weight) if weight != "normal" else (FONT_DISPLAY, size)


def scaled_font_px(scale: float) -> int:
    """Размер шрифта для нативных tk/ttk-виджетов (напр. Treeview) в пикселях.

    Нативные виджеты НЕ следуют за CTk widget-scaling, поэтому размер считаем
    сами под текущий масштаб окна. Отрицательное значение в Tk = пиксели (как
    внутри CTk) — это и даёт совпадение со скейлингом CTk-виджетов. Нижняя
    граница 8px — чтобы при крошечном масштабе текст не исчез.
    """
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    return -max(8, int(round(14 * s)))


# ---- Цвета (light, dark) ----
# Каждый цвет — кортеж (light, dark), CTk подставляет нужный автоматически.

C = {
    # Фон
    "bg":           ("#FFFFFF", "#1A1A1A"),
    "surface":      ("#F5F6F8", "#242424"),
    "card":         ("#F0F2F5", "#2C2C2C"),
    "card_hover":   ("#E8EBF0", "#333333"),

    # Текст
    "text":         ("#0F1117", "#F1F3F5"),
    "text_sec":     ("#6B7280", "#9CA3AF"),
    "text_dim":     ("#9CA3AF", "#6B7280"),

    # Акценты
    "primary":      ("#2563EB", "#3B82F6"),
    "primary_h":    ("#1D4ED8", "#2563EB"),
    "primary_text": ("#FFFFFF", "#FFFFFF"),

    # Семантика
    "success":      ("#10B981", "#34D399"),
    "warning":      ("#F59E0B", "#FBBF24"),
    "error":        ("#EF4444", "#F87171"),

    # Границы
    "border":       ("#E2E6EC", "#374151"),
    "border_focus": ("#2563EB", "#3B82F6"),
}


def pick(key: str) -> str:
    """Возвращает текущий цвет для активного режима (light/dark)."""
    pair = C[key]
    return pair[0] if ctk.get_appearance_mode() == "Light" else pair[1]


# ---- Радиусы и отступы ----

RADIUS = {
    "sm":  6,
    "md":  8,
    "lg":  12,
    "xl":  16,
    "2xl": 24,
}

SPACING = {
    "xs":  4,
    "sm":  8,
    "md":  12,
    "lg":  16,
    "xl":  20,
    "2xl": 28,
    "3xl": 40,
}

# ---- Размеры виджетов ----

WIDGET = {
    "btn_h":       38,
    "btn_h_sm":    30,
    "entry_h":     38,
    "entry_h_sm":  30,
    "progress_h":  6,
}

# ---- Окно приложения ----

WINDOW = {
    "title":       "Telegram Exporter",
    "size":        "1040x720",
    "min_size":    (860, 600),
}

# Ширина бокового меню
SIDEBAR_W = 200
