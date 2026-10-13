"""Build catalog.json from the official THQ Nordic JA3 modding repo.

    python build_catalog.py [path-to-JaggedAlliance3Modding.zip | repo dir]

Downloads the repo zip if no source is given. The catalog gives the editor
real display names, groups and costs for every item, merc and perk in the
base game, instead of guessing from ids.
"""
import io
import json
import os
import sys
import urllib.request
import zipfile

import lua_text

ZIP_URL = 'https://codeload.github.com/THQNordic/JaggedAlliance3Modding/zip/refs/heads/main'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalog.json')

ITEM_DIR = 'Data/InventoryItemCompositeDef/'
UNIT_DIR = 'Data/UnitDataCompositeDef/'
EFFECT_DIR = 'Data/CharacterEffectCompositeDef/'
COMPONENT_FILE = 'Data/WeaponComponentSharedClass.lua'

STATS = ('Health', 'Agility', 'Dexterity', 'Strength', 'Wisdom', 'Leadership',
         'Marksmanship', 'Mechanical', 'Explosives', 'Medical')


def text_of(node):
    """Pull the English string out of T(id, "text") / T{...} / a bare string."""
    if node is None:
        return None
    if node.kind == 'str':
        return node.value
    if node.kind == 'call' and node.fname == 'T':
        tbl = node.args[0] if node.args and node.args[0].kind == 'table' else None
        if tbl is not None:
            for it in tbl.items:
                if it.kind == 'str':
                    return it.value
        for a in node.args:
            if a.kind == 'str':
                return a.value
    return None


def num_of(node):
    return node.value if node is not None and node.kind == 'num' else None


_re = __import__('re')
_TAGS = _re.compile(r'<[^>]{1,80}>')


def _params(node):
    """{name: value} from a def's Parameters list."""
    out = {}
    if node is None or node.kind != 'table':
        return out
    for entry in node.items:
        tbl = entry.table() if entry.kind == 'call' else entry
        if tbl is None or tbl.kind != 'table':
            continue
        p = tbl.props()
        name = text_of(p.get('Name'))
        val = num_of(p.get('Value'))
        if name is not None and val is not None:
            out[name] = val
    return out


def _plain(s, params=None):
    """Resolve the game's inline markup into readable English.

    Descriptions are written with the numbers as tags that the game fills in
    from the effect's own Parameters - Deadeye's reads "Gain
    <em><percent(crit_per_aim)></em> extra <GameTerm('Crit')> chance per
    <GameTerm('Aim')>." Stripping the tags wholesale throws the numbers away
    along with the markup, so they are substituted here instead.
    """
    params = params or {}

    def num(name):
        v = params.get(name)
        if v is None:
            return None
        return str(int(v)) if float(v).is_integer() else str(v)

    # <percent(x)> -> "5%"
    def percent(m):
        v = num(m.group(1))
        return (v + '%') if v is not None else ''
    s = _re.sub(r"<percent\(\s*([A-Za-z_0-9]+)\s*\)>", percent, s)

    # <Multiply(a, b)> -> the product
    def multiply(m):
        a, b = params.get(m.group(1)), params.get(m.group(2))
        if a is None or b is None:
            return ''
        v = a * b
        return str(int(v)) if float(v).is_integer() else str(v)
    s = _re.sub(r"<Multiply\(\s*([A-Za-z_0-9]+)\s*,\s*([A-Za-z_0-9]+)\s*\)>", multiply, s)

    # <StatPercent('Medical', medicalPercent)> -> "20% of Medical"
    def statpercent(m):
        v = num(m.group(2))
        return ('%s%% of %s' % (v, m.group(1))) if v is not None else m.group(1)
    s = _re.sub(r"<StatPercent\(\s*'([^']+)'\s*,\s*([A-Za-z_0-9]+)\s*\)>", statpercent, s)

    # <GameTerm('Crit')> / <AdditionalTerm('X')> -> the term itself
    s = _re.sub(r"<(?:GameTerm|AdditionalTerm)\(\s*'([^']+)'\s*\)>", r'\1', s)

    # bare <paramName> -> its value
    def bare(m):
        v = num(m.group(1))
        return v if v is not None else ''
    s = _re.sub(r"<([A-Za-z_][A-Za-z_0-9]*)>",
                lambda m: bare(m) if m.group(1) in params else m.group(0), s)

    # whatever markup is left (<em>, <color X>, </color>) is presentation only
    s = _TAGS.sub('', s)
    s = _re.sub(r'\s+([.,;:!?])', r'\1', s)
    return ' '.join(s.split())


