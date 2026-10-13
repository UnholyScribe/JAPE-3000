"""Command line front end, for when you don't want the window.

    python ja3_cli.py list
    python ja3_cli.py info      <save>
    python ja3_cli.py mercs     <save>
    python ja3_cli.py roster    <save>
    python ja3_cli.py money     <save> 500000
    python ja3_cli.py stat      <save> Fox Marksmanship 95
    python ja3_cli.py level     <save> Fox 8
    python ja3_cli.py heal      <save> [merc]
    python ja3_cli.py give      <save> Fox AK47 [--amount N] [--slot Inventory]
    python ja3_cli.py perk      <save> Fox Deadeye [--remove]
    python ja3_cli.py recruit   <save> Blood [--squad 58]
    python ja3_cli.py items     [filter]      (browse the catalog)
    python ja3_cli.py perks     [filter]

Every writing command makes a timestamped .bak next to the save first.
Add --dry-run to see what would change without writing.
"""
import argparse
import json
import os
import re
import sys

import ja3_save
import server


# Commands that mean something for a Jagged Alliance 2 save. Everything else
# is a JA3 concept - squads, perks, sectors, the live link - and gets told so
# in a sentence instead of dying inside the JA3 parser on a file that is fine.
JA2_COMMANDS = {'list', 'info', 'mercs', 'stat', 'money',
                'backups', 'restore', 'folders'}


def _save(path):
    """Open a save with the reader that matches the file.

    The file decides, exactly as it does in the GUI. Without this the CLI hands
    a JA2 save to the JA3 parser and reports "not a JA3 savegame" about a save
    that is perfectly valid - it is simply for the other game.
    """
    import games
    if games.detect(path) == games.JA2:
        import ja2_save
        return ja2_save.Save(path)
    return ja3_save.Save(path)


def _finish(s, args):
    log = s.apply()
    for line in log:
        print('  ' + line)
    if not log:
        print('  nothing to do')
        return 0
    if args.dry_run:
        print('dry run - nothing written')
        return 0
    r = s.write()
    print('wrote %s' % r['path'])
    print('backup %s' % r['backup'])
    return 0


def cmd_list(args):
    saves = server.list_saves()
    if not saves:
        print('no saves found in:')
        dirs = server.save_dirs()
        for d, game in dirs:
            print('  %s  [%s]' % (d, game))
        if not dirs:
            print('  (no save folders found)')
        return 1
    for s in saves:
        print('%-34s %9d bytes  %s' % (s['name'], s['size'], s['path']))
    return 0


def cmd_info(args):
    s = _save(args.save)
    if getattr(s, 'GAME', 'ja3') == 'ja2':
        sm = s.summary()
        m = sm['meta']
        print('%s - %s (%s)' % (m['displayname'], m['campaign'], m['build']))
        print('  day         %s' % m['game_date'])
        print('  money       %s' % f"{sm['money']:,}")
        print('  mercs       %d' % len(sm['mercs']))
        print('  editable    %s' % ('no - ' + s.blocked_because
                                    if s.read_only else 'yes'))
        for w in sm['warnings']:
            print('  ! %s' % w)
        return 0
    m = s.meta_info()
    print('%s - %s' % (m['displayname'], m['playthrough_name'] or m['campaign']))
    print('  sector      %s' % m['map'])
    print('  difficulty  %s' % m['difficulty'])
    print('  money       %s' % f"{s.money():,}")
    print('  income      %s/day' % f"{m['daily_income']:,}")
    if m['mods']:
        print('  mods        %s' % ', '.join(str(x) for x in m['mods']))
    for sq in s.squads():
        if sq['player']:
            print('  squad %-4s %-16s %s' % (sq['uid'], sq['name'] or '', ', '.join(sq['units'])))
    for w in s.warnings:
        print('  ! %s' % w)
    return 0


