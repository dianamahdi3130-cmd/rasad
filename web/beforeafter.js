// Rasad «قبل وبعد»: the satellite-picture browser of the app. Any place the user picks (a tap on the
// map, an area drawn on it, the map as it is on screen, a name, an incident) is shown in the free
// Sentinel-2 and Landsat pictures: one date or two, with a divider to drag, a blink, or side by
// side; in natural colour, three infrared combinations, or as an index (NBR, NDVI); with a layer of
// what changed, a layer of likely burnt ground (dNBR) with the area of each class, flames at the
// moment of the picture, a point inspector, distance and area measures, and a report to save or
// print. The pictures are read in the phone itself by imagery.js; this file is the screen.
// It uses the app's own helpers from index.html (S, setRoute, toast, copy, bidi, presentPicture) and
// brings its own styles (a <style id="ba-style"> added once by BA.init).
//
// Routes:  #ba  choose a place · #ba:i:<incident id>[,options]
//          #ba:<lat>,<lon>,<half km>[,<before>,<after>][,options][;name]
//          options: s=single|blink|side (how it is shown), c=swir|veg|fire|nbr|ndvi (the bands),
//                   o=change|burn (the layer on top). A link without them opens as it always did.
(function (root) {
"use strict";
const I = root.RasadImagery || (typeof require === "function" ? require("./imagery.js") : null);
const BA = {available: !!I};
root.BA = BA;
if (typeof module === "object" && module.exports) module.exports = BA;
if (!I) return;

const DAY = 864e5;
const SIZES = [1, 2, 3, 5, 10, 20, 40];         // half the box, km (the box is twice as wide)
const LOOK_BACK = 120 * DAY;
const SHOWS = ["single", "split", "blink", "side"];
const COMBOS = ["color", "swir", "veg", "fire", "nbr", "ndvi"];
const LAYERS = ["change", "burn"];
const KEYS = {places: "firms-places", recent: "firms-places-recent", lite: "firms-ba-lite", enhance: "firms-ba-enhance",
  ticks: "firms-ba-ticks", mark: "firms-ba-mark"};
// the colours a mark can take (a thin rectangle or an arrow drawn on the pictures)
const MARK_COLORS = ["#ff3b30", "#ffd60a", "#40d6ec", "#3ddc97", "#ff7a1a", "#ffffff"];
const store = {
  get(k, d) { try { const v = JSON.parse(localStorage.getItem(k)); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode: not kept */ } },
};
const el = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const num = (v, d = 0) => Number(v).toLocaleString("en-US", {maximumFractionDigits: d, minimumFractionDigits: d});
// Latin names kept whole inside Arabic lines (the app's own helper where there is one)
const ltr = s => typeof bidi === "function" ? bidi(s) : esc(s);

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
// how it is shown, which bands, which layer: only what the link says (a link without them is as it was)
function readOptions(tokens, into) {
  for (const t of tokens) {
    const m = /^([a-z])=([a-z]+)$/.exec(t || "");
    if (!m) continue;
    if (m[1] === "s" && SHOWS.includes(m[2])) into.show = m[2];
    else if (m[1] === "c" && COMBOS.includes(m[2])) into.combo = m[2];
    else if (m[1] === "o" && LAYERS.includes(m[2])) into.layer = m[2];
  }
  return into;
}
function parseRoute(h) {
  if (h === "ba") return {view: "chooser"};
  if (!String(h).startsWith("ba:")) return null;
  const rest = h.slice(3);
  if (rest.startsWith("i:")) {
    const parts = rest.slice(2).split(","), options = [];
    while (parts.length > 1 && /^[a-z]=[a-z]+$/.test(parts[parts.length - 1])) options.push(parts.pop());
    return readOptions(options, {view: "incident", id: parts.join(",")});
  }
  const cut = rest.indexOf(";"), p = (cut < 0 ? rest : rest.slice(0, cut)).split(",");
  const lat = parseFloat(p[0]), lon = parseFloat(p[1]);
  if (p.length < 2 || !isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 75 || Math.abs(lon) > 180) return {view: "chooser"};
  const day = t => /^\d{8}$/.test(t || "") ? `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6)}` : null;
  return readOptions(p.slice(3), {view: "viewer", lat, lon, half: SIZES.includes(+p[2]) ? +p[2] : 2, before: day(p[3]), after: day(p[4]),
    name: cut < 0 ? "" : rest.slice(cut + 1)});
}
function routeOf(v) {
  const c = d => d ? d.replace(/-/g, "") : "";
  const options = [v.show && v.show !== "split" && SHOWS.includes(v.show) ? "s=" + v.show : "",
    v.combo && v.combo !== "color" && COMBOS.includes(v.combo) ? "c=" + v.combo : "",
    LAYERS.includes(v.layer) ? "o=" + v.layer : ""].filter(Boolean);
  let s;
  if (v.id != null) s = "ba:i:" + v.id;
  else {
    s = `ba:${(+v.lat).toFixed(5)},${(+v.lon).toFixed(5)},${v.half}`;
    if (v.before || v.after) s += `,${c(v.before)},${c(v.after)}`;
  }
  if (options.length) s += "," + options.join(",");
  return v.name && v.id == null ? s + ";" + v.name : s;
}
const dateAr = t => new Date(t).toLocaleDateString("ar-IQ-u-nu-latn", {timeZone: "Asia/Baghdad", day: "numeric", month: "long", year: "numeric"});
const timeAr = t => new Date(t).toLocaleTimeString("en-GB", {timeZone: "Asia/Baghdad", hour: "2-digit", minute: "2-digit"});
const timeUTC = t => new Date(t).toISOString().slice(11, 16);
// the day, the month and the year of a pass in Baghdad, apart (for the date strip)
function dayParts(t) {
  const parts = new Intl.DateTimeFormat("ar-IQ-u-nu-latn", {timeZone: "Asia/Baghdad", day: "numeric", month: "long", year: "numeric"}).formatToParts(new Date(t));
  const get = type => (parts.find(p => p.type === type) || {value: ""}).value;
  return {day: get("day"), month: get("month"), year: get("year")};
}
function skyText(p) {
  if (p.failed) return "تعذّر فحصها";
  if (!p.sky) return "تُفحص…";
  if (I.isClear(p)) return "صافية";
  const cloud = Math.round((p.sky.cloud + p.sky.shadow + p.sky.cirrus * 0.5) * 100), out = [];
  if (p.cover < 0.985 || p.sky.nodata > 0.02) out.push("تغطي جزءاً من المربع");
  if (cloud >= 1) out.push(`غيوم ${cloud}%`);
  return out.join(" · ") || "صافية تقريباً";
}
// how much of the box a pass shows, in per cent (cloud, its shadow, half of thin cirrus and what the pass does not cover are out)
const clearPct = p => p && p.sky ? Math.max(0, Math.min(100, Math.round((1 - p.sky.hidden) * 100))) : null;
// 0 clear, 1 partly, 2 cloudy, 3 not checked yet, 4 could not be checked
const gradeOf = p => p.failed ? 4 : I.grade(p);
function areaText(ha) {
  if (!(ha > 0)) return "0 م²";
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
// Sentinel-2 (10 m) for the default pictures; Landsat (30 m) only where there is nothing else
const sharpest = all => { const sharp = all.filter(p => p.sensor !== "landsat"); return sharp.length ? sharp : all; };
// The default pair while the passes are being checked one by one (`passes` newest first).
function choose(all, event) {
  const passes = sharpest(all);
  if (event && event.start) {
    const s = Date.parse(event.start), e = Date.parse(event.end || event.start);
    const after = settle(passes.filter(p => p.time > e).reverse()), older = passes.filter(p => p.time < s);
    return {before: after.done ? sameTrack(older, settle(older), after.pass) : settle(older), after};
  }
  const after = settle(passes);
  if (!(after.done && after.pass)) return {before: {pass: null, done: after.done}, after};
  const older = passes.filter(p => p.time < after.pass.time - 36e5);
  return {before: sameTrack(older, settle(older), after.pass), after};
}
// «قبل» from the same track of the satellite as «بعد»: when the nearest clear pass was taken from
// the track beside it (towers lean the other way, shadows fall differently: tens of hectares of
// "change" that never happened), a clear pass of the same track at most twelve days older takes its
// place (the engine's sameView). Not settled while such a pass is still being checked.
function sameTrack(list, first, other) {
  if (!first.done || !first.pass || !other || I.sameView(first.pass, other)) return first;
  for (const p of list) {
    if (p.time >= first.pass.time || first.pass.time - p.time > I.SAME_VIEW_REACH || !I.sameView(p, other)) continue;
    if (!p.sky && !p.failed) return {pass: null, done: false};
    if (p.sky && I.isClear(p)) return {pass: p, done: true};
  }
  return first;
}
// the order in which passes are checked: nearest to the event first, else newest first
function checkOrder(passes, event) {
  const coarse = p => p.sensor === "landsat" ? 1 : 0;              // the default pair's candidates first
  if (!event || !event.start) return passes.slice().sort((a, b) => coarse(a) - coarse(b));
  const mid = (Date.parse(event.start) + Date.parse(event.end || event.start)) / 2;
  return passes.slice().sort((a, b) => coarse(a) - coarse(b) || Math.abs(a.time - mid) - Math.abs(b.time - mid));
}
const sensorText = p => p.sensor === "landsat" ? " · Landsat 30 م" : "";
// "Sentinel-2B", "Landsat 9": the satellite of a pass by its name; "S2B", "L9" for a small chip
function satName(p) {
  const s = String(p && p.platform || "");
  let m = /^sentinel-?(\d)([a-z])$/i.exec(s);
  if (m) return `Sentinel-${m[1]}${m[2].toUpperCase()}`;
  m = /^landsat-?(\d+)$/i.exec(s);
  if (m) return "Landsat " + m[1];
  return p && p.sensor === "landsat" ? "Landsat" : "Sentinel-2";
}
const satShort = p => satName(p).replace(/^Sentinel-/, "S").replace(/^Landsat ?/, "L");
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
  const steps = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000];
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

/* ---------- the box: from a tap, a drawn rectangle or the map as it is on screen ---------- */
const KM_LAT = 110.574;                                            // kilometres in one degree of latitude
const kmLon = lat => 111.32 * Math.cos(lat * Math.PI / 180);       // and of longitude, at this latitude
const kmText = km => num(km, km < 10 ? 1 : 0);
// A picture never holds more than 800 x 800 points (400 x 400 for a wide box with «توفير البيانات»):
// a wider box is read from the files' coarser overviews, so each point is more ground.
const pointsOf = (half, lite) => lite && half >= 10 ? 400 : 800;
const pixelOf = (half, lite) => Math.max(10, 2000 * half / pointsOf(half, lite));     // metres of ground in one point of the picture
const metresText = m => `${num(m, m % 1 ? 1 : 0)} م`;
// The engine reads square boxes of fixed sizes: a drawn rectangle becomes the smallest of them that
// holds it (centred on it). `cut` says the rectangle is wider than the widest box.
function boxOfRect(r) {
  const lat = (r.south + r.north) / 2, lon = (r.west + r.east) / 2;
  const hKm = Math.abs(r.north - r.south) * KM_LAT, wKm = Math.abs(r.east - r.west) * kmLon(lat), need = Math.max(wKm, hKm);
  const half = SIZES.find(h => 2 * h >= need * 0.97) || SIZES[SIZES.length - 1];
  return {lat, lon, wKm, hKm, half, sideKm: 2 * half, cut: need * 0.97 > 2 * half};
}
// «صوّر ما أراه»: the size nearest to what the map shows (a square of the same area), around its
// centre. `far` says the map is zoomed out so much that even the widest box is a small part of it.
function boxOfView(r) {
  const lat = (r.south + r.north) / 2, lon = (r.west + r.east) / 2;
  const hKm = Math.abs(r.north - r.south) * KM_LAT, wKm = Math.abs(r.east - r.west) * kmLon(lat), side = Math.sqrt(wKm * hKm);
  let half = SIZES[0];
  for (const h of SIZES) if (Math.abs(Math.log(2 * h / side)) < Math.abs(Math.log(2 * half / side))) half = h;
  return {lat, lon, wKm, hKm, half, sideKm: 2 * half, far: side > 5 * SIZES[SIZES.length - 1]};
}
// the corners of the square box around a place, as the engine's grid lays it (for drawing it on the map)
function squareOf(lat, lon, half) {
  return {south: lat - half / KM_LAT, north: lat + half / KM_LAT, west: lon - half / kmLon(lat), east: lon + half / kmLon(lat)};
}
const boxText = b => `${kmText(b.wKm)} × ${kmText(b.hKm)} كم ← مربع ${b.sideKm} × ${b.sideKm} كم`;
// wide boxes open on one picture (two dates of 80 km are a lot to load); the rest on the comparison
const showFor = half => half >= 20 ? "single" : "split";

/* ---------- the dates: a strip of all passes, and the quick choices ---------- */
// One chip per pass, newest first, Sentinel-2 and Landsat together. `year` is set where the year
// changes (the strip then says so); `mark` says which side the pass is chosen for.
function stripOf(passes, chosen = {}) {
  let year = "";
  return passes.slice().sort((a, b) => b.time - a.time).map(p => {
    const d = dayParts(p.time), g = gradeOf(p);
    const chip = {pass: p, key: p.key, day: d.day, month: d.month, year: d.year !== year ? d.year : "", sat: satShort(p), name: satName(p),
      metres: p.sensor === "landsat" ? 30 : 10, grade: g, dim: g === 2 || g === 4,
      mark: p === chosen.before ? "before" : p === chosen.after ? "after" : "",
      title: `${dateAr(p.time)} · ${timeAr(p.time)} · ${skyText(p)} · ${satName(p)}`};
    year = d.year;
    return chip;
  });
}
// «أحدث صورة صافية»: the newest pass that shows the whole box (a sharp one, where there is one)
function latestClear(passes) {
  const clear = list => list.slice().sort((a, b) => b.time - a.time).find(p => p.sky && I.isClear(p));
  return clear(sharpest(passes)) || clear(passes) || null;
}
// of the passes already checked (newest first): the first clear one, else the first that is partly clear
const newestGood = list => list.find(p => p.sky && I.isClear(p)) || list.find(p => p.sky && I.grade(p) === 1) || null;
// the pass to show beside one already chosen: the clear pass before it (or after it), around the event if there is one
function partnerOf(passes, side, pass, event) {
  const list = sharpest(passes).slice().sort((a, b) => b.time - a.time), c = event && event.start ? choose(list, event) : null;
  if (side === "before") {                                   // looking for «قبل»: older than the pass in hand
    if (c && c.before.pass && c.before.pass.time < pass.time - 36e5) return c.before.pass;
    return newestGood(list.filter(p => p.time < pass.time - 36e5));
  }
  if (c && c.after.pass && c.after.pass.time > pass.time + 36e5) return c.after.pass;
  return newestGood(list.filter(p => p.time > pass.time + 36e5));
}

/* ---------- the views ---------- */
const SHOW_AR = {single: "صورة واحدة", split: "فاصل", blink: "وميض", side: "جنباً إلى جنب"};
// [name, what it shows, the legend: colour and meaning] of each band combination
const COMBO_AR = {
  color: {name: "ألوان طبيعية", note: "كما تراها العين", legend: []},
  swir: {name: "تحت الحمراء القصيرة", note: "الأرض المحترقة بنية داكنة، واللهب برتقالي لامع، والنبات أخضر",
    legend: [["#4fae58", "نبات"], ["#6b3a2a", "أرض محترقة"], ["#ff9a3c", "لهب"]]},
  veg: {name: "نبات (أحمر كاذب)", note: "النبات أحمر: كلما اشتدّ احمراره كان أكثف",
    legend: [["#d2362f", "نبات"], ["#9fb3c2", "عمران وأرض جرداء"], ["#1c2b3a", "ماء"]]},
  fire: {name: "حرائق وأثر احتراق", note: "اللهب أحمر إلى أصفر لامع ويظهر من خلال الدخان، وأثر الاحتراق بني محمر",
    legend: [["#ffcc33", "لهب"], ["#7a2f25", "أثر احتراق"], ["#4fae58", "نبات"]]},
  nbr: {name: "مؤشر الاحتراق NBR", note: "ينخفض حيث احترق النبات", ends: ["محترق أو أجرد داكن", "نبات سليم"]},
  ndvi: {name: "مؤشر النبات NDVI", note: "يرتفع مع كثافة النبات", ends: ["بلا نبات", "نبات كثيف"]},
};
const LAYER_AR = {change: "التغيّر", burn: "أثر احتراق محتمل"};
const BURN_AR = ["", "منخفض", "متوسط", "مرتفع", "شديد"];
const BAND_AR = {blue: "أزرق", green: "أخضر", red: "أحمر", nir: "تحت الحمراء القريبة", nir08: "تحت الحمراء القريبة (ضيق)",
  swir16: "تحت الحمراء القصيرة 1", swir22: "تحت الحمراء القصيرة 2"};
// ESA's scene classes in words (Landsat only tells cloud, shadow, water and clear ground apart)
const CLASS_AR = {0: "لا بيانات", 1: "بكسل معطوب", 2: "منطقة داكنة", 3: "ظل غيم أو أرض داكنة", 4: "نبات", 5: "أرض جرداء", 6: "ماء",
  7: "غير مصنّف", 8: "غيم (احتمال متوسط)", 9: "غيم", 10: "سحاب رقيق", 11: "ثلج"};
const classText = (c, pass) => c == null ? "" : pass && pass.sensor === "landsat" && c === 5 ? "أرض بلا غيم" : CLASS_AR[c] || "";
// the bands of a view under their names on the satellite: "B12 · B8A · B04"
const bandsText = (combo, scene) => I.comboNames(combo, scene).join(" · ");
// A view that one of the satellites on screen cannot show is closed, with a word why.
function comboNote(combo, passes) {
  const bad = passes.filter(Boolean).find(p => !I.comboAvailable(combo, p));
  return bad ? `غير متاحة في صور ${satName(bad)}` : "";
}
// What is loaded, and in which order, for a picture of this sharpness: first the bands on screen at
// 20 m, then the rest (for the change, the flames and the inspector), then the 10 m bands sharp.
// A picture coarser than 20 m, or with «توفير البيانات», has no third step.
function stagesFor(res, lite, combo) {
  const c = I.COMBOS[combo] || I.COMBOS.color, show = [...new Set(c.bands || c.index)], sharp = res < 20;
  const out = [{quality: sharp ? "fast" : "full", names: show, what: "show"},
    {quality: sharp ? "fast" : "full", names: I.REFLECTANCE.filter(b => !show.includes(b)), what: "rest"}];
  if (sharp && !lite) out.push({quality: "full", names: ["red", "green", "blue"].concat(show.includes("nir") ? ["nir"] : []), what: "sharp"});
  return out.filter(s => s.names.length);
}
// Where the pictures sit on the stage (w x h): one pane, or two for «جنباً إلى جنب»: beside each
// other on a wide stage («قبل» on the right, as the divider has it), one above the other on a tall one.
function panesOf(show, w, h, both) {
  if (show !== "side" || !both) return [{x: 0, y: 0, w, h, side: ""}];
  const gap = 2;
  if (w >= h * 1.4) return [{x: (w + gap) / 2, y: 0, w: (w - gap) / 2, h, side: "before"}, {x: 0, y: 0, w: (w - gap) / 2, h, side: "after"}];
  return [{x: 0, y: 0, w, h: (h - gap) / 2, side: "before"}, {x: 0, y: (h + gap) / 2, w, h: (h - gap) / 2, side: "after"}];
}
// the part of the picture a pane can show at this zoom, as shares of its width and height (a pane
// that is not square shows the picture as wide as its longer side)
const sharesOf = (pane, zoom) => ({x: pane.w / Math.max(pane.w, pane.h) / zoom, y: pane.h / Math.max(pane.w, pane.h) / zoom});
// area of a polygon and length of a path given in picture points, in square metres and metres
function polygonArea(pts, res) {
  let a = 0;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) a += pts[j].x * pts[i].y - pts[i].x * pts[j].y;
  return Math.abs(a) / 2 * res * res;
}
function pathLength(pts, res, closed) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  if (closed && pts.length > 2) d += Math.hypot(pts[0].x - pts[pts.length - 1].x, pts[0].y - pts[pts.length - 1].y);
  return d * res;
}
// The film of a comparison, as a script, in the display the user chose on the screen. At second t,
// `split` is how much shows «بعد»: the divider's place (divider), the fade from «قبل» to «بعد» (one
// picture), which of the two is on (blink); side by side shows both all along. Every film starts and
// ends on «قبل» (or holds still), so it loops without a jump.
const FILM_STEPS = {
  split: [["before", 1.4], ["sweep", 2.2], ["after", 2.2], ["back", 1.6], ["end", 0.4]],
  single: [["before", 1.6], ["sweep", 1.2], ["after", 2.6], ["back", 1.2], ["end", 0.4]],
  blink: [["before", 0.8], ["after", 0.8], ["before", 0.8], ["after", 0.8], ["before", 0.8], ["after", 0.8], ["before", 0.8], ["after", 0.8], ["end", 0.4]],
  side: [["side", 6.0]],
};
function filmPlan(mode) {
  const ease = x => x < 0 ? 0 : x > 1 ? 1 : x * x * (3 - 2 * x);
  const steps = FILM_STEPS[mode] || FILM_STEPS.split;
  const duration = steps.reduce((sum, st) => sum + st[1], 0);
  function at(t) {
    let left = Math.min(Math.max(t, 0), duration), phase = "end", share = 1;
    for (const [name, length] of steps) {
      if (left <= length) { phase = name; share = length ? left / length : 1; break; }
      left -= length;
    }
    const split = phase === "after" || phase === "side" ? 1 : phase === "sweep" ? ease(share) : phase === "back" ? 1 - ease(share) : 0;
    return {phase, split};
  }
  return {duration, at, mode: FILM_STEPS[mode] ? mode : "split"};
}
// what the phone can record: MP4 where it can (it plays everywhere), else WebM
function filmType() {
  if (typeof MediaRecorder !== "function") return null;
  const kinds = ["video/mp4;codecs=avc1.42E01E", "video/mp4", "video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"];
  return kinds.find(k => { try { return MediaRecorder.isTypeSupported(k); } catch (e) { return false; } }) || null;
}
function failText(e) {
  const code = e && e.code;
  if (code === "offline") return "لا اتصال بالإنترنت.";
  if (code === "timeout") return "أرشيف الصور بطيء الآن. حاول مرة أخرى.";
  if (code === "none") return "لا توجد صور لهذا الموقع في هذه المدة.";
  return "أرشيف الصور الأوروبي المفتوح لا يستجيب الآن. حاول بعد قليل.";
}