def read_defs(source, prefix, class_name):
    """Yield the property dict of each PlaceObj(class_name, {...}) under prefix.

    These files are machine-generated with one tab of indent per top-level
    property. Many also embed Lua function bodies, which the save-text parser
    deliberately does not accept, so top-level properties are read directly
    off the text and only the value part is parsed.
    """
    needle = "PlaceObj('%s', {" % class_name
    for name in source.namelist():
        rel = name.split('/', 1)[-1]
        if not rel.startswith(prefix) or not rel.endswith('.lua'):
            continue
        src = source.read(name).decode('utf-8', errors='replace')
        i = src.find(needle)
        if i < 0:
            continue
        yield _top_level_props(src[i + len(needle):])


_PROP_RE = __import__('re').compile(r"^\t'([A-Za-z_][A-Za-z_0-9 ]*)',[ \t]*", __import__('re').M)


def _top_level_props(body):
    """{'Key': Node} for properties written at exactly one tab of indent.

    Each value is parsed from a slice bounded by the next top-level property,
    so embedded Lua function bodies further down the file can't derail it.
    """
    matches = list(_PROP_RE.finditer(body))
    out = {}
    for k, m in enumerate(matches):
        key = m.group(1)
        if key in out:
            continue
        stop = matches[k + 1].start() if k + 1 < len(matches) else len(body)
        try:
            out[key] = lua_text.Parser(body[m.end():stop]).parse_value()
        except (lua_text.LuaSyntaxError, IndexError):
            continue
    return out


# Which of the merc's slots each kind of item may legally occupy. Weapon and
# armour classes come from the game's own object_class; armour's Slot field
# names Head or Legs and is simply absent for torso pieces.
HAND_CLASSES = {
    'AssaultRifle', 'SniperRifle', 'SubmachineGun', 'MachineGun', 'Shotgun',
    'Pistol', 'Revolver', 'MeleeWeapon', 'MacheteWeapon', 'StackableMeleeWeapon',
    'GrenadeLauncher', 'RocketLauncher', 'Mortar', 'FlareGun',
}
ARMOR_CLASSES = {'Armor', 'TransmutedArmor'}
HANDS = ('Handheld A', 'Handheld B')


# The game sorts perks by Tier, and that is the cleanest way to tell the three
# kinds apart: the blue signature talents, the tactical (specialization) perks,
# personality perks and quirks, and the tiered stat perks.
POOLS = {
    'Personal': 'Talent',
    'Specialization': 'Tactical',
    'Personality': 'Personality',
    'Quirk': 'Quirk',
    'Bronze': 'Stat', 'Silver': 'Stat', 'Gold': 'Stat',
}

# Bond is incomplete in the game data - it expects a partner character and has
# a placeholder description. Every IMP mod that touches perks excludes it.
NEVER_ASSIGN = {'Bond'}

# Signature talents that "do not make sense or can potentially break the game"
# on a character they were not written for. This list is the one used by the
# Extended IMP Character Creation mod, which gates them behind an opt-in.
RISKY_TALENTS = {
    'DesignerExplosives', 'FoxPerk', 'HawksEye', 'HundredKnives', 'InnerInfo',
    'KillingWind', 'NaturalHealing', 'SteroidPunch', 'WeGotThis', 'YouSeeIgor',
}

# What the game will actually accept in a merc's Specialization field.
SPECIALIZATIONS = ['AllRounder', 'Marksmen', 'Leader', 'Doctor',
                   'ExplosiveExpert', 'Mechanic']

# Specialization is derived from stats when the game builds an IMP: the highest
# mapped stat wins if it clears 74, with the technical skills given a +15 edge.
SPEC_FROM_STAT = {'Marksmanship': 'Marksmen', 'Leadership': 'Leader',
                  'Medical': 'Doctor', 'Explosives': 'ExplosiveExpert',
                  'Mechanical': 'Mechanic'}
SPEC_TECH_BONUS = 15
SPEC_THRESHOLD = 74


