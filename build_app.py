"""Build the shipping executables with PyInstaller.

    python build_app.py            build, then install into the parent folder
    python build_app.py --no-copy  build into dist/ only

catalog.json and webui/ (icons included) are bundled inside the GUI exe, so a
single file is everything the app needs - no game install, no Python.
Builds into dist/ first and only copies over the working exes on success.
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
DIST = os.path.join(HERE, 'dist')
BUILD = os.path.join(HERE, 'build')

HIDDEN_GUI = ['webview.platforms.edgechromium', 'webview.platforms.winforms',
              'clr_loader', 'zstandard', 'tkinter', 'tkinter.filedialog',
              'PIL', 'PIL.Image']
# imported lazily inside request handlers / commands, so PyInstaller's
# static scan does not see them
LAZY = ['modconflicts', 'mods', 'backups', 'portraits', 'gamepaths', 'hpk',
        'savediff', 'gamelog',
        # the live link: the editor builds its own DLC in memory and writes it
        # into the game folder, so the packer has to travel inside the exe
        'livegame', 'games', 'ja3se', 'ja2_save', 'build_livelink', 'hpk_write', 'zstd_chunks',
        'build_ja3se', 'ja3se_install', 'removal',
        # the Jagged Alliance 2 half. ja2_save imports these at module level, so
        # the scan would most likely find them on its own - but "most likely"
        # ships an exe that opens a JA3 save and dies on a JA2 one, which is
        # precisely the failure a smoke test on this machine never reproduces.
        'ja2', 'ja2.profiles', 'ja2.profile_data', 'ja2.item_data', 'ja2.soldiers',
        'ja2.slf', 'ja2.sti', 'ja2.icon_index', 'ja2.item_stats', 'ja2art']
HIDDEN_CLI = ['zstandard'] + LAZY


def use_custom_bootloader():
    """Put our own bootloader in place of the one pip shipped.

    Nexus quarantines uploads on VirusTotal hits, and the single biggest
    source of those is not our code at all - it is PyInstaller's prebuilt
    bootloader. The identical binary ships inside thousands of real malware
    samples, so engines have signatured it and every PyInstaller app inherits
    the reputation.

    bootloader/ holds one we compiled ourselves from the PyInstaller source
    (MinGW, `python waf all`). Same code, different binary, no shared
    signature.

    This runs on every build on purpose: `pip install --upgrade pyinstaller`
    silently restores the stock bootloader, and the only symptom would be a
    quarantined upload weeks later with nothing obvious to connect it to.
    """
    import hashlib
    try:
        import PyInstaller
    except ImportError:
        return
    ours = os.path.join(HERE, 'bootloader', 'Windows-64bit-intel')
    if not os.path.isdir(ours):
        print('note: no custom bootloader in bootloader/ - using the stock one,')
        print('      which raises the chance of a false positive on upload')
        return
    installed = os.path.join(os.path.dirname(PyInstaller.__file__),
                             'bootloader', 'Windows-64bit-intel')
    if not os.path.isdir(installed):
        return
    digest = lambda f: hashlib.sha256(open(f, 'rb').read()).hexdigest()
    swapped = []
    for name in sorted(os.listdir(ours)):
        src, dst = os.path.join(ours, name), os.path.join(installed, name)
        if not os.path.isfile(src):
            continue
        if not os.path.isfile(dst) or digest(src) != digest(dst):
            shutil.copy2(src, dst)
            swapped.append(name)
    if swapped:
        print('bootloader: replaced the stock %s with ours' % ', '.join(swapped))
    else:
        print('bootloader: ours is already in place')


def other_game_art():
    """Any JA2 picture sitting in webui/, which would be bundled."""
    webui = os.path.join(HERE, 'webui')
    found = []
    for dirpath, _dirs, files in os.walk(webui):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), webui).replace(os.sep, '/')
            low = rel.lower()
            if (low.startswith('ja2icons/') or low.startswith('portraits/ja2_')
                    or (low.startswith('ja2-') and low.endswith(('.png', '.jpg', '.jpeg')))):
                found.append(rel)
    return sorted(found)


EXCLUDED = ['lupa']


def run(entry, name, windowed, data, hidden):
    sep = ';' if os.name == 'nt' else ':'
    cmd = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--onefile', '--name', name,
           '--distpath', DIST, '--workpath', BUILD, '--specpath', BUILD,
           '--icon', os.path.join(HERE, 'app.ico'),
           # UPX roughly doubles antivirus false positives on PyInstaller
           # output, and Nexus quarantines on scan hits. Never compress.
           '--noupx',
           '--version-file', os.path.join(HERE, 'version_info.txt')]
    for src, dst in data:
        cmd += ['--add-data', '%s%s%s' % (os.path.join(HERE, src), sep, dst)]
    for mod in hidden:
        cmd += ['--hidden-import', mod]
    # Build-time only, and it must not travel. lupa syntax checks the DLC's
    # Lua while building; PyInstaller collected its Python modules anyway and
    # left the runtime behind, which broke add-on install in the shipped exe.
    # Excluding it also keeps a compiled extension out of a binary that is
    # deliberately kept boring for antivirus scanners.
    for mod in EXCLUDED:
        cmd += ['--exclude-module', mod]
    cmd += ['--windowed'] if windowed else ['--console']
    cmd += [os.path.join(HERE, entry)]
    print('\n=== building %s ===' % name)
    if subprocess.run(cmd, cwd=HERE).returncode:
        raise SystemExit('PyInstaller failed for %s' % name)


def main():
    use_custom_bootloader()
    if not os.path.exists(os.path.join(HERE, 'app.ico')):
        subprocess.run([sys.executable, os.path.join(HERE, 'build_icon.py')], check=True)
    if not os.path.exists(os.path.join(HERE, 'catalog.json')):
        raise SystemExit('catalog.json missing - run build_catalog.py first')

    icons = os.path.join(HERE, 'webui', 'icons')
    n_icons = len([f for f in os.listdir(icons) if f.endswith('.png')]) \
        if os.path.isdir(icons) else 0
    portraits = os.path.join(HERE, 'webui', 'portraits')
    n_faces = len([f for f in os.listdir(portraits) if f.endswith('.png')]) \
        if os.path.isdir(portraits) else 0
    print('bundling %d icons and %d portraits' % (n_icons, n_faces))
    other_game = other_game_art()
    if other_game:
        raise SystemExit(
            'Jagged Alliance 2 art is in webui/ and would ship inside the exe:\n   '
            + '\n   '.join(other_game[:10]) + '\nNexus Mods does not allow '
            'another game\'s art in a JA3 mod. ja2art.py reads it from the '
            'player\'s own install instead - delete these and build again.')
    if not n_icons:
        print('  (none found - run build_icons.py against an installed copy of the game)')

    run('ja3_gui.py', 'JAPE3000', True,
        [('catalog.json', '.'), ('webui', 'webui')], HIDDEN_GUI + LAZY)
    # The command line lives inside the same exe - ja3_gui.main() hands over
    # to ja3_cli when it sees --cli. A second executable was redundant, and
    # shipping two of them doubled what a scanner had to be happy about.
    if '--with-cli-exe' in sys.argv:
        run('ja3_cli.py', 'JAPE3000-cli', False,
            [('catalog.json', '.')], HIDDEN_CLI)

    print('\nbuilt:')
    for f in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, f)
        if os.path.isfile(p):
            print('  %-26s %5.1f MB' % (f, os.path.getsize(p) / 1e6))

    if '--no-copy' not in sys.argv:
        # Windows will not let us write over a running exe. That is a normal
        # thing to hit - the editor is often open while its own build runs -
        # so say which file is held and carry on with the rest rather than
        # aborting and leaving half the build uninstalled.
        locked = []
        for f in ('JAPE3000.exe',):
            src, dst = os.path.join(DIST, f), os.path.join(PARENT, f)
            if not os.path.isfile(src):
                continue
            try:
                shutil.copy2(src, dst)
                print('installed -> %s' % dst)
            except PermissionError:
                locked.append(f)
                print('IN USE, not installed -> %s' % dst)
        if locked:
            print()
            print('These are still running, so the new build is waiting in')
            print('%s:' % DIST)
            for f in locked:
                print('   %s' % f)
            print('Close them and run this again, or copy the files by hand.')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
