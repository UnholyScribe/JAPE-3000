/* The Jagged Alliance 2 dashboard.
 *
 * JAPE puts everything on one screen and never makes you open anything: the
 * roster down the left, every attribute permanently visible, the gear as a
 * grid, and one editor panel underneath that becomes whatever slot you last
 * clicked. That arrangement is JAPE's and it is the right one - a save editor
 * is a thing you scan, not a thing you navigate. The finish is ours.
 *
 * Loaded after app.js and uses its helpers (h, queue, render, portraitUrl,
 * initials, pendStat, pendField, maxStats, toast). Kept in its own file
 * because app.js is already long, not because it is separate in any other
 * sense.
 */

/* ── slots ───────────────────────────────────────────────────────────────
 *
 * Nineteen of them, in the order profile_data.SLOT_NAMES has them:
 *   0 helmet  1 body armour  2 leg armour  3-4 headgear
 *   5 right hand  6 left hand
 *   7-18 pack
 */
const JA2_WORN = [0, 1, 2, 3, 4];
const JA2_HANDS = [5, 6];
const JA2_PACK = [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18];

/* What may go in each worn slot. Taken from the game's own item categories,
   so "a Glock does not go on your head" is enforced by the data rather than
   by a list somebody typed out. Hands and pack take almost anything, which is
   true in the game as well - a merc can carry a helmet in their pack. */
const JA2_SLOT_OK = {
  0: ['Helmet'],
  1: ['Body armour'],
  2: ['Leg armour'],
  3: ['Head gear'],
  4: ['Head gear'],
};
const JA2_CARRY = [
  'Weapon', 'Melee weapon', 'Weapon attachment', 'Ammo', 'Grenade', 'Explosive',
  'Detonator', 'Helmet', 'Body armour', 'Armour attachment', 'Leg armour',
  'Head gear', 'Medical', 'Tool', 'Usable item', 'Misc', 'Key', 'Money',
];

/* No icon for most items yet - the item-name-to-graphic table lives inside the
   game executable rather than in a data file. A glyph for the KIND of thing is
   most of what the icon was going to tell you at this size anyway. */
const JA2_GLYPH = {
  'Weapon': '▬', 'Melee weapon': '⚔', 'Weapon attachment': '◆',
  'Ammo': '▤', 'Grenade': '●', 'Explosive': '✹',
  'Detonator': '⚡', 'Helmet': '◓', 'Body armour': '▣',
  'Armour attachment': '▧', 'Leg armour': '▥', 'Head gear': '◔',
  'Medical': '✚', 'Tool': '⚒', 'Usable item': '◈',
  'Misc': '○', 'Key': '⚿', 'Money': '¤',
};

const ja2Allowed = slot => JA2_SLOT_OK[slot] || JA2_CARRY;

const JA2_FITS = {
  'Weapon': ['Weapon attachment'],
  'Melee weapon': ['Weapon attachment'],
  'Explosive': ['Detonator'],
  'Grenade': ['Detonator'],
  'Helmet': ['Armour attachment'],
  'Body armour': ['Armour attachment'],
  'Leg armour': ['Armour attachment'],
};

function ja2Items(cats) {
  const ok = new Set(cats);
  return (S.sum.items || []).filter(i => ok.has(i.cat));
}

function ja2IconUrl(id) {
  const f = S.ja2icons ? S.ja2icons[String(id)] : null;
  return f ? 'ja2icons/' + f + '.png' : null;
}

/* ── the view ────────────────────────────────────────────────────────── */
/* What to put in a slot when a DIFFERENT item is chosen for it.
 *
 * Condition starts at 100: the wear belonged to the item being replaced.
 * The count is carried over only as far as the new item can legally stack -
 * three magazines swapped for a rifle would otherwise become three rifles,
 * which is exactly the state the editor's own plausibility check warns about.
 * Making the editor able to create a problem it then reports is silly.
 */
function ja2Swap(sl, id) {
  if (!id) return { item: 0, count: 0, condition: 0 };
  if (id === sl.item) return { item: id };          // same item, leave it be
  const info = (S.sum.items || []).find(x => x.id === id) || {};
  const limit = info.stack || 1;
  return { item: id, count: clamp(sl.count || 1, 1, limit), condition: 100 };
}

