"""The editor's half of the live link to a running game.

The game side is the Nemesis Live DLC (see build_livelink.py). The two talk
through two small text files in the game's own AppData folder:

    nemesis-cmd.txt     we write commands here
    nemesis-state.txt   the game writes what it can see here

Why files and not a socket: mods run inside LuaModEnv, which hides all file
IO, all networking, and 144 other things. DLC code has none of those
restrictions but retail JA3 has no socket functions left at all, so a file
channel is the whole channel. It also means nothing has to be listening, no
port is opened, and a stale link is obvious rather than silently wrong.

Freshness comes from the state file's timestamp. The DLC rewrites it every
couple of seconds even when nothing has changed, so an old timestamp means
the game is gone rather than idle.
"""
import os
import time

import gamepaths

CMD_FILE = 'nemesis-cmd.txt'
STATE_FILE = 'nemesis-state.txt'
MODS_FILE = 'nemesis-mods.txt'
MERCS_FILE = 'nemesis-mercs.txt'

# chr(9) is a tab. Spelled out because an escape written here goes
# through shells that quietly turn it into a real tab, which reads as a
# stray blank in the source.
TAB = chr(9)
DLC_FILE = 'nemesis-live.hpk'

# The DLC rewrites state at least every 2s; allow generous slack for a
# loading screen or a stutter before calling the link dead.
STALE_AFTER = 8.0


def channel_dir(folder=None):
    return folder or gamepaths.user_data_dir()


def _path(name, folder=None):
    return os.path.join(channel_dir(folder), name)


