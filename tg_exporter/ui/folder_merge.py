"""
Помощники плоских режимов экспорта папки («Один .md на чат» / «на папку»).

Чистые функции без Tk и сети — чтобы поведение при коллизиях имён было
покрыто тестами.
"""

from __future__ import annotations

import os

from ..exporters.base import sanitize_filename


def unique_md_dest(base_dir: str, chat_name: str) -> str:
    """
    Свободный путь «{base_dir}/{чат}.md», при занятости — «_2», «_3», ….

    Экспорт чата бьётся на части по words_per_file, и в плоском режиме все
    части переносятся в одну директорию. Единственный суффикс «_2» терял
    третью и последующие части, поэтому счётчик растёт до свободного имени.
    """
    safe = sanitize_filename(chat_name)
    dest = os.path.join(base_dir, f"{safe}.md")
    if not os.path.exists(dest):
        return dest
    n = 2
    while True:
        candidate = os.path.join(base_dir, f"{safe}_{n}.md")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def merge_sort_key(path: str):
    """
    Порядок склейки «Один .md на папку»: части одного чата идут по номеру.

    Обычная сортировка строк ставит «Чат_10.md» перед «Чат_2.md», из-за чего
    сообщения в общем файле оказывались вперемешку.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    head, sep, tail = stem.rpartition("_")
    if sep and tail.isdigit():
        return (head, int(tail))
    return (stem, 1)
