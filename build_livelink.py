"""Builds the Nemesis Live DLC - the save editor's live link to a running game.

How it works: two small text files in the game's own AppData folder.

    nemesis-cmd.txt     editor writes commands here
    nemesis-state.txt   the game writes what it can see here

The DLC polls the command file a few times a second, applies anything whose
sequence number it has not seen, and reports back. No sockets, no console, no
modding tools - all of which the mod sandbox denied us and the DLC
environment does not.

Safety, in the order the game checks it:
  * multiplayer is refused outright - if netInGame is set, nothing is applied
  * money goes through AddMoney(), not Game.Money, because AddMoney fires
    ObjModified(Game) and Msg("MoneyChanged"). Assigning Game.Money directly
    changes the number but leaves the UI showing the old one, which is exactly
    what went wrong when this was tried through the console.

Install with --install-game (real install) or --install-copy (Test Game Copy).
Removing it deletes the pack and every file named in OWNED_APPDATA; the game
boots normally without it.
"""
import fnmatch
import os
import re
import shutil
import sys
import time

import gamepaths
import hpk_write

NAME = 'nemesis-live'
HPK = NAME + '.hpk'
LUA_REVISION = 366685

# Only used by --install-copy when testing from the source tree. The shipping
# editor never refers to it; it asks gamepaths where the game is.
TEST_COPY = 'E:/Jagged Alliance 3 - Nemesis/Test Game Copy/Jagged Alliance 3'

# Every file the link puts in the game's AppData folder, by name, so uninstall
# can take all of it back and a person can see exactly what was ours.
# `unlisted_appdata()` fails the tests if the Lua writes a name missing here.
OWNED_APPDATA = (
	'nemesis-cmd.txt',
	'nemesis-cmd.txt.tmp',     # the editor's half of an atomic write
	'nemesis-state.txt',
	'nemesis-mods.txt',
	'nemesis-mercs.txt',
	'nemesis-shot.txt',
	'nemesis-dlc-proof.txt',   # the first experiment that proved DLC code runs
)

# The one pattern. The `shot` op names its own picture, so the names cannot be
# listed - but only that op writes a nemesis-*.png, and it strips the name to
# letters, digits, dash and underscore first.
OWNED_PATTERN = 'nemesis-*.png'

REVISIONS = "return %d, %d\n" % (LUA_REVISION, LUA_REVISION)
CONSOLE = "return 0;\n"

