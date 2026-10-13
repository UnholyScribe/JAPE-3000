"""BPUL container used by Jagged Alliance 3 .savegame.sav files.

Verified against real saves: parsing then rebuilding an untouched save
reproduces the original file byte-for-byte (see test_real_saves.py).

Header (little-endian uint32 unless noted):
   0  magic 'BPUL'
   4  data_start          92 in game-written saves; data begins here
   8  frags_per_file      8 in game-written saves
  12  int32               -1
  16  0
  20  0
  24  1
  28  frag_table_offset
  32  frag_table_length   (bytes; 8 per fragment slot)
  ...  zero padding out to data_start

The fragment table is a flat array of (offset uint32, size uint32) slots.
Slot 0 holds the directory. A directory entry with id N has its data in
fragment slot (N-1) * frags_per_file; the remaining frags_per_file-1 slots
of each entry are zero in every save observed.

Directory entry: id uint32, type uint32 (0 = file), name_len uint16, name.
Real entry names: 'savegame_metadata', 'screenshot<W>x<H>.jpg', 'game_session'.
"""
import struct

MAGIC = b'BPUL'
HDR = '<4sIIiIIIII'
HDR_FIELDS = 36


class BpulError(ValueError):
    pass


class Container:
    def __init__(self, raw):
        self.raw = raw
        if len(raw) < HDR_FIELDS or raw[:4] != MAGIC:
            raise BpulError('not a JA3 savegame (magic %r)' % raw[:4])
        (_m, self.data_start, self.frags_per_file, self.f12, self.f16,
         self.f20, self.f24, self.ft_off, self.ft_len) = struct.unpack_from(HDR, raw, 0)
        if self.frags_per_file == 0:
            raise BpulError('frags_per_file is 0')
        ft = raw[self.ft_off:self.ft_off + self.ft_len]
        self.frags = [list(struct.unpack_from('<II', ft, i)) for i in range(0, len(ft), 8)]
        if not self.frags:
            raise BpulError('empty fragment table')
        self.dir = self._read_dir()
        self._edits = {}

    # -- directory --------------------------------------------------------
    def _read_dir(self):
        off, size = self.frags[0]
        b, p, out = self.raw[off:off + size], 0, []
        while p + 10 <= len(b):
            fid, ftype, nlen = struct.unpack_from('<IIH', b, p)
            p += 10
            if nlen == 0 or p + nlen > len(b):
                raise BpulError('malformed directory entry at %d' % p)
            out.append({'id': fid, 'type': ftype, 'name': b[p:p + nlen].decode('utf-8')})
            p += nlen
        return out

    def _slot(self, entry):
        return (entry['id'] - 1) * self.frags_per_file

    def _entry(self, name):
        for e in self.dir:
            if e['name'] == name:
                return e
        # tolerate the screenshot's resolution-dependent name
        for e in self.dir:
            if e['name'].startswith(name):
                return e
        raise KeyError(name)

    # -- blobs ------------------------------------------------------------
    def names(self):
        return [e['name'] for e in self.dir]

    def has(self, name):
        try:
            self._entry(name)
            return True
        except KeyError:
            return False

    def blob(self, name):
        e = self._entry(name)
        if e['name'] in self._edits:
            return self._edits[e['name']]
        off, size = self.frags[self._slot(e)]
        return self.raw[off:off + size]

    def set_blob(self, name, data):
        self._edits[self._entry(name)['name']] = data

    # -- diagnostics ------------------------------------------------------
    def warnings(self):
        out = []
        for e in self.dir:
            slot = self._slot(e)
            extra = self.frags[slot + 1:slot + self.frags_per_file]
            if any(o or s for o, s in extra):
                out.append('entry %r uses more than one fragment; this tool only '
                           'handles single-fragment entries' % e['name'])
        for want in ('game_session', 'savegame_metadata'):
            if not self.has(want):
                out.append('missing expected entry %r (found: %s)' % (want, self.names()))
        return out

    # -- rebuild ----------------------------------------------------------
    def build(self):
        """Re-emit the container, preserving header, entry ids, names and
        blob order. With no set_blob() calls the result is byte-identical."""
        frags = [[0, 0] for _ in self.frags]
        out = bytearray(self.raw[:self.data_start])
        for e in self.dir:
            data = self.blob(e['name'])
            frags[self._slot(e)] = [len(out), len(data)]
            out += data
        dir_bytes = bytearray()
        for e in self.dir:
            nb = e['name'].encode('utf-8')
            dir_bytes += struct.pack('<IIH', e['id'], e['type'], len(nb)) + nb
        frags[0] = [len(out), len(dir_bytes)]
        out += dir_bytes
        ft_off = len(out)
        for o, s in frags:
            out += struct.pack('<II', o, s)
        struct.pack_into(HDR, out, 0, MAGIC, self.data_start, self.frags_per_file,
                         self.f12, self.f16, self.f20, self.f24, ft_off, len(frags) * 8)
        return bytes(out)


def parse(raw):
    return Container(raw)
