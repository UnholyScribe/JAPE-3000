"""Pull the item table out of ja2.exe, and every item's icon out of Bigitems.slf.

    python build_ja2_items.py ["E:\\Jagged Alliance 2"]

Writes ja2/icon_index.py (which picture each item uses - names only) and
ja2/item_stats.py. Reads the game, never writes to it. The pictures themselves
are not shipped; ja2art.py decodes them from the player's install at run time.

WHY THIS EXISTS
---------------
The editor could name all 328 items and draw pictures for 49 of them. Armour,
ammunition, medical kit and tools all showed a category symbol instead, and the
item panel had Weight greyed out with "not available". Both had the same cause:
the name-to-picture and name-to-weight tables are not in any data file, they
are static arrays compiled into the executable.

FINDING THE TABLE
-----------------
Not by knowing an address - by its shape, so it survives a different build.

The guns are items 1..45 and their art is GUN00..GUN49.STI, so somewhere in the
executable is a byte column that counts up in step with the item id. Scanning
every stride from 8 to 48 for a column that counts 1, 2, 3 ... for at least 30
entries finds exactly one convincing answer: **stride 20**, which is the size
of one record. From there the fields fall out by checking them against things
that can be verified from outside:

    Glock 17        0.6 kg      Thompson M1A1   4.8 kg
    H&K MP5K        2.1 kg      Steel Helmet    1.4 kg

Those are the real weights. A wrong offset does not produce four right answers.

    +0  usItemClass   UINT16    2 = gun, and so on
    +4  ubClassIndex  UINT8
    +5  ubCursor      UINT8
    +7  ubGraphicType UINT8     0 = GUNnn.STI, n = the PnITEMnn.STI bank
    +8  ubGraphicNum  UINT8     which file inside that bank
    +9  ubWeight      UINT8     tenths of a kilo
    +10 ubPerPocket   UINT8
    +12 usPrice       UINT16
    +14 ubCoolness    UINT8
    +15 bReliability  INT8
    +16 bRepairEase   INT8

The graphic mapping then checks itself: type 3 is used by exactly 12 items and
Bigitems.slf holds exactly 12 P3ITEM files.

This also corrects something. The 49 gun icons already shipped were extracted
on the assumption that GUNnn is item nn. That is true for most guns and wrong
for the rest - item 50, the Underslung Grenade Launcher, uses GUN39 - so a few
weapons were showing the wrong picture. The table settles it.
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from ja2 import item_data                                        # noqa: E402
from ja2.slf import Library                                      # noqa: E402
from ja2.sti import Sti                                          # noqa: E402

ICON_INDEX = os.path.join(HERE, 'ja2', 'icon_index.py')
STATS = os.path.join(HERE, 'ja2', 'item_stats.py')
RECORD = 20
N = max(item_data.ITEMS) + 1


def find_game(argv):
    if len(argv) > 1:
        return argv[1]
    for p in (r'E:\Jagged Alliance 2', r'C:\Jagged Alliance 2',
              r'C:\Program Files (x86)\Jagged Alliance 2'):
        if os.path.isfile(os.path.join(p, 'ja2.exe')):
            return p
    raise SystemExit('Point me at the game: python build_ja2_items.py "<path>"')


def find_table(exe):
    """Locate Item[] by the column that counts with the item id.

    Deliberately a search and not a constant. An address is right for one
    executable; the shape is right for the format.
    """
    n = len(exe)
    runs = []
    for stride in range(8, 49):
        limit = n - 40 * stride
        p = 0
        while True:
            p = exe.find(b'\x01', p, limit)
            if p < 0:
                break
            if exe[p + stride] == 2 and exe[p + 2 * stride] == 3:
                k = 1
                while exe[p + (k - 1) * stride] == k and k < 60:
                    k += 1
                if k >= 30:
                    runs.append((k - 1, stride, p))
                    p += stride * k
                    continue
            p += 1
    if not runs:
        raise SystemExit('no counting column in this executable - either it is '
                         'not ja2.exe or this build lays the table out '
                         'differently')
    runs.sort(reverse=True)
    return [(k, st, p) for k, st, p in runs if st == RECORD]


def read_table(exe, base):
    out = {}
    for i in range(N):
        p = base + i * RECORD
        (cls, _a, cidx, cur, _b, gtype, gnum, weight, per_pocket, _c,
         price, cool, rel, rep) = struct.unpack_from('<HHBBBBBBBBHBbb', exe, p)
        out[i] = dict(cls=cls, gtype=gtype, gnum=gnum, weight=weight,
                      per_pocket=per_pocket, price=price, coolness=cool,
                      reliability=rel, repair=rep)
    return out


def check(table):
    """Everything wrong with this table, as a list. Empty means it is right.

    Two different columns count up with the item id - the index within the
    item's class, and the graphic number - because for guns those are the same
    number. So the scan cannot tell on its own which field it landed on. Rather
    than reason about which, every alignment is tried and this decides. The
    check is made of facts from outside the file: real gun weights, and the
    rule that a weapon's art is always a GUN file.
    """
    problems = []
    weapons = [i for i in range(N) if item_data.category(i) == 'Weapon']
    off = [i for i in weapons if table[i]['gtype'] != 0]
    if off:
        problems.append('%d weapons are not graphic type 0 (%s)'
                        % (len(off), off[:5]))
    # the four weights that were checked against the real guns by hand
    for i, want in ((1, 6), (9, 21), (11, 48), (176, 14)):
        if i in table and table[i]['weight'] != want:
            problems.append('item %d (%s) weighs %d, expected %d'
                            % (i, item_data.name(i), table[i]['weight'], want))
    return problems


def art_name(row):
    """Which file holds this item's picture, or None if it has none.

    Forty items point at GUN00, and only one of them means it: the Mortar,
    which weighs 7.7 kg and costs 1800. The other thirty-nine are creature
    claws, spit, and the twenty-four Key entries - things the player never
    sees in an inventory panel - and their graphic fields were simply never
    filled in, so they read 0/0 like everything else that was left alone.
    Drawing GUN00 for those put a mortar tube next to the word "Key".

    Weight and price are the tell. An item with no graphic, no weight and no
    price is an unset row; an item with a weight is a real thing.
    """
    if row['gtype'] == 0 and row['gnum'] == 0 \
            and not row['weight'] and not row['price']:
        return None
    if row['gtype'] == 0:
        return 'GUN%02d.STI' % row['gnum']
    return 'P%dITEM%02d.STI' % (row['gtype'], row['gnum'])


def main(argv):
    root = find_game(argv)
    exe = open(os.path.join(root, 'ja2.exe'), 'rb').read()
    runs = find_table(exe)
    if not runs:
        raise SystemExit('no counting column at stride %d in this executable'
                         % RECORD)

    table = base = None
    why = []
    for length, _stride, p in runs:
        for field in range(RECORD):
            start = p - field - RECORD          # item 0
            if start < 0 or start + N * RECORD > len(exe):
                continue
            cand = read_table(exe, start)
            bad = check(cand)
            if not bad:
                table, base = cand, start
                print('item table at 0x%X - a column at +%d counts to %d'
                      % (start, field, length))
                break
            why = bad
        if table:
            break
    if not table:
        raise SystemExit('found a counting column but no alignment of it '
                         'produces a sane table. Closest attempt:\n  '
                         + '\n  '.join(why))
    print('checked: every weapon is a GUN file, and four known weights match')

    pack = os.path.join(root, 'Data', 'Bigitems.slf')
    lib = Library(pack)
    have = {}
    for nm, _off, _ln, _st in lib.listing():
        have[nm.split('\\')[-1].upper()] = nm

    index = {}
    missing = {}
    written = set()
    for i in range(1, N):
        if i not in item_data.ITEMS:
            continue
        row = table[i]
        want = art_name(row)
        if not want:
            continue           # no picture in the game either - see art_name
        real = have.get(want)
        if not real:
            missing.setdefault(want, []).append(i)
            continue
        key = want[:-4].lower()
        if key not in written:
            try:
                Sti(lib.read(real)).image(0)        # must decode, or skip it
            except Exception as exc:                          # noqa: BLE001
                missing.setdefault('%s (%s)' % (want, exc), []).append(i)
                continue
            written.add(key)
        index[i] = key
    lib.close()

    with open(ICON_INDEX, 'w', encoding='utf-8', newline='\n') as f:
        f.write('"""Which picture each JA2 item uses: item id -> file in '
                'Data/Bigitems.slf\n(lower case, no extension). Generated by '
                'build_ja2_items.py.\n\nNames only. The pictures are the '
                'game\'s and are read from the player\'s own\ninstall at run '
                'time by ja2art.py - none travel inside the editor.\n"""\n\n'
                'ICONS = {\n')
        for i in sorted(index):
            f.write('    %d: %r,\n' % (i, index[i]))
        f.write('}\n')
    print('wrote %s' % ICON_INDEX)

    named = len(item_data.ITEMS) - 1
    print('%d icons covering %d of %d items (%d have no art in the game)'
          % (len(written), len(index), named, named - len(index)))
    if missing:
        print('no art for %d files:' % len(missing))
        for k, v in sorted(missing.items())[:10]:
            print('   %-16s wanted by items %s' % (k, v[:6]))

    with open(STATS, 'w', encoding='utf-8') as f:
        f.write('"""Weight, price and durability for every item, out of'
                ' ja2.exe.\n\n'
                'Generated by build_ja2_items.py - see that file for how the'
                ' table was\nfound and how to check it is still right.'
                ' Weight is in tenths of a kilo,\nwhich is how the game'
                ' stores it.\n"""\n\n')
        f.write('# id: (weight, price, per_pocket, coolness, reliability,'
                ' repair_ease)\nSTATS = {\n')
        for i in sorted(index):
            r = table[int(i)]
            f.write('    %s: (%d, %d, %d, %d, %d, %d),\n'
                    % (i, r['weight'], r['price'], r['per_pocket'],
                       r['coolness'], r['reliability'], r['repair']))
        f.write('}\n\n\n')
        f.write('def weight(item):\n'
                '    """Kilos, or None if this item is not in the table."""\n'
                '    row = STATS.get(item)\n'
                '    return row[0] / 10.0 if row else None\n\n\n'
                'def price(item):\n'
                '    row = STATS.get(item)\n'
                '    return row[1] if row else None\n\n\n'
                'def stack(item):\n'
                '    """How many fit in one pocket. 0 means it does not'
                ' stack."""\n'
                '    row = STATS.get(item)\n'
                '    return row[2] if row else None\n')
    print('wrote %s' % STATS)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
