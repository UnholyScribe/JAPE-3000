"""Warn when two installed mods look like they will fight each other.

There is no manifest of what a mod overrides, so this reads what the mods
actually contain and looks for the two things that genuinely collide:

  * the same preset id - two mods both defining merc `Fox` or perk `Deadeye`.
    The game keeps whichever loaded last and silently drops the other.
  * the same global Lua name - two mods both defining `ImpGetPersonalPerks`.
    Same story: the second definition replaces the first, so one mod's version
    of that function stops existing.

`OnMsg` handlers are deliberately *not* treated as conflicts. The engine calls
every handler registered for a message, so two mods reacting to the same event
is normal and works fine.

None of this can be certain. Which mod wins depends on load order, and two mods
touching the same function sometimes cooperate on purpose - the Merc-InGame
Editor and Make IMP Great Again both extend the IMP screen. So findings are
graded, and the wording says what was seen rather than predicting doom.
"""
import collections
import os
import re

import mods as mods_mod

# `function Foo(`, `function Foo.Bar(`, `function Foo:Bar(`
FUNC = re.compile(r'^[ \t]*function[ \t]+([A-Za-z_][\w.:]*)[ \t]*\(', re.M)
CLASS = re.compile(r'DefineClass\.([A-Za-z_]\w*)')
ONMSG = re.compile(r'^[ \t]*function[ \t]+OnMsg\.(\w+)', re.M)

# names so generic that two mods sharing one says nothing useful
BORING = {'main', 'init', 'Init', 'OnMsg'}


def _lua_names(folder):
    """Every global function and class a mod's Lua defines."""
    funcs, classes, msgs = set(), set(), set()
    for _name, src in mods_mod.iter_lua_files(folder):
        for fn in FUNC.findall(src):
            if fn.startswith('OnMsg.') or fn in BORING:
                continue
            funcs.add(fn)
        classes.update(CLASS.findall(src))
        msgs.update(ONMSG.findall(src))
    return {'funcs': funcs, 'classes': classes, 'msgs': msgs}


def _by_name(pairs):
    """[(name, mod)] -> {name: [mod, ...]} keeping only the shared ones."""
    out = collections.defaultdict(list)
    for name, mod in pairs:
        if mod not in out[name]:
            out[name].append(mod)
    return {k: v for k, v in out.items() if len(v) > 1}


def analyse(scanned=None):
    """Findings across the installed mods, worst first.

    `scanned` is a mods.scan() result; it is taken as an argument so the
    server can reuse the scan it already did rather than walking twice.
    """
    data = scanned if scanned is not None else mods_mod.scan()
    active = [m for m in (data.get('mods') or []) if m.get('installed')]
    findings = []

    # ---- same preset id, defined by more than one mod ---------------------
    for key, label in (('mercs', 'merc'), ('perks', 'perk'),
                       ('items', 'item'), ('components', 'attachment')):
        pairs = [(e['id'], e.get('mod')) for e in (data.get(key) or [])
                 if e.get('mod')]
        for name, owners in sorted(_by_name(pairs).items()):
            findings.append({
                'level': 'will',
                'kind': 'preset',
                'what': '%s %s' % (label, name),
                'mods': owners,
                'detail': 'Both mods define the %s "%s". The game keeps '
                          'whichever loads last and ignores the other.'
                          % (label, name),
            })

    # ---- same global Lua name --------------------------------------------
    fn_pairs, cls_pairs, msg_pairs = [], [], []
    for m in active:
        folder = m.get('folder')
        if not folder or not os.path.isdir(folder):
            continue
        names = _lua_names(folder)
        title = m.get('title') or os.path.basename(folder)
        fn_pairs += [(n, title) for n in names['funcs']]
        cls_pairs += [(n, title) for n in names['classes']]
        msg_pairs += [(n, title) for n in names['msgs']]

    for name, owners in sorted(_by_name(fn_pairs).items()):
        findings.append({
            'level': 'will',
            'kind': 'function',
            'what': name + '()',
            'mods': owners,
            'detail': 'Both mods replace the function %s. Only the one that '
                      'loads last exists afterwards, so the other mod loses '
                      'whatever it changed there.' % name,
        })

    for name, owners in sorted(_by_name(cls_pairs).items()):
        findings.append({
            'level': 'will',
            'kind': 'class',
            'what': name,
            'mods': owners,
            'detail': 'Both mods define the class %s. The later one wins.'
                      % name,
        })

    # Handlers stack, so this is context rather than a problem.
    for name, owners in sorted(_by_name(msg_pairs).items()):
        findings.append({
            'level': 'note',
            'kind': 'event',
            'what': 'OnMsg.' + name,
            'mods': owners,
            'detail': 'Both mods react to %s. That is normally fine - the '
                      'game runs every handler - but if they change the same '
                      'thing, the result depends on which ran first.' % name,
        })

    # ---- built for an older game build -----------------------------------
    revs = [m.get('saved_with_revision') for m in active
            if isinstance(m.get('saved_with_revision'), int)]
    if revs:
        newest = max(revs)
        for m in active:
            rev = m.get('saved_with_revision')
            if isinstance(rev, int) and newest - rev >= 20000:
                findings.append({
                    'level': 'may',
                    'kind': 'age',
                    'what': m.get('title'),
                    'mods': [m.get('title')],
                    'detail': 'Built against an older game build (%d) than '
                              'your newest mod (%d). Older mods usually still '
                              'work, but this is the first thing to disable '
                              'if the game misbehaves.' % (rev, newest),
                })

    # ---- declared dependencies that are not installed --------------------
    have = {(m.get('mod_id') or m.get('id')) for m in active}
    have |= {(m.get('title') or '').lower() for m in active}
    for m in active:
        for dep in (m.get('dependencies') or []):
            dep_id = dep.get('id') if isinstance(dep, dict) else dep
            if not dep_id:
                continue
            # an optional dependency only sets load order when it is present
            if isinstance(dep, dict) and dep.get('required') is False:
                continue
            title = (dep.get('title') if isinstance(dep, dict) else None) \
                or dep_id
            if dep_id not in have and str(dep_id).lower() not in have:
                findings.append({
                    'level': 'will',
                    'kind': 'missing',
                    'what': title,
                    'mods': [m.get('title')],
                    'detail': '%s needs "%s", which is not installed.'
                              % (m.get('title'), title),
                })

    order = {'will': 0, 'may': 1, 'note': 2}
    findings.sort(key=lambda f: (order.get(f['level'], 3), f['kind'],
                                 f['what']))
    return {
        'checked': len(active),
        'findings': findings,
        'counts': {
            'will': sum(1 for f in findings if f['level'] == 'will'),
            'may': sum(1 for f in findings if f['level'] == 'may'),
            'note': sum(1 for f in findings if f['level'] == 'note'),
        },
    }
