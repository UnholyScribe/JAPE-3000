"""Read the game's installed mods so the editor knows about their content.

Mods declare what they add in an `items.lua` manifest, as PlaceObj entries of
the same CompositeDef shapes the base game uses - the John Wick mod adds a merc
with `ModItemUnitDataCompositeDef` and a perk with
`ModItemCharacterEffectCompositeDef`, using the same Id / DisplayName / Tier
fields. So the same extraction that builds the vanilla catalog reads them too.

The game loads mods from the user data folder - `%APPDATA%/Jagged Alliance 3/
Mods` on Windows - and, on Steam, from each subscribed Workshop item's folder.
A downloaded archive, or a folder unpacked anywhere else, is *not* installed
and the game will never see it, so those are reported separately with an
option to install them.

A mod folder comes in two shapes, and the game takes them in this order
(CommonLua/Classes/Mod.lua, ModsReloadDefs):

  packed    ModContent.hpk, with metadata.lua and items.lua inside the pack.
            This is what the Mod Editor uploads, so every Workshop mod is one.
  loose     metadata.lua and items.lua as plain files - hand-made mods and
            anything unpacked from a Nexus download.

A folder holding both is read from the pack, as the game does.

`<game install>/Mods` is scanned too and reported as misplaced rather than
installed. It is the obvious guess and the Mod Manager silently ignores it,
which is a miserable thing to debug without being told.

Nothing here is baked into the exe: mods are scanned at run time.
"""
import json
import os
import re
import shutil

import build_catalog as B
import gamepaths
import lua_text

# CommonLua/Classes/Mod.lua: ModsPackFileName
PACK_NAME = 'ModContent.hpk'


def is_packed(folder):
    return os.path.isfile(os.path.join(folder, PACK_NAME))


def is_mod_folder(folder):
    return is_packed(folder) or os.path.isfile(
        os.path.join(folder, 'metadata.lua'))


def read_mod_bytes(folder, rel):
    """One file of a mod, by its path inside the mod, or None.

    Taken from ModContent.hpk when the folder has one - that is the copy the
    game mounts - and from the plain file otherwise.
    """
    rel = rel.replace('\\', '/').strip('/')
    if not rel or '..' in rel.split('/'):
        return None
    if is_packed(folder):
        import hpk
        try:
            with hpk.Pack(os.path.join(folder, PACK_NAME)) as pack:
                return pack.read(rel)
        except Exception:                                   # noqa: BLE001
            # a damaged or half-downloaded pack is the mod's problem, not a
            # reason to fail the whole scan
            return None
    try:
        with open(os.path.join(folder, rel.replace('/', os.sep)), 'rb') as f:
            return f.read()
    except OSError:
        return None


def read_mod_text(folder, rel):
    data = read_mod_bytes(folder, rel)
    return None if data is None else data.decode('utf-8', 'replace')


def iter_lua_files(folder):
    """(path inside the mod, source) for every .lua file a mod carries."""
    if is_packed(folder):
        import hpk
        try:
            with hpk.Pack(os.path.join(folder, PACK_NAME)) as pack:
                for full, fid in list(pack.walk()):
                    if not full.lower().endswith('.lua'):
                        continue
                    try:
                        data = pack.read_id(fid)
                    except Exception:                       # noqa: BLE001
                        continue
                    yield full, data.decode('utf-8', 'replace')
        except Exception:                                   # noqa: BLE001
            return
        return
    for root, _, files in os.walk(folder):
        for name in files:
            if not name.endswith('.lua'):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    src = f.read()
            except OSError:
                continue
            yield os.path.relpath(path, folder).replace(os.sep, '/'), src


def installed_root():
    """The one folder the game actually loads mods from."""
    return gamepaths.mods_dir()


def misplaced_root():
    """<game>/Mods - looks right, is ignored by the game."""
    return gamepaths.legacy_mods_dir()


