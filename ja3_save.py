"""Editing engine for Jagged Alliance 3 savegames.

Everything here works on the decompressed game_session text and applies
changes as minimal in-place patches, so any part of the save the user did
not touch comes back byte-for-byte identical.

Edits are collected as declarative intents (merc id + field, not text
offsets) and executed in phases at save time, re-parsing between phases so
structural changes never invalidate a pending edit's offsets.
"""
import json
import os
import random
import re
import shutil
import sys
import time

import bpul
import features
import lua_text
import zstd_chunks

STATS = ('Health', 'Agility', 'Dexterity', 'Strength', 'Wisdom', 'Leadership',
         'Marksmanship', 'Mechanical', 'Explosives', 'Medical')

# Experience thresholds per level, from the game's Lua (Mercenary.lua).
XP_TABLE = [0, 1000, 2500, 4500, 7000, 10000, 13500, 17500, 22000, 27000]

EQUIP_SLOTS = ('Handheld A', 'Handheld B', 'Head', 'Torso', 'Legs', 'Pick', 'SetpieceWeapon')
ALL_SLOTS = ('Inventory',) + EQUIP_SLOTS

# Mods a save may list that put nothing into the save itself, so taking them
# off its mod list is safe. id -> what to tell the player.
DROPPABLE_MODS = {
    'JA3SaveEditorFaces': (
        "this editor's old portraits mod. It is not needed any more - custom "
        'portraits now travel in the live link - so it can come off this save'),
}

# First key of an InventorySlot's packed grid positions; successive items in
# the same slot use base+1, base+2, ...
SLOT_KEY_BASE = 0x7ffff00000400001

_ROOT = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
CATALOG_PATH = os.path.join(_ROOT, 'catalog.json')


class Ja3Error(Exception):
    pass


def level_for_xp(xp):
    lvl = 1
    for i, need in enumerate(XP_TABLE):
        if xp >= need:
            lvl = i + 1
    return lvl


def xp_for_level(level):
    return XP_TABLE[max(1, min(level, len(XP_TABLE))) - 1]


_CATALOG_CACHE = None


def load_catalog(rescan=False):
    """The bundled base-game catalog with any installed mods layered on top.

    Cached: this is called from inner loops, and the mod scan touches the disk.
    Pass rescan=True after a mod is installed or removed.
    """
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None and not rescan:
        return _CATALOG_CACHE
    try:
        with open(CATALOG_PATH, encoding='utf-8') as f:
            cat = json.load(f)
    except (OSError, ValueError):
        cat = {'items': [], 'mercs': [], 'perks': [], 'stats': list(STATS)}
    try:
        import mods
        cat = mods.merge_into(cat)
    except Exception:                                            # noqa: BLE001
        cat.setdefault('mods', [])       # a broken mod must not break the editor
    _CATALOG_CACHE = cat
    return cat


# ---------------------------------------------------------------------------
# value rendering
# ---------------------------------------------------------------------------

def lua_num(v):
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def scalar(node):
    """Plain Python value of a leaf node, or None."""
    if node is None or node.kind in ('nil',):
        return None
    if node.kind in ('num', 'str', 'bool'):
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


def user_text(value, steam_id):
    """The T{...} block the game writes for player-entered names."""
    return ('T{\n\t_language = "English",\n\t_steam_id = "%s",\n'
            '\t_user_text_type = "name",\n\t%s,\n}' % (steam_id, lua_text.quote(value)))


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

# Keys the game writes into gv_Cheats but never reads back, mapped to the name
# its own code checks. Found by reading Lua.hpk: Cheat.lua sets
# `oneHpEnemies`, while IGMainActions, the CheatEnable handler and
# Unit:GameInit all use `OneHpEnemies`.
DEAD_CHEAT_KEYS = {'oneHpEnemies': 'OneHpEnemies'}


