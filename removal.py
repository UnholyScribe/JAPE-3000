"""Taking the editor, the live link and the script extender off a machine.

Two operations, and neither ever runs on its own:

  remove_live_service()   the live link add-on AND the script extender, with
                          every file either has written. The extender is an
                          optional part of the live link, so removing the link
                          takes both - a tester once pressed Remove on the
                          add-on, reasonably believed that covered everything,
                          and watched the extender stay.

  remove_everything()     the above, plus everything the editor itself keeps:
                          settings, diagnostics log, the window's browser
                          cache and custom portraits, the portraits mod older
                          versions wrote, and temp folders left
                          by earlier runs. Save backups only when asked.

What is NOT removed, and cannot be from inside: JAPE3000.exe itself, which is
running. The result names it so the person can delete it by hand - there is no
installer and nothing else to find.

`plan()` lists every path either would touch, present or not, so "what has
this put on my machine?" has a complete answer before anything is deleted.
Every function takes its folders as arguments so the whole thing can be
rehearsed somewhere other than a real machine; a destructive path that cannot
be rehearsed will be rehearsed on somebody's machine.
"""
import glob
import os
import re
import shutil
import sys
import tempfile

import gamepaths

SETTINGS_FILES = ('ja3editor-settings.json', 'ja3editor-settings-test.json')
DIAG_FILES = ('jape-diagnostics.log', 'jape-diagnostics.log.1')
LOCAL_DIRS = ('JAPE 3000', 'JAPE 3000 (test build)')
PORTRAIT_MOD = 'JA3SaveEditorFaces'

BACKUP_RE = re.compile(r'\.sav\.\d{8}-\d{6}\.bak$', re.I)
MEI_RE = re.compile(r'^_MEI\w+$')
# Present in every one-file build of the editor and in no other program's.
MEI_SIGNATURE = ('webui', 'webview')

# Folders remove_everything() could not take while the window was open.
PENDING_ON_EXIT = []


def _roaming(appdata=None):
    return appdata or os.environ.get('APPDATA') or os.path.expanduser('~')


def _local(local=None):
    return local or os.environ.get('LOCALAPPDATA') or os.path.join(
        os.path.expanduser('~'), 'AppData', 'Local')


def _item(path, what, kind='file'):
    exists = os.path.isdir(path) if kind == 'folder' else os.path.isfile(path)
    return {'path': path, 'what': what, 'kind': kind, 'exists': exists}


def _in_use(folder):
    """True when something still has files open inside `folder`.

    Windows refuses to rename a folder while anything inside it is open, and a
    rename back to the same name changes nothing. Checked rather than assumed,
    because the other copy of the editor - a test build runs beside the real
    one - would lose the page it is serving if its folder were swept.
    """
    probe = folder + '.jape-probe'
    try:
        os.rename(folder, probe)
    except OSError:
        return True
    try:
        os.rename(probe, folder)
    except OSError:
        pass
    return False


def stale_temp_folders(temp=None, current=None):
    """PyInstaller unpack folders from earlier runs of the editor.

    Each run unpacks itself into %TEMP%\\_MEI<n> and deletes it on exit - unless
    the process is ended rather than closed, in which case it stays for good.
    Only folders carrying the editor's own signature are offered, never the
    one this process is running from, and never one still in use.
    """
    temp = temp or tempfile.gettempdir()
    current = os.path.normcase(os.path.abspath(
        current if current is not None else getattr(sys, '_MEIPASS', '') or '-'))
    out = []
    try:
        names = os.listdir(temp)
    except OSError:
        return out
    for name in names:
        p = os.path.join(temp, name)
        if not MEI_RE.match(name) or not os.path.isdir(p):
            continue
        if os.path.normcase(os.path.abspath(p)) == current:
            continue
        if not all(os.path.isdir(os.path.join(p, s)) for s in MEI_SIGNATURE):
            continue
        out.append(p)
    return sorted(out)


def old_webview_folders(temp=None):
    """Browser caches older builds left in %TEMP% (tmpXXXXXXXX\\EBWebView).

    Before the editor gave its window a fixed cache folder, pywebview made a
    random one on every launch and never removed it. Any pywebview program
    makes the same shape, so these are offered only by the full removal, and
    only when nothing is using them.
    """
    temp = temp or tempfile.gettempdir()
    out = []
    for p in glob.glob(os.path.join(temp, 'tmp*')):
        if os.path.isdir(os.path.join(p, 'EBWebView')):
            out.append(p)
    return sorted(out)


def backup_files(roots=None):
    """Every `<save>.<stamp>.bak` the editor made, in every save folder."""
    if roots is None:
        try:
            import games
            roots = [r for r, _g in games.save_roots()]
        except Exception:                                        # noqa: BLE001
            roots = gamepaths.save_roots()
    out, seen = [], set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for pattern in ('*.bak', os.path.join('*', '*.bak')):
            for p in glob.glob(os.path.join(root, pattern)):
                key = os.path.normcase(os.path.abspath(p))
                if key in seen or not BACKUP_RE.search(p):
                    continue
                seen.add(key)
                out.append(p)
    return sorted(out)


def live_service_items(game_dir=None, appdata=None):
    """[(group title, [items])] for the live link and the extender."""
    import build_livelink
    groups = [('Live link add-on',
               [dict(path=p, exists=e, what=w, kind='file')
                for p, e, w in build_livelink.owned_files(game_dir, appdata)])]
    try:
        import ja3se_install
        groups.append(('Script extender (part of the live link)',
                       [dict(path=p, exists=e, what=w, kind='file')
                        for p, e, w in ja3se_install.owned_files(game_dir,
                                                                 appdata)]))
    except Exception:                                            # noqa: BLE001
        pass
    return groups


