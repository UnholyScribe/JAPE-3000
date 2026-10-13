'use strict';

/* ── tiny helpers ──────────────────────────────────────────────────── */
const $ = (s, r = document) => r.querySelector(s);
const api = async (path, body) => {
  const res = await fetch(path, body ? {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  } : undefined);
  const data = await res.json().catch(() => ({ error: 'bad response' }));
  if (!res.ok || data.error) {
    /* Record it before throwing. The caller usually turns this into a toast,
       which is gone the moment it is dismissed - and the server's traceback,
       which is the useful half, never reaches anywhere it can be read later.
       Guarded so a failure inside the logger cannot mask the real error. */
    try {
      if (path !== 'api/diag' && typeof report === 'function') {
        report('api-failed', data.error || ('HTTP ' + res.status),
               { call: path, trace: (data.trace || '').slice(0, 2000) });
      }
    } catch (e) { /* never let logging replace the error being reported */ }
    throw new Error(data.error || ('HTTP ' + res.status));
  }
  return data;
};

function h(tag, attrs, ...kids) {
  const parts = tag.split(/(?=[.#])/);
  const el = document.createElement(parts[0] || 'div');
  parts.slice(1).forEach(p => p[0] === '#' ? (el.id = p.slice(1)) : el.classList.add(p.slice(1)));
  if (attrs && (attrs.nodeType || typeof attrs !== 'object' || Array.isArray(attrs))) {
    kids.unshift(attrs); attrs = null;
  }
  for (const k in attrs || {}) {
    const v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on')) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') el.innerHTML = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k in el && k !== 'list') el[k] = v;
    else el.setAttribute(k, v);
  }
  const add = k => {
    if (k === null || k === undefined || k === false) return;
    if (Array.isArray(k)) return k.forEach(add);
    el.append(k.nodeType ? k : document.createTextNode(k));
  };
  kids.forEach(add);
  return el;
}

const num = n => (n === null || n === undefined) ? '—' : n.toLocaleString('en-US');
const money = n => (n === null || n === undefined) ? '—' : '$' + n.toLocaleString('en-US');
const initials = s => (s || '?').replace(/[^A-Za-z ]/g, '').split(/\s+/)
  .filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || '?';
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const titleOf = s => (s || '').replace(/([a-z])([A-Z])/g, '$1 $2');

/* JA3 campaign timestamps are seconds; day 1 of Hot Diamonds is this epoch. */
const CAMPAIGN_START = 986299200;
const gameDay = t => t ? Math.floor((t - CAMPAIGN_START) / 86400) + 1 : null;

/* ── state ─────────────────────────────────────────────────────────── */
const S = {
  saves: [], path: null, sum: null, cat: null, icons: null, portraits: null,
  game: null, folders: null,
  route: 'overview', sel: null, pending: [], busy: false, err: null,
  sectorFilter: 'interesting', sectorQuery: '',
  live: null, liveState: null, liveTimer: null,
  gamelog: null,
};

/* Pending edits are keyed so re-editing one field replaces it rather than
   stacking a second intent for the same thing. */
const sigOf = i => {
  switch (i.kind) {
    case 'money': return 'money';
    case 'stat': return `stat:${i.merc}:${i.stat}`;
    case 'merc_field': return `field:${i.merc}:${i.field}`;
    case 'perks': return `perks:${i.merc}`;
    case 'new_merc': return `new:${i.merc}`;
    case 'squad': return `squad:${i.merc}:${i.squad}`;
    case 'ja2_item': return `j2i:${i.merc}:${i.slot}`;
    case 'ja2_attach': return `j2a:${i.merc}:${i.slot}:${i.index}`;
    case 'ja2_name': return `j2n:${i.merc}:${i.name !== undefined ? 'name' : 'nick'}`;
    case 'ja2_fire': return `j2f:${i.merc}`;
    case 'ja2_hire': return `j2h:${i.merc}`;
    case 'item_edit': return `ie:${i.merc}:${i.slot}:${i.index}`;
    case 'item_components': return `ic:${i.merc}:${i.slot}:${i.index}`;
    case 'cheats': return 'cheats';
    case 'drop_mod': return `dm:${i.id}`;
    case 'sector_flags': return `sf:${i.sector}`;
    case 'sector_side': return `ss:${i.sector}`;
    default: return null;                       // adds/removes always stack
  }
};

/* ── live editing ─────────────────────────────────────────────────────
 *
 * When the game is running and connected, an edit goes straight into it
 * instead of onto the pending pile. Every control in the editor already
 * funnels through queue(), so intercepting here means the whole app works
 * both ways without a single duplicated button.
 *
 * Only these kinds can go live. Anything else - perks, squads, sectors,
 * cheats, whole-item surgery - still goes to the save file, because the
 * running game has no safe way to take them yet.
 */
const LIVE_OPS = {
  money: i => [['setmoney', i.value]],
  stat: i => [['stat', [i.merc, i.stat, i.value].join('|')]],
  merc_field: i => {
    const op = { HitPoints: 'hp', MaxHitPoints: 'maxhp', Experience: 'exp' }[i.field];
    return op ? [[op, [i.merc, i.value].join('|')]] : null;
  },
  /* No slot means "into the backpack", which is what the game's own give
     cheat does. A named slot has to go through the inventory, which can
     refuse it - and the game's refusal is passed straight back. */
  /* Without this, pressing Restock while the game was connected looked up
     'bag_add', found nothing, and fell through to the SAVE queue - a prominent
     button that quietly did nothing to the running game. Reported by Jager
     Allianz as "I am not sure if/how it works (or why it doesn't)". */
  bag_add: i => [['bagadd', [i.item, i.count].join('|')]],
  item_add: i => (!i.slot || i.slot === 'Inventory')
    ? [['give', [i.merc, i.item, i.amount || 1].join('|')]]
    : [['equip', [i.merc, i.slot, i.item].join('|')]],
  /* The editor hands over the whole list, so work out what actually changed
     and send only that. Removing a perk the merc does not have is harmless,
     but adding one twice is not, and neither is touching a wound. */
  perks: i => {
    const m = (S.sum && S.sum.mercs || []).find(x => x.id === i.merc);
    const before = (m && m.perks) || [];
    const after = i.perks || [];
    const cmds = [];
    after.filter(p => !before.includes(p))
      .forEach(p => cmds.push(['perkon', [i.merc, p].join('|')]));
    before.filter(p => !after.includes(p))
      .forEach(p => cmds.push(['perkoff', [i.merc, p].join('|')]));
    return cmds.length ? cmds : null;
  },
};

/* The catalog knows which effects are perks you can choose; the rest are
   wounds and buffs the game applies itself. */
function isPerk(id) {
  const list = (S.cat && S.cat.perks) || [];
  const hit = list.find(p => p.id === id);
  return !!(hit && hit.assignable);
}

function liveOn() {
  return !!(S.live && S.live.can_edit && S.liveMode !== false);
}

/* Keep the page honest about what we just changed, without refetching the
   save - the save on disk has not changed at all. */
function applyToSummary(intent) {
  const sum = S.sum;
  if (!sum) return;
  if (intent.kind === 'money') { sum.money = intent.value; return; }
  const m = (sum.mercs || []).find(x => x.id === intent.merc);
  if (!m) return;
  if (intent.kind === 'stat' && m.stats && m.stats[intent.stat]) {
    m.stats[intent.stat].value = intent.value;
  } else if (intent.kind === 'merc_field') {
    if (intent.field === 'HitPoints') m.hp = intent.value;
    if (intent.field === 'MaxHitPoints') m.max_hp = intent.value;
    if (intent.field === 'Experience') m.xp = intent.value;
  } else if (intent.kind === 'perks') {
    m.perks = (intent.perks || []).slice();
  }
}

async function applyLive(intent, cmds) {
  try {
    let last = '';
    for (const [op, value] of cmds) {
      const r = await api('/api/live-send', { op: op, value: value });
      if (r.status) S.live = r.status;
      if (!r.ok) { toast('The game did not take that', r.message, 'bad'); render(); return; }
      last = r.message;
    }
    applyToSummary(intent);
    toast('Changed in the game',
          cmds.length > 1 ? cmds.length + ' changes applied' : last);
    render();
  } catch (e) { toast('Failed', e.message, 'bad'); render(); }
}

function queue(intent) {
  if (liveOn()) {
    const make = LIVE_OPS[intent.kind];
    const cmds = make && make(intent);
    if (cmds && cmds.length) return applyLive(intent, cmds);
  }
  const sig = sigOf(intent);
  if (sig) S.pending = S.pending.filter(p => sigOf(p) !== sig);
  /* Which world this value was reasoned about against. Stripped before it is
     sent - writeSave drops every underscore key - so it exists purely so the
     editor can warn when the ground moves under a queued edit. */
  intent._from = liveOn() ? 'game' : 'save';
  S.pending.push(intent);
  render();
}
function unqueue(pred) { S.pending = S.pending.filter(p => !pred(p)); render(); }
const pendingFor = pred => S.pending.filter(pred);

/* Effective value of a field once pending edits are taken into account. */
function pendStat(merc, stat, fallback) {
  const p = [...S.pending].reverse().find(i => i.kind === 'stat' && i.merc === merc && i.stat === stat);
  return p ? p.value : fallback;
}
function pendField(merc, field, fallback) {
  const p = [...S.pending].reverse().find(i => i.kind === 'merc_field' && i.merc === merc && i.field === field);
  return p ? p.value : fallback;
}
function pendPerks(merc, fallback) {
  const p = [...S.pending].reverse().find(i => i.kind === 'perks' && i.merc === merc);
  return p ? p.perks : fallback;
}
function pendMoney(fallback) {
  const p = [...S.pending].reverse().find(i => i.kind === 'money');
  return p ? p.value : fallback;
}

/* ── toasts ────────────────────────────────────────────────────────── */
function toast(title, detail, kind) {
  const t = h('div.toast' + (kind ? '.' + kind : ''),
    h('div', h('div.t', title), detail ? h('div.d', detail) : null));
  $('#toasts').append(t);
  setTimeout(() => { t.style.transition = 'opacity .3s'; t.style.opacity = '0'; }, kind === 'bad' ? 8000 : 3600);
  setTimeout(() => t.remove(), kind === 'bad' ? 8400 : 4000);
}

/* ── modal ─────────────────────────────────────────────────────────── */
function modal({ title, body, footer, wide }) {
  const root = $('#modal-root');
  const close = () => { root.innerHTML = ''; document.removeEventListener('keydown', esc); };
  const esc = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', esc);
  const box = h('div.modal', wide ? { style: { width: 'min(860px,94vw)' } } : null,
    h('div.modal-h', h('h2', title), h('div.spacer'), h('button.btn.ghost.sm', { onclick: close }, '✕')),
    h('div.modal-b', body),
    footer ? h('div.modal-f', footer) : null);
  root.append(h('div.scrim', { onclick: e => { if (e.target.classList.contains('scrim')) close(); } }, box));
  const f = box.querySelector('input');
  if (f) setTimeout(() => f.focus(), 30);
  return close;
}

/* A yes/no in front of something that cannot be undone. Built on modal()
   rather than window.confirm, which a webview will not always show. */
function confirmThen(title, detail, go, yes) {
  const close = modal({
    title: title,
    body: h('p.dim', detail),
    footer: [
      h('button.btn', { onclick: () => close() }, 'Cancel'),
      h('button.btn.primary', {
        onclick: () => { close(); go(); },
      }, yes || 'Do it'),
    ],
  });
}

/* ── the black box ──────────────────────────────────────────────────
 *
 * Records only failures, and records state rather than pictures. A screenshot
 * every minute would be a lot of images of nothing happening, and the moment
 * that matters would land between two of them.
 *
 * `report` also covers the failure this project keeps hitting: something that
 * says it worked and changed nothing. A crash announces itself. A silent no-op
 * looks exactly like success, which is why it survives testing.
 */
function diagContext() {
  const sum = S.sum || {};
  return {
    route: S.route,
    save: S.path,
    game: sum.game || 'ja3',
    merc: S.sel,
    pending: (S.pending || []).length,
    live: S.live ? { state: S.live.state, ingame: S.live.ingame,
                     can_edit: S.live.can_edit,
                     mercs: (S.live.mercs || []).length } : null,
    url: location.hash || '',
  };
}

function report(kind, detail, extra) {
  try {
    fetch('api/diag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign(
        { kind: kind, detail: String(detail) }, diagContext(), extra || {})),
    }).catch(() => {});
  } catch (e) { /* a broken logger must never break the editor */ }
}

/* the tester: "ever so often when I maximise it, it goes completely black and never
   refreshes". That is the webview losing its drawing surface, not the page
   breaking - so nothing above catches it: no error fires, no request fails,
   the DOM is still perfectly intact. It is invisible to everything except a
   pair of eyes.
   What can be recorded is the circumstance. After a resize settles, check the
   page still measures like a page. If it does and the screen is black anyway,
   that itself is the finding - it says the fault is below us, in the webview
   or the graphics driver, and rules out our own layout collapsing. */
let resizeSettle = null;
window.addEventListener('resize', () => {
  clearTimeout(resizeSettle);
  resizeSettle = setTimeout(() => {
    const main = document.querySelector('main');
    const box = main ? main.getBoundingClientRect() : null;
    const blank = !box || box.height < 20 || box.width < 20;
    if (blank) {
      report('layout-collapsed-after-resize',
             'the page measures empty after a resize',
             { w: innerWidth, h: innerHeight,
               main: box ? Math.round(box.width) + 'x' + Math.round(box.height) : 'none' });
    }
    /* Cheap and harmless either way: ask for a repaint. If the surface is
       alive and merely stale this fixes it; if it is dead, nothing in
       JavaScript could have. */
    document.body.style.transform = 'translateZ(0)';
    requestAnimationFrame(() => { document.body.style.transform = ''; });
  }, 350);
});

window.addEventListener('error', e =>
  report('js-error', e.message, { where: (e.filename || '') + ':' + e.lineno,
                                  stack: (e.error && e.error.stack || '').slice(0, 1200) }));
window.addEventListener('unhandledrejection', e =>
  report('promise-rejected', (e.reason && e.reason.message) || e.reason));

/* Something the game accepted but that changed nothing. Called by liveSend
   with the state either side of the command. */
function reportNoOp(op, value, before, after) {
  report('did-nothing', op + ' reported success but nothing changed',
         { op: op, value: value, before: before, after: after });
}

/* ── data loading ──────────────────────────────────────────────────── */
async function boot() {
  try {
    S.cat = await api('/api/catalog');
    S.icons = await fetch('icons/index.json').then(r => r.ok ? r.json() : {}).catch(() => ({}));
    S.portraits = await fetch('portraits/index.json').then(r => r.ok ? r.json() : {}).catch(() => ({}));
    S.ja2icons = await fetch('ja2icons/index.json').then(r => r.ok ? r.json() : {}).catch(() => ({}));
    await refreshFaces();
    S.game = await api('/api/game-status', {}).catch(() => null);
    await loadFolders();
    watchGameSearch();
    const d = await api('/api/saves');
    S.saves = d.saves;
    if (!S.saves.length) { S.err = 'no-saves'; render(); return; }
    /* Open the newest save we can actually READ. The list shows every game,
       so the newest overall may be one this build cannot open yet - booting
       straight into an error message is a poor greeting. */
    const first = S.saves.find(s => s.openable !== false) || S.saves[0];
    await open(first.path);
  } catch (e) { S.err = e.message; render(); }
}

async function open(path, reload) {
  S.busy = true; render();
  try {
    S.sum = await api('/api/save?path=' + encodeURIComponent(path) + (reload ? '&reload=1' : ''));
    S.path = path;
    S.pending = [];
    if (!S.sel || !S.sum.mercs.some(m => m.id === S.sel)) S.sel = (S.sum.mercs[0] || {}).id || null;
    S.mercFilter = '';
    setPageTitle();
    S.err = null;
  } catch (e) { S.err = e.message; toast('Could not read that save', e.message, 'bad'); }
  S.busy = false; render();
}

/* The window is named before any save is open, so it cannot say which game
   this is without risking the wrong one. Name it once we actually know - and
   take the label from the save listing rather than keeping a list of game
   names here, which is the whole point of not naming games in this file. */
function setPageTitle() {
  const s = (S.saves || []).find(x => x.path === S.path);
  const game = s && s.game_title;
  document.title = game ? 'JAPE 3000 — ' + game : 'JAPE 3000';
}

/* ── save / discard ────────────────────────────────────────────────── */
async function writeSave() {
  if (!S.pending.length) return;
  S.busy = true; render();
  try {
    const clean = S.pending.map(i => Object.fromEntries(
      Object.entries(i).filter(([k]) => !k.startsWith('_'))));
    const r = await api('/api/apply', { path: S.path, intents: clean });
    toast('Save written', r.log.join('\n') + '\n\nBackup: ' + (r.result.backup || '(none)'), 'good');
    S.pending = [];
    await open(S.path, true);
  } catch (e) {
    toast('Nothing was written', e.message, 'bad');
    S.busy = false; render();
  }
}

/* ── views ─────────────────────────────────────────────────────────── */
/* Ask the SAVE what it supports, never which game it is. A reader that cannot
   back a section says so in summary.supports and the section disappears -
   which is why there is no game name anywhere in this file.

   Only an explicit true counts. This used to read `!== false`, so a feature
   nobody remembered to declare was silently opted IN - which is how the
   Portrait button appeared on a Jagged Alliance 2 merc and offered to write
   into Jagged Alliance 3's mods folder. Now a forgotten feature hides a
   section that should have shown: visible, and harmless. See features.py. */
function can(feature) {
  const sup = S.sum && S.sum.supports;
  if (!sup) return true;                 // a reader that declares nothing
  return sup[feature] === true;
}

const NAV = [
  /* No group: the Overview spans all three, so claiming one would misdescribe
     the other two thirds of the page. Its cards carry their own labels. */
  { id: 'overview', ic: '◈', name: 'Overview' },

  { id: 'party', ic: '❖', name: 'Party', grp: 'any' },

  { id: 'roster', ic: '☰', name: 'Recruit', grp: 'closed', needs: 'roster' },
  { id: 'create', ic: '✦', name: 'Create merc', grp: 'closed', needs: 'new_merc' },
  { id: 'stash', ic: '▣', name: 'Sectors', grp: 'closed', needs: 'sectors' },
  { id: 'cheats', ic: '⚑', name: 'Cheats', grp: 'closed', needs: 'cheats' },

  { id: 'live', ic: '⌁', name: 'Live link', grp: 'run', needs: 'live' },
  { id: 'mapedit', ic: '✥', name: 'Map editor', grp: 'run', needs: 'live' },
];

/* Named for the CONDITION, not the jargon. In the tester's order: what you can only
   do with the game shut, what you can do either way, what needs it running. */
const NAV_GROUPS = [
  { id: 'closed', label: 'Game closed' },
  { id: 'any', label: 'Anytime' },
  { id: 'run', label: 'Game running' },
];

function navLink(n) {
  const count = n.id === 'party' ? (S.sum ? S.sum.mercs.length : null)
    : n.id === 'roster' ? (S.sum ? S.sum.roster.filter(r => !r.hired).length : null)
    : null;
  return h('a' + (S.route === n.id ? '.on' : ''),
    { onclick: () => { S.route = n.id; render(); } },
    h('span.ic', n.ic), n.name,
    count !== null ? h('span.count', String(count)) : null);
}

function renderNav() {
  const nav = $('#nav');
  nav.innerHTML = '';
  const shown = NAV.filter(n => !n.needs || can(n.needs));

  const loose = shown.filter(n => !n.grp);
  if (loose.length) {
    nav.append(h('div.nav-label', 'Campaign'));
    loose.forEach(n => nav.append(navLink(n)));
  }

  const connected = !!(S.live && S.live.can_edit);
  NAV_GROUPS.forEach(grp => {
    const items = shown.filter(n => n.grp === grp.id);
    if (!items.length) return;
    nav.append(h('div.nav-label', grp.label));
    /* Say why the group is quiet rather than disabling it. The Live link page
       is where the add-on gets installed, so making it unreachable exactly
       when somebody needs it would be the wrong kind of tidy. */
    if (grp.id === 'run' && !connected)
      nav.append(h('div.nav-note', 'Start the game and these switch on'));
    items.forEach(n => nav.append(navLink(n)));
  });

  nav.append(h('div.nav-label', 'Tools'));
  nav.append(h('a' + (S.route === 'about' ? '.on' : ''),
    { onclick: () => { S.route = 'about'; render(); } }, h('span.ic', '?'), 'How this works'));
}

function viewOverview() {
  const m = S.sum.meta;
  const cur = pendMoney(S.sum.money);
  return [
    h('h1', m.playthrough_name || m.displayname || S.sum.file),
    h('p.sub', S.sum.read_only
      ? 'This save can be read but not changed yet, so nothing here will be written.'
      : 'Everything here is written straight into the save file. A timestamped backup is made every time you write.'),
    S.sum.warnings.length ? h('div.banner', '⚠ ' + S.sum.warnings.join(' · ')) : null,
    h('div.card',
      h('div.hero',
        h('div.hero-shot', { style: { backgroundImage: `url(/api/screenshot?path=${encodeURIComponent(S.path)})` } }),
        h('div.hero-b',
          h('div.hero-title', m.displayname || S.sum.file),
          h('div.facts',
            can('sectors') ? fact('Sector', m.map || '—') : null,
            fact('Day', gameDay(m.campaign_time) ? 'Day ' + gameDay(m.campaign_time) : (m.game_date || '—')),
            m.difficulty ? fact('Difficulty', m.difficulty) : null,
            can('squads') ? fact('Squads', String(S.sum.squads.filter(s => s.player).length)) : null,
            fact('On the team', String(m.team ?? S.sum.mercs.length)),
            m.daily_income == null ? null : fact('Daily income', money(m.daily_income))),
          h('div',
            h('div.fact', h('div.k', 'Money'),
              h('div.money-row', { style: { marginTop: '6px' } },
                h('input.money-in', {
                  type: 'number', value: cur, min: 0, max: 1000000000,
                  onchange: e => queue({ kind: 'money', value: clamp(parseInt(e.target.value || 0, 10), 0, 1000000000) }),
                }),
                [10000, 100000, 1000000].map(v =>
                  h('button.btn.sm', { onclick: () => queue({ kind: 'money', value: cur + v }) }, '+' + money(v))),
                cur !== S.sum.money
                  ? h('button.btn.ghost.sm', { onclick: () => unqueue(i => i.kind === 'money') }, 'reset')
                  : null)))))),
    !can('squads') ? null :
    h('div.card',
      h('div.card-h', h('h2', 'Squads'), h('span.hint', 'player squads in this campaign')),
      h('div.card-b',
        S.sum.squads.filter(s => s.player).map(sq =>
          h('div.item', { style: { marginBottom: '6px' } },
            h('div', h('div.nm', sq.name || ('Squad ' + sq.uid)),
              h('div.grp', sq.units.join(', ') || 'empty')),
            h('div.spacer'), h('span.chip', 'Sector ' + (sq.sector || '?')),
            h('span.chip', 'id ' + sq.uid))))),
    requiredModsBanner(),
    suppliesCard(),
    backupsCard(),
    modsCard(),
    gameLogCard(),
    foldersCard(),
  ];
}

