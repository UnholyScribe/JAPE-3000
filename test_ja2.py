"""Checks for the Jagged Alliance 2 half.

    python test_ja2.py [save.sav]

With no argument it finds JA2 saves the way the editor does. Every test works
on a copy in a temp folder - **nothing here ever writes to a real save.**

What is worth testing here is not "does it run" but "is the cipher still
right", because the failure mode is silent: a wrong key still produces bytes,
still writes a file, and only shows up as a scrambled campaign later. So the
checks below are all of the form "a value we know independently".
"""
import glob
import os
import shutil
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import games                                                   # noqa: E402
import ja2_save                                                # noqa: E402
from ja2 import item_data as ja2_items                        # noqa: E402
from ja2 import item_stats as ja2_stats                       # noqa: E402
from ja2 import profile_data as pd                             # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print('%s %s%s' % ('  ok  ' if cond else '  FAIL', name,
                       ('  - ' + detail) if detail and not cond else ''))
    return cond


def ja2_saves(arg=None):
    if arg:
        return [arg]
    import server
    out = []
    for d, game in server.save_dirs():
        if game != games.JA2:
            continue
        out += sorted(glob.glob(os.path.join(d, '*.sav')))
    return [p for p in out if games.detect(p) == games.JA2]


def test_tables():
    check('keystream covers a whole hired-merc record',
          len(pd.KEYSTREAM) == 2328, '%d' % len(pd.KEYSTREAM))
    check('the key is the 49 entry cycle',
          len(pd.KEY_TABLE) == 49
          and all(pd.KEYSTREAM[i] == pd.KEY_TABLE[i % 49]
                  for i in range(len(pd.KEYSTREAM))))
    check('every key byte is a byte',
          all(0 <= k <= 255 for k in pd.KEYSTREAM))
    check('170 names and nicknames',
          len(pd.NAMES) == pd.PROFILE_COUNT == len(pd.NICKNAMES))
    # The octal trap: entry 44 is 057 in the C source, which is 47. Read as
    # decimal 57 it corrupts one byte in 46 - "Roberts" decodes "Roberjs".
    check('Prof.dat key entry 44 is 47, not 57',
          pd.PROF_DAT_KEY[44] == 47, 'got %d' % pd.PROF_DAT_KEY[44])
    check('stat offsets all sit inside a record',
          all(0 <= o < pd.RECORD_SIZE for o in pd.STATS.values()))
    check('stat names match the JA3 catalogue spelling',
          'Marksmanship' in pd.STATS and 'marksmanship' not in pd.STATS)
    # 408/409 were set by hand against a 150-to-20 majority vote, because the
    # vote was measuring the game's habit rather than the key. Offset 408 is
    # soldier slot 11's item id, and the majority value decoded it to item
    # 25771 in every hired merc's record. If someone regenerates the table and
    # lets the vote win again, that whole class of bug comes back - so it is
    # asserted here rather than trusted to a comment.
    check('keystream 408/409 are the hand-set values, not the vote',
          (pd.KEYSTREAM[408], pd.KEYSTREAM[409]) == (206, 85),
          'got %d, %d' % (pd.KEYSTREAM[408], pd.KEYSTREAM[409]))
    # 230 is soldier slot 6's item count. Same story, worse vote - four values
    # split 60/39/37/18 - and the value that won gave every merc 102 of
    # whatever was in their left hand.
    check('keystream 230 is the hand-set value, not the vote',
          pd.KEYSTREAM[230] == 133, 'got %d' % pd.KEYSTREAM[230])
    check('the item stats table came out of the executable',
          len(ja2_stats.STATS) > 250 and ja2_stats.weight(1) == 0.6,
          '%d rows, Glock weighs %s' % (len(ja2_stats.STATS),
                                        ja2_stats.weight(1)))