function ja2Dashboard() {
  const all = S.sum.mercs;
  const m = all.find(x => x.id === S.sel) || all[0];
  if (S.ja2Slot === undefined || S.ja2Slot === null) S.ja2Slot = 5;

  return [
    h('div.row', h('h1', 'Mercenaries'), h('div.spacer'),
      h('span.dim', { style: { fontSize: '12px' } },
        all.length + ' in this save')),
    h('p.sub', 'Everyone in the file. Attributes on the left, gear on the '
      + 'right, and the panel underneath edits whichever slot you click.'),
    h('div.ja2',
      ja2Roster(all, m),
      ja2Stats(m),
      ja2Face(m),
      h('div', ja2Gearboard(m), ja2ItemEditor(m))),
  ];
}

/* ── roster ──────────────────────────────────────────────────────────── */
function ja2Roster(all, sel) {
  return h('div.card',
    h('div.card-h', h('h2', 'Roster'), h('div.spacer'),
      h('input', {
        type: 'text', placeholder: 'Find…', value: S.mercFilter || '',
        style: { maxWidth: '104px' },
        /* Repaint only the list. Redrawing the view would take the focus out
           of this box mid-word, which is the same reason viewParty does it. */
        oninput: e => { S.mercFilter = e.target.value; S.ja2RosterTop = 0; ja2RepaintRoster(sel); },
      })),
    ja2RosterBox(ja2Shown(all), sel));
}

/* Picking a merc redraws the whole view, roster included. Without putting
   the scroll back, the list jumps to the top on every click. */
function ja2RosterBox(shown, sel) {
  const box = h('div.card-b.ja2-roster', {
    id: 'ja2roster',
    onscroll: e => { S.ja2RosterTop = e.target.scrollTop; },
  }, ja2RosterRows(shown, sel));
  requestAnimationFrame(() => { box.scrollTop = S.ja2RosterTop || 0; });
  return box;
}

/* A face that fails once is asked for again, then falls back to initials,
   so a dropped request never leaves a blank square in the list. */
function ja2RosterFace(url, label) {
  let tries = 0;
  const img = h('img', {
    src: url, alt: '', loading: 'lazy', draggable: false,
    onerror: () => {
      tries++;
      if (tries === 1) img.src = url + (url.includes('?') ? '&' : '?') + 'retry=1';
      else img.replaceWith(h('div.avatar.sm', initials(label)));
    },
  });
  return img;
}

function ja2Shown(all) {
  const q = (S.mercFilter || '').trim().toLowerCase();
  if (!q) return all;
  return all.filter(x =>
    ((x.name || '') + ' ' + (x.nick || '')).toLowerCase().includes(q));
}

function ja2RosterRows(shown, sel) {
  if (!shown.length) return h('p.dim', { style: { fontSize: '12px' } },
    'Nobody in this save matches that.');
  return shown.map(x => {
    const url = portraitUrl(x.id, x.preset);
    return h('div.ja2-row' + (x.id === sel.id ? '.on' : ''), {
      onclick: () => { S.sel = x.id; S.ja2Slot = 5; render(); },
      title: x.soldier !== undefined ? 'On the team' : 'Listed, not hired',
    },
      url ? ja2RosterFace(url, x.nick || x.name)
          : h('div.avatar.sm', initials(x.nick || x.name)),
      h('div', { style: { minWidth: 0 } },
        h('div.nm' + (x.soldier !== undefined ? '.hired' : ''), x.nick || x.name),
        h('div.sub', x.name)),
      x.level ? h('div.lv', 'L' + x.level) : null);
  });
}

function ja2RepaintRoster(sel) {
  const box = document.getElementById('ja2roster');
  if (!box) return render();
  box.textContent = '';
  box.append(...[].concat(ja2RosterRows(ja2Shown(S.sum.mercs), sel)));
}

