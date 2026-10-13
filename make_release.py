"""Build the executables, and promote a build once it has been tested.

    python make_release.py                build to dist/ - the TEST build
    python make_release.py --promote      passed? update the trusted exe AND
                                          cut the portable zip, together
    python make_release.py --no-build     skip compiling, just package

One artifact, three states:

    test      Portable/dist/JAPE3000.exe        every build lands here first
    trusted   ../JAPE3000.exe                   the day-to-day copy
    shareable ../JAPE 3000 (Portable).zip       only cut with --zip

Promotion updates the trusted exe and the zip in the same step, on purpose.
Updating one without the other is how they drifted apart before.

A build sitting in dist/ keeps its own settings file, so testing can never
damage the game/mods/save folders the trusted copy depends on.
"""
import os
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))   # Source Code/
ROOT = os.path.dirname(HERE)                       # the project folder
DIST = os.path.join(HERE, 'dist')

# The finished app lives in its own folder, away from the sources. Everything
# a person actually runs or shares is in there and nothing else is.
APP_DIR = os.path.join(ROOT, 'JAPE 3000')

VERSION = '1.0'
LABEL = 'JAPE 3000'

APP_FILES = ['JAPE3000.exe']

# Kept only so check_source_list() can warn when a new module is written and
# forgotten. Nothing here is shipped - the exes are self-contained.
SOURCE_FILES = [
    'bpul.py', 'zstd_chunks.py', 'hpk.py', 'lua_text.py', 'ja3_save.py',
    'mods.py', 'modconflicts.py', 'portraits.py', 'gamepaths.py',
    'backups.py', 'savediff.py',
    'gamelog.py', 'server.py',
    'hpk_write.py', 'livegame.py', 'ja3se.py', 'build_livelink.py', 'games.py', 'ja2_save.py',
    'build_ja3se.py', 'ja3se_install.py', 'removal.py',
    'features.py',
    'ja3_gui.py', 'ja3_cli.py',
    'build_app.py', 'build_catalog.py', 'build_icon.py', 'build_icons.py',
    'build_ja2_art.py', 'build_ja2_items.py',
    # the Jagged Alliance 2 reader, its recovered cipher key, and the
    # decoders for the game's own archive and image formats
    'ja2/profiles.py', 'ja2/profile_data.py', 'ja2/item_data.py',
    'ja2/soldiers.py', 'ja2/slf.py', 'ja2/sti.py', 'ja2/item_stats.py',
    'ja2/icon_index.py', 'ja2/ammo.py', 'ja2/key_sets.py', 'ja2/layout.py', 'ja2art.py',
    'make_release.py', 'make_testbundle.py', 'TESTER READ ME.txt',
    'test_saves.py', 'test_livegame.py', 'test_ja2.py', 'test_features.py',
    'test_livelink_lua.py', 'test_webui.py', 'test_mods.py', 'test_removal.py',
    'catalog.json', 'app.ico', 'Run.bat', 'README.md', 'version_info.txt',
]