def cmd_mercs(args):
    s = _save(args.save)
    if getattr(s, 'GAME', 'ja3') == 'ja2':
        for i in s.summary()['mercs']:
            print('%-6s %-26s %s' % (i['id'], i['name'],
                  '  '.join('%s %s' % (k[:3].upper(), v['value'])
                            for k, v in i['stats'].items())))
        return 0
    for uid in s.player_units():
        i = s.merc_info(uid)
        print('%-14s %-22s Lv%-3d HP %3s/%-3s  XP %-6s %s'
              % (i['id'], i['nick'], i['level'], i['hp'], i['max_hp'], i['xp'],
                 i['hire_status'] or ''))
        print('   ' + '  '.join('%s %s' % (k[:3].upper(), i['stats'][k]['value'])
                                for k in ja3_save.STATS))
        if i['perks']:
            print('   perks: %s' % ', '.join(i['perks']))
    return 0


def cmd_roster(args):
    s = _save(args.save)
    for r in s.roster():
        print('%-14s %-24s %-12s %s' % (r['id'], r['nick'], r['spec'] or '',
                                        'ON TEAM' if r['hired'] else ''))
    return 0


def cmd_money(args):
    s = _save(args.save)
    s.queue('money', value=args.amount)
    return _finish(s, args)


def cmd_stat(args):
    s = _save(args.save)
    s.queue('stat', merc=args.merc, stat=args.stat, value=args.value)
    return _finish(s, args)


def cmd_level(args):
    s = _save(args.save)
    s.queue('merc_field', merc=args.merc, field='Experience',
            value=ja3_save.xp_for_level(args.level))
    return _finish(s, args)


def cmd_heal(args):
    s = _save(args.save)
    targets = [args.merc] if args.merc else s.player_units()
    for uid in targets:
        i = s.merc_info(uid)
        if i is None:
            continue
        s.queue('merc_field', merc=uid, field='HitPoints', value=i['max_hp'])
        keep = [p for p in i['perks'] if p not in ('Wounded', 'Bleeding')]
        if keep != i['perks']:
            s.queue('perks', merc=uid, perks=keep)
    return _finish(s, args)


def cmd_give(args):
    s = _save(args.save)
    cat = {i['id']: i for i in ja3_save.load_catalog()['items']}
    if args.item not in cat:
        print('unknown item %r - try: python ja3_cli.py items %s' % (args.item, args.item))
        return 1
    it = cat[args.item]
    intent = {'merc': args.merc, 'slot': args.slot, 'item': args.item}
    if getattr(args, 'force', False):
        intent['force'] = True
    if args.amount:
        intent['amount'] = args.amount
    else:
        intent['condition'] = args.condition
    if it.get('caliber') and it.get('mag'):
        ammo = next((a for a in ja3_save.load_catalog()['items']
                     if a['group'] == 'Ammo' and a['caliber'] == it['caliber']), None)
        if ammo:
            intent['ammo'] = {'item': ammo['id'], 'amount': it['mag']}
    s.queue('item_add', **intent)
    return _finish(s, args)


def cmd_perk(args):
    s = _save(args.save)
    i = s.merc_info(args.merc)
    if i is None:
        print('no merc %r' % args.merc)
        return 1
    perks = [p for p in i['perks'] if p != args.perk] if args.remove \
        else (i['perks'] + [args.perk] if args.perk not in i['perks'] else i['perks'])
    s.queue('perks', merc=args.merc, perks=perks)
    return _finish(s, args)


def cmd_recruit(args):
    s = _save(args.save)
    squad = args.squad
    if squad is None:
        player = [q for q in s.squads() if q['player']]
        if not player:
            print('no player squad in this save; pass --squad')
            return 1
        squad = player[0]['uid']
    s.queue('new_merc', merc=args.merc, squad=squad)
    s.queue('squad', merc=args.merc, squad=squad)
    return _finish(s, args)


def cmd_items(args):
    q = (args.filter or '').lower()
    for i in ja3_save.load_catalog()['items']:
        if q and q not in (i['id'] + ' ' + i['name'] + ' ' + i['group']).lower():
            continue
        print('%-28s %-30s %s' % (i['id'], i['name'], i['group']))
    return 0


