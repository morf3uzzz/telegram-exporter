"""
Самописный генератор QR-кодов (byte-mode, уровень коррекции L, версии 1–10).

Ноль внешних зависимостей — для отрисовки QR-кода входа без библиотек
qrcode/Pillow (политика проекта: permissive-only, минимум зависимостей).

Покрывает ровно то, что нужно для QR-логина Telegram (`tg://login?token=…`,
~45–65 символов → версии 3–4): byte-mode, EC-уровень L. NumERIC/alphanumeric
режимы и уровни M/Q/H не реализованы (YAGNI).

`qr_matrix(data) -> list[list[bool]]` — True = чёрный модуль.

Корректность проверяется бит-в-бит против эталона библиотеки `qrcode`
(tests/fixtures/qr_reference.json, tests/test_qrcode_gen.py).
"""

from __future__ import annotations

from typing import List


# --------------------------------------------------------------------------
# Галуа-поле GF(256) для Reed-Solomon (примитивный многочлен 0x11D).
# --------------------------------------------------------------------------

_EXP = [0] * 512
_LOG = [0] * 256


def _init_tables() -> None:
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_tables()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator_poly(nsym: int) -> List[int]:
    """Генераторный многочлен Рида-Соломона для nsym проверочных байт."""
    g = [1]
    for i in range(nsym):
        # умножаем g на (x - α^i)
        g2 = [0] * (len(g) + 1)
        for j in range(len(g)):
            g2[j] ^= g[j]
            g2[j + 1] ^= _gf_mul(g[j], _EXP[i])
        g = g2
    return g


def _rs_encode(data: List[int], nsym: int) -> List[int]:
    """Возвращает nsym проверочных байт для data."""
    gen = _rs_generator_poly(nsym)
    res = [0] * nsym
    for b in data:
        factor = b ^ res[0]
        res.pop(0)
        res.append(0)
        if factor != 0:
            lf = _LOG[factor]
            for i in range(nsym):
                if gen[i + 1]:
                    res[i] ^= _EXP[lf + _LOG[gen[i + 1]]]
    return res


# --------------------------------------------------------------------------
# Таблицы по версиям (только EC-уровень L). Версии 1–10.
# --------------------------------------------------------------------------

# Число проверочных (EC) кодовых слов на блок и число блоков (уровень L).
# Формат: версия -> (ec_per_block, num_blocks_group1, data_per_block_g1,
#                    num_blocks_group2, data_per_block_g2)
# Для версий 1–10 уровня L группа 2 пустая (кроме где указано).
_EC_TABLE_L = {
    1:  (7,  1, 19, 0, 0),
    2:  (10, 1, 34, 0, 0),
    3:  (15, 1, 55, 0, 0),
    4:  (20, 1, 80, 0, 0),
    5:  (26, 1, 108, 0, 0),
    6:  (18, 2, 68, 0, 0),
    7:  (20, 2, 78, 0, 0),
    8:  (24, 2, 97, 0, 0),
    9:  (30, 2, 116, 0, 0),
    10: (18, 2, 68, 2, 69),
}

# Полная вместимость данных (кодовых слов) уровня L по версии = сумма data-слов.
def _data_codewords(version: int) -> int:
    ec, n1, d1, n2, d2 = _EC_TABLE_L[version]
    return n1 * d1 + n2 * d2


# Позиции выравнивающих паттернов (центры) по версии.
_ALIGN_POS = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50],
}


# --------------------------------------------------------------------------
# Выбор версии под длину данных (byte-mode, уровень L).
# --------------------------------------------------------------------------

def _byte_capacity(version: int) -> int:
    """Сколько байт данных вмещает версия в byte-mode (уровень L)."""
    total_data_bits = _data_codewords(version) * 8
    # Накладные: 4 бита индикатор режима + индикатор длины (8 или 16 бит).
    count_bits = 8 if version <= 9 else 16
    return (total_data_bits - 4 - count_bits) // 8


def _choose_version(data_len: int) -> int:
    for v in range(1, 11):
        if _byte_capacity(v) >= data_len:
            return v
    raise ValueError(
        f"Слишком длинные данные для QR версий 1–10: {data_len} байт "
        f"(макс {_byte_capacity(10)})."
    )


# --------------------------------------------------------------------------
# Сборка битового потока данных + EC.
# --------------------------------------------------------------------------