READ_ME = (
    "JAPE 3000\r\n"
    "=========\r\n"
    "\r\n"
    "The Jagged Alliance 3 Person Editor.\r\n"
    "\r\n"
    "Named after JAPE, the Jagged Alliance 2 Person Editor - the tool that let\r\n"
    "you edit your mercs back in 1999. Same idea, one game later.\r\n"
    "\r\n"
    "Run JAPE3000.exe. There is nothing to install.\r\n"
    "\r\n"
    "It finds your saves on its own. Every write copies the save to a\r\n"
    "timestamped .bak beside it first, so a bad edit can always be undone.\r\n"
    "\r\n"
    "The Look button in the top bar switches between the modern look and the\r\n"
    "A.I.M. terminal look. Your choice is remembered.\r\n"
    "\r\n"
    "If the game is running with the live add-on installed, the editor changes\r\n"
    "things in it directly instead of writing to the save. The LIVE mark in the\r\n"
    "top bar tells you which way round you are.\r\n"
    "\r\n"
    "Nothing is ever installed into your game unless you press Install on the\r\n"
    "Live link page. The script extender is an optional part of the live link.\r\n"
    "\r\n"
    "To take it all off your computer: How this works -> Remove it from this\r\n"
    "computer. It lists every file first. If the window will not open, run\r\n"
    "this from a Command Prompt in the folder JAPE3000.exe is in:\r\n"
    "    start /wait JAPE3000.exe --cli uninstall          (lists, deletes nothing)\r\n"
    "    start /wait JAPE3000.exe --cli uninstall --yes    (deletes)\r\n"
    "(start /wait keeps the prompt from coming back before the output.)\r\n"
    "Then delete JAPE3000.exe itself.\r\n"
    "\r\n"
    "Requirements\r\n"
    "------------\r\n"
    "Windows 10 or 11, 64-bit, with the Microsoft Edge WebView2 Runtime\r\n"
    "(already part of Windows 11 and of any up-to-date Windows 10).\r\n"
    "Jagged Alliance 3, Steam or GOG, on any drive.\r\n"
    "\r\n"
    "What it puts on your computer\r\n"
    "----------------------------\r\n"
    "  %APPDATA%\\ja3editor-settings.json     your folders and choices\r\n"
    "  %APPDATA%\\jape-diagnostics.log        errors only, never sent anywhere\r\n"
    "  %LOCALAPPDATA%\\JAPE 3000\\             window cache, custom portraits,\r\n"
    "                                         and JA2 pictures read from your\r\n"
    "                                         own JA2 install\r\n"
    "  <save>.<date>.bak                      a backup before every save edit\r\n"
    "\r\n"
    "In the game folder, only if you press Install (both optional):\r\n"
    "  DLC\\nemesis-live.hpk                   the live link (also carries your\r\n"
    "                                         custom portraits into the game)\r\n"
    "  DLC\\ja3se.hpk                          the script extender\r\n"
    "These are DLC files, not mods. The editor never writes a mod, ships no\r\n"
    "mods, and needs none to run. Edited saves work without either DLC.\r\n"
    "If you have mods sitting outside the game's Mods folder, the editor\r\n"
    "tells you, and moves them only when you press the button.\r\n"
    "\"Remove it from this computer\" lists and removes all of the above.\r\n"
    "\r\n"
    "No internet\r\n"
    "-----------\r\n"
    "The editor itself never connects to the internet: no updates, no\r\n"
    "telemetry, no accounts. Its window is a page served from your own\r\n"
    "machine (127.0.0.1) and nothing else can reach it.\r\n"
    "That window is drawn by Microsoft's WebView2 (the engine inside Edge),\r\n"
    "which is part of Windows. WebView2 may contact Microsoft on its own, for\r\n"
    "things like its own updates and safety checks. That is Windows, not this\r\n"
    "editor, and nothing from your saves goes with it.\r\n"
    "\r\n"
    "Antivirus\r\n"
    "---------\r\n"
    "JAPE3000.exe is a Python program packed into one file with PyInstaller.\r\n"
    "Some scanners guess at packed programs like that. This build is not\r\n"
    "compressed, carries full version details, and uses a PyInstaller loader\r\n"
    "compiled for it rather than the stock one, all to keep those guesses\r\n"
    "away. If your scanner still objects, it is a false positive - please\r\n"
    "report it on the Nexus page.\r\n"
    "\r\n"
    "Source code\r\n"
    "-----------\r\n"
    "The full source, with build steps, is public:\r\n"
    "    https://github.com/UnholyScribe/JAPE-3000\r\n"
    "\r\n"
    "Credits and permissions\r\n"
    "-----------------------\r\n"
    "Jagged Alliance 3 (c) THQ Nordic, developed by Haemimont Games. This is\r\n"
    "a fan-made tool, not affiliated with or endorsed by either.\r\n"
    "Merc, item and perk data come from THQ Nordic's official modding\r\n"
    "source (github.com/THQNordic/JaggedAlliance3Modding), used as its\r\n"
    "licence allows for Jagged Alliance 3 mods. Item icons and merc portraits\r\n"
    "are Jagged Alliance 3's own art. No other game's art is included: the\r\n"
    "Jagged Alliance 2 side reads pictures from your own JA2 install, if you\r\n"
    "have one.\r\n"
    "Built with Python, PyInstaller, pywebview, Pillow and zstandard, each\r\n"
    "under its own open-source licence.\r\n"
)


