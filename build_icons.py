"""Extract the game's item and attachment icons into webui/icons/.

    python build_icons.py ["C:\\path\\to\\Jagged Alliance 3"]

Reads UI.hpk straight from an installed copy of the game, decodes the BC7
textures, trims the transparent margin and writes optimised PNGs. Run once:
the PNGs are then bundled into the exe, so the finished app never needs the
game installed.

Needs Pillow (build time only): python -m pip install Pillow
"""
import io
import json
import os
import re
import sys

import hpk

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'webui', 'icons')
PORTRAIT_DIR = os.path.join(HERE, 'webui', 'portraits')
CATALOG = os.path.join(HERE, 'catalog.json')

# Where the game is, if it isn't passed on the command line.
GUESSES = [
    r'E:\Jagged Alliance 3',
    r'C:\Program Files (x86)\Steam\steamapps\common\Jagged Alliance 3',
]
for _d in 'CDEFGHIJ':
    GUESSES.append(r'%s:\SteamLibrary\steamapps\common\Jagged Alliance 3' % _d)
    GUESSES.append(r'%s:\Jagged Alliance 3' % _d)

MAX_W = 200          # icons are drawn small; no point shipping more
PORTRAIT_W = 160     # merc faces, shown at avatar size
PREFIX = 'UI/'       # the UI pack's root is the UI/ folder itself


def find_game(argv):
    if len(argv) > 1:
        return argv[1]
    for g in GUESSES:
        if os.path.isfile(os.path.join(g, 'Packs', 'UI.hpk')):
            return g
    raise SystemExit('could not find the game - pass its folder as an argument')


def wanted_icons(catalog):
    """Every icon path the catalog refers to, mapped to a flat output name."""
    out = {}
    for entry in (catalog['items'] + catalog.get('components', [])
                  + catalog.get('perks', [])):
        path = entry.get('icon')
        if not path:
            continue
        out[path] = flat_name(path)
    return out


def wanted_portraits(catalog):
    """The head portrait of every merc the catalog knows."""
    out = {}
    for m in catalog.get('mercs', []):
        if not m.get('hireable'):
            continue                             # enemies never appear in the UI
        path = m.get('portrait')
        if path and path.startswith(PREFIX):     # mod portraits are read live
            out[path] = flat_name(path)
    return out


def flat_name(path):
    """'UI/Icons/Weapons/AK47' -> 'weapons_ak47'.

    A handful of the game's textures have spaces in their names; those become
    underscores so every icon is a URL-safe filename.
    """
    rel = path[len(PREFIX):] if path.startswith(PREFIX) else path
    for p in ('Icons/', 'Hud/'):
        if rel.startswith(p):
            rel = rel[len(p):]
            break
    rel = os.path.splitext(rel)[0].replace('/', '_').lower()
    return re.sub(r'[^a-z0-9._-]+', '_', rel).strip('_')


def convert(raw, max_w=MAX_W):
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert('RGBA')
    bbox = im.getbbox()                       # drop the transparent margin
    if bbox and bbox != (0, 0, im.width, im.height):
        im = im.crop(bbox)
    if im.width > max_w:
        h = max(1, round(im.height * max_w / im.width))
        im = im.resize((max_w, h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'PNG', optimize=True)
    return buf.getvalue(), im.size


def main():
    game = find_game(sys.argv)
    ui_pack = os.path.join(game, 'Packs', 'UI.hpk')
    print('game : %s' % game)

    with open(CATALOG, encoding='utf-8') as f:
        catalog = json.load(f)
    want = wanted_icons(catalog)
    print('icons referenced by the catalog: %d' % len(want))

    os.makedirs(OUT_DIR, exist_ok=True)
    done, portraits, missing, total_bytes = {}, {}, [], 0

    with hpk.Pack(ui_pack) as pack:
        # one pass over the pack, so each icon is found by name not by search
        # keyed both with and without the extension: most Icon fields omit it,
        # but a few (DLC items) name the file outright
        index = {}
        for root in ('Icons', 'Hud'):
            for full, fid in pack.walk(root):
                key = ('UI/' + full).lower()
                index[key] = fid
                index[os.path.splitext(key)[0]] = fid

        for path, name in sorted(want.items()):
            fid = index.get(path.lower()) or index.get(os.path.splitext(path)[0].lower())
            if fid is None:
                missing.append(path)
                continue
            try:
                png, size = convert(pack.read_id(fid))
            except Exception as exc:                          # noqa: BLE001
                missing.append('%s (%s)' % (path, exc))
                continue
            with open(os.path.join(OUT_DIR, name + '.png'), 'wb') as f:
                f.write(png)
            done[path] = name
            total_bytes += len(png)

        # merc faces, from UI/MercsPortraits
        for full, fid in pack.walk('MercsPortraits'):
            index[('UI/' + full).lower()] = fid
            index[os.path.splitext(('UI/' + full).lower())[0]] = fid
        os.makedirs(PORTRAIT_DIR, exist_ok=True)
        for path, name in sorted(wanted_portraits(catalog).items()):
            fid = index.get(path.lower()) or index.get(os.path.splitext(path)[0].lower())
            if fid is None:
                missing.append(path)
                continue
            try:
                png, _size = convert(pack.read_id(fid), PORTRAIT_W)
            except Exception as exc:                          # noqa: BLE001
                missing.append('%s (%s)' % (path, exc))
                continue
            with open(os.path.join(PORTRAIT_DIR, name + '.png'), 'wb') as f:
                f.write(png)
            portraits[path] = name
            total_bytes += len(png)

    manifest = os.path.join(OUT_DIR, 'index.json')
    with open(manifest, 'w', encoding='utf-8') as f:
        json.dump(done, f, separators=(',', ':'), sort_keys=True)
    with open(os.path.join(PORTRAIT_DIR, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(portraits, f, separators=(',', ':'), sort_keys=True)

    print('extracted %d icons and %d portraits, %.1f MB'
          % (len(done), len(portraits), total_bytes / 1e6))
    print('manifest : %s' % manifest)
    if missing:
        print('no texture found for %d:' % len(missing))
        for m in missing[:15]:
            print('   %s' % m)


if __name__ == '__main__':
    main()