class Save:
    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.raw = f.read()
        self.container = bpul.parse(self.raw)
        self.warnings = self.container.warnings()
        gs = self.container.blob('game_session')
        self.compressed = zstd_chunks.is_wrapped(gs)
        self.text = zstd_chunks.decompress(gs).decode('utf-8')
        self.meta_text = self.container.blob('savegame_metadata').decode('utf-8')
        # keep the originals so an unedited save is never recompressed: the
        # game's own zstd output differs from ours byte-for-byte even when the
        # contents match, and there is no reason to churn a file we didn't edit
        self._orig_text = self.text
        self._orig_meta = self.meta_text
        self._reparse()
        self.intents = []

    # -- parsing ---------------------------------------------------------
    def _reparse(self):
        self.root = lua_text.parse(self.text)
        self.meta = lua_text.parse(self.meta_text)
        self._unit_cache = None

    @property
    def gvars(self):
        g = self.root.get('gvars')
        if g is None:
            raise Ja3Error('no gvars in this save')
        return g

    @property
    def game(self):
        g = self.root.get('game')
        if g is None:
            raise Ja3Error('no game object in this save')
        return g.table()

    def unit_table(self):
        ud = self.gvars.get('gv_UnitData')
        if ud is None:
            raise Ja3Error('no gv_UnitData in this save')
        return ud

    def unit(self, uid):
        node = self.unit_table().get(uid)
        return node.table() if node is not None else None

    def steam_id(self):
        return str(scalar(self.meta.get('savegame_id')) or
                   os.path.basename(os.path.dirname(os.path.abspath(self.path))))

    # -- reading ---------------------------------------------------------
    def money(self):
        return scalar(self.game.props().get('Money'))

    def screenshot(self):
        for n in self.container.names():
            if n.startswith('screenshot'):
                return self.container.blob(n)
        return None

    def meta_info(self):
        m = self.meta
        out = {}
        for k in ('displayname', 'campaign', 'map', 'playthrough_name', 'game_date',
                  'money', 'playtime', 'lua_revision', 'gameid'):
            out[k] = scalar(m.get(k))
        mods = m.get('active_mods')
        out['mods'] = [scalar(x.get('title')) or scalar(x.get('id'))
                       for x in mods.items] if mods is not None and mods.kind == 'table' else []
        gp = self.game.props()
        out['difficulty'] = scalar(gp.get('game_difficulty'))
        out['daily_income'] = scalar(gp.get('DailyIncome'))
        out['campaign_time'] = scalar(gp.get('CampaignTime'))
        return out

    def squads(self):
        out = []
        node = self.gvars.get('gv_Squads')
        if node is None:
            return out
        for it in node.items:
            tbl = it.table() if it.kind == 'call' else None
            if tbl is None:
                continue
            p = tbl.props()
            out.append({
                'uid': scalar(p.get('UniqueId')),
                'name': scalar(p.get('Name')),
                'side': scalar(p.get('Side')),
                'sector': scalar(p.get('CurrentSector')),
                'player': scalar(p.get('Side')) == 'player1',
                'units': [scalar(u) for u in p['units'].items] if 'units' in p else [],
            })
        return out

    def player_units(self):
        """Session ids of every merc in a player squad, plus any hired unit."""
        ids = []
        for sq in self.squads():
            if sq['player']:
                ids.extend(sq['units'])
        for key in self.unit_table().keys():
            u = self.unit(key)
            if u is None:
                continue
            if scalar(u.props().get('HireStatus')) in ('Hired', 'Ready') and key not in ids:
                ids.append(key)
        return ids

    def merc_info(self, uid):
        node = self.unit_table().get(uid)
        if node is None:
            return None
        tbl = node.table()
        p = tbl.props()
        preset = node.placed_name() or uid
        cat = _catalog_merc(preset)

        stats = {}
        for s in STATS:
            stored = scalar(p.get(s))
            base = (cat or {}).get('stats', {}).get(s)
            stats[s] = {
                'value': stored if stored is not None else base,
                'stored': stored is not None,
                'preset': base,
                'modifier': 0,
            }
        for mod in _modifiers(p.get('applied_modifiers')):
            if mod['prop'] in stats:
                stats[mod['prop']]['modifier'] += mod['amount']

        xp = scalar(p.get('Experience')) or 0
        return {
            'id': uid,
            'preset': preset,
            'name': scalar(p.get('Name')) or (cat or {}).get('name') or uid,
            'nick': scalar(p.get('Nick')) or (cat or {}).get('nick') or uid,
            'custom_name': p.get('Name') is not None,
            'is_imp': preset.startswith('IMP_'),
            'hire_status': scalar(p.get('HireStatus')),
            'hp': scalar(p.get('HitPoints')),
            'max_hp': scalar(p.get('MaxHitPoints')),
            'xp': xp,
            'level': level_for_xp(xp),
            'squad': scalar(p.get('Squad')),
            'spec': scalar(p.get('Specialization')) or (cat or {}).get('spec'),
            'tiredness': scalar(p.get('Tiredness')),
            'morale': scalar(p.get('Morale')),
            'operation': scalar(p.get('Operation')),
            'hired_until': scalar(p.get('HiredUntil')),
            'stats': stats,
            'perks': _perks(p.get('StatusEffects')),
            'wounds': _wound_stacks(p.get('StatusEffects')),
            'slots': {s: self._slot_items(tbl, s) for s in ALL_SLOTS},
        }

    def _slot_items(self, unit_tbl, slot):
        node = unit_tbl.props().get(slot)
        if node is None:
            return []
        tbl = node.table()
        if tbl is None:
            return []
        out = []
        items = tbl.items
        for i in range(0, len(items) - 1, 2):
            key, item = items[i], items[i + 1]
            if item.kind != 'call' or not item.fname.startswith('PlaceInventoryItem'):
                continue
            ip = item.table().props() if item.table() is not None else {}
            out.append({
                'index': i // 2,
                'item': item.placed_name(),
                'condition': scalar(ip.get('Condition')),
                'amount': scalar(ip.get('Amount')),
                'id': scalar(ip.get('id')),
                'drop_chance': scalar(ip.get('drop_chance')),
                'ammo': item.table() is not None and _ammo_of(item.table()),
                'components': _components(ip.get('components')),
                'slot_key': scalar(key),
            })
        return out

    def sector_stash(self, sector=None):
        """Items sitting in sector stashes, grouped by sector id."""
        node = self.gvars.get('gv_SectorInventory')
        out = {}
        if node is None or node.kind != 'table':
            return out
        for sec, val in node.fields.items():
            if sector and sec != sector:
                continue
            tbl = val[2].table() if val[2].kind in ('call', 'table') else None
            if tbl is None:
                continue
            names = []
            for n in lua_text.walk(tbl):
                if n.kind == 'call' and n.fname == 'PlaceInventoryItem':
                    names.append(n.placed_name())
            if names:
                out[sec] = names
        return out

    # -- sectors ---------------------------------------------------------
    def _sector_table(self, sid):
        node = self.gvars.get('gv_Sectors')
        if node is None:
            raise Ja3Error('no gv_Sectors in this save')
        entry = node.get(sid)
        if entry is None:
            raise Ja3Error('no sector %r in this save' % sid)
        tbl = entry.table() if entry.kind == 'call' else entry
        if tbl is None:
            raise Ja3Error('sector %r is not readable' % sid)
        return tbl

    def sectors(self):
        """Every sector, with the parts of its state that are worth showing.

        Most of the 259 entries are untouched map squares. The ones that carry
        real state are the ones you have been to, own, or have looted.
        """
        node = self.gvars.get('gv_Sectors')
        out = []
        if node is None:
            return out
        for sid, (_ks, _ke, entry) in sorted(node.fields.items()):
            tbl = entry.table() if entry.kind == 'call' else entry
            if tbl is None:
                continue
            p = tbl.props()
            stash, containers = [], 0
            inv = p.get('sector_inventory')
            if inv is not None and inv.kind == 'table':
                for slot in inv.items:
                    if slot.kind != 'table' or len(slot.items) < 3:
                        continue
                    containers += 1
                    bucket = slot.items[2]
                    if bucket.kind != 'table':
                        continue
                    for it in bucket.items:
                        if it.kind == 'call' and it.fname == 'PlaceInventoryItem':
                            ip = it.table().props() if it.table() is not None else {}
                            stash.append({'item': it.placed_name(),
                                          'amount': scalar(ip.get('Amount')),
                                          'condition': scalar(ip.get('Condition'))})
            out.append({
                'id': sid,
                'side': scalar(p.get('Side')),
                'owned': scalar(p.get('Side')) == 'player1',
                'intel': bool(scalar(p.get('intel_discovered'))),
                'visited': bool(scalar(p.get('player_visited'))),
                'militia_squad': scalar(p.get('militia_squad_id')),
                'training_stat': scalar(p.get('training_stat')),
                'guardpost': p.get('guardpost_obj') is not None,
                'containers': containers,
                'stash': stash,
            })
        return out

    def _do_sector_flags(self, patch, intent):
        """Intel and visited are plain reveal flags - nothing listens to them,
        so setting them has no knock-on effects."""
        tbl = self._sector_table(intent['sector'])
        changed = []
        for key, field in (('intel', 'intel_discovered'), ('visited', 'player_visited')):
            if key not in intent:
                continue
            value = bool(intent[key])
            _set_prop(patch, tbl, field, 'true' if value else 'false')
            changed.append('%s=%s' % (field, value))
        return 'sector %s: %s' % (intent['sector'], ', '.join(changed) or 'no change')

    def _do_sector_side(self, patch, intent):
        """Hand a sector to a side.

        The game normally routes this through a SectorSideChanged message that
        also settles city loyalty, guardpost conquest state, mine depletion
        timers and quest variables. Writing the field alone skips all of that,
        which is why this needs force=True.
        """
        if not intent.get('force'):
            raise Ja3Error(
                'Changing who owns a sector skips the loyalty, guardpost, mine '
                'and quest updates the game would normally do. Pass force=True '
                'if you want it anyway.')
        tbl = self._sector_table(intent['sector'])
        before = scalar(tbl.props().get('Side'))
        _set_prop(patch, tbl, 'Side', lua_text.quote(intent['side']))
        return 'sector %s: owner %s -> %s' % (intent['sector'], before, intent['side'])

    def _do_sector_stash_add(self, patch, intent):
        """Put an item in a sector's own stash - the 'virtual' container, which
        is the pile you see in the satellite view rather than a crate on the
        map."""
        tbl = self._sector_table(intent['sector'])
        cat = {i['id']: i for i in load_catalog()['items']}
        entry = cat.get(intent['item'])
        if entry is None:
            raise Ja3Error('unknown item %r' % intent['item'])
        count = int(intent.get('count', 1))
        stack_max = entry.get('stack') or 1

        chunks = []
        if stack_max > 1:
            remaining = count
            while remaining > 0:
                take = min(remaining, stack_max)
                chunks.append(take)
                remaining -= take
        else:
            chunks = [None] * count

        pieces = []
        for amount in chunks:
            item_id = self._next_id
            self._next_id += 2
            pieces.append(_render_item(intent['item'], item_id, '',
                                       amount=amount,
                                       condition=intent.get('condition')))

        inv = tbl.props().get('sector_inventory')
        if inv is None or inv.kind != 'table':
            _set_prop(patch, tbl, 'sector_inventory',
                      '{{"virtual",true,{%s}}}' % ','.join(pieces))
            return 'sector %s stash: +%d %s (new stash)' % (
                intent['sector'], count, entry['name'])

        # find the virtual container, or add one
        for slot in inv.items:
            if slot.kind != 'table' or len(slot.items) < 3:
                continue
            if slot.items[0].kind == 'str' and slot.items[0].value == 'virtual':
                bucket = slot.items[2]
                inner = self.text[bucket.start + 1:bucket.end - 1]
                lead = '' if not inner.strip() or inner.rstrip().endswith(',') else ','
                patch.insert(bucket.end - 1, lead + ','.join(pieces) + ',')
                return 'sector %s stash: +%d %s' % (
                    intent['sector'], count, entry['name'])

        patch.insert(inv.start + 1, '{"virtual",true,{%s}},' % ','.join(pieces))
        return 'sector %s stash: +%d %s (new virtual container)' % (
            intent['sector'], count, entry['name'])

    # -- mods this save expects ------------------------------------------
    def active_mod_ids(self):
        """Ids of the mods that were switched on when this save was made."""
        node = self.meta.get('active_mods')
        out = set()
        if node is None or node.kind != 'table':
            return out
        for entry in node.items:
            mid = _mod_entry_id(entry)
            if mid:
                out.add(mid)
        return out

    def required_mods(self):
        """What the save was played with, checked against what's installed.

        The game records {id, title, version, lua_revision, ...} per active mod
        in the metadata, and decides on load whether each is missing, too old,
        or disabled. Disabled cannot be checked from here - that list lives in
        an encrypted account.dat - but missing and out-of-date can.
        """
        node = self.meta.get('active_mods')
        wanted = []
        if node is not None and node.kind == 'table':
            for entry in node.items:
                tbl = entry.table() if entry.kind in ('call', 'table') else None
                if tbl is None:
                    # older saves list bare ids
                    if entry.kind == 'str':
                        wanted.append({'id': entry.value, 'title': entry.value,
                                       'version': None, 'lua_revision': None})
                    continue
                p = tbl.props()
                wanted.append({
                    'id': scalar(p.get('id')),
                    'title': scalar(p.get('title')),
                    'version': scalar(p.get('version')),
                    'lua_revision': scalar(p.get('lua_revision')),
                })
        if not wanted:
            return []

        try:
            import mods as modscan
            local = {m['id']: m for m in modscan.find_mods() if m.get('id')}
        except Exception:                                        # noqa: BLE001
            local = {}

        out = []
        for w in wanted:
            got = local.get(w['id'])
            if w['id'] in DROPPABLE_MODS:
                state, note = 'retired', DROPPABLE_MODS[w['id']]
            elif got is None:
                state, note = 'missing', 'not on this machine at all'
            elif not got.get('installed'):
                state, note = 'not_installed', (
                    'found at %s but not in the game\'s Mods folder, so the game '
                    'will not load it' % got['folder'])
            elif (w.get('version') is not None and got.get('version') is not None
                  and got['version'] < w['version']):
                state, note = 'older', (
                    'installed version %s is older than the %s this save used'
                    % (got.get('version_text') or got['version'], w['version']))
            else:
                state, note = 'ok', 'installed'
            out.append(dict(w, state=state, note=note,
                            folder=(got or {}).get('folder')))
        return out

    # -- export / import -------------------------------------------------
    def export_merc(self, uid, with_gear=True):
        """A merc as portable JSON.

        What travels is the character: identity, the ten attributes, perks,
        level and gear. What cannot travel is anything the save owns rather
        than the merc - session_id, squad, hire expiry, item ids, wounds - and
        anything the *preset* owns rather than the save: appearance, portrait
        and gender all come from the slot you import into, not from this file.
        """
        info = self.merc_info(uid)
        if info is None:
            raise Ja3Error('no merc %r' % uid)

        out = {
            'ja3_merc': 1,
            'exported': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'source': {
                'save': scalar(self.meta.get('displayname')),
                'merc': uid,
                'preset': info['preset'],
            },
            'name': info['name'],
            'nick': info['nick'],
            'specialization': info['spec'],
            'level': info['level'],
            'xp': info['xp'],
            'hp': info['hp'],
            'max_hp': info['max_hp'],
            'stats': {k: v['value'] for k, v in info['stats'].items()
                      if v['value'] is not None},
            # only what the character sheet owns: wounds and transient statuses
            # belong to the save this merc is leaving, not to the merc
            'perks': [p for p in info['perks'] if p in {
                x['id'] for x in load_catalog()['perks'] if x.get('assignable')}],
        }
        if with_gear:
            gear = {}
            for slot, items in info['slots'].items():
                rows = []
                for it in items:
                    row = {'item': it['item']}
                    for key in ('condition', 'amount'):
                        if it.get(key) is not None:
                            row[key] = it[key]
                    comps = {k: v for k, v in (it.get('components') or {}).items() if v}
                    if comps:
                        row['components'] = comps
                    if it.get('ammo'):
                        row['ammo'] = it['ammo']
                    rows.append(row)
                if rows:
                    gear[slot] = rows
            out['gear'] = gear

        # so an import can say what it is missing before it writes anything
        cat = load_catalog()
        by_perk = {p['id']: p for p in cat['perks']}
        by_item = {i['id']: i for i in cat['items']}
        needs = set()
        for pid in out['perks']:
            mod = (by_perk.get(pid) or {}).get('mod')
            if mod:
                needs.add(mod)
        for rows in (out.get('gear') or {}).values():
            for row in rows:
                mod = (by_item.get(row['item']) or {}).get('mod')
                if mod:
                    needs.add(mod)
        out['requires_mods'] = sorted(needs)
        return out

    def check_import(self, data, target):
        """What would go wrong, before anything is written."""
        problems, warnings = [], []
        if data.get('ja3_merc') != 1:
            problems.append('not a merc export file')
            return problems, warnings
        if self.unit(target) is None:
            problems.append('this save has no merc slot called %r' % target)

        cat = load_catalog()
        known_perks = {p['id'] for p in cat['perks']}
        known_items = {i['id'] for i in cat['items']}
        missing_perks = [p for p in data.get('perks', []) if p not in known_perks]
        missing_items = sorted({row['item']
                                for rows in (data.get('gear') or {}).values()
                                for row in rows if row['item'] not in known_items})
        if missing_perks:
            problems.append('perks this game does not have: %s' % ', '.join(missing_perks))
        if missing_items:
            problems.append('items this game does not have: %s' % ', '.join(missing_items))

        src = (data.get('source') or {}).get('preset')
        if src and src != target:
            warnings.append(
                'Imported onto %s, so it keeps that slot\'s face, body and voice - '
                'appearance is not stored in the save. Only the character comes across.'
                % target)
        if data.get('requires_mods'):
            warnings.append('Originally used: %s' % ', '.join(data['requires_mods']))
        return problems, warnings

    def _do_import_merc(self, patch, intent):
        data, target = intent['data'], intent['merc']
        problems, _warn = self.check_import(data, target)
        if problems and not intent.get('force'):
            raise Ja3Error('; '.join(problems))

        tbl = self._require_unit(target)
        campaign_time = scalar(self.game.props().get('CampaignTime')) or 0

        for stat, val in (data.get('stats') or {}).items():
            if stat in STATS:
                _set_prop(patch, tbl, stat, lua_num(int(val)))
        if data.get('name'):
            _set_prop(patch, tbl, 'Name', user_text(data['name'], self.steam_id()))
        if data.get('nick'):
            _set_prop(patch, tbl, 'Nick', user_text(data['nick'], self.steam_id()))
        spec = data.get('specialization')
        if spec:
            valid = load_catalog().get('specializations') or []
            if not valid or spec in valid:
                _set_prop(patch, tbl, 'Specialization', lua_text.quote(spec))

        hp = int(data.get('max_hp') or data.get('hp') or 50)
        _set_prop(patch, tbl, 'HireStatus', '"Hired"')
        _set_prop(patch, tbl, 'HiredUntil', lua_num(campaign_time + 30 * 24 * 3600))
        _set_prop(patch, tbl, 'Experience', lua_num(int(data.get('xp') or 0)))
        _set_prop(patch, tbl, 'MaxHitPoints', lua_num(hp))
        _set_prop(patch, tbl, 'HitPoints', lua_num(int(data.get('hp') or hp)))
        _set_prop(patch, tbl, 'ActionPoints', '11000')
        _set_prop(patch, tbl, 'session_id', lua_text.quote(target))

        # the merc's own stat modifiers belong to the save it came from
        mods_node = tbl.props().get('applied_modifiers')
        if mods_node is not None and mods_node.kind == 'table':
            keep = [m for m in mods_node.items
                    if not (m.kind == 'table' and scalar(m.get('prop')) in STATS)]
            patch.replace(mods_node, _render_table_items(keep, self.text))

        _set_prop(patch, tbl, 'StatusEffects', _render_status_effects(
            [(p, None) for p in data.get('perks', [])], campaign_time))

        if 'gear' in data:
            gear = data['gear'] or {}
            for slot in ALL_SLOTS:
                rows = gear.get(slot) or []
                pieces, key = [], SLOT_KEY_BASE - 1
                for row in rows:
                    key += 1
                    item_id = self._next_id
                    self._next_id += 2
                    pieces.append('%d, %s, ' % (key, _render_item(
                        row['item'], item_id, target,
                        condition=row.get('condition'),
                        amount=row.get('amount'),
                        ammo=row.get('ammo'),
                        components=row.get('components'))))
                src = "PlaceObj('InventorySlot', {%s})" % ''.join(pieces)
                node = tbl.props().get(slot)
                if node is not None:
                    patch.replace(node, src)
                else:
                    _set_prop(patch, tbl, slot, src)

        return 'imported %s onto %s' % (data.get('nick') or data.get('name') or '?', target)

    # -- squad bag -------------------------------------------------------
    def _player_squads(self):
        """Every squad the player owns, with its supply bag node.

        The bag is NOT `gv_SquadBag`. That global exists, parses cleanly, and
        is permanently empty: reading it reported HAVE 0 forever, and writing
        to it put supplies into a container the game never reads. Proven
        against seven of the tester's saves - every one had an empty gv_SquadBag
        and the real contents on the squad.

        The real store is a `squad_bag` list on each SatelliteSquad in
        gv_Squads, and gv_Squads holds enemy squads too - 57 of the 58 entries
        in one save - so it MUST be filtered by Side.
        """
        node = self.gvars.get('gv_Squads')
        tbl = node.table() if node is not None else None
        if tbl is None:
            return []
        out = []
        for entry in tbl.items:
            t = entry.table()
            if t is None:
                continue
            props = t.props()
            if scalar(props.get('Side')) != 'player1':
                continue
            out.append({
                'uid': scalar(props.get('UniqueId')),
                'name': scalar(props.get('Name')),
                'sector': scalar(props.get('CurrentSector')),
                'bag': props.get('squad_bag'),
            })
        return out

    def _bag_slot(self, squad=None):
        """One player squad's `squad_bag` table, defaulting to the first.

        Unlike an InventorySlot this is a PLAIN LIST - no alternating slot
        keys - so callers walk .items one at a time, never in pairs.
        """
        squads = self._player_squads()
        if squad is not None:
            squads = [q for q in squads if str(q['uid']) == str(squad)]
        if not squads:
            return None
        bag = squads[0]['bag']
        return bag.table() if bag is not None else None

    def squad_bag(self, squad=None):
        slot = self._bag_slot(squad)
        if slot is None:
            return []
        out = []
        for i, item in enumerate(slot.items):
            if item.kind != 'call' or item.fname != 'PlaceInventoryItem':
                continue
            p = item.table().props() if item.table() is not None else {}
            out.append({
                'index': i,
                'item': item.placed_name(),
                'amount': scalar(p.get('Amount')),
                'condition': scalar(p.get('Condition')),
                'id': scalar(p.get('id')),
            })
        return out

    def _do_bag_add(self, patch, intent):
        """Drop supplies straight into the shared squad bag.

        Stackable resources (Parts, Meds) are one entry carrying an Amount;
        the single-stack materials (Chip, Lens, Steel Pipe) need one entry
        each, which is why a count of 10 writes ten items.
        """
        slot = self._bag_slot(intent.get('squad'))
        if slot is None:
            raise Ja3Error('this save has no player squad carrying a supply '
                           'bag, so there is nowhere to put these')

        cat = {i['id']: i for i in load_catalog()['items']}
        entry = cat.get(intent['item'])
        if entry is None:
            raise Ja3Error('unknown item %r' % intent['item'])
        count = int(intent.get('count', 1))
        stack_max = entry.get('stack') or 1

        remaining = count

        # Fill what is already in the bag first. The game does this itself on
        # a LIVE add - AddItemsToSquadBag ends with SortItemsInBag, which
        # merges - but nothing merges a savegame on load, so an append leaves
        # 54 and 50 of the same round sitting side by side forever.
        if stack_max > 1:
            # Running totals, so two adds of the same item in one batch do not
            # both patch the same number from the same starting value.
            seen = self._slot_keys.setdefault('__bag_amounts__', {})
            for node in slot.items:
                if remaining <= 0:
                    break
                if node.kind != 'call' or node.fname != 'PlaceInventoryItem':
                    continue
                if node.placed_name() != intent['item']:
                    continue
                tbl = node.table()
                amt = tbl.props().get('Amount') if tbl is not None else None
                if amt is None or getattr(amt, 'start', None) is None:
                    continue
                cur = seen.get(amt.start)
                if cur is None:
                    cur = scalar(amt) or 0
                room = stack_max - cur
                if room <= 0:
                    continue
                take = min(room, remaining)
                seen[amt.start] = cur + take
                # set_span, not replace_span: a second add of the same item
                # revises the number this batch already wrote rather than
                # queueing a second edit over it, which apply() refuses.
                patch.set_span(amt.start, amt.end, str(cur + take))
                remaining -= take

        chunks = []
        if stack_max > 1:
            while remaining > 0:
                take = min(remaining, stack_max)
                chunks.append(take)
                remaining -= take
        else:
            chunks = [None] * count       # one entry each, no Amount field

        # A plain list, so no slot key is written - that numbering belongs to
        # InventorySlot and putting it here would produce `1234, PlaceInv...`
        # inside a table the game reads positionally.
        pieces = []
        for amount in chunks:
            item_id = self._next_id
            self._next_id += 2
            src = _render_item(intent['item'], item_id, '',
                               amount=amount, condition=None)
            pieces.append('%s, ' % src)

        # Two bag_adds in one batch both measure the ORIGINAL text, so both
        # would decide they need a leading comma and produce `}), ,PlaceInv`.
        # Only the first insert into a given bag adds one; the rest follow a
        # piece that already ends in ", ". This could not fire while the write
        # went to the always-empty gv_SquadBag, because an empty table never
        # needs a leading comma - the dead store was hiding it.
        if pieces:
            cursor = ('__squad_bag__', str(intent.get('squad')))
            first = cursor not in self._slot_keys
            self._slot_keys[cursor] = True

            inner = self.text[slot.start + 1:slot.end - 1]
            lead = ',' if (first and inner.strip()
                           and not inner.rstrip().endswith(',')) else ''
            patch.insert(slot.end - 1, lead + ''.join(pieces))
        return 'squad bag: +%d %s' % (count, entry['name'])

    # -- cheats ----------------------------------------------------------
    def cheats(self):
        """The save's own gv_Cheats table.

        These are the flags the in-game cheat menu sets. CheatEnabled() reads
        them straight out of this table with no check that cheats are enabled,
        so setting them here works on an ordinary install; and they are only
        reset by NewGameSession(), i.e. starting a new campaign, never by
        loading a save.
        """
        node = self.gvars.get('gv_Cheats')
        out = {'flags': {}, 'sides': {}, 'sides_seen': []}
        # Cheat.lua initialises `gv_Cheats.oneHpEnemies` but every consumer
        # reads `OneHpEnemies` - Unit:GameInit() applies it to enemy1/enemy2 as
        # each unit spawns, the same way GodMode is applied. So the lowercase
        # key that ships in every save is dead, and writing it does nothing.
        # Present and write the name the game actually reads.
        if node is None or node.kind != 'table':
            return out
        for key, (_ks, _ke, val) in sorted(node.fields.items()):
            if val.kind == 'bool':
                out['flags'][key] = val.value
            elif val.kind == 'table':
                per = {side: bool(v.kind == 'bool' and v.value)
                       for side, (_a, _b, v) in sorted(val.fields.items())}
                if per:
                    out['sides'][key] = per
                    for s in per:
                        if s not in out['sides_seen']:
                            out['sides_seen'].append(s)
        for dead, real in DEAD_CHEAT_KEYS.items():
            if dead in out['flags']:
                stale = out['flags'].pop(dead)
                out['flags'].setdefault(real, stale)
        out['sides_seen'].sort()
        return out

    def _do_cheats(self, patch, intent):
        node = self.gvars.get('gv_Cheats')
        if node is None or node.kind != 'table':
            raise Ja3Error('this save has no gv_Cheats table')
        current = self.cheats()
        flags = dict(current['flags'])
        sides = {k: dict(v) for k, v in current['sides'].items()}

        changed = []
        for key, value in (intent.get('flags') or {}).items():
            if key not in flags:
                raise Ja3Error('unknown cheat %r' % key)
            if flags[key] != bool(value):
                changed.append('%s=%s' % (key, bool(value)))
            flags[key] = bool(value)
        for key, per in (intent.get('sides') or {}).items():
            if key not in sides:
                raise Ja3Error('unknown per-side cheat %r' % key)
            for side, value in per.items():
                if side not in sides[key]:
                    raise Ja3Error('unknown side %r for %s' % (side, key))
                if sides[key][side] != bool(value):
                    changed.append('%s.%s=%s' % (key, side, bool(value)))
                sides[key][side] = bool(value)

        # anything that is neither a bool nor a side map - the developer-only
        # Teleport flag, say - is carried through exactly as it was
        keep = ['%s = %s' % (key, self.text[val.start:val.end])
                for key, (_ks, _ke, val) in node.fields.items()
                if key not in flags and key not in sides
                and key not in DEAD_CHEAT_KEYS]

        parts = ['%s = %s' % (k, 'true' if v else 'false')
                 for k, v in flags.items()]
        parts += ['%s = {%s}' % (key, ','.join(
            '%s = %s' % (s, 'true' if v else 'false')
            for s, v in sorted(sides[key].items()))) for key in sides]
        patch.replace(node, '{%s}' % ','.join(sorted(parts + keep)))
        return 'cheats: %s' % (', '.join(changed) if changed else 'no change')

    def next_item_id(self):
        biggest = 0
        for m in re.finditer(r"'id', (\d+),", self.text):
            biggest = max(biggest, int(m.group(1)))
        return biggest + 1

    def roster(self):
        """Every hireable merc the save already carries an entry for, with
        whether they're currently on the team. Saves hold a gv_UnitData entry
        for all 42 AIM mercs and all 6 IMP presets from campaign start, so
        recruiting is a matter of flipping HireStatus rather than inventing
        a unit from nothing."""
        keys = set(self.unit_table().keys())
        out = []
        for m in load_catalog()['mercs']:
            if not m.get('hireable') or m['id'] not in keys:
                continue
            p = self.unit(m['id']).props()
            status = scalar(p.get('HireStatus'))
            # a merc who has been edited stores their own numbers; fall back to
            # the preset for everyone still at factory settings
            stats = dict(m.get('stats', {}))
            for s in STATS:
                if scalar(p.get(s)) is not None:
                    stats[s] = scalar(p.get(s))
            out.append({
                'id': m['id'],
                'name': scalar(p.get('Name')) or m['name'],
                'nick': scalar(p.get('Nick')) or m['nick'] or m['id'],
                'imp': m.get('imp', False),
                'spec': scalar(p.get('Specialization')) or m.get('spec'),
                'salary': m.get('salary'),
                'stats': stats,
                'hired': status == 'Hired',
                'status': status,
                'squad': scalar(p.get('Squad')),
                'mod': m.get('mod'),
            })
        # Hireable mercs from installed mods that this save has never heard of.
        # The game makes every A.I.M. merc's record when a campaign starts, so
        # a merc from a mod switched on later has none - John Wick in every one
        # of the tester's saves. Recruiting one writes the record (_do_new_merc).
        active = self.active_mod_ids()
        for m in load_catalog()['mercs']:
            if (not m.get('hireable') or not m.get('mod') or m.get('imp')
                    or m['id'] in keys):
                continue
            out.append({
                'id': m['id'],
                'name': m['name'],
                'nick': m.get('nick') or m['name'] or m['id'],
                'imp': False,
                'spec': m.get('spec'),
                'salary': m.get('salary'),
                'stats': dict(m.get('stats', {})),
                'hired': False,
                'status': None,
                'squad': None,
                'mod': m.get('mod'),
                'new_record': True,
                # was the mod switched on when this save was made? Without it
                # the game cannot build the merc when the save loads.
                'mod_on': m.get('mod_id') in active,
            })
        out.sort(key=lambda d: (not d['hired'], d['imp'], d['nick'].lower()))
        return out

    def free_imp_slots(self):
        """IMP preset ids not currently in the player's employ."""
        return [r['id'] for r in self.roster() if r['imp'] and not r['hired']]

    # What this reader can actually change. The front end asks the SAVE what it
    # supports rather than asking which game it is, so a section with no
    # backing stands down instead of offering a dead control. Declaring every
    # feature - not just the unsupported ones - is deliberate; see features.py.
    SUPPORTS = features.all_true()

    GAME = 'ja3'

    def summary(self):
        squads = self.squads()
        return {
            'supports': self.SUPPORTS,
            'path': self.path,
            'file': os.path.basename(self.path),
            'meta': self.meta_info(),
            'money': self.money(),
            'warnings': self.warnings,
            'squads': squads,
            'mercs': [self.merc_info(u) for u in self.player_units()],
            'stash': self.sector_stash(),
            'free_imp_slots': self.free_imp_slots(),
            'roster': self.roster(),
            'cheats': self.cheats(),
            'squad_bag': self.squad_bag(),
            'required_mods': self.required_mods(),
            'sectors': self.sectors(),
        }

    # -- intents ---------------------------------------------------------
    def queue(self, kind, **kw):
        kw['kind'] = kind
        self.intents.append(kw)
        return kw

    def has_changes(self):
        return bool(self.intents)

    # -- applying --------------------------------------------------------
    PHASES = ('money', 'cheats', 'sector_flags', 'sector_side', 'import_merc',
              'merc_field', 'stat', 'perks', 'item_edit', 'item_components',
              'item_remove', 'item_add', 'bag_add', 'sector_stash_add',
              'squad', 'new_merc', 'drop_mod')

    def apply(self):
        """Run every queued intent. Each phase patches the text then re-parses,
        so later phases always see fresh offsets."""
        applied = []
        self._slot_keys = {}
        self._next_id = self.next_item_id()
        for phase in self.PHASES:
            todo = [i for i in self.intents if i['kind'] == phase]
            if not todo:
                continue
            patch = lua_text.Patcher(self.text)
            for intent in todo:
                desc = getattr(self, '_do_' + phase)(patch, intent)
                if desc:
                    applied.append(desc)
            self.text = patch.apply()
            self._reparse()
        self.intents = []
        return applied

    # -- phase implementations -------------------------------------------
    def _do_money(self, patch, intent):
        node = self.game.props().get('Money')
        if node is None:
            raise Ja3Error('no game.Money in this save')
        old = node.value
        patch.replace(node, lua_num(int(intent['value'])))
        # the load-menu preview mirrors it; keep them consistent
        mnode = self.meta.get('money')
        if mnode is not None:
            mp = lua_text.Patcher(self.meta_text)
            mp.replace(mnode, lua_num(int(intent['value'])))
            self.meta_text = mp.apply()
        return 'money %s -> %s' % (old, intent['value'])

    def _do_drop_mod(self, patch, intent):
        """Take one mod off the list the save says it was played with. Only
        offered for mods that put nothing into the save itself - the editor's
        old portraits mod - so the game stops asking for it on load."""
        if intent.get('id') not in DROPPABLE_MODS:
            raise Ja3Error('%s is not a mod the editor can take off a save'
                           % intent.get('id'))
        node = self.meta.get('active_mods')
        if node is None or node.kind != 'table':
            return None
        for entry in node.items:
            if _mod_entry_id(entry) != intent['id']:
                continue
            start, end = entry.start, entry.end
            rest = self.meta_text[end:]
            gap = len(rest) - len(rest.lstrip())
            if rest[gap:gap + 1] in (',', ';'):
                end += gap + 1
            line = self.meta_text.rfind('\n', 0, start)
            if line >= 0 and not self.meta_text[line + 1:start].strip():
                start = line
            mp = lua_text.Patcher(self.meta_text)
            mp.replace_span(start, end, '')
            self.meta_text = mp.apply()
            self.meta = lua_text.parse(self.meta_text)
            return 'mod list: removed %s' % intent['id']
        return None

    def _do_merc_field(self, patch, intent):
        tbl = self._require_unit(intent['merc'])
        key, val = intent['field'], intent['value']
        if key in ('Name', 'Nick'):
            src = user_text(val, self.steam_id())
        elif isinstance(val, str):
            src = lua_text.quote(val)
        else:
            src = lua_num(val)
        old = scalar(tbl.props().get(key))
        _set_prop(patch, tbl, key, src)
        return '%s.%s %s -> %s' % (intent['merc'], key, old, val)

    def _do_stat(self, patch, intent):
        tbl = self._require_unit(intent['merc'])
        stat, value = intent['stat'], int(intent['value'])
        if stat not in STATS:
            raise Ja3Error('unknown stat %r' % stat)
        old = scalar(tbl.props().get(stat))
        _set_prop(patch, tbl, stat, lua_num(value))
        # Drop this stat's StatGain modifiers so the stored number is what the
        # game shows, rather than the number plus an old training bonus.
        mods = tbl.props().get('applied_modifiers')
        if mods is not None and mods.kind == 'table':
            keep = [m for m in mods.items
                    if not (m.kind == 'table' and scalar(m.get('prop')) == stat)]
            if len(keep) != len(mods.items):
                patch.replace(mods, _render_table_items(keep, self.text))
        return '%s.%s %s -> %s' % (intent['merc'], stat, old, value)

    def _do_perks(self, patch, intent):
        tbl = self._require_unit(intent['merc'])
        wanted = list(dict.fromkeys(intent['perks']))
        node = tbl.props().get('StatusEffects')
        campaign_time = scalar(self.game.props().get('CampaignTime')) or 0

        # Effects already on the merc keep their own source, so fields like a
        # wound's `stacks` survive. Effects the character sheet doesn't own -
        # wounds, quest flags, combat statuses - are carried over even when the
        # caller didn't list them.
        assignable = {p['id'] for p in load_catalog()['perks'] if p.get('assignable')}
        existing, carried = {}, []
        if node is not None and node.kind == 'table':
            for name, span in _perk_entries(node):
                existing[name] = self.text[span[0]:span[1]]
                if name not in assignable and name not in wanted:
                    carried.append((name, existing[name]))

        entries = [(n, existing.get(n)) for n in wanted] + carried
        src = _render_status_effects(entries, campaign_time)
        if node is not None:
            patch.replace(node, src)
        else:
            _set_prop(patch, tbl, 'StatusEffects', src)
        return '%s perks -> %s' % (intent['merc'], ', '.join(wanted) or '(none)')

    def _do_item_edit(self, patch, intent):
        item = self._require_item(intent['merc'], intent['slot'], intent['index'])
        tbl = item.table()
        for key, val in intent['fields'].items():
            _set_prop(patch, tbl, key, lua_num(val) if not isinstance(val, str)
                      else lua_text.quote(val))
        return '%s %s[%d] %s' % (intent['merc'], intent['slot'], intent['index'],
                                 intent['fields'])

    def _do_item_components(self, patch, intent):
        """Fit or strip weapon attachments. The game stores these as a
        slot -> component-id map with an empty string for an empty mount, and
        it keeps a key for every mount the weapon has."""
        item = self._require_item(intent['merc'], intent['slot'], intent['index'])
        tbl = item.table()
        comps = intent['components']
        src = '{%s}' % ','.join('%s = %s' % (k, lua_text.quote(v or ''))
                                for k, v in sorted(comps.items()))
        node = tbl.props().get('components')
        if node is not None:
            patch.replace(node, src)
        else:
            _set_prop(patch, tbl, 'components', src)
        fitted = ', '.join('%s=%s' % (k, v) for k, v in sorted(comps.items()) if v)
        return '%s %s[%d] %s attachments -> %s' % (
            intent['merc'], intent['slot'], intent['index'],
            item.placed_name(), fitted or '(none)')

    def _do_item_remove(self, patch, intent):
        unit = self._require_unit(intent['merc'])
        slot_tbl = unit.props().get(intent['slot']).table()
        items = slot_tbl.items
        i = intent['index'] * 2
        if i + 1 >= len(items):
            raise Ja3Error('no item at index %d' % intent['index'])
        start = items[i].start
        end = items[i + 1].end
        # swallow a trailing comma so the table stays well-formed
        m = re.match(r'\s*,', self.text[end:])
        if m:
            end += m.end()
        patch.replace_span(start, end, '')
        return '%s %s: removed %s' % (intent['merc'], intent['slot'],
                                      items[i + 1].placed_name())

    def _do_item_add(self, patch, intent):
        unit = self._require_unit(intent['merc'])
        slot_node = unit.props().get(intent['slot'])
        if slot_node is None:
            raise Ja3Error('%s has no %s slot' % (intent['merc'], intent['slot']))

        # Refuse a placement the game itself would not make - a rifle in a
        # helmet slot, say - unless the caller insists with force=True.
        if not intent.get('force'):
            entry = next((i for i in load_catalog()['items']
                          if i['id'] == intent['item']), None)
            fits = (entry or {}).get('fits')
            if fits and intent['slot'] not in fits:
                raise Ja3Error(
                    '%s does not go in %s (the game puts it in: %s). '
                    'Pass force=True to do it anyway.'
                    % (entry.get('name', intent['item']), intent['slot'],
                       ', '.join(fits)))

        slot_tbl = slot_node.table()

        # Grid keys must stay unique within a slot, and several adds can land
        # in the same slot in one pass, so the running maximum is tracked here
        # rather than re-derived from the (not yet patched) text each time.
        cursor = (intent['merc'], intent['slot'])
        used = self._slot_keys.get(cursor)
        if used is None:
            used = max([scalar(slot_tbl.items[i]) or 0
                        for i in range(0, len(slot_tbl.items) - 1, 2)] or
                       [SLOT_KEY_BASE - 1])
        key = used + 1
        self._slot_keys[cursor] = key

        item_id = self._next_id
        self._next_id += 2          # leave room for a nested ammo item
        src = _render_item(intent['item'], item_id, intent['merc'],
                           condition=intent.get('condition'),
                           amount=intent.get('amount'),
                           ammo=intent.get('ammo'))

        # append inside the slot's braces, keeping the table well-formed
        inner = self.text[slot_tbl.start + 1:slot_tbl.end - 1]
        lead = '' if not inner.strip() or inner.rstrip().endswith(',') else ','
        patch.insert(slot_tbl.end - 1, '%s%d, %s, ' % (lead, key, src))
        return '%s %s: added %s' % (intent['merc'], intent['slot'], intent['item'])

    def _do_squad(self, patch, intent):
        uid, squad_uid = intent['merc'], intent['squad']
        squad_node = self._squad_node(squad_uid)
        if squad_node is None:
            raise Ja3Error('no squad %r' % squad_uid)
        units = squad_node.props().get('units')
        members = [scalar(u) for u in units.items]
        if intent.get('remove'):
            members = [m for m in members if m != uid]
        elif uid not in members:
            members.append(uid)
        patch.replace(units, '{%s}' % ','.join(lua_text.quote(m) for m in members))
        unit = self.unit(uid)
        if unit is not None and not intent.get('remove'):
            _set_prop(patch, unit, 'Squad', lua_num(squad_uid))
        return '%s %s squad %s' % (uid, 'left' if intent.get('remove') else 'joined',
                                   squad_uid)

    def _do_new_merc(self, patch, intent):
        """Bring a merc onto the team. The save already holds a gv_UnitData
        entry for every hireable merc and IMP preset, so this configures that
        entry rather than fabricating a unit the game never made."""
        uid = intent['merc']
        tbl = self.unit(uid)
        campaign_time = scalar(self.game.props().get('CampaignTime')) or 0
        if tbl is None:
            preset = next((m for m in load_catalog()['mercs']
                           if m['id'] == uid and m.get('mod')), None)
            if preset is not None and not intent.get('stats'):
                spec = _preset_spec(preset, intent, campaign_time)
            else:
                spec = intent
            src = _render_unit_data(uid, spec, self.steam_id())
            patch.insert(self.unit_table().start + 1, '["%s"] = %s,' % (uid, src))
            return 'created %s (%s)' % (spec.get('nick') or uid, uid)

        stats = intent.get('stats') or {}
        for stat, val in stats.items():
            if stat in STATS:
                _set_prop(patch, tbl, stat, lua_num(int(val)))
        if intent.get('name'):
            _set_prop(patch, tbl, 'Name', user_text(intent['name'], self.steam_id()))
        if intent.get('nick'):
            _set_prop(patch, tbl, 'Nick', user_text(intent['nick'], self.steam_id()))
        if intent.get('spec'):
            valid = load_catalog().get('specializations') or []
            if valid and intent['spec'] not in valid:
                raise Ja3Error('%r is not a specialization the game knows (%s)'
                               % (intent['spec'], ', '.join(valid)))
            _set_prop(patch, tbl, 'Specialization', lua_text.quote(intent['spec']))

        hp = int(intent.get('max_hp') or stats.get('Health') or 50)
        xp = int(intent.get('xp', xp_for_level(int(intent.get('level', 1)))))
        _set_prop(patch, tbl, 'HireStatus', '"Hired"')
        _set_prop(patch, tbl, 'HiredUntil', lua_num(campaign_time + 30 * 24 * 3600))
        _set_prop(patch, tbl, 'Experience', lua_num(xp))
        _set_prop(patch, tbl, 'HitPoints', lua_num(hp))
        _set_prop(patch, tbl, 'MaxHitPoints', lua_num(hp))
        _set_prop(patch, tbl, 'ActionPoints', '11000')
        _set_prop(patch, tbl, 'session_id', lua_text.quote(uid))
        if intent.get('squad') is not None:
            _set_prop(patch, tbl, 'Squad', lua_num(int(intent['squad'])))
        if intent.get('perks') is not None:
            _set_prop(patch, tbl, 'StatusEffects', _render_status_effects(
                [(p, None) for p in intent['perks']], campaign_time))
        for slot in ALL_SLOTS:
            if tbl.props().get(slot) is None:
                _set_prop(patch, tbl, slot, "PlaceObj('InventorySlot', {})")
        return 'hired %s (%s)' % (intent.get('nick') or uid, uid)

    # -- helpers ---------------------------------------------------------
    def _require_unit(self, uid):
        tbl = self.unit(uid)
        if tbl is None:
            raise Ja3Error('no merc %r in gv_UnitData' % uid)
        return tbl

    def _require_item(self, uid, slot, index):
        unit = self._require_unit(uid)
        slot_node = unit.props().get(slot)
        if slot_node is None:
            raise Ja3Error('%s has no %s slot' % (uid, slot))
        items = slot_node.table().items
        i = index * 2 + 1
        if i >= len(items):
            raise Ja3Error('no item at %s[%d]' % (slot, index))
        return items[i]

    def _squad_node(self, uid):
        node = self.gvars.get('gv_Squads')
        for it in node.items:
            tbl = it.table() if it.kind == 'call' else None
            if tbl is not None and scalar(tbl.props().get('UniqueId')) == uid:
                return tbl
        return None

    # -- writing ---------------------------------------------------------
    def write(self, out_path=None, backup=True):
        out_path = out_path or self.path
        if self.text != self._orig_text:
            blob = zstd_chunks.compress(self.text.encode('utf-8')) if self.compressed \
                else self.text.encode('utf-8')
            self.container.set_blob('game_session', blob)
        if self.meta_text != self._orig_meta:
            self.container.set_blob('savegame_metadata', self.meta_text.encode('utf-8'))
        data = self.container.build()

        # never hand the game something we can't read back
        check = bpul.parse(data)
        got = zstd_chunks.decompress(check.blob('game_session')).decode('utf-8')
        if got != self.text:
            raise Ja3Error('self-check failed: rebuilt save does not round-trip')
        lua_text.parse(got)

        if backup and os.path.exists(out_path):
            stamp = time.strftime('%Y%m%d-%H%M%S')
            bak = '%s.%s.bak' % (out_path, stamp)
            shutil.copy2(out_path, bak)
        else:
            bak = None
        tmp = out_path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, out_path)
        self.raw = data
        return {'path': out_path, 'backup': bak, 'bytes': len(data)}