def cmd_perks(args):
    q = (args.filter or '').lower()
    for p in ja3_save.load_catalog()['perks']:
        if not p.get('assignable'):
            continue
        if q and q not in (p['id'] + ' ' + p['name'] + ' ' + p['group']).lower():
            continue
        print('%-28s %-28s %s' % (p['id'], p['name'], p['group']))
    return 0


def _owns_console():
    """True when nothing else is sharing our console - i.e. we were
    double-clicked, so the window vanishes the moment we return.

    Measured on this build: a double-clicked one-file exe sees 2 processes
    (PyInstaller's bootloader plus the app it launches), while the same exe
    run from cmd.exe sees 3. A plain script sees 1 and 2 respectively.
    """
    try:
        import ctypes
        buf = (ctypes.c_uint * 16)()
        n = ctypes.windll.kernel32.GetConsoleProcessList(buf, 16)
        return n <= (2 if getattr(sys, 'frozen', False) else 1)
    except Exception:                                            # noqa: BLE001
        return False


def _pause():
    try:
        input('\nPress Enter to close this window. ')
    except (EOFError, KeyboardInterrupt):
        pass


EXAMPLES = r"""
Examples:
  JAPE3000.exe --cli list
  JAPE3000.exe --cli info  "C:\path\to\save.savegame.sav"
  JAPE3000.exe --cli mercs "C:\path\to\save.savegame.sav"
  JAPE3000.exe --cli money "C:\path\to\save.savegame.sav" 500000
  JAPE3000.exe --cli items sniper

More:
  JAPE3000.exe --cli cheats  "<save>"                 list every cheat flag
  JAPE3000.exe --cli cheats  "<save>" GodMode on      per-side, --side player1
  JAPE3000.exe --cli export  "<save>" Fox -o fox.json
  JAPE3000.exe --cli import  "<save>" fox.json --onto IMP_male_01
  JAPE3000.exe --cli supply  "<save>" Parts 200
  JAPE3000.exe --cli respec  "<save>" Fox --default
  JAPE3000.exe --cli backups "<save>"                 then: restore "<save>" 0
  JAPE3000.exe --cli mods                             what is installed
  JAPE3000.exe --cli conflicts                        do mods clash?
  JAPE3000.exe --cli folders                          where things are
  JAPE3000.exe --cli attach "<save>" Fox "Handheld A"     list the mounts
  JAPE3000.exe --cli attach "<save>" Fox "Handheld A" Scope=ThermalScope
  JAPE3000.exe --cli diff "<save>" --backup 0          what changed
  JAPE3000.exe --cli live install                     add the live mod
  JAPE3000.exe --cli live state                       what is on screen
  JAPE3000.exe --cli live give AK47 --amount 2        into the running game
  JAPE3000.exe --cli sectors "<save>"                 seen/owned/loot/all
  JAPE3000.exe --cli sector  "<save>" H2 --intel on --visited on
  JAPE3000.exe --cli sector-loot "<save>" H2 Parts 150

Start with `list` - it prints the full path of every save it can find, which
you can paste straight into the other commands.

For the window version, run JAPE3000.exe instead.
"""


def cmd_cheats(args):
    s = _save(args.save)
    c = s.cheats()
    if not args.name:
        for k in sorted(c['flags']):
            print('  %-18s %s' % (k, 'ON' if c['flags'][k] else 'off'))
        for k in sorted(c['sides']):
            on = [sd for sd, v in c['sides'][k].items() if v]
            print('  %-18s %s' % (k, ', '.join(on) if on else 'off'))
        return 0
    want = args.state == 'on'
    if args.name in c['flags']:
        s.queue('cheats', flags={args.name: want})
    elif args.name in c['sides']:
        s.queue('cheats', sides={args.name: {args.side: want}})
    else:
        print('unknown cheat %r - run `cheats <save>` to list them' % args.name)
        return 1
    return _finish(s, args)