/* What this save was played with, checked against what is installed. The game
   also treats an installed-but-switched-off mod as a problem, but that list
   lives in an encrypted account.dat, so it cannot be checked from here. */
function requiredModsBanner() {
  const req = S.sum.required_mods || [];
  if (!req.length) return null;
  const bad = req.filter(r => r.state !== 'ok' && r.state !== 'retired');
  const retired = req.filter(r => r.state === 'retired');
  const label = { missing: 'missing', not_installed: 'not installed', older: 'out of date' };
  const dropping = id => S.pending.some(p => p.kind === 'drop_mod' && p.id === id);
  const counted = req.length - retired.length;
  return h('div',
    retired.length ? h('div.banner.info',
      h('div',
        retired.map(r => h('div', { style: { marginTop: '3px' } },
          h('div', { style: { fontWeight: '600' } },
            'This save still lists ' + (r.title || r.id)),
          h('div', { style: { marginTop: '3px' } }, 'That was ' + r.note + '. '
            + 'Until it is removed, the game may say a mod is missing when it '
            + 'loads this save.'),
          h('div.row', { style: { marginTop: '8px' } },
            dropping(r.id)
              ? h('span.dim', 'Will be removed when you press Write to save.')
              : h('button.btn.sm', {
                onclick: () => queue({ kind: 'drop_mod', id: r.id }),
              }, 'Remove it from this save')))))) : null,
    counted ? h('div.banner' + (bad.length ? '' : '.info'),
      h('div',
        h('div', { style: { fontWeight: '600', marginBottom: bad.length ? '5px' : '0' } },
          bad.length
            ? bad.length + ' of the ' + counted + ' mods this save uses need attention'
            : 'All ' + counted + ' mods this save uses are installed'),
        bad.map(r => h('div', { style: { marginTop: '3px' } },
          '· ' + (r.title || r.id) + ' — ' + label[r.state] + ': ' + r.note)),
        bad.length ? h('div.dim', { style: { marginTop: '6px', fontSize: '12px' } },
          'A mod can also be installed but switched off in the game, which this '
          + 'cannot see — the game keeps that list encrypted.') : null)) : null);
}

/* ── folders ───────────────────────────────────────────────────────── */
/* Everything the editor needs to locate, and a way to correct any of it by
   hand when an install is somewhere unusual. */
/* ── backups ───────────────────────────────────────────────────────── */
/* Every write leaves a timestamped .bak beside the save. This is the only
   safety net the editor has, so it should be reachable without Explorer. */
async function backupsDialog() {
  let rows = [];
  try {
    const r = await api('/api/backups', { path: S.path });
    rows = r.backups || [];
  } catch (e) { return toast('Could not read backups', e.message, 'bad'); }

  const list = h('div');
  const draw = () => {
    list.innerHTML = '';
    if (!rows.length) {
      list.append(h('div.empty',
        'No backups yet. One is written automatically every time you save.'));
      return;
    }
    rows.forEach(b => {
      const detail = h('div.grp', b.name);
      list.append(h('div.item', { style: { marginBottom: '6px' } },
        h('div', { style: { minWidth: '0' } },
          h('div.nm', b.when), detail),
        h('div.spacer'),
        h('span.chip', (b.size / 1024 / 1024).toFixed(1) + ' MB'),
        h('button.btn.sm', {
          onclick: async () => {
            detail.textContent = 'reading…';
            try {
              const d = await api('/api/backup-peek', { backup: b.path });
              detail.textContent = d.ok
                ? [d.save, d.sector, money(d.money),
                   d.mercs + ' mercs: ' + (d.names || []).join(', ')]
                  .filter(Boolean).join('  ·  ')
                : 'could not read: ' + d.error;
            } catch (e) { detail.textContent = e.message; }
          },
        }, 'Peek'),
        h('button.btn.sm.primary', {
          onclick: () => confirmRestore(b),
        }, 'Restore'),
        h('button.btn.ghost.sm.danger', {
          onclick: async () => {
            try {
              const r = await api('/api/backup-delete',
                { backup: b.path, path: S.path });
              rows = r.backups || [];
              draw();
              toast('Backup deleted');
            } catch (e) { toast('Failed', e.message, 'bad'); }
          },
        }, '✕')));
    });
  };
  draw();

  const close = modal({
    title: 'Backups of ' + (S.sum.file || 'this save'),
    wide: true,
    body: [
      h('div.banner.info',
        'One of these is written every time you save, before anything is '
        + 'changed. Restoring keeps your current file as a new backup, so a '
        + 'restore can itself be undone.'),
      list,
    ],
    footer: [h('button.btn.ghost', { onclick: () => close() }, 'Close')],
  });

  function confirmRestore(b) {
    if (S.pending.length) {
      return toast('You have unsaved changes',
        'Write or discard them first, so a restore cannot be confused with them.',
        'bad');
    }
    const close2 = modal({
      title: 'Restore this backup?',
      body: [
        h('div', { style: { lineHeight: '1.7' } },
          h('div', 'This replaces ' + (S.sum.file || 'the save') + ' with the '
            + 'copy from ' + b.when + '.'),
          h('div.dim', { style: { marginTop: '6px' } },
            'Your current file is kept as a new backup first, so this is '
            + 'reversible.')),
      ],
      footer: [
        h('button.btn.ghost', { onclick: () => close2() }, 'Cancel'),
        h('button.btn.primary', {
          onclick: async () => {
            try {
              const r = await api('/api/backup-restore',
                { backup: b.path, path: S.path });
              if (!r.ok) return toast('Restore failed', r.result, 'bad');
              close2(); close();
              await open(S.path, true);
              toast('Restored', 'From ' + b.when
                + '\nYour previous file was kept as a backup.');
            } catch (e) { toast('Restore failed', e.message, 'bad'); }
          },
        }, 'Restore it'),
      ],
    });
  }
}

function backupsCard() {
  return h('div.card',
    h('div.card-h', h('h2', 'Backups'),
      h('span.hint', 'written automatically before every save'),
      h('div.spacer'),
      h('button.btn.sm', { onclick: backupsDialog }, 'Browse & restore')),
    h('div.card-b', h('div.grp',
      'Every write copies the save first. If something goes wrong in game, '
      + 'come here and put an earlier copy back.')));
}

function foldersCard() {
  const f = S.folders || {};
  const line = (label, value, note, actions) =>
    h('div.item', { style: { marginBottom: '6px' } },
      h('div', { style: { minWidth: '0' } },
        h('div.nm', label),
        h('div.grp', value || note || 'not found')),
      h('div.spacer'),
      value ? h('span.chip.good', 'found') : h('span.chip.bad', 'not found'),
      actions);

  return h('div.card',
    h('div.card-h', h('h2', 'Folders'),
      h('span.hint', 'where the editor looks'), h('div.spacer'),
      h('button.btn.sm', { onclick: refindGame }, 'Search again')),
    h('div.card-b',
      /* These two point at the install the mods and portrait routes use, so
         under a save from another game they were naming the wrong game's
         folders. The Saves row below is game-agnostic and stays - it is how
         you point the editor at a save folder it did not find. */
      !(can('mods') || can('portraits')) ? null :
      line('Game install' + ({ steam: ' (Steam)', gog: ' (GOG)' }[f.game_store] || ''),
        f.game_dir,
        f.searching ? 'searching your drives…' : 'needed for mods and custom portraits',
        h('button.btn.sm', { onclick: pickGameFolder }, 'Change')),
      !can('mods') ? null :
      line(f.mods_dir_custom ? 'Mods folder (your choice)' : 'Mods folder', f.mods_dir,
        'not found', [
          f.mods_dir_custom ? h('button.btn.ghost.sm', {
            title: 'Go back to the folder the game itself loads mods from',
            onclick: resetModsFolder,
          }, 'Use default') : null,
          h('button.btn.sm', { onclick: pickModsFolder }, 'Change')]),
      !can('mods') || !(f.workshop_dirs || []).length ? null :
      line('Steam Workshop', f.workshop_dirs.join('   ·   '), '',
        h('span.dim', { style: { fontSize: '12px' } }, 'found automatically')),
      line('Saves', (f.save_dirs || []).join('   ·   '),
        'no save folder found',
        h('button.btn.sm', { onclick: addSaveFolder }, 'Add folder')),
      (f.extra_save_dirs || []).length
        ? h('div', { style: { marginTop: '8px' } },
            h('div.slot-h', 'Folders you added', h('span.line')),
            f.extra_save_dirs.map(d => h('div.item', { style: { marginBottom: '6px' } },
              h('div.grp', d), h('div.spacer'),
              h('button.btn.ghost.sm.danger', {
                onclick: () => forgetSaveFolder(d),
              }, 'Remove'))))
        : null,
      h('div.dim', { style: { fontSize: '12px', marginTop: '10px' } },
        'Choices are remembered in ' + (f.settings_file || 'your AppData folder') + '.')));
}

async function loadFolders() {
  try { S.folders = await api('/api/folders', {}); } catch (e) { S.folders = null; }
}

async function refindGame() {
  try {
    const r = await api('/api/find-game', {});
    await loadFolders();
    S.game = { game_dir: r.game_dir, mods_dir: r.mods_dir };
    S.cat = await api('/api/catalog');
    render();
    if (r.searching) {
      toast('Searching your drives', 'This can take a minute or two. '
        + 'You can keep working meanwhile.');
      watchGameSearch(true);
      return;
    }
    toast(r.ok ? 'Game found' : 'Still not found',
      r.ok ? r.game_dir : 'Use Change to point at the folder holding JA3.exe',
      r.ok ? undefined : 'bad');
  } catch (e) { toast('Search failed', e.message, 'bad'); }
}

/* The drive search runs in the background when the game is not in any place
   an installer recorded. Check back until it lands, then pick up the game's
   mods and Workshop folder without a restart. */
let gameSearchTimer = null;
function watchGameSearch(announce) {
  if (gameSearchTimer || !(S.folders && S.folders.searching)) return;
  gameSearchTimer = setInterval(async () => {
    let st;
    try { st = await api('/api/game-status', {}); } catch (e) { return; }
    if (st.searching) return;
    clearInterval(gameSearchTimer);
    gameSearchTimer = null;
    S.game = st;
    await loadFolders();
    if (st.game_dir) {
      await api('/api/rescan-mods', {}).catch(() => null);
      S.cat = await api('/api/catalog').catch(() => S.cat);
    }
    render();
    if (st.game_dir) toast('Game found', st.game_dir);
    else if (announce) toast('Still not found',
      'Use Change to point at the folder holding JA3.exe', 'bad');
  }, 3000);
}

async function addSaveFolder() {
  try {
    const r = await api('/api/add-save-dir', {});
    if (!r.ok) return r.message !== 'cancelled' && toast('Not a save folder', r.message, 'bad');
    S.saves = r.saves;
    await loadFolders();
    render();
    toast('Save folder added', r.message);
  } catch (e) { toast('Failed', e.message, 'bad'); }
}

async function forgetSaveFolder(folder) {
  try {
    const r = await api('/api/forget-save-dir', { folder });
    S.saves = r.saves;
    await loadFolders();
    render();
    toast('Folder removed');
  } catch (e) { toast('Failed', e.message, 'bad'); }
}

/* ── mod conflicts ─────────────────────────────────────────────────── */
/* Nothing declares what a mod overrides, so this compares what they actually
   contain: the same preset id, or the same global Lua name, defined twice.
   Which one wins depends on load order, so the wording stays descriptive. */
const CONFLICT_LEVEL = {
  will: ['bad', 'Will collide'],
  may: ['warn', 'Might be a problem'],
  note: ['', 'Worth knowing'],
};

async function conflictsDialog() {
  let data;
  const close = modal({
    title: 'Do your mods conflict?',
    wide: true,
    body: [h('div.empty', 'Reading your installed mods…')],
    footer: [],
  });
  try {
    data = await api('/api/mod-conflicts', {});
  } catch (e) {
    close();
    return toast('Could not check mods', e.message, 'bad');
  }
  close();

  const groups = ['will', 'may', 'note']
    .map(lv => [lv, (data.findings || []).filter(f => f.level === lv)])
    .filter(([, list]) => list.length);

  const body = [
    h('div.banner.info',
      'Compared ' + data.checked + ' installed mod'
      + (data.checked === 1 ? '' : 's') + '. This looks for two mods defining '
      + 'the same thing — the same merc or perk id, or the same Lua function. '
      + 'When that happens the game keeps whichever loaded last.'),
  ];

  if (!groups.length) {
    body.push(h('div.empty',
      'Nothing overlapping found. That does not guarantee they get along, '
      + 'but none of them define the same things.'));
  }

  groups.forEach(([lv, list]) => {
    const [cls, label] = CONFLICT_LEVEL[lv];
    body.push(h('div.slot-h', { style: { marginTop: '14px' } }, label,
      h('span.line'),
      h('span.dim', { style: { textTransform: 'none', letterSpacing: 0 } },
        list.length + ' found')));
    list.forEach(f => body.push(
      h('div.item' , { style: { marginBottom: '6px', alignItems: 'flex-start' } },
        h('div', { style: { minWidth: '0' } },
          h('div.row', { style: { gap: '7px' } },
            h('div.nm', f.what),
            h('span.chip' + (cls ? '.' + cls : ''), f.kind)),
          h('div.grp', f.mods.join('  +  ')),
          h('div.grp', { style: { marginTop: '3px' } }, f.detail)))));
  });

  const close2 = modal({
    title: 'Do your mods conflict?',
    wide: true,
    body: body,
    footer: [h('button.btn.ghost', { onclick: () => close2() }, 'Close')],
  });
}

/* ── what the game actually loaded ─────────────────────────────────────
   Everywhere else we infer mod state from folders on disk, which is a guess.
   The game writes what really happened to its log, and that is the only
   statement that settles "is this mod actually doing anything". Our own Live
   Link mod sat there looking installed for a whole day while the game never
   ran a line of it - this panel is what would have caught that. */
async function loadGameLog() {
  try {
    S.gamelog = await api('/api/game-log', {});
  } catch (e) {
    S.gamelog = { error: e.message };
  }
  render();
}

function gameLogCard() {
  /* this reads Jagged Alliance 3's own log to show what it really loaded;
     it says nothing about a save from another game */
  if (!can('mods')) return null;
  const g = S.gamelog;

  if (!g) {
    return h('div.card',
      h('div.card-h', h('h2', 'What the game loaded'),
        h('span.hint', 'read from the game\u2019s own log'),
        h('div.spacer'),
        h('button.btn.sm', { onclick: loadGameLog }, 'Check')),
      h('div.card-b', h('div.grp',
        'The folders on disk only show what is installed. The game\u2019s log '
        + 'shows what it actually loaded, which is not always the same thing.')));
  }

  if (g.error || !g.ran) {
    return h('div.card',
      h('div.card-h', h('h2', 'What the game loaded'), h('div.spacer'),
        h('button.btn.sm', { onclick: loadGameLog }, 'Check again')),
      h('div.card-b', h('div.empty',
        g.error ? g.error
          : 'No mod activity in the last log. Run the game once and check again.')));
  }

  const rows = Object.keys(g.by_id || {}).map(id => ({ id: id, ...g.by_id[id] }));
  rows.sort((a, b) => (b.loaded - a.loaded) || a.title.localeCompare(b.title));
  const on = rows.filter(r => r.loaded).length;

  return h('div.card',
    h('div.card-h', h('h2', 'What the game loaded'),
      h('span.chip' + (on ? '.good' : ''), on + ' of ' + rows.length + ' loaded'),
      h('div.spacer'),
      h('button.btn.sm', { onclick: loadGameLog }, 'Check again')),
    h('div.card-b',
      h('div.grp', { style: { marginBottom: '10px' } },
        'Straight from the game\u2019s last run. A mod can be installed and '
        + 'still not load \u2014 this is the difference.'),
      rows.map(r => h('div.item', { style: { marginBottom: '6px' } },
        h('div', { style: { minWidth: '0' } },
          h('div.nm', r.title),
          h('div.grp', r.id + '  \u00b7  v' + r.version)),
        h('div.spacer'),
        r.loaded ? h('span.chip.good', 'loaded')
          : h('span.chip', 'not loaded'))),
      (g.problems && g.problems.length) ? [
        h('div.slot-h', { style: { marginTop: '14px' } }, 'Reported problems',
          h('span.line')),
        g.problems.map(p => h('div.grp', { style: { fontSize: '12px' } }, p)),
      ] : null));
}

function modsCard() {
  /* the mod list is read from the JA3 install; showing it beside a save from
     another game would be pointing at somebody else's mods folder */
  if (!can('mods')) return null;
  const found = (S.cat.mods || []);
  const installed = found.filter(m => m.installed);
  const notInstalled = found.filter(m => !m.installed);
  const store = { steam: 'Steam', gog: 'GOG' }[(S.folders || {}).game_store];
  const target = S.game && S.game.mods_dir ? S.game.mods_dir : 'the Mods folder';

  const row = md => {
    const a = md.adds || {};
    const bits = [['merc', a.mercs], ['perk', a.perks], ['item', a.items]]
      .filter(([, n]) => n > 0)
      .map(([w, n]) => n + ' ' + w + (n === 1 ? '' : 's'));
    const dupes = (md.other_copies || []).length;
    return h('div.item', { style: { marginBottom: '6px' } },
      h('div', h('div.row', { style: { gap: '7px' } },
          h('div.nm', md.title),
          md.version_text ? h('span.chip', 'v' + md.version_text) : null,
          md.source === 'steam' ? h('span.chip', {
            title: 'Subscribed on the Steam Workshop (item ' + md.workshop_id
              + '). Steam keeps it up to date; unsubscribe in Steam to remove it.',
          }, 'Steam Workshop') : null,
          dupes ? h('span.chip', {
            title: ['Also unpacked at:'].concat(md.other_copies).join('\n'),
          }, '+' + dupes + ' copy') : null),
        h('div.grp', [md.author && ('by ' + md.author), md.folder]
          .filter(Boolean).join('  ·  '))),
      h('div.spacer'),
      bits.length ? h('span.chip.accent', bits.join(', ')) : h('span.chip', 'code only'),
      md.installed ? null : h('button.btn.sm', {
        title: 'Moves this folder into ' + target,
        onclick: () => installMod(md),
      }, 'Move to Mods folder'));
  };

  return h('div.card',
    h('div.card-h', h('h2', 'Mods'),
      h('span.hint', S.game && S.game.mods_dir ? S.game.mods_dir
        : 'mods folder not found'),
      h('div.spacer'),
      h('button.btn.sm', { onclick: pickGameFolder }, 'Set game folder'),
      h('button.btn.sm', { onclick: conflictsDialog }, 'Check conflicts'),
      h('button.btn.sm', { onclick: rescanMods }, 'Rescan')),
    h('div.card-b',
      !S.game || !S.game.game_dir ? h('div.banner',
        'The Jagged Alliance 3 folder has not been found, so the editor cannot '
        + 'tell which mods are installed, and cannot install any. Point it at '
        + 'the folder containing JA3.exe.') : null,

      notInstalled.length ? h('div.banner',
        h('div',
          h('div', { style: { fontWeight: '600', marginBottom: '5px' } },
            'You have ' + notInstalled.length + ' mod'
            + (notInstalled.length === 1 ? '' : 's')
            + ' that ' + (notInstalled.length === 1 ? 'is' : 'are')
            + ' not installed, so the game cannot see '
            + (notInstalled.length === 1 ? 'it' : 'them') + '.'),
          h('div', 'Your ' + (store ? store + ' ' : '') + 'copy of Jagged '
            + 'Alliance 3 loads mods from ' + target + '. The editor can move '
            + (notInstalled.length === 1 ? 'it' : 'them') + ' there for you; '
            + 'nothing is moved unless you press the button.'),
          h('div.row', { style: { marginTop: '8px' } },
            h('button.btn.sm', { onclick: () => installMods(notInstalled) },
              notInstalled.length === 1 ? 'Move it there'
                : 'Move all ' + notInstalled.length + ' there')))) : null,

      h('div.slot-h', 'Installed', h('span.line'),
        h('span.dim', { style: { textTransform: 'none', letterSpacing: 0 } },
          'the game loads these')),
      installed.length ? installed.map(row)
        : h('div.empty', 'No mods installed yet.'),

      notInstalled.length ? [
        h('div.slot-h', { style: { marginTop: '14px' } }, 'Not installed',
          h('span.line'),
          h('span.dim', { style: { textTransform: 'none', letterSpacing: 0 } },
            'found on this computer - the game will not load these')),
        notInstalled.map(row),
      ] : null));
}

async function installMods(list) {
  const moved = [], failed = [];
  for (const md of list) {
    try {
      const r = await api('/api/install-mod', { folder: md.folder });
      (r.ok ? moved : failed).push(r.ok ? md.title : md.title + ': ' + r.message);
    } catch (e) { failed.push(md.title + ': ' + e.message); }
  }
  S.cat = await api('/api/catalog');
  render();
  if (moved.length) {
    toast('Moved ' + moved.length + ' mod' + (moved.length === 1 ? '' : 's'),
      'Turn ' + (moved.length === 1 ? 'it' : 'them') + ' on in the game\'s Mod '
      + 'Manager, then restart the game.', 'good');
  }
  if (failed.length) toast('Not moved', failed.join('\n'), 'bad');
}