/* ── every attribute, permanently visible ────────────────────────────── */
function ja2Stats(m) {
  const row = (label, value, set, max) => h('div.ja2-stat',
    h('div.k', label),
    h('input', { type: 'range', min: 0, max: max || 100, value: value,
      oninput: e => set(+e.target.value) }),
    h('input', { type: 'number', min: 0, max: max || 100, value: value,
      onchange: e => set(clamp(+e.target.value, 0, max || 100)) }));

  const field = (f, v) => queue({ kind: 'merc_field', merc: m.id, field: f, value: v });

  return h('div.card',
    h('div.card-h', h('h2', 'Attributes'), h('div.spacer'),
      h('button.btn.sm', { onclick: () => maxStats(m) }, 'Max')),
    h('div.card-b',
      S.cat.stats.filter(s => (m.stats[s] || {}).value != null).map(s =>
        row(s, pendStat(m.id, s, m.stats[s].value),
            v => queue({ kind: 'stat', merc: m.id, stat: s, value: v }))),

      h('div.slot-h', { style: { marginTop: '12px' } }, 'Condition', h('span.line')),
      m.hp == null ? null
        : row('Health', pendField(m.id, 'HitPoints', m.hp),
              v => field('HitPoints', v), 255),
      m.max_hp == null ? null
        : row('Max health', pendField(m.id, 'MaxHitPoints', m.max_hp),
              v => field('MaxHitPoints', v), 255),
      row('Level', pendField(m.id, 'Level', m.level || 1),
          v => field('Level', v), 10),
      /* Energy and morale live only in the record of someone on the team. */
      m.energy == null ? null : h('div',
        row('Energy', pendField(m.id, 'Energy', m.energy),
            v => field('Energy', Math.min(v,
              pendField(m.id, 'MaxEnergy', m.max_energy || 100))),
            100),
        row('Max energy', pendField(m.id, 'MaxEnergy', m.max_energy || 100),
            v => field('MaxEnergy', v), 100),
        m.morale == null ? null
          : row('Morale', pendField(m.id, 'Morale', m.morale),
                v => field('Morale', v), 100)),

      ja2Traits(m),
      ja2Gains(m),
      ja2Career(m)));
}

function ja2Traits(m) {
  if (!m.skills) return null;
  const list = S.sum.skill_list || [];
  const pick = (label, value, f) => h('div.ja2-stat',
    h('div.k', label),
    h('select', { style: { gridColumn: '2 / -1' },
      onchange: e => queue({ kind: 'merc_field', merc: m.id, field: f,
                             value: +e.target.value }) },
      list.map((nm, i) => h('option',
        { value: String(i), selected: i === pendField(m.id, f, value) }, nm))));
  return h('div',
    h('div.slot-h', { style: { marginTop: '12px' } }, 'Traits', h('span.line')),
    pick('Skill 1', m.skills[0], 'Skill1'),
    pick('Skill 2', m.skills[1], 'Skill2'));
}

/* The "(+ bonus)" box beside each stat in the game - what a merc trained up.
   Folded, because it is eleven more boxes and most edits are not these. */
function ja2Gains(m) {
  if (!m.gains) return null;
  return h('details', { style: { marginTop: '12px' } },
    h('summary.dim', { style: { cursor: 'pointer', fontSize: '11.5px' } },
      'Trained bonuses'),
    h('div', { style: { marginTop: '8px' } },
      Object.keys(m.gains).map(k => h('div.ja2-stat',
        h('div.k', k),
        h('input', { type: 'number', min: 0, max: 255,
          style: { gridColumn: '2 / -1' },
          value: pendField(m.id, 'Gain' + k, m.gains[k]),
          onchange: e => queue({ kind: 'merc_field', merc: m.id,
                                 field: 'Gain' + k,
                                 value: clamp(+e.target.value, 0, 255) }) })))));
}

function ja2Career(m) {
  if (!m.career) return null;
  return h('details', { style: { marginTop: '6px' } },
    h('summary.dim', { style: { cursor: 'pointer', fontSize: '11.5px' } },
      'Career record'),
    h('div', { style: { marginTop: '8px' } },
      Object.keys(m.career).map(k => h('div.ja2-stat',
        h('div.k', k),
        h('input', { type: 'number', min: 0, max: 65535,
          style: { gridColumn: '2 / -1' },
          value: pendField(m.id, 'Career' + k, m.career[k]),
          onchange: e => queue({ kind: 'merc_field', merc: m.id,
                                 field: 'Career' + k,
                                 value: clamp(+e.target.value, 0, 65535) }) })))));
}

/* ── the merc, in the middle ─────────────────────────────────────────── */
function ja2Face(m) {
  const url = portraitUrl(m.id, m.preset);
  const pend = key => {
    const p = [...(S.pending || [])].reverse().find(
      i => i.kind === 'ja2_name' && i.merc === m.id && i[key] !== undefined);
    return p ? p[key] : (key === 'name' ? m.name : m.nick);
  };
  /* The game keeps 29 characters of name and 9 of nickname. */
  const box = (key, label, max) => h('div', { style: { width: '100%' } },
    h('label.dim', { style: { fontSize: '10.5px' } }, label),
    h('input', { type: 'text', maxLength: max, value: pend(key),
      style: { width: '100%' },
      onchange: e => {
        const v = e.target.value.trim();
        if (!v) { toast('Not changed', 'A ' + label.toLowerCase()
                        + ' cannot be blank.', 'bad'); render(); return; }
        queue({ kind: 'ja2_name', merc: m.id, [key]: v });
      } }));
  return h('div.card',
    h('div.card-b.ja2-face',
      url ? h('img', { src: url, alt: m.name })
          : h('div.avatar.big', initials(m.nick || m.name)),
      h('div.who', pend('nick')),
      h('div.sub', pend('name')),
      m.soldier !== undefined
        ? h('span.chip.accent', { style: { marginTop: '2px' } }, 'On the team')
        : h('span.chip', { style: { marginTop: '2px' } }, 'Not hired'),
      box('nick', 'Nickname', 9),
      box('name', 'Full name', 29),
      ja2SendHome(m),
      ja2Hire(m),
      h('div.pending', {
        title: 'Jagged Alliance 2 does not store a picture of each merc. It '
          + 'builds them at run time from shared animation frames with the '
          + 'palette swapped per person, so the body sprite is a real job '
          + 'rather than an extraction.',
      }, 'Body sprite still to come')));
}

