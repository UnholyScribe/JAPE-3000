"""Custom merc portraits, carried by the live link DLC - never by a mod.

A save does not store portraits at all - `Portrait` and `BigPortrait` belong
to the merc's preset - so a custom face has to reach the game some other way.
The editor installs nothing but its optional DLC, so that is the way:

    UI/JAPE3000Faces/<merc>_head.png    inside the live link's .hpk
    UI/JAPE3000Faces/<merc>_big.png

Every DLC's UI/ folder is mounted over the game's own UI/ (Dlc.lua,
DlcReloadAssets -> DlcMountUI), exactly where the stock portraits sit
(UI/MercsPortraits, UI/Mercs). The DLC's code then points the two fields at
them on load, leaving the merc's stats, perks and bio untouched.

The pictures themselves are kept in the editor's own folder, so they survive
the DLC being removed and reinstalled, and go when the editor is removed.

Sizes come from measuring the game's own art (UI.hpk: MercsPortraits/*.dds
300x300, Mercs/*.dds 2000x2000) and two working portrait mods, which ship the
same sizes as RGBA PNG.
"""
import json
import os
import re
import shutil

import gamepaths

UI_DIR = 'UI/JAPE3000Faces'
# Older builds wrote portraits as a mod. Only ever read, to bring those
# pictures across and then take the mod away.
LEGACY_MOD_ID = 'JA3SaveEditorFaces'

HEAD_SIZE = 300
# The big portrait is a 2000x2000 canvas (every UI/Mercs/*.dds is) holding a
# standing figure in its centre column, transparent either side. Screens cut
# that column out by *pixel position*, so the canvas must stay 2000 wide:
#
#     Inventory.lua            ImageRect box(550, 50, 1480, 1950)
#     PDAMercImageInspect.lua  ImageRect box(500, 0, 1520, 2000)
#     PDAAimEvaluation.lua     376x730 frame, ImageFit "largest" (centre strip)
#     ConversationDialog.lua   the whole canvas
#
# So the figure is a 1030x2000 slice of the photo, pasted centred.
BIG_W, BIG_H = 1030, 2000
BIG_CANVAS = 2000
BIG_SIZE = BIG_CANVAS            # kept: status() and older callers read this


def store_dir():
    """Where the pictures and the list live: the editor's own folder."""
    return os.path.join(gamepaths.local_data_dir(), 'portraits')


class PortraitError(Exception):
    pass


