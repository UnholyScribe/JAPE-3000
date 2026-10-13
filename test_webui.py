"""Invariants the web UI has to hold, checked against the source.

    python test_webui.py

There is no JavaScript test runner here and this is not one. It asserts a
handful of cross-file facts that cannot be checked by reading either file
alone, and every one of them is a bug that actually happened.

**The op table is the important one.** Jager Allianz reported that the Supplies
section "has such a prominent place but I am not sure if/how it works (or why
it doesn't)". The cause: the editor's LIVE_OPS had no entry for the squad bag,
so pressing Restock while the game was connected looked the op up, found
nothing, and fell through to the SAVE queue - a prominent button that quietly
edited a file nobody was looking at. Nothing failed. No exception, no refusal.

The reverse is just as silent: an op the editor SENDS that the Lua does not
handle comes back "unknown command" into a result line nobody reads. Both
directions live in two different files in two different languages, and nothing
connected them until this.
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_livelink                                          # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('%s %s%s' % ('  ok  ' if cond else '  FAIL', name,
                       ('  - ' + detail) if detail and not cond else ''))
    return bool(cond)


def app_js():
    with io.open(os.path.join(HERE, 'webui', 'app.js'), encoding='utf-8') as f:
        return f.read()


def live_ops_block(js):
    """The body of the LIVE_OPS object, brace-matched rather than guessed."""
    i = js.index('const LIVE_OPS = {')
    j = i + len('const LIVE_OPS = ')
    depth = 0
    for k in range(j, len(js)):
        if js[k] == '{':
            depth += 1
        elif js[k] == '}':
            depth -= 1
            if depth == 0:
                return js[j:k]
    raise AssertionError('LIVE_OPS is not brace-balanced')


def test_ops_match():
    """Every op the editor can SEND is one the game's Lua handles."""
    js = app_js()
    body = live_ops_block(js)

    # The dispatcher builds ['opname', value] pairs. Take the first string of
    # each pair; that is the op the game will be asked for.
    sent = set(re.findall(r"\[\s*'([a-z_]+)'\s*,", body))
    handled = set(re.findall(r'op == "([a-z_]+)"', build_livelink.AUTORUN))

    check('the editor knows how to send something at all', len(sent) >= 6,
          'found %d' % len(sent))
    check('the game handles something at all', len(handled) >= 10,
          'found %d' % len(handled))

    orphan = sorted(sent - handled)
    check('every op the editor sends is handled by the game', not orphan,
          'the game has no handler for: ' + ', '.join(orphan))

    # Not a failure - the Lua also answers ops raised outside this table, like
    # despawn from the map page - but worth printing so a genuinely dead
    # handler is visible rather than accumulating unnoticed.
    if handled - sent:
        print('       (game also handles, from elsewhere: %s)'
              % ', '.join(sorted(handled - sent)))


def test_bag_add_is_wired():
    """The exact fault Jager hit, pinned so it cannot come back quietly."""
    body = live_ops_block(app_js())
    check('bag_add has a live op, so Restock cannot fall through to the save',
          'bag_add:' in body, 'bag_add is missing from LIVE_OPS again')


def test_receipts_name_their_destination():
    """No receipt says only "Queued".

    the tester's screenshot, 2026-09-09: a change that went straight into the
    running game announced itself as "Queued" - save-file language - under a
    lit LIVE badge. Nothing was false and the reader could not tell.
    """
    js = app_js()
    bare = re.findall(r"toast\(\s*'Queued'\s*,", js)
    check('no receipt says only "Queued"', not bare,
          '%d bare Queued toast(s) left' % len(bare))


def test_nav_groups_are_real():
    """A nav entry pointing at a group that does not exist would vanish.

    renderNav only emits an entry inside a group it finds, so a typo in `grp`
    silently removes a whole page from the sidebar - the kind of failure that
    looks like a feature that was never built.
    """
    js = app_js()
    i = js.index('const NAV = [')
    nav = js[i:js.index('];', i)]
    j = js.index('const NAV_GROUPS = [')
    groups = set(re.findall(r"id:\s*'([a-z]+)'",
                            js[j:js.index('];', j)]))

    used = set(re.findall(r"grp:\s*'([a-z]+)'", nav))
    check('every group a nav entry names really exists',
          used <= groups, 'unknown: ' + ', '.join(sorted(used - groups)))

    entries = len(re.findall(r"\{\s*id:\s*'", nav))
    grouped = len(re.findall(r'grp:', nav))
    check('every nav entry is grouped, or deliberately not',
          entries - grouped <= 1,
          '%d of %d entries have no group' % (entries - grouped, entries))


def test_extender_is_removable():
    """The script extender must be removable from the page it appears on.

    It was not, for three weeks. The install became automatic, the button came
    out with it, and the only Remove on that card belonged to the live link -
    so a tester removed "the add-on", watched the extender reinstall itself,
    and had no way to stop it. Nothing failed; nothing was testing this.
    """
    js = app_js()
    start = js.index('function seBlock(')
    end = js.index('\nfunction ', start + 1)
    block = js[start:end]

    # seInstall(true) is the removal. seInstall(false) is the way back IN,
    # which is what the removed-state branch offers - not the same thing.
    check('the script extender can be removed from the UI',
          'seInstall(true)' in block,
          'no removal in seBlock - the sticky uninstall is unreachable')

    # Optional means the person installs it. A page that describes the
    # editor installing it by itself is describing the bug.
    check('the script extender is installed by a button, not by the editor',
          'seInstall(false)' in block and 'editor handles it' not in block,
          'seBlock has no install button, or still promises to install itself')

    # "what ARE these files?" should be answerable without uninstalling first.
    check('the file list is reachable without removing anything',
          'ownedFileList(L.se_files' in block.split('return h(')[-1],
          'the list is not part of every state')


def test_editor_can_remove_itself():
    """Everything the editor puts on a machine can be taken off from inside it,
    on a page that opens without a save."""
    js = app_js()
    start = js.index('function viewAbout(')
    end = js.index('\nfunction ', start + 1)
    check('the About page offers removal', 'removalCard()' in js[start:end])
    check('removal asks the server, and confirms first',
          "'/api/removal-plan'" in js and "'/api/remove-everything'" in js
          and "confirm: 'REMOVE'" in js)
    run = js[js.index('async function removalRun('):]
    run = run[:run.index('\nfunction ')]
    check('removal stops the page polling afterwards', 'liveStop()' in run)


def main():
    test_extender_is_removable()
    test_editor_can_remove_itself()
    test_ops_match()
    test_bag_add_is_wired()
    test_receipts_name_their_destination()
    test_nav_groups_are_real()
    print('\n%d passed, %d failed' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('  FAILED: %s' % f)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