def _str_list(node):
    return [x.value for x in node.items if x.kind == 'str']         if node is not None and node.kind == 'table' else []


def _fits(object_class, wear_slot):
    """Editor slots this item may be placed in. The backpack takes anything."""
    fits = ['Inventory', 'Pick']
    if object_class in HAND_CLASSES:
        fits += list(HANDS)
    elif object_class in ARMOR_CLASSES:
        fits.append(wear_slot if wear_slot in ('Head', 'Legs') else 'Torso')
    return fits


def _component_slots(node):
    """[{slot, optional, default, options[]}] from a weapon's ComponentSlots.

    This is what the game will let you bolt onto that particular weapon: each
    slot names the mount point and the components it accepts.
    """
    out = []
    if node is None or node.kind != 'table':
        return out
    for entry in node.items:
        tbl = entry.table() if entry.kind == 'call' else entry
        if tbl is None or tbl.kind != 'table':
            continue
        p = tbl.props()
        slot = text_of(p.get('SlotType'))
        if not slot:
            continue
        avail = p.get('AvailableComponents')
        options = [x.value for x in avail.items if x.kind == 'str'] \
            if avail is not None and avail.kind == 'table' else []
        out.append({
            'slot': slot,
            'optional': bool(p.get('CanBeEmpty') is not None
                             and p.get('CanBeEmpty').value),
            'default': text_of(p.get('DefaultComponent')),
            'options': options,
        })
    return out


# WeaponComponentSharedClass.lua writes `Key = value,` at one tab of indent
# rather than the positional `'Key', value` style the composite defs use.
_EQ_PROP_RE = __import__('re').compile(r"^\t([A-Za-z_][A-Za-z_0-9]*) = ", __import__('re').M)


def read_components(source):
    """Every WeaponComponent preset: the attachments themselves."""
    name = next((n for n in source.namelist()
                 if n.split('/', 1)[-1] == COMPONENT_FILE), None)
    if name is None:
        return []
    src = source.read(name).decode('utf-8', errors='replace')
    out, needle = [], "PlaceObj('WeaponComponent', {"
    pos = 0
    while True:
        i = src.find(needle, pos)
        if i < 0:
            break
        pos = i + len(needle)
        nxt = src.find(needle, pos)
        body = src[pos:nxt if nxt > 0 else len(src)]
        matches = list(_EQ_PROP_RE.finditer(body))
        props = {}
        for k, m in enumerate(matches):
            stop = matches[k + 1].start() if k + 1 < len(matches) else len(body)
            if m.group(1) in props:
                continue
            try:
                props[m.group(1)] = lua_text.Parser(body[m.end():stop]).parse_value()
            except (lua_text.LuaSyntaxError, IndexError):
                continue
        cid = text_of(props.get('id'))
        if not cid:
            continue
        effects = props.get('ModificationEffects')
        out.append({
            'id': cid,
            'name': text_of(props.get('DisplayName')) or cid,
            'slot': text_of(props.get('Slot')),
            'icon': text_of(props.get('Icon')),
            'cost': num_of(props.get('Cost')),
            'group': text_of(props.get('group')) or '',
            'effects': [x.value for x in effects.items if x.kind == 'str']
                       if effects is not None and effects.kind == 'table' else [],
        })
    return out