def _encode_data(data: bytes, version: int) -> List[int]:
    """Возвращает полный поток кодовых слов (data + EC), готовый к размещению."""
    count_bits = 8 if version <= 9 else 16

    # Битовый буфер
    bits: List[int] = []
    def put(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)              # индикатор byte-mode
    put(len(data), count_bits)  # длина
    for b in data:
        put(b, 8)

    total_data_cw = _data_codewords(version)
    total_data_bits = total_data_cw * 8

    # Терминатор (до 4 нулевых бит, но не больше остатка)
    term = min(4, total_data_bits - len(bits))
    put(0, term)
    # Дополнить до байта
    while len(bits) % 8 != 0:
        bits.append(0)
    # Дополняющие байты 0xEC, 0x11 до заполнения
    data_cw = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    pi = 0
    while len(data_cw) < total_data_cw:
        data_cw.append(pad[pi % 2])
        pi += 1

    # Разбивка на блоки + EC по блокам
    ec_per_block, n1, d1, n2, d2 = _EC_TABLE_L[version]
    blocks = []
    idx = 0
    for _ in range(n1):
        blocks.append(data_cw[idx:idx + d1])
        idx += d1
    for _ in range(n2):
        blocks.append(data_cw[idx:idx + d2])
        idx += d2

    ec_blocks = [_rs_encode(b, ec_per_block) for b in blocks]

    # Interleave data, затем EC (стандарт QR)
    result: List[int] = []
    maxd = max(len(b) for b in blocks)
    for i in range(maxd):
        for b in blocks:
            if i < len(b):
                result.append(b[i])
    maxe = max(len(b) for b in ec_blocks)
    for i in range(maxe):
        for b in ec_blocks:
            if i < len(b):
                result.append(b[i])
    return result


# --------------------------------------------------------------------------
# Построение матрицы: паттерны, данные, маски, format-info.
# --------------------------------------------------------------------------

def _new_matrix(size: int):
    # None = не заполнено (резерв под функц. паттерны определяем через mask-карту)
    return [[None for _ in range(size)] for _ in range(size)]


def _place_finder(m, reserved, r, c) -> None:
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            rr, cc = r + dr, c + dc
            if 0 <= rr < len(m) and 0 <= cc < len(m):
                # рамка finder 7×7
                if 0 <= dr <= 6 and 0 <= dc <= 6:
                    inner = (1 <= dr <= 5 and 1 <= dc <= 5)
                    center = (2 <= dr <= 4 and 2 <= dc <= 4)
                    val = not inner or center
                    m[rr][cc] = val
                else:
                    m[rr][cc] = False  # сепаратор
                reserved[rr][cc] = True


def _place_alignment(m, reserved, version) -> None:
    pos = _ALIGN_POS[version]
    for r in pos:
        for c in pos:
            # пропускаем там, где finder-паттерны
            if (r, c) in ((6, 6), (6, pos[-1]), (pos[-1], 6)):
                continue
            if reserved[r][c]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    ring = max(abs(dr), abs(dc))
                    m[r + dr][c + dc] = (ring != 1)
                    reserved[r + dr][c + dc] = True


def _place_timing(m, reserved) -> None:
    n = len(m)
    for i in range(8, n - 8):
        v = (i % 2 == 0)
        if not reserved[6][i]:
            m[6][i] = v
            reserved[6][i] = True
        if not reserved[i][6]:
            m[i][6] = v
            reserved[i][6] = True


def _reserve_format(reserved, n) -> None:
    for i in range(9):
        if i != 6:
            reserved[8][i] = True
            reserved[i][8] = True
    for i in range(8):
        reserved[8][n - 1 - i] = True
        reserved[n - 1 - i][8] = True
    # тёмный модуль
    reserved[n - 8][8] = True


