"""Tests that run against real Jagged Alliance 3 save files.

    python test_saves.py [folder-with-saves]

With no argument it uses the save folders found on this machine. Every test
works on a temporary copy; nothing writes to your actual saves.
"""
import glob
import os
import shutil
import sys
import tempfile

import bpul
import ja3_save
import lua_text
import zstd_chunks
import server

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('%s %s%s' % ('  ok  ' if cond else '  FAIL', name,
                       ('  — ' + detail) if detail and not cond else ''))
    return cond


def saves_in(folder=None):
    """Every JA3 save worth running these tests over.

    Two things changed under this function when the editor learned a second
    game. server.save_dirs() yields (folder, game) pairs now, not paths - and
    everything below parses BPUL containers, so a JA2 save reaching
    test_container would report a failure on a file that is perfectly fine.
    The game tag does the filtering, and it comes from reading each file
    rather than trusting the folder it sits in.
    """
    import games
    if folder:
        paths = sorted(glob.glob(os.path.join(folder, '*.sav')))
    else:
        paths = []
        for d, _game in server.save_dirs():
            paths += sorted(glob.glob(os.path.join(d, '*.sav')))
    return [p for p in paths if games.detect(p) == games.JA3]


def test_container(path):
    raw = open(path, 'rb').read()
    c = bpul.parse(raw)
    check('%s: container rebuilds byte-identical' % os.path.basename(path),
          c.build() == raw)
    check('%s: no container warnings' % os.path.basename(path),
          not c.warnings(), '; '.join(c.warnings()))
    return c


def test_compression(c, path):
    blob = c.blob('game_session')
    plain = zstd_chunks.decompress(blob)
    name = os.path.basename(path)
    check('%s: zstd wrapper round-trips' % name,
          zstd_chunks.decompress(zstd_chunks.compress(plain)) == plain)
    return plain


def test_parser(plain, path):
    text = plain.decode('utf-8')
    name = os.path.basename(path)
    root = lua_text.parse(text)
    check('%s: lua parses' % name, root.kind == 'table')
    check('%s: unedited patch is character-identical' % name,
          lua_text.Patcher(text).apply() == text)
    check('%s: has game and gvars' % name,
          root.get('game') is not None and root.get('gvars') is not None)
    return root


def test_read(path):
    s = ja3_save.Save(path)
    name = os.path.basename(path)
    check('%s: money reads as an int' % name, isinstance(s.money(), int))
    check('%s: metadata money matches session money' % name,
          s.meta_info()['money'] == s.money(),
          '%s vs %s' % (s.meta_info()['money'], s.money()))
    summary = s.summary()
    check('%s: summary is JSON-safe' % name, _json_safe(summary))
    check('%s: roster found' % name, len(summary['roster']) > 0,
          '%d entries' % len(summary['roster']))
    return s


def _json_safe(obj):
    import json
    try:
        json.dumps(obj)
        return True
    except (TypeError, ValueError):
        return False


