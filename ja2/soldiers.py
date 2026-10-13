"""The hired mercs' records - SOLDIERTYPE - inside a Jagged Alliance 2 save.

A profile record says what a merc *is*. This says what they are carrying right
now, in detail the profile has no room for: which attachments are fitted to
which gun, and at what condition. the tester's report was "I put a scope on Shadow
and the editor has nowhere to show it" - this is the thing that was missing.

**It is enciphered with the same key.** Not a similar one - the same array.
Energy at 711 and max energy at 712 decode to 100/100 for a merc JAPE agrees is
at 100/100, using the keystream recovered from Prof.dat, and the two
neighbouring block bases produce garbage. The key does NOT repeat every 716
bytes - it repeats every 49 (see profile_data.KEY_TABLE), so the whole 2328
byte record decodes: nickname, morale, the merc's live stats, the profile
number at 1825 and the game's checksum at 2208.

Where the records are was found by measurement, not arithmetic: the array
begins immediately after the profile array, and the stride is **2462**, which
is 134 bytes more than the 2328 of ciphertext. What those 134 bytes hold is not
known, and nothing here touches them.

**The slot numbers here are the same nineteen positions as the profile's.**
An earlier note in this file said the two records "number their slots
differently" and that was a wrong inference from a real observation: the two
records often hold different things in the same slot. They are not disagreeing
about numbering, they are disagreeing about the world - this record is the live
inventory and the profile is the loadout. Checked on a real save: for all three
hired mercs, slots 0, 1 and 5 carry the identical item at the identical
condition in both, while the pack contents differ exactly as play would make
them differ.
"""
from . import ammo
from . import item_data
from . import profile_data as D


def _i8(b):
    return b - 256 if b > 127 else b


