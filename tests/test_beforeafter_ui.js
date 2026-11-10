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
  vm.runInContext(inline.split("/* ---------- events ---------- */")[0] + "\nglobalThis.app = {S, route, setRoute, showFocus, openDetail};", context);
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

test("without the imagery scripts the app works as before, with no button", () => {
  const {app, elements} = appPage([incident], undefined);
  app.showFocus(incident.id);
  assert.doesNotMatch(elements.get("#focusCard").innerHTML, /قبل وبعد/);
  app.setRoute("ba:26.2,50.8,2");       // an old link: ignored, back to the list
  assert.equal(elements.get("#sheet").hidden, true);
});

/* ---------- the screen itself, in a stand-in page ---------- */
function page(archive, extra = {}) {
  const els = new Map(), bodyClass = new Set(), routes = [], toasts = [], storage = new Map(Object.entries(extra.storage || {}));
  const canvasContext = new Proxy({}, {get: (t, k) => k in t ? t[k] : () => {}, set: (t, k, v) => { t[k] = v; return true; }});
  const element = () => {
    const listeners = {};
    return {hidden: false, innerHTML: "", textContent: "", className: "", value: "", style: {}, attrs: {}, clientWidth: 390, width: 0, height: 0, scrollTop: 0,
      setAttribute(k, v) { this.attrs[k] = v; }, addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
      fire(type, event) { (listeners[type] || []).forEach(fn => fn(event)); },
      getContext: () => canvasContext, getBoundingClientRect: () => ({left: 0, top: 0, width: 390, height: 390}), setPointerCapture() {}};
  };
  const el = sel => { if (!els.has(sel)) els.set(sel, element()); return els.get(sel); };
  const mapHandlers = {};
  const context = vm.createContext({
    console, setTimeout, clearTimeout, setInterval, clearInterval, AbortController, URL,
    fetch: (url, init) => /nominatim/.test(url) ? Promise.reject(new Error("no names in tests")) : archive.fetch(url, init),
    document: {querySelector: el, createElement: element, body: {classList: {add: c => bodyClass.add(c), remove: c => bodyClass.delete(c)}}},
    ImageData: class { constructor(data, w, h) { this.data = data; this.width = w; this.height = h; } },
    navigator: {}, devicePixelRatio: 2, addEventListener() {},
    location: {hash: "", origin: "https://app.test", pathname: "/"},
    history: {replaceState(a, b, url) { context.location.hash = url.slice(url.indexOf("#")); }},
    localStorage: {getItem: k => storage.has(k) ? storage.get(k) : null, setItem: (k, v) => storage.set(k, v)},
    S: {items: extra.items || [], layer: {addTo() {}}, map: {on(type, fn) { mapHandlers[type] = fn; }, off(type) { delete mapHandlers[type]; }, invalidateSize() {}, removeLayer() {}}},
    toast: t => toasts.push(t), copy() {}, bidi: s => String(s), presentPicture() {},
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
  const until = async (what, cond) => {
    for (const end = Date.now() + 6000; Date.now() < end;) { if (cond()) return; await new Promise(r => setTimeout(r, 5)); }
    assert.fail("never happened: " + what + "\nveil: " + el("#baVeil").innerHTML + "\nfound: " + el("#baFind").innerHTML);
  };
  const loaded = () => until("both pictures at full sharpness", () => el("#baVeil").hidden && /دقة 10 م/.test(el("#baQ").textContent));
  return {context, el, act, until, loaded, routes, toasts, bodyClass, mapHandlers, storage};
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
  assert.match(p.el("#baList").innerHTML, /غيوم 100%/);
  assert.equal(p.el("#baAllSum").textContent, "كل تواريخ المرور (3)");
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
  assert.match(p.el("#baList").innerHTML, /data-side="after"[^>]*aria-pressed="true"/);
  p.context.setRoute("ba:i:gone");
  assert.match(p.toasts.at(-1), /لم يعد في القائمة/);
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