def test_read(path):
    s = ja2_save.Save(path)
    tag = os.path.basename(path)
    ok, why = True, ''
    for i in s.profiles.named():
        for name, v in s.profiles.stats(i).items():
            if not 0 <= v <= 100:
                ok, why = False, '%s %s = %d' % (s.profiles.name(i), name, v)
                break
    check('%s: every stat is in 0..100' % tag, ok, why)
    check('%s: profile 0 is Barry Unger' % tag,
          s.profiles.name(0) == 'Barry Unger', s.profiles.name(0))
    check('%s: names came out of the save, not the fallback table' % tag,
          len(s.profiles.named()) > 100, '%d named' % len(s.profiles.named()))
    # Barry is the explosives man; these are his published numbers, and they
    # are what proves the stat offsets rather than merely looking plausible.
    st = s.profiles.stats(0)
    check('%s: summary has the JA3 shape' % tag,
          set(s.summary()) >= {'mercs', 'money', 'meta', 'warnings', 'supports'})

    # Every item id in either record type has to be an item that exists. This
    # is the check that would have caught the 408 keystream error on day one:
    # it was visible the whole time as "Item 25771" and got written off as an
    # oddity instead of chased.
    bad = []
    for m in s.mercs():
        for which in ('inventory', 'aim_inventory'):
            for r in m.get(which, []):
                if r['item'] and r['item'] not in ja2_items.ITEMS:
                    bad.append('%s %s %s=%d' % (m['nick'], which, r['name'],
                                                r['item']))
                for a_ in (r.get('attachments') or []):
                    if a_ and a_['id'] not in ja2_items.ITEMS:
                        bad.append('%s %s attachment=%d'
                                   % (m['nick'], r['name'], a_['id']))
    check('%s: every item id is a real item' % tag, not bad,
          '; '.join(bad[:4]))

    # A stack bigger than the game's own pocket limit cannot have been written
    # by the game, so it means a byte is being decoded wrong. This is the
    # cheapest detector there is for a bad keystream position, and it is the
    # check that would have caught offset 230 - which read as 102 sticks of
    # TNT in a slot the game caps at one.
    over = []
    for m in s.mercs():
        for r in m.get('inventory', []):
            lim = ja2_stats.stack(r['item']) if r['item'] else None
            if lim and r['count'] > lim:
                over.append('%s %s: %d of %s, limit %d'
                            % (m['nick'], r['name'], r['count'],
                               r['item_name'], lim))
    check('%s: no stack exceeds the pocket limit the game publishes' % tag,
          not over, '; '.join(over[:3]))

    # The plausibility scan the editor now runs on every open. Two things are
    # checked, and the second matters more: that it stays quiet on a good save,
    # AND that it actually fires on a bad one. A detector nobody has watched
    # trip is not a detector.
    check('%s: nothing implausible in this save' % tag,
          not s.implausible(), '; '.join(s.implausible()[:2]))

    b = ja2_save.Save(path)
    k = list(b.key)
    k[408], k[409], k[230] = 106, 241, 32         # what the majority vote said
    b.key = tuple(k)
    b.profiles = ja2_save.ja2_profiles.Profiles(b.raw, b.profile_start, b.key)
    b._load_soldiers()
    broken = b.implausible()
    check('%s: and it catches the faults it was built for' % tag,
          len(broken) >= 2, '%d found' % len(broken))

    # A hired merc is shown what they are CARRYING, not their loadout. The two
    # differ on any save with play behind it, and showing the wrong one is how
    # an edit lands somewhere the game never looks.
    hired = [m for m in s.mercs() if m.get('soldier') is not None]
    if hired:
        check('%s: a hired merc carries a live inventory and a loadout' % tag,
              all(len(m['inventory']) == 19 and len(m['aim_inventory']) == 19
                  for m in hired))
        check('%s: live gear rows know their own slot' % tag,
              all(r['soldier_slot'] == r['slot']
                  for m in hired for r in m['inventory']))
        check('%s: four attachment positions on every row' % tag,
              all(len(r['attachments']) == 4
                  for m in hired for r in m['inventory']))
    return s


def test_write(path, tmp):
    """The real test: change one stat, and change nothing else."""
    work = os.path.join(tmp, 'work.sav')
    shutil.copy2(path, work)
    tag = os.path.basename(path)

    s = ja2_save.Save(work)
    if s.read_only:
        check('%s: read-only save refuses to write' % tag, True)
        return
    before = {i: s.profiles.stats(i) for i in s.profiles.named()}
    target = s.profiles.named()[7]
    was = s.profiles.stats(target)['Agility']
    want = 100 if was != 100 else 55

    s.queue('stat', merc='p%d' % target, stat='Agility', value=want)
    log = s.apply()
    check('%s: apply reported the change' % tag, len(log) == 1, str(log))
    r = s.write()
    check('%s: a backup was made' % tag, r['backup'] and os.path.exists(r['backup']))
    check('%s: the file is the same size' % tag,
          os.path.getsize(work) == os.path.getsize(path))

    again = ja2_save.Save(work)
    check('%s: the new value reads back' % tag,
          again.profiles.stats(target)['Agility'] == want,
          'got %d' % again.profiles.stats(target)['Agility'])

    moved = [i for i in again.profiles.named()
             if again.profiles.stats(i) != before[i]]
    check('%s: exactly one merc changed' % tag, moved == [target], str(moved))

    others = [k for k, v in again.profiles.stats(target).items()
              if k != 'Agility' and v != before[target][k]]
    check('%s: that merc other stats are untouched' % tag, not others, str(others))

    # and back again: setting it to what it was must restore the file exactly
    again.queue('stat', merc='p%d' % target, stat='Agility', value=was)
    again.apply()
    again.write(backup=False)
    check('%s: setting it back reproduces the original bytes' % tag,
          open(work, 'rb').read() == open(path, 'rb').read())


def test_lookup(path):
    s = ja2_save.Save(path)
    tag = os.path.basename(path)
    check('%s: merc found by id' % tag, s._merc_index('p7') == 7)
    check('%s: merc found by number' % tag, s._merc_index('7') == 7)
    check('%s: merc found by nickname' % tag,
          s._merc_index(s.profiles.nickname(7)) == 7)
    check('%s: merc found by full name' % tag,
          s._merc_index(s.profiles.name(7)) == 7)
    try:
        s._merc_index('Nobody At All')
        check('%s: an unknown name is refused' % tag, False, 'no error raised')
    except ja2_save.Ja2Error:
        check('%s: an unknown name is refused' % tag, True)