/* Dismissing a merc, the way the game's own "Dismiss" does it. Queued like
   any edit, so nothing happens until Write to save - and it can be taken back
   until then. */
function ja2SendHome(m) {
  if (m.soldier === undefined) return null;
  const queued = S.pending.some(i => i.kind === 'ja2_fire' && i.merc === m.id);
  const who = m.nick || m.name;
  if (queued) {
    return h('div', { style: { width: '100%', display: 'flex', gap: '6px',
                               alignItems: 'center' } },
      h('span.chip.warn', 'Leaving when you write'),
      h('button.btn.ghost.sm', {
        onclick: () => unqueue(i => i.kind === 'ja2_fire' && i.merc === m.id),
      }, 'Keep them'));
  }
  if (m.fire_refused) {
    return h('button.btn.sm', { style: { width: '100%' }, disabled: true,
      title: who + ' ' + m.fire_refused }, 'Send home…');
  }
  return h('button.btn.sm', {
    style: { width: '100%' },
    title: 'Dismiss this merc, as the game does',
    onclick: () => confirmThen(
      'Send ' + who + ' home?',
      who + ' leaves the team when you write the save: out of their squad, '
      + 'listed as departed in the laptop, and back on the market in a day. '
      + 'Everything they are carrying goes with them, so hand anything you '
      + 'want to keep to someone else in the game first.',
      () => queue({ kind: 'ja2_fire', merc: m.id }),
      'Send home'),
  }, 'Send home…');
}

/* Hiring from A.I.M. or M.E.R.C., the way the laptop does it: they arrive
   by helicopter an hour after the save is loaded, and the game puts them in
   a squad itself. */
function ja2Hire(m) {
  if (m.soldier !== undefined || m.hire_refused === undefined) return null;
  const who = m.nick || m.name;
  const mine = i => i.kind === 'ja2_hire' && i.merc === m.id;
  const queued = S.pending.find(mine);
  const row = { width: '100%', display: 'flex', gap: '6px', alignItems: 'center' };
  if (queued) {
    return h('div', { style: row },
      h('span.chip.accent', 'Joining when you write'),
      h('button.btn.ghost.sm', { onclick: () => unqueue(mine) }, 'Cancel'));
  }
  if (m.hire_refused) {
    return h('button.btn.sm', { style: { width: '100%' }, disabled: true,
      title: who + ' ' + m.hire_refused }, 'Hire…');
  }
  const merc = m.hire_site === 'M.E.R.C.';
  let days = merc ? 1 : 7, gear = true;
  const contract = merc
    ? 'M.E.R.C. bills by the day, as it does in the game.'
    : 'The contract runs from when they land.';
  return h('div', { style: { width: '100%', display: 'flex',
                             flexDirection: 'column', gap: '4px' } },
    h('div', { style: row },
      merc
        ? h('span.dim', { style: { flex: '1', fontSize: '11px' } }, 'Paid daily')
        : h('select', { style: { flex: '1' }, title: 'Contract length',
                        onchange: e => { days = +e.target.value; } },
            h('option', { value: 1 }, '1 day'),
            h('option', { value: 7, selected: true }, '1 week'),
            h('option', { value: 14 }, '2 weeks')),
      h('label', { style: { fontSize: '11px', whiteSpace: 'nowrap' },
                   title: 'Arrive carrying their ' + m.hire_site + ' kit' },
        h('input', { type: 'checkbox', checked: true,
                     onchange: e => { gear = e.target.checked; } }),
        ' With kit')),
    h('button.btn.sm', {
      style: { width: '100%' },
      title: 'Hire from ' + m.hire_site + ' without paying for it',
      onclick: () => confirmThen(
        'Hire ' + who + '?',
        who + ' joins when you write the save and lands by helicopter an '
        + 'hour after you load it, like any hire made from the laptop. '
        + contract + ' No money is taken.',
        () => queue({ kind: 'ja2_hire', merc: m.id, days, gear }),
        'Hire'),
    }, 'Hire…'));
}

