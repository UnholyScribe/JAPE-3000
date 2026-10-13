# COPIED FROM THE SCRIPT EXTENDER PROJECT - DO NOT EDIT THIS COPY.
#
#   source: E:\Jagged Alliance 3 - Script Extender\Source\build_ja3se.py
#   sha256: 3d9b0b8c35f72d9c
#
# The original lives in the extender project and is copied here so PyInstaller
# can bundle it. Edit the original and re-run its sync script; `check_sync()`
# in make_release.py reports drift, so an edit made here will be noticed and
# then lost.
"""Builds JA3SE - the Jagged Alliance 3 Script Extender, v0.1.

Architecture is EpicEnabler's, not invented here. See
`Documentation/How EpicEnabler does it.md`. The three techniques that matter:

  1. OnMsg.ReloadLua that RE-REGISTERS ITSELF. The game reloads all its Lua on
     ~11 occasions, including every mod toggle, and a reload reassigns every
     engine global. A handler that waits for Loading to clear, re-applies the
     patches, and re-installs itself is the only thing that survives that.

  2. A dofile WRAPPER. OnMsg.ReloadLua gets you in after a reload; the wrapper
     gets you in DURING one, right after a named engine file is defined. That
     is how the mod sandbox gets opened before ModsLoadCode consumes it. It
     must re-hook itself after lib.lua, because lib.lua redefines dofile.

  3. pre_load / post_load in the returned table, which receive self.folder.

What v0.1 actually does, i.e. the thing the game cannot otherwise do:
GRANTS SANDBOXED MODS FILE IO. Mods are denied AsyncStringToFile and friends by
ModEnvBlacklist. JA3SE removes a declared list of names from that blacklist at
the exact moment between Mod.lua defining it and ModsLoadCode using it, so
every mod loaded afterwards can call them with no change to the mod.

Test copy only. There is deliberately no --install-game.
"""
import io
import os
import sys

# hpk_write lives in the save editor project. Both tools write the same pack
# format, so there is ONE copy rather than two that can quietly drift apart.
# Inside JAPE it is a sibling module and imports plainly; only a dev checkout
# of the extender needs the path. Do not make this an unconditional insert -
# an absolute E:\ path baked into a shipped exe is dead weight at best.
try:
	import hpk_write
except ImportError:
	sys.path.insert(0, r'E:\Jagged Alliance 3 - Nemesis\Source Code')
	import hpk_write

NAME = 'ja3se'
DLC_NAME = 'JA3SE'
HPK = NAME + '.hpk'
# Every file the Lua below writes into the game's AppData folder, by name.
#
# EXPLICIT, never a glob. `ja3se-*.txt` describes our files today and would
# quietly claim a tester's own notes tomorrow - and the point of this list is
# to be able to tell somebody who does not trust us yet exactly what we put on
# their machine and exactly what we took back off.
#
# It lives here, next to the code that writes the files, so that adding a file
# and forgetting the list is a one-line mistake rather than a cross-file one.
# check_lua() refuses to build if the Lua writes a name that is not here.
OWNED_APPDATA = (
	'ja3se-catalog-error.txt',
	'ja3se-catalog-summary.txt',
	'ja3se-catalog.txt',
	'ja3se-code.txt',          # written by build_probe.py, one of our tools
	'ja3se-probe.txt',         # the same tool
	'ja3se-cth-sweep.txt',
	'ja3se-enable-test.txt',
	'ja3se-enable.txt',
	'ja3se-heartbeat.txt',
	'ja3se-modeditor-probe.txt',
	'ja3se-presets.txt',
	'ja3se-report.txt',
	'ja3se-scale.txt',
	'ja3se-sector-before.txt',
	'ja3se-sector.txt',
	'ja3se-status.txt',
	'ja3se-written-by-a-mod.txt',
	# Not written by the Lua below, and still ours. The native winmm proxy
	# writes the first; earlier builds rotated the other three, so any machine
	# that ran one of them still has them.
	'ja3se-native.txt',
	'ja3se-heartbeat.prev',
	'ja3se-report.prev',
	'ja3se-status.prev',
)


# Files a PERSON wrote, as opposed to output a run regenerates. A purge still
# removes these - clean means clean - but they are named individually first,
# because losing something somebody typed is not the same as losing a log that
# rewrites itself on the next boot.
AUTHORED_APPDATA = (
	'ja3se-enable.txt',
)

LUA_REVISION = 366685
VERSION = 1

# Bumped automatically every time this file is run as a builder - see
# _bump_build(). It exists so an installer can tell OLDER from NEWER rather
# than merely DIFFERENT, which is all a byte comparison can say.
#
# VERSION above has been 1 since the first build. That is exactly why this is
# automatic: a number a person has to remember to raise is a number that
# quietly stops moving, and then everything built on it is wrong.
BUILD = '20260927090855'

# The Postload thread auto-enables every mod, opens dialogs and runs the
# end-to-end experiments. Useful for measurement, absolutely not something to
# hand a person. False = a clean build with only the real features.
TEST_MODE = False

# One-off: answer the save editor session's open question about what exists
# on the mod editor map. Independent of TEST_MODE.
MODEDITOR_PROBE = False
ENABLE_TEST = False
HEARTBEAT = True     # bisect: the only thing that runs continuously
PROBE_ENGINE_ERRORS = False  # fires error()/assert(false); pops a modal on assert-enabled builds
SPAWN_TEST = False   # quick-start, spawn units fast, read them back
FAULT_INJECT = False  # deliberately break things, to prove the report goes red
CTH_TEST = False  # quick-starts a campaign to exercise the sweep

# The mod used to prove the extender does something. Built by
# build_servicerecord.py. Enabled in memory only, never saved to disk.
TEST_MOD = 'JA3SEsr'

TEST_COPY = r'E:\Jagged Alliance 3 - Nemesis\Test Game Copy\Jagged Alliance 3'

REVISIONS = 'return %d, %d\n' % (LUA_REVISION, LUA_REVISION)

# Loaded into the CONSOLE's own environment by pre_load, the way EpicEnabler
# does it. The console runs inside LuaModEnv, so this is the lever that opens
# it - and it is why "the console is sandboxed" stops being a wall.
CONSOLE = """
rawset(_G, "JA3SE_ConsoleOpened", true)
return 0;
"""

