"""Reader for Jagged Alliance 2 savegames.

Nothing like a JA3 save. JA3 is a BPUL container holding compressed Lua source
text; JA2 is C structs written straight to disk, little endian, no compression
and no framing. Fixed offsets, mixed integer widths, and a different layout per
game build.

    0x000  uint32   save format version   (95-99, and 103 for 1.13)
    0x004  char[16] build string          "Build 04.12.02", NUL padded
    0x014  int16[80] save name            UTF-16LE, NUL terminated
    ...
    0x118  uint32   day
    0x11C  uint8    hour
    0x11D  uint8    minute
    0x124  int32    current balance - a COPY, used to pick the key set
    ...
    header ends at 0x1B0 for version 99, 0x1B4 for 103

After the header come the tactical status, sector coordinates, the game clock,
strategic events, laptop state, then the MERCPROFILESTRUCT array (every
character in the game) and the SOLDIERTYPE array (the hired ones).

**Where these offsets came from.** The header layout is documented in the
source of JAPE, by Douglas Greiman, who gave permission for its use. Offsets
are facts about a file format, not code - none of his implementation is here,
and every value below was checked against a real save before being trusted.
See CREDITS.md.
"""
import os
import shutil
import struct
import time

import features
from ja2 import ammo as ja2_ammo
from ja2 import item_data as ja2_items
from ja2 import item_stats as ja2_stats
from ja2 import key_sets as ja2_key_sets
from ja2 import layout as ja2_layout
from ja2 import profiles as ja2_profiles
from ja2 import soldiers as ja2_soldiers

# save format versions we recognise
VERSIONS = (95, 96, 97, 98, 99, 103)

# the header is a different length in 1.13, because GAME_OPTIONS grew
HEADER_LEN = {99: 0x1B0, 103: 0x1B4}
HEADER_LEN_DEFAULT = 0x1B0

NAME_OFFSET = 0x14
NAME_CHARS = 80                      # int16 each, so 160 bytes

OFF_VERSION = 0x00
OFF_BUILD = 0x04
BUILD_LEN = 16
OFF_DAY = 0x118
OFF_HOUR = 0x11C
OFF_MIN = 0x11D
OFF_BALANCE = 0x124
OFF_FLAGS = 0x114
OFF_SECTOR_Z = 0x122
OFF_MERCS = 0x123
OFF_ALT_SECTOR = 0x12C
OFF_LOAD_SCREEN = 0x12E
OFF_GUN_NUT = 0x12F
OFF_SCI_FI = 0x130
OFF_DIFFICULTY = 0x131
OFF_RANDOM = 0x13C

# What follows the header, in order. Sizes from the game's own save code
# (Stracciatella LoadSaveTacticalStatusType.cc, Game_Clock.cc,
# Game_Events.cc, Laptop.cc), checked against SaveGame01: they put the laptop
# block at 5131 and the profiles at 12571, which is where they were measured.
TACTICAL_STATUS = 316 + 5        # the struct, then the world sector
GAME_CLOCK = 42 + 20             # the fields, then TIME_PADDINGBYTES
EVENT_SIZE = 28                  # after a uint32 count
LAPTOP_SIZE = 7440
LAPTOP_BALANCE = 104             # LaptopSaveInfo.iCurrentBalance - the REAL one
LAPTOP_BOBBY_COUNT = 7276        # orders on delivery: array size, then used
LAPTOP_INSURANCE_COUNT = 7284    # insurance payouts: array size, then used
BOBBY_ORDER_SIZE = 84
INSURANCE_PAYOUT_SIZE = 8
LAPTOP_LEFT_LIST = 622           # int16[256] personnel "left" list, -1 = free

# inside one 28-byte strategic event: param, then the callback - which IS the
# event id; the byte before it only says one-off / ranged / periodic
EV_PARAM = 8
EV_CALLBACK = 17
SOLDIER_EVENTS = (12, 73)        # delayed hiring, RPC whines: param = soldier
PROFILE_EVENTS = (20, 77)        # equipment complaint, level-up mail: profile
GROUP_EVENTS = (18, 35)          # group arrival / about to arrive: group id

# Leaving the team (see Save._do_ja2_fire)
TEAM_MEN_IN_SECTOR = 13          # TacticalStatus, team 0's bMenInSector
P_DAY_AVAILABLE = 292            # profile uiDayBecomesAvailable, uint32
P_STATUS = 652                   # profile bMercStatus
MERC_RETURNING_HOME = -6
S_FLAGS = 8                      # soldier uiStatusFlags
S_IN_SECTOR = 705
SOLDIER_VEHICLE = 0x00008000
SOLDIER_OFF_MAP = 0x02000000
FIRE_REFUSED = {23: 'is in a vehicle', 24: 'is still on the way here',
                32: 'is a prisoner of war'}

# Joining the team (see Save._do_ja2_hire). Soldier offsets are the game's
# InjectSoldierType, profile offsets its InjectMercProfile; each one was read
# back off the three hired mercs in SaveGame01 before being written anywhere.
TEAM_FIRST_ID = 4                # TacticalStatus, team 0's slot range
TEAM_LAST_ID = 5
CLOCK_SECONDS = 8                # guiGameClock, into the game clock block
ARRIVAL_DELAY = 60               # minutes until the delayed-hiring event
EVENT_DELAYED_HIRING = 12
LAPTOP_DEAD_LIST = 110           # then left (+512) and other (+1024)
LAPTOP_POPUP_SHOWN = 7316
LAPTOP_LAST_HIRED = 7320         # int32 profile, uint32 arrival minute
MAX_TEAM = 18
LAST_AIM = 39                    # profiles 0-39 are A.I.M.
LAST_MERC_SITE = 50              # 40-50 are M.E.R.C.
MERC_TYPE_AIM = 1
MERC_TYPE_MERC = 2
MERC_ANNOYED_CAN_CONTACT = -2
MERC_HIRED_NOT_ARRIVED = -4
HIRE_REFUSED = {
    -1: 'has no file at A.I.M. or M.E.R.C.',
    -3: 'is too annoyed with you to take the call',
    -4: 'is already on the way',
    -5: 'is dead',
    -6: 'has only just gone home and is not available yet',
    -7: 'is working for someone else',
    -8: 'was let go as a prisoner of war and will not come back',
}
CONTRACTS = {1: 0, 7: 1, 14: 2}  # days -> CONTRACT_EXTEND_1_DAY/1_WEEK/2_WEEK
IN_TRANSIT = 24
INSERTION_ARRIVING = 5           # INSERTION_CODE_ARRIVING_GAME
NOWHERE = 160 * 160 + 1
DEFAULT_MORALE = 50
REGFEMALE = 3
CAMOUFLAGED = 15
EMPTY_KEYRING = b'\xff\x00' * 64

P_MISC_FLAGS = 233
ALREADY_USED_ITEMS = 0x80
P_BODY = 260
P_ANIM_SUB = 328
P_MEDICAL_DEPOSIT = 552
P_GEAR_COST = 574
P_CIV_GROUP = 682
P_MERC_CONTRACT = 704
# profile colour name -> soldier palette field: hair, pants, vest, skin
PALETTES = ((199, 924), (109, 954), (139, 984), (169, 1014))

H_ID = 0
H_BODY = 2
H_OLD_LIFE = 704
H_VISIBLE = 750
H_BETWEEN = 754
H_GRIDNO = 774
H_INSERT_CODE = 1504
H_INSERT_DATA = 1506
H_BATTLE_SOUND = 1828
H_ANIM_SUB = 1852
H_CONTRACT_END = 1900
H_CONTRACT_START = 1904
H_CONTRACT_LEN = 1908
H_KIND = 1916
H_MEDICAL_DEPOSIT = 1938
H_INSURANCE_START = 1968
H_ASSIGN_CHANGED = 1972
H_INSURANCE_LEN = 1976
H_CIV_GROUP = 1988
H_UNIQUE = 2036
H_CAMO = 2104
H_LAST_CONTRACT_UPDATE = 2148
H_CONTRACT_TYPE = 2152
H_ARRIVES = 2244
H_LANDING_ZONE = 2249
H_SIGN_ELSEWHERE = 2252
H_INSURANCE_TIME = 2260
# (offset, length) of a teammate's state that is not the new merc's
CLEAR_ON_HIRE = (
    (710, 1),                    # bleeding
    (1408, 4),                   # destination, final destination
    (1416, 64),                  # pathing data, its size and index
    (1826, 2),                   # quote record / action
    (1842, 2),                   # quotes said
    (2041, 38),                  # new-item markers
    (2090, 2),                   # weapon being attacked with
    (2156, 10),                  # drug effects
    (2170, 2),                   # done-assignment, asleep
    (2176, 2),                   # more quotes said
    (2197, 1),                   # ammo quote pending
    (2214, 1),                   # misc soldier flags
    (2220, 5),                   # last spoke, renewal quote
)


