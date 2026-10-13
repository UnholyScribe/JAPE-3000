"""Local HTTP API behind the editor UI.

Binds to 127.0.0.1 on an ephemeral port. Nothing leaves the machine: the
server only reads and writes save files the user points it at.
"""
import glob
import json
import mimetypes
import os
import posixpath
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

import gamepaths
import ja3_save
import livegame

def resource_root():
    """Where bundled data lives: the PyInstaller temp dir when frozen,
    otherwise this source folder."""
    return getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))


HERE = resource_root()
WEBUI = os.path.join(HERE, 'webui')

NEW_UI_MARKER = 'NEW-UI.txt'


def app_dir():
    """Where the executable actually sits - not the PyInstaller temp folder."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_skin():
    """Which look this build opens on.

    A frozen build serves the `webui` folder embedded *inside* the exe, so
    rewriting the index.html sitting next to it changes nothing - that copy
    is never read. The edition therefore has to be decided at run time, by a
    marker file shipped alongside the executable.

    A choice the user makes in the app always wins over this; the marker only
    sets the starting point.
    """
    try:
        for name in os.listdir(app_dir()):
            if name.lower() == NEW_UI_MARKER.lower():
                return 'aim'
    except OSError:
        pass
    return 'modern'

# One save is kept open at a time; edits queue up on it until the UI saves.
_lock = threading.Lock()
_open = {'path': None, 'save': None}


# Which games this build can open. Adding a reader is one line here, and the
# UI follows automatically - it never hardcodes a game name of its own.
def openable_games():
    """Games this build can LOAD. Whether a save can be CHANGED is a separate
    question, answered by `read_only` on the save itself - a JA2 save from an
    unverified build opens fine and refuses writes."""
    import games
    return {games.JA3, games.JA2}


def save_dirs():
    """[(folder, game id)] for every folder that actually holds saves.

    JA3 keeps saves under the user profile and one level down (per Steam id);
    JA2 keeps them inside the install. games.save_roots() knows both, so this
    only has to work out which of those folders really contain anything.
    """
    import games
    out = []
    for root, game in games.save_roots():
        if not os.path.isdir(root):
            continue
        if glob.glob(os.path.join(root, '*.sav')):
            out.append((root, game))
        try:
            subs = sorted(os.listdir(root))
        except OSError:
            continue
        for sub in subs:
            p = os.path.join(root, sub)
            if os.path.isdir(p) and glob.glob(os.path.join(p, '*.sav')):
                out.append((p, game))
    return out


def list_saves(extra_dirs=()):
    """Every save we can see, newest first, each tagged with its game.

    The game comes from reading the file, not from the folder it sits in - a
    save someone dragged somewhere odd still identifies itself correctly.
    The folder is only the fallback when the file is unreadable.
    """
    import games
    seen, out = set(), []
    pairs = list(save_dirs()) + [(d, None) for d in extra_dirs]
    for d, folder_game in pairs:
        for p in sorted(glob.glob(os.path.join(d, '*.sav'))):
            rp = os.path.realpath(p)
            if rp in seen:
                continue
            seen.add(rp)
            st = os.stat(p)
            game = games.detect(p) or folder_game
            # JA2 names the FILE by slot - SaveGame01.sav - and keeps what the
            # player actually called it inside. Showing the filename would be
            # showing a slot number, so read the real name out of the header.
            label = os.path.basename(p).replace('.savegame.sav', '')
            if game == games.JA2:
                try:
                    import ja2_save
                    real = ja2_save.Save(p).name
                    if real:
                        label = real
                except Exception:                                # noqa: BLE001
                    pass          # unreadable save: fall back to the filename
            out.append({
                'path': p,
                'name': label,
                'file': os.path.basename(p),
                'dir': d,
                'size': st.st_size,
                'mtime': st.st_mtime,
                'game': game,
                'game_short': games.SHORT.get(game, ''),
                # the full name, so the front end can label a window
                # without holding a list of game names of its own
                'game_title': games.TITLES.get(game, ''),
                'build': games.build_string(p) if game == games.JA2 else None,
                'openable': game in openable_games(),
            })
    out.sort(key=lambda d: d['mtime'], reverse=True)
    return out


# Jagged Alliance 3 ships its own UI typeface, and the editor can wear it - but
# it is Haemimont's font, not ours to redistribute. So it is never bundled: it
# is read out of the copy of the game already on this machine and served to the
# local page. Nothing leaves the machine, nothing extra ships, and a build with
# no game installed simply falls back to the stack in the stylesheet.
GAME_FONTS = {
    'ja3-ui': 'HMGothic-Regular.ttf',
    'ja3-mono': 'SourceCodePro-Regular.ttf',
}
_font_cache = {}


def game_font(key):
    """The bytes of one of the game's fonts, or None if we cannot get it."""
    if key in _font_cache:
        return _font_cache[key]
    name = GAME_FONTS.get(key)
    got = None
    if name:
        try:
            import hpk
            root = gamepaths.game_dir()
            if root:
                pack = os.path.join(root, 'Packs', 'Fonts.hpk')
                if os.path.exists(pack):
                    p = hpk.Pack(pack)
                    for entry, fid in p.walk():
                        if entry == name:
                            got = p.read_id(fid)
                            break
        except Exception:                                    # noqa: BLE001
            got = None            # a missing font is cosmetic, never fatal
    _font_cache[key] = got
    return got


