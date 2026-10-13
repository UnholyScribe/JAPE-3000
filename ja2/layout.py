"""Where everything after the merc profiles sits in a JA2 save.

Every section here has a size that depends on the campaign, so nothing is at a
fixed offset - the file is walked in the order the game writes it
(SaveLoadGame.cc, SaveGame) and each size is read as it goes:

    soldier array   156 slots: a 1-byte active flag, and for an active one the
                    2328-byte enciphered record, its strategic path (uint32
                    count + 20 per node) and a keyring flag (+128 if present)
    finances, history, files      uint32 size + that many bytes, each
    email           uint32 count; each: uint32 subject size, subject, 44 bytes
    strategic info  324 x 41 map elements, 256 x 116 sectors, 324 SAM bytes, 1
    underground     uint32 count + 72 each
    squads          20 squads x 6 records of 12 bytes (int16 soldier id first,
                    -1 when empty), then 20 bytes of squad movement group ids
    movement groups uint32 count; each: 84 bytes, then for a player group with
                    members a uint32 count + one uint32 PROFILE id each, for an
                    enemy group 29 bytes, then uint32 waypoints + 8 each

Checked against SaveGame01: the walk lands on every section where its own
contents say it should - the finances blob holds the balance, squad 1 holds
soldiers 0/1/2, group 30 lists profiles 51/10/25.
"""
import struct

TOTAL_SOLDIERS = 156
SOLDIER_CIPHER = 2328
PATH_NODE = 20
KEYRING = 128
EMAIL_TAIL = 44
STRATEGIC_INFO = 324 * 41 + 256 * 116 + 324 + 1
UNDERGROUND = 72
SQUADS = 20
SQUAD_SLOTS = 6
SQUAD_RECORD = 12
SQUAD_BLOCK = SQUADS * SQUAD_SLOTS * SQUAD_RECORD
GROUP_HEAD = 84
ENEMY_GROUP = 29
WAYPOINT = 8

# inside a soldier record (offsets from the game's InjectSoldierType)
S_ID = 0
S_TEAM = 752
S_GROUP = 753
S_PROFILE = 1825
S_ASSIGNMENT = 1917

# inside a movement group's 84-byte head
G_PLAYER = 1
G_PERSISTENT = 3
G_ID = 4
G_SIZE = 5


class LayoutError(Exception):
    pass


def _u32(raw, at):
    if at + 4 > len(raw):
        raise LayoutError('ran off the end of the file at %d' % at)
    return struct.unpack_from('<I', raw, at)[0]


def plain_byte(raw, base, off, key):
    prev = raw[base + off - 1] if off else 0
    return (raw[base + off] - prev - key[off]) % 256


class Layout:
    """The walked map of one save. Offsets are absolute file positions."""

    def __init__(self, raw, profiles_end, key):
        self.soldiers = []
        self.flags = []               # every slot's active-flag offset
        at = profiles_end
        for slot in range(TOTAL_SOLDIERS):
            flag = at
            self.flags.append(flag)
            if at >= len(raw):
                raise LayoutError('soldier array runs off the file')
            active = raw[at]
            at += 1
            if not active:
                continue
            if active != 1:
                raise LayoutError('slot %d active flag is %d' % (slot, active))
            base = at
            at += SOLDIER_CIPHER
            path_at = at
            nodes = _u32(raw, at)
            if nodes > 1000:
                raise LayoutError('slot %d has a %d-node path' % (slot, nodes))
            at += 4 + PATH_NODE * nodes
            ring = raw[at]
            if ring > 1:
                raise LayoutError('slot %d keyring flag is %d' % (slot, ring))
            at += 1 + (KEYRING if ring else 0)
            g = lambda off: plain_byte(raw, base, off, key)        # noqa: E731
            if g(S_ID) != slot:
                raise LayoutError('slot %d holds soldier %d' % (slot, g(S_ID)))
            self.soldiers.append({
                'slot': slot, 'flag': flag, 'base': base, 'path': path_at,
                'end': at, 'team': g(S_TEAM), 'group': g(S_GROUP),
                'profile': g(S_PROFILE), 'nodes': nodes, 'keyring': bool(ring),
                'assignment': g(S_ASSIGNMENT)})
        self.soldiers_end = at

        self.blobs = {}
        for name in ('finances', 'history', 'files'):
            self.blobs[name] = at
            at += 4 + _u32(raw, at)
        self.email = at
        n = _u32(raw, at)
        if n > 10000:
            raise LayoutError('%d emails' % n)
        at += 4
        for _ in range(n):
            at += 4 + _u32(raw, at) + EMAIL_TAIL
        self.strategic = at
        at += STRATEGIC_INFO
        self.underground = at
        n = _u32(raw, at)
        if n > 1000:
            raise LayoutError('%d underground sectors' % n)
        at += 4 + UNDERGROUND * n

        self.squads = at
        for r in range(SQUADS * SQUAD_SLOTS):
            sid = struct.unpack_from('<h', raw, at + r * SQUAD_RECORD)[0]
            if not -1 <= sid < TOTAL_SOLDIERS:
                raise LayoutError('squad record %d names soldier %d' % (r, sid))
        at += SQUAD_BLOCK
        self.squad_groups = at
        at += SQUADS

        self.groups_count = at
        n = _u32(raw, at)
        if n > 1000:
            raise LayoutError('%d movement groups' % n)
        at += 4
        self.groups = []
        for _ in range(n):
            head = at
            if head + GROUP_HEAD > len(raw):
                raise LayoutError('movement groups run off the file')
            player, size = raw[head + G_PLAYER], raw[head + G_SIZE]
            at += GROUP_HEAD
            grp = {'head': head, 'id': raw[head + G_ID], 'player': bool(player),
                   'persistent': bool(raw[head + G_PERSISTENT]),
                   'size': size, 'members_at': None, 'members': []}
            if player:
                if size:
                    grp['members_at'] = at
                    cnt = _u32(raw, at)
                    if cnt > 32:
                        raise LayoutError('group of %d' % cnt)
                    grp['members'] = list(struct.unpack_from(
                        '<%dI' % cnt, raw, at + 4))
                    at += 4 + 4 * cnt
            else:
                at += ENEMY_GROUP
            wp = _u32(raw, at)
            if wp > 1000:
                raise LayoutError('%d waypoints' % wp)
            at += 4 + WAYPOINT * wp
            grp['end'] = at
            self.groups.append(grp)
        self.groups_end = at

    # -- lookups ---------------------------------------------------------

    def team(self):
        """Records of the player's team, in slot order."""
        return [s for s in self.soldiers if s['team'] == 0]

    def soldier(self, slot):
        for s in self.soldiers:
            if s['slot'] == slot:
                return s
        return None

    def group(self, gid):
        for g in self.groups:
            if g['id'] == gid:
                return g
        return None

    def squad_of(self, raw, slot):
        """(squad, position) holding soldier `slot`, or None."""
        for r in range(SQUADS * SQUAD_SLOTS):
            at = self.squads + r * SQUAD_RECORD
            if struct.unpack_from('<h', raw, at)[0] == slot:
                return divmod(r, SQUAD_SLOTS)
        return None


def walk(raw, profiles_end, key):
    """A Layout, or None when this save does not walk cleanly."""
    try:
        return Layout(raw, profiles_end, key)
    except (LayoutError, struct.error, IndexError):
        return None