async function pickModsFolder() {
  try {
    const r = await api('/api/mods-pick', {});
    if (!r.ok) {
      return r.message !== 'cancelled'
        && toast('Not usable', r.message, 'bad');
    }
    S.game = Object.assign({}, S.game, { mods_dir: r.mods_dir });
    await loadFolders();
    await rescanMods();
    toast('Mods folder set', r.mods_dir);
  } catch (e) { toast('Failed', e.message, 'bad'); }
}

async function resetModsFolder() {
  try {
    const r = await api('/api/mods-reset', {});
    S.game = Object.assign({}, S.game, { mods_dir: r.mods_dir });
    await loadFolders();
    await rescanMods();
  } catch (e) { toast('Failed', e.message, 'bad'); }
}

async function pickGameFolder() {
  try {
    const r = await api('/api/game-pick', {});
    if (!r.ok) return r.message !== 'cancelled' && toast('Not a game folder', r.message, 'bad');
    S.game = { game_dir: r.game_dir, mods_dir: r.mods_dir };
    S.cat = await api('/api/catalog');
    render();
    toast('Game folder set', r.game_dir);
  } catch (e) { toast('Failed', e.message, 'bad'); }
}

async function installMod(md) {
  return installMods([md]);
}

async function rescanMods() {
  try {
    const r = await api('/api/rescan-mods', {});
    S.cat = await api('/api/catalog');
    render();
    toast('Mods rescanned', r.mods.length + ' found · '
      + r.mercs + ' mercs, ' + r.perks + ' perks, ' + r.items + ' items in the catalog');
  } catch (e) { toast('Rescan failed', e.message, 'bad'); }
}

/* ── supplies ──────────────────────────────────────────────────────── */
/* The crafting materials weapon modding eats. Parts and Meds stack to 500 an
   entry; Chip, Lens and Steel Pipe are single-stack, so a count of 10 writes
   ten separate entries - which is what the game itself does. They go into the
   shared squad bag, not onto one merc. */
/* The shared squad bag holds ONE class of item. From the game's SquadBag.lua,
   AddItemsToSquadBag skips anything that is not IsKindOf("SquadBagItem") -
   ResourceItem inherits from it, MiscItem does not. So Parts and Meds go in
   and Microchip, OpticalLens and FineSteelPipe never can, however prominent
   the button is.
   Jager Allianz worked this out from the outside before we did: "those are not
   items that the vanilla squadbag can take, except PARTS should be there hmm,
   but they're not." Parts really should have, and did not, because the live op
   below did not exist. */
const CRAFT_MATS = [
  { id: 'Parts', note: 'Repairs, crafting, weapon mods' },
  { id: 'Meds', note: 'Treating wounds' },
];
/* Real items, but the bag will not take them - they go to a merc. Listed so
   the panel can say so instead of leaving someone guessing. */
const NOT_BAGGABLE = [
  { id: 'Microchip', note: 'Optics and electronic parts' },
  { id: 'OpticalLens', note: 'Scopes and sights' },
  { id: 'FineSteelPipe', note: 'Barrels and frames' },
];
const RESTOCK = { Parts: 200, Meds: 100 };

function bagTotal(itemId) {
  let n = 0;
  (S.sum.squad_bag || []).forEach(e => { if (e.item === itemId) n += (e.amount || 1); });
  pendingFor(i => i.kind === 'bag_add' && i.item === itemId)
    .forEach(i => { n += i.count; });
  return n;
}

/* The game caps a stack and will not carry the excess: MaxStacks in
   AddItemsToSquadBag. Writing past it produced the "10,000 / 500" Jager
   screenshotted - a number the game then has to cope with. The catalogue
   already knows the cap, so use it rather than inventing one. */
function stackCap(itemId) {
  const c = (S.cat.items || []).find(i => i.id === itemId);
  return (c && c.stack) || 1;
}

/* Whatever was typed, bounded by what the game will hold. Saying it was
   capped beats the number silently changing - the second is how somebody
   concludes the editor ignored them. */
function addTyped(m, cat) {
  const cap = stackCap(m.id);
  const raw = parseInt((S.bagAmt || {})[m.id], 10);
  if (!raw || raw < 1) return toast('Type an amount first', null, 'bad');
  const n = clamp(raw, 1, cap);
  const live = addSupply(m.id, n);
  const capped = n < raw;
  /* A cap has to be reported either way - that is the whole point of the
     function - but when it went live, applyLive has already said so, so this
     one only carries the news the other cannot know. */
  if (capped)
    toast('Capped at ' + cap,
          '+' + n + ' ' + (cat.name || m.id)
          + ' - the game will not hold more than ' + cap + ' in one stack');
  else if (!live)
    toast('Queued for the save file', '+' + n + ' ' + (cat.name || m.id));
}

/* Returns truthy when the edit went straight into the running game, so the
   caller can say WHICH and not guess. queue() returns applyLive's promise on
   the live path and nothing on the save path - reading that beats asking
   liveOn() again here, which would be a second copy of the same rule. */
function addSupply(itemId, count) {
  const cap = stackCap(itemId);
  return queue({ kind: 'bag_add', item: itemId,
                 count: clamp(count, 1, cap) });
}

/* Pretty names for the calibers the base game ships. Anything else - a mod's
   own caliber - falls back to its raw id, which is better than being hidden. */
const CALIBER_LABEL = {
  '44CAL': '.44', '50BMG': '.50', '12gauge': '12-gauge', '556': '5.56 mm',
  '762NATO': '7.62 NATO', '762WP': '7.62 WP', '9mm': '9 mm',
};

/* Everything the squad bag will actually take, straight out of the catalogue.
   ResourceItem is Parts and Meds; Ammo is the other 35. Nothing of either
   class stacks to anything but 500, so this is the same set as "stack 500" -
   checked both ways rather than assumed. */
function baggableAmmo() {
  return (S.cat.items || []).filter(i => i.cls === 'Ammo');
}

/* One row of the Supplies panel. Shared by the crafting materials and by every
   round of ammunition, so a fix to the cap, the receipt or the wording lands
   in one place instead of two that slowly disagree. */
function supplyRow(itemId, note) {
  const cat = S.cat.items.find(i => i.id === itemId) || {};
  const m = { id: itemId, note: note };
  return h('div.item', { style: { marginBottom: '6px' } },
    thumb(iconUrl(cat.icon), cat.name, 'thumb.sm'),
    h('div', h('div.nm', cat.name || itemId), note ? h('div.grp', note) : null),
    h('div.spacer'),
    h('span.chip', 'have ' + bagTotal(itemId)),
    /* Presets for the common amounts, then a box for any number at all -
       the tester: "preset numbers so like 50 100 500 and the last one is whatever
       we want to enter manually".
       A preset the game would not keep is left out rather than shown and
       silently clamped, and a typed value is clamped to the same cap with the
       reason said out loud rather than the number quietly changing. */
    [50, 100, 500].filter(n => n <= stackCap(itemId)).map(n =>
      h('button.btn.sm', {
        onclick: () => {
          /* applyLive already announces a live change, so saying anything
             here too would be a second receipt for one action - and the
             wrong one, as the tester's screenshot showed. */
          if (!addSupply(itemId, n))
            toast('Queued for the save file', '+' + n + ' ' + (cat.name || itemId));
        },
      }, '+' + n)),
    h('input', {
      type: 'number', min: 1, max: stackCap(itemId), placeholder: 'any',
      style: { width: '72px' },
      value: S.bagAmt && S.bagAmt[itemId] || '',
      oninput: e => {
        S.bagAmt = S.bagAmt || {};
        S.bagAmt[itemId] = e.target.value;
      },
      onkeydown: e => { if (e.key === 'Enter') addTyped(m, cat); },
    }),
    h('button.btn.sm.primary', { onclick: () => addTyped(m, cat) }, 'Add'),
    h('span.dim', { style: { fontSize: '11px' } }, 'max ' + stackCap(itemId)));
}

function suppliesCard() {
  /* the squad bag is a JA3 idea and needs an item reader behind it */
  if (!can('stash')) return null;
  const known = CRAFT_MATS.filter(m => S.cat.items.some(i => i.id === m.id));
  const ammo = baggableAmmo();
  if (!known.length && !ammo.length) return null;

  const cals = [];
  ammo.forEach(a => { if (!cals.includes(a.caliber)) cals.push(a.caliber); });
  cals.sort((a, b) => String(CALIBER_LABEL[a] || a)
                        .localeCompare(String(CALIBER_LABEL[b] || b)));
  if (S.bagCal === undefined || !cals.includes(S.bagCal)) S.bagCal = cals[0];
  const showing = ammo.filter(a => a.caliber === S.bagCal);

  return h('div.card',
    h('div.card-h', h('h2', 'Supplies'),
      h('span.hint', 'shared squad bag'),
      h('div.spacer'),
      known.length ? h('button.btn.sm.primary', {
        onclick: () => {
          known.forEach(m => addSupply(m.id, RESTOCK[m.id] || 20));
          toast('Restock queued',
            known.map(m => (RESTOCK[m.id] || 20) + ' ' + itemName(m.id)).join(', '));
        },
      /* Named for what it does. It used to say "everything" while filling two
         of the thirty-seven things the bag can hold, and nobody would ask for
         five hundred of each of thirty-five kinds of round by accident. */
      }, 'Restock Parts & Meds') : null),

    h('div.card-b',
      known.map(m => supplyRow(m.id, m.note)),

      /* Three real items that used to sit in this list and could never work.
         Removing them silently would leave somebody hunting for where the
         lenses went, so the panel says it instead. */
      h('p.dim', { style: { fontSize: '11.5px', marginTop: '10px' } },
        'The shared bag only holds supplies like these. ',
        NOT_BAGGABLE.map(m => (S.cat.items.find(i => i.id === m.id) || {}).name
                              || m.id).join(', '),
        ' cannot go in it \u2014 the game keeps those on a merc, so add them '
        + 'to somebody\'s inventory instead.'),

      ammo.length ? h('div', { style: { marginTop: '18px' } },
        h('div.row', { style: { alignItems: 'baseline', gap: '10px' } },
          h('h3', { style: { margin: 0, fontSize: '13px' } }, 'Ammunition'),
          h('span.dim', { style: { fontSize: '11.5px' } },
            ammo.length + ' kinds, and the bag takes all of them')),
        h('div.row.wrap', { style: { gap: '6px', margin: '9px 0 10px' } },
          cals.map(c => h('button.btn.sm' + (c === S.bagCal ? '.primary' : ''),
            { onclick: () => { S.bagCal = c; render(); } },
            CALIBER_LABEL[c] || c))),
        showing.map(a => supplyRow(a.id, null))) : null));
}

const fact = (k, v) => h('div.fact', h('div.k', k), h('div.v', v));

/* How many cards before the roster stops being a list you skim and starts
   being a list you get lost in. JA3 shows a squad; JA2 shows all 156 people in
   the game, and at that size the editor used to sit below the whole grid - so
   every edit meant clicking a face at the top and scrolling past everyone to
   reach the fields. The editor goes FIRST, and a long roster gets its own
   scrolling box so the page itself never sprawls. */
const ROSTER_SCROLLS_ABOVE = 12;

function viewParty() {
  if (!S.sum.mercs.length) return h('div.center', h('div', 'No hired mercs in this save.'));
  /* A merc carrying a 19-slot inventory array is a Jagged Alliance 2
     merc, and that shape wants the dashboard rather than the JA3 card
     list. Decided by the data, not by a game name. */
  if ((S.sum.mercs[0] || {}).inventory) return ja2Dashboard();
  const all = S.sum.mercs;
  const sel = all.find(m => m.id === S.sel) || all[0];

  const q = (S.mercFilter || '').trim().toLowerCase();
  const shown = q
    ? all.filter(m => ((m.name || '') + ' ' + (m.nick || '') + ' ' + mercLabel(m)).toLowerCase().includes(q))
    : all;
  const long = all.length > ROSTER_SCROLLS_ABOVE;

  return [
    h('div.row', h('h1', 'Party'), h('div.spacer'),
      can('import_merc') ? h('button.btn.sm', { onclick: importMerc }, 'Import merc…') : null),
    h('p.sub', can('roster')
      ? 'Stats, perks, health and gear for everyone currently on the team.'
      : 'Every character in this save. Pick one to edit their stats.'),

    /* the editor, before the roster rather than after it */
    h('div#mercdetail', mercDetail(sel)),

    h('div.slot-h', { style: { marginTop: '18px' } },
      long ? all.length + ' characters' : 'Everyone',
      h('span.line'),
      long ? h('input', {
        type: 'text',
        placeholder: 'Find by name…',
        value: S.mercFilter || '',
        style: { maxWidth: '220px' },
        oninput: e => {
          S.mercFilter = e.target.value;
          /* redrawing the whole view would take the focus out of this box
             mid-word, so repaint only the roster and leave the input alone */
          repaintRoster(sel);
        },
      }) : null),

    h('div#roster', long ? {
      style: { maxHeight: '46vh', overflowY: 'auto', paddingRight: '6px' },
      onscroll: e => { S.rosterScroll = e.target.scrollTop; },
    } : null, rosterGrid(shown, sel)),
  ];
}

function rosterGrid(list, sel) {
  /* the node is built detached, so its scroll position can only be restored
     once it is actually in the document */
  requestAnimationFrame(() => {
    const b = document.getElementById('roster');
    if (b && S.rosterScroll) b.scrollTop = S.rosterScroll;
  });
  if (!list.length) return h('div.empty', 'Nobody matches that name.');
  return h('div.grid.g3', list.map(m => mercCard(m, m.id === sel.id)));
}

function repaintRoster(sel) {
  const box = document.getElementById('roster');
  if (!box) return render();
  const q = (S.mercFilter || '').trim().toLowerCase();
  const shown = q
    ? S.sum.mercs.filter(m => ((m.name || '') + ' ' + (m.nick || '') + ' ' + mercLabel(m)).toLowerCase().includes(q))
    : S.sum.mercs;
  box.innerHTML = '';
  box.append(rosterGrid(shown, sel));
}

/* Picking somebody swaps the editor and touches nothing else.
   A full render() would rebuild the roster too, which throws away where you
   had scrolled to - so you would click a face 90 names down and be returned to
   the top of the list every single time. */
function selectMerc(id) {
  S.sel = id;
  const box = document.getElementById('mercdetail');
  const m = S.sum.mercs.find(x => x.id === id);
  if (!box || !m) return render();
  box.innerHTML = '';
  box.append(mercDetail(m));
  document.querySelectorAll('.merc').forEach(c =>
    c.classList.toggle('on', c.dataset.merc === id));
  showDetail();
}

/* Bring the editor back into view after picking somebody. Without this the
   selection changes off-screen and it looks as though the click did nothing.

   The scroll is worked out rather than left to scrollIntoView, which does not
   move the scrolling <main> here - it was measured doing nothing at all while
   the editor sat 248px above the top of the window. */
function showDetail() {
  const el = document.getElementById('mercdetail');
  if (!el) return;
  const sc = el.closest('main');
  if (!sc) return el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  const goal = Math.max(0, el.getBoundingClientRect().top
                           - sc.getBoundingClientRect().top + sc.scrollTop - 12);
  const was = sc.scrollTop;
  sc.scrollTo({ top: goal, behavior: 'smooth' });
  /* Smooth scrolling is quietly ignored by some browsers - measured here doing
     nothing whatsoever, with prefers-reduced-motion unset, so there is nothing
     to feature-detect. If it has not begun to move shortly after, snap instead:
     arriving instantly is worse than gliding, and far better than not arriving. */
  setTimeout(() => {
    if (Math.abs(sc.scrollTop - was) < 2) sc.scrollTop = goal;
  }, 120);
}

function mercCard(m, on) {
  const hp = pendField(m.id, 'HitPoints', m.hp) || 0;
  const mx = pendField(m.id, 'MaxHitPoints', m.max_hp) || 1;
  /* the first five stats this merc actually has - JA3 leads with Health,
     which a JA2 merc has no value for, and a blank column reads as a bug */
  const stats = S.cat.stats.filter(s => (m.stats[s] || {}).value != null).slice(0, 5);
  return h('div.merc' + (on ? '.on' : ''),
    { 'data-merc': m.id, onclick: () => selectMerc(m.id) },
    h('div.merc-h',
      avatar(m.id, m.preset, mercLabel(m), m.is_imp ? '.imp' : ''),
      h('div', h('div.merc-name', mercLabel(m)),
        h('div.merc-sub', mercSub(m))),
      h('div.spacer'),
      /* Jagged Alliance 2 saves have no level or health field located yet.
         Leaving these out beats printing "Lv null", and beats a health bar
         pinned at 0/1 as though the whole roster were dying. */
      m.level == null ? null : h('span.chip.accent', 'Lv ' + m.level)),
    m.hp == null ? null : h('div',
      h('div.row', { style: { justifyContent: 'space-between', marginBottom: '4px' } },
        h('span.dim', { style: { fontSize: '11.5px' } }, 'Health'),
        h('span.mono', { style: { fontSize: '11.5px' } }, hp + ' / ' + mx)),
      h('div.bar.hp', h('i', { style: { width: clamp(hp / mx * 100, 0, 100) + '%' } }))),
    h('div.statmini', stats.map(s =>
      h('div', h('div.n', s.slice(0, 3).toUpperCase()),
        h('div.v', String(pendStat(m.id, s, (m.stats[s] || {}).value ?? '—')))))));
}

/* A mod may declare no Nick and no Name at all - 16 of the 17 IMP Portraits
   mercs are exactly that, so every menu showed a raw id like "IMP_06_mod".

   DISPLAY ONLY, deliberately. The catalog keeps the empty nick the mod really
   declares, because `nick` is also what gets written into a save
   (ja3_save.py sets the Nick property from it). A label we invented here must
   never be able to travel into someone's save file as a merc's real name. */
function mercLabel(m) {
  if (m.nick) return m.nick;
  if (m.name && m.name !== m.id) return m.name;
  return String(m.id || '').replace(/_mod$/, '').replace(/_/g, ' ').trim() || m.id;
}

/* The line under the name: the full name when it adds something, otherwise
   what the merc does. Never the same text twice, never a bare id. */
function mercSub(m) {
  const full = (m.name && m.name !== m.id) ? m.name : '';
  return (full && full !== mercLabel(m)) ? full : (m.spec || m.preset || '');
}