def test_write(path, tmp):
    """A full edit cycle on a copy, verified by reading the result back."""
    work = os.path.join(tmp, os.path.basename(path))
    shutil.copy2(path, work)
    s = ja3_save.Save(work)
    name = os.path.basename(path)

    mercs = s.summary()['mercs']
    if not mercs:
        print('  --   %s: no hired mercs, skipping write test' % name)
        return
    # prefer a wounded merc so the effect-preservation path gets exercised
    merc = next((m['id'] for m in mercs if m['wounds']), mercs[0]['id'])

    # a no-op write must reproduce the original file exactly
    s.write(backup=False)
    check('%s: write with no edits is byte-identical' % name,
          open(work, 'rb').read() == open(path, 'rb').read())

    s = ja3_save.Save(work)
    s.queue('money', value=1234567)
    s.queue('stat', merc=merc, stat='Marksmanship', value=97)
    s.queue('merc_field', merc=merc, field='Experience', value=22000)
    s.queue('perks', merc=merc, perks=['Deadeye', 'Ambidextrous'])
    s.queue('item_add', merc=merc, slot='Inventory', item='AK47', condition=100,
            ammo={'item': '_762WP_Basic', 'amount': 30})
    s.queue('item_add', merc=merc, slot='Inventory', item='Meds', amount=5)
    s.apply()
    s.write(backup=False)

    back = ja3_save.Save(work)
    info = back.merc_info(merc)
    check('%s: money survived the round trip' % name, back.money() == 1234567)
    check('%s: stat survived' % name, info['stats']['Marksmanship']['value'] == 97)
    check('%s: level derived from xp' % name, info['level'] == 9, str(info['level']))
    check('%s: perks written' % name,
          {'Deadeye', 'Ambidextrous'} <= set(info['perks']))
    inv = info['slots']['Inventory']
    check('%s: items added' % name,
          {'AK47', 'Meds'} <= {i['item'] for i in inv})
    keys = [i['slot_key'] for i in inv]
    check('%s: inventory grid keys stay unique' % name, len(keys) == len(set(keys)))
    ids = [i['id'] for i in inv if i['id'] is not None]
    check('%s: item ids stay unique' % name, len(ids) == len(set(ids)))
    check('%s: stackable kept its amount' % name,
          any(i['item'] == 'Meds' and i['amount'] == 5 for i in inv))

    # weapon attachments: fit one part and check the other mounts survive
    cat = {c['id']: c for c in ja3_save.load_catalog()['items']}
    weapon = None
    for slot_name, items in back.merc_info(merc)['slots'].items():
        for k, it in enumerate(items):
            entry = cat.get(it['item'])
            if entry and entry.get('slots'):
                weapon = (slot_name, k, it, entry)
                break
        if weapon:
            break
    if weapon:
        slot_name, k, it, entry = weapon
        mount = next((m for m in entry['slots'] if m['options']), None)
        if mount:
            before = dict(it['components'] or {})
            want = dict(before)
            want[mount['slot']] = mount['options'][0]
            s2 = ja3_save.Save(work)
            s2.queue('item_components', merc=merc, slot=slot_name, index=k,
                     components=want)
            s2.apply()
            s2.write(backup=False)
            after = ja3_save.Save(work).merc_info(merc)['slots'][slot_name][k]['components']
            check('%s: attachment fitted' % name,
                  after.get(mount['slot']) == mount['options'][0],
                  '%s -> %r' % (mount['slot'], after.get(mount['slot'])))
            check('%s: other mounts untouched' % name,
                  all(after.get(m) == before[m] for m in before if m != mount['slot']))

    # wounds and other non-perk effects ride through a perk rewrite intact,
    # including their own fields (a wound's stack count)
    before_wounds = ja3_save.Save(path).merc_info(merc)['wounds']
    if before_wounds:
        check('%s: wound stacks survive a perk rewrite' % name,
              info['wounds'] == before_wounds,
              '%s -> %s' % (before_wounds, info['wounds']))

    # untouched mercs must come back exactly as they were
    others = [m['id'] for m in ja3_save.Save(path).summary()['mercs'] if m['id'] != merc]
    if others:
        before = ja3_save.Save(path).merc_info(others[0])
        after = back.merc_info(others[0])
        check('%s: untouched merc is unchanged' % name, before == after)


def test_catalog():
    cat = ja3_save.load_catalog()
    check('catalog: items present', len(cat['items']) > 200, str(len(cat['items'])))
    check('catalog: mercs present', len(cat['mercs']) > 200, str(len(cat['mercs'])))
    check('catalog: perks present', len(cat['perks']) > 100, str(len(cat['perks'])))
    check('catalog: assignable perks present',
          sum(1 for p in cat['perks'] if p.get('assignable')) > 50)
    check('catalog: hireable mercs present',
          sum(1 for m in cat['mercs'] if m.get('hireable')) >= 40)
    ak = [i for i in cat['items'] if i['id'] == 'AK47']
    check('catalog: AK47 has a display name', bool(ak) and ak[0]['name'] == 'AK-47')