# -- the black box ------------------------------------------------------
#
# Not a screenshot every minute: that is a lot of pictures of nothing happening,
# and the moment that matters falls between two of them. This records only when
# something FAILS, and records state rather than an image - a few kilobytes of
# text that can be searched, instead of megabytes that have to be looked at.
#
# It also catches the failure that has cost the most time on this project: the
# command that reports success and changes nothing. A crash announces itself; a
# silent no-op looks exactly like success.
DIAG = 'jape-diagnostics.log'
DIAG_MAX = 2 * 1024 * 1024        # keep the last couple of MB, then start over


def diag_path():
    """Beside the settings file, never beside the code.

    A frozen build runs from PyInstaller's temp folder, which is deleted when
    the app closes - so a log written next to __file__ would be gone by the
    time anyone went looking for it, and it would look as though nothing had
    ever been recorded.
    """
    try:
        return os.path.join(os.path.dirname(gamepaths.settings_path()), DIAG)
    except Exception:                                        # noqa: BLE001
        return os.path.join(app_dir(), DIAG)


def diag_write(record):
    """Append one failure record. Never raises - a broken logger must not be
    the thing that breaks the editor."""
    if gamepaths.writes_stopped():
        return None
    try:
        import datetime
        path = diag_path()
        if os.path.exists(path) and os.path.getsize(path) > DIAG_MAX:
            os.replace(path, path + '.1')
        record = dict(record)
        record['at'] = datetime.datetime.now().isoformat(timespec='seconds')
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, default=str) + chr(10))
        return path
    except Exception:                                        # noqa: BLE001
        return None


def require_writable(save):
    """Refuse to change a save this build can only read.

    Most saves are writable. A JA2 save from a build whose layout has not been
    checked is not, and in that case the save itself carries the reason -
    which is more use to somebody than a generic refusal.
    """
    if getattr(save, 'read_only', False):
        why = getattr(save, 'blocked_because', '')
        raise ja3_save.Ja3Error(
            ('This save can be viewed but not changed: %s.' % why) if why
            else 'This save can be viewed but not changed.')
    return save


def get_save(path, reload=False):
    """Open a save, picking the reader from what the file actually is.

    The file decides, never a mode or a toggle. Both readers expose the same
    summary/queue/apply/write protocol, so nothing downstream of here needs to
    know which game it is holding.
    """
    import games
    game = games.detect(path)
    if game == games.JA2:
        import ja2_save
        with _lock:
            if reload or _open['path'] != path or _open['save'] is None:
                _open['save'] = ja2_save.Save(path)
                _open['path'] = path
            return _open['save']
    with _lock:
        if reload or _open['path'] != path or _open['save'] is None:
            _open['save'] = ja3_save.Save(path)
            _open['path'] = path
        return _open['save']