function mercDetail(m) {
  const perks = pendPerks(m.id, m.perks);
  const assignable = new Set(S.cat.perks.filter(p => p.assignable).map(p => p.id));
  const perkName = id => (S.cat.perks.find(p => p.id === id) || {}).name || id;
  const defaults = ((S.cat.mercs.find(x => x.id === m.preset) || {}).starting_perks) || [];
  const hp = pendField(m.id, 'HitPoints', m.hp);
  const mx = pendField(m.id, 'MaxHitPoints', m.max_hp);
  const xp = pendField(m.id, 'Experience', m.xp);
  const lvl = levelForXp(xp);

  return h('div.card',
    h('div.card-h',
      avatar(m.id, m.preset, mercLabel(m), m.is_imp ? '.imp' : ''),
      h('div', h('h2', mercLabel(m)),
        h('div.merc-sub', [mercSub(m), m.preset].filter(Boolean).join(' · '))),
      h('div.spacer'),
      m.wounds ? h('span.chip.bad', m.wounds + ' wound' + (m.wounds > 1 ? 's' : '')) : null,
      /* Only say something about hiring when the reader actually knows. An
         empty hire_status used to fall through to "not hired", which told
         somebody their hired merc was not hired. */
      m.hire_status ? h('span.chip', m.hire_status) : null,
      /* Patch up writes HitPoints, which only a reader that has found the
         health field can apply - on JA2 it queued an intent that was silently
         dropped, so the button looked fine and did nothing. Same signal the
         Condition block uses. */
      m.hp == null ? null : h('button.btn.sm', { onclick: () => healUp(m) }, 'Patch up'),
      h('button.btn.sm', { onclick: () => maxStats(m) }, 'Max stats'),
      can('portraits') ? h('button.btn.sm', { onclick: () => portraitDialog(m) }, 'Portrait') : null,
      can('export_merc') ? h('button.btn.sm', { onclick: () => exportMerc(m) }, 'Export') : null),
    h('div.card-b',
      h('div.grid.g2',
        /* left: identity + condition */
        h('div',
          /* Health, experience and level are JA3 fields. A Jagged Alliance 2
             save reports them as null because their offsets are not known
             yet, and an input box that writes nowhere is worse than no box. */
          m.hp == null ? null : [
            h('div.slot-h', 'Condition', h('span.line')),
            h('div.row', { style: { gap: '14px', flexWrap: 'wrap' } },
              numField('Health', hp, v => queue({ kind: 'merc_field', merc: m.id, field: 'HitPoints', value: v })),
              numField('Max health', mx, v => queue({ kind: 'merc_field', merc: m.id, field: 'MaxHitPoints', value: v })),
              /* Experience is a JA3 idea: it stores points and derives the
                 level. A game that stores the level itself reports no xp, and
                 both the box and the level buttons write through Experience -
                 so they stand down together rather than writing nowhere. */
              m.xp == null ? null :
              numField('Experience', xp, v => queue({ kind: 'merc_field', merc: m.id, field: 'Experience', value: v }), 0, 999999)),
            m.xp == null
              ? (m.level == null ? null : h('div.row', { style: { marginTop: '10px' } },
                  h('span.dim', { style: { fontSize: '12px' } }, 'Level ' + m.level)))
              : h('div.row', { style: { marginTop: '10px', gap: '6px', flexWrap: 'wrap' } },
                  h('span.dim', { style: { fontSize: '12px', marginRight: '4px' } }, 'Level ' + lvl + ' →'),
                  [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(l =>
                    h('button.btn.sm' + (l === lvl ? '.primary' : ''), {
                      onclick: () => queue({ kind: 'merc_field', merc: m.id, field: 'Experience', value: XP_TABLE[l - 1] }),
                    }, String(l)))),
          ],

          !can('perks') ? null :
      h('div.slot-h', 'Perks', h('span.line'),
            defaults.length ? h('button.btn.sm', {
              title: 'Back to the perks this merc is hired with: '
                   + defaults.map(perkName).join(', '),
              onclick: () => {
                queue({ kind: 'perks', merc: m.id, perks: defaults });
                toast('Perks reset', mercLabel(m) + ' back to default');
              },
            }, 'Reset') : null,
            h('button.btn.sm', {
              title: 'Strip every perk, leaving wounds and statuses alone',
              onclick: () => {
                const keep = perks.filter(p => !assignable.has(p));
                queue({ kind: 'perks', merc: m.id, perks: keep });
                toast('Perks cleared', (m.nick || m.id) + ' has a clean sheet');
              },
            }, 'Clear'),
            h('button.btn.sm', { onclick: () => pickPerks(m, perks) }, '+ Add')),
          !can('perks') ? null :
          h('div.row.wrap', { style: { gap: '6px' } },
            perks.length ? perks.map(p =>
              h('span.chip' + (assignable.has(p) ? '.accent' : '') + '.x', {
                title: (S.cat.perks.find(x => x.id === p) || {}).desc || p,
                onclick: () => queue({ kind: 'perks', merc: m.id, perks: perks.filter(x => x !== p) }),
              }, perkName(p), h('span.rm', '✕')))
              : h('div.empty', 'No perks.'))),

        /* right: stats */
        h('div',
          h('div.slot-h', 'Attributes', h('span.line'),
            h('span.dim', { style: { fontSize: '11px', textTransform: 'none', letterSpacing: 0 } },
              liveOn() ? 'going straight into the game'
                : m.is_imp ? 'stored in the save' : 'preset marked ▏')),
          /* only the stats this merc actually has - a slider with no value
             behind it writes nowhere, which is worse than not offering it */
          S.cat.stats.filter(s => (m.stats[s] || {}).value != null)
            .map(s => statRow(m, s)))),


      /* A game whose inventory this build cannot read reports no slots at
         all. Offering "+ Add item" there would be offering a button that
         cannot work, so the whole section stands down. */
      !Object.keys(m.slots || {}).length ? null :
      h('div.slot-h', 'Gear', h('span.line'),
        h('button.btn.sm', { onclick: () => pickItem(m) }, '+ Add item')),
      /* The running game reports stats, health and effects - not inventory.
         So while the link is live this list is still the SAVE's, and the panel
         says so rather than letting a lit badge imply otherwise. Adding an
         item does go straight into the game; only the reading is behind. */
      (!Object.keys(m.slots || {}).length || !liveOn()) ? null :
      h('p.dim', { style: { fontSize: '11.5px', margin: '6px 0 0' } },
        'This list comes from the save file - the running game does not report '
        + 'what a merc is carrying, so it can be behind what you see in game. '
        + 'Adding an item still goes straight into the running game.'),
      Object.keys(m.slots || {}).map(slot => {
        const items = m.slots[slot];
        const adds = pendingFor(i => i.kind === 'item_add' && i.merc === m.id && i.slot === slot);
        const rms = pendingFor(i => i.kind === 'item_remove' && i.merc === m.id && i.slot === slot);
        if (!items.length && !adds.length) return null;
        return h('div',
          h('div.slot-h', { style: { marginTop: '12px' } }, slot, h('span.line')),
          items.map((it, idx) => itemRow(m, slot, it, idx, rms.some(r => r.index === idx))),
          adds.map((a, i) => h('div.item', { style: { borderColor: 'rgba(240,180,41,.4)' } },
            h('span.chip.accent', 'new'), h('div.nm', itemName(a.item)),
            h('div.spacer'),
            h('button.btn.ghost.sm', { onclick: () => unqueue(p => p === a) }, '✕'))));
      })));
}


/* The game keeps perks in three families plus the tiered stat perks; the
   Extended IMP Character Creation mod names them the same way. */
const POOL_NAMES = {
  Talent: 'Signature talents (blue)',
  Tactical: 'Tactical perks',
  Personality: 'Personality perks',
  Quirk: 'Quirks',
  Stat: 'Stat perks',
  Other: 'Other',
};
const poolOrder = () => (S.cat.pool_order || ['Talent','Tactical','Personality','Quirk','Stat','Other']);

/* ── custom portraits ──────────────────────────────────────────────── */
/* The save never holds a portrait - it belongs to the merc's preset - so a
   custom face travels inside the live link DLC, which patches the two
   portrait fields on load. Everything else about the merc is left alone. */
async function portraitDialog(m) {
  let st = null, chosen = null;
  try { st = await api('/api/portrait-status', {}); }
  catch (e) { return toast('Could not check portraits', e.message, 'bad'); }

  const preview = h('div.portrait-preview');
  const bigPreview = h('div.portrait-preview.big');
  const bigBox = h('div', { style: { display: 'none' } },
    bigPreview, h('div.portrait-cap.dim', 'A.I.M. / inventory'));
  const info = h('div');
  const already = (st.assignments || {})[m.preset];

  const drawPreview = (src, big) => {
    preview.innerHTML = '';
    preview.append(src
      ? h('img', { src, alt: '' })
      : h('div.dim', { style: { fontSize: '12.5px' } }, 'No picture chosen yet'));
    bigPreview.innerHTML = '';
    if (big) bigPreview.append(h('img', { src: big, alt: '' }));
    bigBox.style.display = big ? '' : 'none';
  };
  drawPreview(already ? ('api/facepic?merc=' + encodeURIComponent(m.preset) + '&t=' + Date.now())
    : portraitUrl(m.id, m.preset));

  const setInfo = () => {
    info.innerHTML = '';
    if (!st.pillow) {
      info.append(h('div.banner',
        'Image conversion needs Pillow, which is missing from this build.'));
      return;
    }
    info.append(h('div.banner.info',
      'Any picture works. It becomes a ' + st.head_size + '×' + st.head_size
      + ' face for the squad bar and A.I.M. lists, and a standing figure on a '
      + st.big_size + '×' + st.big_size + ' canvas for the inventory, A.I.M. '
      + 'profile and conversations — the same sizes as the game\'s own art.'));
    info.append(h('div.banner' + (st.live_link_installed ? '.info' : ''),
      st.live_link_installed
        ? 'Portraits cannot live in a save, so they travel in the live link '
          + 'add-on you installed. No mod is written. Restart the game after '
          + 'setting one, and it applies to every save.'
        : 'This shows in the editor straight away. To see it in the game, '
          + 'install the live link (Live link page) - portraits travel inside '
          + 'it. The editor never writes a mod.'));
    if (already) {
      info.append(h('div.banner.info', m.nick + ' already has a custom portrait.'));
    }
  };
  setInfo();

  const close = modal({
    title: 'Portrait — ' + (m.nick || m.id),
    body: [
      h('div.row', { style: { gap: '16px', alignItems: 'flex-start' } },
        h('div', preview, h('div.portrait-cap.dim', 'face')),
        bigBox,
        h('div', { style: { flex: '1' } }, info)),
      h('div.row', { style: { gap: '8px', marginTop: '12px' } },
        h('button.btn', {
          onclick: async () => {
            try {
              const r = await api('/api/portrait-pick', {});
              if (!r.path) return;
              chosen = r.path;
              const pv = await api('/api/portrait-preview', { image: chosen });
              drawPreview(pv.head, pv.big);
            } catch (e) { toast('Could not open the picker', e.message, 'bad'); }
          },
        }, 'Choose a picture…'),
        already ? h('button.btn.danger', {
          onclick: async () => {
            try {
              const r = await api('/api/portrait-remove', { merc: m.preset });
              await refreshFaces();
              toast('Portrait removed', r.message || (m.nick + ' back to the default face'));
              close(); render();
            } catch (e) { toast('Failed', e.message, 'bad'); }
          },
        }, 'Remove custom portrait') : null),
    ],
    footer: [
      h('button.btn.ghost', { onclick: () => close() }, 'Close'),
      h('button.btn.primary', {
        onclick: async () => {
          if (!chosen) return toast('Pick a picture first', null, 'bad');
          try {
            const r = await api('/api/portrait-assign', { merc: m.preset, image: chosen });
            await refreshFaces();
            toast('Portrait set', r.message, r.in_game ? 'good' : undefined);
            close(); render();
          } catch (e) { toast('Could not set it', e.message, 'bad'); }
        },
      }, 'Use this picture'),
    ],
  });
}

/* ── merc export / import ──────────────────────────────────────────── */
/* A merc travels as readable JSON: identity, attributes, perks, level and the
   full loadout with attachments. Appearance does not travel - it belongs to
   the preset slot, not the save - so an import wears the target slot's face. */
async function exportMerc(m) {
  let text = '';
  try {
    const r = await api('/api/export', { path: S.path, merc: m.id, gear: true });
    text = JSON.stringify(r.data, null, 1);
  } catch (e) { return toast('Export failed', e.message, 'bad'); }

  const box = h('textarea.code', { readonly: true, rows: 16, value: text });
  const close = modal({
    title: 'Export ' + (m.nick || m.id),
    wide: true,
    body: [
      h('div.banner.info',
        'Everything except looks: attributes, perks, level, and every item with '
        + 'its attachments. Appearance comes from whichever slot it is imported '
        + 'onto, so the face will not follow.'),
      box,
    ],
    footer: [
      h('button.btn.ghost', { onclick: () => close() }, 'Close'),
      h('button.btn', {
        onclick: () => {
          box.select();
          navigator.clipboard.writeText(text)
            .then(() => toast('Copied', 'Paste it anywhere'))
            .catch(() => toast('Select and copy manually'));
        },
      }, 'Copy'),
      h('button.btn.primary', {
        onclick: async () => {
          try {
            const r = await api('/api/save-file', {
              name: (m.nick || m.id).replace(/[^A-Za-z0-9_-]+/g, '_') + '.json',
              text,
            });
            if (r.path) { toast('Saved', r.path); close(); }
          } catch (e) { toast('Could not save', e.message, 'bad'); }
        },
      }, 'Save as file'),
    ],
  });
}

function importMerc() {
  let parsed = null, target = null;
  const box = h('textarea.code', { rows: 10, placeholder: 'Paste a merc file here…' });
  const report = h('div');
  const targetRow = h('div');
  let importBtn = null;

  const targets = () => {
    const free = (S.sum.free_imp_slots || []).map(id => ({ id, label: id + '  (free slot)' }));
    const taken = (S.sum.roster || []).filter(r => r.hired)
      .map(r => ({ id: r.id, label: r.nick + '  (overwrite)' }));
    return free.concat(taken);
  };

  const check = async () => {
    report.innerHTML = '';
    if (!parsed) return;
    try {
      const r = await api('/api/check-import',
        { path: S.path, data: parsed, target });
      r.problems.forEach(p => report.append(h('div.banner', '✕  ' + p)));
      r.warnings.forEach(w => report.append(h('div.banner.info', w)));
      if (importBtn) importBtn.disabled = r.problems.length > 0;
    } catch (e) { report.append(h('div.banner', e.message)); }
  };

  const load = text => {
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      parsed = null;
      report.innerHTML = '';
      report.append(h('div.banner', 'That is not valid JSON.'));
      return;
    }
    const opts = targets();
    target = target || (opts[0] && opts[0].id);
    targetRow.innerHTML = '';
    targetRow.append(
      h('div.row', { style: { gap: '10px', margin: '12px 0' } },
        h('label.fld', 'Import onto',
          h('select', { onchange: e => { target = e.target.value; check(); } },
            opts.map(o => h('option', { value: o.id, selected: o.id === target }, o.label)))),
        h('div', h('div.nm', parsed.nick || parsed.name || '(unnamed)'),
          h('div.grp', 'level ' + (parsed.level || '?')
            + (parsed.gear ? ' · with gear' : ' · no gear')
            + (parsed.source ? ' · from ' + (parsed.source.save || '?') : '')))));
    check();
  };

  box.addEventListener('input', () => load(box.value));

  const close = modal({
    title: 'Import a merc',
    wide: true,
    body: [
      h('div.row', { style: { gap: '8px', marginBottom: '10px' } },
        h('button.btn.sm', {
          onclick: async () => {
            try {
              const r = await api('/api/open-file', {});
              if (r.text) { box.value = r.text; load(r.text); }
            } catch (e) { toast('Could not open', e.message, 'bad'); }
          },
        }, 'Load from file…'),
        h('span.dim', { style: { fontSize: '12.5px' } }, 'or paste below')),
      box, targetRow, report,
    ],
    footer: [
      h('button.btn.ghost', { onclick: () => close() }, 'Cancel'),
      importBtn = h('button.btn.primary', {
        disabled: true,
        onclick: () => {
          queue({ kind: 'import_merc', merc: target, data: parsed });
          queue({ kind: 'squad', merc: target, squad: (S.sum.squads.find(q => q.player) || {}).uid });
          close();
          toast('Import queued', (parsed.nick || parsed.name) + ' onto ' + target);
        },
      }, 'Import'),
    ],
  });
}

/* Jagged Alliance 2 carries its gear as three parallel arrays of 19 slots, not
   as the named containers JA3 uses, so it gets its own panel rather than being
   forced into the JA3 one. Item names are not known yet - the id is shown and
   is editable, which is what JAPE itself does. */
/* Items are typed by name against one shared datalist rather than picked from
   a select per row: nineteen selects of three hundred options each is a lot of
   DOM for a panel most people will use two rows of. A number still works, so
   an item this table has never heard of is still reachable. */
const ja2ItemName = (id) => {
  const hit = (S.sum.items || []).find(x => x.id === id);
  return hit ? hit.name : String(id);
};

function ja2ItemId(text) {
  const t = (text || '').trim();
  if (!t) return 0;
  if (/^[0-9]+$/.test(t)) return +t;
  const list = S.sum.items || [];
  const low = t.toLowerCase();
  const hit = list.find(x => x.name.toLowerCase() === low)
           || list.find(x => x.name.toLowerCase().startsWith(low));
  return hit ? hit.id : null;
}

/* ── portraits ─────────────────────────────────────────────────────── */
/* Base-game faces were pulled out of UI.hpk at build time; a modded merc's
   portrait is read live from the mod folder it came with. Jagged Alliance 2
   faces come out of its Faces.slf the same way - see build_ja2_art.py. */
/* Which mercs have a custom portrait, and a token that changes whenever that
   set does. Loaded at boot and refreshed after every assign or remove.
 *
 * Both are needed for different reasons. Without the SET, a custom portrait is
 * invisible outside the portrait dialog - the resolver below only knew about
 * portraits extracted at build time and portraits belonging to a modded merc,
 * so setting one changed the picture in the game and nowhere in the editor.
 * Without the TOKEN, replacing a portrait leaves the URL identical and the
 * browser serves the copy it already has, which looks exactly like the write
 * having failed. */
let FACES = {};
let FACES_V = 0;

async function refreshFaces() {
  try {
    const st = await api('/api/portrait-status', {});
    FACES = st.assignments || {};
  } catch (e) { FACES = {}; }
  FACES_V++;
  return FACES;
}

/* Every picture a merc could show, best first. Several, because any one of
   them can fail to load - a custom portrait whose file was deleted, a mod
   image that moved - and a merc should then show the next one rather than a
   broken image. */
function portraitUrls(mercId, presetId) {
  const out = [];
  /* A custom portrait wins over everything. It is the one the player chose,
     and it is what the game shows once the live link is installed. */
  const key = presetId || mercId;
  if (FACES && FACES[key]) {
    out.push('api/facepic?merc=' + encodeURIComponent(key) + '&v=' + FACES_V);
  }
  /* JA2 keys faces by profile number, which is the same number the save
     indexes mercs by, so the id resolves straight to a file with no catalog
     entry in between. A JA3 merc has no 'ja2/' key and falls through. */
  const ja2 = S.portraits ? S.portraits['ja2/' + key] : null;
  if (ja2) out.push('portraits/' + ja2 + '.png');
  const cat = S.cat.mercs.find(m => m.id === key);
  for (const ref of cat ? [cat.portrait, cat.big_portrait] : []) {
    if (!ref) continue;
    const file = S.portraits ? S.portraits[ref] : null;
    if (file) out.push('portraits/' + file + '.png');
    else if (cat.mod) out.push('api/modimage?path=' + encodeURIComponent(ref));
  }
  return out;
}

function portraitUrl(mercId, presetId) {
  return portraitUrls(mercId, presetId)[0] || null;
}

function avatar(mercId, presetId, name, extraClass) {
  const urls = portraitUrls(mercId, presetId);
  const cls = 'avatar' + (extraClass || '');
  const fallback = () => h('div.' + cls.replace(/\s+/g, '.'), initials(name));
  if (!urls.length) return fallback();
  let at = 0;
  const img = h('img.' + cls.replace(/\s+/g, '.') + '.face', {
    src: urls[0], alt: name || '', loading: 'lazy', draggable: false,
    onerror: () => {
      at++;
      if (at < urls.length) img.src = urls[at];
      else img.replaceWith(fallback());
    },
  });
  return img;
}

/* ── icons ─────────────────────────────────────────────────────────── */
/* S.icons maps a catalog Icon path to the PNG lifted out of the game's UI
   pack at build time. Anything without art falls back to a glyph. */
function iconUrl(path) {
  const file = path && S.icons ? S.icons[path] : null;
  return file ? 'icons/' + file + '.png' : null;
}
const itemIcon = id => iconUrl((S.cat.items.find(c => c.id === id) || {}).icon);
const compOf = id => (S.cat.components || []).find(c => c.id === id) || null;

function thumb(url, alt, cls) {
  const c = (cls || 'thumb').trim().replace(/\s+/g, '.');   // 'thumb sm' -> 'thumb.sm'
  return url
    ? h('img.' + c, { src: url, alt: alt || '', loading: 'lazy', draggable: false })
    : h('div.' + c + '.noart', '?');
}

function itemRow(m, slot, it, idx, removing) {
  const cat = S.cat.items.find(c => c.id === it.item);
  const cond = it.condition;
  const hasCond = cond !== null && cond !== undefined;
  const mounts = (cat && cat.slots) || [];
  const fitted = pendComponents(m.id, slot, idx, it.components || {});

  return h('div.item.rich', removing ? { style: { opacity: .4 } } : null,
    thumb(itemIcon(it.item), cat && cat.name, 'thumb'),
    h('div.item-main',
      h('div.row', { style: { gap: '8px' } },
        h('div.nm' + (removing ? '.strike' : ''), (cat && cat.name) || it.item),
        it.amount ? h('span.chip', '×' + it.amount) : null,
        it.ammo ? h('span.chip', itemName(it.ammo.item) + ' ' + it.ammo.amount) : null),
      h('div.grp', (cat && cat.group) || ''),
      mounts.length
        ? h('div.mounts', mounts.map(ms => mountSlot(m, slot, idx, ms, fitted)))
        : null),
    h('div.item-side',
      hasCond ? h('div.row', { style: { gap: '6px' } },
        h('span.meta', cond + '%'),
        cond < 100 ? h('button.btn.sm', {
          onclick: () => queue({ kind: 'item_edit', merc: m.id, slot, index: idx, fields: { Condition: 100 } }),
        }, 'Repair') : null) : null,
      removing
        ? h('button.btn.ghost.sm', {
          onclick: () => unqueue(p => p.kind === 'item_remove' && p.merc === m.id
            && p.slot === slot && p.index === idx),
        }, 'undo')
        : h('button.btn.ghost.sm.danger', {
          onclick: () => queue({ kind: 'item_remove', merc: m.id, slot, index: idx }),
        }, '✕')));
}

/* One mount point on a weapon: what's fitted now, click to change it. */
function mountSlot(m, slot, idx, ms, fitted) {
  const id = fitted[ms.slot] || '';
  const c = id ? compOf(id) : null;
  const url = c ? iconUrl(c.icon) : null;
  return h('button.mount' + (id ? '' : '.empty') + (isMountChanged(m.id, slot, idx, ms.slot, id) ? '.changed' : ''), {
    title: (c ? c.name : 'Empty ' + titleOf(ms.slot)) + ' - click to change',
    onclick: () => pickComponent(m, slot, idx, ms, fitted),
  },
    url ? h('img', { src: url, alt: c.name, loading: 'lazy', draggable: false })
      : h('span.mount-plus', id ? '?' : '+'),
    h('span.mount-label', titleOf(ms.slot)));
}

const itemName = id => (S.cat.items.find(c => c.id === id) || {}).name || id;

/* pending attachment state for one item */
function pendComponents(merc, slot, idx, fallback) {
  const p = [...S.pending].reverse().find(i => i.kind === 'item_components'
    && i.merc === merc && i.slot === slot && i.index === idx);
  return p ? p.components : fallback;
}
function isMountChanged(merc, slot, idx, mount, value) {
  const p = S.pending.find(i => i.kind === 'item_components' && i.merc === merc
    && i.slot === slot && i.index === idx);
  if (!p) return false;
  return ((p._before || {})[mount] || '') !== (value || '');
}

