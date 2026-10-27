"""A QR code for a short text (an address), as SVG: the camera page shows one so a phone can fetch the Blackmagic
settings file by pointing its camera at the screen (AirDrop is Apple's alone; Blackmagic Camera also runs on Android).

Byte mode, error correction M, versions 1-6 (up to 106 bytes), the mask with the lowest penalty: the standard
(ISO/IEC 18004), written out because the bridge runs on the system Python with the standard library only. Checked by
decoding with OpenCV's QR reader (services/tests/test_camera_doctor.py).
"""
from __future__ import annotations

# Error correction M: (error-correction codewords per block, [(blocks, data codewords per block), ...]) per version.
_BLOCKS = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
           6: (16, [(4, 27)])}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}

# GF(256) with the QR polynomial x^8 + x^4 + x^3 + x^2 + 1.
_EXP = [0] * 512
_LOG = [0] * 256
_value = 1
for _i in range(255):
    _EXP[_i] = _value
    _LOG[_value] = _i
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _ecc(data: list[int], count: int) -> list[int]:
    generator = [1]
    for i in range(count):
        generator = [a ^ b for a, b in zip(generator + [0], [0] + [_mul(c, _EXP[i]) for c in generator])]
    remainder = list(data) + [0] * count
    for i in range(len(data)):
        factor = remainder[i]
        if factor:
            for j, coefficient in enumerate(generator):
                remainder[i + j] ^= _mul(coefficient, factor)
    return remainder[len(data):]


def _codewords(text: bytes) -> tuple[int, list[int]]:
    for version in range(1, 7):
        ecc_count, groups = _BLOCKS[version]
        capacity = sum(blocks * size for blocks, size in groups)
        if len(text) + 2 <= capacity:  # mode (4 bits) + count (8 bits) fit with the data in `capacity` bytes
            break
    else:
        raise ValueError("too long for a small QR code")
    bits = [0, 1, 0, 0] + [int(b) for b in f"{len(text):08b}"]
    for byte in text:
        bits += [int(b) for b in f"{byte:08b}"]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(data) < capacity:
        data.append((0xEC, 0x11)[pad % 2])
        pad += 1
    blocks, start = [], 0
    for count, size in groups:
        for _ in range(count):
            blocks.append(data[start:start + size])
            start += size
    eccs = [_ecc(block, ecc_count) for block in blocks]
    out = [block[i] for i in range(max(map(len, blocks))) for block in blocks if i < len(block)]
    out += [ecc[i] for i in range(ecc_count) for ecc in eccs]
    return version, out


def _format_bits(mask: int) -> int:
    value = (0b00 << 3 | mask) << 10  # level M is 00
    for i in range(14, 9, -1):
        if value >> i & 1:
            value ^= 0x537 << (i - 10)
    return ((0b00 << 3 | mask) << 10 | value) ^ 0x5412


def _penalty(grid: list[list[int]]) -> int:
    size, score = len(grid), 0
    for lines in (grid, [list(col) for col in zip(*grid)]):
        for line in lines:
            run = 1
            for i in range(1, size):
                if line[i] == line[i - 1]:
                    run += 1
                else:
                    score += run - 2 if run >= 5 else 0
                    run = 1
            score += run - 2 if run >= 5 else 0
            text = "".join(map(str, line))
            score += 40 * (text.count("10111010000") + text.count("00001011101"))
    for r in range(size - 1):
        for c in range(size - 1):
            if grid[r][c] == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]:
                score += 3
    dark = sum(map(sum, grid)) * 100 // (size * size)
    return score + abs(dark - 50) // 5 * 10


def matrix(text: str) -> list[list[int]]:
    """The QR code's modules (1 dark), without the quiet zone."""
    version, codewords = _codewords(text.encode("utf-8"))
    size = 17 + 4 * version
    grid = [[0] * size for _ in range(size)]
    fixed = [[False] * size for _ in range(size)]

    def put(r: int, c: int, value: int) -> None:
        grid[r][c], fixed[r][c] = value, True

    for top, left in ((0, 0), (0, size - 7), (size - 7, 0)):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = top + r, left + c
                if 0 <= rr < size and 0 <= cc < size:
                    ring = max(abs(r - 3), abs(c - 3))
                    put(rr, cc, 1 if ring in (0, 1, 3) else 0)
    for i in range(8, size - 8):
        put(6, i, int(i % 2 == 0))
        put(i, 6, int(i % 2 == 0))
    centres = _ALIGN[version]
    for r in centres:
        for c in centres:
            if fixed[r][c]:
                continue  # under a finder
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    put(r + dr, c + dc, int(max(abs(dr), abs(dc)) != 1))
    put(size - 8, 8, 1)  # the dark module
    format_cells = [(8, i) for i in range(9) if i != 6] + [(i, 8) for i in range(8) if i != 6] + \
                   [(8, size - 1 - i) for i in range(8)] + [(size - 1 - i, 8) for i in range(7)]
    for r, c in format_cells:
        fixed[r][c] = True

    bits = [codeword >> (7 - i) & 1 for codeword in codewords for i in range(8)]
    index, upward, column = 0, True, size - 1
    while column > 0:
        if column == 6:
            column -= 1
        for step in range(size):
            row = size - 1 - step if upward else step
            for c in (column, column - 1):
                if not fixed[row][c]:
                    grid[row][c] = bits[index] if index < len(bits) else 0
                    index += 1
        upward = not upward
        column -= 2

    masks = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
             lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
             lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
             lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0]
    best = None
    for number, rule in enumerate(masks):
        candidate = [[grid[r][c] ^ (0 if fixed[r][c] else int(rule(r, c))) for c in range(size)] for r in range(size)]
        info = _format_bits(number)
        for i in range(15):
            bit = info >> i & 1
            # The first copy: around the top-left finder.
            if i < 6:
                candidate[i][8] = bit
            elif i < 8:
                candidate[i + 1][8] = bit
            elif i == 8:
                candidate[8][7] = bit
            else:
                candidate[8][14 - i] = bit
            # The second copy: beside the other two finders.
            if i < 8:
                candidate[8][size - 1 - i] = bit
            else:
                candidate[size - 15 + i][8] = bit
        candidate[size - 8][8] = 1
        score = _penalty(candidate)
        if best is None or score < best[0]:
            best = (score, candidate)
    return best[1]


def svg(text: str, scale: int = 6, quiet: int = 4) -> str:
    modules = matrix(text)
    size = len(modules) + 2 * quiet
    rects = "".join(f'<rect x="{c + quiet}" y="{r + quiet}" width="1" height="1"/>'
                    for r, row in enumerate(modules) for c, dark in enumerate(row) if dark)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size * scale}" '
            f'height="{size * scale}" shape-rendering="crispEdges"><rect width="{size}" height="{size}" fill="#fff"/>'
            f'<g fill="#000">{rects}</g></svg>')