def have_pillow():
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def convert(src_path, out_dir, base, focus=None):
    """Turn any image into the two PNGs the game wants.

    focus is an optional (cx, cy, zoom) in 0..1 / >0 so the UI can let someone
    frame the crop; by default it centre-crops the largest square.
    """
    try:
        from PIL import Image
    except ImportError:
        raise PortraitError(
            'Image conversion needs Pillow. Install it with:  pip install Pillow')

    try:
        im = Image.open(src_path)
        im.load()
    except Exception as exc:                                     # noqa: BLE001
        raise PortraitError('could not read that image: %s' % exc)
    im = im.convert('RGBA')
    # Keep the untouched original. The square crop below replaces `im`, and the
    # tall big-portrait needs to cut its own shape out of the WHOLE picture -
    # taking a tall slice out of an already-square crop would throw away the
    # very part of the body that frame exists to show.
    src = im

    cx, cy, zoom = (focus or (0.5, 0.5, 1.0))
    if focus is None and im.height > im.width:
        # A dead centre crop of a portrait photo frames the chest, because
        # faces sit above the middle of the frame. Bias upward, but only for
        # taller-than-wide images - on a square headshot this would cut the
        # chin off. This is a better default, not a substitute for letting
        # someone frame it by hand.
        cy = 0.42
    side = int(min(im.width, im.height) / max(zoom, 0.05))
    side = max(8, min(side, min(im.width, im.height)))
    left = int(cx * im.width - side / 2)
    top = int(cy * im.height - side / 2)
    left = max(0, min(left, im.width - side))
    top = max(0, min(top, im.height - side))
    im = im.crop((left, top, left + side, top + side))

    os.makedirs(out_dir, exist_ok=True)
    written = {}
    for suffix, (w, h) in (('_head', (HEAD_SIZE, HEAD_SIZE)),
                           ('_big', (BIG_W, BIG_H))):
        if w == h:
            out = im.resize((w, h), Image.LANCZOS)          # the square crop
        else:
            # The tallest slice of the original that matches the frame, rather
            # than a stretched square - stretching a face is worse than showing
            # less of it. Biased slightly upward for the same reason the square
            # crop is: heads sit above the middle of a photograph.
            want = w / float(h)
            sw = max(8, min(src.width, int(src.height * want)))
            sh = max(8, min(src.height, int(sw / want)))
            left = max(0, (src.width - sw) // 2)
            top = max(0, min(src.height - sh, int(src.height * 0.02)))
            figure = src.crop((left, top, left + sw, top + sh))
            figure = figure.resize((w, h), Image.LANCZOS)
            out = Image.new('RGBA', (BIG_CANVAS, BIG_CANVAS), (0, 0, 0, 0))
            out.paste(figure, ((BIG_CANVAS - w) // 2, (BIG_CANVAS - h) // 2))
        path = os.path.join(out_dir, base + suffix + '.png')
        out.save(path, 'PNG', optimize=True)
        written[suffix.strip('_')] = path
    return written


def preview(src_path, focus=None, big_width=400):
    """The two pictures assign() would write, as PNG data URLs, without
    touching the mod. The big one is scaled down; its shape is unchanged."""
    import base64
    import io
    import tempfile
    from PIL import Image
    tmp = tempfile.mkdtemp(prefix='ja3-face-preview-')
    try:
        written = convert(src_path, tmp, 'preview', focus)
        out = {}
        for key, path in written.items():
            im = Image.open(path)
            if key == 'big':
                im = im.resize((big_width, big_width), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, 'PNG')
            out[key] = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _safe(name):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', name).strip('_') or 'merc'


def _list_path(folder):
    return os.path.join(folder, 'portraits.json')


def _image_file(folder, ref):
    """Disk path of a picture named in the list."""
    return os.path.join(folder, 'Images', ref.split('/')[-1])


def _present(folder, art):
    return all(os.path.isfile(_image_file(folder, art[k])) for k in ('head', 'big'))


def read_assignments(folder=None, only_present=False):
    """{merc_id: {'head': 'UI/JAPE3000Faces/x_head.png', 'big': ...}}.
    only_present skips any whose picture files are missing."""
    folder = folder or store_dir()
    try:
        with open(_list_path(folder), encoding='utf-8') as f:
            got = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for mid, art in (got or {}).items():
        if not isinstance(art, dict) or not art.get('head') or not art.get('big'):
            continue
        if only_present and not _present(folder, art):
            continue
        out[mid] = {'head': art['head'], 'big': art['big']}
    return out


def _write_list(assignments, folder):
    os.makedirs(os.path.join(folder, 'Images'), exist_ok=True)
    tmp = _list_path(folder) + '.part'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(assignments, f, indent=1, sort_keys=True)
    os.replace(tmp, _list_path(folder))


def broken_assignments(folder=None):
    """Merc ids whose picture files are gone."""
    folder = folder or store_dir()
    return sorted(mid for mid, art in read_assignments(folder).items()
                  if not _present(folder, art))


def drop_broken(folder=None):
    """Forget assignments whose files are gone. Returns the ids dropped."""
    folder = folder or store_dir()
    broken = broken_assignments(folder)
    if broken:
        _write_list(read_assignments(folder, only_present=True), folder)
    return broken


def assign(merc_id, image_path, focus=None, folder=None):
    """Give one merc a custom face. Returns the updated assignment map.
    Call apply() afterwards to put it into the game."""
    folder = folder or store_dir()
    base = _safe(merc_id)
    written = convert(image_path, os.path.join(folder, 'Images'), base, focus)
    current = read_assignments(folder, only_present=True)
    current[merc_id] = {
        'head': '%s/%s' % (UI_DIR, os.path.basename(written['head'])),
        'big': '%s/%s' % (UI_DIR, os.path.basename(written['big'])),
    }
    _write_list(current, folder)
    return current


def unassign(merc_id, folder=None):
    folder = folder or store_dir()
    current = read_assignments(folder, only_present=True)
    gone = current.pop(merc_id, None)
    _write_list(current, folder)
    for ref in (gone or {}).values():
        try:
            os.remove(_image_file(folder, ref))
        except OSError:
            pass
    return current


# -- what goes into the DLC ----------------------------------------------

def dlc_files(folder=None):
    """{path inside the pack: bytes} for every portrait whose files exist."""
    folder = folder or store_dir()
    out = {}
    for art in read_assignments(folder, only_present=True).values():
        for ref in (art['head'], art['big']):
            with open(_image_file(folder, ref), 'rb') as f:
                out[ref] = f.read()
    return out


def dlc_lua(folder=None):
    """The DLC code that points each merc at its picture. Empty when there are
    no portraits, so a DLC without any is byte-for-byte what it always was."""
    assignments = read_assignments(folder or store_dir(), only_present=True)
    if not assignments:
        return ''
    rows = ''.join(
        '\t["%s"] = { head = "%s", big = "%s" },\n' % (mid, v['head'], v['big'])
        for mid, v in sorted(assignments.items()))
    return (
        '\n-- ---- custom portraits (JAPE 3000) ----------------------------------\n'
        '-- The pictures are inside this pack under UI/, which the game mounts\n'
        '-- over its own UI/ folder. Two stores are read by different screens:\n'
        '-- UnitDataDefs (tactical, inventory, conversations) and the preset\n'
        '-- (A.I.M. laptop), so both are set.\n'
        'do\n'
        'local JAPE_PORTRAITS = {\n%s}\n'
        'local function JapeSet(o, art)\n'
        '\tif type(o) ~= "table" then return end\n'
        '\to.Portrait = art.head\n'
        '\to.BigPortrait = art.big\n'
        '\tif type(o.SetProperty) == "function" then\n'
        '\t\tpcall(function()\n'
        '\t\t\to:SetProperty("Portrait", art.head)\n'
        '\t\t\to:SetProperty("BigPortrait", art.big)\n'
        '\t\tend)\n'
        '\tend\n'
        'end\n'
        'local function JapeApplyPortraits()\n'
        '\tlocal defs = rawget(_G, "UnitDataDefs")\n'
        '\tlocal presets = rawget(_G, "Presets")\n'
        '\tlocal groups = presets and presets.UnitDataCompositeDef\n'
        '\tfor id, art in pairs(JAPE_PORTRAITS) do\n'
        '\t\tJapeSet(defs and defs[id], art)\n'
        '\t\tfor _, grp in pairs(groups or {}) do\n'
        '\t\t\tif type(grp) == "table" then\n'
        '\t\t\t\tfor _, p in pairs(grp) do\n'
        '\t\t\t\t\tif type(p) == "table" and rawget(p, "id") == id then JapeSet(p, art) end\n'
        '\t\t\t\tend\n'
        '\t\t\tend\n'
        '\t\tend\n'
        '\tend\n'
        'end\n'
        'rawset(_G, "JAPE_ApplyPortraits", JapeApplyPortraits)\n'
        'local on = rawget(_G, "OnMsg")\n'
        'if on then\n'
        '\ton.DataLoaded = JapeApplyPortraits\n'
        '\ton.ModsReloaded = JapeApplyPortraits\n'
        'end\n'
        'pcall(JapeApplyPortraits)\n'
        'end\n' % rows)


def apply():
    """Put the current portraits into the game, if the live link is installed.
    Returns (in_game, message)."""
    import build_livelink
    installed, up_to_date, _path = build_livelink.state()
    if not installed:
        return (False, 'Saved. To see it in the game, install the live link '
                '(Live link page) - it carries the portraits. No mod is written.')
    if up_to_date:
        return True, 'Already in the live link.'
    ok, msg, _p = build_livelink.install()
    if ok:
        return True, 'Added to the live link. Restart the game to see it.'
    return False, 'Saved, but the live link could not be updated: ' + msg


# -- older builds wrote a mod; bring its pictures across, then remove it --

def legacy_mod_folder(mods=None):
    root = mods if mods is not None else gamepaths.mods_dir()
    return os.path.join(root, LEGACY_MOD_ID) if root else None


def migrate_legacy(mods=None, folder=None):
    """Move portraits out of the old JA3SaveEditorFaces mod into the store and
    delete the mod. Returns the merc ids brought across."""
    old = legacy_mod_folder(mods)
    if not old or not os.path.isdir(old):
        return []
    folder = folder or store_dir()
    src = ''
    try:
        with open(os.path.join(old, 'Code', 'portraits.lua'), encoding='utf-8') as f:
            src = f.read()
    except OSError:
        pass
    current = read_assignments(folder, only_present=True)
    moved = []
    images = os.path.join(old, 'Images')
    try:
        names = sorted(os.listdir(images))
    except OSError:
        names = []
    for name in names:
        if not name.endswith('_head.png'):
            continue
        base = name[:-len('_head.png')]
        files = [os.path.join(images, base + s) for s in ('_head.png', '_big.png')]
        if not os.path.isfile(files[1]):
            continue
        # The file name is the merc id made file-safe; the old code names the
        # real id on the same line as the picture, when it differs.
        m = re.search(r'\["([^"]+)"\][^\n]*/%s_head\.png' % re.escape(base), src)
        mid = m.group(1) if m else base
        if mid in current:
            continue
        os.makedirs(os.path.join(folder, 'Images'), exist_ok=True)
        for p in files:
            shutil.copy2(p, os.path.join(folder, 'Images', os.path.basename(p)))
        current[mid] = {'head': '%s/%s' % (UI_DIR, os.path.basename(files[0])),
                        'big': '%s/%s' % (UI_DIR, os.path.basename(files[1]))}
        moved.append(mid)
    if moved:
        _write_list(current, folder)
    shutil.rmtree(old, ignore_errors=True)
    return moved


def status():
    try:
        migrate_legacy()
    except Exception:                                        # noqa: BLE001
        pass
    folder = store_dir()
    try:
        import build_livelink
        live = build_livelink.state()[0]
    except Exception:                                        # noqa: BLE001
        live = False
    return {
        'folder': folder,
        'assignments': read_assignments(folder, only_present=True),
        'broken': broken_assignments(folder),
        'pillow': have_pillow(),
        'head_size': HEAD_SIZE,
        'big_size': BIG_SIZE,
        'live_link_installed': live,
    }
