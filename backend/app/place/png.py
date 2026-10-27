"""A PNG decoder on zlib alone, for the few small WMS images the report
reads (a 9x9 fire-danger tile, a legend). 8-bit RGBA / RGB / grey /
palette, and 1/2/4-bit palettes; every scanline filter. No interlacing."""

from __future__ import annotations

import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
Pixel = tuple[int, int, int, int]


class PngError(ValueError):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(data: bytes) -> list[list[Pixel]]:
    """Rows of RGBA pixels, top row first."""
    if data[:8] != SIGNATURE:
        raise PngError("not a PNG")
    pos = 8
    ihdr = None
    idat = bytearray()
    plte = b""
    trns = b""
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"PLTE":
            plte = chunk
        elif kind == b"tRNS":
            trns = chunk
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
    if ihdr is None:
        raise PngError("no IHDR")
    width, height, depth, ctype, _, _, interlace = ihdr
    if interlace:
        raise PngError("interlaced PNG")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(ctype)
    if channels is None:
        raise PngError(f"colour type {ctype}")
    if depth != 8 and not (ctype == 3 and depth in (1, 2, 4)):
        raise PngError(f"bit depth {depth} for colour type {ctype}")
    raw = zlib.decompress(bytes(idat))
    bits = depth * channels
    stride = (width * bits + 7) // 8
    bpp = max(1, bits // 8)
    rows: list[list[Pixel]] = []
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        filt = raw[i]
        line = bytearray(raw[i + 1 : i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            up = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if filt == 1:
                line[x] = (line[x] + a) & 255
            elif filt == 2:
                line[x] = (line[x] + up) & 255
            elif filt == 3:
                line[x] = (line[x] + ((a + up) >> 1)) & 255
            elif filt == 4:
                line[x] = (line[x] + _paeth(a, up, c)) & 255
            elif filt != 0:
                raise PngError(f"filter {filt}")
        prev = line
        rows.append([_pixel(line, x, depth, ctype, plte, trns) for x in range(width)])
    return rows


def _pixel(line: bytearray, x: int, depth: int, ctype: int, plte: bytes, trns: bytes) -> Pixel:
    if ctype == 3:
        if depth == 8:
            index = line[x]
        else:
            per = 8 // depth
            byte = line[x // per]
            shift = 8 - depth * (x % per + 1)
            index = (byte >> shift) & ((1 << depth) - 1)
        r, g, b = plte[index * 3 : index * 3 + 3]
        alpha = trns[index] if index < len(trns) else 255
        return r, g, b, alpha
    if ctype == 6:
        r, g, b, alpha = line[x * 4 : x * 4 + 4]
        return r, g, b, alpha
    if ctype == 2:
        r, g, b = line[x * 3 : x * 3 + 3]
        return r, g, b, 255
    if ctype == 4:
        grey, alpha = line[x * 2 : x * 2 + 2]
        return grey, grey, grey, alpha
    grey = line[x]
    return grey, grey, grey, 255
