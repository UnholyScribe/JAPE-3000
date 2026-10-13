"""Reader for the game's .hpk packs.

Same BPUL container as a savegame, but with a real directory tree and one
fragment per file. Files inside are wrapped in the same chunked ZSTD format
as a save's game_session, so zstd_chunks handles them unchanged.

Reads by seeking - the packs run to well over a gigabyte and are never loaded
whole.
"""
import struct

import zstd_chunks

HDR = '<4sIIiIIIII'
DIR_TYPE = 1


class HpkError(ValueError):
    pass


class Pack:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        magic, self.data_start, self.frags_per_file, self.f12, self.f16, \
            self.f20, self.f24, self.ft_off, self.ft_len = \
            struct.unpack(HDR, self.f.read(struct.calcsize(HDR)))
        if magic != b'BPUL':
            raise HpkError('%s is not a BPUL pack (magic %r)' % (path, magic))
        self.f.seek(self.ft_off)
        ft = self.f.read(self.ft_len)
        self.frags = [struct.unpack_from('<II', ft, i) for i in range(0, len(ft), 8)]

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # -- raw ------------------------------------------------------------
    def _slot(self, fid):
        return (fid - 1) * self.frags_per_file

    def _blob(self, fid):
        off, size = self.frags[self._slot(fid)]
        self.f.seek(off)
        return self.f.read(size)

    def _listing(self, fid):
        """[(id, type, name)] for a directory entry (fid 1 is the root)."""
        b, p, out = self._blob(fid), 0, []
        while p + 10 <= len(b):
            cid, ctype, nlen = struct.unpack_from('<IIH', b, p)
            p += 10
            if nlen == 0 or p + nlen > len(b):
                break
            out.append((cid, ctype, b[p:p + nlen].decode('utf-8', 'replace')))
            p += nlen
        return out

    # -- navigation -----------------------------------------------------
    def listdir(self, path=''):
        fid = self._resolve(path) if path else 1
        return self._listing(fid)

    def _resolve(self, path):
        """Directory id for a slash-separated path."""
        fid = 1
        for part in [p for p in path.replace('\\', '/').split('/') if p]:
            for cid, ctype, name in self._listing(fid):
                if name.lower() == part.lower() and (ctype & 0xff) == DIR_TYPE:
                    fid = cid
                    break
            else:
                raise KeyError(path)
        return fid

    def read(self, path):
        """Decompressed bytes of a file, by full path inside the pack."""
        path = path.replace('\\', '/').strip('/')
        parent, _, leaf = path.rpartition('/')
        fid = self._resolve(parent) if parent else 1
        for cid, ctype, name in self._listing(fid):
            if name.lower() == leaf.lower() and (ctype & 0xff) != DIR_TYPE:
                return zstd_chunks.decompress(self._blob(cid))
        raise KeyError(path)

    def walk(self, path=''):
        """Yield (full_path, id) for every file at or below path."""
        stack = [(path.strip('/'), self._resolve(path) if path else 1)]
        while stack:
            prefix, fid = stack.pop()
            for cid, ctype, name in self._listing(fid):
                full = '%s/%s' % (prefix, name) if prefix else name
                if (ctype & 0xff) == DIR_TYPE:
                    stack.append((full, cid))
                else:
                    yield full, cid

    def read_id(self, fid):
        return zstd_chunks.decompress(self._blob(fid))