# No backslash escapes anywhere in this Lua. string.char(10) is a newline.
# They travel through shells and heredocs that eat them, which has bitten this
# project five times.
AUTORUN = """
-- JA3SE - Jagged Alliance 3 Script Extender, v%(VERSION)d
-- Architecture follows EpicEnabler by injto4ka/svetlio.

local NL = string.char(10)

-- The capabilities v0.1 hands to sandboxed mods. Everything here is currently
-- in ModEnvBlacklist, so a mod calling one of these gets nil today.
local GRANTS = {
	"AsyncStringToFile",
	"AsyncFileToString",
	"AsyncCreatePath",
	"AsyncListFiles",
	"AsyncCopyFile",
	-- Asked for by the map-rebuild session, and it was one line. Both are in
	-- ModEnvBlacklist (Mod.lua ~1129), so a mod calling either gets nil today,
	-- and the alternative was reimplementing the .hpk reader in Python.
	-- AsyncMountPack is the better of the two for reading Packs/Maps: it needs
	-- no destination and writes nothing to disk. Marginal risk over what is
	-- already granted here is nil - file WRITE has been on this list from the
	-- start, and reading an archive is strictly weaker than that.
	"AsyncUnpack",
	"AsyncMountPack",
	-- Yoda measured this one: AsyncFileDelete does nothing from mod code and
	-- says nothing, so a mod trying to consume its own instruction file could
	-- not, and its disarm was decoration. It is a real engine function
	-- (LuaExportedDocs/Global/AsyncOp.lua) and it is in ModEnvBlacklist.
	--
	-- Granting single-file delete is NOT an escalation over what is already
	-- here: AsyncStringToFile has been granted since v0.1, and anything that
	-- can overwrite a file can already destroy its contents. Delete just makes
	-- it honest instead of leaving a zero-byte corpse.
	--
	-- AsyncDeletePath and DeleteFolderTree stay blocked. Removing a TREE is a
	-- different power from removing a file you wrote, and nothing here needs
	-- it.
	"AsyncFileDelete",
	-- Skywalker asked and said the call was mine. Granting it lets
	-- JA3_CommonLib's out-of-memory shield run, which turns an access
	-- violation with no message into an error box that says what happened.
	--
	-- Against it: JA3 may have blacklisted it deliberately, GC timing being
	-- the kind of thing that could break co-op determinism. I judge that risk
	-- low - Lua GC is not observable from game logic unless a mod is using
	-- finalizers or weak tables to watch it - and a silent hard crash is a
	-- certain harm today against a speculative one.
	--
	-- REVERTIBLE IN ONE LINE. If co-op ever desyncs on a machine running the
	-- extender, delete this entry first and re-test before looking anywhere
	-- else.
	"collectgarbage",
}

-- State, created once and never wiped by a reload.
--
-- NOT "if FirstLoad then" - that looks right and is wrong here. The engine
-- clears FirstLoad during its first autorun pass, which happens BEFORE
-- LoadDlcs runs, so FirstLoad is already false by the time a DLC autorun
-- executes and the table would never be created at all. EpicEnabler's
-- FixGlobal is the pattern that actually applies: create if absent, leave
-- alone if present. Costly mistake - a nil here reached inside the dofile
-- wrapper and hung the game on the loading screen.

-- Ordered event trace. Counters answered "how many" but the failure is a
-- sequencing one, so record the actual order once and stop guessing.
if rawget(_G, "JA3SE_TRACE") == nil then rawset(_G, "JA3SE_TRACE", {}) end
-- rawset, not `function JA3SE_T(...)`. The engine guards _G against new
-- globals, so the plain form logged
--     [LUA ERROR] Attempt to create a new global 'JA3SE_T'
-- on every boot since this was written. The trace worked anyway, which is why
-- it survived: an error nobody has to act on is an error nobody reads.
rawset(_G, "JA3SE_T", function(what)
	local t = rawget(_G, "JA3SE_TRACE")
	if t and #t < 400 then t[#t + 1] = what end
end)

if rawget(_G, "JA3SE") == nil then
	rawset(_G, "JA3SE", {
		version = %(VERSION)d,
		reloads_seen = 0,
		applies = 0,
		granted = {},
		weapon_effects = {},   -- weapon id -> { effect names }, set by mods
		patched_action = {},   -- action object -> true, so a reload re-patches
		mod_touch = {},        -- mod id -> { global names it replaced }
		mod_added = {},        -- mod id -> globals it CREATED
		mod_onmsg = {},        -- mod id -> OnMsg handler count after it loaded
		mod_msgs = {},         -- mod id -> { {key,fn} } handlers it registered
		mod_prev = {},         -- mod id -> { global -> value before that mod }
		replace_kind = {},     -- name -> { mod id -> "wraps" | "replaces" }
		versions = {},         -- name -> { engine=fn, {mod=,fn=}, ... } load order
		policy = {},           -- name -> "last"|"first"|"chain"|"merge"
		log = {},
	})
end
local SE = rawget(_G, "JA3SE")

-- marker for "this global did not exist before that mod ran"
if rawget(_G, "JA3SE_ABSENT") == nil then
	rawset(_G, "JA3SE_ABSENT", { absent = true })
end

-- Registering the name makes a plain assignment to it legal. _G's __newindex
-- raises "Attempt to create a new global" for anything not in here, so this is
-- also the mechanism for letting MODS create globals later.
if rawget(_G, "PersistableGlobals") then
	PersistableGlobals.JA3SE = true
end

local function say(s)
	SE.log[#SE.log + 1] = tostring(s)
	if rawget(_G, "DebugPrint") then
		DebugPrint("[JA3SE] " .. tostring(s) .. NL)
	end
end

----------------------------------------------------------------------
-- PATH RESOLVER
--
-- The snapshot records two shapes of name: a plain global like
-- "ImpGetMinMaxStat", and a composite one like
-- "CombatActions.MeleeAttack.GetActionResults" gathered from the preset
-- tables. Everything that WRITES a value back needs to handle both.
--
-- Until now they did not. The conflict policies skipped dotted names outright,
-- and the surgical unload did rawset(_G, "CombatActions.X.Y", v) - which
-- creates a literal global with a dot in its name instead of walking the path.
-- Harmless only because nothing reads such a name.
--
-- Returns the containing TABLE and the final KEY, so callers can get, set, or
-- delete. Returns nil when any step is missing or not a table, which is the
-- normal case after a reload has rebuilt the presets.
----------------------------------------------------------------------

local function ResolvePath(name)
	if type(name) ~= "string" then return nil end
	if not name:find(".", 1, true) then
		return _G, name
	end
	local parts = {}
	for part in string.gmatch(name, "[^.]+") do
		parts[#parts + 1] = part
	end
	if #parts < 2 then return nil end
	local t = _G
	for i = 1, #parts - 1 do
		t = rawget(t, parts[i])
		if type(t) ~= "table" then return nil end
	end
	return t, parts[#parts]
end

local function PathGet(name)
	local t, k = ResolvePath(name)
	if t then return rawget(t, k) end
	return nil
end

local function PathSet(name, value)
	local t, k = ResolvePath(name)
	if not t then return false end
	rawset(t, k, value)
	return true
end

rawset(_G, "JA3SE_ResolvePath", ResolvePath)
rawset(_G, "JA3SE_PathGet", PathGet)
rawset(_G, "JA3SE_PathSet", PathSet)


----------------------------------------------------------------------
-- The capability grant
----------------------------------------------------------------------

-- Must run AFTER Mod.lua defines ModEnvBlacklist and BEFORE ModsLoadCode
-- consumes it. Mod.lua reassigns the table wholesale on every reload, so this
-- has to happen again every single time.
local function OpenSandbox()
	if rawget(_G, "JA3SE_Receipt") then JA3SE_Receipt("sandbox_opened") end
	local bl = rawget(_G, "ModEnvBlacklist")
	if not bl then return false end
	SE.granted = {}
	for i = 1, #GRANTS do
		local name = GRANTS[i]
		if rawget(_G, name) ~= nil then
			bl[name] = nil
			SE.granted[#SE.granted + 1] = name
		end
	end
	return true
end

-- Editing ModEnvBlacklist is not enough on its own.
--
-- LuaModEnv captures the blacklist by REFERENCE when it builds an env
-- (Mod.lua:1316, `local env_blacklist = ModEnvBlacklist`), and a mod's env is
-- built in ModsReloadDefs - which normally ran long before the extender
-- existed. So the mod keeps consulting whichever table was current back then,
-- and opening the CURRENT table does nothing for it. Measured: a fresh env saw
-- the grant, the real mod did not.
--
-- Wrapping the constructor removes the ordering problem entirely: whatever
-- table is current when an env is built, it is opened first.
-- Editing the blacklist turned out to be unreliable in practice: which table
-- a given env consults depends on exactly when that env was built, and mod
-- envs are built in ModsReloadDefs at a point we do not control. Two runs were
-- spent chasing that.
--
-- Granting straight INTO the env table removes the timing question. The
-- sandbox's own safe_rawget returns rawget(env, key) whenever it is non-nil,
-- and __index is never consulted for a key the table already has - so a value
-- placed here is visible no matter what any blacklist says.
--
-- It is also better design: capabilities are granted per mod, explicitly,
-- instead of weakening the sandbox for everyone.
local function GrantToEnv(env)
	if type(env) ~= "table" then return 0 end
	local n = 0
	for i = 1, #GRANTS do
		local name = GRANTS[i]
		local fn = rawget(_G, name)
		if fn ~= nil and rawget(env, name) == nil then
			rawset(env, name, fn)
			n = n + 1
		end
	end
	-- the extender's own API, so it is reachable even if something blacklists it
	for _, api in ipairs({"JA3SE_Version", "JA3SE_Granted", "JA3SE_Native",
		"JA3SE_SetWeaponEffects", "JA3SE_GetWeaponEffects"}) do
		rawset(env, api, rawget(_G, api))
	end
	return n
end

local function GrantToAllMods()
	local mods = rawget(_G, "Mods")
	if not mods then return 0 end
	local n = 0
	for _, m in pairs(mods) do
		local e = m and rawget(m, "env")
		if e then
			GrantToEnv(e)
			n = n + 1
		end
	end
	SE.envs_granted = n
	return n
end

local function WrapLuaModEnv()
	local orig = rawget(_G, "LuaModEnv")
	if not orig or SE.wrapped_env == orig then return false end
	rawset(_G, "LuaModEnv", function(...)
		SE.env_opens = (SE.env_opens or 0) + 1
		pcall(OpenSandbox)
		local env = orig(...)
		pcall(function() GrantToEnv(env) end)
		return env
	end)
	SE.wrapped_env = rawget(_G, "LuaModEnv")
	return true
end

----------------------------------------------------------------------
-- The public API mods call. Anything put in _G is visible to sandboxed
-- mod code for free, because LuaModEnv's __index falls through to the
-- real _G for names that are not blacklisted.
----------------------------------------------------------------------

local function PublishAPI()
	rawset(_G, "JA3SE_Version", function() return SE.version end)

	-- ---- the map editor catalog ---------------------------------------------
	-- Everything a map editor needs that a savegame cannot tell you. Units,
	-- items, weapon attachments, squads - these live in the engine's preset
	-- tables, so the only place to read them is from inside the running game.
	--
	-- Measured, not assumed: 185 preset classes exist; these are the ones with
	-- map-editor value. `JA3SE_ListPresets` is generic, so JAPE can pull any of
	-- the other 180 without another build from me.
	--
	-- Output is TSV in sections, matching how the live link already writes its
	-- files, so nothing new is needed to parse it.
	
	-- DisplayName and friends are T() objects, not strings. tostring() on one
	-- gives a useless table address, so translate properly and fall back quietly.
	local function CatText(v)
		if v == nil then return "" end
		-- A T() string is USERDATA on this build, not a table, so a
		-- type=="table" test misses every display name and the export
		-- fills up with "userdata: FF0000...". Translate anything that
		-- is not already plain text.
		local vt = type(v)
		if vt ~= "string" and vt ~= "number" and vt ~= "boolean" then
			local ok, out = pcall(function() return _InternalTranslate(v) end)
			if ok and type(out) == "string" then v = out else return "" end
		end
		v = tostring(v)
		v = v:gsub("%%s+", " ")
		return v
	end
	
	local function PresetRows(cls, fields)
		local P = rawget(_G, "Presets")
		local grp = P and rawget(P, cls)
		local rows = {}
		-- Preset groups are reachable under BOTH a numeric index and their
		-- group name, so a plain pairs() walk visits every preset exactly
		-- twice - the first export came out perfectly doubled. Dedupe on the
		-- object itself. An if-guard rather than goto, so this does not
		-- depend on which Lua version the engine ships.
		local seen = {}
		for _, sub in pairs(grp or {}) do
			for _, item in ipairs(sub or {}) do
				if not seen[item] then
					seen[item] = true
					local vals = {}
					for _, f in ipairs(fields) do
						-- index the object, not rawget: most values are inherited
						-- from the composite class defaults, and rawget returns
						-- nil for every one of them.
						local ok, v = pcall(function() return item[f] end)
						vals[#vals + 1] = CatText(ok and v or nil)
					end
					rows[#rows + 1] = table.concat(vals, "	")
				end
			end
		end
		table.sort(rows)
		return rows
	end
	
	rawset(_G, "JA3SE_ListPresets", function(cls, fields)
		return PresetRows(cls, fields or { "id" })
	end)
	
	-- class, the columns to export, and the section name JAPE will look for
	local CATALOG = {
		{ "units",       "UnitDataCompositeDef",
		  { "id", "Name", "Affiliation", "MaxHitPoints", "Portrait", "group" } },
		{ "items",       "InventoryItemCompositeDef",
		  { "id", "DisplayName", "group", "Cost", "Caliber", "BaseDamage",
		    "PenetrationClass", "Icon" } },
		{ "attachments", "WeaponComponentSharedClass",
		  { "id", "DisplayName", "Slot", "Cost" } },
		{ "squads",      "EnemySquads",       { "id", "group" } },
		{ "roles",       "EnemyRole",         { "id", "DisplayName" } },
		{ "weapontypes", "WeaponType",        { "id", "Name", "Description" } },
		{ "archetypes",  "AIArchetype",       { "id", "group" } },
		{ "bodyparts",   "TargetBodyPart",    { "id", "display_name" } },
	}
	

	-- ---- chance-to-hit range sweep ------------------------------------------
	-- For anyone rebalancing combat, the question is "what does accuracy
	-- actually do over distance" - and until now that has been guesswork plus
	-- playtesting. The game will just tell us.
	--
	--   Unit:CalcChanceToHit(target, action, args, chance_only)   Unit.lua:6947
	--
	-- Two things make this work. It returns THREE values - final chance, base
	-- chance, and the list of modifiers that applied - so we get the formula
	-- showing its work. And its first argument accepts a bare map POINT
	-- (`IsPoint(target)`, line 2 of the function), so distance can be swept by
	-- passing positions further and further out. Nothing is teleported and no
	-- target unit is needed.
	rawset(_G, "JA3SE_CTHSweep", function(opts)
		opts = opts or {}
		local out = {}
		local function add(s) out[#out + 1] = s end
		local function finish()
			pcall(function()
				AsyncStringToFile("AppData/ja3se-cth-sweep.txt",
					table.concat(out, NL) .. NL)
			end)
			return #out
		end
	
		local shooter = opts.unit
		if not shooter then
			for _, u in ipairs(rawget(_G, "g_Units") or {}) do
				local ok = pcall(function()
					return u.team and u.team.player_team and not u:IsDead()
				end)
				if ok and u.team and u.team.player_team and not u:IsDead() then
					shooter = u
					break
				end
			end
		end
		if not shooter then
			add("no shooter found - load a game and be in a sector with a merc")
			return finish()
		end
	
		local CA = rawget(_G, "CombatActions") or {}
		local action
		for _, id in ipairs(opts.actions or { "SingleShot", "BurstFire", "Buckshot" }) do
			if CA[id] then action = CA[id] break end
		end
		if not action then
			add("no attack action available")
			return finish()
		end
	
		local weapon = select(1, action:GetAttackWeapons(shooter))
		add("shooter=" .. tostring(shooter.session_id or shooter.unitdatadef_id))
		add("action=" .. tostring(action.id))
		add("weapon=" .. tostring(weapon and (weapon.class or weapon.id)))
		add("")
	
		local tile = (rawget(_G, "const") or {}).SlabSizeX or 0
		local pos = shooter:GetPos()
		local maxd = opts.max_tiles or 40
		local aims = opts.aims or { 0, 1, 2, 3 }
	
		local head = { "distance_tiles" }
		for _, a in ipairs(aims) do head[#head + 1] = "aim" .. a end
		add(table.concat(head, "	"))
	
		for d = 1, maxd do
			local row = { tostring(d) }
			for _, a in ipairs(aims) do
				local ok, cth = pcall(function()
					local p = point(pos:x() + d * tile, pos:y(), pos:z())
					return shooter:CalcChanceToHit(p, action,
						{ aim = a, weapon = weapon }, "chance_only")
				end)
				row[#row + 1] = tostring(ok and cth or "err")
			end
			add(table.concat(row, "	"))
		end
	
		-- the formula showing its work, at a few representative distances
		for _, d in ipairs(opts.breakdown_at or { 5, 10, 20, 30 }) do
			add("")
			add("[breakdown at " .. d .. " tiles, aim 0]")
			local ok, cth, base, mods = pcall(function()
				local p = point(pos:x() + d * tile, pos:y(), pos:z())
				return shooter:CalcChanceToHit(p, action,
					{ aim = 0, weapon = weapon })
			end)
			if not ok then
				add("  failed: " .. tostring(cth))
			else
				add("  final=" .. tostring(cth) .. "  base=" .. tostring(base))
				for _, m in ipairs(mods or {}) do
					add("  " .. tostring(m.id) .. "	" .. tostring(m.value)
						.. "	" .. CatText(m.name))
				end
			end
		end
		return finish()
	end)

	rawset(_G, "JA3SE_WriteCatalog", function()
		local out = { "# JA3SE map editor catalog",
		              "# written by the running game - none of this is in a save",
		              "version	" .. tostring(SE.version) }
		local counts = {}
		for _, spec in ipairs(CATALOG) do
			local section, cls, fields = spec[1], spec[2], spec[3]
			local rows = PresetRows(cls, fields)
			counts[#counts + 1] = section .. "=" .. #rows
			out[#out + 1] = ""
			out[#out + 1] = "[" .. section .. "] " .. cls
			out[#out + 1] = "#" .. table.concat(fields, "	")
			for _, r in ipairs(rows) do out[#out + 1] = r end
		end
		local text = table.concat(out, NL) .. NL
		pcall(function()
			AsyncStringToFile("AppData/ja3se-catalog.txt", text)
		end)
		pcall(function()
			AsyncStringToFile("AppData/ja3se-catalog-summary.txt",
				table.concat(counts, NL) .. NL .. "bytes=" .. #text .. NL)
		end)
		return #text
	end)
	

	-- ---- map-editor catalog probe -------------------------------------------
	-- the tester wants JAPE to grow a real map editor, which needs everything the
	-- game's own ModEditor can see: unit types, items, weapons, and whatever
	-- else lives in the engine rather than in a savegame. None of that can be
	-- read from a .sav, so it has to come from in here.
	--
	-- Discover first. Guessing preset class names has cost this project days;
	-- dump what is actually present, then build the export against that.
	rawset(_G, "JA3SE_ProbePresets", function()
		local lines = {}
		local function add(s) lines[#lines + 1] = s end
		local P = rawget(_G, "Presets")
		if type(P) ~= "table" then
			add("NO Presets TABLE")
		else
			local names = {}
			for cls in pairs(P) do names[#names + 1] = tostring(cls) end
			table.sort(names)
			add("preset_classes=" .. #names)
			for _, cls in ipairs(names) do
				local group = P[cls]
				local n, sample, groups = 0, nil, 0
				-- dedupe: groups are reachable by index AND by name, so a
				-- plain walk counts everything twice. This dump fed doubled
				-- numbers into the map-rebuild spec before it was caught.
				local seen = {}
				for _, sub in pairs(group or {}) do
					groups = groups + 1
					for _, item in ipairs(sub or {}) do
						if not seen[item] then
							seen[item] = true
							n = n + 1
						if not sample then sample = item end
						end
					end
				end
				local fields = {}
				if type(sample) == "table" then
					for k, v in pairs(sample) do
						if type(v) ~= "function" and type(v) ~= "table" then
							fields[#fields + 1] = tostring(k)
						end
					end
					table.sort(fields)
					while #fields > 14 do table.remove(fields) end
				end
				add(string.format("%%s	count=%%d	groups=%%d	id=%%s	fields=%%s",
					cls, n, groups,
					type(sample) == "table" and tostring(rawget(sample, "id")) or "?",
					table.concat(fields, ",")))
			end
		end
		pcall(function()
			AsyncStringToFile("AppData/ja3se-presets.txt",
				table.concat(lines, NL) .. NL)
		end)
		return #lines
	end)
	

	-- NOTE: there is deliberately no event registry here any more.
	-- Haemimont's own docs say OnMsg already stacks: "you can register as many
	-- handlers for the same message as you want", run in registration order.
	-- v0.1 reimplemented that for nothing. Mods should just use OnMsg.
	--
	-- The real gap is FUNCTION overrides, which do clobber each other. That is
	-- what the weapon-effects registry below is an instance of: instead of two
	-- mods each replacing GetActionResults, they both write to one table.

	-- The other half of the extender.
	--
	-- The native loader runs before Lua exists, so it cannot call into here and
	-- we cannot call into it. It leaves a status file beside the exe saying what
	-- it did; we read that and republish it, so a mod can ask what native
	-- features are present without knowing any of this.
	--
	-- Absent file means no DLL installed, which is a perfectly normal state -
	-- the Lua half works on its own.
	local function ReadNative()
		SE.native = { present = false }
		local read = rawget(_G, "AsyncFileToString")
		if not read then return end
		local err, text = read("ja3se-native.txt")
		if err or not text then return end
		SE.native.present = true
		for line in string.gmatch(text, "[^" .. NL .. "]+") do
			local k, v = string.match(line, "^([%%w_]+)=(%%-?%%w+)")
			if k and v then
				SE.native[k] = tonumber(v) or v
			end
		end
	end

	rawset(_G, "JA3SE_Native", function()
		local n = SE.native or { present = false }
		return {
			present = n.present or false,
			version = n.native_version or 0,
			skip_splash = (n.skip_splash_applied == 1),
		}
	end)

	ReadNative()

	-- Declare which status effects a weapon applies on hit.
	--   JA3SE_SetWeaponEffects("Machete", { "Bleeding" })
	-- Key is the item id / class name, matched with IsKindOf, so it also
	-- covers anything inheriting from it.
	rawset(_G, "JA3SE_SetWeaponEffects", function(weapon_id, effects)
		if type(weapon_id) ~= "string" then return false end
		if effects ~= nil and type(effects) ~= "table" then return false end
		SE.weapon_effects[weapon_id] = effects or nil
		return true
	end)

	rawset(_G, "JA3SE_GetWeaponEffects", function(weapon)
		if not weapon then return nil end
		for id, effects in pairs(SE.weapon_effects) do
			local ok, is = pcall(IsKindOf, weapon, id)
			if ok and is then return effects end
		end
		return nil
	end)

	rawset(_G, "JA3SE_Granted", function()
		local out = {}
		for i = 1, #SE.granted do out[i] = SE.granted[i] end
		return out
	end)

	----------------------------------------------------------------------
	-- Un-hardcoding weapon effects
	--
	-- The engine already has a general mechanism - args.applied_status - and
	-- wired it to exactly one weapon by name. From CombatAction.lua, in both
	-- MeleeAttack (the id at line 1345) and Brutalize (2416):
	--
	--     if IsKindOf(args.weapon, "GutHookKnife") then
	--         args.applied_status = { "Bleeding" }
	--     end
	--
	-- So melee weapons can carry effects; only one is allowed to. (Firearms
	-- have a proper data-driven AppliedEffects field, but it lives on
	-- AmmoProperties, so melee cannot use it.)
	--
	-- We wrap rather than replace: work out the weapon, look up its declared
	-- effects, set applied_status, then call the original. The original still
	-- does its GutHookKnife check, which is harmless - it sets the same value
	-- that knife would have had anyway.
	----------------------------------------------------------------------

	----------------------------------------------------------------------
	-- Mod conflict detection
	--
	-- The single biggest complaint from actual mod authors: two mods replace
	-- the same function, the last one loaded wins, the other silently does
	-- nothing, and nobody can see it happening. "ppl go to great lengths to
	-- try to stay compatible" - Jaeger Allianz, 2026-09-02.
	--
	-- The trick is that this needs NO cooperation from the mods. ModsLoadCode
	-- loads them one at a time through ModDef:LoadCode. Wrap that, snapshot
	-- every function-valued global before and after each mod, and we know
	-- exactly what each mod replaced. Anything two mods both touched is a
	-- conflict, named, with no changes to either mod.
	----------------------------------------------------------------------

	-- Does `fn` keep `target` as an upvalue? If so it can still call it, which
	-- means the two mods are layering rather than fighting.
	rawset(_G, "JA3SE_HasUpvalue", function(fn, target)
		if type(fn) ~= "function" or target == nil then return false end
		local dbg = rawget(_G, "debug")
		if not dbg or not dbg.getupvalue then return false end
		for i = 1, 60 do
			local ok, name, val = pcall(dbg.getupvalue, fn, i)
			if not ok or name == nil then break end
			if val == target then return true end
		end
		return false
	end)

	-- The real OnMsg handler store.
	--
	-- OnMsg itself is an empty facade: cthreads.lua:64 gives it a __newindex
	-- that appends into `message_to_staticfuncs`, which is a FILE-LOCAL
	-- (cthreads.lua:7). Iterating OnMsg therefore reports nothing, which is
	-- exactly the wrong answer a first attempt got.
	--
	-- MsgClear is a two-line function whose only upvalue is that table, so it
	-- is reachable. debug is blacklisted for mods but we are a DLC.
	local function GetMsgTable()
		local dbg = rawget(_G, "debug")
		local mc = rawget(_G, "MsgClear")
		if not dbg or not dbg.getupvalue or type(mc) ~= "function" then return nil end
		for i = 1, 8 do
			local ok, nm, val = pcall(dbg.getupvalue, mc, i)
			if not ok or nm == nil then break end
			if type(val) == "table" then return val end
		end
		return nil
	end
	rawset(_G, "JA3SE_GetMsgTable", GetMsgTable)

	-- flat map: "message#index" -> handler, so a diff names what was added
	local function MsgSnapshot()
		local t = GetMsgTable()
		local snap, n = {}, 0
		if not t then return snap, 0 end
		for msg, list in pairs(t) do
			if type(list) == "table" then
				for i = 1, #list do
					snap[tostring(msg) .. "#" .. i] = list[i]
					n = n + 1
				end
			end
		end
		return snap, n
	end
	rawset(_G, "JA3SE_MsgSnapshot", MsgSnapshot)

	local function SnapshotFuncs()
		local snap = {}
		for k, v in pairs(_G) do
			if type(k) == "string" and type(v) == "function" then
				snap[k] = v
			end
		end
		-- also watch the preset tables mods most often reach into
		for _, tname in ipairs({"CombatActions", "CharacterEffectDefs"}) do
			local t = rawget(_G, tname)
			if type(t) == "table" then
				for id, obj in pairs(t) do
					if type(obj) == "table" then
						for mk, mv in pairs(obj) do
							if type(mv) == "function" and type(mk) == "string" then
								snap[tname .. "." .. tostring(id) .. "." .. mk] = mv
							end
						end
					end
				end
			end
		end
		return snap
	end

	local function WrapLoadCode()
		local MD = rawget(_G, "ModDef")
		if not MD or type(MD.LoadCode) ~= "function" then JA3SE_T("wrap:NO_MODDEF") return false end
		if SE.wrapped_loadcode == MD.LoadCode then JA3SE_T("wrap:ALREADY") return false end
		JA3SE_T("wrap:INSTALL")
		local orig = MD.LoadCode
		MD.LoadCode = function(self, ...)
			rawset(_G, "JA3SE_DBG_calls", (rawget(_G, "JA3SE_DBG_calls") or 0) + 1)
			JA3SE_T("LoadCode:" .. tostring(rawget(self, "id")))
			local id = tostring(rawget(self, "id") or "?")
			local before = SnapshotFuncs()
			local msg_before, msg_before_n = MsgSnapshot()
			local res = orig(self, ...)
			local after = SnapshotFuncs()
			local msg_after, msg_after_n = MsgSnapshot()

			-- OnMsg handlers this mod registered. Needed for a surgical
			-- unload: they are not function-valued globals, so the scan
			-- below misses them completely.
			local msg_added = {}
			for k, v in pairs(msg_after) do
				if msg_before[k] == nil then
					-- keep the handler itself: indices shift as handlers are
					-- removed, so an unload has to match by identity
					msg_added[#msg_added + 1] = { key = k, fn = v }
				end
			end
			SE.mod_msgs[id] = msg_added
			SE.msg_total = msg_after_n

			-- exact pre-mod value of everything this mod replaced or created,
			-- which is what a surgical unload has to put back. nil means the
			-- mod created that name and unloading should remove it.
			-- ABSENT is a sentinel, not decoration: `prev[k] = nil` stores
			-- nothing in a Lua table, so recording "this did not exist before"
			-- that way silently loses every global the mod CREATED. Measured:
			-- 10 created globals, 0 removed, and the unload looked like it
			-- worked.
			local prev = {}
			for k, v in pairs(after) do
				if before[k] ~= v then
					prev[k] = (before[k] == nil) and JA3SE_ABSENT or before[k]
				end
			end
			SE.mod_prev[id] = prev
			-- Feasibility measurement for a surgical unload (avoiding the
			-- full Lua reload the engine does on every mod toggle).
			-- A precise undo is only safe if we captured everything the mod
			-- did. Count what it ADDED as well as what it replaced.
			local added, added_names = 0, {}
			for k, v in pairs(after) do
				if before[k] == nil then
					added = added + 1
					if #added_names < 6 then added_names[#added_names + 1] = k end
				end
			end
			SE.mod_added[id] = { n = added, sample = added_names }

			-- OnMsg handlers are the other thing mod code leaves behind, and
			-- they are not function-valued globals so the scan above misses
			-- them entirely.
			local om = rawget(_G, "OnMsg")
			local omn = 0
			if type(om) == "table" then
				for _, list in pairs(om) do
					if type(list) == "table" then
						for _ in pairs(list) do omn = omn + 1 end
					elseif type(list) == "function" then
						omn = omn + 1
					end
				end
			end
			SE.mod_onmsg[id] = omn

			local touched = {}
			for k, v in pairs(after) do
				if before[k] ~= nil and before[k] ~= v then
					touched[#touched + 1] = k          -- replaced something
					-- Is this a COOPERATING wrapper or a HARD replacement?
					--
					-- The distinction static analysis cannot make, and the one
					-- that decides whether a "conflict" actually matters. A
					-- polite mod keeps the previous function and calls it, so
					-- the old version survives as an upvalue of the new one.
					-- A rude mod throws it away.
					--
					-- debug is blacklisted for mods but we are a DLC, so we
					-- can look.
					SE.replace_kind[k] = SE.replace_kind[k] or {}
					SE.replace_kind[k][id] =
						JA3SE_HasUpvalue(v, before[k]) and "wraps" or "replaces"

					-- Keep EVERY version, in load order, plus the engine's
					-- own. Nothing is lost - which is what makes it possible
					-- to run the losers again later.
					if not SE.versions[k] then
						SE.versions[k] = { engine = before[k] }
					end
					SE.versions[k][#SE.versions[k] + 1] = { mod = id, fn = v }
				end
			end
			table.sort(touched)
			SE.mod_touch[id] = touched
			return res
		end
		JA3SE_Receipt("loadcode_wrapped")
		SE.wrapped_loadcode = MD.LoadCode
		rawset(_G, "JA3SE_DBG_installs", (rawget(_G, "JA3SE_DBG_installs") or 0) + 1)
		-- count ModsLoadCode itself, to tell "our wrapper was bypassed" apart
		-- from "the engine never loaded mod code again after we armed"
		local MLC = rawget(_G, "ModsLoadCode")
		if type(MLC) == "function" and rawget(_G, "JA3SE_DBG_mlc_orig") ~= MLC then
			rawset(_G, "JA3SE_DBG_mlc_orig", MLC)
			rawset(_G, "ModsLoadCode", function(...)
				rawset(_G, "JA3SE_DBG_mlc", (rawget(_G, "JA3SE_DBG_mlc") or 0) + 1)
				JA3SE_T("ModsLoadCode")
				rawset(_G, "JA3SE_DBG_mlc_n",
					#(rawget(_G, "ModsLoaded") or ""))
				return MLC(...)
			end)
		end
		return true
	end

	JA3SE_Receipt("api_published")
	rawset(_G, "JA3SE_WrapLoadCode", WrapLoadCode)
	rawset(_G, "JA3SE_OpenSandboxNow", OpenSandbox)
	WrapLoadCode()

	----------------------------------------------------------------------
	-- Conflict RESOLUTION
	--
	-- Nexus picks a winner per file, because two files cannot share a path.
	-- These are functions, so there is a third option: run more than one.
	--
	--   last   what happens today - the last mod loaded wins, others lost
	--   first  the earliest mod wins (Nexus-style priority, inverted)
	--   chain  run every version in load order; last return value is used.
	--          Right for functions that DO something.
	--   merge  run every version and concatenate the tables they return.
	--          Right for functions that ANSWER something with a list - two
	--          mods each adding perks should give you both sets of perks.
	--
	-- Honest limit: two mods that hard-replace the same value-returning
	-- function with genuinely different logic sometimes have no correct
	-- merge. That is what `first`/`last` are for, and why the report always
	-- says which policy was used.
	----------------------------------------------------------------------

	rawset(_G, "JA3SE_SetPolicy", function(name, policy)
		if type(name) ~= "string" then return false end
		if policy ~= nil and policy ~= "last" and policy ~= "first"
			and policy ~= "chain" and policy ~= "merge" then return false end
		SE.policy[name] = policy
		return true
	end)

	local function MakeDispatcher(vers, policy)
		if policy == "first" then
			return vers[1].fn
		end
		if policy == "chain" then
			return function(...)
				local last
				for i = 1, #vers do
					local packed = table.pack(pcall(vers[i].fn, ...))
					if packed[1] then
						last = packed
					end
				end
				if last then return table.unpack(last, 2, last.n) end
			end
		end
		-- merge
		return function(...)
			local out = {}
			local seen = {}
			for i = 1, #vers do
				local ok, res = pcall(vers[i].fn, ...)
				if ok and type(res) == "table" then
					for _, item in ipairs(res) do
						if not seen[item] then
							seen[item] = true
							out[#out + 1] = item
						end
					end
				end
			end
			return out
		end
	end

	-- Install dispatchers for every conflicted name that has a policy set.
	-- Runs after all mods have loaded, since that is when every version is
	-- known. Only plain global names for now - nested paths like
	-- CombatActions.X.Y are recorded but not yet re-installable.
	rawset(_G, "JA3SE_ApplyPolicies", function()
		local n = 0
		SE.applied_policy = {}
		for name, vers in pairs(SE.versions) do
			-- nested names are now installable too, via the resolver
			if #vers > 1 then
				local pol = SE.policy[name] or "last"
				if pol ~= "last" then
					local ok, fn = pcall(MakeDispatcher, vers, pol)
					if ok and fn and PathSet(name, fn) then
						SE.applied_policy[name] = pol .. "(" .. #vers .. ")"
						n = n + 1
					end
				end
			end
		end
		return n
	end)

	rawset(_G, "JA3SE_Conflicts", function()
		-- name -> list of mods that replaced it
		local by_name = {}
		for modid, names in pairs(SE.mod_touch) do
			for _, n in ipairs(names) do
				by_name[n] = by_name[n] or {}
				by_name[n][#by_name[n] + 1] = modid
			end
		end
		local out = {}
		for n, mods in pairs(by_name) do
			if #mods > 1 then
				table.sort(mods)
				-- A conflict only BITES if someone hard-replaced. If every mod
				-- after the first wrapped what it found, they are layered and
				-- all of them still run.
				local kinds = SE.replace_kind[n] or {}
				local hard = 0
				local desc = {}
				for i = 1, #mods do
					local k = kinds[mods[i]] or "?"
					desc[#desc + 1] = mods[i] .. "(" .. k .. ")"
					if k == "replaces" then hard = hard + 1 end
				end
				out[#out + 1] = {
					name = n,
					mods = mods,
					desc = desc,
					-- first mod always "replaces" the engine's own version,
					-- which is fine. Trouble starts at the second one.
					severity = (hard > 1) and "BREAKS" or "layered",
				}
			end
		end
		table.sort(out, function(a, b) return a.name < b.name end)
		return out
	end)

	----------------------------------------------------------------------
-- The user-facing half
--
-- Everything above lands in a text file that only a developer reads. This
-- puts it on screen, using the game's own mechanism: the engine already
-- shows mod load errors in a popup on the pre-game menu
-- (Mod.lua:2000 OnMsg.PreGameMenuOpen -> DisplayModsLoadCodeErrorsMessage),
-- so a conflict report there looks native rather than bolted on.
--
-- Shown ONCE per session, and only when there is something to say. A popup
-- on every launch that says "nothing is wrong" is how people learn to click
-- through warnings without reading them.
----------------------------------------------------------------------

----------------------------------------------------------------------
-- SETTINGS PAGE
--
-- Everything below this point worked but had no caller: the quiet mod
-- disable, preset merging, conflict policies. Rather than wait for another
-- tool to call them, put them behind switches the player can actually reach.
--
-- Uses the engine's own options system (OptionsCategories + OptionsObject
-- properties, options.lua:785), which is the mechanism EpicEnabler proved on
-- this engine. Both tables are rebuilt by a Lua reload, so this re-runs from
-- Apply() every time.
----------------------------------------------------------------------

local function OptionOn(id, default_on)
	local f = rawget(_G, "GetAccountStorageOptionValue")
	if not f then return default_on end
	local ok, v = pcall(f, id)
	if not ok or v == nil then return default_on end
	return v == true or v == "On"
end
rawset(_G, "JA3SE_OptionOn", OptionOn)

local function AddSettingsPage()
	local cats = rawget(_G, "OptionsCategories")
	local oo = rawget(_G, "OptionsObject")
	if type(cats) ~= "table" or type(oo) ~= "table" or type(oo.properties) ~= "table" then
		return false
	end
	if table.find(cats, "id", "JA3SE") then return true end   -- already added

	cats[#cats + 1] = {
		id = "JA3SE",
		display_name = Untranslated("Extender"),
		caps_name = Untranslated("EXTENDER"),
	}

	local function prop(id, name, help, default)
		if table.find(oo.properties, "id", id) then return end
		oo.properties[#oo.properties + 1] = {
			name = Untranslated(name), id = id, category = "JA3SE",
			storage = "account", editor = "bool", default = default,
			help_text = Untranslated(help),
		}
	end

	prop("JA3SE_ShowConflicts", "Show mod conflicts",
		"On the main menu, list any mods that overwrite each other.", true)
	prop("JA3SE_FastModSwitch", "Fast mod switching",
		"Disable a mod without the game restarting when you leave the Mod Manager.", true)
	prop("JA3SE_MergeMods", "Merge conflicting mods",
		"When two mods change different parts of the same item, apply both.", false)

	SE.settings_added = true
	return true
end
rawset(_G, "JA3SE_AddSettingsPage", AddSettingsPage)

-- Fast mod switching: intercept the Mod Manager's exit.
--
-- This is the whole user-facing point. The player unticks a mod and backs out;
-- normally that path sees the list changed, warns, opens a loading screen and
-- reloads everything. Instead, apply each removal precisely first, then sync
-- the snapshot so the exit has nothing to react to.
--
-- Only DISABLES are handled. Enabling still takes the normal path, because a
-- quiet enable has not been built or tested.
local function WrapModsUIDialogEnd()
	local orig = rawget(_G, "ModsUIDialogEnd")
	if type(orig) ~= "function" or SE.wrapped_modsend == orig then return false end
	rawset(_G, "ModsUIDialogEnd", function(dialog, callback)
		pcall(function()
			if not OptionOn("JA3SE_FastModSwitch", true) then return end
			local acct = rawget(_G, "AccountStorage")
			local now = (acct and acct.LoadMods) or {}
			local was = rawget(_G, "g_InitialMods") or {}
			local removed = {}
			for _, id in ipairs(was) do
				if not table.find(now, id) then removed[#removed + 1] = id end
			end
			-- anything ADDED still needs the engine's normal path
			local added = false
			for _, id in ipairs(now) do
				if not table.find(was, id) then added = true end
			end
			if #removed == 0 or added then return end
			for _, id in ipairs(removed) do
				pcall(function() JA3SE_DisableModQuietly(id) end)
			end
			SE.fast_switches = (SE.fast_switches or 0) + #removed
			say("fast mod switch: disabled " .. #removed .. " without a reload")
		end)
		return orig(dialog, callback)
	end)
	SE.wrapped_modsend = rawget(_G, "ModsUIDialogEnd")
	return true
end
rawset(_G, "JA3SE_WrapModsUIDialogEnd", WrapModsUIDialogEnd)

----------------------------------------------------------------------
-- DISABLE A MOD WITHOUT THE RESTART
--
-- The complete flow, combining the two halves proven separately:
--
--   1. undo the mod's CODE surgically      (no ReloadLua needed)
--   2. unload its ITEMS the engine's way   (presets, options)
--   3. drop it from ModsLoaded and AccountStorage
--   4. sync g_InitialMods so the Mod Manager sees nothing changed
--
-- Step 4 alone would be dangerous: it suppresses the heavy exit path, and the
-- heavy path is what normally APPLIES the change. Suppressing it without doing
-- 1-3 would leave a mod ticked off in the UI and still live in the game. The
-- steps only make sense together.
--
-- What this buys: disabling a mod and backing out of the Mod Manager stops
-- restarting the game. That is vanilla JA3 behaviour, not a JAPE bug - every
-- modded player hits it.
----------------------------------------------------------------------

rawset(_G, "JA3SE_EnableModQuietly", function(mod_id)
	mod_id = tostring(mod_id)
	local mods = rawget(_G, "Mods")
	local mod = mods and mods[mod_id]
	if not mod then return false, "unknown mod " .. mod_id end

	local loaded = rawget(_G, "ModsLoaded")
	if type(loaded) == "table" then
		for _, m in ipairs(loaded) do
			if rawget(m, "id") == mod_id then return true, "already loaded" end
		end
	end

	local steps = {}

	-- 1. into AccountStorage, so GetModsToLoad and the Mod Manager agree
	steps[#steps+1] = "storage=" .. tostring(pcall(function()
		if rawget(_G, "TurnModOn") then TurnModOn(mod_id, false) end
	end))

	-- 2. into ModsLoaded, which is what LoadCode/LoadItems iterate
	steps[#steps+1] = "listed=" .. tostring(pcall(function()
		local l = rawget(_G, "ModsLoaded")
		if type(l) == "table" then l[#l + 1] = mod end
	end))

	-- 3. its env must carry our grants BEFORE its code runs, or a mod written
	-- against JA3SE sees nothing on the very load that matters
	steps[#steps+1] = "granted=" .. tostring(pcall(function()
		local e = rawget(mod, "env")
		if e then GrantToEnv(e) end
	end))

	-- 4. code, then items, then options - the order the engine uses.
	-- ModsReloadItems fills ModsLoaded, reloads (running ModsLoadCode), and
	-- only then calls ContinueModsReloadItems to load items. Reversing it
	-- would run item code against classes that do not exist yet.
	steps[#steps+1] = "code=" .. tostring(pcall(function() mod:LoadCode() end))
	-- Count, do not merely report that the step ran. "items=true" was
	-- reported for a mod holding zero items and cost the map-rebuild session
	-- four boots. ModDef:ItemsLoaded() cannot help either: Mod.lua:565 sets
	-- self.items = ok and items or {}, so a FAILED items.lua leaves an empty
	-- table and the flag reads true forever after.
	local item_n = -1
	steps[#steps+1] = "items=" .. tostring(pcall(function()
		if not mod:ItemsLoaded() then mod:LoadItems() end
		local it = rawget(mod, "items")
		item_n = (type(it) == "table") and #it or -1
	end)) .. ":" .. tostring(item_n)
	steps[#steps+1] = "options=" .. tostring(pcall(function()
		if mod.LoadOptions then mod:LoadOptions() end
	end))

	-- 5. the Mod Manager must not think its list changed underneath it
	steps[#steps+1] = "snapshot=" .. tostring(pcall(function()
		local acct = rawget(_G, "AccountStorage")
		rawset(_G, "g_InitialMods", table.copy((acct and acct.LoadMods) or {}))
	end))

	SE.last_quiet_enable = { mod = mod_id, steps = table.concat(steps, " ") }
	say("quiet enable " .. mod_id .. ": " .. table.concat(steps, " "))
	return true
end)

-- The safety valve. Every quiet path is a place state can drift from what the
-- engine expects; this is the one-click way back to the engine doing it its own
-- way, without relaunching the game.
rawset(_G, "JA3SE_HardReset", function()
	local ok = pcall(function()
		local acct = rawget(_G, "AccountStorage")
		rawset(_G, "g_InitialMods", nil)          -- let the normal path decide
		if rawget(_G, "ModsReloadDefs") then ModsReloadDefs() end
		if rawget(_G, "ModsReloadItems") then ModsReloadItems(nil, true) end
	end)
	say("hard reset requested, engine path ok=" .. tostring(ok))
	return ok
end)

rawset(_G, "JA3SE_DisableModQuietly", function(mod_id)
	mod_id = tostring(mod_id)
	local mods = rawget(_G, "Mods")
	local mod = mods and mods[mod_id]
	if not mod then return false, "unknown mod " .. mod_id end

	local steps = {}

	-- 1. the code
	local ok1 = pcall(function() return JA3SE_UnloadModSurgically(mod_id) end)
	steps[#steps + 1] = "code=" .. tostring(ok1)

	-- 2. the items, using the engine's own unload rather than inventing one
	local ok2 = pcall(function()
		if mod.ItemsLoaded and mod:ItemsLoaded() then mod:UnloadItems() end
		if mod.UnloadOptions then mod:UnloadOptions() end
	end)
	steps[#steps + 1] = "items=" .. tostring(ok2)

	-- 3. out of the loaded list and the account storage
	local ok3 = pcall(function()
		local loaded = rawget(_G, "ModsLoaded")
		if type(loaded) == "table" then
			for i = #loaded, 1, -1 do
				if loaded[i] and rawget(loaded[i], "id") == mod_id then
					table.remove(loaded, i)
				end
			end
		end
		if rawget(_G, "TurnModOff") then TurnModOff(mod_id, false) end
	end)
	steps[#steps + 1] = "delisted=" .. tostring(ok3)

	-- 4. tell the Mod Manager its list is current, so leaving it stays quiet
	local ok4 = pcall(function()
		local acct = rawget(_G, "AccountStorage")
		rawset(_G, "g_InitialMods", table.copy((acct and acct.LoadMods) or {}))
	end)
	steps[#steps + 1] = "snapshot=" .. tostring(ok4)

	SE.last_quiet_disable = { mod = mod_id, steps = table.concat(steps, " ") }
	say("quiet disable " .. mod_id .. ": " .. table.concat(steps, " "))
	return true
end)

----------------------------------------------------------------------
-- RELOAD SURVIVAL PROBE
--
-- "The game throws away everything it knows and rebuilds from scratch" is the
-- folk description, and it is not quite true. ReloadLua does NOT wipe _G - it
-- re-runs autorun.lua, which REASSIGNS the engine's own globals. Anything the
-- engine does not reassign is untouched.
--
-- So the useful question is not "can anything survive" (things already do)
-- but "which KINDS survive". Plant one of each before a reload, count them
-- after, and stop guessing.
----------------------------------------------------------------------

local function PlantSurvivalMarkers()
	SE.survive = SE.survive or {}
	local S = SE.survive
	S.planted = (S.planted or 0) + 1

	-- 1. a plain global the engine never defines
	rawset(_G, "JA3SE_S_PlainGlobal", { stamp = S.planted })

	-- 2. a global registered in PersistableGlobals
	if rawget(_G, "PersistableGlobals") then
		PersistableGlobals.JA3SE_S_Persistable = true
	end
	rawset(_G, "JA3SE_S_Persistable", { stamp = S.planted })

	-- 3. a field inside the engine's own const table
	local c = rawget(_G, "const")
	if type(c) == "table" then c.JA3SE_S_Const = S.planted end

	-- 4. a patched engine function (the thing we know dies)
	local gm = rawget(_G, "GetPreciseTicks")
	if gm and not S.patched_fn then
		S.orig_ticks = gm
		rawset(_G, "GetPreciseTicks", function(...) return S.orig_ticks(...) end)
		S.patched_fn = rawget(_G, "GetPreciseTicks")
	end

	-- 5. an OnMsg handler
	if not S.msg_fn then
		S.msg_fn = function() end
		OnMsg.JA3SE_S_Message = S.msg_fn
	end

	-- 6. a running real-time thread
	if not S.thread then
		S.thread = CreateRealTimeThread(function()
			while true do
				Sleep(1000)
				S.thread_ticks = (S.thread_ticks or 0) + 1
			end
		end)
	end

	-- 7. an entry in a table the engine rebuilds wholesale
	local bl = rawget(_G, "ModEnvBlacklist")
	if type(bl) == "table" then bl.JA3SE_S_Blacklist = true end
end

local function ReadSurvivalMarkers()
	local S = SE.survive or {}
	local out = {}
	local function chk(what, ok)
		out[#out + 1] = what .. "=" .. (ok and "LIVED" or "died")
	end
	chk("plain_global",   rawget(_G, "JA3SE_S_PlainGlobal") ~= nil)
	chk("persistable",    rawget(_G, "JA3SE_S_Persistable") ~= nil)
	chk("const_field",    (rawget(_G, "const") or {}).JA3SE_S_Const ~= nil)
	chk("patched_engine_fn", S.patched_fn ~= nil
		and rawget(_G, "GetPreciseTicks") == S.patched_fn)
	local t = rawget(_G, "JA3SE_GetMsgTable")
	t = t and t()
	local msg_alive = false
	if t and t.JA3SE_S_Message and S.msg_fn then
		for _, f in ipairs(t.JA3SE_S_Message) do
			if f == S.msg_fn then msg_alive = true end
		end
	end
	chk("onmsg_handler",  msg_alive)
	chk("realtime_thread", S.thread ~= nil and IsValidThread(S.thread))
	chk("engine_table_entry",
		(rawget(_G, "ModEnvBlacklist") or {}).JA3SE_S_Blacklist ~= nil)
	out[#out + 1] = "thread_ticks=" .. tostring(S.thread_ticks)
	out[#out + 1] = "planted_passes=" .. tostring(S.planted)
	return table.concat(out, " ")
end

rawset(_G, "JA3SE_PlantSurvival", PlantSurvivalMarkers)
rawset(_G, "JA3SE_ReadSurvival", ReadSurvivalMarkers)

----------------------------------------------------------------------
-- SURGICAL UNLOAD
--
-- Switching a mod off normally makes the engine reload ALL of its Lua
-- (Mod.lua:1837+, "if next(mod.code) then reload_lua = true end"). That
-- rebuilds every window in the game, which a user sees as the game
-- restarting - reported from the wild by a mod author using JAPE's live mod
-- manager while the Mod Manager screen was open.
--
-- The reload exists because the engine keeps no record of what a mod's code
-- did, so rebuilding everything is the only way back to a clean state. We DO
-- have that record: per mod, every global it replaced or created with the
-- exact previous value, and every OnMsg handler it registered. Measured
-- footprints are small - the largest mod installed here replaces 194 globals
-- and adds 104 handlers.
--
-- So undo precisely and skip the reload.
--
-- Deliberately an API rather than an interception of the engine's own path. A
-- caller uses this INSTEAD of the normal toggle. Silently hijacking the
-- engine would risk a half-completed undo, which is worse than a reload.
----------------------------------------------------------------------

local function RemoveMsgHandlers(entries)
	local t = rawget(_G, "JA3SE_GetMsgTable")
	t = t and t()
	if not t or not entries then return 0 end
	local removed = 0
	local miss_key, miss_msg, miss_fn, miss_notfound = 0, 0, 0, 0
	for _, e in ipairs(entries) do
		local msg = string.match(e.key, "^(.+)#%%d+$")
		if not msg then
			miss_key = miss_key + 1
		elseif not e.fn then
			miss_fn = miss_fn + 1
		elseif not t[msg] then
			-- the message has no list at all any more. Expected for one-shot
			-- messages: the engine calls MsgClear after firing them
			-- (autorun.lua does exactly that for Autorun / Start /
			-- EngineStarted), which sets message_to_staticfuncs[msg] = nil and
			-- takes every handler with it. Nothing left to undo.
			miss_msg = miss_msg + 1
			SE.msg_gone = SE.msg_gone or {}
			SE.msg_gone[msg] = (SE.msg_gone[msg] or 0) + 1
		else
			local list, hit = t[msg], false
			for i = #list, 1, -1 do
				if list[i] == e.fn then
					table.remove(list, i)
					removed = removed + 1
					hit = true
					break
				end
			end
			if not hit then
				-- list exists but our recorded function is not in it: the
				-- handler object we recorded is stale
				miss_notfound = miss_notfound + 1
			end
		end
	end
	SE.msg_miss = { key = miss_key, msg = miss_msg, fn = miss_fn,
		notfound = miss_notfound, total = #entries }
	return removed
end

rawset(_G, "JA3SE_UnloadModSurgically", function(mod_id)
	mod_id = tostring(mod_id)
	local prev = SE.mod_prev[mod_id]
	if not prev then
		return false, "no record for " .. mod_id
	end

	local restored, cleared, unreachable = 0, 0, 0
	for name, before_value in pairs(prev) do
		-- Do NOT blindly restore: a later mod may have replaced the same name,
		-- and putting our old value back would silently undo theirs. Rebuild
		-- the chain without us and take whatever is left on top.
		if before_value == JA3SE_ABSENT then before_value = nil end
		local winner, others = before_value, 0
		local vers = SE.versions[name]
		if vers then
			for i = 1, #vers do
				if vers[i].mod ~= mod_id then
					others = others + 1
					winner = vers[i].fn
				end
			end
		end
		if others == 0 then winner = before_value end

		if winner == nil then
			if PathSet(name, nil) then cleared = cleared + 1
			else unreachable = unreachable + 1 end
		else
			if PathSet(name, winner) then restored = restored + 1
			else unreachable = unreachable + 1 end
		end
	end

	local msgs = RemoveMsgHandlers(SE.mod_msgs[mod_id])
	SE.last_unload = { mod = mod_id, restored = restored,
		cleared = cleared, msgs = msgs, unreachable = unreachable }
	say("surgical unload " .. mod_id .. ": restored " .. restored
		.. ", cleared " .. cleared .. ", handlers removed " .. msgs
		.. ", unreachable " .. unreachable)
	return true
end)

local function ModTitle(id)
	local mods = rawget(_G, "Mods")
	local m = mods and mods[id]
	local t = m and rawget(m, "title")
	if t and t ~= "" then return tostring(t) end
	return tostring(id)
end

local function ConflictReportText()
	local conflicts = JA3SE_Conflicts()
	if #conflicts == 0 then return nil end

	local breaks = {}
	local layered = {}
	for i = 1, #conflicts do
		local c = conflicts[i]
		local who = {}
		for j = 1, #c.mods do
			who[#who + 1] = ModTitle(c.mods[j])
		end
		local line = c.name .. NL .. "        " .. table.concat(who, "   vs   ")
		if c.severity == "BREAKS" then
			breaks[#breaks + 1] = line
		else
			layered[#layered + 1] = line
		end
	end

	local out = {}
	if #breaks > 0 then
		out[#out + 1] = tostring(#breaks) .. " conflict(s) where one mod overwrites another."
		out[#out + 1] = "The later mod wins and the earlier one loses that change."
		out[#out + 1] = ""
		for i = 1, #breaks do out[#out + 1] = breaks[i] end
	end
	if #layered > 0 then
		if #out > 0 then out[#out + 1] = "" end
		out[#out + 1] = tostring(#layered) .. " place(s) where mods layer safely. No action needed."
		for i = 1, #layered do out[#out + 1] = layered[i] end
	end

	local nat = JA3SE_Native()
	out[#out + 1] = ""
	out[#out + 1] = "JA3SE v" .. tostring(SE.version)
		.. (nat.present and (" + native v" .. tostring(nat.version)) or " (Lua only)")

	return table.concat(out, NL)
end

rawset(_G, "JA3SE_ConflictReportText", ConflictReportText)

local function ShowConflictReport()
	if SE.shown_report then return end
	if not JA3SE_OptionOn("JA3SE_ShowConflicts", true) then return end
	local ok, text = pcall(ConflictReportText)
	if not ok or not text then return end
	SE.shown_report = true
	CreateRealTimeThread(function()
		Sleep(1500)   -- let the menu settle before stealing focus
		pcall(function()
			WaitMessage(terminal.desktop,
				Untranslated("JA3 Script Extender"),
				Untranslated(text),
				Untranslated("OK"))
		end)
	end)
end

rawset(_G, "JA3SE_ShowConflictReport", ShowConflictReport)

----------------------------------------------------------------------
-- PRESET MERGING - the clone conflict
--
-- The case a veteran JA3 modder said he could not imagine a program solving:
-- two mods both CLONE the same preset. A clone carries every property with it,
-- so the second one loaded wipes the first one's change wholesale, even when
-- the two mods changed completely different things.
--
-- It turns out to be a three-way merge, and the engine has already done the
-- hard half. Measured: after both mods load, Presets holds THREE FlakVest
-- objects side by side -
--
--     mod = nil          the vanilla preset
--     mod = JA3SEcta     that mod's clone, carrying only its own change
--     mod = JA3SEctb     that mod's clone, carrying only its own change
--
-- so vanilla is still there to diff against, and each mod's version is tagged
-- with its owner. That is exactly what a merge needs.
--
-- Diff each clone against vanilla to get what that mod ACTUALLY changed, then
-- apply every change to one object. Two mods touching different properties -
-- which is the common case, because cloning is a convenience, not an intent -
-- merge cleanly with no conflict at all. A real conflict is only where two
-- mods changed the SAME property, which is far rarer than "both cloned it".
----------------------------------------------------------------------

-- Keys that describe the preset's identity or bookkeeping rather than its
-- content. Copying these from a clone would be meaningless or harmful.
local PRESET_SKIP = {
	id = true, mod = true, class = true, ModdedPresetClass = true,
	__index = true, __parents = true, Comment = true,
	name = true, ModItemDescription = true,
}

local function PresetChanges(clone, vanilla)
	local changed = {}
	for k, v in pairs(clone) do
		if type(k) == "string" and not PRESET_SKIP[k] then
			if vanilla[k] ~= v then
				changed[k] = v
			end
		end
	end
	return changed
end

-- Returns a plan, and does not touch anything. Deciding and doing are kept
-- apart so the plan can be shown to a user before it is applied.
local function PlanPresetMerges(preset_class)
	local pc = (rawget(_G, "Presets") or {})[preset_class]
	if not pc then return {} end

	-- collect every object by id, de-duplicated (groups appear under more than
	-- one key, so the same object is reachable twice)
	local byid, seen = {}, {}
	for _, grp in pairs(pc) do
		if type(grp) == "table" then
			for _, obj in ipairs(grp) do
				if type(obj) == "table" and not seen[obj] then
					seen[obj] = true
					local id = rawget(obj, "id")
					if id then
						byid[id] = byid[id] or {}
						byid[id][#byid[id] + 1] = obj
					end
				end
			end
		end
	end

	local plans = {}
	for id, objs in pairs(byid) do
		if #objs > 1 then
			local vanilla
			local clones = {}
			for _, o in ipairs(objs) do
				local m = rawget(o, "mod")
				if m == nil then
					vanilla = vanilla or o
				else
					clones[#clones + 1] = { mod = rawget(m, "id") or "?", obj = o }
				end
			end
			if vanilla and #clones > 0 then
				local changes, owners, conflict = {}, {}, {}
				for _, c in ipairs(clones) do
					for k, v in pairs(PresetChanges(c.obj, vanilla)) do
						if owners[k] and changes[k] ~= v then
							conflict[k] = conflict[k] or { owners[k] }
							conflict[k][#conflict[k] + 1] = c.mod
						end
						changes[k] = v
						owners[k] = c.mod
					end
				end
				plans[#plans + 1] = {
					id = id, vanilla = vanilla, clones = clones,
					changes = changes, owners = owners, conflict = conflict,
				}
			end
		end
	end
	return plans
end

rawset(_G, "JA3SE_PlanPresetMerges", PlanPresetMerges)

-- Carry out a plan.
--
-- Which object the game actually consults depends on the preset class and the
-- lookup path - measured, the generated class kept vanilla values while three
-- preset objects sat in the group. Rather than guess, write the merged result
-- to every object sharing the id AND to the generated class, so they agree
-- whichever way the game reaches it.
--
-- Only ever writes properties that some mod already set. A property nobody
-- touched keeps its vanilla value untouched.
local function ApplyPresetMerges(preset_class)
	local plans = PlanPresetMerges(preset_class)
	local applied, skipped = 0, 0
	for _, pl in ipairs(plans) do
		-- a property two mods set differently has no safe automatic answer
		local safe = {}
		local any = false
		for k, v in pairs(pl.changes) do
			if pl.conflict[k] then
				skipped = skipped + 1
			else
				safe[k] = v
				any = true
			end
		end
		if any then
			local targets = { pl.vanilla }
			for _, c in ipairs(pl.clones) do targets[#targets + 1] = c.obj end
			local cls = (rawget(_G, "g_Classes") or {})[pl.id]
			if cls then targets[#targets + 1] = cls end
			for _, t in ipairs(targets) do
				for k, v in pairs(safe) do
					pcall(function() rawset(t, k, v) end)
				end
			end
			applied = applied + 1
		end
	end
	SE.merges_applied = applied
	SE.merges_skipped = skipped
	return applied
end

rawset(_G, "JA3SE_ApplyPresetMerges", ApplyPresetMerges)


	local MELEE_ACTIONS = { "MeleeAttack", "Brutalize" }

	local function PatchMeleeActions()
		local actions = rawget(_G, "CombatActions")
		if not actions then return 0 end
		local n = 0
		for i = 1, #MELEE_ACTIONS do
			local id = MELEE_ACTIONS[i]
			local action = actions[id]
			if action and type(action.GetActionResults) == "function"
				and not SE.patched_action[action] then
				local orig = action.GetActionResults
				action.GetActionResults = function(self, unit, args)
					local a = args
					pcall(function()
						local weapon = self:GetAttackWeapons(unit, args)
						local eff = JA3SE_GetWeaponEffects(weapon)
						if eff and #eff > 0 then
							a = table.copy(args)
							a.applied_status = eff
						end
					end)
					return orig(self, unit, a)
				end
				-- keyed on the action OBJECT, so a reload that rebuilds the
				-- presets gives us a fresh object and we patch it again
				SE.patched_action[action] = true
				n = n + 1
			end
		end
		SE.actions_patched = n
		return n
	end

	rawset(_G, "JA3SE_PatchMeleeActions", PatchMeleeActions)
	PatchMeleeActions()

	-- Registering a name in PersistableGlobals is what makes a plain
	-- assignment to it legal - _G's __newindex raises otherwise. This is also
	-- the ONLY sanctioned way a mod can hand a value back out of the sandbox,
	-- because rawset inside a mod writes to the sandbox table, not to _G. So
	-- this list is itself a capability: names mods are allowed to create.
	if rawget(_G, "PersistableGlobals") then
		for _, n in ipairs({"JA3SE_Version","JA3SE_Granted","JA3SE_Native",
			"JA3SE_SetWeaponEffects","JA3SE_GetWeaponEffects","JA3SE_PatchMeleeActions",
			"JA3SE_SR_Entered","JA3SE_SR_HaveExtender","JA3SE_SR_CanRead",
			"JA3SE_SR_CanWrite","JA3SE_SR_Loaded","JA3SE_SR_Blocked","JA3SE_SR_Error",
			"JA3SE_WE_Registered","JA3SE_WE_Lookup"}) do
			PersistableGlobals[n] = true
		end
	end
end

----------------------------------------------------------------------
-- Reload survival
----------------------------------------------------------------------

local Registration

-- forward declaration: see the note on InstallDofileHook below
local InstallDofileHook
local function Apply()
	SE.applies = SE.applies + 1
	PublishAPI()
	if SE.applies == 1 then pcall(JA3SE_PlantSurvival) end
	OpenSandbox()
	WrapLuaModEnv()
	GrantToAllMods()
	pcall(function() JA3SE_WrapModsUIDialogEnd() end)
	-- Must be installed BEFORE the reload that runs ModsLoadCode, or no mod
	-- footprints are recorded and conflict detection, the quiet disable and
	-- the quiet enable all silently do nothing. Apply() runs from post_load,
	-- which is before DlcReloadLua, so this is in time. Safe to do here: every
	-- path inside the wrapper is pcall'd, and the initial boot is already past.
	pcall(InstallDofileHook)
	-- Ground truth, every boot. Conflict detection, the quiet disable and the
	-- quiet enable all depend on footprints being recorded, and when that
	-- silently stopped nothing else showed it: the game booted, mods worked,
	-- and three features were dead. mods_recorded=0 with mods loaded is the
	-- one-line symptom.
	if %(HEARTBEAT)s then JA3SE_StartHeartbeat() end
	CreateRealTimeThread(function()
		-- No xpcall around this loop: it yields, and the publish itself is
		-- guarded internally.
		while true do
			Sleep(5000)
			if #(rawget(_G, "g_Units") or {}) > 0 then
				JA3SE_Guard("sector_autopublish", JA3SE_SectorUnits)
				Sleep(10000)
			end
		end
	end)
	CreateRealTimeThread(function()
		-- xpcall + traceback, not pcall. An error with no location cost this
		-- project two debugging sessions already; get the location first.
		local ok, err = xpcall(function()
			WaitMsg("EngineStarted", 20000)
			Sleep(3000)
			JA3SE_ProbePresets()
			JA3SE_WriteCatalog()
			if %(CTH_TEST)s then
				-- QuickStartCampaign drops straight into a sector with mercs,
				-- which is the only state where a sweep means anything.
				local camps = rawget(_G, "CampaignPresets") or {}
				local cid = rawget(_G, "DefaultCampaign")
				if not cid then for k in pairs(camps) do cid = k break end end
				-- The sector stops on a loading screen with a START button and
				-- waits - the tester sent a photo of it parked on Flag Hill. The
				-- engine already has an auto-click for this
				-- (Combat.lua:2625 sets it to 1); on retail it defaults to
				-- false because Platform.developer is off. It clears itself
				-- after each use, so re-arm it every pass.
				-- Wait for the game to be genuinely ready. Measured: calling
				-- QuickStartCampaign ~10s in blocks forever. WaitMsg on
				-- EngineStarted is not enough on its own - it returns on
				-- timeout as well as on the message, and cannot tell you which.
				JA3SE_Phase("cth:settling")
				-- Let the engine finish coming up. Measured: calling
				-- QuickStartCampaign ~10s in blocks forever, and WaitMsg on
				-- EngineStarted is not enough on its own - it returns on timeout as
				-- well as on the message and cannot tell you which.
				Sleep(30000)
				-- JA3SE_EnterSector, NOT a hand-rolled quick start. This block used
				-- to arm g_AutoClickLoadingScreenStart once and then call
				-- QuickStartCampaign, which BLOCKS on a loading screen that wants a
				-- click; the flag clears itself after each use and the re-arm loop
				-- sits after a call that never returns. It parked on
				-- `cth:quickstart-in` for the full 900s timeout, twice in one run.
				--
				-- EnterSector already solves exactly this, by arming from a parallel
				-- thread for the whole call, and it is deliberately not wrapped in
				-- JA3SE_Guard because xpcall cannot yield. Third time on this project
				-- a fix has been applied at one call site and not its twin.
				JA3SE_EnterSector(180)
				JA3SE_Phase("cth:sweeping-units-" .. tostring(#(rawget(_G, "g_Units") or {})))
				JA3SE_CTHSweep()
				JA3SE_Guard("scale_report", JA3SE_ScaleReport)
				JA3SE_Phase("cth:swept")
			end
		end, function(e) return tostring(e) .. NL .. debug.traceback("", 2) end)
		if not ok then
			pcall(function()
				AsyncStringToFile("AppData/ja3se-catalog-error.txt",
					tostring(err) .. NL)
			end)
		end
		JA3SE_Guard("selfcheck_cycle", function()
			WaitMsg("EngineStarted", 60000)
			JA3SE_Phase("engine")
			JA3SE_Guard("force_enable_pass", JA3SE_ForceEnableFromFile)
			JA3SE_Guard("mod_item_audit_pass", JA3SE_ModItemAudit)
			JA3SE_Guard("code_item_check_pass", JA3SE_CodeItemCheck)
			Sleep(3000)
			if %(SPAWN_TEST)s then JA3SE_EnterSector(120) end
			Sleep(3000)          -- let the mod load settle so receipts are all in
			if %(FAULT_INJECT)s then
				-- Prove the detector detects. A watchdog that has only ever
				-- reported "healthy" is indistinguishable from one that is
				-- broken, and this project has twice shipped a check that
				-- passed while measuring nothing.
				JA3SE_DIAG.receipts["dofile_hook"] = nil; JA3SE_Guard("deliberate_fault", function() local t = nil; return t.this_index_will_throw
				end)
			end
			JA3SE_SelfCheck()
			if %(SPAWN_TEST)s then
				-- Reproduce Jager's scenario rather than describe it: drop
				-- several units in quickly through the same CheatAddMerc path
				-- the live link uses, then read them back. The questions are
				-- whether session ids collide and whether a spawned unit's
				-- inventory is reachable at all.
				JA3SE_SectorUnits()
				JA3SE_Guard("spawn_before", function()
					AsyncCopyFile("AppData/ja3se-sector.txt",
						"AppData/ja3se-sector-before.txt", "raw")
				end)
				for _, who in ipairs({ "Barry", "Grizzly", "Fox", "Buns" }) do
					JA3SE_Guard("spawn:" .. who, function()
						CheatAddMerc(who)
					end)
					Sleep(50)   -- fast, the way Jager triggered it
				end
				Sleep(4000)
				JA3SE_SectorUnits()
			end
			JA3SE_Phase("playing")
		end)
		local n = 0
		for _ in pairs(SE.mod_prev or {}) do n = n + 1 end
		local ids = {}
		for _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do
			ids[#ids + 1] = tostring(rawget(m, "id"))
		end
		pcall(function()
			-- JA3SE_Publish, not a bare write. This was the last published file
			-- without an integrity header, so a copy truncated by the process
			-- dying mid-write read as a valid short status - Skywalker saw one
			-- come out with a single line and had no way to tell it was
			-- damaged rather than terse.
			JA3SE_Publish("AppData/ja3se-status.txt",
				"ja3se_version=" .. tostring(SE.version) .. NL ..
				"mods_loaded=" .. #ids .. NL ..
				"mods_recorded=" .. n .. NL ..
				"footprint_recorder=" ..
					((n > 0 or #ids == 0) and "ok" or "NOT RECORDING") .. NL ..
				"mod_ids=" .. table.concat(ids, ",") .. NL)
		end)
	end)
	pcall(function() JA3SE_WrapLoadCode() end)
	-- NOT AddSettingsPage here. Apply() runs mid-reload while classes are
	-- being rebuilt, and editing OptionsObject.properties at that moment left
	-- the game alive but never reaching the menu (~1000 MB, fluctuating).
	-- It is deferred to a thread after EngineStarted instead.
	if not SE.settings_thread then
		SE.settings_thread = CreateRealTimeThread(function()
			WaitMsg("EngineStarted")
			Sleep(500)
			-- xpcall with a traceback: "attempt to call a nil value" with no
			-- location is not a diagnosis
			pcall(function() JA3SE_AddSettingsPage() end)
			-- The GLOBAL form, deliberately. The bare local is in scope here by
			-- every reading of the source and was still nil at run time -
			-- this body runs in a thread that resumes after EngineStarted
			-- and across a Lua reload, and a resumed thread is not a safe
			-- place to rely on a chunk upvalue. JA3SE_OptionOn is rawset
			-- into _G and cannot go out of scope.
			if JA3SE_OptionOn("JA3SE_MergeMods", false) then
				JA3SE_Guard("apply:ApplyPresetMerges", function()
		JA3SE_ApplyPresetMerges("InventoryItemCompositeDef")
	end)
			end
		end)
	end
	say("applied #" .. SE.applies .. ", granted " .. #SE.granted
		.. " names to " .. tostring(SE.envs_granted) .. " mod envs")
end

-- Wrapping dofile is what lets us act mid-reload: a callback after each engine
-- file, so the sandbox is opened the instant Mod.lua has defined it - before
-- ModsLoadCode at autorun.lua:370 reads it.
--
-- This wrapper sits in front of ALL of the game's file loading, so it must be
-- incapable of throwing. Everything inside is pcall'd, and it is installed
-- only after EngineStarted, so the initial boot never runs through it.
local old_dofile, new_dofile
function InstallDofileHook()
	if rawget(_G, "JA3SE_Receipt") then JA3SE_Receipt("dofile_hook") end
	if new_dofile and dofile == new_dofile then return end
	if not new_dofile then
		new_dofile = function(file, ...)
			local res = old_dofile(file, ...)
			pcall(function()
				if type(file) ~= "string" then return end
				SE.dofile_calls = (SE.dofile_calls or 0) + 1
				if string.ends_with(file, "Mod.lua") then
					SE.modlua_hits = (SE.modlua_hits or 0) + 1
				end
				if dofile ~= new_dofile and string.ends_with(file, "lib.lua") then
					-- lib.lua redefines dofile; re-hook or the wrapper is lost
					old_dofile, dofile = dofile, new_dofile
				elseif string.ends_with(file, "Mod.lua") then
					-- Mod.lua has just redefined BOTH ModEnvBlacklist and
					-- LuaModEnv, so both patches have to go back on, here,
					-- before ModsLoadCode at autorun.lua:370 uses them.
					OpenSandbox()
					WrapLuaModEnv()
					GrantToAllMods()
					-- must be in place before ModsLoadCode at autorun.lua:370
					JA3SE_T("modlua_dofile")
					if rawget(_G, "JA3SE_WrapLoadCode") then JA3SE_WrapLoadCode() end
				end
			end)
			return res
		end
	end
	old_dofile, dofile = dofile, new_dofile
end

Registration = function()
	-- the only user-visible thing the extender does
	OnMsg.PreGameMenuOpen = function()
		pcall(function() JA3SE_ShowConflictReport() end)
	end

	OnMsg.ReloadLua = function()
		SE.reloads_seen = SE.reloads_seen + 1
		-- identity of the UI right before the reload, to compare after
		local tm = rawget(_G, "terminal")
		SE.ui_desktop = tm and tm.desktop
		SE.ui_dialogs_tbl = rawget(_G, "Dialogs")
		local n = 0
		if type(SE.ui_dialogs_tbl) == "table" then
			for _ in pairs(SE.ui_dialogs_tbl) do n = n + 1 end
		end
		SE.ui_dialogs_pre = n
		CreateRealTimeThread(function()
			Sleep(50)
			while Loading do
				Sleep(20)
			end
			Registration()   -- re-install for the NEXT reload
			Apply()          -- re-apply everything the reload wiped
			say("survived reload #" .. SE.reloads_seen)
		end)
	end
end

----------------------------------------------------------------------
-- Proof that the grant is real, written to a file we can read outside
----------------------------------------------------------------------

local function WriteReport(tag)
	local out = {}
	local function put(k, v) out[#out + 1] = tostring(k) .. "=" .. tostring(v) end

	put("tag", tag)
	put("version", SE.version)
	put("applies", SE.applies)
	put("reloads_seen", SE.reloads_seen)
	put("granted", table.concat(SE.granted, ","))
	put("console_opened", tostring(rawget(_G, "JA3SE_ConsoleOpened")))
	put("mods_loaded", #(rawget(_G, "ModsLoaded") or {}))

	-- Did the real mod on disk load, and did it get its capabilities?
	for _, k in ipairs({"JA3SE_SR_Entered","JA3SE_SR_HaveExtender","JA3SE_SR_CanRead",
		"JA3SE_SR_CanWrite","JA3SE_SR_Loaded","JA3SE_SR_Blocked","JA3SE_SR_Error"}) do
		put(k, tostring(rawget(_G, k)))
	end

	-- Why did the mod load but never run its code? Ask the game directly.
	local mods = rawget(_G, "Mods")
	local m = mods and mods["%(TEST_MOD)s"]
	put("mod_def_found", m ~= nil)
	if m then
		put("mod_path", tostring(rawget(m, "path")))
		put("mod_content_path", tostring(rawget(m, "content_path")))
		put("mod_packed", tostring(rawget(m, "packed")))
		put("mod_code_entries", #(rawget(m, "code") or {}))
		put("mod_code_1", tostring((rawget(m, "code") or {})[1]))
		put("mod_items_loaded", tostring(rawget(m, "items") ~= nil))
		local cp = rawget(m, "content_path") or ""
		put("exists_content_root", tostring(io.exists(cp)))
		put("exists_code_file", tostring(io.exists(cp .. "Code/ServiceRecord.lua")))
		put("listing", table.concat(io.listfiles(cp, "*", "recursive") or {}, " | "))
	end
	put("LuaLoadedForMods_us", tostring((rawget(_G, "LuaLoadedForMods") or {})["%(TEST_MOD)s"]))

	-- Interrogate the mod's OWN environment object, not a fresh one.
	if m then
		local menv = rawget(m, "env")
		put("mod_env_exists", menv ~= nil)
		if menv then
			local okA, vA = pcall(function() return menv.AsyncStringToFile end)
			put("mod_env_AsyncStringToFile", okA and tostring(vA) or ("raised: " .. tostring(vA)))
			local okB, vB = pcall(function() return menv.JA3SE_Version end)
			put("mod_env_JA3SE_Version", okB and tostring(vB) or ("raised: " .. tostring(vB)))
			-- what does rawget-through-the-proxy give, which is what the mod used
			local okC, vC = pcall(function() return menv.rawget(menv, "AsyncStringToFile") end)
			put("mod_env_rawget_AsyncStringToFile", okC and tostring(vC) or ("raised: " .. tostring(vC)))
		end
	end
	local blnow = rawget(_G, "ModEnvBlacklist") or {}
	put("blacklist_AsyncStringToFile_now", tostring(blnow.AsyncStringToFile))
	local ok_a, n_a = pcall(JA3SE_ApplyPresetMerges, "InventoryItemCompositeDef")
	put("preset_merges_applied", ok_a and n_a or ("failed " .. tostring(n_a)))
	put("preset_props_skipped_as_conflict", tostring(SE.merges_skipped))

	-- CLONE CONFLICT PROBE (Jaeger's FlakVest case)
	-- vanilla: AdditionalReduction=20 ScrapParts=4 no OnItemGained
	-- mod A  : adds OnItemGained
	-- mod B  : AdditionalReduction=40 ScrapParts=9
	-- both applied correctly would be: OnItemGained AND 40 AND 9
	local fv = rawget(_G, "FlakVest")
	put("flakvest_class_found", fv ~= nil)
	if fv then
		put("flakvest_OnItemGained", tostring(rawget(fv, "OnItemGained") ~= nil))
		put("flakvest_AdditionalReduction", tostring(rawget(fv, "AdditionalReduction")))
		put("flakvest_ScrapParts", tostring(rawget(fv, "ScrapParts")))
	end
	local pr = rawget(_G, "Presets")
	local pc = pr and pr.InventoryItemCompositeDef
	if pc then
		-- groups hold an array AND an id key; check both
		local byid = pc.FlakVest
		put("flakvest_by_id_in_class", tostring(byid ~= nil))
		local n = 0
		for gname, grp in pairs(pc) do
			if type(grp) == "table" then
				for _, obj in ipairs(grp) do
					if type(obj) == "table" and rawget(obj, "id") == "FlakVest" then
						n = n + 1
						put("fv" .. n .. "_group", tostring(gname))
						put("fv" .. n .. "_class", tostring(obj.class))
						put("fv" .. n .. "_mod", tostring(rawget(obj, "mod") and rawget(rawget(obj, "mod"), "id")))
						put("fv" .. n .. "_OnItemGained", tostring(rawget(obj, "OnItemGained") ~= nil))
						put("fv" .. n .. "_AddRed", tostring(rawget(obj, "AdditionalReduction")))
						put("fv" .. n .. "_Scrap", tostring(rawget(obj, "ScrapParts")))
					end
				end
				if grp.FlakVest then
					put("group_" .. tostring(gname) .. "_has_id_key", "yes")
				end
			end
		end
		put("flakvest_preset_entries", n)
	end

	-- What would a three-way merge of the cloned presets produce?
	local ok_p, plans = pcall(JA3SE_PlanPresetMerges, "InventoryItemCompositeDef")
	put("preset_merge_planned", ok_p and #plans or ("failed " .. tostring(plans)))
	if ok_p then
		for i = 1, #plans do
			local pl = plans[i]
			local who, props, clash = {}, {}, {}
			for _, c in ipairs(pl.clones) do who[#who + 1] = c.mod end
			for k, _ in pairs(pl.changes) do
				props[#props + 1] = k .. "<-" .. tostring(pl.owners[k])
			end
			for k, mods in pairs(pl.conflict) do
				clash[#clash + 1] = k .. "(" .. table.concat(mods, ",") .. ")"
			end
			table.sort(props)
			put("merge_" .. pl.id .. "_clones", table.concat(who, ","))
			put("merge_" .. pl.id .. "_result", table.concat(props, " "))
			put("merge_" .. pl.id .. "_realconflict",
				#clash > 0 and table.concat(clash, " ") or "none")
		end
	end

	-- Full footprint per mod: replaced globals / created globals / OnMsg
	-- handlers. A surgical unload is only safe if all three are captured.
	put("msg_table_reachable", tostring(JA3SE_GetMsgTable() ~= nil))
	put("msg_handlers_total", tostring(SE.msg_total))
	local full = {}
	for id, msgs in pairs(SE.mod_msgs) do
		local replaced = #(SE.mod_touch[id] or {})
		local added = (SE.mod_added[id] or {}).n or 0
		if replaced > 0 or added > 0 or #msgs > 0 then
			full[#full + 1] = id .. " ~" .. replaced .. " +" .. added .. " msg" .. #msgs
		end
	end
	table.sort(full)
	put("footprint_replaced_added_msgs", table.concat(full, " | "))
	for id, msgs in pairs(SE.mod_msgs) do
		if #msgs > 0 and #msgs <= 12 then
			local mk = {} for _, e in ipairs(msgs) do mk[#mk+1] = e.key end
			put("msgs_" .. id, table.concat(mk, ","))
		end
	end

	-- Surgical-unload feasibility: how much does a mod's code leave behind?
	local foot = {}
	for id, a in pairs(SE.mod_added) do
		local replaced = #(SE.mod_touch[id] or {})
		if a.n > 0 or replaced > 0 then
			foot[#foot + 1] = id .. " +" .. a.n .. "/~" .. replaced
		end
	end
	table.sort(foot)
	put("mod_footprint_added_replaced", table.concat(foot, " "))
	local oml = {}
	for id, n in pairs(SE.mod_onmsg) do oml[#oml + 1] = id .. "=" .. n end
	table.sort(oml)
	put("onmsg_total_after_each_mod", table.concat(oml, " "))

	-- END-TO-END: disable a mod quietly, with the Mod Manager open
	local acct = rawget(_G, "AccountStorage")
	pcall(function() OpenDialog("ModManagerDialog", terminal.desktop) end)
	Sleep(700)
	local d = GetDialog("ModManagerDialog")
	put("e2e_dialog_open", tostring(d ~= nil))

	local victim = nil
	if acct and acct.LoadMods then
		for i = #acct.LoadMods, 1, -1 do
			local id = acct.LoadMods[i]
			-- pick one that actually ships code, so the surgical part matters
			if SE.mod_prev[id] then victim = id break end
		end
	end
	put("e2e_victim", tostring(victim))

	if d and victim then
		rawset(_G, "g_InitialMods", table.copy(acct.LoadMods))
		local pre_reloads = SE.reloads_seen
		local was_loaded = false
		for _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do
			if rawget(m, "id") == victim then was_loaded = true end
		end
		put("e2e_was_loaded_before", tostring(was_loaded))

		local okq = pcall(function() return JA3SE_DisableModQuietly(victim) end)
		put("e2e_disable_ok", tostring(okq))
		put("e2e_steps", tostring((SE.last_quiet_disable or {}).steps))
		Sleep(600)

		-- did it actually go?
		local still = false
		for _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do
			if rawget(m, "id") == victim then still = true end
		end
		put("e2e_still_in_ModsLoaded", tostring(still))
		put("e2e_still_in_LoadMods",
			tostring(table.find(acct.LoadMods or {}, victim) ~= nil))
		put("e2e_reloads", tostring(pre_reloads) .. "/" .. tostring(SE.reloads_seen))

		-- now leave the screen: quiet or heavy?
		d:DeleteThread("warning")
		pcall(function() ModsUIDialogEnd(d, false) end)
		Sleep(600)
		local heavy = d:GetThread("warning") ~= nil
		put("e2e_exit_branch", heavy and "HEAVY(restart)" or "quiet(no restart)")
		d:DeleteThread("warning")
		pcall(function() CloseDialog("ModManagerDialog") end)
		Sleep(400)

		-- and is the game still healthy?
		put("e2e_engine_ok", tostring(pcall(function()
			local _ = GetPreciseTicks()
			local t = rawget(_G, "CombatActions")
			if t and t.MeleeAttack then local _ = t.MeleeAttack.id end
			for _ in pairs(rawget(_G, "Mods") or {}) do end
		end)))
	end

	-- THE REAL REPRO
	-- Open the Mod Manager, then change the mod list behind its back the way
	-- an external tool does, and watch what happens to the dialog. This is the
	-- scenario the user reported, reproduced without touching the mouse.
	local function DlgCount()
		local d = rawget(_G, "Dialogs")
		local n = 0
		if type(d) == "table" then for _ in pairs(d) do n = n + 1 end end
		return n
	end
	local opened, open_err = pcall(function()
		return OpenDialog("ModManagerDialog", terminal.desktop)
	end)
	put("repro_dialog_opened", tostring(opened) .. (opened and "" or (" " .. tostring(open_err))))
	Sleep(1500)
	local dlg = rawget(_G, "GetDialog") and GetDialog("ModManagerDialog")
	put("repro_dialog_present", tostring(dlg ~= nil))
	put("repro_dialogs_open", DlgCount())
	put("repro_g_InitialMods_before", tostring(rawget(_G, "g_InitialMods")))

	-- change the mod list underneath it, in memory only
	local acct = rawget(_G, "AccountStorage")
	local lm = acct and acct.LoadMods
	put("repro_loadmods_count_before", tostring(lm and #lm))
	if rawget(_G, "TurnModOff") and lm and #lm > 0 then
		local victim = lm[#lm]
		put("repro_turned_off", tostring(victim))
		pcall(function() TurnModOff(victim, false) end)
		Sleep(500)
		put("repro_loadmods_count_after", tostring(acct.LoadMods and #acct.LoadMods))
		-- this is what JAPE does next
		pcall(function() ModsReloadItems(nil, true) end)
		Sleep(2500)
	end
	local dlg2 = rawget(_G, "GetDialog") and GetDialog("ModManagerDialog")
	put("repro_dialog_still_open", tostring(dlg2 ~= nil))
	put("repro_same_dialog_object", tostring(dlg ~= nil and dlg2 == dlg))
	put("repro_dialogs_after", DlgCount())

	-- UI SURVIVAL ACROSS A RELOAD
	--
	-- Folk wisdom says the reload rebuilds the UI and dumps you to the main
	-- menu. The source says otherwise: terminal.desktop is only created by
	-- OnMsg.Start (XDesktop.lua:628), which fires only on FIRST load, and the
	-- Dialogs registry is FirstLoad-guarded (XDialog.lua:1). ReloadLua itself
	-- touches no UI at all.
	--
	-- If that holds, the "restart" people see is NOT the reload. The Mod
	-- Manager has its own exit path (ModsUI.lua:86 ModsUIDialogEnd) that fires
	-- when AccountStorage.LoadMods differs from g_InitialMods - the snapshot
	-- taken when the screen opened. Changing mods behind its back from an
	-- external tool is exactly that condition: warning, LoadingScreenOpen,
	-- ModsReloadItems, dialog closed.
	local dsk = rawget(_G, "terminal")
	dsk = dsk and dsk.desktop
	put("ui_desktop_exists", tostring(dsk ~= nil))
	put("ui_desktop_same_object", tostring(SE.ui_desktop ~= nil and SE.ui_desktop == dsk))
	local dl = rawget(_G, "Dialogs")
	local ndl = 0
	if type(dl) == "table" then for _ in pairs(dl) do ndl = ndl + 1 end end
	put("ui_dialogs_now", ndl)
	put("ui_dialogs_before_reload", tostring(SE.ui_dialogs_pre))
	put("ui_dialogs_table_same", tostring(SE.ui_dialogs_tbl ~= nil and SE.ui_dialogs_tbl == dl))
	put("ui_g_InitialMods", tostring(rawget(_G, "g_InitialMods")))

	put("reload_survival", tostring(pcall(JA3SE_ReadSurvival) and JA3SE_ReadSurvival() or "failed"))

	-- PATH RESOLVER checks: the thing that was blocking both features
	put("resolve_plain", tostring(select(1, JA3SE_ResolvePath("ModsLoaded")) == _G))
	local t_ca, k_ca = JA3SE_ResolvePath("CombatActions.MeleeAttack.GetActionResults")
	put("resolve_nested_table_found", tostring(t_ca ~= nil))
	put("resolve_nested_key", tostring(k_ca))
	put("resolve_nested_is_function",
		tostring(t_ca ~= nil and type(rawget(t_ca, k_ca)) == "function"))
	put("resolve_missing_is_nil",
		tostring(JA3SE_ResolvePath("NoSuchTable.NoSuchKey.Nope") == nil))
	-- round trip through the resolver without changing anything
	local before_rt = JA3SE_PathGet("CombatActions.MeleeAttack.GetActionResults")
	local set_ok = JA3SE_PathSet("CombatActions.MeleeAttack.GetActionResults", before_rt)
	put("resolve_roundtrip_ok", tostring(set_ok and
		JA3SE_PathGet("CombatActions.MeleeAttack.GetActionResults") == before_rt))

	-- policies can now install on nested names
	local nested_conf = 0
	for name, vers in pairs(SE.versions) do
		if #vers > 1 and name:find(".", 1, true) then nested_conf = nested_conf + 1 end
	end
	put("nested_conflicts_seen", nested_conf)

	-- SURGICAL UNLOAD TEST
	-- The stress case. JA3_CommonLib is the biggest footprint installed
	-- (~194 replaced, 104 handlers) AND a library other mods build on, so if
	-- a surgical unload is going to come apart anywhere it is here.
	-- Checks all three categories, that nothing was unreachable, that no Lua
	-- reload happened, and - via the harness - that the game survives it.
	local target = "JA3_CommonLib"
	local pre_reloads = SE.reloads_seen
	local before_touch = #(SE.mod_touch[target] or {})
	local sample_name, sample_before
	for n, v in pairs(SE.mod_prev[target] or {}) do
		if v ~= JA3SE_ABSENT then sample_name, sample_before = n, v break end
	end
	local created_name
	for n, v in pairs(SE.mod_prev[target] or {}) do
		if v == JA3SE_ABSENT then created_name = n break end
	end
	put("unload_target", target)
	put("unload_sample_replaced", tostring(sample_name))
	put("unload_sample_created", tostring(created_name))
	put("unload_created_present_before", tostring(created_name and rawget(_G, created_name) ~= nil))
	put("unload_replaced_is_mods_before",
		tostring(sample_name and rawget(_G, sample_name) ~= sample_before))

	local ok_u, err_u = JA3SE_UnloadModSurgically(target)
	put("unload_ok", tostring(ok_u) .. (ok_u and "" or (" " .. tostring(err_u))))
	local lu = SE.last_unload or {}
	put("unload_restored", tostring(lu.restored))
	put("unload_cleared", tostring(lu.cleared))
	put("unload_handlers_removed", tostring(lu.msgs))
	put("unload_unreachable", tostring(lu.unreachable))
	local mm = SE.msg_miss or {}
	put("msg_miss_total_recorded", tostring(mm.total))
	local gone = {}
	for m, c in pairs(SE.msg_gone or {}) do gone[#gone + 1] = m .. "x" .. c end
	table.sort(gone)
	put("msg_already_cleared_by_engine", table.concat(gone, " "))
	put("msg_miss_badkey_nomsg_nofn_notfound",
		tostring(mm.key) .. "/" .. tostring(mm.msg) .. "/" ..
		tostring(mm.fn) .. "/" .. tostring(mm.notfound))
	put("unload_created_present_after",
		tostring(created_name and rawget(_G, created_name) ~= nil))
	put("unload_replaced_back_to_original",
		tostring(sample_name and rawget(_G, sample_name) == sample_before))
	put("unload_reloads_before_after", tostring(pre_reloads) .. "/" .. tostring(SE.reloads_seen))

	-- Does the game survive it? Touch a few engine paths that CommonLib
	-- wrapped, and write a heartbeat the harness can see from outside.
	local alive_ok, alive_err = pcall(function()
		local n = 0
		for _ in pairs(rawget(_G, "Mods") or {}) do n = n + 1 end
		local _ = GetPreciseTicks()
		local t = rawget(_G, "CombatActions")
		if t and t.MeleeAttack then local _ = t.MeleeAttack.id end
		return n
	end)
	put("post_unload_engine_ok", tostring(alive_ok) .. (alive_ok and "" or (" " .. tostring(alive_err))))

	pcall(function()
		AsyncStringToFile("AppData/ja3se-heartbeat.txt",
			"alive after surgical unload of " .. target .. NL)
	end)


	-- The native half: did the two sides find each other?
	local nat = JA3SE_Native()
	put("native_present", tostring(nat.present))
	put("native_version", tostring(nat.version))
	put("native_skip_splash", tostring(nat.skip_splash))

	-- Mod conflict detection: what did each mod actually replace?
	local touched_lines = {}
	for modid, names in pairs(SE.mod_touch) do
		touched_lines[#touched_lines + 1] = modid .. "[" .. #names .. "]"
			.. (#names > 0 and (":" .. table.concat(names, ",")) or "")
	end
	table.sort(touched_lines)
	put("mods_analysed", #touched_lines)
	put("mod_overrides", table.concat(touched_lines, " | "))

	local conflicts = rawget(_G, "JA3SE_Conflicts") and JA3SE_Conflicts() or {}
	put("conflicts_found", #conflicts)
	for i = 1, #conflicts do
		local c = conflicts[i]
		put(c.severity .. "_" .. c.name, table.concat(c.desc, " vs "))
	end
	put("debug_getupvalue_available",
		tostring(rawget(_G, "debug") ~= nil and rawget(_G, "debug").getupvalue ~= nil))

	-- Sanity check on the classifier itself. If NOTHING anywhere is detected
	-- as a wrapper, "everything replaces" is more likely a broken detector
	-- than a true finding. JA3_CommonLib is known to wrap ForceRestartGame.
	local nwrap, nrep, wrap_eg = 0, 0, {}
	for name, kinds in pairs(SE.replace_kind) do
		for modid, kind in pairs(kinds) do
			if kind == "wraps" then
				nwrap = nwrap + 1
				if #wrap_eg < 4 then wrap_eg[#wrap_eg + 1] = modid .. ":" .. name end
			else
				nrep = nrep + 1
			end
		end
	end
	put("classifier_wraps_total", nwrap)
	put("classifier_replaces_total", nrep)
	put("classifier_wrap_examples", table.concat(wrap_eg, " "))

	-- Resolution demo
	put("demo_each_version_returned", tostring(SE.demo_before))
	put("demo_policies_applied", tostring(SE.demo_applied))
	put("demo_merged_returned", tostring(SE.demo_after))
	local ap = {}
	for k, v in pairs(SE.applied_policy or {}) do ap[#ap + 1] = k .. "=" .. v end
	put("policies_in_force", table.concat(ap, " "))

	-- Weapon effects: is the un-hardcoding actually live?
	put("melee_actions_patched", tostring(SE.actions_patched))
	local reg = {}
	for k, v in pairs(SE.weapon_effects) do
		reg[#reg + 1] = k .. "=" .. table.concat(v, "+")
	end
	put("weapon_effects_registered", table.concat(reg, " "))

	-- Make a real weapon and ask the registry about it. This exercises the
	-- exact IsKindOf path the patched action uses.
	if rawget(_G, "PlaceInventoryItem") then
		for _, wid in ipairs({"Machete", "Knife", "GutHookKnife"}) do
			local oki, item = pcall(PlaceInventoryItem, wid)
			if oki and item then
				local eff = JA3SE_GetWeaponEffects(item)
				put("lookup_" .. wid, eff and table.concat(eff, "+") or "none")
				pcall(function() item:delete() end)
			else
				put("lookup_" .. wid, "could not create")
			end
		end
	end

	put("LuaModEnv_is_ours", tostring(SE.wrapped_env == rawget(_G, "LuaModEnv")))
	put("dofile_calls_seen", tostring(SE.dofile_calls))
	put("modlua_hook_fired", tostring(SE.modlua_hits))
	put("envs_opened_by_wrapper", tostring(SE.env_opens))
	local names = {}
	for _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do
		names[#names + 1] = tostring(m and m.id)
	end
	put("mods_loaded_ids", table.concat(names, ","))

	-- THE TEST. Build the exact environment a mod is loaded into and try to
	-- use a granted capability through it. If the file lands, a real mod
	-- could have written it - which is impossible without JA3SE.
	local mkenv = rawget(_G, "LuaModEnv")
	if mkenv then
		local ok, env = pcall(mkenv)
		put("modenv_built", ok)
		if ok and env then
			put("mod_sees_JA3SE_Version", tostring(env.JA3SE_Version))
			local okv, ver = pcall(function() return env.JA3SE_Version() end)
			put("mod_called_JA3SE_Version", okv and tostring(ver) or "FAILED")

			put("mod_sees_AsyncStringToFile", tostring(env.AsyncStringToFile))
			local okw, err = pcall(function()
				return env.AsyncStringToFile("AppData/ja3se-written-by-a-mod.txt",
					"A sandboxed mod environment wrote this. Without JA3SE it cannot.")
			end)
			put("mod_wrote_file", okw and "yes" or ("FAILED " .. tostring(err)))

			-- a name we did NOT grant should still be hidden - the control
			put("ungranted_still_hidden", tostring(env.AccountStorage))
		end
	end

	put("log", table.concat(SE.log, " | "))
	AsyncStringToFile("AppData/ja3se-report.txt",
		"[" .. tag .. "]" .. NL .. table.concat(out, NL) .. NL)
end

----------------------------------------------------------------------
-- DLC lifecycle
----------------------------------------------------------------------

local function Preload(self)
	-- Load into the console's OWN environment. The console runs inside
	-- LuaModEnv, so this reaches past its sandbox.
	CreateRealTimeThread(function()
		local n = 0
		while not rawget(_G, "g_ConsoleFENV") and n < 200 do
			Sleep(150)
			n = n + 1
		end
		if rawget(_G, "g_ConsoleFENV") then
			pcall(function() dofile(self.folder .. "/console.lua", g_ConsoleFENV._G) end)
		end
	end)
end

local function Postload(self)
	if rawget(_G, "g_AvailableDlc") then
		g_AvailableDlc[self.name] = true
	end
	Registration()
	Apply()
	say("JA3SE v" .. SE.version .. " up, folder " .. tostring(self.folder))


	-- MOD EDITOR PROBE
	-- The save editor session's one open question: does gv_UnitData exist on
	-- the ModEditor map? It is set in GameSession.lua:716, so it belongs to a
	-- game SESSION, not a map - and the mod editor map may not have one. If it
	-- is missing, the ids their live-link command protocol keys on do not
	-- exist there either, and enumerating live objects becomes an engine job.
	if %(MODEDITOR_PROBE)s then
		CreateRealTimeThread(function()
			WaitMsg("EngineStarted")
			Sleep(3000)
			local out = {}
			local function put(k, v) out[#out + 1] = tostring(k) .. "=" .. tostring(v) end
			local function snap(tag)
				put(tag .. ".CurrentMap", tostring(rawget(_G, "CurrentMap")))
				local ud = rawget(_G, "gv_UnitData")
				local n = 0
				if type(ud) == "table" then for _ in pairs(ud) do n = n + 1 end end
				put(tag .. ".gv_UnitData", type(ud) == "table" and ("table:" .. n) or tostring(ud))
				local gu = rawget(_G, "g_Units")
				local m = 0
				if type(gu) == "table" then for _ in pairs(gu) do m = m + 1 end end
				put(tag .. ".g_Units", type(gu) == "table" and ("table:" .. m) or tostring(gu))
				local G = rawget(_G, "Game")
				put(tag .. ".Game", tostring(G ~= nil))
				put(tag .. ".Game.testModGame", tostring(G and rawget(G, "testModGame")))
				local f = rawget(_G, "AreModdingToolsActive")
				put(tag .. ".ModdingToolsActive", tostring(f and f()))
				local f2 = rawget(_G, "IsModEditorMap")
				put(tag .. ".IsModEditorMap", tostring(f2 and f2(rawget(_G, "CurrentMap"))))
				-- a sample id, since their protocol keys on these
				if type(ud) == "table" then
					for id in pairs(ud) do put(tag .. ".sample_unitdata_id", tostring(id)) break end
				end
				if type(gu) == "table" then
					for _, u in ipairs(gu) do
						put(tag .. ".sample_g_Units_session_id", tostring(rawget(u, "session_id")))
						break
					end
				end
			end

			snap("menu")
			put("changing_map", "ModEditor")
			local okc, errc = pcall(function() ChangeMap(rawget(_G, "ModEditorMapName") or "ModEditor") end)
			put("ChangeMap_ok", tostring(okc) .. (okc and "" or (" " .. tostring(errc))))
			Sleep(12000)
			snap("modeditor")

			pcall(function()
				AsyncStringToFile("AppData/ja3se-modeditor-probe.txt",
					table.concat(out, NL) .. NL)
			end)
		end)
	end


	-- QUIET ENABLE round trip: disable a mod, put it back, no reload either way.
	if %(ENABLE_TEST)s then
		CreateRealTimeThread(function()
			WaitMsg("EngineStarted")
			Sleep(4000)
			local out = {}
			local function put(k, v) out[#out+1] = tostring(k) .. "=" .. tostring(v) end
			local function isLoaded(id)
				for _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do
					if rawget(m, "id") == id then return true end
				end
				return false
			end

			-- pick one that ships code, so code really has to come back
			local target
			for _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do
				local id = rawget(m, "id")
				if id and SE.mod_prev[id] then target = id break end
			end
			put("target", tostring(target))
			if not target then
				-- why is there no footprint? measure instead of guessing
				local np, nt = 0, 0
				for _ in pairs(SE.mod_prev or {}) do np = np + 1 end
				for _ in pairs(SE.mod_touch or {}) do nt = nt + 1 end
				put("mod_prev_entries", np)
				put("mod_touch_entries", nt)
				put("dofile_calls_seen", tostring(SE.dofile_calls))
				put("modlua_hook_fired", tostring(SE.modlua_hits))
				put("wrapped_loadcode", tostring(SE.wrapped_loadcode ~= nil))
				local MD = rawget(_G, "ModDef")
				put("loadcode_is_ours",
					tostring(MD ~= nil and SE.wrapped_loadcode == MD.LoadCode))
				put("applies", tostring(SE.applies))
				put("reloads_seen", tostring(SE.reloads_seen))
				local ids = {}
				for _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do
					ids[#ids+1] = tostring(rawget(m, "id"))
				end
				put("mods_loaded", table.concat(ids, ","))
				put("code_slot_ran", tostring(rawget(_G, "JA3SE_CodeSlotRan")))
				put("TRACE", table.concat(rawget(_G, "JA3SE_TRACE") or {}, " > "))
				put("dbg_wrapper_installs", tostring(rawget(_G, "JA3SE_DBG_installs")))
				put("dbg_loadcode_calls", tostring(rawget(_G, "JA3SE_DBG_calls")))
				put("dbg_ModsLoadCode_calls", tostring(rawget(_G, "JA3SE_DBG_mlc")))
				put("dbg_ModsLoaded_at_that_time", tostring(rawget(_G, "JA3SE_DBG_mlc_n")))
				local DF = rawget(_G, "DlcFolders")
				put("dlc_folders", type(DF) == "table" and table.concat(DF, ",") or tostring(DF))
				for _, f in ipairs(type(DF) == "table" and DF or {}) do
					put("exists_" .. f .. "_Code", tostring(io.exists(f .. "/Code/")))
					local e, fl = AsyncListFiles(f .. "/Code/", "*.lua")
					put("listfiles_" .. f, tostring(e) .. " n=" ..
						tostring(fl and #fl or -1) ..
						" " .. table.concat(fl or {}, ";"))
				end
				local e2, top = AsyncListFiles("Dlc1/", "*", "relative,folders")
				put("dlc1_folders", tostring(e2) .. " " .. table.concat(top or {}, ";"))
				pcall(function() AsyncStringToFile("AppData/ja3se-enable-test.txt",
					table.concat(out, NL) .. NL) end)
				return
			end

			-- Prefer a global the mod CREATED over one it replaced: a mod that
			-- only adds new names has an empty mod_touch, and the probe was
			-- nil for exactly that reason. A created global is also the
			-- cleaner proof - it can only be there if the mod's code ran.
			local probe = ((SE.mod_added[target] or {}).sample or {})[1]
				or (SE.mod_touch[target] or {})[1]
			put("probe_name", tostring(probe))
			put("probe_created_count", tostring((SE.mod_added[target] or {}).n))
			put("probe_replaced_count", tostring(#(SE.mod_touch[target] or {})))
			local function MsgN()
				local _, n = JA3SE_MsgSnapshot()
				return n
			end
			local function ModMsgN()
				return #(SE.mod_msgs[target] or {})
			end
			put("msgs_total_before", tostring(MsgN()))
			put("msgs_from_mod_before", tostring(ModMsgN()))
			put("before.loaded", tostring(isLoaded(target)))
			put("before.probe_is_mods", tostring(probe and JA3SE_PathGet(probe) ~= nil))
			local reloads0 = SE.reloads_seen

			pcall(function() JA3SE_DisableModQuietly(target) end)
			Sleep(800)
			put("afterDisable.loaded", tostring(isLoaded(target)))
			put("afterDisable.probe", tostring(probe and JA3SE_PathGet(probe) ~= nil))
			put("msgs_total_afterDisable", tostring(MsgN()))

			pcall(function() JA3SE_EnableModQuietly(target) end)
			Sleep(1200)
			put("afterEnable.loaded", tostring(isLoaded(target)))
			put("afterEnable.probe", tostring(probe and JA3SE_PathGet(probe) ~= nil))
			put("msgs_total_afterEnable", tostring(MsgN()))
			put("msgs_from_mod_afterEnable", tostring(ModMsgN()))
			put("enable_steps", tostring((SE.last_quiet_enable or {}).steps))
			put("reloads", tostring(reloads0) .. "/" .. tostring(SE.reloads_seen))
			put("engine_ok", tostring(pcall(function()
				local _ = GetPreciseTicks()
				for _ in pairs(rawget(_G, "Mods") or {}) do end
				local t = rawget(_G, "CombatActions")
				if t and t.MeleeAttack then local _ = t.MeleeAttack.id end
			end)))
			pcall(function() AsyncStringToFile("AppData/ja3se-enable-test.txt",
				table.concat(out, NL) .. NL) end)
		end)
	end

	if not %(TEST_MODE)s then
		-- clean build: the features are already applied by Apply(), and the
		-- conflict report shows itself from OnMsg.PreGameMenuOpen. Nothing
		-- else should happen on its own.
		return
	end

	CreateRealTimeThread(function()
		WaitMsg("EngineStarted")
		Sleep(1000)
		pcall(function() WriteReport("boot") end)

		-- Only NOW is it safe to get in front of the game's file loading.
		-- EpicEnabler does the same: hook dofile after EngineStarted, then
		-- drive one controlled reload. Installing it earlier means the boot
		-- path runs through the wrapper, and a fault there hangs the game.
		Sleep(1500)
		pcall(InstallDofileHook)
		say("dofile hook installed, forcing a reload to test survival")

		-- Switch the test mod on IN MEMORY ONLY.
		--
		-- TurnModOn edits AccountStorage.LoadMods, which is a plain Lua table
		-- until something calls SaveAccountStorage(). We never call it, and
		-- the harness force-kills the game, so this never reaches disk -
		-- %%APPDATA%% is shared with the tester's real install and its mod list must
		-- not change. Second argument false so the Mod Manager preset in
		-- LocalStorage is left alone too.
		-- Turn on EVERY installed mod, in memory only. This is the realistic
		-- case the conflict detector is for: a player with a full mod list.
		-- Never saved - SaveAccountStorage is not called and the harness
		-- force-kills, so the real game's mod list is untouched.
		if rawget(_G, "TurnModOn") then
			local n = 0
			for id in pairs(rawget(_G, "Mods") or {}) do
				local ok = pcall(function() TurnModOn(id, false) end)
				if ok then n = n + 1 end
			end
			say("enabled " .. n .. " mods in memory (never saved)")
		end

		-- TurnModOn alone only edits AccountStorage.LoadMods - measured, the
		-- mod showed enabled=1 loaded=0. ModsReloadItems is what rebuilds the
		-- load queue from GetModsToLoad(), and because our mod ships code it
		-- sets reload_lua and drives ReloadLua itself (Mod.lua:1837+). It
		-- asserts IsRealTimeThread, which this is.
		--
		-- This is also exactly what the Mod Manager does when a user ticks a
		-- box, so it tests reload survival on the real path rather than a
		-- synthetic ReloadLua().
		-- ModsReloadDefs rebuilds every mod's ENVIRONMENT. It has to run after
		-- LuaModEnv is wrapped, or the mods keep their old closed sandboxes.
		-- Capture the pcall result. Saying "done" unconditionally hid a
		-- failure here for two runs.
		if rawget(_G, "ModsReloadDefs") then
			local ok1, err1 = pcall(function() ModsReloadDefs() end)
			SE.defs_ok, SE.defs_err = ok1, tostring(err1)
			say("ModsReloadDefs ok=" .. tostring(ok1) .. " err=" .. tostring(err1))
		end

		if rawget(_G, "ModsReloadItems") then
			local ok2, err2 = pcall(function() ModsReloadItems(nil, true) end)
			SE.items_ok, SE.items_err = ok2, tostring(err2)
			say("ModsReloadItems ok=" .. tostring(ok2) .. " err=" .. tostring(err2))
		else
			pcall(function() ReloadLua() end)
		end
		Sleep(3000)
		while Loading do Sleep(100) end
		Sleep(1500)

		-- Demonstrate resolution on a REAL conflict found in the tester's own mod
		-- list: Make IMP Great Again and Extended IMP Character Creation both
		-- hard-replace ImpGetPersonalPerks. It returns a list of perks, so
		-- merging is the right answer - you should end up with both mods'
		-- perks instead of only the last one's.
		pcall(function()
			local name = "ImpGetPersonalPerks"
			local vers = SE.versions[name]
			if vers and #vers > 1 then
				local counts = {}
				for i = 1, #vers do
					local ok, res = pcall(vers[i].fn)
					counts[#counts + 1] = vers[i].mod .. "="
						.. ((ok and type(res) == "table") and #res or "?")
				end
				SE.demo_before = table.concat(counts, " ")

				JA3SE_SetPolicy(name, "merge")
				SE.demo_applied = JA3SE_ApplyPolicies()

				local ok, res = pcall(rawget(_G, name))
				SE.demo_after = (ok and type(res) == "table") and #res or "failed"
			end
		end)

		pcall(function() WriteReport("after-reload") end)

		-- Try again now the analysis is done. PreGameMenuOpen can fire
		-- before mods have been loaded and compared, in which case there
		-- was nothing to report yet; whichever of the two happens last is
		-- the one that shows it. Also covers a mod being toggled later,
		-- which re-runs the analysis.
		pcall(function() JA3SE_ShowConflictReport() end)
	end)
end

-- display_name omitted deliberately. LoadDlcs complains about a plain string,
-- and a T() with an id this game does not have is a needless risk while the
-- basics are still being proven.
return function(folder)
	-- DlcsLoadCode reads DlcFolders, which holds the REMOUNTED path
	-- "Dlc/<name>" - and that remount fails for a packed DLC
	-- ("Path Not Found", Dlc.lua:675, error discarded). So mount the pack
	-- ourselves onto the name it will look for, or Code/ never runs.
	pcall(function()
		MountPack("Dlc/%(DLC_NAME)s", "DLC/%(HPK)s", "priority:high,label:JA3SE")
	end)

	-- Arm the footprint recorder NOW. This function body runs at DlcAutoruns
	-- (Dlc.lua step 2); DlcReloadLua at step 4 then reloads Lua and runs
	-- ModsLoadCode. post_load is later still, so anything armed there watches
	-- a mod load that already happened - measured: mod_prev_entries=0 with
	-- every mod loaded and working. Code/ja3se_early.lua re-arms after each
	-- reload, because Mod.lua re-executing replaces ModDef.LoadCode.
	JA3SE_T("autorun_body")
	-- Identity first. AppData is SHARED between the real install and the test
	-- copy, so without recording WHICH game wrote a file, two runs blur into
	-- one - which is exactly what happened when a test-copy status file was
	-- read as though it described the real game.
	JA3SE_DIAG.run_id = tostring(GetPreciseTicks and GetPreciseTicks() or 0)
		.. "-" .. tostring(AsyncRand and AsyncRand(1000000) or 0)
	JA3SE_Guard("install_identity", function()
		for _, t in ipairs({ "DLC/", "", "Packs/" }) do
			local ok, v = pcall(function() return ConvertToOSPath(t) end)
			if ok and type(v) == "string" and #v > 0 then
				JA3SE_DIAG.install = v
				return
			end
		end
	end)
	JA3SE_Phase("dlc_autorun")
	JA3SE_Receipt("autorun_body")
	-- the file now belongs to THIS run, not the last one that worked
	pcall(function() JA3SE_EarlyReport() end)
	pcall(OpenSandbox)
	-- PublishAPI, not WrapLoadCode: WrapLoadCode is a local declared INSIDE
	-- PublishAPI and is not in scope out here, so calling it directly is
	-- calling nil - and the pcall swallowed that in total silence. Fourth
	-- time a Lua local out of scope has cost a debugging session on this
	-- project; the trace is what finally showed it (autorun_body > CODE_SLOT
	-- > wrap:INSTALL, with the install arriving a whole reload too late).
	-- PublishAPI ends by arming the recorder, which is what we need here.
	pcall(PublishAPI)

	return {
		name = "%(DLC_NAME)s",
		enabled = true,
		required_lua_revision = 227000,
		pre_load = function(self) Preload(self) end,
		post_load = function(self) Postload(self) end,
	}
end
""" % {'VERSION': VERSION, 'DLC_NAME': DLC_NAME, 'TEST_MOD': TEST_MOD,
        'TEST_MODE': 'true' if TEST_MODE else 'false',
        'MODEDITOR_PROBE': 'true' if MODEDITOR_PROBE else 'false',
        'HPK': HPK,
        'CTH_TEST': 'true' if CTH_TEST else 'false',
        'FAULT_INJECT': 'true' if FAULT_INJECT else 'false',
        'SPAWN_TEST': 'true' if SPAWN_TEST else 'false',
        'PROBE_ENGINE_ERRORS': 'true' if PROBE_ENGINE_ERRORS else 'false',
        'HEARTBEAT': 'true' if HEARTBEAT else 'false',
        'ENABLE_TEST': 'true' if ENABLE_TEST else 'false'}

