"""Removing the live link, the extender and the editor. No game required.

Everything is rehearsed on a fabricated machine inside a temp folder: a game
install, the game's AppData, %APPDATA%, %LOCALAPPDATA%, %TEMP%, a Mods folder
and a save folder. Nothing here can touch a real one.
"""
import os
import sys
import tempfile

import build_livelink
import gamepaths
import ja3se_install
import removal

RESULTS = []


def check(name, cond, detail=''):
    print('%-4s %s%s' % ('ok' if cond else 'FAIL', name,
                         ('  <- ' + detail) if detail and not cond else ''))
    RESULTS.append(bool(cond))


def touch(*parts):
    p = os.path.join(*parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write('x')
    return p


def machine(root):
    """A fake machine with every kind of file the editor leaves, plus some
    that are not ours and must survive."""
    m = {k: os.path.join(root, k) for k in
         ('game', 'appdata', 'roaming', 'local', 'temp', 'mods', 'saves')}
    for p in m.values():
        os.makedirs(p)
    touch(m['game'], 'JA3.exe')
    m['live'] = os.path.join(m['game'], 'DLC', build_livelink.HPK)
    m['se'] = os.path.join(m['game'], 'DLC', ja3se_install.HPK)
    with open(touch(m['live']), 'wb') as f:
        f.write(build_livelink.pack())
    with open(touch(m['se']), 'wb') as f:
        f.write(ja3se_install.pack())
    for n in build_livelink.OWNED_APPDATA + ja3se_install.OWNED_APPDATA:
        touch(m['appdata'], n)
    touch(m['appdata'], 'nemesis-step1.png')
    m['foreign'] = touch(m['appdata'], 'a-testers-own-notes.txt')
    m['game_save'] = touch(m['appdata'], 'Mods', 'SomeoneElsesMod', 'metadata.lua')
    for n in removal.SETTINGS_FILES + removal.DIAG_FILES:
        touch(m['roaming'], n)
    touch(m['local'], 'JAPE 3000', 'WebView2', 'EBWebView', 'Local State')
    touch(m['mods'], removal.PORTRAIT_MOD, 'metadata.lua')
    m['other_mod'] = touch(m['mods'], 'SomeoneElsesMod', 'metadata.lua')
    m['stale'] = os.path.join(m['temp'], '_MEI1001')
    touch(m['stale'], 'webui', 'app.js')
    os.makedirs(os.path.join(m['stale'], 'webview'))
    m['busy'] = os.path.join(m['temp'], '_MEI1002')
    touch(m['busy'], 'webui', 'app.js')
    os.makedirs(os.path.join(m['busy'], 'webview'))
    m['current'] = os.path.join(m['temp'], '_MEI1003')
    touch(m['current'], 'webui', 'app.js')
    os.makedirs(os.path.join(m['current'], 'webview'))
    m['not_ours'] = os.path.dirname(touch(m['temp'], '_MEI2000', 'other.dll'))
    m['old_cache'] = os.path.dirname(os.path.dirname(
        touch(m['temp'], 'tmpab12cd', 'EBWebView', 'Local State')))
    m['save'] = touch(m['saves'], 'Quick.savegame.sav')
    m['bak'] = touch(m['saves'], 'Quick.savegame.sav.20260927-101112.bak')
    return m


def where(m):
    return dict(game_dir=m['game'], appdata=m['appdata'], roaming=m['roaming'],
                local=m['local'], temp=m['temp'], mods=m['mods'],
                save_roots=[m['saves']])


def test_manifest_guard():
    check('live link: every AppData file the Lua writes is listed',
          build_livelink.unlisted_appdata() == [],
          str(build_livelink.unlisted_appdata()))
    # A guard nobody has watched trip is not a guard.
    check('live link: the guard names an unlisted file',
          build_livelink.unlisted_appdata('x("AppData/nemesis-new.txt")')
          == ['nemesis-new.txt'])
    check('live link: the guard names a built name no pattern covers',
          build_livelink.unlisted_appdata('x("AppData/other-" .. n)')
          == ['other-...'])


def test_live_service(root):
    m = machine(root)
    ok, msg = removal.remove_live_service(m['game'], m['appdata'])
    check('remove live link: ok', ok, msg)
    check('remove live link: the add-on is gone', not os.path.exists(m['live']))
    check('remove live link: the extender goes with it',
          not os.path.exists(m['se']))
    left = sorted(n for n in os.listdir(m['appdata'])
                  if n.startswith(('nemesis-', 'ja3se-')))
    check('remove live link: every file either wrote is gone', left == [],
          str(left))
    check('remove live link: foreign files survive',
          os.path.exists(m['foreign']) and os.path.exists(m['game_save']))

    touch(m['appdata'], 'nemesis-state.txt')
    ok, msg, _p = build_livelink.uninstall(m['game'], appdata=m['appdata'])
    check('remove live link: cleans up after a hand-deleted pack',
          ok and not os.path.exists(os.path.join(m['appdata'],
                                                 'nemesis-state.txt')), msg)
    ok, msg, _p = build_livelink.uninstall(m['game'], appdata=m['appdata'])
    check('remove live link: twice is harmless', ok and 'not installed' in msg,
          msg)


def test_install_is_atomic(root):
    game = os.path.join(root, 'game')
    touch(game, 'JA3.exe')
    ok, msg, path = build_livelink.install(game)
    check('live install: writes the pack, leaves no .part',
          ok and os.path.getsize(path) == len(build_livelink.pack())
          and not os.path.exists(path + '.part'), msg)

    with open(path, 'wb') as f:
        f.write(b'the old add-on')
    real = os.replace

    def refuse(*_a, **_k):
        raise PermissionError('held open')
    os.replace = refuse
    try:
        ok, msg, _p = build_livelink.install(game)
    finally:
        os.replace = real
    check('live install: a failed write leaves the old add-on intact',
          not ok and open(path, 'rb').read() == b'the old add-on', msg)
    check('live install: a failed write leaves no .part behind',
          not os.path.exists(path + '.part'))


def test_everything(root, backups):
    m = machine(root)
    w = where(m)
    busy = open(os.path.join(m['busy'], 'webui', 'app.js'))
    try:
        groups = removal.plan(include_backups=backups, **w)
        listed = {i['path'] for g in groups for i in g['items'] if i['exists']}
        check('plan: lists the stale temp folder', m['stale'] in listed)
        check('plan: never offers a temp folder that is not ours',
              m['not_ours'] not in listed)
        check('plan: never touches a save', m['save'] not in listed)

        r = removal.remove_everything(include_backups=backups, **w)
    finally:
        busy.close()
    try:
        tag = 'with backups' if backups else 'without backups'
        check('everything (%s): nothing failed' % tag, not r['failed'],
              str(r['failed']))
        check('everything (%s): both DLC files gone' % tag,
              not os.path.exists(m['live']) and not os.path.exists(m['se']))
        check('everything (%s): settings and log gone' % tag,
              not any(os.path.exists(os.path.join(m['roaming'], n))
                      for n in removal.SETTINGS_FILES + removal.DIAG_FILES))
        check('everything (%s): window cache gone' % tag,
              not os.path.exists(os.path.join(m['local'], 'JAPE 3000')))
        check('everything (%s): portrait mod gone, other mods kept' % tag,
              not os.path.exists(os.path.join(m['mods'], removal.PORTRAIT_MOD))
              and os.path.exists(m['other_mod']))
        check('everything (%s): stale temp folder gone' % tag,
              not os.path.exists(m['stale']))
        check('everything (%s): a temp folder in use is left alone' % tag,
              os.path.exists(os.path.join(m['busy'], 'webui', 'app.js')))
        check('everything (%s): a temp folder that is not ours is left' % tag,
              os.path.exists(m['not_ours']))
        check('everything (%s): old browser caches gone' % tag,
              not os.path.exists(m['old_cache']))
        check('everything (%s): the save itself is untouched' % tag,
              os.path.exists(m['save']))
        check('everything (%s): backups %s' % (tag, 'gone' if backups else 'kept'),
              os.path.exists(m['bak']) != backups)
        check('everything (%s): the editor stops writing settings' % tag,
              gamepaths.save_settings({'game_dir': 'x'}) is False)
    finally:
        gamepaths._WRITES_OFF = False


def test_sweep(root):
    m = machine(root)
    gone = removal.sweep_stale_temp(m['temp'], current=m['current'])
    check('sweep: removes the stale folder', m['stale'] in gone)
    check('sweep: never the folder this run is using',
          os.path.isdir(m['current']))
    check('sweep: never a folder that is not ours', os.path.isdir(m['not_ours']))


def main():
    test_manifest_guard()
    for fn in (test_live_service, test_install_is_atomic,
               lambda d: test_everything(d, False),
               lambda d: test_everything(d, True), test_sweep):
        with tempfile.TemporaryDirectory() as d:
            fn(d)
    passed = sum(RESULTS)
    print('\n%d passed, %d failed' % (passed, len(RESULTS) - passed))
    return 0 if passed == len(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