def test_levels():
    check('xp: level 1 at 0 xp', ja3_save.level_for_xp(0) == 1)
    check('xp: level 2 at 1000 xp', ja3_save.level_for_xp(1000) == 2)
    check('xp: level 10 at 27000 xp', ja3_save.level_for_xp(27000) == 10)
    check('xp: round trip', all(
        ja3_save.level_for_xp(ja3_save.xp_for_level(l)) == l for l in range(1, 11)))


def _bag_on_node(path):
    """Count the squad's bag entries WITHOUT going through squad_bag().

    A write test that measures itself with the reader it is testing agrees
    with its own bug: point the reader at the empty global and "before 0,
    after 2" still looks like a pass. This walks the squad node instead, so
    the two halves cannot agree with each other by being wrong together.
    """
    s = ja3_save.Save(path)
    squads = s._player_squads()
    if not squads:
        return None
    node = squads[0]['bag']
    raw = node.table() if node is not None else None
    if raw is None:
        return []
    out = []
    for it in raw.items:
        if it.kind != 'call' or it.fname != 'PlaceInventoryItem':
            continue
        tbl = it.table()
        amt = tbl.props().get('Amount') if tbl is not None else None
        out.append((it.placed_name(), ja3_save.scalar(amt) if amt is not None else 1))
    return out