CODE_SLOT = '''-- Runs from DlcsLoadCode() at autorun.lua:369, ONE LINE before ModsLoadCode
-- at 370. That is the only moment early enough to see mods load.
--
-- Without this the extender loads from post_load, which is AFTER
-- DlcReloadLua has already reloaded and after ModsLoadCode has already run -
-- so no mod footprints are recorded and conflict detection, the quiet
-- disable and the quiet enable are all silently inert. Measured:
-- reloads_seen=0, dofile_calls=nil, mod_prev_entries=0 in a build that
-- looked fine.
--
-- Everything is pcall'd: this runs inside the engine's reload and must not
-- be able to break it.
pcall(function()
	if rawget(_G, "JA3SE_WrapLoadCode") then JA3SE_WrapLoadCode() end
	if rawget(_G, "JA3SE_OpenSandboxNow") then JA3SE_OpenSandboxNow() end
	-- One line before ModsLoadCode and a whole engine phase before
	-- ContinueModsReloadItems: the only slot where a mod added to
	-- ModsLoaded still gets BOTH its code and its items from the engine.
	if rawget(_G, "JA3SE_ListModsForBoot") then JA3SE_ListModsForBoot() end
end)
if rawget(_G, "JA3SE_T") then JA3SE_T("CODE_SLOT") end
rawset(_G, "JA3SE_CodeSlotRan", (rawget(_G, "JA3SE_CodeSlotRan") or 0) + 1)
'''