/* ---------- what was found, in words (the summary card, the report, the shared text) ---------- */
const quietChange = c => c.areaHa < 0.3 && c.share < 0.0008;
const quietBurn = b => b.areaHa < 0.3 && b.share < 0.0008;
const hotCount = n => n === 1 ? "موقع واحد" : n === 2 ? "موقعان" : n <= 10 ? `${n} مواقع` : `${n} موقعاً`;
// the standing cautions of every comparison, with the true size of a point
function caveatsOf(d) {
  const out = [];
  if (d.pair) out.push("غيوم إحدى الصورتين وظلالها لا تُحسب تغيّراً، وتُذكر النسبة التي قورنت من المربع.");
  out.push("الصور تُظهر التغيّر المرصود ولا تحدد سببه.");
  out.push(d.landsat ? "كل نقطة 10 أمتار على الأرض في صور Sentinel-2 و30 متراً في صور Landsat: لا يظهر ما هو أصغر من ذلك."
    : "كل نقطة 10 أمتار على الأرض: لا يظهر ما هو أصغر من ذلك.");
  if (d.pixel > 10.5) out.push(`هذا المربع واسع: كل نقطة في الصورة المعروضة ≈ ${metresText(d.pixel)}.`);
  if (d.burn) out.push("أثر الاحتراق المحتمل (dNBR) مؤشر لا دليل: الحصاد والحرث والجفاف والغمر تخفّض المؤشر كذلك.");
  if (d.enhance) out.push("«تحسين العرض» يزيد التباين والحدّة فقط، ولا يضيف تفاصيل غير موجودة في الصورة.");
  return out;
}
// Everything the report says, as plain data, from what the viewer holds:
//   d = {title, lat, lon, half, pixel, combo, names, enhance, sides: [{side, pass, res}], change, burn,
//        hot: {after, fresh, old} | null, now}
// The picture report and the printed one are drawn from this, so they say the same.
function reportModel(d) {
  const pair = d.sides.length === 2, landsat = d.sides.some(s => s.pass.sensor === "landsat"), sentinel = d.sides.some(s => s.pass.sensor !== "landsat");
  const sides = d.sides.map(s => {
    const sensor = I.sensorOf(s.pass), clear = clearPct(s.pass), baghdad = timeAr(s.pass.time), utc = timeUTC(s.pass.time);
    const name = satName(s.pass), res = metresText(Math.max(s.res || 0, sensor.metres));
    return {side: s.side, label: !pair ? "الصورة" : s.side === "before" ? "قبل" : "بعد", date: dateAr(s.pass.time), baghdad, utc,
      time: `${baghdad} بتوقيت بغداد · ${utc} UTC`, satName: name, sensor: sensor.instrument, res,
      sat: `${name} · ${sensor.instrument} · ${res}`, clear: clear == null ? "" : `صفاء ${clear}%`, sky: skyText(s.pass)};
  });
  const m = {brand: "ALFAQAAR FIRMS · رصد", kind: "تقرير صور الأقمار", title: d.title,
    where: `${(+d.lat).toFixed(5)}, ${(+d.lon).toFixed(5)}`, box: `مربع ${d.half * 2} × ${d.half * 2} كم`, pixel: `كل نقطة ≈ ${metresText(d.pixel)}`,
    comboName: COMBO_AR[d.combo].name, bands: d.names.join(" · "), combo: `${COMBO_AR[d.combo].name} (${d.names.join(" · ")})`,
    sides, change: null, burn: null, hot: "", summary: sides.map(s => `${s.label}: ${s.date} · ${s.sat}${s.clear ? " · " + s.clear : ""}.`), sources: []};
  if (pair && d.change) {
    const c = d.change, quiet = quietChange(c);
    m.change = {title: "تغيّر مرصود", headline: quiet ? "لا تغيّر واضح بين الصورتين" : `نحو ${areaText(c.areaHa)}`,
      rows: quiet ? [] : [["#ff3b30", "صار أغمق", areaText(c.darkHa)], ["#22d3ee", "صار أفتح", areaText(c.brightHa)]],
      note: `قورن ${Math.round(c.compared * 100)}% من المربع` + (c.clouds || c.compared < 0.9 ? " (غيوم أو ظلالها في إحدى الصورتين: التقدير أقل ثقة)" : "")};
    m.summary.push(quiet ? "لا تغيّر واضح بين الصورتين." : `تغيّر مرصود: نحو ${areaText(c.areaHa)} (صار أغمق ${areaText(c.darkHa)}، صار أفتح ${areaText(c.brightHa)}).`);
    m.summary.push(m.change.note + ".");
  }
  if (pair && d.burn) {
    const b = d.burn, quiet = quietBurn(b);
    m.burn = {title: "أثر احتراق محتمل (dNBR)", headline: quiet ? "لا أثر احتراق واضح" : `نحو ${areaText(b.areaHa)}`,
      rows: quiet ? [] : [1, 2, 3, 4].map(k => [I.BURN_COLORS[k - 1], BURN_AR[k], areaText(b.classHa[k]), b.classHa[k] > 0]).filter(r => r[3]).map(r => r.slice(0, 3)),
      note: `حُسب في ${Math.round(b.compared * 100)}% من المربع (يُستثنى الغيم وظله والماء واللهب)`};
    m.summary.push(quiet ? "لا أثر احتراق واضح في مؤشر dNBR."
      : `أثر احتراق محتمل أو تغيّر في الغطاء النباتي: نحو ${areaText(b.areaHa)} (${m.burn.rows.map(r => `${r[1]} ${r[2]}`).join("، ")}).`);
  }
  if (d.hot && d.hot.after) {
    m.hot = pair ? [d.hot.fresh ? `${hotCount(d.hot.fresh)} لم ${d.hot.fresh === 1 ? "يكن ظاهراً" : "تكن ظاهرة"} قبل` : "",
      d.hot.old ? `${hotCount(d.hot.old)} ${d.hot.old === 1 ? "كان ظاهراً" : "كانت ظاهرة"} قبل أيضاً (شعلة غاز دائمة على الأرجح)` : ""].filter(Boolean).join("، ") : hotCount(d.hot.after);
    m.summary.push(`لهب أو حرارة شديدة لحظة التقاط ${pair ? "صورة «بعد»" : "الصورة"}: ${m.hot}.`);
  } else if (d.hot) m.summary.push("لا لهب ظاهراً لحظة الالتقاط.");
  m.caveats = caveatsOf({pair, landsat, pixel: d.pixel, burn: !!m.burn, enhance: d.enhance});
  if (sentinel) m.sources.push("Sentinel-2 · برنامج كوبرنيكوس (وكالة الفضاء الأوروبية)");
  if (landsat) m.sources.push("Landsat · USGS/NASA");
  const now = d.now || Date.now();
  m.generated = `أُعدّ التقرير في ${dateAr(now)}، ${timeAr(now)} بتوقيت بغداد (${timeUTC(now)} UTC)`;
  m.credit = [sentinel ? "Contains modified Copernicus Sentinel data " + new Date(now).getUTCFullYear() : "",
    landsat ? "Landsat imagery courtesy of the U.S. Geological Survey" : ""].filter(Boolean).join(" · ");
  m.file = `ALFAQAAR-report-${(+d.lat).toFixed(3)}_${(+d.lon).toFixed(3)}`;
  return m;
}
// every word of a report in reading order (what is shared as text, and what the tests read)
function reportText(m) {
  const out = [m.brand, m.kind, m.title, `${m.where} · ${m.box} · ${m.pixel}`, "التركيبة: " + m.combo];
  for (const s of m.sides) out.push(`${s.label}: ${s.date}`, s.time, s.sat + (s.clear ? " · " + s.clear : ""));
  for (const part of [m.change, m.burn]) if (part) out.push(`${part.title}: ${part.headline}`, ...part.rows.map(r => `${r[1]}: ${r[2]}`), part.note);
  out.push("ملخص التحليل", ...m.summary, "تنبيهات", ...m.caveats, "المصدر: " + m.sources.join(" · "), m.generated, m.credit);
  return out.filter(Boolean);
}
BA._ = {parsePlace, parseRoute, routeOf, skyText, areaText, settle, choose, checkOrder, splitHot, scaleBar, failText, dateAr, SIZES, sensorText,
  windowsFor, joinWindows, eventOfDay, distanceText, filmPlan, filmType,
  SHOWS, COMBOS, LAYERS, timeAr, timeUTC, dayParts, clearPct, gradeOf, satName, satShort, pointsOf, pixelOf, metresText, boxOfRect, boxOfView, squareOf,
  boxText, showFor, stripOf, latestClear, partnerOf, SHOW_AR, COMBO_AR, LAYER_AR, BURN_AR, BAND_AR, classText, bandsText, comboNote, stagesFor,
  panesOf, sharesOf, polygonArea, pathLength, quietChange, quietBurn, caveatsOf, reportModel, reportText};
if (typeof document === "undefined") return;       // Node: only the helpers above

/* ------------------------------------------------------------------ state */
// a phone with little memory keeps less in hand (the pictures are simply read again when needed)
const small = typeof navigator !== "undefined" && navigator.deviceMemory && navigator.deviceMemory <= 2;
const engine = I.createEngine(small ? {bandCache: 48e6, tileCache: 32e6} : {});
let V = null;                    // the open viewer
let picking = false, from = "";  // choosing a place on the map; the route to go back to
const sheet = () => el("#baSheet");
const places = () => store.get(KEYS.places, []);
const samePlace = (a, b) => Math.abs(a.lat - b.lat) < 2e-4 && Math.abs(a.lon - b.lon) < 2e-4;
const later = fn => (root.requestAnimationFrame || (f => setTimeout(f, 16)))(fn);

// line icons, drawn in the colour of their text (no emoji: they differ from phone to phone)
const svg = d => `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const ICON = {
  back: svg('<path d="M9 6l6 6-6 6"/>'),
  older: svg('<path d="M15 6l-6 6 6 6"/>'), newer: svg('<path d="M9 6l6 6-6 6"/>'),
  star: svg('<path d="M12 3.6l2.6 5.3 5.8.8-4.2 4.1 1 5.8-5.2-2.8-5.2 2.8 1-5.8-4.2-4.1 5.8-.8z"/>'),
  single: svg('<rect x="4" y="4" width="16" height="16" rx="2"/>'),
  split: svg('<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 2v20"/>'),
  blink: svg('<path d="M13 3L5 14h6l-1 7 8-11h-6z"/>'),
  side: svg('<rect x="3" y="5" width="8" height="14" rx="1.5"/><rect x="13" y="5" width="8" height="14" rx="1.5"/>'),
  probe: svg('<circle cx="12" cy="12" r="5.5"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>'),
  ruler: svg('<path d="M3 17L17 3l4 4L7 21z"/><path d="M7.5 12.5l2 2M10.5 9.5l2 2M13.5 6.5l2 2"/>'),
  area: svg('<path d="M5 8l7-4 7 5-2 9H8z"/><circle cx="5" cy="8" r="1.3"/><circle cx="12" cy="4" r="1.3"/><circle cx="19" cy="9" r="1.3"/><circle cx="17" cy="18" r="1.3"/><circle cx="8" cy="18" r="1.3"/>'),
  fit: svg('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'), minus: svg('<path d="M5 12h14"/>'),
  camera: svg('<path d="M4 8h3l1.5-2h7L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.4"/>'),
  film: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9.5v5l4.5-2.5z"/>'),
  report: svg('<path d="M7 3h8l4 4v14H7z"/><path d="M15 3v4h4M10 12h6M10 16h6"/>'),
  print: svg('<path d="M7 9V4h10v5"/><rect x="4" y="9" width="16" height="8" rx="1.5"/><path d="M7 14h10v6H7z"/>'),
  share: svg('<circle cx="6" cy="12" r="2.4"/><circle cx="17" cy="6" r="2.4"/><circle cx="17" cy="18" r="2.4"/><path d="M8.2 10.9l6.6-3.7M8.2 13.1l6.6 3.7"/>'),
  pin: svg('<path d="M12 21s7-6.2 7-11a7 7 0 10-14 0c0 4.8 7 11 7 11z"/><circle cx="12" cy="10" r="2.4"/>'),
  draw: svg('<rect x="5" y="5" width="14" height="14" rx="1" stroke-dasharray="3 3"/><circle cx="5" cy="5" r="1.7" fill="currentColor"/><circle cx="19" cy="19" r="1.7" fill="currentColor"/>'),
  here: svg('<path d="M4 8V4h4M20 8V4h-4M4 16v4h4M20 16v4h-4"/><circle cx="12" cy="12" r="3"/>'),
  hand: svg('<path d="M12 3v18M3 12h18M12 3l-2.5 2.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5M3 12l2.5-2.5M3 12l2.5 2.5M21 12l-2.5-2.5M21 12l-2.5 2.5"/>'),
  map: svg('<path d="M9 5l6 2 5-2v14l-5 2-6-2-5 2V7z"/><path d="M9 5v14M15 7v14"/>'),
  mark: svg('<rect x="3.5" y="5" width="11" height="9" rx="1"/><path d="M13 19l7-7M20 12v5M20 12h-5"/>'),
  ticks: svg('<path d="M12 3v5M12 16v5M3 12h5M16 12h5"/>'),
  hires: svg('<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5.5 5.5M8 10.5h5M10.5 8v5"/>'),
  flame: svg('<path d="M12 3c1 3.5 5 5.5 5 10a5 5 0 01-10 0c0-2 1-3.2 2-4.2.3 1.6 1 2.4 2 2.7C10.600 9 11 6 12 3z"/>'),
};

/* ------------------------------------------------------------------ routes */
BA.route = function (h) {
  const r = parseRoute(h);
  if (!r) return false;
  stopPick();
  const look = {show: r.show, combo: r.combo, layer: r.layer};
  if (r.view === "incident") {
    const it = typeof incidentById === "function" ? incidentById(r.id) : S.items.find(x => x.id === r.id);
    if (!it) { toast("هذا الحدث لم يعد في القائمة"); showChooser(); return true; }
    openViewer({lat: it.lat, lon: it.lon, half: it.best === "polar" ? 2 : 3, title: it.headline, sub: it.where || "",
      event: {start: it.first, end: it.last}, incident: it, ...look,
      // the pair the monitor already compared and posted to the channel, when there is one
      want: it.satpic && it.satpic.after ? {before: it.satpic.before, after: it.satpic.after} : undefined});
  } else if (r.view === "viewer") {
    openViewer({lat: r.lat, lon: r.lon, half: r.half, title: r.name, want: {before: r.before, after: r.after}, ...look});
  } else showChooser();
  return true;
};
BA.hide = function () {
  if (watching) { watching.abort(); watching = null; }
  if (V) { V.session.abort(); if (V.load) V.load.abort(); clearInterval(V.blinkTimer); V = null; }
  const s = sheet();
  if (s && !s.hidden) { s.hidden = true; s.innerHTML = ""; }
  homeOpen(false);
};
BA.isOpen = () => !!(sheet() && !sheet().hidden) || !!(home() && !home().hidden);
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
// Three ways, on the app's own map: tap a point (a 4 km box around it), draw an area, or take the
// map as it is on screen. Drawing must not fight with moving the map: a switch says which of the two
// a finger does. In «تحديد» a drag draws the rectangle (or two taps set two opposite corners); the
// map then moves again, and the four corners can be pulled to adjust it before «اعرض الصور».
const pick = {mode: "point", draw: false, layer: null, rect: null, square: null, corners: [], first: null, firstMark: null, down: null, box: null, hint: null};
const canDraw = () => !!(root.L && S.map && S.map.getContainer && S.map.dragging && root.L.rectangle);
BA.startPick = function (mode) {
  if (!S.map) { toast("الخريطة غير متاحة الآن"); return; }
  BA.hide();
  picking = true;
  document.body.classList.add("picking");
  el("#pickBar").hidden = false;
  if (S.layer) S.map.removeLayer(S.layer);          // fires are not tappable while choosing
  S.map.invalidateSize();
  S.map.on("click", onPick);
  S.map.on("moveend", onMapMoved);
  pick.mode = "point";
  setPickMode(mode === "area" ? "area" : "point");
};
BA.draw = () => BA.startPick("area");
function onPick(e) {
  if (pick.mode !== "point") return;
  const {lat, lng} = e.latlng;
  stopPick();
  from = "";
  setRoute(routeOf({lat, lon: lng, half: 2}));
}
function onMapMoved() { if (picking) renderPickTools(); }
function stopPick() {
  if (!picking) return;
  picking = false;
  document.body.classList.remove("picking");
  el("#pickBar").hidden = true;
  setHint(null);
  clearDraw();
  renderPickTools();
  if (S.map) {
    S.map.off("click", onPick);
    S.map.off("moveend", onMapMoved);
    if (S.layer) S.layer.addTo(S.map);
    S.map.invalidateSize();
  }
}
BA.cancelPick = function () { stopPick(); setRoute("ba"); };
// the line of the app's own bar above the map says what a tap does now (and is put back afterwards)
function setHint(text) {
  const b = el("#pickBar b");
  if (!b) return;
  if (pick.hint == null) pick.hint = b.textContent;
  b.textContent = text == null ? pick.hint : text;
}
function setPickMode(mode) {
  if (mode === "area" && !canDraw()) { toast("رسم المنطقة غير متاح الآن. اضغط على الموقع."); mode = "point"; }
  if (pick.mode === "area" && mode !== "area") clearDraw();
  pick.mode = mode;
  if (mode === "area") {
    if (!pick.layer) pick.layer = root.L.layerGroup().addTo(S.map);
    drawMode(true);
  }
  setHint(mode === "area" ? "ارسم مستطيلاً حول المنطقة" : "اضغط على الموقع في الخريطة");
  renderPickTools();
}
// «صوّر ما أراه»: the map as it is on screen, as the nearest box
function viewBox() {
  if (!S.map || !S.map.getBounds) return null;
  const g = S.map.getBounds();
  return boxOfView({south: g.getSouth(), west: g.getWest(), north: g.getNorth(), east: g.getEast()});
}
const viewText = b => !b ? "" : b.far ? "قرّب الخريطة أولاً (أوسع مربع 80 كم)" : `الظاهر الآن نحو ${kmText(b.wKm)} × ${kmText(b.hKm)} كم ← مربع ${b.sideKm} كم`;
BA.here = function () {
  const b = viewBox();
  if (!b) { toast("الخريطة غير متاحة الآن"); return; }
  if (b.far) { toast("قرّب الخريطة إلى المنطقة أولاً: أوسع مربع 80 كم"); if (!picking) BA.startPick(); return; }
  const cur = decodeURIComponent(location.hash.slice(1));
  if (picking) from = ""; else if (!cur.startsWith("ba")) from = cur;
  stopPick();
  setRoute(routeOf({lat: b.lat, lon: b.lon, half: b.half, show: showFor(b.half)}));
};
function pickSizeText() {
  const b = pick.box;
  if (b) return `${boxText(b)} · كل نقطة ≈ ${metresText(pixelOf(b.half, false))}${b.cut ? " · أوسع من أكبر مربع: يُعرض وسطه" : ""}`;
  if (pick.first) return "اضغط الزاوية المقابلة";
  return pick.draw ? "اسحب على الخريطة لرسم مستطيل، أو اضغط زاويتين" : "اضغط «تحديد» ثم ارسم المستطيل";
}
// the tools under the map while choosing (made here: the page only gives the bar above the map)
function pickBox() {
  let box = el("#baPickTools");
  if (!box && document.body && document.body.appendChild) {
    box = document.createElement("div");
    box.id = "baPickTools"; box.className = "ba-picktools"; box.hidden = true;
    document.body.appendChild(box);
  }
  if (box && !box.baBound) { box.baBound = true; box.addEventListener("click", onPickTool); }
  return box;
}
function renderPickTools() {
  const box = pickBox();
  if (!box) return;
  box.hidden = !picking;
  const tall = px => { try { document.documentElement.style.setProperty("--ba-pick-h", px + "px"); } catch (e) { /* no styles here */ } };
  if (!picking) { box.innerHTML = ""; tall(0); return; }
  const area = pick.mode === "area", b = pick.box, seen = viewBox();
  box.innerHTML = `
    <div class="ba-seg" role="toolbar" aria-label="طريقة اختيار الموقع">
      <button type="button" data-act="pmode" data-mode="point" aria-pressed="${!area}">${ICON.pin}<span>نقطة</span></button>
      <button type="button" data-act="pmode" data-mode="area" aria-pressed="${area}">${ICON.draw}<span>ارسم منطقة</span></button>
    </div>
    ${area ? `<div class="ba-pt-row">
      <div class="ba-seg" role="toolbar" aria-label="ما يفعله الإصبع على الخريطة">
        <button type="button" data-act="pdraw" data-on="0" aria-pressed="${!pick.draw}">${ICON.hand}<span>تحريك</span></button>
        <button type="button" data-act="pdraw" data-on="1" aria-pressed="${pick.draw}">${ICON.draw}<span>تحديد</span></button>
      </div>
      <button class="ba-btn" type="button" data-act="pclear" ${b || pick.first ? "" : "disabled"}>مسح</button>
    </div>
    <p class="ba-pt-size" id="baPickSize" role="status">${esc(pickSizeText())}</p>
    <button class="ba-btn primary ba-full" type="button" data-act="pgo" ${b ? "" : "disabled"}>اعرض الصور</button>`
    : `<p class="ba-pt-size">اضغط على الموقع: تُعرض صور مربع 4 × 4 كم حوله.</p>`}
    <button class="ba-btn ba-full" type="button" data-act="here">${ICON.here}<span>صوّر ما أراه</span>${seen ? `<small>${esc(seen.far ? "قرّب الخريطة أولاً" : `مربع ${seen.sideKm} كم`)}</small>` : ""}</button>`;
  tall(box.offsetHeight || 0);
}
function onPickTool(e) {
  const t = e.target.closest("[data-act]");
  if (!t || t.disabled) return;
  const act = t.dataset.act;
  if (act === "pmode") setPickMode(t.dataset.mode);
  else if (act === "pdraw") { drawMode(t.dataset.on === "1"); renderPickTools(); }
  else if (act === "pclear") { clearRect(); drawMode(true); renderPickTools(); }
  else if (act === "here") BA.here();
  else if (act === "pgo" && pick.box) {
    const b = pick.box;
    stopPick();
    from = "";
    setRoute(routeOf({lat: b.lat, lon: b.lon, half: b.half, show: showFor(b.half)}));
  }
}
/* ---------- drawing the rectangle on the Leaflet map ---------- */
const DRAW = [["pointerdown", onDrawDown], ["pointermove", onDrawMove], ["pointerup", onDrawUp], ["pointercancel", onDrawCancel]];
function drawMode(on) {
  if (!canDraw() || pick.draw === on) return;
  const map = S.map, box = map.getContainer();
  pick.draw = on;
  if (on) {
    map.dragging.disable();
    if (map.doubleClickZoom) map.doubleClickZoom.disable();
    if (map.boxZoom) map.boxZoom.disable();
    pick.touch = box.style.touchAction;
    box.style.touchAction = "none";                 // a finger draws: the browser must not scroll or zoom instead
    box.classList.add("ba-drawing");
    for (const [type, fn] of DRAW) box.addEventListener(type, fn);
  } else {
    for (const [type, fn] of DRAW) box.removeEventListener(type, fn);
    box.classList.remove("ba-drawing");
    box.style.touchAction = pick.touch || "";
    map.dragging.enable();
    if (map.doubleClickZoom) map.doubleClickZoom.enable();
    if (map.boxZoom) map.boxZoom.enable();
    pick.down = null;
  }
}
function onDrawDown(e) {
  if (!e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) { pick.down = null; return; }     // a second finger zooms the map
  if (e.target.closest && e.target.closest(".leaflet-control, .ba-corner")) return;
  const pt = S.map.mouseEventToContainerPoint(e);
  pick.down = {id: e.pointerId, x: pt.x, y: pt.y, moved: false, at: S.map.containerPointToLatLng(pt)};
  try { S.map.getContainer().setPointerCapture(e.pointerId); } catch (err) { /* not capturable: the moves still arrive */ }
}
function onDrawMove(e) {
  const d = pick.down;
  if (!d || e.pointerId !== d.id) return;
  const pt = S.map.mouseEventToContainerPoint(e);
  if (!d.moved && Math.hypot(pt.x - d.x, pt.y - d.y) < 8) return;
  if (!d.moved) clearRect();                        // a new rectangle replaces the one before
  d.moved = true;
  setRect(d.at, S.map.containerPointToLatLng(pt));
  e.preventDefault();
}
function onDrawUp(e) {
  const d = pick.down;
  if (!d || e.pointerId !== d.id) return;
  pick.down = null;
  if (d.moved) return finishRect();
  // a tap: the first sets a corner, the second the opposite one
  if (!pick.first) {
    clearRect();
    pick.first = d.at;
    pick.firstMark = root.L.circleMarker(d.at, {radius: 7, color: "#fff", weight: 2, fillColor: "#ff7a1a", fillOpacity: 1, interactive: false}).addTo(pick.layer);
    renderPickTools();
  } else {
    const a = pick.first;
    setRect(a, d.at);
    finishRect();
  }
}
function onDrawCancel() { pick.down = null; }
// the rectangle as drawn (orange) and the square box that will really be shown (dashed white)
function setRect(a, b) {
  const L = root.L, bounds = L.latLngBounds(a, b);
  if (pick.firstMark) { pick.layer.removeLayer(pick.firstMark); pick.firstMark = null; }
  pick.first = null;
  if (!pick.rect) pick.rect = L.rectangle(bounds, {color: "#ff7a1a", weight: 2, fillColor: "#ff7a1a", fillOpacity: 0.14, interactive: false}).addTo(pick.layer);
  else pick.rect.setBounds(bounds);
  pick.box = boxOfRect({south: bounds.getSouth(), west: bounds.getWest(), north: bounds.getNorth(), east: bounds.getEast()});
  const q = squareOf(pick.box.lat, pick.box.lon, pick.box.half), square = [[q.south, q.west], [q.north, q.east]];
  if (!pick.square) pick.square = L.rectangle(square, {color: "#ffffff", weight: 1.5, dashArray: "6 6", fill: false, interactive: false}).addTo(pick.layer);
  else pick.square.setBounds(square);
  const size = el("#baPickSize");
  if (size) size.textContent = pickSizeText();
}
function finishRect() {
  if (!pick.rect) return;
  drawMode(false);                                  // the map moves again; the corners adjust the box
  placeCorners(pick.rect.getBounds());
  renderPickTools();
}
// Four handles to pull, in the order NW, NE, SE, SW. The corner across stays where it is; of the two
// neighbours one shares the pulled corner's latitude and the other its longitude, and both follow.
function placeCorners(bounds) {
  const L = root.L, at = [bounds.getNorthWest(), bounds.getNorthEast(), bounds.getSouthEast(), bounds.getSouthWest()];
  if (pick.corners.length) { at.forEach((p, i) => pick.corners[i].setLatLng(p)); return; }
  pick.corners = at.map((p, i) => {
    const m = L.marker(p, {draggable: true, keyboard: false, zIndexOffset: 1000, icon: L.divIcon({className: "ba-corner", iconSize: [34, 34]})}).addTo(pick.layer);
    const pull = () => {
      const here = m.getLatLng(), across = pick.corners[(i + 2) % 4].getLatLng();
      pick.corners[[1, 0, 3, 2][i]].setLatLng([here.lat, across.lng]);
      pick.corners[[3, 2, 1, 0][i]].setLatLng([across.lat, here.lng]);
      setRect(here, across);
    };
    m.on("drag", pull);
    m.on("dragend", () => { pull(); renderPickTools(); });
    return m;
  });
}
function clearRect() {
  if (!pick.layer) return;
  for (const l of [pick.rect, pick.square, pick.firstMark, ...pick.corners]) if (l) pick.layer.removeLayer(l);
  pick.rect = pick.square = pick.firstMark = pick.first = null; pick.corners = []; pick.box = null;
}
function clearDraw() {
  drawMode(false);
  clearRect();
  if (pick.layer && S.map) { S.map.removeLayer(pick.layer); pick.layer = null; }
}

/* ------------------------------------------------------------------ the chooser */
function placeRow(p, i, kind) {
  return `<div class="ba-place">
    <button class="t" type="button" data-act="open" data-lat="${p.lat}" data-lon="${p.lon}" data-half="${p.half || 2}" data-name="${esc(p.name || "")}">
      <b>${ltr(p.name || `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`)}</b><small>${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}</small>${kind === "saved" ? `<span class="new" id="baNew${i}"></span>` : ""}</button>
    <button class="ba-btn" type="button" data-act="show" data-lat="${p.lat}" data-lon="${p.lon}" data-name="${esc(p.name || "")}">على الخريطة</button>
    ${kind === "saved" ? `<button class="ba-btn" type="button" data-act="forget" data-i="${i}" aria-label="حذف الموقع">حذف</button>` : ""}
  </div>`;
}
// the satellite-picture workspace: in the panel beside the map (the map itself picks the place)
const home = () => el("#baHome");
function homeOpen(on) {
  const h = home();
  if (!h) return;
  const was = !h.hidden;
  if (!on) { if (!was) return; h.hidden = true; h.innerHTML = ""; }
  else h.hidden = false;
  const cl = document.body && document.body.classList;
  if (cl) { if (on) cl.add("ba-home"); else cl.remove("ba-home"); }
  if (!S.map || on === was) return;
  if (on) { S.map.on("click", onHomeClick); if (S.layer) S.map.removeLayer(S.layer); }
  else { S.map.off("click", onHomeClick); if (S.layer && !picking) S.layer.addTo(S.map); }
}
function onHomeClick(e) {
  const {lat, lng} = e.latlng;
  from = "ba";
  setRoute(routeOf({lat, lon: lng, half: 2}));
}
function showChooser(results, note) {
  BA.hide();
  const s = home() || sheet(), saved = places(), recent = store.get(KEYS.recent, []).filter(r => !saved.some(p => samePlace(p, r)));
  const seen = viewBox();
  const docked = s === home();
  s.innerHTML = `<div class="ba-app${docked ? " ba-docked" : ""}">
    ${docked ? `<div class="ba-hero">
      <span class="ba-kicker">${ICON.map}<span>مساحة العمل</span></span>
      <b class="ba-title">صور الأقمار الصناعية</b>
      <span class="ba-srcs"><bdi dir="ltr">Sentinel-2 · 10 m</bdi><bdi dir="ltr">Landsat 8/9 · 30 m</bdi></span>
      <p class="ba-tap">${ICON.pin}<span>اضغط على أي نقطة في الخريطة لعرض صورها، أو اختر طريقة أخرى.</span></p>
    </div>` : `<div class="ba-top"><button class="ba-btn" type="button" data-act="back">${ICON.back}<span>رجوع</span></button>
      <div class="ba-head"><b class="ba-title">صور الأقمار · قبل وبعد</b></div></div>`}
    <div class="ba-main ba-plain"><div class="ba-pick">
      <p class="ba-note">تصفّح صور القمر الأوروبي <bdi dir="ltr">Sentinel-2</bdi> لأي موقع وأي تاريخ، وقارن صورة قبل الحدث بصورة بعده: تظهر آثار الحريق والاسوداد والتغيّر في المباني الكبيرة، والدخان واللهب لحظة الالتقاط.</p>
      <div class="ba-ways">
        <button class="ba-way" type="button" data-act="pick">${ICON.pin}<b>اختر موقعاً من الخريطة</b><small>اضغط على النقطة: مربع 4 كم حولها</small></button>
        <button class="ba-way" type="button" data-act="draw">${ICON.draw}<b>ارسم منطقة</b><small>مستطيل حول ما تريد، حتى 80 كم</small></button>
        <button class="ba-way" type="button" data-act="here">${ICON.here}<b>صوّر ما أراه</b><small>${esc(viewText(seen) || "المنطقة الظاهرة في الخريطة الآن")}</small></button>
      </div>
      <form class="ba-search" id="baSearch"><input id="baQuery" type="search" enterkeyhint="search" placeholder="اسم مكان، أو إحداثيات مثل 26.23, 50.83" autocomplete="off"><button class="ba-btn" type="submit">بحث</button></form>
      <div id="baResults">${note ? `<p class="ba-note">${esc(note)}</p>` : ""}${(results || []).map((p, i) => placeRow(p, i, "found")).join("")}</div>
      ${saved.length ? `<h3>المواقع المحفوظة (قائمة المراقبة)</h3>${saved.map((p, i) => placeRow(p, i, "saved")).join("")}` : ""}
      ${recent.length ? `<h3>فتحتها مؤخراً</h3>${recent.map((p, i) => placeRow(p, i, "recent")).join("")}` : ""}
      <div class="ba-help"><b>ما الذي يظهر؟</b> كل نقطة في الصورة 10 أمتار على الأرض، ويمر القمر كل يومين إلى خمسة أيام. تظهر التغيّرات الكبيرة (خزانات، حظائر طائرات، مساحات محترقة)، ولا يظهر مبنى صغير أو مركبة واحدة. الصور تُظهر التغيّر المرصود ولا تحدد سببه.</div>
    </div></div></div>`;
  if (s === home()) homeOpen(true); else s.hidden = false;
  s.scrollTop = 0;
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
  el("#baResults").innerHTML = `<p class="ba-note">جارٍ البحث…</p>`;
  let found = null;
  try {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 12000);
    const r = await fetch("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&accept-language=ar&q=" + encodeURIComponent(q), {signal: ctl.signal});
    clearTimeout(timer);
    if (r.ok) found = (await r.json()).map(x => ({lat: +x.lat, lon: +x.lon, name: String(x.display_name || "").split(/[،,]\s*/).slice(0, 3).join("، ")}))
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
    want, auto: !(want.before || want.after), windows: windowsFor(now, o.event, want),
    // how it is shown, the bands, the layer on top, the flame marks (null: with any view but natural colour), the display stretch
    show: SHOWS.includes(o.show) ? o.show : "split", combo: COMBOS.includes(o.combo) ? o.combo : "color", layer: LAYERS.includes(o.layer) ? o.layer : "",
    marks: null, enhance: !!store.get(KEYS.enhance, false),
    focus: "after", active: "after",                 // the picture on screen when one is shown; the side a tapped date goes to
    passes: [], before: null, after: null, shown: {}, img: {before: new Map(), after: new Map()}, pair: null, job: null,
    hot: {before: [], after: []}, hotKey: {before: "", after: ""}, hotSplit: null,
    tool: "", measure: null, area: null, probe: null, hover: null,
    ticks: store.get(KEYS.ticks, true) !== false, notes: [], draft: null,
    mark: Object.assign({kind: "rect", color: MARK_COLORS[0]}, store.get(KEYS.mark, {})),
    split: 0.5, zoom: 1, cx: 0.5, cy: 0.5, blinkOn: false, blinkTimer: 0, expand: false, more: 0,
    run: 0, gen: 0, session: new AbortController(), load: null, checked: false, busy: "", working: "", error: null,
    lite: lite == null ? !!(navigator.connection && navigator.connection.saveData) : !!lite,
  };
  const s = sheet();
  s.innerHTML = `<div class="ba-app" id="baApp">
    <div class="ba-top">
      <button class="ba-btn" type="button" data-act="back">${ICON.back}<span>رجوع</span></button>
      <div class="ba-head"><b class="ba-title" id="baTitle"></b><small id="baSub"></small></div>
      <button class="ba-btn" type="button" data-act="star" id="baStar" aria-label="حفظ الموقع"></button>
    </div>
    <div class="ba-main">
      <div class="ba-stagecol"><div class="ba-stagefit">
        <div class="ba-stage" id="baStage">
          <canvas id="baCanvas" aria-label="صورة القمر الصناعي للموقع"></canvas>
          <span class="ba-tag b" id="baTagB" hidden></span><span class="ba-tag a" id="baTagA" hidden></span>
          <div class="ba-handle" id="baHandle" role="slider" tabindex="0" aria-label="الفاصل بين صورة قبل وصورة بعد" aria-valuemin="0" aria-valuemax="100" aria-valuenow="50" hidden><i></i></div>
          <div class="ba-tools" id="baTools" role="toolbar" aria-label="أدوات الصورة"></div>
          <div class="ba-edges" role="group" aria-label="تحريك المنطقة">
            <button type="button" class="ba-edge n" data-act="shift" data-dx="0" data-dy="-1" aria-label="تحريك المنطقة شمالاً"></button>
            <button type="button" class="ba-edge s" data-act="shift" data-dx="0" data-dy="1" aria-label="تحريك المنطقة جنوباً"></button>
            <button type="button" class="ba-edge e" data-act="shift" data-dx="1" data-dy="0" aria-label="تحريك المنطقة شرقاً"></button>
            <button type="button" class="ba-edge w" data-act="shift" data-dx="-1" data-dy="0" aria-label="تحريك المنطقة غرباً"></button>
          </div>
          <div class="ba-scale" id="baScale" hidden></div>
          <div class="ba-q" id="baQ" hidden></div>
          <div class="ba-veil" id="baVeil" role="status"></div>
        </div>
        <div class="ba-legend" id="baLegend" hidden></div>
      </div></div>
      <div class="ba-panel">
        <div class="ba-readout" id="baMeasure" hidden></div>
        <div class="ba-seg ba-views" id="baViews" role="toolbar" aria-label="طريقة العرض"></div>
        <div class="ba-layers" id="baModes"></div>
        <div id="baDates"></div>
        <div id="baStrip"></div>
        <div class="ba-find" id="baFind"></div>
        <div class="ba-boxrow">
          <div class="ba-row"><span>عرض المربع (كم)</span><div class="ba-seg" id="baSizes"></div></div>
          <div class="ba-row" id="baPixel"></div>
          <div class="ba-row" id="baEvent"></div>
        </div>
        <div class="ba-actions" id="baActions"></div>
        <div class="ba-help" id="baHelp"></div>
      </div>
    </div></div>`;
  s.hidden = false; s.scrollTop = 0;
  bindStage();
  remember();
  if (V.show === "blink") blinkOn();
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
// a name for a tapped point or a drawn area, from OpenStreetMap (the viewer does not wait for it)
async function nameThePlace() {
  const v = V, zoom = v.half >= 20 ? 10 : v.half >= 5 ? 12 : 14;       // a wide box is named by its town or district
  try {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 8000);
    const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=${zoom}&accept-language=ar&lat=${v.lat}&lon=${v.lon}`, {signal: ctl.signal});
    clearTimeout(timer);
    const d = r.ok ? await r.json() : null, name = d && d.display_name ? String(d.display_name).split(/[،,]\s*/).slice(0, 2).join("، ") : "";
    if (V === v && name && !v.title) { v.sub = name; renderHead(); }
  } catch (e) { /* the coordinates stay as the title */ }
}
const titleOf = () => V.title || V.sub || `${V.lat.toFixed(4)}, ${V.lon.toFixed(4)}`;
const pairOn = v => !!(v.before && v.after);
const marksOn = v => v.marks == null ? v.combo !== "color" || !!v.layer : v.marks;

