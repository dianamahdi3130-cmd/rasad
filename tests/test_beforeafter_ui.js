"use strict";
// The «قبل وبعد» screen (web/beforeafter.js) and its hooks in the app: the real scripts run in a
// stand-in page against the made-up archive, without a browser and without the network.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const BA = require("../web/beforeafter.js");
const {makeArchive} = require("./support/archive.js");

const web = name => fs.readFileSync(path.join(__dirname, "../web", name), "utf8");
const DAY = 864e5;
const ago = days => new Date(Date.now() - days * DAY).toISOString().slice(0, 10);
const H = BA._;

/* ---------- plain helpers ---------- */
test("links: a place, its box and its two dates survive the round trip", () => {
  const v = {lat: 26.23456, lon: 50.83111, half: 3, before: "2026-10-30", after: "2026-11-07", name: "مصفاة رأس تنورة; الخزانات"};
  const h = H.routeOf(v);
  assert.equal(h, "ba:26.23456,50.83111,3,20261030,20261107;مصفاة رأس تنورة; الخزانات");
  assert.deepEqual(H.parseRoute(h), {view: "viewer", ...v});
  assert.deepEqual(H.parseRoute("ba:26.2,50.8,7"), {view: "viewer", lat: 26.2, lon: 50.8, half: 2, before: null, after: null, name: ""});
  assert.deepEqual(H.parseRoute("ba"), {view: "chooser"});
  assert.deepEqual(H.parseRoute("ba:i:20261108-0740_14.793_33.326"), {view: "incident", id: "20261108-0740_14.793_33.326"});
  assert.deepEqual(H.parseRoute("ba:hello"), {view: "chooser"});
  assert.equal(H.parseRoute("20261108-0740_14.793_33.326"), null, "an incident's own route is left to the app");
  assert.equal(H.parseRoute("bahrain"), null);
});

test("a typed place: coordinates in any common form, otherwise a name to search", () => {
  assert.deepEqual(H.parsePlace("26.23, 50.83"), {lat: 26.23, lon: 50.83});
  assert.deepEqual(H.parsePlace(" 26.23 50.83 "), {lat: 26.23, lon: 50.83});
  assert.deepEqual(H.parsePlace("٢٦٫٢٣، ٥٠٫٨٣"), {lat: 26.23, lon: 50.83});
  assert.deepEqual(H.parsePlace("https://www.google.com/maps/@30.6129,47.335,14z"), {lat: 30.6129, lon: 47.335});
  assert.deepEqual(H.parsePlace("https://maps.google.com/?q=-4.5,39.25"), {lat: -4.5, lon: 39.25});
  assert.equal(H.parsePlace("مطار البصرة"), null);
  assert.equal(H.parsePlace("شارع 14 منزل 25"), null);
  assert.equal(H.parsePlace("95, 50"), null);
});

test("wording: sky, area, scale bar and failures", () => {
  const clear = {cover: 1, sky: {cloud: 0, shadow: 0, cirrus: 0, nodata: 0, hidden: 0}};
  assert.equal(H.skyText(clear), "صافية");
  assert.equal(H.skyText({cover: 1, sky: {cloud: 0.3, shadow: 0.05, cirrus: 0, nodata: 0, hidden: 0.35}}), "غيوم 35%");
  assert.equal(H.skyText({cover: 0.5, sky: {cloud: 0, shadow: 0, cirrus: 0, nodata: 0.5, hidden: 0.5}}), "تغطي جزءاً من المربع");
  assert.equal(H.skyText({cover: 1, sky: null}), "تُفحص…");
  assert.equal(H.skyText({cover: 1, sky: null, failed: true}), "تعذّر فحصها");
  assert.equal(H.areaText(0.25), "2,500 م²");
  assert.equal(H.areaText(2.53), "2.5 هكتار");
  assert.equal(H.areaText(250), "2.50 كم²");
  assert.deepEqual(H.scaleBar(10, 110), {metres: 1000, px: 100, label: "1 كم"});
  assert.equal(H.scaleBar(2.5, 110).label, "200 م");
  assert.match(H.failText({code: "offline"}), /لا اتصال/);
  assert.match(H.failText({code: "timeout"}), /بطيء/);
  assert.match(H.failText({code: "source"}), /لا يستجيب/);
  assert.equal(H.dateAr(Date.parse("2026-11-07T21:30:00Z")), "8 تشرين الثاني 2026", "dates are Baghdad dates");
});

test("the default pair is settled as soon as the nearer passes are checked, never before", () => {
  const sky = hidden => ({cloud: hidden, shadow: 0, cirrus: 0, nodata: 0, hidden});
  const pass = (date, hidden) => ({date, time: Date.parse(date + "T07:22:00Z"), cover: 1, sky: hidden == null ? null : sky(hidden)});
  const event = {start: "2026-10-28T10:00:00Z", end: "2026-10-29T02:00:00Z"};
  // newest first, as the engine lists them
  const passes = [pass("2026-11-09", null), pass("2026-11-04", 0.6), pass("2026-11-02", null), pass("2026-10-25", 0), pass("2026-10-20", null)];
  let c = H.choose(passes, event);
  assert.equal(c.before.pass.date, "2026-10-25"); assert.equal(c.before.done, true, "the nearest pass before is clear: settled");
  assert.equal(c.after.done, false, "2 Nov is nearer than anything checked: wait for it");
  passes[2].sky = sky(0.9);
  assert.equal(H.choose(passes, event).after.done, false, "9 Nov is still unknown");
  passes[0].sky = sky(0);
  c = H.choose(passes, event);
  assert.equal(c.after.pass.date, "2026-11-09"); assert.equal(c.after.done, true);
  // without an event: newest clear and the clear one before it
  c = H.choose(passes, null);
  assert.equal(c.after.pass.date, "2026-11-09"); assert.equal(c.before.pass.date, "2026-10-25");
  // a pass that could not be checked is skipped, not waited for
  assert.equal(H.settle([{...pass("2026-11-09", null), failed: true}, pass("2026-11-04", 0)]).pass.date, "2026-11-04");
  // nothing clear: a partly clear pass is offered
  assert.equal(H.settle([pass("2026-11-09", 0.9), pass("2026-11-04", 0.2)]).pass.date, "2026-11-04");
  assert.deepEqual(H.settle([pass("2026-11-09", 0.9)]), {pass: null, done: true});
  assert.deepEqual(H.checkOrder(passes, event).map(p => p.date).slice(0, 2), ["2026-10-25", "2026-11-02"], "nearest to the event are checked first");
});

test("passes are listed around the event or the dates of a link, never the whole archive", () => {
  const now = Date.parse("2026-11-10T12:00:00Z"), days = w => w.map(x => x.map(t => new Date(t).toISOString().slice(0, 10)));
  assert.deepEqual(days(H.windowsFor(now, null, null)), [["2026-07-13", "2026-11-10"]], "no event: the last four months");
  assert.deepEqual(days(H.windowsFor(now, {start: "2026-11-08T07:40:00Z", end: "2026-11-08T11:59:00Z"}, null)), [["2026-08-25", "2026-11-10"]], "not into the future");
  assert.deepEqual(days(H.windowsFor(now, null, {before: "2020-07-24", after: "2020-08-08"})), [["2020-06-24", "2020-09-08"]], "two near dates: one stretch");
  assert.deepEqual(days(H.windowsFor(now, null, {before: "2020-07-24", after: "2026-11-01"})), [["2020-06-24", "2020-08-24"], ["2026-10-02", "2026-11-10"]], "years apart: two short stretches");
  assert.deepEqual(days(H.joinWindows([[now - 20 * DAY, now - 10 * DAY], [now - 140 * DAY, now - 20 * DAY]], now)), [["2026-06-23", "2026-10-31"]]);
  // an event known by its day only (Baghdad time): that whole day is the event
  const e = H.eventOfDay("2020-08-04");
  assert.equal(e.start, "2020-08-03T21:00:00.000Z"); assert.equal(e.end, "2020-08-04T21:00:00.000Z");
  assert.equal(H.eventOfDay("nonsense"), null);
  assert.equal(H.distanceText(240.4), "240 م"); assert.equal(H.distanceText(2345), "2.35 كم");
});

test("the film's script: before, the divider sweeps to after, the change is marked, and back to before", () => {
  const film = H.filmPlan(true), short = H.filmPlan(false);
  assert.ok(film.duration > 9 && film.duration < 11, "about ten seconds");
  assert.ok(short.duration < film.duration - 3, "without a change to show, that part is left out");
  assert.deepEqual(film.at(0), {phase: "before", split: 0, change: 0});
  assert.deepEqual(film.at(1.0), {phase: "before", split: 0, change: 0}, "«قبل» is held first");
  const sweep = [1.5, 2.0, 2.4, 2.8, 3.3].map(t => film.at(t).split);
  assert.ok(sweep.every((s, i) => i === 0 || s > sweep[i - 1]), "the divider only moves forward");
  assert.equal(film.at(2.4).split, 0.5, "half way at the middle of the sweep");
  assert.deepEqual(film.at(4.0), {phase: "after", split: 1, change: 0});
  assert.equal(film.at(6.5).change, 1, "the change is drawn fully while it is held");
  assert.ok(film.at(5.0).change > 0 && film.at(5.0).change < 1, "and fades in");
  assert.deepEqual(film.at(film.duration), {phase: "end", split: 0, change: 0}, "it ends where it began: it loops without a jump");
  assert.deepEqual(film.at(99), film.at(film.duration));
  for (let t = 0; t <= short.duration; t += 0.1) assert.equal(short.at(t).change, 0);
  assert.equal(H.filmType(), null, "no recorder here: the app then says so instead of failing");
});

test("flames already there before are told apart from new ones", () => {
  const after = [{lat: 30.61, lon: 47.33}, {lat: 30.65, lon: 47.36}], before = [{lat: 30.6101, lon: 47.3301}];
  assert.deepEqual(H.splitHot(after, before), {fresh: 1, old: 1});
  assert.equal(after[0].old, true); assert.equal(after[1].old, false);
});

