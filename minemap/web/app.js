// MineMap front end. One Project object mirrors the server; every edit mutates it
// in place and schedules a debounced PUT. No framework, no build step.
import { icon, iconButton } from './icons.js';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const S = {
  project: null,
  estimate: null,
  catalog: null,        // {vanilla, modded, kg}
  structCatalog: null,
  scan: null,
  instances: null,
  tools: null,
  job: null,            // last /api/job payload
  tab: 'instance',
  overlays: { heightmap: null, biomes: null },
  selectedSteps: null,
};

// --------------------------------------------------------------------------- api
async function api(path, method = 'GET', body = null) {
  const opts = { method, headers: {} };
  if (body !== null) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = j.detail || JSON.stringify(j); } catch (e) { /* plain */ }
    throw new Error(msg);
  }
  const ct = r.headers.get('content-type') || '';
  return ct.includes('json') ? r.json() : r.text();
}

function toast(msg, isError = false) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('error', isError);
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 2500);
}

let saveTimer = null;
function scheduleSave(delay = 400) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, delay);
}
async function saveNow() {
  clearTimeout(saveTimer);
  try {
    const r = await api('/api/project', 'PUT', S.project);
    S.project = r.project;
    S.estimate = r.estimate;
    renderEstimate();
  } catch (e) {
    toast('Save failed: ' + e.message, true);
  }
}

// --------------------------------------------------------------------------- dom helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v === false || v === null || v === undefined) continue;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}
function ib(name, title, onclick, cls = '') {
  const b = h('button', { class: 'ib ' + cls, type: 'button', title, 'aria-label': title, html: icon(name) });
  if (onclick) b.addEventListener('click', onclick);
  return b;
}
function section(title, iconName, ...children) {
  return h('div', { class: 'section' }, h('div', { class: 'head', html: icon(iconName) + ' ' + title }), ...children);
}
function numField(label, obj, key, opts = {}) {
  const inp = h('input', { type: 'number', value: obj[key] ?? '', step: opts.step ?? 'any', min: opts.min, max: opts.max, title: opts.title || label });
  inp.addEventListener('change', () => {
    const v = inp.value === '' ? null : Number(inp.value);
    obj[key] = (v === null && opts.nullable) ? null : (v ?? obj[key]);
    scheduleSave();
    if (opts.onchange) opts.onchange();
  });
  return h('label', { class: 'field', title: opts.title || label }, h('span', {}, label), inp);
}
function textField(label, obj, key, opts = {}) {
  const inp = h('input', { type: 'text', value: obj[key] ?? '', title: opts.title || label, placeholder: opts.placeholder || '' });
  inp.addEventListener('change', () => { obj[key] = inp.value; scheduleSave(); if (opts.onchange) opts.onchange(); });
  return h('label', { class: 'field', title: opts.title || label }, h('span', {}, label), inp);
}
function boolField(label, obj, key, opts = {}) {
  const inp = h('input', { type: 'checkbox', title: opts.title || label });
  inp.checked = !!obj[key];
  inp.addEventListener('change', () => { obj[key] = inp.checked; scheduleSave(); if (opts.onchange) opts.onchange(); });
  return h('label', { title: opts.title || label }, inp, label);
}
function selectField(label, obj, key, options, opts = {}) {
  const sel = h('select', { title: opts.title || label });
  for (const o of options) sel.append(h('option', { value: o, selected: obj[key] === o }, o));
  sel.addEventListener('change', () => { obj[key] = sel.value; scheduleSave(); if (opts.onchange) opts.onchange(); });
  return h('label', { class: 'field', title: opts.title || label }, h('span', {}, label), sel);
}

// searchable picker popover ----------------------------------------------------
function openPicker(anchor, items, onPick, opts = {}) {
  const pop = $('#popover');
  pop.innerHTML = '';
  const search = h('input', { type: 'text', placeholder: opts.placeholder || 'Search', title: 'Filter the list' });
  const list = h('div', { class: 'opts' });
  const render = () => {
    const q = search.value.toLowerCase();
    list.innerHTML = '';
    let n = 0;
    for (const it of items) {
      if (q && !it.id.toLowerCase().includes(q)) continue;
      if (++n > 300) break;
      const row = h('div', { class: 'opt', title: it.title || it.id, html: (it.mod ? icon('sparkles', 'mod') : icon(it.icon || 'circle')) + ' ' });
      row.append(document.createTextNode(it.id));
      row.addEventListener('click', () => { close(); onPick(it); });
      list.append(row);
    }
    if (!n) list.append(h('div', { class: 'hint', style: 'padding:6px' }, 'No match'));
  };
  const close = () => { pop.hidden = true; document.removeEventListener('mousedown', outside); };
  const outside = (e) => { if (!pop.contains(e.target) && e.target !== anchor) close(); };
  search.addEventListener('input', render);
  search.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); if (e.key === 'Enter') { const f = list.querySelector('.opt'); if (f) f.click(); } });
  pop.append(h('div', { class: 'search', html: icon('search') }), list);
  pop.firstChild.append(search);
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.min(r.left, window.innerWidth - 330) + 'px';
  pop.style.top = Math.min(r.bottom + 4, window.innerHeight - 370) + 'px';
  pop.hidden = false;
  render();
  search.focus();
  setTimeout(() => document.addEventListener('mousedown', outside), 0);
}

function biomeItems() {
  const c = S.catalog || { vanilla: [], modded: [] };
  return [...c.modded.map((id) => ({ id, mod: true, title: 'Modded biome' })), ...c.vanilla.map((id) => ({ id, icon: 'trees' }))];
}

async function pickPath(kind, initial = '') {
  try {
    const r = await api('/api/pick', 'POST', { kind, initial });
    return r.path || '';
  } catch (e) {
    toast('Picker failed: ' + e.message, true);
    return '';
  }
}

// --------------------------------------------------------------------------- map
let map, rect, handles = [], spawnMarker = null, drawMode = null, drawFirst = null;

