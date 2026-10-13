"""Decoder for Sir-Tech Cartoon Image files - JA2's .STI art format.

Two shapes exist. Everything in the laptop and interface archives is the
8-bit indexed kind, ETRLE compressed:

    0     4  "STCI"
    4     4  uint32  original (uncompressed) size
    8     4  uint32  stored size
   12     4  uint32  transparent colour index
   16     4  uint32  flags   0x08 indexed, 0x20 ETRLE compressed
   20     2  uint16  height
   22     2  uint16  width
   24     4  uint32  number of palette colours (256)
   28     2  uint16  number of sub images
   44     1  uint8   colour depth
   64        palette, 256 x RGB
  832        sub image headers, 16 bytes each
    ...      then the ETRLE data

ETRLE is a run length scheme with a transparency bit:

    byte == 0     end of this scanline
    byte & 0x80   skip (byte & 0x7f) transparent pixels
    otherwise     copy the next (byte & 0x7f) bytes literally
"""
import struct

MAGIC = b'STCI'
FLAG_INDEXED = 0x08
FLAG_ETRLE = 0x20
HEADER = 64
PALETTE = 256 * 3
SUBHEADER = 16


class StiError(ValueError):
    pass


class Sti:
    def __init__(self, data):
        if data[:4] != MAGIC:
            raise StiError('not an STI (magic %r)' % data[:4])
        self.data = data
        (self.orig_size, self.stored_size, self.transparent,
         self.flags) = struct.unpack_from('<IIII', data, 4)
        self.height, self.width = struct.unpack_from('<HH', data, 20)
        self.colours, self.subimages = struct.unpack_from('<IH', data, 24)
        self.depth = data[44]
        if not self.flags & FLAG_INDEXED:
            raise StiError('only the 8-bit indexed form is supported '
                           '(flags 0x%04x)' % self.flags)
        pal = data[HEADER:HEADER + PALETTE]
        self.palette = [tuple(pal[i * 3:i * 3 + 3]) for i in range(256)]
        self.sub_start = HEADER + PALETTE

    def sub_header(self, i):
        off = self.sub_start + i * SUBHEADER
        data_off, data_len, ox, oy, h, w = struct.unpack_from('<IIhhHH', self.data, off)
        return data_off, data_len, ox, oy, h, w

    @property
    def data_start(self):
        return self.sub_start + self.subimages * SUBHEADER

    def frame(self, index=0):
        """One sub image as (width, height, [palette index per pixel]).

        Transparent pixels come back as None so the caller can decide.
        """
        data_off, data_len, _ox, _oy, h, w = self.sub_header(index)
        start = self.data_start + data_off
        blob = self.data[start:start + data_len]
        pixels = []
        row = []
        i = 0
        while i < len(blob) and len(pixels) < h:
            b = blob[i]; i += 1
            if b == 0:                       # end of scanline
                row.extend([None] * (w - len(row)))
                pixels.append(row[:w])
                row = []
            elif b & 0x80:                   # a run of transparency
                row.extend([None] * (b & 0x7f))
            else:                            # literal pixels
                n = b & 0x7f
                row.extend(blob[i:i + n]); i += n
        while len(pixels) < h:
            pixels.append(row[:w] + [None] * (w - len(row)))
            row = []
        return w, h, pixels

    def image(self, index=0):
        """A Pillow RGBA image of one sub image."""
        from PIL import Image
        w, h, rows = self.frame(index)
        im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        px = im.load()
        for y, row in enumerate(rows[:h]):
            for x, v in enumerate(row[:w]):
                if v is None:
                    continue
                r, g, b = self.palette[v]
                px[x, y] = (r, g, b, 255)
        return im