# Steam's own app id for JA3. Proven by the game's Lua rather than guessed:
# ModTools/Src/Lua/UpdateMsg.lua links steamcommunity.com/app/1084160/ .
JA3_STEAM_APPID = gamepaths.STEAM_APPID

_WORKSHOP_TAILS = (
    r'SteamLibrary\steamapps\workshop\content\%s' % JA3_STEAM_APPID,
    r'Program Files (x86)\Steam\steamapps\workshop\content\%s' % JA3_STEAM_APPID,
    r'Program Files\Steam\steamapps\workshop\content\%s' % JA3_STEAM_APPID,
    r'steamapps\workshop\content\%s' % JA3_STEAM_APPID,
)


def workshop_roots():
    r"""Steam Workshop folders that hold JA3 subscribed mods.

    Each subscribed mod ends up at
      <library>\steamapps\workshop\content\1084160\<workshop_id>\
    holding a ModContent.hpk. Steam loads them from there directly, so they
    ARE installed - not misplaced. Reported as MISSING before this scan
    existed, which is what Jaeger's tester report was showing.

    Steam keeps Workshop content in the library the game is installed in,
    which can be any drive and any folder name, so the libraries come from
    Steam itself (gamepaths.steam_libraries) before the drive-root guesses.

    GOG users have no Steam layout and get an empty list back.
    """
    import string
    tail = os.path.join('steamapps', 'workshop', 'content', JA3_STEAM_APPID)
    libs = []
    game_lib = gamepaths.steam_library_of(gamepaths.game_dir())
    if game_lib:
        libs.append(game_lib)
    libs += gamepaths.steam_libraries()
    places = [os.path.join(lib, tail) for lib in libs]
    for drive in string.ascii_uppercase:
        root = '%s:\\' % drive
        if not os.path.isdir(root):
            continue
        places += [os.path.join(root, t) for t in _WORKSHOP_TAILS]
    out, seen = [], set()
    for p in places:
        key = os.path.normcase(os.path.normpath(p))
        if key not in seen and os.path.isdir(p):
            seen.add(key)
            out.append(p)
    return out


def elsewhere_roots():
    """Places people leave mods unpacked but not installed."""
    home = os.path.expanduser('~')
    out = [os.path.join(home, 'Downloads'), os.path.join(home, 'Desktop')]
    return [r for r in out if os.path.isdir(r)]


def find_mods(extra_dirs=(), max_depth=3):
    """Every unpacked mod, tagged with whether the game can actually see it."""
    roots = []
    root = installed_root()
    if root and os.path.isdir(root):
        roots.append((root, True, False, 'appdata'))
    # Steam Workshop content is loaded by the game the same way AppData mods
    # are, so it belongs on the installed list. Every Steam tester reported
    # MISSING for their subscribed mods until this scan was added.
    for ws in workshop_roots():
        roots.append((ws, True, False, 'steam'))
    stray = misplaced_root()
    if stray and os.path.isdir(stray):
        roots.append((stray, False, True, 'misplaced'))
    for r in list(elsewhere_roots()) + list(extra_dirs):
        roots.append((r, False, False, 'elsewhere'))

    found, seen = [], set()
    for root, is_installed, is_misplaced, source in roots:
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            if 'metadata.lua' not in filenames and PACK_NAME not in filenames:
                continue
            real = os.path.realpath(dirpath)
            if real in seen:
                continue
            seen.add(real)
            info = read_metadata(dirpath)
            if info:
                info['installed'] = is_installed
                info['misplaced'] = is_misplaced
                info['source'] = source
                if source == 'steam':
                    info['workshop_id'] = os.path.basename(dirpath)
                found.append(info)
            dirnames[:] = []          # a mod folder does not contain another

    # The same mod is often unpacked more than once, and the copies are not
    # always the same release. An installed copy always wins, since that is the
    # one the game loads; otherwise keep the newest by version, then by the
    # timestamp the author saved it.
    def rank(m):
        return (1 if m.get('installed') else 0,
                m.get('version') or -1, m.get('saved') or -1)

    by_id = {}
    for m in found:
        key = m['id'] or m['folder']
        best = by_id.get(key)
        if best is None:
            by_id[key] = m
            continue
        loser, winner = (best, m) if rank(m) > rank(best) else (m, best)
        winner.setdefault('other_copies', []).extend(
            [loser['folder']] + loser.pop('other_copies', []))
        by_id[key] = winner

    return sorted(by_id.values(), key=lambda m: (not m.get('installed'),
                                                 (m['title'] or '').lower()))