// everything around the picture that does not depend on the passes
function renderHead() {
  const v = V;
  el("#baTitle").innerHTML = ltr(titleOf());
  const sub = el("#baSub");
  if (sub) sub.innerHTML = `<bdi dir="ltr">${v.lat.toFixed(4)}, ${v.lon.toFixed(4)}</bdi> · مربع ${v.half * 2} كم${v.title && v.sub ? " · " + ltr(v.sub) : ""}`;
  const saved = places().some(p => samePlace(p, v));
  el("#baStar").textContent = saved ? "★ محفوظ" : "☆ حفظ";
}
function renderChrome() {
  const v = V;
  renderHead(); renderViews(); renderModes(); renderTools(); renderMeasure();
  el("#baSizes").innerHTML = SIZES.map(h => `<button type="button" data-act="size" data-half="${h}" aria-pressed="${v.half === h}">${h * 2}</button>`).join("");
  renderPixel();
  el("#baActions").innerHTML = `
    <button class="ba-btn primary" type="button" data-act="shot">${ICON.camera}<span>صورة</span></button>
    <button class="ba-btn" type="button" data-act="film">${ICON.film}<span>فيديو</span></button>
    <button class="ba-btn" type="button" data-act="report">${ICON.report}<span>تقرير</span></button>
    <button class="ba-btn" type="button" data-act="print">${ICON.print}<span>طباعة / PDF</span></button>
    <button class="ba-btn" type="button" data-act="share">${ICON.share}<span>مشاركة</span></button>
    <a class="ba-btn" href="https://www.google.com/maps?q=${v.lat},${v.lon}&t=k" target="_blank" rel="noopener">${ICON.map}<span>الموقع على الخريطة</span></a>
    <button class="ba-btn" type="button" data-act="lite" aria-pressed="${v.lite}">${v.lite ? "توفير البيانات: مفعّل" : "توفير البيانات"}</button>`;
  el("#baEvent").innerHTML = v.incident ? "" : `<label for="baDay">تاريخ الحدث (اختياري)</label>
    <input type="date" id="baDay" value="${esc(v.event && v.event.day || "")}" max="${new Date().toISOString().slice(0, 10)}">
    ${v.event ? `<button class="ba-btn" type="button" data-act="noday">مسح</button>` : ""}`;
  const day = el("#baDay");
  if (day && day.addEventListener) day.addEventListener("change", () => setDay(day.value));
  el("#baHelp").innerHTML = `<b>كيف تُقرأ؟</b> اختر التاريخ من شريط التواريخ: النقطة الخضراء صورة صافية، والصفراء جزئية، والحمراء غائمة. «فاصل» يقارن الصورتين بسحب الخط بينهما، و«وميض» يبدّل بينهما، و«جنباً إلى جنب» يعرضهما معاً. كبّر بإصبعين أو بعجلة الفأرة.<br>
    <b>التركيبات:</b> «تحت الحمراء القصيرة» (<bdi dir="ltr">B12 · B8A · B04</bdi>) تُظهر الأرض المحترقة بلون بني داكن واللهب بلون برتقالي لامع. «نبات» (<bdi dir="ltr">B08 · B04 · B03</bdi>) يلوّن النبات بالأحمر. «حرائق» (<bdi dir="ltr">B12 · B11 · B04</bdi>) تُظهر اللهب من خلال الدخان. <bdi dir="ltr">NBR</bdi> و<bdi dir="ltr">NDVI</bdi> مؤشران يُرسمان بسلّم ألوان ثابت.<br>
    <b>التحليل:</b> «التغيّر» يلوّن ما صار أغمق بالأحمر وما صار أفتح بالأزرق. «أثر احتراق محتمل» فرق مؤشر الاحتراق بين التاريخين (<bdi dir="ltr">dNBR</bdi>) بأربع درجات: منخفض من 0.10، متوسط من 0.27، مرتفع من 0.44، شديد من 0.66. الغيم وظله والماء لا تدخل في الحساب. «تحسين العرض» يزيد التباين والحدّة فقط ولا يضيف تفاصيل.<br>
    الصور من القمر الأوروبي <bdi dir="ltr">Sentinel-2</bdi> (برنامج كوبرنيكوس)، كل نقطة 10 أمتار. في الشريط تواريخ إضافية من القمر الأمريكي <bdi dir="ltr">Landsat 8/9</bdi> (30 م، تصل متأخرة أسبوعين إلى ثلاثة؛ نطاقاته <bdi dir="ltr">B2–B7</bdi> تقابل نطاقات <bdi dir="ltr">Sentinel-2</bdi>). الظلال والمدّ والزراعة والبناء تتغيّر طبيعياً، والصور لا تحدد السبب.
    <span id="baData"></span>`;
  renderData();
}
function renderPixel() {
  const v = V, box = el("#baPixel");
  if (!box) return;
  const m = pixelOf(v.half, v.lite);
  box.innerHTML = `<span>المربع ${v.half * 2} × ${v.half * 2} كم · كل نقطة في الصورة ≈ <b>${metresText(m)}</b>${m > 10.5 ? " (مربع واسع: يُقرأ من نسخة مصغّرة للصورة)" : ""}</span>`;
}
function renderViews() {
  const box = el("#baViews");
  if (box) box.innerHTML = SHOWS.map(k => `<button type="button" data-act="look" data-look="${k}" aria-pressed="${V.show === k}">${ICON[k]}<span>${SHOW_AR[k]}</span></button>`).join("");
}
// the band combinations, the layers on top, and a line saying which bands are on screen
function renderModes() {
  const v = V, box = el("#baModes");
  if (!box) return;
  const passes = [v.before, v.after], pair = pairOn(v);
  const combos = COMBOS.map(k => {
    const why = comboNote(k, passes);
    return `<button class="ba-pill" type="button" data-act="mode" data-mode="${k}" aria-pressed="${v.combo === k}"${why ? ` disabled title="${esc(why)}"` : ""}><i class="ba-sw ba-sw-${k}"></i>${COMBO_AR[k].name}</button>`;
  }).join("");
  const layers = LAYERS.map(k => `<button class="ba-pill" type="button" data-act="mode" data-mode="${k}" aria-pressed="${v.layer === k}"${pair ? "" : ` disabled title="تحتاج صورتين: قبل وبعد"`}>${LAYER_AR[k]}</button>`).join("");
  box.innerHTML = `<div class="ba-pills" role="toolbar" aria-label="تركيبة النطاقات">${combos}</div>
    <div class="ba-pills" role="toolbar" aria-label="طبقات فوق الصورة">${layers}
      <button class="ba-pill" type="button" data-act="marks" aria-pressed="${marksOn(v)}">${ICON.flame}النقاط الحارة</button>
      <button class="ba-pill" type="button" data-act="enhance" aria-pressed="${v.enhance}">تحسين العرض</button></div>
    <p class="ba-modenote" id="baModeNote"></p>`;
  renderModeNote();
}
// which bands are on screen, by their names on the satellite (it changes when band 8 arrives), and
// which views a satellite on screen cannot show
function renderModeNote() {
  const v = V, box = el("#baModeNote");
  if (!box) return;
  const passes = [v.before, v.after], scene = v.shown[v.focus] || v.shown.after || v.shown.before;
  const closed = COMBOS.map(k => comboNote(k, passes) ? `${COMBO_AR[k].name}: ${comboNote(k, passes)}` : "").filter(Boolean);
  box.innerHTML = `<bdi dir="ltr">${esc(namesOn(scene).join(" · "))}</bdi> · ${esc(COMBO_AR[v.combo].note)}${closed.length ? `<br>${esc(closed.join(" · "))}` : ""}`;
}
function renderTools() {
  const v = V, box = el("#baTools");
  if (!box) return;
  const tool = (name, icon, label) => `<button class="ba-tool" type="button" data-act="tool" data-tool="${name}" aria-pressed="${v.tool === name}" aria-label="${label}" title="${label}">${icon}</button>`;
  box.innerHTML = tool("probe", ICON.probe, "فحص نقطة") + tool("measure", ICON.ruler, "قياس مسافة") + tool("area", ICON.area, "قياس مساحة")
    + tool("mark", ICON.mark, "تعليم: مستطيل أو سهم")
    + `<button class="ba-tool" type="button" data-act="hires" aria-label="تفاصيل عالية الدقة" title="تفاصيل عالية الدقة (صورة الخريطة نحو نصف متر)">${ICON.hires}</button>`
    + `<button class="ba-tool" type="button" data-act="ticks" aria-pressed="${v.ticks}" aria-label="علامة الموقع" title="${v.ticks ? "إخفاء علامة الموقع" : "إظهار علامة الموقع"}">${ICON.ticks}</button>`
    + `<button class="ba-tool ba-zoom" type="button" data-act="zoom" data-dir="1" aria-label="تكبير" title="تكبير">${ICON.plus}</button>
    <button class="ba-tool ba-zoom" type="button" data-act="zoom" data-dir="-1" aria-label="تصغير" title="تصغير">${ICON.minus}</button>`
    + (v.zoom > 1.01 ? `<button class="ba-tool" type="button" data-act="fit" aria-label="الصورة كاملة" title="الصورة كاملة">${ICON.fit}</button>` : "");
}
// what the tool in hand reads off the picture: a distance, an area, or the numbers of a point
function renderMeasure() {
  const v = V, box = el("#baMeasure");
  if (!box) return;
  let html = "";
  const copyAt = (x, y) => { const [lat, lon] = I.gridPoint(v.grid, x, y), at = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
    return `<button class="ba-btn" type="button" data-act="copy" data-text="${at}" dir="ltr">${at}</button>`; };
  if (v.tool === "measure") {
    const m = v.measure, pts = m ? m.pts : [];
    html = `<b class="ba-ro-k">قياس مسافة</b>` + (!pts.length ? "<span>اضغط نقطتين على الصورة</span>"
      : (pts.length === 2 ? `<b>${distanceText(Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y) * v.grid.res)}</b>` : "<span>اضغط النقطة الثانية</span>")
        + " " + copyAt(pts[pts.length - 1].x, pts[pts.length - 1].y));
  } else if (v.tool === "area") {
    const a = v.area, pts = a ? a.pts : [];
    html = `<b class="ba-ro-k">قياس مساحة</b>` + (pts.length < 3 ? `<span>اضغط زوايا المساحة على الصورة (${pts.length} من 3 على الأقل)</span>`
      : `<b>${areaText(polygonArea(pts, v.grid.res) / 1e4)}</b><span>المحيط ${distanceText(pathLength(pts, v.grid.res, true))}</span>`)
      + (pts.length ? `<button class="ba-btn" type="button" data-act="undo">تراجع</button><button class="ba-btn" type="button" data-act="wipe">مسح</button>` : "");
  } else if (v.tool === "probe") html = probeHTML(copyAt);
  else if (v.tool === "mark") {
    const k = v.mark.kind;
    html = `<b class="ba-ro-k">تعليم</b>
      <span class="ba-seg"><button type="button" data-act="mshape" data-kind="rect" aria-pressed="${k === "rect"}">مستطيل</button><button type="button" data-act="mshape" data-kind="arrow" aria-pressed="${k === "arrow"}">سهم</button></span>
      <span class="ba-colors">${MARK_COLORS.map(c => `<button type="button" class="ba-color" data-act="mcolor" data-color="${c}" aria-pressed="${v.mark.color === c}" aria-label="اللون" style="--c:${c}"></button>`).join("")}</span>
      <span>${k === "rect" ? "اسحب على الصورة لرسم مستطيل" : "اسحب من ذيل السهم إلى رأسه"}</span>`
      + (v.notes.length ? `<button class="ba-btn" type="button" data-act="mundo">تراجع</button><button class="ba-btn" type="button" data-act="mclear">مسح الكل</button>` : "");
  }
  box.innerHTML = html;
  box.hidden = !html;
}
// the numbers of the inspected point: every band held, on each date, the two indices and the class
function probeHTML(copyAt) {
  const v = V, p = v.probe, head = `<b class="ba-ro-k">فحص نقطة</b>`;
  const sides = ["before", "after"].filter(k => v.shown[k]);
  if (!p || !sides.length) return head + "<span>اضغط على أي نقطة في الصورة</span>";
  const got = sides.map(k => I.inspect(v.shown[k], p.x, p.y));
  if (!got[0]) return head + "<span>النقطة خارج الصورة</span>";
  const f = x => x == null ? "—" : num(x, 3), names = ["blue", "green", "red", "nir", "nir08", "swir16", "swir22"].filter(b => got.some(g => g && b in g.bands));
  const sensors = sides.map(k => I.sensorOf(v.shown[k].pass)), one = sensors.every(s => s === sensors[0]);
  const row = (label, cells) => `<tr><th>${label}</th>${cells.map(c => `<td>${c}</td>`).join("")}</tr>`;
  const burn = v.pair && v.pair.burn && sides.length === 2 ? v.pair.burn.dnbr[got[0].y * v.grid.size + got[0].x] : null;
  return head + copyAt(got[0].x + 0.5, got[0].y + 0.5) + `<table class="ba-probe">
    ${sides.length === 2 ? `<thead>${row("الانعكاس", ["قبل", "بعد"])}</thead>` : `<thead>${row("النطاق", ["الانعكاس"])}</thead>`}
    <tbody>${names.map(b => row(`${one ? `<bdi dir="ltr">${sensors[0].bands[b][0]}</bdi> ` : ""}${BAND_AR[b]}`, got.map(g => f(g.bands[b])))).join("")}
    ${row(`<bdi dir="ltr">NDVI</bdi> النبات`, got.map(g => f(g.ndvi)))}${row(`<bdi dir="ltr">NBR</bdi> الاحتراق`, got.map(g => f(g.nbr)))}
    ${burn != null ? `<tr><th><bdi dir="ltr">dNBR</bdi> فرق الاحتراق</th><td colspan="2">${burn === burn ? num(burn, 3) + (v.pair.burn.cls[got[0].y * v.grid.size + got[0].x] ? " · " + BURN_AR[v.pair.burn.cls[got[0].y * v.grid.size + got[0].x]] : "") : "غير محسوب هنا"}</td></tr>` : ""}
    ${row("صنف السطح", got.map((g, i) => esc(classText(g.scl, v.shown[sides[i]].pass) || "—")))}</tbody></table>
    <small class="ba-ro-note">قيمة نقطة واحدة من الصورة (${metresText(Math.max(...sides.map(k => v.shown[k].res || v.grid.res), v.grid.res))}). الانعكاس من 0 إلى 1.</small>`;
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
// A new place or box: list the passes, check the sky of each over the box, settle the pictures.
async function start() {
  const v = V, run = ++v.run;
  v.session.abort(); if (v.load) v.load.abort();
  v.session = new AbortController();
  v.grid = I.makeGrid(v.lat, v.lon, v.half, pointsOf(v.half, v.lite));
  Object.assign(v, {passes: [], before: null, after: null, shown: {}, img: {before: new Map(), after: new Map()}, pair: null, job: null,
    hot: {before: [], after: []}, hotKey: {before: "", after: ""}, hotSplit: null,
    checked: false, error: null, busy: "", working: "", more: 0, zoom: 1, cx: 0.5, cy: 0.5, measure: null, area: null, probe: null, hover: null});
  renderMeasure(); renderTools(); renderPixel();
  veil("جارٍ البحث عن مرور القمر فوق الموقع…");
  renderDates(); renderStrip(); renderModes(); renderFind(); renderLegend(); paint();
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
    if (v.before || v.after) { v.focus = v.active = v.after ? "after" : "before"; loadPair(); } else v.auto = true;
  }
  tick(false);
  await checkSky(passes, run);
  if (V !== v || run !== v.run) return;
  I.refineSky(passes);
  v.checked = true;
  tick(true);
}
function checkSky(passes, run) {
  const v = V, signal = v.session.signal;
  return Promise.all(checkOrder(passes, v.event).map(p => engine.assessPass(p, v.grid, signal)
    .catch(e => { if (e.code !== "aborted") p.failed = true; })
    .then(() => { if (V === v && run === v.run) tick(false); })));
}
// «أقدم» / «أحدث» at the ends of the strip: four more months are listed and added to it; the
// pictures on screen stay as they are.
async function extend(dir) {
  const v = V, now = Date.now(), first = v.windows[0][0], last = v.windows[v.windows.length - 1][1];
  const add = dir < 0 ? [first - LOOK_BACK, first] : [last, Math.min(now, last + LOOK_BACK)];
  v.windows = joinWindows(v.windows.concat([add]), now);
  if (v.error || !v.passes.length) {                 // nothing on screen yet: look again over the longer stretch
    v.want = {before: v.before && v.before.date, after: v.after && v.after.date};
    if (!v.want.before && !v.want.after) v.auto = true;
    return start();
  }
  if (v.more) return;
  const run = v.run;
  v.more = dir; renderStrip();
  let got;
  try { got = await engine.listPasses(v.grid, add[0], add[1], v.session.signal); }
  catch (e) {
    if (V === v && run === v.run && e.code !== "aborted") { v.more = 0; renderStrip(); toast(failText(e)); }
    return;
  }
  if (V !== v || run !== v.run) return;
  const have = new Set(v.passes.map(p => p.key)), fresh = got.filter(p => !have.has(p.key));
  v.passes = v.passes.concat(fresh).sort((a, b) => b.time - a.time);
  v.more = 0;
  if (!fresh.length) toast(dir < 0 ? "لا مرور أقدم في الأشهر الأربعة السابقة" : "لا مرور أحدث");
  renderStrip(); renderDates();
  await checkSky(fresh, run);
  if (V !== v || run !== v.run) return;
  I.refineSky(v.passes);
  tick(true);
}
// after every checked pass: refresh the dates and, while the pictures are still automatic, settle them
function tick(final) {
  const v = V;
  renderStrip(); renderDates(); renderData();
  if (!v.auto) return;
  const c = choose(v.passes, v.event), one = v.show === "single";
  if (!final && !(one ? c.after.done && (c.after.pass || c.before.done) : c.before.done && c.after.done)) return;
  let b = c.before.pass, a = c.after.pass;
  if (one && a) b = null;                            // one picture asked for: the clear one after the event, or the newest
  if (b === v.before && a === v.after && (v.shown.before || v.shown.after || v.load)) return;
  if (!b && !a) { if (final) { v.before = v.after = null; fail({code: "cloud"}); } return; }
  v.before = b; v.after = a;
  v.focus = v.active = a ? "after" : "before";
  renderDates(); renderStrip(); renderModes();
  loadPair();
}
function setSide(side, pass) {
  const v = V, other = side === "before" ? "after" : "before", o = v[other];
  if (pass && o && (side === "before" ? pass.time >= o.time : pass.time <= o.time)) {
    if (v.show !== "single") { toast("صورة «قبل» يجب أن تكون أقدم من «بعد»"); return; }
    v[other] = null;                                 // one picture on screen: the other date no longer fits and is let go
  }
  v[side] = pass; v.auto = false;
  if (pass) v.focus = side; else if (v.focus === side) v.focus = other;
  if (!v[v.active]) v.active = v.focus;
  syncRoute();
  renderDates(); renderStrip(); renderModes();
  loadPair();
}
function step(side, dir) {
  const v = V, cur = v[side];
  const list = v.passes.filter(p => p === cur || gradeOf(p) < 2 || gradeOf(p) === 3).sort((a, b) => a.time - b.time);
  const other = side === "before" ? v.after : v.before;
  const ok = p => !other || v.show === "single" || (side === "before" ? p.time < other.time : p.time > other.time);
  let i = cur ? list.indexOf(cur) + dir : dir > 0 ? 0 : list.length - 1;
  while (i >= 0 && i < list.length && !ok(list[i])) i += dir;
  if (i < 0 || i >= list.length) { toast(dir < 0 ? "لا مرور أقدم ضمن القائمة. اطلب «أقدم» من آخر شريط التواريخ." : "لا مرور أحدث"); return; }
  setSide(side, list[i]);
}
// the quick choices above the strip
function quick(kind) {
  const v = V;
  if (kind === "clear") {
    const p = latestClear(v.passes);
    if (!p) { toast(v.checked ? "لا صورة صافية ضمن القائمة. اطلب «أقدم» من آخر الشريط." : "ما زالت الصور تُفحص. بعد لحظات."); return; }
    const side = pairOn(v) || v.show !== "single" ? "after" : v.focus;
    if (v[side] === p) { toast("أحدث صورة صافية معروضة"); return; }
    if (side === "after" && v.before && v.before.time >= p.time) v.before = null;
    v.active = side;
    return setSide(side, p);
  }
  // the two pictures again as the app would choose them: around the event, or the last two clear ones
  const c = choose(v.passes, v.event);
  if (!c.before.pass && !c.after.pass) { toast(v.checked ? "لا صورة صافية ضمن القائمة." : "ما زالت الصور تُفحص. بعد لحظات."); return; }
  v.before = c.before.pass; v.after = c.after.pass; v.auto = true;
  v.focus = v.active = v.after ? "after" : "before";
  if (v.show === "single" && pairOn(v)) setShow("split", true);
  syncRoute(); renderDates(); renderStrip(); renderModes();
  loadPair();
}
function syncRoute() {
  const v = V, look = {show: v.show, combo: v.combo, layer: v.layer};
  const hash = v.incident ? routeOf({id: v.incident.id, ...look})
    : routeOf({lat: v.lat, lon: v.lon, half: v.half, before: v.before && v.before.date, after: v.after && v.after.date, name: v.title, ...look});
  try { history.replaceState(null, "", location.pathname + "#" + encodeURIComponent(hash)); } catch (e) { /* the link stays as it was */ }
}
// the two dates on screen (or the one), each with its own «أقدم» and «أحدث»; a tap makes it the
// side that a date tapped in the strip goes to
function renderDates() {
  const v = V, box = el("#baDates");
  if (!box) return;
  const pair = pairOn(v), sides = v.show === "single" && !pair ? [v.before && !v.after ? "before" : "after"] : ["before", "after"];
  const card = side => {
    const p = v[side], label = sides.length === 1 ? "الصورة" : side === "before" ? "قبل" : "بعد";
    return `<div class="ba-slot ${side}${v.active === side || sides.length === 1 ? " on" : ""}" data-act="side" data-side="${side}" role="button" tabindex="0" aria-pressed="${v.active === side || sides.length === 1}">
      <span class="k">${label}</span>
      <span class="d">${p ? `<b>${dateAr(p.time)}</b><small>${timeAr(p.time)} · ${esc(skyText(p))}${sensorText(p)}</small>`
        : `<b>—</b><small>${v.passes.length ? (side === "after" && v.event && v.checked ? "لم يمر القمر بصورة صافية بعد الحدث" : "لم تُحدَّد: اختر من الشريط") : "…"}</small>`}</span>
      <span class="st"><button class="ba-mini" type="button" data-act="step" data-side="${side}" data-dir="1" aria-label="مرور أحدث" title="أحدث">${ICON.newer}</button>
        <button class="ba-mini" type="button" data-act="step" data-side="${side}" data-dir="-1" aria-label="مرور أقدم" title="أقدم">${ICON.older}</button></span>
    </div>`;
  };
  box.innerHTML = `<div class="ba-slots">${sides.map(card).join("")}</div>
    <div class="ba-pills ba-quick" role="toolbar" aria-label="اختيار سريع">
      <button class="ba-pill" type="button" data-act="quick" data-q="clear">أحدث صورة صافية</button>
      <button class="ba-pill" type="button" data-act="quick" data-q="pair">${v.event ? "قبل الحدث وبعده" : "آخر صورتين صافيتين"}</button>
    </div>`;
}
// The date strip: every pass found, newest first, as a chip with its day, its satellite and a dot
// for the sky. Cloudy ones are dimmed but can still be chosen. «أقدم» at its end lists more.
function renderStrip() {
  const v = V, box = el("#baStrip");
  if (!box) return;
  const chips = stripOf(v.passes, {before: v.before, after: v.after});
  const clear = chips.filter(c => c.grade === 0).length, checking = chips.some(c => c.grade === 3);
  const old = el("#baDays"), at = old && !old.hidden ? old.scrollLeft : 0;
  const newer = v.passes.length && v.windows[v.windows.length - 1][1] < Date.now() - DAY;
  const chip = c => `${c.year ? `<span class="ba-year">${c.year}</span>` : ""}<button class="ba-day g${c.grade}${c.dim ? " dim" : ""}${c.mark ? " " + c.mark : ""}" type="button" role="option"
    data-act="take" data-key="${esc(c.key)}" aria-selected="${!!c.mark}" title="${esc(c.title)} · ${c.metres} م"><i class="dot"></i><b>${c.day}</b><span>${c.month}</span><small dir="ltr">${c.sat}</small>${c.mark ? `<em>${c.mark === "before" ? "قبل" : "بعد"}</em>` : ""}</button>`;
  box.innerHTML = `<div class="ba-striphead"><b>تواريخ المرور</b><span id="baCount">${chips.length ? `${chips.length} · ${clear} صافية${checking ? " · تُفحص…" : ""}` : ""}</span>
      <span class="ba-keys"><i class="g0"></i>صافية<i class="g1"></i>جزئية<i class="g2"></i>غائمة</span>
      <button class="ba-link" type="button" data-act="expand" aria-pressed="${!!v.expand}">${v.expand ? "شريط" : "عرض الكل"}</button></div>
    <div class="ba-days${v.expand ? " open" : ""}" id="baDays" role="listbox" aria-label="تواريخ مرور القمر، الأحدث أولاً">
      ${newer ? `<button class="ba-more" type="button" data-act="newer">${v.more > 0 ? "جارٍ…" : "أحدث"}</button>` : ""}
      ${chips.map(chip).join("")}
      ${v.passes.length || v.error ? `<button class="ba-more" type="button" data-act="older">${v.more < 0 ? "جارٍ…" : "أقدم<small>4 أشهر</small>"}</button>` : ""}
    </div>`;
  const now = el("#baDays");
  if (now && at) now.scrollLeft = at;
}

