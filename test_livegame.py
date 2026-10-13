"""Tests for the editor half of the live link. No game required."""
import os
import sys
import tempfile
import time

import livegame


def check(name, cond, detail=''):
    print('%-4s %s%s' % ('ok' if cond else 'FAIL', name, ('  <- ' + detail) if detail and not cond else ''))
    return bool(cond)


def write_state(folder, text):
    with open(os.path.join(folder, livegame.STATE_FILE), 'w', encoding='utf-8') as f:
        f.write(text)


def main():
    passed = failed = 0
    results = []
    with tempfile.TemporaryDirectory() as d:
        # nothing there yet
        results.append(check('no state file -> None', livegame.read_state(d) is None))
        results.append(check('status says never', livegame.status(d, game_dir=d)['state'] == 'never'))

        # a normal report
        write_state(d, 'seq=4\ningame=1\nmp=0\nmoney=12345\nresult=ok: connected\n')
        st = livegame.read_state(d)
        results.append(check('parses seq', st['seq'] == 4, repr(st)))
        results.append(check('parses money', st['money'] == 12345, repr(st)))
        results.append(check('parses in_game', st['in_game'] is True))
        results.append(check('parses multiplayer', st['multiplayer'] is False))
        results.append(check('fresh when just written', st['fresh'] is True))
        results.append(check('status live', livegame.status(d, game_dir=d)['state'] == 'live'))
        results.append(check('can_edit true', livegame.status(d, game_dir=d)['can_edit'] is True))

        # -- the squad bag -------------------------------------------------
        #
        # The panel showed HAVE 15 while the running game held 115, because
        # bag_add could write live and nothing could read, so it quietly fell
        # back to the save file under a lit LIVE badge. The reader that fixes
        # that leans on ABSENT and EMPTY meaning different things:
        #
        #   no bag= key  -> the add-on predates the reader. The save's figure
        #                   is all there is, so leave it alone.
        #   bag=         -> the game reported, and its bag is empty. Say zero.
        #
        # Collapse those two and the silent fallback comes straight back.
        results.append(check('no bag key -> None, so the save figure stands',
                             livegame.read_state(d)['bag'] is None,
                             repr(livegame.read_state(d)['bag'])))

        write_state(d, 'seq=5\ningame=1\nmp=0\nmoney=1\nresult=\nbag=\n')
        results.append(check('an empty bag is [] and NOT None',
                             livegame.read_state(d)['bag'] == [],
                             repr(livegame.read_state(d)['bag'])))

        write_state(d, 'seq=6\ningame=1\nmp=0\nmoney=1\nresult=\n'
                       'bag=Meds:58,Parts:115,_9mm_Basic:128\n')
        bag = livegame.read_state(d)['bag']
        results.append(check('every entry is read',
                             [b['item'] for b in bag]
                             == ['Meds', 'Parts', '_9mm_Basic'], repr(bag)))
        results.append(check('amounts come back as numbers',
                             [b['amount'] for b in bag] == [58, 115, 128],
                             repr(bag)))

        write_state(d, 'seq=7\ningame=1\nmp=0\nmoney=1\nresult=\n'
                       'bag=Parts:15,,Meds:lots,\n')
        bag = livegame.read_state(d)['bag']
        results.append(check('a blank between commas is dropped, not an item',
                             len(bag) == 2, repr(bag)))
        results.append(check('an unreadable amount counts as one, not zero',
                             bag[1] == {'item': 'Meds', 'amount': 1}, repr(bag)))

        # multiplayer must lock editing out
        write_state(d, 'seq=4\ningame=1\nmp=1\nmoney=1\nresult=\n')
        results.append(check('multiplayer blocks editing',
                             livegame.status(d, game_dir=d)['can_edit'] is False))

        # at the main menu there is no campaign
        write_state(d, 'seq=4\ningame=0\nmp=0\nmoney=\nresult=\n')
        s = livegame.status(d, game_dir=d)
        results.append(check('no campaign blocks editing', s['can_edit'] is False))
        results.append(check('money None when blank', livegame.read_state(d)['money'] is None))

        # stale timestamp = game gone
        p = os.path.join(d, livegame.STATE_FILE)
        old = time.time() - (livegame.STALE_AFTER + 5)
        os.utime(p, (old, old))
        results.append(check('old file is stale', livegame.read_state(d)['fresh'] is False))
        results.append(check('status stale', livegame.status(d, game_dir=d)['state'] == 'stale'))

        # sending commands
        seq1 = livegame.send('ping', folder=d)
        results.append(check('first seq is 1', seq1 == 1, str(seq1)))
        seq2 = livegame.send('setmoney', 999, folder=d)
        results.append(check('seq increments', seq2 == 2, str(seq2)))
        with open(os.path.join(d, livegame.CMD_FILE), encoding='utf-8') as f:
            body = f.read()
        results.append(check('command has op', 'op=setmoney' in body, body))
        results.append(check('command has value', 'value=999' in body, body))
        results.append(check('no temp file left', not os.path.exists(
            os.path.join(d, livegame.CMD_FILE + '.tmp'))))

        # seq survives an editor restart (it is read back off disk)
        results.append(check('seq continues after restart', livegame.send('ping', folder=d) == 3))

        # acknowledgement
        write_state(d, 'seq=3\ningame=1\nmp=0\nmoney=5\nresult=ok: money is now 5\n')
        ok, msg = livegame.wait_for(3, timeout=1, folder=d)
        results.append(check('wait_for accepts ack', ok and 'money' in msg, msg))

        write_state(d, 'seq=4\ningame=1\nmp=0\nmoney=5\nresult=refused: multiplayer game\n')
        ok, msg = livegame.wait_for(4, timeout=1, folder=d)
        results.append(check('refusal is not ok', ok is False, msg))

        write_state(d, 'seq=5\ningame=0\nmp=0\nmoney=\nresult=error: no campaign loaded\n')
        ok, msg = livegame.wait_for(5, timeout=1, folder=d)
        results.append(check('error is not ok', ok is False, msg))

        # no answer at all
        ok, msg = livegame.wait_for(99, timeout=0.4, folder=d)
        results.append(check('timeout reports clearly', ok is False and 'running' in msg, msg))

        # --- the mod list -------------------------------------------
        T = livegame.TAB
        rows = [
            T.join(['JA3_CommonLib', '1', '1', 'JA3 CommonLib']),
            T.join(['kkiLFvQ', '1', '0', 'John Wick']),      # on, not applied yet
            T.join(['epGDHgh', '0', '1', 'Make IMP Great Again']),  # off, still running
            T.join(['zzz', '0', '0', 'Something Off']),
            'rubbish line with no tabs',
        ]
        with open(os.path.join(d, livegame.MODS_FILE), 'w', encoding='utf-8') as f:
            f.write(chr(10).join(rows))
        mods = livegame.read_mods(d)
        results.append(check('parses the mod list', len(mods) == 4, str(len(mods))))
        results.append(check('skips malformed lines', all(m['id'] for m in mods)))
        by = {m['id']: m for m in mods}
        results.append(check('reads enabled', by['JA3_CommonLib']['enabled'] is True))
        results.append(check('reads loaded', by['JA3_CommonLib']['loaded'] is True))
        results.append(check('settled mod is not pending', by['JA3_CommonLib']['pending'] is False))
        results.append(check('on-but-not-loaded is pending', by['kkiLFvQ']['pending'] is True))
        results.append(check('off-but-still-loaded is pending', by['epGDHgh']['pending'] is True))
        results.append(check('off and unloaded is not pending', by['zzz']['pending'] is False))
        results.append(check('sorted by title',
                             [m['title'] for m in mods][0] == 'JA3 CommonLib',
                             str([m['title'] for m in mods])))

        # mods show up in status, and can be switched at the main menu
        write_state(d, chr(10).join(['seq=9', 'ingame=0', 'mp=0', 'money=', 'result=', '']))
        st = livegame.status(d, game_dir=d)
        results.append(check('status carries mods', len(st['mods']) == 4))
        results.append(check('counts pending mods', st['mods_pending'] == 2, str(st['mods_pending'])))
        results.append(check('mods switchable at the menu', st['can_mod'] is True))
        results.append(check('money still locked at the menu', st['can_edit'] is False))

        # multiplayer locks mods too
        write_state(d, chr(10).join(['seq=9', 'ingame=1', 'mp=1', 'money=1', 'result=', '']))
        results.append(check('multiplayer blocks mod switching',
                             livegame.status(d, game_dir=d)['can_mod'] is False))

        # no mod file yet
        os.remove(os.path.join(d, livegame.MODS_FILE))
        results.append(check('missing mod file is empty, not an error',
                             livegame.read_mods(d) == []))

        # ---- the script extender: removing it has to MEAN something.
        # A tester removed it, the editor put it back on the next poll, and he
        # lost two days unable to get a clean game to test another mod against.
        # 297 checks passed throughout, because none of them asked whether a
        # removal survives.
        import ja3se_install
        game = os.path.join(d, 'game')
        os.makedirs(os.path.join(game, 'DLC'))
        open(os.path.join(game, 'JA3.exe'), 'wb').close()
        appdata = os.path.join(d, 'appdata')
        os.makedirs(appdata)
        hpk = os.path.join(game, 'DLC', 'ja3se.hpk')

        with open(hpk, 'wb') as f:
            f.write(ja3se_install.pack())
        for name in ja3se_install.OWNED_APPDATA:
            with open(os.path.join(appdata, name), 'w') as f:
                f.write('x')
        theirs = os.path.join(appdata, 'a-testers-own-notes.txt')
        with open(theirs, 'w') as f:
            f.write('not ours')

        ok, _msg, _p = ja3se_install.uninstall(game, appdata=appdata)
        results.append(check('extender: uninstall removes it',
                             ok and not os.path.exists(hpk)))
        results.append(check('extender: uninstall takes its own files too',
                             not any(os.path.exists(os.path.join(appdata, n))
                                     for n in ja3se_install.OWNED_APPDATA)))
        results.append(check('extender: a purge leaves foreign files alone',
                             os.path.exists(theirs)))

        # Nothing remembers the removal any more, because nothing needs to:
        # polling never writes. So this holds with no settings file at all,
        # which is the case the remembered flag could not cover - a second
        # copy of the editor, or settings deleted while tidying up.
        for _ in range(3):
            livegame.status(d, game_dir=game)
        results.append(check('extender: removal survives reopening the editor',
                             not os.path.exists(hpk),
                             'polling put it back'))
        results.append(check('extender: a clean machine is never installed onto',
                             not os.listdir(os.path.join(game, 'DLC')),
                             str(os.listdir(os.path.join(game, 'DLC')))))

        # A pack deleted by hand still leaves its files; Remove must take them.
        for name in ja3se_install.OWNED_APPDATA[:3]:
            with open(os.path.join(appdata, name), 'w') as f:
                f.write('x')
        ok, msg, _p = ja3se_install.uninstall(game, appdata=appdata)
        results.append(check('extender: remove cleans up after a hand-deleted pack',
                             ok and not any(
                                 os.path.exists(os.path.join(appdata, n))
                                 for n in ja3se_install.OWNED_APPDATA), msg))

        ok, _msg, _p = ja3se_install.install(game)
        results.append(check('extender: asking for it back installs it',
                             ok and os.path.exists(hpk)))

    passed = sum(1 for r in results if r)
    failed = len(results) - passed
    print('\n%d passed, %d failed' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