# ---------------------------------------------------------------------------
# node helpers
# ---------------------------------------------------------------------------

def _mod_entry_id(entry):
    """The id of one active_mods entry: a bare string in older saves, a
    table with an id field in newer ones."""
    if entry.kind == 'str':
        return entry.value
    tbl = entry.table() if entry.kind in ('call', 'table') else None
    return scalar(tbl.props().get('id')) if tbl is not None else None


def _catalog_merc(preset):
    for m in load_catalog()['mercs']:
        if m['id'] == preset:
            return m
    return None


def _modifiers(node):
    out = []
    if node is None or node.kind != 'table':
        return out
    for m in node.items:
        if m.kind != 'table':
            continue
        prop = scalar(m.get('prop'))
        params = m.get('params')
        amount = 0
        if params is not None and params.kind == 'table':
            for a in params.items:
                if a.kind == 'num':
                    amount += a.value
        if prop:
            out.append({'id': scalar(m.get('id')), 'prop': prop, 'amount': amount})
    return out


def _perk_entries(node):
    """[(effect_id, (start, end))] in array order."""
    out = []
    for it in node.items:
        if it.kind == 'call' and it.fname == 'PlaceCharacterEffect':
            out.append((it.placed_name(), (it.start, it.end)))
    return out


def _perks(node):
    if node is None or node.kind != 'table':
        return []
    return [name for name, _ in _perk_entries(node)]