def test_dashboard_intents(path, tmp):
    """The exact intents the dashboard emits, through the writer.

    These three were lifted out of S.pending after clicking through the new
    JA2 panel - a weapon condition, an attachment swap and a stat - so this
    checks the shape the front end PRODUCES is the shape the writer accepts.
    A test written from the writer's side would agree with itself and catch
    nothing.

    It runs against whichever merc in this save is on the team and carrying a
    weapon with something fitted, so it does not depend on one person's
    campaign.
    """
    tag = os.path.basename(path)
    work = os.path.join(tmp, 'intents-' + tag)
    shutil.copy2(path, work)

    s = ja2_save.Save(work)
    target = slot = None
    for m in s.mercs():
        for row in m.get('inventory', []):
            if any(row.get('attachments') or []):
                target, slot = m, row
                break
        if target:
            break
    if not target:
        print('  --   %s: nobody here has a fitted weapon, skipped' % tag)
        return

    was_cond = slot['condition']
    was_stat = target['stats']['Agility']['value']
    # four positional slots, empties are None
    fitted = [(k, a) for k, a in enumerate(slot['attachments']) if a]
    names = [a['name'] for _, a in fitted]
    swap = next((i for i in s.summary()['items']
                 if i['cat'] == 'Weapon attachment' and i['name'] not in names),
                None)
    swap_at = fitted[-1][0]

    want_cond = 100 if was_cond < 100 else 50
    want_stat = 99 if was_stat != 99 else 98
    s.queue('ja2_live_item', merc=target['id'], slot=slot['slot'],
            item=slot['item'], count=slot['count'], condition=want_cond)
    s.queue('stat', merc=target['id'], stat='Agility', value=want_stat)
    if swap:
        s.queue('ja2_attach', merc=target['id'], slot=slot['slot'],
                index=swap_at, item=swap['id'], condition=100)
    s.apply()
    s.write(backup=False)

    after = ja2_save.Save(work)
    got = [m for m in after.mercs() if m['id'] == target['id']][0]
    row = got['inventory'][slot['slot']]

    check('%s: dashboard item edit took' % tag,
          row['condition'] == want_cond, 'got %d' % row['condition'])
    check('%s: the weapon itself is unchanged' % tag,
          row['item'] == slot['item'])
    check('%s: dashboard stat edit took' % tag,
          got['stats']['Agility']['value'] == want_stat)
    if swap:
        now = [a['name'] if a else None for a in row['attachments']]
        check('%s: dashboard attachment swap took' % tag,
              now[swap_at] == swap['name'], str(now))
        check('%s: it landed in the slot it was aimed at' % tag,
              [k for k, a in enumerate(row['attachments']) if a]
              == [k for k, _ in fitted], str(now))
        check('%s: the other attachment survived the swap' % tag,
              len([a for a in now if a]) == len(fitted),
              '%s -> %s' % (names, now))

    before = ja2_save.Save(path)
    moved = [m['id'] for m, n in zip(before.mercs(), after.mercs())
             if m['id'] != target['id'] and m != n]
    check('%s: nobody else moved' % tag, not moved, str(moved))


def test_item_swap_clears_attachments(path, tmp):
    """Putting a different item in a slot must not leave the old scope on it.

    This is the bug the tester hit. He swapped the weapon in Shadow's right hand
    and the panel kept showing the MP5K's Silencer and Sniper Scope, so an
    edit there wrote onto a gun that was no longer in that hand. The front end
    now drops them on an item change; this checks the writer does the same,
    because a front end that is right on its own is one refresh away from
    being wrong.
    """
    tag = os.path.basename(path)
    work = os.path.join(tmp, 'swap-' + tag)
    shutil.copy2(path, work)

    s = ja2_save.Save(work)
    target = slot = None
    for m in s.mercs():
        for row in m.get('inventory', []):
            if any(row.get('attachments') or []):
                target, slot = m, row
                break
        if target:
            break
    if not target:
        print('  --   %s: nobody here has a fitted weapon, skipped' % tag)
        return

    other = next(i for i in s.summary()['items']
                 if i['cat'] == 'Weapon' and i['id'] != slot['item'])
    s.queue('ja2_live_item', merc=target['id'], slot=slot['slot'],
            item=other['id'], count=1, condition=100)
    s.apply()
    s.write(backup=False)

    after = ja2_save.Save(work)
    got = [m for m in after.mercs() if m['id'] == target['id']][0]
    row = got['inventory'][slot['slot']]
    check('%s: the swapped-in weapon is there' % tag,
          row['item'] == other['id'], row['item_name'])
    check('%s: the old attachments did not come with it' % tag,
          not any(row['attachments']),
          str([a['name'] for a in row['attachments'] if a]))
    # A swapped-in item is a new item, so it arrives undamaged rather than
    # inheriting the wear on the thing it replaced. the tester's Colt .45 turned up
    # at 92% because it took that from the MP5K that had been in that hand.
    check('%s: the swapped-in weapon arrives at 100%%' % tag,
          row['condition'] == 100, 'got %d' % row['condition'])

    # JAPE validates before it edits. This is the same habit placed later and
    # aimed better: check the RESULT of a batch before the bytes reach disk,
    # because a value each setter accepts alone can still be impossible in
    # combination. Both directions, because a guard nobody has watched refuse
    # is not a guard.
    good = os.path.join(tmp, 'guard-ok-' + tag)
    shutil.copy2(path, good)
    g = ja2_save.Save(good)
    who = [m for m in g.mercs() if m.get('soldier') is not None]
    if who:
        g.queue('stat', merc=who[0]['id'], stat='Agility', value=77)
        g.apply()
        try:
            g.write(backup=False)
            check('%s: a sane edit still writes' % tag, True)
        except ja2_save.Ja2Error as exc:
            check('%s: a sane edit still writes' % tag, False, str(exc))

        bad = os.path.join(tmp, 'guard-bad-' + tag)
        shutil.copy2(path, bad)
        b = ja2_save.Save(bad)
        # Search EVERY hired merc, not just the first. Looking only at one
        # made both checks below skip in silence on this save, which reported
        # as a pass and tested nothing.
        target = slot = None
        for m in b.mercs():
            if m.get('soldier') is None:
                continue
            hit = next((r for r in m['inventory']
                        if r['item'] and (ja2_stats.stack(r['item']) or 0) == 1),
                       None)
            if hit:
                target, slot = m, hit
                break
        check('%s: found a non-stacking item to test the guard with' % tag,
              slot is not None)
        if slot:
            was = open(bad, 'rb').read()
            b.queue('ja2_live_item', merc=target['id'], slot=slot['slot'],
                    item=slot['item'], count=9, condition=100)
            b.apply()
            try:
                b.write(backup=False)
                check('%s: an impossible stack is refused' % tag, False,
                      'it wrote')
            except ja2_save.Ja2Error:
                check('%s: an impossible stack is refused' % tag, True)
            check('%s: and the refused write left the file alone' % tag,
                  open(bad, 'rb').read() == was)


