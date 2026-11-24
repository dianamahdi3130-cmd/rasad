"use strict";
// The map-first platform page (web/index.html): the logic behind its filters, cards, marks, time
// slider, dashboard, archive, watched places, sign-in screen and manager's panel. The page's real
// declarations run in a stand-in page: no browser, no network.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const sw = fs.readFileSync(path.join(__dirname, "../web/sw.js"), "utf8");
const inline = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => m[1]).find(s => s.includes("const S ="));
const declarations = inline.split("/* ---------- events ---------- */")[0];
const NOW = Date.parse("2026-11-15T13:45:00Z");
const NAMES = ["S", "UI", "ARC", "ADM", "WATCH", "NAV", "visible", "scoped", "passes", "shown", "render", "normAr", "kmBetween", "categoryOf", "confOf", "markerStyle",
  "sourceTags", "timelineView", "tlRange", "dashStats", "archiveFilter", "archiveMonths", "csvOf", "nearPlace", "watchNews", "sourceState", "statusInfo", "looksLikeKey", "gateText",
  "expiryInfo", "keyMessage", "subsHTML", "adminHTML", "nasaUrl", "nightDefault", "dailyUrl", "NASA_VIEWS", "chartModel", "strengthChart", "niceStep", "detailHTML", "reportHTML",
  "suggestions", "parseCoords", "route", "setRoute", "go", "leave", "setTab", "openDetail", "lock", "cardHTML", "usualHTML", "histHTML", "TOOLS", "SEV", "flareCards", "showTab", "signalsHTML", "signalCount"];

function page(extra = {}) {
  const elements = new Map(), body = new Set(), storage = new Map(Object.entries(extra.storage || {}));
  const context = vm.createContext({
    Date: class extends Date { static now() { return NOW; } },
    document: {
      querySelector(sel) { if (!elements.has(sel)) elements.set(sel, {innerHTML: "", hidden: false}); return elements.get(sel); },
      body: {classList: {add: c => body.add(c), remove: c => body.delete(c), contains: c => body.has(c)}},
      documentElement: {style: {setProperty() {}}, dataset: {}},
    },
    localStorage: {getItem: k => storage.has(k) ? storage.get(k) : null, setItem: (k, v) => storage.set(k, v)},
    location: {hostname: "localhost", pathname: "/", origin: "https://app.test", hash: "", search: ""},
    history: {state: null, pushState(s, b, url) { context.location.hash = url.includes("#") ? url.slice(url.indexOf("#")) : ""; }, replaceState(s, b, url) { context.location.hash = url.includes("#") ? url.slice(url.indexOf("#")) : ""; }},
    navigator: {userAgent: "node", onLine: true}, setTimeout() { return 0; }, clearTimeout() {}, setInterval() { return 0; }, clearInterval() {},
    URLSearchParams, ...(extra.globals || {}),
  });
  vm.runInContext(declarations + `\nglobalThis.app = {${NAMES.join(", ")}};`, context);
  if (extra.items) context.app.S.items = extra.items;
  if (extra.meta) context.app.S.meta = extra.meta;
  return {app: context.app, elements, body, storage, context};
}
const at = hoursAgo => new Date(NOW - hoursAgo * 3600e3).toISOString();
function incident(id, extra = {}) {
  return {id, headline: "حريق جديد", where: "قرب الساحل", first: at(2), last: at(1), lat: 26, lon: 50, verdict: "يحتاج تأكيداً", confidence: 27,
    confidence_level: "منخفضة", severity: "منخفض", category: "fire", priority: "high", regions: ["gulf"], region_name: "الخليج الفارسي", place: "land",
    reason: "رصدة واحدة حتى الآن", start_text: "2026-11-15 الساعة 14:45 (بغداد)", last_text: "2026-11-15 الساعة 15:45 (بغداد)", best: "polar", max_frp: 12, ...extra};
}
const ids = list => [...list].map(it => it.id);
const plain = value => JSON.parse(JSON.stringify(value));      // a value made inside the stand-in page, as one of this file's own

/* ---------- the feed ---------- */
test("the feed's own filters: kind, how sure, how serious; the counters ignore «المهم فقط»", () => {
  const items = [
    incident("fire-low"),
    incident("fire-sure", {verdict: "مؤكد", confidence: 92, confidence_level: "عالية", severity: "حرج", first: at(3)}),
    incident("plant", {category: "industrial", kind: "unusual", verdict: "مؤكد", confidence: 60, confidence_level: "متوسطة", severity: "مرتفع", first: at(4)}),
    incident("speck", {category: "candidate", preliminary: true, confidence: 5, severity: "متوسط", first: at(5)}),
    incident("minor", {priority: "low", first: at(6)}),
  ];
  const {app} = page({items});
  assert.deepEqual(ids(app.visible()), ["fire-low", "fire-sure", "plant", "speck"], "the important ones, newest start first");
  app.S.f.cat = "industrial"; assert.deepEqual(ids(app.visible()), ["plant"]);
  app.S.f.cat = "candidate"; assert.deepEqual(ids(app.visible()), ["speck"]);
  app.S.f.cat = "all"; app.S.f.conf = "high"; assert.deepEqual(ids(app.visible()), ["fire-sure"]);
  app.S.f.conf = "mid"; assert.deepEqual(ids(app.visible()), ["fire-sure", "plant"], "medium and above");
  app.S.f.conf = "low"; assert.deepEqual(ids(app.visible()), ["fire-low", "speck"]);
  app.S.f.conf = "all"; app.S.f.sev = "2"; assert.deepEqual(ids(app.visible()), ["fire-sure", "plant"], "high and above");
  app.S.f.sev = "3"; assert.deepEqual(ids(app.visible()), ["fire-sure"]);
  app.S.f.sev = "all"; app.S.important = false;
  assert.equal(app.visible().length, 5);
  assert.equal(app.scoped().length, 5, "what the counters and the dashboard describe: the region and the period only");
  app.S.region = "iraq"; assert.equal(app.scoped().length, 0);
});