# -- the merc profile array ---------------------------------------------
#
# Found by oracle diffing: edit one stat in JAPE, save, compare the bytes.
# Two mercs four apart in the list produced blocks exactly 2864 bytes apart,
# which is 4 x 716 - and 716 is also Prof.dat's size divided by its 170
# records. Two unrelated routes to the same number.
#
# The records are enciphered, not obfuscated, and the scheme is now known: it
# is the one in the game's own "Tactical Save.c", cipher[i] = plain[i] +
# cipher[i-1] + key[i], each record its own block. See ja2/profile_data.py for
# the key and how it was recovered. The cascade that made this look impenetrable
# is just that chaining, and it doubles as a measuring tool - **the first
# changed byte IS the field's offset**, so a new field costs one edit and one
# diff.
#
# Barry is profile 0: Prof.dat decodes to "Barry Unger" at record 0, and
# 121720 / 716 = 170 exactly, which agrees with the record size found by
# diffing two mercs four apart in the list.
#
# Verified against build 04.12.02, save version 99. Whether these hold for the
# other six builds is not yet known, so writing is refused elsewhere.
PROFILE_START = 12571          # SaveGame01's; others are found by locate()
PROFILE_SIZE = 716
PROFILE_COUNT = 170

# Builds whose profile layout has actually been checked. Reading a wrong offset
# shows a wrong number; writing one corrupts a campaign, so this list gates
# writing and nothing else.
WRITABLE_VERSIONS = (99,)

# Offsets WITHIN a profile record, every one measured by oracle diff against
# build 04.12.02. Note they are NOT contiguous - the ten stats are scattered
# through the struct rather than sitting in a block, which is worth knowing
# before anyone "tidies" this list into a range.
#
# Agility was re-measured in a separate JAPE session and came back at 405
# again, so the method reproduces.
# One definition, over in the data module beside the key it belongs with.
# Two copies of a table of byte offsets is two chances to fix only one of them.
STATS = ja2_profiles.D.STATS
# still to measure: health, energy, morale, exp level, the (+bonus) box beside
# each stat, the two skills, and the six career counters

# CORRECTION, 2026-09-03. This comment used to say the bytes at 696 were ten
# int16 "gain" counters and that nothing in the record is checksummed. Both
# halves were wrong.
#
# 696..699 are a four byte CHECKSUM and 700..715 are zero padding. JAPE
# declares it as CHECKSUM_OFFSET_99 = 696, CHECKSUM_LENGTH = 4, and computes it
# in ActorChecksum - a routine whose local variables are named eax/ebx/edx
# because it was transcribed from the game's own disassembly.
#
# Since 2026-10-11 every record a batch changes is re-signed with the value the
# game would have written (Save._fix_checksums). The real gain counters are at
# 298..308; see ja2/profile_data.py GAINS.


def profile_offset(index, start=PROFILE_START):
    """Where merc `index` record begins in the file."""
    if not 0 <= index < PROFILE_COUNT:
        raise Ja2Error('profile index %d is outside 0..%d'
                       % (index, PROFILE_COUNT - 1))
    return start + index * PROFILE_SIZE


class Ja2Error(Exception):
    pass


def locate(raw, header_len):
    """(laptop block, profile array) file offsets, or None if they do not fit.

    Neither is fixed: 28 bytes move per scheduled event, and Bobby Ray orders
    and insurance payouts in progress are appended to the laptop block. Only
    the used counts decide whether those arrays were written at all.
    """
    at = header_len + TACTICAL_STATUS + GAME_CLOCK
    if at + 4 > len(raw):
        return None
    laptop = at + 4 + EVENT_SIZE * struct.unpack_from('<I', raw, at)[0]
    if laptop + LAPTOP_SIZE > len(raw):
        return None
    profiles = laptop + LAPTOP_SIZE
    size, used = raw[laptop + LAPTOP_BOBBY_COUNT:laptop + LAPTOP_BOBBY_COUNT + 2]
    if used:
        profiles += size * BOBBY_ORDER_SIZE
    size, used = raw[laptop + LAPTOP_INSURANCE_COUNT:
                     laptop + LAPTOP_INSURANCE_COUNT + 2]
    if used:
        profiles += size * INSURANCE_PAYOUT_SIZE
    if profiles + PROFILE_SIZE * PROFILE_COUNT > len(raw):
        return None
    return laptop, profiles