def test_squad_bag(path, tmp):
    """The supply bag lives on the SQUAD, not in gv_SquadBag.

    gv_SquadBag is present in every save, parses cleanly, and is always empty.
    The editor read it for the whole life of the feature: Supplies reported
    HAVE 0 while the game showed a full bag, and every write went into a
    container the game never reads. Nothing here had a single test, which is
    exactly why it survived. So this counts the squad's bag independently and
    demands the reader agree with it - repointing at the global fails here.
    """
    name = os.path.basename(path)
    s = ja3_save.Save(path)
    squads = s._player_squads()
    if not check('%s: found a player squad' % name, bool(squads),
                 'no squad with Side == player1'):
        return

    # Count the items independently of squad_bag(), straight off the node.
    node = squads[0]['bag']
    raw = node.table() if node is not None else None
    expect = 0 if raw is None else sum(
        1 for it in raw.items
        if it.kind == 'call' and it.fname == 'PlaceInventoryItem')

    got = s.squad_bag()
    check('%s: reader agrees with the squad node (%d)' % (name, expect),
          len(got) == expect, 'reader said %d' % len(got))
    if expect:
        check('%s: bag is not silently empty' % name, bool(got),
              'read nothing from a bag holding %d' % expect)

    # Two adds in ONE batch. Each measures the original text, so both used to
    # decide they needed a leading comma and produced `}), ,PlaceInv` - which
    # could never fire while the write went to the empty decoy.
    work = os.path.join(tmp, 'bag-' + name)
    shutil.copy(path, work)
    def total(rows, item):
        return sum(a or 0 for n, a in (rows or []) if n == item)

    before_rows = _bag_on_node(work) or []
    w = ja3_save.Save(work)
    w.queue('bag_add', item='Parts', count=100)
    w.queue('bag_add', item='Meds', count=50)
    try:
        w.apply()
        w.write(work, backup=False)
        ok, why = True, ''
    except Exception as exc:                                 # noqa: BLE001
        ok, why = False, str(exc)
    if not check('%s: two adds in one batch stay valid Lua' % name, ok, why):
        return

    after_rows = _bag_on_node(work) or []
    # Rounds, not rows. An add now fills an existing stack before making a new
    # one, so counting entries would fail while the code did the better thing.
    for item, added in (('Parts', 100), ('Meds', 50)):
        want = total(before_rows, item) + added
        check('%s: %d %s reached the squad bag' % (name, added, item),
              total(after_rows, item) == want,
              'holds %d, expected %d' % (total(after_rows, item), want))
    check('%s: the save still parses after writing' % name,
          len(ja3_save.Save(work).player_units()) > 0)

    # -- filling a stack before starting a new one ------------------------
    #
    # I claimed on 2026-09-09 that this needed no fix, because the tester's live
    # +50s produced one stack of 115. That was the LIVE path: AddItemsToSquadBag
    # ends by calling SortItemsInBag, which merges properly. NOTHING merges a
    # savegame on load, so a plain append leaves 54 and 50 of the same round
    # side by side until somebody drags one onto the other. He reloaded and
    # showed me exactly that.
    work2 = os.path.join(tmp, 'topup-' + name)
    shutil.copy(path, work2)
    w = ja3_save.Save(work2)
    start = w.squad_bag()
    if start:
        first = start[0]
        cap = 500 if (first['amount'] or 0) <= 500 else None
        if first['amount'] and cap and first['amount'] < cap:
            room = cap - first['amount']
            want = min(room, 7)          # small, so it must fit in the stack
            n_before = len(start)
            w.queue('bag_add', item=first['item'], count=want)
            w.apply()
            w.write(work2, backup=False)
            after = ja3_save.Save(work2).squad_bag()
            same = [b for b in after if b['item'] == first['item']]
            check('%s: a small add fills the stack, no new entry' % name,
                  len(after) == n_before,
                  '%d entries, was %d' % (len(after), n_before))
            check('%s: and the stack really grew' % name,
                  any((b['amount'] or 0) == first['amount'] + want for b in same),
                  str([(b['item'], b['amount']) for b in same]))

            # Two adds of the SAME item in one batch. Each measures the
            # original text, so without a running total they would both patch
            # the same number from the same starting value - the double-comma
            # trap wearing different clothes.
            w2 = ja3_save.Save(work2)
            before2 = [b for b in w2.squad_bag() if b['item'] == first['item']]
            total_before = sum((b['amount'] or 0) for b in before2)
            w2.queue('bag_add', item=first['item'], count=3)
            w2.queue('bag_add', item=first['item'], count=4)
            w2.apply()
            w2.write(work2, backup=False)
            after2 = [b for b in ja3_save.Save(work2).squad_bag()
                      if b['item'] == first['item']]
            check('%s: two adds of one item in a batch both land' % name,
                  sum((b['amount'] or 0) for b in after2) == total_before + 7,
                  'total %d, expected %d'
                  % (sum((b['amount'] or 0) for b in after2), total_before + 7))

    # Over the cap has to split; no single entry may exceed it.
    w2 = ja3_save.Save(work)
    w2.queue('bag_add', item='_44CAL_HP', count=600)
    w2.apply()
    w2.write(work, backup=False)
    rows = _bag_on_node(work) or []
    hp_rows = [a for n, a in rows if n == '_44CAL_HP']
    check('%s: 600 rounds split across stacks' % name,
          len(hp_rows) == 2, 'squad node holds %d' % len(hp_rows))
    check('%s: and all 600 of them arrived' % name,
          sum(a or 0 for a in hp_rows) == 600,
          'holds %d' % sum(a or 0 for a in hp_rows))
    hp = [b for b in ja3_save.Save(work).squad_bag() if b['item'] == '_44CAL_HP']
    check('%s: no stack exceeds its cap' % name,
          all((b['amount'] or 0) <= 500 for b in hp),
          str([b['amount'] for b in hp]))