def plan(include_backups=False, game_dir=None, appdata=None, roaming=None,
         local=None, temp=None, mods=None, save_roots=None):
    """Every path remove_everything() would delete, grouped, present or not.

    `appdata` is the game's AppData folder; `roaming` is %APPDATA% itself,
    where the editor's own settings sit.
    """
    groups = [{'title': t, 'items': items, 'optional': False}
              for t, items in live_service_items(game_dir, appdata)]

    roam = _roaming(roaming)
    editor = [_item(os.path.join(roam, n), 'editor settings')
              for n in SETTINGS_FILES]
    editor += [_item(os.path.join(roam, n), 'diagnostics log')
               for n in DIAG_FILES]
    editor += [_item(os.path.join(_local(local), n),
                     'window cache, and JA2 pictures read from your JA2 install', 'folder')
               for n in LOCAL_DIRS]
    groups.append({'title': 'The editor itself', 'items': editor,
                   'optional': False})

    mods = mods if mods is not None else gamepaths.mods_dir()
    if mods:
        groups.append({'title': 'Portraits mod from an older version',
                       'optional': False,
                       'items': [_item(os.path.join(mods, PORTRAIT_MOD),
                                       'older versions kept custom faces in '
                                       'a mod; this one keeps them in the '
                                       'live link instead', 'folder')]})

    temps = [_item(p, 'left by an earlier run', 'folder')
             for p in stale_temp_folders(temp)]
    temps += [_item(p, 'throwaway browser cache - older builds of the editor '
                       'left these, and so can other programs built the same '
                       'way; nothing in use is touched', 'folder')
              for p in old_webview_folders(temp)]
    groups.append({'title': 'Temporary folders', 'items': temps,
                   'optional': False})

    baks = [_item(p, 'a backup of a save, made before an edit')
            for p in backup_files(save_roots)]
    groups.append({'title': 'Save backups (.bak)', 'items': baks,
                   'optional': True, 'included': bool(include_backups)})
    return groups


def _delete(item):
    """(ok, error). A folder that is in use is skipped, not half-deleted."""
    p = item['path']
    try:
        if item['kind'] == 'folder':
            if not os.path.isdir(p):
                return True, None
            shutil.rmtree(p)
        else:
            if not os.path.isfile(p):
                return True, None
            os.remove(p)
        return True, None
    except OSError as exc:
        return False, str(exc)


def remove_live_service(game_dir=None, appdata=None):
    """Both DLC files and everything they wrote. Returns (ok, message)."""
    import build_livelink
    ok, msg, _p = build_livelink.uninstall(game_dir, purge=True,
                                           appdata=appdata)
    parts = ['Live link: ' + msg]
    try:
        import ja3se_install
        se_ok, se_msg, _p = ja3se_install.uninstall(game_dir, purge=True,
                                                    appdata=appdata)
        parts.append('Script extender: ' + se_msg)
        ok = ok and se_ok
    except Exception as exc:                                     # noqa: BLE001
        parts.append('Script extender: could not check it (%s)' % exc)
        ok = False
    return ok, ' '.join(parts)


def remove_everything(include_backups=False, **where):
    """Delete everything plan() lists. Returns a report dict.

    Stops the editor writing anything afterwards, so the settings file it just
    deleted is not recreated by the next poll.
    """
    groups = plan(include_backups=include_backups, **where)
    # The window's own cache is open for as long as the window is, so it can
    # only go once the window closes; ja3_gui does that on exit.
    own_cache = os.path.join(_local(where.get('local')),
                             LOCAL_DIRS[1 if gamepaths.is_test_build() else 0])
    removed, failed, kept, on_exit = [], [], [], []
    for g in groups:
        if g.get('optional') and not g.get('included'):
            kept += [i['path'] for i in g['items'] if i['exists']]
            continue
        for item in g['items']:
            if not item['exists']:
                continue
            p = item['path']
            # Never rmtree a folder something is using: it deletes whatever
            # is not locked and leaves the rest broken, which for a running
            # window or a second copy of the editor is worse than nothing.
            if item['kind'] == 'folder' and _in_use(p):
                if os.path.normcase(p) == os.path.normcase(own_cache):
                    on_exit.append(p)
                else:
                    kept.append(p)
                continue
            ok, err = _delete(item)
            (removed if ok else failed).append(
                p if ok else '%s (%s)' % (p, err))

    gamepaths.stop_writing()
    PENDING_ON_EXIT[:] = on_exit
    exe = sys.executable if getattr(sys, 'frozen', False) else None
    return {'removed': removed, 'failed': failed, 'kept': kept, 'exe': exe,
            'on_exit': on_exit}


def clean_cache_on_exit(path, attempts=20, wait=0.25):
    """Delete the window cache after the window has closed. WebView2's helper
    processes let go a moment after the window does, so retry briefly."""
    import time
    for _ in range(attempts):
        if not os.path.isdir(path):
            return True
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.isdir(path):
            return True
        time.sleep(wait)
    return not os.path.isdir(path)


def sweep_stale_temp(temp=None, current=None):
    """Remove editor temp folders from runs that were killed. Quiet and safe:
    anything in use is left alone. Returns the folders removed."""
    gone = []
    for p in stale_temp_folders(temp, current):
        if _in_use(p):
            continue
        shutil.rmtree(p, ignore_errors=True)
        if not os.path.isdir(p):
            gone.append(p)
    return gone