class Handler(BaseHTTPRequestHandler):
    server_version = 'JA3Editor'

    def log_message(self, *a):
        pass

    # -- plumbing --------------------------------------------------------
    def _send(self, code, body, ctype='application/json; charset=utf-8',
              cache='no-store'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def _fail(self, exc):
        self._send(400, {'error': str(exc), 'trace': traceback.format_exc()})

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == '/api/saves':
                return self._send(200, {'dirs': [d for d, _g in save_dirs()],
                                        'saves': list_saves()})
            if u.path == '/api/live-status':
                return self._send(200, livegame.status())
            if u.path == '/api/diag-path':
                return self._send(200, {'path': diag_path()})
            if u.path == '/api/live-catalog':
                import ja3se
                return self._send(200, ja3se.summary())
            if u.path == '/api/gamefont':
                blob = game_font((q.get('name') or [''])[0])
                if not blob:
                    return self._send(404, b'', 'font/ttf')
                return self._send(200, blob, 'font/ttf')
            if u.path == '/api/catalog':
                return self._send(200, ja3_save.load_catalog())
            if u.path == '/api/save':
                s = get_save(q['path'][0], reload='reload' in q)
                out = s.summary()
                # A reader that decodes a field wrongly produces no error at
                # all - it produces a save that looks fine and is not. So on
                # every open, ask whether anything here is impossible, and put
                # what comes back in the log without waiting to be asked.
                try:
                    odd = s.implausible() if hasattr(s, 'implausible') else []
                except Exception as exc:                          # noqa: BLE001
                    odd = ['the plausibility check itself failed: %s' % exc]
                if odd:
                    diag_write({'kind': 'implausible-data',
                                'save': getattr(s, 'path', None),
                                'count': len(odd), 'found': odd[:40]})
                return self._send(200, out)
            if u.path == '/api/screenshot':
                s = get_save(q['path'][0])
                shot = s.screenshot()
                if not shot:
                    return self._send(404, {'error': 'no screenshot'})
                return self._send(200, shot, 'image/jpeg')
            if u.path == '/api/facepic':
                import portraits
                got = portraits.read_assignments().get(q['merc'][0])
                if not got:
                    return self._send(404, {'error': 'no custom portrait'})
                rel = got['head'].split('/')[-1]
                full = os.path.join(portraits.store_dir(), 'Images', rel)
                if not os.path.isfile(full):
                    return self._send(404, {'error': 'missing file'})
                with open(full, 'rb') as f:
                    return self._send(200, f.read(), 'image/png')
            if u.path == '/api/sector':
                import ja3se
                return self._send(200, ja3se.sector())
            if u.path == '/api/modimage':
                return self._mod_image(q['path'][0])
            if u.path == '/api/pending':
                s = get_save(q['path'][0])
                return self._send(200, {'intents': s.intents})
            return self._static(u.path)
        except Exception as e:                                   # noqa: BLE001
            return self._fail(e)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            data = self._body()
            if u.path == '/api/live-send':
                op = data.get('op') or 'ping'
                value = data.get('value')
                if op in ('setmoney', 'addmoney'):
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        return self._send(200, {'ok': False,
                                                'message': 'that is not a whole number'})
                    if not -2000000000 <= value <= 2000000000:
                        return self._send(200, {'ok': False,
                                                'message': 'that number is out of range'})
                seq = livegame.send(op, value)
                ok, message = livegame.wait_for(seq)
                return self._send(200, {'ok': ok, 'message': message,
                                        'status': livegame.status()})
            if u.path == '/api/live-install':
                import build_livelink
                ok, message, path = build_livelink.install()
                return self._send(200, {'ok': ok, 'message': message,
                                        'path': path, 'status': livegame.status()})
            if u.path == '/api/live-uninstall':
                # The extender is part of the live link, so this takes both.
                import removal
                ok, message = removal.remove_live_service()
                return self._send(200, {'ok': ok, 'message': message,
                                        'status': livegame.status()})
            if u.path == '/api/se-install':
                import build_livelink
                import ja3se_install
                if not build_livelink.state()[0]:
                    return self._send(200, {
                        'ok': False, 'status': livegame.status(),
                        'message': 'The script extender is an optional part of '
                                   'the live link. Install the add-on first.'})
                ok, message, path = ja3se_install.install()
                return self._send(200, {'ok': ok, 'message': message,
                                        'path': path, 'status': livegame.status()})
            if u.path == '/api/se-uninstall':
                import ja3se_install
                ok, message, path = ja3se_install.uninstall()
                return self._send(200, {'ok': ok, 'message': message,
                                        'path': path, 'status': livegame.status()})
            if u.path == '/api/removal-plan':
                import removal
                return self._send(200, {
                    'groups': removal.plan(
                        include_backups=bool(data.get('backups'))),
                    'exe': sys.executable if getattr(sys, 'frozen', False)
                    else None})
            if u.path == '/api/remove-everything':
                import removal
                if data.get('confirm') != 'REMOVE':
                    return self._send(400, {'error': 'not confirmed'})
                return self._send(200, removal.remove_everything(
                    include_backups=bool(data.get('backups'))))
            if u.path == '/api/diag':
                return self._send(200, {'path': diag_write(data)})
            if u.path == '/api/queue':
                s = require_writable(get_save(data['path']))
                for intent in data['intents']:
                    kind = intent.pop('kind')
                    s.queue(kind, **intent)
                return self._send(200, {'pending': len(s.intents)})
            if u.path == '/api/discard':
                s = require_writable(get_save(data['path'], reload=True))
                return self._send(200, {'pending': 0})
            if u.path == '/api/apply':
                s = require_writable(get_save(data['path']))
                for intent in data.get('intents', []):
                    kind = intent.pop('kind')
                    s.queue(kind, **intent)
                log = s.apply()
                result = s.write(data.get('out') or None,
                                 backup=data.get('backup', True))
                get_save(result['path'], reload=True)
                return self._send(200, {'log': log, 'result': result})
            if u.path == '/api/preview':
                s = require_writable(get_save(data['path']))
                for intent in data.get('intents', []):
                    kind = intent.pop('kind')
                    s.queue(kind, **intent)
                log = s.apply()
                get_save(data['path'], reload=True)   # throw the edits away
                return self._send(200, {'log': log})
            if u.path == '/api/export':
                s = get_save(data['path'])
                return self._send(200, {'data': s.export_merc(
                    data['merc'], with_gear=data.get('gear', True))})
            if u.path == '/api/check-import':
                s = get_save(data['path'])
                problems, warnings = s.check_import(data['data'], data['target'])
                return self._send(200, {'problems': problems, 'warnings': warnings})
            if u.path == '/api/save-file':
                return self._send(200, {'path': _save_file(
                    data.get('name', 'merc.json'), data.get('text', ''))})
            if u.path == '/api/open-file':
                return self._send(200, _open_text_file())
            if u.path == '/api/backups':
                import backups
                return self._send(200, {'backups': backups.backups_for(data['path'])})
            if u.path == '/api/backup-peek':
                import backups
                return self._send(200, backups.describe(data['backup']))
            if u.path == '/api/backup-restore':
                import backups
                ok, res = backups.restore(data['backup'], data['path'])
                if ok:
                    get_save(data['path'], reload=True)
                return self._send(200, {'ok': ok, 'result': res})
            if u.path == '/api/backup-delete':
                import backups
                ok, res = backups.delete(data['backup'], data['path'])
                return self._send(200, {'ok': ok, 'result': res,
                                        'backups': backups.backups_for(data['path'])})
            if u.path == '/api/folders':
                import gamepaths
                import mods
                game = gamepaths.game_dir()
                return self._send(200, {
                    'game_dir': game,
                    'game_store': gamepaths.store_of(game),
                    'searching': gamepaths.searching(),
                    'mods_dir': gamepaths.mods_dir(),
                    'mods_dir_custom': bool(
                        gamepaths.load_settings().get('mods_dir')),
                    'workshop_dirs': mods.workshop_roots(),
                    'legacy_mods_dir': gamepaths.legacy_mods_dir(),
                    'save_roots': gamepaths.save_roots(),
                    'save_dirs': [d for d, _g in save_dirs()],
                    'extra_save_dirs': gamepaths.load_settings().get('save_dirs') or [],
                    'settings_file': gamepaths.settings_path(),
                    'ui_pack': gamepaths.ui_pack(),
                })
            if u.path == '/api/find-game':
                import gamepaths
                found = gamepaths.game_dir(rescan=True)
                if found:
                    # the game's own library may hold Workshop mods
                    ja3_save.load_catalog(rescan=True)
                return self._send(200, {'ok': bool(found), 'game_dir': found,
                                        'searching': gamepaths.searching(),
                                        'mods_dir': gamepaths.mods_dir()})
            if u.path == '/api/mods-reset':
                import gamepaths
                gamepaths.clear_mods_dir()
                ja3_save.load_catalog(rescan=True)
                return self._send(200, {'ok': True,
                                        'mods_dir': gamepaths.mods_dir()})
            if u.path == '/api/add-save-dir':
                import gamepaths
                folder = _pick_folder('Where are your Jagged Alliance 3 saves?')
                if not folder:
                    return self._send(200, {'ok': False, 'message': 'cancelled'})
                ok, msg = gamepaths.add_save_dir(folder)
                return self._send(200, {'ok': ok, 'message': msg,
                                        'saves': list_saves()})
            if u.path == '/api/forget-save-dir':
                import gamepaths
                gamepaths.forget_save_dir(data['folder'])
                return self._send(200, {'ok': True, 'saves': list_saves()})
            if u.path == '/api/game-status':
                import gamepaths
                return self._send(200, {
                    'game_dir': gamepaths.game_dir(),
                    'searching': gamepaths.searching(),
                    'mods_dir': gamepaths.mods_dir(),
                })
            if u.path == '/api/game-pick':
                import gamepaths
                folder = _pick_folder()
                if not folder:
                    return self._send(200, {'ok': False, 'message': 'cancelled'})
                ok, msg = gamepaths.set_game_dir(folder)
                if ok:
                    ja3_save.load_catalog(rescan=True)
                return self._send(200, {'ok': ok, 'message': msg,
                                        'game_dir': gamepaths.game_dir(),
                                        'mods_dir': gamepaths.mods_dir()})
            if u.path == '/api/mods-pick':
                import gamepaths
                folder = _pick_folder()
                if not folder:
                    return self._send(200, {'ok': False, 'message': 'cancelled'})
                ok, msg = gamepaths.set_mods_dir(folder)
                if ok:
                    ja3_save.load_catalog(rescan=True)
                return self._send(200, {'ok': ok, 'message': msg,
                                        'mods_dir': gamepaths.mods_dir()})
            if u.path == '/api/install-mod':
                import mods
                ok, msg = mods.install_mod(data['folder'])
                if ok:
                    ja3_save.load_catalog(rescan=True)
                return self._send(200, {'ok': ok, 'message': msg})
            if u.path == '/api/portrait-status':
                import portraits
                return self._send(200, portraits.status())
            if u.path == '/api/portrait-pick':
                return self._send(200, {'path': _pick_image()})
            if u.path == '/api/portrait-preview':
                import portraits
                if not data.get('image') or data['image'] != _PICKED.get('image'):
                    return self._send(400, {'error': 'choose the picture first'})
                try:
                    return self._send(200, portraits.preview(
                        data['image'], focus=data.get('focus')))
                except portraits.PortraitError as exc:
                    return self._send(400, {'error': str(exc)})
            if u.path == '/api/portrait-assign':
                import portraits
                try:
                    portraits.assign(data['merc'], data['image'],
                                     focus=data.get('focus'))
                except portraits.PortraitError as exc:
                    return self._send(400, {'error': str(exc)})
                in_game, msg = portraits.apply()
                return self._send(200, dict(portraits.status(),
                                            in_game=in_game, message=msg))
            if u.path == '/api/portrait-remove':
                import portraits
                portraits.unassign(data['merc'])
                in_game, msg = portraits.apply()
                return self._send(200, dict(portraits.status(),
                                            in_game=in_game, message=msg))
            if u.path == '/api/rescan-mods':
                cat = ja3_save.load_catalog(rescan=True)
                return self._send(200, {'mods': cat.get('mods', []),
                                        'mercs': len(cat['mercs']),
                                        'perks': len(cat['perks']),
                                        'items': len(cat['items'])})
            if u.path == '/api/mod-conflicts':
                import modconflicts
                import mods as mods_mod
                return self._send(200, modconflicts.analyse(mods_mod.scan()))
            if u.path == '/api/game-log':
                import gamelog
                import mods as mods_mod
                cat = ja3_save.load_catalog()
                return self._send(200, gamelog.status(cat.get('mods') or []))
            if u.path == '/api/browse':
                return self._send(200, {'path': _pick_file()})
            if u.path == '/api/reveal':
                path = data['path']
                os.startfile(os.path.dirname(os.path.abspath(path)))  # noqa: S606
                return self._send(200, {'ok': True})
            return self._send(404, {'error': 'no route %s' % u.path})
        except Exception as e:                                   # noqa: BLE001
            return self._fail(e)

    def _mod_image(self, ref):
        """A mod's own image, addressed the way the game does it:
        Mod/<modid>/Images/whatever.png"""
        parts = ref.replace(os.sep, '/').split('/')
        if len(parts) < 2 or parts[0] != 'Mod':
            return self._send(400, {'error': 'not a mod image path'})
        mod_id, rel = parts[1], '/'.join(parts[2:])
        if '..' in rel:
            return self._send(403, {'error': 'nope'})
        import mods
        for m in ja3_save.load_catalog().get('mods', []):
            if m.get('id') != mod_id:
                continue
            data = mods.read_mod_bytes(m['folder'], rel)
            if data is not None:
                ctype = mimetypes.guess_type(rel)[0] or 'image/png'
                return self._send(200, data, ctype)
        return self._send(404, {'error': 'no such mod image: %s' % ref})

    # -- static ----------------------------------------------------------
    def _static(self, path):
        rel = posixpath.normpath(unquote(path)).lstrip('/') or 'index.html'
        if rel.startswith('..'):
            return self._send(403, {'error': 'nope'})
        if rel in ('portraits/index.json', 'ja2icons/index.json'):
            return self._send(200, _art_index(rel))
        full = os.path.join(WEBUI, rel.replace('/', os.sep))
        if not os.path.isfile(full):
            import ja2art
            full = ja2art.file_for(rel) or full
        if not os.path.isfile(full):
            # only bare routes fall back to the app shell; a missing asset
            # must fail loudly rather than masquerade as an image
            if '.' in posixpath.basename(rel):
                return self._send(404, {'error': 'no such file: %s' % rel})
            full = os.path.join(WEBUI, 'index.html')
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript',
                                                  'text/javascript'):
            ctype += '; charset=utf-8'
        with open(full, 'rb') as f:
            body = f.read()
        if os.path.basename(full).lower() == 'index.html':
            skin = default_skin()
            if skin != 'modern':
                body = body.replace(b'data-default-skin="modern"',
                                    b'data-default-skin="' + skin.encode() + b'"')
        # Every redraw rebuilds the roster, so without this each click asks
        # for every face again at once. Pictures do not change while the
        # editor runs; custom portraits come from api/facepic, not here.
        cache = ('max-age=3600' if ctype.startswith('image/')
                 and rel.split('/')[0] in ('portraits', 'ja2icons', 'icons')
                 else 'no-store')
        return self._send(200, body, ctype, cache)


