"""Reads the catalogue the script extender writes out of the running game.

JAPE's own `catalog.json` is baked into the exe at build time, so it knows the
322 mercs that shipped with the game and nothing else. Anything a mod adds is
invisible to it - which is why a modded merc can be playable in the game and
still not appear under Recruit, and why the enemy box on the live page was a
free-text field asking for internal ids nobody could be expected to guess.

The extender writes `ja3se-catalog.txt` into the game's AppData folder every
time the game boots, straight out of the running game's own preset tables. So
it already contains whatever mods are loaded, with no per-mod special casing.

**This is an overlay, never a replacement.** The editor has to keep working for
somebody with no extender installed, so every caller treats an empty catalogue
as "carry on as before" rather than as an error. The file is also only as fresh
as the last time the game ran.

Format is sectioned TSV, the same shape as the other live files:

    [units] UnitDataCompositeDef
    #id     Name    Affiliation   MaxHitPoints  Portrait  group
    Legion_Raider   Raider  Legion  50  UI/...  Legion

Lines starting with # are comments or the column header for the section above.
"""
import os
import time

import livegame

CATALOG = 'ja3se-catalog.txt'
# Everything standing in the sector that is loaded right now, with what each
# one is carrying. Rewritten by the extender on every change, so it is a
# snapshot rather than a subscription - read it, do not cache it hard.
SECTOR = 'ja3se-sector.txt'

# Which affiliations count as somebody you would place as an enemy. Taken from
# the Affiliation column rather than guessed: the file also carries Civilian,
# Rebel and blank, and offering those under "enemies" would be wrong.
HOSTILE = ('Legion', 'Adonis', 'Army', 'Thugs', 'SuperSoldiers', 'Beast')

_cache = {'when': 0, 'path': None, 'data': None}
FRESH_FOR = 5.0            # seconds; the file only changes when the game boots


def path(folder=None):
    return livegame._path(CATALOG, folder)                     # noqa: SLF001


def parse(text):
    """Sectioned TSV -> {section: [ {column: value}, ... ]}."""
    out, rows, cols = {}, None, None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith('['):
            name = line[1:].split(']', 1)[0].strip()
            rows = out.setdefault(name, [])
            cols = None
            continue
        if line.startswith('#'):
            # the first # line after a section header names the columns
            if rows is not None and cols is None:
                cols = [c.strip() for c in line[1:].split('\t')]
            continue
        if rows is None or cols is None:
            continue
        parts = line.split('\t')
        row = {}
        for i, c in enumerate(cols):
            row[c] = parts[i] if i < len(parts) else ''
        rows.append(row)
    return out


def read(folder=None, force=False):
    """The catalogue, or {} when the extender has never run here."""
    p = path(folder)
    now = time.time()
    if (not force and _cache['data'] is not None and _cache['path'] == p
            and now - _cache['when'] < FRESH_FOR):
        return _cache['data']
    data = {}
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            data = parse(f.read())
    except OSError:
        data = {}                 # no extender, or the game has not run yet
    _cache.update(when=now, path=p, data=data)
    return data


def sector_path(folder=None):
    return livegame._path(SECTOR, folder)                      # noqa: SLF001


def sector(folder=None):
    """Units on the map right now, each with its inventory attached.

    Shape, from the extender's own writer:

        [units]      session_id, unit_id, name, side, hired, flag
        [inventory]  session_id, slot, item, amount, condition

    The two are joined here rather than in the browser, because the join key is
    a fact about the file and belongs beside the thing that knows the column
    names.

    Returns `{'ok': False, 'why': ...}` when the file is not there, which is the
    normal state until the game has run with a current extender. That is not an
    error and the panel says so rather than looking broken.
    """
    p = sector_path(folder)
    try:
        with open(p, 'r', encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        return {'ok': False, 'path': p, 'units': [],
                'why': 'no sector file yet - start the game with the script '
                       'extender installed and load a sector'}

    data = parse(text)
    head = {}
    for line in text.splitlines()[:6]:
        if line.startswith('#ja3se'):
            for bit in line.split():
                if '=' in bit:
                    k, v = bit.split('=', 1)
                    head[k.lstrip('#')] = v

    inv = {}
    for r in data.get('inventory', []):
        inv.setdefault(r.get('session_id', ''), []).append({
            'slot': r.get('slot', ''),
            'item': r.get('item', ''),
            'amount': _int(r.get('amount')),
            'condition': _int(r.get('condition')),
        })

    units = []
    for r in data.get('units', []):
        sid = r.get('session_id', '')
        units.append({
            'session_id': sid,
            'id': r.get('unit_id', ''),
            'name': r.get('name', '') or r.get('unit_id', ''),
            'side': r.get('side', ''),
            'hired': (r.get('hired', '') or '').lower() in ('1', 'true', 'yes'),
            'flag': r.get('flag', ''),
            'inventory': inv.get(sid, []),
        })

    # The extender counts Jager's session-id collision directly rather than
    # asserting on it, because assert() does not raise in this engine and would
    # report nothing. If it is non-zero, an edit can land on the wrong unit -
    # so the panel has to say so rather than quietly editing something.
    dupes = _int(head.get('duplicate_session_ids'))
    seen = {}
    for u in units:
        seen[u['session_id']] = seen.get(u['session_id'], 0) + 1
    clashing = sorted(k for k, n in seen.items() if n > 1 and k)

    return {
        'ok': True,
        'path': p,
        'units': units,
        'install': head.get('install'),
        'duplicate_session_ids': dupes or len(clashing),
        'clashing': clashing,
    }


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0

def summary(folder=None):
    """What the front end needs, already shaped and sorted.

    Enemies and mercs are separated here rather than in the browser, because
    the rule for telling them apart is a fact about the game's data and belongs
    next to the thing that knows the column names.
    """
    cat = read(folder)
    units = cat.get('units') or []
    enemies, mercs = [], []
    for u in units:
        uid = (u.get('id') or '').strip()
        if not uid:
            continue
        row = {
            'id': uid,
            'name': (u.get('Name') or uid).strip() or uid,
            'group': (u.get('Affiliation') or '').strip(),
            'hp': (u.get('MaxHitPoints') or '').strip(),
        }
        if row['group'] in HOSTILE:
            enemies.append(row)
        # MercenariesOld and MercenariesNew are the recruitable lists; a mod
        # that adds a hireable merc puts them in one of those, so filtering on
        # the group picks up mod content with nothing per-mod anywhere.
        if (u.get('group') or '').startswith('Mercenaries'):
            mercs.append(row)
    key = lambda r: (r['group'], r['name'].lower())
    enemies.sort(key=key)
    mercs.sort(key=lambda r: r['name'].lower())
    return {
        'present': bool(units),
        'path': path(folder),
        'enemies': enemies,
        'mercs': mercs,
        'counts': {k: len(v) for k, v in cat.items()},
    }