test("older data without the new fields still gets a kind, a level and a mark", () => {
  const {app} = page();
  assert.equal(app.categoryOf({kind: "unusual"}), "industrial");
  assert.equal(app.categoryOf({preliminary: true}), "candidate");
  assert.equal(app.categoryOf({kind: "thermal_candidate"}), "candidate");
  assert.equal(app.categoryOf({kind: "new"}), "fire");
  assert.deepEqual({...app.confOf({verdict: "مؤكد"})}, {n: null, level: "عالية", label: "الثقة", cls: "v-sure", lcls: "v-sure"});
  assert.equal(app.confOf({verdict: "مرجّح"}).level, "متوسطة");
  // an archived event carries no label: at a plant the number is about the plant's habits
  assert.equal(app.confOf({category: "industrial", verdict: "مؤكد", confidence: 60}).label, "الثقة بأنه خارج المعتاد");
  assert.equal(app.confOf({kind: "unusual", confidence: 60}).label, "الثقة بأنه خارج المعتاد");
  assert.equal(app.confOf({category: "industrial", confidence_label: "من البيانات"}).label, "من البيانات");
  assert.equal(app.confOf({verdict: "يحتاج تأكيداً", confidence: 140}).n, 100, "a number out of range is clamped, never shown as it is");
  assert.equal(app.confOf({confidence: 60}).level, "متوسطة", "without a level the number decides");
});

test("the confidence number is printed under the engine's own label, coloured by its level", () => {
  const {app} = page();
  const plant = incident("plant", {category: "industrial", verdict: "مؤكد", confidence: 60, confidence_level: "متوسطة", confidence_label: "الثقة بأنه خارج المعتاد"});
  const c = app.confOf(plant);
  assert.equal(c.label, "الثقة بأنه خارج المعتاد");
  assert.equal(c.cls, "v-sure", "stripe, badge and map mark keep the verdict's colour");
  assert.equal(c.lcls, "v-likely", "the meter takes the colour of the confidence level");
  const card = app.cardHTML(plant);
  assert.match(card, /class="conf v-likely"/);
  assert.match(card, /<span class="vd">مؤكد<\/span><span class="cn">الثقة بأنه خارج المعتاد <b>60<\/b><\/span>/);
  assert.match(card, /style="width:60%"/);
  assert.match(app.cardHTML(incident("fire")), /<span class="cn">الثقة <b>27<\/b>/, "a fire's number is plain «الثقة»");
  assert.doesNotMatch(app.cardHTML(incident("old", {confidence: undefined, confidence_level: undefined})), /class="cn"/, "no number in the data: none invented");
});

test("a card shows kind, seriousness, the channel mark and who saw it", () => {
  const {app} = page();
  const card = app.cardHTML(incident("x", {severity: "حرج", alerted: true, sources: [{key: "mtg_frp"}, {key: "VIIRS_NOAA20_NRT"}, {key: "VIIRS_NOAA21_NRT"}, {key: "MODIS_NRT"}]}));
  assert.match(card, /class="kind k-fire"/);
  assert.match(card, /class="sev" data-s="3"[^>]*>.*حرج/);
  assert.match(card, /class="tag sent"/);
  assert.deepEqual([...app.sourceTags({sources: [{key: "mtg_frp"}, {key: "VIIRS_NOAA20_NRT"}, {key: "VIIRS_NOAA21_NRT"}]})], ["Meteosat-12", "VIIRS"], "two VIIRS satellites are one name");
  assert.deepEqual([...app.sourceTags({sources_text: "أمريكي، أمريكي قديم، أوروبي"})], ["أمريكي", "أمريكي قديم", "أوروبي"]);
  assert.doesNotMatch(app.cardHTML(incident("y", {severity: undefined, alerted: false})), /class="sev"|class="tag sent"/);
  assert.doesNotMatch(card, /class="(?:[^"]* )?fire(?: [^"]*)?"/, "the kind badge never borrows the map mark's class");
});

test("map marks: colour by verdict, size and ring by seriousness, shape by kind", () => {
  const {app} = page();
  const style = it => ({...app.markerStyle(it)});
  assert.deepEqual(style({verdict: "مؤكد", severity: "حرج", category: "fire"}), {cls: "v-sure", shape: "dot", size: 24, ring: true, crit: true});
  assert.deepEqual(style({verdict: "مرجّح", severity: "مرتفع", category: "industrial"}), {cls: "v-likely", shape: "diamond", size: 20, ring: true, crit: false});
  assert.deepEqual(style({verdict: "يحتاج تأكيداً", severity: "منخفض", category: "candidate"}), {cls: "v-check", shape: "spot", size: 12, ring: false, crit: false});
  assert.equal(style({verdict: "مؤكد"}).size, 16, "no seriousness in the data: the middle size, no ring");
  const sizes = app.SEV.map(severity => style({severity}).size);
  assert.deepEqual(plain(sizes), [12, 16, 20, 24], "more serious is never smaller");
});

/* ---------- the time slider ---------- */
test("the time slider shows what had begun by the chosen time, the newest marked; at «الآن» nothing changes", () => {
  const items = [incident("old", {first: at(20), last: at(18)}), incident("mid", {first: at(10), last: at(1)}), incident("new", {first: at(2), last: at(1)})];
  const {app} = page({items});
  const view = (hours) => app.timelineView(app.visible(), NOW - hours * 3600e3, 24).map(r => r.it.id + ":" + r.state);
  assert.deepEqual(view(9.5), ["mid:new", "old:past"], "half an hour after «mid» began; «old» was last seen long before");
  assert.deepEqual(view(19.5), ["old:new"]);
  assert.deepEqual(view(21), [], "before anything began");
  assert.deepEqual(view(5), ["mid:", "old:past"]);
  assert.deepEqual(ids(app.shown()), ids(app.visible()), "live view: the slider is not involved");
  app.S.tl = NOW - 9.5 * 3600e3;
  assert.deepEqual(ids(app.shown()), ["mid", "old"]);
  const r = app.tlRange(NOW);
  assert.equal(r.max - r.min, 24 * 3600e3); assert.equal(r.max % r.step, 0); assert.ok(r.max <= NOW);
});