# No backslash escapes in this Lua on purpose - they travel through shells and
# heredocs that eat them. string.char(10) is a newline.
AUTORUN = """
-- Nemesis Live - the save editor's link to a running game.
-- Loaded as a DLC, so none of the mod sandbox restrictions apply.

local NL = string.char(10)
local TAB = string.char(9)
local CMD = "AppData/nemesis-cmd.txt"
local STATE = "AppData/nemesis-state.txt"
local MODS = "AppData/nemesis-mods.txt"
local MERCS = "AppData/nemesis-mercs.txt"
local POLL = 400

local LastSeq = -1
local Primed = false
local LastResult = "idle"
local LastState = ""
-- nil, not "". These start unequal to ANY text so the first tick always
-- writes, even when the answer is "nobody". Starting them at "" meant that at
-- the main menu the new empty list matched the old one, the write was skipped,
-- and the file from the previous session survived - so the editor showed a
-- merc list for a game that was no longer loaded, with a live connection
-- beside it. Stale data that looks current is worse than no data.
local LastMods = nil
local LastMercs = nil
local Ticks = 0

local function say(s)
	if DebugPrint then
		DebugPrint("[NemesisLive] " .. tostring(s) .. NL)
	end
end

local function ParseKV(text)
	local t = {}
	for line in string.gmatch(text, "[^" .. NL .. "]+") do
		local k, v = string.match(line, "^%s*([%w_]+)%s*=%s*(.-)%s*$")
		if k then
			t[k] = v
		end
	end
	return t
end

local function InGame()
	local g = rawget(_G, "Game")
	if g and g.CampaignTime then
		return true
	end
	return false
end

local function IsMultiplayer()
	if rawget(_G, "netInGame") then
		return true
	end
	return false
end

-- The squad bag, so the editor can show what the GAME holds instead of falling
-- back to the save file. `item.class` is the canonical id: it is what the
-- game's own AddItemsToSquadBag compares on and what PlaceInventoryItem takes,
-- and it is the name that appears in a savegame's PlaceInventoryItem call.
--
-- GetSquadBag is the game's own accessor (SquadBag.lua:218) rather than walking
-- gv_Squads here - two copies of a rule drift apart, and theirs is next to the
-- data. Same squad choice as the bagadd op above it, so a read and a write can
-- never disagree about which bag they mean.
local function BagText()
	local squads = rawget(_G, "GetPlayerMercSquads")
	local squad = squads and squads()[1]
	if not squad then return "" end
	local get = rawget(_G, "GetSquadBag")
	if not get then return "" end
	local bag = get(squad.UniqueId or squad.unique_id or squad.id)
	if type(bag) ~= "table" then return "" end
	local out = {}
	for i = 1, #bag do
		local item = bag[i]
		local id = item and item.class
		if id then
			out[#out + 1] = tostring(id) .. ":" .. tostring(item.Amount or 1)
		end
	end
	return table.concat(out, ",")
end

local function WriteState()
	local g = rawget(_G, "Game")
	local out = {}
	out[#out + 1] = "seq=" .. tostring(LastSeq)
	out[#out + 1] = "ingame=" .. (InGame() and "1" or "0")
	out[#out + 1] = "mp=" .. (IsMultiplayer() and "1" or "0")
	out[#out + 1] = "money=" .. tostring((g and g.Money) or "")
	out[#out + 1] = "result=" .. tostring(LastResult)
	-- pcall because WriteState runs on every tick and must not be able to
	-- throw. An empty value is honest: it says "nothing to report", and the
	-- reader tells that apart from the key being absent altogether.
	local okbag, bagtext = pcall(BagText)
	out[#out + 1] = "bag=" .. ((okbag and bagtext) or "")
	local text = table.concat(out, NL)
	-- Rewrite when something changed, and every 5th tick regardless so the
	-- file's timestamp keeps ticking. The editor uses that as the heartbeat.
	if text ~= LastState or Ticks % 5 == 0 then
		AsyncStringToFile(STATE, text)
		LastState = text
	end
end

-- id, enabled, loaded, title - one mod per line, tab separated.
-- "enabled" is what AccountStorage says should load next time; "loaded" is
-- what is actually running now. They differ after a toggle until a reload,
-- and showing both is the whole point - the editor can then say so plainly.
local function ModsText()
	local enabled = {}
	local acc = rawget(_G, "AccountStorage")
	if acc and acc.LoadMods then
		for i = 1, #acc.LoadMods do
			enabled[acc.LoadMods[i]] = true
		end
	end
	local loaded = {}
	for _, m in ipairs(rawget(_G, "ModsLoaded") or {}) do
		if m and m.id then
			loaded[m.id] = true
		end
	end
	local out = {}
	for id, m in pairs(rawget(_G, "Mods") or {}) do
		out[#out + 1] = table.concat({
			tostring(id),
			enabled[id] and "1" or "0",
			loaded[id] and "1" or "0",
			tostring((m and m.title) or id),
		}, TAB)
	end
	table.sort(out)
	return table.concat(out, NL)
end

local function WriteMods()
	local text = ModsText()
	if text ~= LastMods then
		AsyncStringToFile(MODS, text)
		LastMods = text
	end
end

local STATS = {
	"Health", "Agility", "Dexterity", "Strength", "Wisdom",
	"Leadership", "Marksmanship", "Mechanical", "Explosives", "Medical",
}
local IsStat = {}
for i = 1, #STATS do
	IsStat[STATS[i]] = true
end

local function Split(s)
	local out = {}
	for part in string.gmatch(s or "", "[^|]+") do
		out[#out + 1] = part
	end
	return out
end

local function MercName(u, id)
	local name = id
	if u and u.Nick then
		local ok, t = pcall(function() return _InternalTranslate(u.Nick) end)
		if ok and t and t ~= "" then
			name = t
		end
	end
	return name
end

-- A merc exists in two places: gv_UnitData is the campaign record, and
-- g_Units is the body standing in the sector when one is loaded. Editing
-- only the first leaves the merc on screen unchanged, so write to both.
local function Targets(id)
	local out = {}
	local ud = rawget(_G, "gv_UnitData")
	if ud and ud[id] then
		out[#out + 1] = ud[id]
	end
	local units = rawget(_G, "g_Units")
	if units and units[id] then
		out[#out + 1] = units[id]
	end
	return out
end

-- A unit spawned as an enemy comes back flagged HireStatus == "Hired", so a
-- plain hire-status test hands the editor Legion raiders as though they were
-- on the payroll. Check which side the body on the map is actually fighting
-- for. g_Units is the only place that knows; gv_UnitData does not.
local ENEMY_SIDES = { enemy1 = true, enemy2 = true, ally = false }

local function OnMap(id)
	local ok, there = pcall(function()
		local units = rawget(_G, "g_Units")
		return units and units[id] ~= nil
	end)
	if not ok then
		return "?"
	end
	return there and "1" or "0"
end

local function IsEnemySide(id)
	local units = rawget(_G, "g_Units")
	local u = units and units[id]
	if not u then
		return false
	end
	local side = u.side or (u.GetSide and u:GetSide())
	return ENEMY_SIDES[side] == true
end

-- Take a merc back off the map. This is what makes a test setup re-appliable:
-- CheatAddMerc quietly returns when the merc is already there, so without a
-- despawn first, clicking "apply" a second time does nothing and says nothing.
-- The game's own Test-mod-item button on a unit preset does the same thing in
-- the same order, which is where this came from.
local function Despawn(id)
	if rawget(_G, "LocalRemoveMercFromSquad") then
		pcall(LocalRemoveMercFromSquad, id)
	end
	local ud = rawget(_G, "gv_UnitData")
	if ud then
		ud[id] = nil
	end
end

local function SetProp(id, prop, n, useBase)
	local objs = Targets(id)
	if #objs == 0 then
		return "error: no merc called " .. tostring(id)
	end
	for i = 1, #objs do
		local o = objs[i]
		-- stats carry modifiers, so SetBase is the honest way in; a plain
		-- assignment would be overwritten the next time they are recomputed
		if useBase and o.SetBase then
			o:SetBase(prop, n)
		else
			o[prop] = n
		end
		if rawget(_G, "ObjModified") then
			ObjModified(o)
		end
	end
	return "ok: " .. tostring(id) .. " " .. prop .. " = " .. tostring(n)
end

local function HiredIds()
	local ids = {}
	local ud = rawget(_G, "gv_UnitData") or {}
	for id, u in pairs(ud) do
		if u and u.HireStatus == "Hired" and not IsEnemySide(id) then
			ids[#ids + 1] = id
		end
	end
	return ids
end

-- id, name, hp, max hp, experience, then the ten stats comma separated
local function MercsText()
	local ud = rawget(_G, "gv_UnitData")
	if not ud then
		return ""
	end
	local out = {}
	for id, u in pairs(ud) do
		if u and u.HireStatus == "Hired" and not IsEnemySide(id) then
			local vals = {}
			for i = 1, #STATS do
				vals[#vals + 1] = tostring(u[STATS[i]] or 0)
			end
			local perks = {}
			for _, e in ipairs(u.StatusEffects or {}) do
				local eid = e and (e.class or e.id)
				if eid then
					perks[#perks + 1] = tostring(eid)
				end
			end
			out[#out + 1] = table.concat({
				tostring(id),
				MercName(u, id),
				tostring(u.HitPoints or 0),
				tostring(u.MaxHitPoints or 0),
				tostring(u.Experience or 0),
				table.concat(vals, ","),
				table.concat(perks, ","),
				-- Is this merc actually standing in the loaded sector? Being
				-- hired is not the same thing, and the editor was offering to
				-- take people "off the map" who were never on it.
				-- g_Units is the game's own container, not a plain table, so
				-- indexing it is wrapped: a merc list that loses one column is
				-- a great deal better than one that stops updating entirely.
				OnMap(id),
			}, TAB)
		end
	end
	table.sort(out)
	return table.concat(out, NL)
end

local function WriteMercs()
	local text = MercsText()
	if text ~= LastMercs then
		AsyncStringToFile(MERCS, text)
		LastMercs = text
	end
end

local function Apply(cmd)
	local op = cmd.op or ""
	if IsMultiplayer() then
		return "refused: multiplayer game - nothing was changed"
	end
	if op == "ping" then
		return "ok: connected"
	end
	if op == "setmoney" or op == "addmoney" then
		if not InGame() then
			return "error: no campaign loaded"
		end
		local n = tonumber(cmd.value)
		if not n then
			return "error: bad value"
		end
		local delta = n
		if op == "setmoney" then
			delta = n - Game.Money
		end
		if delta ~= 0 then
			AddMoney(delta, "system")
		end
		return "ok: money is now " .. tostring(Game.Money)
	end
	if op == "stat" then
		local a = Split(cmd.value)
		if #a < 3 then
			return "error: need a merc, a stat and a number"
		end
		if not IsStat[a[2]] then
			return "error: " .. tostring(a[2]) .. " is not one of the ten stats"
		end
		local n = tonumber(a[3])
		if not n then
			return "error: that is not a number"
		end
		if n < 1 then n = 1 end
		if n > 100 then n = 100 end
		LastMercs = ""
		return SetProp(a[1], a[2], n, true)
	end
	if op == "hp" or op == "maxhp" or op == "exp" then
		local a = Split(cmd.value)
		if #a < 2 then
			return "error: need a merc and a number"
		end
		local n = tonumber(a[2])
		if not n then
			return "error: that is not a number"
		end
		if n < 0 then n = 0 end
		local prop = "HitPoints"
		if op == "maxhp" then prop = "MaxHitPoints" end
		if op == "exp" then prop = "Experience" end
		LastMercs = ""
		return SetProp(a[1], prop, n, false)
	end
	if op == "heal" then
		local ids
		if not cmd.value or cmd.value == "" or cmd.value == "all" then
			ids = HiredIds()
		else
			ids = { cmd.value }
		end
		local healed = 0
		local rallied = 0
		for i = 1, #ids do
			local objs = Targets(ids[i])
			for j = 1, #objs do
				local o = objs[j]
				if o.MaxHitPoints then
					o.HitPoints = o.MaxHitPoints

					-- the tester, 2026-09-08: "once they're down and I saved the
					-- game, even if I fully heal them, they don't get back up."
					--
					-- Because being downed is not a health value. From the
					-- game's own Unit.lua:
					--
					--     function Unit:IsDowned()
					--         return self.command == "Downed"
					--             or self.combat_behavior == "Downed"
					--     end
					--
					-- and the Downed effect's OnAdded runs
					-- SetCommandIfNotDead(obj, "Downed"). So it is a status
					-- effect driving a command state, and setting HitPoints
					-- left a merc at full health lying on the floor. Same
					-- shape as the two-inventory bug: we wrote one store while
					-- another governed the behaviour.
					--
					-- DownedRally is the game's OWN revive
					-- (Tactical/UnitActions.lua). It clears Stabilized,
					-- BleedingOut, Unconscious and Downed, resets combat
					-- behaviour, tiredness and the downed check penalty,
					-- restores AP in combat, plays the stand-up animation and
					-- ends on SetCommand("Idle"). Both its arguments are
					-- optional - with no medic it just checks whether somebody
					-- is already bandaging you.
					--
					-- Calling the game's function rather than reproducing that
					-- list is the whole point: a hand-written sequence would
					-- miss a step this week and a different one next week.
					if o.IsDowned and o.DownedRally and o:IsDowned() then
						o:DownedRally()
						rallied = rallied + 1
					end

					if rawget(_G, "ObjModified") then
						ObjModified(o)
					end
					healed = healed + 1
				end
			end
		end
		LastMercs = ""
		if healed == 0 then
			return "error: nobody to heal"
		end
		if rallied > 0 then
			return "ok: patched up " .. tostring(healed) .. " merc(s), and got "
				.. tostring(rallied) .. " back on their feet"
		end
		return "ok: patched up " .. tostring(healed) .. " merc(s)"
	end
	if op == "give" then
		local a = Split(cmd.value)
		if #a < 2 then
			return "error: need a merc and an item"
		end
		if not rawget(_G, "PlaceItemInInventoryCheat") then
			return "error: this build cannot add items"
		end
		local units = rawget(_G, "g_Units")
		local unit = units and units[a[1]]
		if not unit then
			return "error: " .. tostring(a[1]) .. " is not in a loaded sector - "
				.. "items can only go to a merc who is actually on the map"
		end
		local amount = tonumber(a[3]) or 1
		-- sync_call = true, so the placement happens HERE and hands back
		-- MoveItem's verdict. Without it the engine queues a NetSyncEvent and
		-- returns nothing, so "ok" was printed before the game had even tried:
		-- a Gewehr98 into a backpack with one free cell reported success and
		-- never arrived, because a large item needs two.
		--
		-- Inline is safe because the editor refuses to touch a multiplayer
		-- game at all, and the net event is what multiplayer needs.
		--
		-- MoveItem's contract, from the comment above it in the game's own
		-- source: "string - why the move could not happen". So a STRING is a
		-- REFUSAL and false is success. Backwards, this would call every
		-- successful give an error.
		local called, why = pcall(PlaceItemInInventoryCheat, a[2], amount,
		                          unit, nil, true)
		if not called then
			return "error: the game raised while placing that item: "
				.. tostring(why)
		end
		if why then
			return "error: " .. tostring(a[2]) .. " did not go in - "
				.. tostring(why) .. ". A large item needs two free slots; "
				.. "the backpack may be full."
		end
		return "ok: gave " .. tostring(amount) .. " x " .. tostring(a[2])
			.. " to " .. MercName(units[a[1]], a[1])
	end
	if op == "equip" then
		local a = Split(cmd.value)
		if #a < 3 then
			return "error: need a merc, a slot and an item"
		end
		if not rawget(_G, "PlaceInventoryItem") then
			return "error: this build cannot place items"
		end
		local units = rawget(_G, "g_Units")
		local ud = rawget(_G, "gv_UnitData")
		-- one item object goes to one owner: the body on the map if the merc
		-- is loaded, otherwise the campaign record
		local o = (units and units[a[1]]) or (ud and ud[a[1]])
		if not o then
			return "error: no merc called " .. tostring(a[1])
		end
		if not o.AddItem then
			return "error: that merc has no inventory right now"
		end
		local ok, item = pcall(PlaceInventoryItem, a[3])
		if not ok or not item then
			return "error: no item called " .. tostring(a[3])
		end
		local pos, reason = o:AddItem(a[2], item)
		if not pos then
			pcall(function() item:delete() end)
			return "error: " .. tostring(reason or "the game refused that slot")
		end
		if rawget(_G, "ObjModified") then
			ObjModified(o)
		end
		LastMercs = ""
		return "ok: " .. tostring(a[3]) .. " into " .. tostring(a[2])
	end
	if op == "spawnmerc" then
		local a = Split(cmd.value)
		local id = a[1]
		if not id or id == "" then
			return "error: need a merc id"
		end
		if not rawget(_G, "CheatAddMerc") then
			return "error: this build cannot place mercs"
		end
		-- CheatAddMerc only ADDS somebody when the player already has a merc
		-- squad. With none it falls through to DbgStartExploration, which is
		-- not a placement at all: it starts a new game session, repoints
		-- gv_CurrentSectorId at whatever sector comes first, rebuilds the
		-- teams from the map and creates a "GAMETEST" squad with a default
		-- party of Ivan, Vicki and Buns. That is why asking for one merc
		-- produced a random group with the sides scrambled. Refuse instead.
		local squads = rawget(_G, "GetPlayerMercSquads")
		if not squads or not next(squads()) then
			return "error: place this from a sector where you already have a "
				.. "squad - with none, the game would reset the scenario "
				.. "instead of adding anyone"
		end
		local units = rawget(_G, "g_Units")
		local here = units and units[id]
		if here then
			-- already there: replacing is what makes re-applying a setup work
			if a[2] ~= "replace" then
				return "ok: " .. MercName(here, id) .. " is already on the map"
			end
			Despawn(id)
		end
		CheatAddMerc(id)
		units = rawget(_G, "g_Units")
		if not (units and units[id]) then
			return "error: the game did not place " .. tostring(id)
		end

		-- The session id collision, found by Jager Allianz on 2026-09-05 and
		-- traced back to this line.
		--
		-- The engine checks for duplicates in CheckUniqueSessionId, called from
		-- AddToGlobalUnits during the spawn. But assert() does not raise in this
		-- engine - it logs and carries on - so the duplicate is added anyway,
		-- the pcall around Apply returns true, and we reported a placement that
		-- had already gone wrong. The only reason anyone knew is that Jager
		-- plays in debug mode and got a dialog. In a normal build it is silent.
		--
		-- It matters more now that units on the map can be EDITED: with two
		-- units sharing an id, JA3SE_FindUnit returns whichever it meets first
		-- and the edit lands on the wrong body.
		--
		-- Checking that the unit is merely PRESENT was never enough. So: look
		-- for the clash, undo our own placement, and say so. Undoing rather
		-- than reporting-and-leaving is deliberate - a refusal that leaves the
		-- damage in place is not a refusal.
		local placed = units[id]
		local sid = type(placed) == "table" and placed.session_id
		if sid then
			local clash
			for k, u in pairs(units) do
				if k ~= id and type(u) == "table" and u.session_id == sid then
					clash = k
					break
				end
			end
			if clash then
				Despawn(id)
				LastMercs = ""
				return "error: the game gave " .. tostring(id)
					.. " the same session id as " .. tostring(clash)
					.. " (" .. tostring(sid) .. "). Two units sharing an id "
					.. "means an edit can land on the wrong one, so the "
					.. "placement was undone. Try again - it usually happens "
					.. "when several are placed in quick succession."
			end
		end

		LastMercs = ""
		return "ok: placed " .. MercName(units[id], id)
	end
	-- Equipping a unit that is standing on the map, rather than one on the
	-- payroll. Jager Allianz's report, 2026-09-05: "when I spawn in another
	-- merc I cannot edit their inventory ... so this is not practical."
	--
	-- The extender supplies both calls and does every check itself before it
	-- writes anything - the unit exists, the item exists, the unit has that
	-- slot, and the engine agrees there is room. So this deliberately does NOT
	-- re-check any of that: two copies of a rule drift apart, and theirs is
	-- next to the data. All this does is refuse to call a function that is not
	-- there, and pass the refusal back verbatim.
	if op == "giveitem" or op == "takeitem" then
		local fn = rawget(_G, op == "giveitem" and "JA3SE_GiveItem"
		                                       or "JA3SE_TakeItem")
		-- nil is the honest test: the extender is not installed, or is too
		-- old. Insisting on type()=="function" was narrower than the truth -
		-- anything callable will do, and a table with __call would have been
		-- refused for no reason.
		if fn == nil then
			return "error: this needs the script extender, and a version new "
				.. "enough to have " .. (op == "giveitem" and "JA3SE_GiveItem"
				                                          or "JA3SE_TakeItem")
				.. ". Install or update it from the Live link page."
		end
		local a = Split(cmd.value)
		-- A session id is NOT a number. The extender's own sector file header
		-- says `session_id<TAB>unit_id<TAB>...` and a hired merc's is the
		-- string "IMP_male_02"; g_Units is keyed by that string. tonumber()
		-- turned every hired merc into nil, so this refused before the game
		-- was ever asked - which is why equipping a merc in the sector has
		-- never worked. Measured: nine attempts, nine identical refusals.
		--
		-- Units the engine spawns DO get numeric ids, and "101" as a string
		-- will not match a key of 101, so convert only when the whole id is a
		-- number and otherwise pass it through untouched.
		local sid = a[1]
		if sid ~= nil and tonumber(sid) ~= nil then sid = tonumber(sid) end
		local slot, item = a[2], a[3]
		if sid == nil or sid == "" or not slot or slot == ""
				or not item or item == "" then
			return "error: need a session id, a slot and an item"
		end
		local ok, msg
		if op == "giveitem" then
			local opts = {}
			if tonumber(a[4]) then opts.condition = tonumber(a[4]) end
			if tonumber(a[5]) then opts.amount = tonumber(a[5]) end
			ok, msg = fn(sid, slot, item, opts)
		else
			ok, msg = fn(sid, slot, item)
		end
		-- Their calls return (ok, message). Hand the message straight through:
		-- it names the slots a unit actually has when the slot is wrong, which
		-- is the thing a person needs and is not ours to paraphrase.
		if ok then
			return "ok: " .. tostring(msg or "done")
		end
		return "error: " .. tostring(msg or "the game refused it")
	end
	-- Putting supplies in the shared squad bag.
	--
	-- Jager Allianz, 2026-09-07: "you gave this SPAM CRAFTING SUPPLIES section
	-- such a prominent place but I am not sure if/how it works (or why it
	-- doesn't)". Two reasons, and he had already worked out the second one
	-- himself.
	--
	-- 1. There was no live op for it at all. The editor queued the intent, the
	--    dispatcher had nothing to map it to, and it fell through to the SAVE
	--    edit queue while the user was editing a running game. A prominent
	--    button that quietly did nothing.
	--
	-- 2. The bag only takes one class of item. From the game's own
	--    SquadBag.lua: AddItemsToSquadBag skips anything that is not
	--    IsKindOf("SquadBagItem"). ResourceItem inherits from it - Parts and
	--    Meds - and MiscItem does not, so Microchip, OpticalLens and
	--    FineSteelPipe can never go in. His words: "those are not items that
	--    the vanilla squadbag can take, except PARTS should be there hmm".
	--
	-- So this refuses by NAME rather than failing quietly, because "the squad
	-- bag cannot hold a lens" is a fact the person can act on and a silent
	-- no-op is not.
	if op == "bagadd" then
		local a = Split(cmd.value)
		local id, count = a[1], tonumber(a[2]) or 1
		if not id or id == "" then
			return "error: need an item id"
		end
		local squads = rawget(_G, "GetPlayerMercSquads")
		local squad = squads and squads()[1]
		if not squad then
			return "error: no squad to put supplies in - load a game with a "
				.. "squad in it first"
		end
		local place = rawget(_G, "PlaceInventoryItem")
		local add = rawget(_G, "AddItemsToSquadBag")
		if not place or not add then
			return "error: this build of the game does not expose the squad bag"
		end
		local item = place(id)
		if not item then
			return "error: no such item: " .. tostring(id)
		end
		if not (item.IsKindOf and item:IsKindOf("SquadBagItem")) then
			if rawget(_G, "DoneObject") then DoneObject(item) end
			return "error: the squad bag only holds supplies like Parts and "
				.. "Meds. " .. tostring(id) .. " is not one, so it has to go "
				.. "to a merc instead."
		end
		-- Respect the game's own cap rather than writing past it. MaxStacks is
		-- what produced the 10,000/500 Jager screenshotted.
		if item.MaxStacks and count > item.MaxStacks then
			count = item.MaxStacks
		end
		item.Amount = count
		add(squad.UniqueId or squad.unique_id or squad.id, { item })
		LastMercs = ""
		return "ok: put " .. tostring(count) .. " " .. tostring(id)
			.. " in the squad bag"
	end
	if op == "despawn" then
		local a = Split(cmd.value)
		local id = a[1]
		if not id or id == "" then
			return "error: need a merc id"
		end
		local units = rawget(_G, "g_Units")
		if not (units and units[id]) then
			return "ok: " .. tostring(id) .. " was not on the map"
		end
		local name = MercName(units[id], id)
		Despawn(id)
		LastMercs = ""
		return "ok: removed " .. name
	end
	if op == "spawnenemy" then
		local a = Split(cmd.value)
		if not rawget(_G, "CheatSpawnEnemy") then
			return "error: this build cannot place enemies"
		end
		-- They are dropped at the middle of the screen, so there has to be a
		-- sector loaded to drop them into. Saying so beats a silent no-op.
		if not rawget(_G, "g_Units") then
			return "error: no sector is loaded to place an enemy in"
		end
		-- Split drops empty fields, so "|3" arrives as one field, not two:
		-- taking a[1] as the type would try to spawn an enemy called "3" and
		-- place exactly one of it. Decide by what the field IS, not where.
		local wanted, how_many = nil, nil
		if #a == 1 then
			if tonumber(a[1]) then
				how_many = tonumber(a[1])
			elseif a[1] ~= "" then
				wanted = a[1]
			end
		else
			if a[1] and a[1] ~= "" then wanted = a[1] end
			how_many = tonumber(a[2])
		end
		how_many = how_many or 1
		if how_many < 1 then how_many = 1 end
		if how_many > 20 then how_many = 20 end
		local made = 0
		for i = 1, how_many do
			if pcall(CheatSpawnEnemy, wanted) then
				made = made + 1
			end
		end
		if made == 0 then
			return "error: the game refused to place that enemy"
		end
		LastMercs = ""
		return "ok: placed " .. tostring(made) .. " x "
			.. tostring(wanted or "LegionRaider")
	end
	if op == "perkon" or op == "perkoff" then
		local a = Split(cmd.value)
		if #a < 2 then
			return "error: need a merc and a perk"
		end
		local defs = rawget(_G, "CharacterEffectDefs")
		if defs and not defs[a[2]] then
			return "error: no perk called " .. tostring(a[2])
		end
		local objs = Targets(a[1])
		if #objs == 0 then
			return "error: no merc called " .. tostring(a[1])
		end
		for i = 1, #objs do
			local o = objs[i]
			if op == "perkon" then
				if o.AddStatusEffect then o:AddStatusEffect(a[2]) end
			else
				if o.RemoveStatusEffect then o:RemoveStatusEffect(a[2]) end
			end
			if rawget(_G, "ObjModified") then
				ObjModified(o)
			end
		end
		LastMercs = ""
		return "ok: " .. (op == "perkon" and "gave " or "removed ") .. tostring(a[2])
	end
	if op == "modon" or op == "modoff" then
		local id = cmd.value
		if not id or id == "" then
			return "error: no mod given"
		end
		if not rawget(_G, "TurnModOn") then
			return "error: this build cannot switch mods"
		end
		-- second argument keeps the game's own mod preset in step, so the
		-- in-game Mod Manager shows the same thing the editor does
		if op == "modon" then
			TurnModOn(id, true)
		else
			TurnModOff(id, true)
		end
		if rawget(_G, "SaveAccountStorage") then
			SaveAccountStorage()
		end
		LastMods = ""
		return "ok: " .. (op == "modon" and "enabled " or "disabled ") .. id
	end
	if op == "modreload" then
		if not rawget(_G, "ModsReloadItems") then
			return "error: this build cannot reload mods"
		end
		-- ModsReloadItems asserts it is on a real time thread; the poll loop
		-- is one, so this is safe to call from here.
		ModsReloadItems(nil, true)
		LastMods = ""
		return "ok: mods reloaded"
	end
	if op == "modlist" then
		LastMods = ""
		return "ok: mod list refreshed"
	end
	-- SCREENSHOT. Proof by photograph, for the times a save file says one
	-- thing and the screen says another.
	--
	-- interface = TRUE. Yoda's map shots pass false because he is
	-- photographing terrain and does not want the HUD over it. Everything we
	-- want to see - the backpack grid, an item that did or did not appear -
	-- is drawn by the interface, so switching it off would photograph the
	-- room behind the panel.
	--
	-- Threaded, because WaitCaptureScreenshot yields and the dispatcher
	-- cannot. That means this op returns BEFORE the picture exists, so it
	-- reports what it started and the thread writes the outcome to
	-- AppData/nemesis-shot.txt. Claiming a success we had not watched would
	-- be the same fault as a button that says Done and does nothing.
	if op == "shot" then
		local cap = rawget(_G, "WaitCaptureScreenshot")
		local th = rawget(_G, "CreateRealTimeThread")
		local slp = rawget(_G, "Sleep")
		if not cap then
			return "error: this build of the game does not expose "
				.. "WaitCaptureScreenshot, so it cannot photograph itself"
		end
		if not th or not slp then
			return "error: no thread available to take a screenshot in"
		end
		local a = Split(cmd.value)
		local name = tostring(a[1] or "")
		name = string.gsub(name, "[^%w%-_]", "")
		if name == "" then name = "shot" end
		-- A pause so the panel has actually redrawn after whatever the editor
		-- just did. Caller can raise it; anything unreadable falls back.
		local wait = tonumber(a[2]) or 400
		if wait < 0 then wait = 0 end
		if wait > 10000 then wait = 10000 end
		local path = "AppData/nemesis-" .. name .. ".png"
		th(function()
			slp(wait)
			local okc, err = pcall(cap, path, { interface = true })
			local out
			if not okc then
				out = "error raised: " .. tostring(err)
			elseif err then
				out = "error: " .. tostring(err)
			else
				out = "ok: " .. path
			end
			pcall(AsyncStringToFile, "AppData/nemesis-shot.txt",
				out .. NL .. "name=" .. name .. NL .. "waited=" .. tostring(wait) .. NL)
		end)
		return "ok: capturing " .. path .. " in " .. tostring(wait)
			.. "ms - the outcome goes to nemesis-shot.txt"
	end
	-- ATTACHMENTS. A scope is not an item and never can be given: it is a
	-- weapon COMPONENT, so this is the only way to build a gun live.
	--   attach  merc|weapon slot|component slot|component
	--   attach  IMP_male_02|Handheld A|Scope|LROptics
	--
	-- Read back afterwards. SetWeaponComponent can decline - a scope that does
	-- not fit that weapon - and reporting the request instead of the result is
	-- exactly how `give` came to say ok for a rifle that never arrived.
	if op == "attach" then
		local a = Split(cmd.value)
		local mid, wslot, cslot, comp = a[1], a[2], a[3], a[4]
		if not mid or not wslot or wslot == "" or not cslot or cslot == ""
				or not comp or comp == "" then
			return "error: need a merc, a weapon slot, a component slot "
				.. "and a component"
		end
		local units = rawget(_G, "g_Units")
		local ud = rawget(_G, "gv_UnitData")
		local o = (units and units[mid]) or (ud and ud[mid])
		if not o then
			return "error: no merc called " .. tostring(mid)
		end
		if not o.GetItemInSlot then
			return "error: that merc has no inventory right now"
		end
		local okget, weapon = pcall(o.GetItemInSlot, o, wslot)
		if not okget or not weapon then
			return "error: nothing in " .. tostring(wslot)
		end
		if not weapon.SetWeaponComponent then
			return "error: " .. tostring(weapon.class or "that")
				.. " does not take attachments"
		end
		local okset, err = pcall(weapon.SetWeaponComponent, weapon, cslot, comp)
		if not okset then
			return "error: the game refused that attachment: " .. tostring(err)
		end
		-- Did it actually land? components is the weapon's own table.
		local now = weapon.components and weapon.components[cslot]
		if now ~= comp then
			return "error: " .. tostring(comp) .. " did not go into "
				.. tostring(cslot) .. " - it now holds "
				.. tostring(now or "nothing") .. ". Wrong slot, or that "
				.. "component does not fit this weapon."
		end
		return "ok: " .. tostring(comp) .. " fitted to "
			.. tostring(weapon.class or wslot) .. " (" .. tostring(cslot) .. ")"
	end
	-- Show a given merc in the inventory panel, so a screenshot can be aimed
	-- without anybody having to click.
	if op == "select" then
		local mid = tostring(cmd.value or "")
		if mid == "" then return "error: need a merc" end
		local units = rawget(_G, "g_Units")
		local ud = rawget(_G, "gv_UnitData")
		local o = (units and units[mid]) or (ud and ud[mid])
		if not o then
			return "error: no merc called " .. tostring(mid)
		end
		local getdlg = rawget(_G, "GetMercInventoryDlg")
		local dlg = getdlg and getdlg()
		if not dlg or not dlg.GetContext then
			return "error: the inventory screen is not open"
		end
		local ctx = dlg:GetContext()
		if not ctx then
			return "error: the inventory screen has no context"
		end
		ctx.unit = o
		local re = rawget(_G, "InventoryUIRespawn")
		if re then pcall(re) end
		return "ok: showing " .. MercName(o, mid)
	end
	-- The weapon specs window. This is the screen that answered whether a
	-- force-fitted component actually does anything - it lists the effects the
	-- engine has applied, which is a far better instrument than a firefight.
	--   inspect  merc|weapon slot     open it on that weapon
	--   inspect  close                shut it
	if op == "inspect" then
		local a = Split(cmd.value)
		local getd = rawget(_G, "GetDialog")
		local closed = rawget(_G, "CloseDialog")
		if tostring(a[1] or "") == "close" then
			if not closed then
				return "error: this build cannot close dialogs"
			end
			pcall(closed, "ModifyWeaponDlg")
			if getd and getd("ModifyWeaponDlg") then
				return "error: the specs window would not close"
			end
			return "ok: specs window closed"
		end
		local mid, wslot = a[1], a[2]
		if not mid or mid == "" or not wslot or wslot == "" then
			return "error: need a merc and a weapon slot, or the word close"
		end
		local opend = rawget(_G, "OpenDialog")
		if not opend then
			return "error: this build cannot open dialogs"
		end
		local units = rawget(_G, "g_Units")
		local ud = rawget(_G, "gv_UnitData")
		local o = (units and units[mid]) or (ud and ud[mid])
		if not o then
			return "error: no merc called " .. tostring(mid)
		end
		if not o.GetItemInSlot then
			return "error: that merc has no inventory right now"
		end
		local okget, weapon = pcall(o.GetItemInSlot, o, wslot)
		if not okget or not weapon then
			return "error: nothing in " .. tostring(wslot)
		end
		-- The window wants where the item sits, not just the item.
		local pos
		if o.GetItemPackedPos then
			local okp, p = pcall(o.GetItemPackedPos, o, weapon)
			if okp then pos = p end
		end
		local okopen, err = pcall(opend, "ModifyWeaponDlg", nil,
			{ weapon = weapon, slot = pos, owner = o })
		if not okopen then
			return "error: the game raised opening the specs window: "
				.. tostring(err)
		end
		-- Did a window actually appear? Reporting the request instead of the
		-- result is the fault that cost two hours this morning.
		if getd and not getd("ModifyWeaponDlg") then
			return "error: the specs window did not open for "
				.. tostring(weapon.class or wslot)
		end
		return "ok: showing " .. tostring(weapon.class or wslot)
			.. " in the specs window"
	end
	return "error: unknown command " .. tostring(op)
end

CreateRealTimeThread(function()
	WaitMsg("EngineStarted")
	say("live link running")
	while true do
		local ok, err = pcall(function()
			local e, text = AsyncFileToString(CMD)
			if not e and text then
				local cmd = ParseKV(text)
				local seq = tonumber(cmd.seq or "")
				if seq and seq ~= LastSeq then
					LastSeq = seq
					if Primed then
						LastResult = Apply(cmd)
						say("seq " .. tostring(seq) .. " -> " .. LastResult)
					else
						-- The command file holds the LATEST command, not a
						-- queue. On startup we adopt whatever is sitting there
						-- without acting on it - otherwise every launch would
						-- silently replay the last thing the editor did.
						LastResult = "idle"
					end
				end
			end
			-- outside the file check on purpose: with no command file yet we
			-- are still primed, so the first command the editor ever sends
			-- is acted on rather than swallowed as startup leftovers
			Primed = true
			Ticks = Ticks + 1
			-- One pcall per writer, not one around all three. They shared
			-- one, so an error in WriteMercs stopped the merc file updating
			-- while the state file carried on looking perfectly healthy -
			-- the editor showed a live connection and a merc list frozen
			-- fifteen minutes in the past, with nothing anywhere saying why.
			local w1, e1 = pcall(WriteState)
			if not w1 then say("WriteState: " .. tostring(e1)) end
			local w2, e2 = pcall(WriteMods)
			if not w2 then say("WriteMods: " .. tostring(e2)) end
			local w3, e3 = pcall(WriteMercs)
			if not w3 then say("WriteMercs: " .. tostring(e3)) end
		end)
		if not ok then
			say("tick error: " .. tostring(err))
		end
		Sleep(POLL)
	end
end)

return {
	name = "NemesisLive",
	enabled = true,
	required_lua_revision = 227000,
}
"""