/* ── gear ────────────────────────────────────────────────────────────── */

/* Which record this merc's gear rows came out of, and therefore which one an
   edit has to be written back to. A merc on the team is shown their LIVE
   inventory - what they are actually carrying - because that is what the game
   reads in the field. Anyone else has only a loadout. */
const ja2ItemKind = m => m.soldier !== undefined ? 'ja2_live_item' : 'ja2_item';

/* Effective contents of a slot once queued edits are counted. Without this a
   change you just made would vanish from the grid until you saved.
 *
 * Note what happens to the attachments: changing the ITEM drops them. That is
 * not cosmetic. The panel used to keep showing the old weapon's attachments
 * after the item was swapped, and editing one of those rows wrote a scope onto
 * a gun that was no longer the one on screen - which is exactly what happened
 * to Shadow's MP5K when a Glock was put in that hand. The writer clears them
 * too, so the two agree.
 */
function ja2SlotNow(m, sl) {
  const kind = ja2ItemKind(m);
  /* Every queued edit for this slot, in order - they are applied in order,
     and a magazine picked before a count change has to survive it. */
  const qs = (S.pending || []).filter(
    i => i.kind === kind && i.merc === m.id && i.slot === sl.slot);
  if (!qs.length) return sl;
  let now = Object.assign({}, sl);
  for (const q of qs) {
    const item = q.item !== undefined ? q.item : now.item;
    const changed = item !== sl.item;
    const cat = item ? ((S.sum.items || []).find(x => x.id === item) || {}).cat : '';
    now = Object.assign({}, now, {
      item: item,
      item_name: item ? ja2ItemName(item) : '',
      category: cat || '',
      count: q.count !== undefined ? q.count : now.count,
      condition: q.condition !== undefined ? q.condition : now.condition,
      attachments: changed ? [null, null, null, null] : sl.attachments,
      /* A different item's magazine, stack and cash are not this one's;
         the writer clears them, so the panel does too. */
      gun: changed ? undefined : now.gun,
      stack: changed ? undefined : now.stack,
      money: changed ? undefined : now.money,
      swapped: changed,
      empty: !item,
      dirty: true,
    });
    if (now.gun && (q.mag !== undefined || q.rounds !== undefined)) {
      const mag = q.mag !== undefined ? q.mag : now.gun.mag;
      now.gun = Object.assign({}, now.gun, {
        mag: mag,
        mag_name: mag ? ja2ItemName(mag) : '',
        rounds: q.rounds !== undefined ? q.rounds : (mag ? now.gun.rounds : 0),
      });
    }
    if (now.stack && q.condition !== undefined && !q.stack) {
      now.stack = [q.condition].concat(now.stack.slice(1));
    }
    if (now.stack && q.stack) {
      now.stack = now.stack.map((v, k) => q.stack[k] != null ? q.stack[k] : v);
      now.condition = now.stack[0];
    }
    if (now.stack && q.count !== undefined) {
      const n = Math.min(Math.max(q.count, 0), 8);
      now.stack = Array.from({ length: n },
        (_, k) => now.stack[k] != null ? now.stack[k] : now.stack[0]);
    }
    if (now.money !== undefined && q.money !== undefined) now.money = q.money;
  }
  return now;
}

function ja2SlotTile(m, sl) {
  const url = sl.item ? ja2IconUrl(sl.item) : null;
  const glyph = JA2_GLYPH[sl.category] || '○';
  const fitted = (sl.attachments || []).filter(a => a && a.id).length;
  /* A magazine's "condition" is its round count, so the bar is measured
     against how many it holds - a full 30 round mag is a full bar. */
  const cap = ((S.sum.items || []).find(x => x.id === sl.item) || {}).capacity;
  const pct = cap ? Math.round(100 * sl.condition / cap) : sl.condition;
  return h('div.ja2-slot' + (sl.empty ? '.empty' : '')
           + (S.ja2Slot === sl.slot ? '.on' : '')
           + (sl.dirty ? '.dirty' : ''), {
    onclick: () => { S.ja2Slot = sl.slot; render(); },
    title: sl.empty ? sl.name + ' — empty'
                    : sl.name + ': ' + sl.item_name + ' ('
                      + (cap ? sl.condition + ' of ' + cap + ' rounds'
                             : sl.condition + '%') + ')',
  },
    h('div.lbl', sl.name),
    h('div.art', sl.empty ? null
      : (url ? h('img', { src: url, alt: '' })
             : h('span.glyph', { title: sl.category || 'unknown kind' }, glyph))),
    h('div.it', sl.empty ? '' : sl.item_name),
    (!sl.empty && sl.count > 1) ? h('div.qty', '×' + sl.count) : null,
    fitted ? h('div.pips', '◆'.repeat(Math.min(fitted, 4))) : null,
    sl.empty ? null : h('div.cond' + (pct < 50 ? '.low' : ''),
      { style: { width: Math.max(2, clamp(pct, 0, 100)) + '%' } }));
}