/* ---------- the app's side ---------- */
test("the app loads both scripts, with one version shared by the page and the offline cache", () => {
  const html = web("index.html"), sw = web("sw.js");
  const tags = [...html.matchAll(/<script src="(imagery|beforeafter)\.js\?v=(\d+)"><\/script>/g)];
  assert.deepEqual(tags.map(t => t[1]), ["imagery", "beforeafter"]);
  const version = tags[0][2];
  assert.equal(tags[1][2], version);
  assert.ok(sw.includes(`"imagery.js?v=${version}"`) && sw.includes(`"beforeafter.js?v=${version}"`), "the offline cache holds the same versions");
  assert.ok(sw.includes(`const CACHE = "firms-v${version}"`), "and the cache name moves with them");
  // the page knows its own version, to tell when the site has a newer one (an installed app stays open for days)
  assert.ok(html.includes(`const APP_V = ${version};`), "APP_V in index.html is the same number");
  const newer = new Function("APP_V", "return " + /function newerVersion\(swText\)\{[^\n]*\}/.exec(html)[0])(+version);
  assert.equal(newer(sw), false, "this page is the current one");
  assert.equal(newer(`const CACHE = "firms-v${+version + 1}";`), true);
  assert.equal(newer("something else"), false);
  for (const id of ["finder", "finderQ", "update", "updateBtn"]) assert.ok(html.includes(`id="${id}"`), id);
  assert.ok(html.indexOf("beforeafter.js") < html.indexOf("const S ="), "loaded before the app starts");
  for (const id of ["baBtn", "baSheet", "pickBar", "pickCancel"]) assert.ok(html.includes(`id="${id}"`), id);
  for (const file of ["imagery.js", "beforeafter.js"]) assert.ok(!web(file).includes("الخليج العربي"));
});

test("an incident opened from a link is not pulled back to the overview by a map animation", () => {
  // Leaflet finishes a running zoom animation over a later setView: the overview must not animate
  const draw = web("index.html").split("function drawMarkers()")[1].split("\n}\n")[0];
  const fits = [...draw.matchAll(/fitBounds\(.*\);/g)].map(m => m[0]);
  assert.equal(fits.length, 2);
  for (const f of fits) assert.match(f, /animate:false/);
});

function appPage(items, ba) {
  const html = web("index.html");
  const inline = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(s => s.includes("const S ="));
  const elements = new Map(), body = new Set();
  const context = vm.createContext({
    document: {
      querySelector(sel) { if (!elements.has(sel)) elements.set(sel, {innerHTML: "", hidden: false}); return elements.get(sel); },
      body: {classList: {add: c => body.add(c), remove: c => body.delete(c)}},
      documentElement: {style: {setProperty() {}}},
    },
    localStorage: {getItem() { return null; }},
    location: {hostname: "localhost", pathname: "/", hash: "", search: ""},
    history: {pushState(a, b, url) { context.location.hash = url.includes("#") ? url.slice(url.indexOf("#")) : ""; }, replaceState() {}},
    URLSearchParams, BA: ba,
  });
  context.window = context;
  vm.runInContext(inline.split("/* ---------- events ---------- */")[0]
    + "\nglobalThis.app = {S, route, setRoute, showFocus, openDetail, dailyDefault, dailyStep, dailyUrl, dailyLabel};", context);
  context.app.S.items = items;
  return {app: context.app, elements, context};
}
const incident = {id: "20261108-0740_14.793_33.326", headline: "حريق جديد في السودان", where: "", lat: 14.79, lon: 33.33, first: "2026-11-08T07:40:00Z",
  last: "2026-11-08T11:59:00Z", verdict: "مؤكد", reason: "", best: "polar", place: "land", start_text: "", last_text: ""};

test("an incident carries the «قبل وبعد» button, and its route goes to the viewer", () => {
  const seen = [];
  const ba = {available: true, route: h => { seen.push(h); return h.startsWith("ba"); }, leave: () => seen.push("leave"), go: h => seen.push("go " + h)};
  const {app, elements} = appPage([incident], ba);
  app.showFocus(incident.id);
  assert.match(elements.get("#focusCard").innerHTML, /id="fBA">🛰 قبل وبعد</);
  elements.get("#fBA").onclick();
  app.openDetail(incident.id);
  assert.match(elements.get("#detail").innerHTML, /id="dBA">🛰 قبل وبعد</);
  elements.get("#dBA").onclick();
  assert.deepEqual(seen.filter(s => s.startsWith("go")), ["go ba:i:" + incident.id, "go ba:i:" + incident.id]);
  seen.length = 0;
  app.setRoute("ba:26.2,50.8,2");
  assert.deepEqual(seen, ["ba:26.2,50.8,2"], "the viewer's routes are handed to it");
  app.setRoute(incident.id);
  assert.deepEqual(seen.slice(1), [incident.id, "leave"], "any other screen closes it");
});

test("NASA's daily picture: the newest one that is published by now, a day at a time, never the future", () => {
  const {app} = appPage([], undefined), at = s => Date.parse(s);
  assert.deepEqual({...app.dailyDefault(at("2026-11-11T15:00:00Z"))}, {date: "2026-11-11", pass: "pm"});
  assert.deepEqual({...app.dailyDefault(at("2026-11-11T11:00:00Z"))}, {date: "2026-11-11", pass: "am"}, "before the afternoon pass is out: the morning one");
  assert.deepEqual({...app.dailyDefault(at("2026-11-11T01:30:00Z"))}, {date: "2026-11-10", pass: "pm"}, "at night: yesterday afternoon");
  const d = {date: "2026-11-10", pass: "pm"}, now = at("2026-11-11T01:30:00Z");
  assert.deepEqual({...app.dailyStep(d, -1, now)}, {date: "2026-11-09", pass: "pm"});
  assert.deepEqual({...app.dailyStep(d, 1, now)}, {date: "2026-11-11", pass: "pm"});
  assert.deepEqual({...app.dailyStep({date: "2026-11-11", pass: "am"}, 1, now)}, {date: "2026-11-11", pass: "am"}, "not past today");
  assert.equal(app.dailyUrl(d), "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_NOAA20_CorrectedReflectance_TrueColor/default/2026-11-10/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg");
  assert.match(app.dailyUrl({date: "2026-11-10", pass: "am"}), /MODIS_Terra_CorrectedReflectance_TrueColor\/default\/2026-11-10\//);
  assert.equal(app.dailyLabel(d), "10 تشرين الثاني");
});

test("without the imagery scripts the app works as before, with no button", () => {
  const {app, elements} = appPage([incident], undefined);
  app.showFocus(incident.id);
  assert.doesNotMatch(elements.get("#focusCard").innerHTML, /قبل وبعد/);
  app.setRoute("ba:26.2,50.8,2");       // an old link: ignored, back to the list
  assert.equal(elements.get("#sheet").hidden, true);
});

/* ---------- the screen itself, in a stand-in page ---------- */
// A stand-in for Leaflet, enough for drawing an area: the map is 400 x 400 screen points over
// 0.1 degree each way around `at`, north up.
function fakeLeaflet(at, mapHandlers) {
  const span = 0.1, toLatLng = pt => ({lat: at[0] + span / 2 - pt.y / 400 * span, lng: at[1] - span / 2 + pt.x / 400 * span});
  const bounds = (a, b) => { const s = Math.min(a.lat, b.lat), n = Math.max(a.lat, b.lat), w = Math.min(a.lng, b.lng), e = Math.max(a.lng, b.lng);
    return {getSouth: () => s, getNorth: () => n, getWest: () => w, getEast: () => e, getNorthWest: () => ({lat: n, lng: w}), getNorthEast: () => ({lat: n, lng: e}),
      getSouthEast: () => ({lat: s, lng: e}), getSouthWest: () => ({lat: s, lng: w})}; };
  const layers = [], listeners = {}, classes = new Set(), state = {dragging: true};
  const shape = (kind, more) => { const l = {kind, ...more, addTo() { layers.push(l); return l; }}; return l; };
  const L = {
    latLngBounds: bounds, latLng: (lat, lng) => ({lat, lng}), divIcon: o => o,
    layerGroup: () => ({addTo() { return this; }, removeLayer(l) { const i = layers.indexOf(l); if (i >= 0) layers.splice(i, 1); }}),
    rectangle: (b, o) => { const r = shape("rect", {o, b: Array.isArray(b) ? bounds({lat: b[0][0], lng: b[0][1]}, {lat: b[1][0], lng: b[1][1]}) : b,
      setBounds(nb) { r.b = Array.isArray(nb) ? bounds({lat: nb[0][0], lng: nb[0][1]}, {lat: nb[1][0], lng: nb[1][1]}) : nb; }, getBounds: () => r.b}); return r; },
    circleMarker: p => shape("first", {p}),
    marker: p => { const on = {}, m = shape("corner", {p, on(type, fn) { on[type] = fn; }, getLatLng: () => m.p, setLatLng(q) { m.p = Array.isArray(q) ? {lat: q[0], lng: q[1]} : q; },
      drag(to) { m.p = to; on.drag(); on.dragend(); }}); return m; },
  };
  const box = {style: {}, classList: {add: c => classes.add(c), remove: c => classes.delete(c)}, setPointerCapture() {},
    addEventListener(type, fn) { listeners[type] = fn; }, removeEventListener(type) { delete listeners[type]; }};
  const map = {on(type, fn) { mapHandlers[type] = fn; }, off(type) { delete mapHandlers[type]; }, invalidateSize() {}, removeLayer() {},
    getContainer: () => box, dragging: {enable() { state.dragging = true; }, disable() { state.dragging = false; }},
    mouseEventToContainerPoint: e => ({x: e.clientX, y: e.clientY}), containerPointToLatLng: toLatLng,
    getBounds: () => bounds(toLatLng({x: 0, y: 400}), toLatLng({x: 400, y: 0}))};
  const finger = (type, x, y, more = {}) => listeners[type] && listeners[type]({type, pointerId: 1, isPrimary: true, pointerType: "touch", clientX: x, clientY: y, preventDefault() {}, target: {closest: () => null}, ...more});
  return {L, map, layers, classes, state, finger, listening: () => Object.keys(listeners).sort()};
}
function page(archive, extra = {}) {
  const els = new Map(), bodyClass = new Set(), routes = [], toasts = [], storage = new Map(Object.entries(extra.storage || {}));
  const drawn = [], styles = [], pictures = [], printed = [];
  const canvasContext = new Proxy({}, {get: (t, k) => k in t ? t[k] : k === "measureText" ? s => ({width: String(s).length * 9})
    : k === "createLinearGradient" ? () => ({addColorStop() {}}) : k === "fillText" ? s => { drawn.push(String(s)); } : () => {}, set: (t, k, v) => { t[k] = v; return true; }});
  const element = () => {
    const listeners = {};
    return {hidden: false, innerHTML: "", textContent: "", className: "", value: "", style: {}, attrs: {}, clientWidth: 390, width: 0, height: 0, scrollTop: 0,
      setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
      fire(type, event) { (listeners[type] || []).forEach(fn => fn(event)); },
      toBlob(done) { done({size: 1, type: "image/png"}); }, toDataURL: () => "data:image/jpeg;base64,AAAA",
      getContext: () => canvasContext, getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 390}), setPointerCapture() {}};
  };
  const el = sel => { if (!els.has(sel)) els.set(sel, element()); return els.get(sel); };
  const mapHandlers = {}, leaflet = extra.leaflet ? fakeLeaflet(extra.leaflet, mapHandlers) : null;
  const view = extra.view;                    // [south, west, north, east] of the app's map as it is on screen
  const context = vm.createContext({
    console, setTimeout, clearTimeout, setInterval, clearInterval, AbortController, URL,
    fetch: (url, init) => !/nominatim/.test(url) ? archive.fetch(url, init)
      : extra.found ? Promise.resolve({ok: true, json: async () => extra.found}) : Promise.reject(new Error("no names in tests")),
    document: {querySelector: el, createElement: element, getElementById: () => null, head: {appendChild: s => styles.push(s)},
      body: {classList: {add: c => bodyClass.add(c), remove: c => bodyClass.delete(c)}, appendChild() {}}},
    ImageData: class { constructor(data, w, h) { this.data = data; this.width = w; this.height = h; } },
    navigator: {}, devicePixelRatio: 2, addEventListener() {}, removeEventListener() {}, print: () => printed.push(Date.now()),
    getComputedStyle: () => ({getPropertyValue: () => ""}),
    location: {hash: "", origin: "https://app.test", pathname: "/"},
    history: {replaceState(a, b, url) { context.location.hash = url.slice(url.indexOf("#")); }},
    localStorage: {getItem: k => storage.has(k) ? storage.get(k) : null, setItem: (k, v) => storage.set(k, v)},
    S: {items: extra.items || [], layer: {addTo() {}}, map: leaflet ? leaflet.map : {on(type, fn) { mapHandlers[type] = fn; }, off(type) { delete mapHandlers[type]; }, invalidateSize() {}, removeLayer() {},
      ...(view ? {getBounds: () => ({getSouth: () => view[0], getWest: () => view[1], getNorth: () => view[2], getEast: () => view[3]})} : {})}},
    ...(leaflet ? {L: leaflet.L} : {}),
    toast: t => toasts.push(t), copy() {}, bidi: s => String(s), presentPicture: (make, name, title, wait) => { pictures.push({make, name, title, wait}); },
  });
  context.setRoute = hash => {               // as the app's router does
    routes.push(hash);
    context.location.hash = hash ? "#" + encodeURIComponent(hash) : "";
    if (!context.BA.route(hash)) context.BA.leave();
  };
  vm.runInContext(web("imagery.js"), context);
  vm.runInContext(web("beforeafter.js"), context);
  context.BA.init();
  const act = (name, data = {}) => el("#baSheet").fire("click", {target: {closest: () => ({dataset: {act: name, ...data}})}});
  const until = async (what, cond, ms = 15000) => {
    for (const end = Date.now() + ms; Date.now() < end;) { if (cond()) return; await new Promise(r => setTimeout(r, 5)); }
    assert.fail("never happened: " + what + "\nveil: " + el("#baVeil").innerHTML + "\nfound: " + el("#baFind").innerHTML);
  };
  const loaded = () => until("both pictures at full sharpness", () => el("#baVeil").hidden && /دقة 10 م/.test(el("#baQ").textContent));
  const settled = ms => until("the pictures and the comparison", () => el("#baVeil").hidden && !el("#baQ").hidden, ms);
  const stage = el("#baStage");
  const tap = (x, y) => { for (const type of ["pointerdown", "pointerup"]) stage.fire(type, {type, pointerId: 2, clientX: x, clientY: y, target: {closest: () => null}}); };
  const pickTool = (name, data = {}) => el("#baPickTools").fire("click", {target: {closest: () => ({dataset: {act: name, ...data}})}});
  const files = () => archive.calls.filter(c => /\.tif$/i.test(c.url.split("?")[0])).length;
  return {context, el, act, until, loaded, settled, tap, pickTool, files, routes, toasts, bodyClass, mapHandlers, storage, drawn, styles, pictures, printed, leaflet,
    hash: () => decodeURIComponent(context.location.hash)};
}
const PASSES = () => [{date: ago(40)}, {date: ago(20), cloud: "all"}, {date: ago(6), burn: true, flame: true}];

