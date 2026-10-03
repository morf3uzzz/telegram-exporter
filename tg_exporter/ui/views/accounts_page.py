"""AccountsPage — управление аккаунтами карточками (вместо popup «Аккаунт ▾»)."""

from __future__ import annotations

import tkinter.messagebox as mb
from typing import TYPE_CHECKING
import customtkinter as ctk

from ..theme import C, RADIUS, SPACING, font, font_display
from ..components.button import AppButton
from ..modal_utils import setup_smooth_scroll

if TYPE_CHECKING:
    from ..app import App


def _mask_phone(phone: str) -> str:
    """
    Маскирует номер для отображения: +7 905 ***-**-67.
    Видны код страны+оператора и последние 2 цифры — понять «что за аккаунт»,
    но не светить весь номер. Country-agnostic (работает для любой страны).
    """
    p = (phone or "").strip()
    digits = "".join(c for c in p if c.isdigit())
    if len(digits) < 7:
        return p  # слишком короткий — как есть
    plus = "+" if p.startswith("+") else ""
    head = digits[:4]
    tail = digits[-2:]
    return f"{plus}{head[:1]} {head[1:4]} ***-**-{tail}"


class AccountsPage(ctk.CTkFrame):
    def __init__(self, master, app: "App") -> None:
        super().__init__(master, fg_color="transparent")
        self._app = app
        self._build()
        self.after(100, lambda: setup_smooth_scroll(self, self._scroll))

    def refresh(self) -> None:
        for w in self._scroll.winfo_children():
            w.destroy()
        self._render_cards()

    def _build(self) -> None:
        pad = SPACING["2xl"]
        ctk.CTkLabel(self, text="Аккаунты", font=font_display(20, "bold"),
                     text_color=C["text"]).pack(anchor="w", padx=pad, pady=(pad, SPACING["lg"]))
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=C["bg"])
        self._scroll.pack(fill="both", expand=True, padx=pad, pady=(0, SPACING["md"]))
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=pad, pady=(0, pad))
        AppButton(actions, text="+ Добавить аккаунт", variant="primary",
                  command=self._app.show_add_account).pack(side="left")
        # Вход по QR — на экране входа (переключатель «По номеру / QR»),
        # для первичного логина. Здесь, при добавлении аккаунта, его нет (спек §13).
        self._render_cards()

    def _render_cards(self) -> None:
        profiles = self._app.profiles()
        active = self._app.active_profile()
        active_phone = active.phone if active else None
        if not profiles:
            ctk.CTkLabel(self._scroll, text="Аккаунтов пока нет. Добавьте первый.",
                         font=font(13), text_color=C["text_sec"]).pack(anchor="w", pady=SPACING["md"])
            return
        for p in profiles:
            self._card(p, p.phone == active_phone, self._app._profiles.has_session(p))

    def _card(self, profile, is_active: bool, has_session: bool) -> None:
        card = ctk.CTkFrame(self._scroll, fg_color=C["surface"], corner_radius=RADIUS["lg"],
                            border_width=1, border_color=C["primary"] if is_active else C["border"])
        card.pack(fill="x", pady=(0, SPACING["sm"]))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=SPACING["lg"], pady=SPACING["md"])

        # Слева — имя + маскированный номер (понять, что за аккаунт).
        meta = ctk.CTkFrame(inner, fg_color="transparent")
        meta.pack(side="left", fill="x", expand=True)
        name = (profile.display_name or profile.phone or "").strip() or profile.phone
        title = name + ("   ● активный" if is_active else "")
        ctk.CTkLabel(meta, text=title, font=font(14, "bold"),
                     text_color=C["text"], anchor="w").pack(fill="x")
        masked = _mask_phone(profile.phone)
        if masked and masked != name:
            ctk.CTkLabel(meta, text=masked, font=font(11),
                         text_color=C["text_sec"], anchor="w").pack(fill="x")
        if not has_session:
            # Сессии нет (отозвана или оказалась от другого аккаунта) —
            # переключиться нельзя, только войти заново.
            ctk.CTkLabel(meta, text="⚠ нужен вход", font=font(11),
                         text_color=C["warning"], anchor="w").pack(fill="x")

        proxy_mark = " ✓" if (self._app._profiles.load_proxy(profile) or "").strip() else ""
        AppButton(inner, text="Удалить", variant="ghost", size="sm",
                  command=lambda ph=profile.phone, act=is_active: self._remove(ph, act)).pack(side="right")
        AppButton(inner, text=f"🌐 Прокси{proxy_mark}", variant="secondary", size="sm",
                  command=lambda ph=profile.phone: self._app.show_proxy_settings(ph)).pack(side="right", padx=(0, SPACING["sm"]))
        if not has_session:
            AppButton(inner, text="Войти", variant="primary", size="sm",
                      command=lambda ph=profile.phone: self._app.show_add_account(ph)).pack(side="right", padx=(0, SPACING["sm"]))
        elif not is_active:
            AppButton(inner, text="Сделать активным", variant="secondary", size="sm",
                      command=lambda ph=profile.phone: self._app.switch_profile(ph)).pack(side="right", padx=(0, SPACING["sm"]))

    def _remove(self, phone: str, is_active: bool) -> None:
        if is_active:
            question = (
                f"Выйти из аккаунта {_mask_phone(phone)} и убрать его из списка?\n\n"
                "Сессия на этом устройстве будет завершена."
            )
        else:
            question = f"Удалить аккаунт {_mask_phone(phone)} из списка вместе с его сессией?"
        if not mb.askyesno("Удалить аккаунт", question):
            return
        # Список перерисуется по событию profiles_changed / profile_switched.
        self._app.remove_profile(phone)