/* ---------- the pictures ---------- */
const PAIR_BANDS = ["blue", "green", "red", "nir08", "swir16", "swir22"];      // what the comparison of two dates needs
const HOT_BANDS = ["swir22", "swir16", "nir08"];
// every band array gets a number, so a picture or a result knows which arrays it was made from
const marks = new WeakMap();
let nextMark = 0;
const markOf = a => { if (!a) return 0; let m = marks.get(a); if (!m) marks.set(a, m = ++nextMark); return m; };
const marksOf = (scene, names) => names.map(b => markOf(scene[b])).join(".") + "/" + markOf(scene.scl);
function dropSide(side) {
  const v = V;
  delete v.shown[side];
  v.img[side].clear();
  v.hot[side] = []; v.hotKey[side] = "";
  v.pair = null; v.job = null; v.working = "";
}
// Load what the screen needs for the chosen dates, in steps (see stagesFor): each band is asked for
// on its own, so the picture appears as soon as its three bands are there and the progress is true.
// Bands already in memory come back at once; a load that was superseded is cancelled.
async function loadPair() {
  const v = V, gen = ++v.gen;
  // the load before this one is cancelled only after this one has asked for its first bands: what
  // both want keeps coming in (see the engine's store), the rest stops
  let old = v.load;
  const ctl = v.load = new AbortController(), leave = () => { if (old) { old.abort(); old = null; } };
  v.error = null;
  for (const k of ["before", "after"]) if (!v[k] || (v.shown[k] && v.shown[k].pass !== v[k])) dropSide(k);
  refresh();
  const sides = ["before", "after"].filter(k => v[k]);
  if (!sides.length) { leave(); v.busy = ""; v.load = null; showBusy(); return; }
  const stages = stagesFor(v.grid.res, v.lite, v.combo);
  try {
    for (const st of stages) {
      const quality = k => v[k].sensor === "landsat" ? "full" : st.quality;          // Landsat is 30 m either way: read once
      const res = k => v[k].sensor === "landsat" ? Math.max(30, v.grid.res) : quality(k) === "fast" ? Math.max(20, v.grid.res) : Math.max(10, v.grid.res);
      const todo = [];
      for (const k of sides) for (const name of st.names) {
        const sc = v.shown[k];
        if (!(sc && sc.pass === v[k] && sc.resOf[name] <= res(k))) todo.push([k, name]);
      }
      if (!todo.length) continue;
      let done = 0;
      const say = () => {
        v.busy = (st.what === "sharp" ? "جارٍ تحسين الدقة إلى 10 أمتار…" : "جارٍ تحميل الصور من الأرشيف…") + (done ? ` ${done} من ${todo.length}` : "");
        showBusy();
      };
      say();
      const jobs = todo.map(async ([k, name]) => {
        const pass = v[k], part = await engine.loadBands(pass, v.grid, [name], quality(k), ctl.signal);
        if (V !== v || gen !== v.gen) return;
        take(k, pass, part, [name]);
        done++; say();
        refresh();
      });
      leave();
      await Promise.all(jobs);
      if (V !== v || gen !== v.gen) return;
    }
    leave();
    v.busy = ""; v.load = null;
    showBusy(); renderData();
  } catch (e) {
    leave();
    if (V !== v || gen !== v.gen || e.code === "aborted") return;
    v.busy = ""; v.load = null;
    fail(e, "load");
  }
}
// put freshly read bands into the scene of that side (a band is never replaced by a coarser copy)
function take(side, pass, part, names) {
  const v = V, old = v.shown[side];
  const sc = old && old.pass === pass ? old : {pass, grid: v.grid, from: {}, resOf: {}, res: Infinity};
  for (const name of names.concat("scl")) {
    if (!part[name] || sc.resOf[name] <= part.res) continue;
    sc[name] = part[name]; sc.resOf[name] = part.res;
    if (part.from[name]) sc.from[name] = part.from[name];
  }
  sc.res = Math.min(sc.res, part.res);
  v.shown[side] = sc;
}
// the two scenes on screen that hold the bands of a view (for one brightness range over both dates)
function scenesFor(names) {
  return ["before", "after"].map(k => V.shown[k]).filter(sc => sc && I.hasBands(sc, names));
}
// the bands of the view on screen: band 8 only when every scene on screen holds it
function namesNow(combo) {
  const all = ["before", "after"].map(k => V.shown[k]).filter(Boolean);
  return I.comboBands(combo, all.length && all.every(sc => sc.nir) ? {nir: true} : {});
}
function toCanvas(rgba, n) {
  const c = document.createElement("canvas");
  c.width = c.height = n;
  c.getContext("2d").putImageData(new ImageData(rgba, n, n), 0, 0);
  return c;
}
// The picture of one side in one view, made once and kept until its bands (or the other date's:
// they share a brightness range) change. Null while the bands of this view are not there yet.
function picture(side, combo) {
  const v = V, sc = v.shown[side], names = namesNow(combo);
  if (!sc || !I.hasBands(sc, names)) return null;
  const index = !!I.COMBOS[combo].index, scenes = index ? [sc] : scenesFor(names);
  const key = [v.enhance && !index ? 1 : 0, ...scenes.map(s => marksOf(s, names))].join("|") + "@" + marksOf(sc, names);
  const have = v.img[side].get(combo);
  if (have && have.key === key) { v.img[side].delete(combo); v.img[side].set(combo, have); return have.canvas; }
  const n = v.grid.size;
  let rgba;
  if (index) rgba = I.indexImage(I.indexOf(sc, combo, names), combo, sc.scl);
  else {
    rgba = I.bandsImage(sc, names, v.enhance ? I.stretchRange(scenes, names) : I.stretchOf(scenes, names));
    if (v.enhance) I.sharpen(rgba, n);
  }
  v.img[side].set(combo, {key, canvas: toCanvas(rgba, n)});
  while (v.img[side].size > 3) v.img[side].delete(v.img[side].keys().next().value);       // the three views used last are kept
  return v.img[side].get(combo).canvas;
}
const hasPicture = () => !!(V && (picture("before", V.combo) || picture("after", V.combo)));
// the layer on top (what changed, or likely burnt ground), drawn once per result
function layerCanvas(kind) {
  const v = V, p = v.pair;
  if (!p || !p[kind]) return null;
  if (!p.img[kind]) p.img[kind] = toCanvas(kind === "burn" ? I.burnImage(p.burn) : I.changeImage(p.change), v.grid.size);
  return p.img[kind];
}
// The comparison of the two dates runs in a background worker made from the engine's own text, so
// the screen stays smooth; where a worker cannot start it runs here, after the picture is drawn.
const analyst = (() => {
  let worker = null, dead = false, seq = 0;
  const waiting = new Map();
  function open() {
    if (worker || dead) return worker;
    try {
      if (typeof Worker !== "function" || typeof Blob !== "function" || !root.URL || !root.URL.createObjectURL) throw new Error("no worker here");
      const url = root.URL.createObjectURL(new Blob([`self.RasadImagery=${I.source};self.RasadImagery.serve(self);`], {type: "text/javascript"}));
      worker = new Worker(url);
      worker.onmessage = e => {
        const w = waiting.get(e.data.id);
        if (!w) return;
        waiting.delete(e.data.id);
        if (e.data.error) w.here(); else w.resolve(e.data.result);
      };
      worker.onerror = () => { dead = true; worker = null; for (const w of waiting.values()) w.here(); waiting.clear(); };
    } catch (e) { dead = true; worker = null; }
    return worker;
  }
  function run(input) {
    return new Promise((resolve, reject) => {
      const here = () => setTimeout(() => { try { resolve(I.pairJob(input)); } catch (e) { reject(e); } }, 0);
      const w = open();
      if (!w) return here();
      const id = ++seq;
      waiting.set(id, {resolve, here});
      try { w.postMessage({id, ...input}); } catch (e) { waiting.delete(id); here(); }
    });
  }
  return {run};
})();
// Something changed (a band arrived, a date was dropped, the view was switched): draw what is in
// hand at once, then bring the flames and the comparison up to date.
function refresh() {
  const v = V;
  // flames at the moment of each picture
  for (const k of ["before", "after"]) {
    const sc = v.shown[k];
    if (!sc || !I.hasBands(sc, HOT_BANDS)) { v.hot[k] = []; v.hotKey[k] = ""; continue; }
    const key = marksOf(sc, HOT_BANDS);
    if (v.hotKey[k] !== key) { v.hot[k] = I.hotSpots(sc).slice(0, 40); v.hotKey[k] = key; }
  }
  const b = v.shown.before, a = v.shown.after;
  v.hotSplit = a && b ? splitHot(v.hot.after, v.hot.before) : null;
  // the comparison: once per pair of band sets; the last result stays on screen while a sharper one is worked out
  if (a && b && I.hasBands(b, PAIR_BANDS) && I.hasBands(a, PAIR_BANDS)) {
    const key = marksOf(b, PAIR_BANDS) + "|" + marksOf(a, PAIR_BANDS);
    if (!(v.pair && v.pair.key === key) && !(v.job && v.job.key === key)) {
      const job = v.job = {key}, pick = s => { const o = {scl: s.scl}; for (const name of PAIR_BANDS) o[name] = s[name]; return o; };
      const res = Math.max(a.res, b.res);
      v.working = "جارٍ حساب التغيّر بين الصورتين…";
      analyst.run({size: v.grid.size, res: v.grid.res, before: pick(b), after: pick(a)}).then(r => {
        if (V !== v || v.job !== job) return;        // the dates or the bands changed meanwhile
        r.change.grid = r.burn.grid = v.grid;
        v.pair = {key, change: r.change, burn: r.burn, res, img: {}};
        v.job = null; v.working = "";
        renderFind(); renderLegend(); renderMeasure(); showBusy(); paint();
      }, () => {
        if (V !== v || v.job !== job) return;
        v.job = null; v.working = "";
        showBusy(); renderFind();
      });
    }
  } else if (!(a && b)) { v.pair = null; v.job = null; v.working = ""; }
  renderFind(); renderLegend(); renderMeasure(); renderModeNote(); showBusy(); paint();
}
// The true sharpness of what is on screen, in metres: the coarsest of the bands of this view as they
// were read (a 20 m band stays 20 m in a 10 m picture, Landsat is 30 m, a wide box is coarser still).
function sharpness(names) {
  const v = V;
  let worst = v.grid.res;
  for (const k of ["before", "after"]) {
    const sc = v.shown[k];
    if (!sc || !I.hasBands(sc, names)) continue;
    const sensor = I.sensorOf(sc.pass);
    for (const b of names) worst = Math.max(worst, sc.resOf[b] || 0, (sensor.bands[sc.from[b] || b] || [0, 0, 0])[2]);
  }
  return worst;
}
function showBusy() {
  const v = V, any = hasPicture();
  if (v.error) return;
  const text = v.busy || v.working;
  if (text) veil(text, any); else if (any) veil("");
  const q = el("#baQ");
  if (!any) { q.hidden = true; return; }
  q.hidden = false;
  q.textContent = "دقة " + metresText(sharpness(namesNow(v.combo))) + (v.lite && pixelOf(v.half, false) < 20 ? " (توفير البيانات)" : "") + (v.enhance ? " · عرض محسَّن" : "");
}
// the bands on screen under their names on the satellite of that scene: ["B12", "B8A", "B04"]
function namesOn(sc) {
  const sensor = I.sensorOf(sc && sc.pass);
  return namesNow(V.combo).map(b => (sensor.bands[sc && sc.from && sc.from[b] || b] || ["?"])[0]);
}
function veil(text, soft) {
  const b = el("#baVeil");
  if (!b) return;
  b.hidden = !text;
  b.className = "ba-veil" + (soft ? " soft" : "");
  b.innerHTML = text ? `<div class="ba-spin" aria-hidden="true"></div><p>${esc(text)}</p>` : "";
}
function fail(e, retry) {
  const v = V;
  v.error = e; v.busy = ""; v.working = "";
  const b = el("#baVeil");
  b.hidden = false; b.className = "ba-veil";
  const text = e.code === "cloud" ? "لا صورة صافية لهذا الموقع في هذه المدة. اختر تاريخاً من شريط التواريخ أو اطلب تواريخ أقدم." : failText(e);
  b.innerHTML = `<p>${esc(text)}</p><div class="ba-acts">${retry === "older" || e.code === "cloud" ? `<button class="ba-btn" type="button" data-act="older">تواريخ أقدم</button>`
    : `<button class="ba-btn primary" type="button" data-act="retry" data-what="${retry || "start"}">إعادة المحاولة</button>`}</div>`;
  renderFind(); renderStrip();
}