function ja2Gearboard(m) {
  const inv = m.inventory || [];
  const at = i => ja2SlotNow(m, inv[i] || { slot: i, name: '?', empty: true });
  const used = inv.map((_, i) => at(i)).filter(x => !x.empty).length;
  const band = (title, ids, cls) => [
    h('div.slot-h', { style: { marginTop: '10px' } }, title, h('span.line')),
    h('div.ja2-slots' + (cls || ''), ids.map(i => ja2SlotTile(m, at(i)))),
  ];
  const live = m.soldier !== undefined;
  return h('div.card',
    h('div.card-h', h('h2', live ? 'Carrying' : 'Loadout'), h('div.spacer'),
      h('span.dim', { style: { fontSize: '11.5px' } },
        used + ' of ' + inv.length + ' slots used')),
    h('div.card-b',
      h('p.dim', { style: { fontSize: '11px', marginTop: 0 } },
        live
          ? 'What this merc has on them right now. This is the inventory the '
            + 'game reads in the field, so this is where an edit has to land.'
          : 'The kit this merc turns up with. They are not on the team, so '
            + 'there is no live inventory to show yet.'),
      band('Worn', JA2_WORN, '.worn'),
      band('Hands', JA2_HANDS, '.hands'),
      band('Pack', JA2_PACK),
      live ? ja2Loadout(m) : null));
}

/* The A.I.M. loadout, for a merc who is on the team - kept visible but folded,
   because it is not what the game reads in the field and editing it by mistake
   is exactly the confusion this screen was rebuilt to remove. */
function ja2Loadout(m) {
  const kit = (m.aim_inventory || []).filter(r => !r.empty);
  if (!kit.length) return null;
  const differs = kit.some(r => {
    const live = (m.inventory || [])[r.slot];
    return !live || live.item !== r.item;
  });
  return h('details', { style: { marginTop: '12px' } },
    h('summary.dim', { style: { cursor: 'pointer', fontSize: '11.5px' } },
      'A.I.M. loadout' + (differs ? ' \u2014 differs from what they carry' : '')),
    h('div', { style: { marginTop: '8px' } },
      h('p.dim', { style: { fontSize: '11px' } },
        'What this merc signed on with. The game keeps it separate from what '
        + 'they are carrying, and the two drift apart as soon as anything is '
        + 'moved in a pack. Shown here so it is not invisible; edit the grid '
        + 'above, not this.'),
      kit.map(r => h('div.row', { style: { gap: '8px', fontSize: '11.5px',
                                           padding: '2px 0' } },
        h('span.dim', { style: { width: '92px', flex: 'none' } }, r.name),
        h('span', r.item_name),
        r.count > 1 ? h('span.dim', '\u00d7' + r.count) : null,
        h('div.spacer'),
        h('span.dim', r.condition + '%')))));
}