def _wound_stacks(node):
    if node is None or node.kind != 'table':
        return 0
    total = 0
    for it in node.items:
        if it.kind == 'call' and it.placed_name() == 'Wounded':
            tbl = it.table()
            total += scalar(tbl.props().get('stacks')) if tbl and tbl.props().get('stacks') else 1
    return total


def _ammo_of(item_tbl):
    node = item_tbl.props().get('ammo')
    if node is None or node.kind != 'call':
        return None
    p = node.table().props() if node.table() is not None else {}
    return {'item': node.placed_name(), 'amount': scalar(p.get('Amount'))}


def _components(node):
    if node is None or node.kind != 'table':
        return {}
    return {k: scalar(v[2]) for k, v in node.fields.items()}


def _set_prop(patch, tbl, key, value_src):
    """Replace a PlaceObj property's value, or insert the property."""
    node = tbl.props().get(key)
    if node is not None:
        patch.replace(node, value_src)
    else:
        patch.insert(tbl.start + 1, " '%s', %s," % (key, value_src))


def _render_table_items(items, text):
    return '{%s}' % ','.join(text[n.start:n.end] for n in items)


def _render_status_effects(entries, campaign_time):
    """entries: [(effect_id, original_source or None)].

    The game stores this as a name -> 1-based array index map followed by the
    PlaceCharacterEffect entries themselves, in that same order.
    """
    names = [n for n, _ in entries]
    index = ','.join('%s = %d' % (n, i + 1) for i, n in enumerate(names))
    bodies = ','.join(
        src if src else
        "PlaceCharacterEffect('%s', { 'CampaignTimeAdded', %d, })" % (n, campaign_time)
        for n, src in entries)
    inner = ','.join(x for x in (index, bodies) if x)
    return '{%s}' % inner