DIAG_LUA = '\n-- ============================================================================\n-- DIAGNOSTICS: silence is failure\n--\n-- Written after dissecting Douglas Greiman\'s JAPE 0.41 for its field-testing\n-- model. The IDEAS are his and are credited in\n-- `Documentation/The watchdog - silence is failure.md`; the code is ours.\n--\n-- His model, in one line: spend the safety budget at the ENTRANCE. Refuse to\n-- proceed on anything you do not fully recognise, and prove your position with\n-- values that cannot vary (his `ubOne`, a byte that must always be 1).\n--\n-- Ours adds the half he did not need: he parsed a file that sat still, we run\n-- inside a live engine that can move under us. So we also need proof that\n-- things RAN. Eighteen recorded failures on this project were not crashes -\n-- they ran, said nothing, and did nothing.\n--\n-- Everything here must be incapable of breaking the game. It is diagnostics;\n-- if it throws, it has done more harm than the bug it was looking for.\n-- ============================================================================\n\nlocal D = rawget(_G, "JA3SE_DIAG")\nif D == nil then\n\tD = {\n\t\trun_id   = nil,\n\t\tinstall  = "?",\n\t\tphase    = "autorun",\n\t\tphases   = {},\n\t\treceipts = {},      -- name -> count. Proof a thing actually executed.\n\t\tfailures = {},      -- ordered list. Guard() writes here instead of\n\t\t                    -- swallowing, because pcall is where our errors\n\t\t                    -- have gone to die - four sessions lost to it.\n\t\texpects  = {},      -- name -> {ok=bool, detail=string}\n\t\tticks    = 0,\n\t}\n\trawset(_G, "JA3SE_DIAG", D)\nend\n\n-- Adler-32. Arithmetic only, no bitwise operators, so it does not depend on\n-- which Lua version the engine happens to ship.\nlocal function Adler32(s)\n\tlocal a, b = 1, 0\n\tfor i = 1, #s do\n\t\ta = (a + s:byte(i)) % 65521\n\t\tb = (b + a) % 65521\n\tend\n\treturn b * 65536 + a\nend\n\nlocal function Now()\n\tlocal ok, t = pcall(function() return GetPreciseTicks() end)\n\tif ok and type(t) == "number" then return t end\n\tok, t = pcall(function() return os.time() end)\n\treturn (ok and type(t) == "number") and t or 0\nend\n\n-- Proof of life. The only thing that catches "it never ran", and nothing else\n-- does - you cannot detect absence by watching for errors.\nlocal function Receipt(name, detail)\n\tD.receipts[name] = (D.receipts[name] or 0) + 1\n\tif detail ~= nil then D.receipts[name .. ":detail"] = tostring(detail) end\nend\n\n-- A pcall that REMEMBERS. Never use a bare pcall for anything that matters.\nlocal function Guard(name, fn, ...)\n\tD.guard_calls = (D.guard_calls or 0) + 1; local res = { xpcall(fn, function(e)\n\t\treturn tostring(e) .. "\\n" .. debug.traceback("", 2)\n\tend, ...) }\n\tif res[1] then\n\t\tReceipt(name)\n\t\treturn true, res[2]\n\tend\n\tD.guard_fails = (D.guard_fails or 0) + 1; D.failures[#D.failures + 1] = {\n\t\tname = name, err = tostring(res[2]), phase = D.phase, at = Now(),\n\t}\n\treturn false, res[2]\nend\n\n-- An invariant. `ok` false is recorded, not thrown.\nlocal function Expect(name, ok, detail)\n\tD.expects[name] = { ok = not not ok, detail = tostring(detail or "") }\n\treturn ok\nend\n\nlocal function Phase(p)\n\tD.phase = p\n\tD.phases[#D.phases + 1] = tostring(p) .. "@" .. tostring(Now())\nend\n\nrawset(_G, "JA3SE_Receipt", Receipt)\nrawset(_G, "JA3SE_Guard",   Guard)\nrawset(_G, "JA3SE_Expect",  Expect)\nrawset(_G, "JA3SE_Phase",   Phase)\nrawset(_G, "JA3SE_Adler32", Adler32)\n\n-- ---- publishing with integrity ---------------------------------------------\n-- A consumer must be able to tell four things apart: good, stale, damaged and\n-- unrecognised. Timestamps alone give two. A file half-written when the game\n-- died currently reads as perfectly valid - the same class of bug as the\n-- 0-byte DLC a failed install left in a real game folder.\nlocal function Publish(path, body)\n\tbody = tostring(body or "")\n\tlocal lines = 1\n\tfor _ in body:gmatch("\\n") do lines = lines + 1 end\n\tlocal header = table.concat({\n\t\t"#ja3se", "v" .. tostring(rawget(_G, "JA3SE") and JA3SE.version or "?"),\n\t\t"run=" .. tostring(D.run_id),\n\t\t"install=" .. tostring(D.install),\n\t\t"at=" .. tostring(Now()),\n\t\t"bytes=" .. tostring(#body),\n\t\t"lines=" .. tostring(lines),\n\t\t"adler32=" .. tostring(Adler32(body)),\n\t}, " ") .. "\\n"\n\tlocal ok = Guard("publish:" .. path, function()\n\t\tAsyncStringToFile(path, header .. body)\n\tend)\n\treturn ok\nend\nrawset(_G, "JA3SE_Publish", Publish)\n'