def cmd_export(args):
    s = _save(args.save)
    blob = s.export_merc(args.merc, with_gear=not args.no_gear)
    text = json.dumps(blob, indent=1)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('wrote %s (%d bytes)' % (args.out, len(text)))
    else:
        print(text)
    return 0


def cmd_import(args):
    s = _save(args.save)
    with open(args.file, encoding='utf-8') as f:
        data = json.load(f)
    problems, warnings = s.check_import(data, args.onto)
    for w in warnings:
        print('note: %s' % w)
    if problems and not args.force:
        for p in problems:
            print('error: %s' % p)
        print('(pass --force to do it anyway)')
        return 1
    s.queue('import_merc', merc=args.onto, data=data, force=args.force)
    return _finish(s, args)


def cmd_supply(args):
    s = _save(args.save)
    s.queue('bag_add', item=args.item, count=args.count)
    return _finish(s, args)


def cmd_respec(args):
    s = _save(args.save)
    info = s.merc_info(args.merc)
    if info is None:
        print('no merc %r' % args.merc)
        return 1
    cat = ja3_save.load_catalog()
    assignable = {p['id'] for p in cat['perks'] if p.get('assignable')}
    if args.default:
        entry = next((m for m in cat['mercs'] if m['id'] == info['preset']), None)
        perks = list((entry or {}).get('starting_perks') or [])
        if not perks:
            print('%s has no default perk set to go back to' % args.merc)
            return 1
    else:
        perks = [p for p in info['perks'] if p not in assignable]
    s.queue('perks', merc=args.merc, perks=perks)
    return _finish(s, args)


def cmd_backups(args):
    import backups as bk
    rows = bk.backups_for(args.save)
    if not rows:
        print('no backups of that save yet')
        return 0
    for i, r in enumerate(rows):
        print('%2d  %-24s %7.1f MB  %s' % (i, r['when'], r['size'] / 1e6, r['name']))
    return 0


def cmd_restore(args):
    import backups as bk
    rows = bk.backups_for(args.save)
    if not rows:
        print('no backups of that save')
        return 1
    if args.index < 0 or args.index >= len(rows):
        print('pick a number from 0 to %d - run `backups <save>` to list them'
              % (len(rows) - 1))
        return 1
    chosen = rows[args.index]
    if args.dry_run:
        print('would restore %s (%s)' % (chosen['name'], chosen['when']))
        return 0
    ok, res = bk.restore(chosen['path'], args.save)
    if not ok:
        print('error: %s' % res)
        return 1
    print('restored %s' % chosen['when'])
    print('previous file kept as %s' % os.path.basename(res['previous_kept_as']))
    return 0


def cmd_mods(args):
    cat = ja3_save.load_catalog(rescan=True)
    found = cat.get('mods') or []
    if not found:
        print('no mods found')
        return 0
    for m in found:
        a = m.get('adds') or {}
        bits = ', '.join('%d %s' % (n, k) for k, n in a.items() if n) or 'code only'
        where = 'not installed'
        if m.get('installed'):
            where = ('Steam Workshop' if m.get('source') == 'steam'
                     else 'installed')
        print('%-34s %-14s %s' % (m['title'][:34], where, bits))
        print('    %s' % m['folder'])
    return 0


def cmd_portrait(args):
    import portraits
    try:
        portraits.assign(args.merc, args.image)
    except portraits.PortraitError as exc:
        print('error: %s' % exc)
        return 1
    st = portraits.status()
    print('portrait set for %s' % args.merc)
    print('kept in %s' % st['folder'])
    print(portraits.apply()[1])
    return 0


def cmd_folders(args):
    import gamepaths
    import mods
    game = gamepaths.game_dir()
    store = {'steam': ' (Steam)', 'gog': ' (GOG)'}.get(gamepaths.store_of(game), '')
    print('game        %s%s' % (game or '(not found)', store))
    print('mods        %s' % (gamepaths.mods_dir() or '(not found)'))
    for ws in mods.workshop_roots():
        print('workshop    %s' % ws)
    print('settings    %s' % gamepaths.settings_path())
    for d, game in server.save_dirs():
        print('saves       %s  (%s)' % (d, game))
    return 0