function initMap() {
  map = L.map('map', { zoomControl: true, worldCopyJump: true }).setView([36, 138], 5);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  map.on('click', onMapClick);
  const tb = $('#mapTools');
  tb.append(
    ib('box', 'Draw the map box: click two corners', () => setDrawMode('box'), ''),
    ib('trash', 'Clear the map box', clearBox),
    ib('fit', 'Zoom to the map box', fitBox),
    h('div', { class: 'sep' }),
    ib('flag', 'Set spawn: click on the map', () => setDrawMode('spawn')),
    ib('x', 'Remove the spawn point', clearSpawn),
    h('div', { class: 'sep' }),
    ib('mountain', 'Toggle the heightmap preview overlay', () => toggleOverlay('heightmap')),
    ib('trees', 'Toggle the biome preview overlay', () => toggleOverlay('biomes')),
  );
  tb.children[0].id = 'btnDraw';
  tb.children[4].id = 'btnSpawn';
  tb.children[7].id = 'btnOvHeight';
  tb.children[8].id = 'btnOvBiome';
  syncRectFromProject();
  fitBox();
}

function setDrawMode(mode) {
  drawMode = drawMode === mode ? null : mode;
  drawFirst = null;
  $('#btnDraw').classList.toggle('active', drawMode === 'box');
  $('#btnSpawn').classList.toggle('active', drawMode === 'spawn');
  map.getContainer().classList.toggle('drawing', !!drawMode);
}

function onMapClick(e) {
  if (drawMode === 'box') {
    if (!drawFirst) { drawFirst = e.latlng; toast('Click the opposite corner'); return; }
    const a = drawFirst, b = e.latlng;
    setBBox({ north: Math.max(a.lat, b.lat), south: Math.min(a.lat, b.lat), west: Math.min(a.lng, b.lng), east: Math.max(a.lng, b.lng) });
    setDrawMode(null);
  } else if (drawMode === 'spawn') {
    S.project.world.spawn_lat = +e.latlng.lat.toFixed(5);
    S.project.world.spawn_lon = +e.latlng.lng.toFixed(5);
    syncSpawn();
    scheduleSave();
    setDrawMode(null);
    if (S.tab === 'world') renderPanel();
  }
}

function setBBox(bb) {
  const r = (v) => Math.round(v * 10000) / 10000;
  S.project.bbox = { north: r(bb.north), south: r(bb.south), west: r(bb.west), east: r(bb.east) };
  syncRectFromProject();
  scheduleSave(150);
}

function syncRectFromProject() {
  const b = S.project.bbox;
  const bounds = [[b.south, b.west], [b.north, b.east]];
  if (!rect) {
    rect = L.rectangle(bounds, { color: '#1d6fd1', weight: 2, fillOpacity: 0.06 }).addTo(map);
  } else {
    rect.setBounds(bounds);
  }
  const corners = [[b.north, b.west], [b.north, b.east], [b.south, b.east], [b.south, b.west]];
  const hicon = L.divIcon({ className: 'mm-handle', iconSize: [12, 12] });
  corners.forEach((c, i) => {
    if (!handles[i]) {
      handles[i] = L.marker(c, { draggable: true, icon: hicon, title: 'Drag to resize the box' }).addTo(map);
      handles[i].on('drag', () => onHandleDrag(i));
      handles[i].on('dragend', () => scheduleSave(150));
    } else {
      handles[i].setLatLng(c);
    }
  });
  $('#bbN').value = b.north; $('#bbS').value = b.south; $('#bbW').value = b.west; $('#bbE').value = b.east;
  syncSpawn();
}

function onHandleDrag(i) {
  // opposite corner stays fixed; the dragged corner defines the new extent
  const p = handles[i].getLatLng();
  const opp = handles[(i + 2) % 4].getLatLng();
  const bb = { north: Math.max(p.lat, opp.lat), south: Math.min(p.lat, opp.lat), west: Math.min(p.lng, opp.lng), east: Math.max(p.lng, opp.lng) };
  const r = (v) => Math.round(v * 10000) / 10000;
  S.project.bbox = { north: r(bb.north), south: r(bb.south), west: r(bb.west), east: r(bb.east) };
  const b = S.project.bbox;
  rect.setBounds([[b.south, b.west], [b.north, b.east]]);
  const corners = [[b.north, b.west], [b.north, b.east], [b.south, b.east], [b.south, b.west]];
  corners.forEach((c, k) => { if (k !== i) handles[k].setLatLng(c); });
  $('#bbN').value = b.north; $('#bbS').value = b.south; $('#bbW').value = b.west; $('#bbE').value = b.east;
}

function clearBox() {
  const c = map.getCenter();
  setBBox({ north: c.lat + 0.25, south: c.lat - 0.25, west: c.lng - 0.35, east: c.lng + 0.35 });
}
function fitBox() {
  if (rect) map.fitBounds(rect.getBounds(), { padding: [30, 30] });
}
function syncSpawn() {
  const w = S.project.world;
  if (w.spawn_lat === null || w.spawn_lat === undefined) {
    if (spawnMarker) { spawnMarker.remove(); spawnMarker = null; }
    return;
  }
  const icn = L.divIcon({ className: 'mm-spawn', html: icon('flag'), iconSize: [26, 26], iconAnchor: [4, 24] });
  if (!spawnMarker) {
    spawnMarker = L.marker([w.spawn_lat, w.spawn_lon], { draggable: true, icon: icn, title: 'Spawn point (drag to move)' }).addTo(map);
    spawnMarker.on('dragend', () => {
      const p = spawnMarker.getLatLng();
      S.project.world.spawn_lat = +p.lat.toFixed(5);
      S.project.world.spawn_lon = +p.lng.toFixed(5);
      scheduleSave();
      if (S.tab === 'world') renderPanel();
    });
  } else {
    spawnMarker.setLatLng([w.spawn_lat, w.spawn_lon]);
  }
}
function clearSpawn() {
  S.project.world.spawn_lat = null;
  S.project.world.spawn_lon = null;
  syncSpawn();
  scheduleSave();
  if (S.tab === 'world') renderPanel();
}