/* ── attachment picker ─────────────────────────────────────────────── */
function pickComponent(m, slot, idx, ms, fitted) {
  const current = fitted[ms.slot] || '';
  const options = ms.options.map(compOf).filter(Boolean);
  const choose = (id, off, c) => {
    queue({
      kind: 'item_components', merc: m.id, slot, index: idx,
      components: { ...fitted, [ms.slot]: id },
      _before: pendBefore(m.id, slot, idx, fitted),
    });
    close();
    /* Say it at the moment of the choice, not only in the list above it. */
    if (off && c)
      toast('Not made for this weapon',
            c.name + (c.group ? ' is ' + c.group + '. ' : '. ')
            + 'It will fit and the game will honour it — measured. Nothing '
            + 'in game will mark the weapon as carrying it, so you will only '
            + 'know from here.', 'bad');
  };
  const row = (c, isEmpty, off) => h(
    'div.pick.comp' + ((isEmpty ? !current : current === c.id) ? '.on' : '')
      + (off ? '.offbook' : ''),
    { onclick: () => choose(isEmpty ? '' : c.id, off, c) },
    isEmpty ? h('div.thumb.sm.noart', '–') : thumb(iconUrl(c.icon), c.name, 'thumb sm'),
    h('div', h('div.nm', isEmpty ? 'Nothing fitted' : c.name),
      h('div.ds', isEmpty ? 'Leave this mount empty'
        : [c.effects.map(titleOf).join(', '), c.cost ? money(c.cost) : null]
          .filter(Boolean).join(' · ') || 'No listed effect')),
    /* What it WAS made for. "Desert Eagle Specific" on a Mauser explains
       itself better than any wording of mine. */
    off && c.group ? h('span.chip.warn', c.group) : null,
    isEmpty ? null : h('span.tag', c.id));

  /* Everything else that physically goes in this kind of mount. The engine
     does not enforce a weapon's own list - a modern Suppressor went onto an
     1898 Mauser in the tester's running game and stayed there - so these really do
     fit. They are simply not what the weapon was built for, and the panel says
     so rather than mixing them in. */
  const ids = new Set(ms.options);
  const offbook = (S.cat.components || [])
    .filter(c => c.slot === ms.slot && !ids.has(c.id))
    .sort((a, b) => (a.group || '').localeCompare(b.group || '')
                    || a.name.localeCompare(b.name));

  const close = modal({
    title: titleOf(ms.slot) + ' mount',
    wide: true,
    body: [
      h('div.banner.info', 'This weapon is built for '
        + options.length + ' part' + (options.length === 1 ? '' : 's')
        + ' here.'),
      ms.optional ? row({}, true) : null,
      options.map(c => row(c, false)),

      offbook.length ? h('div', { style: { marginTop: '18px' } },
        h('div.slot-h', 'Not made for this weapon', h('span.line')),
        /* Deliberately unreassuring. The game will never offer these, and
           "it attaches" is not the same as "it works" - that distinction is
           the whole reason this section is separate. */
        /* A label, not an essay. This shows on every mount, every time,
           and the A.I.M. skin renders it in caps - five sentences became a
           wall. The evidence for "they work" (a modern Suppressor forced
           onto an 1898 Mauser: it silenced the rifle, carried its own crit
           bonus, overrode the weapon's own "very noisy" trait and rendered
           on the model - measured in the tester's game 2026-09-09) is history,
           and history belongs here rather than in front of somebody who
           just wants to fit a barrel. */
        h('div.banner.warn', { style: { marginTop: '8px' } },
          'These fit, and the game honours them - effects apply and they '
          + 'render. It simply would never offer them for this weapon, and '
          + 'nothing in game will show that one is fitted.'),
        offbook.map(c => row(c, false, true))) : null,
    ],
    footer: [h('button.btn.ghost', { onclick: () => close() }, 'Close')],
  });
}

/* the attachment set as it was on disk, so "changed" dots stay accurate */
function pendBefore(merc, slot, idx, fallback) {
  const p = S.pending.find(i => i.kind === 'item_components' && i.merc === merc
    && i.slot === slot && i.index === idx);
  return p ? p._before : { ...fallback };
}

function statRow(m, s) {
  const info = m.stats[s] || {};
  const val = pendStat(m.id, s, info.value ?? 0);
  const base = info.preset;
  const set = v => queue({ kind: 'stat', merc: m.id, stat: s, value: clamp(Math.round(v), 1, 100) });
  return h('div.stat-row',
    h('div.nm', s),
    h('div.track',
      h('input', { type: 'range', min: 1, max: 100, value: val, style: { width: '100%' }, oninput: e => set(+e.target.value) }),
      base ? h('div.base', { style: { left: `calc(${base}% - 1px)` }, title: 'preset ' + base }) : null),
    h('input', { type: 'number', min: 1, max: 100, value: val, onchange: e => set(+e.target.value) }));
}

const numField = (label, value, on, lo = 0, hi = 10000) =>
  h('label.fld', label, h('input', {
    type: 'number', value: value ?? 0, min: lo, max: hi, style: { width: '104px' },
    onchange: e => on(clamp(parseInt(e.target.value || 0, 10), lo, hi)),
  }));

const XP_TABLE = [0, 1000, 2500, 4500, 7000, 10000, 13500, 17500, 22000, 27000];
function levelForXp(xp) { let l = 1; XP_TABLE.forEach((n, i) => { if (xp >= n) l = i + 1; }); return l; }

function maxStats(m) {
  S.cat.stats.forEach(s => queue({ kind: 'stat', merc: m.id, stat: s, value: 100 }));
  toast('Attributes set to 100', m.nick || m.id);
}
function healUp(m) {
  const mx = pendField(m.id, 'MaxHitPoints', m.max_hp);
  queue({ kind: 'merc_field', merc: m.id, field: 'HitPoints', value: mx });
  const keep = pendPerks(m.id, m.perks).filter(p => p !== 'Wounded' && p !== 'Bleeding');
  queue({ kind: 'perks', merc: m.id, perks: keep });
  toast('Patched up', (m.nick || m.id) + ' — wounds cleared, health full');
}

/* ── perk picker ───────────────────────────────────────────────────── */
function pickPerks(m, current) {
  // only the perks the character sheet owns are pickable; wounds and quest
  // statuses ride along untouched, handled by the engine
  const assignable = new Set(S.cat.perks.filter(p => p.assignable).map(p => p.id));
  let chosen = new Set(current.filter(p => assignable.has(p)));
  let q = '';
  const list = h('div');
  let applyBtn = null;
  const row = p => h('div.pick.perk' + (chosen.has(p.id) ? '.on' : ''), {
    onclick: () => { chosen.has(p.id) ? chosen.delete(p.id) : chosen.add(p.id); draw(); },
  },
    thumb(iconUrl(p.icon), p.name, 'thumb.perk'),
    h('div', h('div.row', { style: { gap: '7px' } },
        h('div.nm', p.name),
        p.tier ? h('span.chip', p.tier) : null,
        p.stat_req ? h('span.chip', p.stat + ' ' + p.stat_req) : null,
        p.mod ? h('span.chip.mod', { title: 'Added by the mod: ' + p.mod }, p.mod) : null,
        p.risky ? h('span.chip.bad', {
          title: 'Written for one specific merc. It may do nothing on someone '
               + 'else, or misbehave. Other IMP mods hide this one by default.',
        }, 'merc-specific') : null),
      p.desc ? h('div.ds', p.desc) : null),
    h('span.tag', p.id));
  const draw = () => {
    list.innerHTML = '';
    const all = S.cat.perks.filter(p => p.assignable);
    const picked = all.filter(p => chosen.has(p.id));
    if (picked.length) {
      list.append(h('div.slot-h', 'On this merc', h('span.line')));
      picked.forEach(p => list.append(row(p)));
    }
    const pool = all.filter(p => !chosen.has(p.id))
      .filter(p => !q || (p.name + ' ' + p.id + ' ' + p.desc + ' ' + p.group).toLowerCase().includes(q));
    const groups = {};
    pool.forEach(p => (groups[p.pool || 'Other'] ||= []).push(p));
    poolOrder().forEach(g => {
      if (!groups[g]) return;
      list.append(h('div.slot-h', POOL_NAMES[g] || g, h('span.line'),
        h('span.dim', { style: { textTransform: 'none', letterSpacing: 0 } },
          groups[g].length + '')));
      groups[g].sort((a, b) => a.name.localeCompare(b.name)).forEach(p => list.append(row(p)));
    });
    if (!pool.length && !picked.length) list.append(h('div.empty', 'Nothing matches.'));
    if (applyBtn) applyBtn.textContent = 'Apply ' + chosen.size + ' perk' + (chosen.size === 1 ? '' : 's');
  };
  draw();
  const close = modal({
    title: 'Perks — ' + (m.nick || m.id),
    wide: true,
    body: [h('input.search', {
      type: 'text', placeholder: 'Search perks…',
      oninput: e => { q = e.target.value.toLowerCase(); draw(); },
    }), list],
    footer: [
      h('button.btn.ghost', { onclick: () => close() }, 'Cancel'),
      applyBtn = h('button.btn.primary', {
        onclick: () => { queue({ kind: 'perks', merc: m.id, perks: [...chosen] }); close(); },
      }, 'Apply ' + chosen.size + ' perk' + (chosen.size === 1 ? '' : 's')),
    ],
  });
}

/* ── item picker ───────────────────────────────────────────────────── */
const PICK_SLOTS = ['Inventory', 'Handheld A', 'Handheld B', 'Head', 'Torso', 'Legs', 'Pick'];
const fitsSlot = (item, slot) => !item.fits || item.fits.includes(slot);

function pickItem(m, forcedSlot) {
  let q = '', slot = forcedSlot || 'Inventory', pickId = null, amount = 1, cond = 100;
  let showAll = false;
  const list = h('div');
  const status = h('div.picker-status');
  let addBtn = null;

  const draw = () => {
    list.innerHTML = '';
    const usable = S.cat.items.filter(i =>
      !['Debug', 'Beasts', 'Exploding Vehicles'].includes(i.group));
    const forSlot = usable.filter(i => fitsSlot(i, slot));
    const pool = (showAll ? usable : forSlot)
      .filter(i => !q || (i.name + ' ' + i.id + ' ' + i.group).toLowerCase().includes(q))
      .slice(0, 400);

    // the chosen item may no longer belong here after a slot change
    if (pickId && !pool.some(i => i.id === pickId)) pickId = null;

    const groups = {};
    pool.forEach(i => (groups[i.group] ||= []).push(i));
    Object.keys(groups).sort().forEach(g => {
      list.append(h('div.slot-h', g, h('span.line')));
      groups[g].forEach(i => {
        const bad = !fitsSlot(i, slot);
        list.append(h('div.pick' + (pickId === i.id ? '.on' : '') + (bad ? '.misfit' : ''), {
          onclick: () => { pickId = i.id; draw(); },
          title: bad ? 'The game does not put this in ' + slot : '',
        },
          thumb(iconUrl(i.icon), i.name, 'thumb sm'),
          h('div', h('div.row', { style: { gap: '7px' } }, h('div.nm', i.name),
              i.mod ? h('span.chip.mod', i.mod) : null),
            h('div.ds', [i.cls, i.caliber && ('· ' + i.caliber), i.damage && ('· dmg ' + i.damage),
              i.cost && ('· ' + money(i.cost))].filter(Boolean).join(' '))),
          bad ? h('span.chip.bad', 'wrong slot') : null,
          h('span.tag', i.id)));
      });
    });
    if (!pool.length) {
      list.append(h('div.empty', q ? 'Nothing matches that search.'
        : 'Nothing can go in ' + slot + '.'));
    }

    status.innerHTML = '';
    status.append(
      h('span', showAll
        ? 'Showing all ' + usable.length + ' items — ' + forSlot.length + ' of them fit ' + slot
        : 'Showing the ' + forSlot.length + ' items that fit ' + slot),
      h('button.btn.ghost.sm', { onclick: () => { showAll = !showAll; draw(); } },
        showAll ? 'Only what fits' : 'Show everything'));

    if (addBtn) {
      const c = pickId && S.cat.items.find(i => i.id === pickId);
      const bad = c && !fitsSlot(c, slot);
      addBtn.textContent = bad ? 'Add anyway' : 'Add item';
      addBtn.classList.toggle('danger', !!bad);
    }
  };
  draw();

  const close = modal({
    title: 'Add item — ' + (m.nick || m.id),
    wide: true,
    body: [
      h('div.row', { style: { gap: '10px', marginBottom: '10px' } },
        h('label.fld', 'Goes into', h('select', {
          onchange: e => { slot = e.target.value; draw(); },
        }, PICK_SLOTS.map(s => h('option', { value: s, selected: s === slot }, s)))),
        h('label.fld', 'Condition', h('input', {
          type: 'number', value: 100, min: 1, max: 100, style: { width: '90px' },
          onchange: e => { cond = +e.target.value; },
        })),
        h('label.fld', 'Amount (stackables)', h('input', {
          type: 'number', value: 1, min: 1, max: 999, style: { width: '90px' },
          onchange: e => { amount = +e.target.value; },
        }))),
      status,
      h('input.search', {
        type: 'text', placeholder: 'Search…',
        oninput: e => { q = e.target.value.toLowerCase(); draw(); },
      }),
      list,
    ],
    footer: [
      h('button.btn.ghost', { onclick: () => close() }, 'Cancel'),
      addBtn = h('button.btn.primary', {
        onclick: () => {
          if (!pickId) return toast('Pick an item first', null, 'bad');
          const c = S.cat.items.find(i => i.id === pickId);
          const stackable = /Ammo|Stackable|Meds|Resources|ValuablesStack/.test(c.group);
          const intent = { kind: 'item_add', merc: m.id, slot, item: pickId };
          if (stackable) intent.amount = amount; else intent.condition = cond;
          if (!fitsSlot(c, slot)) intent.force = true;
          if (c.caliber && c.mag) {
            const ammo = S.cat.items.find(i => i.group === 'Ammo' && i.caliber === c.caliber);
            if (ammo) intent.ammo = { item: ammo.id, amount: c.mag };
          }
          const live = queue(intent); close();
          /* The wrong-slot warning has to survive either way - it is the one
             thing neither the live receipt nor the queue receipt carries. */
          if (intent.force)
            toast('Wrong slot', c.name + ' → ' + slot
              + '\nThe game does not normally put this there.', 'bad');
          else if (!live)
            toast('Queued for the save file', c.name + ' → ' + slot);
        },
      }, 'Add item'),
    ],
  });
  draw();
}

/* ── roster ────────────────────────────────────────────────────────── */
function viewRoster() {
  const squads = S.sum.squads.filter(s => s.player);
  return [
    h('h1', 'Recruit'),
    h('p.sub', 'Every A.I.M. merc, plus any merc from a mod you have installed. Recruiting hires them and drops them into a squad — no contract, no fee.'),
    h('div.banner.info', 'Mercs join at their normal stats. Edit them afterwards from the Party tab.'),
    S.sum.roster.some(r => r.new_record) ? h('div.banner.info',
      'Mercs marked "new" come from a mod that was added after this campaign started, so the game never gave them a record. '
      + 'Recruiting creates one; their stats, looks and voice come from the mod, so keep it switched on in the game\'s Mod Manager.') : null,
    h('div.grid.g3', S.sum.roster.map(r => {
      const queued = S.pending.some(i => i.kind === 'new_merc' && i.merc === r.id);
      return h('div.merc' + (queued ? '.on' : ''),
        h('div.merc-h',
          avatar(r.id, r.id, r.nick, r.imp ? '.imp' : ''),
          h('div', h('div.merc-name', r.nick), h('div.merc-sub', r.name)),
          h('div.spacer'),
          r.mod ? h('span.chip.mod', { title: 'From ' + r.mod }, 'mod') : null,
          r.new_record ? h('span.chip', { title: 'Not in this save yet — recruiting adds him' }, 'new') : null,
          r.hired ? h('span.chip.good', 'on team') : queued ? h('span.chip.accent', 'joining') : null),
        h('div.row.wrap', { style: { gap: '6px' } },
          r.spec ? h('span.chip', r.spec) : null,
          r.salary ? h('span.chip', money(r.salary) + '/day') : null),
        r.new_record && r.mod_on === false ? h('div.banner', { style: { margin: '6px 0' } },
          r.mod + ' was not switched on when this save was made. Turn it on in the game\'s Mod Manager before loading, or the game cannot build this merc.') : null,
        h('div.statmini', S.cat.stats.filter(s => r.stats[s] != null).slice(0, 5).map(s =>
          h('div', h('div.n', s.slice(0, 3).toUpperCase()), h('div.v', String(r.stats[s]))))),
        r.hired ? null : queued
          ? h('button.btn.sm', { onclick: () => unqueue(i => i.kind === 'new_merc' && i.merc === r.id || (i.kind === 'squad' && i.merc === r.id)) }, 'Cancel')
          : h('div.row', { style: { gap: '6px' } },
            squads.map(sq => h('button.btn.sm', {
              onclick: () => {
                queue({ kind: 'new_merc', merc: r.id, squad: sq.uid });
                queue({ kind: 'squad', merc: r.id, squad: sq.uid });
                toast('Queued for the save file',
                      r.nick + ' → ' + (sq.name || 'squad ' + sq.uid));
              },
            }, '+ ' + (sq.name || 'Squad ' + sq.uid)))));
    })),
  ];
}

/* ── create ────────────────────────────────────────────────────────── */
/* How the game picks a specialisation when it builds an I.M.P.: the highest
   mapped stat wins if it clears the threshold, with the technical skills given
   an edge. Mirrors CreateImpMercData in the game's Lua. */
function derivedSpec(stats) {
  const map = S.cat.spec_from_stat || {};
  const bonus = S.cat.spec_tech_bonus || 0;
  const floor = S.cat.spec_threshold || 74;
  const tech = ['Doctor', 'ExplosiveExpert', 'Mechanic'];
  let best = null, bestVal = null;
  for (const stat in map) {
    const spec = map[stat];
    let v = stats[stat] || 0;
    if (tech.includes(spec)) v += bonus;
    if ((bestVal === null && v > floor) || (bestVal !== null && v > bestVal)) {
      bestVal = v; best = spec;
    }
  }
  return best || 'AllRounder';
}

function viewCreate() {
  const free = S.sum.free_imp_slots;
  const squads = S.sum.squads.filter(s => s.player);
  if (!free.length) {
    return [h('h1', 'Create merc'),
      h('div.banner', 'All six I.M.P. slots in this save are already in use. Free one by editing an existing custom merc in the Party tab instead.')];
  }
  const st = window.__create ||= {
    id: free[0], name: 'John Doe', nick: 'Doe', level: 5,
    spec: 'AllRounder', squad: squads.length ? squads[0].uid : null,
    stats: Object.fromEntries(S.cat.stats.map(s => [s, 80])), perks: [],
  };
  const total = S.cat.stats.reduce((a, s) => a + st.stats[s], 0);
  const derived = derivedSpec(st.stats);

  return [
    h('h1', 'Create merc'),
    h('p.sub', 'Builds a character on one of the six I.M.P. slots the game keeps in every save, then hires them into a squad.'),
    h('div.banner', 'This is the most experimental feature here. The record it writes matches the shape the game uses for an I.M.P. character, but you should load the result and check it before playing on.'),
    h('div.card',
      h('div.card-h', h('h2', 'Identity')),
      h('div.card-b', h('div.row.wrap', { style: { gap: '14px' } },
        h('label.fld', 'I.M.P. slot', h('select', { onchange: e => { st.id = e.target.value; render(); } },
          free.map(f => h('option', { value: f, selected: f === st.id }, f)))),
        h('label.fld', 'Full name', h('input', { type: 'text', value: st.name, style: { width: '200px' }, onchange: e => { st.name = e.target.value; } })),
        h('label.fld', 'Nickname', h('input', { type: 'text', value: st.nick, style: { width: '130px' }, onchange: e => { st.nick = e.target.value; } })),
        h('label.fld', 'Specialisation', h('select', {
          onchange: e => { st.spec = e.target.value; render(); },
        }, (S.cat.specializations || []).map(s =>
          h('option', { value: s, selected: s === st.spec }, titleOf(s))))),
        h('label.fld', 'Level', h('input', { type: 'number', min: 1, max: 10, value: st.level, style: { width: '80px' }, onchange: e => { st.level = clamp(+e.target.value, 1, 10); render(); } })),
        squads.length ? h('label.fld', 'Squad', h('select', { onchange: e => { st.squad = +e.target.value; } },
          squads.map(sq => h('option', { value: sq.uid, selected: sq.uid === st.squad }, sq.name || 'Squad ' + sq.uid)))) : null))),
    st.spec !== derived ? h('div.banner.info',
      'From these stats the game would make this merc a ' + titleOf(derived)
      + '. You have picked ' + titleOf(st.spec) + ', which is written straight '
      + 'into the save and kept.') : null,
    h('div.card',
      h('div.card-h', h('h2', 'Attributes'), h('div.spacer'),
        h('span.chip', 'total ' + total),
        h('button.btn.sm', { onclick: () => { S.cat.stats.forEach(s => st.stats[s] = 100); render(); } }, 'Max all'),
        h('button.btn.sm', { onclick: () => { S.cat.stats.forEach(s => st.stats[s] = 80); render(); } }, 'Reset')),
      h('div.card-b', S.cat.stats.map(s => h('div.stat-row',
        h('div.nm', s),
        h('div.track', h('input', {
          type: 'range', min: 1, max: 100, value: st.stats[s], style: { width: '100%' },
          oninput: e => { st.stats[s] = +e.target.value; render(); },
        })),
        h('input', { type: 'number', min: 1, max: 100, value: st.stats[s], onchange: e => { st.stats[s] = clamp(+e.target.value, 1, 100); render(); } }))))),
    h('div.card',
      h('div.card-h', h('h2', 'Perks'), h('div.spacer'),
        h('button.btn.sm', {
          onclick: () => pickPerksFor(st.perks, list => { st.perks = list; render(); }, 'New merc'),
        }, '+ Add perks')),
      h('div.card-b', h('div.row.wrap', { style: { gap: '6px' } },
        st.perks.length ? st.perks.map(p => h('span.chip.accent.x', {
          onclick: () => { st.perks = st.perks.filter(x => x !== p); render(); },
        }, (S.cat.perks.find(x => x.id === p) || {}).name || p, h('span.rm', '✕')))
          : h('div.empty', 'No perks selected.')))),
    h('div.row', { style: { marginTop: '16px' } },
      h('button.btn.primary', {
        onclick: () => {
          queue({
            kind: 'new_merc', merc: st.id, name: st.name, nick: st.nick,
            stats: { ...st.stats }, level: st.level, spec: st.spec,
            squad: st.squad, perks: [...st.perks], max_hp: st.stats.Health,
          });
          if (st.squad !== null) queue({ kind: 'squad', merc: st.id, squad: st.squad });
          toast('Queued for the save file',
                st.nick + ' will be created on ' + st.id);
        },
      }, 'Queue this merc')),
  ];
}

function pickPerksFor(current, onDone, who) {
  let chosen = new Set(current), q = '';
  const list = h('div');
  const draw = () => {
    list.innerHTML = '';
    const pool = S.cat.perks.filter(p => p.assignable)
      .filter(p => !q || (p.name + ' ' + p.id + ' ' + p.desc).toLowerCase().includes(q));
    pool.forEach(p => list.append(h('div.pick.perk' + (chosen.has(p.id) ? '.on' : ''), {
      onclick: () => { chosen.has(p.id) ? chosen.delete(p.id) : chosen.add(p.id); draw(); },
    }, thumb(iconUrl(p.icon), p.name, 'thumb.perk'),
      h('div', h('div.row', { style: { gap: '7px' } },
          h('div.nm', p.name),
          p.stat_req ? h('span.chip', p.stat + ' ' + p.stat_req) : null,
          p.mod ? h('span.chip.mod', p.mod) : null),
        p.desc ? h('div.ds', p.desc) : null),
      h('span.tag', p.id))));
  };
  draw();
  const close = modal({
    title: 'Perks — ' + who, wide: true,
    body: [h('input.search', { type: 'text', placeholder: 'Search perks…', oninput: e => { q = e.target.value.toLowerCase(); draw(); } }), list],
    footer: [h('button.btn.ghost', { onclick: () => close() }, 'Cancel'),
    h('button.btn.primary', { onclick: () => { onDone([...chosen]); close(); } }, 'Apply')],
  });
}

