"""Compare two savegames and say what is different, in plain terms.

Written for one job in particular: you edit a save, load it in game, let the
game write it back, and then ask what the game actually kept. A field the game
recomputes or rejects shows up here immediately, which beats staring at a merc
sheet trying to remember what the number used to be.

It also works on a save and one of its own `.bak` files, which is the quickest
way to see what the editor just did.

This is read-only. Nothing here opens a file for writing.
"""
import os

import ja3_save


def _walk_slots(merc):
    """Gear, keyed by where it sits so the same slot compares to itself.

    The per-item `id` is deliberately left out. It is reassigned whenever an
    item is added, so including it would report a change every time and drown
    the things you actually want to see.
    """
    out = {}
    for slot, entries in (merc.get('slots') or {}).items():
        for i, item in enumerate(entries or []):
            where = item.get('index')
            key = '%s[%s]' % (slot, i if where is None else where)
            out[key + '.item'] = item.get('item')
            for field in ('amount', 'condition', 'drop_chance'):
                if item.get(field) is not None:
                    out['%s.%s' % (key, field)] = item.get(field)
            comps = item.get('components') or {}
            if isinstance(comps, dict):
                for cslot in sorted(comps):
                    out['%s.%s' % (key, cslot)] = comps[cslot]
    return out


def flatten(summary):
    """A summary as {path: value}, with stable keys on both sides.

    Everything is keyed by identity - merc id, squad uid, sector id - rather
    than by list position, so a merc moving in the list is not reported as
    every merc changing.
    """
    flat = {'money': summary.get('money')}

    meta = summary.get('meta') or {}
    for key in ('game_date', 'map', 'difficulty', 'daily_income', 'playtime',
                'campaign_time', 'playthrough_name'):
        if key in meta:
            flat['meta.' + key] = meta[key]

    for merc in summary.get('mercs') or []:
        who = merc.get('nick') or merc.get('name') or merc.get('id')
        base = 'merc[%s]' % who
        for key in ('hp', 'max_hp', 'xp', 'level', 'squad', 'tiredness',
                    'morale', 'operation', 'hire_status', 'hired_until',
                    'custom_name'):
            flat['%s.%s' % (base, key)] = merc.get(key)
        for stat, val in sorted((merc.get('stats') or {}).items()):
            if isinstance(val, dict):
                val = val.get('value')
            flat['%s.stat.%s' % (base, stat)] = val
        # perks as a set: order in the save is not meaningful
        for perk in sorted(merc.get('perks') or []):
            flat['%s.perk.%s' % (base, perk)] = True
        wounds = merc.get('wounds')
        if isinstance(wounds, (list, tuple, set)):
            for wound in sorted(wounds):
                flat['%s.wound.%s' % (base, wound)] = True
        elif wounds is not None:
            flat[base + '.wounds'] = wounds
        for key, val in _walk_slots(merc).items():
            flat['%s.%s' % (base, key)] = val

    for squad in summary.get('squads') or []:
        base = 'squad[%s]' % (squad.get('name') or squad.get('uid'))
        flat[base + '.sector'] = squad.get('sector')
        flat[base + '.units'] = ', '.join(squad.get('units') or [])

    for sector in summary.get('sectors') or []:
        base = 'sector[%s]' % sector.get('id')
        for key in ('owned', 'side', 'intel', 'visited', 'guardpost'):
            flat['%s.%s' % (base, key)] = sector.get(key)
        if sector.get('stash'):
            counts = {}
            for item in sector['stash']:
                name = item.get('item')
                counts[name] = counts.get(name, 0) + (item.get('amount') or 1)
            for name in sorted(counts):
                flat['%s.stash.%s' % (base, name)] = counts[name]

    cheats = summary.get('cheats') or {}
    for name, on in sorted((cheats.get('flags') or {}).items()):
        flat['cheat.' + name] = on

    bag = {}
    for item in summary.get('squad_bag') or []:
        name = item.get('item')
        bag[name] = bag.get(name, 0) + (item.get('amount') or 1)
    for name in sorted(bag):
        flat['bag.%s' % name] = bag[name]

    return flat


def compare(path_a, path_b):
    """What changed going from A to B."""
    a = flatten(ja3_save.Save(path_a).summary())
    b = flatten(ja3_save.Save(path_b).summary())

    changed, added, removed = [], [], []
    for key in sorted(set(a) | set(b)):
        in_a, in_b = key in a, key in b
        if in_a and in_b:
            if a[key] != b[key]:
                changed.append({'what': key, 'from': a[key], 'to': b[key]})
        elif in_b:
            added.append({'what': key, 'to': b[key]})
        else:
            removed.append({'what': key, 'from': a[key]})

    return {
        'a': os.path.basename(path_a),
        'b': os.path.basename(path_b),
        'changed': changed,
        'added': added,
        'removed': removed,
        'total': len(changed) + len(added) + len(removed),
    }


def _show(val):
    if val is True:
        return 'yes'
    if val is False:
        return 'no'
    if val is None:
        return '-'
    return str(val)


def report(diff):
    """The comparison as lines of text, for the console."""
    lines = ['%s  ->  %s' % (diff['a'], diff['b'])]
    if not diff['total']:
        lines.append('  identical in everything the editor can see')
        return lines
    for row in diff['changed']:
        lines.append('  ~ %-44s %s -> %s'
                     % (row['what'], _show(row['from']), _show(row['to'])))
    for row in diff['added']:
        lines.append('  + %-44s %s' % (row['what'], _show(row['to'])))
    for row in diff['removed']:
        lines.append('  - %-44s was %s' % (row['what'], _show(row['from'])))
    lines.append('%d difference%s'
                 % (diff['total'], '' if diff['total'] == 1 else 's'))
    return lines