def _i32(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x & 0x80000000 else x


def _put_text(rec, base, off, length, text):
    """Write a NUL-padded UTF-16LE field through `rec._put`. Shared by both
    record types, which is why it takes the record object."""
    chars = length // 2 - 1          # always leave room for the terminator
    raw = str(text).strip()[:chars].encode('utf-16-le')
    raw = raw.ljust(length, b'\0')
    for i in range(length):
        rec._put(base, off + i, raw[i])

# from Jape.jar, Mercenary.class
CIPHERTEXT = 2328
FIRST_ITEM = 12
ITEM_LEN = 36
ITEM_COUNT = 19
RECORD_STRIDE = 2462          # measured; 2328 of ciphertext plus 134 unknown

OFF_ENERGY = 711
OFF_MAX_ENERGY = 712
OFF_NICK = 730                # CHAR16[10], the name shown in the field
LEN_NICK = 20
OFF_MORALE = 1736
OFF_PROFILE = 1825            # which profile record this soldier is
OFF_CHECKSUM = 2208           # int32, see checksum()

# A hired merc carries a second copy of everything below, and it is this copy
# the game plays with. Offsets from JAPE's Mercenary class; checked on a real
# save, where all fourteen read the same as the merc's profile for every hired
# merc. Named to match profile_data.STATS so one stat edit can write both.
STATS = {
    'Dexterity':    840,
    'Wisdom':       841,
    'Agility':      880,
    'Strength':     886,
    'Leadership':   895,
    'Mechanical':   916,
    'Medical':      1372,
    'Marksmanship': 1377,
    'Explosives':   1378,
}
OFF_SKILL1 = 832
OFF_SKILL2 = 833
OFF_LEVEL = 849
OFF_HEALTH = 868
OFF_MAX_HEALTH = 917

# inside one 36-byte item (OBJECTTYPE; the layout is pinned by a static_assert
# in the game's own source, Item_Types.h)
IT_ID = 0                     # uint16
IT_COUNT = 2
IT_STATUS = 4                 # the condition of the first object in the stack
IT_GUN_AMMO_TYPE = 5          # guns: which kind of round is loaded
IT_GUN_SHOTS = 6              # guns: rounds left in the gun
IT_GUN_AMMO_ITEM = 8          # guns: uint16, the magazine those rounds came from
IT_MONEY = 8                  # cash: uint32 amount (same bytes as the ammo item)
IT_ATTACH = 16                # four uint16
IT_ATTACH_STATUS = 24         # four uint8
MAX_OBJECTS = 8               # a stack keeps one condition per object, 4..11
MAX_MONEY = 20000             # the game's MAX_MONEY_PER_SLOT

SLOT_NAMES = (
    'Helmet', 'Body armour', 'Leg armour', 'Headgear 1', 'Headgear 2',
    'Right hand', 'Left hand',
    'Pack 1/3', 'Pack 2/3', 'Pack 3/3', 'Pack 4/3',
    'Pack 1/1', 'Pack 2/1', 'Pack 3/1', 'Pack 4/1',
    'Pack 1/2', 'Pack 2/2', 'Pack 3/2', 'Pack 4/2',
)


class Soldiers:
    """The hired-merc records of one save, if they can be found."""

    def __init__(self, buf, profile_start, profile_size, profile_count,
                 key=None, bases=None):
        self.buf = buf
        self.key = key or D.KEYSTREAM
        self.first = profile_start + profile_size * profile_count
        self.bases = list(bases) if bases is not None else self._find()

    # -- reading one byte ------------------------------------------------

    def _get(self, base, off):
        i = base + off
        prev = 0 if off == 0 else self.buf[i - 1]
        return (self.buf[i] - prev - self.key[off]) % 256

    def _word(self, base, off):
        return self._get(base, off) | (self._get(base, off + 1) << 8)

    # -- finding the records ---------------------------------------------

    def _plausible(self, base):
        """Does the item array at this base read like real gear?

        Kept tolerant, but no longer for the reason it was written. The "one
        pack slot always decodes to an item that does not exist" it was built
        around was a keystream error at offset 408, and it is fixed - every id
        in every record of every save here now resolves. The tolerance stays
        because these are the only saves anyone has checked, and a record that
        is 17 slots right is still obviously a record.
        """
        if base < 0 or base + CIPHERTEXT > len(self.buf):
            return False
        used = bad = 0
        for s in range(ITEM_COUNT):
            item = self._word(base, FIRST_ITEM + s * ITEM_LEN)
            if not item:
                continue
            used += 1
            if item not in item_data.ITEMS:
                bad += 1
        return used >= 3 and bad <= 2 and bad < used

    def _find(self):
        """Walk the stride from the end of the profiles until it stops making
        sense. The count is not stored anywhere we know, so the data decides."""
        out = []
        base = self.first + 1        # the ciphertext starts one byte in
        while self._plausible(base):
            out.append(base)
            base += RECORD_STRIDE
        return out

    # -- writing ---------------------------------------------------------

    def _put(self, base, off, value):
        """Set one byte, carrying the change to the end of THIS block.

        The cascade runs to the end of the 2328-byte ciphertext, not to 716 -
        a soldier record is a bigger block than a profile record, and stopping
        early would leave the rest of it decoding to nonsense.
        """
        delta = (int(value) - self._get(base, off)) % 256
        if not delta:
            return False
        for i in range(base + off, base + CIPHERTEXT):
            self.buf[i] = (self.buf[i] + delta) % 256
        return True

    def _put_word(self, base, off, value):
        """uint16, high byte first - see Profiles.put_word for why the order
        matters under a cipher that chains forward."""
        value = int(value) & 0xFFFF
        self._put(base, off + 1, (value >> 8) & 0xFF)
        self._put(base, off, value & 0xFF)

    def set_slot(self, index, s, item=None, count=None, condition=None):
        """Change one slot of a hired merc's LIVE inventory.

        Same shape as Profiles.set_slot and for the same reasons: clearing the
        item clears count and condition with it, and a slot that has just
        gained an item is given a sane count and condition rather than
        inheriting whatever bytes were lying there.

        Clearing the item also clears **all four attachments**. A scope hanging
        off an empty hand is not a state the game writes, and leaving one
        behind is exactly how it ends up fitted to whatever goes in next.

        Putting a *different* item in the slot resets its condition to 100
        unless the caller says otherwise, for the same reason: the wear
        belonged to the thing that was there before.
        """
        if not 0 <= s < ITEM_COUNT:
            raise ValueError('slot %d is outside 0..%d' % (s, ITEM_COUNT - 1))
        base = self.bases[index]
        lo = FIRST_ITEM + s * ITEM_LEN
        had = self._stack_size(base, lo)
        fresh = False
        if item is not None:
            item = int(item) & 0xFFFF
            was = self._word(base, lo + IT_ID)
            fresh = item != was
            self._put_word(base, lo + IT_ID, item)
            # A DIFFERENT item means the old attachments are not this item's.
            # Clearing only when the slot is emptied was not enough: swapping
            # the gun in Shadow's hand left a Silencer and a Sniper Scope
            # fitted to whatever went in next, which is how a scope ended up
            # on a pistol that never had one.
            if item != was:
                for k in range(4):
                    self._put_word(base, lo + IT_ATTACH + 2 * k, 0)
                    self._put(base, lo + IT_ATTACH_STATUS + k, 0)
                # The rest of the 12 byte union is the old item's too - a
                # loaded magazine, a cash amount, other objects' conditions.
                for k in range(IT_STATUS + 1, IT_STATUS + 12):
                    self._put(base, lo + k, 0)
            if item == 0:
                self._put(base, lo + IT_COUNT, 0)
                self._put(base, lo + IT_STATUS, 0)
                return self.slot(index, s)
            if count is None and not self._get(base, lo + IT_COUNT):
                count = 1
            # A DIFFERENT item arrives new. Carrying the old condition
            # across is how a brand new Colt .45 turned up at 92%, inheriting
            # the wear on the MP5K it replaced. Only a condition the caller
            # actually asked for survives a swap.
            if condition is None and (item != was
                                      or not self._get(base, lo + IT_STATUS)):
                condition = 100
            # A magazine's "condition" byte is its round count, so new means
            # full - 100 would be 100 rounds in a 30 round magazine.
            if condition == 100 and item in ammo.MAGAZINES:
                condition = ammo.MAGAZINES[item][1]
            # and a new gun comes loaded, the way the game hands one over
            if fresh and ammo.is_gun(item) and ammo.fits(item):
                mag = ammo.fits(item)[0]
                self._put_word(base, lo + IT_GUN_AMMO_ITEM, mag)
                self._put(base, lo + IT_GUN_AMMO_TYPE, ammo.ammo_type(mag))
                self._put(base, lo + IT_GUN_SHOTS, ammo.capacity(item))
                self._put(base, lo + IT_GUN_AMMO_ITEM + 2, 100)
        if count is not None:
            self._put(base, lo + IT_COUNT, max(0, min(255, int(count))))
        if condition is not None:
            item_now = self._word(base, lo + IT_ID)
            top = ammo.MAGAZINES.get(item_now, (None, 100))[1]
            self._put(base, lo + IT_STATUS, max(0, min(top, int(condition))))
        self._fill_stack(base, lo, 1 if fresh else had)
        return self.slot(index, s)

    def _stack_size(self, base, lo):
        """How many per-object entries this slot really has (1 for cash and
        guns, whose union bytes mean something else)."""
        item = self._word(base, lo + IT_ID)
        if ammo.is_gun(item) or item_data.category(item) == 'Money':
            return 1
        return max(1, min(MAX_OBJECTS, self._get(base, lo + IT_COUNT)))

    def _fill_stack(self, base, lo, had):
        """Give every object in a stack a value, and clear the ones past it.

        A stack keeps one byte per object - rounds for a magazine, condition
        for anything else. Raising the count used to set only the first, so
        "3 magazines" came out as one full one and two holding 0 rounds.
        New objects copy the first; entries past the count are zeroed, as
        the game leaves them.
        """
        item = self._word(base, lo + IT_ID)
        if ammo.is_gun(item) or item_data.category(item) == 'Money':
            return
        n = self._stack_size(base, lo) if self._get(base, lo + IT_COUNT) else 0
        first = self._get(base, lo + IT_STATUS)
        for k in range(1, MAX_OBJECTS):
            if k >= n:
                want = 0
            elif k >= had:
                want = first
            else:
                want = self._get(base, lo + IT_STATUS + k)
            self._put(base, lo + IT_STATUS + k, want)

    def set_stack(self, index, s, values):
        """Set each object's own value in a stack: rounds per magazine, or
        condition per item. Missing entries are left as they are."""
        base = self.bases[index]
        lo = FIRST_ITEM + s * ITEM_LEN
        n = self._stack_size(base, lo)
        item = self._word(base, lo + IT_ID)
        top = (ammo.MAGAZINES.get(item, (None, 255))[1]
               if item_data.category(item) == 'Ammo' else 100)
        for k, v in enumerate(values[:n]):
            if v is not None:
                self._put(base, lo + IT_STATUS + k, max(0, min(top, int(v))))
        return self.slot(index, s)

    def set_gun_ammo(self, index, s, mag=None, rounds=None):
        """Load a gun: which magazine its rounds came from, and how many.

        The round type is not the user's to pick - it belongs to the magazine,
        so it is set from the magazine every time. Rounds are capped at the
        gun's magazine size, the most the game ever loads.
        """
        base = self.bases[index]
        lo = FIRST_ITEM + s * ITEM_LEN
        gun = self._word(base, lo + IT_ID)
        if not ammo.is_gun(gun):
            raise ValueError('%s is not a gun' % (item_data.name(gun) or 'nothing'))
        if mag is not None:
            mag = int(mag)
            if mag and mag not in ammo.fits(gun):
                raise ValueError('%s does not fit a %s'
                                 % (item_data.name(mag), item_data.name(gun)))
            self._put_word(base, lo + IT_GUN_AMMO_ITEM, mag)
            self._put(base, lo + IT_GUN_AMMO_TYPE, ammo.ammo_type(mag))
            if not mag:
                rounds = 0
        if rounds is not None:
            self._put(base, lo + IT_GUN_SHOTS,
                      max(0, min(ammo.capacity(gun), int(rounds))))
        return self.slot(index, s)

    def set_money(self, index, s, amount):
        """The cash in one money item, capped where the game caps a pocket."""
        base = self.bases[index]
        lo = FIRST_ITEM + s * ITEM_LEN
        item = self._word(base, lo + IT_ID)
        if item_data.category(item) != 'Money':
            raise ValueError('%s is not money' % (item_data.name(item) or 'nothing'))
        amount = max(0, min(MAX_MONEY, int(amount)))
        for k in (3, 2, 1, 0):          # high byte first, as in _put_word
            self._put(base, lo + IT_MONEY + k, (amount >> (8 * k)) & 0xFF)
        return self.slot(index, s)

    # -- the merc themself -----------------------------------------------

    def profile(self, index):
        """The profile number this record belongs to."""
        return self._get(self.bases[index], OFF_PROFILE)

    def nickname(self, index):
        base = self.bases[index]
        raw = bytes(self._get(base, OFF_NICK + i) for i in range(LEN_NICK))
        return raw.decode('utf-16-le', 'replace').split('\0', 1)[0].strip()

    def set_nickname(self, index, text):
        _put_text(self, self.bases[index], OFF_NICK, LEN_NICK, text)
        return self.nickname(index)

    def field(self, index, off):
        return self._get(self.bases[index], off)

    def set_field(self, index, off, value, top=100):
        return self._put(self.bases[index], off, max(0, min(top, int(value))))

    def stats(self, index):
        return {k: self.field(index, off) for k, off in STATS.items()}

    def merc(self, index):
        """The live copy of what the profile also holds, plus what only lives
        here."""
        f = lambda off: self.field(index, off)
        return {
            'energy': f(OFF_ENERGY),
            'max_energy': f(OFF_MAX_ENERGY),
            'morale': f(OFF_MORALE),
            'nick': self.nickname(index),
            'level': f(OFF_LEVEL),
            'health': f(OFF_HEALTH),
            'max_health': f(OFF_MAX_HEALTH),
            'skills': [f(OFF_SKILL1), f(OFF_SKILL2)],
            'stats': self.stats(index),
        }

    # -- the checksum ----------------------------------------------------

    def checksum(self, index):
        """What the game's MercChecksum gives for this record as it stands.

        Transcribed from JAPE (which took it from the game's disassembly) and
        checked: it reproduces the stored value for every hired merc in every
        save here. The game is not known to verify it, but writing the value
        it would have written is what makes an edited save indistinguishable
        from one the game saved.
        """
        base = self.bases[index]
        g = lambda off: self._get(base, off)
        sx = lambda off: _i8(g(off))
        a = _i32((sx(OFF_MAX_HEALTH) + 1) * (sx(OFF_HEALTH) + 2))
        a = _i32(a + sx(STATS['Agility']) + 1)
        a = _i32(a * (sx(STATS['Dexterity']) + 1))
        a = _i32(a + sx(STATS['Strength']) + 1)
        a = _i32(a * (sx(STATS['Marksmanship']) + 1))
        a = _i32(a + sx(STATS['Medical']) + 1)
        a = _i32(a * (sx(STATS['Mechanical']) + 1))
        a = _i32(a + sx(STATS['Explosives']) + 1)
        a = _i32(a * (sx(OFF_LEVEL) + 1))
        a = _i32(a + g(OFF_PROFILE) + 1)
        for s in range(ITEM_COUNT):
            lo = FIRST_ITEM + s * ITEM_LEN
            a = _i32(_i32(self._word(base, lo) + a) + g(lo + IT_COUNT))
        return a

    def stored_checksum(self, index):
        base = self.bases[index]
        v = 0
        for k in range(4):
            v |= self._get(base, OFF_CHECKSUM + k) << (8 * k)
        return _i32(v)

    def fix_checksum(self, index):
        """Store the checksum the game would have written. True if it moved."""
        want = self.checksum(index) & 0xFFFFFFFF
        base = self.bases[index]
        moved = False
        for k in (3, 2, 1, 0):
            moved |= self._put(base, OFF_CHECKSUM + k, (want >> (8 * k)) & 0xFF)
        return moved

    def set_attachment(self, index, s, k, item=None, condition=None):
        """Fit, change or remove attachment `k` (0-3) on slot `s`.

        Clearing the item clears its condition too: an attachment percentage
        with nothing attached is a state the game never writes.
        """
        if not 0 <= k < 4:
            raise ValueError('attachment %d is outside 0..3' % k)
        base = self.bases[index]
        lo = FIRST_ITEM + s * ITEM_LEN
        if item is not None:
            item = int(item) & 0xFFFF
            self._put_word(base, lo + IT_ATTACH + 2 * k, item)
            if item == 0:
                self._put(base, lo + IT_ATTACH_STATUS + k, 0)
                return self.slot(index, s)
            if condition is None and not self._get(base, lo + IT_ATTACH_STATUS + k):
                condition = 100
        if condition is not None:
            c = max(0, min(100, int(condition)))
            self._put(base, lo + IT_ATTACH_STATUS + k, c)
        return self.slot(index, s)

    # -- what a record holds ---------------------------------------------

    def slot(self, index, s):
        base = self.bases[index]
        lo = FIRST_ITEM + s * ITEM_LEN
        item = self._word(base, lo)
        count = self._get(base, lo + IT_COUNT)
        if not count:
            item = 0                  # see Profiles.slot - zero quantity is empty
        # Four entries, always, position kept - an empty one is None rather
        # than left out. Compacting the list was quietly wrong: a weapon with
        # something in attachment slot 2 and nothing in slot 0 came back as a
        # one-item list, so the editor showed it as attachment 0 and writing
        # to it hit the wrong physical slot. It is also why only three rows
        # appeared where four exist.
        attach = []
        for k in range(4):
            aid = self._word(base, lo + IT_ATTACH + 2 * k)
            attach.append({
                'index': k,
                'id': aid,
                'name': item_data.name(aid) if aid else '',
                'condition': self._get(base, lo + IT_ATTACH_STATUS + k),
            } if aid else None)
        out = {
            'slot': s,
            'name': SLOT_NAMES[s],
            'item': item,
            'item_name': item_data.name(item) if item else '',
            'count': count,
            'condition': self._get(base, lo + IT_STATUS),
            'attachments': attach,
            'empty': item == 0,
        }
        if not item:
            return out
        if ammo.is_gun(item):
            mag = self._word(base, lo + IT_GUN_AMMO_ITEM)
            out['gun'] = {
                'mag': mag,
                'mag_name': item_data.name(mag) if mag else '',
                'rounds': self._get(base, lo + IT_GUN_SHOTS),
                'capacity': ammo.capacity(item),
                'type': self._get(base, lo + IT_GUN_AMMO_TYPE),
                'fits': [{'id': m, 'name': item_data.name(m)}
                         for m in ammo.fits(item)],
            }
        elif item_data.category(item) == 'Money':
            v = 0
            for k in range(4):
                v |= self._get(base, lo + IT_MONEY + k) << (8 * k)
            out['money'] = v
        elif count > 1:
            out['stack'] = [self._get(base, lo + IT_STATUS + k)
                            for k in range(min(count, MAX_OBJECTS))]
            if item in ammo.MAGAZINES:
                out['capacity'] = ammo.MAGAZINES[item][1]
        return out

    def inventory(self, index):
        return [self.slot(index, s) for s in range(ITEM_COUNT)]

    def energy(self, index):
        base = self.bases[index]
        return {'energy': self._get(base, OFF_ENERGY),
                'max_energy': self._get(base, OFF_MAX_ENERGY),
                'morale': self._get(base, OFF_MORALE)}