test("a place opens on the newest clear pass and the clear one before it, and says what changed", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES()});
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,1;خزانات الاختبار");
  assert.match(p.el("#baVeil").innerHTML, /جارٍ البحث عن مرور القمر/);
  await p.loaded();
  assert.equal(p.el("#baTitle").innerHTML, "خزانات الاختبار");
  const dates = p.el("#baDates").innerHTML;
  assert.ok(dates.includes(H.dateAr(Date.parse(ago(40) + "T07:22:30Z"))), "before: the clear pass, not the cloudy one after it");
  assert.ok(dates.includes(H.dateAr(Date.parse(ago(6) + "T07:22:32Z"))), "after: the newest pass");
  const found = p.el("#baFind").innerHTML;
  // the burnt hectare, plus the small patch the flame itself changed
  const area = +/تغيّر مرصود: نحو (\d\.\d) هكتار/.exec(found)[1], darker = +/صار أغمق: (\d\.\d) هكتار/.exec(found)[1];
  assert.ok(area >= 1.0 && area <= 1.6, "changed area " + area); assert.ok(darker >= 1.0 && darker <= 1.3, "darkened area " + darker);
  assert.match(found, /لهب أو حرارة شديدة لحظة التقاط صورة «بعد»:<\/b> 1 لم يكن ظاهراً قبل/);
  assert.doesNotMatch(found, /class="warn"/);
  assert.match(p.el("#baStrip").innerHTML, /غيوم 100%/);                       // the date strip took the place of the list of passes
  assert.match(p.el("#baStrip").innerHTML, /<span id="baCount">3 · 2 صافية<\/span>/);
  assert.equal(p.el("#baTagB").textContent, "قبل · " + H.dateAr(Date.parse(ago(40) + "T07:22:30Z")));
  assert.equal(p.el("#baHandle").hidden, false);
  assert.match(p.el("#baHelp").innerHTML, /لا تحدد السبب/);
  assert.ok(archive.calls.every(c => !/\.tif$/.test(c.url) || c.range), "pictures are read by range only");
});

test("the divider follows the finger, the views switch, and a cloudy choice is flagged", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES()});
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,1");
  await p.loaded();
  const stage = p.el("#baStage"), finger = (type, x) => stage.fire(type, {type, pointerId: 1, clientX: x, clientY: 200, target: {closest: () => null}});
  finger("pointerdown", 97.5);
  assert.equal(p.el("#baHandle").style.left, "25%");
  finger("pointermove", 292.5); finger("pointerup", 292.5);
  assert.equal(p.el("#baHandle").style.left, "75%");
  assert.equal(p.el("#baHandle").attrs["aria-valuenow"], "75");
  p.act("mode", {mode: "change"});
  assert.match(p.el("#baModes").innerHTML, /data-mode="change" aria-pressed="true"/);
  // the film button is there; a browser that cannot record says so and points to the picture
  assert.match(p.el("#baActions").innerHTML, /data-act="film">.*<span>فيديو<\/span>/);
  p.act("film");
  assert.match(p.el("#shot").innerHTML, /لا يسجّل الفيديو/);
  assert.equal(p.el("#shot").hidden, false);
  p.el("#shotClose").onclick();
  assert.equal(p.el("#shot").hidden, true);
  p.act("blink");
  assert.equal(p.el("#baHandle").hidden, true, "no divider while the two pictures alternate");
  p.act("blink");
  // measuring: two taps give a distance and the coordinates of the last point; the divider stays put
  p.act("measure");
  assert.match(p.el("#baMeasure").innerHTML, /اضغط نقطتين على الصورة/);
  const tap = (x, y) => { for (const type of ["pointerdown", "pointerup"]) stage.fire(type, {type, pointerId: 2, clientX: x, clientY: y, target: {closest: () => null}}); };
  tap(100, 100); tap(100, 295);            // 195 of 390 screen pixels = half of the 2 km box
  assert.match(p.el("#baMeasure").innerHTML, /<b>1\.00 كم<\/b>/);
  assert.match(p.el("#baMeasure").innerHTML, /data-act="copy" data-text="2[56]\.\d{5}, (49|50)\.\d{5}"/);
  assert.equal(p.el("#baHandle").style.left, "75%");
  p.act("measure");
  // "after" cannot be older than "before"
  p.act("step", {side: "after", dir: "-1"});
  assert.match(p.toasts.at(-1), /لا مرور أقدم/);
  // choosing the cloudy pass by hand is allowed, and the result says it is not reliable
  const cloudy = archive.passes[1];
  p.act("take", {side: "before", key: cloudy.date + "|" + cloudy.platform});
  await p.until("the cloudy pass shown", () => /class="warn"/.test(p.el("#baFind").innerHTML) && p.el("#baVeil").hidden);
  assert.match(p.el("#baFind").innerHTML, /غيوم أو ظلالها في إحدى الصورتين/);
  assert.ok(decodeURIComponent(p.context.location.hash).includes("," + cloudy.date.replace(/-/g, "") + ","), "the link now carries the chosen date");
});

test("Landsat dates are offered in the list, but the default pair stays on Sentinel-2", async () => {
  const passes = [{date: ago(40)}, {date: ago(12), sensor: "landsat"}, {date: ago(6), burn: true}];
  const archive = makeArchive({lat: 26, lon: 50, passes});
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,1");
  await p.loaded();
  assert.ok(p.el("#baDates").innerHTML.includes(H.dateAr(Date.parse(ago(40) + "T07:22:30Z"))), "before: the Sentinel-2 pass, though Landsat's is nearer");
  assert.doesNotMatch(p.el("#baDates").innerHTML, /Landsat/);
  await p.until("the Landsat pass checked", () => /صافية · Landsat 9 · 30 م/.test(p.el("#baStrip").innerHTML));
  assert.doesNotMatch(p.el("#baFind").innerHTML, /Landsat/);
  const landsat = archive.passes[1];
  p.act("take", {side: "before", key: landsat.date + "|" + landsat.platform});
  await p.until("the Landsat picture shown", () => /القمر الأمريكي Landsat/.test(p.el("#baFind").innerHTML) && p.el("#baVeil").hidden);
  assert.match(p.el("#baDates").innerHTML, /Landsat 30 م/);
  assert.match(p.el("#baQ").textContent, /دقة 30 م/);
  assert.doesNotMatch(p.el("#baFind").innerHTML, /تقدير أولي/);
  // only Landsat over a place: then it is used rather than nothing
  const only = [{sensor: "landsat", time: 2, cover: 1, sky: {cloud: 0, shadow: 0, cirrus: 0, nodata: 0, hidden: 0}},
    {sensor: "landsat", time: 1, cover: 1, sky: {cloud: 0, shadow: 0, cirrus: 0, nodata: 0, hidden: 0}}];
  assert.equal(H.choose(only, null).after.pass, only[0]);
  assert.equal(H.sensorText(only[0]), " · Landsat 30 م");
});

