"""Read the game's own log, so the editor can report facts instead of guesses.

Everything else in this app infers mod state from what is on disk: a folder
with a metadata.lua is "installed". That is a guess, and it was wrong for a
whole day - our Live Link mod sat there looking perfectly installed while the
game silently never ran a line of it.

The game writes what actually happened to a log file:

    %APPDATA%/Jagged Alliance 3/logs/JA3.exe-<timestamp>.log

    [mod] Loaded mod def Save Editor - Live Link (id SEdLink, v1.00-001) ...
    [mod] Loaded mod items for: SEdLink, JA3_CommonLib, eqezLyH, kkiLFvQ

The example above is the OLD mod, kept because it is what the two-line rule was
worked out on. The live link is a DLC now and announces itself differently:

    DLC NemesisLive loaded
    [NemesisLive] live link running

The rule is the same either way - the second line is the one that means the
code is actually running.

The first line means the game *found* a mod. The second names the mods it
*loaded* - which is the only statement that matters, and the one no amount of
looking at folders can tell you.

Console `print` output lands here too, which is what makes the in-game console
a two-way channel rather than a write-only one.

Read-only. Nothing here writes to the game or its folders.
"""
import os
import re

import gamepaths

DEF_RE = re.compile(
    r'\[mod\]\s+Loaded mod def\s+(?P<title>.+?)\s+'
    r'\(id\s+(?P<id>[\w-]+),\s*v(?P<version>[\w.-]+)\)')
ITEMS_RE = re.compile(r'\[mod\]\s+Loaded mod items for:\s*(?P<ids>.+)')
ERROR_RE = re.compile(r'(?i)\[mod\][^\n]*(error|failed|could not|not found)')


def log_dir():
    return os.path.join(gamepaths.user_data_dir(), 'logs')


def newest_log():
    """The log from the most recent run, or None if the game never ran."""
    d = log_dir()
    try:
        logs = [os.path.join(d, f) for f in os.listdir(d) if f.endswith('.log')]
    except OSError:
        return None
    if not logs:
        return None
    return max(logs, key=os.path.getmtime)


def read(path=None):
    path = path or newest_log()
    if not path:
        return ''
    try:
        # the log is written while the game runs, so never lock it
        with open(path, 'rb') as f:
            return f.read().decode('utf-8', 'replace')
    except OSError:
        return ''


def parse(text=None, path=None):
    """What the last run actually did with mods."""
    text = text if text is not None else read(path)
    seen = {}
    for m in DEF_RE.finditer(text):
        seen[m.group('id')] = {
            'id': m.group('id'),
            'title': m.group('title').strip(),
            'version': m.group('version'),
        }
    # several of these lines appear as loading progresses; the last is final
    loaded = []
    for m in ITEMS_RE.finditer(text):
        loaded = [i.strip() for i in m.group('ids').split(',') if i.strip()]
    problems = []
    for line in text.splitlines():
        if ERROR_RE.search(line):
            problems.append(line.strip())
    return {
        'log': path or newest_log(),
        'seen': seen,          # id -> what the game found on disk
        'loaded': loaded,      # ids the game actually loaded
        'problems': problems[:20],
        'ran': bool(seen),
    }


def status(scanned_mods=None):
    """Cross-check what we believe on disk against what the game reported.

    `scanned_mods` is a mods.find_mods() / scan()['mods'] list. Anything the
    game loaded that we did not expect, or that we thought was enabled and the
    game ignored, is worth showing rather than hiding.
    """
    info = parse()
    out = dict(info)
    out['by_id'] = {}
    if not info['ran']:
        return out
    loaded = set(info['loaded'])
    for mod_id, meta in info['seen'].items():
        out['by_id'][mod_id] = {
            'title': meta['title'],
            'version': meta['version'],
            'loaded': mod_id in loaded,
        }
    if scanned_mods:
        # our own scan keys on folder names and titles, the log on mod ids,
        # so match on whichever we can
        titles = {(m.get('title') or '').lower(): m for m in scanned_mods}
        for mod_id, row in out['by_id'].items():
            m = titles.get(row['title'].lower())
            if m is not None:
                row['folder'] = m.get('folder')
    return out


def since(offset, path=None):
    """New text appended to the log since `offset` bytes, and the new offset.

    This is how a console query reads its answer: note the size, send the
    statement, then read only what appeared after it.
    """
    path = path or newest_log()
    if not path:
        return '', offset
    try:
        size = os.path.getsize(path)
    except OSError:
        return '', offset
    if size < offset:          # the game restarted and began a new log
        offset = 0
    try:
        with open(path, 'rb') as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return '', offset
    return chunk.decode('utf-8', 'replace'), offset + len(chunk)