def _parse_kv(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or '=' not in line:
            continue
        k, _, v = line.partition('=')
        out[k.strip()] = v.strip()
    return out


def read_state(folder=None):
    """What the game last reported, or None if it has never reported."""
    path = _path(STATE_FILE, folder)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        age = max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None
    kv = _parse_kv(text)
    money = kv.get('money') or ''
    return {
        'seq': int(kv['seq']) if kv.get('seq', '').lstrip('-').isdigit() else None,
        'in_game': kv.get('ingame') == '1',
        'multiplayer': kv.get('mp') == '1',
        'money': int(money) if money.lstrip('-').isdigit() else None,
        'result': kv.get('result', ''),
        # None when the add-on is too old to report a bag at all, [] when it
        # reported an empty one. The UI has to tell those apart: the first
        # means "no live source, leave the save's figure alone", the second
        # means "the game's bag really is empty".
        'bag': _parse_bag(kv['bag']) if 'bag' in kv else None,
        'age': round(age, 1),
        'fresh': age < STALE_AFTER,
    }


def _parse_bag(text):
    """"Parts:115,Meds:58" -> [{'item': 'Parts', 'amount': 115}, ...]"""
    out = []
    for chunk in (text or '').split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, amount = chunk.partition(':')
        name = name.strip()
        if not name:
            continue
        out.append({'item': name,
                    'amount': int(amount) if amount.strip().isdigit() else 1})
    return out


def read_mods(folder=None):
    """Every mod the game can see, with two different truths about each.

    `enabled` is what the game will load next time. `loaded` is what is
    running right now. A toggle changes the first immediately and the second
    only after a reload, so the UI can say "on, but not applied yet" rather
    than quietly lying.
    """
    try:
        with open(_path(MODS_FILE, folder), 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        parts = line.split(TAB)
        if len(parts) < 4:
            continue
        mod_id, enabled, loaded, title = parts[0], parts[1], parts[2], parts[3]
        out.append({
            'id': mod_id,
            'enabled': enabled == '1',
            'loaded': loaded == '1',
            'title': title,
            'pending': (enabled == '1') != (loaded == '1'),
        })
    return sorted(out, key=lambda m: m['title'].lower())


# Same ten, in the same order the DLC writes them.
STATS = ('Health', 'Agility', 'Dexterity', 'Strength', 'Wisdom', 'Leadership',
         'Marksmanship', 'Mechanical', 'Explosives', 'Medical')


def read_mercs(folder=None):
    """What the running game says about each hired merc, right now.

    The save on disk can be hours behind the running game, so while the link
    is live the editor shows these instead - otherwise you would be editing
    against numbers that are no longer true.
    """
    try:
        with open(_path(MERCS_FILE, folder), 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        parts = line.split(TAB)
        if len(parts) < 6:
            continue
        try:
            stats = [int(x) for x in parts[5].split(',')]
        except ValueError:
            continue
        if len(stats) != len(STATS):
            continue
        as_int = lambda v: int(v) if v.lstrip('-').isdigit() else None
        out.append({
            'id': parts[0],
            'name': parts[1],
            'hp': as_int(parts[2]),
            'max_hp': as_int(parts[3]),
            'xp': as_int(parts[4]),
            'stats': dict(zip(STATS, stats)),
            # everything on the merc, perks and wounds alike - the editor
            # decides which of those it is willing to touch
            'effects': [x for x in parts[6].split(',') if x] if len(parts) > 6 else [],
            # standing in the loaded sector, as opposed to merely hired. An
            # older DLC does not send this; treat silence as "do not know"
            # rather than as "no", so nothing disappears from an old build.
            # '?' means the game would not answer - treat that as unknown,
            # the same as an older DLC that never sends the column at all.
            'onmap': ({'1': True, '0': False}.get(parts[7])
                      if len(parts) > 7 else None),
        })
    return out


def _next_seq(folder=None):
    """One past whatever we last sent. Read from disk so it survives a restart."""
    try:
        with open(_path(CMD_FILE, folder), 'r', encoding='utf-8') as f:
            prev = _parse_kv(f.read()).get('seq', '')
        return int(prev) + 1 if prev.lstrip('-').isdigit() else 1
    except OSError:
        return 1


def send(op, value=None, folder=None):
    """Write one command. Returns the sequence number the game should echo."""
    seq = _next_seq(folder)
    lines = ['seq=%d' % seq, 'op=%s' % op]
    if value is not None:
        lines.append('value=%s' % value)
    tmp = _path(CMD_FILE + '.tmp', folder)
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    # Replace atomically - the game polls this file and must never read half
    # of one command and half of the next.
    os.replace(tmp, _path(CMD_FILE, folder))
    return seq


def wait_for(seq, timeout=5.0, folder=None):
    """Block until the game acknowledges `seq`. Returns (ok, message)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = read_state(folder)
        if st and st['seq'] is not None and st['seq'] >= seq:
            msg = st['result'] or ''
            return (not msg.startswith('error') and not msg.startswith('refused'), msg)
        time.sleep(0.15)
    return (False, 'the game did not answer - is it running with the Live DLC installed?')


def se_verdict(game_dir=None):
    """How the installed extender compares with the one this editor carries:
    'missing', 'older', 'same', 'newer', 'unreadable', or None if unknown.

    Read only. Nothing in the editor installs or updates the extender unless
    the user presses a button - an automatic install could not tell "never
    installed" from "removed on purpose", and a tester lost two days to it
    putting the file back after every removal.
    """
    try:
        import ja3se_install
        return ja3se_install.compare(game_dir)[0]
    except Exception:                                            # noqa: BLE001
        return None


def se_owned_files(game_dir=None):
    """Every path the extender owns, so the question "what has this put on my
    machine?" has an answer that does not depend on trusting us."""
    try:
        import ja3se_install
        return [{'path': p, 'exists': e, 'what': w}
                for p, e, w in ja3se_install.owned_files(game_dir)]
    except Exception:                                            # noqa: BLE001
        return []


def live_owned_files(game_dir=None):
    """The same answer for the live link add-on."""
    try:
        import build_livelink
        return [{'path': p, 'exists': e, 'what': w}
                for p, e, w in build_livelink.owned_files(game_dir)]
    except Exception:                                            # noqa: BLE001
        return []


def se_state(game_dir=None):
    """(installed, up_to_date, available) for the script extender.

    Same lazy import as dlc_state, for the same reason: the editor has to keep
    working if the packer is missing. And the same rule about not knowing -
    if the pack cannot be built we cannot COMPARE, so unknown reads as up to
    date. Reporting "yours is older, press Update" when building is what
    failed sends the user to press a button that runs the same failing code.
    """
    try:
        import ja3se_install
        installed, fresh, _path = ja3se_install.state(game_dir)
        return installed, fresh, True
    except Exception:                                            # noqa: BLE001
        return False, True, False


def dlc_state(game_dir=None):
    """(installed, up_to_date). Imported lazily so the editor still runs if
    the packer is missing for any reason."""
    try:
        import build_livelink
        installed, fresh, _path = build_livelink.state(game_dir)
        return installed, fresh
    except Exception:                                            # noqa: BLE001
        game = game_dir or gamepaths.game_dir()
        if not game:
            return False, False
        # If the pack cannot be built we cannot COMPARE, so we do not know
        # whether it is stale. This used to hardcode False, which the UI
        # showed as "the copy in your game folder is older than this editor -
        # press Update". It was not older; building had failed. Pressing
        # Update then ran the same failing code, so the user got an error and
        # a permanent nag for a problem that was never about staleness.
        # Unknown reads as up to date: if building is broken, updating is
        # broken too, and there is nothing useful for the user to press.
        return os.path.exists(os.path.join(game, 'DLC', DLC_FILE)), True


def status(folder=None, game_dir=None):
    """Everything the UI needs to describe the link in one call.

    Never writes anything. It is polled every two seconds, and anything it
    wrote would be impossible to switch off.
    """
    st = read_state(folder)
    mods = read_mods(folder) if (st and st['fresh']) else []
    mercs = read_mercs(folder) if (st and st['fresh'] and st['in_game']) else []
    installed, up_to_date = dlc_state(game_dir)
    se_installed, se_fresh, se_available = se_state(game_dir)
    if st is None:
        state = 'never'
    elif not st['fresh']:
        state = 'stale'
    else:
        state = 'live'
    return {
        'state': state,
        'installed': installed,
        'up_to_date': up_to_date,
        'se_installed': se_installed,
        'se_up_to_date': se_fresh,
        'se_available': se_available,
        'se_verdict': se_verdict(game_dir) if se_installed else None,
        'se_files': se_owned_files(game_dir),
        'files': live_owned_files(game_dir),
        'game_dir': gamepaths.game_dir(),
        'game': st,
        'can_edit': bool(st and st['fresh'] and st['in_game']
                         and not st['multiplayer']),
        'mods': mods,
        'mercs': mercs,
        # mods can be switched at the main menu too - no campaign needed
        'can_mod': bool(st and st['fresh'] and not st['multiplayer']),
        'mods_pending': sum(1 for m in mods if m['pending']),
    }