def test_mod_recruit(path, tmp):
    """A merc from an installed mod that the save never heard of can be hired.

    Mods added mid-campaign never get a gv_UnitData record, so the roster
    offers them as new and recruiting has to write one from scratch.
    """
    name = os.path.basename(path)
    s = ja3_save.Save(path)
    fresh = [r for r in s.roster() if r.get('new_record')]
    squads = [q for q in s.squads() if q['player']]
    if not fresh or not squads:
        print('  --   %s: no mod merc missing from this save, skipping recruit test' % name)
        return
    r, sq = fresh[0], squads[0]['uid']
    check('%s: %s is offered but not hired' % (name, r['id']),
          not r['hired'] and s.unit(r['id']) is None)

    work = os.path.join(tmp, 'recruit-' + name)
    shutil.copy2(path, work)
    s = ja3_save.Save(work)
    s.queue('new_merc', merc=r['id'], squad=sq)
    s.queue('squad', merc=r['id'], squad=sq)
    s.apply()
    s.write(backup=False)

    s = ja3_save.Save(work)
    info = s.merc_info(r['id'])
    preset = next(m for m in ja3_save.load_catalog()['mercs'] if m['id'] == r['id'])
    check('%s: %s now has a record and is hired' % (name, r['id']),
          info is not None and info['hire_status'] == 'Hired')
    check('%s: %s is in the squad' % (name, r['id']),
          r['id'] in next(q for q in s.squads() if q['uid'] == sq)['units'])
    check('%s: %s keeps the mod\'s stats (none stored in the save)' % (name, r['id']),
          all(not v['stored'] for v in info['stats'].values()))
    check('%s: %s starts at full health with a contract' % (name, r['id']),
          # the game derives max HP from Health when it builds a merc
          info['hp'] == info['max_hp'] == preset['stats'].get('Health', 100)
          and info['hired_until'], '%s/%s' % (info['hp'], info['max_hp']))
    check('%s: %s has the mod\'s starting perks' % (name, r['id']),
          set(preset.get('starting_perks') or []) <= set(info['perks']),
          str(info['perks']))
    check('%s: %s shows as on the team' % (name, r['id']),
          any(x['id'] == r['id'] and x['hired'] and not x.get('new_record')
              for x in s.roster()))


def test_drop_old_portraits_mod(path, tmp):
    """Saves made while the editor's old portraits mod was on still list it,
    and the game would ask for it on every load. It can come off the list;
    nothing else in the save changes, and no other mod can be taken off."""
    name = os.path.basename(path)
    s = ja3_save.Save(path)
    if 'JA3SaveEditorFaces' not in s.active_mod_ids():
        print('  --   %s: does not list the old portraits mod, skipping' % name)
        return
    others = s.active_mod_ids() - {'JA3SaveEditorFaces'}
    check('%s: the old portraits mod is reported as retired, not missing' % name,
          any(r['id'] == 'JA3SaveEditorFaces' and r['state'] == 'retired'
              for r in s.required_mods()))
    try:
        s.queue('drop_mod', id=sorted(others)[0])
        s.apply()
        refused = False
    except ja3_save.Ja3Error:
        refused = True
    check('%s: any other mod is refused' % name, refused)

    work = os.path.join(tmp, 'dropmod-' + name)
    shutil.copy2(path, work)
    s = ja3_save.Save(work)
    session = s.text
    s.queue('drop_mod', id='JA3SaveEditorFaces')
    s.apply()
    s.write(backup=False)
    s = ja3_save.Save(work)
    check('%s: removed, every other mod kept' % name,
          s.active_mod_ids() == others, str(sorted(s.active_mod_ids())))
    check('%s: the game itself is untouched' % name, s.text == session)
    check('%s: no blank line left in the list' % name,
          '\n\t\t\n' not in s.meta_text and '\n\n' not in s.meta_text)


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else None
    paths = saves_in(folder)
    if not paths:
        print('no save files found; pass a folder as an argument')
        return 1

    print('catalog')
    test_catalog()
    test_levels()

    tmp = tempfile.mkdtemp(prefix='ja3test-')
    try:
        for p in paths:
            print('\n%s' % os.path.basename(p))
            c = test_container(p)
            plain = test_compression(c, p)
            test_parser(plain, p)
            test_read(p)
            test_write(p, tmp)
            test_squad_bag(p, tmp)
            test_mod_recruit(p, tmp)
            test_drop_old_portraits_mod(p, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('  FAILED: %s' % f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