async function toggleOverlay(name) {
  const btn = name === 'heightmap' ? $('#btnOvHeight') : $('#btnOvBiome');
  if (S.overlays[name]) {
    S.overlays[name].remove();
    S.overlays[name] = null;
    btn.classList.remove('active');
    return;
  }
  let meta;
  try { meta = await api('/api/preview/meta'); } catch (e) { toast('No preview yet. Run the biomes step first.', true); return; }
  const bb = meta.bbox || meta;
  const bounds = [[bb.south, bb.west], [bb.north, bb.east]];
  S.overlays[name] = L.imageOverlay(`/api/preview/${name}.png?t=${Date.now()}`, bounds, { opacity: 0.75 }).addTo(map);
  btn.classList.add('active');
}

function bindBBoxInputs() {
  for (const id of ['bbN', 'bbS', 'bbW', 'bbE']) {
    $('#' + id).addEventListener('change', () => {
      const v = { north: +$('#bbN').value, south: +$('#bbS').value, west: +$('#bbW').value, east: +$('#bbE').value };
      if (v.north <= v.south || v.east <= v.west) { toast('North must exceed south and east must exceed west', true); syncRectFromProject(); return; }
      setBBox(v);
    });
  }
}

function renderEstimate() {
  const e = S.estimate;
  const el = $('#estimate');
  if (!e || !e.ok) { el.innerHTML = `<span class="bad">${e ? e.error : ''}</span>`; return; }
  const big = e.pixels > 800e6;
  el.innerHTML = `<b>${e.width.toLocaleString()}</b> x <b>${e.height.toLocaleString()}</b> blocks` +
    ` | z${e.zoom} ${e.tiles.toLocaleString()} tiles` +
    ` | ${e.export_gb} GB world` +
    ` | <span class="${big ? 'bad' : ''}">${e.ram_gb} GB RAM</span>`;
  el.title = `Map ${e.width} x ${e.height} blocks, DEM zoom ${e.zoom} (${e.tiles} tiles), estimated world size ${e.export_gb} GB, estimated peak RAM ${e.ram_gb} GB. Border radius ${e.radius_x} x ${e.radius_z}.`;
}

// --------------------------------------------------------------------------- tabs
const TABS = [
  ['instance', 'package', 'Instance: pick the modded instance to read'],
  ['scale', 'ruler', 'Scale: metres per block and vertical exaggeration'],
  ['biomes', 'trees', 'Biomes: climate class to biome mapping'],
  ['rivers', 'waves', 'Rivers: carving settings'],
  ['structures', 'castle', 'Structures: what to place and how many'],
  ['world', 'globe', 'World: name, output, spawn, tools'],
  ['build', 'hammer', 'Build: run the pipeline'],
];

function renderTabs() {
  const nav = $('#tabs');
  nav.innerHTML = '';
  for (const [key, ic, title] of TABS) {
    const b = ib(ic, title, () => { S.tab = key; renderTabs(); renderPanel(); }, S.tab === key ? 'active' : '');
    b.dataset.tab = key;
    nav.append(b);
  }
}

function renderPanel() {
  const p = $('#panel');
  p.innerHTML = '';
  const fn = { instance: tabInstance, scale: tabScale, biomes: tabBiomes, rivers: tabRivers, structures: tabStructures, world: tabWorld, build: tabBuild }[S.tab];
  fn(p);
}

// ---- instance ----------------------------------------------------------------
function launcherIcon(l) {
  return { prism: 'layers', polymc: 'layers', multimc: 'layers', curseforge: 'download', modrinth: 'sparkles', vanilla: 'package', folder: 'folder' }[l] || 'folder';
}

async function tabInstance(p) {
  const cur = S.project.instance;
  const curBox = h('div', { class: 'item selected' },
    h('span', { html: icon(launcherIcon(cur.launcher)) }),
    h('div', { class: 'grow' },
      h('div', { class: 'title' }, cur.name || 'No instance selected'),
      h('div', { class: 'sub' }, cur.path ? `${cur.loader || '?'} ${cur.mc_version || ''}  ${cur.path}` : 'Pick one below or choose a folder')),
    ib('refresh', 'Rescan this instance', () => cur.path && selectInstance(cur.path)),
  );
  const head = section('Current instance', 'package', curBox);
  p.append(head);

  const listEl = h('div', { class: 'list' }, h('div', { class: 'hint' }, 'Looking for launchers'));
  const sec = section('Detected instances', 'search', listEl);
  sec.querySelector('.head').append(
    ib('folder-open', 'Choose a game folder (the one holding mods)', async () => {
      const path = await pickPath('dir', cur.path || '');
      if (path) selectInstance(path);
    }),
    ib('refresh', 'Search launchers again', () => { S.instances = null; renderPanel(); }),
  );
  p.append(sec);

  const scanSec = section('Scan results', 'cpu', renderScanSummary());
  p.append(scanSec);

  if (!S.instances) {
    try { S.instances = await api('/api/instances'); } catch (e) { listEl.innerHTML = ''; listEl.append(h('div', { class: 'hint' }, 'Detection failed: ' + e.message)); return; }
  }
  listEl.innerHTML = '';
  const rows = S.instances.instances || [];
  if (!rows.length) listEl.append(h('div', { class: 'hint' }, 'No launcher instances found. Use the folder icon above.'));
  for (const it of rows) {
    listEl.append(h('div', { class: 'item' + (it.game_dir === cur.path ? ' selected' : '') },
      h('span', { html: icon(launcherIcon(it.launcher)), title: it.launcher }),
      h('div', { class: 'grow' }, h('div', { class: 'title' }, it.name), h('div', { class: 'sub' }, `${it.launcher}  ${it.loader || 'vanilla'} ${it.mc_version || ''}`)),
      ib('check', 'Use this instance and scan it', () => selectInstance(it.game_dir)),
    ));
  }
  for (const r of (S.instances.recent || [])) {
    if (rows.some((x) => x.game_dir === r)) continue;
    listEl.append(h('div', { class: 'item' },
      h('span', { html: icon('folder') }),
      h('div', { class: 'grow' }, h('div', { class: 'sub' }, r)),
      ib('check', 'Use this folder and scan it', () => selectInstance(r)),
    ));
  }
}