def build():
    print('=== building executables ===')
    r = subprocess.run([sys.executable, os.path.join(HERE, 'build_app.py'),
                        '--no-copy'], cwd=HERE)
    if r.returncode:
        raise SystemExit('build failed')


def check_source_list():
    """Warn about a module written but never added to SOURCE_FILES.

    backups.py and modconflicts.py were both written, wired up and then
    silently left out of a release, because adding a module and listing it are
    two separate steps with nothing connecting them.
    """
    here = {f for f in os.listdir(HERE) if f.endswith('.py')}
    # Package folders too. The check used to look only at the top level, which
    # meant the whole ja2/ package - the JA2 reader, the cipher key, the SLF
    # and STI decoders - sat in the one blind spot this function exists to
    # cover. Posix separator so an entry reads the same as it is written below.
    for pkg in sorted(os.listdir(HERE)):
        d = os.path.join(HERE, pkg)
        if not os.path.isdir(d) or pkg.startswith(('.', '_', 'build', 'dist')):
            continue
        mods = [f for f in os.listdir(d) if f.endswith('.py')]
        here |= {pkg + '/' + f for f in mods}
    missing = sorted(here - set(SOURCE_FILES))
    if missing:
        print('note: not listed in SOURCE_FILES: %s' % ', '.join(missing))
    return missing


def check_sync():
    """Refuse to build when a copied extender module has drifted from its origin.

    `build_ja3se.py` and `ja3se_install.py` live in the extender project and are
    COPIED here so PyInstaller can bundle them. Each copy carries a header
    naming its origin and that origin's hash, and `wire_into_jape.py` says of
    the whole arrangement: "the drift check above is the only thing that has to
    be right."

    It was never written. Three file headers and that docstring all pointed at a
    `check_sync()` in this file and no such function existed, so the single
    guard the design rests on had been fictional since the day it was described.
    Found while deciding whether the editor should keep building the extender
    from source at all - it should, and this is the condition of that.

    Two ways to drift and both matter:

      * the ORIGIN moved and the copy is stale. The editor then installs an
        extender older than the one that exists, believing it is current
      * somebody edited the COPY, which the header forbids precisely because
        the next sync discards it without a word

    Comparing the copy's body against the origin catches both at once, and does
    not depend on the recorded hash being maintained. When the extender project
    is not on this machine the origin cannot be read: that is reported and does
    NOT fail, because the editor has to stay buildable on its own.
    """
    import hashlib
    import re

    MARK = '# COPIED FROM THE SCRIPT EXTENDER PROJECT'
    drifted, unchecked, checked = [], [], 0

    for name in sorted(f for f in os.listdir(HERE) if f.endswith('.py')):
        with open(os.path.join(HERE, name), encoding='utf-8') as f:
            text = f.read()
        if not text.startswith(MARK):
            continue

        m = re.search(r'^#\s+source:\s*(.+?)\s*$', text, flags=re.M)
        origin = m.group(1) if m else ''
        if not origin or not os.path.isfile(origin):
            unchecked.append((name, origin or '(no source: line in the header)'))
            continue

        # The header is exactly the leading comment block and the rest is the
        # original byte for byte, so stripping it gives something directly
        # comparable - no trust placed in the recorded hash.
        lines = text.split('\n')
        while lines and lines[0].startswith('#'):
            lines.pop(0)
        body = '\n'.join(lines)

        with open(origin, encoding='utf-8') as f:
            want = f.read()

        checked += 1
        if body != want:
            drifted.append((
                name, origin,
                hashlib.sha256(body.encode('utf-8')).hexdigest()[:16],
                hashlib.sha256(want.encode('utf-8')).hexdigest()[:16]))

    for name, origin in unchecked:
        print('note: cannot check %s - origin not on this machine' % name)
        print('      %s' % origin)

    if drifted:
        print()
        print('DRIFT: a copied extender module no longer matches its origin.')
        print('Building now would bundle the wrong version of the extender,')
        print('and the editor installs what it bundles.')
        print()
        for name, origin, got, want in drifted:
            print('   %-20s here=%s  origin=%s' % (name, got, want))
            print('   %-20s %s' % ('', origin))
        print()
        print("Re-run the extender project's sync script, then build again.")
    elif unchecked:
        # Never print a clean all-clear beside something we could not look at.
        # "in sync (1 checked)" under a note saying the other one was skipped
        # is the same reassuring-summary failure this file guards against.
        print('extender sources: %d in sync, %d NOT checked here'
              % (checked, len(unchecked)))
    elif checked:
        print('extender sources in sync (%d checked)' % checked)

    return drifted