def cmd_sectors(args):
    s = _save(args.save)
    rows = s.sectors()
    pick = args.filter or 'seen'
    if pick == 'owned':
        rows = [x for x in rows if x['owned']]
    elif pick == 'loot':
        rows = [x for x in rows if x['stash']]
    elif pick == 'seen':
        rows = [x for x in rows if x['owned'] or x['intel'] or x['visited']
                or x['stash']]
    elif pick != 'all':
        print('filter is one of: seen, owned, loot, all')
        return 1
    if not rows:
        print('no sectors match')
        return 0
    print('%-6s %-7s %-6s %-8s %-10s %s'
          % ('ID', 'OWNED', 'INTEL', 'VISITED', 'GUARDPOST', 'LOOT'))
    for x in rows:
        print('%-6s %-7s %-6s %-8s %-10s %s'
              % (x['id'], x['owned'], x['intel'], x['visited'],
                 x['guardpost'], len(x['stash']) or ''))
    print()
    print('%d of %d sectors' % (len(rows), len(s.sectors())))
    return 0


def cmd_sector(args):
    s = _save(args.save)
    known = {x['id'] for x in s.sectors()}
    if args.id not in known:
        print('no sector %r in this save' % args.id)
        return 1
    flags = {}
    if args.intel is not None:
        flags['intel'] = args.intel == 'on'
    if args.visited is not None:
        flags['visited'] = args.visited == 'on'
    if flags:
        s.queue('sector_flags', sector=args.id, **flags)
    if args.owner:
        if not args.force:
            print('Changing who owns a sector skips the loyalty, guardpost, mine')
            print('and quest updates the game would normally do. Pass --force')
            print('if you want it anyway.')
            return 1
        s.queue('sector_side', sector=args.id, side=args.owner, force=True)
    if not flags and not args.owner:
        print('nothing asked for - try --intel on, --visited on, or --owner player1')
        return 1
    return _finish(s, args)


def cmd_sector_loot(args):
    s = _save(args.save)
    known = {x['id'] for x in s.sectors()}
    if args.id not in known:
        print('no sector %r in this save' % args.id)
        return 1
    intent = {'sector': args.id, 'item': args.item, 'count': args.count}
    cat = ja3_save.load_catalog()
    entry = next((i for i in cat['items'] if i['id'] == args.item), None)
    if entry is None:
        print('no item %r - run `items` to search the catalog' % args.item)
        return 1
    if not re.search(r'Ammo|Stackable|Meds|Resources|ValuablesStack',
                     entry.get('group') or ''):
        intent['condition'] = 100
    s.queue('sector_stash_add', **intent)
    return _finish(s, args)


def cmd_conflicts(args):
    import modconflicts
    import mods as mods_mod
    r = modconflicts.analyse(mods_mod.scan())
    if not r['checked']:
        print('no installed mods found')
        return 0
    print('compared %d installed mods' % r['checked'])
    if not r['findings']:
        print('nothing overlapping found')
        return 0
    label = {'will': 'WILL COLLIDE', 'may': 'MIGHT', 'note': 'NOTE'}
    for f in r['findings']:
        print()
        print('%-13s %s (%s)' % (label.get(f['level'], f['level']),
                                 f['what'], f['kind']))
        print('              %s' % ' + '.join(f['mods']))
        print('              %s' % f['detail'])
    return 0


def cmd_diff(args):
    import savediff
    if args.backup is not None:
        import backups as bk
        rows = bk.backups_for(args.save)
        if not rows:
            print('no backups of that save')
            return 1
        if args.backup < 0 or args.backup >= len(rows):
            print('pick 0 to %d - run `backups <save>` to list them'
                  % (len(rows) - 1))
            return 1
        other = rows[args.backup]['path']
        d = savediff.compare(other, args.save)
    else:
        if not args.other:
            print('give a second save, or --backup N to compare with a backup')
            return 1
        d = savediff.compare(args.save, args.other)
    for line in savediff.report(d):
        print(line)
    return 0