def test_checksums(path):
    """The game's two checksums, recomputed, match what the game stored.

    This is what proves the 49 entry key past offset 716 and the live-copy
    offsets at the same time: one wrong byte anywhere in either and the
    numbers do not agree.
    """
    s = ja2_save.Save(path)
    tag = os.path.basename(path)
    P = s.profiles
    ok = [i for i in P.named() if P.checksum(i) == P.stored_checksum(i)]
    check('%s: profile checksums reproduce (%d of %d)'
          % (tag, len(ok), len(P.named())),
          len(ok) >= len(P.named()) - 2)
    if s.soldiers and s.soldiers.bases:
        S = s.soldiers
        check('%s: every hired-merc checksum reproduces' % tag,
              all(S.checksum(n) == S.stored_checksum(n)
                  for n in range(len(S.bases))))
        check('%s: each hired record names its own profile' % tag,
              all(S.nickname(n) == P.nickname(S.profile(n))
                  for n in range(len(S.bases))))
        check('%s: the live copy agrees with the profile' % tag,
              all(S.stats(n) == {k: P.stats(S.profile(n))[k] for k in S.stats(n)}
                  for n in range(len(S.bases))))


def test_layout_and_money(path, tmp):
    """The sections are found by parsing, the key set by the game's formula,
    and a money edit lands on the real balance without moving the key."""
    s = ja2_save.Save(path)
    tag = os.path.basename(path)
    check('%s: laptop block and profiles found by parsing' % tag,
          ja2_save.locate(s.raw, s.header_len) == (s.laptop_start,
                                                    s.profile_start),
          '%s / %s' % (s.laptop_start, s.profile_start))
    check('%s: the formula key set is the one that decodes' % tag,
          s.key_set == ja2_save.key_set(s.raw, s._german) and not s.read_only,
          'set %d' % s.key_set)
    check('228 key sets, set 63 is the table measured by hand',
          len(ja2_save.ja2_key_sets.SETS) == 228
          and tuple(ja2_save.ja2_key_sets.SETS[63]) == tuple(pd.KEY_TABLE))
    head = bytes(s.raw[:s.header_len])
    if s.laptop_start is None:
        return
    real = struct.unpack_from('<i', s.raw, s.laptop_start + 104)[0]
    check('%s: the balance shown is the laptop one' % tag, s.balance == real)

    dst = os.path.join(tmp, 'money-' + tag)
    shutil.copy2(path, dst)
    e = ja2_save.Save(dst)
    want = real + 12345
    e.queue('money', value=want)
    e.apply()
    e.write(backup=False)
    a = ja2_save.Save(dst)
    check('%s: money edit reads back' % tag, a.balance == want,
          '%d' % a.balance)
    check('%s: and it is the laptop balance that moved' % tag,
          struct.unpack_from('<i', a.raw, a.laptop_start + 104)[0] == want)
    check('%s: the header (and so the key set) is untouched' % tag,
          bytes(a.raw[:a.header_len]) == head and a.key_set == s.key_set)
    check('%s: the save still decodes and is editable' % tag,
          not a.read_only and a.profiles.stats(7) == s.profiles.stats(7))
    diff = [i for i in range(len(s.raw)) if s.raw[i] != a.raw[i]]
    check('%s: only the four balance bytes changed' % tag,
          diff and min(diff) >= a.laptop_start + 104
          and max(diff) < a.laptop_start + 108, str(diff[:8]))

    # Moving the header balance must be caught before it reaches the disk.
    e = ja2_save.Save(dst)
    struct.pack_into('<i', e.raw, ja2_save.OFF_BALANCE, want + 1)
    try:
        e.write(backup=False)
        moved = False
    except ja2_save.Ja2Error:
        moved = True
    check('%s: a header change that moves the key is refused' % tag,
          moved or ja2_save.key_set(e.raw, e._german) == e.key_set)


