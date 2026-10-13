"""Pack the test build and its checklist into one archive to hand to a tester.

    python make_testbundle.py

Last time this was done by hand, and the result was an archive holding
yesterday's exe and no checklist at all - which is exactly the sort of thing
nobody notices until the person on the other end is confused. So it is a
script now: it refuses to build from an exe older than the checklist, and it
prints what went in.

Discord Nitro takes 500 MB in one piece, so nothing is split.
"""
import io
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(HERE, 'dist')

EXE = os.path.join(DIST, 'JAPE3000.exe')
DOC = os.path.join(ROOT, 'WHAT TO TEST.md')
# Plain instructions for someone who has never seen this before, as opposed to
# WHAT TO TEST.md which assumes they have. It lived only inside a zip until
# 2026-09-08, which meant deleting that zip would have destroyed the only copy.
# @DATE@ in its header is filled in at pack time, so a bundle cannot ship last
# week's date on this week's exe.
README = os.path.join(HERE, 'TESTER READ ME.txt')
OUT = os.path.join(DIST, 'JAPE3000-test.rar')

RAR = [r'C:\Program Files\WinRAR\Rar.exe',
       r'C:\Program Files (x86)\WinRAR\Rar.exe']


def rar_exe():
    for p in RAR:
        if os.path.isfile(p):
            return p
    return None


def main():
    for p in (EXE, DOC, README):
        if not os.path.isfile(p):
            raise SystemExit('missing: %s' % p)

    # A bundle whose exe predates the CODE is a bundle of the wrong build.
    # Comparing against the checklist was the first attempt and it was the
    # wrong question - editing a markdown file does not make an exe stale.
    newest, when = None, 0
    for base, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs
                   if d not in ('dist', 'build', '__pycache__', '.git')]
        for f in files:
            if not f.endswith(('.py', '.js', '.css', '.html', '.json')):
                continue
            # The packaging and test scripts do not travel inside the exe, so
            # editing one does not make the exe stale - and this file editing
            # itself into a permanent warning was how that got noticed.
            if f.startswith(('make_', 'test_')):
                continue
            p = os.path.join(base, f)
            t = os.path.getmtime(p)
            if t > when:
                newest, when = p, t

    if newest and os.path.getmtime(EXE) < when - 60:
        print('WARNING: %s is older than %s'
              % (os.path.basename(EXE), os.path.relpath(newest, HERE)))
        print('         Run make_release.py first, or this ships a stale build.')
        if '--anyway' not in sys.argv:
            return 1

    # Stamp today's date into the readme and pack that copy, leaving the
    # source file with its placeholder intact.
    import tempfile
    stamped_dir = tempfile.mkdtemp(prefix='jape-bundle-')
    stamped = os.path.join(stamped_dir, 'READ ME.txt')
    text = io.open(README, encoding='utf-8').read()
    text = text.replace('@DATE@', time.strftime('%Y-%m-%d'))
    io.open(stamped, 'w', encoding='utf-8', newline='\r\n').write(text)

    tool = rar_exe()
    if os.path.exists(OUT):
        os.remove(OUT)

    if tool:
        # -ep strips paths so the tester gets two files, not a folder tree
        cmd = [tool, 'a', '-ep', '-m5', '-idq', OUT, EXE, DOC, stamped]
    else:
        import zipfile
        alt = OUT[:-4] + '.zip'
        with zipfile.ZipFile(alt, 'w', zipfile.ZIP_DEFLATED) as z:
            for p in (EXE, DOC, stamped):
                z.write(p, os.path.basename(p))
        print('WinRAR not found - wrote a zip instead')
        cmd = None
        globals()['OUT'] = alt

    if cmd:
        subprocess.run(cmd, check=True)

    out = OUT if cmd else OUT
    print('\n%s  (%.1f MB)' % (out, os.path.getsize(out) / 1e6))
    for p in (EXE, DOC, stamped):
        print('  %-22s %6.1f MB  %s' % (
            os.path.basename(p), os.path.getsize(p) / 1e6,
            time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(p)))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