/* ---------- what was found, in words: the summary card ---------- */
function renderFind() {
  const v = V, box = el("#baFind");
  if (!box) return;
  const b = v.shown.before, a = v.shown.after, p = v.pair, out = [`<h3 class="ba-h">ملخص التحليل</h3>`];
  const flames = `<b>${ICON.flame}`;
  if (!b && !a) {
    out.push(`<p>${v.error ? "لا صور معروضة." : v.show === "single" ? "تُجهَّز الصورة: أحدث صورة صافية للموقع." : "تُجهَّز الصورتان: أحدث صورة صافية قبل الحدث وأول صورة صافية بعده."}</p>`);
  } else if (a && b) {
    const other = a.pass.sensor === "landsat" || b.pass.sensor === "landsat";
    if (!p) out.push(`<div class="big">${v.working || v.busy ? "يُحسب التغيّر بين الصورتين…" : "تعذّر حساب التغيّر"}</div>`);
    else {
      const c = p.change, quiet = quietChange(c), coarse = !other && p.res > v.grid.res + 1;
      out.push(`<div class="big">${quiet ? "لا تغيّر واضح بين الصورتين" : `تغيّر مرصود: نحو ${areaText(c.areaHa)}`}</div>`);
      if (!quiet) out.push(`<div class="lg"><span><i style="background:#ff3b30"></i>صار أغمق: ${areaText(c.darkHa)}</span><span><i style="background:#22d3ee"></i>صار أفتح: ${areaText(c.brightHa)}</span><span>${num(c.share * 100, c.share < 0.01 ? 2 : 1)}% من المربع</span></div>`);
      if (c.clouds || c.compared < 0.9) out.push(`<p class="warn">غيوم أو ظلالها في إحدى الصورتين: قورن ${Math.round(c.compared * 100)}% من المربع، والتقدير أقل ثقة. اختر تاريخاً أصفى إن وُجد.</p>`);
      if (other) out.push(`<p>إحدى الصورتين من القمر الأمريكي Landsat (كل نقطة 30 م): التفاصيل أقل، والتقدير أقل ثقة.</p>`);
      if (coarse) out.push(`<p>تقدير أولي بدقة ${Math.round(p.res)} م${v.lite ? "" : "، يُحدَّث عند اكتمال التحميل"}.</p>`);
      if (!quiet && v.layer !== "change") out.push(`<button class="ba-btn" type="button" data-act="mode" data-mode="change">أظهر التغيّر على الصورة</button>`);
      // likely burnt ground: the drop of the burn ratio, by class
      const u = p.burn, calm = quietBurn(u);
      out.push(`<div class="ba-burn"><b>أثر احتراق محتمل (<bdi dir="ltr">dNBR</bdi>):</b> ${calm ? "لا أثر واضح" : `نحو ${areaText(u.areaHa)}`}${calm ? "" : `<div class="lg">${[1, 2, 3, 4].filter(k => u.classHa[k] > 0)
        .map(k => `<span><i style="background:${I.BURN_COLORS[k - 1]}"></i>${BURN_AR[k]}: ${areaText(u.classHa[k])}</span>`).join("")}</div>
        <small>قد يكون تغيّراً في الغطاء النباتي (حصاد، حرث، جفاف): مؤشر لا دليل. حُسب في ${Math.round(u.compared * 100)}% من المربع.</small>`}</div>`);
      if (!calm && v.layer !== "burn") out.push(`<button class="ba-btn" type="button" data-act="mode" data-mode="burn">أظهر أثر الاحتراق على الصورة</button>`);
    }
  } else if (b) {
    out.push(`<div class="big">صورة «قبل» فقط</div><p>${v.event ? "لم يمر القمر فوق الموقع بصورة صافية بعد الحدث حتى الآن. يمر كل يومين إلى خمسة أيام: أعد فتحها لاحقاً." : "اختر صورة «بعد» من التواريخ."}</p>`);
  } else if (v.show === "single") {
    out.push(`<div class="big">صورة واحدة</div><p>للمقارنة وحساب التغيّر اختر «فاصل» أو «جنباً إلى جنب»: تُضاف أقرب صورة صافية قبلها.</p>`);
  } else {
    out.push(`<div class="big">صورة «بعد» فقط</div><p>لا صورة صافية قبلها ضمن المدة. اطلب تواريخ أقدم من آخر شريط التواريخ.</p>`);
  }
  const ha = v.hot.after, hb = v.hot.before;
  if (a && ha.length) {
    const s = b ? v.hotSplit : null;
    const what = !s ? `${ha.length === 1 ? "موقع واحد" : ha.length + " مواقع"}`
      : [s.fresh ? `${s.fresh} لم ${s.fresh === 1 ? "يكن ظاهراً" : "تكن ظاهرة"} قبل` : "", s.old ? `${s.old} ${s.old === 1 ? "كان ظاهراً" : "كانت ظاهرة"} قبل أيضاً (شعلة غاز دائمة على الأرجح)` : ""].filter(Boolean).join("، ");
    out.push(`<p>${flames} لهب أو حرارة شديدة لحظة التقاط ${b ? "صورة «بعد»" : "الصورة"}:</b> ${what}.</p>`);
    const list = (s ? ha.filter(h => !h.old) : ha).slice(0, 3);
    if (list.length) out.push(`<div class="ba-acts">${list.map(h => { const at = `${h.lat.toFixed(5)}, ${h.lon.toFixed(5)}`;
      return `<button class="ba-btn" type="button" data-act="copy" data-text="${at}" dir="ltr">${at}</button>`; }).join("")}</div>`);
    if (v.combo === "color") out.push(`<button class="ba-btn" type="button" data-act="mode" data-mode="swir">أظهرها بالأشعة تحت الحمراء</button>`);
  } else if (b && !a && hb.length) {
    out.push(`<p>${flames} لهب أو حرارة شديدة في صورة «قبل»:</b> ${hb.length === 1 ? "موقع واحد" : hb.length + " مواقع"} (شعلات الغاز تظهر هكذا دائماً).</p>`);
  }
  if (v.incident && (v.incident.place === "sea" || v.incident.place === "coast") && (a || b))
    out.push(`<p>في البحر تتحرك السفينة: أحدث صورة بعد الحدث (دخان، بقعة، السفينة نفسها) أنفع من المقارنة.</p>`);
  if (v.incident && v.incident.best !== "polar" && (a || b))
    out.push(`<p>موقع هذا الحدث تقريبي (نحو 3 كم): ابحث عن الأثر في كامل المربع، أو وسّعه.</p>`);
  if (a || b) {
    // the dates, the satellites and how clear each is; then the standing cautions
    const line = (label, sc) => `<dt>${label}</dt><dd>${dateAr(sc.pass.time)} · <bdi dir="ltr">${esc(satName(sc.pass))}</bdi> · صفاء ${clearPct(sc.pass) ?? "—"}%</dd>`;
    const scene = a || b, landsat = [a, b].some(s => s && s.pass.sensor === "landsat");
    out.push(`<dl class="ba-facts">${b ? line(a ? "قبل" : "الصورة", b) : ""}${a ? line(b ? "بعد" : "الصورة", a) : ""}
      <dt>التركيبة</dt><dd>${COMBO_AR[v.combo].name} (<bdi dir="ltr">${esc(namesOn(scene).join(" · "))}</bdi>)</dd></dl>`);
    out.push(`<ul class="ba-caveats">${caveatsOf({pair: !!(a && b), landsat, pixel: Math.max(v.grid.res, landsat ? 30 : 10), burn: !!(p && a && b), enhance: v.enhance}).map(t => `<li>${esc(t)}</li>`).join("")}</ul>`);
  }
  box.innerHTML = out.join("");
}
// what the colours on the picture mean: under the picture, for the view and the layer on screen
function renderLegend() {
  const v = V, box = el("#baLegend");
  if (!box) return;
  const out = [], c = COMBO_AR[v.combo], sw = (color, text) => `<span><i style="background:${color}"></i>${text}</span>`;
  if (hasPicture()) {
    if (c.ends) {
      const r = I.RAMPS[v.combo], pct = x => ((x - r.min) / (r.max - r.min) * 100).toFixed(1);
      const ticks = (v.combo === "ndvi" ? [0, 0.3, 0.6, 0.9] : [-0.5, 0, 0.5]).map(t => `<u style="left:${pct(t)}%">${t}</u>`).join("");
      out.push(`<div class="ba-ramp"><span>${c.ends[0]}</span><div class="bar" dir="ltr"><i style="background:linear-gradient(to right,${r.stops.map(([x, col]) => `${col} ${pct(x)}%`).join(",")})"></i>${ticks}</div><span>${c.ends[1]}</span></div>`,
        sw(`rgb(${I.MASK_COLORS.cloud})`, "غيم"));
      if (v.combo === "nbr") out.push(sw(`rgb(${I.MASK_COLORS.water})`, "ماء"));
    } else for (const [color, text] of c.legend) out.push(sw(color, text));
    if (v.layer === "change" && v.pair) out.push(sw("#ff3b30", "صار أغمق"), sw("#22d3ee", "صار أفتح"));
    if (v.layer === "burn" && v.pair) {
      out.push(`<span class="ba-lgk">أثر احتراق محتمل:</span>`, ...[1, 2, 3, 4].map(k => sw(I.BURN_COLORS[k - 1], BURN_AR[k])));
      if (v.pair.burn.clouds) out.push(`<span><i class="hatch"></i>غيم: لم يُقارن</span>`);
    }
    if (marksOn(v) && (v.hot.before.length || v.hot.after.length)) out.push(`<span><i class="ring"></i>لهب أو حرارة شديدة</span>`);
  }
  box.innerHTML = out.join("");
  box.hidden = !out.length;
}

/* ---------- drawing ---------- */
// the stage in CSS pixels and the canvas in device pixels
function sizeCanvas() {
  const cv = el("#baCanvas"), st = el("#baStage");
  if (!cv || !st) return null;
  const w = st.clientWidth || 0, h = st.clientHeight || w, dpr = Math.min(3, root.devicePixelRatio || 1);
  const W = Math.max(64, Math.round(w * dpr)), H = Math.max(64, Math.round(h * dpr));
  if (cv.width !== W) cv.width = W;
  if (cv.height !== H) cv.height = H;
  return {w, h, W, H, dpr: W / Math.max(1, w)};
}
// which pictures are on screen now, and where the divider is (0 = all «قبل», 1 = all «بعد»)
function viewNow() {
  const v = V, b = picture("before", v.combo), a = picture("after", v.combo), both = !!(a && b);
  let f = both ? v.split : a ? 1 : 0;
  if (both && v.show === "blink") f = v.blinkOn ? 1 : 0;
  if (both && v.show === "single") f = v.focus === "after" ? 1 : 0;
  return {a, b, both, f, two: both && v.show === "side"};
}
function paint() {
  const v = V, cv = el("#baCanvas"), st = el("#baStage");
  if (!v || !cv) return;
  const now = viewNow(), cls = "ba-stage" + (now.two ? " side" : "");
  if (st.className !== cls) st.className = cls;                    // two panes: the stage takes another shape
  const size = sizeCanvas(), {W, H, dpr} = size, g = cv.getContext("2d");
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.fillStyle = "#05080d"; g.fillRect(0, 0, W, H);
  const {a, b, both, f} = now;
  const tagB = el("#baTagB"), tagA = el("#baTagA"), handle = el("#baHandle"), scale = el("#baScale");
  if (!a && !b) { tagA.hidden = tagB.hidden = handle.hidden = scale.hidden = true; return; }
  const n = v.grid.size, panes = panesOf(v.show, size.w, size.h, both);
  v.panes = panes;
  clampView();
  drawPanes(g, panes, f, dpr, {a, b, W, H});
  // labels, divider, scale
  const label = side => (both || v.show !== "single" ? (side === "before" ? "قبل · " : "بعد · ") : "") + dateAr(v.shown[side].pass.time);
  // a label sits in the top right corner of its pane; with one pane the stylesheet places the two
  const place = (tag, pane) => {
    tag.style.top = pane ? (pane.y + 8) + "px" : "";
    tag.style.right = pane ? (size.w - pane.x - pane.w + 8) + "px" : "";
    tag.style.left = pane ? "auto" : "";
  };
  if (panes.length === 2) {
    tagB.hidden = tagA.hidden = false;
    place(tagB, panes[0]); place(tagA, panes[1]);
  } else {
    tagB.hidden = !b || f > 0.94; tagA.hidden = !a || f < 0.06;
    place(tagB, null); place(tagA, null);
  }
  if (b) tagB.textContent = label("before");
  if (a) tagA.textContent = label("after");
  handle.hidden = !both || v.show !== "split";
  handle.style.left = (f * 100) + "%";
  handle.setAttribute("aria-valuenow", String(Math.round(f * 100)));
  const first = panes[0], bar = scaleBar(v.grid.res * n / (Math.max(first.w, first.h) * v.zoom), 110);
  scale.hidden = false;
  scale.innerHTML = `${bar.label}<i style="width:${bar.px.toFixed(1)}px"></i>`;
}
// The pictures of the stage: its one or two panes, as the user framed them (zoom and pan), the
// divider at f, the layer, the hot rings, the place's ticks and what the tools drew. `dpr` is the
// canvas pixels per stage point; (ox0, oy0) where the stage begins on the canvas. The screen and
// the film to save both draw with it, so the film is exactly what was on the screen.
function drawPanes(g, panes, f, dpr, {a, b, W, H, ox0 = 0, oy0 = 0, layerAlpha = 1}) {
  const v = V, n = v.grid.size, showMarks = marksOn(v);
  for (const pane of panes) {
    const px = ox0 + pane.x * dpr, py = oy0 + pane.y * dpr, pw = pane.w * dpr, ph = pane.h * dpr;
    const s = Math.max(pw, ph) / n * v.zoom, ox = px + pw / 2 - v.cx * n * s, oy = py + ph / 2 - v.cy * n * s;
    const X = pane.side ? (pane.side === "after" ? pw : 0) : Math.round(f * pw);       // «بعد» is drawn left of X, «قبل» right of it
    g.save(); g.beginPath(); g.rect(px, py, pw, ph); g.clip();
    g.imageSmoothingEnabled = s < 3.5 * dpr; g.imageSmoothingQuality = "high";
    const flames = side => {
      if (!showMarks) return;
      g.lineWidth = 2 * dpr; g.strokeStyle = "#ffd60a";
      for (const h of v.hot[side]) {
        g.beginPath(); g.arc(ox + h.x * s, oy + h.y * s, Math.max(9 * dpr, Math.sqrt(h.pixels) * s * 0.9), 0, 6.2832); g.stroke();
      }
    };
    if (b && X < pw) {
      g.save(); g.beginPath(); g.rect(px + X, py, pw - X, ph); g.clip();
      g.drawImage(b, ox, oy, n * s, n * s); flames("before"); g.restore();
    }
    if (a && X > 0) {
      g.save(); g.beginPath(); g.rect(px, py, X, ph); g.clip();
      g.drawImage(a, ox, oy, n * s, n * s);
      const layer = v.layer && layerCanvas(v.layer);
      if (layer && layerAlpha > 0) {
        g.globalAlpha = layerAlpha;
        g.fillStyle = "rgba(5,8,13,.3)"; g.fillRect(px, py, X, ph);
        g.imageSmoothingEnabled = false;
        g.drawImage(layer, ox, oy, n * s, n * s);
        g.imageSmoothingEnabled = s < 3.5 * dpr;
        g.globalAlpha = 1;
      }
      flames("after"); g.restore();
    }
    // four ticks around the place itself (the centre is left free to look at); not on a wide overview
    const cx = ox + n / 2 * s, cy = oy + n / 2 * s;
    g.lineCap = "round";
    if (v.half <= 5 && v.ticks) for (const [w, col] of [[4 * dpr, "rgba(0,0,0,.55)"], [2 * dpr, "#fff"]]) {
      g.lineWidth = w; g.strokeStyle = col; g.beginPath();
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { g.moveTo(cx + dx * 12 * dpr, cy + dy * 12 * dpr); g.lineTo(cx + dx * 22 * dpr, cy + dy * 22 * dpr); }
      g.stroke();
    }
    drawTools(g, ox, oy, s, dpr);
    g.restore();
  }
  if (panes.length === 2) {                                        // the line between the two panes
    g.fillStyle = "#05080d";
    if (panes[0].x) g.fillRect(ox0 + panes[1].w * dpr, oy0, (panes[0].x - panes[1].w) * dpr, H); else g.fillRect(ox0, oy0 + panes[0].h * dpr, W, (panes[1].y - panes[0].h) * dpr);
  }
}
// what the tool in hand has put on the picture: the measured line, the area, the inspected point
function drawTools(g, ox, oy, s, dpr) {
  const v = V, at = p => [ox + p.x * s, oy + p.y * s];
  const stroke = (pts, close, fill) => {
    for (const [w, col] of [[5 * dpr, "rgba(0,0,0,.6)"], [2.5 * dpr, "#ffd60a"]]) {
      g.lineWidth = w; g.strokeStyle = col; g.fillStyle = col; g.lineJoin = "round";
      if (pts.length > 1) {
        g.beginPath(); g.moveTo(pts[0][0], pts[0][1]);
        for (const [x, y] of pts.slice(1)) g.lineTo(x, y);
        if (close) g.closePath();
        g.stroke();
      }
      for (const [x, y] of pts) { g.beginPath(); g.arc(x, y, w * 1.6, 0, 6.2832); g.fill(); }
    }
    if (fill && pts.length > 2) {
      g.fillStyle = "rgba(255,214,10,.16)"; g.beginPath(); g.moveTo(pts[0][0], pts[0][1]);
      for (const [x, y] of pts.slice(1)) g.lineTo(x, y);
      g.closePath(); g.fill();
    }
  };
  if (v.tool === "measure" && v.measure && v.measure.pts.length) stroke(v.measure.pts.map(at), false, false);
  if (v.tool === "area" && v.area && v.area.pts.length) stroke(v.area.pts.map(at), v.area.pts.length > 2, true);
  const cross = (p, col, r) => {
    const [x, y] = at(p);
    for (const [w, c] of [[4 * dpr, "rgba(0,0,0,.6)"], [2 * dpr, col]]) {
      g.lineWidth = w; g.strokeStyle = c;
      g.beginPath(); g.arc(x, y, r * dpr, 0, 6.2832);
      g.moveTo(x - (r + 7) * dpr, y); g.lineTo(x - (r - 3) * dpr, y); g.moveTo(x + (r - 3) * dpr, y); g.lineTo(x + (r + 7) * dpr, y);
      g.moveTo(x, y - (r + 7) * dpr); g.lineTo(x, y - (r - 3) * dpr); g.moveTo(x, y + (r - 3) * dpr); g.lineTo(x, y + (r + 7) * dpr);
      g.stroke();
    }
  };
  for (const note of v.notes.concat(v.draft ? [v.draft] : [])) drawNote(g, note, at, dpr);
  if (v.tool === "probe" && v.probe) cross(v.probe, "#40d6ec", 9);
  if (v.hover && v.panes && v.panes.length === 2) cross(v.hover, "#fff", 6);       // the pointer, mirrored in the other pane
}
// a mark kept in latitude and longitude, so it stays on its place when the area moves or is resized
function notePoints(note) {
  const v = V;
  return [note.a, note.b].map(q => { const [x, y] = I.gridPixel(v.grid, q.lat, q.lon); return {x, y}; });
}
// a thin rectangle or a clean arrow, in its colour over a dark edge so it reads on any ground
function drawNote(g, note, at, k) {
  const [p, q] = notePoints(note).map(at);
  g.save(); g.lineJoin = "round"; g.lineCap = "round";
  for (const [w, col] of [[5 * k, "rgba(0,0,0,.55)"], [2.4 * k, note.color]]) {
    g.lineWidth = w; g.strokeStyle = col; g.fillStyle = col;
    if (note.kind === "rect") {
      g.strokeRect(Math.min(p[0], q[0]), Math.min(p[1], q[1]), Math.abs(q[0] - p[0]), Math.abs(q[1] - p[1]));
    } else {
      const len = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1, ux = (q[0] - p[0]) / len, uy = (q[1] - p[1]) / len;
      const head = Math.min(len * 0.45, 18 * k), wide = head * 0.62;
      const bx = q[0] - ux * head, by = q[1] - uy * head;
      g.beginPath(); g.moveTo(p[0], p[1]); g.lineTo(bx, by); g.stroke();
      g.beginPath(); g.moveTo(q[0], q[1]); g.lineTo(bx - uy * wide, by + ux * wide); g.lineTo(bx + uy * wide, by - ux * wide); g.closePath();
      if (w > 3 * k) { g.lineWidth = 3 * k; g.stroke(); } else g.fill();
    }
  }
  g.restore();
}
BA.resize = () => { if (V) paint(); };

/* ---------- touch: drag the divider, pinch and pan, tap for the tool in hand ---------- */
// The view may run a little past the pictures' edge (the map goes on there): let go past it and the
// area moves to where the view is, with the same dates, size and settings («التحرك في الخريطة»).
const OVER = 0.35;
function clampView() {
  const v = V, pane = (v.panes && v.panes[0]) || {w: 1, h: 1};
  v.zoom = Math.min(10, Math.max(1, v.zoom));
  const share = sharesOf(pane, v.zoom);
  v.cx = Math.min(1 - share.x / 2 + OVER, Math.max(share.x / 2 - OVER, v.cx));
  v.cy = Math.min(1 - share.y / 2 + OVER, Math.max(share.y / 2 - OVER, v.cy));
}
function movedOut() {
  const v = V, pane = (v.panes && v.panes[0]) || {w: 1, h: 1}, share = sharesOf(pane, v.zoom), m = 0.12;
  return v.cx < share.x / 2 - m || v.cx > 1 - share.x / 2 + m || v.cy < share.y / 2 - m || v.cy > 1 - share.y / 2 + m;
}
function moveArea() {
  const v = V, n = v.grid.size, [lat, lon] = I.gridPoint(v.grid, v.cx * n, v.cy * n);
  v.lat = lat; v.lon = lon; v.cx = v.cy = 0.5; v.incident = null; v.title = v.title || "";
  v.want = {before: v.before && v.before.date, after: v.after && v.after.date};
  if (!v.want.before && !v.want.after) v.auto = true;
  toast("صور المنطقة الجديدة…");
  syncRoute(); renderChrome(); start();
}
// the pane under a point of the screen, and that point in the picture's own points
function paneAt(clientX, clientY) {
  const v = V, r = el("#baStage").getBoundingClientRect(), x = clientX - r.left, y = clientY - r.top;
  const panes = v.panes && v.panes.length ? v.panes : [{x: 0, y: 0, w: r.width, h: r.height}];
  const pane = panes.find(p => x >= p.x && x <= p.x + p.w && y >= p.y && y <= p.y + p.h) || panes[0];
  return {pane, x, y, r};
}
function pointAt(clientX, clientY) {
  const v = V, {pane, x, y} = paneAt(clientX, clientY), n = v.grid.size, s = Math.max(pane.w, pane.h) / n * v.zoom;
  return {x: v.cx * n + (x - pane.x - pane.w / 2) / s, y: v.cy * n + (y - pane.y - pane.h / 2) / s};
}
function zoomAt(z, clientX, clientY) {
  const v = V, {pane, x, y} = paneAt(clientX, clientY), side = Math.max(pane.w, pane.h);
  const dx = x - pane.x - pane.w / 2, dy = y - pane.y - pane.h / 2;
  const ux = v.cx + dx / (side * v.zoom), uy = v.cy + dy / (side * v.zoom), was = v.zoom > 1.01;
  v.zoom = Math.min(10, Math.max(1, z));
  v.cx = ux - dx / (side * v.zoom); v.cy = uy - dy / (side * v.zoom);
  clampView(); paint();
  if (was !== v.zoom > 1.01) renderTools();
}
function bindStage() {
  const st = el("#baStage"), pts = new Map();
  let drag = null, pinch = null, lastTap = 0, lastX = 0, lastY = 0, queued = false;
  const splitTo = e => { const r = st.getBoundingClientRect(); V.split = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)); paint(); };
  st.addEventListener("pointerdown", e => {
    if (!V || e.target.closest("button")) return;
    st.setPointerCapture(e.pointerId);
    pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
    if (pts.size === 2) {
      const [p, q] = [...pts.values()];
      pinch = {d: Math.hypot(p.x - q.x, p.y - q.y) || 1, zoom: V.zoom}; drag = null;
    } else if (pts.size === 1) {
      const onHandle = !!e.target.closest("#baHandle"), now = viewNow(), both = now.both && V.show === "split";
      if (V.tool === "mark" && !onHandle && V.grid && hasPicture()) {        // a mark is drawn from where the finger lands
        const [lat, lon] = I.gridPoint(V.grid, ...Object.values(pointAt(e.clientX, e.clientY)));
        V.draft = {kind: V.mark.kind, color: V.mark.color, a: {lat, lon}, b: {lat, lon}};
        drag = {mode: "mark", moved: false, far: 0};
        return;
      }
      drag = {mode: both && (onHandle || (V.zoom <= 1.01 && !V.tool)) ? "split" : "pan", moved: false, far: 0, handle: onHandle};
      if (drag.mode === "split" && !onHandle) splitTo(e);
    }
  });
  st.addEventListener("pointermove", e => {
    if (!V) return;
    const p = pts.get(e.pointerId);
    if (!p) {                                         // a mouse moving over the picture, no button down
      if (e.pointerType !== "mouse" || !V.grid || !hasPicture()) return;
      V.hover = pointAt(e.clientX, e.clientY);
      if (V.tool === "probe") V.probe = V.hover;
      if ((V.tool === "probe" || (V.panes && V.panes.length === 2)) && !queued) {
        queued = true;
        later(() => { queued = false; if (!V) return; if (V.tool === "probe") renderMeasure(); paint(); });
      }
      return;
    }
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    p.x = e.clientX; p.y = e.clientY;
    if (pinch && pts.size === 2) {
      const [a, b] = [...pts.values()];
      zoomAt(pinch.zoom * Math.hypot(a.x - b.x, a.y - b.y) / pinch.d, (a.x + b.x) / 2, (a.y + b.y) / 2);
    } else if (drag) {
      drag.far += Math.abs(dx) + Math.abs(dy);
      if (drag.far > 6) drag.moved = true;
      if (drag.mode === "split") splitTo(e);
      else if (drag.mode === "mark") {
        const [lat, lon] = I.gridPoint(V.grid, ...Object.values(pointAt(e.clientX, e.clientY)));
        V.draft.b = {lat, lon}; paint();
      } else {
        const {pane} = paneAt(e.clientX, e.clientY), side = Math.max(pane.w, pane.h);
        V.cx -= dx / (side * V.zoom); V.cy -= dy / (side * V.zoom); clampView(); paint();
      }
    }
  });
  const up = e => {
    if (!pts.delete(e.pointerId) || !V) return;
    if (pts.size < 2) pinch = null;
    if (!pts.size) {
      const now = Date.now(), tap = drag && !drag.moved && !drag.handle && e.type === "pointerup";
      if (drag && drag.mode === "mark") {                           // the mark is kept when it has a size
        const d = V.draft, [p, q] = d ? notePoints(d) : [{x: 0, y: 0}, {x: 0, y: 0}];
        if (d && e.type === "pointerup" && Math.hypot(q.x - p.x, q.y - p.y) >= 3) { V.notes.push(d); renderMeasure(); }
        V.draft = null; drag = null; paint();
        return;
      }
      if (drag && drag.mode === "pan" && drag.moved && e.type === "pointerup" && movedOut()) { drag = null; moveArea(); return; }
      if (tap && V.tool && V.grid && hasPicture()) {                // a tap with a tool in hand: a point for it
        tapTool(pointAt(e.clientX, e.clientY));
      } else if (drag && e.type === "pointerup" && now - lastTap < 320 && Math.hypot(e.clientX - lastX, e.clientY - lastY) < 30) {
        zoomAt(V.zoom > 1.5 ? 1 : 3, e.clientX, e.clientY); lastTap = 0;      // double tap
      } else { lastTap = now; lastX = e.clientX; lastY = e.clientY; }
      drag = null;
    } else drag = {mode: "pan", moved: true};
  };
  st.addEventListener("pointerup", up); st.addEventListener("pointercancel", up);
  st.addEventListener("pointerleave", () => { if (V && V.hover) { V.hover = null; if (V.panes && V.panes.length === 2) paint(); } });
  st.addEventListener("wheel", e => { if (!V) return; e.preventDefault(); zoomAt(V.zoom * (e.deltaY < 0 ? 1.25 : 0.8), e.clientX, e.clientY); }, {passive: false});
  el("#baHandle").addEventListener("keydown", e => {
    const d = e.key === "ArrowLeft" ? -0.04 : e.key === "ArrowRight" ? 0.04 : 0;
    if (d) { e.preventDefault(); V.split = Math.min(1, Math.max(0, V.split + d)); paint(); }
  });
}
function tapTool(p) {
  const v = V;
  if (v.tool === "measure") {
    if (!v.measure || v.measure.pts.length >= 2) v.measure = {pts: []};
    v.measure.pts.push(p);
  } else if (v.tool === "area") {
    if (!v.area) v.area = {pts: []};
    v.area.pts.push(p);
  } else if (v.tool === "probe") v.probe = p;
  renderMeasure(); paint();
}
function setTool(tool) {
  const v = V;
  v.tool = v.tool === tool ? "" : tool;
  if (v.tool === "measure") v.measure = {pts: []};
  if (v.tool === "area") v.area = {pts: []};
  if (v.tool !== "probe") v.probe = null;
  renderTools(); renderMeasure(); paint();
}
function blinkOn() {
  const v = V;
  clearInterval(v.blinkTimer);
  v.blinkTimer = setInterval(() => { if (V !== v) return clearInterval(v.blinkTimer); v.blinkOn = !v.blinkOn; paint(); }, 800);
}
// how the pictures are shown; a comparison needs two dates: the missing one is the clear pass nearest before (or after)
function setShow(show, quiet) {
  const v = V;
  if (!SHOWS.includes(show) || v.show === show) return;
  v.show = show;
  clearInterval(v.blinkTimer); v.blinkOn = false;
  if (show === "blink") blinkOn();
  if (show !== "single" && !pairOn(v) && (v.before || v.after) && !quiet) {
    const have = v.after ? "after" : "before", need = have === "after" ? "before" : "after";
    const partner = partnerOf(v.passes, need, v[have], v.event);
    if (partner) { v[need] = partner; v.auto = false; }
    else toast(need === "before" ? "لا صورة صافية أقدم ضمن القائمة: اطلب «أقدم» من آخر الشريط" : "لا صورة صافية أحدث بعد");
  }
  v.active = v[v.active] ? v.active : v.focus;
  if (quiet) { renderViews(); return; }
  syncRoute();
  renderViews(); renderDates(); renderStrip(); renderModes();
  loadPair();
}
// a band combination, or a layer on top (tapped again, the layer goes away)
function setMode(mode) {
  const v = V;
  if (LAYERS.includes(mode)) {
    if (!pairOn(v)) { toast("هذه الطبقة تحتاج صورتين: قبل وبعد"); return; }
    v.layer = v.layer === mode ? "" : mode;
  } else if (COMBOS.includes(mode)) {
    const why = comboNote(mode, [v.before, v.after]);
    if (why) { toast(why); return; }
    if (mode === "color" && v.combo === "color") v.layer = "";       // «ألوان طبيعية» again: the plain picture
    v.combo = mode;
  } else return;
  syncRoute();
  renderModes(); renderFind(); renderLegend(); showBusy(); paint();
  loadPair();                                        // the bands this view needs and are not in hand yet (nothing, most of the time)
}