FILES = {
	'autorun.lua': AUTORUN.lstrip().encode('utf-8'),
	'revisions.lua': REVISIONS.encode('utf-8'),
	'console.lua': CONSOLE.encode('utf-8'),
}


def autorun_source(portrait_lua=None):
	"""AUTORUN plus the custom-portrait code, which has to come before the
	closing `return {...}` that describes the DLC."""
	if portrait_lua is None:
		try:
			import portraits
			portrait_lua = portraits.dlc_lua()
		except Exception:                                    # noqa: BLE001
			portrait_lua = ''
	if not portrait_lua:
		return AUTORUN
	body, sep, tail = AUTORUN.rpartition('\nreturn {')
	return body + '\n' + portrait_lua + sep + tail


def pack():
	"""The DLC, as bytes. Built fresh every time. Custom portraits travel in
	it under UI/ - the editor writes no mod for them."""
	source = autorun_source()
	check_lua(source)
	files = dict(FILES)
	files['autorun.lua'] = source.lstrip().encode('utf-8')
	try:
		import portraits
		files.update(portraits.dlc_files())
	except Exception:                                        # noqa: BLE001
		pass
	return hpk_write.pack_bytes(files, compress=False)


def check_lua(source=None):
	"""Refuse to pack Lua that will not compile.

	A syntax error here does not produce an error message anywhere a person
	will see it - the game hangs on the loading screen forever, and the log
	only flushes on a clean exit, so there is nothing to read afterwards
	either. Hours have gone into diagnosing that shape of failure.

	`lupa` is a build-time convenience, not a dependency: without it this is
	skipped rather than failing, exactly like Pillow in the icon builder. The
	editor ships one exe and this never travels inside it.

	Returns (checked, message) so a caller can say which of the two happened.
	"""
	src = AUTORUN if source is None else source

	# The shipped exe never syntax checks. The Lua is baked in at build time
	# and was already checked THEN, on the machine that built it, so redoing
	# it on a user's machine cannot catch anything new - it can only fail.
	# And it did: PyInstaller collected lupa's Python modules but not its
	# runtime, so `lupa.LuaRuntime()` raised FileNotFoundError from inside
	# _MEI...\Lupa. That is an OSError, so install()'s disk-write handler
	# caught it and told the user "could not write the file" about a file it
	# had never tried to write.
	if getattr(sys, 'frozen', False):
		return False, 'skipped in the shipped build - checked when it was built'

	# Catch Exception, not ImportError. The import succeeding says nothing
	# about the runtime starting, and anything that goes wrong HERE is a
	# problem with the checker, never with the Lua. A convenience must not be
	# able to block the thing it is checking.
	try:
		import lupa
		runtime = lupa.LuaRuntime()
	except Exception as exc:                                     # noqa: BLE001
		return False, 'autorun.lua was NOT syntax checked (%s)' % exc
	compiles = runtime.eval(
		'function(s) local f, e = load(s, "@autorun.lua") '
		'if f then return true, "" else return false, tostring(e) end end')
	ok, err = compiles(src)
	if not ok:
		raise SyntaxError('autorun.lua will not compile, so it would hang the '
						  'game on the loading screen: %s' % err)
	return True, 'autorun.lua compiles'


