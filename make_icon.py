"""Gera evcantos.ico (Pac-Man neon num quadrado escuro com borda ciano) so com a biblioteca padrao.

    python make_icon.py        -> evcantos.ico (16, 24, 32, 48, 64, 128 e 256 px)
"""
from __future__ import annotations

import math
import struct
import zlib

SIZES = (16, 24, 32, 48, 64, 128, 256)
BG, BORDER, YELLOW, PINK = (11, 7, 32), (0, 240, 255), (255, 230, 0), (255, 43, 214)


def _sample(u: float, v: float):
    """Cor RGBA de um ponto (u, v em 0..1) do desenho."""
    # quadrado arredondado
    r = 0.2
    dx, dy = max(abs(u - 0.5) - (0.5 - r), 0.0), max(abs(v - 0.5) - (0.5 - r), 0.0)
    d = math.hypot(dx, dy)
    if d > r:
        return (0, 0, 0, 0)
    if d > r - 0.06:
        return (*BORDER, 255)
    # Pac-Man virado para a direita, com a boca aberta
    cx, cy, rad = 0.38, 0.5, 0.27
    px, py = u - cx, v - cy
    if px * px + py * py <= rad * rad:
        ang = math.degrees(math.atan2(-py, px))
        if abs(ang) > 32:
            return (*YELLOW, 255)
        return (*BG, 255)
    # pontinhos
    for dot_x, dot_r in ((0.72, 0.055), (0.87, 0.04)):
        if (u - dot_x) ** 2 + (v - 0.5) ** 2 <= dot_r * dot_r:
            return (*PINK, 255)
    return (*BG, 255)


def render(size: int, ss: int = 4) -> bytes:
    rows = []
    for y in range(size):
        row = bytearray([0])  # filtro PNG 0
        for x in range(size):
            r = g = b = a = 0.0
            for j in range(ss):
                for i in range(ss):
                    cr, cg, cb, ca = _sample((x + (i + 0.5) / ss) / size, (y + (j + 0.5) / ss) / size)
                    r, g, b, a = r + cr * ca, g + cg * ca, b + cb * ca, a + ca
            n = ss * ss
            if a:
                row += bytes((round(r / a), round(g / a), round(b / a), round(a / n)))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    return b"".join(rows)


def png(size: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(render(size), 9)) + chunk(b"IEND", b""))


def make_ico(path: str = "evcantos.ico") -> None:
    images = [(s, png(s)) for s in SIZES]
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for s, data in images:
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    for _, data in images:
        out += data
    with open(path, "wb") as f:
        f.write(out)


if __name__ == "__main__":
    make_ico()
    print("evcantos.ico gerado")