# Таблица маскирующих функций (i=строка, j=столбец)
_MASKS = [
    lambda i, j: (i + j) % 2 == 0,
    lambda i, j: i % 2 == 0,
    lambda i, j: j % 3 == 0,
    lambda i, j: (i + j) % 3 == 0,
    lambda i, j: (i // 2 + j // 3) % 2 == 0,
    lambda i, j: (i * j) % 2 + (i * j) % 3 == 0,
    lambda i, j: ((i * j) % 2 + (i * j) % 3) % 2 == 0,
    lambda i, j: ((i + j) % 2 + (i * j) % 3) % 2 == 0,
]

# Format info bits (EC-уровень L = 01) для каждого номера маски, с BCH и маской 0x5412.
_FORMAT_BITS_L = {
    0: 0b111011111000100, 1: 0b111001011110011, 2: 0b111110110101010,
    3: 0b111100010011101, 4: 0b110011000101111, 5: 0b110001100011000,
    6: 0b110110001000001, 7: 0b110100101110110,
}


def _place_data(m, reserved, codewords) -> None:
    n = len(m)
    bits = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    bit_idx = 0
    col = n - 1
    upward = True
    while col > 0:
        if col == 6:  # пропускаем timing-колонку
            col -= 1
        rows = range(n - 1, -1, -1) if upward else range(n)
        for row in rows:
            for c in (col, col - 1):
                if not reserved[row][c]:
                    bit = bits[bit_idx] if bit_idx < len(bits) else 0
                    m[row][c] = (bit == 1)
                    bit_idx += 1
        upward = not upward
        col -= 2


def _apply_mask(m, reserved, mask_fn):
    n = len(m)
    out = [[m[r][c] for c in range(n)] for r in range(n)]
    for r in range(n):
        for c in range(n):
            if not reserved[r][c] and mask_fn(r, c):
                out[r][c] = not out[r][c]
    return out


def _penalty(m) -> int:
    n = len(m)
    score = 0
    # Правило 1: серии ≥5 одного цвета
    for line in list(m) + [[m[r][c] for r in range(n)] for c in range(n)]:
        run = 1
        for i in range(1, n):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    # Правило 2: блоки 2×2
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    # Правило 3: паттерн finder-подобный 1011101 с отступом
    patt1 = [True, False, True, True, True, False, True, False, False, False, False]
    patt2 = [False, False, False, False, True, False, True, True, True, False, True]
    for r in range(n):
        for c in range(n - 10):
            seg = [m[r][c + k] for k in range(11)]
            if seg == patt1 or seg == patt2:
                score += 40
    for c in range(n):
        for r in range(n - 10):
            seg = [m[r + k][c] for k in range(11)]
            if seg == patt1 or seg == patt2:
                score += 40
    # Правило 4: баланс тёмных модулей.
    # Стандарт QR: процент тёмных, округлённый к ближайшим 5% ВНИЗ и ВВЕРХ;
    # берём оба отклонения от 50, делим на 5, ×10, и выбираем меньшее.
    dark = sum(1 for row in m for v in row if v)
    percent = dark * 100 / (n * n)
    prev5 = int(percent // 5) * 5
    next5 = prev5 + 5
    score += min(abs(prev5 - 50) // 5, abs(next5 - 50) // 5) * 10
    return score


def _place_format(m, mask_idx) -> None:
    n = len(m)
    fmt = _FORMAT_BITS_L[mask_idx]
    bits = [(fmt >> i) & 1 for i in range(14, -1, -1)]  # 15 бит, старший первым
    # Раскладка по стандарту QR (две копии).
    # Копия 1: вокруг верхнего левого finder.
    coords1 = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    for (r, c), b in zip(coords1, bits):
        m[r][c] = (b == 1)
    # Копия 2: низ-лево вертикаль + верх-право горизонталь.
    coords2 = [
        (n - 1, 8), (n - 2, 8), (n - 3, 8), (n - 4, 8), (n - 5, 8), (n - 6, 8), (n - 7, 8),
        (8, n - 8), (8, n - 7), (8, n - 6), (8, n - 5), (8, n - 4), (8, n - 3), (8, n - 2), (8, n - 1),
    ]
    for (r, c), b in zip(coords2, bits):
        m[r][c] = (b == 1)
    # тёмный модуль
    m[n - 8][8] = True


def qr_matrix(data: str, mask: int | None = None) -> List[List[bool]]:
    """
    Строит QR-матрицу для строки (byte-mode, EC-уровень L).

    Возвращает list[list[bool]] (True = чёрный модуль). Размер = 4*version+17.

    mask: если None — выбирается лучшая по penalty (стандарт ISO 18004).
    Можно задать 0–7 явно (для детерминизма/тестов). ЛЮБАЯ из 8 масок даёт
    валидный сканируемый QR — декодер читает её номер из format-info.
    """
    raw = data.encode("utf-8")
    version = _choose_version(len(raw))
    size = 4 * version + 17

    m = _new_matrix(size)
    reserved = [[False] * size for _ in range(size)]

    # Функциональные паттерны
    _place_finder(m, reserved, 0, 0)
    _place_finder(m, reserved, 0, size - 7)
    _place_finder(m, reserved, size - 7, 0)
    _place_alignment(m, reserved, version)
    _place_timing(m, reserved)
    _reserve_format(reserved, size)

    # Данные + EC
    codewords = _encode_data(raw, version)
    _place_data(m, reserved, codewords)

    # Заполнить незаполненные (None) функц. ячейки False (на всякий случай)
    for r in range(size):
        for c in range(size):
            if m[r][c] is None:
                m[r][c] = False

    # Явная маска (детерминизм/тесты) либо выбор лучшей по penalty.
    if mask is not None:
        masked = _apply_mask(m, reserved, _MASKS[mask])
        _place_format(masked, mask)
        return masked

    best = None
    best_score = None
    for mi in range(8):
        masked = _apply_mask(m, reserved, _MASKS[mi])
        _place_format(masked, mi)
        sc = _penalty(masked)
        if best_score is None or sc < best_score:
            best_score = sc
            best = masked
    return best
