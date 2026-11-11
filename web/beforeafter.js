// Rasad «قبل وبعد»: the before/after viewer of the app. Any place the user picks (or an incident)
// is shown in two free Sentinel-2 pictures, one before and one after, with a divider to drag, a
// short-wave infrared view, a layer of what changed with its area, and flames seen at the moment
// of the picture. The pictures are read in the phone itself by imagery.js; this file is the screen.
// It uses the app's own helpers from index.html (S, setRoute, toast, copy, bidi, presentPicture).
//
// Routes:  #ba  choose a place · #ba:i:<incident id> · #ba:<lat>,<lon>,<half km>[,<before>,<after>][;name]
(function (root) {
"use strict";
const I = root.RasadImagery || (typeof require === "function" ? require("./imagery.js") : null);
const BA = {available: !!I};
root.BA = BA;
if (typeof module === "object" && module.exports) module.exports = BA;
if (!I) return;

const DAY = 864e5;
const SIZES = [1, 2, 3, 5, 10];                 // half the box, km (the box is twice as wide)
const LOOK_BACK = 120 * DAY;
const KEYS = {places: "firms-places", recent: "firms-places-recent", lite: "firms-ba-lite"};
const store = {
  get(k, d) { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: not kept */ } },
};
const el = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const num = (v, d = 0) => Number(v).toLocaleString("en-US", {maximumFractionDigits: d, minimumFractionDigits: d});

/* ------------------------------------------------------------------ plain helpers (tested in Node) */
// "26.23, 50.83", "26.23 50.83", Arabic digits, or a maps link holding @lat,lon / q=lat,lon
function parsePlace(text) {
  const s = String(text || "").replace(/[٠-٩]/g, d => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/[،؛]/g, ",").replace(/٫/g, ".").trim();
  const N = "(-?\\d{1,3}(?:\\.\\d+)?)";
  const m = new RegExp("^" + N + "\\s*°?\\s*[,\\s]\\s*" + N + "\\s*°?$").exec(s)
    || new RegExp("[@=]" + N + "(?:,|%2C)\\s*" + N).exec(s);
  if (!m) return null;
  const lat = +m[1], lon = +m[2];
  return Math.abs(lat) <= 75 && Math.abs(lon) <= 180 ? {lat, lon} : null;
}
function parseRoute(h) {
  if (h === "ba") return {view: "chooser"};
  if (!String(h).startsWith("ba:")) return null;
  const rest = h.slice(3);
  if (rest.startsWith("i:")) return {view: "incident", id: rest.slice(2)};
  const cut = rest.indexOf(";"), p = (cut < 0 ? rest : rest.slice(0, cut)).split(",");
  const lat = parseFloat(p[0]), lon = parseFloat(p[1]);
  if (p.length < 2 || !isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 75 || Math.abs(lon) > 180) return {view: "chooser"};
  const day = t => /^\d{8}$/.test(t || "") ? `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6)}` : null;
  return {view: "viewer", lat, lon, half: SIZES.includes(+p[2]) ? +p[2] : 2, before: day(p[3]), after: day(p[4]),
    name: cut < 0 ? "" : rest.slice(cut + 1)};
}
function routeOf(v) {
  const c = d => d ? d.replace(/-/g, "") : "";
  let s = `ba:${(+v.lat).toFixed(5)},${(+v.lon).toFixed(5)},${v.half}`;
  if (v.before || v.after) s += `,${c(v.before)},${c(v.after)}`;
  return v.name ? s + ";" + v.name : s;
}
const dateAr = t => new Date(t).toLocaleDateString("ar-IQ-u-nu-latn", {timeZone: "Asia/Baghdad", day: "numeric", month: "long", year: "numeric"});
const timeAr = t => new Date(t).toLocaleTimeString("en-GB", {timeZone: "Asia/Baghdad", hour: "2-digit", minute: "2-digit"});
function skyText(p) {
  if (p.failed) return "تعذّر فحصها";
  if (!p.sky) return "تُفحص…";
  if (I.isClear(p)) return "صافية";
  const cloud = Math.round((p.sky.cloud + p.sky.shadow + p.sky.cirrus * 0.5) * 100), out = [];
  if (p.cover < 0.985 || p.sky.nodata > 0.02) out.push("تغطي جزءاً من المربع");
  if (cloud >= 1) out.push(`غيوم ${cloud}%`);
  return out.join(" · ") || "صافية تقريباً";
}
function areaText(ha) {
  if (ha < 1) return `${num(Math.max(100, Math.round(ha * 100) * 100))} م²`;
  return ha < 100 ? `${num(ha, 1)} هكتار` : `${num(ha / 100, 2)} كم²`;
}
// The first pass in order of preference that shows the ground. Not settled while a nearer pass is
// still being checked (it could be the clear one).
function settle(list) {
  const near = list.slice(0, 14);
  for (const p of near) {
    if (!p.sky && !p.failed) return {pass: null, done: false};
    if (p.sky && I.isClear(p)) return {pass: p, done: true};
  }
  return {pass: near.find(p => p.sky && I.grade(p) === 1) || null, done: true};
}
// The default pair while the passes are being checked one by one (`passes` newest first).
function choose(all, event) {
  // Sentinel-2 (10 m) for the default pair; Landsat (30 m) only where there is nothing else
  const sharp = all.filter(p => p.sensor !== "landsat"), passes = sharp.length ? sharp : all;
  if (event && event.start) {
    const s = Date.parse(event.start), e = Date.parse(event.end || event.start);
    return {before: settle(passes.filter(p => p.time < s)), after: settle(passes.filter(p => p.time > e).reverse())};
  }
  const after = settle(passes);
  const before = after.done && after.pass ? settle(passes.filter(p => p.time < after.pass.time - 36e5)) : {pass: null, done: after.done};
  return {before, after};
}
// the order in which passes are checked: nearest to the event first, else newest first
function checkOrder(passes, event) {
  const coarse = p => p.sensor === "landsat" ? 1 : 0;              // the default pair's candidates first
  if (!event || !event.start) return passes.slice().sort((a, b) => coarse(a) - coarse(b));
  const mid = (Date.parse(event.start) + Date.parse(event.end || event.start)) / 2;
  return passes.slice().sort((a, b) => coarse(a) - coarse(b) || Math.abs(a.time - mid) - Math.abs(b.time - mid));
}
const sensorText = p => p.sensor === "landsat" ? " · Landsat 30 م" : "";
// which of the hot points in the later picture were already there before (a gas flare, most likely)
function splitHot(after, before) {
  let old = 0;
  for (const h of after) {
    const near = before.some(b => {
      const dy = (h.lat - b.lat) * 111320, dx = (h.lon - b.lon) * 111320 * Math.cos(h.lat * Math.PI / 180);
      return Math.hypot(dx, dy) < 250;
    });
    if (near) { old++; h.old = true; } else h.old = false;
  }
  return {fresh: after.length - old, old};
}
function scaleBar(metresPerPx, maxPx) {
  const steps = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000];
  let m = steps[0];
  for (const s of steps) if (s / metresPerPx <= maxPx) m = s;
  return {metres: m, px: m / metresPerPx, label: m >= 1000 ? `${m / 1000} كم` : `${m} م`};
}
// The stretches of time to list passes for: around the event, around the dates of a link, or the
// last four months. Overlapping stretches are joined; nothing reaches into the future.
function windowsFor(now, event, want) {
  const out = [];
  if (event && event.start) out.push([Date.parse(event.start) - 75 * DAY, Date.parse(event.end || event.start) + 75 * DAY]);
  for (const d of [want && want.before, want && want.after]) if (d) out.push([Date.parse(d) - 30 * DAY, Date.parse(d) + 31 * DAY]);
  if (!out.length) out.push([now - LOOK_BACK, now]);
  return joinWindows(out, now);
}
function joinWindows(list, now) {
  const sorted = list.map(([a, b]) => [a, Math.min(b, now)]).filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0]), out = [];
  for (const w of sorted) {
    const last = out[out.length - 1];
    if (last && w[0] <= last[1] + DAY) last[1] = Math.max(last[1], w[1]); else out.push(w.slice());
  }
  return out;
}
// an event known only by its day (Baghdad time): passes on that day count as neither before nor after
function eventOfDay(day) {
  const start = Date.parse(day + "T00:00:00+03:00");
  return isFinite(start) ? {start: new Date(start).toISOString(), end: new Date(start + DAY).toISOString(), day} : null;
}
function distanceText(m) { return m < 1000 ? `${num(Math.round(m))} م` : `${num(m / 1000, 2)} كم`; }
function failText(e) {
  const code = e && e.code;
  if (code === "offline") return "لا اتصال بالإنترنت.";
  if (code === "timeout") return "أرشيف الصور بطيء الآن. حاول مرة أخرى.";
  if (code === "none") return "لا توجد صور لهذا الموقع في هذه المدة.";
  return "أرشيف الصور الأوروبي المفتوح لا يستجيب الآن. حاول بعد قليل.";
}
BA._ = {parsePlace, parseRoute, routeOf, skyText, areaText, settle, choose, checkOrder, splitHot, scaleBar, failText, dateAr, SIZES, sensorText,
  windowsFor, joinWindows, eventOfDay, distanceText};