/* ── the contextual editor ───────────────────────────────────────────── */
function ja2ItemEditor(m) {
  const inv = m.inventory || [];
  const raw = inv[S.ja2Slot];
  if (!raw) {
    return h('div.card', { style: { marginTop: '14px' } },
      h('div.card-h', h('h2', 'Item')),
      h('div.card-b', h('p.dim', 'Pick a slot above to edit what is in it.')));
  }
  const sl = ja2SlotNow(m, raw);

  /* Every write carries the whole slot, because the three arrays behind it
     are written together - see Profiles.set_slot. */
  const set = patch => queue(Object.assign(
    { kind: ja2ItemKind(m), merc: m.id, slot: sl.slot,
      item: sl.item, count: sl.count, condition: sl.condition }, patch));

  const onTeam = m.soldier !== undefined;
  const list = ja2Items(ja2Allowed(sl.slot));
  const info = (S.sum.items || []).find(x => x.id === sl.item) || {};
  const fits = JA2_FITS[sl.category] || [];
  /* Read the attachments off the MERGED row, not the saved one. Those two
     differ the moment a different item is queued into the slot, and showing
     the saved one there is how a scope got written onto the wrong gun. */
  const worn = (sl.attachments || []).filter(a => a && a.id).length;
  const takes = fits.length > 0 || worn > 0;

  const field = (label, node, off, why) =>
    h('div' + (off ? '.off' : ''), { title: why || '' },
      h('label', label), node);

  return h('div.card', { style: { marginTop: '14px' } },
    h('div.card-h', h('h2', sl.name), h('div.spacer'),
      sl.empty ? null : h('button.btn.sm.ghost', {
        onclick: () => set({ item: 0, count: 0, condition: 0 }),
      }, 'Empty this slot')),
    h('div.card-b',
      h('div.ja2-edit',
        h('div.wide', h('label', 'Item'),
          h('select', {
            onchange: e => set(ja2Swap(sl, +e.target.value)),
          }, [h('option', { value: '0', selected: !sl.item },
                '— empty —')]
             .concat(list.map(i => h('option',
               { value: String(i.id), selected: i.id === sl.item }, i.name))),
          )),

        field('By number', h('input', { type: 'number', min: 0, max: 65535,
          value: sl.item || 0, title: 'Item id, for anything the name table '
            + 'has never heard of - a modded install, mostly.',
          onchange: e => set(ja2Swap(sl, clamp(+e.target.value, 0, 65535))),
          }), false),

        field('How many', h('input', { type: 'number', min: 0, max: 255,
          value: sl.count,
          onchange: e => set({ count: clamp(+e.target.value, 0, 255) }) }),
          sl.empty, 'Nothing in this slot'),

        field(sl.category === 'Ammo'
                ? (sl.count > 1 ? 'Rounds in the first' : 'Rounds left')
                : 'Condition %',
          h('input', { type: 'number', min: 0, max: info.capacity || 100,
            value: sl.condition,
            onchange: e => set({ condition: clamp(+e.target.value, 0,
                                                  info.capacity || 100) }) }),
          sl.empty, sl.category === 'Ammo'
            ? 'Ammunition stores its round count in the condition field'
            : 'Nothing in this slot'),

        /* Not greyed. It is read-only, but it HAS a value - dimming it the
           same as the three below would say "we could not get this", which
           is the opposite of true. */
        field('Kind', h('div.derived.known', sl.category || '—'), false,
          'Read from the game’s own item categories'),

        /* Weight and price come out of the game's own item table now, so
           they are real numbers rather than an apology. Still read-only:
           they belong to the item, not to this copy of it. */
        field('Weight', h('div.derived.known',
          info.weight == null ? '—'
            : (info.weight * Math.max(1, sl.count)).toFixed(1) + ' kg'), false,
          sl.count > 1 ? 'For the whole stack of ' + sl.count : 'Out of the game’s item table'),

        field('Value', h('div.derived.known',
          info.price == null ? '—' : '$' + info.price), false,
          'What the game prices one of these at'),

        ja2AmmoFields(m, sl, set, field),
        sl.money !== undefined
          ? field('Cash $', h('input', { type: 'number', min: 0, max: 20000,
              value: sl.money,
              onchange: e => set({ money: clamp(+e.target.value, 0, 20000) }) }),
              false, 'The game holds at most $20,000 in one pocket')
          : null),

      ja2StackBoxes(sl, set),

      h('div.slot-h', { style: { marginTop: '14px' } }, 'Fitted', h('span.line'),
        h('span.dim', { style: { fontSize: '10.5px', textTransform: 'none',
                                 letterSpacing: 0 } },
          !onTeam ? 'needs a merc on the team'
                  : (!takes ? 'nothing fits this'
                            : (fits.join(', ').toLowerCase() || 'fitted')))),

      (takes && onTeam && !sl.empty)
        ? ja2Attachments(m, sl, fits)
        : h('p.dim', { style: { fontSize: '11.5px' } },
            !onTeam
              ? 'Attachments live in the record of a merc who is actually on '
                + 'the team. Someone still sitting at A.I.M. has a loadout, '
                + 'not a rifle with a scope on it.'
              : (sl.empty
                  ? 'Nothing in this slot.'
                  : 'Nothing of this kind takes an attachment.'))));
}

/* What is loaded in a gun. The round type is not offered: it belongs to the
   magazine, and the writer sets it from whichever one is picked. */