/* ── cheats ────────────────────────────────────────────────────────── */
/* gv_Cheats lives in the save. CheatEnabled() reads it with no check that
   cheats are switched on, and only starting a NEW campaign resets it, so
   these stick on an ordinary install. */
const CHEAT_NOTES = {
  AlwaysHit: 'Every shot connects.',
  AlwaysMiss: 'Every shot misses. The other half of the testing pair.',
  AutoResolve: 'Resolve fights from the satellite view.',
  BigGuns: 'Developer weapon damage.',
  FastActivity: 'Satellite operations finish far quicker.',
  FreeMeds: 'Treating wounds costs no meds.',
  FreeParts: 'Repairs cost no parts.',
  FullVisibility: 'The whole map is visible, enemies included.',
  SignatureNoCD: 'Signature abilities have no cooldown.',
  SkillCheck: 'Skill checks always pass.',
  StrongDamage: 'Your side hits much harder.',
  WeakDamage: 'Your side hits much softer.',
  oneHpEnemies: 'Every enemy drops to a single hit point.',
  ShowSquadsPower: 'Shows the numeric strength of enemy squads.',
  CombatUIHidden: 'Hides the combat interface, for screenshots.',
  IWUIHidden: 'Hides the in-world interface.',
  OptionalUIHidden: 'Hides optional interface elements.',
  ReplayUIHidden: 'Hides the replay interface.',
};
const SIDE_NOTES = {
  GodMode: 'Cannot be killed, and takes no damage.',
  InfiniteAP: 'Action points never run out.',
  Invulnerability: 'Takes no damage, but can still be downed.',
};
const SIDE_LABEL = {
  player1: 'You', player2: 'Player 2', ally: 'Allies',
  enemy1: 'Enemy 1', enemy2: 'Enemy 2', enemyNeutral: 'Hostile neutrals',
  neutral: 'Neutrals',
};

function pendCheats(fallback) {
  const p = [...S.pending].reverse().find(i => i.kind === 'cheats');
  if (!p) return fallback;
  const out = { flags: { ...fallback.flags }, sides: {}, sides_seen: fallback.sides_seen };
  for (const k in fallback.sides) out.sides[k] = { ...fallback.sides[k] };
  Object.assign(out.flags, p.flags || {});
  for (const k in (p.sides || {})) Object.assign(out.sides[k], p.sides[k]);
  return out;
}

function setCheat(kind, key, side, value) {
  const cur = [...S.pending].reverse().find(i => i.kind === 'cheats');
  const intent = { kind: 'cheats', flags: { ...(cur && cur.flags) }, sides: {} };
  for (const k in ((cur && cur.sides) || {})) intent.sides[k] = { ...cur.sides[k] };
  if (kind === 'flag') intent.flags[key] = value;
  else (intent.sides[key] ||= {})[side] = value;
  queue(intent);
}

const toggle = (on, label, note, onclick) =>
  h('div.item.toggle' + (on ? '.on' : ''), { onclick },
    h('span.switch' + (on ? '.on' : ''), h('i')),
    h('div', h('div.nm', label), note ? h('div.grp', note) : null));

function viewCheats() {
  const c = pendCheats(S.sum.cheats || { flags: {}, sides: {}, sides_seen: [] });
  const flagKeys = Object.keys(c.flags);
  if (!flagKeys.length) {
    return [h('h1', 'Cheats'),
      h('div.banner', 'This save has no gv_Cheats table, so there is nothing to set.')];
  }
  const isUI = k => /UIHidden/.test(k);
  const gameplay = flagKeys.filter(k => !isUI(k)).sort();
  const uiFlags = flagKeys.filter(isUI).sort();
  const sideKeys = Object.keys(c.sides).sort();
  const sides = c.sides_seen || [];

  return [
    h('h1', 'Cheats'),
    h('p.sub', 'The same switches the cheat menu sets, written straight into the save.'),
    h('div.banner.info',
      'These live in the save as gv_Cheats, and the game reads them without checking '
      + 'whether cheats are enabled, so no mod or developer mode is needed. Starting a '
      + 'NEW campaign resets them all; loading a save does not.'),

    sideKeys.length ? h('div.card',
      h('div.card-h', h('h2', 'Per side'), h('span.hint', 'you are player 1')),
      h('div.card-b', sideKeys.map(key => h('div', { style: { marginBottom: '14px' } },
        h('div.slot-h', titleOf(key), h('span.line'),
          h('span.dim', { style: { textTransform: 'none', letterSpacing: 0 } },
            SIDE_NOTES[key] || '')),
        h('div.row.wrap', { style: { gap: '6px' } },
          sides.map(side => {
            const on = !!(c.sides[key] || {})[side];
            return h('button.btn.sm' + (on ? '.primary' : ''), {
              onclick: () => setCheat('side', key, side, !on),
            }, SIDE_LABEL[side] || side);
          })))))) : null,

    h('div.card',
      h('div.card-h', h('h2', 'Gameplay'), h('div.spacer'),
        h('button.btn.sm', {
          onclick: () => {
            const intent = { kind: 'cheats', flags: {}, sides: {} };
            gameplay.concat(uiFlags).forEach(k => { intent.flags[k] = false; });
            sideKeys.forEach(k => {
              intent.sides[k] = {};
              sides.forEach(sd => { intent.sides[k][sd] = false; });
            });
            queue(intent);
            toast('All cheats cleared');
          },
        }, 'Turn everything off')),
      h('div.card-b', gameplay.map(k =>
        toggle(c.flags[k], titleOf(k), CHEAT_NOTES[k],
          () => setCheat('flag', k, null, !c.flags[k]))))),

    uiFlags.length ? h('div.card',
      h('div.card-h', h('h2', 'Interface'), h('span.hint', 'for screenshots')),
      h('div.card-b', uiFlags.map(k =>
        toggle(c.flags[k], titleOf(k), CHEAT_NOTES[k],
          () => setCheat('flag', k, null, !c.flags[k]))))) : null,
  ];
}


/* ── sectors ───────────────────────────────────────────────────────── */
/* Intel and visited are plain reveal flags, so they are safe to set. Owning a
   sector is not: the game routes that through a message that also settles city
   loyalty, guardpost state, mine timers and quest variables, none of which
   happen when the field is written directly. */
function pendSectorFlag(sid, key, fallback) {
  const p = [...S.pending].reverse().find(
    i => i.kind === 'sector_flags' && i.sector === sid && key in i);
  return p ? p[key] : fallback;
}
function pendSectorSide(sid, fallback) {
  const p = [...S.pending].reverse().find(
    i => i.kind === 'sector_side' && i.sector === sid);
  return p ? p.side === 'player1' : fallback;
}
function pendSectorStash(sid) {
  return pendingFor(i => i.kind === 'sector_stash_add' && i.sector === sid);
}

function setSectorFlag(sid, key, value) {
  const cur = [...S.pending].reverse().find(
    i => i.kind === 'sector_flags' && i.sector === sid);
  const intent = { kind: 'sector_flags', sector: sid };
  if (cur) Object.assign(intent, cur);
  intent[key] = value;
  queue(intent);
}

const SECTOR_FILTERS = [
  ['interesting', 'Where you have been'],
  ['owned', 'Owned'],
  ['stash', 'With loot'],
  ['all', 'Every sector'],
];

function viewSectors() {
  const all = S.sum.sectors || [];
  if (!all.length) {
    return [h('h1', 'Sectors'), h('div.banner', 'This save has no sector data.')];
  }
  S.sectorFilter = S.sectorFilter || 'interesting';
  const q = (S.sectorQuery || '').toLowerCase();

  const shown = all.filter(x => {
    if (q && !x.id.toLowerCase().includes(q)) return false;
    switch (S.sectorFilter) {
      case 'owned': return pendSectorSide(x.id, x.owned);
      case 'stash': return x.stash.length || pendSectorStash(x.id).length;
      case 'all': return true;
      default:
        return x.owned || x.intel || x.visited || x.stash.length
          || pendSectorStash(x.id).length;
    }
  });

  const ownedCount = all.filter(x => pendSectorSide(x.id, x.owned)).length;

  return [
    h('h1', 'Sectors'),
    h('p.sub', ownedCount + ' of ' + all.length + ' sectors under your control.'),
    h('div.banner.info',
      'Intel and Visited are safe to flip — nothing in the game keys off them '
      + 'beyond revealing what you can see. Ownership is different, and is '
      + 'behind a confirmation for good reason.'),

    h('div.row', { style: { gap: '8px', marginBottom: '14px', flexWrap: 'wrap' } },
      SECTOR_FILTERS.map(([id, label]) =>
        h('button.btn.sm' + (S.sectorFilter === id ? '.primary' : ''), {
          onclick: () => { S.sectorFilter = id; render(); },
        }, label)),
      h('input', {
        type: 'text', placeholder: 'Find a sector, e.g. H2',
        value: S.sectorQuery || '', style: { width: '170px', marginLeft: 'auto' },
        oninput: e => { S.sectorQuery = e.target.value; render(); },
      })),

    shown.length ? h('div', shown.map(sectorCard))
      : h('div.empty', 'No sectors match.'),
  ];
}

function sectorCard(x) {
  const owned = pendSectorSide(x.id, x.owned);
  const intel = pendSectorFlag(x.id, 'intel', x.intel);
  const visited = pendSectorFlag(x.id, 'visited', x.visited);
  const added = pendSectorStash(x.id);

  const counts = {};
  x.stash.forEach(i => { counts[i.item] = (counts[i.item] || 0) + (i.amount || 1); });

  return h('div.card', { style: { marginBottom: '12px' } },
    h('div.card-h',
      h('h2', 'Sector ' + x.id),
      owned ? h('span.chip.good', 'yours') : h('span.chip', x.side || 'not yours'),
      x.guardpost ? h('span.chip.bad', 'guardpost') : null,
      x.militia_squad ? h('span.chip', 'militia'
        + (x.training_stat ? ' · ' + x.training_stat : '')) : null,
      h('div.spacer'),
      h('button.btn.sm' + (intel ? '.primary' : ''), {
        onclick: () => setSectorFlag(x.id, 'intel', !intel),
      }, 'Intel'),
      h('button.btn.sm' + (visited ? '.primary' : ''), {
        onclick: () => setSectorFlag(x.id, 'visited', !visited),
      }, 'Visited'),
      h('button.btn.sm', { onclick: () => sectorOwnership(x, owned) },
        owned ? 'Give away' : 'Take over'),
      h('button.btn.sm', { onclick: () => addToSectorStash(x) }, '+ Loot')),
    (x.stash.length || added.length) ? h('div.card-b',
      h('div.row.wrap', { style: { gap: '6px' } },
        Object.keys(counts).sort().map(id =>
          h('span.chip', itemName(id), counts[id] > 1 ? ' ×' + counts[id] : '')),
        added.map(a => h('span.chip.accent',
          '+' + a.count + ' ' + itemName(a.item))),
        x.containers > 1 ? h('span.dim', { style: { fontSize: '12px' } },
          'across ' + x.containers + ' containers') : null)) : null);
}

function addToSectorStash(x) {
  let q = '', pickId = null, count = 1;
  const list = h('div');
  const draw = () => {
    list.innerHTML = '';
    const pool = S.cat.items
      .filter(i => !['Debug', 'Beasts', 'Exploding Vehicles'].includes(i.group))
      .filter(i => !q || (i.name + ' ' + i.id + ' ' + i.group).toLowerCase().includes(q))
      .slice(0, 300);
    const groups = {};
    pool.forEach(i => (groups[i.group] ||= []).push(i));
    Object.keys(groups).sort().forEach(g => {
      list.append(h('div.slot-h', g, h('span.line')));
      groups[g].forEach(i => list.append(
        h('div.pick' + (pickId === i.id ? '.on' : ''), {
          onclick: () => { pickId = i.id; draw(); },
        },
          thumb(iconUrl(i.icon), i.name, 'thumb.sm'),
          h('div', h('div.nm', i.name), h('div.ds', i.group)),
          h('span.tag', i.id))));
    });
    if (!pool.length) list.append(h('div.empty', 'Nothing matches.'));
  };
  draw();

  const close = modal({
    title: 'Add loot to sector ' + x.id,
    wide: true,
    body: [
      h('div.banner.info',
        'Goes into the sector\u2019s own stash, the pile you see in the '
        + 'satellite view rather than a crate on the map.'),
      h('label.fld', { style: { marginBottom: '10px' } }, 'How many',
        h('input', { type: 'number', value: 1, min: 1, max: 999,
          style: { width: '90px' }, onchange: e => { count = +e.target.value; } })),
      h('input.search', { type: 'text', placeholder: 'Search items…',
        oninput: e => { q = e.target.value.toLowerCase(); draw(); } }),
      list,
    ],
    footer: [
      h('button.btn.ghost', { onclick: () => close() }, 'Cancel'),
      h('button.btn.primary', {
        onclick: () => {
          if (!pickId) return toast('Pick an item first', null, 'bad');
          const c = S.cat.items.find(i => i.id === pickId);
          const stackable = /Ammo|Stackable|Meds|Resources|ValuablesStack/.test(c.group);
          const intent = { kind: 'sector_stash_add', sector: x.id,
                           item: pickId, count };
          if (!stackable) intent.condition = 100;
          queue(intent); close();
          toast('Queued for the save file',
                count + ' × ' + c.name + ' → sector ' + x.id);
        },
      }, 'Add to stash'),
    ],
  });
}

function sectorOwnership(x, owned) {
  const close = modal({
    title: (owned ? 'Give away' : 'Take over') + ' sector ' + x.id,
    body: [
      h('div.banner',
        h('div',
          h('div', { style: { fontWeight: '600', marginBottom: '5px' } },
            'This skips what the game normally does when a sector changes hands.'),
          h('div', 'Taking a sector in game also settles city loyalty, marks '
            + 'guardpost objectives conquered, starts or stops the mine '
            + 'depletion timer, and updates quest variables. Writing the field '
            + 'straight into the save does none of that.'),
          h('div', { style: { marginTop: '6px' } },
            'The sector will look like yours. Anything that depends on how you '
            + 'got it may disagree.'))),
    ],
    footer: [
      h('button.btn.ghost', { onclick: () => close() }, 'Leave it alone'),
      h('button.btn.danger', {
        onclick: () => {
          queue({ kind: 'sector_side', sector: x.id,
                  side: owned ? 'enemy1' : 'player1', force: true });
          close();
          toast('Queued for the save file',
                'Sector ' + x.id + ' → ' + (owned ? 'enemy1' : 'you'));
        },
      }, owned ? 'Give it away anyway' : 'Take it anyway'),
    ],
  });
}

/* ── about ─────────────────────────────────────────────────────────── */
/* ── live link ─────────────────────────────────────────────────────── */
/* An addition, not a replacement. Everything else in this editor still
   works on save files with the game shut. This panel only does anything
   while the game is running and the companion mod is enabled. */

function renderLiveChip() {
  const chip = $('#livechip');
  const txt = $('#livetxt');
  if (!chip || !txt) return;
  const connected = !!(S.live && S.live.can_edit);
  chip.classList.toggle('on', connected);
  if (!connected) return;
  const live = liveOn();
  txt.textContent = live ? 'LIVE' : 'live paused';
  chip.style.opacity = live ? '1' : '.55';
}

function liveStop() {
  if (S.liveTimer) { clearInterval(S.liveTimer); S.liveTimer = null; }
}

/* Something on the page has the keyboard, or a list is open under the mouse.
   A repaint would take it away mid-word. */
function busyEditing() {
  const el = document.activeElement;
  return !!el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
}

/* Screens that show the running game and therefore want the poll.
   'overview' is here because it carries the live money and the live squad bag:
   without it mergeLive() updated S.sum every two seconds and the page never
   repainted, so HAVE sat on an old number until an unrelated click forced a
   render. Party and Recruit stay out on purpose - the repaint breakage the
   comment in livePoll records was theirs, and they are far more interactive
   than this page, whose every control is an input that busyEditing() already
   protects. */
const LIVE_ROUTES = new Set(['live', 'mapedit', 'overview']);

async function livePoll(redraw) {
  const before = JSON.stringify(S.live || null);
  /* The extender's catalogue is written when the game boots, so it is fetched
     once rather than on every poll. Absent means no extender installed, and
     everything below falls back to what it did before. */
  if (S.liveCat === undefined) {
    S.liveCat = null;
    api('/api/live-catalog').then(c => { S.liveCat = c; }).catch(() => {});
  }
  try {
    S.live = await api('/api/live-status');
  } catch (e) {
    S.live = { state: 'error', message: e.message };
  }
  if (liveOn()) mergeLive();
  renderLiveChip();
  if (!redraw || !LIVE_ROUTES.has(S.route)) return;
  /* This used to repaint every two seconds unconditionally, which rebuilt
     every control on the page while somebody was using it - the merc list
     closed about a second after opening, and half-typed text vanished. Only
     repaint when the game has actually told us something different, and never
     while a control has focus. The status chip is updated above regardless,
     so the connection state is still live either way. */
  if (JSON.stringify(S.live) === before) return;
  if (busyEditing()) return;
  render();
}

/* While the link is live the running game is the truth, so fold its numbers
   into what the page is showing. Without this you would be editing against a
   save that may be hours behind what is actually happening in the game. */
function mergeLive() {
  if (!S.sum || !S.live) return;
  if (S.live.game && S.live.game.money != null) S.sum.money = S.live.game.money;
  /* The squad bag. An empty array is a real answer - the game's bag is
     empty - so only null/undefined, meaning the add-on never reported
     one, leaves the save's figure in place. Without this the Supplies
     panel showed HAVE 15 while the game held 115, under a lit LIVE
     badge, which is the whole reason live has to mean live. */
  if (S.live.game && S.live.game.bag) S.sum.squad_bag = S.live.game.bag;
  const byId = {};
  (S.live.mercs || []).forEach(m => { byId[m.id] = m; });
  (S.sum.mercs || []).forEach(m => {
    const live = byId[m.id];
    if (!live) return;
    if (live.hp != null) m.hp = live.hp;
    if (live.max_hp != null) m.max_hp = live.max_hp;
    if (live.xp != null) m.xp = live.xp;
    /* The game reports every effect, wounds included. Only fold back the
       ones the editor already knew were perks, so a wound never turns up
       looking like something you chose. */
    if (live.effects && m.perks) {
      const known = new Set(m.perks);
      m.perks = live.effects.filter(e => known.has(e) || isPerk(e));
    }
    if (!m.stats) return;
    Object.keys(live.stats || {}).forEach(k => {
      if (m.stats[k]) m.stats[k].value = live.stats[k];
    });
  });
}

/* Polls for as long as the app is open. Every page needs to know whether the
   game is connected, not just the Live link page - that is what lets an edit
   anywhere go straight into the running game. */
function liveStart() {
  liveStop();
  livePoll(true);
  S.liveTimer = setInterval(() => livePoll(LIVE_ROUTES.has(S.route)), 2000);
}

async function liveSend(op, value, label) {
  const snap = () => JSON.stringify({
    money: (S.live || {}).money,
    mercs: ((S.live || {}).mercs || []).map(m => m.id + ':' + m.hp).join(','),
    /* Mods must be in here. Without them a mod toggle changes nothing this
       snapshot can see, so modon/modoff/modreload reported "success but
       nothing changed" EVERY time however well they worked - four false
       records in the diagnostics log. A detector that cries wolf is one
       people learn to skip, which is the whole reason it exists. */
    mods: ((S.live || {}).mods || [])
      .map(m => m.id + ':' + (m.enabled ? 1 : 0) + (m.loaded ? 'L' : '')
                + (m.pending ? 'P' : '')).join(','),
  });
  const before = snap();
  try {
    const r = await api('/api/live-send', { op: op, value: value });
    if (r.status) S.live = r.status;
    if (!r.ok) {
      toast('The game did not do that', r.message, 'bad');
      report('live-refused', r.message, { op: op, value: value });
    } else {
      toast(label || 'Done', r.message);
      /* The game said yes. Did anything actually move? Checked one poll
         later, because the state file is written on the DLC's own tick. */
      setTimeout(async () => {
        await livePoll(false);
        const after = snap();
        if (after === before && !/already|was not/.test(r.message || '')) {
          reportNoOp(op, value, before, after);
        }
      }, 1200);
    }
    render();
  } catch (e) {
    toast('Failed', e.message, 'bad');
    report('live-send-threw', e.message, { op: op, value: value });
  }
}

/* The complete answer to "what has this put on my machine?", including when
   nothing is installed - which is exactly when somebody wants to know. A
   tester spent two days unable to tell which files in his folder were ours. */
function ownedFileList(files, title) {
  files = files || [];
  if (!files.length) return null;
  var here = files.filter(function (f) { return f.exists; });
  return h('details', { style: { marginTop: '10px' } },
    h('summary.dim', { style: { cursor: 'pointer' } },
      title + ' - ' + here.length + ' of ' + files.length + ' present'),
    h('div.dim', { style: { marginTop: '6px', fontSize: '12px' } },
      files.map(function (f) {
        return h('div', { style: { opacity: f.exists ? 1 : 0.45 } },
          (f.exists ? '\u2022 ' : '\u00b7 ') + f.path
            + (f.exists ? '' : '  (not there)'));
      })));
}

async function seInstall(remove) {
  try {
    const r = await api(remove ? '/api/se-uninstall' : '/api/se-install', {});
    toast(r.ok ? (remove ? 'Removed' : 'Installed') : 'Could not do that',
          r.message, r.ok ? null : 'bad');
    livePoll(true);
  } catch (e) { toast('Failed', e.message, 'bad'); }
}