if (typeof document === "undefined") return;       // Node: only the helpers above

/* ------------------------------------------------------------------ state */
const engine = I.createEngine();
let V = null;                    // the open viewer
let picking = false, from = "";  // choosing a place on the map; the route to go back to
const sheet = () => el("#baSheet");
const places = () => store.get(KEYS.places, []);
const samePlace = (a, b) => Math.abs(a.lat - b.lat) < 2e-4 && Math.abs(a.lon - b.lon) < 2e-4;

/* ------------------------------------------------------------------ routes */
BA.route = function (h) {
  const r = parseRoute(h);
  if (!r) return false;
  stopPick();
  if (r.view === "incident") {
    const it = S.items.find(x => x.id === r.id);
    if (!it) { toast("هذا الحدث لم يعد في القائمة"); showChooser(); return true; }
    openViewer({lat: it.lat, lon: it.lon, half: it.best === "polar" ? 2 : 3, title: it.headline, sub: it.where || "",
      event: {start: it.first, end: it.last}, incident: it,
      // the pair the monitor already compared and posted to the channel, when there is one
      want: it.satpic && it.satpic.after ? {before: it.satpic.before, after: it.satpic.after} : undefined});
  } else if (r.view === "viewer") {
    openViewer({lat: r.lat, lon: r.lon, half: r.half, title: r.name, want: {before: r.before, after: r.after}});
  } else showChooser();
  return true;
};
BA.hide = function () {
  if (watching) { watching.abort(); watching = null; }
  if (V) { V.session.abort(); if (V.load) V.load.abort(); clearInterval(V.blinkTimer); V = null; }
  const s = sheet();
  if (s && !s.hidden) { s.hidden = true; s.innerHTML = ""; }
};
BA.isOpen = () => !!(sheet() && !sheet().hidden);
BA.busy = () => picking || BA.isOpen();
BA.leave = function () { stopPick(); BA.hide(); };          // the app moved to another screen
BA.escape = function () { if (picking) BA.cancelPick(); else BA.back(); };
BA.back = function () { setRoute(from || ""); };
// from the app: remember where the user was, then open
BA.go = function (hash) {
  const cur = decodeURIComponent(location.hash.slice(1));
  if (!cur.startsWith("ba")) from = cur;
  setRoute(hash);
};

/* ------------------------------------------------------------------ choosing a place on the map */
BA.startPick = function () {
  if (!S.map) { toast("الخريطة غير متاحة الآن"); return; }
  BA.hide();
  picking = true;
  document.body.classList.add("picking");
  el("#pickBar").hidden = false;
  if (S.layer) S.map.removeLayer(S.layer);          // fires are not tappable while choosing
  S.map.invalidateSize();
  S.map.on("click", onPick);
};
function onPick(e) {
  const {lat, lng} = e.latlng;
  stopPick();
  from = "";
  setRoute(routeOf({lat, lon: lng, half: 2}));
}
function stopPick() {
  if (!picking) return;
  picking = false;
  document.body.classList.remove("picking");
  el("#pickBar").hidden = true;
  if (S.map) {
    S.map.off("click", onPick);
    if (S.layer) S.layer.addTo(S.map);
    S.map.invalidateSize();
  }
}
BA.cancelPick = function () { stopPick(); setRoute("ba"); };

/* ------------------------------------------------------------------ the chooser */
function placeRow(p, i, kind) {
  return `<div class="ba-place">
    <button class="t" type="button" data-act="open" data-lat="${p.lat}" data-lon="${p.lon}" data-half="${p.half || 2}" data-name="${esc(p.name || "")}">
      <b>${bidi(p.name || `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`)}</b><small>${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}</small>${kind === "saved" ? `<span class="new" id="baNew${i}"></span>` : ""}</button>
    ${kind === "saved" ? `<button class="btn" type="button" data-act="forget" data-i="${i}" aria-label="حذف الموقع">حذف</button>` : ""}
  </div>`;
}
function showChooser(results, note) {
  BA.hide();
  const s = sheet(), saved = places(), recent = store.get(KEYS.recent, []).filter(r => !saved.some(p => samePlace(p, r)));
  s.innerHTML = `
    <div class="sheet-top wrap"><button class="btn" type="button" data-act="back">رجوع</button><span class="ba-title">قبل وبعد · صور الأقمار</span></div>
    <div class="wrap ba-pick">
      <p class="note">قارن صورتين من القمر الأوروبي Sentinel-2 لأي موقع: واحدة قبل الحدث وواحدة بعده. تظهر آثار الحريق والاسوداد والمباني الكبيرة المتضررة، والدخان واللهب لحظة الالتقاط.</p>
      <button class="btn primary ba-wide" type="button" data-act="pick">📍 اختر موقعاً من الخريطة</button>
      <form class="ba-search" id="baSearch"><input id="baQuery" type="search" enterkeyhint="search" placeholder="اسم مكان، أو إحداثيات مثل 26.23, 50.83" autocomplete="off"><button class="btn" type="submit">بحث</button></form>
      <div id="baResults">${note ? `<p class="note">${esc(note)}</p>` : ""}${(results || []).map((p, i) => placeRow(p, i, "found")).join("")}</div>
      ${saved.length ? `<h3>المواقع المحفوظة (قائمة المراقبة)</h3>${saved.map((p, i) => placeRow(p, i, "saved")).join("")}` : ""}
      ${recent.length ? `<h3>فتحتها مؤخراً</h3>${recent.map((p, i) => placeRow(p, i, "recent")).join("")}` : ""}
      <div class="help"><b>ما الذي يظهر؟</b> كل نقطة في الصورة 10 أمتار على الأرض، ويمر القمر كل يومين إلى خمسة أيام. تظهر الأضرار الكبيرة (خزانات، حظائر طائرات، مساحات محترقة)، ولا يظهر ضرر مبنى صغير أو مركبة واحدة. الصور تُظهر التغيّر ولا تحدد سببه.</div>
    </div>`;
  s.hidden = false; s.scrollTop = 0;
  el("#baSearch").addEventListener("submit", e => { e.preventDefault(); search(el("#baQuery").value); });
  watch(saved);
}
// The saved places are a watch list: each one shows the date of its newest pass, marked when it
// came after the last time the place was opened here. One small request per place.
let watching = null;
async function watch(saved) {
  if (watching) watching.abort();
  const ctl = watching = new AbortController(), now = Date.now();
  await Promise.all(saved.slice(0, 12).map(async (p, i) => {
    try {
      const passes = await engine.listPasses(I.makeGrid(p.lat, p.lon, p.half || 2), now - 16 * DAY, now, ctl.signal);
      const box = el("#baNew" + i);
      if (ctl.signal.aborted || !box || !passes.length) return;
      box.textContent = (passes[0].time > (p.seen || 0) ? "● صورة جديدة: " : "أحدث مرور: ") + dateAr(passes[0].time);
      box.className = "new" + (passes[0].time > (p.seen || 0) ? " fresh" : "");
    } catch (e) { /* the row simply stays without a date */ }
  }));
}
async function search(text) {
  const here = parsePlace(text);
  if (here) { setRoute(routeOf({...here, half: 2})); return; }
  const q = String(text || "").trim();
  if (q.length < 2) return;
  el("#baResults").innerHTML = `<p class="note">جارٍ البحث…</p>`;
  let found = null;
  try {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 12000);
    const r = await fetch("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&accept-language=ar&q=" + encodeURIComponent(q), {signal: ctl.signal});
    clearTimeout(timer);
    if (r.ok) found = (await r.json()).map(x => ({lat: +x.lat, lon: +x.lon, name: String(x.display_name || "").split("،").slice(0, 3).join("،")}))
      .filter(p => isFinite(p.lat) && isFinite(p.lon) && Math.abs(p.lat) <= 75);
  } catch (e) { found = null; }
  if (!BA.isOpen() || V) return;
  if (!found) showChooser([], "تعذّر البحث الآن. اختر الموقع من الخريطة أو اكتب الإحداثيات.");
  else showChooser(found, found.length ? "" : "لا نتائج. جرّب اسماً آخر أو اختر الموقع من الخريطة.");
  el("#baQuery").value = q;
}

