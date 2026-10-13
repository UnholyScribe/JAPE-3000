"""Coverage for mods.py and the Steam side of gamepaths.py.

    python test_mods.py

Written for the workshop_roots() addition. The prior symptom was every
subscribed Steam Workshop mod reading back as MISSING on Jaeger's tester
report, because the enumerator only looked in %APPDATA%\\Jagged Alliance 3\\
Mods\\.

Extended for Steam installs proper. A Workshop item is a folder holding only
ModContent.hpk - metadata.lua and items.lua live inside the pack - so a scan
that looked for a loose metadata.lua listed none of them. And Steam libraries
sit wherever the user put them, which only Steam's own registry key and
libraryfolders.vdf can say.

Everything is built in a temp folder: a fake Steam client, a second library
named only in its libraryfolders.vdf, a renamed JA3 install found through its
app manifest, and real packs written with hpk_write. Nothing on this machine's
Steam or mods folders is read for the assertions or touched.
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gamepaths                                               # noqa: E402
import hpk_write                                               # noqa: E402
import modconflicts                                            # noqa: E402
import mods                                                    # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ok   %s' % name)
    else:
        FAIL += 1
        print('  FAIL %s   %s' % (name, detail))


def _metadata(mod_id, title, version=1, extra=''):
    """A metadata.lua in the shape the Mod Editor writes: single-quoted
    PlaceObj, tab-indented positional properties."""
    return (
        "return PlaceObj('ModDef', {\n"
        "\t'title', \"%s\",\n"
        "%s"
        "\t'id', \"%s\",\n"
        "\t'version', %d,\n"
        "\t'saved', 0,\n"
        "})\n"
    ) % (title, extra, mod_id, version)


def _write_mod(folder, mod_id, title, version=1, extra=''):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'metadata.lua'), 'w', encoding='utf-8') as f:
        f.write(_metadata(mod_id, title, version, extra))


# items.lua with the perk inside a ModItemFolder - the nesting real mods use,
# which puts the perk's own properties four tabs deep.
NESTED_ITEMS = (
    "return {\n"
    "PlaceObj('ModItemFolder', {\n"
    "\t'name', \"Perks\",\n"
    "}, {\n"
    "\tPlaceObj('ModItemFolder', {\n"
    "\t\t'name', \"Personal\",\n"
    "\t}, {\n"
    "\t\tPlaceObj('ModItemCharacterEffectCompositeDef', {\n"
    "\t\t\t'Group', \"Perk-Personal\",\n"
    "\t\t\t'Id', \"PackedPerk\",\n"
    "\t\t\t'object_class', \"Perk\",\n"
    "\t\t\t'DisplayName', T(1, \"Packed Perk\"),\n"
    "\t\t\t'Tier', \"Personal\",\n"
    "\t\t}),\n"
    "\t}),\n"
    "}),\n"
    "}\n")


def _write_packed(folder, mod_id, title, version=1, extra='', compress=True,
                  code=''):
    """A Workshop-shaped mod: the folder holds ModContent.hpk and nothing
    else, the way Steam delivers it."""
    files = {
        'metadata.lua': _metadata(mod_id, title, version, extra).encode(),
        'items.lua': NESTED_ITEMS.encode(),
        'Images/face.png': b'\x89PNG\r\n\x1a\nfake',
    }
    if code:
        files['Code/Main.lua'] = code.encode()
    os.makedirs(folder, exist_ok=True)
    hpk_write.pack_files(files, os.path.join(folder, mods.PACK_NAME),
                         compress=compress)


class patched:
    """Swap module attributes for the length of a with-block."""
    def __init__(self, *triples):
        self.triples = triples

    def __enter__(self):
        self.saved = [(m, n, getattr(m, n)) for m, n, _ in self.triples]
        for m, n, v in self.triples:
            setattr(m, n, v)

    def __exit__(self, *a):
        for m, n, v in self.saved:
            setattr(m, n, v)


def _fake_steam(tmp):
    r"""C:\...\Steam (the client) whose libraryfolders.vdf names a second
    library elsewhere, which holds JA3 installed under a non-default folder
    name and two Workshop items."""
    client = os.path.join(tmp, 'Steam')
    lib2 = os.path.join(tmp, 'Games', 'SteamStuff')
    os.makedirs(os.path.join(client, 'steamapps'))
    apps2 = os.path.join(lib2, 'steamapps')
    os.makedirs(apps2)
    esc = lambda p: p.replace('\\', '\\\\')                 # noqa: E731
    with open(os.path.join(client, 'steamapps', 'libraryfolders.vdf'), 'w',
              encoding='utf-8') as f:
        f.write('"libraryfolders"\n{\n'
                '\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t\t"label"\t\t""\n\t}\n'
                '\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t\t"apps"\n\t\t{\n'
                '\t\t\t"%s"\t\t"123"\n\t\t}\n\t}\n}\n'
                % (esc(client), esc(lib2), gamepaths.STEAM_APPID))
    with open(os.path.join(apps2, 'appmanifest_%s.acf' % gamepaths.STEAM_APPID),
              'w', encoding='utf-8') as f:
        f.write('"AppState"\n{\n\t"appid"\t\t"%s"\n'
                '\t"installdir"\t\t"JA3 Renamed"\n}\n' % gamepaths.STEAM_APPID)
    game = os.path.join(apps2, 'common', 'JA3 Renamed')
    os.makedirs(os.path.join(game, 'Packs'))
    ws = os.path.join(apps2, 'workshop', 'content', gamepaths.STEAM_APPID)
    os.makedirs(ws)
    return client, lib2, game, ws


def test_vdf_both_shapes():
    new = '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"D:\\\\Steam Lib"\n\t}\n}'
    old = '"LibraryFolders"\n{\n\t"TimeNextStatsReport"\t\t"1"\n' \
          '\t"1"\t\t"E:\\\\SteamLibrary"\n}'
    check('libraryfolders.vdf: current "path" shape',
          gamepaths._vdf_library_paths(new) == ['D:\\Steam Lib'],
          gamepaths._vdf_library_paths(new))
    check('libraryfolders.vdf: old numbered shape, ignoring non-paths',
          gamepaths._vdf_library_paths(old) == ['E:\\SteamLibrary'],
          gamepaths._vdf_library_paths(old))


def test_library_of():
    lib = gamepaths.steam_library_of
    check('library of a library install',
          lib(r'D:\SteamLibrary\steamapps\common\Jagged Alliance 3')
          == r'D:\SteamLibrary')
    check('library at a drive root keeps its backslash',
          lib(r'D:\steamapps\common\Jagged Alliance 3') == 'D:\\')
    check('a GOG folder has no library', lib(r'E:\Jagged Alliance 3') is None)
    check('no folder, no library', lib(None) is None)


def test_steam_discovery():
    tmp = tempfile.mkdtemp(prefix='ja3-steam-')
    try:
        client, lib2, game, ws = _fake_steam(tmp)
        with patched((gamepaths, 'steam_roots', lambda: [client]),
                     (gamepaths, 'game_dir', lambda rescan=False: None)):
            libs = [os.path.normcase(p) for p in gamepaths.steam_libraries()]
            check('the client folder is a library',
                  os.path.normcase(client) in libs, libs)
            check('a library named only in libraryfolders.vdf is found',
                  os.path.normcase(lib2) in libs, libs)
            dirs = [os.path.normcase(p) for p in gamepaths.steam_game_dirs()]
            check('the app manifest names a renamed install folder',
                  os.path.normcase(game) in dirs, dirs)
            check('the renamed install looks like the game',
                  gamepaths.looks_like_game(game))
            cands = [os.path.normcase(p) for p in gamepaths.candidates()]
            check('the game finder tries the Steam install',
                  os.path.normcase(game) in cands)
            roots = [os.path.normcase(p) for p in mods.workshop_roots()]
            check('Workshop folder of a vdf-only library is scanned',
                  os.path.normcase(ws) in roots, roots)

        # The game's own library counts even when Steam left no trace.
        with patched((gamepaths, 'steam_roots', lambda: []),
                     (gamepaths, 'game_dir', lambda rescan=False: game)):
            roots = [os.path.normcase(p) for p in mods.workshop_roots()]
            check('Workshop folder next to the game is scanned without Steam',
                  os.path.normcase(ws) in roots, roots)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _scan_only(installed, ws):
    return patched((mods, 'installed_root', lambda: installed),
                   (mods, 'misplaced_root', lambda: None),
                   (mods, 'elsewhere_roots', lambda: []),
                   (mods, 'workshop_roots', lambda: [ws]))


def test_packed_workshop_mod():
    tmp = tempfile.mkdtemp(prefix='ja3-pack-')
    try:
        ws = os.path.join(tmp, 'workshop', mods.JA3_STEAM_APPID)
        appdata = os.path.join(tmp, 'AppDataMods')
        os.makedirs(appdata)
        deps = ("\t'dependencies', {\n"
                "\t\tPlaceObj('ModDependency', {\n"
                "\t\t\t'id', \"NeedsMe\",\n"
                "\t\t\t'title', \"A Required Mod\",\n"
                "\t\t}),\n"
                "\t\tPlaceObj('ModDependency', {\n"
                "\t\t\t'id', \"NiceToHave\",\n"
                "\t\t\t'title', \"An Optional Mod\",\n"
                "\t\t\t'required', false,\n"
                "\t\t}),\n"
                "\t},\n"
                "\t'saved_with_revision', 300000,\n"
                "\t'steam_id', \"3200000001\",\n")
        clash = 'function SharedGlobal()\nend\n'
        _write_packed(os.path.join(ws, '3200000001'), 'PackedOne',
                      'Packed One', version=7, extra=deps, code=clash)
        _write_packed(os.path.join(ws, '3200000002'), 'PackedRaw',
                      'Packed Uncompressed', compress=False)
        # Both a pack and a stale loose metadata.lua: the game reads the pack.
        both = os.path.join(ws, '3200000003')
        _write_packed(both, 'BothKinds', 'From The Pack')
        with open(os.path.join(both, 'metadata.lua'), 'w') as f:
            f.write(_metadata('BothKinds', 'From The Loose File'))
        # A half-downloaded pack must not take the scan down with it.
        bad = os.path.join(ws, '3200000004')
        os.makedirs(bad)
        with open(os.path.join(bad, mods.PACK_NAME), 'wb') as f:
            f.write(b'BPUL' + b'\0' * 10)
        # A loose AppData mod defining the same global as the packed one.
        loose = os.path.join(appdata, 'LooseOne')
        _write_mod(loose, 'LooseOne', 'Loose One',
                   extra="\t'saved_with_revision', 366685,\n")
        os.makedirs(os.path.join(loose, 'Code'))
        with open(os.path.join(loose, 'Code', 'x.lua'), 'w') as f:
            f.write(clash)

        with _scan_only(appdata, ws):
            found = mods.find_mods()
            scanned = mods.scan()
        by_id = {m.get('id'): m for m in found}

        m = by_id.get('PackedOne')
        check('a Workshop folder holding only ModContent.hpk is listed',
              m is not None, sorted(by_id))
        if m is None:
            return
        check('...as installed', m.get('installed') is True)
        check('...from Steam, with its Workshop id',
              m.get('source') == 'steam' and m.get('workshop_id') == '3200000001',
              (m.get('source'), m.get('workshop_id')))
        check('...marked packed', m.get('packed') is True)
        check('...with the version from inside the pack', m.get('version') == 7)
        check('...and its steam_id', m.get('steam_id') == '3200000001')
        check('...and the revision it was saved with',
              m.get('saved_with_revision') == 300000)
        deps_read = {d['id']: d['required'] for d in m.get('dependencies', [])}
        check('dependencies read, optional one marked so',
              deps_read == {'NeedsMe': True, 'NiceToHave': False}, deps_read)

        content = mods.read_mod_content(m)
        check('items.lua is read from inside the pack, through two folders',
              [p['id'] for p in content['perks']] == ['PackedPerk'],
              content['perks'])
        check('uncompressed packs read too',
              'PackedRaw' in by_id
              and mods.read_mod_content(by_id['PackedRaw'])['perks'])
        check('a mod image comes out of the pack',
              mods.read_mod_bytes(m['folder'], 'Images/face.png')
              == b'\x89PNG\r\n\x1a\nfake')
        check('paths cannot climb out of a mod',
              mods.read_mod_bytes(m['folder'], '../x') is None)
        check('Lua inside the pack is visible to the conflict check',
              [n for n, _ in mods.iter_lua_files(m['folder'])
               if n.endswith('Main.lua')] == ['Code/Main.lua'])

        b = by_id.get('BothKinds')
        check('a folder with a pack and a loose file is read from the pack',
              b is not None and b.get('title') == 'From The Pack',
              b and b.get('title'))
        check('a damaged pack is skipped, not fatal',
              all(os.path.basename(x['folder']) != '3200000004' for x in found))
        check('the loose AppData mod is still listed, as loose',
              by_id.get('LooseOne', {}).get('packed') is False
              and by_id['LooseOne'].get('source') == 'appdata')

        report = modconflicts.analyse(scanned)
        kinds = {(f['kind'], f['what']) for f in report['findings']}
        check('a function clash between a packed and a loose mod is found',
              ('function', 'SharedGlobal()') in kinds, kinds)
        check('a missing required dependency is reported by its title',
              ('missing', 'A Required Mod') in kinds, kinds)
        check('a missing optional dependency is not',
              ('missing', 'An Optional Mod') not in kinds)
        check('a mod built for a much older game is flagged',
              ('age', 'Packed One') in kinds, kinds)

        dest = os.path.join(tmp, 'InstallHere')
        os.makedirs(dest)
        with patched((gamepaths, 'mods_dir', lambda create=False: dest)):
            ok, where = mods.install_mod(by_id['PackedRaw']['folder'])
        check('a packed mod can be installed (copied) like a loose one',
              ok and os.path.isfile(os.path.join(where, mods.PACK_NAME)),
              where)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_nested_items_in_a_loose_mod():
    """The same folder nesting in a plain items.lua. Before the fix every item
    below the top level read as empty."""
    tmp = tempfile.mkdtemp(prefix='ja3-nest-')
    try:
        _write_mod(tmp, 'Nested', 'Nested')
        with open(os.path.join(tmp, 'items.lua'), 'w', encoding='utf-8') as f:
            f.write(NESTED_ITEMS)
        content = mods.read_mod_content({'folder': tmp, 'title': 'Nested'})
        perks = content['perks']
        check('a perk four tabs deep is read',
              [p['id'] for p in perks] == ['PackedPerk'], perks)
        check('...with its name and tier',
              perks and perks[0]['name'] == 'Packed Perk'
              and perks[0]['tier'] == 'Personal', perks)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_store_label():
    tmp = tempfile.mkdtemp(prefix='ja3-store-')
    try:
        gog = os.path.join(tmp, 'Anywhere', 'JA3')
        os.makedirs(gog)
        # GOG's build ships Steam's dll as well; its own marker must win
        for n in ('goggame-1556263729.info', 'steam_api64.dll'):
            open(os.path.join(gog, n), 'w').close()
        steam = os.path.join(tmp, 'Lib', 'steamapps', 'common', 'JA3')
        os.makedirs(steam)
        check('a GOG install is labelled GOG, despite steam_api64.dll',
              gamepaths.store_of(gog) == 'gog', gamepaths.store_of(gog))
        check('an install in a Steam library is labelled Steam',
              gamepaths.store_of(steam) == 'steam')
        check('no folder, no label', gamepaths.store_of(None) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_drive_search():
    """The last resort: an install no installer recorded, anywhere."""
    tmp = tempfile.mkdtemp(prefix='ja3-walk-')
    try:
        deep = os.path.join(tmp, 'My Stuff', 'old games', 'strategy', 'JA3 copy')
        os.makedirs(os.path.join(deep, 'Packs'))
        open(os.path.join(deep, 'JA3.exe'), 'w').close()
        # a sibling Haemimont game: Packs, but no JA3.exe
        decoy = os.path.join(tmp, 'Surviving Mars')
        os.makedirs(os.path.join(decoy, 'Packs'))
        # and one hidden where nothing is ever installed
        skipped = os.path.join(tmp, 'Windows', 'JA3')
        os.makedirs(os.path.join(skipped, 'Packs'))
        open(os.path.join(skipped, 'JA3.exe'), 'w').close()
        found = gamepaths.search_drives([tmp])
        check('a JA3 four folders deep under any name is found',
              found and os.path.normcase(found) == os.path.normcase(deep), found)
        check('...too deep is left alone',
              gamepaths.search_drives([tmp], max_depth=2) is None)
        os.remove(os.path.join(deep, 'JA3.exe'))
        check('a Packs folder without JA3.exe is not taken for the game',
              gamepaths.search_drives([tmp]) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_search_runs_once_in_background():
    import threading
    import time
    calls = []
    gate = threading.Event()
    settings = {}

    def slow_search(**_kw):
        calls.append(1)
        gate.wait(5)
        return answer[0]

    answer = [None]
    with patched((gamepaths, 'candidates', lambda: []),
                 (gamepaths, 'search_drives', slow_search),
                 (gamepaths, 'load_settings', lambda: dict(settings)),
                 (gamepaths, 'save_settings',
                  lambda d: settings.update(d) or True),
                 (gamepaths, '_GAME_CACHE', None),
                 (gamepaths, '_GAME_MISSING', False),
                 (gamepaths, '_SEARCH', None)):
        t0 = time.monotonic()
        first = gamepaths.game_dir()
        check('the screen is not held while the drives are searched',
              first is None and time.monotonic() - t0 < 1.0)
        check('...and the search is reported as running', gamepaths.searching())
        gamepaths.game_dir()
        gamepaths.game_dir()
        check('asking again while it runs does not start another', len(calls) == 1)
        gate.set()
        gamepaths._SEARCH.join(5)
        for _ in range(5):
            gamepaths.game_dir()
        check('once it found nothing, refreshing does not search again',
              len(calls) == 1 and not gamepaths.searching(), len(calls))

        gate.clear()
        answer[0] = r'X:\Found It'
        gamepaths.game_dir(rescan=True)
        check('"Search again" does search again', len(calls) == 2)
        gate.set()
        got = gamepaths.game_dir(rescan=False, wait=True)
        check('the command line can wait for the answer', got == r'X:\Found It', got)
        check('...which is remembered', settings.get('game_dir') == r'X:\Found It')
        check('and returned from then on without searching',
              gamepaths.game_dir() == r'X:\Found It' and len(calls) == 2)


def _fake_store(tmp, art):
    """A portrait store with some of the listed pictures present."""
    import json
    img = os.path.join(tmp, 'Images')
    os.makedirs(img, exist_ok=True)
    with open(os.path.join(tmp, 'portraits.json'), 'w', encoding='utf-8') as f:
        json.dump(art, f)


def _run_portrait_lua(src, defs, presets='{}'):
    import lupa
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute('UnitDataDefs = %s\nPresets = %s\nOnMsg = {}\n' % (defs, presets))
    lua.execute(src)
    return lua


def test_portrait_with_missing_files():
    """A custom portrait whose pictures were deleted must not hide the merc's
    real one. IMP 06 and IMP 13 on the tester's machine had exactly this: listed,
    files gone, blank face in the Recruit tab."""
    import portraits
    tmp = tempfile.mkdtemp(prefix='ja3-faces-')
    try:
        good = {'head': 'UI/JAPE3000Faces/A_head.png', 'big': 'UI/JAPE3000Faces/A_big.png'}
        gone = {'head': 'UI/JAPE3000Faces/B_head.png', 'big': 'UI/JAPE3000Faces/B_big.png'}
        _fake_store(tmp, {'A': good, 'B': gone})
        for ref in good.values():
            with open(os.path.join(tmp, 'Images', ref.split('/')[-1]), 'wb') as f:
                f.write(b'png')
        check('both are listed',
              sorted(portraits.read_assignments(tmp)) == ['A', 'B'])
        check('the one with missing pictures is reported broken',
              portraits.broken_assignments(tmp) == ['B'])
        check('only the working one is offered to the editor',
              sorted(portraits.read_assignments(tmp, only_present=True)) == ['A'])
        check('only the working one goes into the DLC',
              sorted(portraits.dlc_files(tmp)) == sorted(good.values()))
        src = portraits.dlc_lua(tmp)
        check('...and only it is named in the DLC code', '"A"' in src and '"B"' not in src)
        dropped = portraits.drop_broken(tmp)
        check('repair drops it and keeps the good one',
              dropped == ['B'] and sorted(portraits.read_assignments(tmp)) == ['A'])
        try:
            import lupa  # noqa: F401
        except ImportError:
            print('  (lupa not installed - in-game check skipped)')
            return
        lua = _run_portrait_lua(src, '{ A = {Portrait="old"}, B = {Portrait="old"} }')
        check('in game: the working portrait is applied',
              lua.eval('UnitDataDefs.A.Portrait') == good['head'])
        check('in game: the missing one leaves the merc\'s own portrait',
              lua.eval('UnitDataDefs.B.Portrait') == 'old')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_portrait_import():
    """One picture in, both portraits out, in the shapes the game's screens
    cut from. Sizes and crop boxes are the game's own (see portraits.py).
    Nothing is written as a mod: the pictures stay in the editor's folder and
    travel into the game inside the live link DLC."""
    import portraits
    from PIL import Image
    tmp = tempfile.mkdtemp(prefix='ja3-faces-')
    try:
        src = os.path.join(tmp, 'photo.jpg')
        Image.new('RGB', (800, 1200), (200, 30, 30)).save(src)
        folder = os.path.join(tmp, 'store')
        got = portraits.assign('Barry', src, folder=folder)
        art = got['Barry']
        check('the pictures are named under the DLC\'s UI folder, not a mod',
              art['head'] == 'UI/JAPE3000Faces/Barry_head.png'
              and art['big'] == 'UI/JAPE3000Faces/Barry_big.png', str(art))
        with Image.open(os.path.join(folder, 'Images', 'Barry_head.png')) as f:
            head = f.copy()
        with Image.open(os.path.join(folder, 'Images', 'Barry_big.png')) as f:
            big = f.copy()
        check('head portrait is 300x300 RGBA, like UI/MercsPortraits',
              head.size == (300, 300) and head.mode == 'RGBA')
        check('big portrait is 2000x2000 RGBA, like UI/Mercs',
              big.size == (2000, 2000) and big.mode == 'RGBA', str(big.size))
        a = big.getchannel('A')
        check('the figure fills the strip the Inventory screen shows (x 550-1480)',
              a.getpixel((550, 1000)) == 255 and a.getpixel((1480, 1000)) == 255)
        check('...and most of the strip Merc Inspect shows (x 500-1520)',
              a.getpixel((500, 1000)) == 255 and a.getpixel((1500, 1000)) == 255)
        check('either side of the figure is transparent, as in the game\'s art',
              a.getpixel((100, 1000)) == 0 and a.getpixel((1900, 1000)) == 0)
        files = portraits.dlc_files(folder)
        check('the DLC carries both pictures byte for byte',
              sorted(files) == sorted(art.values()) and files[art['head']][:4] == b'\x89PNG')
        check('with no portraits the DLC code is unchanged',
              portraits.dlc_lua(os.path.join(tmp, 'empty')) == '')

        try:
            import lupa  # noqa: F401
        except ImportError:
            print('  (lupa not installed - in-game check skipped)')
            return
        lua = _run_portrait_lua(
            portraits.dlc_lua(folder),
            '{ Barry = {Portrait="UI/MercsPortraits/Barry"} }',
            '{ UnitDataCompositeDef = { Default = {\n'
            '  { id = "Barry", Portrait = "UI/MercsPortraits/Barry" },\n'
            '  { id = "Wolf", Portrait = "UI/MercsPortraits/Wolf" } } } }')
        check('in game: the merc definition gets both pictures',
              lua.eval('UnitDataDefs.Barry.Portrait') == art['head']
              and lua.eval('UnitDataDefs.Barry.BigPortrait') == art['big'])
        check('in game: so does the preset the A.I.M. laptop reads',
              lua.eval('Presets.UnitDataCompositeDef.Default[1].BigPortrait') == art['big'])
        check('in game: no other merc is touched',
              lua.eval('Presets.UnitDataCompositeDef.Default[2].Portrait')
              == 'UI/MercsPortraits/Wolf')
        lua.execute('UnitDataDefs.Barry.Portrait = "reset"; OnMsg.DataLoaded()')
        check('in game: applied again when the game reloads its data',
              lua.eval('UnitDataDefs.Barry.Portrait') == art['head'])

        portraits.unassign('Barry', folder=folder)
        check('removing it empties the list and deletes its pictures',
              portraits.read_assignments(folder) == {}
              and os.listdir(os.path.join(folder, 'Images')) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_portraits_ride_in_the_live_link():
    """The live link DLC is the only thing that carries portraits into the
    game. Its pack holds the pictures under UI/ and code that still compiles."""
    import portraits
    import build_livelink
    import hpk
    from PIL import Image
    tmp = tempfile.mkdtemp(prefix='ja3-faces-')
    real_store = portraits.store_dir
    try:
        folder = os.path.join(tmp, 'store')
        portraits.store_dir = lambda: folder
        plain = build_livelink.pack()
        src = os.path.join(tmp, 'photo.png')
        Image.new('RGB', (400, 400), (10, 90, 200)).save(src)
        portraits.assign('Barry', src, folder=folder)
        packed = build_livelink.pack()
        path = os.path.join(tmp, 'live.hpk')
        with open(path, 'wb') as f:
            f.write(packed)
        pk = hpk.Pack(path)
        try:
            names = [n for n, _ in pk.walk('')]
            auto = pk.read('autorun.lua').decode('utf-8')
            head = pk.read('UI/JAPE3000Faces/Barry_head.png')
        finally:
            pk.close()
        check('the DLC holds the pictures under UI/JAPE3000Faces',
              'UI/JAPE3000Faces/Barry_head.png' in names
              and 'UI/JAPE3000Faces/Barry_big.png' in names, str(names))
        check('...unchanged', head[:4] == b'\x89PNG')
        check('its code names the merc', '["Barry"]' in auto)
        try:
            import lupa
            lr = lupa.LuaRuntime()
            compiled = lr.eval('function(s) return load(s) ~= nil end')(auto)
            check('...and still compiles', compiled)
        except ImportError:
            print('  (lupa not installed - compile check skipped)')
        check('without portraits the DLC is what it always was',
              plain != packed and b'JAPE3000Faces' not in plain)
    finally:
        portraits.store_dir = real_store
        shutil.rmtree(tmp, ignore_errors=True)


def test_old_portraits_mod_is_removed():
    """Older builds wrote a JA3SaveEditorFaces mod. Its pictures are brought
    into the editor's store and the mod folder is deleted - nothing of the
    editor's stays in Mods."""
    import portraits
    tmp = tempfile.mkdtemp(prefix='ja3-faces-')
    try:
        mods = os.path.join(tmp, 'Mods')
        old = os.path.join(mods, portraits.LEGACY_MOD_ID)
        os.makedirs(os.path.join(old, 'Images'))
        os.makedirs(os.path.join(old, 'Code'))
        os.makedirs(os.path.join(mods, 'SomeoneElses'))
        for name in ('Barry_head.png', 'Barry_big.png', 'IMP_06_head.png'):
            with open(os.path.join(old, 'Images', name), 'wb') as f:
                f.write(name.encode())
        with open(os.path.join(old, 'Code', 'portraits.lua'), 'w') as f:
            f.write('\t["Barry"] = { head = "Mod/JA3SaveEditorFaces/Images/Barry_head.png",'
                    ' big = "Mod/JA3SaveEditorFaces/Images/Barry_big.png" },\n')
        store = os.path.join(tmp, 'store')
        moved = portraits.migrate_legacy(mods, store)
        check('the complete portrait is brought across', moved == ['Barry'], str(moved))
        art = portraits.read_assignments(store, only_present=True)
        check('...pointing at the DLC, not the mod',
              art.get('Barry', {}).get('head') == 'UI/JAPE3000Faces/Barry_head.png')
        check('the old mod folder is gone', not os.path.exists(old))
        check('other mods are left alone', os.path.isdir(os.path.join(mods, 'SomeoneElses')))
        check('nothing to do the second time', portraits.migrate_legacy(mods, store) == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_portrait_with_missing_files()
    test_portrait_import()
    test_portraits_ride_in_the_live_link()
    test_old_portraits_mod_is_removed()
    test_store_label()
    test_drive_search()
    test_search_runs_once_in_background()
    test_vdf_both_shapes()
    test_library_of()
    test_steam_discovery()
    test_packed_workshop_mod()
    test_nested_items_in_a_loose_mod()
    print()
    print('%d passed, %d failed' % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
