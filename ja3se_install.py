# COPIED FROM THE SCRIPT EXTENDER PROJECT - DO NOT EDIT THIS COPY.
#
#   source: E:\Jagged Alliance 3 - Script Extender\Source\ja3se_install.py
#   sha256: 6e72a09856f2edb9
#
# The original lives in the extender project and is copied here so PyInstaller
# can bundle it. Edit the original and re-run its sync script; `check_sync()`
# in make_release.py reports drift, so an edit made here will be noticed and
# then lost.
"""JA3SE installer - the half JAPE 3000 needs to install the script extender.

Deliberately mirrors `build_livelink.py` function for function - `pack`,
`state`, `install`, `uninstall`, `dest_path`, `dlc_folder` - with the same
return shapes, so wiring it up is copying an existing call site and changing
the module name. Nothing new to learn.

ONE EXE IS NOT AT RISK. The pack is built in memory from Lua source that lives
in `build_ja3se.py`, exactly the way the live link builds its own DLC. Nothing
is read from disk at runtime and there is no second file to ship.

WHAT GETS WRITTEN, AND WHERE

    <game>\\DLC\\ja3se.hpk        one file. That is the whole install.

Once the game has run it, the extender also writes the files named in
`OWNED_APPDATA` into the game's AppData folder. Uninstall removes the pack and
every one of those. The extender keeps no registry keys, writes nothing into
Program Files, and touches no savegame.

THE NATIVE HALF IS DELIBERATELY NOT BUNDLED - see `native_note()`.

TEST COPY ONLY from this side: `python ja3se_install.py --install-copy`.
"""

import os
import re
import sys
import time

import build_ja3se

try:                      # inside JAPE, reuse the editor's own game finder
	import gamepaths
except ImportError:       # standalone, in the extender project
	gamepaths = None

HPK = build_ja3se.HPK
VERSION = build_ja3se.VERSION

# The status file the extender writes on every boot. Reading it is the only
# way to know it is actually WORKING rather than merely present - a DLC whose
# Lua fails to compile installs fine, loads nothing, and the game boots
# normally with no error anywhere. That exact failure cost a build cycle.
STATUS_FILE = 'ja3se-status.txt'


def _resolve(game_dir):
	"""None means 'find it'. Anything else is taken literally, including a
	blank string - which means 'no game', not 'go and find one'. Same rule as
	the live link, for the same reason: a silent fallback once wrote a DLC
	into a real install during a test."""
	if game_dir is None:
		return gamepaths.game_dir() if gamepaths else None
	return game_dir or None


def dlc_folder(game_dir=None):
	game = _resolve(game_dir)
	return os.path.join(game, 'DLC') if game else None


def dest_path(game_dir=None):
	folder = dlc_folder(game_dir)
	return os.path.join(folder, HPK) if folder else None


def _check(data):
	"""Invariants that have actually broken this DLC, checked before anything
	is written. Every one of these shipped at least once, and every one
	produced a game that booted perfectly while the extender did nothing.

	A silent no-op is the worst failure mode this project has, so refusing to
	install beats writing a pack that cannot work.
	"""
	files = build_ja3se.FILES
	for name, blob in files.items():
		if name.endswith('.lua') and b'\r' in blob:
			# Lua treats a bare CR as a line terminator. One raw CR made
			# autorun.lua unparseable; the DLC never loaded, the game started
			# fine, and nothing anywhere pointed at the cause.
			raise ValueError('%s contains a carriage return, which makes it '
			                 'unparseable to Lua' % name)
	if not files.get('autorun.lua'):
		raise ValueError('no autorun.lua - the DLC would load nothing')
	if 'Code/ja3se_early.lua' not in files:
		# Without the Code slot the extender arms AFTER mods have loaded, and
		# conflict detection, the quiet disable and the quiet enable are all
		# silently inert. Measured, and invisible from outside the game.
		raise ValueError('no Code/ja3se_early.lua - mod footprint recording '
		                 'would not work and three features would be dead')
	if build_ja3se.TEST_MODE or build_ja3se.ENABLE_TEST:
		raise ValueError('this build has a test harness enabled and must not '
		                 'be shipped to anyone')
	if len(data) < 20000:
		raise ValueError('pack is implausibly small (%d bytes)' % len(data))
	return data


_PACK_CACHE = None


def pack():
	"""The .hpk, built in memory. Raises if the build is not shippable.

	Cached. `state()` calls this to compare against the installed file, and the
	editor polls `state()` continuously - measured at 2054 ms per build with
	lupa present, against 0 ms for the live link's packer. The bytes are a pure
	function of these sources, which cannot change while the process runs.

	Failures are deliberately not cached: a build that raises will be retried
	rather than remembered as broken.
	"""
	global _PACK_CACHE
	if _PACK_CACHE is None:
		_PACK_CACHE = _check(build_ja3se.pack())
	return _PACK_CACHE


def state(game_dir=None):
	"""(installed, up_to_date, path) - up_to_date compares the actual bytes,
	so a newer JAPE notices an older extender and can offer to update it."""
	path = dest_path(game_dir)
	if not path or not os.path.exists(path):
		return False, False, path
	try:
		with open(path, 'rb') as f:
			current = f.read()
	except OSError:
		return True, False, path
	return True, current == pack(), path