test("an incident opens on the passes around it; a fire newer than the last pass shows «قبل» only", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES()});
  const at = days => new Date(Date.now() - days * DAY).toISOString();
  const items = [
    {id: "old", lat: 26, lon: 50, best: "polar", place: "land", headline: "حريق في منشأة", where: "قرب الاختبار", first: at(10), last: at(9.9)},
    {id: "new", lat: 26, lon: 50, best: "polar", place: "sea", headline: "حريق في البحر", where: "", first: at(2), last: at(1.9)},
  ];
  const p = page(archive, {items});
  p.context.setRoute("ba:i:old");
  await p.loaded();
  assert.equal(p.el("#baTitle").innerHTML, "حريق في منشأة");
  assert.match(p.el("#baFind").innerHTML, /تغيّر مرصود: نحو 1\.\d هكتار/);
  p.context.setRoute("ba:i:new");
  await p.until("the single picture", () => /صورة «قبل» فقط/.test(p.el("#baFind").innerHTML) && p.el("#baVeil").hidden);
  assert.match(p.el("#baFind").innerHTML, /لم يمر القمر فوق الموقع بصورة صافية بعد الحدث/);
  assert.match(p.el("#baFind").innerHTML, /في البحر تتحرك السفينة/);
  assert.equal(p.el("#baHandle").hidden, true);
  // a fire whose before/after picture the monitor already posted opens on that same pair
  items[0].satpic = {before: ago(40), after: ago(6), area_ha: 1.2, quiet: false};
  p.context.setRoute("ba:i:old");
  await p.loaded();
  assert.ok(p.el("#baDates").innerHTML.includes(H.dateAr(Date.parse(ago(40) + "T07:22:30Z"))));
  assert.match(p.el("#baStrip").innerHTML, /class="ba-day g0 after"/, "the posted pair is marked in the strip");
  p.context.setRoute("ba:i:gone");
  assert.match(p.toasts.at(-1), /لم يعد في القائمة/);
});

test("the search field at the top finds any place: coordinates open it, a name lists what was found", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES()});
  const found = [{lat: "30.549", lon: "47.662", display_name: "مطار البصرة الدولي, طريق المطار, ناحية الهارثة, قضاء البصرة, العراق"}];
  const p = page(archive, {found});
  assert.equal(p.el("#finder").hidden, false, "shown once the app has started");
  const submit = text => { p.el("#finderQ").value = text; p.el("#finder").fire("submit", {preventDefault() {}}); };
  submit("26.5, 50.1");
  assert.equal(p.routes.at(-1), "ba:26.50000,50.10000,2", "coordinates go straight to the comparison");
  submit("مطار البصرة");
  assert.equal(p.routes.at(-1), "ba");
  await p.until("the place that was found", () => /مطار البصرة الدولي/.test(p.el("#baSheet").innerHTML));
  const sheet = p.el("#baSheet").innerHTML;
  assert.match(sheet, /data-act="open" data-lat="30.549" data-lon="47.662"/);
  assert.match(sheet, /data-name="مطار البصرة الدولي، طريق المطار، ناحية الهارثة"/, "a long address is cut to its first three parts");
  assert.match(sheet, /data-act="show" data-lat="30.549" data-lon="47.662"[^>]*>على الخريطة</);
  p.act("show", {lat: "30.549", lon: "47.662", name: "مطار البصرة الدولي"});
  assert.equal(p.routes.at(-1), "", "back on the app's map (the pin needs the real map)");
  assert.equal(p.el("#baSheet").hidden, true);
  submit("   ");
  assert.equal(p.routes.at(-1), "", "an empty search does nothing");
});

test("when the archive is down the screen says so and offers to try again", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES()});
  const p = page(archive);
  archive.fail = () => "down";
  p.context.setRoute("ba:26.00000,50.00000,1");
  await p.until("the failure text", () => /لا يستجيب الآن/.test(p.el("#baVeil").innerHTML));
  assert.match(p.el("#baVeil").innerHTML, /data-act="retry"/);
  archive.fail = null;
  p.act("retry", {what: "start"});
  await p.loaded();
  assert.match(p.el("#baFind").innerHTML, /تغيّر مرصود/);
  // every pass under cloud: no picture is forced on the user
  const grey = page(makeArchive({lat: 26, lon: 50, passes: [{date: ago(9), cloud: "all"}, {date: ago(4), cloud: "all"}]}));
  grey.context.setRoute("ba:26.00000,50.00000,1");
  await grey.until("the no-clear-picture text", () => /لا صورة صافية/.test(grey.el("#baVeil").innerHTML));
  assert.match(grey.el("#baVeil").innerHTML, /data-act="older"/);
});

test("choosing a place: from the map, by coordinates, or from the saved list", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES()});
  const saved = JSON.stringify([{name: "مطار الاختبار", lat: 26, lon: 50, half: 1}]);
  const p = page(archive, {storage: {"firms-places": saved}});
  p.context.setRoute("ba");
  assert.match(p.el("#baSheet").innerHTML, /اختر موقعاً من الخريطة/);
  assert.match(p.el("#baSheet").innerHTML, /مطار الاختبار/);
  // the saved places are a watch list: a pass newer than the last visit is announced
  await p.until("the newest pass of the watched place", () => /صورة جديدة/.test(p.el("#baNew0").textContent));
  assert.ok(p.el("#baNew0").textContent.includes(H.dateAr(Date.parse(ago(6) + "T07:22:32Z"))));
  // from the map
  p.act("pick");
  assert.ok(p.bodyClass.has("picking")); assert.equal(p.el("#pickBar").hidden, false); assert.equal(p.el("#baSheet").hidden, true);
  p.mapHandlers.click({latlng: {lat: 27.1, lng: 52.2}});
  assert.equal(p.routes.at(-1), "ba:27.10000,52.20000,2");
  assert.ok(!p.bodyClass.has("picking")); assert.equal(p.el("#pickBar").hidden, true);
  // cancelled: back to the chooser, the map is released
  p.context.setRoute("ba"); p.act("pick"); p.el("#pickCancel").fire("click", {});
  assert.equal(p.routes.at(-1), "ba"); assert.ok(!p.bodyClass.has("picking")); assert.equal(p.mapHandlers.click, undefined);
  // typed coordinates
  p.el("#baQuery").value = "26.5, 50.1";
  p.el("#baSearch").fire("submit", {preventDefault() {}});
  assert.equal(p.routes.at(-1), "ba:26.50000,50.10000,2");
  // a saved place opens with its own box and name
  p.context.setRoute("ba");
  p.act("open", {lat: "26", lon: "50", half: "1", name: "مطار الاختبار"});
  assert.equal(p.routes.at(-1), "ba:26.00000,50.00000,1;مطار الاختبار");
  await p.loaded();
  assert.equal(p.el("#baStar").textContent, "★ محفوظ");
  assert.equal(JSON.parse(p.storage.get("firms-places-recent"))[0].name, "مطار الاختبار");
  assert.ok(JSON.parse(p.storage.get("firms-places"))[0].seen > Date.now() - 60000, "a watched place is marked as seen now");
  p.act("back");
  assert.equal(p.el("#baSheet").hidden, true);
});

/* ====================================================================== the imagery browser */
const near = (a, b, eps, what) => assert.ok(Math.abs(a - b) <= eps, `${what || "value"}: ${a} is not within ${eps} of ${b}`);
const skyOf = hidden => ({cloud: hidden, shadow: 0, cirrus: 0, nodata: 0, hidden});
const passOf = (date, hidden, more = {}) => ({key: date + "|" + (more.platform || "sentinel-2b"), date, time: Date.parse(date + "T07:22:00Z"), platform: "sentinel-2b",
  cover: 1, sky: hidden == null ? null : skyOf(hidden), ...more});

/* ---------- plain helpers ---------- */
test("links also carry how the pictures are shown, the bands and the layer; a link without them opens as it always did", () => {
  const v = {lat: 26.23456, lon: 50.83111, half: 3, before: "2026-10-30", after: "2026-11-07", name: "الخزانات"};
  const full = H.routeOf({...v, show: "side", combo: "swir", layer: "burn"});
  assert.equal(full, "ba:26.23456,50.83111,3,20261030,20261107,s=side,c=swir,o=burn;الخزانات");
  assert.deepEqual(H.parseRoute(full), {view: "viewer", ...v, show: "side", combo: "swir", layer: "burn"});
  assert.equal(H.routeOf({...v, show: "split", combo: "color", layer: ""}), H.routeOf(v), "what is as usual is not written");
  // one picture of a wide box, with or without a date
  assert.equal(H.routeOf({lat: 30, lon: 47, half: 40, after: "2026-11-07", show: "single"}), "ba:30.00000,47.00000,40,,20261107,s=single");
  assert.deepEqual(H.parseRoute("ba:30.00000,47.00000,40,,20261107,s=single"),
    {view: "viewer", lat: 30, lon: 47, half: 40, before: null, after: "2026-11-07", name: "", show: "single"});
  assert.equal(H.routeOf({lat: 30, lon: 47, half: 20, show: "single", combo: "ndvi"}), "ba:30.00000,47.00000,20,s=single,c=ndvi");
  assert.deepEqual(H.parseRoute("ba:30.00000,47.00000,20,s=single,c=ndvi"),
    {view: "viewer", lat: 30, lon: 47, half: 20, before: null, after: null, name: "", show: "single", combo: "ndvi"});
  for (const show of H.SHOWS) for (const combo of H.COMBOS) for (const layer of ["", ...H.LAYERS]) {
    const r = H.parseRoute(H.routeOf({...v, show, combo, layer}));
    assert.deepEqual([r.show || "split", r.combo || "color", r.layer || ""], [show, combo, layer]);
  }
  // what is not understood is left out; a name that looks like an option stays a name
  assert.deepEqual(H.parseRoute("ba:30,47,2,s=upside,c=x,o=y,z=1"), {view: "viewer", lat: 30, lon: 47, half: 2, before: null, after: null, name: ""});
  assert.deepEqual(H.parseRoute("ba:26.2,50.8,2;c=swir, s=side"), {view: "viewer", lat: 26.2, lon: 50.8, half: 2, before: null, after: null, name: "c=swir, s=side"});
  // an incident
  const id = "20261108-0740_14.793_33.326";
  assert.equal(H.routeOf({id, show: "blink", layer: "change"}), `ba:i:${id},s=blink,o=change`);
  assert.deepEqual(H.parseRoute(`ba:i:${id},s=blink,o=change`), {view: "incident", id, show: "blink", layer: "change"});
  assert.equal(H.routeOf({id}), "ba:i:" + id);
  assert.deepEqual(H.SIZES, [1, 2, 3, 5, 10, 20, 40]);
  assert.equal(H.parseRoute("ba:26.2,50.8,40").half, 40);
});