def _render_item(item_id, uid, owner, condition=None, amount=None, ammo=None,
                 components=None):
    parts = []
    if condition is not None:
        parts.append("'Condition', %d," % int(condition))
    if owner:
        parts.append("'owner', %s," % lua_text.quote(owner))
    parts.append("'drop_chance', 100,")
    parts.append("'id', %d," % uid)
    if amount is not None:
        parts.append("'Amount', %d," % int(amount))
    if components:
        parts.append("'components', {%s}," % ','.join(
            '%s = %s' % (k, lua_text.quote(v or ''))
            for k, v in sorted(components.items())))
    if ammo:
        parts.append("'ammo', PlaceInventoryItem(%s, { 'id', %d, 'Amount', %d, })," %
                     (lua_text.quote(ammo['item']), uid + 1, int(ammo.get('amount', 30))))
    return "PlaceInventoryItem(%s, { %s })" % (lua_text.quote(item_id), ' '.join(parts))


def _preset_spec(preset, intent, campaign_time):
    """What to write for a merc a mod defines, recruited into a save that has
    no record of them.

    Stats, name, nick and specialisation are left out on purpose: the game
    builds the record as an instance of the merc's own preset (PlaceUnitData,
    Mercenary.lua), so anything absent comes from the mod - exactly as for an
    untouched A.I.M. merc in a normal save. What CreateUnitData would have
    added at campaign start is written here instead: hit points from Health
    (UnitData:InitDerivedProperties) and the starting perks.
    """
    health = int((preset.get('stats') or {}).get('Health') or 50)
    return {
        'nick': preset.get('nick') or preset.get('name'),
        'preset': True,
        'max_hp': health,
        'level': 1,
        'squad': intent.get('squad'),
        'perks': list(preset.get('starting_perks') or []),
        'campaign_time': campaign_time,
        'hired_until': campaign_time + 30 * 24 * 3600,
    }


