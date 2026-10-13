# JAPE 3000 — Jagged Alliance 3 Person Editor

An offline editor for `.savegame.sav` files. No uploads, no browser tool, no
account: it reads and writes the files on your disk and nothing else.

## Building it yourself

This is the full source of the JAPE3000.exe released on
[Nexus Mods](https://www.nexusmods.com/jaggedalliance3/mods/357).

Needs Windows 10/11 64-bit and Python 3.12.

```
python -m pip install pyinstaller pywebview pillow zstandard
python build_catalog.py
python build_icons.py "C:\path\to\Jagged Alliance 3"
python make_release.py
```

- `build_catalog.py` fetches item, merc and perk data from THQ Nordic's
  official [JaggedAlliance3Modding](https://github.com/THQNordic/JaggedAlliance3Modding)
  repository. A prebuilt `catalog.json` is already included.
- `build_icons.py` extracts the item icons and merc portraits from your own
  installed copy of the game. They are not in this repository because they
  are the game's art.
- `make_release.py` runs PyInstaller and leaves `dist/JAPE3000.exe`.
  `python make_release.py --promote --zip` also writes the portable zip.
- See `bootloader/README.md` for the optional custom PyInstaller loader.

Or skip building and run from source: `python ja3_gui.py`.

The editor makes no network connections of its own. Its window is a page
served on 127.0.0.1, shown in Microsoft's WebView2.

Jagged Alliance 3 (c) THQ Nordic, developed by Haemimont Games. This is a
fan-made tool, not affiliated with or endorsed by either.

## Run it

**`dist/JAPE3000.exe`** — double-click. One file, nothing to install,
no Python needed. It finds your saves on its own.

Also in `dist/`:

| | |
|---|---|
| `JAPE3000.exe --cli` | the same engine with a console front end |
| `Portable/` | the Python sources plus `Run.bat`, if you'd rather run from source |

## What it edits

- **Money** — set it, or add in chunks. The load-menu preview is kept in sync.
- **Attributes** — all ten, per merc, 1–100, with the merc's preset marked on
  each slider so you can see how far you've strayed.
- **Health, max health, experience** — plus one-click levels 1–10 using the
  game's own XP thresholds, and a *Patch up* button that clears wounds and
  refills health.
- **Perks** — 121 assignable perks and quirks, searchable, with the real
  in-game names and descriptions. Wounds, quest flags and combat statuses on a
  merc are preserved rather than trampled.
- **Gear** — every equipment slot and the backpack, each item shown with its
  real in-game icon. Add any of 267 items, repair worn gear to 100%, remove
  what you don't want. Weapons come with a magazine of the right calibre
  automatically.
- **Weapon attachments** — every mount a weapon actually has is laid out under
  it: scopes, muzzles, stocks, magazines, handguards, bipods, grenade
  launchers, side rails. Click a mount to see only the parts that weapon
  accepts, with the part's icon, its effect and its price. Fitting one leaves
  every other mount alone.
- **Recruiting** — every A.I.M. merc already has a record in your save, so
  hiring is instant and free: pick one, choose a squad, done.
- **Custom mercs** — build a character on one of the six I.M.P. slots the game
  keeps in every save: name, nickname, specialisation, attributes, perks, level,
  and which squad they land in.
- **Sector stash** — a read-only inventory of what's sitting in storage
  across the map.

## How it works

A save is a container with the magic bytes `BPUL` holding three blobs:
`savegame_metadata` (what the load menu previews), a screenshot, and
`game_session` — the actual state, Zstandard-compressed in 128 KiB chunks.
Decompressed, it's a restricted Lua text format: nested tables plus constructor
calls like `PlaceObj`, `PlaceUnitData`, `PlaceInventoryItem` and `T{...}`.

Edits are applied as **minimal in-place text patches**. Only the bytes for the
values you actually changed are rewritten; everything else in your save comes
back byte-for-byte identical. Before anything reaches disk the rebuilt file is
parsed back and compared against what was intended, and the original is copied
to a timestamped `.bak` alongside it. A save you open and don't edit is never
even recompressed.

Two details worth knowing:

- **Custom (I.M.P.) mercs store their attributes in the save. A.I.M. mercs
  don't** — Fox and company inherit theirs from the game's presets, and the save
  only records the deltas in `applied_modifiers`. Setting an attribute here
  writes an explicit value onto the merc and clears that attribute's old
  training modifiers, so the number you type is the number the game reads.
- **Max health is not the Health attribute.** Wounds carry `stacks` that reduce
  it, which is why a merc can show 68/68 with 78 Health. Both are editable
  separately; *Patch up* clears the wounds.

## Icons and attachments

`build_icons.py` reads `Packs/UI.hpk` straight out of an installed copy of the
game. The packs use the same `BPUL` container and chunked-ZSTD compression as
a savegame, so the same code reads both; the textures inside are BC7 DDS,
which Pillow decodes. It pulls the 260 icons the catalog refers to, trims each
one's transparent margin, and writes optimised PNGs into `webui/icons/`.

Those PNGs are then **bundled inside the exe**, so the finished app shows the
game's own art without needing the game installed. Re-run it only if you want
to refresh them:

```
python build_icons.py "E:\Jagged Alliance 3"
```

Attachment rules come from the catalog: each weapon's `ComponentSlots` lists
its mounts and exactly which parts fit, and the 118 `WeaponComponent` presets
supply names, icons, effects and costs. In the save these live as a
`components` map on the item - slot name to part id, empty string for an empty
mount - which is what the editor writes.

## The catalog

`catalog.json` holds 267 items, 322 mercs, 211 perks and 118 weapon components
with their real display names, groups, costs and descriptions, extracted from
THQ Nordic's official
[JaggedAlliance3Modding](https://github.com/THQNordic/JaggedAlliance3Modding)
repository. To rebuild it:

```
python build_catalog.py
```

Mods you have installed aren't in the catalog. Modded content already in your
save is preserved untouched either way, and the Overview tab tells you when a
save was made with mods active.

## Tests

```
python test_saves.py
```

Runs against every save it finds on this machine — 240 checks across ten saves
at the time of writing: container rebuilds byte-identical, the Lua round-trips
character-identical, a no-op write reproduces the original file exactly, and a
full edit cycle (money, attributes, XP, perks, items) reads back correctly with
unique inventory keys and item ids, leaving untouched mercs unchanged. Fitting
a weapon attachment is checked too, including that the weapon's other mounts
come back exactly as they were. Every test works on a copy.

## Command line

```
JAPE3000.exe --cli list
JAPE3000.exe --cli info    "path\to\save.savegame.sav"
JAPE3000.exe --cli mercs   "path\to\save.savegame.sav"
JAPE3000.exe --cli money   "path\to\save.savegame.sav" 500000
JAPE3000.exe --cli stat    "path\to\save.savegame.sav" Fox Marksmanship 95
JAPE3000.exe --cli level   "path\to\save.savegame.sav" Fox 8
JAPE3000.exe --cli heal    "path\to\save.savegame.sav"
JAPE3000.exe --cli give    "path\to\save.savegame.sav" Fox AK47
JAPE3000.exe --cli perk    "path\to\save.savegame.sav" Fox Deadeye
JAPE3000.exe --cli recruit "path\to\save.savegame.sav" Blood
JAPE3000.exe --cli items   sniper
```

`--dry-run` shows what would change without writing.

## Files

| | |
|---|---|
| `bpul.py` | the `BPUL` container |
| `hpk.py` | reader for the game's `.hpk` packs (same container) |
| `zstd_chunks.py` | the chunked Zstandard wrapper |
| `lua_text.py` | tokenizer, parser and patcher for the save's Lua text |
| `ja3_save.py` | the editing engine |
| `backups.py` | listing, peeking at and restoring `.bak` files |
| `gamepaths.py` | finding the game, mods and saves; remembering your choices |
| `server.py` | local HTTP API (127.0.0.1, ephemeral port) |
| `webui/` | the interface |
| `ja3_gui.py` / `ja3_cli.py` | the two front ends |
| `build_catalog.py` / `build_icons.py` / `build_icon.py` / `build_app.py` | build steps |
| `test_saves.py` | the test suite |

## Caveats

- Creating a custom merc is the most speculative feature here. The record it
  writes matches the shape the game uses for an I.M.P. character, but load the
  result and look it over before playing on.
- Editing a save the game currently has open is asking for trouble. Quit to the
  main menu, or to desktop, first.
- Keep the `.bak` files until you've confirmed a save loads.
