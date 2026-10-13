"""Reader for Sir-Tech Library files - the .slf archives JA2 keeps its data in.

Simple format, and nothing like JA3's BPUL packs:

    0      256  library name, NUL padded      e.g. "LAPTOP.SLF"
    256    256  path prefix                   e.g. "Laptop"
    512      4  int32   number of entries
    516      4  int32   number used
    520      2  uint16  sort
    522      2  uint16  version
    524      1  bool    contains subdirectories
    528      4  int32   reserved
    532         file data starts here

The directory sits at the END of the file, one 280-byte entry per file:

    0      256  file name
    256      4  uint32  offset
    260      4  uint32  length
    264      1  uint8   state
    ...

So the directory begins at filesize - entries * 280.
"""
import os
import struct

HEADER = 532
ENTRY = 280


class SlfError(ValueError):
    pass


def _text(raw):
    return raw.split(b'\0', 1)[0].decode('cp1252', 'replace')


class Library:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        head = self.f.read(HEADER)
        if len(head) < HEADER:
            raise SlfError('%s is too small to be an .slf' % path)
        self.name = _text(head[0:256])
        self.prefix = _text(head[256:512])
        (self.entries, self.used, self.sort, self.version,
         self.has_subdirs) = struct.unpack_from('<iiHHB', head, 512)
        if not 0 < self.entries < 100000:
            raise SlfError('%s reports %d entries' % (path, self.entries))
        size = os.path.getsize(path)
        self.dir_start = size - self.entries * ENTRY
        if self.dir_start < HEADER:
            raise SlfError('%s: directory would start before the data' % path)

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def listing(self):
        """[(name, offset, length, state)] in directory order."""
        self.f.seek(self.dir_start)
        raw = self.f.read(self.entries * ENTRY)
        out = []
        for i in range(self.entries):
            e = raw[i * ENTRY:(i + 1) * ENTRY]
            name = _text(e[0:256])
            off, length, state = struct.unpack_from('<IIB', e, 256)
            out.append((name, off, length, state))
        return out

    def read(self, name):
        want = name.lower().replace('/', chr(92))
        for nm, off, length, _state in self.listing():
            if nm.lower() == want:
                self.f.seek(off)
                return self.f.read(length)
        raise KeyError(name)