def _resolve(game_dir):
	"""None means 'find it'. Anything else is taken literally, including a
	blank string - which means 'no game', not 'go and find one'. Falling back
	silently once wrote a DLC into a real install during a test."""
	if game_dir is None:
		return gamepaths.game_dir()
	return game_dir or None


def dlc_folder(game_dir=None):
	game = _resolve(game_dir)
	return os.path.join(game, 'DLC') if game else None


def dest_path(game_dir=None):
	folder = dlc_folder(game_dir)
	return os.path.join(folder, HPK) if folder else None


def state(game_dir=None):
	"""(installed, up_to_date, path) - up_to_date compares the actual bytes,
	so a newer editor notices its own DLC is out of date."""
	path = dest_path(game_dir)
	if not path or not os.path.exists(path):
		return False, False, path
	try:
		with open(path, 'rb') as f:
			current = f.read()
	except OSError:
		return True, False, path
	return True, current == pack(), path


def _game_is_running():
	"""Is Jagged Alliance 3 up? Best effort - never raises."""
	try:
		import subprocess
		out = subprocess.run(['tasklist', '/fi', 'IMAGENAME eq ja3.exe'],
		                     capture_output=True, text=True, timeout=5,
		                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
		return 'ja3.exe' in (out.stdout or '').lower()
	except Exception:                                        # noqa: BLE001
		return False


def install(game_dir=None):
	"""Write the DLC into the game folder. Returns (ok, message, path)."""
	game = _resolve(game_dir)
	if not game:
		return (False, 'I could not find your Jagged Alliance 3 folder. '
		        'Use the Folder button at the top to point the editor at it, '
		        'then try again.', None)
	# Build BEFORE opening the file. pack() used to be called inside the write
	# try-block, so any failure while building - lupa, a bad Lua edit, anything
	# raising OSError - surfaced as "could not write the file" and sent the
	# reader hunting a permissions problem that did not exist.
	try:
		data = pack()
	except Exception as exc:                                     # noqa: BLE001
		return False, 'Could not build the add-on: %s' % exc, None

	folder = os.path.join(game, 'DLC')
	path = os.path.join(folder, HPK)
	# Written beside it and swapped in, so a write that is cut short leaves the
	# old add-on in place instead of a truncated one - which has happened: a
	# zero-byte pack the game then tried to load.
	tmp = path + '.part'
	try:
		if not os.path.isdir(folder):
			os.makedirs(folder)
		with open(tmp, 'wb') as f:
			f.write(data)
		# A freshly written file is often held for a moment by a virus
		# scanner or the search indexer; a short retry gets past it.
		last = None
		for _attempt in range(5):
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
		# The game holds this file open while it runs, and that is far and away
		# the commonest reason this fails - the message used to blame Program
		# Files and send people off to run as administrator, which fixes
		# nothing when the real answer is "close the game". Say the likely
		# cause first, and check for it rather than guessing.
		if _game_is_running():
			return (False, 'Jagged Alliance 3 is running, and it holds this '
			        'file open while it does. Close the game, press Update '
			        'again, then start it - the new add-on only loads when the '
			        'game boots anyway.', path)
		return (False, 'Windows would not let me write into ' + folder + '. '
		        'That usually means the game sits in Program Files - either run '
		        'the editor as administrator once, or copy the file in by hand.',
		        path)
	except OSError as exc:
		return False, 'Could not write the file: %s' % exc, path
	finally:
		if os.path.exists(tmp):
			try:
				os.remove(tmp)
			except OSError:
				pass
	return (True, 'Installed. Start the game and the link switches itself on.',
	        path)


def appdata_dir(appdata=None):
	return appdata or gamepaths.user_data_dir()


def unlisted_appdata(source=None):
	"""AppData files the Lua writes that OWNED_APPDATA does not name."""
	src = AUTORUN if source is None else source
	# A literal followed by `..` is only the start of a name built at run
	# time; the pattern has to cover it, since no exact name can.
	prefixes = set(re.findall(r'"AppData/([A-Za-z0-9._-]+)"\s*\.\.', src))
	written = set(re.findall(r'"AppData/([A-Za-z0-9._-]+)"', src)) - prefixes
	bad = [n for n in written if n not in OWNED_APPDATA]
	bad += [n + '...' for n in prefixes if not OWNED_PATTERN.startswith(n)]
	return sorted(bad)


def owned_files(game_dir=None, appdata=None):
	"""[(path, exists, what)] - everything the link owns, present or not."""
	out = []
	dlc = dest_path(game_dir)
	if dlc:
		out.append((dlc, os.path.exists(dlc), 'the add-on itself'))
		if os.path.exists(dlc + '.part'):
			out.append((dlc + '.part', True, 'an install that was cut short'))
	base = appdata_dir(appdata)
	for name in OWNED_APPDATA:
		p = os.path.join(base, name)
		out.append((p, os.path.exists(p), 'how it talks to the editor'))
	try:
		shots = sorted(n for n in os.listdir(base)
		               if fnmatch.fnmatch(n.lower(), OWNED_PATTERN))
	except OSError:
		shots = []
	for name in shots:
		out.append((os.path.join(base, name), True, 'a screenshot it took'))
	return out


def uninstall(game_dir=None, purge=True, appdata=None):
	"""Delete the DLC and, with purge, every file it wrote.
	Returns (ok, message, path)."""
	path = dest_path(game_dir)
	if not path:
		return False, 'I could not find your game folder.', None
	was_there = os.path.exists(path)
	if was_there:
		try:
			os.remove(path)
		except OSError as exc:
			if _game_is_running():
				return (False, 'Jagged Alliance 3 is running and holds the '
				        'add-on open. Close the game, then press Remove again.',
				        path)
			return False, 'Could not remove it: %s' % exc, path

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
	if extra:
		msg += ' Also deleted %d file%s it had written.' % (
			len(extra), '' if len(extra) == 1 else 's')
	if failed:
		msg += (' %d could not be deleted (something has them open): %s'
		        % (len(failed), ', '.join(os.path.basename(f) for f in failed)))
	return True, msg, path


def build(out_dir='dist'):
	"""Write a copy to disk - for inspecting the pack, not used when shipping."""
	if not os.path.isdir(out_dir):
		os.makedirs(out_dir)
	out = os.path.join(out_dir, HPK)
	with open(out, 'wb') as f:
		f.write(pack())
	return out


if __name__ == '__main__':
	arg = sys.argv[1] if len(sys.argv) > 1 else ''
	if arg == '--install-copy':
		folder = os.path.join(TEST_COPY, 'DLC')
		if not os.path.isdir(folder):
			os.makedirs(folder)
		dest = os.path.join(folder, HPK)
		with open(dest, 'wb') as f:
			f.write(pack())
		print('installed: %s (%d bytes)' % (dest, os.path.getsize(dest)))
	elif arg in ('--install', '--install-game'):
		ok, msg, path = install()
		print(('ok: ' if ok else 'failed: ') + msg)
		if path:
			print('   ' + path)
	elif arg == '--remove':
		for gd in (None, TEST_COPY):
			ok, msg, path = uninstall(gd)
			if path:
				print('%-6s %s' % ('ok' if ok else 'FAIL', path + ' - ' + msg))
	elif arg == '--status':
		installed, fresh, path = state()
		print('installed=%s up_to_date=%s' % (installed, fresh))
		print('path: %s' % path)
	else:
		out = build()
		print('built: %s (%d bytes)' % (out, os.path.getsize(out)))