def cmd_live(args):
    """Talk to a running game through the Live DLC."""
    import livegame
    a = args.args

    if args.action == 'install':
        import build_livelink
        ok, msg, path = build_livelink.install()
        print(msg)
        if path:
            print('  %s' % path)
        return 0 if ok else 1
    if args.action == 'remove':
        # The extender is part of the live link, so this takes both.
        import removal
        ok, msg = removal.remove_live_service()
        print(msg)
        return 0 if ok else 1
    if args.action in ('extender-install', 'extender-remove'):
        import build_livelink
        import ja3se_install
        if args.action == 'extender-install' and not build_livelink.state()[0]:
            print('The script extender is an optional part of the live link. '
                  'Run "live install" first.')
            return 1
        ok, msg, path = (ja3se_install.install()
                         if args.action == 'extender-install'
                         else ja3se_install.uninstall())
        print(msg)
        return 0 if ok else 1

    st = livegame.status()

    if args.action == 'status':
        state = 'installed' if st['installed'] else 'not installed'
        if st['installed'] and not st['up_to_date']:
            state += ' (older than this editor)'
        print('add-on      %s' % state)
        print('extender    %s' % ('installed (optional)' if st['se_installed']
                                  else 'not installed (optional)'))
        print('game        %s' % (st['game_dir'] or 'not found'))
        print('link        %s' % st['state'])
        g = st['game']
        if g:
            print('campaign    %s' % ('loaded' if g['in_game'] else 'not loaded'))
            print('multiplayer %s' % ('yes - editing is refused' if g['multiplayer'] else 'no'))
            print('money       %s' % ('-' if g['money'] is None else g['money']))
        for m in st['mercs']:
            print('  %-16s %s/%s  xp %s' % (m['name'], m['hp'], m['max_hp'], m['xp']))
        return 0

    if args.action == 'mods':
        if not st['mods']:
            print('the game has not reported any mods - is it running?')
            return 1
        for m in st['mods']:
            note = '  (not applied yet)' if m['pending'] else ''
            print('  %-3s %-24s %s%s' % ('on' if m['enabled'] else 'off',
                                         m['id'], m['title'], note))
        return 0

    ops = {
        'money':  lambda: ('setmoney', a[0]),
        'heal':   lambda: ('heal', args.merc or (a[0] if a else 'all')),
        'stat':   lambda: ('stat', '|'.join(a[:3])),
        'hp':     lambda: ('hp', '|'.join(a[:2])),
        'exp':    lambda: ('exp', '|'.join(a[:2])),
        'give':   lambda: ('give', '|'.join([a[0], a[1], str(args.amount)])),
        'modon':  lambda: ('modon', a[0]),
        'modoff': lambda: ('modoff', a[0]),
        'apply':  lambda: ('modreload', None),
    }
    if args.action not in ops:
        print('unknown action: %s' % args.action)
        return 1
    try:
        op, value = ops[args.action]()
    except IndexError:
        print('not enough arguments for "%s"' % args.action)
        return 1
    seq = livegame.send(op, value)
    ok, msg = livegame.wait_for(seq)
    print(msg)
    return 0 if ok else 1



def cmd_uninstall(args):
    """Remove the editor, the live link and the extender from this machine.

    Lists everything first and deletes nothing without --yes, so the list can
    be read - and checked by hand - before anything goes.
    """
    import removal
    groups = removal.plan(include_backups=args.backups)
    for g in groups:
        present = [i for i in g['items'] if i['exists']]
        skip = g.get('optional') and not g.get('included')
        print('%s%s' % (g['title'], '  (kept - add --backups to include)'
                        if skip and present else ''))
        if not present:
            print('    nothing on this machine')
        for i in present:
            print('    %s' % i['path'])
    if not args.yes:
        print()
        print('Nothing deleted. Run again with --yes to remove the above.')
        return 0
    r = removal.remove_everything(include_backups=args.backups)
    print()
    print('removed %d' % len(r['removed']))
    for p in r['failed']:
        print('FAILED  %s' % p)
    for p in r['kept']:
        print('kept    %s' % p)
    if r['exe']:
        print()
        print('Last step, by hand: delete %s' % r['exe'])
    return 1 if r['failed'] else 0