def install_mod(folder):
    """Move a mod the user downloaded into the folder their copy of the game
    loads mods from. Only ever on request, only the user's own mod - the
    editor ships no mod of its own. Returns (ok, message)."""
    dest_root = gamepaths.mods_dir(create=True)
    if not dest_root:
        return False, ('The game folder has not been found yet, so there is '
                       'nowhere to move it to. Point the editor at your '
                       'Jagged Alliance 3 folder first.')
    if not is_mod_folder(folder):
        return False, ('%s is not a mod folder (no metadata.lua or %s)'
                       % (folder, PACK_NAME))
    name = os.path.basename(os.path.normpath(folder))
    dest = os.path.join(dest_root, name)
    if os.path.realpath(dest) == os.path.realpath(folder):
        return True, dest
    if os.path.exists(dest):
        # never replace what is already installed - it may be a newer copy
        return False, ('%s already has a mod called "%s". Nothing was moved; '
                       'delete one of the two copies first.' % (dest_root, name))
    try:
        shutil.move(folder, dest)
    except OSError as exc:
        if os.path.isdir(dest) and not os.path.isdir(folder):
            return True, dest
        return False, 'could not move it: %s' % exc
    return True, dest


def _dependencies(node):
    """[{'id', 'title', 'required'}] from metadata.lua's ModDependency list.
    `required` defaults to true in the class (Mod.lua, ModDependency)."""
    out = []
    if node is None or node.kind != 'table':
        return out
    for item in node.items:
        dep_id = B.text_of(item.field('id')) if item.kind == 'call' else None
        if not dep_id:
            continue
        req = item.field('required')
        out.append({'id': dep_id,
                    'title': B.text_of(item.field('title')) or dep_id,
                    'required': not (req is not None and req.kind == 'bool'
                                     and not req.value)})
    return out


def read_metadata(folder):
    src = read_mod_text(folder, 'metadata.lua')
    if src is None:
        return None
    props = _obj_props(src, "PlaceObj('ModDef', {")
    if props is None:
        return None
    major = B.num_of(props.get('version_major'))
    minor = B.num_of(props.get('version_minor'))
    rev = B.num_of(props.get('saved_with_revision'))
    return {
        'folder': folder,
        'packed': is_packed(folder),
        'steam_id': B.text_of(props.get('steam_id')),
        'saved_with_revision': int(rev) if rev is not None else None,
        'dependencies': _dependencies(props.get('dependencies')),
        'id': B.text_of(props.get('id')),
        'title': B.text_of(props.get('title')) or os.path.basename(folder),
        'author': B.text_of(props.get('author')),
        'version': B.num_of(props.get('version')),
        'version_text': ('%d.%02d' % (major, minor))
                        if major is not None and minor is not None
                        else (str(B.num_of(props.get('version')))
                              if props.get('version') is not None else None),
        'saved': B.num_of(props.get('saved')),
    }


def _obj_props(src, needle):
    """Top-level properties of one PlaceObj(...) block, bounded so that the
    next object - or an embedded Lua function - cannot bleed into it."""
    i = src.find(needle)
    if i < 0:
        return None
    body = src[i + len(needle):]
    nxt = body.find("PlaceObj('ModItem")
    if nxt > 0:
        body = body[:nxt]
    return _mixed_props(body)


