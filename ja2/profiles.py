"""Reading and writing the merc profiles inside a Jagged Alliance 2 savegame.

The records are enciphered. See profile_data.py for what the scheme is and how
the key was recovered; the short version is

    cipher[i] = plain[i] + cipher[i-1] + key[i]

with each 716 byte record its own block, so cipher[-1] is 0 at a record start.

Two consequences shape this module:

**Reading one byte is cheap.** Because only the immediately preceding cipher
byte feeds the sum, a single field decodes without walking the record:

    plain[i] = cipher[i] - cipher[i-1] - key[i]

**Writing one byte is not local.** cipher[i] feeds cipher[i+1], which feeds
cipher[i+2], so changing a value by N adds N to every byte after it, to the end
of that record. That is not a workaround - it is what the game's own writer
does, and a save edited this way is byte for byte what JA2 would have written.
That was checked rather than assumed: rebuilding two savegames JAPE had
produced, from the file before each edit, reproduced them exactly.
"""
from . import item_data
from . import profile_data as D


class Ja2ProfileError(Exception):
    pass


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Profiles:
    """The profile array of one savegame, addressed by index.

    Holds a reference to the save's mutable buffer rather than a copy, so
    writes here are writes to the save.
    """

    def __init__(self, buf, base, key=None):
        self.buf = buf                  # bytearray of the whole file
        self.base = base                # file offset of profile 0
        self.key = key or D.KEYSTREAM   # this save's key set, repeated

    # -- geometry --------------------------------------------------------

    def record(self, index):
        if not 0 <= index < D.PROFILE_COUNT:
            raise Ja2ProfileError('profile %d is outside 0..%d'
                                  % (index, D.PROFILE_COUNT - 1))
        return self.base + index * D.RECORD_SIZE

    def fits(self):
        """Is there room for the whole array? A truncated save is not ours."""
        end = self.base + D.PROFILE_COUNT * D.RECORD_SIZE
        return 0 <= self.base and end <= len(self.buf)

    # -- one byte in, one byte out ---------------------------------------

    def get(self, index, off):
        rec = self.record(index)
        # at a block start there is no previous cipher byte, so it counts as 0
        prev = 0 if off == 0 else self.buf[rec + off - 1]
        return (self.buf[rec + off] - prev - self.key[off]) % 256

    def put(self, index, off, value):
        """Set one byte, carrying the change to the end of the record."""
        delta = (int(value) - self.get(index, off)) % 256
        if not delta:
            return False
        rec = self.record(index)
        for i in range(rec + off, rec + D.RECORD_SIZE):
            self.buf[i] = (self.buf[i] + delta) % 256
        return True

    def plain(self, index):
        """The whole record decoded, for inspection."""
        rec = self.record(index)
        out = bytearray(D.RECORD_SIZE)
        prev = 0
        for i in range(D.RECORD_SIZE):
            c = self.buf[rec + i]
            out[i] = (c - prev - self.key[i]) % 256
            prev = c
        return bytes(out)

    # -- fields ----------------------------------------------------------

    def text(self, index, off, length):
        raw = bytes(self.get(index, off + i) for i in range(length))
        return raw.decode('utf-16-le', 'replace').split('\0', 1)[0].strip()

    def name(self, index):
        """The merc's name, preferring the save in case a player renamed one."""
        got = self.text(index, D.OFF_NAME, D.LEN_NAME)
        if got:
            return got
        if index < len(D.NAMES) and D.NAMES[index]:
            return D.NAMES[index]
        return 'Profile %d' % index

    def nickname(self, index):
        got = self.text(index, D.OFF_NICK, D.LEN_NICK)
        if got:
            return got
        return D.NICKNAMES[index] if index < len(D.NICKNAMES) else ''

    def stats(self, index):
        return {name: self.get(index, off) for name, off in D.STATS.items()}

    def set_text(self, index, off, length, text):
        from .soldiers import _put_text
        _put_text(_Rec(self, index), 0, off, length, text)

    def checksum(self, index):
        """What the game's ActorChecksum gives for this record as it stands.

        Transcribed from JAPE, which took it from the game's disassembly, and
        checked: it reproduces the stored value on every profile in the saves
        here that this editor had not touched.
        """
        g = lambda off: self.get(index, off)
        sx = lambda off: g(off) - 256 if g(off) > 127 else g(off)
        i32 = lambda x: ((x & 0xFFFFFFFF) ^ 0x80000000) - 0x80000000
        st = D.STATS
        a = i32((sx(D.OFF_MAX_HEALTH) + 1) * (sx(D.OFF_HEALTH) + 2))
        a = i32(a + sx(st['Agility']) + 1)
        a = i32(a * (sx(st['Dexterity']) + 1))
        a = i32(a + sx(st['Strength']) + 1)
        a = i32(a * (sx(st['Marksmanship']) + 1))
        a = i32(a + sx(st['Medical']) + 1)
        a = i32(a * (sx(st['Mechanical']) + 1))
        a = i32(a + sx(st['Explosives']) + 1)
        a = i32(a * (sx(D.OFF_LEVEL) + 1))
        for k in range(D.INV_SLOTS):
            a = i32(i32(self.word(index, D.OFF_INV_ITEM + 2 * k) + a)
                    + g(D.OFF_INV_NUMBER + k))
        return a

    def stored_checksum(self, index):
        v = sum(self.get(index, D.OFF_CHECKSUM + k) << (8 * k) for k in range(4))
        return ((v & 0xFFFFFFFF) ^ 0x80000000) - 0x80000000

    def fix_checksum(self, index):
        want = self.checksum(index) & 0xFFFFFFFF
        moved = False
        for k in (3, 2, 1, 0):
            moved |= self.put(index, D.OFF_CHECKSUM + k, (want >> (8 * k)) & 0xFF)
        return moved

    def word(self, index, off):
        """A uint16 read out of the enciphered record, low byte first."""
        return self.get(index, off) | (self.get(index, off + 1) << 8)

    def put_word(self, index, off, value):
        """Write a uint16. Low byte first, and low byte LAST.

        Order matters. Writing the low byte first would carry its delta
        through the high byte as well, so the high byte would then be written
        against a value that had already moved. Doing the high byte first
        leaves the low byte to absorb only its own change.
        """
        value = int(value) & 0xFFFF
        self.put(index, off + 1, (value >> 8) & 0xFF)
        self.put(index, off, value & 0xFF)

    # -- inventory -------------------------------------------------------
    #
    # Three parallel arrays that are NOT next to each other; see profile_data.
    # A slot is either empty in all three or filled in all three - checked
    # across every profile in the game, 3230 slots, no exceptions.

    def slot(self, index, i):
        if not 0 <= i < D.INV_SLOTS:
            raise Ja2ProfileError('slot %d is outside 0..%d'
                                  % (i, D.INV_SLOTS - 1))
        item = self.word(index, D.OFF_INV_ITEM + i * 2)
        count = self.get(index, D.OFF_INV_NUMBER + i)
        # A slot holding an item id with a quantity of ZERO is empty - JAPE
        # shows it as None, and we were drawing a phantom Glock 17 in Shadow's
        # left hand and counting it in "5 of 19 slots used". It also explains
        # the impossible item ids seen in some pack slots: stale bytes left in
        # a slot the game has emptied.
        if not count:
            item = 0
        return {
            'slot': i,
            'name': D.SLOT_NAMES[i],
            'item': item,
            'item_name': item_data.name(item) if item else '',
            'category': item_data.category(item) if item else '',
            'count': count,
            'condition': self.get(index, D.OFF_INV_STATUS + i),
            'empty': item == 0,
        }

    def inventory(self, index):
        return [self.slot(index, i) for i in range(D.INV_SLOTS)]

    def set_slot(self, index, i, item=None, count=None, condition=None):
        """Change one inventory slot. Returns what it looks like afterwards.

        Clearing a slot clears all three arrays, because a stray count or
        condition on an empty slot is a state the game never writes and this
        editor should not invent.
        """
        if not 0 <= i < D.INV_SLOTS:
            raise Ja2ProfileError('slot %d is outside 0..%d'
                                  % (i, D.INV_SLOTS - 1))
        if item is not None:
            item = int(item) & 0xFFFF
            was = self.word(index, D.OFF_INV_ITEM + i * 2)
            self.put_word(index, D.OFF_INV_ITEM + i * 2, item)
            if item == 0:
                self.put(index, D.OFF_INV_NUMBER + i, 0)
                self.put(index, D.OFF_INV_STATUS + i, 0)
                return self.slot(index, i)
            # a slot that just gained an item needs a sane count and condition
            if count is None and not self.get(index, D.OFF_INV_NUMBER + i):
                count = 1
            # A DIFFERENT item arrives new. Carrying the old condition
            # across is how a brand new Colt .45 turned up at 92%, inheriting
            # the wear on the MP5K it replaced. Only a condition the caller
            # actually asked for survives a swap.
            if condition is None and (
                    item != was
                    or not self.get(index, D.OFF_INV_STATUS + i)):
                condition = 100
        if count is not None:
            self.put(index, D.OFF_INV_NUMBER + i, _clamp(int(count), 0, 255))
        if condition is not None:
            self.put(index, D.OFF_INV_STATUS + i, _clamp(int(condition), 0, 100))
        return self.slot(index, i)

    # -- the fields that came out of JAPE constants -----------------------

    def condition(self, index):
        """Health, level and the two skills."""
        return {
            'health': self.get(index, D.OFF_HEALTH),
            'max_health': self.get(index, D.OFF_MAX_HEALTH),
            'level': self.get(index, D.OFF_LEVEL),
            'personality': self.get(index, D.OFF_PERSONALITY),
            'skill1': self.get(index, D.OFF_SKILL1),
            'skill2': self.get(index, D.OFF_SKILL2),
            'skill1_name': D.skill_name(self.get(index, D.OFF_SKILL1)),
            'skill2_name': D.skill_name(self.get(index, D.OFF_SKILL2)),
        }

    def gains(self, index):
        """The "(+ bonus)" box beside each stat - what a merc trained up."""
        return {k: self.get(index, off) for k, off in D.GAINS.items()}

    def career(self, index):
        """Kills, assists, shots, battles, wounds. JA3 has no equivalent."""
        return {k: self.word(index, off) for k, off in D.CAREER.items()}

    def set_stat(self, index, stat, value):
        if stat not in D.STATS:
            raise Ja2ProfileError('%r is not a stat this editor knows (%s)'
                                  % (stat, ', '.join(sorted(D.STATS))))
        return self.put(index, D.STATS[stat], _clamp(int(value), 0, 100))

    # -- listing ---------------------------------------------------------

    def named(self):
        """Indexes of profiles that are actually somebody.

        15 of the 170 slots are blank padding in Prof.dat and are not worth
        showing anyone.
        """
        return [i for i in range(D.PROFILE_COUNT) if self.name(i)
                and not self.name(i).startswith('Profile ')]

    def entry(self, index):
        """One merc in the shape the front end already renders for JA3."""
        st = self.stats(index)
        nick = self.nickname(index)
        name = self.name(index)
        cond = self.condition(index)
        return {
            'id': 'p%d' % index,
            'index': index,
            'name': name,
            'nick': nick or name,
            'preset': 'p%d' % index,
            'spec': '',
            'level': cond['level'] or None,
            'hp': cond['health'],
            'max_hp': cond['max_health'] or 100,
            # JA3 drives level from experience points; JA2 stores the level
            # itself, so there is no xp value to give and the UI leaves the
            # experience box out rather than showing one that writes nowhere.
            'xp': None,
            'wounds': 0,
            'is_imp': False,
            'hire_status': '',
            'perks': [],
            # No inventory reader for JA2 yet. An empty mapping is the honest
            # answer and keeps the gear section from being handed a null;
            # the UI hides the section when there are no slots at all.
            'slots': {},
            'stats': {k: {'value': v} for k, v in st.items()},
            # JA2 only, for the panel that renders them
            'inventory': self.inventory(index),
            'gains': self.gains(index),
            'career': self.career(index),
            'skills': [cond['skill1'], cond['skill2']],
            'skill_names': [cond['skill1_name'], cond['skill2_name']],
        }


class _Rec:
    """Lets the shared text writer address one profile record."""

    def __init__(self, profiles, index):
        self.p, self.i = profiles, index

    def _put(self, _base, off, value):
        return self.p.put(self.i, off, value)


def sanity(profiles):
    """Does this look like a real profile array?

    A stat outside 0..100 is impossible in JA2, so if the array is where we
    think it is every named merc passes. Used before trusting a save enough to
    offer editing, because the alternative is writing into the middle of
    something else.
    """
    if not profiles.fits():
        return False, 'the file is too short to hold 170 profile records'
    bad = 0
    checked = 0
    for i in range(D.PROFILE_COUNT):
        for v in profiles.stats(i).values():
            checked += 1
            if not 0 <= v <= 100:
                bad += 1
    if bad:
        return False, ('%d of %d stat values are outside 0..100, so the '
                       'profile array is not where this build puts it'
                       % (bad, checked))
    return True, '%d stat values all within range' % checked
