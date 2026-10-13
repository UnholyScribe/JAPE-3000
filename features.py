"""The one list of things a save reader can be asked to back.

Why this file exists: `supports` started as "list what you cannot do", and
anything nobody remembered to list was silently opted IN. That is how the
Portrait button ended up offered on a Jagged Alliance 2 merc, pointing at
Jagged Alliance 3's mods folder. The bug was not the missing entry - it was
that a missing entry meant yes.

So the rule is now the other way round:

    a reader must name every feature, and the UI shows a section only when
    its reader says True.

Forgetting one hides a section that should have shown, which is a visible,
harmless bug. The old direction offered a control that wrote somewhere wrong,
which is neither.

`test_features.py` asserts every reader declares every id here, so adding a
feature to this list without teaching both readers about it fails a test
instead of shipping.
"""

FEATURES = (
    'perks',            # JA3 perk chips; JA2 traits are a different shape
    'gear',             # inventory slots
    'squads',
    'sectors',
    'stash',            # the shared squad bag
    'cheats',
    'roster',           # hiring from A.I.M.
    'new_merc',         # custom I.M.P. mercs
    'import_merc',      # bring a merc in from another save
    'export_merc',
    'portraits',        # replacing a merc's portrait
    'mods',             # the mod list, and what the game actually loaded
    'live',             # editing the running game
)


def all_true():
    return {f: True for f in FEATURES}


def only(*supported):
    """Everything off except the named few."""
    unknown = [f for f in supported if f not in FEATURES]
    if unknown:
        raise ValueError('not features: %s' % ', '.join(unknown))
    return {f: f in supported for f in FEATURES}