def cmd_attach(args):
    """Fit or strip weapon attachments.

    The GUI could do this from the start; the command line could not, which
    made it the one feature you had to click to reach.
    """
    s = _save(args.save)
    info = s.merc_info(args.merc)
    if info is None:
        print('no merc %r' % args.merc)
        return 1
    entries = (info.get('slots') or {}).get(args.slot) or []
    if not entries:
        print('%s has nothing in %r' % (args.merc, args.slot))
        print('slots with something in them: %s'
              % ', '.join(k for k, v in (info.get('slots') or {}).items() if v))
        return 1
    if args.index >= len(entries):
        print('%s has %d item(s) in %r' % (args.merc, len(entries), args.slot))
        return 1
    item = entries[args.index]

    cat = ja3_save.load_catalog()
    entry = next((i for i in cat['items'] if i['id'] == item.get('item')), None)
    mounts = {m['slot']: m for m in ((entry or {}).get('slots') or [])}

    if not args.fit:
        print('%s in %s[%d]' % (item.get('item'), args.slot, args.index))
        if not mounts:
            print('  takes no attachments')
            return 0
        current = item.get('components') or {}
        for name in sorted(mounts):
            fitted = current.get(name) or '(empty)'
            print('  %-10s %-24s options: %s'
                  % (name, fitted, ', '.join(mounts[name].get('options') or [])))
        return 0

    # keep every mount the weapon has - the game expects a key for each
    comps = dict(item.get('components') or {})
    for name in mounts:
        comps.setdefault(name, '')
    for pair in args.fit:
        if '=' not in pair:
            print('write attachments as Slot=ComponentId, e.g. Scope=ThermalScope')
            return 1
        name, value = pair.split('=', 1)
        if mounts and name not in mounts:
            print('%s has no %r mount. It has: %s'
                  % (item.get('item'), name, ', '.join(sorted(mounts))))
            return 1
        allowed = (mounts.get(name) or {}).get('options') or []
        if value and allowed and value not in allowed and not args.force:
            print('%s does not fit %s. Allowed: %s'
                  % (value, name, ', '.join(allowed)))
            print('(pass --force to do it anyway)')
            return 1
        comps[name] = value
    s.queue('item_components', merc=args.merc, slot=args.slot,
            index=args.index, components=comps)
    return _finish(s, args)


