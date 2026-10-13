"""Writer for the game's .hpk packs - the other half of hpk.py.

Same BPUL container. What the reader taught us, stated as rules for writing:

  * header is 36 bytes and data starts immediately after it
  * packs use frags_per_file = 1 (savegames use 8), so the fragment table
    index for an entry id N is simply N - 1
  * entry ids are handed out depth-first starting at 1, and 1 is the root
    directory
  * directory blobs are stored RAW; file blobs are stored wrapped in the
    chunked zstd format from zstd_chunks

That last one is the detail worth remembering - hpk.py parses listings with
_blob() but reads files with read_id(), and the difference is easy to miss.

Written to build fake DLC packs, but there is nothing DLC-specific here.
"""
import os
import struct
import sys

import zstd_chunks

HDR = '<4sIIiIIIII'
HDR_SIZE = struct.calcsize(HDR)

TYPE_DIR = 0x00000001
TYPE_FILE = 0x30000000       # retail: zstd-wrapped payload
TYPE_FILE_RAW = 0x00000000   # EpicEnabler: payload stored verbatim

# EpicEnabler.hpk - a hand-made fake DLC that actually works on this engine -
# stores every file UNCOMPRESSED with entry type 0. Retail packs compress and
# use 0x30000000. Both parse, but only the raw form is known to load as a DLC,
# so pack_files(compress=False) is the default for DLCs.

# Retail packs wrap files with a 262144-byte chunk size. zstd_chunks
# defaults to 131072, which is what SAVEGAMES use - a different number for
# a different container. Only matters for files over 128 KB, but matching
# the game removes a variable.
PACK_CHUNK_SIZE = 262144

# Fixed fields copied from the retail packs, which all agree:
#   data_start=36  frags_per_file=1  f12=-1  f16=0  f20=0  f24=1
_F12, _F16, _F20, _F24 = -1, 0, 0, 1

# chr(92) is a backslash. Spelled this way on purpose: writing the literal
# through a shell heredoc silently collapses the escape and corrupts the file,
# which has now happened five times on this project.
BACKSLASH = chr(92)


class HpkWriteError(ValueError):
    pass


def _dir_blob(listing):
    """Serialise [(id, type, name)] into a directory entry blob."""
    out = bytearray()
    for cid, ctype, name in listing:
        nb = name.encode('utf-8')
        if not nb:
            raise HpkWriteError('empty name in listing')
        if len(nb) > 0xffff:
            raise HpkWriteError('name too long: %s' % name)
        out += struct.pack('<IIH', cid, ctype, len(nb))
        out += nb
    return bytes(out)


def _tree_from_paths(files):
    """{'a/b.lua': b'..'} -> nested dicts, bytes at the leaves."""
    tree = {}
    for path, data in files.items():
        parts = [p for p in path.replace(BACKSLASH, '/').split('/') if p]
        if not parts:
            raise HpkWriteError('empty path')
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise HpkWriteError('%s is used as both a file and a folder' % part)
        leaf = parts[-1]
        if isinstance(node.get(leaf), dict):
            raise HpkWriteError('%s is used as both a file and a folder' % leaf)
        node[leaf] = data
    return tree


def pack_bytes(files, level=3, compress=True):
    """Build an .hpk in memory from {path_inside_pack: bytes}.

    The shipping editor builds the live-link DLC this way: it has no
    source tree and no writable folder of its own to build into, so the
    pack is assembled in memory and written straight to the game folder.
    """
    tree = _tree_from_paths(files)
    blobs = {}
    dir_ids = set()
    state = {'next': 2}          # 1 is the root, handed out below

    def alloc():
        fid = state['next']
        state['next'] += 1
        return fid

    def emit(node, fid):
        listing = []
        # sorted so a given input always produces a byte-identical pack
        for name in sorted(node):
            child = node[name]
            cid = alloc()
            if isinstance(child, dict):
                listing.append((cid, TYPE_DIR, name))
                emit(child, cid)          # depth-first, like the real packs
            else:
                if compress:
                    blobs[cid] = zstd_chunks.compress(
                        child, chunk_size=PACK_CHUNK_SIZE, level=level)
                    listing.append((cid, TYPE_FILE, name))
                else:
                    blobs[cid] = child
                    listing.append((cid, TYPE_FILE_RAW, name))
        blobs[fid] = _dir_blob(listing)
        dir_ids.add(fid)

    emit(tree, 1)

    ids = sorted(blobs)
    if ids != list(range(1, len(ids) + 1)):
        raise HpkWriteError('entry ids are not contiguous - fragment table would be wrong')

    # Retail packs lay out EVERY file blob first, then every directory blob,
    # then the fragment table - deliberately not in id order. Interleaving them
    # (root directory at data_start, where the engine expects file data) made
    # the game hang forever on the loading screen: mounted, never finished.
    # The fragment table still indexes by id, so only the byte order changes.
    order = [i for i in ids if i not in dir_ids] + sorted(dir_ids)

    data = bytearray()
    frags = [None] * len(ids)
    for fid in order:
        blob = blobs[fid]
        frags[fid - 1] = (HDR_SIZE + len(data), len(blob))
        data += blob

    ft = b''.join(struct.pack('<II', off, size) for off, size in frags)
    ft_off = HDR_SIZE + len(data)
    hdr = struct.pack(HDR, b'BPUL', HDR_SIZE, 1, _F12, _F16, _F20, _F24,
                      ft_off, len(ft))

    return hdr + bytes(data) + ft


def pack_files(files, out_path, level=3, compress=True):
    """Write an .hpk to disk. Returns the number of files written."""
    blob = pack_bytes(files, level=level, compress=compress)
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(out_path, 'wb') as f:
        f.write(blob)
    return len(files)


def pack_folder(src_dir, out_path, level=3, compress=True):
    """Write an .hpk containing everything under src_dir."""
    src_dir = os.path.abspath(src_dir)
    if not os.path.isdir(src_dir):
        raise HpkWriteError('no such folder: %s' % src_dir)
    files = {}
    for root, _dirs, names in os.walk(src_dir):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, src_dir).replace(os.sep, '/')
            with open(full, 'rb') as f:
                files[rel] = f.read()
    if not files:
        raise HpkWriteError('%s is empty' % src_dir)
    return pack_files(files, out_path, level=level, compress=compress)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('usage: hpk_write.py <source folder> <out.hpk>')
        raise SystemExit(2)
    n = pack_folder(sys.argv[1], sys.argv[2])
    print('wrote %s (%d files, %d bytes)'
          % (sys.argv[2], n, os.path.getsize(sys.argv[2])))