/* The script extender: an optional part of the live link. Only these buttons
   install, update or remove it. It used to install itself whenever the file
   was missing, which cannot tell "never installed" from "removed on purpose" -
   a tester removed it and watched it come back for two days. */
function seBlock(L) {
  if (!L.se_available || !L.game_dir) return null;
  var line, cls = 'dim', buttons = [];
  var install = h('button.btn.sm.primary', { onclick: () => seInstall(false) },
    'Install it');
  var update = h('button.btn.sm.primary', { onclick: () => seInstall(false) },
    'Update it');
  var remove = h('button.btn.ghost.sm.danger', { onclick: () => seInstall(true) },
    'Remove it');
  if (!L.se_installed) {
    if (!L.installed) {
      line = 'Install the add-on above first. This is an optional extra for it.';
    } else {
      line = 'Not installed. You do not need it for the live link to work.';
      buttons.push(install);
    }
  } else if (!L.installed) {
    cls = 'banner info';
    line = 'Installed without the add-on above. It is part of the live link, '
      + 'so on its own it does nothing for this editor.';
    buttons.push(remove);
  } else if (L.se_verdict === 'newer') {
    // Not a failure. The editor is the stale half, and saying so is more
    // use than silence - it names which piece needs rebuilding.
    cls = 'banner info';
    line = 'Your game has a newer script extender than this editor carries, '
      + 'so the editor leaves it alone.';
    buttons.push(remove);
  } else if (!L.se_up_to_date) {
    cls = 'banner info';
    line = 'The copy in your game folder is older than this editor.';
    buttons.push(update, remove);
  } else {
    line = 'Installed and up to date.';
    buttons.push(remove);
  }
  return h('div', { style: { marginTop: '16px' } },
    h('div', { style: { fontWeight: '600', marginBottom: '4px' } },
      'Script extender (optional)'),
    h('p.dim', { style: { margin: '0 0 6px' } },
      'A second file for the live link. It tells this editor what your game '
      + 'actually contains - including anything your mods have added - so '
      + 'modded mercs and items show up here instead of only the ones that came '
      + 'with the game. Nothing installs or updates it except these buttons.'),
    h('div.' + cls.replace(' ', '.'), line),
    buttons.length
      ? h('div', { style: { display: 'flex', gap: '8px', marginTop: '8px' } },
          buttons)
      : null,
    L.se_installed ? h('p.dim', { style: { margin: '6px 0 0', fontSize: '12px' } },
      'Remove takes the file out of your game folder along with everything it '
      + 'has written. It will not come back on its own.') : null,
    ownedFileList(L.se_files, 'What it puts on your machine'));
}

async function liveInstall(remove) {
  try {
    const r = await api(remove ? '/api/live-uninstall' : '/api/live-install', {});
    const title = r.ok ? (remove ? 'Removed' : 'Installed') : 'Could not do that';
    // The CLI prints this message on its own line and needs its "Installed."
    // lead. Here that word is already the toast's title, so it lands twice.
    const body = (r.message || '').indexOf(title + '.') === 0
      ? r.message.slice(title.length + 1).trim() : r.message;
    toast(title, body, r.ok ? null : 'bad');
    livePoll(true);
  } catch (e) { toast('Failed', e.message, 'bad'); }
}

/* "75259 seconds ago" is arithmetic homework. A tester should not have to
   divide to find out the game has been shut since yesterday. */
function agoWords(sec) {
  sec = Math.max(0, Math.round(sec || 0));
  if (sec < 60) return sec + (sec === 1 ? ' second ago' : ' seconds ago');
  const m = Math.round(sec / 60);
  if (m < 60) return m + (m === 1 ? ' minute ago' : ' minutes ago');
  const h = Math.round(sec / 3600);
  if (h < 48) return h + (h === 1 ? ' hour ago' : ' hours ago');
  const d = Math.round(sec / 86400);
  return d + (d === 1 ? ' day ago' : ' days ago');
}

/* One line describing the link, in plain words, for whatever state it is in. */
function liveSummary(L) {
  const g = L.game;
  if (L.state === 'error') return ['bad', 'Could not reach the editor.', L.message || ''];
  if (!L.game_dir)
    return ['bad', 'I do not know where your game is installed.',
      'Point the editor at it with the Folder button at the top.'];
  if (!L.installed && L.state === 'never')
    return ['warn', 'The add-on is not installed yet.',
      'It is one small file that goes in your game folder. Nothing else changes.'];
  if (L.state === 'never')
    return ['warn', 'Installed, but the game has not said hello yet.',
      'Start Jagged Alliance 3. This page will notice on its own.'];
  if (L.state === 'stale')
    return ['warn', 'The game is not running.',
      'Last heard from it ' + agoWords(g ? g.age : 0) + '.'];
  if (g && g.multiplayer)
    return ['bad', 'This is a multiplayer game, so editing is switched off.',
      'Nothing will be changed while you are playing with other people.'];
  if (g && !g.in_game)
    return ['warn', 'Connected - you are at the main menu.',
      'Load or start a campaign and the controls below will switch on.'];
  return ['good', 'Connected, and changes will show up straight away.',
    'You do not need to save or reload.'];
}

function viewLive() {
  const L = S.live || {};
  const g = L.game || null;
  const [tone, headline, detail] = liveSummary(L);
  const canEdit = !!L.can_edit;
  const bannerClass = tone === 'good' ? 'div.banner.ok'
    : tone === 'bad' ? 'div.banner.bad' : 'div.banner.info';

  return [
    h('h1', 'Live link'),
    h('p.sub', 'Change things in the game while it is running, without saving or reloading.'),

    h(bannerClass, h('div', h('strong', headline), detail ? h('div.dim', detail) : null)),

    h('div.card',
      h('div.card-h', h('h2', 'The add-on'),
        h('span.hint', !L.installed ? 'not installed'
          : L.up_to_date ? 'installed' : 'update available')),
      h('div.card-b',
        h('p.dim',
          'The editor builds this itself and puts one small file in your game folder, '
          + 'so it can talk to the game while you play. It does not change how the game '
          + 'works, and the game starts normally with or without it. Remove takes the '
          + 'add-on, the optional script extender below, and every file either has '
          + 'written. Nothing installs itself again afterwards.'),
        L.game_dir
          ? h('div.dim', { style: { marginTop: '8px' } },
              'Your game: ' + L.game_dir)
          : h('div.banner.bad', { style: { marginTop: '8px' } },
              'I could not find your Jagged Alliance 3 folder. Use the Folder button '
              + 'at the top of the window to point the editor at it.'),
        L.installed && !L.up_to_date
          ? h('div.banner.info', { style: { marginTop: '8px' } },
              'The copy in your game folder is older than this editor. Press Update so '
              + 'the two match.')
          : null,
        h('div', { style: { display: 'flex', gap: '8px', marginTop: '10px' } },
          h('button.btn.sm' + (L.installed && L.up_to_date ? '.ghost' : '.primary'),
            { onclick: () => liveInstall(false), disabled: !L.game_dir },
            !L.installed ? 'Install it' : L.up_to_date ? 'Reinstall' : 'Update it'),
          (L.installed || L.se_installed) ? h('button.btn.ghost.sm.danger',
            { onclick: () => liveInstall(true) },
            L.se_installed ? 'Remove both' : 'Remove') : null),
        ownedFileList(L.files, 'What the add-on puts on your machine'),
        seBlock(L))),

    h('div.card',
      h('div.card-h', h('h2', 'What goes live'),
        h('span.hint', canEdit ? 'switched on' : 'waiting for the game')),
      h('div.card-b',
        h('p.dim',
          'There is no separate set of controls here. While the game is connected, '
          + 'the editor itself changes over and the pages you already use go '
          + 'straight into the running game instead of being queued up for the save '
          + 'file. The LIVE mark in the top bar tells you which way round you are, '
          + 'and clicking it puts you back to editing the save.'),
        h('div.dim', { style: { marginTop: '8px' } },
          'Live: money, health, max health, experience, all ten stats, perks, '
          + 'items into the backpack, equipping a slot, healing, and switching '
          + 'mods on and off.'),
        h('div', { style: { marginTop: '10px' } },
          h('div.dim', canEdit
            ? 'Money is ' + (g && g.money != null ? money(g.money) : 'not readable')
              + ' right now.'
            : 'Load a campaign and this switches on.')),
        h('p.dim', { style: { marginTop: '10px' } },
          'Squads, sectors and cheats still go to the save file even while '
          + 'connected. The editor does not pretend otherwise.'))),

    h('div.card',
      h('div.card-h', h('h2', 'Mods'),
        h('span.hint', (L.mods && L.mods.length)
          ? L.mods.length + ' installed' : 'nothing seen yet')),
      h('div.card-b',
        !L.can_mod
          ? h('p.dim', 'Start the game and this switches on. Unlike money, you do '
              + 'not need a campaign loaded - the main menu is fine.')
          : h('div',
              L.mods_pending
                ? h('div.banner.info',
                    h('div',
                      h('strong', L.mods_pending + (L.mods_pending === 1
                        ? ' change is not live yet' : ' changes are not live yet')),
                      h('div.dim', 'The game is still running what it loaded when it '
                        + 'started. Applying reloads them where they are, without '
                        + 'restarting the game.'),
                      h('button.btn.sm.primary', { style: { marginTop: '9px' },
                        onclick: () => liveSend('modreload', null, 'Mods reloaded') },
                        'Apply them now')))
                : null,
              (L.mods || []).length === 0
                ? h('p.dim', 'The game has not reported any mods.')
                : h('div', (L.mods || []).map(m => h('div', {
                    style: { display: 'flex', alignItems: 'center', gap: '10px',
                             padding: '7px 0', borderBottom: '1px solid var(--border)' },
                  },
                  h('div', { style: { flex: 1, minWidth: 0 } },
                    h('div', m.title),
                    h('div.dim', { style: { fontSize: '11.5px' } },
                      m.pending
                        ? (m.enabled ? 'on - not loaded until you apply'
                                     : 'off - still loaded until you apply')
                        : (m.loaded ? 'on and running' : 'off'))),
                  h('button.btn.sm' + (m.enabled ? '.ghost' : '.primary'),
                    { onclick: () => liveSend(m.enabled ? 'modoff' : 'modon', m.id,
                        m.enabled ? 'Switched off' : 'Switched on') },
                    m.enabled ? 'Turn off' : 'Turn on'))))))),


    h('div.card',
      h('div.card-h', h('h2', 'What still goes to the save')),
      h('div.card-b', h('p.dim',
        'Squads, sectors and cheats are written to the save file even while the '
        + 'game is connected, because the running game has no safe way to take '
        + 'them. Editing those works exactly as it always has, and the editor '
        + 'tells you rather than quietly doing something else.'))),
  ];
}

/* Putting people on the map, so a build can be tested without playing up to
   it. The game already has every piece of this - they are just scattered
   across preset panels in the mod editor, and each one acts on whichever unit
   happens to be selected. Doing it by name, from one screen, is the point. */
/* Enemy ids used to be typed by hand, which is why only "LegionRaider" ever
   got placed - there was nothing to guess from. The script extender writes the
   game's own unit table out on boot, so when it is there we can offer real
   names, grouped by who they fight for. When it is not, the free-text box is
   still the fallback rather than a dead control. */
/* ── the unit picker ──────────────────────────────────────────────────────
 *
 * Feedback from Jager Allianz, 2026-09-05, testing the map editor:
 *
 *   "just a list of the mercs alone would be better tbh ... maybe there should
 *    be categories? like mercs, npcs, enemies"
 *   "for example Len you won't get by pushing 'L' bc he's filed under Corporal
 *    Lenartr Anderson etc. Mouse is filed under Anita 'mouse' lastname"
 *
 * Both were the same fault: one flat list of 322 units, sorted and labelled by
 * the game's formal name, so the name you would actually type was the one you
 * could not find. Everything needed to fix it was already in the catalogue and
 * simply unused - `nick` holds "Len" and "Mouse", and `group` already separates
 * mercenaries from Legion from civilians.
 *
 * So: a filter that searches the nickname, the full name AND the internal id,
 * and a list grouped with the people you actually want at the top.
 */
const UNIT_BANDS = [
  ['Mercenaries', g => g === 'MercenariesOld'],
  ['Enemies', g => ['Legion', 'Adonis', 'Army', 'Thugs',
                    'SiegfriedSuperSoldiers'].includes(g)],
  ['Beasts', g => g === 'Beasts'],
  ['NPCs', g => ['NPC', 'NPC_banter', 'Other Combatants'].includes(g)],
  ['Civilians', g => g === 'Civilians'],
];

function unitBand(u) {
  if (u.hireable || u.imp) return 'Mercenaries';
  for (const [name, test] of UNIT_BANDS) if (test(u.group)) return name;
  return 'Other';
}

/* What to call a unit on screen. The nickname first, because that is the name
   a player knows and the name they will type. */
function unitLabel(u, ambiguous) {
  const nick = u.nick && u.nick !== u.name ? u.nick : null;
  const base = nick ? nick + ' \u2014 ' + u.name : (u.name || u.id);
  /* Two Pierres exist - one on the enemy side, one you hire - and the same
     goes for several units. When two share a display name the internal id is
     the only thing that tells them apart, so show it. */
  return ambiguous ? base + '  [' + u.id + ']' : base;
}

function unitMatches(u, q) {
  if (!q) return true;
  return ((u.nick || '') + ' ' + (u.name || '') + ' ' + (u.id || ''))
    .toLowerCase().includes(q);
}

/* One picker, used for placing anybody. `bands` limits which groups appear -
   the enemy control passes the fighting ones so it is a real list rather than
   a box wanting an internal id. */
function unitPicker(key, bands, width, mark) {
  const all = (S.cat.mercs || []).filter(m => m.id);
  const pool = bands ? all.filter(u => bands.includes(unitBand(u))) : all;
  const q = (S[key + 'Q'] || '').trim().toLowerCase();
  const shown = pool.filter(u => unitMatches(u, q));

  const seen = {};
  pool.forEach(u => { const l = unitLabel(u, false); seen[l] = (seen[l] || 0) + 1; });

  const order = UNIT_BANDS.map(b => b[0]).concat(['Other']);
  const groups = order.map(name => ({
    name: name,
    rows: shown.filter(u => unitBand(u) === name),
  })).filter(g => g.rows.length);

  if (!shown.some(u => u.id === S[key])) S[key] = (shown[0] || {}).id || '';

  return h('div.row', { style: { gap: '6px', alignItems: 'center' } },
    h('input', {
      type: 'text', placeholder: 'Find\u2026', value: S[key + 'Q'] || '',
      style: { maxWidth: '120px' },
      /* Repaint only this control, so the box does not lose focus mid-word. */
      oninput: e => {
        S[key + 'Q'] = e.target.value;
        const box = e.target.parentNode;
        const sel = box.querySelector('select');
        const fresh = unitPicker(key, bands, width, mark).querySelector('select');
        if (sel && fresh) box.replaceChild(fresh, sel);
      },
    }),
    h('select', {
      style: { maxWidth: (width || 260) + 'px' },
      onchange: e => { S[key] = e.target.value; },
    }, groups.length
        ? groups.map(g => h('optgroup', { label: g.name + '  (' + g.rows.length + ')' },
            g.rows.map(u => h('option',
              { value: u.id, selected: u.id === S[key] },
              unitLabel(u, seen[unitLabel(u, false)] > 1)
              + (mark && mark.has(u.id) ? '   (already there)' : '')))))
        : [h('option', { value: '' }, 'nobody matches that')]));
}

function enemyList() {
  return (S.liveCat && S.liveCat.enemies) || [];
}

function enemyPicker() {
  const list = enemyList();
  if (!list.length) {
    /* The extender's catalogue gives a richer list - affiliations, hit points,
       mod content - but it is not the only source. The base catalogue already
       knows which units are Legion, Adonis, Army, Thugs and beasts, so there
       is no reason to make anyone type "LegionRaider" from memory while that
       sits unused. Reported by Jager Allianz: "there's no dropdown on enemies
       so I'll have to write in their ids manually". */
    return unitPicker('spawnEnemy', ['Enemies', 'Beasts'], 230);
  }
  const groups = [];
  list.forEach(e => {
    let g = groups.find(x => x.name === e.group);
    if (!g) { g = { name: e.group, rows: [] }; groups.push(g); }
    g.rows.push(e);
  });
  if (!S.spawnEnemy) S.spawnEnemy = list[0].id;
  return h('select', {
    style: { maxWidth: '260px' },
    value: S.spawnEnemy,
    onchange: e => { S.spawnEnemy = e.target.value; },
  }, groups.map(g => h('optgroup', { label: g.name },
      g.rows.map(r => h('option', { value: r.id, selected: r.id === S.spawnEnemy },
        r.name + (r.hp ? '  (' + r.hp + ' hp)' : ''))))));
}

/* Hired is not the same as present. The list used to be everybody on the
   payroll, so it offered to take people "off the map" who were in another
   sector entirely - clicking them did nothing, because there was nothing
   there to remove. The DLC now says which units are actually standing in the
   loaded sector; an older DLC sends nothing, and then we fall back to the old
   behaviour rather than showing an empty list. */
function onTheMap(L) {
  const all = (L && L.mercs) || [];
  if (!all.length) return [];
  if (all.some(m => m.onmap !== null && m.onmap !== undefined)) {
    return all.filter(m => m.onmap);
  }
  return all;
}

/* ── units standing in the sector ─────────────────────────────────────────
 *
 * Jager Allianz, 2026-09-05: "when I spawn in another merc I cannot edit their
 * inventory ... bc they don't show up in the hired party ... so this is not
 * practical. if I can spawn them in I have to be able to edit their inventory."
 *
 * He was right. Every panel in this editor lists the HIRED party; a unit you
 * drop on the map is present without being on the payroll, so it was invisible
 * to all of them. You could put someone down and not give them anything, which
 * left the feature half-useful - testing a build still meant playing up to it.
 *
 * The extender publishes ja3se-sector.txt (everyone on the map, with what they
 * carry) and exposes JA3SE_GiveItem / JA3SE_TakeItem. This is the client for
 * both halves.
 */
async function loadSector() {
  /* No second argument. api() picks POST whenever a body is passed, and an
     EMPTY OBJECT IS TRUTHY in JavaScript - so `api(path, {})` posted to a
     route registered under do_GET and fell through to 'no route
     /api/sector' every single time. 13 of the 30 records in the
     diagnostics log were this one line, and it silently broke the Stash
     and Map Editor screens. */
  try { S.sector = await api('/api/sector'); }
  catch (e) { S.sector = { ok: false, units: [], why: e.message }; }
  return S.sector;
}

/* Renamed from sectorCard. There were TWO functions with that name - this
   one and the real per-sector renderer above - and a later function
   declaration silently overwrites an earlier one in JavaScript. So every
   sector in the list rendered THIS panel instead of itself: five owned
   sectors, five identical "everyone in this sector" boxes, and the actual
   sector renderer unreachable. No error, nothing in the log. */
function liveSectorCard() {
  const sec = S.sector;
  if (!sec) { loadSector().then(render); return null; }

  return h('div.card', { style: { marginTop: '14px' } },
    h('div.card-h', h('h2', 'Everyone in this sector'),
      h('div.spacer'),
      h('button.btn.sm.ghost', {
        onclick: () => loadSector().then(render),
      }, 'Refresh')),
    h('div.card-b',
      /* Unproven, and said plainly. The extender has not watched either call
         touch a real unit, and neither have we - the headless harness cannot
         load a sector. Both sides work against a stub, which is not the same
         thing and must not be described as if it were. */
      h('div.banner.info', { style: { marginBottom: '10px' } },
        h('div', h('strong', 'New, and nobody has watched it work yet'),
          h('div.dim',
            'Reading and equipping units on the map are both built and tested '
            + 'against a stubbed game, but neither has touched a real unit. '
            + 'Try it on a save you do not mind losing, and say what happened.'))),

      !sec.ok
        ? h('p.dim', sec.why || 'no sector data')
        : sectorBody(sec)));
}

function sectorBody(sec) {
  if (!sec.units.length) {
    return h('p.dim', 'The file is there but lists nobody. Load a sector in the '
      + 'game, then press Refresh.');
  }
  const rows = [];

  /* Two units sharing a session id is Jager's collision. It matters here more
     than anywhere: the extender finds a unit BY that id, so an edit lands on
     whichever it meets first - the two-inventory bug again, in a new place.
     Editing is withheld rather than offered and hoped for. */
  if (sec.duplicate_session_ids) {
    rows.push(h('div.banner.bad', { style: { marginBottom: '10px' } },
      h('div', h('strong', 'Two units share an id, so editing is switched off'),
        h('div.dim',
          sec.duplicate_session_ids + ' duplicate session id'
          + (sec.duplicate_session_ids === 1 ? '' : 's')
          + (sec.clashing && sec.clashing.length
              ? ' (' + sec.clashing.join(', ') + ')' : '')
          + '. An edit is aimed by that id, so it could land on the wrong '
          + 'unit. Take one of them off the map and place it again.'))));
  }
  const safe = !sec.duplicate_session_ids;

  sec.units.forEach(u => {
    const open = S.sectorOpen === u.session_id;
    rows.push(h('div.item', { style: { marginBottom: '6px', flexWrap: 'wrap' } },
      h('div', { style: { minWidth: '190px' } },
        h('div.nm', u.name || u.id),
        h('div.dim', { style: { fontSize: '11px' } },
          (u.side || 'unknown side') + (u.hired ? ' \u00b7 on the payroll' : '')
          + ' \u00b7 id ' + u.session_id)),
      h('div.spacer'),
      h('span.dim', { style: { fontSize: '11.5px' } },
        u.inventory.length + ' item' + (u.inventory.length === 1 ? '' : 's')),
      h('button.btn.sm', {
        onclick: () => { S.sectorOpen = open ? null : u.session_id; render(); },
      }, open ? 'Close' : 'Gear')));
    if (open) rows.push(sectorGear(u, safe));
  });
  return h('div', rows);
}