function renderScanSummary() {
  const box = h('div', {});
  if (!S.scan) {
    box.append(h('div', { class: 'hint' }, 'No scan yet. Select an instance to read its mods.'));
    return box;
  }
  const sc = S.scan;
  const n = (x) => (Array.isArray(x) ? x.length : Object.keys(x || {}).length);
  box.append(h('div', { class: 'chips' },
    h('span', { class: 'chip stat', title: 'Mods found', html: icon('package') + ' ' + n(sc.mods) }),
    h('span', { class: 'chip stat', title: 'Biomes (data driven, vanilla plus modded)', html: icon('trees') + ' ' + n(sc.biomes) }),
    h('span', { class: 'chip stat', title: 'Structures', html: icon('castle') + ' ' + n(sc.structures) }),
    h('span', { class: 'chip stat', title: 'Structure sets', html: icon('layers') + ' ' + n(sc.structure_sets) }),
    h('span', { class: 'chip stat', title: 'Ore features (generate on first load)', html: icon('pickaxe') + ' ' + n(sc.ores) }),
    h('span', { class: 'chip stat', title: 'Warnings', html: icon('alert') + ' ' + n(sc.warnings) }),
  ));
  const ores = h('div', { class: 'list', style: 'max-height:220px;overflow:auto;margin-top:8px' });
  for (const o of (sc.ores || [])) {
    ores.append(h('div', { class: 'item' },
      h('span', { html: icon('pickaxe'), title: o.source || '' }),
      h('div', { class: 'grow' }, h('div', { class: 'sub' }, o.id), h('div', { class: 'sub' }, (o.blocks || []).join(', '))),
      h('span', { class: 'hint', title: 'Placements per chunk' }, o.count_per_chunk ?? ''),
    ));
  }
  if ((sc.ores || []).length) box.append(ores);
  if ((sc.warnings || []).length) {
    const w = h('div', { class: 'list', style: 'max-height:120px;overflow:auto;margin-top:8px' });
    for (const s of sc.warnings.slice(0, 50)) w.append(h('div', { class: 'hint status-warn' }, s));
    box.append(w);
  }
  return box;
}

async function selectInstance(path) {
  try {
    const r = await api('/api/instance/select', 'POST', { path });
    S.project.instance = r.instance;
    toast('Scanning ' + (r.instance.name || path));
    S.tab = 'build';
    renderTabs();
    renderPanel();
  } catch (e) {
    toast('Could not use that folder: ' + e.message, true);
  }
}

// ---- scale -------------------------------------------------------------------
function tabScale(p) {
  const s = S.project.scale;
  const refresh = () => scheduleSave(100);
  p.append(section('Horizontal', 'ruler',
    h('div', { class: 'row grid' },
      numField('Metres per block', s, 'meters_per_block', { min: 1, step: 1, title: 'Real metres represented by one block. 100 means 1:100.', onchange: refresh }),
      numField('DEM zoom override', s, 'zoom_override', { min: 0, max: 15, step: 1, nullable: true, title: 'Force a Terrarium tile zoom. Empty picks the zoom automatically from the scale.' }),
    ),
    h('div', { class: 'hint' }, 'The map size readout under the map updates from these values.'),
  ));
  p.append(section('Vertical', 'mountain',
    h('div', { class: 'row grid' },
      numField('Vertical exaggeration', s, 'vertical_exaggeration', { min: 0.1, step: 0.1, title: 'One block spans metres per block divided by this. 4 with 100 m blocks gives 25 m per block.' }),
      numField('Water level Y', s, 'water_level', { step: 1, title: 'Block Y of sea level' }),
      numField('Terrain floor Y', s, 'y_min', { step: 1, title: 'Lowest baked terrain Y (seabed clamp)' }),
      numField('Terrain ceiling Y', s, 'y_max', { step: 1, title: 'Highest baked terrain Y' }),
      numField('Build low', s, 'build_low', { step: 1, title: 'World lower build limit' }),
      numField('Build high', s, 'build_high', { step: 1, title: 'World upper build limit' }),
    ),
    h('div', { class: 'row' }, boolField('Hide DEM seams', s, 'deseam', { title: 'Light vertical blur that turns dataset boundary steps into ramps' })),
  ));
}