function ja2AmmoFields(m, sl, set, field) {
  const g = sl.gun;
  if (!g) {
    return (sl.swapped && sl.category === 'Weapon' && m.soldier !== undefined)
      ? h('div.wide', h('p.dim', { style: { fontSize: '11px', margin: 0 } },
          'A gun put in this hand arrives with a full magazine. Write the '
          + 'save, and what it is loaded with can be changed here.'))
      : null;
  }
  return [
    h('div.wide', h('label', 'Loaded with'),
      h('select', {
        onchange: e => {
          const mag = +e.target.value;
          set({ mag: mag, rounds: mag ? (g.rounds || g.capacity) : 0 });
        },
      }, [h('option', { value: '0', selected: !g.mag }, '— unloaded —')]
         .concat(g.fits.map(f => h('option',
           { value: String(f.id), selected: f.id === g.mag }, f.name))))),
    field('Rounds in gun', h('input', { type: 'number', min: 0, max: g.capacity,
      value: g.rounds, disabled: !g.mag,
      onchange: e => set({ rounds: clamp(+e.target.value, 0, g.capacity) }) }),
      !g.mag, 'At most ' + g.capacity + ', the size of this gun’s magazine'),
  ];
}

/* A stack keeps one value per object - rounds for each magazine, condition
   for anything else - so three magazines can be 15, 15 and 4. */
function ja2StackBoxes(sl, set) {
  if (!sl.stack || sl.stack.length < 2) return null;
  const ammo = sl.category === 'Ammo';
  const top = ammo ? (sl.capacity || 255) : 100;
  return h('div', { style: { marginTop: '10px' } },
    h('div.slot-h', ammo ? 'Rounds in each magazine' : 'Condition of each one',
      h('span.line')),
    h('div.row', { style: { gap: '6px', flexWrap: 'wrap' } },
      sl.stack.map((v, k) => h('input', {
        type: 'number', min: 0, max: top, value: v,
        style: { width: '64px' }, title: (ammo ? 'Magazine ' : 'Item ') + (k + 1),
        onchange: e => {
          const st = sl.stack.slice();
          st[k] = clamp(+e.target.value, 0, top);
          set({ stack: st, condition: st[0] });
        },
      }))));
}

function ja2Attachments(m, sl, fits) {
  /* Four positional slots, and the list arrives with a null for each empty
     one - so `have[k]` is the thing in attachment slot k, not the k-th thing
     that happens to be fitted. That distinction is the bug this shape fixes:
     a weapon with something in slot 2 and nothing in slot 0 used to be shown
     as slot 0, and editing it wrote to the wrong place. */
  const have = sl.attachments || [];
  const opts = ja2Items(fits && fits.length ? fits : ['Weapon attachment']);
  /* Whatever is on there already goes in the list even if its category is not
     one this editor expects, so opening the box can never silently drop it. */
  have.forEach(a => {
    if (a && a.id && !opts.some(o => o.id === a.id)) {
      opts.unshift({ id: a.id, name: a.name });
    }
  });

  const pend = k => (S.pending || []).filter(
    i => i.kind === 'ja2_attach' && i.merc === m.id
      && i.slot === sl.slot && i.index === k).pop();

  const set = (k, patch) => {
    const now = pend(k) || {};
    const a = have[k];
    queue({
      kind: 'ja2_attach', merc: m.id, slot: sl.slot, index: k,
      item: patch.item !== undefined ? patch.item
        : (now.item !== undefined ? now.item : (a ? a.id : 0)),
      condition: patch.condition !== undefined ? patch.condition
        : (now.condition !== undefined ? now.condition
           : (a ? a.condition : 0)),
    });
  };

  const rows = [];
  /* All four, always. Showing only "however many are fitted, plus one" meant
     four physical slots were being drawn as three, which is what the tester saw. */
  for (let k = 0; k < 4; k++) {
    const q = pend(k) || {};
    const a = have[k];
    const id = q.item !== undefined ? q.item : (a ? a.id : 0);
    const cond = q.condition !== undefined ? q.condition : (a ? a.condition : 0);
    rows.push(h('div.ja2-edit' + (pend(k) ? '.dirty' : ''),
      { style: { marginBottom: '8px' } },
      h('div', h('label', 'Attachment ' + (k + 1)),
        h('select', {
          onchange: e => {
            const pick = +e.target.value;
            set(k, pick ? { item: pick, condition: cond || 100 }
                        : { item: 0, condition: 0 });
          },
        }, [h('option', { value: '0', selected: !id }, '— none —')]
           .concat(opts.map(i => h('option',
             { value: String(i.id), selected: i.id === id }, i.name))))),
      h('div' + (id ? '' : '.off'), h('label', 'Condition %'),
        h('input', { type: 'number', min: 0, max: 100, value: cond,
          onchange: e => set(k, { condition: clamp(+e.target.value, 0, 100) }) }))));
  }
  return h('div', rows);
}