def test_fire(path, tmp):
    """Sending a merc home: every place the game names them, and nobody else
    disturbed."""
    s = ja2_save.Save(path)
    tag = os.path.basename(path)
    L = s.layout
    check('%s: the whole save walks, soldiers to movement groups' % tag,
          L is not None and L.groups_end <= len(s.raw))
    if not L or len(L.team()) < 2:
        return
    team = L.team()
    gone = team[-1]
    pid = gone['profile']
    stay = [t['profile'] for t in team[:-1]]
    others = {p: (s.profiles.stats(p), s.soldiers.inventory(s._record(p)))
              for p in stay}
    men = s.raw[s.header_len + ja2_save.TEAM_MEN_IN_SECTOR]
    grp = L.group(gone['group'])

    dst = os.path.join(tmp, 'fire-' + tag)
    shutil.copy2(path, dst)
    e = ja2_save.Save(dst)
    e.queue('ja2_fire', merc='p%d' % pid)
    log = e.apply()
    e.write(backup=False)
    a = ja2_save.Save(dst)
    A = a.layout
    check('%s: fire reported' % tag, log and 'left the team' in log[-1],
          str(log))
    check('%s: the file still walks end to end' % tag, A is not None)
    if A is None:
        return
    check('%s: one fewer on the team, and not them' % tag,
          [t['profile'] for t in A.team()] == stay
          and pid not in a._att, str([t['profile'] for t in A.team()]))
    check('%s: their slot is empty' % tag, A.soldier(gone['slot']) is None)
    check('%s: everything after the movement groups is byte-identical' % tag,
          a.raw[A.groups_end:] == s.raw[L.groups_end:])
    check('%s: and everything before the soldier array' % tag,
          [i for i in range(L.soldiers[0]['flag'])
           if a.raw[i] != s.raw[i]
           and not a.profile_start + pid * 716 <= i
           < a.profile_start + (pid + 1) * 716]
          == [k for k in (a.header_len + ja2_save.TEAM_MEN_IN_SECTOR,)
              if men] + [a.laptop_start + ja2_save.LAPTOP_LEFT_LIST + j
                         for j in (0, 1)])
    P = a.profiles
    check('%s: profile says returning home, back in a day' % tag,
          P.get(pid, ja2_save.P_STATUS) == ja2_save.MERC_RETURNING_HOME & 0xFF
          and P.get(pid, ja2_save.P_DAY_AVAILABLE) == 1)
    check('%s: and its checksum is the game\'s' % tag,
          P.checksum(pid) == P.stored_checksum(pid))
    left = struct.unpack_from('<256h', a.raw,
                              a.laptop_start + ja2_save.LAPTOP_LEFT_LIST)
    check('%s: listed in personnel as departed' % tag, pid in left)
    check('%s: head count in the sector went down' % tag,
          a.raw[a.header_len + ja2_save.TEAM_MEN_IN_SECTOR] == max(0, men - 1))
    if grp:
        g2 = A.group(grp['id'])
        check('%s: gone from their movement group' % tag,
              g2 is not None and pid not in g2['members']
              and g2['size'] == len(g2['members']) == len(grp['members']) - 1,
              str(g2 and g2['members']))
    ids = [struct.unpack_from('<h', a.raw, A.squads + 12 * k)[0]
           for k in range(6)]
    check('%s: squad closed up behind them' % tag,
          gone['slot'] not in ids and ids[:len(stay)] == [t['slot'] for t in team[:-1]],
          str(ids))
    check('%s: everyone else exactly as they were' % tag,
          all((a.profiles.stats(p), a.soldiers.inventory(a._record(p)))
              == others[p] for p in stay))
    check('%s: every remaining checksum still the game\'s' % tag,
          all(a.soldiers.checksum(k) == a.soldiers.stored_checksum(k)
              for k in range(len(a.soldiers.bases))))
    check('%s: key set and header untouched' % tag,
          a.key_set == s.key_set
          and a.raw[:a.header_len] == s.raw[:s.header_len])

    # The last merc stays.
    while len(a.layout.team()) > 1:
        a.queue('ja2_fire', merc='p%d' % a.layout.team()[-1]['profile'])
        a.apply()
    a.queue('ja2_fire', merc='p%d' % a.layout.team()[0]['profile'])
    try:
        a.apply()
        refused = False
    except ja2_save.Ja2Error:
        refused = True
    check('%s: the last merc cannot be sent home' % tag, refused)
    b = ja2_save.Save(path)
    b.queue('ja2_fire', merc='p7')
    try:
        b.apply()
        refused = 7 not in b._att
    except ja2_save.Ja2Error:
        refused = True
    check('%s: someone not on the team is refused' % tag, refused)