/* ---------- the dashboard ---------- */
test("the dashboard counts the period in one pass", () => {
  const items = [
    incident("a", {verdict: "مؤكد", confidence_level: "عالية", severity: "حرج", place: "sea", alerted: true, max_frp: 210, first: at(1.5)}),
    incident("b", {category: "industrial", verdict: "مرجّح", confidence_level: "متوسطة", severity: "مرتفع", facility: {km: 0.4}, max_frp: 80, region_name: "العراق", first: at(1.2)}),
    incident("c", {priority: "low", facility: {km: 3}, max_frp: null, first: at(30), last: at(1)}),
    incident("d", {priority: "low", place: "coast", facility: {km: 22}, max_frp: 3, first: at(5)}),
  ];
  const {app} = page({items});
  const st = app.dashStats(items, NOW, 24);
  assert.equal(st.total, 4); assert.equal(st.important, 2); assert.equal(st.sure, 1);
  assert.equal(st.sea, 2, "at sea or on the coast"); assert.equal(st.facility, 2, "a plant's own heat, or within 5 km of one"); assert.equal(st.alerted, 1);
  assert.deepEqual({...st.level}, {"عالية": 1, "متوسطة": 1, "منخفضة": 2});
  assert.deepEqual({...st.sev}, {"حرج": 1, "مرتفع": 1, "متوسط": 0, "منخفض": 2});
  assert.deepEqual({...st.cat}, {fire: 3, industrial: 1, candidate: 0});
  assert.deepEqual(plain(st.regions), [["الخليج الفارسي", 3], ["العراق", 1]]);
  assert.deepEqual(ids(st.top), ["a", "b", "d"], "the strongest first; an unmeasured one is not ranked");
  assert.equal(st.earlier, 1, "one began before the period and is still seen: counted, not drawn");
  assert.equal(st.bins.reduce((n, b) => n + b.n, 0), 3);
  assert.equal(st.bins.reduce((n, b) => n + b.sure, 0), 1);
  assert.equal(st.size, 3600e3, "a day is drawn in hours");
  assert.equal(app.dashStats(items, NOW, 168).size, 864e5, "a week in days");
  const chart = app.histHTML(st);
  assert.match(chart, /<details class="tbl">/, "the numbers are also there as a table");
  assert.match(chart, /class="legend"/);
});

/* ---------- the archive ---------- */
test("archive search ignores Arabic spelling differences", () => {
  const {app} = page();
  assert.equal(app.normAr("الأَحْمَدِيّ"), "الاحمدي");
  assert.equal(app.normAr("إلى مصفاة"), "الي مصفاه");
  assert.equal(app.normAr("  مـــيناء   Ras TANURA ٢٠٢٦ "), "ميناء ras tanura 2026");
  const rows = [
    {id: "1", first: "2026-11-10T08:00:00Z", headline: "حرارة غير معتادة في منشأة نفطية في منطقة الأحمدي", where: "قرب الأحمدي (الكويت)", country: "الكويت", verdict: "مؤكد", category: "industrial", max_frp: 110, regions: ["kuwait"], alerted: true},
    {id: "2", first: "2026-11-12T08:00:00Z", headline: "حريق قرب مصفاة", where: "قرب البصرة", facility: "مصفاة الشعيبة", country: "العراق", verdict: "مرجّح", category: "fire", max_frp: 30, regions: ["iraq"], alerted: false},
    {id: "3", first: "2026-10-30T23:30:00Z", headline: "حريق جديد", where: "قرب Ras Tanura", country: "السعودية", verdict: "يحتاج تأكيداً", category: "fire", max_frp: null, regions: ["saudi", "gulf"], alerted: false},
  ];
  const f = extra => ({q: "", from: "", to: "", cat: "all", verdict: "all", region: "all", minMw: "", alerted: false, ...extra});
  assert.deepEqual(ids(app.archiveFilter(rows, f())), ["2", "1", "3"], "newest first");
  assert.deepEqual(ids(app.archiveFilter(rows, f({q: "الاحمدى"}))), ["1"], "أ = ا and ى = ي");
  assert.deepEqual(ids(app.archiveFilter(rows, f({q: "مصفاه الشعيبه"}))), ["2"], "ة = ه, and the facility's name is searched too");
  assert.deepEqual(ids(app.archiveFilter(rows, f({q: "tanura"}))), ["3"]);
  assert.deepEqual(ids(app.archiveFilter(rows, f({q: "الكويت حراره"}))), ["1"], "every word must be found");
  assert.deepEqual(ids(app.archiveFilter(rows, f({cat: "fire"}))), ["2", "3"]);
  assert.deepEqual(ids(app.archiveFilter(rows, f({verdict: "likely"}))), ["2", "1"], "likely and above");
  assert.deepEqual(ids(app.archiveFilter(rows, f({verdict: "sure"}))), ["1"]);
  assert.deepEqual(ids(app.archiveFilter(rows, f({minMw: "50"}))), ["1"], "an unmeasured event never passes a strength filter");
  assert.deepEqual(ids(app.archiveFilter(rows, f({region: "gulf"}))), ["3"]);
  assert.deepEqual(ids(app.archiveFilter(rows, f({alerted: true}))), ["1"]);
  assert.deepEqual(ids(app.archiveFilter(rows, f({from: "2026-10-31", to: "2026-10-31"}))), ["3"], "23:30 UTC on the 30th is the 31st in Baghdad");
  assert.deepEqual(ids(app.archiveFilter(rows, f({from: "2026-11-11"}))), ["2"]);
});

test("archive months: the ones a range touches, newest first; nothing when there is no archive yet", () => {
  const {app} = page();
  const index = {months: [{month: "2026-09", count: 4}, {month: "2026-11", count: 9}, {month: "2026-10", count: 7}, {month: "junk"}]};
  assert.deepEqual([...app.archiveMonths(index)], ["2026-11", "2026-10", "2026-09"]);
  assert.deepEqual([...app.archiveMonths(index, "2026-10-15", "2026-11-02")], ["2026-11", "2026-10"]);
  assert.deepEqual([...app.archiveMonths(index, "2026-12-01", "2026-12-31")], []);
  assert.deepEqual([...app.archiveMonths(null)], []); assert.deepEqual([...app.archiveMonths({})], []);
});

