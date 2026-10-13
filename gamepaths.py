"""Where the game and its mods live, and a place to remember it.

The engine writes mod paths as `AppData/Mods`, and `ConvertToOSPath` resolves
that `AppData` prefix against the *user* data folder, not the install folder:

    %APPDATA%/Jagged Alliance 3/Mods        (Windows)

That folder does not exist until something creates it, which is what makes the
install directory such an easy wrong guess - you can put mods in <game>/Mods,
and the Mod Manager will simply never list them. Confirmed against the in-game
Mod Manager: six mods in the AppData folder, six rows on screen.

<game>/Mods is still scanned, read-only, so anyone who already made that
mistake sees their mods here with a note about where they belong.

Mods live as folders, each holding either a metadata.lua or a ModContent.hpk
with the metadata.lua inside it. Steam Workshop mods are the packed kind and
stay in Steam's own folder, <library>/steamapps/workshop/content/1084160/<id>;
the game reads them from there. A .rar or .zip sitting in Downloads is not
installed, and the game will not see it.
"""
import json
import os
import string
import sys

SETTINGS_NAME = 'ja3editor-settings.json'


def is_test_build():
    """True when running the freshly built exe still sitting in dist/.

    Test builds and the trusted build would otherwise share one settings file
    - the game folder, mods folder and save folders you have set up - so an
    experiment could quietly break the copy you rely on. Anything in dist/ is
    treated as disposable and keeps its own settings.
    """
    if not getattr(sys, 'frozen', False):
        return False
    here = os.path.basename(os.path.dirname(os.path.abspath(sys.executable)))
    return here.lower() == 'dist'


def settings_path():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    name = SETTINGS_NAME
    if is_test_build():
        name = name.replace('.json', '-test.json')
    return os.path.join(base, name)


def local_data_dir():
    """Where the editor keeps machine-local scratch - the window's browser
    cache. One known folder, so removing the editor can name it and take it.

    Left to itself pywebview gives WebView2 a fresh random folder in %TEMP%
    on every launch and never deletes it: forty runs, forty folders.
    """
    base = (os.environ.get('LOCALAPPDATA')
            or os.path.join(os.path.expanduser('~'), 'AppData', 'Local'))
    name = 'JAPE 3000 (test build)' if is_test_build() else 'JAPE 3000'
    return os.path.join(base, name)


# Set once the editor has removed itself from this machine. From then on
# nothing may write a settings file back - the game finder saves what it finds,
# and would quietly recreate the file the user just asked us to delete.
_WRITES_OFF = False


def stop_writing():
    global _WRITES_OFF
    _WRITES_OFF = True


def writes_stopped():
    return _WRITES_OFF


