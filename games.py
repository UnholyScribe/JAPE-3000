"""Which game a save belongs to, and where each game lives.

JAPE 3000 edits more than one game, and the rule is simple:

    the save file decides.

Not a mode, not a toggle, not the theme. Every format announces itself in its
first bytes, so opening a file is the switch. A toggle whose only correct value
is "whatever the file already is" should not exist - set it wrong and the
editor is wrong about the most basic fact of the session.

This module owns three things and nothing else:

    detect(path)   which game a save belongs to, by reading it
    installs()     where each game is installed
    save_roots()   every folder saves live in, tagged by game

JA3 detection already existed in gamepaths.py and is left exactly as it was -
this wraps it rather than replacing it, so the JA3 half cannot break.
"""
import glob
import os
import string

import gamepaths

JA3 = 'ja3'
JA2 = 'ja2'

TITLES = {JA3: 'Jagged Alliance 3', JA2: 'Jagged Alliance 2'}
SHORT = {JA3: 'JA3', JA2: 'JA2'}


# -- recognising a save --------------------------------------------------

def detect(path):
    """Which game wrote this save, or None if we do not recognise it.

    JA3 is a BPUL container. JA2 is a plain struct that starts with a version
    number and then the build string in ASCII, e.g. "Build 04.12.02" - which
    is also how we tell one JA2 build from another later.
    """
    try:
        with open(path, 'rb') as f:
            head = f.read(24)
    except OSError:
        return None
    if head[:4] == b'BPUL':
        return JA3
    if head[4:9] == b'Build':
        return JA2
    return None


def build_string(path):
    """The JA2 build, e.g. "Build 04.12.02". None for anything else.

    Seven JA2 builds exist and their field offsets differ, so this is the
    first thing a JA2 reader has to establish. It is plain ASCII at offset 4,
    which makes version detection a 16-byte read rather than a guess.
    """
    if detect(path) != JA2:
        return None
    with open(path, 'rb') as f:
        f.seek(4)
        raw = f.read(16)
    return raw.split(b'\0', 1)[0].decode('ascii', 'replace').strip() or None


# -- finding the installs ------------------------------------------------

def looks_like_ja2(folder):
    """A JA2 install has the Data folder full of .slf archives."""
    if not folder or not os.path.isdir(folder):
        return False
    data = os.path.join(folder, 'Data')
    if not os.path.isdir(data):
        return False
    return bool(glob.glob(os.path.join(data, '*.slf')))


_JA2_TAILS = (
    'Jagged Alliance 2',
    'Jagged Alliance 2 Wildfire',
    'Jagged Alliance 2 Gold',
    os.path.join('GOG Games', 'Jagged Alliance 2'),
    os.path.join('GOG Games', 'Jagged Alliance 2 Gold'),
    os.path.join('Games', 'Jagged Alliance 2'),
    os.path.join('SteamLibrary', 'steamapps', 'common', 'Jagged Alliance 2'),
    os.path.join('Program Files (x86)', 'Steam', 'steamapps', 'common',
                 'Jagged Alliance 2'),
)


def ja2_candidates():
    """Everywhere a JA2 install plausibly sits, remembered choice first."""
    out = []
    saved = gamepaths.load_settings().get('ja2_dir')
    if saved:
        out.append(saved)
    for drive in string.ascii_uppercase:
        root = drive + ':' + os.sep
        if not os.path.isdir(root):
            continue
        for tail in _JA2_TAILS:
            out.append(os.path.join(root, tail))
    return out


_JA2_CACHE = None


def ja2_dir(rescan=False):
    """The JA2 install folder, or None. Remembers what it finds."""
    global _JA2_CACHE
    if _JA2_CACHE is not None and not rescan:
        return _JA2_CACHE
    for folder in ja2_candidates():
        if looks_like_ja2(folder):
            _JA2_CACHE = folder
            data = gamepaths.load_settings()
            if data.get('ja2_dir') != folder:
                data['ja2_dir'] = folder
                gamepaths.save_settings(data)
            return folder
    _JA2_CACHE = None
    return None


def ja2_dirs():
    """Every JA2 install found, including one nested inside another.

    Wildfire contains "Jagged Alliance 2 Classic", which is a separate build
    with its own saves - so nested installs are normal here, not a mistake.
    """
    found, seen = [], set()
    for folder in ja2_candidates():
        if not looks_like_ja2(folder):
            continue
        key = os.path.normcase(os.path.abspath(folder))
        if key in seen:
            continue
        seen.add(key)
        found.append(folder)
        try:
            for sub in sorted(os.listdir(folder)):
                p = os.path.join(folder, sub)
                if looks_like_ja2(p):
                    k = os.path.normcase(os.path.abspath(p))
                    if k not in seen:
                        seen.add(k)
                        found.append(p)
        except OSError:
            pass
    return found


def set_ja2_dir(folder):
    """Point the editor at a JA2 install by hand. Returns (ok, message)."""
    global _JA2_CACHE
    if not folder:
        return False, 'no folder given'
    folder = os.path.abspath(folder)
    # tolerate someone picking Data or SavedGames inside the install
    for candidate in (folder, os.path.dirname(folder)):
        if looks_like_ja2(candidate):
            data = gamepaths.load_settings()
            data['ja2_dir'] = candidate
            gamepaths.save_settings(data)
            _JA2_CACHE = candidate
            return True, candidate
    return False, ('%s does not look like a Jagged Alliance 2 install '
                   '(no Data folder with .slf archives)' % folder)


def installs():
    """{game id: folder} for everything we can find."""
    out = {}
    ja3 = gamepaths.game_dir()
    if ja3:
        out[JA3] = ja3
    ja2 = ja2_dir()
    if ja2:
        out[JA2] = ja2
    return out


def data_dir(game):
    """Where a game keeps its packed data - Packs for JA3, Data for JA2."""
    if game == JA3:
        root = gamepaths.game_dir()
        return os.path.join(root, 'Packs') if root else None
    if game == JA2:
        root = ja2_dir()
        return os.path.join(root, 'Data') if root else None
    return None


# -- finding the saves ---------------------------------------------------

def save_roots():
    """[(folder, game id)] for every folder saves might live in.

    JA3 keeps saves in the user profile; JA2 keeps them inside the install,
    which is why this cannot be one list of paths with one rule.
    """
    out = [(d, JA3) for d in gamepaths.save_roots()]
    for install in ja2_dirs():
        out.append((os.path.join(install, 'SavedGames'), JA2))
    return out