/* ------------------------------------------------------------------ the viewer */
function openViewer(o) {
  BA.hide();
  const now = Date.now(), want = o.want || {};
  const lite = store.get(KEYS.lite, null);
  V = {
    lat: o.lat, lon: o.lon, half: o.half, title: o.title || "", sub: o.sub || "", event: o.event || null, incident: o.incident || null,
    want, auto: !(want.before || want.after), windows: windowsFor(now, o.event, want), measure: null,
    passes: [], before: null, after: null, scenes: new Map(), shown: {}, img: {}, change: null, hot: {before: [], after: []},
    mode: "color", split: 0.5, zoom: 1, cx: 0.5, cy: 0.5, blink: false, blinkOn: false, blinkTimer: 0,
    run: 0, gen: 0, session: new AbortController(), load: null, checked: false, busy: "", error: null,
    lite: lite == null ? !!(navigator.connection && navigator.connection.saveData) : !!lite,
  };
  const s = sheet();
  s.innerHTML = `
    <div class="sheet-top wrap">
      <button class="btn" type="button" data-act="back">رجوع</button>
      <span class="ba-title" id="baTitle"></span>
      <button class="btn" type="button" data-act="star" id="baStar" aria-label="حفظ الموقع"></button>
    </div>
    <div class="ba-body">
      <div class="ba-stagewrap"><div class="ba-stage" id="baStage">
        <canvas id="baCanvas" aria-label="صورة القمر الصناعي للموقع"></canvas>
        <span class="ba-tag b" id="baTagB" hidden></span><span class="ba-tag a" id="baTagA" hidden></span>
        <div class="ba-handle" id="baHandle" role="slider" tabindex="0" aria-label="الفاصل بين صورة قبل وصورة بعد" aria-valuemin="0" aria-valuemax="100" aria-valuenow="50" hidden><i></i></div>
        <div class="ba-scale" id="baScale" hidden></div>
        <div class="ba-q" id="baQ" hidden></div>
        <div class="ba-veil" id="baVeil" role="status"></div>
      </div></div>
      <div class="ba-side wrap">
        <div class="seg ba-modes" id="baModes" role="toolbar" aria-label="نوع العرض"></div>
        <div class="ba-row" id="baMeasure"></div>
        <div id="baDates"></div>
        <details class="ba-all" id="baAll"><summary id="baAllSum">كل تواريخ المرور</summary><div id="baList"></div></details>
        <div class="ba-find" id="baFind"></div>
        <div class="ba-row"><span>عرض المربع</span><div class="seg" id="baSizes"></div></div>
        <div class="ba-row" id="baEvent"></div>
        <div class="actions" id="baActions"></div>
        <div class="help" id="baHelp"></div>
      </div>
    </div>`;
  s.hidden = false; s.scrollTop = 0;
  bindStage();
  remember();
  renderChrome();
  start();
  if (!V.title) nameThePlace();
}
// the last places opened, for the chooser
function remember() {
  const me = {lat: +V.lat.toFixed(5), lon: +V.lon.toFixed(5), half: V.half, name: V.title};
  const list = store.get(KEYS.recent, []).filter(p => !samePlace(p, me));
  list.unshift(me);
  store.set(KEYS.recent, list.slice(0, 6));
  const saved = places(), mine = saved.find(p => samePlace(p, me));      // a watched place: seen now
  if (mine) { mine.seen = Date.now(); store.set(KEYS.places, saved); }
}
// a name for a tapped point, from OpenStreetMap (the viewer does not wait for it)
async function nameThePlace() {
  const v = V;
  try {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 8000);
    const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=14&accept-language=ar&lat=${v.lat}&lon=${v.lon}`, {signal: ctl.signal});
    clearTimeout(timer);
    const d = r.ok ? await r.json() : null, name = d && d.display_name ? String(d.display_name).split("،").slice(0, 2).join("،") : "";
    if (V === v && name && !v.title) { v.sub = name; renderChrome(); }
  } catch (e) { /* the coordinates stay as the title */ }
}
const titleOf = () => V.title || V.sub || `${V.lat.toFixed(4)}, ${V.lon.toFixed(4)}`;

// everything around the picture that does not depend on the passes
function renderChrome() {
  el("#baTitle").innerHTML = bidi(titleOf());
  const saved = places().some(p => samePlace(p, V));
  el("#baStar").textContent = saved ? "★ محفوظ" : "☆ حفظ";
  el("#baModes").innerHTML = [["color", "ألوان طبيعية"], ["swir", "تحت الحمراء"], ["change", "التغيّر"]]
    .map(([m, t]) => `<button type="button" data-act="mode" data-mode="${m}" aria-pressed="${V.mode === m}">${t}</button>`).join("");
  el("#baSizes").innerHTML = SIZES.map(h => `<button type="button" data-act="size" data-half="${h}" aria-pressed="${V.half === h}">${h * 2} كم</button>`).join("");
  el("#baActions").innerHTML = `
    <button class="btn primary" type="button" data-act="shot">📸 صورة</button>
    <button class="btn" type="button" data-act="share">مشاركة</button>
    <a class="btn" href="https://www.google.com/maps?q=${V.lat},${V.lon}&t=k" target="_blank" rel="noopener">الموقع على الخريطة</a>
    <button class="btn" type="button" data-act="lite" aria-pressed="${V.lite}">${V.lite ? "توفير البيانات: مفعّل" : "توفير البيانات"}</button>`;
  el("#baEvent").innerHTML = V.incident ? "" : `<label for="baDay">تاريخ الحدث (اختياري)</label>
    <input type="date" id="baDay" value="${esc(V.event && V.event.day || "")}" max="${new Date().toISOString().slice(0, 10)}">
    ${V.event ? `<button class="btn" type="button" data-act="noday">مسح</button>` : ""}`;
  const day = el("#baDay");
  if (day && day.addEventListener) day.addEventListener("change", () => setDay(day.value));
  renderMeasure();
  el("#baHelp").innerHTML = `<b>كيف تُقرأ؟</b> اسحب الفاصل بين الصورتين، وكبّر بإصبعين. «تحت الحمراء» تُظهر الأرض المحترقة بلون بني داكن واللهب بلون برتقالي لامع. «التغيّر» يلوّن ما صار أغمق بالأحمر وما صار أفتح بالأزرق.<br>
    الصور من القمر الأوروبي Sentinel-2 (برنامج كوبرنيكوس)، كل نقطة 10 أمتار. في «كل تواريخ المرور» تواريخ إضافية من القمر الأمريكي Landsat (30 م، تصل متأخرة أسبوعين إلى ثلاثة). الظلال والمدّ والزراعة والبناء تتغيّر طبيعياً، والصور لا تحدد السبب.
    <span id="baData"></span>`;
  renderData();
}
// the distance between two tapped points, and the coordinates of the last one
function renderMeasure() {
  const v = V, box = el("#baMeasure"), m = v.measure;
  if (!box) return;
  let text = "";
  if (m && m.pts.length) {
    const last = m.pts[m.pts.length - 1], [lat, lon] = I.gridPoint(v.grid, last.x, last.y), at = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    text = (m.pts.length === 2 ? `<b>${distanceText(Math.hypot(m.pts[1].x - m.pts[0].x, m.pts[1].y - m.pts[0].y) * v.grid.res)}</b>` : "اضغط النقطة الثانية")
      + ` <button class="btn" type="button" data-act="copy" data-text="${at}" dir="ltr">${at}</button>`;
  } else if (m) text = "اضغط نقطتين على الصورة";
  box.innerHTML = `<button class="btn" type="button" data-act="measure" aria-pressed="${!!m}">📏 قياس</button>
    <button class="btn" type="button" data-act="blink" aria-pressed="${v.blink}">${v.blink ? "إيقاف الوميض" : "وميض"}</button>
    ${v.zoom > 1.01 ? `<button class="btn" type="button" data-act="fit">الصورة كاملة</button>` : ""}<span>${text}</span>`;
}
function setDay(day) {
  const v = V, event = day ? eventOfDay(day) : null;
  if (day && !event) return;
  v.event = event; v.auto = true; v.want = {};
  v.windows = windowsFor(Date.now(), event, null);
  renderChrome(); start();
}
function renderData() {
  const d = el("#baData");
  if (d) d.textContent = engine.stats.bytes > 5e4 ? ` حُمّل ${num(engine.stats.bytes / 1e6, 1)} م.ب في هذه الجلسة.` : "";
}

/* ---------- the passes ---------- */
async function start() {
  const v = V, run = ++v.run;
  v.session.abort(); if (v.load) v.load.abort();
  v.session = new AbortController();
  v.grid = I.makeGrid(v.lat, v.lon, v.half);
  Object.assign(v, {passes: [], before: null, after: null, shown: {}, img: {}, change: null, hot: {before: [], after: []},
    checked: false, error: null, zoom: 1, cx: 0.5, cy: 0.5, measure: null});
  renderMeasure();
  v.scenes.clear();
  veil("جارٍ البحث عن مرور القمر فوق الموقع…");
  renderDates(); renderList(); renderFind(); paint();
  let passes;
  try {
    const lists = await Promise.all(v.windows.map(([a, b]) => engine.listPasses(v.grid, a, b, v.session.signal)));
    const seen = new Set();
    passes = lists.flat().filter(p => !seen.has(p.key) && seen.add(p.key)).sort((a, b) => b.time - a.time);
  } catch (e) { if (V === v && run === v.run && e.code !== "aborted") fail(e, "start"); return; }
  if (V !== v || run !== v.run) return;
  v.passes = passes;
  if (!passes.length) { fail({code: "none"}, "older"); return; }
  if (!v.auto) {                                     // dates given in a link
    v.before = passes.find(p => p.date === v.want.before) || null;
    v.after = passes.find(p => p.date === v.want.after) || null;
    if (v.before || v.after) loadPair(); else v.auto = true;
  }
  tick(false);
  const signal = v.session.signal;
  await Promise.all(checkOrder(passes, v.event).map(p => engine.assessPass(p, v.grid, signal)
    .catch(e => { if (e.code !== "aborted") p.failed = true; })
    .then(() => { if (V === v && run === v.run) tick(false); })));
  if (V !== v || run !== v.run) return;
  I.refineSky(passes);
  v.checked = true;
  tick(true);
}
// after every checked pass: refresh the lists and, while the pair is still automatic, settle it
function tick(final) {
  const v = V;
  renderList(); renderDates(); renderData();
  if (!v.auto) return;
  const c = choose(v.passes, v.event);
  if (!final && !(c.before.done && c.after.done)) return;
  const b = c.before.pass, a = c.after.pass;
  if (b === v.before && a === v.after && (v.shown.before || v.shown.after || v.load)) return;
  if (!b && !a) { if (final) { v.before = v.after = null; fail({code: "cloud"}); } return; }
  v.before = b; v.after = a;
  loadPair();
}
function setSide(side, pass) {
  const v = V, other = side === "before" ? v.after : v.before;
  if (pass && other && (side === "before" ? pass.time >= other.time : pass.time <= other.time)) { toast("صورة «قبل» يجب أن تكون أقدم من «بعد»"); return; }
  v[side] = pass; v.auto = false;
  syncRoute();
  renderDates(); renderList();
  loadPair();
}
function step(side, dir) {
  const v = V, cur = v[side];
  const list = v.passes.filter(p => p === cur || I.grade(p) !== 2).sort((a, b) => a.time - b.time);
  const other = side === "before" ? v.after : v.before;
  const ok = p => !other || (side === "before" ? p.time < other.time : p.time > other.time);
  let i = cur ? list.indexOf(cur) + dir : dir > 0 ? 0 : list.length - 1;
  while (i >= 0 && i < list.length && !ok(list[i])) i += dir;
  if (i < 0 || i >= list.length) { toast(dir < 0 ? "لا مرور أقدم ضمن القائمة. افتح «كل تواريخ المرور»." : "لا مرور أحدث"); return; }
  setSide(side, list[i]);
}
function syncRoute() {
  const v = V;
  if (v.incident) return;
  const hash = routeOf({lat: v.lat, lon: v.lon, half: v.half, before: v.before && v.before.date, after: v.after && v.after.date, name: v.title});
  try { history.replaceState(null, "", location.pathname + "#" + encodeURIComponent(hash)); } catch (e) { /* the link stays as it was */ }
}
function passLine(p) { return `${dateAr(p.time)} <small>· ${timeAr(p.time)} · ${esc(skyText(p))}${sensorText(p)}</small>`; }
function renderDates() {
  const v = V, row = (side, label) => {
    const p = v[side];
    return `<div class="ba-date ${side}">
      <span class="k">${label}</span>
      <span class="d">${p ? `<b>${dateAr(p.time)}</b><small>${timeAr(p.time)} · ${esc(skyText(p))}${sensorText(p)}</small>`
        : `<b>—</b><small>${v.passes.length ? (side === "after" && v.event && v.checked ? "لم يمر القمر بصورة صافية بعد الحدث" : "لم تُحدَّد") : "…"}</small>`}</span>
      <button class="btn" type="button" data-act="step" data-side="${side}" data-dir="-1">أقدم</button>
      <button class="btn" type="button" data-act="step" data-side="${side}" data-dir="1">أحدث</button>
    </div>`;
  };
  el("#baDates").innerHTML = row("before", "قبل") + row("after", "بعد");
}
function renderList() {
  const v = V;
  el("#baAllSum").textContent = v.passes.length ? `كل تواريخ المرور (${v.passes.length})` : "كل تواريخ المرور";
  el("#baList").innerHTML = v.passes.map(p => `<div class="ba-pass">
      <span class="g g${p.failed ? 3 : I.grade(p)}"></span><span class="t">${passLine(p)}</span>
      <button class="btn" type="button" data-act="take" data-side="before" data-key="${esc(p.key)}" aria-pressed="${v.before === p}">قبل</button>
      <button class="btn" type="button" data-act="take" data-side="after" data-key="${esc(p.key)}" aria-pressed="${v.after === p}">بعد</button>
    </div>`).join("") + (v.passes.length || v.error ? `<button class="btn ba-wide" type="button" data-act="older">تواريخ أقدم (4 أشهر أخرى)</button>` : "")
    + (v.passes.length && v.windows[v.windows.length - 1][1] < Date.now() - DAY ? `<button class="btn ba-wide" type="button" data-act="newer">تواريخ أحدث (4 أشهر أخرى)</button>` : "");
}

/* ---------- the pictures ---------- */
async function scene(pass, quality, signal) {
  if (pass.sensor === "landsat") quality = "full";            // 30 m either way: read once
  const v = V, key = pass.key + "|" + quality;
  if (v.scenes.has(key)) return v.scenes.get(key);
  const sc = await engine.loadScene(pass, v.grid, quality, signal);
  if (V === v) {
    v.scenes.set(key, sc);
    while (v.scenes.size > 6) v.scenes.delete(v.scenes.keys().next().value);
  }
  return sc;
}
async function loadPair() {
  const v = V, gen = ++v.gen;
  if (v.load) v.load.abort();
  const ctl = v.load = new AbortController();
  v.error = null;
  const sides = ["before", "after"].filter(k => v[k]);
  for (const k of ["before", "after"]) if (!v[k] || (v.shown[k] && v.shown[k].pass !== v[k])) delete v.shown[k];
  rebuild();
  if (!sides.length) return;
  const full = v.grid.res < 20 && !v.lite;
  const stages = full ? ["fast", "full"] : [v.grid.res < 20 ? "fast" : "full"];
  try {
    for (const q of stages) {
      v.busy = q === "full" && stages.length > 1 ? "جارٍ تحسين الدقة إلى 10 أمتار…" : "جارٍ تحميل الصور من الأرشيف…";
      showBusy();
      await Promise.all(sides.map(async k => {
        // a sharper copy already in memory is used straight away
        const have = v.scenes.get(v[k].key + "|full");
        const sc = have || await scene(v[k], q, ctl.signal);
        if (V !== v || gen !== v.gen) return;
        v.shown[k] = sc;
        rebuild();
      }));
      if (V !== v || gen !== v.gen) return;
    }
    v.busy = ""; v.load = null;
    showBusy(); renderData();
  } catch (e) {
    if (V !== v || gen !== v.gen || e.code === "aborted") return;
    v.busy = ""; v.load = null;
    fail(e, "load");
  }
}
function toCanvas(rgba, n) {
  const c = document.createElement("canvas");
  c.width = c.height = n;
  c.getContext("2d").putImageData(new ImageData(rgba, n, n), 0, 0);
  return c;
}
// from the scenes in hand: the pictures, the change layer and the hot points; then draw
function rebuild() {
  const v = V, b = v.shown.before, a = v.shown.after, both = [b, a].filter(Boolean), n = v.grid.size;
  v.img = {}; v.change = null; v.hot = {before: [], after: []};
  if (both.length) {
    const cs = I.stretchOf(both, I.COLOR_BANDS), ss = I.stretchOf(both, I.SWIR_BANDS);
    for (const [k, sc] of [["before", b], ["after", a]]) {
      if (!sc) continue;
      v.img[k] = {color: toCanvas(I.colorImage(sc, cs), n), swir: toCanvas(I.swirImage(sc, ss), n)};
      v.hot[k] = I.hotSpots(sc).slice(0, 40);
    }
    if (a && b) {
      v.change = I.changeOf(b, a);
      v.img.change = toCanvas(I.changeImage(v.change), n);
      v.hotSplit = splitHot(v.hot.after, v.hot.before);
    }
  }
  renderFind(); showBusy(); paint();
}
function showBusy() {
  const v = V, any = v.shown.before || v.shown.after;
  if (v.error) return;
  if (v.busy) veil(v.busy, !!any); else if (any) veil("");
  const q = el("#baQ");
  if (!any) { q.hidden = true; return; }
  const res = Math.max(...[v.shown.before, v.shown.after].filter(Boolean).map(s => s.res));
  q.hidden = false;
  q.textContent = `دقة ${Math.round(res)} م` + (v.lite && v.grid.res < 20 ? " (توفير البيانات)" : "");
}
function veil(text, soft) {
  const b = el("#baVeil");
  if (!b) return;
  b.hidden = !text;
  b.className = "ba-veil" + (soft ? " soft" : "");
  b.innerHTML = text ? `<div class="spin" aria-hidden="true"></div><p>${esc(text)}</p>` : "";
}
function fail(e, retry) {
  const v = V;
  v.error = e; v.busy = "";
  const b = el("#baVeil");
  b.hidden = false; b.className = "ba-veil";
  const text = e.code === "cloud" ? "لا صورة صافية لهذا الموقع في هذه المدة. اختر تاريخاً من «كل تواريخ المرور» أو اطلب تواريخ أقدم." : failText(e);
  b.innerHTML = `<p>${esc(text)}</p><div class="actions">${retry === "older" || e.code === "cloud" ? `<button class="btn" type="button" data-act="older">تواريخ أقدم</button>`
    : `<button class="btn primary" type="button" data-act="retry" data-what="${retry || "start"}">إعادة المحاولة</button>`}</div>`;
  renderFind(); renderList();
}

/* ---------- what was found, in words ---------- */
function renderFind() {
  const v = V, box = el("#baFind");
  if (!box) return;
  const b = v.shown.before, a = v.shown.after, out = [];
  if (!b && !a) {
    out.push(`<p>${v.error ? "لا صور معروضة." : "تُجهَّز الصورتان: أحدث صورة صافية قبل الحدث وأول صورة صافية بعده."}</p>`);
  } else if (a && b) {
    const c = v.change, other = a.pass.sensor === "landsat" || b.pass.sensor === "landsat";
    const coarse = !other && Math.max(a.res, b.res) > v.grid.res + 1;
    const quiet = c.areaHa < 0.3 && c.share < 0.0008;
    out.push(`<div class="big">${quiet ? "لا تغيّر واضح بين الصورتين" : `تغيّر مرصود: نحو ${areaText(c.areaHa)}`}</div>`);
    if (!quiet) out.push(`<div class="lg"><span><i style="background:#ff3b30"></i>صار أغمق: ${areaText(c.darkHa)}</span><span><i style="background:#22d3ee"></i>صار أفتح: ${areaText(c.brightHa)}</span><span>${num(c.share * 100, c.share < 0.01 ? 2 : 1)}% من المربع</span></div>`);
    if (c.clouds || c.compared < 0.9) out.push(`<p class="warn">غيوم أو ظلالها في إحدى الصورتين: قورن ${Math.round(c.compared * 100)}% من المربع، والتقدير أقل ثقة. اختر تاريخاً أصفى إن وُجد.</p>`);
    if (other) out.push(`<p>إحدى الصورتين من القمر الأمريكي Landsat (كل نقطة 30 م): التفاصيل أقل، والتقدير أقل ثقة.</p>`);
    if (coarse) out.push(`<p>تقدير أولي بدقة ${Math.round(Math.max(a.res, b.res))} م${v.lite ? "" : "، يُحدَّث عند اكتمال التحميل"}.</p>`);
    if (!quiet && v.mode !== "change") out.push(`<button class="btn" type="button" data-act="mode" data-mode="change">أظهر التغيّر على الصورة</button>`);
  } else if (b) {
    out.push(`<div class="big">صورة «قبل» فقط</div><p>${v.event ? "لم يمر القمر فوق الموقع بصورة صافية بعد الحدث حتى الآن. يمر كل يومين إلى خمسة أيام: أعد فتحها لاحقاً." : "اختر صورة «بعد» من التواريخ."}</p>`);
  } else {
    out.push(`<div class="big">صورة «بعد» فقط</div><p>لا صورة صافية قبلها ضمن المدة. اطلب تواريخ أقدم من «كل تواريخ المرور».</p>`);
  }
  const ha = v.hot.after, hb = v.hot.before;
  if (a && ha.length) {
    const s = b ? v.hotSplit : null;
    const what = !s ? `${ha.length === 1 ? "موقع واحد" : ha.length + " مواقع"}`
      : [s.fresh ? `${s.fresh} لم ${s.fresh === 1 ? "يكن ظاهراً" : "تكن ظاهرة"} قبل` : "", s.old ? `${s.old} ${s.old === 1 ? "كان ظاهراً" : "كانت ظاهرة"} قبل أيضاً (شعلة غاز دائمة على الأرجح)` : ""].filter(Boolean).join("، ");
    out.push(`<p><b>🔥 لهب أو حرارة شديدة لحظة التقاط صورة «بعد»:</b> ${what}.</p>`);
    const list = (s ? ha.filter(h => !h.old) : ha).slice(0, 3);
    if (list.length) out.push(`<div class="actions">${list.map(h => { const at = `${h.lat.toFixed(5)}, ${h.lon.toFixed(5)}`;
      return `<button class="btn" type="button" data-act="copy" data-text="${at}" dir="ltr">${at}</button>`; }).join("")}</div>`);
    if (v.mode === "color") out.push(`<button class="btn" type="button" data-act="mode" data-mode="swir">أظهرها بالأشعة تحت الحمراء</button>`);
  } else if (b && !a && hb.length) {
    out.push(`<p><b>🔥 لهب أو حرارة شديدة في صورة «قبل»:</b> ${hb.length === 1 ? "موقع واحد" : hb.length + " مواقع"} (شعلات الغاز تظهر هكذا دائماً).</p>`);
  }
  if (v.incident && (v.incident.place === "sea" || v.incident.place === "coast") && (a || b))
    out.push(`<p>في البحر تتحرك السفينة: أحدث صورة بعد الحدث (دخان، بقعة، السفينة نفسها) أنفع من المقارنة.</p>`);
  if (v.incident && v.incident.best !== "polar" && (a || b))
    out.push(`<p>موقع هذا الحدث تقريبي (نحو 3 كم): ابحث عن الأثر في كامل المربع، أو وسّعه.</p>`);
  box.innerHTML = out.join("");
}

/* ---------- drawing ---------- */
function sizeCanvas() {
  const cv = el("#baCanvas"), st = el("#baStage");
  if (!cv || !st) return 0;
  const css = st.clientWidth, dpr = Math.min(3, root.devicePixelRatio || 1), px = Math.max(64, Math.round(css * dpr));
  if (cv.width !== px) cv.width = cv.height = px;
  return css;
}
function paint() {
  const v = V, cv = el("#baCanvas");
  if (!v || !cv) return;
  const css = sizeCanvas(), W = cv.width, g = cv.getContext("2d"), dpr = W / Math.max(1, css);
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.fillStyle = "#05080d"; g.fillRect(0, 0, W, W);
  const b = v.img.before, a = v.img.after, both = !!(a && b);
  const tagB = el("#baTagB"), tagA = el("#baTagA"), handle = el("#baHandle"), scale = el("#baScale");
  if (!a && !b) { tagA.hidden = tagB.hidden = handle.hidden = scale.hidden = true; return; }
  const n = v.grid.size, s = W / n * v.zoom, ox = W / 2 - v.cx * n * s, oy = W / 2 - v.cy * n * s;
  let f = both ? v.split : a ? 1 : 0;
  if (both && v.blink) f = v.blinkOn ? 1 : 0;
  const X = Math.round(f * W), kind = v.mode === "swir" ? "swir" : "color";
  g.imageSmoothingEnabled = s < 3.5 * dpr; g.imageSmoothingQuality = "high";
  const marks = side => {
    if (v.mode === "color") return;
    g.lineWidth = 2 * dpr; g.strokeStyle = "#ffd60a";
    for (const h of v.hot[side]) {
      g.beginPath(); g.arc(ox + h.x * s, oy + h.y * s, Math.max(9 * dpr, Math.sqrt(h.pixels) * s * 0.9), 0, 6.2832); g.stroke();
    }
  };
  if (b && X < W) {
    g.save(); g.beginPath(); g.rect(X, 0, W - X, W); g.clip();
    g.drawImage(b[kind], ox, oy, n * s, n * s); marks("before"); g.restore();
  }
  if (a && X > 0) {
    g.save(); g.beginPath(); g.rect(0, 0, X, W); g.clip();
    g.drawImage(a[kind], ox, oy, n * s, n * s);
    if (v.mode === "change" && v.img.change) {
      g.fillStyle = "rgba(5,8,13,.3)"; g.fillRect(0, 0, X, W);
      g.imageSmoothingEnabled = false;
      g.drawImage(v.img.change, ox, oy, n * s, n * s);
    }
    marks("after"); g.restore();
  }
  // four ticks around the place itself (the centre is left free to look at)
  const px = ox + n / 2 * s, py = oy + n / 2 * s;
  g.lineCap = "round";
  for (const [w, col] of [[4 * dpr, "rgba(0,0,0,.55)"], [2 * dpr, "#fff"]]) {
    g.lineWidth = w; g.strokeStyle = col; g.beginPath();
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { g.moveTo(px + dx * 12 * dpr, py + dy * 12 * dpr); g.lineTo(px + dx * 22 * dpr, py + dy * 22 * dpr); }
    g.stroke();
  }
  const m = v.measure;
  if (m && m.pts.length) {
    const P = m.pts.map(p => [ox + p.x * s, oy + p.y * s]);
    for (const [w, col] of [[5 * dpr, "rgba(0,0,0,.6)"], [2.5 * dpr, "#ffd60a"]]) {
      g.lineWidth = w; g.strokeStyle = col; g.fillStyle = col;
      if (P.length === 2) { g.beginPath(); g.moveTo(P[0][0], P[0][1]); g.lineTo(P[1][0], P[1][1]); g.stroke(); }
      for (const [x, y] of P) { g.beginPath(); g.arc(x, y, w * 1.6, 0, 6.2832); g.fill(); }
    }
  }
  // labels, divider, scale
  tagB.hidden = !b || f > 0.94; tagA.hidden = !a || f < 0.06;
  if (b) tagB.textContent = "قبل · " + dateAr(v.shown.before.pass.time);
  if (a) tagA.textContent = "بعد · " + dateAr(v.shown.after.pass.time);
  handle.hidden = !both || v.blink;
  handle.style.left = (f * 100) + "%";
  handle.setAttribute("aria-valuenow", String(Math.round(f * 100)));
  const bar = scaleBar(v.grid.res * n / (css * v.zoom), 110);
  scale.hidden = false;
  scale.innerHTML = `${bar.label}<i style="width:${bar.px.toFixed(1)}px"></i>`;
}
BA.resize = () => { if (V) paint(); };

/* ---------- touch: drag the divider, pinch and pan ---------- */
function clampView() {
  const v = V, h = 0.5 / v.zoom;
  v.zoom = Math.min(10, Math.max(1, v.zoom));
  v.cx = Math.min(1 - h, Math.max(h, v.cx)); v.cy = Math.min(1 - h, Math.max(h, v.cy));
}
function zoomAt(z, clientX, clientY) {
  const v = V, r = el("#baStage").getBoundingClientRect();
  const px = (clientX - r.left) / r.width - 0.5, py = (clientY - r.top) / r.height - 0.5;
  const ux = v.cx + px / v.zoom, uy = v.cy + py / v.zoom;
  const was = v.zoom > 1.01;
  v.zoom = Math.min(10, Math.max(1, z));
  v.cx = ux - px / v.zoom; v.cy = uy - py / v.zoom;
  clampView(); paint();
  if (was !== v.zoom > 1.01) renderMeasure();
}
function bindStage() {
  const st = el("#baStage"), pts = new Map();
  let drag = null, pinch = null, lastTap = 0, lastX = 0, lastY = 0;
  const splitTo = e => { const r = st.getBoundingClientRect(); V.split = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)); paint(); };
  st.addEventListener("pointerdown", e => {
    if (!V || e.target.closest("button")) return;
    st.setPointerCapture(e.pointerId);
    pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
    if (pts.size === 2) {
      const [p, q] = [...pts.values()];
      pinch = {d: Math.hypot(p.x - q.x, p.y - q.y) || 1, zoom: V.zoom}; drag = null;
    } else if (pts.size === 1) {
      const onHandle = !!e.target.closest("#baHandle"), both = V.img.before && V.img.after && !V.blink;
      drag = {mode: both && (onHandle || (V.zoom <= 1.01 && !V.measure)) ? "split" : "pan", moved: false, far: 0, handle: onHandle};
      if (drag.mode === "split" && !onHandle) splitTo(e);
    }
  });
  st.addEventListener("pointermove", e => {
    const p = pts.get(e.pointerId);
    if (!p || !V) return;
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    p.x = e.clientX; p.y = e.clientY;
    if (pinch && pts.size === 2) {
      const [a, b] = [...pts.values()];
      zoomAt(pinch.zoom * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d, (a.x + b.x) / 2, (a.y + b.y) / 2);
    } else if (drag) {
      drag.far += Math.abs(dx) + Math.abs(dy);
      if (drag.far > 6) drag.moved = true;
      if (drag.mode === "split") splitTo(e);
      else { const r = st.getBoundingClientRect(); V.cx -= dx / (r.width * V.zoom); V.cy -= dy / (r.height * V.zoom); clampView(); paint(); }
    }
  });
  const up = e => {
    if (!pts.delete(e.pointerId) || !V) return;
    if (pts.size < 2) pinch = null;
    if (!pts.size) {
      const now = Date.now();
      if (drag && V.measure && !drag.moved && !drag.handle && e.type === "pointerup") {          // a tap while measuring: a point
        const r = st.getBoundingClientRect(), n = V.grid.size;
        const x = (V.cx + ((e.clientX - r.left) / r.width - 0.5) / V.zoom) * n, y = (V.cy + ((e.clientY - r.top) / r.height - 0.5) / V.zoom) * n;
        if (V.measure.pts.length >= 2) V.measure.pts = [];
        V.measure.pts.push({x, y});
        renderMeasure(); paint();
      } else if (drag && e.type === "pointerup" && now - lastTap < 320 && Math.hypot(e.clientX - lastX, e.clientY - lastY) < 30) {
        zoomAt(V.zoom > 1.5 ? 1 : 3, e.clientX, e.clientY); lastTap = 0;      // double tap
      } else { lastTap = now; lastX = e.clientX; lastY = e.clientY; }
      drag = null;
    } else drag = {mode: "pan", moved: true};
  };
  st.addEventListener("pointerup", up); st.addEventListener("pointercancel", up);
  st.addEventListener("wheel", e => { if (!V) return; e.preventDefault(); zoomAt(V.zoom * (e.deltaY < 0 ? 1.25 : 0.8), e.clientX, e.clientY); }, {passive: false});
  el("#baHandle").addEventListener("keydown", e => {
    const d = e.key === "ArrowLeft" ? -0.04 : e.key === "ArrowRight" ? 0.04 : 0;
    if (d) { e.preventDefault(); V.split = Math.min(1, Math.max(0, V.split + d)); paint(); }
  });
}
function setBlink(on) {
  const v = V;
  v.blink = on; clearInterval(v.blinkTimer);
  if (on) v.blinkTimer = setInterval(() => { if (V !== v) return clearInterval(v.blinkTimer); v.blinkOn = !v.blinkOn; paint(); }, 800);
  renderMeasure(); paint();
}

/* ---------- the picture to save: portrait, no logo ---------- */
async function renderShot() {
  const v = V, b = v.img.before, a = v.img.after;
  if (!a && !b) return null;
  const W = 1080, H = 1350, M = 12, cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const g = cv.getContext("2d"), css = getComputedStyle(document.documentElement);
  const UI = css.getPropertyValue("--font-ui") || "sans-serif", DISPLAY = css.getPropertyValue("--font-display") || UI;
  try { if (document.fonts) await Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 1500))]); } catch (e) { /* default fonts */ }
  const WHITE = "#f0f4fa", DIM = "#93a0b8", CYAN = "#40d6ec";
  g.fillStyle = "#06090e"; g.fillRect(0, 0, W, H);
  g.direction = "rtl"; g.textAlign = "right"; g.textBaseline = "alphabetic";
  const fit = (text, font, size, max) => {           // shrink, then cut, until the line fits
    let t = text;
    for (; size > 22; size -= 2) { g.font = font.replace("%", size); if (g.measureText(t).width <= max) return t; }
    while (t.length > 8 && g.measureText(t + "…").width > max) t = t.slice(0, -1);
    return t + "…";
  };
  g.fillStyle = CYAN; g.font = `600 26px ${UI}`;
  g.fillText("قبل وبعد · صور القمر الأوروبي Sentinel-2", W - 28, 50);
  g.fillStyle = WHITE;
  g.fillText(fit(titleOf(), `800 %px ${DISPLAY}`, 46, W - 56), W - 28, 112);
  g.fillStyle = DIM; g.font = `500 25px ${UI}`;
  g.fillText(`${v.lat.toFixed(4)}, ${v.lon.toFixed(4)} · مربع ${v.half * 2}×${v.half * 2} كم${v.sub && v.title ? " · " + fit(v.sub, `500 %px ${UI}`, 25, 520) : ""}`, W - 28, 156);
  const kind = v.mode === "swir" ? "swir" : "color", Y = 184;
  const panel = (img, x, y, size, label, accent, extra) => {
    g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
    g.drawImage(img, x, y, size, size);
    if (extra) extra(x, y, size);
    g.strokeStyle = "rgba(255,255,255,.14)"; g.lineWidth = 2; g.strokeRect(x + 1, y + 1, size - 2, size - 2);
    g.font = `700 27px ${UI}`;
    const w = g.measureText(label).width + 28;
    g.fillStyle = "rgba(5,8,13,.82)"; g.fillRect(x + size - w - 10, y + 10, w, 46);
    g.fillStyle = accent; g.fillRect(x + size - 14, y + 10, 4, 46);
    g.fillStyle = WHITE; g.fillText(label, x + size - 26, y + 43);
    const bar = scaleBar(v.half * 2000 / size, size * 0.3);
    g.fillStyle = "rgba(5,8,13,.6)"; g.fillRect(x + 12, y + size - 46, bar.px + 16, 36);
    g.fillStyle = WHITE; g.fillRect(x + 20, y + size - 20, bar.px, 4);
    g.font = `600 20px ${UI}`; g.textAlign = "center"; g.fillText(bar.label, x + 20 + bar.px / 2, y + size - 26); g.textAlign = "right";
  };
  const lines = [];
  if (a && b) {
    const P = (W - 3 * M) / 2, c = v.change, quiet = c.areaHa < 0.3 && c.share < 0.0008;
    panel(b[kind], W - M - P, Y, P, "قبل · " + dateAr(v.shown.before.pass.time), WHITE);
    panel(a[kind], M, Y, P, "بعد · " + dateAr(v.shown.after.pass.time), "#ff7a1a");
    const Y2 = Y + P + M;
    panel(a.color, M, Y2, P, "ما تغيّر", CYAN, (x, y, size) => {
      g.fillStyle = "rgba(5,8,13,.3)"; g.fillRect(x, y, size, size);
      g.imageSmoothingEnabled = false; g.drawImage(v.img.change, x, y, size, size);
    });
    const tx = W - 28;
    let ty = Y2 + 46;
    g.fillStyle = CYAN; g.font = `600 26px ${UI}`; g.fillText("تغيّر مرصود", tx, ty); ty += 62;
    g.fillStyle = WHITE; g.font = `800 50px ${DISPLAY}`;
    g.fillText(quiet ? "لا تغيّر واضح" : "نحو " + areaText(c.areaHa), tx, ty); ty += 54;
    g.font = `500 26px ${UI}`;
    if (!quiet) {
      for (const [col, text] of [["#ff3b30", "صار أغمق: " + areaText(c.darkHa)], ["#22d3ee", "صار أفتح: " + areaText(c.brightHa)]]) {
        g.fillStyle = col; g.fillRect(tx - 22, ty - 20, 22, 22);
        g.fillStyle = WHITE; g.fillText(text, tx - 34, ty); ty += 42;
      }
    }
    if (c.clouds || c.compared < 0.9) lines.push("غيوم في إحدى الصورتين: التقدير أقل ثقة.");
    if (v.hot.after.length) lines.push(`لهب ظاهر في صورة «بعد»: ${v.hot.after.length === 1 ? "موقع واحد" : v.hot.after.length + " مواقع"}.`);
    lines.push("تُظهر الصور التغيّر ولا تحدد سببه.", "كل نقطة 10 أمتار على الأرض.");
    g.fillStyle = DIM; g.font = `500 24px ${UI}`; ty += 14;
    for (const l of lines) { g.fillText(fit(l, `500 %px ${UI}`, 24, P - 30), tx, ty); ty += 38; }
  } else {
    const P = W - 2 * M, one = a || b, sc = v.shown.after || v.shown.before;
    panel(one[kind], M, Y, P, (a ? "بعد · " : "قبل · ") + dateAr(sc.pass.time), a ? "#ff7a1a" : WHITE);
  }
  g.textAlign = "center"; g.fillStyle = "#56627a"; g.font = `500 19px ${UI}`;
  g.fillText("Contains modified Copernicus Sentinel data " + new Date().getUTCFullYear(), W / 2, H - 22);
  return new Promise(res => { try { cv.toBlob(res, "image/png"); } catch (e) { res(null); } });
}
async function share() {
  const v = V;
  const hash = routeOf({lat: v.lat, lon: v.lon, half: v.half, before: v.before && v.before.date, after: v.after && v.after.date, name: titleOf()});
  const url = location.origin + location.pathname + "#" + encodeURIComponent(hash);
  const c = v.change, text = [titleOf(), v.before ? "قبل: " + dateAr(v.before.time) : "", v.after ? "بعد: " + dateAr(v.after.time) : "",
    c && !(c.areaHa < 0.3 && c.share < 0.0008) ? "تغيّر مرصود: نحو " + areaText(c.areaHa) : "", `${v.lat.toFixed(4)}, ${v.lon.toFixed(4)}`].filter(Boolean).join("\n");
  if (navigator.share) { try { await navigator.share({title: titleOf(), text, url}); return; } catch (e) { if (e.name === "AbortError") return; } }
  copy(text + "\n" + url);
}
function star() {
  const v = V, list = places(), at = list.findIndex(p => samePlace(p, v));
  if (at >= 0) { list.splice(at, 1); store.set(KEYS.places, list); toast("حُذف من المواقع المحفوظة"); renderChrome(); return; }
  const name = (root.prompt("اسم الموقع", titleOf()) || "").trim();
  if (!name) return;
  list.unshift({name: name.slice(0, 80), lat: +v.lat.toFixed(5), lon: +v.lon.toFixed(5), half: v.half, seen: Date.now()});
  store.set(KEYS.places, list.slice(0, 60));
  if (!v.title) { v.title = name; syncRoute(); }
  toast("حُفظ الموقع"); renderChrome();
}

/* ------------------------------------------------------------------ taps */
function onClick(e) {
  const t = e.target.closest("[data-act]");
  if (!t) return;
  const act = t.dataset.act, v = V;
  if (act === "back") return BA.back();
  if (act === "pick") return BA.startPick();
  if (act === "open") return setRoute(routeOf({lat: +t.dataset.lat, lon: +t.dataset.lon, half: +t.dataset.half || 2, name: t.dataset.name}));
  if (act === "forget") { const list = places(); list.splice(+t.dataset.i, 1); store.set(KEYS.places, list); return showChooser(); }
  if (!v) return;
  if (act === "mode") { v.mode = t.dataset.mode; renderChrome(); renderFind(); paint(); }
  else if (act === "step") step(t.dataset.side, +t.dataset.dir);
  else if (act === "take") { const p = v.passes.find(x => x.key === t.dataset.key); if (p) setSide(t.dataset.side, v[t.dataset.side] === p ? null : p); }
  else if (act === "size") { const h = +t.dataset.half; if (h !== v.half) { v.half = h; v.want = {before: v.before && v.before.date, after: v.after && v.after.date}; if (!v.auto && !v.want.before && !v.want.after) v.auto = true; syncRoute(); renderChrome(); start(); } }
  else if (act === "older" || act === "newer") {
    const first = v.windows[0][0], last = v.windows[v.windows.length - 1][1];
    v.windows = joinWindows(v.windows.concat([act === "older" ? [first - LOOK_BACK, first] : [last, last + LOOK_BACK]]), Date.now());
    v.want = {before: v.before && v.before.date, after: v.after && v.after.date};
    if (!v.want.before && !v.want.after) v.auto = true;
    start();
  }
  else if (act === "noday") setDay("");
  else if (act === "measure") { v.measure = v.measure ? null : {pts: []}; renderMeasure(); paint(); }
  else if (act === "fit") { v.zoom = 1; v.cx = v.cy = 0.5; renderMeasure(); paint(); }
  else if (act === "copy") copy(t.dataset.text);
  else if (act === "retry") { v.error = null; if (t.dataset.what === "load") loadPair(); else start(); }
  else if (act === "blink") setBlink(!v.blink);
  else if (act === "lite") { v.lite = !v.lite; store.set(KEYS.lite, v.lite); renderChrome(); loadPair(); }
  else if (act === "share") share();
  else if (act === "star") star();
  else if (act === "shot") presentPicture(renderShot, `ALFAQAAR-before-after-${v.lat.toFixed(3)}_${v.lon.toFixed(3)}.png`, titleOf(), "جارٍ تجهيز صورة المقارنة…");
}
BA.init = function () {
  const s = sheet();
  if (!s || BA.ready) return;
  BA.ready = true;
  s.addEventListener("click", onClick);
  el("#baBtn").hidden = false;
  el("#baBtn").addEventListener("click", () => BA.go("ba"));
  el("#pickCancel").addEventListener("click", BA.cancelPick);
  addEventListener("resize", BA.resize);
};
})(typeof self !== "undefined" ? self : globalThis);