test("a drawn rectangle becomes the smallest box that holds it, the map on screen the nearest one, each point's true size said", () => {
  const rect = (lat, lon, wKm, hKm) => { const dy = hKm / 2 / 110.574, dx = wKm / 2 / (111.32 * Math.cos(lat * Math.PI / 180));
    return {south: lat - dy, north: lat + dy, west: lon - dx, east: lon + dx}; };
  let b = H.boxOfRect(rect(26, 50, 3, 2));
  near(b.lat, 26, 1e-9); near(b.lon, 50, 1e-9); near(b.wKm, 3, 0.01); near(b.hKm, 2, 0.01);
  assert.equal(b.half, 2, "the 4 km box holds 3 x 2 km"); assert.equal(b.sideKm, 4); assert.equal(b.cut, false);
  assert.equal(H.boxText(b), "3.0 × 2.0 كم ← مربع 4 × 4 كم");
  assert.equal(H.boxOfRect(rect(26, 50, 12.4, 8.1)).half, 10, "12.4 km needs the 20 km box");
  assert.equal(H.boxOfRect(rect(26, 50, 10.2, 4)).half, 5, "a hair over 10 km is still the 10 km box");
  assert.equal(H.boxOfRect(rect(26, 50, 0.2, 0.1)).half, 1, "never smaller than 2 km");
  assert.equal(H.boxOfRect(rect(30, 47, 30, 70)).half, 40);
  b = H.boxOfRect(rect(30, 47, 120, 100));
  assert.equal(b.half, 40); assert.equal(b.cut, true, "wider than the widest box: said so");
  // the map as it is on screen: the box of the nearest size, by area
  assert.equal(H.boxOfView(rect(26, 50, 13, 28)).half, 10, "13 x 28 km on a phone: a 20 km box");
  assert.equal(H.boxOfView(rect(26, 50, 12, 9)).half, 5);
  assert.equal(H.boxOfView(rect(26, 50, 1.5, 3)).half, 1);
  assert.equal(H.boxOfView(rect(26, 50, 60, 100)).half, 40);
  assert.equal(H.boxOfView(rect(26, 50, 60, 100)).far, false);
  assert.equal(H.boxOfView(rect(27, 51, 900, 600)).far, true, "the whole Gulf on screen: zoom in first");
  near(H.boxOfView(rect(27, 51, 12, 9)).lat, 27, 1e-9);
  // metres of ground in one point of the picture: 10 m up to a 6 km box, more for a wider one
  assert.deepEqual(H.SIZES.map(h => H.pixelOf(h, false)), [10, 10, 10, 12.5, 25, 50, 100]);
  assert.deepEqual(H.SIZES.map(h => H.pixelOf(h, true)), [10, 10, 10, 12.5, 50, 100, 200], "«توفير البيانات» halves a wide picture");
  assert.equal(H.metresText(12.5), "12.5 م"); assert.equal(H.metresText(100), "100 م");
  assert.equal(H.showFor(40), "single"); assert.equal(H.showFor(20), "single"); assert.equal(H.showFor(10), "split");
  const q = H.squareOf(26, 50, 2);
  near((q.north - q.south) * 110.574, 4, 1e-9); near((q.east - q.west) * 111.32 * Math.cos(26 * Math.PI / 180), 4, 1e-9);
});

test("the date strip: every pass of both satellites, newest first, with its sky and the side it is chosen for", () => {
  const passes = [passOf("2026-10-25", 0), passOf("2026-11-09", 0), passOf("2026-11-04", 0.6), passOf("2026-11-02", null), passOf("2026-10-28", 0.2),
    passOf("2026-10-30", 0, {platform: "landsat-9", sensor: "landsat"}), passOf("2025-12-30", 0), passOf("2026-10-20", null, {failed: true})];
  const chips = H.stripOf(passes, {before: passes[0], after: passes[1]});
  assert.deepEqual(chips.map(c => c.pass.date), ["2026-11-09", "2026-11-04", "2026-11-02", "2026-10-30", "2026-10-28", "2026-10-25", "2026-10-20", "2025-12-30"]);
  assert.deepEqual(chips.map(c => c.grade), [0, 2, 3, 0, 1, 0, 4, 0], "clear, cloudy, being checked, clear, partly, clear, failed, clear");
  assert.deepEqual(chips.map(c => c.dim), [false, true, false, false, false, false, true, false], "cloudy ones are dimmed");
  assert.deepEqual(chips.map(c => c.mark), ["after", "", "", "", "", "before", "", ""]);
  assert.deepEqual(chips.map(c => c.sat), ["S2B", "S2B", "S2B", "L9", "S2B", "S2B", "S2B", "S2B"]);
  assert.equal(chips[3].metres, 30); assert.equal(chips[0].metres, 10); assert.equal(chips[3].name, "Landsat 9");
  assert.deepEqual(chips.map(c => c.year), ["2026", "", "", "", "", "", "", "2025"], "the year is said where it changes");
  assert.deepEqual([chips[0].day, chips[0].month], ["9", "تشرين الثاني"]);
  assert.match(chips[1].title, /4 تشرين الثاني 2026 · 10:22 · غيوم 60% · Sentinel-2B/);
  assert.equal(H.satName({platform: "sentinel-2c"}), "Sentinel-2C"); assert.equal(H.satName({platform: "landsat-8", sensor: "landsat"}), "Landsat 8");
  assert.equal(H.satShort({platform: "sentinel-2a"}), "S2A"); assert.equal(H.satName({}), "Sentinel-2");
  assert.equal(H.clearPct(passes[4]), 80); assert.equal(H.clearPct(passes[3]), null);
  // «أحدث صورة صافية»: the newest clear one, a sharp one where there is one
  assert.equal(H.latestClear(passes).date, "2026-11-09");
  const late = [passOf("2026-11-12", 0, {platform: "landsat-9", sensor: "landsat"}), passOf("2026-11-09", 0.9), passOf("2026-11-04", 0)];
  assert.equal(H.latestClear(late).date, "2026-11-04", "Sentinel-2 before Landsat");
  assert.equal(H.latestClear(late.slice(0, 2)).date, "2026-11-12", "Landsat where nothing else is clear");
  assert.equal(H.latestClear([passOf("2026-11-09", 0.9)]), null);
  // the date to put beside one already chosen: the clear pass before it, or around the event
  assert.equal(H.partnerOf(passes, "before", passes[1], null).date, "2026-10-25");
  assert.equal(H.partnerOf(passes, "after", passes[0], null).date, "2026-11-09");
  assert.equal(H.partnerOf(passes, "before", passes[1], {start: "2026-10-27T00:00:00Z"}).date, "2026-10-25", "the clear pass before the event");
  assert.equal(H.partnerOf(passes, "before", passes[6], null), null, "nothing older in the list");
});

test("each view says its bands on each satellite and what loads when; a satellite that lacks a band closes the view with a word", () => {
  assert.equal(H.bandsText("swir", {pass: {}}), "B12 · B8A · B04");
  assert.equal(H.bandsText("fire", {pass: {sensor: "landsat"}}), "B7 · B6 · B4");
  assert.equal(H.comboNote("fire", [{platform: "sentinel-2b", assets: {red: {}, swir22: {}}}, null]), "غير متاحة في صور Sentinel-2B");
  assert.equal(H.comboNote("fire", [{}, {sensor: "landsat", platform: "landsat-9"}]), "");
  for (const k of H.COMBOS) { assert.ok(H.COMBO_AR[k].name); assert.ok(H.COMBO_AR[k].legend || H.COMBO_AR[k].ends); }
  // first the bands on screen at 20 m, then the rest, then the 10 m bands sharp
  assert.deepEqual(H.stagesFor(10, false, "color"), [{quality: "fast", names: ["red", "green", "blue"], what: "show"},
    {quality: "fast", names: ["nir08", "swir16", "swir22"], what: "rest"}, {quality: "full", names: ["red", "green", "blue"], what: "sharp"}]);
  assert.deepEqual(H.stagesFor(10, true, "color").map(s => s.quality), ["fast", "fast"], "«توفير البيانات» stops at 20 m");
  assert.deepEqual(H.stagesFor(10, false, "veg").map(s => s.names), [["nir", "red", "green"], ["blue", "nir08", "swir16", "swir22"], ["red", "green", "blue", "nir"]]);
  assert.deepEqual(H.stagesFor(10, false, "nbr").map(s => s.names), [["nir08", "swir22"], ["red", "green", "blue", "swir16"], ["red", "green", "blue"]]);
  assert.deepEqual(H.stagesFor(25, false, "swir"), [{quality: "full", names: ["swir22", "nir08", "red"], what: "show"}, {quality: "full", names: ["green", "blue", "swir16"], what: "rest"}],
    "a wide box has nothing sharper to wait for");
  assert.equal(H.classText(4, {}), "نبات"); assert.equal(H.classText(5, {sensor: "landsat"}), "أرض بلا غيم"); assert.equal(H.classText(null), "");
});

test("panes, zoom limits and measures: side by side on a wide stage, stacked on a tall one; areas by the corners tapped", () => {
  assert.deepEqual(H.panesOf("split", 390, 390, true), [{x: 0, y: 0, w: 390, h: 390, side: ""}]);
  assert.deepEqual(H.panesOf("side", 1000, 500, true), [{x: 501, y: 0, w: 499, h: 500, side: "before"}, {x: 0, y: 0, w: 499, h: 500, side: "after"}], "«قبل» on the right");
  assert.deepEqual(H.panesOf("side", 390, 546, true), [{x: 0, y: 0, w: 390, h: 272, side: "before"}, {x: 0, y: 274, w: 390, h: 272, side: "after"}]);
  assert.equal(H.panesOf("side", 390, 546, false).length, 1, "one picture: one pane");
  assert.deepEqual(H.sharesOf({w: 390, h: 390}, 1), {x: 1, y: 1});
  assert.deepEqual(H.sharesOf({w: 390, h: 273}, 1), {x: 1, y: 0.7}, "a wide pane shows the whole width and 70% of the height");
  assert.deepEqual(H.sharesOf({w: 400, h: 400}, 4), {x: 0.25, y: 0.25});
  const sq = [{x: 10, y: 10}, {x: 110, y: 10}, {x: 110, y: 60}, {x: 10, y: 60}];
  assert.equal(H.polygonArea(sq, 10), 100 * 50 * 100, "100 x 50 points of 10 m = 50 hectares");
  assert.equal(H.polygonArea(sq.slice().reverse(), 10), 500000, "whichever way round it was tapped");
  assert.equal(H.polygonArea(sq.slice(0, 3), 25), 100 * 50 / 2 * 625);
  assert.equal(H.pathLength(sq, 10, true), 3000); assert.equal(H.pathLength(sq, 10, false), 2500);
  assert.equal(H.scaleBar(100, 110).label, "10 كم", "a wide box has a longer scale bar");
});