// ---- biomes ------------------------------------------------------------------
async function tabBiomes(p) {
  if (!S.catalog) {
    try { S.catalog = await api('/api/biomes/catalog'); } catch (e) { p.append(h('div', { class: 'hint' }, 'Catalog failed: ' + e.message)); return; }
  }
  const b = S.project.biomes;
  const kg = S.catalog.kg;

  const rulesSec = section('Climate classes', 'globe');
  rulesSec.querySelector('.head').append(ib('reset', 'Reset biome rules and bands to the defaults', resetBiomes));
  const grid = h('div', { class: 'kg' });
  const codes = Object.keys(kg).sort((a, c) => kg[a].num - kg[c].num);
  for (const code of codes) {
    const info = kg[code];
    if (!b.rules[code]) b.rules[code] = [];
    const chips = h('div', { class: 'chips' });
    const renderChips = () => {
      chips.innerHTML = '';
      b.rules[code].forEach((wb, i) => {
        const isMod = S.catalog.modded.includes(wb.biome);
        const w = h('input', { type: 'number', step: 0.1, min: 0, value: wb.weight, title: 'Weight: share of this class given to this biome' });
        w.addEventListener('change', () => { wb.weight = +w.value; scheduleSave(); });
        chips.append(h('span', { class: 'chip', title: wb.biome },
          h('span', { html: isMod ? icon('sparkles', 'mod') : '' }),
          wb.biome.replace('minecraft:', ''), w,
          ib('x', 'Remove this biome from the class', () => { b.rules[code].splice(i, 1); renderChips(); scheduleSave(); }, 'small')));
      });
      const add = ib('plus', 'Add a biome to this climate class', () => openPicker(add, biomeItems(), (it) => { b.rules[code].push({ biome: it.id, weight: 1 }); renderChips(); scheduleSave(); }), 'small');
      chips.append(add);
    };
    renderChips();
    grid.append(h('div', { class: 'kgrow' },
      h('div', { class: 'kgcode', title: `${code}: ${info.name}` }, h('span', { class: 'swatch', style: `background: rgb(${info.rgb.join(',')})` }), code),
      chips));
  }
  rulesSec.append(grid);
  p.append(rulesSec);

  const bandsSec = section('Elevation bands', 'mountain');
  const tbl = h('table', { class: 'tbl' }, h('thead', {}, h('tr', {}, h('th', {}, 'Above (m)'), h('th', {}, 'Biome'), h('th', { title: 'Climate codes or group letters, comma separated. Empty means every climate.' }, 'Climates'), h('th', {}, ''))));
  const tbody = h('tbody');
  const renderBands = () => {
    tbody.innerHTML = '';
    b.elevation_bands.forEach((band, i) => {
      const m = h('input', { type: 'number', step: 10, value: band.min_m, title: 'Metres above sea level where this band starts' });
      m.addEventListener('change', () => { band.min_m = +m.value; scheduleSave(); });
      const bi = h('button', { class: 'chip', type: 'button', title: 'Change the biome' }, band.biome);
      bi.addEventListener('click', () => openPicker(bi, biomeItems(), (it) => { band.biome = it.id; renderBands(); scheduleSave(); }));
      const cl = h('input', { type: 'text', value: band.climates.join(','), title: 'Climate filter, e.g. D,E or Cfa,Cfb', style: 'width:8em' });
      cl.addEventListener('change', () => { band.climates = cl.value.split(',').map((s) => s.trim()).filter(Boolean); scheduleSave(); });
      tbody.append(h('tr', {}, h('td', {}, m), h('td', {}, bi), h('td', {}, cl), h('td', {}, ib('trash', 'Remove this band', () => { b.elevation_bands.splice(i, 1); renderBands(); scheduleSave(); }, 'small danger'))));
    });
  };
  renderBands();
  tbl.append(tbody);
  bandsSec.querySelector('.head').append(ib('plus', 'Add an elevation band', () => { b.elevation_bands.push({ min_m: 2000, biome: 'minecraft:stony_peaks', climates: [] }); renderBands(); scheduleSave(); }));
  bandsSec.append(tbl);
  p.append(bandsSec);

  const riverBtn = h('button', { class: 'chip', type: 'button', title: 'Biome painted on flooded river pixels' }, b.river_biome);
  riverBtn.addEventListener('click', () => openPicker(riverBtn, biomeItems(), (it) => { b.river_biome = it.id; riverBtn.textContent = it.id; scheduleSave(); }));
  const fillBtn = h('button', { class: 'chip', type: 'button', title: 'Biome for land whose climate class has no rule' }, b.fill_unknown);
  fillBtn.addEventListener('click', () => openPicker(fillBtn, biomeItems(), (it) => { b.fill_unknown = it.id; fillBtn.textContent = it.id; scheduleSave(); }));
  p.append(section('Other', 'settings',
    h('div', { class: 'row' }, h('span', { class: 'hint', html: icon('waves'), title: 'River biome' }), riverBtn, h('span', { class: 'hint', html: icon('help'), title: 'Fallback biome for land without a rule' }), fillBtn),
    h('div', { class: 'row grid' },
      numField('Noise cell (blocks)', b, 'noise_cell', { min: 2, step: 1, title: 'Size of the patches used to mix weighted biomes' }),
      numField('Seed', b, 'seed', { step: 1, title: 'Noise seed' }),
    ),
    h('div', { class: 'row' }, boolField('Coast ring', b, 'coast_ring', { title: 'Leave a one block ring unpainted so WorldPainter adds beaches' })),
  ));
}

async function resetBiomes() {
  try {
    const d = await api('/api/biomes/defaults');
    S.project.biomes = d.biomes;
    await saveNow();
    renderPanel();
    toast('Biome defaults restored');
  } catch (e) {
    toast('Reset failed: ' + e.message, true);
  }
}

// ---- rivers ------------------------------------------------------------------
function tabRivers(p) {
  const r = S.project.rivers;
  p.append(section('Rivers', 'waves',
    h('div', { class: 'row' }, boolField('Carve rivers', r, 'enabled', { title: 'Download HydroRIVERS and carve real river courses into the terrain' })),
    h('div', { class: 'row grid' },
      numField('Min Strahler order', r, 'min_strahler', { min: 1, max: 10, step: 1, title: 'Smallest stream order kept. 4 keeps major rivers only.' }),
      numField('Max width (blocks)', r, 'width_max', { min: 1, step: 1, title: 'Channel width for order 7 and above' }),
      numField('Flood threshold Y', r, 'flood_threshold_y', { step: 1, title: 'Terrain at or below this Y becomes a water river' }),
      numField('Channel depth', r, 'channel_depth', { min: 0, step: 1, title: 'Blocks below sea level for flooded channel bottoms' }),
      numField('Dry valley depth', r, 'dry_depth', { min: 0, step: 1, title: 'Max depth of dry valleys in the uplands' }),
      numField('Carve limit (m)', r, 'carve_max_m', { min: 0, step: 50, title: 'Real elevation above which rivers are not carved' }),
      numField('Bank blur (px)', r, 'bank_blur_px', { min: 0, step: 0.5, title: 'Gaussian radius that softens the banks' }),
      numField('Wall slope', r, 'wall_slope', { min: 0, step: 0.1, title: 'Blocks of extra depth per pixel toward the centre line. 0 gives a flat bottom.' }),
    ),
  ));
  const widths = h('div', { class: 'chips' });
  const keys = Object.keys(r.width_by_order).sort((a, b) => +a - +b);
  for (const k of keys) {
    const inp = h('input', { type: 'number', min: 1, step: 1, value: r.width_by_order[k], title: `Width in blocks for Strahler order ${k}` });
    inp.addEventListener('change', () => { r.width_by_order[k] = +inp.value; scheduleSave(); });
    widths.append(h('span', { class: 'chip', title: `Order ${k}` }, `order ${k}`, inp,
      ib('x', 'Remove this order', () => { delete r.width_by_order[k]; renderPanel(); scheduleSave(); }, 'small')));
  }
  widths.append(ib('plus', 'Add a width for the next Strahler order', () => {
    const next = keys.length ? Math.max(...keys.map(Number)) + 1 : r.min_strahler;
    r.width_by_order[next] = (r.width_by_order[next - 1] || 3) + 3;
    renderPanel(); scheduleSave();
  }, 'small'));
  p.append(section('Width by stream order', 'ruler', widths, h('div', { class: 'hint' }, 'Orders above the last entry use the max width.')));
}

