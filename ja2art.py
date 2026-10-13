"""Jagged Alliance 2 pictures, read from the player's own copy of the game.

The editor is published on Nexus Mods, which does not allow one game's art to
travel inside a mod for another. So none of JA2's pictures ship in the exe:
the first time they are wanted they are decoded out of the JA2 install found
on this machine into the editor's local folder, and served from there.

    portraits/ja2_NNN.png   Data/Faces.slf       by profile number
    ja2icons/<name>.png     Data/Bigitems.slf    by ja2.icon_index
    ja2-bg.png              Data/Laptop.slf      GREENBACK.STI
    ja2-aim.png             Data/Laptop.slf      AIMSYMBOL.STI
    ja2-wildfire.jpg        Data/Interface.slf   LoadScreen.sti, Wildfire's own
                                                 if installed, else JA2's

No JA2 installed means none of this exists, and the front end already copes:
faces fall back to initials, icons to category symbols, the skin to its
colours. Removing the editor takes the folder with it (removal.LOCAL_DIRS).
"""
import json
import os
import shutil
import threading

import gamepaths

VERSION = 1                  # bump to rebuild every cache on next start
BACKSLASH = chr(92)          # SLF entry names are DOS paths

FACE_SOURCES = ('BIGFACES' + BACKSLASH + '%02d.STI',
                'B%02d.STI',
                '%02d.STI',
                '65FACE' + BACKSLASH + '%02d.STI',
                '33FACE' + BACKSLASH + '%02d.STI')
PROFILE_COUNT = 170
MAX_FACE_W = 212

# web path -> (slf, entry); first install that has it wins
SKIN = {
    'ja2-bg.png': ('Laptop.slf', 'GREENBACK.STI'),
    'ja2-aim.png': ('Laptop.slf', 'AIMSYMBOL.STI'),
    'ja2-wildfire.jpg': ('Interface.slf', 'LOADSCREEN.STI'),
}

_LOCK = threading.Lock()
_BUILT = {}                  # cache folder -> manifest, once built this run


def cache_dir(local=None):
    return os.path.join(local or gamepaths.local_data_dir(), 'ja2art')


def _installs():
    """JA2 installs, Wildfire first so its loading screen is the one used."""
    try:
        import games
        found = games.ja2_dirs()
    except Exception:                                       # noqa: BLE001
        return []
    return sorted(found, key=lambda p: 'wildfire' not in p.lower()
                  or 'classic' in p.lower())


def _slf(root, name):
    data = os.path.join(root, 'Data')
    try:
        for f in os.listdir(data):
            if f.lower() == name.lower():
                return os.path.join(data, f)
    except OSError:
        pass
    return None


def _entries(lib):
    return {e[0].upper(): e[0] for e in lib.listing()}


def _image(lib, name):
    from ja2 import sti
    return sti.Sti(lib.read(name)).image(0)


def _faces(roots, out):
    from ja2.slf import Library
    index = {}
    for root in roots:
        pack = _slf(root, 'Faces.slf')
        if not pack:
            continue
        with Library(pack) as lib:
            names = _entries(lib)
            for i in range(PROFILE_COUNT):
                key = 'ja2/p%d' % i
                if key in index:
                    continue
                got = next((names[p % i] for p in FACE_SOURCES if (p % i) in names), None)
                if not got:
                    continue
                try:
                    img = _image(lib, got)
                except Exception:                           # noqa: BLE001
                    continue
                if img.width > MAX_FACE_W:
                    img = img.resize((MAX_FACE_W, round(img.height * MAX_FACE_W / img.width)))
                name = 'ja2_%03d' % i
                img.save(os.path.join(out, 'portraits', name + '.png'), optimize=True)
                index[key] = name
    return index


def _icons(roots, out):
    from ja2.slf import Library
    from ja2.icon_index import ICONS
    wanted = set(ICONS.values())
    written = set()
    for root in roots:
        pack = _slf(root, 'Bigitems.slf')
        if not pack:
            continue
        with Library(pack) as lib:
            have = {n.split(BACKSLASH)[-1].upper(): n for n in _entries(lib).values()}
            for key in sorted(wanted - written):
                real = have.get(key.upper() + '.STI')
                if not real:
                    continue
                try:
                    _image(lib, real).save(os.path.join(out, 'ja2icons', key + '.png'))
                    written.add(key)
                except Exception:                           # noqa: BLE001
                    pass
    return {str(i): k for i, k in ICONS.items() if k in written}


def _skin(roots, out):
    from ja2.slf import Library
    done = []
    for web, (pack_name, entry) in SKIN.items():
        for root in roots:
            pack = _slf(root, pack_name)
            if not pack:
                continue
            with Library(pack) as lib:
                real = _entries(lib).get(entry)
                if not real:
                    continue
                try:
                    img = _image(lib, real)
                except Exception:                           # noqa: BLE001
                    continue
                path = os.path.join(out, web)
                if web.endswith('.jpg'):
                    img.convert('RGB').save(path, quality=90)
                else:
                    img.save(path)
                done.append(web)
                break
    return done


def build(roots=None, local=None):
    """Decode everything into the cache. Returns the manifest, or None when
    there is no JA2 to read from. Rebuilds only when the installs or VERSION
    change."""
    roots = list(roots if roots is not None else _installs())
    folder = cache_dir(local)
    with _LOCK:
        if folder in _BUILT:
            return _BUILT[folder]
        if not roots:
            _BUILT[folder] = None
            return None
        manifest_path = os.path.join(folder, 'manifest.json')
        try:
            with open(manifest_path, encoding='utf-8') as f:
                old = json.load(f)
            if old.get('version') == VERSION and old.get('roots') == roots:
                _BUILT[folder] = old
                return old
        except (OSError, ValueError):
            pass
        tmp = folder + '.part'
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, 'portraits'))
        os.makedirs(os.path.join(tmp, 'ja2icons'))
        manifest = {
            'version': VERSION, 'roots': roots,
            'faces': _faces(roots, tmp),
            'icons': _icons(roots, tmp),
            'skin': _skin(roots, tmp),
        }
        with open(os.path.join(tmp, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump(manifest, f, separators=(',', ':'))
        shutil.rmtree(folder, ignore_errors=True)
        os.replace(tmp, folder)
        _BUILT[folder] = manifest
        return manifest


def start_background():
    """Build on a worker so the window opens without waiting for it."""
    t = threading.Thread(target=lambda: _safe_build(), daemon=True)
    t.start()
    return t


def _safe_build():
    try:
        return build()
    except Exception:                                       # noqa: BLE001
        return None


def manifest():
    """The manifest, building first if needed (blocks the caller)."""
    return _safe_build()


def file_for(rel):
    """Disk path for a web path that belongs to the JA2 art, or None."""
    rel = rel.replace('\\', '/')
    is_ours = (rel in SKIN
               or (rel.startswith('portraits/ja2_') and rel.endswith('.png'))
               or (rel.startswith('ja2icons/') and rel.endswith('.png')))
    if not is_ours or '..' in rel:
        return None
    if not manifest():
        return None
    path = os.path.join(cache_dir(), rel.replace('/', os.sep))
    return path if os.path.isfile(path) else None