DIAG_LUA = DIAG_LUA + '\n-- Get into a live sector, headless. Extracted because two tests needed it and\n-- the second one silently measured an empty map without it - g_Units was 0\n-- before AND after spawning, which looked like "spawning does not work" and\n-- was actually "we were never in a sector".\n--\n-- QuickStartCampaign does not land you in the map: it stops on a loading\n-- screen with a START button. The engine has its own auto-click for that\n-- (Combat.lua:2625 sets it to 1) which is off on retail because\n-- Platform.developer is false, and it clears itself after each use - so it has\n-- to be re-armed every pass.\nrawset(_G, "JA3SE_EnterSector", function(timeout_s)\n\tif #(rawget(_G, "g_Units") or {}) > 0 then return true end\n\tlocal camps = rawget(_G, "CampaignPresets") or {}\n\tlocal cid = rawget(_G, "DefaultCampaign")\n\tif not cid then for k in pairs(camps) do cid = k break end end\n\tJA3SE_Phase("enter:arming")\n\trawset(_G, "g_AutoClickLoadingScreenStart", 1)\n\tJA3SE_Phase("enter:calling-quickstart")\n\t-- QuickStartCampaign BLOCKS until the campaign is loaded, and the\n\t-- loading screen it stops on wants a click. The auto-click flag\n\t-- clears itself after each use, so arming it once before the call is\n\t-- not enough and re-arming after the call never happens - the call\n\t-- has not returned. Measured: the phase sat on\n\t-- \'enter:calling-quickstart\' for six minutes.\n\t-- So keep it armed from a parallel thread for the whole call, which\n\t-- also covers however many loading screens the sequence puts up.\n\tlocal arming = true\n\tCreateRealTimeThread(function()\n\t\twhile arming do\n\t\t\trawset(_G, "g_AutoClickLoadingScreenStart", 1)\n\t\t\tSleep(400)\n\t\tend\n\tend)\n\t-- NOT wrapped in JA3SE_Guard. Guard uses xpcall, and QuickStartCampaign\n\t-- YIELDS - it calls SectorLoadingScreenOpen, which waits for the\n\t-- loading screen. You cannot yield across an xpcall boundary, so the\n\t-- call never returns and the thread parks forever. Measured: the\n\t-- phase sat on \'enter:calling-quickstart\' for six minutes across two\n\t-- runs, while the same call under a plain pcall had worked earlier\n\t-- the same day.\n\t--\n\t-- GENERAL RULE: JA3SE_Guard must never wrap anything that yields -\n\t-- Sleep, WaitMsg, dialog waits, or any Async* that blocks. Guard is\n\t-- for synchronous work. Yielding work gets a plain call.\n\tQuickStartCampaign(cid)\n\tarming = false\n\tJA3SE_Phase("enter:quickstart-returned")\n\tfor _ = 1, (timeout_s or 120) do\n\t\trawset(_G, "g_AutoClickLoadingScreenStart", 1)\n\t\tSleep(1000)\n\t\tif #(rawget(_G, "g_Units") or {}) > 0 then break end\n\tend\n\tSleep(3000)\n\tJA3SE_Phase("enter:wait-done-units-" .. tostring(#(rawget(_G, "g_Units") or {})))\n\tif rawget(_G, "gv_DeploymentStarted") then\n\t\tJA3SE_Guard("enter:DeploymentToExploration", function()\n\t\t\tNetSyncEvents.DeploymentToExploration(true, "ja3se")\n\t\tend)\n\t\tSleep(6000)\n\tend\n\tJA3SE_Phase("enter:finished")\n\tlocal n = #(rawget(_G, "g_Units") or {})\n\tJA3SE_Expect("enter:reached a sector with units", n > 0, n)\n\treturn n > 0\nend)\n'
DIAG_LUA = DIAG_LUA + '\n-- ---- what MUST have happened ----------------------------------------------\n-- The list is the point. Without it, "nothing ran" is indistinguishable from\n-- "nothing went wrong" - which is precisely how the footprint recorder stayed\n-- dead through a whole build cycle while every mod worked and the game booted\n-- perfectly.\nlocal EXPECTED = {\n\t"autorun_body",      -- the DLC loader function ran at DlcAutoruns\n\t"sandbox_opened",    -- ModEnvBlacklist opened up\n\t"api_published",     -- JA3SE_* functions exist\n\t"loadcode_wrapped",  -- mod footprints are being recorded\n\t"dofile_hook",       -- re-arms across Lua reloads\n}\n\n-- Canaries. Greiman\'s `ubOne` idea: assert things that CANNOT vary, so a\n-- mismatch proves the ground moved rather than that our value is wrong. A game\n-- patch is the likeliest thing to break this extender silently, and these fire\n-- before anything is exported.\nlocal function RunCanaries()\n\tlocal P = rawget(_G, "Presets") or {}\n\tlocal function count(cls)\n\t\tlocal n, seen = 0, {}\n\t\tfor _, sub in pairs(rawget(P, cls) or {}) do\n\t\t\tfor _, it in ipairs(sub or {}) do\n\t\t\t\tif not seen[it] then seen[it] = true; n = n + 1 end\n\t\t\tend\n\t\tend\n\t\treturn n\n\tend\n\tlocal MD = rawget(_G, "ModDef")\n\tExpect("engine:ModDef.LoadCode is a function",\n\t\tMD ~= nil and type(MD.LoadCode) == "function", type(MD and MD.LoadCode))\n\tExpect("engine:ModsLoaded is a table",\n\t\ttype(rawget(_G, "ModsLoaded")) == "table")\n\tlocal slab = (rawget(_G, "const") or {}).SlabSizeX\n\tExpect("engine:const.SlabSizeX is a positive number",\n\t\ttype(slab) == "number" and slab > 0, slab)\n\tlocal units = count("UnitDataCompositeDef")\n\tExpect("engine:UnitDataCompositeDef has hundreds of rows",\n\t\tunits > 100, units)\n\tlocal items = count("InventoryItemCompositeDef")\n\tExpect("engine:InventoryItemCompositeDef has hundreds of rows",\n\t\titems > 100, items)\n\tlocal wt, found = rawget(P, "WeaponType"), false\n\tfor _, sub in pairs(wt or {}) do\n\t\tfor _, it in ipairs(sub or {}) do\n\t\t\tif rawget(it, "id") == "Handgun" then found = true end\n\t\tend\n\tend\n\tExpect("engine:WeaponType still contains Handgun", found); if %(PROBE_ENGINE_ERRORS)s then local e1 = pcall(function() error("probe") end); Expect("engine:error() is neutered - it does NOT raise", e1 == true, "pcall returned " .. tostring(e1)); local e2 = pcall(function() assert(false, "probe") end); Expect("engine:assert(false) does NOT raise", e2 == true, "pcall returned " .. tostring(e2)) end; local e3 = pcall(function() local t = nil; return t.x end); Expect("engine:a real runtime error DOES raise", e3 == false, "pcall returned " .. tostring(e3))\n\n\t-- The canary that MUST fail. If this one passes, the machinery is broken\n\t-- and every other green light on this page is worthless. Testing the test:\n\t-- twice on this project a check passed while measuring nothing.\n\tExpect("SELFTEST:this must fail",\n\t\trawget(_G, "JA3SE_DeliberatelyAbsent_ZZZ") ~= nil,\n\t\t"if this shows ok, the detector is broken")\nend\n\n-- ---- the report ------------------------------------------------------------\nlocal function SelfCheck()\n\tGuard("selfcheck", RunCanaries)\n\n\tlocal out, problems = {}, 0\n\tlocal function add(l) out[#out + 1] = l end\n\n\tadd("phase_reached=" .. tostring(D.phase))\n\tadd("phases=" .. table.concat(D.phases, " > "))\n\tadd("heartbeats=" .. tostring(D.ticks))\n\tadd("")\n\n\tadd("[expected]")\n\tfor _, name in ipairs(EXPECTED) do\n\t\tlocal n = D.receipts[name] or 0\n\t\tif n == 0 then problems = problems + 1 end\n\t\tadd((n > 0 and "  ok      " or "  MISSING ") .. name ..\n\t\t\t(n > 1 and ("  x" .. n) or ""))\n\tend\n\n\tadd("")\n\tadd("[invariants]")\n\tlocal selftest_ok = false\n\tfor name, e in pairs(D.expects) do\n\t\tlocal is_selftest = name:sub(1, 9) == "SELFTEST:"\n\t\tif is_selftest then\n\t\t\t-- inverted on purpose: this one is healthy when it FAILS\n\t\t\tselftest_ok = not e.ok\n\t\t\tadd((selftest_ok and "  ok      " or "  BROKEN  ") ..\n\t\t\t\tname .. "  " .. e.detail)\n\t\t\tif not selftest_ok then problems = problems + 1 end\n\t\telse\n\t\t\tif not e.ok then problems = problems + 1 end\n\t\t\tadd((e.ok and "  ok      " or "  FAILED  ") .. name ..\n\t\t\t\t(e.detail ~= "" and ("  got=" .. e.detail) or ""))\n\t\tend\n\tend\n\tif not selftest_ok then\n\t\tadd("  !! the self-test canary did not fire - treat everything above")\n\t\tadd("  !! as unverified, the detector itself is not working")\n\tend\n\n\tadd("")\n\tadd("[diag] guard_calls=" .. tostring(D.guard_calls or 0) .. " guard_fails=" .. tostring(D.guard_fails or 0) .. " same_table=" .. tostring(D == rawget(_G, "JA3SE_DIAG"))); local rl = {}; for k in pairs(D.receipts) do rl[#rl+1] = k end; table.sort(rl); add("[receipts] " .. table.concat(rl, ", ")); add("[failures] " .. tostring(#D.failures) ..\n\t\t"   (guarded calls that threw - these used to vanish into pcall)")\n\tfor i, f in ipairs(D.failures) do\n\t\tif i > 20 then add("  ... and " .. (#D.failures - 20) .. " more") break end\n\t\tadd("  " .. f.phase .. "  " .. f.name)\n\t\tfor line in tostring(f.err):gmatch("[^\\n]+") do\n\t\t\tadd("      " .. line)\n\t\tend\n\tend\n\tproblems = problems + #D.failures\n\n\tadd("")\n\tadd("verdict=" .. (problems == 0 and "healthy" or ("PROBLEMS:" .. problems)))\n\tPublish("AppData/ja3se-report.txt", table.concat(out, "\\n"))\n\tReceipt("selfcheck_wrote_report")\n\treturn problems\nend\nrawset(_G, "JA3SE_SelfCheck", SelfCheck)\n\n-- ---- heartbeat -------------------------------------------------------------\n-- The game log only flushes on a clean exit, so a hang or a crash writes\n-- nothing at all - exactly the failures worth catching. A heartbeat survives\n-- both: whatever it last said is where the game died, and which phase it was\n-- in. Fast while booting, because that is when things hang; slow afterwards,\n-- because nobody wants constant disk writes during play.\nlocal function StartHeartbeat()\n\tif D.heartbeat_running then return end\n\tD.heartbeat_running = true\n\tCreateRealTimeThread(function()\n\t\twhile true do\n\t\t\tD.ticks = D.ticks + 1\n\t\t\tGuard("heartbeat", function()\n\t\t\t\tAsyncStringToFile("AppData/ja3se-heartbeat.txt",\n\t\t\t\t\t"run=" .. tostring(D.run_id) ..\n\t\t\t\t\t"\\ntick=" .. tostring(D.ticks) ..\n\t\t\t\t\t"\\nphase=" .. tostring(D.phase) ..\n\t\t\t\t\t"\\ninstall=" .. tostring(D.install) ..\n\t\t\t\t\t"\\nat=" .. tostring(Now()) ..\n\t\t\t\t\t"\\nfailures=" .. tostring(#D.failures) .. "\\n")\n\t\t\tend)\n\t\t\tSleep(D.phase == "playing" and 15000 or 2000)\n\t\tend\n\tend)\n\tReceipt("heartbeat_started")\nend\nrawset(_G, "JA3SE_StartHeartbeat", StartHeartbeat)\n'