test("the CSV of the results opens in a spreadsheet with Arabic intact", () => {
  const {app} = page();
  const csv = app.csvOf([{id: "20261110-0800_29.0_48.0", first: "2026-11-10T21:30:00Z", last: "2026-11-11T02:00:00Z", lat: 29.07, lon: 48.13, headline: 'حريق "كبير"، قرب مصفاة',
    where: "قرب الأحمدي", country: "الكويت", region_name: "الكويت", category: "industrial", verdict: "مؤكد", confidence: 80, severity: "مرتفع", max_frp: 110.5, size: "كبير",
    sources_text: "أمريكي، أوروبي", facility: "=CMD()", facility_km: 0.7, alerted: true, pictures: 9}]);
  assert.ok(csv.startsWith("﻿"), "the mark that tells Excel the file is UTF-8");
  const [head, row] = csv.slice(1).trim().split("\r\n");
  assert.equal(head.split(",").length, 20);
  assert.match(head, /^المعرّف,البداية \(بغداد\),/);
  assert.match(row, /^20261110-0800_29\.0_48\.0,2026-11-11 00:30,2026-11-11 05:00,29\.07,48\.13,"حريق ""كبير""، قرب مصفاة",/, "Baghdad times; quotes doubled");
  assert.match(row, /,نشاط منشأة,مؤكد,80,مرتفع,110\.5,كبير,أمريكي، أوروبي,'=CMD\(\),0\.7,نعم,9$/, "a cell that looks like a formula is defused");
  assert.equal(app.csvOf([]).trim().split("\r\n").length, 1, "no results: the header alone");
});

/* ---------- watched places ---------- */
test("watched places: what is seen around each, and what began since the last visit", () => {
  const items = [incident("near", {lat: 29.10, lon: 48.15, first: at(3)}), incident("far", {lat: 30.5, lon: 47.4, first: at(1)}), incident("older", {lat: 29.05, lon: 48.10, first: at(9)})];
  const {app} = page({items});
  const ahmadi = {name: "ميناء الأحمدي", lat: 29.07, lon: 48.13};
  assert.ok(Math.abs(app.kmBetween(29.07, 48.13, 29.10, 48.15) - 3.86) < 0.1);
  assert.deepEqual(ids(app.nearPlace(ahmadi, items, 25)), ["near", "older"], "newest first");
  assert.deepEqual(ids(app.nearPlace(ahmadi, items, 3)), []);
  assert.equal(app.nearPlace(ahmadi, items, 200).length, 3);
  const radius = () => 25;
  assert.equal(app.watchNews([ahmadi], items, radius, NOW - 5 * 3600e3), 1, "only «near» began after the visit");
  assert.equal(app.watchNews([ahmadi], items, radius, 0), 2);
  assert.equal(app.watchNews([ahmadi, {...ahmadi, name: "نفسه"}], items, radius, 0), 2, "an incident near two places is one piece of news");
  assert.equal(app.watchNews([], items, radius, 0), 0);
});

/* ---------- sources and status ---------- */
test("each source says in plain words how fresh it is", () => {
  const {app} = page();
  const s = (key, src) => ({...app.sourceState(key, src, NOW)});
  assert.deepEqual(s("mtg_frp", {ok: true, latest: at(0.4)}), {cls: "ok", text: "أحدث بيانات قبل 24 دقيقة"});
  assert.equal(s("mtg_frp", {ok: false, latest: at(2)}).cls, "warn");
  assert.match(s("mtg_frp", {ok: false, latest: at(2)}).text, /^متأخر · أحدث بيانات قبل ساعتين$/);
  assert.deepEqual(s("seawatch", {ok: true, latest: null}), {cls: "off", text: "يعمل ليلاً · لا رصد الآن"});
  assert.deepEqual(s("seawatch_day", {enabled: true, daylight: false, ok: false}), {cls: "off", text: "ينتظر ضوء النهار"});
  assert.deepEqual(s("firms", {enabled: false}), {cls: "off", text: "متوقف"});
  assert.deepEqual(s("recovery", {enabled: true, pending: 94}), {cls: "ok", text: "94 صورة سابقة تنتظر"});
  assert.equal(s("firms", {ok: true, latest: "2030-01-01T00:00:00Z"}).cls, "off", "a time in the future is not a time");
  const info = app.statusInfo({generated: at(0.02), scan_finished: at(0.02), scan_partial: true, sources: {a: {ok: true, latest: at(0.2)}}}, NOW);
  assert.equal(info.cls, "warn"); assert.equal(info.partial, true); assert.match(info.text, /^فحص جزئي · آخر فحص مكتمل قبل دقيقة$/);
});

/* ---------- NASA's pictures as backgrounds ---------- */
test("NASA backgrounds: the daily picture, false colour and the night picture; no fire-points layer", () => {
  const {app} = page();
  const d = {date: "2026-11-14", pass: "am"};
  assert.equal(app.nasaUrl("daily", d), app.dailyUrl(d));
  assert.equal(app.nasaUrl("false", d), "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_Bands721/default/2026-11-14/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg");
  assert.match(app.nasaUrl("false", {date: "2026-11-14", pass: "pm"}), /VIIRS_NOAA20_CorrectedReflectance_BandsM11-I2-I1\/default\/2026-11-14\/GoogleMapsCompatible_Level9\/\{z\}\/\{y\}\/\{x\}\.jpg$/);
  assert.equal(app.nasaUrl("night", {date: "2026-11-14", pass: "night"}), "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance/default/2026-11-14/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png");
  assert.deepEqual({...app.nightDefault(Date.parse("2026-11-15T13:45:00Z"))}, {date: "2026-11-14", pass: "night"}, "last night's pass is out by the afternoon");
  assert.deepEqual({...app.nightDefault(Date.parse("2026-11-15T02:00:00Z"))}, {date: "2026-11-13", pass: "night"}, "early in the day it is not published yet");
  assert.deepEqual(Object.keys(app.NASA_VIEWS), ["daily", "false", "night"]);
  assert.doesNotMatch(html, /Thermal_Anomalies/, "NASA serves its fire points as vector tiles only: not offered");
  for (const key of ["false", "night"]) assert.doesNotMatch(app.NASA_VIEWS[key].name, /تجريبي/, "official backgrounds since 22 Nov 2026");
});

/* ---------- one incident ---------- */
const timeline = [["2026-11-15T08:00:00Z", "mtg_frp", 30], ["2026-11-15T08:10:00Z", "mtg_frp", 34.5], ["2026-11-15T08:20:00Z", "mtg_frp", null],
  ["2026-11-15T10:28:00Z", "VIIRS_NOAA20_NRT", 62.3], ["2026-11-15T10:40:00Z", "MODIS_NRT", 41], ["2026-11-15T11:00:00Z", "mtg_spot", null], ["bad", "mtg_frp", 9]];
test("the strength chart: one point per measurement, grouped by who measured, with a legend and a table", () => {
  const {app} = page({meta: {local_offset_hours: 3}});
  const m = app.chartModel({timeline});
  assert.equal(m.pts.length, 4); assert.equal(m.blind.length, 2, "sightings without a measurement are kept apart"); assert.equal(m.max, 62.3);
  assert.deepEqual(plain(m.pts.map(p => p.group + "/" + p.shape)), ["geo/dot", "geo/dot", "viirs/tri", "modis/sq"]);
  assert.deepEqual([...new Set(m.blind.map(p => p.name))], ["Meteosat-12", "كاشف رصد الليلي"]);
  const old = app.chartModel({frp_series: [["2026-11-15T08:00:00Z", 30, "mtg"], ["2026-11-15T10:28:00Z", 62.3, "viirs"], ["2026-11-15T10:40:00Z", "NaN", "modis"], ["2026-11-15T11:00:00Z", 7]]});
  assert.deepEqual(plain(old.pts.map(p => p.name)), ["Meteosat-12", "VIIRS", "مصدر آخر"], "older rows carry the family only; an invalid number is no measurement");
  const svg = app.strengthChart({timeline});
  assert.match(svg, /<svg viewBox="0 0 360 176"/);
  assert.match(svg, /class="val"[^>]*>62 MW</, "the peak is the one value written on the chart");
  assert.match(svg, /class="legend">.*Meteosat-12.*VIIRS.*MODIS.*رصدة بلا قياس/s);
  assert.match(svg, /<details class="tbl"><summary>عرض القياسات \(4\)<\/summary>/);
  assert.match(svg, /data-tip="62 MW · 15\/11 13:28 · VIIRS"/, "Baghdad time under the pointer");
  assert.equal((svg.match(/class="hit"/g) || []).length, 4);
  assert.equal(app.strengthChart({timeline: timeline.slice(0, 1)}).includes("<svg"), false, "one measurement is a sentence, not a chart");
  assert.match(app.strengthChart({timeline: timeline.slice(0, 1)}), /قياس واحد حتى الآن: <b>30 MW<\/b>/);
  assert.equal(app.strengthChart({}), "");
  assert.deepEqual([68, 7, 0.4, 1200].map(v => +app.niceStep(v).toFixed(6)), [25, 2.5, 0.2, 500]);
});

test("the details explain the confidence, list who saw it, and compare a plant with its usual heat", () => {
  const {app, elements} = page({meta: {local_offset_hours: 3, local_name: "بغداد"}});
  const it = incident("plant", {category: "industrial", verdict: "مؤكد", confidence: 70, confidence_level: "متوسطة", confidence_label: "الثقة بأنه خارج المعتاد", severity: "مرتفع",
    alerted: true, max_frp: 68, typical_frp: 3.5, known_days: 19, timeline, agreement: "اتفقت عائلتان من الأقمار",
    evidence: [{s: "+", text: "قاسه أكثر من قمر"}, {s: "-", text: "قد يكون حرق غاز تشغيلياً"}, {s: "·", text: "حرارة معتادة هنا"}],
    sources: [{key: "mtg_frp", name: "Meteosat-12 · منتج الحرائق الرسمي", family: "mtg", first: "2026-11-15T08:00:00Z", last: "2026-11-15T08:20:00Z", n: 3, max_frp: 34.5}]});
  app.S.items = [it];
  app.openDetail("plant");
  const d = elements.get("#detail").innerHTML;
  assert.match(d, /class="dial v-likely" role="img" aria-label="الثقة بأنه خارج المعتاد 70 من 100"/);
  assert.match(d, /<span class="badge v-sure">مؤكد<\/span><span class="lvl">الثقة بأنه خارج المعتاد: متوسطة<\/span>/);
  assert.match(d, /<h3>لماذا هذه الدرجة؟<\/h3><ul class="ev">/);
  assert.equal((d.match(/class="s plus"/g) || []).length, 1); assert.equal((d.match(/class="s minus"/g) || []).length, 1); assert.equal((d.match(/class="s dotn"/g) || []).length, 1);
  assert.match(d, /<h3>من رآه ومتى<\/h3><table class="srcs">/);
  assert.match(d, /<td>15\/11 11:00<\/td><td>15\/11 11:20<\/td><td>3<\/td><td>35 MW<\/td>/, "first and last sighting in Baghdad time, how many, the strongest");
  assert.match(d, /اتفقت عائلتان من الأقمار/);
  assert.match(d, /<h3>الشدة عبر الوقت<\/h3>/);
  assert.match(d, /مقارنة بالمعتاد في هذا الموقع/);
  assert.match(d, /class="meter m-now">.*68 MW/s); assert.match(d, /نحو 19 ضعفاً من المعتاد/);
  assert.match(d, /class="tag sent"/);
  assert.match(d, /id="reportBtn"/);
  assert.match(d, /<video src=|لا يتوفر فيديو لهذا الحدث حالياً/);
  assert.equal(app.usualHTML(incident("fire", {typical_frp: 3, max_frp: 60})), "", "only a plant is compared with its usual heat");
});

test("«مؤشرات تستحق الانتباه»: facts in the details, a count on the card, and never a cause", () => {
  const {app} = page({meta: {local_offset_hours: 3, local_name: "بغداد"}});
  const signals = {n: 2, note: "هذه علامات تستحق النظر وليست حكماً: الأقمار ترى الحرارة ولا ترى سببها، ومعرفة السبب تحتاج مصدراً آخر كخبر أو بيان رسمي.",
    warn: [{key: "odd_spot", text: "الحرارة في نقطة لم تظهر فيها حرارة إلا نادراً منذ آذار 2024، بينما الشعلات المعتادة قربها على بعد 1.2 كم"},
           {key: "sudden", text: "بدأت فجأة وبقوة: 45 MW في أول نصف ساعة، ولم تظهر في الصورة التي قبلها"}],
    calm: [], wait: []};
  const it = incident("s1", {signals, place: "land", evidence: [{s: "+", text: "رصدته أقمار ناسا بثقة عادية"}]});
  const d = app.detailHTML(it);
  assert.match(d, /<section class="sig on"><h3>مؤشرات تستحق الانتباه <span class="tag sig-n">مؤشران<\/span><\/h3>/);
  assert.equal((d.match(/class="s warn"/g) || []).length, 2);
  assert.match(d, /الشعلات المعتادة قربها على بعد 1.2 كم/);
  assert.match(d, /هذه علامات تستحق النظر وليست حكماً/);
  assert.ok(d.indexOf('class="sig on"') < d.indexOf("لماذا هذه الدرجة؟"), "seen before the confidence reasons");
  assert.match(app.cardHTML(it), /class="tag sig-n"[^>]*>⚠ مؤشران</);
  assert.match(app.reportHTML(it), /<h2>مؤشرات تستحق الانتباه/);
  const calm = incident("s2", {signals: {n: 0, note: signals.note, warn: [], wait: [],
    calm: [{key: "usual_spot", text: "الحرارة في مكان شعلة معتادة: ظهرت حرارة فيه في نحو 62% من الأيام منذ آذار 2024"}]}});
  assert.match(app.detailHTML(calm), /<section class="sig"><h3>مؤشرات تستحق الانتباه: لا شيء لافت حتى الآن<\/h3>/);
  assert.match(app.detailHTML(calm), /class="s calm"/);
  assert.doesNotMatch(app.cardHTML(calm), /sig-n/, "no badge without a signal");
  assert.equal(app.signalsHTML(incident("s3")), "", "older data without signals shows nothing");
  for (const text of [d, app.reportHTML(it)]) for (const cause of ["هجوم", "استهداف", "ضربة"]) assert.ok(!text.includes(cause));
});

test("«تقرير الحدث»: everything on one printable page, and never a cause", () => {
  const {app} = page({meta: {local_offset_hours: 3, local_name: "بغداد"}});
  const it = incident("r1", {verdict: "مؤكد", confidence: 92, confidence_level: "عالية", severity: "حرج", timeline, snap: "clips/r1.jpg", alerted: true,
    evidence: [{s: "+", text: "رصدته أقمار ناسا بثقة عالية"}], facility: {text: "1.3 كم جنوب محطة كهرباء", km: 1.3},
    sources: [{key: "VIIRS_NOAA20_NRT", name: "NOAA-20 · VIIRS (ناسا)", first: "2026-11-15T10:28:00Z", last: "2026-11-15T10:28:00Z", n: 1, max_frp: 62.3}]});
  const r = app.reportHTML(it);
  assert.match(r, /ALFAQAAR<i>FIRMS<\/i>/); assert.match(r, /رصد · تقرير حدث حراري/);
  assert.match(r, /<h1>حريق جديد<\/h1>/);
  assert.match(r, /<span class="badge v-sure">مؤكد<\/span>/); assert.match(r, /الثقة: <b>92 من 100 \(عالية\)<\/b>/); assert.match(r, /الخطورة لو صحّ الحدث: <b>حرج<\/b>/);
  assert.match(r, /2026-11-15 الساعة 14:45 \(بغداد\)/); assert.match(r, /2026-11-15 الساعة 15:45 \(بغداد\)/);
  assert.match(r, /26\.00000, 50\.00000/); assert.match(r, /https:\/\/www\.google\.com\/maps\?q=26,50/);
  assert.match(r, /https:\/\/app\.test\/\?e=r1#r1/, "the link that opens this event in the app");
  assert.match(r, /رصدته أقمار ناسا بثقة عالية/); assert.match(r, /<table class="srcs">/); assert.match(r, /<svg viewBox/);
  assert.match(r, /1\.3 كم جنوب محطة كهرباء/); assert.match(r, /<img src="clips\/r1\.jpg"/);
  assert.match(r, /<b>يرصد النظام الحرارة ولا يحدد سببها\.<\/b>/);
  assert.doesNotMatch(r, /بسبب|نتيجة هجوم|قصف|ضرر مؤكد|الخليج العربي/);
  assert.doesNotMatch(app.reportHTML(incident("r2", {snap: null})), /<img/);
});

/* ---------- moving around ---------- */
test("sections and incidents are routes: «رجوع» and Escape always end on the map and the feed", () => {
  const items = [incident("a"), incident("b")];
  const {app, elements, context} = page({items});
  const hash = () => decodeURIComponent(context.location.hash.slice(1));
  app.setRoute("");
  assert.equal(app.S.tab, "feed"); assert.equal(elements.get("#viewFeed").hidden, false); assert.equal(elements.get("#viewStats").hidden, true);
  assert.match(elements.get("#tabs").innerHTML, /data-tab="feed" aria-selected="true"/);
  app.setTab("stats");
  assert.equal(hash(), "tab:stats"); assert.equal(elements.get("#viewStats").hidden, false); assert.equal(elements.get("#viewFeed").hidden, true);
  assert.match(elements.get("#viewStats").innerHTML, /class="hero"/);
  const step = app.NAV.i;
  app.setTab("sources");
  assert.equal(hash(), "tab:sources"); assert.equal(app.NAV.i, step, "from one section to another: the same step of the history, so one «رجوع» returns to the feed");
  app.go("a");
  assert.equal(app.S.sel, "a"); assert.equal(elements.get("#sheet").hidden, false); assert.equal(app.S.tab, "sources", "the section stays under the incident");
  assert.equal(app.NAV.i, step + 1);
  app.go("b");
  assert.equal(app.S.sel, "b"); assert.equal(app.NAV.i, step + 1, "another incident takes the first one's place");
  app.setRoute("tab:sources");
  assert.equal(app.S.sel, null); assert.equal(elements.get("#sheet").hidden, true);
  app.setRoute("");
  assert.equal(app.S.tab, "feed"); assert.equal(elements.get("#viewFeed").hidden, false);
  app.setRoute("a:i");
  assert.equal(app.S.sel, "a", "an old «details» link still opens its incident");
  app.setRoute("tab:nonsense");
  assert.equal(app.S.tab, "feed");
});

/* ---------- the private app ---------- */
test("what is typed in the key field goes to the door as typed, and the screen has a plain answer for every refusal", () => {
  const {app} = page();
  // The door tidies a subscriber's key itself and compares the manager's code letter by letter: a page
  // that reshaped the text would lock out a manager whose code looks like a key ("rsd…", or 20 letters).
  assert.match(html, /A\.login\(input\.value\.trim\(\)\)/, "sent exactly as typed");
  assert.doesNotMatch(html, /normalizeKey\(input\.value\)|formatKey/, "never reshaped by the page");
  const field = html.match(/<input id="lKey"[^>]*>/)[0];
  assert.match(field, /autocapitalize="off"/, "a phone keyboard must not turn the manager's code into capitals");
  assert.match(field, /autocomplete="off"/);
  assert.equal(app.looksLikeKey("rsd abcde"), true, "a key in the making is shown in capitals (style only)");
  assert.equal(app.looksLikeKey(" RSD-"), true);
  assert.equal(app.looksLikeKey("My long Manager code 2026!"), false);
  assert.match(html, /\.gate input\.keyish\{text-transform:uppercase\}/);
  const device = app.gateText("device");
  assert.match(device.state, /class="state warn"/); assert.match(device.state, /type="submit"/, "the same key can be tried again once the manager has approved");
  assert.equal(device.error, "");
  assert.match(app.gateText("expired").state, /انتهى الاشتراك/); assert.match(app.gateText("disabled").state, /الاشتراك موقوف/);
  assert.match(app.gateText("refused").state, /لم يُسمح بهذا الجهاز/); assert.match(app.gateText("bye").state, /تم تسجيل الخروج/);
  assert.equal(app.gateText("wrong").state, ""); assert.match(app.gateText("wrong").error, /المفتاح غير صحيح/);
  assert.deepEqual({...app.gateText("login")}, {state: "", error: "", down: false});
  const down = app.gateText("server", true);
  assert.equal(down.down, true, "the door is out of reach: no key field"); assert.match(down.state, /تعذّر الاتصال/); assert.match(down.state, /data-act="reboot"/);
  assert.equal(app.gateText("network", false).down, false, "after a sign-in attempt it is one line under the field");
  // with the sign-in library present its own sentences are shown
  const lib = page({globals: {RasadAccess: {errorText: code => "نص المكتبة: " + code}}});
  assert.equal(lib.app.gateText("wrong").error, "نص المكتبة: wrong");
  assert.match(lib.app.gateText("device").state, /نص المكتبة: device/);
});

test("when the door closes nothing protected stays on the screen", () => {
  const dropped = [];
  const {app, elements, body} = page({items: [incident("a")], meta: {generated: at(0.01)},
    globals: {caches: {keys: () => Promise.resolve(["firms-v18"]), delete: k => { dropped.push(k); return Promise.resolve(true); }}}});
  app.S.me = {id: "s1", name: "علي", role: "subscriber", expires: null};
  app.render();
  assert.match(elements.get("#list").innerHTML, /data-id="a"/);
  app.lock("expired");
  assert.equal(app.S.locked, true); assert.equal(app.S.me, null); assert.deepEqual([...app.S.items], []);
  assert.doesNotMatch(elements.get("#list").innerHTML, /data-id=/);
  assert.equal(elements.get("#gate").hidden, false); assert.ok(body.has("locked"));
  assert.match(elements.get("#lState").innerHTML, /انتهى الاشتراك/);
  assert.equal(elements.get("#acctBtn").hidden, true); assert.equal(elements.get("#adminBtn").hidden, true);
  return new Promise(done => setImmediate(() => { assert.deepEqual(dropped, ["firms-v18"], "the offline store is emptied"); done(); }));
});

const DAY = 864e5;
const sub = extra => ({id: "s1", name: "حيدر", note: "", status: "active", disabled: false, created: NOW - 40 * DAY, expires: NOW + 2 * DAY, key_hint: "3F9Q", max_devices: 1,
  devices: [{id: "d1", label: "آيفون · Safari", bound_at: NOW - 39 * DAY, last_seen: NOW - 5 * 60000}], pending: [], last_login: NOW - DAY, last_seen: NOW - 5 * 60000, last_open: NOW - 5 * 60000,
  city: "بغداد", country: "IQ", online: true, ...extra});
test("the manager's panel: plain words for every state, and a key message ready to send", () => {
  const {app} = page();
  assert.deepEqual({...app.expiryInfo({expires: null}, NOW)}, {days: null, late: false, text: "بلا نهاية"});
  assert.equal(app.expiryInfo({expires: NOW + 2 * DAY}, NOW).late, true, "three days or less is red");
  assert.match(app.expiryInfo({expires: NOW + 2 * DAY}, NOW).text, /\(بقي يومان\)$/);
  assert.equal(app.expiryInfo({expires: NOW + 27 * DAY}, NOW).late, false);
  assert.match(app.expiryInfo({expires: NOW + 27 * DAY}, NOW).text, /^12 كانون الأول 2026 \(بقي 27 يوماً\)$/);
  assert.match(app.expiryInfo({expires: NOW - 5 * DAY}, NOW).text, /\(انتهى قبل 5 أيام\)$/);
  assert.match(app.expiryInfo({expires: NOW + 3600e3}, NOW).text, /\(بقي يوم واحد\)$/);
  const st = {now: NOW, telegram: true, code_weak: true, online: [{id: "s1", name: "حيدر", city: "بغداد", country: "IQ"}],
    subs: [sub({pending: [{rid: "r1", label: "ويندوز · Chrome", t: NOW - 600e3, city: "البصرة", country: "IQ"}]}),
      sub({id: "s2", name: "سارة <b>", status: "waiting", devices: [], expires: NOW + 6 * DAY, online: false, last_seen: null, last_open: null, last_login: null}),
      sub({id: "s3", name: "مصطفى", status: "expired", expires: NOW - 5 * DAY, online: false}), sub({id: "s4", name: "عمر", status: "disabled", disabled: true, expires: null, online: false})],
    log: [{t: NOW - 600e3, event: "requested", id: "s1", name: "حيدر", city: "البصرة", country: "IQ", device: "ويندوز · Chrome"}, {t: NOW - 7200e3, event: "failed", id: "", name: "", city: "طهران", country: "IR", device: ""}]};
  const ui = {q: "", filter: "all", secret: null, edit: null, form: {name: "", valid: "30", date: "", note: "", devices: "1"}};
  const h = app.adminHTML(st, ui, NOW);
  assert.match(h, /رمز المدير قصير:<\/b> اختر رمزاً أطول من إعدادات GitHub/);
  assert.match(h, /<b>4<\/b><span>المشتركون/); assert.match(h, /<b>1<\/b><span>نشطون الآن/); assert.match(h, /<b>2<\/b><span>ينتهون خلال 7 أيام/);
  assert.match(h, /class="tile hot"><b>1<\/b><span>طلبات أجهزة معلّقة/);
  assert.match(h, /حيدر يطلب الدخول من جهاز جديد/); assert.match(h, /data-do="approve">موافقة</); assert.match(h, /data-do="reject">رفض</);
  for (const word of ["نشط", "بانتظار أول دخول", "منتهٍ", "موقوف"]) assert.ok(h.includes(`>${word}</span></div>`), word);
  assert.match(h, /class="late">17 تشرين الثاني 2026 \(بقي يومان\)/);
  assert.match(h, /لا جهاز مرتبط بعد/); assert.match(h, /آخر نشاط: لم يدخل بعد/); assert.match(h, /آخر نشاط: قبل 5 دقائق · بغداد، العراق/);
  for (const act of ["extend", "edit", "logout", "disable", "enable", "rekey", "delete", "unbind"]) assert.ok(h.includes(`data-do="${act}"`), act);
  assert.match(h, /سارة &lt;b&gt;/, "a name is always escaped"); assert.doesNotMatch(h, /سارة <b>/);
  assert.match(h, /طلب جهاز جديد|طلب دخول من جهاز جديد/); assert.match(h, /class="bad">مفتاح خاطئ/);
  assert.doesNotMatch(h, /class="secret"/, "no key on the screen unless one was just made");
  // search and status filter
  assert.equal((app.subsHTML(st, {...ui, q: "مصطفي"}, NOW).match(/class="subcard"/g) || []).length, 1, "the search ignores spelling differences");
  assert.equal((app.subsHTML(st, {...ui, filter: "waiting"}, NOW).match(/class="subcard"/g) || []).length, 1);
  assert.match(app.subsHTML(st, {...ui, q: "لا أحد"}, NOW), /لا مشترك يطابق البحث/);
  assert.match(app.subsHTML({subs: []}, ui, NOW), /لا مشتركين بعد/);
  assert.match(app.subsHTML(st, {...ui, edit: "s1"}, NOW), /<form class="edit" data-edit="s1">/);
  // the key is shown once, with a message ready to send
  const secret = {name: "سارة", key: "RSD-ABCDE-FGHJK-LMNPQ-RSTUV", devices: 1, expires: NOW + 30 * DAY, link: "https://app.test/"};
  const shown = app.adminHTML(st, {...ui, secret}, NOW);
  assert.match(shown, /class="secret"><b>مفتاح سارة<\/b> · يظهر مرة واحدة فقط/); assert.match(shown, /<code>RSD-ABCDE-FGHJK-LMNPQ-RSTUV<\/code>/);
  const msg = app.keyMessage(secret);
  assert.match(msg, /^مرحباً سارة،\n/); assert.match(msg, /\nRSD-ABCDE-FGHJK-LMNPQ-RSTUV\n/); assert.match(msg, /رابط التطبيق: https:\/\/app\.test\/\n/);
  assert.match(msg, /يعمل على جهاز واحد/); assert.match(msg, /ينتهي الاشتراك: 15 كانون الأول 2026$/);
  assert.match(app.keyMessage({...secret, devices: 2, expires: null}), /يعمل على 2 أجهزة.*\nالاشتراك بلا تاريخ انتهاء\.$/);
});

/* ---------- search and the page itself ---------- */
test("the top search offers live incidents and watched places while typing", () => {
  const items = [incident("a", {headline: "حرارة غير معتادة في منشأة نفطية في منطقة الأحمدي", last: at(1)}), incident("b", {headline: "حريق جديد", where: "قرب الأحمدي", last: at(0.5)}), incident("c", {headline: "حريق في البحر"})];
  const {app} = page({items});
  const places = [{name: "ميناء الأحمدي", lat: 29.07, lon: 48.13}];
  assert.deepEqual(plain(app.suggestions("الاحمدي", items, places).map(s => s.kind === "place" ? "place:" + s.name : s.it.id)), ["place:ميناء الأحمدي", "b", "a"], "places first, then incidents by last sighting");
  assert.equal(app.suggestions("", items, places).length, 0);
  assert.equal(app.suggestions("لا شيء", items, places).length, 0);
  assert.deepEqual({...app.parseCoords("26.23, 50.83")}, {lat: 26.23, lon: 50.83});
  assert.deepEqual({...app.parseCoords("٢٦٫٢٣، ٥٠٫٨٣")}, {lat: 26.23, lon: 50.83});
  assert.equal(app.parseCoords("مطار البصرة"), null); assert.equal(app.parseCoords("95, 50"), null);
});

test("the page keeps what the before/after screen and the phone need", () => {
  for (const id of ["baSheet", "pickBar", "pickCancel", "baBtn", "finder", "finderQ", "shot", "toast", "map", "sheet", "detail", "list", "summary", "status", "learning", "gate", "adminSheet", "report"])
    assert.ok(html.includes(`id="${id}"`), id);
  for (const name of ["setRoute", "route", "toast", "copy", "bidi", "esc", "presentPicture", "hasBA", "incidentById", "go"])
    assert.match(declarations, new RegExp(`(function ${name}\\(|const ${name} = )`), name + " is still a global of the page");
  assert.match(declarations, /^const \$ = /m);
  assert.doesNotMatch(html, /الخليج العربي/);
  assert.match(html, /viewport-fit=cover/); assert.match(html, /prefers-reduced-motion/); assert.match(html, /@media print/);
  assert.match(html, /preload="none"/); assert.doesNotMatch(html, /<video[^>]*autoplay/);
  assert.match(sw, /const CACHE = "firms-v24"/); assert.match(html, /const APP_V = 24;/);
  // the three sizes of the phone's sheet are states of the page, not page reloads
  for (const state of ["sheet-peek", "sheet-half", "sheet-full"]) assert.ok(html.includes(state), state);
});
