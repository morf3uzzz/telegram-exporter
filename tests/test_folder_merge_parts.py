"""
Плоские режимы экспорта папки («Один .md на чат» / «Один .md на папку»)
не должны терять части разбитого чата.

Регрессия: экспорт чата бьётся на части по words_per_file (50 000 слов по
умолчанию). При переносе в плоский режим все части клались под одно имя
«{чат}.md», а коллизия разрешалась единственным суффиксом «_2» — третья и
последующие части затирали вторую.
"""

from __future__ import annotations

import os

from tg_exporter.ui.folder_merge import unique_md_dest


def test_three_parts_produce_three_files(tmp_path):
    base = str(tmp_path)
    dests = []
    for _ in range(3):
        d = unique_md_dest(base, "Мой чат")
        open(d, "w", encoding="utf-8").write("x")
        dests.append(d)
    assert len(set(dests)) == 3
    assert len(os.listdir(base)) == 3


def test_first_part_keeps_plain_name(tmp_path):
    d = unique_md_dest(str(tmp_path), "Чат")
    assert os.path.basename(d) == "Чат.md"


def test_unsafe_chat_name_sanitized(tmp_path):
    d = unique_md_dest(str(tmp_path), "../../etc/passwd")
    name = os.path.basename(d)
    assert "/" not in name and ".." not in name


def test_many_parts_all_unique(tmp_path):
    base = str(tmp_path)
    for _ in range(12):
        open(unique_md_dest(base, "Чат"), "w", encoding="utf-8").write("x")
    assert len(os.listdir(base)) == 12


def test_parts_merge_in_numeric_order():
    from tg_exporter.ui.folder_merge import merge_sort_key
    files = ["/x/Чат.md", "/x/Чат_10.md", "/x/Чат_2.md", "/x/Чат_3.md"]
    got = [os.path.basename(p) for p in sorted(files, key=merge_sort_key)]
    assert got == ["Чат.md", "Чат_2.md", "Чат_3.md", "Чат_10.md"]


def test_different_chats_still_grouped():
    """Части одного чата держатся вместе, чаты — по алфавиту."""
    from tg_exporter.ui.folder_merge import merge_sort_key
    files = ["/x/Б.md", "/x/А_2.md", "/x/А.md"]
    got = [os.path.basename(p) for p in sorted(files, key=merge_sort_key)]
    assert got == ["А.md", "А_2.md", "Б.md"]