DIAG_LUA = DIAG_LUA + '\n-- ---- units on the map, with their inventory --------------------------------\n-- Jager\'s report: you can drop a merc onto the map but not equip them, because\n-- every panel lists the HIRED party and a spawned unit is on the map without\n-- being on the payroll. His own aside answered the hard question - "the AI\n-- units all have a huge bagpack inventory" - so the data exists on non-hired\n-- units and nothing was reporting it.\n--\n-- Slots are read from the unit class\'s own `inventory_slots` (Inventory.lua:719\n-- builds it) rather than a hardcoded list, so a modded slot appears for free.\n-- Self-contained on purpose. An earlier version called CatText, a local\n-- belonging to the catalogue section and NOT in scope here, so Lua\n-- compiled it as a global, it was nil, and it threw once per unit. Every\n-- row was dropped and the file reported units=0 while the map was full -\n-- the tester saw "the file is there but lists nobody".\n--\n-- FIFTH occurrence of this trap on this project. The fix is not to\n-- remember scope better; it is that nothing in this block may depend on a\n-- name defined outside it.\nlocal function SectorText(v)\n\tif v == nil then return "" end\n\tlocal t = type(v)\n\tif t ~= "string" and t ~= "number" and t ~= "boolean" then\n\t\tlocal ok, out = pcall(function() return _InternalTranslate(v) end)\n\t\tif ok and type(out) == "string" then v = out else return "" end\n\tend\n\treturn (tostring(v):gsub("%s+", " "))\nend\n\nrawset(_G, "JA3SE_SectorUnits", function()\n\tlocal units = rawget(_G, "g_Units") or {}\n\tlocal ud_all = rawget(_G, "gv_UnitData") or {}\n\tlocal rows, inv, seen_sid = {}, {}, {}\n\tlocal dupes = 0\n\n\tfor _, u in ipairs(units) do\n\t\tJA3SE_Guard("sector:unit", function()\n\t\t\tlocal sid = rawget(u, "session_id")\n\t\t\tlocal skey = tostring(sid)\n\t\t\t-- The collision Jager hit. Counted rather than asserted, because\n\t\t\t-- assert() does not raise in this engine and would tell us nothing.\n\t\t\tif seen_sid[skey] then\n\t\t\t\tdupes = dupes + 1\n\t\t\tend\n\t\t\tseen_sid[skey] = (seen_sid[skey] or 0) + 1\n\n\t\t\tlocal ud = sid and rawget(ud_all, sid) or nil\n\t\t\tlocal hired = ud and tostring(rawget(ud, "HireStatus")) or ""\n\t\t\tlocal team = rawget(u, "team")\n\t\t\tlocal side = team and tostring(rawget(team, "side") or "") or ""\n\t\t\tlocal name = SectorText(rawget(u, "Name"))\n\t\t\tif name == "" then name = tostring(rawget(u, "unitdatadef_id") or "?") end\n\n\t\t\trows[#rows + 1] = table.concat({\n\t\t\t\tskey,\n\t\t\t\ttostring(rawget(u, "unitdatadef_id") or ""),\n\t\t\t\tname,\n\t\t\t\tside,\n\t\t\t\thired,\n\t\t\t\ttostring(seen_sid[skey] > 1 and "DUPLICATE" or ""),\n\t\t\t}, "\\t")\n\n\t\t\tlocal slots = u.inventory_slots or {}\n\t\t\tfor slot_name in pairs(slots) do\n\t\t\t\tif type(slot_name) == "string" then JA3SE_Guard("sector:slot", function()\n\t\t\t\t\tu:ForEachItemInSlot(slot_name, false, function(item)\n\t\t\t\t\t\tinv[#inv + 1] = table.concat({\n\t\t\t\t\t\t\tskey,\n\t\t\t\t\t\t\ttostring(slot_name),\n\t\t\t\t\t\t\ttostring(item.class or rawget(item, "id") or "?"),\n\t\t\t\t\t\t\ttostring(rawget(item, "Amount") or ""),\n\t\t\t\t\t\t\ttostring(rawget(item, "Condition") or ""),\n\t\t\t\t\t\t}, "\\t")\n\t\t\t\t\tend)\n\t\t\t\tend) end\n\t\t\tend\n\t\tend)\n\tend\n\n\ttable.sort(rows)\n\ttable.sort(inv)\n\tlocal out = {\n\t\t"units=" .. #rows,\n\t\t"inventory_rows=" .. #inv,\n\t\t"duplicate_session_ids=" .. dupes,\n\t\t"",\n\t\t"[units]",\n\t\t"#session_id\\tunit_id\\tname\\tside\\thired\\tflag",\n\t}\n\tfor _, r in ipairs(rows) do out[#out + 1] = r end\n\tout[#out + 1] = ""\n\tout[#out + 1] = "[inventory]"\n\tout[#out + 1] = "#session_id\\tslot\\titem\\tamount\\tcondition"\n\tfor _, r in ipairs(inv) do out[#out + 1] = r end\n\n\tJA3SE_Publish("AppData/ja3se-sector.txt", table.concat(out, "\\n"))\n\treturn #rows, #inv, dupes\nend)\n'