def _render_unit_data(uid, spec, steam_id):
    """A fresh gv_UnitData entry: a custom (IMP-preset) character, or with
    spec['preset'] a merc whose own definition supplies everything left out."""
    stats = spec.get('stats') or {}
    parts = []
    for s in STATS:
        if spec.get('preset') and s not in stats:
            continue
        parts.append("'%s', %d," % (s, int(stats.get(s, 50))))
    if spec.get('name') and not spec.get('preset'):
        parts.append("'Name', %s," % user_text(spec['name'], steam_id))
    if spec.get('nick') and not spec.get('preset'):
        parts.append("'Nick', %s," % user_text(spec['nick'], steam_id))
    hp = int(spec.get('max_hp') or stats.get('Health', 50))
    xp = int(spec.get('xp', xp_for_level(int(spec.get('level', 1)))))
    parts.append("'HireStatus', \"Hired\",")
    if spec.get('hired_until') is not None:
        parts.append("'HiredUntil', %d," % int(spec['hired_until']))
    parts.append("'Experience', %d," % xp)
    parts.append("'ActionPoints', 11000,")
    parts.append("'HitPoints', %d," % hp)
    parts.append("'MaxHitPoints', %d," % hp)
    if spec.get('spec'):
        parts.append("'Specialization', %s," % lua_text.quote(spec['spec']))
    if spec.get('squad') is not None:
        parts.append("'Squad', %d," % int(spec['squad']))
    parts.append("'session_id', %s," % lua_text.quote(uid))
    parts.append("'randomization_seed', %d," % random.randint(1, 2 ** 31 - 1))
    parts.append("'OperationProfessions', {},")
    perks = spec.get('perks') or []
    parts.append("'StatusEffects', %s," % _render_status_effects(
        [(p, None) for p in perks], int(spec.get('campaign_time', 0))))
    parts.append("'StatusEffectImmunity', {},")
    parts.append("'StatusEffectReceivedTime', {},")
    for slot in ALL_SLOTS + ('InventoryDead',):
        parts.append("'%s', PlaceObj('InventorySlot', {})," % slot)
    return "PlaceUnitData(%s, { %s })" % (lua_text.quote(uid), ' '.join(parts))