def key_set(raw, german=False):
    """Which of the 228 key sets the game used - CalcJA2EncryptionSet.

    Every input is a header field, which is why the header balance must never
    change on its own: the game would decode every merc with the wrong key.
    """
    u32 = lambda off: struct.unpack_from('<I', raw, off)[0]          # noqa: E731
    s = u32(OFF_VERSION) * u32(OFF_FLAGS)
    s += u32(OFF_BALANCE)
    s *= raw[OFF_MERCS] + 1
    s += struct.unpack_from('<b', raw, OFF_SECTOR_Z)[0] * 3
    s += raw[OFF_LOAD_SCREEN]
    if raw[OFF_ALT_SECTOR]:
        s += 7
    r = u32(OFF_RANDOM)
    if r % 2 == 0:
        s += 1
        if r % 7 == 0:
            s += 1
            if r % 23 == 0:
                s += 1
            if r % 79 == 0:
                s += 2
    s &= 0xFFFFFFFF
    if german:
        s = (s * 11) & 0xFFFFFFFF
    s = (s % 10 + u32(OFF_DAY) // 10) % 19
    if raw[OFF_GUN_NUT]:
        s += 19 * 6
    if raw[OFF_SCI_FI]:
        s += 19 * 3
    s += 19 * max(0, min(2, raw[OFF_DIFFICULTY] - 1))
    return s


def keystream(n):
    """Key set `n` repeated across the longest block it has to cover."""
    k = ja2_key_sets.SETS[n]
    return tuple(k[i % len(k)] for i in range(ja2_soldiers.CIPHERTEXT))


def _text16(raw):
    """A NUL-terminated UTF-16LE string out of a fixed-width field."""
    s = raw.decode('utf-16-le', 'replace')
    return s.split('\0', 1)[0].strip()


def _text8(raw):
    return raw.split(b'\0', 1)[0].decode('cp1252', 'replace').strip()


class Save:
    """A JA2 savegame.

    Reading stays separated from writing. A wrong offset that only reads gives
    you a wrong number on screen; a wrong offset that writes corrupts somebody's
    campaign - so writing is refused unless the build is one whose layout has
    been checked AND the profile array passes a range check on all 1530 stat
    values. Both have to agree before a single byte moves.
    """

    GAME = 'ja2'

    # Stats are the whole of it so far, so everything in features.FEATURES
    # comes back False - including the ones nobody thought to list before.
    # `portraits` was one of those: it was offered on a JA2 merc and pointed at
    # JA3's mods folder, which is what changed the rule from "list what you
    # cannot do" to "name everything". Declared as a class attribute to match
    # the JA3 reader, so one test can read both.
    SUPPORTS = features.only()

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as f:
            self.raw = bytearray(f.read())
        if len(self.raw) < HEADER_LEN_DEFAULT:
            raise Ja2Error('%s is too small to be a JA2 save' % path)
        self.version = struct.unpack_from('<I', self.raw, OFF_VERSION)[0]
        if self.version not in VERSIONS:
            raise Ja2Error(
                'save format version %d is not one this reader knows (%s)'
                % (self.version, ', '.join(str(v) for v in VERSIONS)))
        self.build = _text8(self.raw[OFF_BUILD:OFF_BUILD + BUILD_LEN])
        self.header_len = HEADER_LEN.get(self.version, HEADER_LEN_DEFAULT)
        self.dirty = False
        self.intents = []
        self._parse()

    def _parse(self):
        """Find every section and the key. Run again after a batch that
        changes the file's length, since everything after the change moved."""
        where = locate(self.raw, self.header_len)
        self.laptop_start = where[0] if where else None
        starts = [where[1]] if where else []
        if PROFILE_START not in starts:
            starts.append(PROFILE_START)
        sets = []
        for german in (False, True):
            n = key_set(self.raw, german)
            if n not in sets:
                sets.append(n)
        # The first (start, key) the 1530-value range check accepts. When none
        # does the first is kept, and editing is refused on the sanity result.
        tried = None
        for start in starts:
            for n in sets:
                self.profile_start, self.key_set = start, n
                self.key = keystream(n)
                self.profiles = ja2_profiles.Profiles(self.raw, start, self.key)
                self._ok, self._why = ja2_profiles.sanity(self.profiles)
                tried = tried or (start, n, self._why)
                if self._ok:
                    break
            if self._ok:
                break
        if not self._ok:
            self.profile_start, self.key_set, self._why = tried
            self.key = keystream(self.key_set)
            self.profiles = ja2_profiles.Profiles(
                self.raw, self.profile_start, self.key)
        self._german = self.key_set != key_set(self.raw)
        self._load_soldiers()

    def _load_soldiers(self):
        # The walked map knows which records are the player's; the old stride
        # walk only works while the team sits in unbroken slots from 0.
        end = self.profile_start + PROFILE_SIZE * PROFILE_COUNT
        self.layout = (ja2_layout.walk(self.raw, end, self.key)
                       if self._ok else None)
        bases = ([s['base'] for s in self.layout.team()]
                 if self.layout else None)
        try:
            self.soldiers = ja2_soldiers.Soldiers(
                self.raw, self.profile_start, PROFILE_SIZE, PROFILE_COUNT,
                self.key, bases)
        except Exception:                                    # noqa: BLE001
            self.soldiers = None      # never let the extras break the basics
        self._att = self._match_soldiers()

    # -- may we write? ---------------------------------------------------
    #
    # The server checks `read_only` before letting anything mutate a save.
    # JA3's Save has no such attribute, so getattr(save, 'read_only', False)
    # is False there and correct here without either class knowing about the
    # other.

    @property
    def read_only(self):
        return bool(self.blocked_because)

    @property
    def blocked_because(self):
        """Why editing is refused, or '' when it is allowed."""
        if self.version not in WRITABLE_VERSIONS:
            return ('this editor has only checked the merc layout of save '
                    'version %s, and this save is version %d (%s), so it is '
                    'shown but not edited'
                    % ('/'.join(str(v) for v in WRITABLE_VERSIONS),
                       self.version, self.build or 'unknown build'))
        if not self._ok:
            return ('the merc records are not where this build puts them - %s'
                    % self._why)
        return ''

    # -- mercs -----------------------------------------------------------

    def _match_soldiers(self):
        """{profile index: {'record': soldier record number}}.

        Each hired-merc record names its profile at offset 1825, readable now
        that the whole key is known. That is used when the profile it names is
        a real one and the nickname agrees; the old match on carried items
        stays as the fallback for a record that does not.
        """
        out = {}
        if not self.soldiers or not self._ok or not self.soldiers.bases:
            return out
        named = self.profiles.named()
        left = []
        for n in range(len(self.soldiers.bases)):
            p = self.soldiers.profile(n)
            if (p in named and p not in out
                    and self.soldiers.nickname(n) == self.profiles.nickname(p)):
                out[p] = {'record': n}
            else:
                left.append(n)
        if not left:
            return out
        named = [i for i in named if i not in out]
        profile_items = {}
        for i in named:
            # (item, condition) rather than item alone. Half the roster wears a
            # Steel Helmet, so matching on ids alone ties constantly and the
            # tie-break then refuses everything - which is how the merc who
            # actually had the scope fitted came back unmatched. A worn item at
            # 92% is nearly a fingerprint.
            items = {(x['item'], x['condition'])
                     for x in self.profiles.inventory(i) if x['item']}
            if items:
                profile_items[i] = items
        taken = set()
        for n in left:
            mine = {(x['item'], x['condition'])
                    for x in self.soldiers.inventory(n) if x['item']}
            if not mine:
                continue
            scored = sorted(
                ((len(mine & items), i) for i, items in profile_items.items()
                 if i not in taken), reverse=True)
            if len(scored) < 2 or scored[0][0] <= scored[1][0] or scored[0][0] < 2:
                continue          # no clear winner: say nothing
            best = scored[0][1]
            taken.add(best)
            out[best] = {'record': n}
        return out

    def mercs(self):
        """Everyone in the save, with the gear the GAME will actually use.

        This is the correction that matters most in the whole JA2 half.

        There are two inventories per hired merc and they are not copies of
        each other. The profile record holds the loadout - what the merc turns
        up with. The soldier record holds what they are carrying right now,
        and that is what the game reads in the field. On the tester's save KDog's
        profile has TNT in a pack slot with a separate detonator beside it,
        while his soldier record has the TNT in his left hand with the
        detonator fitted to it. Both are true; only one of them is the game.

        The editor used to show the profile inventory and hang the soldier's
        attachments onto it by **matching item ids**. That produced three
        separate faults, all seen on a real save:

          * change the item and the attachments vanished, because the match
            broke - the data was still in the file
          * put the same item in a different slot and it picked up a *different
            weapon's* attachments, because an id says nothing about where
          * and the panel kept showing the previous weapon's attachments after
            an item change, so editing one wrote a scope onto a gun that was
            no longer the one on screen

        So: a hired merc's `inventory` is now the soldier record, slot for
        slot, no matching anywhere. The profile loadout is still carried, as
        `aim_inventory`, because it is the real answer for anyone not hired
        and it is what a merc comes back with.
        """
        if not self._ok:
            return []
        out = []
        for i in self.profiles.named():
            e = self.profiles.entry(i)
            att = self._att.get(i)
            if att and self.soldiers:
                n = att['record']
                e['soldier'] = n
                e['aim_inventory'] = e['inventory']
                live = self.soldiers.inventory(n)
                for row in live:
                    # the slot IS the soldier slot - they are the same
                    # nineteen positions in both records
                    row['soldier_slot'] = row['slot']
                    row['category'] = (ja2_items.category(row['item'])
                                       if row['item'] else '')
                e['inventory'] = live
                e.update(self.soldiers.energy(n))
                # A hired merc's own copy is the one the game plays with,
                # so that is the one shown.
                live = self.soldiers.merc(n)
                e['stats'] = {k: {'value': v} for k, v in live['stats'].items()}
                e['hp'] = live['health']
                e['max_hp'] = live['max_health'] or 100
                e['level'] = live['level'] or None
                e['skills'] = live['skills']
                e['skill_names'] = [ja2_profiles.D.skill_name(k)
                                    for k in live['skills']]
                e['field_nick'] = live['nick']
                e['fire_refused'] = self.fire_refusal(i)
            elif i <= LAST_MERC_SITE:
                e['hire_refused'] = self.hire_refusal(i)
                e['hire_site'] = 'A.I.M.' if i <= LAST_AIM else 'M.E.R.C.'
            out.append(e)
        return out

    def _record(self, index):
        """The hired-merc record for profile `index`, or None."""
        att = self._att.get(index)
        return att['record'] if att and self.soldiers else None

    def set_stat(self, index, stat, value):
        if self.read_only:
            raise Ja2Error(self.blocked_because)
        if self.profiles.set_stat(index, stat, value):
            self.dirty = True
        rec = self._record(index)
        if rec is not None and stat in ja2_soldiers.STATS:
            if self.soldiers.set_field(rec, ja2_soldiers.STATS[stat], value):
                self.dirty = True
        return self.profiles.get(index, ja2_profiles.D.STATS[stat])

    # -- the same queue/apply/write protocol the JA3 save speaks -----------
    #
    # The server drives both games through these three calls, so matching the
    # JA3 shape here is what keeps `if game ==` out of the request handlers.

    def queue(self, kind, **kw):
        kw['kind'] = kind
        self.intents.append(kw)
        return kw

    def has_changes(self):
        return bool(self.intents)

    PHASES = ('money', 'ja2_name', 'stat', 'merc_field', 'ja2_item',
              'ja2_live_item', 'ja2_attach')

    def apply(self):
        """Run every queued intent, returning a line per change for the log."""
        if self.read_only:
            raise Ja2Error(self.blocked_because)
        # Capture the baseline BEFORE the first byte moves. It is a lazy
        # property, so leaving it to write() would compute it AFTER the edit -
        # the new problem would be in the baseline, nothing would look new, and
        # the guard would pass every time. A guard that cannot fail is the
        # thing this whole project keeps finding.
        self._opened_implausible
        before = bytes(self.raw)
        applied = []
        for phase in self.PHASES:
            for intent in [i for i in self.intents if i['kind'] == phase]:
                desc = getattr(self, '_do_' + phase)(intent)
                if desc:
                    applied.append(desc)
        self._fix_checksums(before)
        # Joining or leaving the team changes the file's length, so those run
        # last, one merc at a time, re-reading the file after each.
        for intent in [i for i in self.intents
                       if i['kind'] in ('ja2_fire', 'ja2_hire')]:
            desc = getattr(self, '_do_' + intent['kind'])(intent)
            if desc:
                applied.append(desc)
        self.intents = []
        return applied

    def _fix_checksums(self, before):
        """Re-sign every record this batch changed, the way the game would.

        Only changed records: one the game left with an odd checksum is not
        ours to "correct". A record we changed earlier and left stale counts
        as changed the moment it is edited again, which is how those heal.
        """
        P = self.profiles
        for i in range(PROFILE_COUNT):
            lo = self.profile_start + i * PROFILE_SIZE
            if self.raw[lo:lo + PROFILE_SIZE] != before[lo:lo + PROFILE_SIZE]:
                P.fix_checksum(i)
        if self.soldiers:
            size = ja2_soldiers.CIPHERTEXT
            for n, b in enumerate(self.soldiers.bases):
                if self.raw[b:b + size] != before[b:b + size]:
                    self.soldiers.fix_checksum(n)

    def _merc_index(self, merc_id):
        """Find a merc by id, profile number, nickname or full name.

        The UI sends 'p7' - the id it was handed. A person at the command line
        types 'Ivan', and refusing that on the grounds that the internal id is
        'p7' would be the tool being difficult about its own bookkeeping.
        """
        want = str(merc_id).strip()
        try:
            return int(want[1:] if want[:1] in 'pP' else want)
        except ValueError:
            pass
        low = want.lower()
        for i in self.profiles.named():
            if low in (self.profiles.nickname(i).lower(),
                       self.profiles.name(i).lower()):
                return i
        raise Ja2Error('no merc called %r in this save' % (merc_id,))

    def _do_money(self, intent):
        """The balance the game plays with is LaptopSaveInfo.iCurrentBalance.

        Not the header's. The header holds a copy for the load screen, and the
        game feeds that copy into CalcJA2EncryptionSet - changing it alone
        would make the game decode every merc with the wrong key. So the
        header copy is left alone; the game rewrites it on the next save.
        """
        if self.laptop_start is None:
            raise Ja2Error('could not find the laptop block in this save, '
                           'which is where the real balance lives')
        want = int(intent['value'])
        lo, hi = -2147483648, 2147483647
        if not lo <= want <= hi:
            raise Ja2Error('%d will not fit in the balance field' % want)
        before = self.balance
        if before == want:
            return None
        struct.pack_into('<i', self.raw,
                         self.laptop_start + LAPTOP_BALANCE, want)
        self.dirty = True
        return 'money %d -> %d' % (before, want)

    def _do_ja2_fire(self, intent):
        """Send a merc home - what the game's StrategicRemoveMerc does.

        The game's loader trusts these to agree: a movement group that still
        lists someone no longer on the team is dereferenced as a missing
        soldier. So every place the merc is named goes in one step:

          * the profile: status "returning home", available again in a day
          * the laptop's personnel list of mercs who left
          * the team's head count in the loaded sector, if they were in it
          * their movement group's member list (and the group itself, if it
            was a temporary one they were alone in, with its arrival events)
          * their squad, closing the gap the way RemoveCharacterFromSquads does
          * the soldier slot - flag cleared, record, path and keyring removed
          * strategic events that name this soldier or merc

        What they carry leaves with them, as when the game lets a merc go
        without dropping their gear. The header's merc count is left alone:
        the game picks the merc key from it, and rewrites it on its next save.
        """
        i = self._merc_index(intent['merc'])
        who = self.profiles.nickname(i) or self.profiles.name(i) or 'p%d' % i
        why = self.fire_refusal(i)
        if why:
            raise Ja2Error('%s %s' % (who, why))
        L = self.layout
        S = self.soldiers
        n = self._record(i)
        sol = self._team_entry(n)
        b = sol['base']
        flags = sum(S._get(b, S_FLAGS + k) << (8 * k) for k in range(4))
        raw = self.raw
        slot = sol['slot']

        # -- in place ------------------------------------------------------
        P = self.profiles
        P.put(i, P_STATUS, MERC_RETURNING_HOME & 0xFF)
        for k in (3, 2, 1, 0):
            P.put(i, P_DAY_AVAILABLE + k, (1 >> (8 * k)) & 0xFF)
        P.fix_checksum(i)

        left = self.laptop_start + LAPTOP_LEFT_LIST
        names = struct.unpack_from('<256h', raw, left)
        if i not in names and -1 in names:
            struct.pack_into('<h', raw, left + 2 * names.index(-1), i)

        if not flags & SOLDIER_OFF_MAP:
            at = self.header_len + TEAM_MEN_IN_SECTOR
            raw[at] = max(0, raw[at] - 1)

        # -- structural, from the end of the file back ---------------------
        gone_group = None
        g = L.group(sol['group']) if sol['group'] else None
        if g and g['player'] and i in g['members']:
            keep = [m for m in g['members'] if m != i]
            span = slice(g['members_at'], g['members_at'] + 4
                         + 4 * len(g['members']))
            if not keep and not g['persistent']:
                del raw[g['head']:g['end']]
                struct.pack_into('<I', raw, L.groups_count,
                                 len(L.groups) - 1)
                gone_group = g['id']
            else:
                raw[span] = (struct.pack('<I%dI' % len(keep), len(keep), *keep)
                             if keep else b'')
                raw[g['head'] + ja2_layout.G_SIZE] = len(keep)

        pos = L.squad_of(raw, slot)
        if pos:
            squad = pos[0]
            first = L.squads + squad * ja2_layout.SQUAD_SLOTS \
                * ja2_layout.SQUAD_RECORD
            ids = [struct.unpack_from('<h', raw, first + k
                                      * ja2_layout.SQUAD_RECORD)[0]
                   for k in range(ja2_layout.SQUAD_SLOTS)]
            ids = [x for x in ids if x != slot]
            ids += [-1] * (ja2_layout.SQUAD_SLOTS - len(ids))
            for k, x in enumerate(ids):
                struct.pack_into('<h', raw, first + k * ja2_layout.SQUAD_RECORD,
                                 x)

        del raw[sol['base']:sol['end']]
        raw[sol['flag']] = 0

        dropped = self._drop_events(
            lambda cb, param: (cb in SOLDIER_EVENTS and param == slot)
            or (cb in PROFILE_EVENTS and param == i)
            or (gone_group is not None and cb in GROUP_EVENTS
                and param == gone_group))

        self.dirty = True
        self._parse()
        return '%s left the team%s' % (
            who, ' (%d scheduled event%s about them removed)'
            % (dropped, '' if dropped == 1 else 's') if dropped else '')

    def _do_ja2_hire(self, intent):
        """Hire an A.I.M. or M.E.R.C. merc, the way the game's HireMerc does.

        The new soldier is created IN TRANSIT with a delayed-hiring event an
        hour from now, which is how every hire the game makes starts. When it
        fires, the game's own MercArrivesCallback puts them in a squad at the
        landing zone and starts the contract - so this never has to invent a
        position on a map or a place in a movement group.

        The record is a teammate's, with everything that belongs to one
        particular soldier replaced: who they are, their body and colours,
        stats, gear, position, contract and unique id. The fields left alone
        are the ones that read the same on every soldier of the team.
        """
        i = self._merc_index(intent['merc'])
        P = self.profiles
        who = P.nickname(i) or P.name(i) or 'p%d' % i
        days = int(intent.get('days', 7))
        if days not in CONTRACTS:
            raise Ja2Error('a contract is 1, 7 or 14 days, not %d' % days)
        gear = bool(intent.get('gear', True))
        why = self.hire_refusal(i)
        if why:
            raise Ja2Error('%s %s' % (who, why))
        L = self.layout
        team = L.team()
        kind = MERC_TYPE_AIM if i <= LAST_AIM else MERC_TYPE_MERC
        if kind == MERC_TYPE_MERC:
            days = 1                # M.E.R.C. bills daily; the game hires for 1
        h = self.header_len
        slot = self._free_slot()
        S = self.soldiers
        tmpl = next((s for s in team if s['team'] == 0
                     and S._get(s['base'], ja2_layout.S_ASSIGNMENT) < 20
                     and S._get(s['base'], H_KIND) == MERC_TYPE_AIM), None) \
            or next((s for s in team
                     if not sum(S._get(s['base'], S_FLAGS + k) << (8 * k)
                                for k in range(4)) & SOLDIER_VEHICLE), None)
        if tmpl is None:
            raise Ja2Error('no teammate to model the new record on')

        now = struct.unpack_from(
            '<I', self.raw, h + TACTICAL_STATUS + CLOCK_SECONDS)[0] // 60
        arrive = now + ARRIVAL_DELAY
        rec = bytearray(S._get(tmpl['base'], k)
                        for k in range(ja2_layout.SOLDIER_CIPHER))
        self._hire_record(rec, i, slot, kind, days, now, arrive)

        # -- in place ------------------------------------------------------
        P.put(i, P_STATUS, MERC_HIRED_NOT_ARRIVED & 0xFF)
        if kind == MERC_TYPE_MERC:
            for k in (3, 2, 1, 0):
                P.put(i, P_MERC_CONTRACT + k, (1 >> (8 * k)) & 0xFF)
        if gear:
            if kind == MERC_TYPE_AIM:
                P.put(i, P_MISC_FLAGS, P.get(i, P_MISC_FLAGS) | ALREADY_USED_ITEMS)
            P.put_word(i, P_GEAR_COST, 0)
        P.fix_checksum(i)

        lap = self.laptop_start
        for lst in range(3):           # dead, left, other
            at = lap + LAPTOP_DEAD_LIST + 512 * lst
            names = list(struct.unpack_from('<256h', self.raw, at))
            if i in names:
                names[names.index(i)] = -1
                struct.pack_into('<256h', self.raw, at, *names)
        self.raw[lap + LAPTOP_POPUP_SHOWN] = 0
        struct.pack_into('<iI', self.raw, lap + LAPTOP_LAST_HIRED, i, arrive)

        at = h + TEAM_MEN_IN_SECTOR
        self.raw[at] = min(127, self.raw[at] + 1)

        # -- structural, from the end of the file back ---------------------
        cipher = bytearray(len(rec))
        prev = 0
        for k, v in enumerate(rec):
            prev = cipher[k] = (v + prev + self.key[k]) % 256
        flag = L.flags[slot]
        self.raw[flag] = 1
        self.raw[flag + 1:flag + 1] = (bytes(cipher) + struct.pack('<I', 0)
                                       + b'\x01' + EMPTY_KEYRING)

        ev = bytearray(EVENT_SIZE)
        struct.pack_into('<II', ev, 4, arrive, slot)
        ev[EV_CALLBACK] = EVENT_DELAYED_HIRING
        self._add_event(bytes(ev), arrive)

        self.dirty = True
        self._parse()

        # The gear goes in through the same path an edit takes, so a gun
        # arrives loaded and a magazine full, exactly as an edit would.
        # The new event moved everything after it, profiles included, so the
        # readers from before the insert point at the wrong bytes now.
        n = self._record(i)
        if n is None:
            raise Ja2Error('self-check failed: %s was written but cannot be '
                           'read back' % who)
        if gear:
            for k in range(ja2_soldiers.ITEM_COUNT):
                row = self.profiles.slot(i, k)
                if row['item']:
                    self.soldiers.set_slot(n, k, item=row['item'],
                                           count=max(1, row['count']),
                                           condition=row['condition'] or 100)
        self.soldiers.fix_checksum(n)
        return '%s hired for %d day%s, arriving in an hour%s' % (
            who, days, '' if days == 1 else 's',
            ' with their kit' if gear else ', without kit')

    def _team_entry(self, n):
        base = self.soldiers.bases[n]
        return next((s for s in self.layout.team() if s['base'] == base), None)

    def fire_refusal(self, i):
        """Why merc `i` cannot be sent home, or '' if they can."""
        n = self._record(i)
        if self.read_only or self.layout is None or n is None:
            return 'is not on the team'
        sol = self._team_entry(n)
        if sol is None:
            return 'could not be found in the file'
        if len(self.layout.team()) <= 1:
            return 'is the last merc on the team - the game needs at least one'
        S, b = self.soldiers, sol['base']
        if ja2_soldiers._i8(S._get(b, ja2_soldiers.OFF_HEALTH)) <= 0:
            return 'is dead, not on a contract'
        asg = ja2_soldiers._i8(S._get(b, ja2_layout.S_ASSIGNMENT))
        if asg in FIRE_REFUSED:
            return FIRE_REFUSED[asg] + ' - send them home in the game instead'
        if sum(S._get(b, S_FLAGS + k) << (8 * k)
               for k in range(4)) & SOLDIER_VEHICLE:
            return 'is a vehicle'
        return ''

    def _free_slot(self):
        h = self.header_len
        first, last = self.raw[h + TEAM_FIRST_ID], self.raw[h + TEAM_LAST_ID]
        used = {s['slot'] for s in self.layout.soldiers}
        return next((k for k in range(first, last + 1) if k not in used), None)

    def hire_refusal(self, i):
        """Why merc `i` cannot be hired into this save, or '' if they can."""
        if self.read_only or self.layout is None:
            return 'cannot be hired - this save cannot be edited'
        if i in self._att:
            return 'is already on the team'
        if i > LAST_MERC_SITE:
            return ('is not an A.I.M. or M.E.R.C. merc - other characters '
                    'join through the story')
        status = ja2_soldiers._i8(self.profiles.get(i, P_STATUS))
        if status not in (0, MERC_ANNOYED_CAN_CONTACT):
            return HIRE_REFUSED.get(
                status, 'is already under contract' if status > 0
                else 'cannot be hired right now (status %d)' % status)
        if len(self.layout.team()) >= MAX_TEAM:
            return 'cannot join - the team is full (the game allows %d)' \
                % MAX_TEAM
        if self._free_slot() is None:
            return 'cannot join - there is no free soldier slot'
        return ''

    def _hire_record(self, rec, i, slot, kind, days, now, arrive):
        """Turn a teammate's plain record into merc `i`'s, in place."""
        P = self.profiles
        S = ja2_soldiers

        def u8(off, v):
            rec[off] = v & 0xFF

        def u16(off, v):
            struct.pack_into('<H', rec, off, v & 0xFFFF)

        def u32(off, v):
            struct.pack_into('<I', rec, off, v & 0xFFFFFFFF)

        def text(off, raw, n):
            rec[off:off + n] = raw[:n].ljust(n, b'\0')

        # who
        u8(H_ID, slot)
        u8(S.OFF_PROFILE, i)
        nick = (P.nickname(i) or P.name(i))[:9].encode('utf-16-le')
        text(S.OFF_NICK, nick, S.LEN_NICK)
        for src, dst in PALETTES:
            text(dst, bytes(P.get(i, src + k) for k in range(30)), 30)
        body = P.get(i, P_BODY)
        u8(H_BODY, body)
        u32(H_ANIM_SUB, sum(P.get(i, P_ANIM_SUB + k) << (8 * k)
                            for k in range(4)))
        u8(H_CIV_GROUP, P.get(i, P_CIV_GROUP))
        u8(H_BATTLE_SOUND, 7 if body == REGFEMALE else 0)
        others = [struct.unpack_from('<I', bytes(
            self.soldiers._get(s['base'], H_UNIQUE + k) for k in range(4)))[0]
            for s in self.layout.soldiers]
        u32(H_UNIQUE, max(others + [0]) + 1)

        # what they are
        D = ja2_profiles.D
        for name, off in S.STATS.items():
            u8(off, P.get(i, D.STATS[name]))
        u8(S.OFF_LEVEL, P.get(i, D.OFF_LEVEL))
        u8(S.OFF_SKILL1, P.get(i, D.OFF_SKILL1))
        u8(S.OFF_SKILL2, P.get(i, D.OFF_SKILL2))
        life, top = P.get(i, D.OFF_HEALTH), P.get(i, D.OFF_MAX_HEALTH)
        u8(S.OFF_HEALTH, life)
        u8(S.OFF_MAX_HEALTH, top)
        u8(H_OLD_LIFE, top)
        u8(H_CAMO, 100 if CAMOUFLAGED in (P.get(i, D.OFF_SKILL1),
                                          P.get(i, D.OFF_SKILL2)) else 0)
        u8(S.OFF_ENERGY, 100)
        u8(S.OFF_MAX_ENERGY, 100)
        u8(S.OFF_MORALE, DEFAULT_MORALE)

        # nothing carried over from the teammate's day
        rec[S.FIRST_ITEM:S.FIRST_ITEM + S.ITEM_COUNT * S.ITEM_LEN] = \
            bytes(S.ITEM_COUNT * S.ITEM_LEN)
        for off, n in CLEAR_ON_HIRE:
            rec[off:off + n] = bytes(n)

        # where: nowhere yet - in transit, in no squad and no group
        u16(H_GRIDNO, NOWHERE)
        u8(S_IN_SECTOR, 0)
        u8(ja2_layout.S_GROUP, 0)
        u8(H_BETWEEN, 0)
        u8(H_VISIBLE, 1)
        u8(ja2_layout.S_ASSIGNMENT, IN_TRANSIT)
        u32(H_ASSIGN_CHANGED, now)
        u8(H_INSERT_CODE, INSERTION_ARRIVING)
        u16(H_INSERT_DATA, 0)
        u8(H_LANDING_ZONE, 1)
        u32(H_ARRIVES, arrive)

        # the contract, as HireMerc sets it; arrival restarts the clock
        u8(H_KIND, kind)
        u32(H_CONTRACT_LEN, days)
        u32(H_CONTRACT_END, (arrive // 1440 + 1 + days) * 1440 + 7 * 60)
        u32(H_CONTRACT_START, now // 1440 if kind == MERC_TYPE_MERC else 0)
        u8(H_CONTRACT_TYPE, CONTRACTS[days])
        u32(H_SIGN_ELSEWHERE, now if days < 14 else
            (arrive // 1440 + 1 + days) * 1440)
        u32(H_LAST_CONTRACT_UPDATE, arrive)
        u16(H_MEDICAL_DEPOSIT, P.word(i, P_MEDICAL_DEPOSIT)
            if kind == MERC_TYPE_AIM else 0)
        for off in (H_INSURANCE_START, H_INSURANCE_LEN, H_INSURANCE_TIME):
            u32(off, 0)

    def _add_event(self, rec, when):
        """Insert one strategic event, keeping the list in time order."""
        at = self.header_len + TACTICAL_STATUS + GAME_CLOCK
        n = struct.unpack_from('<I', self.raw, at)[0]
        k = 0
        while k < n and struct.unpack_from(
                '<I', self.raw, at + 4 + EVENT_SIZE * k + 4)[0] <= when:
            k += 1
        pos = at + 4 + EVENT_SIZE * k
        self.raw[pos:pos] = rec
        struct.pack_into('<I', self.raw, at, n + 1)

    def _drop_events(self, match):
        """Remove strategic events `match(callback, param)` picks. Count."""
        at = self.header_len + TACTICAL_STATUS + GAME_CLOCK
        n = struct.unpack_from('<I', self.raw, at)[0]
        keep = []
        for k in range(n):
            rec = bytes(self.raw[at + 4 + EVENT_SIZE * k:
                                 at + 4 + EVENT_SIZE * (k + 1)])
            if not match(rec[EV_CALLBACK],
                         struct.unpack_from('<I', rec, EV_PARAM)[0]):
                keep.append(rec)
        if len(keep) == n:
            return 0
        self.raw[at:at + 4 + EVENT_SIZE * n] = (
            struct.pack('<I', len(keep)) + b''.join(keep))
        return n - len(keep)

    def _do_stat(self, intent):
        i = self._merc_index(intent['merc'])
        stat = intent['stat']
        if stat not in STATS:
            # JA3 has stats JA2 does not. Skip rather than fail the whole batch.
            return None
        rec = self._record(i)
        before = (self.soldiers.field(rec, ja2_soldiers.STATS[stat])
                  if rec is not None else self.profiles.get(i, STATS[stat]))
        after = self.set_stat(i, stat, intent['value'])
        if before == after:
            return None
        return '%s %s %d -> %d' % (self.profiles.name(i), stat, before, after)

    def _do_ja2_attach(self, intent):
        """Fit, change or remove one attachment on a hired merc's weapon."""
        if not self.soldiers:
            return None
        i = self._merc_index(intent['merc'])
        att = self._att.get(i)
        if not att:
            return None          # not a hired merc, or we could not match them
        rec = att['record']
        slot = int(intent['slot'])
        k = int(intent['index'])
        before = self.soldiers.slot(rec, slot)
        after = self.soldiers.set_attachment(
            rec, slot, k,
            item=intent.get('item'), condition=intent.get('condition'))
        if after == before:
            return None
        self.dirty = True
        # attachments is four positional entries, so index k IS slot k
        was = (before['attachments'][k] or {}).get('name') or 'nothing'
        now = (after['attachments'][k] or {}).get('name') or 'nothing'
        return '%s %s: %s -> %s' % (self.profiles.name(i),
                                    after['item_name'], was, now)

    def _do_ja2_live_item(self, intent):
        """Change one slot of a hired merc's LIVE inventory.

        The counterpart of _do_ja2_item, which edits the loadout in the
        profile record. This one edits what the merc is actually carrying,
        which is what the game reads in the field - see mercs() for why the
        two are not the same thing.

        `ja2_live_item` runs BEFORE `ja2_attach` in PHASES on purpose. Putting
        a different item in a slot clears that slot's attachments, so an
        attachment queued in the same batch has to land afterwards or it would
        be wiped by an edit the user made first.
        """
        if not self.soldiers:
            return None
        i = self._merc_index(intent['merc'])
        att = self._att.get(i)
        if not att:
            # Not hired, or we could not match them to a record. Say nothing
            # rather than quietly writing this into the profile instead - an
            # edit that lands somewhere other than where it was aimed is the
            # failure this whole rewrite exists to remove.
            return None
        rec = att['record']
        slot = int(intent['slot'])
        S = self.soldiers
        before = S.slot(rec, slot)
        after = S.set_slot(
            rec, slot,
            item=intent.get('item'),
            count=intent.get('count'),
            condition=intent.get('condition'))
        # The extras ride on the same intent, after the item and count, so a
        # gun put in a hand and loaded in one go is loaded into the new gun.
        try:
            if intent.get('stack') is not None and after.get('stack'):
                after = S.set_stack(rec, slot, list(intent['stack']))
            if after.get('gun') and ('mag' in intent or 'rounds' in intent):
                after = S.set_gun_ammo(rec, slot, mag=intent.get('mag'),
                                       rounds=intent.get('rounds'))
            if 'money' in after and intent.get('money') is not None:
                after = S.set_money(rec, slot, intent['money'])
        except ValueError as e:
            raise Ja2Error(str(e))
        if after == before:
            return None
        self.dirty = True

        def said(r):
            unit = ' rds' if r['item'] in ja2_ammo.MAGAZINES else '%'
            return '%s x%d %d%s' % (r['item_name'] or 'nothing', r['count'],
                                    r['condition'], unit)
        line = '%s carrying: %s %s' % (self.profiles.name(i), after['name'],
                                       said(after))
        if said(before) != said(after):
            line = '%s carrying: %s %s -> %s' % (
                self.profiles.name(i), after['name'], said(before), said(after))
        g = after.get('gun')
        if g and g != before.get('gun'):
            line += ', loaded %d x %s' % (g['rounds'], g['mag_name'] or 'nothing')
        if after.get('stack') and after['stack'] != before.get('stack'):
            line += ', each %s' % '/'.join(str(v) for v in after['stack'])
        if 'money' in after and after['money'] != before.get('money'):
            line += ', $%d' % after['money']
        return line

    def _do_ja2_name(self, intent):
        """Rename a merc: full name and/or nickname.

        A hired merc's nickname is stored twice - the profile, and the copy in
        their soldier record the game shows in the field - so both are written.
        """
        i = self._merc_index(intent['merc'])
        D = ja2_profiles.D
        P = self.profiles
        done = []
        for key, off, length, read in (
                ('name', D.OFF_NAME, D.LEN_NAME, P.name),
                ('nick', D.OFF_NICK, D.LEN_NICK, P.nickname)):
            want = intent.get(key)
            if want is None:
                continue
            want = str(want).strip()
            if not want:
                raise Ja2Error('a %s cannot be blank'
                               % ('name' if key == 'name' else 'nickname'))
            want = want[:length // 2 - 1]
            was = read(i)
            if was == want:
                continue
            P.set_text(i, off, length, want)
            if key == 'nick':
                rec = self._record(i)
                if rec is not None:
                    self.soldiers.set_nickname(rec, want)
            done.append('%s %r -> %r' % (key, was, want))
        if not done:
            return None
        self.dirty = True
        return '%s renamed: %s' % (P.name(i), ', '.join(done))

    def _do_ja2_item(self, intent):
        i = self._merc_index(intent['merc'])
        slot = int(intent['slot'])
        before = self.profiles.slot(i, slot)
        after = self.profiles.set_slot(
            i, slot,
            item=intent.get('item'),
            count=intent.get('count'),
            condition=intent.get('condition'))
        if after == before:
            return None
        self.dirty = True
        who = '%s %s' % (self.profiles.name(i), after['name'].lower())
        if not after['item']:
            return '%s emptied' % who
        if not before['item']:
            return '%s <- item %d x%d at %d%%' % (who, after['item'],
                                                  after['count'], after['condition'])
        return '%s item %d x%d %d%% -> %d x%d %d%%' % (
            who, before['item'], before['count'], before['condition'],
            after['item'], after['count'], after['condition'])

    # JA3 field name -> where it lives in a JA2 profile record. Experience is
    # deliberately absent: JA3 stores experience points and derives the level,
    # JA2 stores the level itself, so there is nothing honest to map it to.
    MERC_FIELDS = dict(
        {
            'HitPoints': ja2_profiles.D.OFF_HEALTH,
            'MaxHitPoints': ja2_profiles.D.OFF_MAX_HEALTH,
            'Level': ja2_profiles.D.OFF_LEVEL,
            'Skill1': ja2_profiles.D.OFF_SKILL1,
            'Skill2': ja2_profiles.D.OFF_SKILL2,
            'Personality': ja2_profiles.D.OFF_PERSONALITY,
        },
        # the "(+ bonus)" box beside each stat
        **{'Gain' + k: v for k, v in ja2_profiles.D.GAINS.items()}
    )

    # Career counters are uint16, so they take a different write.
    CAREER_FIELDS = {'Career' + k: v for k, v in ja2_profiles.D.CAREER.items()}

    # Fields the profile also holds, and where the hired merc's own copy is.
    LIVE_COPIES = {
        'HitPoints': ja2_soldiers.OFF_HEALTH,
        'MaxHitPoints': ja2_soldiers.OFF_MAX_HEALTH,
        'Level': ja2_soldiers.OFF_LEVEL,
        'Skill1': ja2_soldiers.OFF_SKILL1,
        'Skill2': ja2_soldiers.OFF_SKILL2,
    }
    # Fields only a hired merc has: (offset, highest value the game uses)
    LIVE_ONLY = {
        'Energy': (ja2_soldiers.OFF_ENERGY, 100),
        'MaxEnergy': (ja2_soldiers.OFF_MAX_ENERGY, 100),
        'Morale': (ja2_soldiers.OFF_MORALE, 100),
    }

    def _do_merc_field(self, intent):
        field = intent['field']
        i = self._merc_index(intent['merc'])
        only = self.LIVE_ONLY.get(field)
        if only is not None:
            rec = self._record(i)
            if rec is None:
                return None          # not hired: there is no such field
            off, top = only
            before = self.soldiers.field(rec, off)
            want = max(0, min(top, int(intent['value'])))
            if field == 'Energy':
                want = min(want, self.soldiers.field(
                    rec, ja2_soldiers.OFF_MAX_ENERGY))
            if before == want:
                return None
            self.soldiers.set_field(rec, off, want, top)
            if (field == 'MaxEnergy' and self.soldiers.field(
                    rec, ja2_soldiers.OFF_ENERGY) > want):
                self.soldiers.set_field(rec, ja2_soldiers.OFF_ENERGY, want, top)
            self.dirty = True
            return '%s %s %d -> %d' % (self.profiles.name(i), field.lower(),
                                       before, want)
        wide = self.CAREER_FIELDS.get(field)
        if wide is not None:
            before = self.profiles.word(i, wide)
            want = max(0, min(65535, int(intent['value'])))
            if before == want:
                return None
            self.profiles.put_word(i, wide, want)
            self.dirty = True
            return '%s %s %d -> %d' % (self.profiles.name(i),
                                       field[6:].lower(), before, want)
        off = self.MERC_FIELDS.get(field)
        if off is None:
            return None          # a JA3-only field; skip, do not fail the batch
        before = self.profiles.get(i, off)
        want = max(0, min(255, int(intent['value'])))
        live = self.LIVE_COPIES.get(field)
        rec = self._record(i) if live is not None else None
        if rec is not None:
            before = self.soldiers.field(rec, live)
        if before == want and (rec is None
                               or self.profiles.get(i, off) == want):
            return None
        self.profiles.put(i, off, want)
        if rec is not None:
            self.soldiers.set_field(rec, live, want, 255)
        self.dirty = True
        return '%s %s %d -> %d' % (self.profiles.name(i), intent['field'],
                                   before, want)

    def write(self, out_path=None, backup=True):
        """Write the save out, keeping a timestamped copy of what was there.

        Same return shape as the JA3 writer, and the same refusal to hand back
        a file we cannot read: the bytes are re-parsed and every stat compared
        against what we meant to write before the temp file is moved into
        place. A save that fails that check never reaches the campaign folder.
        """
        if self.read_only:
            raise Ja2Error(self.blocked_because)
        out_path = out_path or self.path
        data = bytes(self.raw)

        # The game picks the key from header fields; if any of them moved, it
        # would read every merc in this file with the wrong one.
        if key_set(data, self._german) != self.key_set:
            raise Ja2Error('self-check failed: this edit changes the header '
                           'fields the game derives the merc key from')
        check = Save.__new__(Save)
        check.raw = bytearray(data)
        check.profiles = ja2_profiles.Profiles(
            check.raw, self.profile_start, self.key)
        for i in self.profiles.named():
            if check.profiles.stats(i) != self.profiles.stats(i):
                raise Ja2Error('self-check failed: %s does not read back the '
                               'way it was written' % self.profiles.name(i))

        # And the same question the editor asks when OPENING a save, asked
        # again here: could the game have written what we are about to save?
        #
        # This is JAPE's habit and it is a good one - it validates before it
        # edits. Ours is placed later and is the better half of the same idea:
        # check the RESULT of a batch, not just its inputs, because a value
        # each setter accepts on its own can still be impossible in
        # combination. The self-check above only ever compared stats, so an
        # edit that produced a stack over the game's pocket limit, or a scope
        # left on an empty hand, was written and only reported the NEXT time
        # the file was opened.
        #
        # Only NEW problems refuse. A save that already had an oddity in it
        # stays editable - refusing there would hold someone's campaign hostage
        # over something we did not do, which is the opposite of useful.
        try:
            after = set(self.implausible())
        except Exception:                                         # noqa: BLE001
            after = set()          # never let the guard be the thing that fails
        fresh = sorted(after - set(self._opened_implausible))
        if fresh:
            raise Ja2Error(
                'refusing to write: this edit produces %d thing%s the game '
                'could not have written, so it would very likely be rejected '
                'or misread. %s'
                % (len(fresh), '' if len(fresh) == 1 else 's',
                   ' '.join(fresh[:3])))

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
        self.dirty = False
        return {'path': out_path, 'backup': bak, 'bytes': len(data)}

    # -- header fields ---------------------------------------------------

    @property
    def name(self):
        end = NAME_OFFSET + NAME_CHARS * 2
        return _text16(self.raw[NAME_OFFSET:end])

    @property
    def day(self):
        return struct.unpack_from('<I', self.raw, OFF_DAY)[0]

    @property
    def hour(self):
        return self.raw[OFF_HOUR]

    @property
    def minute(self):
        return self.raw[OFF_MIN]

    @property
    def balance(self):
        if self.laptop_start is None:
            return struct.unpack_from('<i', self.raw, OFF_BALANCE)[0]
        return struct.unpack_from(
            '<i', self.raw, self.laptop_start + LAPTOP_BALANCE)[0]

    @property
    def is_113(self):
        """1.13 bumped the save version to 103 and grew the header."""
        return self.version >= 103

    def clock(self):
        return '%02d:%02d' % (self.hour, self.minute)

    @property
    def _opened_implausible(self):
        """What was already impossible in this save before we touched it.

        Captured lazily and cached, so write() can tell a problem IT created
        from one that was already in the file. Without that distinction the
        guard would refuse to save a campaign over an oddity nobody here
        introduced.
        """
        if getattr(self, '_baseline', None) is None:
            try:
                self._baseline = list(self.implausible())
            except Exception:                                     # noqa: BLE001
                self._baseline = []
        return self._baseline

    def implausible(self):
        """Things in this save the GAME could not have written.

        The diagnostics log was built to catch failures - a crash, a refused
        request, a command that claimed success and did nothing. Every fault
        found on 2026-09-04 slipped past all of that, because none of them
        failed. The code ran, the write succeeded, the file changed. The data
        was simply wrong, and wrong data raises nothing.

        So this asks a different question: could the game have produced this?
        It needs no known-good save to compare against, which is the point -
        it works on a file nobody has ever looked at, including the tester's.

        Each check below is here because it would have caught a real bug:

          * an item id that is not in the game    -> keystream offset 408,
            which read every hired merc's small pocket as item 25771
          * a stack over the game's own limit     -> keystream offset 230,
            which read 102 sticks of TNT in a slot that holds one
          * a condition over 100                  -> the same class of fault
            on a field nothing here exercises yet
          * an attachment on nothing              -> a slot cleared without
            its attachments being cleared with it

        Returns a list of plain sentences. Empty is the normal answer.
        """
        out = []
        if not self._ok:
            return out
        for m in self.mercs():
            who = m['nick'] or m['name']
            for which in ('inventory', 'aim_inventory'):
                for r in m.get(which, []) or []:
                    item = r['item']
                    if item and item not in ja2_items.ITEMS:
                        out.append('%s has item %d in %s, which is not an item '
                                   'in this game.' % (who, item, r['name']))
                        continue
                    if not item:
                        fitted = [a for a in (r.get('attachments') or []) if a]
                        if fitted:
                            out.append('%s has %s fitted to an empty %s.'
                                       % (who, fitted[0]['name'], r['name']))
                        continue
                    limit = ja2_stats.stack(item)
                    if limit and r['count'] > limit:
                        out.append('%s is carrying %d %s in one %s; the game '
                                   'allows %d.' % (who, r['count'],
                                                   r['item_name'], r['name'],
                                                   limit))
                    # Ammunition keeps its round count in the condition field,
                    # so it is legitimately above 100 and is not checked here.
                    if (r['condition'] > 100
                            and ja2_items.category(item) != 'Ammo'):
                        out.append('%s has a %s at %d%% in their %s.'
                                   % (who, r['item_name'], r['condition'],
                                      r['name']))
                    for a in (r.get('attachments') or []):
                        if a and a['id'] not in ja2_items.ITEMS:
                            out.append('%s has attachment %d on their %s, '
                                       'which is not an item in this game.'
                                       % (who, a['id'], r['item_name']))
                    g = r.get('gun')
                    if g and g['mag'] and g['mag'] not in [f['id'] for f in g['fits']]:
                        out.append('%s has %s loaded in a %s, which it does '
                                   'not fit.' % (who, g['mag_name'] or
                                                 'item %d' % g['mag'],
                                                 r['item_name']))
                    if g and g['rounds'] > g['capacity']:
                        out.append('%s has %d rounds in a %s that holds %d.'
                                   % (who, g['rounds'], r['item_name'],
                                      g['capacity']))
                    cap = r.get('capacity')
                    if cap and any(v > cap for v in r.get('stack') or []):
                        out.append('%s has a %s holding more than its %d '
                                   'rounds.' % (who, r['item_name'], cap))
                    if r.get('money', 0) > ja2_soldiers.MAX_MONEY:
                        out.append('%s has $%d in one %s; the game caps a '
                                   'pocket at $%d.' % (who, r['money'],
                                                       r['name'],
                                                       ja2_soldiers.MAX_MONEY))
        return out

    def _warnings(self):
        """What the user should know before touching this save.

        Says what IS understood as well as what is not, because "some fields
        are missing" reads as "this may scramble my campaign" unless the
        boundary is drawn explicitly.
        """
        out = []
        if self.blocked_because:
            out.append('This save is read only: ' + self.blocked_because + '.')
            return out
        out.append(
            'This Jagged Alliance 2 save can be edited: names and nicknames, '
            'the nine stats, money, health, experience level, both skill '
            'traits, the trained bonuses and the career counters - and for a '
            'hired merc also energy, morale and everything they carry: '
            'attachments, the magazine loaded in a gun, each magazine in a '
            'stack, and cash. A hired merc keeps a second copy of their stats '
            'that the game plays with, and both are written. Mercs can be '
            'hired from A.I.M. or M.E.R.C. (they land by helicopter an hour '
            'after loading) and sent home, both the way the game does it. '
            'Everything is '
            'read straight out of the file and written back the way the game '
            'writes it, checksums included.')

        # Anything the game could not have produced. Loud, because this is the
        # class of fault that used to sit in the file for days looking normal.
        odd = self.implausible()
        if odd:
            out.append('%d thing%s in this save could not have been written by '
                       'the game, which usually means this editor is decoding '
                       'a field wrongly. Please report %s: %s'
                       % (len(odd), '' if len(odd) == 1 else 's',
                          'it' if len(odd) == 1 else 'them',
                          ' '.join(odd[:3])))
        return out

    def summary(self):
        """The same shape the JA3 half returns, so the UI needs no special case.

        Collections come back empty rather than absent. The UI already hides
        sections with nothing in them, so an empty list is the honest way to
        say "this game has none of these yet" without a single `if game ==`
        anywhere in the front end.
        """
        return {
            'supports': self.SUPPORTS,
            # the item list, once per save rather than once per merc
            # weight and price come out of the executable's item table -
            # see build_ja2_items.py. A few rows there were never filled in,
            # so a missing value is left as None rather than shown as zero.
            'items': [{'id': i, 'name': v[0],
                       'cat': ja2_items.CATEGORIES[v[1]],
                       'weight': ja2_stats.weight(i),
                       'price': ja2_stats.price(i),
                       'stack': ja2_stats.stack(i),
                       'capacity': (ja2_ammo.MAGAZINES[i][1]
                                    if i in ja2_ammo.MAGAZINES else None)}
                      for i, v in sorted(ja2_items.ITEMS.items()) if i],
            'skill_list': list(ja2_profiles.D.SKILLS),
            'game': 'ja2',
            'path': self.path,
            'file': os.path.basename(self.path),
            'read_only': self.read_only,
            'meta': {
                'displayname': self.name,
                'playthrough_name': self.name,
                'campaign': 'Arulco',
                'game_date': 'Day %d, %s' % (self.day, self.clock()),
                'campaign_time': None,
                'map': None,
                'difficulty': None,
                'daily_income': None,
                'money': self.balance,
                'playtime': None,
                'gameid': None,
                'lua_revision': None,
                'mods': [],
                # JA2 specific, and harmless to the JA3 views
                'build': self.build,
                'save_version': self.version,
                'is_113': self.is_113,
                'day': self.day,
                'clock': self.clock(),
                # `mercs` is every character in the game here, not the team
                'team': len(self._att),
            },
            'money': self.balance,
            'mercs': self.mercs(),
            'roster': [],
            'sectors': [],
            'squads': [],
            'squad_bag': [],
            'stash': {},
            'cheats': {'flags': {}, 'sides': {}, 'sides_seen': []},
            'free_imp_slots': [],
            'required_mods': [],
            'warnings': self._warnings(),
        }


def read(path):
    return Save(path)