test("the report says the place, both dates with satellite and sharpness, the areas and the cautions, and never a cause", () => {
  const before = passOf("2026-10-30", 0.03, {platform: "sentinel-2c"}), after = passOf("2026-11-07", 0);
  const d = {title: "مصفاة الاختبار", lat: 26.23456, lon: 50.83111, half: 2, pixel: 10, combo: "swir", names: ["B12", "B8A", "B04"], enhance: false,
    sides: [{side: "before", pass: before, res: 20}, {side: "after", pass: after, res: 20}],
    change: {areaHa: 1.23, darkHa: 1.1, brightHa: 0.13, share: 0.01, compared: 0.82, clouds: true},
    burn: {areaHa: 2.5, share: 0.02, compared: 0.8, classHa: [0, 0.5, 0.8, 0, 1.2]},
    hot: {after: 3, fresh: 1, old: 2}, now: Date.parse("2026-11-15T12:00:00Z")};
  const m = H.reportModel(d), text = H.reportText(m).join("\n");
  for (const want of ["ALFAQAAR FIRMS · رصد", "تقرير صور الأقمار", "مصفاة الاختبار", "26.23456, 50.83111", "مربع 4 × 4 كم", "كل نقطة ≈ 10 م",
    "تحت الحمراء القصيرة (B12 · B8A · B04)", "قبل: 30 تشرين الأول 2026", "بعد: 7 تشرين الثاني 2026", "10:22 بتوقيت بغداد · 07:22 UTC",
    "Sentinel-2C · MSI · 20 م · صفاء 97%", "Sentinel-2B · MSI · 20 م · صفاء 100%", "تغيّر مرصود: نحو 1.2 هكتار", "صار أغمق: 1.1 هكتار", "صار أفتح: 1,300 م²",
    "قورن 82% من المربع", "التقدير أقل ثقة", "أثر احتراق محتمل (dNBR): نحو 2.5 هكتار", "منخفض: 5,000 م²", "متوسط: 8,000 م²", "شديد: 1.2 هكتار",
    "موقع واحد لم يكن ظاهراً قبل", "شعلة غاز دائمة على الأرجح", "لا تُحسب تغيّراً", "ولا تحدد سببه", "كل نقطة 10 أمتار على الأرض", "مؤشر لا دليل",
    "Sentinel-2 · برنامج كوبرنيكوس", "أُعدّ التقرير في 15 تشرين الثاني 2026، 15:00 بتوقيت بغداد (12:00 UTC)", "Contains modified Copernicus Sentinel data 2026"])
    assert.ok(text.includes(want), "the report says: " + want);
  assert.ok(!text.includes("مرتفع:"), "a class with no area is not listed");
  for (const bad of ["ضرر مؤكد", "الخليج العربي", "بسبب", "قصف", "هجوم", "ذكاء اصطناعي"]) assert.ok(!text.includes(bad), "the report never says: " + bad);
  assert.equal(m.file, "ALFAQAAR-report-26.235_50.831");
  assert.deepEqual(m.sides.map(s => s.label), ["قبل", "بعد"]);
  // nothing found: said plainly
  const calm = H.reportText(H.reportModel({...d, change: {areaHa: 0, darkHa: 0, brightHa: 0, share: 0, compared: 1, clouds: false},
    burn: {areaHa: 0, share: 0, compared: 1, classHa: [0, 0, 0, 0, 0]}, hot: {after: 0, fresh: 0, old: 0}})).join("\n");
  for (const want of ["لا تغيّر واضح بين الصورتين", "لا أثر احتراق واضح", "لا لهب ظاهراً لحظة الالتقاط", "قورن 100% من المربع"]) assert.ok(calm.includes(want), want);
  // one Landsat picture of a wide box, with the display stretch on
  const one = H.reportModel({title: "حقل", lat: 30, lon: 47, half: 40, pixel: 100, combo: "ndvi", names: ["B5", "B4"], enhance: true,
    sides: [{side: "after", pass: passOf("2026-11-01", 0.2, {platform: "landsat-9", sensor: "landsat"}), res: 100}], change: null, burn: null, hot: null, now: d.now});
  const single = H.reportText(one).join("\n");
  for (const want of ["الصورة: 1 تشرين الثاني 2026", "Landsat 9 · OLI · 100 م · صفاء 80%", "مربع 80 × 80 كم", "كل نقطة ≈ 100 م", "مؤشر النبات NDVI (B5 · B4)", "30 متراً في صور Landsat",
    "هذا المربع واسع", "«تحسين العرض» يزيد التباين والحدّة فقط", "Landsat · USGS/NASA", "Landsat imagery courtesy of the U.S. Geological Survey"]) assert.ok(single.includes(want), want);
  assert.equal(one.change, null); assert.ok(!single.includes("تغيّر مرصود:")); assert.ok(!single.includes("Copernicus"));
  assert.deepEqual(H.caveatsOf({pair: false, landsat: false, pixel: 10, burn: false, enhance: false}),
    ["الصور تُظهر التغيّر المرصود ولا تحدد سببه.", "كل نقطة 10 أمتار على الأرض: لا يظهر ما هو أصغر من ذلك."]);
});

/* ---------- the screen ---------- */
const FIELD = () => [{date: ago(150), veg: true}, {date: ago(40), veg: true}, {date: ago(20), cloud: "all"}, {date: ago(6), veg: true, scar: true, flame: true}];

test("the screen brings its own looks, once, and uses no class the page listens to", () => {
  const p = page(makeArchive({lat: 26, lon: 50, passes: PASSES()}));
  p.context.BA.init();
  assert.equal(p.styles.length, 1, "one stylesheet, however often the app starts");
  const css = p.styles[0].textContent;
  assert.equal(p.styles[0].id, "ba-style");
  for (const part of [".ba-app", ".ba-stage", ".ba-day", ".ba-picktools", ".ba-legend", "@media print", "@media (min-width:900px)", "prefers-reduced-motion",
    "var(--bg", "var(--surface", "var(--line", "var(--fg", "var(--muted", "var(--accent", "var(--font-ui", "var(--font-display"]) assert.ok(css.includes(part), part);
  assert.ok(!/(^|[\s,}])\.(chip|card|btn|seg|sheet)\b/.test(css), "it styles only its own classes");
  p.context.setRoute("ba");
  const chooser = p.el("#baSheet").innerHTML;
  for (const text of ["اختر موقعاً من الخريطة", "ارسم منطقة", "صوّر ما أراه"]) assert.ok(chooser.includes(text), text);
  p.context.setRoute("ba:26.00000,50.00000,1");
  const markup = chooser + p.el("#baSheet").innerHTML + ["#baViews", "#baModes", "#baDates", "#baStrip", "#baActions", "#baTools"].map(s => p.el(s).innerHTML).join("");
  assert.ok(!/class="(?:[^"]* )?(chip|card)(?: [^"]*)?"/.test(markup), "the page's own click handler looks for .chip and .card");
  assert.ok(!/data-(hours|flare)=/.test(markup));
  for (const file of ["imagery.js", "beforeafter.js"]) for (const bad of ["ضرر مؤكد", "الخليج العربي"]) assert.ok(!web(file).includes(bad), bad);
});

test("«صوّر ما أراه»: the map as it is on screen becomes the nearest box; a map zoomed far out is asked to come closer", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES(), size: 12800, pixel: 40});      // 12.8 km of made-up ground, coarse to stay quick
  // about 14 km wide and 11 km high on screen
  const p = page(archive, {view: [25.95, 49.93, 26.05, 50.07]});
  p.context.setRoute("ba");
  assert.match(p.el("#baSheet").innerHTML, /الظاهر الآن نحو 14 × 11 كم ← مربع 10 كم/);
  p.act("here");
  assert.equal(p.routes.at(-1), "ba:26.00000,50.00000,5", "the 10 km box around the middle of the map");
  await p.settled(40000);                       // a 10 km box is 800 x 800 points: slow without a browser
  assert.match(p.el("#baPixel").innerHTML, /المربع 10 × 10 كم · كل نقطة في الصورة ≈ <b>12\.5 م<\/b>/);
  assert.match(p.el("#baQ").textContent, /دقة 12\.5 م/);
  // from the map itself, while choosing
  p.context.setRoute("ba"); p.act("pick");
  assert.match(p.el("#baPickTools").innerHTML, /data-act="here">.*صوّر ما أراه.*مربع 10 كم/s);
  p.pickTool("here");
  assert.equal(p.routes.at(-1), "ba:26.00000,50.00000,5"); assert.ok(!p.bodyClass.has("picking")); assert.equal(p.el("#baPickTools").hidden, true);
  // a wide view opens on one picture; the whole Gulf on screen opens nothing
  const wide = page(archive, {view: [25.7, 49.6, 26.3, 50.4]});
  wide.context.BA.here();
  assert.equal(wide.routes.at(-1), "ba:26.00000,50.00000,40,s=single");
  wide.context.BA.leave(); p.context.BA.leave();                // nothing keeps loading behind the next test
  const far = page(archive, {view: [22, 44, 31, 58]});
  far.context.setRoute("ba");
  assert.match(far.el("#baSheet").innerHTML, /قرّب الخريطة أولاً/);
  far.act("here");
  assert.match(far.toasts.at(-1), /قرّب الخريطة/); assert.ok(far.bodyClass.has("picking"), "the map comes up to zoom in");
  assert.equal(far.routes.filter(r => r.startsWith("ba:")).length, 0);
  far.context.BA.leave();
});