/* ---------- what goes out: the pictures on screen, and what the report says about them ---------- */
const looks = () => { const css = getComputedStyle(document.documentElement), ui = css.getPropertyValue("--font-ui") || "sans-serif";
  return {ui, display: css.getPropertyValue("--font-display") || ui}; };
async function fontsReady() {
  try { if (document.fonts) await Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 1500))]); } catch (e) { /* default fonts */ }
}
// the sides that are on screen: both, or with «صورة واحدة» the one being looked at
function sidesShown() {
  const v = V, have = ["before", "after"].filter(k => picture(k, v.combo));
  return v.show === "single" && have.length === 2 ? [v.focus] : have;
}
// the place, the pictures and the results, as the report model wants them
function reportData() {
  const v = V, sides = sidesShown();
  if (!sides.length) return null;
  const pair = sides.length === 2, last = sides[sides.length - 1], names = namesNow(v.combo);
  const oneRes = k => { const sc = v.shown[k], sensor = I.sensorOf(sc.pass);
    return Math.max(v.grid.res, ...names.map(b => Math.max(sc.resOf[b] || 0, (sensor.bands[sc.from[b] || b] || [0, 0, 0])[2]))); };
  return {title: titleOf(), lat: v.lat, lon: v.lon, half: v.half, pixel: v.grid.res, combo: v.combo, names: namesOn(v.shown[last]), enhance: v.enhance,
    sides: sides.map(k => ({side: k, pass: v.shown[k].pass, res: oneRes(k)})),
    change: pair && v.pair ? v.pair.change : null, burn: pair && v.pair ? v.pair.burn : null,
    hot: I.hasBands(v.shown[last], HOT_BANDS) ? {after: v.hot[last].length, fresh: pair && v.hotSplit ? v.hotSplit.fresh : v.hot[last].length, old: pair && v.hotSplit ? v.hotSplit.old : 0} : null,
    now: Date.now()};
}
// the later picture in natural colour (or as on screen) with a layer on it, for the report and the saved picture
function layered(kind, size) {
  const v = V, base = picture("after", "color") || picture("after", v.combo), layer = layerCanvas(kind);
  if (!base || !layer) return null;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const g = c.getContext("2d");
  g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
  g.drawImage(base, 0, 0, size, size);
  g.fillStyle = "rgba(5,8,13,.3)"; g.fillRect(0, 0, size, size);
  g.imageSmoothingEnabled = false;
  g.drawImage(layer, 0, 0, size, size);
  return c;
}