// ---- structures --------------------------------------------------------------
async function tabStructures(p) {
  const st = S.project.structures;
  if (!S.structCatalog) {
    try { S.structCatalog = (await api('/api/structures/catalog')).structures; } catch (e) { S.structCatalog = []; }
  }
  if (!S.catalog) { try { S.catalog = await api('/api/biomes/catalog'); } catch (e) { /* optional */ } }
  const top = section('Placement', 'castle',
    h('div', { class: 'row' }, boolField('Place structures', st, 'enabled', { title: 'Generate the structure placer datapack' })),
    h('div', { class: 'row grid' },
      numField('Density', st, 'density', { min: 0, step: 0.1, title: 'Multiplies every count' }),
      numField('Waves', st, 'waves', { min: 1, step: 1, title: 'Number of in-game placement waves' }),
      numField('Batch', st, 'batch', { min: 1, step: 1, title: 'Placements per tick batch' }),
      numField('Stride', st, 'stride', { min: 1, step: 1, title: 'Raster sampling stride in blocks' }),
      numField('Seed', st, 'seed', { step: 1, title: 'Random seed for site picking' }),
      textField('Namespace', st, 'namespace', { title: 'Datapack namespace used by the functions' }),
    ));
  p.append(top);

  const sec = section('Structures', 'list');
  const tbl = h('table', { class: 'tbl' }, h('thead', {}, h('tr', {},
    h('th', { title: 'Enabled' }, ''), h('th', {}, 'Structure'), h('th', { title: 'Where it may sit' }, 'Where'),
    h('th', { title: 'Count for a 20k x 20k map, scaled by area and density' }, 'Count'), h('th', { title: 'Large: wider forceload box and flatter ground' }, 'Large'), h('th', {}, 'Biomes'), h('th', {}, ''))));
  const tbody = h('tbody');
  const PLACEMENTS = ['auto', 'land', 'any_land', 'coast', 'ocean', 'ocean_deep'];
  const renderRows = () => {
    tbody.innerHTML = '';
    st.entries.forEach((e, i) => {
      const en = h('input', { type: 'checkbox', title: 'Place this structure' }); en.checked = e.enabled;
      en.addEventListener('change', () => { e.enabled = en.checked; scheduleSave(); });
      const sel = h('select', { title: 'Placement class. auto resolves from the structure biome tags.' });
      for (const o of PLACEMENTS) sel.append(h('option', { value: o, selected: e.placement === o }, o));
      sel.addEventListener('change', () => { e.placement = sel.value; scheduleSave(); });
      const cnt = h('input', { type: 'number', min: 0, step: 1, value: e.count, title: 'Target count before area and density scaling' });
      cnt.addEventListener('change', () => { e.count = +cnt.value; scheduleSave(); });
      const lg = h('input', { type: 'checkbox', title: 'Large footprint' }); lg.checked = e.large;
      lg.addEventListener('change', () => { e.large = lg.checked; scheduleSave(); });
      const chips = h('div', { class: 'chips' });
      e.biomes.forEach((bm, k) => chips.append(h('span', { class: 'chip', title: bm }, bm.replace('minecraft:', ''), ib('x', 'Remove', () => { e.biomes.splice(k, 1); renderRows(); scheduleSave(); }, 'small'))));
      const add = ib('plus', 'Restrict to a biome', () => openPicker(add, biomeItems(), (it) => { e.biomes.push(it.id); renderRows(); scheduleSave(); }), 'small');
      chips.append(add);
      tbody.append(h('tr', { title: e.note || '' }, h('td', {}, en), h('td', { class: 'id' }, e.id), h('td', {}, sel), h('td', {}, cnt), h('td', {}, lg), h('td', {}, chips),
        h('td', {}, ib('trash', 'Remove this structure', () => { st.entries.splice(i, 1); renderRows(); scheduleSave(); }, 'small danger'))));
    });
  };
  renderRows();
  tbl.append(tbody);
  const addBtn = ib('plus', 'Add a structure from the instance or the vanilla list', () => {
    const have = new Set(st.entries.map((e) => e.id));
    const items = S.structCatalog.filter((c) => !have.has(c.id)).map((c) => ({ id: c.id, mod: c.source !== 'vanilla', title: `${c.source} ${c.step || ''} suggested: ${c.placement}`, _c: c }));
    openPicker(addBtn, items, (it) => {
      const c = it._c;
      st.entries.push({ id: c.id, enabled: true, count: c.count || 10, large: !!c.large, placement: c.placement || 'auto', biomes: c.biomes || [], note: c.note || '' });
      renderRows(); scheduleSave();
    });
  });
  sec.querySelector('.head').append(addBtn);
  sec.append(tbl);
  p.append(sec);
}