_FIRST_PROP = re.compile(
    r"^(\t+)(?:'[A-Za-z_][A-Za-z_0-9 ]*',|[A-Za-z_][A-Za-z_0-9]* = )", re.M)


def _mixed_props(body):
    """Mods use both the positional 'Key', value style and Key = value.

    Only properties at the object's own depth count, so nested tables cannot
    leak keys into it. That depth is taken from the first property rather
    than assumed to be one tab: items inside a ModItemFolder sit three or
    four levels down, and a fixed one-tab rule read every one of them as
    empty - Grim Perk Lab's 290 perks came back as none.
    """
    first = _FIRST_PROP.search(body)
    if not first:
        return {}
    ind = re.escape(first.group(1))
    tab_prop = re.compile(r"^%s'([A-Za-z_][A-Za-z_0-9 ]*)',[ \t]*" % ind, re.M)
    tab_eq = re.compile(r"^%s([A-Za-z_][A-Za-z_0-9]*) = " % ind, re.M)
    marks = [(m.start(), m.end(), m.group(1)) for m in tab_prop.finditer(body)]
    marks += [(m.start(), m.end(), m.group(1)) for m in tab_eq.finditer(body)]
    marks.sort()
    out = {}
    for k, (_start, end, key) in enumerate(marks):
        if key in out:
            continue
        stop = marks[k + 1][0] if k + 1 < len(marks) else len(body)
        try:
            out[key] = lua_text.Parser(body[end:stop]).parse_value()
        except (lua_text.LuaSyntaxError, IndexError):
            continue
    return out


def _each_moditem(src, kind):
    """Yield the property dict of every PlaceObj('ModItem<kind>', {...})."""
    needle = "PlaceObj('ModItem%s', {" % kind
    pos = 0
    while True:
        i = src.find(needle, pos)
        if i < 0:
            return
        pos = i + len(needle)
        nxt = src.find("PlaceObj('ModItem", pos)
        yield _mixed_props(src[pos:nxt if nxt > 0 else len(src)])


