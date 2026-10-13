"""Generate app.ico - a reticle mark, drawn with arithmetic.

No image libraries needed: PNGs are written by hand and packed into an ICO,
which has accepted PNG-encoded entries since Vista.

    python build_icon.py
"""
import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.ico')
SIZES = (16, 24, 32, 48, 64, 128, 256)

BG_OUTER = (0x0f, 0x13, 0x16)
BG_INNER = (0x20, 0x27, 0x2d)
AMBER = (0xf0, 0xb4, 0x29)
AMBER_DIM = (0xb8, 0x79, 0x1a)


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _cover(d, feather=1.2):
    """Coverage in 0..1 for a signed distance (negative = inside)."""
    return max(0.0, min(1.0, 0.5 - d / feather))


def _rounded_rect(x, y, cx, cy, half, radius):
    dx = abs(x - cx) - (half - radius)
    dy = abs(y - cy) - (half - radius)
    dx, dy = max(dx, 0.0), max(dy, 0.0)
    return math.hypot(dx, dy) - radius + min(max(abs(x - cx) - (half - radius),
                                                 abs(y - cy) - (half - radius)), 0.0)


def render(size):
    """RGBA pixels for one icon size."""
    s = float(size)
    px = bytearray(size * size * 4)
    c = s / 2.0
    tile_half = s * 0.5
    tile_r = s * 0.22
    ring_r = s * 0.30
    ring_w = max(1.0, s * 0.075)
    tick_in = s * 0.155
    tick_out = s * 0.425
    tick_w = max(1.0, s * 0.062)
    dot_r = max(0.8, s * 0.058)
    feather = max(0.9, s / 22.0)

    for py in range(size):
        for pxi in range(size):
            x, y = pxi + 0.5, py + 0.5

            tile = _cover(_rounded_rect(x, y, c, c, tile_half, tile_r), feather)
            if tile <= 0.0:
                continue

            # subtle top-left lift so the tile doesn't read as flat
            grad = ((x + y) / (2.0 * s))
            base = _mix(BG_INNER, BG_OUTER, grad)

            r = math.hypot(x - c, y - c)
            ink = 0.0

            # ring
            ink = max(ink, _cover(abs(r - ring_r) - ring_w / 2.0, feather))

            # four ticks, N/S/E/W, stopping short of the ring
            for horiz in (True, False):
                along = abs(x - c) if horiz else abs(y - c)
                across = abs(y - c) if horiz else abs(x - c)
                if tick_in <= along <= tick_out:
                    ink = max(ink, _cover(across - tick_w / 2.0, feather))

            # centre dot
            ink = max(ink, _cover(r - dot_r, feather))

            col = _mix(base, _mix(AMBER_DIM, AMBER, 1.0 - grad), ink)
            i = (py * size + pxi) * 4
            px[i:i + 4] = bytes((col[0], col[1], col[2], int(round(255 * tile))))
    return bytes(px)


def png(size, rgba):
    def chunk(tag, data):
        body = tag + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body))

    raw = bytearray()
    stride = size * 4
    for row in range(size):
        raw.append(0)                                   # filter: none
        raw += rgba[row * stride:(row + 1) * stride]
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 9))
            + chunk(b'IEND', b''))


def main():
    images = [(sz, png(sz, render(sz))) for sz in SIZES]
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b'', b''
    for sz, blob in images:
        entries += struct.pack('<BBBBHHII', sz if sz < 256 else 0, sz if sz < 256 else 0,
                               0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
        blobs += blob
    with open(OUT, 'wb') as f:
        f.write(header + entries + blobs)
    print('wrote %s (%d bytes, %s)' % (OUT, len(header + entries + blobs),
                                       ', '.join(str(s) for s in SIZES)))


if __name__ == '__main__':
    main()