// ---- world -------------------------------------------------------------------
async function tabWorld(p) {
  const w = S.project.world, t = S.project.tools;
  if (!S.tools) { try { S.tools = await api('/api/tools'); } catch (e) { S.tools = null; } }
  const outRow = h('div', { class: 'row' }, h('span', { class: 'path', title: w.output_dir || 'Instance saves folder' }, w.output_dir || '(instance saves folder)'),
    ib('folder-open', 'Choose the output folder', async () => { const d = await pickPath('dir', w.output_dir); if (d) { w.output_dir = d; scheduleSave(); renderPanel(); } }),
    ib('x', 'Use the instance saves folder', () => { w.output_dir = ''; scheduleSave(); renderPanel(); }));
  const spawnTxt = w.spawn_lat === null || w.spawn_lat === undefined ? 'Map centre' : `${w.spawn_lat}, ${w.spawn_lon}`;
  p.append(section('World', 'globe',
    h('div', { class: 'row grid' },
      textField('World name', w, 'name', { title: 'Folder name of the exported world' }),
      textField('Map format id', w, 'map_format', { title: 'WorldPainter platform id. org.pepsoft.anvil.1.20.5 covers 1.20.5 to 1.21.x.' }),
    ),
    h('div', { class: 'field' }, h('span', {}, 'Output folder'), outRow),
    h('div', { class: 'row' }, h('span', { html: icon('flag'), title: 'Spawn' }), h('span', { class: 'mono', title: 'Spawn latitude, longitude. Use the flag tool on the map.' }, spawnTxt),
      numField('Spawn Y fallback', w, 'spawn_y', { step: 1, title: 'Used when the heightmap is unavailable' })),
    h('div', { class: 'row' },
      boolField('Vanilla backstop border', w, 'vanilla_border', { title: 'Write a square world border into level.dat' }),
      boolField('Void outside the map', w, 'void_outside', { title: 'Datapack that stops terrain and structures generating beyond the map' })),
  ));
  const wp = S.tools ? S.tools.wpscript : null;
  const jv = S.tools ? S.tools.java : null;
  const wpRow = h('div', { class: 'row' },
    h('span', { class: wp && wp.found ? 'status-ok' : 'status-bad', html: icon(wp && wp.found ? 'circle-check' : 'circle-x'), title: wp && wp.found ? 'WorldPainter found' : 'WorldPainter not found' }),
    h('span', { class: 'path', title: wp ? wp.path : '' }, wp && wp.found ? wp.path : 'wpscript not found. Install WorldPainter and pick wpscript.exe.'),
    ib('folder-open', 'Pick wpscript.exe', async () => { const f = await pickPath('file', ''); if (f) { t.wpscript_path = f; await saveNow(); S.tools = null; renderPanel(); } }),
    ib('x', 'Auto-detect again', async () => { t.wpscript_path = ''; await saveNow(); S.tools = null; renderPanel(); }));
  const jvRow = h('div', { class: 'row' },
    h('span', { class: jv && jv.found ? 'status-ok' : 'status-bad', html: icon(jv && jv.found ? 'circle-check' : 'circle-x'), title: 'Java on PATH' }),
    h('span', { class: 'path', title: jv ? jv.path : '' }, jv && jv.found ? jv.version : 'java not found on PATH (WorldPainter needs Java 17 or newer)'));
  p.append(section('Tools', 'settings', wpRow, jvRow,
    h('div', { class: 'row grid' }, numField('WorldPainter heap (GB)', t, 'java_xmx_gb', { min: 1, step: 1, title: 'Written to wpscript.vmoptions as -Xmx. Big maps need 8 to 16 GB.' })),
    S.tools ? h('div', { class: 'hint', title: 'Shared cache under the local app data folder' }, `Cache: DEM ${fmtBytes(S.tools.caches.dem)}, rivers ${fmtBytes(S.tools.caches.rivers)}, climate ${fmtBytes(S.tools.caches.climate)}`) : null,
  ));
  const c = S.project.caves;
  p.append(section('Caves', 'layers',
    h('div', { class: 'row grid' },
      numField('Caves level', c, 'level', { min: 0, max: 15, step: 1, title: 'WorldPainter caves everywhere level. 0 disables.' }),
      numField('Min Y', c, 'min_y', { step: 1 }), numField('Max Y', c, 'max_y', { step: 1 }), numField('Water Y', c, 'water_y', { step: 1, title: 'Caves below this Y flood' })),
    h('div', { class: 'row' }, boolField('Surface breaking', c, 'surface_breaking', { title: 'Allow caves to open onto the surface' })),
  ));
}
function fmtBytes(n) { return n > 1e9 ? (n / 1e9).toFixed(1) + ' GB' : n > 1e6 ? (n / 1e6).toFixed(0) + ' MB' : (n / 1e3).toFixed(0) + ' kB'; }

// ---- build -------------------------------------------------------------------
let es = null, logEl = null, progEl = null, stepEls = {};

async function tabBuild(p) {
  try { S.job = await api('/api/job'); } catch (e) { p.append(h('div', { class: 'hint' }, 'Job status failed: ' + e.message)); return; }
  const steps = S.job.available_steps || [];
  if (!S.selectedSteps) S.selectedSteps = new Set(steps.map((s) => s.key));
  const running = S.job.job && S.job.job.running;

  const stepsEl = h('div', { class: 'steps' });
  stepEls = {};
  for (const s of steps) {
    const cb = h('input', { type: 'checkbox', title: 'Include this step in the run', disabled: running });
    cb.checked = S.selectedSteps.has(s.key);
    cb.addEventListener('change', () => { if (cb.checked) S.selectedSteps.add(s.key); else S.selectedSteps.delete(s.key); });
    const st = h('span', { class: 'st', html: '' });
    const have = S.job.outputs && S.job.outputs[s.key];
    const row = h('div', { class: 'step' }, cb, st, h('span', { class: 'lbl' }, s.label),
      h('span', { class: 'have', html: have ? icon('check') : '', title: have ? 'Output already exists' : 'Not built yet' }));
    stepEls[s.key] = st;
    stepsEl.append(row);
  }
  const ow = h('input', { type: 'checkbox', title: 'Delete an existing world folder of the same name before export' });
  ow.checked = false;
  const run = ib('play', 'Run the selected steps', () => startJob(ow.checked), 'primary');
  const stop = ib('square', 'Cancel the running job', async () => { await api('/api/job/cancel', 'POST', {}); }, 'danger');
  run.disabled = !!running; stop.disabled = !running;
  run.id = 'btnRun'; stop.id = 'btnStop';
  const sec = section('Steps', 'hammer', stepsEl,
    h('div', { class: 'row', style: 'margin-top:8px' }, run, stop,
      ib('circle-check', 'Select every step', () => { S.selectedSteps = new Set(steps.map((s) => s.key)); renderPanel(); }),
      ib('circle', 'Select no step', () => { S.selectedSteps = new Set(); renderPanel(); }),
      h('label', { title: 'Overwrite an existing world of the same name' }, ow, 'overwrite world')));
  p.append(sec);

  progEl = h('div', { class: 'progress' }, h('div'));
  logEl = h('div', { class: 'console', title: 'Build log' });
  for (const line of (S.job.log || [])) appendLog(line);
  const logSec = section('Log', 'terminal', progEl, logEl);
  logSec.querySelector('.head').append(ib('trash', 'Clear the log view', () => { logEl.innerHTML = ''; }));
  p.append(logSec);

  const cmdPre = h('pre', { class: 'cmds' }, 'Commands appear after the structures step.');
  const cmdSec = section('In-game commands', 'terminal', cmdPre);
  cmdSec.querySelector('.head').append(
    ib('copy', 'Copy the commands', () => navigator.clipboard.writeText(cmdPre.textContent).then(() => toast('Copied'))),
    ib('refresh', 'Reload the commands', () => loadCommands(cmdPre)));
  p.append(cmdSec);
  loadCommands(cmdPre);

  if (S.job.job) applyJobStatus(S.job.job);
  connectEvents();
}