_BUILD_RE = re.compile(rb'--\s*JA3SE_BUILD\s+([0-9]+)')


def build_of(data):
	"""The build stamp inside a packed extender, or None if it has none.

	None is not an error. It means a build from before stamping existed, or a
	damaged file. Both are safe to overwrite.
	"""
	m = _BUILD_RE.search(data or b'')
	return m.group(1).decode('ascii') if m else None


def compare(game_dir=None):
	"""How the installed extender relates to the one this build would write.

	Returns (verdict, installed_build, our_build) where verdict is one of
	'missing', 'older', 'same', 'newer', 'unreadable'.

	This exists because byte equality is symmetric and the decision is not.
	An installer that only knows the two files DIFFER will happily write an
	older build over a newer one, which is precisely what this editor did
	until now - silently, and by design.
	"""
	ours = build_of(pack())
	path = dest_path(game_dir)
	if not path or not os.path.exists(path):
		return 'missing', None, ours
	try:
		with open(path, 'rb') as f:
			current = f.read()
	except OSError:
		return 'unreadable', None, ours
	if current == pack():
		return 'same', build_of(current), ours
	theirs = build_of(current)
	if theirs and ours and theirs > ours:
		return 'newer', theirs, ours
	# No stamp, or a stamp that is not ahead of ours: safe to replace. An
	# unstamped file predates stamping, so it is older by definition.
	return 'older', theirs, ours


def install(game_dir=None):
	"""Write the extender into the game folder. Returns (ok, message, path)."""
	game = _resolve(game_dir)
	if not game:
		return (False, 'I could not find your Jagged Alliance 3 folder. '
		        'Point the editor at it with the Folder button, then try '
		        'again.', None)
	if not os.path.exists(os.path.join(game, 'JA3.exe')):
		# Refuse rather than create a DLC folder somewhere random. Picking the
		# wrong folder is the likeliest mistake a user makes here.
		return (False, 'That folder does not look like Jagged Alliance 3 - '
		        'there is no JA3.exe in it. Pick the folder JA3.exe sits in.',
		        None)
	try:
		data = pack()
	except ValueError as exc:
		return False, 'Refusing to install a broken build: %s' % exc, None

	folder = os.path.join(game, 'DLC')
	path = os.path.join(folder, HPK)
	tmp = path + '.part'
	try:
		if not os.path.isdir(folder):
			os.makedirs(folder)
		# Write to a temp name and replace, so an interrupted write cannot
		# leave a half-written pack for the game to try to load.
		with open(tmp, 'wb') as f:
			f.write(data)
		# Retry the replace. A file that was just written is routinely held
		# for a moment by a virus scanner or the search indexer, and this
		# installer runs immediately after building - measured: a replace
		# denied outright, then the identical write succeeding first try a
		# few minutes later, with the game not running, the file not
		# read-only and no process holding the folder.
		last = None
		for attempt in range(5):
			try:
				os.replace(tmp, path)
				last = None
				break
			except PermissionError as exc:
				last = exc
				time.sleep(0.3)
		if last is not None:
			raise last
	except PermissionError:
		return (False, 'Something else is holding ' + HPK + ' in your game '
		        'folder, so I could not replace it. Usually that is the game '
		        'still running, or a virus scanner that has not let go yet - '
		        'close the game and try again in a moment. If it keeps '
		        'happening and your game is in Program Files, running the '
		        'editor as administrator once will do it.', path)
	except OSError as exc:
		return False, 'Could not write the file: %s' % exc, path
	finally:
		# Never leave a .part behind. A 144 KB file sitting in a game folder
		# looking like a real pack is how a failed install becomes a mystery
		# three days later.
		if os.path.exists(tmp):
			try:
				os.remove(tmp)
			except OSError:
				pass
	return (True, 'Script extender installed. Start the game once, then check '
	        'that it reports as working.', path)


# The manifest lives in build_ja3se.py, beside the Lua that writes these
# files, and is re-exported here because this is the module that deletes them.
#
# One direction only. This module already imports build_ja3se; when the guard
# over there imported this one back, the cycle left a half-initialised module
# and the guard reported "no problems" because it could not run at all.
OWNED_APPDATA = build_ja3se.OWNED_APPDATA
AUTHORED_APPDATA = build_ja3se.AUTHORED_APPDATA


def appdata_dir(appdata=None):
	return appdata or os.path.join(os.environ.get('APPDATA', ''),
	                               'Jagged Alliance 3')


def owned_files(game_dir=None, appdata=None):
	"""Every path this extender owns, and whether it is there right now.

	Returns a list of (path, exists, what) so a person can be shown the
	complete answer to "what has this put on my machine?" - including when
	nothing is installed, which is exactly when they most want to know.
	"""
	out = []
	dlc = dest_path(game_dir)
	if dlc:
		out.append((dlc, os.path.exists(dlc), 'the extender itself'))
	base = appdata_dir(appdata)
	for name in OWNED_APPDATA:
		p = os.path.join(base, name)
		what = ('YOUR OWN FILE - the mods you told it to switch on at boot; '
		        'it does not come back by itself'
		        if name in AUTHORED_APPDATA
		        else 'diagnostics it writes while running')
		out.append((p, os.path.exists(p), what))
	return out


