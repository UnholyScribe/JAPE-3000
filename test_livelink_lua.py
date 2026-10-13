"""Runs the DLC's Lua for real, with the game stubbed out.

    python test_livelink_lua.py

Needs `lupa` (pip install lupa). Without it this skips rather than fails, the
same way the syntax check in build_livelink does - it is a development tool,
never part of the shipped exe.

**Why this exists.** The live link's failure mode is silence. A command that
does nothing still returns, still writes a result, and the editor still says
"Done" - so a broken op looks exactly like a working one until somebody notices
their test did not actually change anything. The placing ops are the worst
case: `CheatAddMerc` returns early when the merc is already on the map, so
"place again" would quietly do nothing on every use after the first.

That is not a hypothetical - it is why `Despawn` exists, and this is the test
that keeps it there.

The chunk's last statement is `return { name = "NemesisLive", ... }`. Swapping
that for `return Apply` hands the test the dispatcher, because the appended
line sits in the same scope as the local it is reaching for.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import build_livelink                                          # noqa: E402

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print('%s %s%s' % ('  ok  ' if cond else '  FAIL', name,
                       ('  - ' + detail) if detail and not cond else ''))
    return bool(cond)


RETURNS_DLC = 'return {\n\tname = "NemesisLive",'


def make_apply():
    """A fresh Lua world with the game faked, and the dispatcher out of it."""
    import lupa
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    src = build_livelink.AUTORUN
    assert RETURNS_DLC in src, 'the chunk stopped ending the way this test expects'
    src = src.replace(RETURNS_DLC, 'return Apply\n--[[', 1) + ']]'

    g = lua.globals()
    calls = {'add': [], 'enemy': [], 'removed': []}

    g.g_Units = lua.eval('{}')
    g.gv_UnitData = lua.eval('{}')

    def cheat_add_merc(mid):
        # the real one bails out when the merc is already there
        if g.g_Units[mid] is not None:
            return
        calls['add'].append(mid)
        # Every unit the engine places carries a session_id, and the DLC now
        # checks it is unique. calls['next_sid'] lets a test force the clash
        # the real engine produces when several are placed in quick succession.
        sid = calls.get('next_sid')
        if sid is None:
            calls['sid_seq'] = calls.get('sid_seq', 100) + 1
            sid = calls['sid_seq']
        calls['next_sid'] = None
        g.g_Units[mid] = lua.eval('{Nick = "%s", session_id = %d}' % (mid, sid))
        g.gv_UnitData[mid] = lua.eval('{HireStatus = "Hired"}')

    def cheat_spawn_enemy(eid):
        calls['enemy'].append(eid)

    def local_remove(mid):
        calls['removed'].append(mid)
        g.g_Units[mid] = None

    # The real CheatAddMerc only ADDS when the player already has a merc
    # squad; with none it falls through to DbgStartExploration and resets the
    # scenario. The DLC refuses in that case, so the stub world has to have a
    # squad for the normal path to be reachable at all.
    squads = lua.eval('{ {Side = "player1"} }')
    g.GetPlayerMercSquads = lambda: squads

    g.CheatAddMerc = cheat_add_merc
    g.CheatSpawnEnemy = cheat_spawn_enemy
    g.LocalRemoveMercFromSquad = local_remove
    # the chunk starts its poll loop at the top level; keep it from running
    g.CreateRealTimeThread = lambda fn: None
    g.Sleep = lambda ms: None
    g.WaitMsg = lambda *a: None

    chunk = lua.eval('function(s) return load(s, "@autorun.lua") end')(src)
    assert chunk is not None, 'the stubbed chunk would not compile'
    apply_fn = chunk()
    return lua, apply_fn, g, calls


def cmd(lua, op, value=''):
    t = lua.eval('{}')
    t.op = op
    t.value = value
    return t


def make_writestate():
    """A fresh Lua world handing back WriteState instead of the dispatcher.

    The bag reader is a local, and testing it through the function that
    actually ships beats exposing it. AsyncStringToFile is captured so the test
    reads the same text the editor would.
    """
    import lupa
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    src = build_livelink.AUTORUN
    assert RETURNS_DLC in src, 'the chunk stopped ending the way this test expects'
    src = src.replace(RETURNS_DLC, 'return WriteState\n--[[', 1) + ']]'

    g = lua.globals()
    written = {}
    g.AsyncStringToFile = lambda path, text: written.__setitem__(str(path), str(text))
    g.AsyncFileToString = lambda path: None
    g.AsyncCreatePath = lambda path: None
    g.Game = lua.eval('{CampaignTime = 1, Money = 13271}')
    # The chunk starts a real-time thread while it loads, so these have to be
    # in place before it runs at all - same set make_apply needs.
    g.CreateRealTimeThread = lambda fn: None
    g.Sleep = lambda ms: None
    g.WaitMsg = lambda *a: None

    chunk = lua.eval('function(s) return load(s, "@autorun.lua") end')(src)
    assert chunk is not None, 'the stubbed chunk would not compile'
    return lua, chunk(), g, written


def test_bag_reader():
    """The bag the GAME holds, reported so the panel stops reading the save."""
    lua, write_state, g, written = make_writestate()

    def state_text():
        written.clear()
        write_state()
        for path, text in written.items():
            if 'nemesis-state' in path:
                return text
        return ''

    def bag_line(text):
        for line in text.split(chr(10)):
            if line.startswith('bag='):
                return line[4:]
        return None

    # No accessor at all - an older game, or one that moved the function.
    g.GetPlayerMercSquads = None
    g.GetSquadBag = None
    line = bag_line(state_text())
    check('the bag line is always present, even with nothing to report',
          line is not None, repr(line))
    check('and it is empty rather than absent', line == '', repr(line))

    # A real bag. the tester's actual numbers, so a regression reads as a story
    # rather than as arithmetic.
    g.GetPlayerMercSquads = lambda: lua.eval('{{UniqueId = 58}}')
    g.GetSquadBag = lambda sid: lua.eval(
        '{{class = "Meds", Amount = 58}, {class = "Parts", Amount = 115},'
        ' {class = "_9mm_Basic", Amount = 128}}') if sid == 58 else None
    line = bag_line(state_text())
    check('the bag is reported with ids and amounts',
          line == 'Meds:58,Parts:115,_9mm_Basic:128', repr(line))

    # An item with no class is skipped rather than emitted as "nil:1".
    g.GetSquadBag = lambda sid: lua.eval(
        '{{class = "Parts", Amount = 15}, {Amount = 9}}')
    check('an item with no class is skipped, not written as nil',
          bag_line(state_text()) == 'Parts:15', repr(bag_line(state_text())))

    # An empty bag is a real answer and must not look like "no data".
    g.GetSquadBag = lambda sid: lua.eval('{}')
    check('an empty bag reports empty', bag_line(state_text()) == '',
          repr(bag_line(state_text())))

    # WriteState runs every tick. If the accessor throws, the whole state file
    # stops being written and the editor loses the link, not just the bag.
    def boom(sid):
        raise RuntimeError('the game blew up')
    g.GetSquadBag = boom
    text = state_text()
    check('an accessor that throws does not take the state file with it',
          'money=' in text, repr(text[:120]))
    check('and the bag line is still there, empty', bag_line(text) == '',
          repr(bag_line(text)))


def test_shot():
    """Photographing the game, and the one argument that fails silently.

    `interface` decides whether the HUD is in the picture. Yoda's map walk
    passes FALSE, because he is photographing terrain. Everything we want to
    see - a backpack grid, an item that did or did not appear - is drawn by
    the interface, so ours must be TRUE. Get it backwards and every shot comes
    back beautiful, correct, and useless: a photograph of the room behind the
    panel. Nothing would report an error.

    The op is threaded because WaitCaptureScreenshot yields, so the test's
    thread stub runs the body immediately - otherwise it would assert on a
    capture that had not happened yet and pass for the wrong reason.
    """
    lua, apply_fn, g, calls = make_apply()

    # No capture function at all - an older build, or one that moved it.
    g.WaitCaptureScreenshot = None
    r = apply_fn(cmd(lua, 'shot', 'x'))
    check('no capture function is refused, and says why',
          str(r).startswith('error:') and 'WaitCaptureScreenshot' in str(r), str(r))

    # Now with one. The thread stub RUNS the body, so the capture really happens.
    shots = []
    g.WaitCaptureScreenshot = lambda path, opts: shots.append(
        (str(path), bool(opts.interface))) or None
    g.CreateRealTimeThread = lambda fn: fn()
    g.Sleep = lambda ms: None

    r = apply_fn(cmd(lua, 'shot', 'bag1'))
    check('a screenshot is accepted', str(r).startswith('ok:'), str(r))
    check('exactly one capture was taken', len(shots) == 1, str(shots))
    check('it went to a named path under AppData',
          shots and shots[0][0] == 'AppData/nemesis-bag1.png',
          str(shots))
    check('THE INTERFACE IS IN THE PICTURE',
          shots and shots[0][1] is True,
          'interface=false photographs the room, not the inventory panel')

    # A name that would escape the folder, or break the filename.
    shots.clear()
    apply_fn(cmd(lua, 'shot', '../../etc/passwd'))
    check('a path-traversing name is scrubbed, not obeyed',
          shots and shots[0][0] == 'AppData/nemesis-etcpasswd.png', str(shots))

    shots.clear()
    apply_fn(cmd(lua, 'shot', ''))
    check('an empty name still produces a file', len(shots) == 1, str(shots))

    # The op returns BEFORE the picture exists, so it must not claim success.
    r = apply_fn(cmd(lua, 'shot', 'later'))
    check('the receipt says capturing, not captured',
          'capturing' in str(r) and 'written' not in str(r), str(r))


def test_give_reports_refusals():
    """give must not say ok for an item that never arrived.

    Measured 2026-09-09 in the tester's game: a Gewehr98 into a backpack holding
    eleven items in twelve cells returned "ok: gave 1 x Gewehr98 to Gray Fox".
    The extender's own sector reader then listed eleven items and no rifle. A
    large item needs two free cells and there was one.

    It could not have known. Called without sync_call the engine fires a
    NetSyncEvent and returns nothing - the placement happens later - so the
    receipt was printed before the game had tried. Passing sync_call = true
    does the work inline and hands back MoveItem's verdict, whose contract, in
    the game's own comment, is "string - why the move could not happen": a
    string is a REFUSAL, false is success. Both directions are checked here
    because reading it backwards would call every success an error.
    """
    lua, apply_fn, g, calls = make_apply()
    g.g_Units = lua.eval('{Ivan = {Nick = "Ivan", session_id = 1}}')

    seen = []

    def place(item, amount, unit, drop, sync):
        seen.append((str(item), amount, bool(sync)))
        return False          # MoveItem: false means it went in
    g.PlaceItemInInventoryCheat = place

    r = apply_fn(cmd(lua, 'give', 'Ivan|Knife|1'))
    check('a successful give still reports ok', str(r).startswith('ok:'), str(r))
    check('and it is done inline, so the answer is real',
          seen and seen[0][2] is True,
          'called without sync_call - the verdict would come back empty')

    # Now the refusal. This is the case that used to say ok.
    g.PlaceItemInInventoryCheat = (lambda item, amount, unit, drop, sync:
                                   'no room for that item')
    r = apply_fn(cmd(lua, 'give', 'Ivan|Gewehr98|1'))
    check('A REFUSED ITEM IS REPORTED AS AN ERROR',
          str(r).startswith('error:'), str(r))
    check('and the game\'s own reason is passed through',
          'no room for that item' in str(r), str(r))

    # A raise must not escape either.
    def boom(*a):
        raise RuntimeError('engine blew up')
    g.PlaceItemInInventoryCheat = boom
    r = apply_fn(cmd(lua, 'give', 'Ivan|Knife|1'))
    check('a raise is caught and reported, not swallowed',
          str(r).startswith('error:'), str(r))


def test_session_ids_may_be_strings():
    """A hired merc's session id is a STRING, and tonumber() killed it.

    The extender's sector file header is
    `session_id<TAB>unit_id<TAB>name<TAB>side<TAB>hired`, and Gray Fox's first
    column is "IMP_male_02". g_Units is keyed by that string. The op did
    `tonumber(a[1])` and refused anything that was not a number, so equipping a
    hired merc in the sector could never work - nine attempts in the tester's game,
    nine identical refusals, none of which reached the extender.

    Numbers still have to arrive as numbers: spawned units DO get numeric ids
    and "101" will not match a key of 101.
    """
    lua, apply_fn, g, calls = make_apply()
    got = []
    g.JA3SE_TakeItem = lambda sid, slot, item: (
        got.append((sid, str(slot), str(item))) or True, 'taken')
    g.JA3SE_GiveItem = lambda sid, slot, item, opts: (
        got.append((sid, str(slot), str(item))) or True, 'given')

    r = apply_fn(cmd(lua, 'takeitem', 'IMP_male_02|Inventory|Knife'))
    check('A HIRED MERC\'S STRING SESSION ID IS ACCEPTED',
          not str(r).startswith('error:'), str(r))
    check('and it reaches the game unchanged',
          got and got[0][0] == 'IMP_male_02', str(got))

    got.clear()
    apply_fn(cmd(lua, 'giveitem', '101|Inventory|Knife'))
    check('a numeric id still arrives as a number, not "101"',
          got and got[0][0] == 101, str(got))

    r = apply_fn(cmd(lua, 'takeitem', '|Inventory|Knife'))
    check('an empty id is still refused', str(r).startswith('error:'), str(r))


def test_attach():
    """Fitting a scope, and refusing to claim one that did not fit.

    Attachments are not items - they are weapon components - so `give` can
    never place one and this is the only live route to building a gun. The
    engine call is the game's own, weapon:SetWeaponComponent(slot, component).

    It can decline. So the op reads the component back off the weapon and
    reports what is THERE, not what was asked for. Two hours before this was
    written, `give` reported ok for a rifle that never arrived, because it
    reported the request.
    """
    lua, apply_fn, g, calls = make_apply()

    # A weapon that accepts anything, and records what it was told.
    lua.execute("""
        _G.mkweapon = function(accept)
            local w = { class = "Gewehr98", components = {} }
            w.SetWeaponComponent = function(self, slot, comp)
                if accept then self.components[slot] = comp end
            end
            return w
        end
        _G.mkunit = function(w)
            local u = {}
            u.GetItemInSlot = function(self, slot)
                if slot == "Handheld A" then return w end
                return nil
            end
            return u
        end
    """)
    weapon = lua.eval('mkweapon(true)')
    g.g_Units = lua.eval('{}')
    g.g_Units['Ivan'] = lua.eval('mkunit(...)', weapon) if False else None
    g.gv_UnitData = lua.eval('{}')
    lua.execute('gv_UnitData.Ivan = mkunit(_G.__w)') if False else None

    # simpler: build the unit in Lua with the weapon attached
    lua.execute('_G.__w = mkweapon(true); gv_UnitData.Ivan = mkunit(_G.__w)')

    r = apply_fn(cmd(lua, 'attach', 'Nobody|Handheld A|Scope|LROptics'))
    check('an unknown merc is refused', str(r).startswith('error:'), str(r))

    r = apply_fn(cmd(lua, 'attach', 'Ivan|Handheld B|Scope|LROptics'))
    check('an empty weapon slot is refused, and says so',
          str(r).startswith('error:') and 'Handheld B' in str(r), str(r))

    r = apply_fn(cmd(lua, 'attach', 'Ivan|Handheld A|Scope|LROptics'))
    check('a scope that fits is reported ok', str(r).startswith('ok:'), str(r))
    check('and it really is on the weapon',
          str(lua.eval('_G.__w.components.Scope')) == 'LROptics',
          str(lua.eval('_G.__w.components.Scope')))

    # The case that matters: the call succeeds and the component does NOT land.
    lua.execute('_G.__w2 = mkweapon(false); gv_UnitData.Boris = mkunit(_G.__w2)')
    r = apply_fn(cmd(lua, 'attach', 'Boris|Handheld A|Scope|LROptics'))
    check('A COMPONENT THAT DID NOT LAND IS AN ERROR, NOT AN OK',
          str(r).startswith('error:'), str(r))
    check('and the refusal says what the slot actually holds',
          'nothing' in str(r), str(r))

    r = apply_fn(cmd(lua, 'attach', 'Ivan|Handheld A|Scope'))
    check('a malformed attach is refused before the game is touched',
          str(r).startswith('error:'), str(r))


def test_select():
    """Point the inventory panel at a merc, so a screenshot can be aimed."""
    lua, apply_fn, g, calls = make_apply()
    lua.execute("""
        _G.__ctx = { unit = false }
        _G.__respawned = 0
        _G.GetMercInventoryDlg = function()
            return { GetContext = function(self) return _G.__ctx end }
        end
        _G.InventoryUIRespawn = function() _G.__respawned = _G.__respawned + 1 end
        gv_UnitData = gv_UnitData or {}
        gv_UnitData.Ivan = { Nick = "Ivan" }
    """)

    r = apply_fn(cmd(lua, 'select', 'Nobody'))
    check('selecting a merc who does not exist is refused',
          str(r).startswith('error:'), str(r))

    r = apply_fn(cmd(lua, 'select', 'Ivan'))
    check('selecting a real merc reports ok', str(r).startswith('ok:'), str(r))
    check('the panel was actually pointed at him',
          lua.eval('_G.__ctx.unit ~= false'), 'context.unit was not set')
    check('and the panel was told to redraw',
          lua.eval('_G.__respawned') == 1, str(lua.eval('_G.__respawned')))

    lua.execute('_G.GetMercInventoryDlg = function() return nil end')
    r = apply_fn(cmd(lua, 'select', 'Ivan'))
    check('with the screen shut it says so rather than pretending',
          str(r).startswith('error:') and 'not open' in str(r), str(r))


def main():
    try:
        import lupa                                            # noqa: F401
    except ImportError:
        print('lupa is not installed - skipping (pip install lupa)')
        return 0

    test_bag_reader()
    test_shot()
    test_give_reports_refusals()
    test_session_ids_may_be_strings()
    test_attach()
    test_select()

    lua, apply_fn, g, calls = make_apply()

    # -- placing ---------------------------------------------------------
    r = apply_fn(cmd(lua, 'spawnmerc', 'Ivan'))
    check('placing a merc reports ok', str(r).startswith('ok:'), str(r))
    check('the game was asked to place him', calls['add'] == ['Ivan'], str(calls['add']))
    check('he is on the map', g.g_Units['Ivan'] is not None)

    # -- the silent no-op this test exists for ---------------------------
    r = apply_fn(cmd(lua, 'spawnmerc', 'Ivan'))
    check('placing him twice says he is already there',
          'already on the map' in str(r), str(r))
    check('and does NOT quietly ask the game again',
          calls['add'] == ['Ivan'], str(calls['add']))

    r = apply_fn(cmd(lua, 'spawnmerc', 'Ivan|replace'))
    check('placing again DOES replace him', str(r).startswith('ok: placed'), str(r))
    check('he was taken off the map first', calls['removed'] == ['Ivan'],
          str(calls['removed']))
    check('and put back', calls['add'] == ['Ivan', 'Ivan'], str(calls['add']))

    # -- despawning ------------------------------------------------------
    r = apply_fn(cmd(lua, 'despawn', 'Ivan'))
    check('despawn reports ok', str(r).startswith('ok: removed'), str(r))
    check('he is off the map', g.g_Units['Ivan'] is None)
    check('his campaign record went too', g.gv_UnitData['Ivan'] is None)

    r = apply_fn(cmd(lua, 'despawn', 'Ivan'))
    check('despawning nobody is not an error', str(r).startswith('ok:'), str(r))

    # -- enemies ---------------------------------------------------------
    r = apply_fn(cmd(lua, 'spawnenemy', '|3'))
    check('three enemies means three calls', len(calls['enemy']) == 3,
          str(calls['enemy']))
    check('an empty type means the default', calls['enemy'][0] is None,
          str(calls['enemy'][0]))

    calls['enemy'].clear()
    apply_fn(cmd(lua, 'spawnenemy', 'LegionElite|2'))
    check('a named type is passed through',
          calls['enemy'] == ['LegionElite', 'LegionElite'], str(calls['enemy']))

    calls['enemy'].clear()
    apply_fn(cmd(lua, 'spawnenemy', '|500'))
    check('a silly number is capped', len(calls['enemy']) == 20,
          str(len(calls['enemy'])))

    # -- refusing rather than pretending ---------------------------------
    r = apply_fn(cmd(lua, 'spawnmerc', ''))
    check('placing nobody is refused', str(r).startswith('error:'), str(r))

    # With no player squad the game's own CheatAddMerc would start a debug
    # exploration - new session, teams rebuilt from the map, a GAMETEST squad -
    # which is how "place John Wick" produced a random party with the sides
    # scrambled. It has to refuse, not attempt it.
    before = list(calls['add'])
    g.GetPlayerMercSquads = lambda: lua.eval('{}')
    r = apply_fn(cmd(lua, 'spawnmerc', 'Fox'))
    check('no squad in the sector is refused', str(r).startswith('error:'), str(r))
    check('and the game is never asked', calls['add'] == before, str(calls['add']))
    g.GetPlayerMercSquads = lambda: squads

    g.g_Units = None
    r = apply_fn(cmd(lua, 'spawnenemy', 'LegionRaider|1'))
    check('no sector loaded is refused, not silently dropped',
          str(r).startswith('error:'), str(r))

    # -- the session id collision ----------------------------------------
    #
    # Found by Jager Allianz, 2026-09-05, and traced to CheatAddMerc in this
    # DLC. The engine detects the duplicate in CheckUniqueSessionId, but
    # assert() does not raise in this engine - it logs and carries on - so the
    # unit was added anyway and our pcall returned true. We reported a
    # placement that had already gone wrong. He only saw it because he plays in
    # debug mode; for everyone else it was completely silent.
    #
    # Both directions, because a check nobody has watched refuse is not a check.
    lua, apply_fn, g, calls = make_apply()
    g.GetPlayerMercSquads = lambda: lua.eval('{{1}}')

    r = apply_fn(cmd(lua, 'spawnmerc', 'Ivan'))
    check('a placement with a fresh session id is accepted',
          str(r).startswith('ok'), str(r))
    ivan_sid = g.g_Units['Ivan'].session_id
    check('the placed unit carries a session id', ivan_sid is not None)

    calls['next_sid'] = ivan_sid          # force the engine to repeat itself
    r = apply_fn(cmd(lua, 'spawnmerc', 'Fox'))
    check('a colliding session id is REFUSED, not reported as ok',
          str(r).startswith('error'), str(r))
    check('the refusal names both units and the id',
          'Fox' in str(r) and 'Ivan' in str(r) and str(int(ivan_sid)) in str(r),
          str(r))
    check('and the bad placement was undone, not left on the map',
          g.g_Units['Fox'] is None, 'Fox is still there after a refusal')
    check('the merc who was already there is untouched',
          g.g_Units['Ivan'] is not None)

    # -- equipping a unit standing on the map ----------------------------
    #
    # The extender does every check itself before writing, so this side must
    # NOT re-implement them - two copies of a rule drift apart. What it must do
    # is refuse clearly when the call is absent, and pass the game's own
    # message through untouched when it is there. Both are tested, because the
    # absent case is the one that runs on every machine today.
    lua, apply_fn, g, calls = make_apply()

    r = apply_fn(cmd(lua, 'giveitem', '674318|Handheld A|AK47'))
    check('giving an item without the extender refuses, and says why',
          str(r).startswith('error') and 'script extender' in str(r), str(r))

    seen = {}

    def give(sid, slot, item, opts=None):
        seen['give'] = (sid, slot, item,
                        dict(opts) if opts is not None else None)
        return True, 'gave ' + str(item) + ' to ' + str(sid)

    def take(sid, slot, item):
        seen['take'] = (sid, slot, item)
        return False, "that unit has no slot 'Pocket'; it has: Handheld A, Head"

    g.JA3SE_GiveItem = give
    g.JA3SE_TakeItem = take

    r = apply_fn(cmd(lua, 'giveitem', '674318|Handheld A|AK47|90|2'))
    check('giving reaches the extender with the right arguments',
          seen.get('give', (0,))[0] == 674318
          and seen['give'][1] == 'Handheld A' and seen['give'][2] == 'AK47',
          str(seen.get('give')))
    check('condition and amount are passed as options',
          (seen['give'][3] or {}).get('condition') == 90
          and (seen['give'][3] or {}).get('amount') == 2,
          str(seen['give'][3]))
    check('a successful give reports ok', str(r).startswith('ok'), str(r))

    r = apply_fn(cmd(lua, 'takeitem', '674318|Pocket|AK47'))
    check('a refusal comes back as an error', str(r).startswith('error'), str(r))
    check("and the game's own wording survives - it lists the real slots",
          'Handheld A, Head' in str(r), str(r))

    r = apply_fn(cmd(lua, 'giveitem', '674318|Handheld A'))
    check('a malformed command is refused before the game is called',
          str(r).startswith('error') and 'session id' in str(r), str(r))

    # -- healing somebody who is DOWNED ----------------------------------
    #
    # the tester, 2026-09-08: "once they're down... even if I fully heal them, they
    # don't get back up." Being downed is a status effect driving a command
    # state, not a health value, so setting HitPoints left a merc at full
    # health lying on the floor.
    #
    # The fix calls the game's own Unit:DownedRally. This checks we call it
    # when someone is down, do NOT call it when they are not, and still report
    # honestly - "patched up 5 mercs" while two of them stood up leaves out the
    # part the person was waiting for.
    lua, apply_fn, g, calls = make_apply()
    rallied = []

    def unit(nick, down):
        u = lua.eval('{}')
        u.Nick = nick
        u.HitPoints = 10
        u.MaxHitPoints = 100
        u.IsDowned = lambda self=None: down
        u.DownedRally = lambda self=None: rallied.append(nick)
        return u

    g.g_Units = lua.eval('{}')
    g.g_Units['Ivan'] = unit('Ivan', True)
    g.g_Units['Fox'] = unit('Fox', False)
    g.gv_UnitData = lua.eval('{}')
    for k in ('Ivan', 'Fox'):
        g.gv_UnitData[k] = lua.eval('{HireStatus = "Hired"}')

    r = apply_fn(cmd(lua, 'heal', 'Ivan'))
    check('healing a downed merc calls the game\'s own DownedRally',
          rallied == ['Ivan'], str(rallied))
    check('and his health is restored too',
          g.g_Units['Ivan'].HitPoints == 100,
          str(g.g_Units['Ivan'].HitPoints))
    check('the report says they got back up, not just that they were healed',
          'back on their feet' in str(r), str(r))

    rallied[:] = []
    r = apply_fn(cmd(lua, 'heal', 'Fox'))
    check('healing someone who is NOT down does not rally them',
          rallied == [], str(rallied))
    check('and that report does not claim anyone stood up',
          'back on their feet' not in str(r), str(r))

    print('\n%d passed, %d failed' % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