# DIAG_LUA is appended AFTER the % substitution that builds AUTORUN, so a
# %(name)s placeholder inside it is never filled and reaches Lua verbatim.
# Substitute the few flags it needs explicitly. check_lua() caught this.
DIAG_LUA = DIAG_LUA + '\n-- ---- equipping units on the map -------------------------------------------\n-- The read half (JA3SE_SectorUnits) only makes the problem visible. Jager\'s\n-- actual complaint is that he can drop a merc in and then cannot equip them,\n-- so testing a build still means playing up to it.\n--\n-- Everything is validated BEFORE anything is written - Greiman\'s model, and\n-- the same rule the save editor arrived at independently: refuse at the\n-- entrance rather than repair afterwards. Every refusal says why, in words a\n-- person can act on.\nlocal function FindUnitBySession(sid)\n\tlocal want = tostring(sid)\n\tfor _, u in ipairs(rawget(_G, "g_Units") or {}) do\n\t\tif tostring(rawget(u, "session_id")) == want then return u end\n\tend\n\treturn nil\nend\nrawset(_G, "JA3SE_FindUnit", FindUnitBySession)\n\n-- "could the game have produced this?" - the cheapest check that needs no\n-- known-good answer, and the one the save editor session says earned the most.\nlocal function IsRealItem(item_id)\n\tlocal P = rawget(_G, "Presets")\n\tlocal grp = P and rawget(P, "InventoryItemCompositeDef")\n\tfor _, sub in pairs(grp or {}) do\n\t\tfor _, it in ipairs(sub or {}) do\n\t\t\tif rawget(it, "id") == item_id then return true end\n\t\tend\n\tend\n\treturn false\nend\nrawset(_G, "JA3SE_IsRealItem", IsRealItem)\n\nrawset(_G, "JA3SE_GiveItem", function(sid, slot, item_id, opts)\n\topts = opts or {}\n\tlocal u = FindUnitBySession(sid)\n\tif not u then\n\t\treturn false, "no unit on the map with session_id " .. tostring(sid)\n\tend\n\tif not IsRealItem(item_id) then\n\t\treturn false, "no such item in this game: " .. tostring(item_id)\n\tend\n\tlocal slots = u.inventory_slots or {}\n\tif slot == nil or slots[slot] == nil then\n\t\tlocal names = {}\n\t\tfor k in pairs(slots) do if type(k) == "string" then names[#names + 1] = k end end\n\t\ttable.sort(names)\n\t\treturn false, "this unit has no slot named " .. tostring(slot) ..\n\t\t\t"; it has: " .. table.concat(names, ", ")\n\tend\n\n\tlocal ok, item = pcall(function() return PlaceInventoryItem(item_id) end)\n\tif not ok or not item then\n\t\treturn false, "the game refused to create " .. tostring(item_id)\n\tend\n\tif opts.condition then pcall(function() item.Condition = opts.condition end) end\n\tif opts.amount then pcall(function() item.Amount = opts.amount end) end\n\n\t-- Ask permission first. AddItem on a full slot is how you get a silent\n\t-- half-write, and the engine already knows the answer.\n\tlocal okc, pos, why = pcall(function() return u:CanAddItem(slot, item) end)\n\tif not okc or not pos then\n\t\tpcall(function() item:delete() end)\n\t\treturn false, "no room in " .. tostring(slot) .. ": " .. tostring(why or "refused")\n\tend\n\tlocal l, t\n\tpcall(function() l, t = point_unpack(pos) end)\n\tlocal oka, res = pcall(function() return u:AddItem(slot, item, l, t) end)\n\tif not oka then\n\t\tpcall(function() item:delete() end)\n\t\treturn false, "AddItem failed: " .. tostring(res)\n\tend\n\tJA3SE_Receipt("give_item")\n\t-- refresh so JAPE sees it without waiting for the next poll\n\tpcall(function() JA3SE_SectorUnits() end)\n\treturn true, "gave " .. tostring(item_id) .. " to " .. tostring(sid) ..\n\t\t" in " .. tostring(slot)\nend)\n\nrawset(_G, "JA3SE_TakeItem", function(sid, slot, item_id)\n\tlocal u = FindUnitBySession(sid)\n\tif not u then\n\t\treturn false, "no unit on the map with session_id " .. tostring(sid)\n\tend\n\tlocal found\n\tpcall(function()\n\t\tu:ForEachItemInSlot(slot, false, function(item)\n\t\t\tif not found and (item_id == nil or\n\t\t\t\titem.class == item_id or rawget(item, "id") == item_id) then\n\t\t\t\tfound = item\n\t\t\tend\n\t\tend)\n\tend)\n\tif not found then\n\t\treturn false, "nothing matching " .. tostring(item_id) ..\n\t\t\t" in " .. tostring(slot)\n\tend\n\tlocal ok, err = pcall(function() u:RemoveItem(slot, found) end)\n\tif not ok then return false, "RemoveItem failed: " .. tostring(err) end\n\tJA3SE_Receipt("take_item")\n\tpcall(function() JA3SE_SectorUnits() end)\n\treturn true, "removed " .. tostring(item_id) .. " from " .. tostring(sid)\nend)\n'

DIAG_LUA = DIAG_LUA + '\n-- ---- boot-time mod enable, from a config file ------------------------------\n-- Yoda\'s ask: enable a mod at boot without touching AccountStorage, whose tail\n-- is compressed or encrypted engine internals. This reuses the quiet enable,\n-- which is already proven end to end (4 handlers left on disable, the same 4\n-- returned on enable, no Lua reload).\n--\n-- A file rather than a constant on purpose: the map-rebuild session can enable\n-- its own mod without needing the extender rebuilt, so this is not a\n-- bottleneck on someone else\'s inner loop.\n--\n-- IN MEMORY ONLY. Never SaveAccountStorage. the tester\'s real mod selection is\n-- untouched and closing the game reverts everything here.\nrawset(_G, "JA3SE_ForceEnableFromFile", function()\n\tlocal out = {}\n\tlocal ok, text = pcall(function()\n\t\tlocal err, s = AsyncFileToString("AppData/ja3se-enable.txt")\n\t\tif err then return nil end\n\t\treturn s\n\tend)\n\tif not ok or type(text) ~= "string" then\n\t\treturn out          -- no config file is the normal case, not an error\n\tend\n\tfor line in text:gmatch("[^\\r\\n]+") do\n\t\tlocal id = line:match("^%s*([^#%s][^%s]*)%s*$")\n\t\tif id then\n\t\t\tlocal already = false\n\t\t\tfor _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do\n\t\t\t\tif rawget(m, "id") == id then already = true end\n\t\t\tend\n\t\t\tif already then\n\t\t\t\tout[#out + 1] = id .. " = already loaded"\n\t\t\telse\n\t\t\t\tlocal okk, msg = JA3SE_Guard("force_enable:" .. id, function()\n\t\t\t\t\treturn JA3SE_EnableModQuietly(id)\n\t\t\t\tend)\n\t\t\t\tout[#out + 1] = id .. " = " .. (okk and "enabled" or "FAILED")\n\t\t\tend\n\t\tend\n\tend\n\tif #out > 0 then\n\t\tJA3SE_Receipt("force_enable", table.concat(out, "; "))\n\tend\n\treturn out\nend)\n'

DIAG_LUA = DIAG_LUA + '\n-- ---- listing a mod for boot, BEFORE the engine loads items -----------------\n-- The force-enable below works and is proven, but it runs at EngineStarted -\n-- measured at Lua 0:01:23 on a real boot, which is after the main menu, after\n-- LoadData, and after Msg("ModsReloaded"). A campaign preset registered that\n-- late has missed every pass that would have looked at it.\n--\n-- This runs from the Code/ slot instead, which lands between\n-- ModsReloadItems(first_load) and ContinueModsReloadItems - the only window\n-- where adding a mod to ModsLoaded gets it BOTH its code (ModsLoadCode, one\n-- line later) and its items (ContinueModsReloadItems, Dlc.lua:737) from the\n-- engine itself, in the engine\'s own order.\n--\n-- Deliberately NOT calling LoadCode or LoadItems here. ModsLoadCode is one\n-- line away; calling it ourselves would load the mod\'s code twice.\n--\n-- TurnModOn is Lua/Mod.lua:520 and is one line:\n--     table.insert_unique(AccountStorage.LoadMods, id)\n-- The second argument is updatePreset, not save. Nothing reaches disk and\n-- SaveAccountStorage is never called, so the tester\'s real mod selection reverts\n-- when the game closes. It has to be set, though: any later ModsReloadItems\n-- compares GetModsToLoad() against the loaded ids and rebuilds ModsLoaded from\n-- the queue on a mismatch, which would silently drop a mod that was only ever\n-- in the list.\n--\n-- Known limit: a mod with ENTITIES still needs the heavy path. reload_assets\n-- is decided at step 688, before we append. Code and items are fine.\nrawset(_G, "JA3SE_ReadEnableList", function()\n\tlocal ids = {}\n\tlocal ok, text = pcall(function()\n\t\tlocal err, s = AsyncFileToString("AppData/ja3se-enable.txt")\n\t\tif err then return nil end\n\t\treturn s\n\tend)\n\tif not ok or type(text) ~= "string" then return ids end\n\tfor line in text:gmatch("[^\\r\\n]+") do\n\t\tlocal id = line:match("^%s*([^#%s][^%s]*)%s*$")\n\t\tif id then ids[#ids + 1] = id end\n\tend\n\treturn ids\nend)\n\nrawset(_G, "JA3SE_ListModsForBoot", function()\n\tlocal out = {}\n\tlocal mods = rawget(_G, "Mods")\n\tlocal loaded = rawget(_G, "ModsLoaded")\n\t-- Too early is a real state, not a failure: on the very first pass through\n\t-- autorun this slot can run before ModsReloadDefs has populated Mods. The\n\t-- EngineStarted force-enable is still there and still catches that case.\n\tif type(mods) ~= "table" or type(loaded) ~= "table" then\n\t\tJA3SE_Receipt("list_for_boot:too_early")\n\t\treturn out\n\tend\n\tfor _, id in ipairs(JA3SE_ReadEnableList()) do\n\t\tlocal already = false\n\t\tfor _, m in ipairs(loaded) do\n\t\t\tif rawget(m, "id") == id then already = true end\n\t\tend\n\t\tlocal mod = rawget(mods, id)\n\t\tif already then\n\t\t\tout[#out + 1] = id .. " = already listed"\n\t\telseif not mod then\n\t\t\tout[#out + 1] = id .. " = NOT INSTALLED"\n\t\telse\n\t\t\tlocal okk = JA3SE_Guard("list_for_boot:" .. id, function()\n\t\t\t\tif rawget(_G, "TurnModOn") then TurnModOn(id, false) end\n\t\t\t\tloaded[#loaded + 1] = mod\n\t\t\t\t-- options are loaded at step 688, which we arrived too late for\n\t\t\t\tif mod.LoadOptions then mod:LoadOptions() end\n\t\t\tend)\n\t\t\tout[#out + 1] = id .. " = " .. (okk and "listed" or "FAILED")\n\t\tend\n\tend\n\tif #out > 0 then\n\t\tJA3SE_Receipt("list_for_boot", table.concat(out, "; "))\n\tend\n\treturn out\nend)\n\n-- ---- what a mod ACTUALLY got ----------------------------------------------\n-- "items=true" means the items STEP RAN. It does not mean the mod has items,\n-- and half a day went into the difference: a mod reported enabled, healthy,\n-- zero failures, and did nothing at all.\n--\n-- ModDef:ItemsLoaded() cannot close the gap either. Mod.lua:565 sets\n-- self.items = ok and items or {}, so a FAILED items.lua leaves an empty table\n-- and ItemsLoaded() answers true forever after. True, true, and nothing there.\n--\n-- So count. #mod.items is the top level; ForEachModItem walks the tree, which\n-- is what actually matters for a campaign whose presets are nested.\nrawset(_G, "JA3SE_ModItemAudit", function()\n\tlocal rows = {}\n\tlocal mods = rawget(_G, "Mods") or {}\n\tlocal loaded = rawget(_G, "ModsLoaded") or {}\n\tfor _, id in ipairs(JA3SE_ReadEnableList()) do\n\t\tlocal mod = rawget(mods, id)\n\t\tlocal listed = false\n\t\tfor _, m in ipairs(loaded) do\n\t\t\tif rawget(m, "id") == id then listed = true end\n\t\tend\n\t\tlocal top, deep = -1, 0\n\t\tif mod then\n\t\t\tlocal items = rawget(mod, "items")\n\t\t\ttop = (type(items) == "table") and #items or -1\n\t\t\tif mod.ForEachModItem then\n\t\t\t\tpcall(function()\n\t\t\t\t\tmod:ForEachModItem(function() deep = deep + 1 end)\n\t\t\t\tend)\n\t\t\tend\n\t\tend\n\t\tlocal row = id ..\n\t\t\t" installed=" .. tostring(mod ~= nil) ..\n\t\t\t" listed=" .. tostring(listed) ..\n\t\t\t" items=" .. tostring(top) ..\n\t\t\t" moditems=" .. tostring(deep)\n\t\trows[#rows + 1] = row\n\t\t-- A named invariant, so an empty mod turns the verdict red instead of\n\t\t-- reading healthy. That is the whole complaint being answered.\n\t\tJA3SE_Expect("mod:" .. id .. " has loaded items",\n\t\t\tmod ~= nil and listed and deep > 0, row)\n\tend\n\tif #rows > 0 then\n\t\tJA3SE_Receipt("mod_item_audit", table.concat(rows, "; "))\n\tend\n\treturn rows\nend)\n'

DIAG_LUA = DIAG_LUA + '\n-- ---- code items that will never run ---------------------------------------\n-- A ModItemCode in items.lua is NOT what makes a code file load. ModDef:LoadCode\n-- (Mod.lua:524) walks `self.code`, which comes from the `code` field of\n-- metadata.lua, written by the Mod Editor at save time. ModItemCode has no\n-- OnModLoad and never registers itself.\n--\n-- So a hand-written mod can carry a Code/ folder, a ModItemCode, a correct\n-- items.lua, load cleanly, report healthy - and never execute a line of it.\n-- That cost the map-rebuild session four boots and two unrelated bug fixes\n-- before anything pointed at it, because there is no error: LoadCode ran and\n-- walked an empty list.\n--\n-- This checks every loaded mod, not just force-enabled ones. It is true of no\n-- working mod, so a hit is always a real defect.\nrawset(_G, "JA3SE_CodeItemCheck", function()\n\tlocal bad = {}\n\tfor _, mod in ipairs(rawget(_G, "ModsLoaded") or {}) do\n\t\tlocal id = tostring(rawget(mod, "id"))\n\t\tlocal n_code = 0\n\t\tlocal code = rawget(mod, "code")\n\t\tif type(code) == "table" then n_code = #code end\n\t\tlocal n_items = 0\n\t\tif mod.ForEachModItem then\n\t\t\tpcall(function()\n\t\t\t\tmod:ForEachModItem("ModItemCode", function() n_items = n_items + 1 end)\n\t\t\tend)\n\t\tend\n\t\tif n_items > 0 and n_code == 0 then\n\t\t\tbad[#bad + 1] = id .. " (" .. n_items ..\n\t\t\t\t" ModItemCode, metadata.lua declares no code files)"\n\t\tend\n\tend\n\tJA3SE_Expect("mods:every ModItemCode is declared in metadata.lua",\n\t\t#bad == 0,\n\t\t#bad == 0 and "" or table.concat(bad, "; "))\n\tif #bad > 0 then\n\t\tJA3SE_Receipt("code_item_orphaned", table.concat(bad, "; "))\n\tend\n\treturn bad\nend)\n'