test("drawing an area: a finger draws only in «تحديد», two taps work too, corners adjust it, and the box that holds it opens", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES()});
  const p = page(archive, {leaflet: [26, 50]}), map = p.leaflet;
  p.context.setRoute("ba"); p.act("draw");
  assert.ok(p.bodyClass.has("picking")); assert.equal(p.el("#pickBar b").textContent, "ارسم مستطيلاً حول المنطقة");
  assert.equal(map.state.dragging, false, "in «تحديد» a finger does not move the map"); assert.ok(map.classes.has("ba-drawing"));
  assert.match(p.el("#baPickTools").innerHTML, /data-act="pdraw" data-on="1" aria-pressed="true"/);
  assert.match(p.el("#baPickTools").innerHTML, /data-act="pgo" disabled/);
  // a drag from (100, 120) to (180, 160): 0.02 x 0.01 degrees = 2.0 x 1.1 km
  map.finger("pointerdown", 100, 120); map.finger("pointermove", 104, 122);
  assert.equal(map.layers.length, 0, "a few points of movement are not a drag yet");
  map.finger("pointermove", 150, 150);
  assert.match(p.el("#baPickSize").textContent, /كم ← مربع 2 × 2 كم/, "the size is said while drawing");
  map.finger("pointermove", 180, 160); map.finger("pointerup", 180, 160);
  assert.equal(map.state.dragging, true, "the map moves again once the rectangle is drawn");
  assert.deepEqual(map.layers.map(l => l.kind).sort(), ["corner", "corner", "corner", "corner", "rect", "rect"], "the rectangle, the box around it and four corners to pull");
  assert.match(p.el("#baPickTools").innerHTML, /2\.0 × 1\.1 كم ← مربع 2 × 2 كم · كل نقطة ≈ 10 م/);
  assert.doesNotMatch(p.el("#baPickTools").innerHTML, /data-act="pgo" disabled/);
  // pull the south-east corner far out: the box grows, the corner across stays
  const corners = map.layers.filter(l => l.kind === "corner"), nw = {...corners[0].p};
  corners[2].drag({lat: nw.lat - 0.1, lng: nw.lng + 0.12});
  assert.deepEqual(corners[0].p, nw);
  near(corners[1].p.lng, nw.lng + 0.12, 1e-9, "the neighbour follows"); near(corners[3].p.lat, nw.lat - 0.1, 1e-9);
  assert.match(p.el("#baPickTools").innerHTML, /12 × 11 كم ← مربع 20 × 20 كم · كل نقطة ≈ 25 م/);
  // «مسح», then two taps instead of a drag
  p.pickTool("pclear");
  assert.equal(map.layers.length, 0); assert.equal(map.state.dragging, false);
  map.finger("pointerdown", 200, 200); map.finger("pointerup", 200, 200);
  assert.deepEqual(map.layers.map(l => l.kind), ["first"]); assert.match(p.el("#baPickTools").innerHTML, /اضغط الزاوية المقابلة/);
  map.finger("pointerdown", 240, 220); map.finger("pointerup", 240, 220);
  assert.equal(map.layers.filter(l => l.kind === "corner").length, 4);
  // a second finger does not draw (the map zooms with two)
  p.pickTool("pdraw", {on: "1"});
  map.finger("pointerdown", 10, 10, {isPrimary: false}); map.finger("pointermove", 90, 90, {isPrimary: false});
  assert.equal(map.layers.filter(l => l.kind === "corner").length, 4, "still the rectangle from before");
  p.pickTool("pdraw", {on: "0"});
  p.pickTool("pgo");
  // the middle of (200..240, 200..220) on the 400-point map of 0.1 degree: 26.0 - 0.0025, 50.0 + 0.005
  assert.equal(p.routes.at(-1), "ba:25.99750,50.00500,1");
  p.context.BA.leave();
  assert.ok(!p.bodyClass.has("picking")); assert.equal(map.layers.length, 0); assert.deepEqual(map.listening(), [], "the map is given back as it was");
  assert.equal(p.el("#pickBar b").textContent, "", "and the bar's own words are put back");
  // without Leaflet's drawing tools the app still offers the point
  const plain = page(archive);
  plain.context.setRoute("ba"); plain.act("draw");
  assert.match(plain.toasts.at(-1), /رسم المنطقة غير متاح/); assert.ok(plain.bodyClass.has("picking"));
  plain.mapHandlers.click({latlng: {lat: 27.1, lng: 52.2}});
  assert.equal(plain.routes.at(-1), "ba:27.10000,52.20000,2");
  plain.context.BA.leave();
});

test("the date strip on screen: chips with satellite and sky, quick choices, and «أقدم» adds dates without touching the pictures", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: FIELD()});
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,1");
  await p.loaded();
  const strip = () => p.el("#baStrip").innerHTML, key = d => `${ago(d)}|sentinel-2b`;
  assert.match(strip(), /<span id="baCount">3 · 2 صافية<\/span>/, "the pass of five months ago is not listed yet");
  assert.match(strip(), new RegExp(`class="ba-day g0 after"[^>]*data-act="take" data-key="${key(6).replace("|", "\\|")}"`));
  assert.match(strip(), new RegExp(`class="ba-day g2 dim"[^>]*data-key="${key(20).replace("|", "\\|")}"`), "the cloudy pass is dimmed, and still a button");
  assert.match(strip(), /<small dir="ltr">S2B<\/small><em>بعد<\/em>/); assert.match(strip(), /<em>قبل<\/em>/);
  assert.match(strip(), /data-act="older">أقدم/);
  assert.match(p.el("#baDates").innerHTML, /class="ba-slot after on"/, "a tapped date goes to «بعد» first");
  // «أقدم»: four more months are listed; the pictures stay
  const veil = p.el("#baVeil").hidden;
  p.act("older");
  assert.match(strip(), /data-act="older">جارٍ…/);
  await p.until("the older pass listed and checked", () => /<span id="baCount">4 · 3 صافية<\/span>/.test(strip()));
  assert.equal(p.el("#baVeil").hidden, veil, "nothing was loaded again"); assert.match(p.el("#baFind").innerHTML, /تغيّر مرصود/);
  // a date for «قبل»: tap the card, then the chip
  p.act("side", {side: "before"});
  assert.match(p.el("#baDates").innerHTML, /class="ba-slot before on"/);
  p.act("take", {key: key(150)});
  await p.loaded();
  assert.ok(p.el("#baDates").innerHTML.includes(H.dateAr(Date.parse(ago(150) + "T07:22:30Z"))));
  assert.ok(p.hash().includes("," + ago(150).replace(/-/g, "") + ","));
  // the cloudy pass by hand, then «أحدث صورة صافية» brings the clear one back
  p.act("side", {side: "after"}); p.act("take", {key: key(20)});
  await p.settled();
  assert.match(p.el("#baDates").innerHTML, /غيوم 100%/);
  p.act("quick", {q: "clear"});
  await p.loaded();
  assert.ok(p.el("#baTagA").textContent.includes(H.dateAr(Date.parse(ago(6) + "T07:22:33Z"))));
  p.act("quick", {q: "clear"});
  assert.match(p.toasts.at(-1), /أحدث صورة صافية معروضة/);
  // «آخر صورتين صافيتين»: the pair as the app would choose it
  p.act("quick", {q: "pair"});
  await p.loaded();
  assert.ok(p.el("#baDates").innerHTML.includes(H.dateAr(Date.parse(ago(40) + "T07:22:31Z"))), "«قبل» is the clear pass before the newest again");
  p.act("expand");
  assert.match(strip(), /class="ba-days open"/);
});

test("one picture, the divider, the blink, or side by side: the same dates shown four ways, and the link says which", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES()});
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,1");
  await p.loaded();
  assert.match(p.el("#baViews").innerHTML, /data-look="split" aria-pressed="true"/);
  p.act("look", {look: "side"});
  assert.equal(p.el("#baStage").className, "ba-stage side"); assert.equal(p.el("#baHandle").hidden, true);
  assert.equal(p.el("#baTagB").hidden, false); assert.equal(p.el("#baTagA").hidden, false);
  assert.ok(p.el("#baTagB").style.right && p.el("#baTagA").style.right, "each label sits in its own pane");
  assert.ok(p.hash().endsWith(",s=side"));
  p.act("look", {look: "single"});
  assert.equal(p.el("#baStage").className, "ba-stage");
  assert.equal(p.el("#baTagA").hidden, false); assert.equal(p.el("#baTagB").hidden, true, "one picture: the later one");
  p.act("side", {side: "before"});
  assert.equal(p.el("#baTagB").hidden, false); assert.equal(p.el("#baTagA").hidden, true, "a tap on «قبل» shows the earlier one");
  assert.match(p.el("#baFind").innerHTML, /تغيّر مرصود/, "the comparison is still known");
  p.act("look", {look: "blink"});
  assert.equal(p.el("#baHandle").hidden, true); assert.ok(p.hash().endsWith(",s=blink"));
  p.act("blink");
  assert.match(p.el("#baViews").innerHTML, /data-look="split" aria-pressed="true"/); assert.equal(p.el("#baHandle").hidden, false);
  assert.ok(!p.hash().includes("s="));
  // opened as one picture: only the newest clear pass is read; a comparison then adds the clear pass before it
  const one = page(makeArchive({lat: 26, lon: 50, passes: PASSES()}));
  one.context.setRoute("ba:26.00000,50.00000,1,s=single");
  await one.loaded();
  assert.match(one.el("#baViews").innerHTML, /data-look="single" aria-pressed="true"/);
  assert.match(one.el("#baFind").innerHTML, /صورة واحدة/); assert.match(one.el("#baDates").innerHTML, />الصورة</);
  assert.doesNotMatch(one.el("#baDates").innerHTML, />قبل</);
  assert.match(one.el("#baModes").innerHTML, /data-mode="change" aria-pressed="false" disabled/, "a layer of change needs two dates");
  one.act("look", {look: "split"});
  await one.loaded();
  assert.match(one.el("#baFind").innerHTML, /تغيّر مرصود: نحو 1\.\d هكتار/);
  assert.ok(one.el("#baDates").innerHTML.includes(H.dateAr(Date.parse(ago(40) + "T07:22:30Z"))));
});

test("band combinations and indices: only the bands a view lacks are fetched, the legend and the true sharpness follow", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: FIELD().slice(1)});
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,1");
  await p.loaded();
  assert.ok(!archive.calls.some(c => /B08\.tif$/.test(c.url)), "natural colour never asks for band 8");
  assert.equal(p.el("#baLegend").hidden, true);
  let before = p.files();
  p.act("mode", {mode: "swir"});
  assert.match(p.el("#baModes").innerHTML, /data-mode="swir" aria-pressed="true"/);
  assert.equal(p.files(), before, "all its bands were already in hand");
  assert.match(p.el("#baModeNote").innerHTML, /B12 · B8A · B04/);
  assert.match(p.el("#baQ").textContent, /دقة 20 م/, "two of its three bands are 20 m bands: said so");
  assert.match(p.el("#baLegend").innerHTML, /أرض محترقة/); assert.match(p.el("#baLegend").innerHTML, /لهب أو حرارة شديدة/);
  // the plant view: band 8A at once, band 8 (10 m) fetched behind it
  p.act("mode", {mode: "veg"});
  assert.match(p.el("#baModeNote").innerHTML, /B8A · B04 · B03/);
  await p.until("band 8", () => /B08 · B04 · B03/.test(p.el("#baModeNote").innerHTML) && p.el("#baVeil").hidden);
  assert.ok(archive.calls.some(c => /B08\.tif$/.test(c.url))); assert.match(p.el("#baQ").textContent, /دقة 10 م/);
  // the indices: a fixed ramp with its legend; NDVI from band 8 is 10 m, NBR is 20 m
  before = p.files();
  p.act("mode", {mode: "ndvi"});
  assert.match(p.el("#baLegend").innerHTML, /linear-gradient\(to right,#33415c 0\.0%/); assert.match(p.el("#baLegend").innerHTML, /بلا نبات.*نبات كثيف/s);
  assert.match(p.el("#baModeNote").innerHTML, /B08 · B04/); assert.match(p.el("#baQ").textContent, /دقة 10 م/);
  p.act("mode", {mode: "nbr"});
  assert.match(p.el("#baLegend").innerHTML, /محترق أو أجرد داكن.*نبات سليم/s); assert.match(p.el("#baLegend").innerHTML, />ماء</);
  assert.match(p.el("#baQ").textContent, /دقة 20 م/);
  p.act("mode", {mode: "fire"});
  assert.match(p.el("#baModeNote").innerHTML, /B12 · B11 · B04/);
  assert.equal(p.files(), before, "switching between views downloads nothing twice");
  assert.ok(p.hash().endsWith(",c=fire"));
  // «تحسين العرض» is a display stretch, said on the picture and in the cautions
  p.act("enhance");
  assert.match(p.el("#baQ").textContent, /عرض محسَّن/); assert.match(p.el("#baFind").innerHTML, /يزيد التباين والحدّة فقط/);
  p.act("enhance");
  // a link opens on the view it names
  const linked = page(makeArchive({lat: 26, lon: 50, passes: FIELD().slice(1)}));
  linked.context.setRoute("ba:26.00000,50.00000,1,c=nbr,o=burn");
  await linked.settled();
  assert.match(linked.el("#baModes").innerHTML, /data-mode="nbr" aria-pressed="true"/); assert.match(linked.el("#baModes").innerHTML, /data-mode="burn" aria-pressed="true"/);
});