def _art_index(rel):
    """The picture indexes the front end loads: what ships in the exe (JA3)
    plus whatever ja2art decoded from this machine's JA2."""
    import ja2art
    out = {}
    try:
        with open(os.path.join(WEBUI, rel.replace('/', os.sep)), encoding='utf-8') as f:
            out.update(json.load(f))
    except (OSError, ValueError):
        pass
    got = ja2art.manifest() or {}
    out.update(got.get('faces' if rel.startswith('portraits') else 'icons') or {})
    return out


def _pick_file():
    """Native file picker, run on the calling thread (tk needs a main loop)."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        dirs = save_dirs()
        initial = dirs[0][0] if dirs else os.path.expanduser('~')
        p = filedialog.askopenfilename(
            title='Open a Jagged Alliance 3 save',
            initialdir=initial,
            filetypes=[('JA3 savegame', '*.sav'), ('All files', '*.*')])
        root.destroy()
        return p or None
    except Exception:                                            # noqa: BLE001
        return None


def _pick_folder(title='Where is Jagged Alliance 3 installed?'):
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        p = filedialog.askdirectory(title=title)
        root.destroy()
        return p or None
    except Exception:                                            # noqa: BLE001
        return None


_PICKED = {}     # the one picture the preview may read: whatever was last chosen


def _pick_image():
    p = _ask_image()
    if p:
        _PICKED['image'] = p
    return p


def _ask_image():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        p = filedialog.askopenfilename(
            title='Choose a picture for this merc',
            filetypes=[('Images', '*.png *.jpg *.jpeg *.bmp *.webp *.gif'),
                       ('All files', '*.*')])
        root.destroy()
        return p or None
    except Exception:                                            # noqa: BLE001
        return None


def _save_file(suggested, text):
    """Native save dialog, then write the text there."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        p = filedialog.asksaveasfilename(
            title='Save merc file', initialfile=suggested, defaultextension='.json',
            filetypes=[('Merc file', '*.json'), ('All files', '*.*')])
        root.destroy()
        if not p:
            return None
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        return p
    except Exception:                                            # noqa: BLE001
        return None


def _open_text_file():
    """Native open dialog, returning the file's text."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        p = filedialog.askopenfilename(
            title='Open a merc file',
            filetypes=[('Merc file', '*.json'), ('All files', '*.*')])
        root.destroy()
        if not p:
            return {'path': None, 'text': None}
        with open(p, encoding='utf-8') as f:
            return {'path': p, 'text': f.read()}
    except Exception as exc:                                     # noqa: BLE001
        return {'path': None, 'text': None, 'error': str(exc)}


def start(port=0):
    httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    import ja2art
    ja2art.start_background()
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, 'http://127.0.0.1:%d/' % httpd.server_address[1]


if __name__ == '__main__':
    httpd, url = start(8765)
    print('serving %s' % url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