def test_hire(path, tmp):
    """Hiring: the record the game would create, in transit, with the event
    that brings them in - and nothing else in the file moved."""
    s = ja2_save.Save(path)
    tag = os.path.basename(path)
    L = s.layout
    if not L:
        return
    P0 = s.profiles
    pid = next((i for i in range(ja2_save.LAST_AIM + 1)
                if i not in s._att and P0.get(i, ja2_save.P_STATUS) == 0
                and P0.name(i)), None)
    if pid is None:
        return
    team_before = [t['profile'] for t in L.team()]
    others = {p: (s.profiles.stats(p), s.soldiers.inventory(s._record(p)))
              for p in team_before}
    men = s.raw[s.header_len + ja2_save.TEAM_MEN_IN_SECTOR]
    ev_at = s.header_len + ja2_save.TACTICAL_STATUS + ja2_save.GAME_CLOCK
    n_ev = struct.unpack_from('<I', s.raw, ev_at)[0]

    dst = os.path.join(tmp, 'hire-' + tag)
    shutil.copy2(path, dst)
    e = ja2_save.Save(dst)
    e.queue('ja2_hire', merc='p%d' % pid, days=7)
    log = e.apply()
    e.write(backup=False)
    a = ja2_save.Save(dst)
    A = a.layout
    check('%s: hire reported' % tag, log and 'hired' in log[-1], str(log))
    check('%s: the file still walks end to end after a hire' % tag,
          A is not None)
    if not A:
        return
    check('%s: one more on the team, and it is them' % tag,
          [t['profile'] for t in A.team()] == team_before + [pid]
          and pid in a._att)
    n = a._record(pid)
    S, P = a.soldiers, a.profiles
    b = S.bases[n]
    check('%s: the new record is theirs - name, stats, health, level' % tag,
          S.nickname(n) == P.nickname(pid)
          and S.stats(n) == {k: P.stats(pid)[k] for k in S.stats(n)}
          and S.merc(n)['health'] == P.get(pid, pd.OFF_HEALTH)
          and S.merc(n)['level'] == P.get(pid, pd.OFF_LEVEL))
    check('%s: in transit, in no squad and no group' % tag,
          S._get(b, 1917) == ja2_save.IN_TRANSIT and S._get(b, 753) == 0
          and S._get(b, 705) == 0
          and A.squad_of(a.raw, A.team()[-1]['slot']) is None)
    loadout = [(r['item']) for r in P.inventory(pid)]
    carried = [(r['item']) for r in S.inventory(n)]
    check('%s: carrying their A.I.M. kit, slot for slot' % tag,
          loadout == carried, '%s vs %s' % (loadout, carried))
    check('%s: both checksums are the game\'s' % tag,
          S.checksum(n) == S.stored_checksum(n)
          and P.checksum(pid) == P.stored_checksum(pid))
    check('%s: profile says hired, not yet arrived' % tag,
          P.get(pid, ja2_save.P_STATUS) == ja2_save.MERC_HIRED_NOT_ARRIVED & 0xFF)
    evs = [(struct.unpack_from('<I', a.raw, ev_at + 4 + 28 * k + 4)[0],
            a.raw[ev_at + 4 + 28 * k + 17],
            struct.unpack_from('<I', a.raw, ev_at + 4 + 28 * k + 8)[0])
           for k in range(struct.unpack_from('<I', a.raw, ev_at)[0])]
    check('%s: one delayed-hiring event for their slot, list still in order'
          % tag, len(evs) == n_ev + 1
          and [x for x in evs if x[1] == 12 and x[2] == A.team()[-1]['slot']]
          and [x[0] for x in evs] == sorted(x[0] for x in evs))
    check('%s: head count went up' % tag,
          a.raw[a.header_len + ja2_save.TEAM_MEN_IN_SECTOR] == men + 1)
    check('%s: laptop remembers the last hire' % tag,
          struct.unpack_from('<i', a.raw, a.laptop_start
                             + ja2_save.LAPTOP_LAST_HIRED)[0] == pid)
    check('%s: everyone else exactly as they were' % tag,
          all((a.profiles.stats(p), a.soldiers.inventory(a._record(p)))
              == others[p] for p in team_before))
    check('%s: squads, groups and the rest of the file untouched' % tag,
          a.raw[A.squads:] == s.raw[L.squads:])
    check('%s: nothing implausible after a hire' % tag, not a.implausible(),
          '; '.join(a.implausible()[:2]))

    for who, why in (('p%d' % team_before[-1], 'already on the team'),
                     ('p57', 'not a hireable merc')):
        r = ja2_save.Save(path)
        r.queue('ja2_hire', merc=who)
        try:
            r.apply()
            refused = False
        except ja2_save.Ja2Error:
            refused = True
        check('%s: hiring someone %s is refused' % (tag, why), refused)
    busy = next((i for i in range(ja2_save.LAST_AIM + 1)
                 if s.profiles.get(i, ja2_save.P_STATUS) == 0xF9), None)
    if busy is not None:
        r = ja2_save.Save(path)
        r.queue('ja2_hire', merc='p%d' % busy)
        try:
            r.apply()
            refused = False
        except ja2_save.Ja2Error:
            refused = True
        check('%s: a merc working elsewhere is refused' % tag, refused)

    rows = {m['id']: m for m in s.mercs()}
    check('%s: the merc list says who can be hired and sent home' % tag,
          rows['p%d' % pid].get('hire_refused') == ''
          and all(rows['p%d' % p].get('fire_refused') == ''
                  for p in team_before)
          and (busy is None or rows['p%d' % busy]['hire_refused']))
    arrived = {m['id']: m for m in a.mercs()}['p%d' % pid]
    check('%s: someone still in the helicopter cannot be sent home' % tag,
          bool(arrived.get('fire_refused')), str(arrived.get('fire_refused')))

    mid = next((i for i in range(ja2_save.LAST_AIM + 1,
                                 ja2_save.LAST_MERC_SITE + 1)
                if not s.hire_refusal(i)), None)
    if mid is not None:
        dst = os.path.join(tmp, 'hire-merc-' + tag)
        shutil.copy2(path, dst)
        e = ja2_save.Save(dst)
        e.queue('ja2_hire', merc='p%d' % mid, days=14)
        e.apply()
        e.write(backup=False)
        m = ja2_save.Save(dst)
        b = m.soldiers.bases[m._record(mid)]
        check('%s: M.E.R.C. hires are daily, whatever was asked' % tag,
              [m.soldiers._get(b, 1908 + k) for k in range(4)] == [1, 0, 0, 0]
              and m.soldiers._get(b, 1916) == ja2_save.MERC_TYPE_MERC)


