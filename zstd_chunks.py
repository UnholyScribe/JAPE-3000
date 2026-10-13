"""The chunked-Zstandard wrapper around game_session inside a JA3 save.

Verified against real saves. Layout:

   0  'ZSTD'
   4  uint32  total decompressed size of all chunks
   8  uint32  decompressed chunk size (131072 in every save seen)
  12  uint32  data_start: byte offset of the first zstd frame
  16  uint32 x (nchunks-1): END offset of each chunk but the last, i.e. the
          start offset of the next one. data_start doubles as chunk 0's start
          and the last chunk runs to the end of the blob.
  ...  nchunks independent zstd frames

nchunks = (data_start - 16) / 4 + 1.

Note the field at offset 4 is the total decompressed size; earlier notes that
called it a `0x02 'AK\\0'` constant were reading one save's size bytes.
"""
import struct

import zstandard

CHUNK_SIZE = 131072


class ZstdChunkError(ValueError):
    pass


def is_wrapped(data):
    return len(data) >= 4 and data[:4] == b'ZSTD'


def decompress(data):
    """Decompress a chunked blob. Passes plain (unwrapped) bytes through."""
    if not is_wrapped(data):
        return data
    if len(data) < 16:
        raise ZstdChunkError('ZSTD blob too short')
    total, chunk_size, data_start = struct.unpack_from('<III', data, 4)
    if data_start < 16 or data_start > len(data) or (data_start - 16) % 4:
        raise ZstdChunkError('bad data_start %d' % data_start)
    n_bounds = (data_start - 16) // 4
    bounds = [data_start] + list(struct.unpack_from('<%dI' % n_bounds, data, 16)) + [len(data)]
    for a, b in zip(bounds, bounds[1:]):
        if not (0 < a <= b <= len(data)):
            raise ZstdChunkError('chunk bounds out of range: %d..%d' % (a, b))
    dctx = zstandard.ZstdDecompressor()
    out = bytearray()
    for a, b in zip(bounds, bounds[1:]):
        out += dctx.decompress(data[a:b], max_output_size=chunk_size)
    if len(out) != total:
        raise ZstdChunkError('decompressed %d bytes, header says %d' % (len(out), total))
    return bytes(out)


def compress(plaintext, chunk_size=CHUNK_SIZE, level=3):
    """Re-wrap plaintext into the chunked format the game reads."""
    cctx = zstandard.ZstdCompressor(level=level)
    pieces = [plaintext[i:i + chunk_size] for i in range(0, len(plaintext), chunk_size)]
    if not pieces:
        pieces = [b'']
    frames = [cctx.compress(p) for p in pieces]
    data_start = 16 + 4 * (len(frames) - 1)
    bounds, cur = [], data_start
    for f in frames[:-1]:
        cur += len(f)
        bounds.append(cur)
    head = struct.pack('<4sIII', b'ZSTD', len(plaintext), chunk_size, data_start)
    return head + struct.pack('<%dI' % len(bounds), *bounds) + b''.join(frames)