/* ---------- the picture to save: portrait, no logo ---------- */
async function renderShot() {
  const v = V, b = picture("before", v.combo), a = picture("after", v.combo);
  if (!a && !b) return null;
  const W = 1080, H = 1350, M = 12, cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const g = cv.getContext("2d"), {ui: UI, display: DISPLAY} = looks();
  await fontsReady();
  const WHITE = "#f0f4fa", DIM = "#93a0b8", CYAN = "#40d6ec";
  g.fillStyle = "#06090e"; g.fillRect(0, 0, W, H);
  g.direction = "rtl"; g.textAlign = "right"; g.textBaseline = "alphabetic";
  const fit = (text, font, size, max) => {           // shrink, then cut, until the line fits
    let t = text;
    for (; size > 22; size -= 2) { g.font = font.replace("%", size); if (g.measureText(t).width <= max) return t; }
    while (t.length > 8 && g.measureText(t + "…").width > max) t = t.slice(0, -1);
    return t + "…";
  };
  const landsat = [v.shown.before, v.shown.after].some(s => s && s.pass.sensor === "landsat");
  g.fillStyle = CYAN; g.font = `600 26px ${UI}`;
  g.fillText((landsat ? "قبل وبعد · صور الأقمار" : "قبل وبعد · صور القمر الأوروبي Sentinel-2") + (v.combo === "color" ? "" : " · " + COMBO_AR[v.combo].name), W - 28, 50);
  g.fillStyle = WHITE;
  g.fillText(fit(titleOf(), `800 %px ${DISPLAY}`, 46, W - 56), W - 28, 112);
  g.fillStyle = DIM; g.font = `500 25px ${UI}`;
  g.fillText(`\u2066${v.lat.toFixed(4)}, ${v.lon.toFixed(4)}\u2069 · مربع ${v.half * 2}×${v.half * 2} كم${v.sub && v.title ? " · " + fit(v.sub, `500 %px ${UI}`, 25, 520) : ""}`, W - 28, 156);
  const Y = 184;
  const panel = (img, x, y, size, label, accent, extra) => {
    g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
    g.drawImage(img, x, y, size, size);
    if (extra) extra(x, y, size);
    const n = v.grid.size;
    for (const note of v.notes) drawNote(g, note, pt => [x + pt.x / n * size, y + pt.y / n * size], size / 520);
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
  const point = v.grid.res > 10.5 ? `كل نقطة ≈ ${metresText(v.grid.res)} على الأرض.` : landsat ? "كل نقطة 10 أمتار (30 في صور Landsat)." : "كل نقطة 10 أمتار على الأرض.";
  if (a && b && v.show !== "single") {
    const P = (W - 3 * M) / 2, p = v.pair, c = p && p.change, u = p && p.burn, lines = [];
    panel(b, W - M - P, Y, P, "قبل · " + dateAr(v.shown.before.pass.time), WHITE);
    panel(a, M, Y, P, "بعد · " + dateAr(v.shown.after.pass.time), "#ff7a1a");
    const Y2 = Y + P + M, tx = W - 28, burn = v.layer === "burn" && u && !quietBurn(u), quiet = !c || quietChange(c);
    const base = picture("after", "color") || a, layer = p ? layerCanvas(burn ? "burn" : "change") : null;
    panel(base, M, Y2, P, burn ? "أثر احتراق محتمل" : "ما تغيّر", CYAN, (x, y, size) => {
      if (!layer) return;
      g.fillStyle = "rgba(5,8,13,.3)"; g.fillRect(x, y, size, size);
      g.imageSmoothingEnabled = false; g.drawImage(layer, x, y, size, size);
    });
    let ty = Y2 + 46;
    g.fillStyle = CYAN; g.font = `600 26px ${UI}`; g.fillText(burn ? "أثر احتراق محتمل (dNBR)" : "تغيّر مرصود", tx, ty); ty += 62;
    g.fillStyle = WHITE; g.font = `800 50px ${DISPLAY}`;
    g.fillText(burn ? "نحو " + areaText(u.areaHa) : !c ? "لم يُحسب بعد" : quiet ? "لا تغيّر واضح" : "نحو " + areaText(c.areaHa), tx, ty); ty += 54;
    g.font = `500 26px ${UI}`;
    const rows = burn ? [1, 2, 3, 4].filter(k => u.classHa[k] > 0).map(k => [I.BURN_COLORS[k - 1], `${BURN_AR[k]}: ${areaText(u.classHa[k])}`])
      : quiet ? [] : [["#ff3b30", "صار أغمق: " + areaText(c.darkHa)], ["#22d3ee", "صار أفتح: " + areaText(c.brightHa)]];
    for (const [col, text] of rows) {
      g.fillStyle = col; g.fillRect(tx - 22, ty - 20, 22, 22);
      g.fillStyle = WHITE; g.fillText(text, tx - 34, ty); ty += 42;
    }
    if (c && (c.clouds || c.compared < 0.9)) lines.push("غيوم في إحدى الصورتين: التقدير أقل ثقة.");
    if (burn) lines.push("مؤشر لا دليل: قد يكون تغيّراً في الغطاء النباتي.");
    if (v.hot.after.length) lines.push(`لهب ظاهر في صورة «بعد»: ${v.hot.after.length === 1 ? "موقع واحد" : v.hot.after.length + " مواقع"}.`);
    lines.push("تُظهر الصور التغيّر ولا تحدد سببه.", point);
    g.fillStyle = DIM; g.font = `500 24px ${UI}`; ty += 14;
    for (const l of lines) { g.fillText(fit(l, `500 %px ${UI}`, 24, P - 30), tx, ty); ty += 38; }
  } else {
    const P = W - 2 * M, side = sidesShown()[0], one = side === "after" ? a : b, sc = v.shown[side];
    panel(one, M, Y, P, (a && b ? (side === "after" ? "بعد · " : "قبل · ") : "") + dateAr(sc.pass.time), side === "after" ? "#ff7a1a" : WHITE);
    g.fillStyle = DIM; g.font = `500 24px ${UI}`;
    g.fillText(`${timeAr(sc.pass.time)} بتوقيت بغداد · \u2066${satName(sc.pass)}\u2069 · ${point}`, W - 28, Y + P + 44);
  }
  g.textAlign = "center"; g.fillStyle = "#56627a"; g.font = `500 19px ${UI}`;
  g.fillText((landsat ? "Sentinel-2 (Copernicus) · Landsat (USGS/NASA) " : "Contains modified Copernicus Sentinel data ") + new Date().getUTCFullYear(), W / 2, H - 22);
  return new Promise(res => { try { cv.toBlob(res, "image/png"); } catch (e) { res(null); } });
}

/* ---------- the report: one tall picture (A4 portrait), and the same on paper ---------- */
let brand = null;
function brandMark() {                                 // the app's own mark, if the page has it beside it
  if (brand) return brand;
  return brand = new Promise(res => {
    if (typeof Image !== "function") return res(null);
    const img = new Image();
    img.onload = () => res(img); img.onerror = () => res(null);
    setTimeout(() => res(null), 1500);
    img.src = "brand-mark.svg";
  });
}
async function renderReport() {
  const v = V, d = reportData();
  if (!d) return null;
  const m = reportModel(d), sides = d.sides.map(s => s.side), pair = sides.length === 2;
  // twice as sharp where the phone can hold it (2480 x 3508: A4 at 300 points an inch)
  const k = (navigator.deviceMemory && navigator.deviceMemory < 4) || v.lite ? 1 : 2;
  const W = 1240, H = 1754, cv = document.createElement("canvas");
  cv.width = W * k; cv.height = H * k;
  const g = cv.getContext("2d"), {ui: UI, display: DISPLAY} = looks();
  await fontsReady();
  const mark = await brandMark();
  if (V !== v) return null;
  g.scale(k, k);
  const INK = "#141b2b", MUTED = "#5b6780", LINE = "#d9dfea", ACCENT = "#e5600a", NAVY = "#0a101c", SOFT = "#f3f5f9";
  const M = 64, R = W - M, iso = s => "\u2066" + s + "\u2069";       // a Latin run kept whole inside an Arabic line
  g.fillStyle = "#ffffff"; g.fillRect(0, 0, W, H);
  g.textBaseline = "alphabetic";
  const text = (s, x, y, font, color, align = "right") => { g.direction = "rtl"; g.font = font; g.fillStyle = color; g.textAlign = align; g.fillText(s, x, y); };
  const fit = (s, font, size, max) => {
    for (; size > 20; size -= 2) { g.font = font.replace("%", size); if (g.measureText(s).width <= max) return [s, font.replace("%", size)]; }
    while (s.length > 8 && g.measureText(s + "…").width > max) s = s.slice(0, -1);
    return [s + "…", font.replace("%", size)];
  };
  const wrap = (s, font, max) => {
    g.font = font;
    const out = [];
    let line = "";
    for (const word of s.split(" ")) {
      const t = line ? line + " " + word : word;
      if (line && g.measureText(t).width > max) { out.push(line); line = word; } else line = t;
    }
    if (line) out.push(line);
    return out;
  };
  const rule = y => { g.fillStyle = LINE; g.fillRect(M, y, W - 2 * M, 1.5); };
  // the head: the mark and the name
  g.fillStyle = NAVY; g.fillRect(0, 0, W, 124);
  g.fillStyle = ACCENT; g.fillRect(0, 124, W, 5);
  let x = M;
  if (mark) { try { g.drawImage(mark, x, 28, 68, 68); x += 84; } catch (e) { /* no mark */ } }
  g.direction = "ltr"; g.textAlign = "left"; g.font = `700 34px "Chakra Petch", ${UI}`; g.fillStyle = "#ffffff";
  g.fillText("ALFAQAAR", x, 76);
  const w1 = g.measureText("ALFAQAAR").width;
  g.font = `500 34px "Chakra Petch", ${UI}`; g.fillStyle = "#40d6ec"; g.fillText("FIRMS", x + w1 + 14, 76);
  text("رصد", R, 68, `800 40px ${DISPLAY}`, "#ffffff");
  text(m.kind, R, 102, `500 20px ${UI}`, "#c9d3e3");
  // the place
  let y = 190;
  const [title, titleFont] = fit(m.title, `800 %px ${DISPLAY}`, 42, W - 2 * M);
  text(title, R, y, titleFont, INK); y += 38;
  text(`${iso(m.where)} · ${m.box} · ${m.pixel}`, R, y, `500 21px ${UI}`, MUTED); y += 31;
  text(`التركيبة: ${m.comboName} (${iso(m.bands)})`, R, y, `500 21px ${UI}`, MUTED); y += 24;
  // the pictures, «قبل» on the right
  const panel = (img, px, py, size, s) => {
    g.imageSmoothingEnabled = true; g.imageSmoothingQuality = "high";
    g.fillStyle = "#05080d"; g.fillRect(px, py, size, size);
    g.drawImage(img, px, py, size, size);
    g.strokeStyle = LINE; g.lineWidth = 1.5; g.strokeRect(px, py, size, size);
    g.font = `700 22px ${UI}`; g.direction = "rtl";
    const w = g.measureText(s.label).width + 26;
    g.fillStyle = "rgba(5,8,13,.82)"; g.fillRect(px + size - w - 10, py + 10, w, 40);
    g.fillStyle = s.side === "after" ? "#ff7a1a" : "#ffffff"; g.fillRect(px + size - 14, py + 10, 4, 40);
    text(s.label, px + size - 24, py + 38, `700 22px ${UI}`, "#ffffff");
    const bar = scaleBar(v.half * 2000 / size, size * 0.3);
    g.fillStyle = "rgba(5,8,13,.6)"; g.fillRect(px + 12, py + size - 44, bar.px + 16, 34);
    g.fillStyle = "#ffffff"; g.fillRect(px + 20, py + size - 19, bar.px, 4);
    text(bar.label, px + 20 + bar.px / 2, py + size - 25, `600 18px ${UI}`, "#ffffff", "center");
    const right = px + size;
    text(`${s.label} · ${s.date}`, right, py + size + 34, `700 24px ${DISPLAY}`, INK);
    text(`${iso(s.baghdad)} بتوقيت بغداد · ${iso(s.utc + " UTC")}`, right, py + size + 65, `500 19px ${UI}`, MUTED);
    text(`${iso(s.satName + " · " + s.sensor)} · ${s.res}${s.clear ? " · " + s.clear : ""}`, right, py + size + 94, `500 19px ${UI}`, INK);
  };
  const top = y + 4, G = 24, P = pair ? (W - 2 * M - G) / 2 : 720;
  if (pair) { panel(picture("before", v.combo), R - P, top, P, m.sides[0]); panel(picture("after", v.combo), M, top, P, m.sides[1]); }
  else panel(picture(sides[0], v.combo), (W - P) / 2, top, P, m.sides[0]);
  y = top + P + 118;
  // the analysis: the layers as small pictures with their legends, and the summary beside them
  rule(y); y += 42;
  text("التحليل", R, y, `800 26px ${DISPLAY}`, INK); y += 18;
  // each Latin name is kept whole, and the items of a line still run from the right
  const latin = s => s.replace(/[A-Za-z][A-Za-z0-9\-\/]*/g, w => iso(w));
  // the cautions go in a box at the foot: the small pictures above take the room that is left
  const notes = m.caveats.flatMap(c => wrap(latin(c), `500 16.5px ${UI}`, W - 2 * M - 60).map((l, i) => [l, i === 0]));
  const boxH = notes.length * 24 + 58, boxY = H - 96 - boxH;
  const parts = [m.change, m.burn].filter(Boolean), kinds = [m.change && "change", m.burn && "burn"].filter(Boolean);
  const under = part => 72 + part.rows.length * 26 + wrap(part.note, `500 15px ${UI}`, 240).length * 21;
  const T = Math.max(180, Math.min(286, boxY - 14 - y - Math.max(0, ...parts.map(under))));
  let left = R;                                                   // where the free column for the summary ends
  parts.forEach((part, i) => {
    const px = R - T - i * (T + G), img = layered(kinds[i], Math.round(T * k));
    g.fillStyle = "#05080d"; g.fillRect(px, y, T, T);
    if (img) { g.imageSmoothingEnabled = true; g.drawImage(img, px, y, T, T); }
    g.strokeStyle = LINE; g.lineWidth = 1.5; g.strokeRect(px, y, T, T);
    let ty = y + T + 30;
    text(part.title, px + T, ty, `700 20px ${DISPLAY}`, INK); ty += 30;
    text(part.headline, px + T, ty, `800 22px ${DISPLAY}`, ACCENT); ty += 30;
    for (const [color, label, area] of part.rows) {
      g.fillStyle = color; g.fillRect(px + T - 16, ty - 15, 16, 16);
      text(`${label}: ${area}`, px + T - 24, ty, `500 18px ${UI}`, INK); ty += 26;
    }
    for (const l of wrap(part.note, `500 15px ${UI}`, T)) { text(l, px + T, ty, `500 15px ${UI}`, MUTED); ty += 21; }
    left = px - G;
  });
  // the colour ramp of an index on screen
  let sy = y + 8;
  if (I.COMBOS[v.combo].index) {
    const r = I.RAMPS[v.combo], bw = Math.min(360, left - M), bx = left - bw, grad = g.createLinearGradient(bx, 0, bx + bw, 0);
    for (const [val, col] of r.stops) grad.addColorStop((val - r.min) / (r.max - r.min), col);
    text(m.comboName, left, sy + 14, `700 19px ${DISPLAY}`, INK); sy += 26;
    g.fillStyle = grad; g.fillRect(bx, sy, bw, 16);
    text(COMBO_AR[v.combo].ends[0], bx, sy + 38, `500 16px ${UI}`, MUTED, "left");
    text(COMBO_AR[v.combo].ends[1], bx + bw, sy + 38, `500 16px ${UI}`, MUTED, "right");
    sy += 62;
  }
  text("ملخص التحليل", left, sy + 18, `700 21px ${DISPLAY}`, INK); sy += 50;
  for (const line of m.summary) {
    const rows = wrap(latin(line), `500 18px ${UI}`, left - M - 22);
    if (sy + rows.length * 26 > boxY - 8) break;                  // no room left above the cautions
    g.fillStyle = ACCENT; g.beginPath(); g.arc(left - 6, sy - 6, 3.5, 0, 6.2832); g.fill();
    for (const l of rows) { text(l, left - 20, sy, `500 18px ${UI}`, INK); sy += 26; }
    sy += 6;
  }
  g.fillStyle = SOFT; g.fillRect(M, boxY, W - 2 * M, boxH);
  g.fillStyle = ACCENT; g.fillRect(R - 4, boxY, 4, boxH);
  text("تنبيهات", R - 22, boxY + 32, `700 19px ${DISPLAY}`, INK);
  let ny = boxY + 60;
  for (const [l, first] of notes) {
    if (first) { g.fillStyle = MUTED; g.beginPath(); g.arc(R - 28, ny - 6, 3, 0, 6.2832); g.fill(); }
    text(l, R - 40, ny, `500 16.5px ${UI}`, INK); ny += 24;
  }
  // the foot: the source, when it was made, the credit line
  rule(H - 78);
  text("المصدر: " + m.sources.map(s => s.replace(/^(Sentinel-2|Landsat)/, x0 => iso(x0))).join(" · "), R, H - 50, `500 16px ${UI}`, MUTED);
  text(m.generated.replace(/\((\d\d:\d\d UTC)\)/, (x0, t) => "(" + iso(t) + ")"), R, H - 26, `500 16px ${UI}`, MUTED);
  g.direction = "ltr"; g.textAlign = "left"; g.font = `500 13px ${UI}`; g.fillStyle = "#8590a6";
  g.fillText(m.credit, M, H - 26);
  return new Promise(res => { try { cv.toBlob(res, "image/png"); } catch (e) { res(null); } });
}
// The report on paper, or as a PDF: the same words and pictures as a page of its own that only
// the printer sees («طباعة» of the phone or the browser then saves it as a PDF).
function printHTML(m, pics) {
  const bd = s => `<bdi dir="ltr">${esc(s)}</bdi>`, li = list => list.map(t => `<li>${esc(t)}</li>`).join("");
  const figure = (s, src) => `<figure><div class="bp-pic">${src ? `<img src="${src}" alt="">` : ""}<span>${s.label}</span></div>
    <figcaption><b>${s.label} · ${esc(s.date)}</b><span>${bd(s.baghdad)} بتوقيت بغداد · ${bd(s.utc + " UTC")}</span><span>${bd(s.satName + " · " + s.sensor)} · ${esc(s.res)}${s.clear ? " · " + esc(s.clear) : ""}</span></figcaption></figure>`;
  const layer = (part, src) => `<figure>${src ? `<div class="bp-pic"><img src="${src}" alt=""></div>` : ""}
    <figcaption><b>${esc(part.title)}</b><strong>${esc(part.headline)}</strong>${part.rows.map(r => `<span><i style="background:${r[0]}"></i>${esc(r[1])}: ${esc(r[2])}</span>`).join("")}<small>${esc(part.note)}</small></figcaption></figure>`;
  return `<div class="bp">
    <header><div class="bp-name"><b>رصد</b><small>${esc(m.kind)}</small></div><div class="bp-latin" dir="ltr"><img src="brand-mark.svg" alt=""><b>ALFAQAAR</b><span>FIRMS</span></div></header>
    <h1>${ltr(m.title)}</h1>
    <p class="bp-sub">${bd(m.where)} · ${esc(m.box)} · ${esc(m.pixel)}<br>التركيبة: ${esc(m.comboName)} (${bd(m.bands)})</p>
    <div class="bp-pics n${m.sides.length}">${m.sides.map((s, i) => figure(s, pics.sides[i])).join("")}</div>
    ${m.change || m.burn ? `<h2>التحليل</h2><div class="bp-layers">${m.change ? layer(m.change, pics.change) : ""}${m.burn ? layer(m.burn, pics.burn) : ""}</div>` : ""}
    <h2>ملخص التحليل</h2><ul class="bp-list">${li(m.summary)}</ul>
    <div class="bp-notes"><b>تنبيهات</b><ul>${li(m.caveats)}</ul></div>
    <footer><span>المصدر: ${esc(m.sources.join(" · "))}</span><span>${esc(m.generated)}</span><span dir="ltr">${esc(m.credit)}</span></footer>
  </div>`;
}
async function printReport() {
  const v = V, d = reportData();
  if (!d) { toast("لا صورة معروضة بعد"); return; }
  if (typeof root.print !== "function") { toast("هذا المتصفح لا يطبع. استخدم «تقرير» واطبع الصورة."); return; }
  const m = reportModel(d), url = c => { try { return c && c.toDataURL ? c.toDataURL("image/jpeg", 0.9) : ""; } catch (e) { return ""; } };
  const pics = {sides: d.sides.map(s => url(picture(s.side, v.combo))), change: m.change ? url(layered("change", 640)) : "", burn: m.burn ? url(layered("burn", 640)) : ""};
  let box = el("#baPrint");
  if (!box && document.body.appendChild) {
    box = document.createElement("section");
    box.id = "baPrint";
    document.body.appendChild(box);
  }
  if (!box) return;
  box.innerHTML = printHTML(m, pics);
  document.body.classList.add("ba-printing");
  const done = () => { document.body.classList.remove("ba-printing"); box.innerHTML = ""; if (root.removeEventListener) root.removeEventListener("afterprint", done); };
  if (root.addEventListener) root.addEventListener("afterprint", done);
  // the pictures must be decoded before the printer takes the page
  try { if (box.querySelectorAll) await Promise.all([...box.querySelectorAll("img")].map(img => img.decode ? img.decode().catch(() => {}) : null)); } catch (e) { /* print anyway */ }
  try { root.print(); } catch (e) { done(); toast("تعذّرت الطباعة. استخدم «تقرير» واطبع الصورة."); }
}

/* ---------- the film to save: what the screen shows, in the display chosen there, in high detail ---------- */
// The film keeps the stage's own shape and framing (zoom, pan, panes), drawn 1920 points wide from
// a wide screen and 1080 from a phone, under a title band and over a band with the scale and source.
function filmSize() {
  const st = el("#baStage"), w = (st && st.clientWidth) || 720, h = (st && st.clientHeight) || w;
  const FW = w >= h * 1.15 ? 1920 : 1080, u = Math.min(FW, 1280) / 720;
  const M = Math.round(14 * u), AW = FW - 2 * M, AH = Math.round(AW * h / w / 2) * 2;
  const HH = Math.round(118 * u / 2) * 2, FB = Math.round(64 * u / 2) * 2;
  return {w, h, FW, FH: HH + AH + FB, u, M, AW, AH, HH, FB, k: AW / w};
}
function drawFilm(g, look, state) {
  const v = V, z = look.size, {FW, FH, u, M, AW, AH, HH, k} = z, Y = HH;
  const R = FW - Math.round(22 * u), mode = look.mode;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.fillStyle = "#06090e"; g.fillRect(0, 0, FW, FH);
  // the title band
  g.direction = "rtl"; g.textAlign = "right"; g.textBaseline = "alphabetic";
  g.fillStyle = "#ff7a1a"; g.fillRect(FW - Math.round(6 * u), Math.round(18 * u), Math.round(6 * u), Math.round(74 * u));
  g.fillStyle = "#40d6ec"; g.font = `600 ${Math.round(18 * u)}px ${look.ui}`;
  g.fillText(look.head, R, Math.round(36 * u));
  g.fillStyle = "#f0f4fa"; g.font = `800 ${Math.round(look.titleSize * u)}px ${look.display}`;
  g.fillText(look.title, R, Math.round(74 * u));
  g.fillStyle = "#93a0b8"; g.font = `500 ${Math.round(16 * u)}px ${look.ui}`;
  g.fillText(`\u2066${v.lat.toFixed(4)}, ${v.lon.toFixed(4)}\u2069 · مربع ${v.half * 2}×${v.half * 2} كم`, R, Math.round(102 * u));
  // the pictures, as on the screen
  const both = !!(look.a && look.b), panes = panesOf(mode, z.w, z.h, both);
  g.save(); g.beginPath(); g.rect(M, Y, AW, AH); g.clip();
  g.fillStyle = "#05080d"; g.fillRect(M, Y, AW, AH);
  const opts = {a: look.a, b: look.b, W: AW, H: AH, ox0: M, oy0: Y};
  if (mode === "single") {                                    // one picture: «قبل» fades into «بعد»
    drawPanes(g, panes, 0, k, opts);
    if (state.split > 0) { g.globalAlpha = state.split; drawPanes(g, panes, 1, k, opts); g.globalAlpha = 1; }
  } else drawPanes(g, panes, mode === "side" ? 1 : state.split, k, opts);
  g.restore();
  const f = mode === "side" ? 1 : state.split;
  if (mode === "split" && f > 0 && f < 1) {                   // the divider and its knob
    const X = M + Math.round(f * AW);
    g.fillStyle = "#fff"; g.fillRect(X - Math.round(1.5 * u), Y, Math.max(2, Math.round(3 * u)), AH);
    g.beginPath(); g.arc(X, Y + AH / 2, 17 * u, 0, 6.2832); g.fill();
    g.fillStyle = "#111"; g.font = `700 ${Math.round(15 * u)}px system-ui, sans-serif`; g.textAlign = "center"; g.direction = "ltr";
    g.fillText("‹ ›", X, Y + AH / 2 + 5 * u);
    g.direction = "rtl"; g.textAlign = "right";
  }
  // the date labels, where the screen has them
  const tag = (text, x0, y0, right, accent, alpha) => {
    if (alpha <= 0) return;
    g.globalAlpha = alpha;
    g.font = `700 ${Math.round(18 * u)}px ${look.ui}`;
    const w = g.measureText(text).width + 22 * u, hgt = Math.round(34 * u), x = right ? x0 - w : x0;
    g.fillStyle = "rgba(5,8,13,.82)"; g.fillRect(x, y0, w, hgt);
    g.fillStyle = accent; g.fillRect(right ? x + w - 4 * u : x, y0, Math.round(4 * u), hgt);
    g.fillStyle = "#f0f4fa"; g.fillText(text, x + w - 11 * u, y0 + hgt - 10 * u);
    g.globalAlpha = 1;
  };
  const pad = Math.round(8 * u);
  if (panes.length === 2) for (const pane of panes) {
    tag(pane.side === "before" ? look.before : look.after, M + (pane.x + pane.w) * k - pad, Y + pane.y * k + pad, true,
      pane.side === "before" ? "#f0f4fa" : "#ff7a1a", 1);
  } else if (mode === "split") {
    tag(look.before, M + AW - pad, Y + pad, true, "#f0f4fa", f < 0.86 ? 1 : 0);
    tag(look.after, M + pad, Y + pad, false, "#ff7a1a", f > 0.14 ? 1 : 0);
  } else {
    tag(look.before, M + AW - pad, Y + pad, true, "#f0f4fa", 1 - f);
    tag(look.after, M + AW - pad, Y + pad, true, "#ff7a1a", f);
  }
  // the scale, as on the screen: 110 stage points at most
  const first = panes[0], bar = scaleBar(v.grid.res * v.grid.size / (Math.max(first.w, first.h) * v.zoom), 110);
  const bw = bar.px * k, bx = M + pad, by = Y + AH - pad - Math.round(10 * u);
  g.fillStyle = "rgba(5,8,13,.72)"; g.fillRect(bx - 6 * u, by - 24 * u, bw + 12 * u, 36 * u);
  g.fillStyle = "#fff"; g.fillRect(bx, by, bw, Math.max(2, Math.round(3 * u)));
  g.fillRect(bx, by - 6 * u, Math.max(2, 2 * u), 9 * u); g.fillRect(bx + bw - Math.max(2, 2 * u), by - 6 * u, Math.max(2, 2 * u), 9 * u);
  g.textAlign = "left"; g.direction = "ltr"; g.font = `600 ${Math.round(14 * u)}px ${look.ui}`;
  g.fillText(String(bar.label).replace(/<[^>]+>/g, ""), bx, by - 8 * u);
  // the bottom band: what the layer shows (when one is on), the picture's maker, the source
  const fy = Y + AH + Math.round(40 * u);
  g.direction = "rtl"; g.textAlign = "right"; g.fillStyle = "#f0f4fa"; g.font = `700 ${Math.round(20 * u)}px ${look.display}`;
  g.fillText(look.found || (mode === "side" ? "قبل وبعد" : f > 0.5 ? "بعد" : "قبل"), R, fy);
  g.textAlign = "left"; g.direction = "ltr"; g.fillStyle = "#56627a"; g.font = `500 ${Math.round(13 * u)}px ${look.ui}`;
  g.fillText(look.credit, Math.round(20 * u), fy);
}
// record the film in real time into a video file; the canvas on screen shows what is being recorded
async function recordFilm(canvas, type, onProgress, cancelled) {
  const v = V, g = canvas.getContext("2d"), {ui, display} = looks(), size = filmSize();
  await fontsReady();
  const p = v.pair, burn = v.layer === "burn" && p && !quietBurn(p.burn), c = p && p.change;
  const landsat = [v.shown.before, v.shown.after].some(st => st && st.pass.sensor === "landsat");
  const room = (size.FW - 2 * size.M - 40 * size.u) / size.u;
  let title = titleOf(), titleSize = 32;
  for (; titleSize > 20; titleSize -= 2) { g.font = `800 ${titleSize}px ${display}`; if (g.measureText(title).width <= room) break; }
  while (title.length > 8 && g.measureText(title + "…").width > room) title = title.slice(0, -1);
  if (title !== titleOf()) title += "…";
  const both = !!(picture("before", v.combo) && picture("after", v.combo));
  const mode = both ? (FILM_STEPS[v.show] ? v.show : "split") : "single";
  // everything follows the screen: the display, the band view and «تحسين العرض» (in the pictures),
  // the layer that is on (on the «بعد» side, as on the screen), the hot rings, the zoom and pan
  const look = {ui, display, title, titleSize, size, mode, b: picture("before", v.combo), a: picture("after", v.combo),
    head: (landsat ? "قبل وبعد · صور الأقمار" : "قبل وبعد · صور القمر الأوروبي Sentinel-2") + (v.combo === "color" ? "" : " · " + COMBO_AR[v.combo].name)
      + (v.enhance ? " · عرض محسَّن" : ""),
    before: "قبل · " + dateAr(v.shown.before.pass.time), after: "بعد · " + dateAr(v.shown.after.pass.time),
    found: !v.layer ? "" : burn ? "أثر احتراق محتمل: نحو " + areaText(p.burn.areaHa)
      : v.layer === "change" && c && !quietChange(c) ? "تغيّر مرصود: نحو " + areaText(c.areaHa) : "",
    credit: (landsat ? "Sentinel-2 (Copernicus) · Landsat (USGS/NASA) " : "Contains modified Copernicus Sentinel data ") + new Date().getUTCFullYear()};
  const plan = filmPlan(mode);
  drawFilm(g, look, plan.at(0));
  const stream = canvas.captureStream(25), chunks = [];
  const recorder = new MediaRecorder(stream, {mimeType: type, videoBitsPerSecond: size.FW >= 1920 ? 12000000 : 8000000});
  recorder.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
  const stopped = new Promise((res, rej) => { recorder.onstop = res; recorder.onerror = () => rej(new Error("recorder")); });
  recorder.start(200);
  const started = performance.now();
  await new Promise(res => {
    const frame = () => {
      const t = (performance.now() - started) / 1000;
      if (V !== v || cancelled() || t >= plan.duration + 0.12) return res();
      drawFilm(g, look, plan.at(t));
      onProgress(Math.min(1, t / plan.duration));
      setTimeout(frame, 40);                         // 25 pictures a second, also when the page is not in front
    };
    frame();
  });
  if (recorder.state !== "inactive") recorder.stop();
  await stopped;
  stream.getTracks().forEach(track => track.stop());
  return V !== v || cancelled() || !chunks.length ? null : new Blob(chunks, {type: type.split(";")[0]});
}
async function takeFilm() {
  const v = V, box = el("#shot"), type = filmType();
  const close = () => { box.hidden = true; box.innerHTML = ""; };
  const btn = (id, text, primary) => `<button class="btn ba-btn${primary ? " primary" : ""}" type="button" id="${id}">${text}</button>`;
  if (!picture("before", v.combo) || !picture("after", v.combo)) { toast("الفيديو يحتاج صورتي «قبل» و«بعد» معاً"); return; }
  if (!type || !document.createElement("canvas").captureStream) {
    box.innerHTML = `<p>هذا المتصفح لا يسجّل الفيديو. استخدم «صورة» أو «تقرير» بدلاً منه.</p><div class="actions">${btn("shotClose", "إغلاق")}</div>`;
    box.hidden = false; el("#shotClose").onclick = close;
    return;
  }
  const fz = filmSize();
  box.innerHTML = `<canvas id="filmCanvas" width="${fz.FW}" height="${fz.FH}" aria-label="الفيديو أثناء التسجيل"></canvas>
    <p id="filmStatus" role="status">جارٍ تسجيل الفيديو…</p><div class="actions">${btn("shotClose", "إلغاء")}</div>`;
  box.hidden = false;
  let cancel = false;
  el("#shotClose").onclick = () => { cancel = true; close(); };
  let blob = null;
  try { blob = await recordFilm(el("#filmCanvas"), type, share => { const s = el("#filmStatus"); if (s) s.textContent = `جارٍ تسجيل الفيديو… ${Math.round(share * 100)}%`; }, () => cancel || box.hidden); }
  catch (e) { blob = null; }
  if (cancel || box.hidden) return;
  if (!blob) {
    box.innerHTML = `<p>تعذّر تسجيل الفيديو. حاول مرة أخرى.</p><div class="actions">${btn("shotClose", "إغلاق")}</div>`;
    el("#shotClose").onclick = close;
    return;
  }
  const ext = blob.type.includes("mp4") ? "mp4" : "webm", name = `ALFAQAAR-before-after-${v.lat.toFixed(3)}_${v.lon.toFixed(3)}.${ext}`;
  const file = new File([blob], name, {type: blob.type}), url = URL.createObjectURL(blob);
  const canShare = !!(navigator.canShare && navigator.canShare({files: [file]}));
  box.innerHTML = `<video src="${url}" controls playsinline muted loop autoplay></video>
    <div class="actions">${btn("shotSave", canShare ? "حفظ أو إرسال الفيديو" : "تنزيل الفيديو", true)}${btn("shotClose", "إغلاق")}</div>
    <p>${canShare ? "ستفتح نافذة المشاركة: اختر «حفظ الفيديو» أو أرسله مباشرة." : "يُحفظ الملف في التنزيلات."}${ext === "webm" ? " الصيغة WebM: تعمل في تلغرام وأندرويد والحاسوب." : ""}</p>`;
  el("#shotClose").onclick = () => { close(); URL.revokeObjectURL(url); };
  el("#shotSave").onclick = async () => {
    if (canShare) { try { await navigator.share({files: [file], title: titleOf()}); return; } catch (e) { if (e.name === "AbortError") return; } }
    const link = document.createElement("a"); link.href = url; link.download = name;
    document.body.appendChild(link); link.click(); link.remove(); toast("حُفظ الفيديو");
  };
}
async function share() {
  const v = V;
  const hash = routeOf({lat: v.lat, lon: v.lon, half: v.half, before: v.before && v.before.date, after: v.after && v.after.date, name: titleOf(),
    show: v.show, combo: v.combo, layer: v.layer});
  const url = location.origin + location.pathname + "#" + encodeURIComponent(hash);
  const p = v.pair, c = p && pairOn(v) ? p.change : null, u = p && pairOn(v) ? p.burn : null;
  const text = [titleOf(), v.before ? "قبل: " + dateAr(v.before.time) : "", v.after ? (v.before ? "بعد: " : "الصورة: ") + dateAr(v.after.time) : "",
    c && !quietChange(c) ? "تغيّر مرصود: نحو " + areaText(c.areaHa) : "", u && !quietBurn(u) ? "أثر احتراق محتمل: نحو " + areaText(u.areaHa) : "",
    `${v.lat.toFixed(4)}, ${v.lon.toFixed(4)}`].filter(Boolean).join("\n");
  if (navigator.share) { try { await navigator.share({title: titleOf(), text, url}); return; } catch (e) { if (e.name === "AbortError") return; } }
  copy(text + "\n" + url);
}
function star() {
  const v = V, list = places(), at = list.findIndex(p => samePlace(p, v));
  if (at >= 0) { list.splice(at, 1); store.set(KEYS.places, list); toast("حُذف من المواقع المحفوظة"); renderHead(); return; }
  const name = (root.prompt("اسم الموقع", titleOf()) || "").trim();
  if (!name) return;
  list.unshift({name: name.slice(0, 80), lat: +v.lat.toFixed(5), lon: +v.lon.toFixed(5), half: v.half, seen: Date.now()});
  store.set(KEYS.places, list.slice(0, 60));
  if (!v.title) { v.title = name; syncRoute(); }
  toast("حُفظ الموقع"); renderHead();
}

/* ------------------------------------------------------------------ taps */
function onClick(e) {
  const t = e.target.closest("[data-act]");
  if (!t || t.disabled) return;
  const act = t.dataset.act, v = V;
  if (act === "back") return BA.back();
  if (act === "pick") return BA.startPick();
  if (act === "draw") return BA.startPick("area");
  if (act === "here") return BA.here();
  if (act === "open") return setRoute(routeOf({lat: +t.dataset.lat, lon: +t.dataset.lon, half: +t.dataset.half || 2, name: t.dataset.name}));
  if (act === "show") return BA.pin(+t.dataset.lat, +t.dataset.lon, t.dataset.name);
  if (act === "forget") { const list = places(); list.splice(+t.dataset.i, 1); store.set(KEYS.places, list); return showChooser(); }
  if (!v) return;
  if (act === "look") setShow(t.dataset.look);
  else if (act === "blink") setShow(v.show === "blink" ? "split" : "blink");
  else if (act === "mode") setMode(t.dataset.mode);
  else if (act === "marks") { v.marks = !marksOn(v); renderModes(); renderLegend(); paint(); }
  else if (act === "enhance") { v.enhance = !v.enhance; store.set(KEYS.enhance, v.enhance); renderModes(); renderFind(); showBusy(); paint(); }
  else if (act === "side") { if (v.active !== t.dataset.side) { v.active = t.dataset.side; if (v[v.active]) v.focus = v.active; renderDates(); renderStrip(); paint(); } }
  else if (act === "step") step(t.dataset.side, +t.dataset.dir);
  else if (act === "take") {
    const p = v.passes.find(x => x.key === t.dataset.key), side = t.dataset.side || v.active;
    // a date tapped in the strip goes to the side in hand; in the old list a second tap let it go
    if (p && (v[side] !== p || t.dataset.side)) setSide(side, v[side] === p ? null : p);
  }
  else if (act === "quick") quick(t.dataset.q);
  else if (act === "expand") { v.expand = !v.expand; renderStrip(); }
  else if (act === "size") {
    const h = +t.dataset.half;
    if (h !== v.half && SIZES.includes(h)) {
      v.half = h; v.want = {before: v.before && v.before.date, after: v.after && v.after.date};
      if (!v.auto && !v.want.before && !v.want.after) v.auto = true;
      syncRoute(); renderChrome(); start();
    }
  }
  else if (act === "older" || act === "newer") extend(act === "older" ? -1 : 1);
  else if (act === "noday") setDay("");
  else if (act === "tool") setTool(t.dataset.tool);
  else if (act === "ticks") { v.ticks = !v.ticks; store.set(KEYS.ticks, v.ticks); renderTools(); paint(); }
  else if (act === "mshape" || act === "mcolor") {
    if (act === "mshape") v.mark.kind = t.dataset.kind === "arrow" ? "arrow" : "rect";
    else if (MARK_COLORS.includes(t.dataset.color)) v.mark.color = t.dataset.color;
    store.set(KEYS.mark, v.mark); renderMeasure();
  }
  else if (act === "mundo") { v.notes.pop(); renderMeasure(); paint(); }
  else if (act === "mclear") { v.notes = []; renderMeasure(); paint(); }
  else if (act === "hires") BA.hires(v);
  else if (act === "shift") { v.cx += 0.5 * +t.dataset.dx; v.cy += 0.5 * +t.dataset.dy; moveArea(); }
  else if (act === "measure") setTool("measure");
  else if (act === "undo") { if (v.area && v.area.pts.length) { v.area.pts.pop(); renderMeasure(); paint(); } }
  else if (act === "wipe") { v.area = {pts: []}; renderMeasure(); paint(); }
  else if (act === "fit") { v.zoom = 1; v.cx = v.cy = 0.5; renderTools(); paint(); }
  else if (act === "zoom") { const r = el("#baStage").getBoundingClientRect(); zoomAt(v.zoom * (+t.dataset.dir > 0 ? 1.6 : 0.625), r.left + r.width / 2, r.top + r.height / 2); }
  else if (act === "copy") copy(t.dataset.text);
  else if (act === "retry") { v.error = null; if (t.dataset.what === "load") loadPair(); else start(); }
  else if (act === "lite") {
    v.lite = !v.lite; store.set(KEYS.lite, v.lite);
    renderChrome();
    if (pixelOf(v.half, true) !== pixelOf(v.half, false)) regrid(); else loadPair();
  }
  else if (act === "share") share();
  else if (act === "star") star();
  else if (act === "film") takeFilm();
  else if (act === "shot") presentPicture(renderShot, `ALFAQAAR-before-after-${v.lat.toFixed(3)}_${v.lon.toFixed(3)}.png`, titleOf(), "جارٍ تجهيز صورة المقارنة…");
  else if (act === "report") presentPicture(renderReport, `ALFAQAAR-report-${v.lat.toFixed(3)}_${v.lon.toFixed(3)}.png`, titleOf(), "جارٍ تجهيز التقرير…");
  else if (act === "print") printReport();
}
// «توفير البيانات» on a wide box changes the size of a point: the same dates are read again on the new grid
function regrid() {
  const v = V, sky = v.grid.sky;
  if (v.load) v.load.abort();
  v.grid = I.makeGrid(v.lat, v.lon, v.half, pointsOf(v.half, v.lite));
  v.grid.sky = sky;                                  // the sky was judged over the same box: nothing to check again
  Object.assign(v, {shown: {}, img: {before: new Map(), after: new Map()}, pair: null, job: null, working: "", hot: {before: [], after: []}, hotKey: {before: "", after: ""},
    zoom: 1, cx: 0.5, cy: 0.5, measure: null, area: null, probe: null});
  renderMeasure(); renderPixel();
  loadPair();
}
// The search field at the top of the app: any place, with or without a fire. Coordinates open the
// comparison at once; a name lists what was found, each to compare or to see on the map.
BA.find = function (text) {
  const q = String(text || "").trim();
  if (!q) return;
  const cur = decodeURIComponent(location.hash.slice(1));
  if (!cur.startsWith("ba")) from = cur;
  setRoute("ba");
  const box = el("#baQuery");
  if (box) box.value = q;
  search(q);
};
// show a place on the app's own map, with a pin that offers the comparison
let pinLayer = null;
BA.pin = function (lat, lon, name) {
  from = "";
  setRoute("");
  if (!S.map || !root.L) return;
  if (!pinLayer) pinLayer = root.L.layerGroup().addTo(S.map);
  pinLayer.clearLayers();
  const card = document.createElement("div");
  card.className = "ba-pin";
  card.innerHTML = `<b>${ltr(name || `${lat.toFixed(4)}, ${lon.toFixed(4)}`)}</b><small dir="ltr">${lat.toFixed(4)}, ${lon.toFixed(4)}</small>
    <button class="btn ba-btn primary" type="button">صور الأقمار: قبل وبعد</button>`;
  card.querySelector("button").onclick = () => BA.go(routeOf({lat, lon, half: 2, name: name || ""}));
  root.L.marker([lat, lon]).addTo(pinLayer).bindPopup(card, {closeButton: false}).openPopup();
  S.map.setView([lat, lon], 13, {animate: false});
};
// Sentinel-2 sees 10 m per point at best. For the finer detail of a place (buildings, tanks,
// vehicles) the app's map has Esri's high-detail picture (about 0.5 m in most of the region, a mosaic
// of older dates, its date not given): the place opens there, pinned, as near as the picture goes.
BA.hires = function (v) {
  const n = v.grid ? v.grid.size : 0, [lat, lon] = n ? I.gridPoint(v.grid, v.cx * n, v.cy * n) : [v.lat, v.lon];
  const name = v.title || "";
  BA.pin(lat, lon, name);
  if (typeof pickBase === "function") pickBase("sat");
  if (S.map) S.map.setView([lat, lon], 18, {animate: false});
  toast("صورة عالية التفاصيل (نحو نصف متر) من خريطة Esri، تاريخها غير محدد وقد تكون أقدم من صور القمر الأوروبي");
};
BA.init = function () {
  const s = sheet();
  if (!s || BA.ready) return;
  BA.ready = true;
  if (document.head && document.head.appendChild && !(document.getElementById && document.getElementById("ba-style"))) {
    const style = document.createElement("style");
    style.id = "ba-style";
    style.textContent = STYLE;
    document.head.appendChild(style);
  }
  const finder = el("#finder");
  if (finder) {
    finder.hidden = false;
    finder.addEventListener("submit", e => { e.preventDefault(); const q = el("#finderQ"); BA.find(q.value); if (q.blur) q.blur(); });
  }
  s.addEventListener("click", onClick);
  s.addEventListener("keydown", e => {                // the date cards are buttons for the keyboard too
    if ((e.key === "Enter" || e.key === " ") && e.target && e.target.dataset && e.target.dataset.act === "side") { e.preventDefault(); onClick(e); }
  });
  el("#baBtn").hidden = false;
  el("#baBtn").addEventListener("click", () => BA.go("ba"));
  const nav = el('#secNav a[data-sec="imagery"]');
  if (nav) nav.hidden = false;
  const home = el("#baHome");
  if (home && home.addEventListener) home.addEventListener("click", onClick);
  el("#pickCancel").addEventListener("click", BA.cancelPick);
  addEventListener("resize", BA.resize);
};

/* ------------------------------------------------------------------ the looks */
// Everything this screen needs, in the page's own colours (its CSS variables, dark and light), so
// it does not depend on rules in index.html. The picture's surroundings are always dark.
const STYLE = `
.ba-app,.ba-picktools,.ba-pin{--b:var(--bg,#0a101c);--s:var(--surface,#111a2b);--s2:var(--surface-2,#18233a);--l:var(--line,#24314c);--f:var(--fg,#e8edf6);--m:var(--muted,#93a0b8);--a:var(--accent,#ff7a1a);--g0:var(--ok,#3ddc97);--g1:var(--check,#f2c94c);--g2:var(--sure,#ff4d4f);--w:var(--likely,#ff9f1c);--ui:var(--font-ui,system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif);--dp:var(--font-display,var(--font-ui,system-ui,sans-serif))}
#baSheet{padding-bottom:0;overflow:hidden}
.ba-colors{display:inline-flex;gap:6px;align-items:center}
.ba-color{width:24px;height:24px;border-radius:50%;border:2px solid rgba(255,255,255,.35);background:var(--c);padding:0;cursor:pointer}
.ba-color[aria-pressed="true"]{border-color:#fff;box-shadow:0 0 0 2px var(--c)}
.ba-edges{position:absolute;inset:0;pointer-events:none}
.ba-edge{position:absolute;pointer-events:auto;border:0;background:rgba(5,8,13,.55);color:#fff;opacity:.55;transition:opacity .15s;cursor:pointer;padding:0}
.ba-edge:hover,.ba-edge:focus-visible{opacity:1}
.ba-edge::after{content:"";position:absolute;inset:0;margin:auto;width:10px;height:10px;border:solid #fff;border-width:0 2.5px 2.5px 0}
.ba-edge.n,.ba-edge.s{left:50%;width:54px;height:22px;margin-left:-27px}
.ba-edge.e,.ba-edge.w{top:50%;width:22px;height:54px;margin-top:-27px}
.ba-edge.n{top:0;border-radius:0 0 10px 10px}.ba-edge.n::after{transform:rotate(-135deg) translate(-2px,-2px)}
.ba-edge.s{bottom:0;border-radius:10px 10px 0 0}.ba-edge.s::after{transform:rotate(45deg) translate(-2px,-2px)}
.ba-edge.e{right:0;border-radius:10px 0 0 10px}.ba-edge.e::after{transform:rotate(-45deg) translate(-2px,-2px)}
.ba-edge.w{left:0;border-radius:0 10px 10px 0}.ba-edge.w::after{transform:rotate(135deg) translate(-2px,-2px)}
#baHome{padding:0;background:var(--panel,#0b111c)}
.ba-docked{height:auto;min-height:100%;background:transparent}
.ba-docked .ba-main{padding:0 16px 28px}
.ba-docked .ba-pick{max-width:none;padding-top:0}
.ba-docked .ba-ways{grid-template-columns:1fr;gap:8px}
.ba-docked .ba-way{grid-template-columns:auto minmax(0,1fr);grid-template-areas:"i b" "i s";align-content:center;row-gap:0;padding:12px 14px;
  transition:border-color .15s,background .15s}
.ba-docked .ba-way:hover{border-color:color-mix(in srgb,var(--a) 55%,var(--l));background:color-mix(in srgb,var(--a) 6%,var(--s))}
.ba-docked .ba-pick>.ba-note:first-child{display:none}
.ba-hero{position:relative;padding:20px 16px 16px;margin-bottom:14px;border-bottom:1px solid var(--l);
  background:radial-gradient(120% 140% at 100% 0%,color-mix(in srgb,var(--a) 16%,transparent),transparent 60%),linear-gradient(180deg,var(--s),transparent)}
.ba-kicker{display:inline-flex;align-items:center;gap:6px;color:var(--a);font-size:12px;font-weight:700;letter-spacing:.4px}
.ba-kicker svg{width:15px;height:15px}
.ba-hero .ba-title{display:block;font:800 21px/1.4 var(--dp);margin:4px 0 6px}
.ba-srcs{display:flex;flex-wrap:wrap;gap:6px}
.ba-srcs bdi{font:600 11.5px/1 var(--ui);color:var(--m);border:1px solid var(--l);border-radius:999px;padding:5px 9px;background:var(--s)}
.ba-tap{display:flex;gap:8px;align-items:flex-start;margin-top:12px;padding:10px 12px;border-radius:10px;font-size:13px;line-height:1.6;
  color:var(--f);background:color-mix(in srgb,var(--a) 10%,var(--s));border:1px solid color-mix(in srgb,var(--a) 35%,var(--l))}
.ba-tap svg{flex:none;width:18px;height:18px;color:var(--a);margin-top:1px}
body.ba-home .leaflet-container{cursor:crosshair}
.ba-app{display:flex;flex-direction:column;height:100%;min-height:0;background:var(--b);color:var(--f);font:15px/1.6 var(--ui);direction:rtl;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
.ba-app *,.ba-picktools *{box-sizing:border-box}
:where(.ba-app,.ba-picktools) button{font:inherit;color:inherit;cursor:pointer;-webkit-tap-highlight-color:transparent}
.ba-app [hidden],.ba-picktools[hidden]{display:none!important}
.ba-app :focus-visible,.ba-picktools :focus-visible{outline:2px solid var(--a);outline-offset:2px}
.ba-app svg,.ba-picktools svg{flex:none}
.ba-top{flex:none;display:flex;align-items:center;gap:10px;padding:8px max(12px,env(safe-area-inset-right,0px)) 8px max(12px,env(safe-area-inset-left,0px));border-bottom:1px solid var(--l);background:var(--b)}
.ba-head{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.35}
.ba-app .ba-title{flex:none;font:700 16px/1.4 var(--dp);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ba-head small{color:var(--m);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ba-main{flex:1;min-height:0;overflow-y:auto;-webkit-overflow-scrolling:touch;overscroll-behavior:contain}
.ba-btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:40px;padding:6px 12px;border:1px solid var(--l);border-radius:10px;background:var(--s);color:var(--f);font:inherit;font-size:14px;line-height:1.35;text-decoration:none;cursor:pointer}
.ba-btn svg{width:18px;height:18px}
.ba-btn.primary{background:var(--a);border-color:var(--a);color:#fff;font-weight:600}
.ba-btn[aria-pressed="true"]{background:var(--f);color:var(--b);border-color:var(--f)}
.ba-btn:disabled,.ba-pill:disabled{opacity:.45;cursor:default}
.ba-btn small{font-size:12px;opacity:.75}
.ba-full{width:100%}
.ba-seg{display:flex;border:1px solid var(--l);border-radius:10px;overflow:hidden;background:var(--s)}
.ba-seg button{flex:1;min-width:0;min-height:40px;display:inline-flex;align-items:center;justify-content:center;gap:6px;border:0;background:transparent;color:var(--m);padding:6px 6px;font-size:13.5px;line-height:1.3}
.ba-seg button+button{border-inline-start:1px solid var(--l)}
.ba-seg button[aria-pressed="true"]{background:var(--f);color:var(--b);font-weight:600}
.ba-seg svg{width:18px;height:18px}
.ba-pills{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none;margin-inline:-14px;padding:1px 14px}
.ba-pills::-webkit-scrollbar{display:none}
.ba-pill{flex:none;display:inline-flex;align-items:center;gap:6px;min-height:38px;padding:5px 12px;border:1px solid var(--l);border-radius:999px;background:var(--s);color:var(--f);font-size:13.5px;line-height:1.3;white-space:nowrap}
.ba-pill svg{width:15px;height:15px}
.ba-pill[aria-pressed="true"]{border-color:var(--a);background:color-mix(in srgb,var(--a) 20%,var(--s));font-weight:600}
.ba-sw{width:14px;height:14px;border-radius:4px;flex:none;border:1px solid rgba(128,128,128,.35)}
.ba-sw-color{background:linear-gradient(135deg,#6f8552,#cdb68c 55%,#4f83ad)}
.ba-sw-swir{background:linear-gradient(135deg,#3fa85a,#b0503a 55%,#ff9a3c)}
.ba-sw-veg{background:linear-gradient(135deg,#d2362f 50%,#8fa7b8 50%)}
.ba-sw-fire{background:linear-gradient(135deg,#ffcc33,#c0401f 55%,#5a1f18)}
.ba-sw-nbr{background:linear-gradient(to right,#a3221a,#e2dfd8,#1c8275)}
.ba-sw-ndvi{background:linear-gradient(to right,#7d8597,#ddd8cc,#2f9140)}
/* the picture */
.ba-stagecol{background:#05080d}
.ba-stagefit{display:flex;flex-direction:column;align-items:center}
.ba-app .ba-stage{position:relative;direction:ltr;width:min(100%,calc(100vh - 190px));width:min(100%,calc(100dvh - 190px));max-width:none;aspect-ratio:1;margin:0 auto;background:#05080d;overflow:hidden;touch-action:none;user-select:none;-webkit-user-select:none;-webkit-touch-callout:none}
.ba-app .ba-stage.side{aspect-ratio:5/7;width:min(100%,calc((100vh - 190px)*5/7));width:min(100%,calc((100dvh - 190px)*5/7))}
.ba-app .ba-stage canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
.ba-app .ba-tag{position:absolute;top:8px;z-index:2;direction:rtl;background:rgba(5,8,13,.8);color:#fff;border-radius:8px;padding:2px 9px;font-size:13px;font-weight:600;pointer-events:none;white-space:nowrap;border:1px solid rgba(255,255,255,.3)}
.ba-app .ba-tag.b{right:8px}
.ba-app .ba-tag.a{left:8px;border-color:#ff7a1a}
.ba-app .ba-handle{position:absolute;top:0;bottom:0;width:44px;margin-left:-22px;z-index:3;cursor:ew-resize;touch-action:none;outline:none}
.ba-app .ba-handle::before{content:"";position:absolute;top:0;bottom:0;left:21px;width:2px;background:#fff;box-shadow:0 0 0 1px rgba(0,0,0,.45)}
.ba-app .ba-handle i{position:absolute;top:50%;left:50%;width:38px;height:38px;margin:-19px;border-radius:50%;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.55);display:grid;place-items:center;color:#111;font:700 15px/1 system-ui,sans-serif;font-style:normal}
.ba-app .ba-handle i::before{content:"‹ ›"}
.ba-app .ba-handle:focus-visible i{box-shadow:0 0 0 3px #ff7a1a}
.ba-app .ba-scale{position:absolute;left:10px;bottom:10px;z-index:2;direction:rtl;color:#fff;font-size:12px;font-weight:600;text-shadow:0 1px 3px #000;pointer-events:none;text-align:center}
.ba-app .ba-scale i{display:block;height:6px;margin-top:2px;border:2px solid #fff;border-top:0;box-shadow:0 1px 2px rgba(0,0,0,.6)}
.ba-app .ba-q{position:absolute;right:10px;bottom:10px;z-index:2;direction:rtl;background:rgba(5,8,13,.8);color:#c9d3e3;border-radius:8px;padding:2px 8px;font-size:12px;pointer-events:none}
.ba-app .ba-veil{position:absolute;inset:0;z-index:4;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;direction:rtl;text-align:center;padding:20px;color:#e8edf6;background:rgba(5,8,13,.78);font-size:14px;line-height:1.7}
.ba-app .ba-veil p{margin:0;max-width:420px}
.ba-app .ba-veil.soft{inset:auto 0 36px 0;flex-direction:row;gap:8px;background:none;pointer-events:none;padding:0 12px}
.ba-app .ba-veil.soft p{background:rgba(5,8,13,.84);border-radius:999px;padding:3px 12px;font-size:12.5px}
.ba-spin{width:34px;height:34px;flex:none;border-radius:50%;border:3px solid rgba(255,255,255,.18);border-top-color:#ff7a1a;animation:ba-spin 1s linear infinite}
.ba-veil.soft .ba-spin{width:20px;height:20px;border-width:2px}
@keyframes ba-spin{to{transform:rotate(360deg)}}
@keyframes ba-pulse{50%{opacity:.25}}
@media (prefers-reduced-motion:reduce){.ba-spin,.ba-day .dot{animation:none!important}}
.ba-tools{position:absolute;left:8px;top:50%;transform:translateY(-50%);z-index:3;display:flex;flex-direction:column;gap:6px}
.ba-app .ba-tool{width:40px;height:40px;display:grid;place-items:center;padding:0;border-radius:10px;border:1px solid rgba(255,255,255,.28);background:rgba(5,8,13,.72);color:#fff}
.ba-tool[aria-pressed="true"]{background:#fff;color:#10151f;border-color:#fff}
.ba-zoom{display:none}
@media (hover:hover) and (pointer:fine){.ba-zoom{display:grid}}
.ba-app .ba-legend{width:100%;display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:4px 14px;min-height:34px;padding:6px 12px;background:#0b111c;color:#c9d3e3;border-top:1px solid rgba(255,255,255,.08);font-size:12.5px;line-height:1.5;direction:rtl}
.ba-legend span{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.ba-legend i{display:inline-block;width:11px;height:11px;border-radius:3px;flex:none}
.ba-legend i.ring{background:none;border:2px solid #ffd60a;border-radius:50%}
.ba-legend i.hatch{background:repeating-linear-gradient(45deg,#cdd4e0 0 2px,transparent 2px 5px)}
.ba-legend .ba-lgk{color:#fff;font-weight:600}
.ba-ramp{display:flex;align-items:center;gap:8px;direction:ltr}
.ba-ramp .bar{position:relative;width:150px;height:25px;flex:none}
.ba-ramp .bar i{position:absolute;left:0;right:0;top:0;width:auto;height:10px;border-radius:3px}
.ba-ramp .bar u{position:absolute;top:11px;transform:translateX(-50%);text-decoration:none;font-size:10.5px;color:#93a0b8}
/* beside or under the picture */
.ba-panel{display:flex;flex-direction:column;gap:12px;padding:12px max(14px,env(safe-area-inset-right,0px)) calc(28px + env(safe-area-inset-bottom,0px)) max(14px,env(safe-area-inset-left,0px))}
.ba-panel>*{flex:none}
.ba-views button{flex-direction:column;gap:2px;padding:6px 2px;font-size:12.5px}
.ba-layers{display:flex;flex-direction:column;gap:8px}
.ba-modenote{margin:0;color:var(--m);font-size:12.5px;line-height:1.6}
.ba-readout{display:flex;flex-wrap:wrap;align-items:center;gap:8px;background:var(--s);border:1px solid var(--a);border-radius:12px;padding:8px 10px;font-size:14px;color:var(--m)}
.ba-readout b{color:var(--f);font-size:15px}
.ba-readout .ba-ro-k{font:700 13px/1.4 var(--dp);color:var(--a)}
.ba-ro-note{width:100%;color:var(--m);font-size:12px}
.ba-probe{width:100%;border-collapse:collapse;font-size:13px}
.ba-probe th{text-align:start;font-weight:500;color:var(--m);padding:3px 0}
.ba-probe td{text-align:end;padding:3px 0 3px 2px;color:var(--f);min-width:64px}
.ba-probe thead th,.ba-probe thead td{font-weight:600;color:var(--m);border-bottom:1px solid var(--l)}
.ba-slots{display:flex;gap:8px}
.ba-slot{flex:1;min-width:0;display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"k st" "d d";gap:2px 6px;align-items:center;background:var(--s);border:1.5px solid var(--l);border-radius:12px;padding:8px 10px;cursor:pointer}
.ba-slot.on{border-color:var(--f)}
.ba-slot.after.on{border-color:var(--a)}
.ba-slot .k{grid-area:k;font:700 15px/1.3 var(--dp)}
.ba-slot.after .k{color:var(--a)}
.ba-slot .d{grid-area:d;min-width:0}
.ba-slot .d b{display:block;font-size:14.5px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ba-slot .d small{display:block;color:var(--m);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ba-slot .st{grid-area:st;display:flex;gap:4px}
.ba-mini{width:36px;height:36px;display:grid;place-items:center;padding:0;border:1px solid var(--l);border-radius:9px;background:var(--b)}
.ba-mini svg{width:16px;height:16px}
.ba-quick{margin-top:8px}
.ba-striphead{display:flex;align-items:center;flex-wrap:wrap;gap:4px 8px;margin-bottom:2px;color:var(--m);font-size:13px}
.ba-striphead b{color:var(--f);font:700 14px/1.4 var(--dp)}
.ba-keys{display:inline-flex;align-items:center;margin-inline-start:auto;font-size:11.5px}
.ba-keys i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-inline:8px 4px}
.ba-keys .g0,.ba-day.g0 .dot{background:var(--g0)}
.ba-keys .g1,.ba-day.g1 .dot{background:var(--g1)}
.ba-keys .g2,.ba-day.g2 .dot{background:var(--g2)}
.ba-link{border:0;background:none;color:var(--a)!important;font-size:13px;padding:6px 2px;text-decoration:underline}
.ba-days{display:flex;gap:6px;overflow-x:auto;padding:11px 1px 8px;scrollbar-width:thin}
.ba-days.open{flex-wrap:wrap;overflow:visible}
.ba-day{position:relative;flex:none;width:62px;min-height:66px;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:6px 2px 5px;border:1.5px solid var(--l);border-radius:12px;background:var(--s);line-height:1.25}
.ba-day b{font:700 17px/1.2 var(--dp)}
.ba-day span{font-size:10.5px;color:var(--m);white-space:nowrap}
.ba-day small{font-size:10.5px;color:var(--m)}
.ba-day .dot{position:absolute;top:6px;inset-inline-start:6px;width:8px;height:8px;border-radius:50%;background:var(--l)}
.ba-day.g3 .dot{background:var(--m);animation:ba-pulse 1.2s infinite}
.ba-day.g4 .dot{background:none;border:1.5px solid var(--m)}
.ba-day.dim{opacity:.5}
.ba-day.before{opacity:1;border-color:var(--f)}
.ba-day.after{opacity:1;border-color:var(--a);background:color-mix(in srgb,var(--a) 16%,var(--s))}
.ba-day em{position:absolute;top:-10px;inset-inline-end:5px;font-style:normal;font-size:10.5px;font-weight:700;line-height:1.5;padding:0 6px;border-radius:999px;background:var(--f);color:var(--b)}
.ba-day.after em{background:var(--a);color:#fff}
.ba-year{flex:none;display:grid;place-items:center;padding:0 2px;font:700 12px/1 var(--dp);color:var(--m)}
.ba-days.open .ba-year{flex-basis:100%;place-items:start;padding:2px 0}
.ba-more{flex:none;min-width:62px;min-height:66px;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:4px 8px;border:1.5px dashed var(--l);border-radius:12px;background:none;font-size:13px;line-height:1.3}
.ba-more small{font-size:10.5px;color:var(--m)}
.ba-app .ba-find{display:flex;flex-direction:column;align-items:flex-start;gap:8px;background:var(--s);border:1px solid var(--l);border-radius:14px;padding:12px}
.ba-h{margin:0;font:700 13px/1.4 var(--dp);color:var(--m)}
.ba-app .ba-find .big{font:800 19px/1.5 var(--dp)}
.ba-app .ba-find p{margin:0;color:var(--m);font-size:14px}
.ba-app .ba-find p b{color:var(--f)}
.ba-find p b svg{width:16px;height:16px;vertical-align:-3px;color:var(--w)}
.ba-app .ba-find .warn{color:var(--w)}
.ba-app .ba-find .lg{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13.5px;color:var(--m)}
.ba-app .ba-find .lg i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-inline-end:5px}
.ba-burn{width:100%;border-top:1px solid var(--l);padding-top:8px;font-size:14px}
.ba-burn .lg{margin-top:4px}
.ba-burn small{display:block;margin-top:4px;color:var(--m);font-size:12.5px}
.ba-facts{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 10px;width:100%;margin:0;border-top:1px solid var(--l);padding-top:8px;font-size:13.5px}
.ba-facts dt{color:var(--m)}
.ba-facts dd{margin:0;min-width:0}
.ba-caveats{margin:0;padding-inline-start:18px;color:var(--m);font-size:12.5px;line-height:1.7}
.ba-acts,.ba-actions{display:flex;flex-wrap:wrap;gap:8px}
.ba-boxrow{display:flex;flex-direction:column;gap:8px}
.ba-app .ba-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px 10px;font-size:14px;color:var(--m)}
.ba-app .ba-row:empty{display:none}
.ba-app .ba-row b{color:var(--f);font-size:14px}
.ba-row .ba-seg{flex:1;min-width:230px}
.ba-app .ba-row input[type=date]{font:inherit;font-size:15px;color:var(--f);background:var(--s);border:1px solid var(--l);border-radius:10px;padding:6px 8px;min-height:40px;color-scheme:inherit}
.ba-help{color:var(--m);font-size:13px;line-height:1.8;border-top:1px solid var(--l);padding-top:12px}
.ba-help b{color:var(--f)}
/* choosing a place */
.ba-app .ba-pick{max-width:680px;margin:0 auto;padding:14px max(14px,env(safe-area-inset-right,0px)) calc(28px + env(safe-area-inset-bottom,0px)) max(14px,env(safe-area-inset-left,0px))}
.ba-note{margin:0;padding:12px 14px;border-radius:14px;background:var(--s2);color:var(--m);font-size:14px;line-height:1.7}
.ba-ways{display:grid;grid-template-columns:1fr;gap:8px;margin:12px 0}
.ba-way{display:grid;grid-template-columns:auto minmax(0,1fr);grid-template-areas:"i b" "i s";column-gap:12px;align-items:center;min-height:64px;padding:10px 14px;border:1px solid var(--l);border-radius:14px;background:var(--s);text-align:start}
.ba-way svg{grid-area:i;width:26px;height:26px;color:var(--a)}
.ba-way b{grid-area:b;font:700 15.5px/1.4 var(--dp)}
.ba-way small{grid-area:s;color:var(--m);font-size:12.5px;line-height:1.5}
.ba-app .ba-pick h3{font:700 16px/1.5 var(--dp);margin:18px 0 8px}
.ba-app .ba-search{display:flex;gap:8px;margin:12px 0}
.ba-app .ba-search input{flex:1;min-width:0;font:inherit;font-size:16px;color:var(--f);background:var(--s);border:1px solid var(--l);border-radius:10px;padding:10px 12px}
.ba-app .ba-place{display:flex;align-items:center;gap:8px;background:var(--s);border:1px solid var(--l);border-radius:12px;padding:4px 10px 4px 4px;margin-bottom:8px}
.ba-app .ba-place .t{flex:1;min-width:0;border:0;background:none;text-align:start;padding:6px 4px}
.ba-app .ba-place b{display:block;font-size:15px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ba-app .ba-place small{color:var(--m);font-size:12.5px;direction:ltr;unicode-bidi:isolate}
.ba-app .ba-place .new{display:block;color:var(--m);font-size:12.5px}
.ba-app .ba-place .new.fresh{color:var(--g0);font-weight:600}
.ba-place .ba-btn{min-height:36px;padding:4px 9px;font-size:12.5px}
.ba-pin{display:flex;flex-direction:column;gap:6px;direction:rtl;min-width:170px;font:14px var(--ui)}
.ba-pin small{color:#667}
/* on the map while choosing */
.ba-picktools{position:fixed;z-index:1600;left:0;right:0;bottom:0;display:flex;flex-direction:column;gap:8px;direction:rtl;padding:12px max(14px,env(safe-area-inset-right,0px)) calc(12px + env(safe-area-inset-bottom,0px)) max(14px,env(safe-area-inset-left,0px));background:var(--b);color:var(--f);border-top:1px solid var(--l);border-radius:18px 18px 0 0;box-shadow:0 -10px 30px rgba(0,0,0,.35);font:15px/1.5 var(--ui)}
.ba-pt-row{display:flex;align-items:center;gap:8px}
.ba-pt-row .ba-seg{flex:1}
.ba-pt-size{margin:0;min-height:1.5em;color:var(--m);font-size:13.5px}
.ba-corner{width:34px;height:34px;border-radius:50%;background:#fff;border:4px solid #ff7a1a;box-shadow:0 2px 8px rgba(0,0,0,.5);cursor:grab;touch-action:none}
.leaflet-container.ba-drawing,.leaflet-container.ba-drawing .leaflet-interactive{cursor:crosshair!important}
body.picking .leaflet-bottom{bottom:var(--ba-pick-h,0px)}
body.locked .ba-picktools{display:none!important}
#baPrint{display:none}
@media (min-width:640px){
  .ba-ways{grid-template-columns:repeat(3,1fr)}
  .ba-way{grid-template-columns:1fr;grid-template-areas:"i" "b" "s";row-gap:4px;align-content:start;padding:14px}
}
@media (min-width:900px){
  .ba-app .ba-main:not(.ba-plain){display:grid;grid-template-columns:440px minmax(0,1fr);grid-template-rows:minmax(0,1fr);overflow:hidden}
  .ba-app .ba-panel{order:1;min-height:0;overflow-y:auto;border-inline-end:1px solid var(--l);padding:14px 16px 28px}
  .ba-app .ba-stagecol{order:2;min-width:0;min-height:0;display:flex;align-items:center;justify-content:center;container-type:size}
  .ba-app .ba-stagefit{width:100%;max-height:100%;justify-content:center}
  .ba-app .ba-stage{width:min(100%,calc(100vh - 112px))}
  .ba-app .ba-stage.side{aspect-ratio:2;width:min(100%,calc((100vh - 112px)*2))}
  @supports (width:1cqw){
    .ba-app .ba-stage{width:min(100cqw,100cqh - 36px)}
    .ba-app .ba-stage.side{width:min(100cqw,(100cqh - 36px)*2)}
  }
  .ba-pills{flex-wrap:wrap;overflow:visible;margin-inline:0;padding-inline:1px}
  .ba-picktools{left:auto;right:16px;bottom:16px;width:400px;border:1px solid var(--l);border-radius:16px;box-shadow:0 10px 30px rgba(0,0,0,.35)}
  body.picking .leaflet-bottom{bottom:0}
}
@media print{
  body.ba-printing>*:not(#baPrint){display:none!important}
  body.ba-printing,html:has(body.ba-printing){background:#fff!important;height:auto!important;overflow:visible!important}
  body.ba-printing #baPrint{display:block}
  @page{size:A4 portrait;margin:11mm}
  .bp{direction:rtl;color:#141b2b;font:10.5pt/1.55 var(--font-ui,sans-serif);-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .bp *{box-sizing:border-box}
  .bp header{display:flex;align-items:center;justify-content:space-between;background:#0a101c;color:#fff;padding:9px 14px;border-bottom:3px solid #e5600a}
  .bp-latin{display:flex;align-items:center;gap:8px;font:700 17pt "Chakra Petch",ui-monospace,sans-serif;letter-spacing:1px}
  .bp-latin img{width:32px;height:32px}
  .bp-latin span{color:#40d6ec;font-weight:500}
  .bp-name{text-align:start;line-height:1.3}
  .bp-name b{display:block;font:800 17pt var(--font-display,sans-serif)}
  .bp-name small{color:#c9d3e3;font-size:9pt}
  .bp h1{font:800 18pt/1.4 var(--font-display,sans-serif);margin:10px 0 2px}
  .bp h2{font:800 12.5pt/1.4 var(--font-display,sans-serif);margin:10px 0 6px;border-top:1px solid #d9dfea;padding-top:7px}
  .bp-sub{margin:0 0 8px;color:#5b6780;font-size:9.5pt}
  .bp-pics{display:grid;gap:9px;justify-content:center}
  .bp-pics.n2{grid-template-columns:37% 37%}
  .bp-pics.n1{grid-template-columns:52%}
  .bp figure{margin:0;break-inside:avoid}
  .bp-pic{position:relative;aspect-ratio:1;background:#05080d}
  .bp-pic img{display:block;width:100%;height:100%}
  .bp-pic span{position:absolute;top:5px;inset-inline-start:5px;background:rgba(5,8,13,.82);color:#fff;font-weight:700;font-size:9.5pt;padding:0 8px}
  .bp figcaption{display:flex;flex-direction:column;margin-top:3px;font-size:9pt;line-height:1.5}
  .bp figcaption b{font:700 10.5pt var(--font-display,sans-serif)}
  .bp figcaption span{color:#3c475c}
  .bp figcaption i{display:inline-block;width:9px;height:9px;margin-inline-end:5px}
  .bp figcaption strong{color:#c94f00;font:800 11pt var(--font-display,sans-serif)}
  .bp figcaption small{color:#5b6780}
  .bp-layers{display:grid;grid-template-columns:1fr 1fr;gap:9px}
  .bp-layers figure{display:grid;grid-template-columns:40% 1fr;gap:9px;align-items:start}
  .bp-layers figcaption{margin-top:0}
  .bp-list{margin:0;padding-inline-start:18px;font-size:9.5pt}
  .bp-notes{background:#f3f5f9;border-inline-start:3px solid #e5600a;padding:7px 12px;margin-top:9px;font-size:9pt;break-inside:avoid}
  .bp-notes ul{margin:3px 0 0;padding-inline-start:18px}
  .bp footer{display:flex;flex-direction:column;margin-top:9px;border-top:1px solid #d9dfea;padding-top:5px;color:#5b6780;font-size:8pt}
}`;
})(typeof self !== "undefined" ? self : globalThis);