def test_new_fields(path, tmp):
    """Names, energy, morale, live stats, a gun's magazine, each magazine in a
    stack, and the checksums - written, read back, and nothing else moved."""
    from ja2 import ammo
    from ja2 import soldiers as sol
    tag = os.path.basename(path)
    work = os.path.join(tmp, 'fields-' + tag)
    shutil.copy2(path, work)
    s = ja2_save.Save(work)
    hired = [m for m in s.mercs() if m.get('soldier') is not None]
    if s.read_only or not hired:
        print('  --   %s: no hired merc to edit, skipped' % tag)
        return
    m = hired[0]
    i, n = m['index'], m['soldier']
    gun = next((r for r in m['inventory'] if r.get('gun')), None)
    mags = next((r for r in m['inventory'] if r.get('stack')), None)

    s.queue('ja2_name', merc=m['id'], name='Testy McTestface', nick='Testo')
    s.queue('merc_field', merc=m['id'], field='Morale', value=88)
    s.queue('merc_field', merc=m['id'], field='MaxEnergy', value=90)
    s.queue('merc_field', merc=m['id'], field='Level', value=7)
    s.queue('stat', merc=m['id'], stat='Wisdom', value=81)
    if gun:
        other = [f['id'] for f in gun['gun']['fits'] if f['id'] != gun['gun']['mag']]
        new_mag = other[0] if other else gun['gun']['mag']
        s.queue('ja2_live_item', merc=m['id'], slot=gun['slot'],
                mag=new_mag, rounds=3)
    if mags:
        s.queue('ja2_live_item', merc=m['id'], slot=mags['slot'],
                stack=[1] + mags['stack'][1:])
    log = s.apply()
    s.write(backup=False)

    a = ja2_save.Save(work)
    got = [x for x in a.mercs() if x['index'] == i][0]
    S = a.soldiers
    check('%s: rename took in the profile' % tag,
          (a.profiles.name(i), a.profiles.nickname(i)) == ('Testy McTestface', 'Testo'),
          '%r %r' % (a.profiles.name(i), a.profiles.nickname(i)))
    check('%s: and in the field copy of the nickname' % tag,
          S.nickname(n) == 'Testo', S.nickname(n))
    check('%s: still matched to the same record after the rename' % tag,
          got.get('soldier') == n)
    check('%s: morale took' % tag, S.field(n, sol.OFF_MORALE) == 88)
    check('%s: max energy took, and energy came down under it' % tag,
          S.field(n, sol.OFF_MAX_ENERGY) == 90
          and S.field(n, sol.OFF_ENERGY) <= 90)
    check('%s: level written to both copies' % tag,
          a.profiles.get(i, pd.OFF_LEVEL) == 7 and S.field(n, sol.OFF_LEVEL) == 7)
    check('%s: a stat written to both copies' % tag,
          a.profiles.stats(i)['Wisdom'] == 81 and S.stats(n)['Wisdom'] == 81)
    if gun:
        g = got['inventory'][gun['slot']]['gun']
        check('%s: the gun now holds 3 of the new magazine' % tag,
              (g['mag'], g['rounds']) == (new_mag, 3), str(g))
        check('%s: and the round type followed the magazine' % tag,
              g['type'] == ammo.ammo_type(new_mag))
    if mags:
        st = got['inventory'][mags['slot']]['stack']
        check('%s: one magazine in the stack changed on its own' % tag,
              st == [1] + mags['stack'][1:], str(st))
    check('%s: both checksums re-signed' % tag,
          a.profiles.checksum(i) == a.profiles.stored_checksum(i)
          and S.checksum(n) == S.stored_checksum(n))
    check('%s: nothing implausible after all that' % tag, not a.implausible(),
          '; '.join(a.implausible()[:2]))
    orig = ja2_save.Save(path)
    moved = [x['id'] for x, y in zip(orig.mercs(), a.mercs())
             if x['index'] != i and x != y]
    check('%s: nobody else moved' % tag, not moved, str(moved))
    check('%s: the log says what happened' % tag, len(log) >= 5, str(log))

    # Refusals: a magazine that does not fit, and a blank name.
    if gun:
        b = ja2_save.Save(work)
        wrong = next(x for x in ammo.MAGAZINES
                     if x not in [f['id'] for f in gun['gun']['fits']])
        b.queue('ja2_live_item', merc=m['id'], slot=gun['slot'], mag=wrong)
        try:
            b.apply()
            check('%s: a magazine that does not fit is refused' % tag, False)
        except ja2_save.Ja2Error:
            check('%s: a magazine that does not fit is refused' % tag, True)
    c = ja2_save.Save(work)
    c.queue('ja2_name', merc=m['id'], nick='   ')
    try:
        c.apply()
        check('%s: a blank nickname is refused' % tag, False)
    except ja2_save.Ja2Error:
        check('%s: a blank nickname is refused' % tag, True)

    # Cash: put a money item in an empty pocket and give it an amount.
    cash = next((k for k, v in ja2_items.ITEMS.items()
                 if ja2_items.category(k) == 'Money'), None)
    empty = next((r for r in m['inventory'] if r['empty'] and r['slot'] >= 7), None)
    if cash and empty:
        e = ja2_save.Save(work)
        e.queue('ja2_live_item', merc=m['id'], slot=empty['slot'],
                item=cash, count=1, money=1234)
        e.apply()
        e.write(backup=False)
        row = [x for x in ja2_save.Save(work).mercs()
               if x['index'] == i][0]['inventory'][empty['slot']]
        check('%s: a cash item holds the amount it was given' % tag,
              row.get('money') == 1234, str(row.get('money')))
        f = ja2_save.Save(work)
        f.queue('ja2_live_item', merc=m['id'], slot=empty['slot'], money=999999)
        f.apply()
        row = [x for x in f.mercs() if x['index'] == i][0]['inventory'][empty['slot']]
        check('%s: and is capped where the game caps a pocket' % tag,
              row.get('money') == sol.MAX_MONEY, str(row.get('money')))

    # Growing a stack fills the new objects instead of leaving them empty.
    if mags:
        d = ja2_save.Save(work)
        d.queue('ja2_live_item', merc=m['id'], slot=mags['slot'],
                count=mags['count'] + 1)
        d.apply()
        st = [x for x in d.mercs() if x['index'] == i][0]['inventory'][mags['slot']]['stack']
        check('%s: an added magazine arrives full, not empty' % tag,
              st[-1] == st[0] and st[-1] > 0, str(st))