DIAG_LUA = DIAG_LUA + '\n-- ---- scale: chance-to-hit against distance, in WORLD UNITS -----------------\n-- The map-rebuild session needs one JA2 tile expressed in JA3 world units, and\n-- the only honest way to that number is what the engine does with distance.\n--\n-- CalcChanceToHit takes a bare POINT (Unit.lua:6947, `IsPoint(target)`), so a\n-- sweep needs no target unit and teleports nothing - we just ask about\n-- positions further and further out.\n--\n-- Reported in world units, NOT slabs. The previous sweep stepped in\n-- const.SlabSizeX and called the column `distance_tiles`, which is precisely\n-- the ambiguity that makes a measurement useless to someone else: a "tile" is\n-- a JA2 tile, a JA3 slab and a JA3 terrain cell, and they are three different\n-- sizes.\nrawset(_G, "JA3SE_ScaleReport", function(opts)\n\topts = opts or {}\n\tlocal out = {}\n\tlocal function add(s) out[#out + 1] = tostring(s) end\n\tlocal function publish()\n\t\tJA3SE_Publish("AppData/ja3se-scale.txt", table.concat(out, "\\n"))\n\t\treturn #out\n\tend\n\n\t-- ---- the map, measured rather than assumed\n\tadd("[map]")\n\tlocal cell = 0\n\tJA3SE_Guard("scale:mapsize", function()\n\t\tlocal sx, sy = terrain.GetMapSize()\n\t\tadd("map_size_world_units=" .. tostring(sx) .. " x " .. tostring(sy))\n\t\t-- 512 x 512 is the fixed terrain grid in every shipped map\n\t\tcell = sx / 512\n\t\tadd("terrain_grid=512 x 512")\n\t\tadd("world_units_per_terrain_cell=" .. tostring(cell))\n\tend)\n\tadd("")\n\n\t-- ---- the constants. Named by the engine, not guessed by us.\n\tadd("[const]")\n\tlocal C = rawget(_G, "const") or {}\n\tlocal function dump(prefix, t, depth)\n\t\tif type(t) ~= "table" or depth > 2 then return end\n\t\tlocal keys = {}\n\t\tfor k in pairs(t) do\n\t\t\tif type(k) == "string" then keys[#keys + 1] = k end\n\t\tend\n\t\ttable.sort(keys)\n\t\tfor _, k in ipairs(keys) do\n\t\t\tlocal v = rawget(t, k)\n\t\t\tlocal tv = type(v)\n\t\t\tif tv == "number" or tv == "boolean" or tv == "string" then\n\t\t\t\tadd("  " .. prefix .. k .. " = " .. tostring(v))\n\t\t\telseif tv == "table" and depth < 2 then\n\t\t\t\tdump(prefix .. k .. ".", v, depth + 1)\n\t\t\tend\n\t\tend\n\tend\n\t-- everything at the top level whose NAME is about size, distance or AP\n\tlocal top = {}\n\tfor k in pairs(C) do\n\t\tif type(k) == "string" and (k:find("Slab") or k:find("Pass")\n\t\t\tor k:find("Tile") or k:find("Move") or k:find("Scale")\n\t\t\tor k:find("AP")) then\n\t\t\ttop[#top + 1] = k\n\t\tend\n\tend\n\ttable.sort(top)\n\tfor _, k in ipairs(top) do\n\t\tlocal v = rawget(C, k)\n\t\tif type(v) == "table" then dump(k .. ".", v, 1)\n\t\telse add("  " .. k .. " = " .. tostring(v)) end\n\tend\n\t-- Combat and Scale in full: the movement cost lives in one of them and we\n\t-- do not know its name. Dumping beats guessing an identifier and reporting\n\t-- nil, which is how a measurement turns into a wrong fact.\n\tfor _, name in ipairs({ "Scale", "Combat" }) do\n\t\tlocal t = rawget(C, name)\n\t\tif type(t) == "table" then add("  -- const." .. name); dump(name .. ".", t, 1) end\n\tend\n\tadd("")\n\n\t-- ---- the shooter\n\tlocal shooter = opts.unit\n\tif not shooter then\n\t\tfor _, u in ipairs(rawget(_G, "g_Units") or {}) do\n\t\t\tlocal ok, is_ours = pcall(function()\n\t\t\t\treturn u.team and u.team.player_team and not u:IsDead()\n\t\t\tend)\n\t\t\tif ok and is_ours then shooter = u break end\n\t\tend\n\tend\n\tif not shooter then\n\t\tadd("[sweep] NO SHOOTER - not in a sector with a living merc.")\n\t\tadd("Everything above is still valid; the curve is not.")\n\t\treturn publish()\n\tend\n\n\tlocal CA = rawget(_G, "CombatActions") or {}\n\tlocal action\n\tfor _, id in ipairs(opts.actions or { "SingleShot", "BurstFire", "Buckshot" }) do\n\t\tif CA[id] then action = CA[id] break end\n\tend\n\tif not action then\n\t\tadd("[sweep] NO ATTACK ACTION available")\n\t\treturn publish()\n\tend\n\tlocal weapon = select(1, action:GetAttackWeapons(shooter))\n\n\tadd("[shooter]")\n\tadd("merc=" .. tostring(rawget(shooter, "unitdatadef_id") or\n\t\trawget(shooter, "session_id")))\n\tadd("action=" .. tostring(action.id) .. "   aim=0 (unaimed), standing")\n\t-- NORMAL indexing, not rawget. `class` is defined on the class, so\n\t-- rawget skips it and returns nil - this printed "weapon=184", the\n\t-- instance id, and named no gun at all. Fourth occurrence of that trap\n\t-- on this project; the build now refuses rawget on this field.\n\tlocal wclass = weapon and weapon.class or "?"\n\tlocal wrange = weapon and weapon.WeaponRange or -1\n\tadd("weapon=" .. tostring(wclass))\n\tadd("weapon_published_WeaponRange=" .. tostring(wrange))\n\tJA3SE_Guard("scale:weaponprops", function()\n\t\tlocal names = {}\n\t\tfor k, v in pairs(weapon or {}) do\n\t\t\tif type(k) == "string" and type(v) == "number" then\n\t\t\t\tnames[#names + 1] = k .. "=" .. tostring(v)\n\t\t\tend\n\t\tend\n\t\ttable.sort(names)\n\t\tadd("weapon_numeric_props: " .. table.concat(names, " "))\n\tend)\n\tadd("")\n\n\t-- ---- the sweep\n\t-- Step one terrain cell at a time so the curve is not aliased by a coarse\n\t-- step, and keep going until the chance has been zero for long enough that\n\t-- it is not a dip.\n\tlocal step = opts.step or (cell > 0 and cell or 600)\n\tlocal maxd = opts.max_units or 150000\n\tlocal pos = shooter:GetPos()\n\tlocal rows, anchors = {}, {}\n\tlocal last_nonzero, first_below90, first_below50 = 0, nil, nil\n\tlocal zeros = 0\n\n\tadd("[sweep]")\n\tadd("# distance_units  terrain_cells  ja2_tiles_at_1800  cth")\n\tlocal d = step\n\twhile d <= maxd do\n\t\tlocal ok, cth = pcall(function()\n\t\t\tlocal p = point(pos:x() + d, pos:y(), pos:z())\n\t\t\treturn shooter:CalcChanceToHit(p, action,\n\t\t\t\t{ aim = 0, weapon = weapon }, "chance_only")\n\t\tend)\n\t\tif not ok or type(cth) ~= "number" then cth = -1 end\n\t\trows[#rows + 1] = table.concat({\n\t\t\ttostring(d),\n\t\t\tstring.format("%.1f", cell > 0 and (d / cell) or 0),\n\t\t\tstring.format("%.1f", d / 1800),\n\t\t\ttostring(cth),\n\t\t}, "\\t")\n\t\tif cth > 0 then\n\t\t\tlast_nonzero = d\n\t\t\tzeros = 0\n\t\telseif cth == 0 then\n\t\t\tzeros = zeros + 1\n\t\t\tif zeros >= 8 then break end\n\t\tend\n\t\tif cth >= 0 and cth < 90 and not first_below90 then first_below90 = d end\n\t\tif cth >= 0 and cth < 50 and not first_below50 then first_below50 = d end\n\t\td = d + step\n\tend\n\tfor _, r in ipairs(rows) do add(r) end\n\tadd("")\n\n\tadd("[anchors]")\n\tlocal function anchor(label, units)\n\t\tif not units or units == 0 then\n\t\t\tadd("  " .. label .. " = not reached in this sweep")\n\t\t\treturn\n\t\tend\n\t\tadd(string.format("  %s = %d world units  = %.1f terrain cells"\n\t\t\t.. "  = %.1f JA2 tiles if a tile is 1800",\n\t\t\tlabel, units, cell > 0 and units / cell or 0, units / 1800))\n\tend\n\tanchor("CtH still >= 90% out to", first_below90 and (first_below90 - step) or 0)\n\tanchor("CtH drops below 50% at ", first_below50)\n\tanchor("last distance with any chance at all", last_nonzero)\n\tadd("")\n\n\t-- ---- calibration: what one point of WeaponRange is worth in world units\n\t-- The sweep gives this weapon\'s envelope in world units. Its preset gives\n\t-- the same envelope in whatever unit the engine counts range in. One\n\t-- division converts every gun in the game.\n\tadd("[calibration]")\n\tif wrange and wrange > 0 and last_nonzero > 0 then\n\t\tadd(string.format("  %s: published WeaponRange=%d, measured last hit at %d units",\n\t\t\ttostring(wclass), wrange, last_nonzero))\n\t\tadd(string.format("  => 1 point of WeaponRange = %d world units",\n\t\t\tlast_nonzero / wrange))\n\t\tadd(string.format("  => and %.2f terrain cells",\n\t\t\tcell > 0 and (last_nonzero / wrange) / cell or 0))\n\telse\n\t\tadd("  could not calibrate: WeaponRange=" .. tostring(wrange) ..\n\t\t\t" last_nonzero=" .. tostring(last_nonzero))\n\tend\n\tadd("")\n\n\t-- ---- every gun in the game, from the presets\n\t-- So nobody has to trust one merc\'s loadout. This is the engine\'s own\n\t-- number for every weapon, and with the calibration above it converts\n\t-- straight into world units and then into JA2 tiles.\n\tadd("[weapon ranges, from InventoryItemCompositeDef]")\n\tJA3SE_Guard("scale:ranges", function()\n\t\tlocal P = rawget(_G, "Presets") or {}\n\t\tlocal grp = rawget(P, "InventoryItemCompositeDef") or {}\n\t\tlocal seen, rows2 = {}, {}\n\t\tfor _, sub in pairs(grp) do\n\t\t\tfor _, it in ipairs(sub or {}) do\n\t\t\t\tif not seen[it] then\n\t\t\t\t\tseen[it] = true\n\t\t\t\t\tlocal r = it.WeaponRange\n\t\t\t\t\tif type(r) == "number" and r > 0 then\n\t\t\t\t\t\trows2[#rows2 + 1] = { r, rawget(it, "id") or "?" }\n\t\t\t\t\tend\n\t\t\t\tend\n\t\t\tend\n\t\tend\n\t\ttable.sort(rows2, function(a, b)\n\t\t\tif a[1] ~= b[1] then return a[1] > b[1] end\n\t\t\treturn tostring(a[2]) < tostring(b[2])\n\t\tend)\n\t\tadd("# WeaponRange  world_units  ja2_tiles_at_1800  id")\n\t\tlocal per = (wrange and wrange > 0 and last_nonzero > 0)\n\t\t\tand (last_nonzero / wrange) or 0\n\t\tfor _, r in ipairs(rows2) do\n\t\t\tadd(table.concat({\n\t\t\t\ttostring(r[1]),\n\t\t\t\ttostring(per > 0 and (r[1] * per) or "?"),\n\t\t\t\tper > 0 and string.format("%.1f", (r[1] * per) / 1800) or "?",\n\t\t\t\ttostring(r[2]),\n\t\t\t}, "\\t"))\n\t\tend\n\t\tadd("weapons_with_a_range=" .. tostring(#rows2))\n\tend)\n\tadd("")\n\tadd("  The RANGE ENVELOPE is the ruler, not a chance-to-hit percentage.")\n\tadd("  A CtH anchor moves with the merc\'s skill and the gun\'s condition;")\n\tadd("  the distance a weapon stops reaching is a property of the engine.")\n\n\tJA3SE_Receipt("scale_report", tostring(#rows) .. " rows")\n\treturn publish()\nend)\n'

DIAG_LUA = DIAG_LUA + '\n-- ---- a report that exists BEFORE the run can die ---------------------------\n-- ja3se-report.txt was written only by SelfCheck, near the end. So a crash\n-- left the PREVIOUS run\'s report on disk - complete, healthy-looking, and an\n-- hour old. It was read as current, and the actual crash had to be diagnosed\n-- from the game\'s own dump instead.\n--\n-- A stale report is worse than a missing one. A missing file asks a question;\n-- a stale file answers it wrongly and is believed.\n--\n-- So publish a minimal one immediately. Every run now owns the file from its\n-- first moments, and SelfCheck overwrites it with the full version if the run\n-- lives that long. What survives a crash then describes THAT crash.\nrawset(_G, "JA3SE_EarlyReport", function()\n\tlocal D = rawget(_G, "JA3SE_DIAG") or {}\n\tlocal out = {\n\t\t"STATUS: RUN IN PROGRESS",\n\t\t"",\n\t\t"If you are reading this line, the run did not reach its self-check -",\n\t\t"it crashed, hung, or was killed. This file was written early on",\n\t\t"purpose so that a death leaves evidence from the run that died,",\n\t\t"rather than the last one that succeeded.",\n\t\t"",\n\t\t"phase_reached=" .. tostring(D.phase),\n\t\t"phases=" .. table.concat(D.phases or {}, " > "),\n\t\t"heartbeats=" .. tostring(D.ticks or 0),\n\t\t"failures=" .. tostring(#(D.failures or {})),\n\t\t"",\n\t\t"Compare with ja3se-heartbeat.txt: if its run= matches this file\'s",\n\t\t"header and its phase is further along, the run got further than this",\n\t\t"and then stopped there.",\n\t}\n\tlocal rl = {}\n\tfor k in pairs(D.receipts or {}) do rl[#rl + 1] = k end\n\ttable.sort(rl)\n\tout[#out + 1] = ""\n\tout[#out + 1] = "[receipts so far] " .. table.concat(rl, ", ")\n\tJA3SE_Publish("AppData/ja3se-report.txt", table.concat(out, "\\n"))\n\tJA3SE_Receipt("early_report")\nend)\n'

DIAG_LUA = DIAG_LUA.replace('%(PROBE_ENGINE_ERRORS)s',
                            'true' if PROBE_ENGINE_ERRORS else 'false')

# Insert the diagnostics before the loader function: a Lua chunk cannot
# have code after its final return. Done here, AFTER the % substitution
# that builds AUTORUN, so nothing in DIAG_LUA needs percent-escaping.
AUTORUN = AUTORUN.replace(
    'return function(folder)',
    DIAG_LUA + chr(10) + 'return function(folder)', 1)

FILES = {
	'Code/ja3se_early.lua': CODE_SLOT.encode('utf-8'),
	'autorun.lua': AUTORUN.lstrip().encode('utf-8'),
	'revisions.lua': REVISIONS.encode('utf-8'),
	'console.lua': CONSOLE.lstrip().encode('utf-8'),
}




def _unowned_appdata_files(src):
	"""Files the Lua writes that the uninstaller does not know about.

	The manifest in ja3se_install.py is what uninstall deletes and what we
	show a person who asks what we put on their machine. A new AppData file
	added here and not added there is invisible: it appears on their disk,
	survives an uninstall, and nothing reports it.

	That is how Jaeger ended up with fifteen leftover files, so the list is
	guarded rather than trusted.
	"""
	import re as _re
	written = set(_re.findall(r'"AppData/([A-Za-z0-9._-]+)"', src))
	origin = {}
	# No import, no try. The manifest is a constant in this module, so the
	# only way this cannot run is a programming error here - and it must NOT
	# be swallowed. The first version imported ja3se_install, which imports
	# this module back; the cycle left one of them half-built and the bare
	# except turned "could not run" into "found nothing". A check that cannot
	# run must never look like a check that passed.
	# Our own tools write into the same folder and their output lands on the
	# same person's disk. ja3se-code.txt came from build_probe.py and survived
	# a purge precisely because this only looked at the shipped Lua.
	for tool in ('build_probe.py',):
		p = os.path.join(os.path.dirname(os.path.abspath(__file__)), tool)
		if os.path.exists(p):
			for n in _re.findall(r'"AppData/([A-Za-z0-9._-]+)"',
			                     io.open(p, encoding='utf-8').read()):
				# Say where it came from. The first version reported a stray
				# from a tool against whichever Lua file happened to be under
				# test, which is the same "the error names the wrong file"
				# problem that left PropertyObject.lua:949 unexamined for days.
				origin[n] = tool
				written.add(n)
	return sorted('%s (written by %s)' % (n, origin[n]) if n in origin else n
	              for n in written - set(OWNED_APPDATA))


def _rawget_class_field_problems(src):
	"""`rawget` on a field the engine defines on the CLASS, not the instance.

	Four occurrences now - `inventory_slots` twice, `item.class`, and the
	weapon name in the scale report, which printed an instance id where a gun
	name belonged and would have made every conclusion be about an unknown
	weapon.

	Banning rawget outright is not possible: there are dozens of legitimate
	calls on instance fields, and a noisy check gets switched off. So this
	names the specific fields that are known to live on the class. The list
	grows by one each time this bites, which is the honest way to do it.
	"""
	fields = ('class', 'inventory_slots', 'WeaponRange')
	out = []
	for i, line in enumerate(src.split(chr(10)), 1):
		if line.lstrip().startswith('--'):
			continue
		for f in fields:
			for quote in ('"%s"' % f, "'%s'" % f):
				if 'rawget(' in line and quote in line:
					seg = line.split('rawget(', 1)[1]
					if quote in seg.split(')', 1)[0]:
						out.append('line %d: rawget(..., %s) - %s is defined on '
						           'the class, so rawget returns nil. Use normal '
						           'indexing.' % (i, quote, f))
	return out

def _contained_call_problems(src):
	"""Calls that must only happen inside the helper written to contain them.

	Three times now a trap has been found, solved in one place, and then walked
	straight into at a second call site:

	    PresetRows fixed, ProbePresets not      doubled counts into another spec
	    InstallDofileHook, then WrapLoadCode    same out-of-scope local, twice
	    JA3SE_EnterSector, then the CTH block   parked 900s on a loading screen

	The helper is not the fix on its own; the fix is the helper plus nothing
	else being allowed to do it by hand.
	"""
	contained = {
		'QuickStartCampaign': 'JA3SE_EnterSector',
	}
	out = []
	for callee, owner in sorted(contained.items()):
		inside = False
		depth = 0
		for i, line in enumerate(src.split(chr(10)), 1):
			if 'rawset(_G, "%s"' % owner in line:
				inside, depth = True, 0
			if inside:
				depth += line.count('function') - line.count(chr(10))
				if line.startswith('end)'):
					inside = False
			if callee + '(' in line and not inside:
				if line.lstrip().startswith('--'):
					continue
				out.append('line %d: %s() outside %s - use %s'
				           % (i, callee, owner, owner))
	return out

def _global_function_declarations(src):
	"""Top-level `function Name(...)` - the one form that bypasses rawset.

	The engine guards _G against new globals, so a plain global function
	declaration logs

	    [LUA ERROR] Attempt to create a new global 'JA3SE_T'

	on every single boot. It still works, which is why the one we shipped
	survived from v1 until a log was read line by line for another reason. An
	error nobody has to act on is an error nobody reads, and it was sitting in
	the diagnostics file whose entire job is to stop that happening.

	`local function` and `function tbl.field()` are fine; only a bare global
	name is refused.
	"""
	import re as _re
	lines = src.split(chr(10))

	# Names forward-declared as locals. `function Name()` is sugar for
	# `Name = function()`, so it assigns to a visible local and creates no
	# global - InstallDofileHook is declared that way on purpose. Not
	# scope-exact, deliberately: a false positive here gets the whole check
	# switched off, and the cost of missing one is a log line, not a bug.
	locals_ = set()
	for line in lines:
		m = _re.match(r'\s*local\s+function\s+([A-Za-z_][A-Za-z0-9_]*)', line)
		if m:
			locals_.add(m.group(1))
			continue
		m = _re.match(r'\s*local\s+([A-Za-z_][A-Za-z0-9_,\s]*)', line)
		if m:
			for part in m.group(1).split('=')[0].split(','):
				part = part.strip()
				if part:
					locals_.add(part)

	out = []
	for i, line in enumerate(lines, 1):
		stripped = line.lstrip()
		if not stripped.startswith('function '):
			continue
		name = stripped[len('function '):].split('(')[0].strip()
		if not name or '.' in name or ':' in name or name in locals_:
			continue
		out.append('line %d: function %s() - use rawset(_G, "%s", function...)'
		           % (i, name, name))
	return out

def _lua_scope_problems(src):
	"""Names used where Lua will compile them as globals.

	Five times on this project a Lua local has been used out of scope. Lua
	compiles that as a global, it is nil at run time, and inside a pcall it
	fails in total silence:

	    CatText            units=0 while the map was full
	    InstallDofileHook  footprint recorder never armed, three features dead
	    WrapLoadCode       the same bug at a second call site
	    ResolvePath        unload reported ten unreachable names

	A name is fine if the call sits inside ANY declaration of it - locals
	shadow, and several helpers here are declared once per enclosing function.
	It is a problem only when the call is outside every one of them.
	"""
	import re as _re
	lines = src.split(chr(10))
	tab = chr(9)

	def block_end(start, indent):
		for j in range(start + 1, len(lines)):
			s = lines[j]
			if not s.strip():
				continue
			ind = len(s) - len(s.lstrip(tab))
			if ind < indent and s.strip().startswith("end"):
				return j
		return len(lines)

	decls = {}
	for i, line in enumerate(lines):
		m = _re.match(r"(\t*)local (?:function )?(\w+)", line)
		if not m:
			continue
		indent, name = len(m.group(1)), m.group(2)
		end = len(lines) if indent == 0 else block_end(i, indent)
		decls.setdefault(name, []).append((i, end))

	problems = []
	for name, ranges in decls.items():
		pat = _re.compile(r"(?<![\w.:])" + name + r"\s*\(")
		for j, raw in enumerate(lines):
			# strip a trailing comment before matching: Guard() named inside
			# a comment was the linter's only false positive on its first run
			line, q = raw, 0
			for k in range(len(raw) - 1):
				if raw[k] == chr(34):
					q += 1
				elif raw[k:k+2] == "--" and q % 2 == 0:
					line = raw[:k]
					break
			if not pat.search(line):
				continue
			if any(a <= j <= b for a, b in ranges):
				continue
			where = ", ".join(str(a + 1) for a, _ in ranges)
			problems.append(
				"line %d: %s() is not in scope here - it is a local declared "
				"at line %s. Lua compiles this as a global; it will be nil at "
				"run time, and silent inside a pcall." % (j + 1, name, where))
	return problems

def check_lua():
	"""Refuse to ship Lua that will not compile.

	There is no error when this goes wrong. The DLC just never loads, the game
	starts normally, and nothing anywhere says why - which is exactly how a
	raw carriage return, and later a Python-eaten backslash escape, each cost
	a debugging session. Cheap to check, expensive to miss.
	"""
	try:
		import lupa
	except Exception:                                            # noqa: BLE001
		return None, 'lupa not installed - Lua was NOT syntax checked'
	rt = lupa.LuaRuntime()
	chk = rt.eval('function(s) local f, e = load(s, "@x") '
	              'if f then return true, "" else return false, tostring(e) end end')
	for name, blob in sorted(FILES.items()):
		if not name.endswith('.lua'):
			continue
		ok, err = chk(blob.decode('utf-8'))
		if not ok:
			raise SyntaxError('%s will not compile, so the DLC would silently '
			                  'never load: %s' % (name, err))
		stray = _unowned_appdata_files(blob.decode('utf-8'))
		if stray:
			raise SyntaxError(
				'%s writes AppData files the uninstaller does not know about, '
				'so they would survive an uninstall and nothing would tell the '
				'user they exist. Add them to OWNED_APPDATA in '
				'ja3se_install.py:%s%s'
				% (name, chr(10) + '  ', (chr(10) + '  ').join(stray)))
		classy = _rawget_class_field_problems(blob.decode('utf-8'))
		if classy:
			raise SyntaxError(
				'%s uses rawget on a class-level field, which silently reads '
				'nil - four occurrences so far, the last one printed an item '
				'id where a weapon name belonged:%s%s'
				% (name, chr(10) + '  ', (chr(10) + '  ').join(classy)))
		held = _contained_call_problems(blob.decode('utf-8'))
		if held:
			raise SyntaxError(
				'%s calls a contained function by hand. A helper written to '
				'hold a trap only works if nothing walks around it - that has '
				'now cost three debugging sessions:%s%s'
				% (name, chr(10) + '  ', (chr(10) + '  ').join(held)))
		glob = _global_function_declarations(blob.decode('utf-8'))
		if glob:
			raise SyntaxError(
				'%s declares %d plain global function(s). The engine guards _G '
				'against new globals, so each one logs "Attempt to create a new '
				'global" on every boot - and works anyway, which is why the one '
				'we shipped survived from v1 unnoticed:%s%s'
				% (name, len(glob), chr(10) + '  ',
				   (chr(10) + '  ').join(glob)))
		bad = _lua_scope_problems(blob.decode('utf-8'))
		if bad:
			raise SyntaxError(
				'%s has %d scope problem(s). Lua compiles an out-of-scope '
				'local as a global, it is nil at run time, and inside a pcall '
				'it fails silently - that has cost this project five debugging '
				'sessions:%s%s'
				% (name, len(bad), chr(10) + '  ', (chr(10) + '  ').join(bad)))
	return True, 'all Lua compiles'


def _stamped_files():
	"""FILES with the build stamp prepended to autorun.lua.

	A Lua comment, so it cannot affect behaviour, and the pack is uncompressed
	so it survives into the .hpk bytes as readable ASCII. That is what lets an
	installer read the identity of a file somebody else wrote without knowing
	anything about the archive format.
	"""
	out = dict(FILES)
	out['autorun.lua'] = (('-- JA3SE_BUILD %s\n' % BUILD).encode('utf-8')
	                      + out['autorun.lua'])
	return out


def _bump_build():
	"""Give this build a stamp greater than the last one, automatically.

	Rewrites the constant in this file so the value is fixed in the source
	afterwards - pack() has to be deterministic, or every call would produce
	different bytes and 'up to date' could never be true.
	"""
	import re
	import time
	global BUILD
	BUILD = time.strftime('%Y%m%d%H%M%S')
	path = os.path.abspath(__file__)
	src = io.open(path, encoding='utf-8').read()
	src = re.sub(r"^BUILD = '[0-9]*'$", "BUILD = '%s'" % BUILD, src,
	             count=1, flags=re.M)
	io.open(path, 'w', encoding='utf-8', newline='\n').write(src)
	return BUILD


def pack():
	# Uncompressed, entry type 0. Compressed entries hang the game forever.
	check_lua()
	return hpk_write.pack_bytes(_stamped_files(), compress=False)


def install():
	folder = os.path.join(TEST_COPY, 'DLC')
	if not os.path.isdir(folder):
		os.makedirs(folder)
	dest = os.path.join(folder, HPK)
	with open(dest, 'wb') as f:
		f.write(pack())
	return dest


def remove():
	dest = os.path.join(TEST_COPY, 'DLC', HPK)
	if os.path.exists(dest):
		os.remove(dest)
		return dest
	return None


if __name__ == '__main__':
	arg = sys.argv[1] if len(sys.argv) > 1 else '--install'
	if arg != '--remove':
		print('build %s' % _bump_build())
	if arg == '--remove':
		p = remove()
		print('removed: %s' % p if p else 'was not installed')
	else:
		p = install()
		print('installed: %s (%d bytes)' % (p, os.path.getsize(p)))