def read_mod_content(mod):
    """The mercs, perks and items one mod adds, in catalog shape."""
    out = {'mercs': [], 'perks': [], 'items': [], 'components': []}
    src = read_mod_text(mod['folder'], 'items.lua')
    if src is None:
        return out
    tag = mod['title']

    for p in _each_moditem(src, 'UnitDataCompositeDef'):
        uid = B.text_of(p.get('Id'))
        if not uid:
            continue
        group = B.text_of(p.get('Group')) or ''
        out['mercs'].append({
            'id': uid,
            'name': B.text_of(p.get('Name')) or uid,
            'nick': B.text_of(p.get('Nick')),
            'group': group,
            'spec': B.text_of(p.get('Specialization')),
            'salary': B.num_of(p.get('StartingSalary')),
            'hireable': group in ('MercenariesOld', 'MercenariesNew', 'IMP'),
            'imp': group == 'IMP',
            'stats': {s: B.num_of(p.get(s)) for s in B.STATS
                      if B.num_of(p.get(s)) is not None},
            'portrait': B.text_of(p.get('Portrait')),
            'big_portrait': B.text_of(p.get('BigPortrait')),
            'starting_perks': B._str_list(p.get('StartingPerks')),
            'max_hp': B.num_of(p.get('MaxHitPoints')),
            'mod': tag,
            'mod_id': mod.get('id'),
            'mod_folder': mod['folder'],
        })

    for p in _each_moditem(src, 'CharacterEffectCompositeDef'):
        eid = B.text_of(p.get('Id'))
        if not eid:
            continue
        cls = B.text_of(p.get('object_class')) or ''
        tier = B.text_of(p.get('Tier')) or ''
        out['perks'].append({
            'id': eid,
            'name': B.text_of(p.get('DisplayName')) or eid,
            'desc': B._plain(B.text_of(p.get('Description')) or '',
                             B._params(p.get('Parameters'))),
            'group': B.text_of(p.get('Group')) or ('Perk-' + tier if tier else ''),
            'cls': cls,
            'stat': B.text_of(p.get('Stat')),
            'stat_req': B.num_of(p.get('StatValue')),
            'icon': B.text_of(p.get('Icon')),
            'tier': tier or None,
            'stacks': B.num_of(p.get('max_stacks')) or 1,
            'pool': B.POOLS.get(tier, 'Other'),
            'risky': False,
            'assignable': cls in ('Perk', 'Quirk'),
            'mod': tag,
        })

    # weapon attachments a mod adds, so the mount pickers stay in sync
    for p in _each_moditem(src, 'WeaponComponent'):
        cid = B.text_of(p.get('id')) or B.text_of(p.get('Id'))
        if not cid:
            continue
        effects = p.get('ModificationEffects')
        out.setdefault('components', []).append({
            'id': cid,
            'name': B.text_of(p.get('DisplayName')) or cid,
            'slot': B.text_of(p.get('Slot')),
            'icon': B.text_of(p.get('Icon')),
            'cost': B.num_of(p.get('Cost')),
            'group': B.text_of(p.get('group')) or B.text_of(p.get('Group')) or '',
            'effects': [x.value for x in effects.items if x.kind == 'str']
                       if effects is not None and effects.kind == 'table' else [],
            'mod': tag,
        })

    for p in _each_moditem(src, 'InventoryItemCompositeDef'):
        iid = B.text_of(p.get('Id'))
        if not iid:
            continue
        cls = B.text_of(p.get('object_class')) or ''
        out['items'].append({
            'id': iid,
            'name': B.text_of(p.get('DisplayName')) or iid,
            'group': B.text_of(p.get('Group')) or 'Modded',
            'cls': cls,
            'cost': B.num_of(p.get('Cost')),
            'caliber': B.text_of(p.get('Caliber')),
            'mag': B.num_of(p.get('MagazineSize')),
            'damage': B.num_of(p.get('Damage')),
            'large': bool(B.num_of(p.get('LargeItem'))),
            'stack': B.num_of(p.get('MaxStacks')) or B.num_of(p.get('MaxStock')),
            'icon': B.text_of(p.get('Icon')),
            'slots': B._component_slots(p.get('ComponentSlots')),
            'wear': B.text_of(p.get('Slot')),
            'hand': B.text_of(p.get('HandSlot')),
            'fits': B._fits(cls, B.text_of(p.get('Slot'))),
            'mod': tag,
        })
    return out


def scan(extra_dirs=()):
    """{'mods': [...], 'mercs': [...], 'perks': [...], 'items': [...]}."""
    mods = find_mods(extra_dirs)
    merged = {'mods': [], 'mercs': [], 'perks': [], 'items': [], 'components': []}
    for m in mods:
        content = read_mod_content(m)
        counts = {k: len(v) for k, v in content.items()}
        # listed either way: a mod that only ships code still explains what a
        # save was played with
        merged['mods'].append(dict(m, adds=counts))
        for k in ('mercs', 'perks', 'items', 'components'):
            merged[k].extend(content.get(k) or [])
    return merged


def merge_into(catalog, extra_dirs=()):
    """Layer mod content over the bundled vanilla catalog. Mod entries win on
    an id clash, which is what the game does when a mod overrides a preset."""
    found = scan(extra_dirs)
    catalog['mods'] = found['mods']
    for key in ('mercs', 'perks', 'items', 'components'):
        by_id = {e['id']: e for e in catalog.get(key, [])}
        for entry in found[key]:
            by_id[entry['id']] = entry
        catalog[key] = sorted(by_id.values(), key=lambda e: e.get('name') or e['id'])
    return catalog


if __name__ == '__main__':
    result = scan()
    print(json.dumps({
        'mods': [{'title': m['title'], 'author': m['author'], 'adds': m['adds']}
                 for m in result['mods']],
        'totals': {k: len(v) for k, v in result.items() if k != 'mods'},
    }, indent=2))
