"""Decode Jagged Alliance 2's faces, item icons and laptop skin into the
editor's local cache, the same way the running editor does on first start.

    python build_ja2_art.py ["C:\\path\\to\\Jagged Alliance 2"]

This used to write PNGs into webui/ so they travelled inside the exe. They no
longer do - the editor is published on Nexus Mods, which does not allow one
game's art inside a mod for another - so the work moved to ja2art.py and runs
on the player's machine against their own install. This script is only a way
to run that by hand and see what it finds.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ja2art                                                    # noqa: E402


def main(argv):
    roots = [argv[1]] if len(argv) > 1 else None
    got = ja2art.build(roots)
    if not got:
        raise SystemExit('No Jagged Alliance 2 install found. Pass the folder:\n'
                         '    python build_ja2_art.py "D:\\Games\\Jagged Alliance 2"')
    print('read from: %s' % ', '.join(got['roots']))
    print('%d faces, %d item icons, skin: %s' % (
        len(got['faces']), len(got['icons']), ', '.join(got['skin']) or 'none'))
    print('cache: %s' % ja2art.cache_dir())
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