function sectorGear(u, safe) {
  /* liveSend takes three arguments and returns a promise. A fourth callback
     would have been accepted silently and never run - the panel would sit
     showing stale gear after every edit, looking exactly like the write having
     failed. Chain on the promise instead. */
  const send = (op, value, said) =>
    liveSend(op, value, said).then(() => loadSector()).then(render);

  const slots = [...new Set(u.inventory.map(r => r.slot))];
  if (!slots.length) slots.push('Handheld A');
  if (!S.giveSlot || !slots.includes(S.giveSlot)) S.giveSlot = slots[0];

  return h('div', { style: { margin: '0 0 12px 12px' } },
    u.inventory.length
      ? u.inventory.map(r => h('div.row', {
          style: { gap: '8px', alignItems: 'center', padding: '2px 0' },
        },
          h('span.dim', { style: { width: '110px', fontSize: '11.5px' } }, r.slot),
          h('span', itemName(r.item) || r.item),
          r.amount > 1 ? h('span.dim', '\u00d7' + r.amount) : null,
          h('div.spacer'),
          r.condition ? h('span.dim', r.condition + '%') : null,
          safe ? h('button.btn.sm.ghost', {
            onclick: () => confirmThen(
              'Take the ' + (itemName(r.item) || r.item) + ' off '
                + (u.name || u.id) + '?',
              'It is removed from the unit in the running game.',
              () => send('takeitem',
                         u.session_id + '|' + r.slot + '|' + r.item,
                         'Taken')),
          }, 'Take') : null))
      : h('p.dim', { style: { fontSize: '11.5px' } }, 'Carrying nothing.'),

    !safe ? null : h('div.row',
      { style: { gap: '6px', marginTop: '8px', alignItems: 'center',
                 flexWrap: 'wrap' } },
      h('select', { style: { maxWidth: '130px' },
        onchange: e => { S.giveSlot = e.target.value; } },
        slots.map(sl => h('option',
          { value: sl, selected: sl === S.giveSlot }, sl))),
      /* The catalogue is already loaded, so the box can suggest real ids
         instead of asking someone to remember one. */
      h('datalist', { id: 'ja3items' },
        (S.cat.items || []).map(i => h('option',
          { value: i.id }, i.name || i.id))),
      h('input', { type: 'text', list: 'ja3items', placeholder: 'item id\u2026',
        style: { maxWidth: '170px' }, value: S.giveItem || '',
        oninput: e => { S.giveItem = e.target.value; } }),
      h('button.btn.sm.primary', {
        onclick: () => {
          if (!S.giveItem) return toast('Pick an item first', null, 'bad');
          send('giveitem',
               u.session_id + '|' + S.giveSlot + '|' + S.giveItem,
               'Given');
        },
      }, 'Give')),

    h('p.dim', { style: { fontSize: '11px', marginTop: '6px' } },
      safe
        ? 'The game checks the unit, the item, the slot and whether there is '
          + 'room before it writes anything, and its refusal is shown as-is - '
          + 'a wrong slot name comes back listing the ones this unit has.'
        : 'Editing is off while two units share an id.'));
}

function viewMapEditor() {
  const L = S.live || {};
  return [
    h('div.row', h('h1', 'Map editor'), h('div.spacer')),
    h('p.sub', 'Put mercs and enemies into the running game, to test a build '
      + 'without playing up to it.'),
    !liveOn()
      ? h('div.banner.info',
          h('div', h('strong', 'The game is not connected'),
            h('div.dim', 'Start Jagged Alliance 3, load a game, and install the '
              + 'add-on from the Live link page. This screen works on the '
              + 'running game, so there has to be one.')))
      : null,
    modTestingCard(L),
    liveOn() ? liveSectorCard() : null,
  ];
}

function modTestingCard(L) {
  const onMap = new Set((L.mercs || []).map(m => m.id));
  const roster = (S.cat.mercs || []).filter(m => m.id);

  const place = (replace) => {
    const id = S.spawnMerc || (roster[0] || {}).id;
    if (!id) return;
    liveSend('spawnmerc', id + (replace ? '|replace' : ''),
             replace ? 'Placed again' : 'Placed');
  };

  return h('div.card',
    h('div.card-h', h('h2', 'Put someone on the map'),
      h('span.hint', 'for testing a build without playing up to it')),
    h('div.card-b',
      !L.can_edit
        ? h('p.dim', 'Load a game and this switches on. Mercs and enemies go into '
            + 'the sector you are looking at, so there has to be one.')
        : h('div',
            h('div.row', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
              unitPicker('spawnMerc', null, 250, onMap),
              h('button.btn.sm.primary', { onclick: () => place(false) }, 'Place'),
              /* Placing again is its own button rather than the default,
                 because it takes the merc off the map first and that throws
                 away everything you had already given them. */
              h('button.btn.sm', {
                title: 'Take them off and place them again, fresh. Whatever you '
                     + 'gave them is lost - this is how you re-run a test.',
                onclick: () => place(true),
              }, 'Place again')),

            h('div.slot-h', { style: { marginTop: '14px' } }, 'Enemies', h('span.line')),
            h('div.row', { style: { gap: '8px', flexWrap: 'wrap', alignItems: 'center' } },
              enemyPicker(),
              [1, 2, 5].map(n => h('button.btn.sm', {
                onclick: () => liveSend('spawnenemy',
                    (S.spawnEnemy || '') + '|' + n, 'Placed ' + n),
              }, '+' + n))),
            h('p.dim', { style: { marginTop: '8px' } },
              'Enemies land where the camera is pointing, so aim at the ground you '
              + 'want them on first.'
              + (enemyList().length ? ''
                 : ' This list comes from the base game. Install the script '
                   + 'extender from the Live link page and the running game '
                   + 'supplies a fuller one, with mod content and hit points.')),

            onTheMap(L).length
              ? h('div',
                  h('div.slot-h', { style: { marginTop: '14px' } },
                    'Take off the map', h('span.line')),
                  h('div.row.wrap', { style: { gap: '6px' } },
                    onTheMap(L).map(m => h('button.btn.sm', {
                      /* one click used to remove somebody from the map with
                         no way back, off a list that can contain units this
                         panel never placed */
                      onclick: () => confirmThen(
                        'Take ' + (m.name || m.id) + ' off the map?',
                        'They are removed from the running game. Anything they '
                        + 'were carrying goes with them.',
                        () => liveSend('despawn', m.id, 'Removed')),
                    }, m.name || m.id))))
              : null)));
}

function viewAbout() {
  return [
    h('h1', 'How this works'),
    h('p.sub', 'A short tour of what the editor touches, and what it deliberately does not.'),
    h('div.card', h('div.card-b', h('div', { style: { lineHeight: '1.75' } },
      h('p', h('b', 'The file. '), 'A JA3 save is a small container (magic ', h('span.mono', 'BPUL'),
        ') holding three blobs: the game session, the metadata the load menu previews, and a screenshot. ' +
        'The session is Zstandard-compressed in 128 KiB chunks, and decompresses to a restricted Lua text format.'),
      h('p', h('b', 'The edits. '), 'Changes are applied as minimal text patches to that Lua — only the bytes for the ' +
        'values you actually change are rewritten. Everything else in your save comes back byte-for-byte identical, ' +
        'and the rebuilt file is read back and verified before it is written to disk.'),
      h('p', h('b', 'Backups. '), 'Every write copies the original to a timestamped ', h('span.mono', '.bak'),
        ' beside it first. Nothing is ever overwritten without one.'),
      h('p', h('b', 'Attributes. '), 'Custom (I.M.P.) mercs store their attributes in the save. A.I.M. mercs like Fox ' +
        'do not — they inherit them from the game\'s preset, and the save only records changes. Setting an attribute here ' +
        'writes an explicit value onto the merc and clears the old training modifiers for that attribute, so what you type ' +
        'is what the game reads.'),
      h('p', h('b', 'The catalog. '), 'Item, merc and perk names come from THQ Nordic\'s official modding repository, ' +
        'so they match the base game exactly. Mods you have installed are not in it; their content in your save is ' +
        'preserved untouched either way.'),
      h('p', h('b', 'Offline. '), 'This runs entirely on your machine, on a local port. Nothing is uploaded.')))),
    removalCard(),
  ];
}

/* Taking the editor off a machine. Nothing is deleted until the list has been
   shown and the person has confirmed, and afterwards the page stops polling so
   nothing it does can put a file back. */
function removalCard() {
  const R = S.removal || (S.removal = { backups: false });
  if (R.done) return removalDone(R.done);
  return h('div.card',
    h('div.card-h', h('h2', 'Remove it from this computer'),
      h('span.hint', 'nothing goes until you confirm')),
    h('div.card-b',
      h('p.dim',
        'Takes off the live link add-on, the script extender, every file either '
        + 'has written in your game folders, the editor\u2019s settings, its log, '
        + 'its window cache, its custom-portrait mod, and any temporary folders '
        + 'earlier runs left behind. Your saves are not touched. Nothing installs '
        + 'itself again afterwards.'),
      h('label', { style: { display: 'flex', gap: '8px', alignItems: 'center',
                            marginTop: '8px' } },
        h('input', { type: 'checkbox', checked: !!R.backups,
                     onchange: e => { R.backups = e.target.checked; } }),
        'Also delete the .bak backups the editor made of your saves'),
      h('div', { style: { display: 'flex', gap: '8px', marginTop: '10px' } },
        h('button.btn.sm.danger', { onclick: removalShow }, 'Remove everything\u2026'))));
}

async function removalShow() {
  const R = S.removal;
  let plan;
  try {
    plan = await api('/api/removal-plan', { backups: !!R.backups });
  } catch (e) { return toast('Could not list the files', e.message, 'bad'); }
  if (R.close) R.close();
  let count = 0;
  const body = plan.groups.map(g => {
    const present = g.items.filter(i => i.exists);
    const skip = g.optional && !g.included;
    if (!skip) count += present.length;
    return h('div', { style: { marginBottom: '10px', opacity: skip ? 0.5 : 1 } },
      h('div', { style: { fontWeight: '600' } },
        g.title + (skip ? ' - kept' : '')),
      present.length
        ? h('div.dim', { style: { fontSize: '12px' } },
            present.map(i => h('div', '\u2022 ' + i.path
              + (i.what ? '  \u2014 ' + i.what : ''))))
        : h('div.dim', { style: { fontSize: '12px' } }, 'nothing on this machine'));
  });
  R.close = modal({
    title: 'Remove the editor from this computer?',
    wide: true,
    body: [
      h('div.banner.info', count + ' item' + (count === 1 ? '' : 's')
        + ' will be deleted. Close the game first - it holds the add-on open.'),
      body,
      plan.exe ? h('div.dim', { style: { marginTop: '6px' } },
        'The program itself cannot delete itself while it runs. Afterwards, '
        + 'close this window and delete ' + plan.exe + '.') : null,
    ],
    footer: [
      h('button.btn.ghost', { onclick: () => { R.close(); R.close = null; } }, 'Cancel'),
      h('button.btn.danger', { onclick: removalRun }, 'Delete them'),
    ],
  });
}

async function removalRun() {
  const R = S.removal;
  try {
    const r = await api('/api/remove-everything',
      { confirm: 'REMOVE', backups: !!R.backups });
    if (R.close) { R.close(); R.close = null; }
    liveStop();
    R.done = r;
    render();
  } catch (e) { toast('Removal failed', e.message, 'bad'); }
}

function removalDone(r) {
  return h('div.card',
    h('div.card-h', h('h2', 'Removed')),
    h('div.card-b',
      h('div.banner' + (r.failed.length ? '.bad' : '.ok'),
        'Deleted ' + r.removed.length + ' item' + (r.removed.length === 1 ? '' : 's')
        + (r.failed.length ? '; ' + r.failed.length + ' could not be deleted.' : '.')),
      r.failed.length ? h('div.dim', { style: { fontSize: '12px', marginTop: '6px' } },
        r.failed.map(p => h('div', '\u2022 ' + p))) : null,
      r.kept.length ? h('div.dim', { style: { fontSize: '12px', marginTop: '6px' } },
        h('div', 'Left in place:'), r.kept.map(p => h('div', '\u2022 ' + p))) : null,
      h('p', { style: { marginTop: '10px' } },
        'The editor has stopped writing anything. Close this window'
        + (r.on_exit && r.on_exit.length ? ' - its cache goes when it closes' : '')
        + (r.exe ? ', then delete ' + r.exe + '.' : '.'))));
}

/* ── render ────────────────────────────────────────────────────────── */
/* Which source the figures on this page came from.
   The LIVE badge answers "is the link up". This answers "where did that number
   come from", and the two came apart badly enough to be worth a whole line:
   HAVE 15 on screen, 115 in the game, badge lit, nothing lying. */
function sourceStrip() {
  if (!(S.live && S.live.can_edit)) return null;   // one source, no ambiguity
  const name = (S.sum && S.sum.meta
                && (S.sum.meta.displayname || S.sum.file)) || 'the save file';

  /* Ask the nav which group this page is in rather than assuming the link
     state answers for every page. Sectors sits under Game closed - none of
     its three intents has a live op - so "reading from the running game" was
     exactly wrong there while the badge was lit and correct. That is the same
     fault this strip was built to prevent, one level up. */
  const entry = NAV.find(n => n.id === S.route);
  if (entry && entry.grp === 'closed')
    return h('div.srcbar',
      h('span.srcdot'),
      h('span.srcwhat', 'Reading from ' + name),
      h('span.srchint', 'This page always works on the save file, even while '
        + 'the game is running - the game has no safe way to take these.'));

  const live = liveOn();
  return h('div.srcbar' + (live ? '.live' : ''),
    h('span.srcdot'),
    h('span.srcwhat', live ? 'Reading from the running game'
                           : 'Reading from ' + name),
    h('span.srchint', live
      ? 'Edits that can go live do. The rest are queued for the save.'
      : 'The game is running - click LIVE up there to follow it instead.'));
}

function render() {
  renderNav();
  const view = $('#view');

  const sel = $('#savesel');
  sel.innerHTML = '';
  /* One list for every game. The badge only appears when there is more than
     one game to tell apart - on a machine with only JA3 installed, nothing
     changes and the dropdown reads exactly as it always did. */
  const games = new Set(S.saves.map(s => s.game_short).filter(Boolean));
  const showGame = games.size > 1;
  S.saves.forEach(s => sel.append(h('option', { value: s.path, selected: s.path === S.path },
    (showGame && s.game_short ? '[' + s.game_short + ']  ' : '')
    + s.name + '  ·  ' + new Date(s.mtime * 1000).toLocaleString())));

  const n = S.pending.length;
  $('#pendchip').classList.toggle('on', n > 0);
  $('#pendtxt').textContent = n + ' unsaved change' + (n === 1 ? '' : 's');
  /* A read-only save must never offer to write. The bar is the only route to
     a write, so hiding it closes the door rather than relying on the server
     to say no afterwards - though the server says no as well. */
  const readOnly = !!(S.sum && S.sum.read_only);
  document.documentElement.setAttribute('data-readonly', readOnly ? '1' : '');
  $('#savebar').classList.toggle('on', n > 0 && !readOnly);
  $('#savetxt').innerHTML = `<b>${n}</b> change${n === 1 ? '' : 's'} ready to write`;
  $('#btn-save').disabled = S.busy;

  view.innerHTML = '';
  if (S.err === 'no-saves') {
    view.append(h('div.center', h('div',
      h('h1', 'No saves found'),
      h('p.sub', 'Looked in your Saved Games folder for Jagged Alliance 3.'),
      h('button.btn.primary', { onclick: browse }, 'Browse for a save file…'))));
    return;
  }
  if (S.busy && !S.sum) { view.append(h('div.center', h('div.spin'))); return; }
  if (S.err) { view.append(h('div.center', h('div', h('h1', 'Something went wrong'), h('p.sub', S.err)))); return; }
  if (!S.sum) return;

  const views = {
    overview: viewOverview, party: viewParty, roster: viewRoster,
    mapedit: viewMapEditor,
    create: viewCreate, stash: viewSectors, cheats: viewCheats,
    live: viewLive, about: viewAbout,
  };
  renderLiveChip();
  const strip = sourceStrip();
  if (strip) view.append(strip);
  const out = views[S.route]();
  (Array.isArray(out) ? out : [out]).forEach(x => x && view.append(x));
}

async function browse() {
  try {
    const r = await api('/api/browse', {});
    if (r.path) await open(r.path);
  } catch (e) { toast('Could not open the file picker', e.message, 'bad'); }
}

/* ── wiring ────────────────────────────────────────────────────────── */
$('#savesel').addEventListener('change', e => open(e.target.value));
$('#btn-open').addEventListener('click', browse);
/* ── skin ────────────────────────────────────────────────────────────
   Two looks, the modern one and the game's A.I.M. terminal. This only sets
   an attribute on <html>; every skin rule is scoped under it, so switching
   changes nothing about behaviour. Remembered per machine. */
const SKINS = ['modern', 'aim', 'ja2'];
/* What the Look button reads in each skin. */
const SKIN_LABEL = { modern: 'Look', aim: 'A.I.M.', ja2: 'JA2' };
const SKIN_NAME = {
  modern: 'Modern look',
  aim: 'A.I.M. terminal look',
  ja2: 'Jagged Alliance 2 laptop',
};
function skinDefault() {
  // the build stamps this; a user's saved choice always wins over it
  return document.documentElement.getAttribute('data-default-skin') || 'modern';
}
function skinGet() {
  try { return localStorage.getItem('ja3-skin') || skinDefault(); }
  catch (e) { return skinDefault(); }
}
function skinApply(name) {
  if (name && name !== 'modern') document.documentElement.setAttribute('data-skin', name);
  else document.documentElement.removeAttribute('data-skin');
  const b = $('#btn-skin');
  if (b) b.textContent = SKIN_LABEL[name] || 'Look';
}
function skinToggle() {
  const next = SKINS[(SKINS.indexOf(skinGet()) + 1) % SKINS.length];
  try { localStorage.setItem('ja3-skin', next); } catch (e) { /* private mode */ }
  skinApply(next);
  toast(SKIN_NAME[next] || 'Modern look', 'Only the appearance changes.');
}
skinApply(skinGet());
$('#btn-skin').addEventListener('click', skinToggle);

$('#btn-reload').addEventListener('click', () => open(S.path, true));

/* ── Reset ─────────────────────────────────────────────────────────────
 *
 * The ↻ beside the save picker only re-reads the SAVE. Everything else the
 * editor loads once at boot - the catalogue, item icons, portraits, which
 * mercs have a custom portrait - stayed as it was found, so anything that
 * changed underneath it looked like the editor ignoring you.
 *
 * This reloads the document, which re-runs boot from the top.
 *
 * **The keyboard shortcut is the part that matters.** When the window goes
 * black the page is still alive - proven on 2026-09-05, with the editor's own
 * server answering in 19ms and the identical page rendering correctly in
 * another browser at the same moment. Only the drawing surface is lost. So the
 * page can still HEAR a keypress even when you cannot see anything to click,
 * and F5 is the one thing you can still reach. Whether re-navigating makes the
 * webview take its surface back is unproven - if it does not, the escape hatch
 * is to open the same address in a normal browser, where the state is intact
 * because all of it lives on the server side.
 *
 * A webview usually swallows the browser's own F5/Ctrl+R, which is why this is
 * bound here rather than left to the host.
 */
function resetEverything() {
  try { sessionStorage.setItem('jape-reset', String(Date.now())); } catch (e) {}
  location.reload();
}
$('#btn-reset').addEventListener('click', resetEverything);
window.addEventListener('keydown', e => {
  if (e.key === 'F5' || ((e.ctrlKey || e.metaKey) && (e.key === 'r' || e.key === 'R'))) {
    e.preventDefault();
    resetEverything();
  }
}, true);
$('#btn-folder').addEventListener('click', () => api('/api/reveal', { path: S.path }).catch(() => { }));
$('#btn-save').addEventListener('click', writeSave);
$('#btn-discard').addEventListener('click', () => { S.pending = []; render(); toast('Changes discarded'); });
window.addEventListener('beforeunload', e => { if (S.pending.length) { e.preventDefault(); e.returnValue = ''; } });

function doFlip() {
  S.liveMode = S.liveMode === false;
  renderLiveChip();
  render();
  toast(S.liveMode === false ? 'Editing the save file again'
                             : 'Editing the running game again',
        S.liveMode === false
          ? 'Changes will be queued up and written to the save, as usual.'
          : 'Changes go straight into the game while it runs.');
}

/* Switching source with edits waiting. Three ways out and no silent one:
   stay, discard, or write them first. A flip that quietly carried them across
   would be the same class of fault as a LIVE badge over a save-file number -
   correct in every part, wrong taken together. */
function flipGuard() {
  const n = S.pending.length;
  const toLive = S.liveMode === false;      // where the click would take us
  const stale = S.pending.filter(i => i._from === (toLive ? 'save' : 'game')).length;
  const close = modal({
    title: n + ' change' + (n === 1 ? '' : 's') + ' still waiting',
    body: [
      h('p.dim', 'You are switching to ' + (toLive ? 'the running game'
        + ' as the source of what you see.' : 'the save file as the source of'
        + ' what you see.')),
      stale ? h('p.dim',
        stale + ' of them ' + (stale === 1 ? 'was' : 'were') + ' worked out '
        + 'against ' + (toLive ? 'the save file' : 'the running game')
        + ', which may hold different numbers. Writing them afterwards would '
        + 'apply figures reasoned about in one world to the other.') : null,
      h('p.dim', 'Discarding drops the queue only. Nothing already written to '
        + 'your save or your game is touched either way.'),
    ],
    footer: [
      h('button.btn', { onclick: () => close() }, 'Stay here'),
      h('button.btn', {
        onclick: () => {
          close();
          S.pending = [];
          doFlip();
          toast('Queue discarded', n + ' change' + (n === 1 ? '' : 's')
                + ' dropped. Your save is untouched.');
        },
      }, 'Discard and switch'),
      h('button.btn.primary', {
        onclick: async () => {
          close();
          await writeSave();
          /* Only flip if the write actually emptied the queue. A failed write
             leaves the edits where they are, and switching underneath them
             would be the exact thing this dialog exists to stop. */
          if (!S.pending.length) doFlip();
        },
      }, 'Write them first'),
    ],
  });
}

$('#livechip').addEventListener('click', () => {
  if (!(S.live && S.live.can_edit)) return;
  if (S.pending.length) return flipGuard();
  doFlip();
});

liveStart();
boot();