def uninstall(game_dir=None, purge=True, appdata=None):
	# `appdata` exists so this can be tested somewhere other than the user's
	# real folder. Without it the only way to exercise the purge was to run it
	# against live data - which is how a real settings file got deleted the
	# first time it was tried. A destructive path that cannot be rehearsed will
	# be rehearsed on somebody's machine.
	"""Delete the extender. Returns (ok, message, path)."""
	path = dest_path(game_dir)
	if not path:
		return False, 'I could not find your game folder.', None
	# A missing pack is not a reason to skip the purge. Somebody who deleted
	# the .hpk by hand still has every file it wrote, and returning early here
	# made those impossible to clean up from the editor at all.
	was_there = os.path.exists(path)
	if was_there:
		try:
			os.remove(path)
		except OSError as exc:
			return False, 'Could not remove it: %s' % exc, path

	# The diagnostics too. Removing the extender and leaving fifteen files
	# behind is how a tester ends up unable to tell what is ours - and unable
	# to prove we are gone, which is the only thing that makes a clean
	# comparison possible.
	extra, failed = [], []
	if purge:
		for p, exists, _what in owned_files(game_dir, appdata):
			if p == path or not exists:
				continue
			try:
				os.remove(p)
				extra.append(p)
			except OSError:
				failed.append(p)

	if not was_there and not extra and not failed:
		return True, 'It was not installed.', path
	msg = ('Removed. The game starts normally without it.' if was_there
	       else 'It was not installed, but it had left files behind.')
	# Name anything a person wrote. It was being counted as a diagnostic, and
	# that is how a real settings file went missing without anybody noticing.
	authored = [p for p in extra if os.path.basename(p) in AUTHORED_APPDATA]
	logs = len(extra) - len(authored)
	if logs:
		msg += ' Also deleted %d diagnostic file%s it had written.' % (
			logs, '' if logs == 1 else 's')
	if authored:
		msg += (' Including your own settings file%s, which you would have to '
		        'write again: %s.'
		        % ('' if len(authored) == 1 else 's',
		           ', '.join(os.path.basename(p) for p in authored)))
	if failed:
		msg += (' %d could not be deleted (something has them open): %s'
		        % (len(failed), ', '.join(os.path.basename(f) for f in failed)))
	return True, msg, path


def runtime_state(appdata=None):
	"""What the extender reported the last time the game ran, or None if it
	has never run.

	Read this rather than trusting `state()`. Installed and working are
	different questions, and the gap between them is where this project has
	lost the most time.

	Returns a dict with at least:
	    ja3se_version, mods_loaded, mods_recorded, footprint_recorder, mod_ids

	`footprint_recorder` is the one that matters. 'NOT RECORDING' while mods
	are loaded means conflict detection, the quiet disable and the quiet
	enable will all silently do nothing.
	"""
	base = appdata or os.path.join(os.environ.get('APPDATA', ''),
	                               'Jagged Alliance 3')
	path = os.path.join(base, STATUS_FILE)
	if not os.path.exists(path):
		return None
	out = {}
	try:
		with open(path, 'r', encoding='utf-8', errors='replace') as f:
			for line in f:
				if '=' in line and not line.startswith('#'):
					k, _, v = line.partition('=')
					out[k.strip()] = v.strip()
	except OSError:
		return None
	return out


def healthy(appdata=None):
	"""(ok, message) - a one-line answer for the UI. ok is None for unknown."""
	st = runtime_state(appdata)
	if st is None:
		return None, 'Not run yet. Start the game once.'
	if st.get('footprint_recorder') == 'ok':
		return True, 'Working. %s mod(s) tracked.' % st.get('mods_recorded', '?')
	return False, ('Loaded, but not tracking mods - conflict detection and '
	               'quiet mod toggling will not work.')


def native_note():
	"""Why the native winmm proxy is NOT bundled into JAPE.

	It exists and it works - it removes the startup logos - but it is a
	hand-built DLL written next to the game executable, and that is exactly
	the shape antivirus flags. JAPE already replaces PyInstaller's stock
	bootloader specifically to avoid AV hits, because Nexus quarantines
	uploads that trip a scanner. Embedding a MinGW DLL would put that work at
	risk to save six seconds of splash screens.

	Recommendation: JAPE installs the .hpk only. The native half stays an
	optional manual download on the extender's own page, for people who want
	it and accept what it is.
	"""
	return native_note.__doc__


if __name__ == '__main__':
	arg = sys.argv[1] if len(sys.argv) > 1 else ''
	copy = build_ja3se.TEST_COPY
	if arg == '--install-copy':
		print(install(copy))
	elif arg == '--uninstall-copy':
		print(uninstall(copy))
	elif arg == '--state-copy':
		print('state:  ', state(copy))
		print('runtime:', runtime_state())
		print('healthy:', healthy())
	else:
		print(__doc__)
		print('  --install-copy / --uninstall-copy / --state-copy')