def main():
    ap = argparse.ArgumentParser(
        prog='ja3_cli', description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='show changes, write nothing')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def add(name, fn, *args):
        p = sub.add_parser(name)
        p.set_defaults(func=fn)
        for a in args:
            p.add_argument(*a[0], **a[1])
        return p

    A = lambda *n, **k: (n, k)                                   # noqa: E731
    SAVE = A('save', help='path to a .savegame.sav')

    add('list', cmd_list)
    add('info', cmd_info, SAVE)
    add('mercs', cmd_mercs, SAVE)
    add('roster', cmd_roster, SAVE)
    add('money', cmd_money, SAVE, A('amount', type=int))
    add('stat', cmd_stat, SAVE, A('merc'), A('stat'), A('value', type=int))
    add('level', cmd_level, SAVE, A('merc'), A('level', type=int))
    add('heal', cmd_heal, SAVE, A('merc', nargs='?'))
    add('give', cmd_give, SAVE, A('merc'), A('item'),
        A('--slot', default='Inventory'), A('--amount', type=int),
        A('--condition', type=int, default=100),
        A('--force', action='store_true', help='allow a slot the game would not use'))
    add('perk', cmd_perk, SAVE, A('merc'), A('perk'), A('--remove', action='store_true'))
    add('recruit', cmd_recruit, SAVE, A('merc'), A('--squad', type=int))
    add('items', cmd_items, A('filter', nargs='?'))
    add('perks', cmd_perks, A('filter', nargs='?'))

    add('cheats', cmd_cheats, SAVE, A('name', nargs='?'),
        A('state', nargs='?', choices=['on', 'off'], default='on'),
        A('--side', default='player1'))
    add('export', cmd_export, SAVE, A('merc'), A('-o', '--out'),
        A('--no-gear', action='store_true'))
    add('import', cmd_import, SAVE, A('file'), A('--onto', required=True),
        A('--force', action='store_true'))
    add('supply', cmd_supply, SAVE, A('item'), A('count', type=int))
    add('respec', cmd_respec, SAVE, A('merc'), A('--default', action='store_true'))
    add('backups', cmd_backups, SAVE)
    add('restore', cmd_restore, SAVE, A('index', type=int))
    add('mods', cmd_mods)
    add('conflicts', cmd_conflicts)
    add('portrait', cmd_portrait, A('merc'), A('image'))
    add('folders', cmd_folders)
    add('uninstall', cmd_uninstall,
        A('--yes', action='store_true', help='actually delete'),
        A('--backups', action='store_true', help='include save backups'))
    add('attach', cmd_attach, SAVE, A('merc'), A('slot'),
        A('fit', nargs='*'), A('--index', type=int, default=0),
        A('--force', action='store_true'))

    add('diff', cmd_diff, SAVE, A('other', nargs='?'),
        A('--backup', type=int))
    add('live', cmd_live,
        A('action', nargs='?', default='status',
          choices=['status', 'install', 'remove', 'extender-install',
                   'extender-remove', 'money', 'heal', 'stat',
                   'hp', 'exp', 'give', 'mods', 'modon', 'modoff', 'apply']),
        A('args', nargs='*'),
        A('--merc', default=None),
        A('--amount', type=int, default=1))

    add('sectors', cmd_sectors, SAVE, A('filter', nargs='?'))
    add('sector', cmd_sector, SAVE, A('id'),
        A('--intel', choices=['on', 'off']),
        A('--visited', choices=['on', 'off']),
        A('--owner', choices=['player1', 'enemy1']),
        A('--force', action='store_true'))
    add('sector-loot', cmd_sector_loot, SAVE, A('id'), A('item'),
        A('count', type=int))

    # Double-clicked with no arguments: say what this is and hold the window
    # open, instead of flashing a usage error and vanishing.
    solo = _owns_console()
    if len(sys.argv) == 1:
        ap.print_help()
        print(EXAMPLES)
        if solo:
            _pause()
        return 0

    args = ap.parse_args()
    # Refuse a JA2 save politely for commands that cannot mean anything for it,
    # rather than letting it reach a JA3-only method and traceback.
    save_path = getattr(args, 'save', None)
    if save_path and os.path.exists(save_path):
        import games
        if games.detect(save_path) == games.JA2 and args.cmd not in JA2_COMMANDS:
            print('error: "%s" is a Jagged Alliance 3 feature, and this is a '
                  'Jagged Alliance 2 save.' % args.cmd)
            print('       JA2 saves support: %s'
                  % ', '.join(sorted(JA2_COMMANDS)))
            return 2
    if args.cmd in ('folders', 'mods', 'conflicts', 'portrait', 'live'):
        import gamepaths
        if gamepaths.game_dir() is None and gamepaths.searching():
            print('looking for Jagged Alliance 3 on your drives...')
        # a command line has no screen to update later, so wait for the search
        gamepaths.game_dir(wait=True)
    try:
        return args.func(args)
    except ja3_save.Ja3Error as e:
        print('error: %s' % e)
        return 1
    except FileNotFoundError as e:
        print('error: %s' % e)
        return 1
    finally:
        if solo:
            _pause()


if __name__ == '__main__':
    sys.exit(main())