async function loadCommands(pre) {
  try { pre.textContent = await api('/api/commands'); } catch (e) { /* not built yet */ }
}

function appendLog(text, isErr = false) {
  if (!logEl) return;
  const line = h('div', { class: isErr ? 'err' : '' }, text);
  logEl.append(line);
  while (logEl.childElementCount > 600) logEl.removeChild(logEl.firstChild);
  logEl.scrollTop = logEl.scrollHeight;
}

function applyJobStatus(job) {
  for (const [k, v] of Object.entries(job.steps || {})) setStepState(k, v.state);
  setBadge(job);
}
function setStepState(key, state) {
  const el = stepEls[key];
  if (!el) return;
  el.className = 'st ' + state;
  el.classList.toggle('spin', state === 'running');
  el.innerHTML = { running: icon('loader'), done: icon('check'), error: icon('x'), cancelled: icon('alert'), pending: '' }[state] || '';
  el.title = state;
}
function setBadge(job) {
  const b = $('#jobBadge');
  if (!job) { b.hidden = true; return; }
  b.hidden = false;
  b.className = 'badge ' + (job.running ? 'running' : job.error ? 'error' : '');
  b.innerHTML = (job.running ? icon('loader', 'spin') : job.error ? icon('alert') : icon('check')) + ' ' + (job.running ? (job.label || job.step) : job.error ? 'failed' : 'done');
  b.title = job.error || (job.running ? 'Job running' : 'Last job finished');
  const run = $('#btnRun'), stop = $('#btnStop');
  if (run) run.disabled = !!job.running;
  if (stop) stop.disabled = !job.running;
}

async function startJob(overwrite) {
  const steps = (S.job.available_steps || []).map((s) => s.key).filter((k) => S.selectedSteps.has(k));
  if (!steps.length) { toast('Select at least one step', true); return; }
  await saveNow();
  try {
    const st = await api('/api/job/start', 'POST', { steps, overwrite_world: overwrite });
    if (logEl) logEl.innerHTML = '';
    applyJobStatus(st);
  } catch (e) {
    toast('Could not start: ' + e.message, true);
  }
}

function connectEvents() {
  if (es) return;
  es = new EventSource('/api/job/events');
  es.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch (e) { return; }
    if (ev.type === 'log') appendLog(ev.text, /^ERROR/.test(ev.text));
    else if (ev.type === 'progress') { if (progEl) progEl.firstChild.style.width = (ev.progress * 100).toFixed(1) + '%'; }
    else if (ev.type === 'step') { setStepState(ev.step, ev.state); setBadge({ running: ev.state === 'running', step: ev.step, label: ev.step }); }
    else if (ev.type === 'done' || ev.type === 'error') {
      setBadge({ running: false, error: ev.error });
      if (ev.error) toast(ev.error, true); else toast('Job finished');
      refreshAfterJob();
    }
  };
  es.onerror = () => { /* the browser reconnects on its own */ };
}

async function refreshAfterJob() {
  try {
    const r = await api('/api/project');
    S.project = r.project; S.estimate = r.estimate; renderEstimate();
  } catch (e) { /* keep local */ }
  try { S.scan = await api('/api/scan'); S.catalog = null; S.structCatalog = null; } catch (e) { /* none */ }
  if (S.tab === 'build' || S.tab === 'instance') renderPanel();
}

// --------------------------------------------------------------------------- top bar
function renderTop() {
  $('#logo').innerHTML = icon('map');
  const name = $('#projectName');
  name.value = S.project.name;
  name.addEventListener('change', () => { S.project.name = name.value; scheduleSave(); });
  const a = $('#topActions');
  a.innerHTML = '';
  a.append(
    ib('plus', 'New project', async () => {
      const nm = prompt('Project name', 'New map');
      if (!nm) return;
      try { const r = await api('/api/project/new', 'POST', { name: nm }); await loadProject(r); toast('Project created'); } catch (e) { toast(e.message, true); }
    }),
    ib('folder-open', 'Open a project folder', async () => {
      const d = await pickPath('dir', '');
      if (!d) return;
      try { const r = await api('/api/project/open', 'POST', { path: d }); await loadProject(r); } catch (e) { toast(e.message, true); }
    }),
    ib('list', 'Recent projects', async (ev) => {
      const r = await api('/api/projects');
      openPicker(ev.currentTarget, r.projects.map((p) => ({ id: p.name, title: p.path, icon: 'folder', _p: p })), async (it) => {
        try { const rr = await api('/api/project/open', 'POST', { path: it._p.path }); await loadProject(rr); } catch (e) { toast(e.message, true); }
      }, { placeholder: 'Find a project' });
    }),
    ib('save', 'Save the project now', async () => { await saveNow(); toast('Saved'); }),
    ib('help', 'Help', () => window.open('https://github.com/west3436/MineMap#readme', '_blank')),
  );
}

async function loadProject(r) {
  S.project = r.project;
  S.estimate = r.estimate;
  S.scan = null; S.catalog = null; S.structCatalog = null; S.selectedSteps = null;
  try { S.scan = await api('/api/scan'); } catch (e) { /* none */ }
  $('#projectName').value = S.project.name;
  syncRectFromProject();
  fitBox();
  renderEstimate();
  renderPanel();
}

// --------------------------------------------------------------------------- boot
async function boot() {
  const r = await api('/api/project');
  S.project = r.project;
  S.estimate = r.estimate;
  try { S.scan = await api('/api/scan'); } catch (e) { /* none */ }
  renderTop();
  initMap();
  bindBBoxInputs();
  renderEstimate();
  renderTabs();
  renderPanel();
  connectEvents();
  try { const j = await api('/api/job'); if (j.job) setBadge(j.job); } catch (e) { /* none */ }
}

boot().catch((e) => { document.body.prepend(h('div', { class: 'toast error', style: 'position:static' }, 'MineMap failed to start: ' + e.message)); });