def load_settings():
    try:
        with open(settings_path(), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    if _WRITES_OFF:
        return False
    try:
        with open(settings_path(), 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=1)
        return True
    except OSError:
        return False


def looks_like_game(folder):
    """A JA3 install has the executable and the Packs folder."""
    if not folder or not os.path.isdir(folder):
        return False
    return (os.path.isfile(os.path.join(folder, 'JA3.exe'))
            or os.path.isdir(os.path.join(folder, 'Packs')))


# Steam's app id for JA3, from ModTools/Src/Lua/UpdateMsg.lua's store link.
STEAM_APPID = '1084160'


def steam_roots():
    """Where the Steam client itself is installed, from its registry keys."""
    if os.name != 'nt':
        return []
    try:
        import winreg
    except ImportError:
        return []
    keys = ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam',
             'InstallPath'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam', 'InstallPath'))
    out = []
    for hive, sub, name in keys:
        try:
            with winreg.OpenKey(hive, sub) as k:
                value = winreg.QueryValueEx(k, name)[0]
        except OSError:
            continue
        if isinstance(value, str) and value:
            out.append(os.path.normpath(value))
    return out


def _vdf_library_paths(text):
    """Library folders named in Steam's libraryfolders.vdf.

    Every library is a block holding `"path"  "D:\\\\SteamLibrary"`, with the
    backslashes escaped. Older files list them as `"1"  "D:\\\\SteamLibrary"`
    instead, so both shapes are read.
    """
    import re
    found = re.findall(r'"path"\s+"((?:[^"\\]|\\.)*)"', text)
    found += re.findall(r'^\s*"\d+"\s+"((?:[^"\\]|\\.)*)"\s*$', text, re.M)
    return [p.replace('\\\\', '\\') for p in found]


def steam_libraries():
    r"""Every Steam library folder on this machine - the folders that hold a
    `steamapps`. Steam lets a game and its Workshop content live on any of
    them, so a fixed list of drive paths misses most real setups.

    Found from the registry, then from each client's libraryfolders.vdf, then
    the usual drive-root names as a fallback when Steam left no trace.
    """
    out, seen = [], set()

    def add(path):
        if not path:
            return
        key = os.path.normcase(os.path.normpath(path))
        if key in seen or not os.path.isdir(os.path.join(path, 'steamapps')):
            return
        seen.add(key)
        out.append(os.path.normpath(path))

    for root in steam_roots():
        add(root)
        for vdf in (os.path.join(root, 'steamapps', 'libraryfolders.vdf'),
                    os.path.join(root, 'config', 'libraryfolders.vdf')):
            try:
                with open(vdf, encoding='utf-8', errors='replace') as f:
                    text = f.read()
            except OSError:
                continue
            for lib in _vdf_library_paths(text):
                add(lib)
    for drive in string.ascii_uppercase:
        root = '%s:\\' % drive
        if not os.path.isdir(root):
            continue
        for tail in ('SteamLibrary', r'Program Files (x86)\Steam',
                     r'Program Files\Steam', 'Steam'):
            add(os.path.join(root, tail))
    return out


def steam_game_dirs():
    """JA3 installs inside Steam libraries. The app manifest names the folder,
    so a renamed install folder is still found."""
    import re
    out = []
    for lib in steam_libraries():
        apps = os.path.join(lib, 'steamapps')
        name = 'Jagged Alliance 3'
        try:
            with open(os.path.join(apps, 'appmanifest_%s.acf' % STEAM_APPID),
                      encoding='utf-8', errors='replace') as f:
                m = re.search(r'"installdir"\s+"([^"]+)"', f.read())
            if m:
                name = m.group(1)
        except OSError:
            pass
        out.append(os.path.join(apps, 'common', name))
    return out


def steam_library_of(folder):
    r"""The library a folder sits in, if it is <lib>\steamapps\common\<game>."""
    if not folder:
        return None
    parts = os.path.normpath(folder).split(os.sep)
    low = [p.lower() for p in parts]
    for i in range(len(low) - 1, 0, -1):
        if low[i] == 'common' and low[i - 1] == 'steamapps':
            lib = os.sep.join(parts[:i - 1])
            if lib.endswith(':'):
                lib += os.sep
            return lib or None
    return None


# GOG's product id for JA3 (goggame-1556263729.info in a GOG install).
GOG_ID = '1556263729'
GAME_NAME = 'Jagged Alliance 3'


def registry_game_dirs():
    """Where the installers say they put the game, on whatever drive.

    GOG records every game under GOG.com\\Games. Both GOG and Steam also
    write a Windows "installed programs" entry - `<GOG id>_is1` and
    `Steam App 1084160` - with the install folder in it.
    """
    if os.name != 'nt':
        return []
    try:
        import winreg
    except ImportError:
        return []
    HKLM, HKCU = winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER

    def value(key, name):
        try:
            v = winreg.QueryValueEx(key, name)[0]
        except OSError:
            return None
        return v if isinstance(v, str) and v.strip() else None

    def subkeys(hive, path):
        try:
            with winreg.OpenKey(hive, path) as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumKey(k, i)
                    except OSError:
                        return
                    i += 1
                    try:
                        with winreg.OpenKey(k, name) as sub:
                            yield name, sub
                    except OSError:
                        continue
        except OSError:
            return

    def is_ja3(title):
        t = (title or '').strip().lower()
        return t == GAME_NAME.lower() or t.startswith(GAME_NAME.lower() + ' ') \
            or t.startswith(GAME_NAME.lower() + ':')

    out = []
    for hive, path in ((HKLM, r'SOFTWARE\WOW6432Node\GOG.com\Games'),
                       (HKLM, r'SOFTWARE\GOG.com\Games')):
        for name, k in subkeys(hive, path):
            if name == GOG_ID or is_ja3(value(k, 'gameName')):
                out.append(value(k, 'path'))
    for hive, path in (
            (HKLM, r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'),
            (HKLM, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'),
            (HKCU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')):
        for name, k in subkeys(hive, path):
            if (name in (GOG_ID + '_is1', 'Steam App ' + STEAM_APPID)
                    or is_ja3(value(k, 'DisplayName'))):
                out.append(value(k, 'InstallLocation'))
    return [os.path.normpath(p.strip().strip('"')) for p in out if p]


def store_of(folder):
    """'steam', 'gog' or None for an install folder."""
    if not folder or not os.path.isdir(folder):
        return None
    if steam_library_of(folder):
        return 'steam'
    try:
        names = [n.lower() for n in os.listdir(folder)]
    except OSError:
        return None
    # GOG's build ships steam_api64.dll too, so its own marker is checked first
    if any(n.startswith('goggame-') for n in names):
        return 'gog'
    if 'steam_appid.txt' in names or 'steam_api64.dll' in names:
        return 'steam'
    return None


def candidates():
    """Everywhere a JA3 install plausibly sits, cheapest checks first.

    Nothing here is a required location: the folder you chose, then what GOG,
    Windows and Steam recorded when the game was installed. The drive-root
    names at the end are only cheap guesses for an install no installer
    recorded; when all of these miss, game_dir() searches the drives.
    """
    out = []
    saved = load_settings().get('game_dir')
    if saved:
        out.append(saved)
    out += registry_game_dirs()
    out += steam_game_dirs()
    for drive in string.ascii_uppercase:
        root = '%s:\\' % drive
        if not os.path.isdir(root):
            continue
        for tail in (GAME_NAME, r'GOG Games\Jagged Alliance 3',
                     r'Games\Jagged Alliance 3'):
            out.append(os.path.join(root, tail))
    return out


# Folders no game is installed in, and which are slow or noisy to walk.
_SKIP = {'windows', '$recycle.bin', 'system volume information', 'programdata',
         'recovery', '$windows.~bt', '$windows.~ws', 'perflogs', 'appdata',
         'node_modules', '.git', 'winsxs', 'msocache', 'config.msi'}


def fixed_drives():
    """Local hard drives only - not DVD drives, card readers or network shares,
    which can each hang a search for minutes."""
    out = []
    try:
        import ctypes
        get_type = ctypes.windll.kernel32.GetDriveTypeW
    except (ImportError, AttributeError):
        get_type = None
    for drive in string.ascii_uppercase:
        root = '%s:\\' % drive
        if get_type is not None:
            if get_type(root) != 3:                      # DRIVE_FIXED
                continue
        elif not os.path.isdir(root):
            continue
        out.append(root)
    return out


def search_drives(roots=None, max_depth=5, time_limit=60.0):
    """Last resort: walk every hard drive for a folder holding JA3.exe.

    Shallow first, so an install near a drive root is found in seconds even
    on a full disk. Needs JA3.exe itself - a Packs folder alone is shared by
    other Haemimont games. Returns the first match, or None.
    """
    import time
    deadline = time.monotonic() + time_limit
    level = [r for r in (roots if roots is not None else fixed_drives())]
    for _depth in range(max_depth + 1):
        nxt = []
        for folder in level:
            if time.monotonic() > deadline:
                return None
            try:
                with os.scandir(folder) as it:
                    entries = list(it)
            except OSError:
                continue
            for e in entries:
                if e.name.lower() == 'ja3.exe' and e.is_file():
                    if os.path.isdir(os.path.join(folder, 'Packs')):
                        return folder
            for e in entries:
                try:
                    if (e.is_dir(follow_symlinks=False)
                            and e.name.lower() not in _SKIP
                            and not e.name.startswith('.')):
                        nxt.append(e.path)
                except OSError:
                    continue
        level = nxt
        if not level:
            break
    return None


_GAME_CACHE = None
# True once a full search came back empty, so the editor does not walk every
# drive again on each screen refresh. "Search again" clears it.
_GAME_MISSING = False
_SEARCH = None          # the background drive search, while it runs
_SEARCH_LOCK = __import__('threading').Lock()


def _remember(found):
    global _GAME_CACHE, _GAME_MISSING
    _GAME_CACHE, _GAME_MISSING = found, found is None
    if found:
        data = load_settings()
        if data.get('game_dir') != found:
            data['game_dir'] = found
            save_settings(data)


def searching():
    """True while the drive search is running."""
    s = _SEARCH
    return s is not None and s.is_alive()


def _start_search():
    """Walk the drives on a background thread. A full walk took 108 s on a
    machine with eight drives - far too long to hold a screen open for."""
    global _SEARCH
    import threading
    with _SEARCH_LOCK:
        if searching():
            return _SEARCH

        def run():
            found = search_drives(time_limit=600.0)
            # the user may have picked a folder by hand meanwhile
            if _GAME_CACHE is None:
                _remember(found)

        _SEARCH = threading.Thread(target=run, name='find-game', daemon=True)
        _SEARCH.start()
        return _SEARCH


def game_dir(rescan=False, wait=False):
    """The install folder, or None. Remembers what it finds.

    The quick places are checked straight away. If none has the game, the
    drive search starts in the background and this returns None until it
    lands - pass wait=True (the command line does) to block for it instead.
    """
    if not rescan:
        if _GAME_CACHE is not None:
            return _GAME_CACHE
        if _GAME_MISSING:
            return None
    found = next((f for f in candidates() if looks_like_game(f)), None)
    if found is not None:
        _remember(found)
        return found
    if rescan or not searching():
        globals()['_GAME_MISSING'] = False
        _start_search()
    if wait:
        s = _SEARCH
        if s is not None:
            s.join()
        return _GAME_CACHE
    return None


def set_game_dir(folder):
    """Point the editor at an install by hand. Returns (ok, message)."""
    global _GAME_CACHE, _GAME_MISSING
    if not folder:
        return False, 'no folder given'
    folder = os.path.abspath(folder)
    # tolerate someone picking the Mods folder, or a mod inside it
    for candidate in (folder, os.path.dirname(folder),
                      os.path.dirname(os.path.dirname(folder))):
        if looks_like_game(candidate):
            data = load_settings()
            data['game_dir'] = candidate
            save_settings(data)
            _GAME_CACHE, _GAME_MISSING = candidate, False
            return True, candidate
    return False, ('%s does not look like a Jagged Alliance 3 install '
                   '(no JA3.exe or Packs folder)' % folder)


def user_data_dir():
    """The folder the engine means by `AppData`. Windows for now; the other
    two are what the same engine uses elsewhere, ready for a Linux build."""
    if os.name == 'nt':
        base = os.environ.get('APPDATA')
        if base:
            return os.path.join(base, 'Jagged Alliance 3')
        return os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming',
                            'Jagged Alliance 3')
    home = os.path.expanduser('~')
    if sys.platform == 'darwin':
        return os.path.join(home, 'Library', 'Application Support',
                            'Jagged Alliance 3')
    base = os.environ.get('XDG_DATA_HOME') or os.path.join(home, '.local',
                                                           'share')
    return os.path.join(base, 'Jagged Alliance 3')


def mods_dir(create=False):
    """Where mods belong, and where the editor installs them.

    A folder the user pointed us at wins; otherwise the engine's own location.
    """
    override = load_settings().get('mods_dir')
    if override and os.path.isdir(override):
        return override
    path = os.path.join(user_data_dir(), 'Mods')
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def legacy_mods_dir():
    """<game>/Mods - a common wrong guess. Read only; the game ignores it."""
    game = game_dir()
    return os.path.join(game, 'Mods') if game else None


def mods_dirs():
    """Every folder worth scanning, the real one first."""
    out, seen = [], set()
    for path in (mods_dir(), legacy_mods_dir()):
        if not path:
            continue
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and os.path.isdir(path):
            seen.add(key)
            out.append(path)
    return out


def set_mods_dir(folder):
    """Point the editor at a Mods folder by hand."""
    if not folder or not os.path.isdir(folder):
        return False, 'that folder does not exist'
    # tolerate someone picking a mod rather than the folder holding them
    if (os.path.isfile(os.path.join(folder, 'metadata.lua'))
            or os.path.isfile(os.path.join(folder, 'ModContent.hpk'))):
        folder = os.path.dirname(folder)
    data = load_settings()
    data['mods_dir'] = os.path.abspath(folder)
    save_settings(data)
    return True, data['mods_dir']


def clear_mods_dir():
    """Forget a hand-picked Mods folder and go back to the game's own."""
    data = load_settings()
    if data.pop('mods_dir', None) is not None:
        save_settings(data)
    return True


def save_roots():
    """Everywhere savegames plausibly live, plus anything the user added."""
    home = os.path.expanduser('~')
    out = list(load_settings().get('save_dirs') or [])
    out += [os.path.join(home, 'Saved Games', 'Jagged Alliance 3'),
            os.path.join(home, 'Documents', 'My Games', 'Jagged Alliance 3'),
            os.path.join(home, 'Documents', 'Jagged Alliance 3')]
    # OneDrive relocates Documents and Saved Games without asking
    one = os.environ.get('OneDrive') or os.path.join(home, 'OneDrive')
    if os.path.isdir(one):
        out += [os.path.join(one, 'Saved Games', 'Jagged Alliance 3'),
                os.path.join(one, 'Documents', 'My Games', 'Jagged Alliance 3')]
    seen, keep = set(), []
    for d in out:
        real = os.path.normcase(os.path.abspath(d))
        if real not in seen:
            seen.add(real)
            keep.append(d)
    return keep


def add_save_dir(folder):
    """Remember a folder to look for saves in. Returns (ok, message)."""
    import glob
    if not folder or not os.path.isdir(folder):
        return False, 'that folder does not exist'
    folder = os.path.abspath(folder)
    found = glob.glob(os.path.join(folder, '*.sav'))
    if not found:
        # maybe they picked the parent, holding one folder per profile id
        sub = [d for d in glob.glob(os.path.join(folder, '*'))
               if os.path.isdir(d) and glob.glob(os.path.join(d, '*.sav'))]
        if not sub:
            return False, 'no .savegame.sav files in there, or in its subfolders'
    data = load_settings()
    dirs = list(data.get('save_dirs') or [])
    if folder not in dirs:
        dirs.insert(0, folder)
    data['save_dirs'] = dirs
    save_settings(data)
    return True, folder


def forget_save_dir(folder):
    data = load_settings()
    data['save_dirs'] = [d for d in (data.get('save_dirs') or [])
                         if os.path.normcase(d) != os.path.normcase(folder)]
    save_settings(data)
    return True


def ui_pack():
    game = game_dir()
    if not game:
        return None
    path = os.path.join(game, 'Packs', 'UI.hpk')
    return path if os.path.isfile(path) else None