def verify(path):
    """A release that will not open is not a release."""
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad:
            raise SystemExit('corrupt entry in %s: %s' % (path, bad))
        return zf.namelist()


def zip_portable(label=LABEL):
    os.makedirs(APP_DIR, exist_ok=True)
    out = os.path.join(APP_DIR, '%s (Portable).zip' % label)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in APP_FILES:
            src = os.path.join(DIST, name)
            if not os.path.isfile(src):
                raise SystemExit('missing build output: %s' % src)
            zf.write(src, os.path.join(label, name))
        zf.writestr(os.path.join(label, 'READ ME.txt'), READ_ME)
    return out


def promote():
    """Put the tested build in place. Both targets, or neither."""
    import shutil
    os.makedirs(APP_DIR, exist_ok=True)
    locked = []
    for name in APP_FILES:
        src = os.path.join(DIST, name)
        if not os.path.isfile(src):
            raise SystemExit('nothing to promote: %s is missing' % src)
        try:
            shutil.copy2(src, os.path.join(APP_DIR, name))
        except PermissionError:
            locked.append(name)
    if locked:
        print()
        print('These are running, so nothing was promoted:')
        for name in locked:
            print('   %s' % name)
        print('Close them and run this again - the trusted exe and the zip')
        print('are left as they were rather than half updated.')
        return False
    for name in APP_FILES:
        print('promoted -> %s' % os.path.join(APP_DIR, name))
    if '--zip' in sys.argv:
        return zip_portable()
    print()
    print('Shipping one bare .exe on purpose. Nexus quarantines nested')
    print('archives and self-extracting files, so the fewer layers the')
    print('better - and there is nothing to extract twice.')
    return None


def main():
    check_source_list()
    # A stale copy is not a warning. The editor INSTALLS what it bundles, so
    # shipping a drifted one silently downgrades somebody's extender.
    if check_sync():
        return 1
    if '--no-build' not in sys.argv:
        build()

    if '--promote' not in sys.argv:
        print()
        print('TEST build ready:')
        for name in APP_FILES:
            src = os.path.join(DIST, name)
            if os.path.isfile(src):
                print('   %s  (%.1f MB)' % (src, os.path.getsize(src) / 1e6))
        print()
        print('Try it. When it passes, run again with --promote to update')
        print('the trusted exe and the portable zip together.')
        return 0

    out = promote()
    if out is False:
        return 1
    if out is None:          # promoted fine, we just did not cut an archive
        return 0
    names = verify(out)
    print()
    print(os.path.basename(out))
    print('  %.1f MB, %d entries' % (os.path.getsize(out) / 1e6, len(names)))
    for n in names:
        print('    %s' % n)
    print()
    print('trusted exe and portable zip are now the same build')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