def build(source):
    items, mercs, perks = [], [], []

    for p in read_defs(source, ITEM_DIR, 'InventoryItemCompositeDef'):
        iid = text_of(p.get('Id'))
        if not iid:
            continue
        items.append({
            'id': iid,
            'name': text_of(p.get('DisplayName')) or iid,
            'group': text_of(p.get('Group')) or 'Other',
            'cls': text_of(p.get('object_class')) or '',
            'cost': num_of(p.get('Cost')),
            'caliber': text_of(p.get('Caliber')),
            'mag': num_of(p.get('MagazineSize')),
            'damage': num_of(p.get('Damage')),
            'large': bool(num_of(p.get('LargeItem'))),
            'stack': num_of(p.get('MaxStacks')) or num_of(p.get('MaxStock')),
            'icon': text_of(p.get('Icon')),
            'slots': _component_slots(p.get('ComponentSlots')),
            'wear': text_of(p.get('Slot')),          # armour: Head / Legs / absent = Torso
            'hand': text_of(p.get('HandSlot')),      # weapons: TwoHanded / absent = one-handed
            'fits': _fits(text_of(p.get('object_class')) or '', text_of(p.get('Slot'))),
        })

    for p in read_defs(source, UNIT_DIR, 'UnitDataCompositeDef'):
        uid = text_of(p.get('Id'))
        if not uid:
            continue
        group = text_of(p.get('Group')) or ''
        mercs.append({
            'id': uid,
            'name': text_of(p.get('Name')) or uid,
            'nick': text_of(p.get('Nick')),
            'group': group,
            'spec': text_of(p.get('Specialization')),
            'salary': num_of(p.get('StartingSalary')),
            'hireable': group in ('MercenariesOld', 'MercenariesNew', 'IMP'),
            'imp': group == 'IMP',
            'stats': {s: num_of(p.get(s)) for s in STATS if num_of(p.get(s)) is not None},
            'starting_perks': _str_list(p.get('StartingPerks')),
            'portrait': text_of(p.get('Portrait')),
            'big_portrait': text_of(p.get('BigPortrait')),
        })

    for p in read_defs(source, EFFECT_DIR, 'CharacterEffectCompositeDef'):
        eid = text_of(p.get('Id'))
        if not eid:
            continue
        cls = text_of(p.get('object_class')) or ''
        group = text_of(p.get('Group')) or ''
        perks.append({
            'id': eid,
            'name': text_of(p.get('DisplayName')) or eid,
            'desc': _plain(text_of(p.get('Description')) or '', _params(p.get('Parameters'))),
            'group': group,
            'cls': cls,
            'stat': text_of(p.get('Stat')),
            'stat_req': num_of(p.get('StatValue')),
            'icon': text_of(p.get('Icon')),
            'tier': text_of(p.get('Tier')),
            'stacks': num_of(p.get('max_stacks')) or 1,
            'pool': POOLS.get(text_of(p.get('Tier')) or '', 'Other'),
            'risky': eid in RISKY_TALENTS,
            # What the in-game character sheet lets you have: perks and quirks,
            # not transient combat statuses, quest bookkeeping, the NPC-only
            # entries that carry no name of their own, or the developers'
            # scratch groups.
            'assignable': ((cls in ('Perk', 'Quirk') or group.startswith('Perk-')
                            or group in STATS)
                           and group not in ('Perk-NPC', '_Reserve', '_Trash')
                           and eid not in NEVER_ASSIGN
                           and bool(text_of(p.get('DisplayName')))),
        })

    components = read_components(source)

    items.sort(key=lambda d: (d['group'], d['name']))
    mercs.sort(key=lambda d: d['id'])
    perks.sort(key=lambda d: (d['group'], d['name']))
    components.sort(key=lambda d: (d['slot'] or '', d['name']))
    return {
        'items': items, 'mercs': mercs, 'perks': perks,
        'components': components, 'stats': list(STATS),
        'specializations': SPECIALIZATIONS,
        'spec_from_stat': SPEC_FROM_STAT,
        'spec_tech_bonus': SPEC_TECH_BONUS,
        'spec_threshold': SPEC_THRESHOLD,
        # order the perk picker presents the pools in
        'pool_order': ['Talent', 'Tactical', 'Personality', 'Quirk', 'Stat', 'Other'],
    }


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if src and os.path.isdir(src):
        raise SystemExit('directory sources not supported; pass the repo .zip')
    if src:
        zf = zipfile.ZipFile(src)
    else:
        print('downloading %s ...' % ZIP_URL)
        with urllib.request.urlopen(ZIP_URL) as r:
            blob = r.read()
        print('  %.1f MB' % (len(blob) / 1e6))
        zf = zipfile.ZipFile(io.BytesIO(blob))

    cat = build(zf)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(cat, f, ensure_ascii=False, separators=(',', ':'))
    print('wrote %s' % OUT)
    print('  %d items, %d mercs, %d perks, %d weapon components'
          % (len(cat['items']), len(cat['mercs']), len(cat['perks']),
             len(cat['components'])))
    modded = sum(1 for i in cat['items'] if i['slots'])
    print('  %d items accept attachments' % modded)
    groups = sorted({i['group'] for i in cat['items']})
    print('  item groups: %s' % ', '.join(groups))


if __name__ == '__main__':
    main()