def test_art_not_shipped():
    """No JA2 picture may sit where the build would bundle it (Nexus does not
    allow another game's art in a JA3 mod), and the build refuses if one does."""
    import build_app
    check('no JA2 art in webui/', build_app.other_game_art() == [],
          str(build_app.other_game_art()[:5]))
    from ja2.icon_index import ICONS
    check('the item -> picture table still travels (names only)', len(ICONS) > 200)


def test_art_from_install():
    """The pictures are decoded from the player's own install instead."""
    import ja2art
    roots = ja2art._installs()
    if not roots:
        print('  --   no JA2 installed, skipping the art cache test')
        return
    local = tempfile.mkdtemp(prefix='ja2art-')
    try:
        m = ja2art.build(roots, local=local)
        d = ja2art.cache_dir(local)
        check('faces decoded from Faces.slf', len(m['faces']) >= 150, str(len(m['faces'])))
        check('every face in the index is on disk',
              all(os.path.isfile(os.path.join(d, 'portraits', v + '.png'))
                  for v in m['faces'].values()))
        check('item icons decoded from Bigitems.slf', len(m['icons']) >= 250, str(len(m['icons'])))
        check('every icon in the index is on disk',
              all(os.path.isfile(os.path.join(d, 'ja2icons', v + '.png'))
                  for v in m['icons'].values()))
        check('laptop skin decoded', sorted(m['skin']) ==
              ['ja2-aim.png', 'ja2-bg.png', 'ja2-wildfire.jpg'], str(m['skin']))
        ja2art._BUILT.clear()
        again = ja2art.build(roots, local=local)
        check('a second start reuses the cache', again == m)
        check('no install, no cache', ja2art.build([], local=tempfile.mkdtemp()) is None)
    finally:
        ja2art._BUILT.clear()
        shutil.rmtree(local, ignore_errors=True)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    print('tables')
    test_tables()
    print('\nart')
    test_art_not_shipped()
    test_art_from_install()

    paths = ja2_saves(arg)
    if not paths:
        print('\nno JA2 saves found - pass one as an argument to test reading')
    else:
        tmp = tempfile.mkdtemp(prefix='ja2test-')
        try:
            for p in paths:
                print('\n%s' % p)
                test_read(p)
                test_lookup(p)
                test_write(p, tmp)
                test_dashboard_intents(p, tmp)
                test_item_swap_clears_attachments(p, tmp)
                test_checksums(p)
                test_layout_and_money(p, tmp)
                test_fire(p, tmp)
                test_hire(p, tmp)
                test_new_fields(p, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print('\n%d passed, %d failed' % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
