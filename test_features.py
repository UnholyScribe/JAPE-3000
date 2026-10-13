"""Every reader must declare every feature.

    python test_features.py

This is the test that would have caught the Portrait button appearing on a
Jagged Alliance 2 merc. `supports` used to mean "list what you cannot do", so
`portraits` - which nobody thought to list - was silently treated as supported,
and the button offered to write into the other game's mods folder.

Declaring is now mandatory, and this asserts it. Add an id to
features.FEATURES without teaching both readers about it and this fails, rather
than a section quietly opting itself in on whichever reader forgot.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import features                                                # noqa: E402
import ja2_save                                                # noqa: E402
import ja3_save                                                # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print('%s %s%s' % ('  ok  ' if cond else '  FAIL', name,
                       ('  - ' + detail) if detail and not cond else ''))


READERS = (('ja3_save', ja3_save.Save), ('ja2_save', ja2_save.Save))


def main():
    want = set(features.FEATURES)
    check('the feature list has no duplicates',
          len(features.FEATURES) == len(want))

    for name, cls in READERS:
        got = set(getattr(cls, 'SUPPORTS', {}))
        missing = sorted(want - got)
        extra = sorted(got - want)
        check('%s declares every feature' % name, not missing,
              'never declares: ' + ', '.join(missing))
        check('%s declares nothing invented' % name, not extra,
              'not in features.FEATURES: ' + ', '.join(extra))
        check('%s answers every feature with a bool' % name,
              all(isinstance(v, bool) for v in getattr(cls, 'SUPPORTS', {}).values()))

    # The UI reads only `=== true`, so a None or a missing key hides a section.
    # That is the safe direction, but it means JA3 must really say yes.
    check('ja3 backs everything it always did',
          all(ja3_save.Save.SUPPORTS.values()),
          'off: ' + ', '.join(k for k, v in ja3_save.Save.SUPPORTS.items() if not v))

    # JA2 edits stats and nothing else yet. If that changes, change this line
    # deliberately - it exists so the change is a decision, not a drift.
    on = sorted(k for k, v in ja2_save.Save.SUPPORTS.items() if v)
    check('ja2 backs nothing beyond stats yet', on == [], 'on: ' + ', '.join(on))

    # features.only() is what ja2 uses; make sure it cannot lie
    try:
        features.only('not_a_real_feature')
        check('only() refuses an unknown id', False, 'no error raised')
    except ValueError:
        check('only() refuses an unknown id', True)

    print('\n%d passed, %d failed' % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