test("likely burnt ground, the point inspector, the area measure and the summary card", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: FIELD().slice(1)});
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,1");
  await p.loaded();
  const found = p.el("#baFind").innerHTML;
  assert.match(found, /ملخص التحليل/);
  const burnt = +/أثر احتراق محتمل \(<bdi dir="ltr">dNBR<\/bdi>\):<\/b> نحو (\d\.\d) هكتار/.exec(found)[1];
  assert.ok(burnt >= 1.6 && burnt <= 3.5, "the burnt middle and its singed ring: " + burnt);
  assert.match(found, /شديد: \d/); assert.match(found, /مؤشر لا دليل/); assert.match(found, /حُسب في \d+% من المربع/);
  assert.match(found, /لهب أو حرارة شديدة لحظة التقاط صورة «بعد»:<\/b> 1 لم يكن ظاهراً قبل/);
  // the dates, the satellites and how clear each is; the standing cautions
  assert.match(found, /<dt>قبل<\/dt><dd>[^<]*<bdi dir="ltr">Sentinel-2B<\/bdi> · صفاء 100%/); assert.match(found, /<dt>التركيبة<\/dt><dd>ألوان طبيعية \(<bdi dir="ltr">B04 · B03 · B02<\/bdi>\)/);
  for (const text of ["لا تُحسب تغيّراً", "ولا تحدد سببه", "كل نقطة 10 أمتار"]) assert.ok(found.includes(text), text);
  // the layer on the picture, with its legend; the change layer takes its place when asked for
  p.act("mode", {mode: "burn"});
  assert.match(p.el("#baModes").innerHTML, /data-mode="burn" aria-pressed="true"/);
  for (const k of ["منخفض", "متوسط", "مرتفع", "شديد"]) assert.ok(p.el("#baLegend").innerHTML.includes(k), k);
  assert.ok(p.hash().endsWith(",o=burn"));
  p.act("mode", {mode: "change"});
  assert.match(p.el("#baModes").innerHTML, /data-mode="burn" aria-pressed="false"/); assert.match(p.el("#baLegend").innerHTML, /صار أغمق/);
  p.act("mode", {mode: "change"});
  assert.equal(p.el("#baLegend").hidden, true);
  // the inspector: a tap in the burnt middle of the field (250 m west, 350 m north of the place)
  p.act("tool", {tool: "probe"});
  assert.match(p.el("#baMeasure").innerHTML, /اضغط على أي نقطة في الصورة/); assert.match(p.el("#baTools").innerHTML, /data-tool="probe" aria-pressed="true"/);
  p.tap(195 - 250 / 2000 * 390, 195 - 350 / 2000 * 390);
  const probe = p.el("#baMeasure").innerHTML;
  assert.match(probe, /data-act="copy" data-text="26\.00\d{3}, 49\.99\d{3}"/);
  assert.match(probe, /B12<\/bdi> تحت الحمراء القصيرة 2<\/th><td>0\.100<\/td><td>0\.220<\/td>/, "band 12 before and after");
  assert.match(probe, /B8A<\/bdi> [^<]*<\/th><td>0\.450<\/td><td>0\.120<\/td>/);
  assert.match(probe, /NDVI<\/bdi> النبات<\/th><td>0\.800<\/td><td>0\.263<\/td>/); assert.match(probe, /NBR<\/bdi> الاحتراق<\/th><td>0\.636<\/td><td>-0\.294<\/td>/);
  assert.match(probe, /dNBR<\/bdi> فرق الاحتراق<\/th><td colspan="2">0\.9\d\d · شديد/); assert.match(probe, /صنف السطح<\/th><td>نبات<\/td><td>نبات<\/td>/);
  p.tap(-50, 20);
  p.act("tool", {tool: "probe"});
  assert.equal(p.el("#baMeasure").hidden, true);
  // an area: four corners of half the picture's width and height (1 km x 1 km)
  p.act("tool", {tool: "area"});
  assert.match(p.el("#baMeasure").innerHTML, /اضغط زوايا المساحة/);
  for (const [x, y] of [[97.5, 97.5], [292.5, 97.5], [292.5, 292.5]]) p.tap(x, y);
  assert.match(p.el("#baMeasure").innerHTML, /<b>50\.0 هكتار<\/b>/, "three corners: a triangle of half a square kilometre");
  p.tap(97.5, 292.5);
  assert.match(p.el("#baMeasure").innerHTML, /<b>1\.00 كم²<\/b><span>المحيط 4\.00 كم<\/span>/);
  p.act("undo");
  assert.match(p.el("#baMeasure").innerHTML, /<b>50\.0 هكتار<\/b>/);
  p.act("wipe");
  assert.match(p.el("#baMeasure").innerHTML, /0 من 3 على الأقل/);
});

test("the report as a picture and on paper: the place, the dates, the satellites, the areas and the cautions, and no cause", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: FIELD().slice(1)});
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,1;حقل الاختبار");
  await p.loaded();
  assert.match(p.el("#baActions").innerHTML, /data-act="report">.*<span>تقرير<\/span>/); assert.match(p.el("#baActions").innerHTML, /data-act="print">.*طباعة \/ PDF/);
  p.act("report");
  const job = p.pictures.at(-1);
  assert.equal(job.name, "ALFAQAAR-report-26.000_50.000.png"); assert.equal(job.title, "حقل الاختبار");
  p.drawn.length = 0;
  assert.ok(await job.make(), "the picture was made");
  const words = p.drawn.join("\n").replace(/[⁦⁩]/g, "");
  for (const want of ["ALFAQAAR", "FIRMS", "رصد", "تقرير صور الأقمار", "حقل الاختبار", "26.00000, 50.00000 · مربع 2 × 2 كم · كل نقطة ≈ 10 م", "التركيبة: ألوان طبيعية (B04 · B03 · B02)",
    "قبل · " + H.dateAr(Date.parse(ago(40) + "T07:22:30Z")), "بعد · " + H.dateAr(Date.parse(ago(6) + "T07:22:32Z")), "10:22 بتوقيت بغداد · 07:22 UTC",
    "Sentinel-2B · MSI · 10 م · صفاء 100%", "تغيّر مرصود", "أثر احتراق محتمل (dNBR)", "شديد:", "ملخص التحليل", "تنبيهات", "المصدر: Sentinel-2 · برنامج كوبرنيكوس",
    "Contains modified Copernicus Sentinel data"]) assert.ok(words.includes(want), "the report shows: " + want);
  for (const part of ["لا تُحسب تغيّراً", "ولا تحدد سببه", "كل نقطة 10 أمتار", "مؤشر لا دليل"]) assert.ok(words.replace(/\n/g, " ").includes(part), part);
  for (const bad of ["ضرر مؤكد", "بسبب", "قصف"]) assert.ok(!words.includes(bad), bad);
  // the picture to save and its file name are as before
  p.act("shot");
  assert.equal(p.pictures.at(-1).name, "ALFAQAAR-before-after-26.000_50.000.png");
  p.drawn.length = 0;
  assert.ok(await p.pictures.at(-1).make());
  assert.ok(p.drawn.some(t => t.includes("تغيّر مرصود")) && p.drawn.some(t => t.includes("تُظهر الصور التغيّر ولا تحدد سببه")));
  // on paper: a page of its own that only the printer sees
  p.act("print");
  await p.until("the printer", () => p.printed.length === 1);
  const paper = p.el("#baPrint").innerHTML;
  assert.ok(p.bodyClass.has("ba-printing"));
  for (const want of ["ALFAQAAR", "حقل الاختبار", "مربع 2 × 2 كم", "قبل · ", "بعد · ", "بتوقيت بغداد", "Sentinel-2B · MSI", "صفاء 100%", "تغيّر مرصود", "أثر احتراق محتمل (dNBR)",
    "ملخص التحليل", "تنبيهات", "ولا تحدد سببه", "برنامج كوبرنيكوس", "أُعدّ التقرير في", 'src="data:image/jpeg']) assert.ok(paper.includes(want), "on paper: " + want);
  assert.ok(!paper.includes("ضرر مؤكد"));
  // one picture on screen: the report is of that picture alone
  p.act("look", {look: "single"});
  p.act("report"); p.drawn.length = 0;
  await p.pictures.at(-1).make();
  const single = p.drawn.join("\n");
  assert.ok(single.includes("الصورة · ")); assert.ok(!single.includes("أثر احتراق محتمل (dNBR)"));
});

test("a wide box opens on one picture read from the coarse copies, and says how much ground one point is", async () => {
  const archive = makeArchive({lat: 26, lon: 50, passes: PASSES(), size: 46080, pixel: 80});      // 46 km of made-up ground at 80 m
  const p = page(archive);
  p.context.setRoute("ba:26.00000,50.00000,20,s=single");
  await p.settled(40000);
  assert.match(p.el("#baViews").innerHTML, /data-look="single" aria-pressed="true"/);
  assert.match(p.el("#baPixel").innerHTML, /المربع 40 × 40 كم · كل نقطة في الصورة ≈ <b>50 م<\/b>/);
  assert.match(p.el("#baQ").textContent, /دقة 50 م/);
  assert.match(p.el("#baSizes").innerHTML, /data-half="20" aria-pressed="true">40</); assert.match(p.el("#baSizes").innerHTML, /data-half="40" aria-pressed="false">80</);
  assert.match(p.el("#baFind").innerHTML, /هذا المربع واسع: كل نقطة في الصورة المعروضة ≈ 50 م/);
  assert.equal(new Set(archive.calls.filter(c => /B04\.tif$/.test(c.url)).map(c => c.url)).size, 1, "one picture: one date was read");
  // «توفير البيانات» halves the picture of a wide box: each point is then 100 m
  p.act("lite");
  await p.settled(40000);
  assert.match(p.el("#baPixel").innerHTML, /≈ <b>100 م<\/b>/); assert.match(p.el("#baQ").textContent, /دقة 100 م/);
  assert.equal(p.storage.get("firms-ba-lite"), "true");
});
