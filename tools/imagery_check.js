#!/usr/bin/env node
"use strict";
// Real-data check of the app's imagery engine (web/imagery.js): what a phone does for one place,
// done here in Node against the real archives (Earth Search + AWS for Sentinel-2, Microsoft's
// Planetary Computer for Landsat). It prints what was found, the time, megabytes and requests of
// each step, and keeps small pictures to look at. Nothing is sent anywhere.
//   node tools/imagery_check.js --lat 30.45 --lon 47.72 [--half 2] [--wide 20] [--days 90] [--out probe-out]
// Run it where the archives can be reached: the probe workflow (python -m rasad imagery-check ...).
const fs = require("node:fs"), path = require("node:path"), zlib = require("node:zlib");
const I = require("../web/imagery.js");

const argv = process.argv.slice(2);
const arg = (name, fallback) => { const i = argv.indexOf("--" + name); return i >= 0 && argv[i + 1] !== undefined ? argv[i + 1] : fallback; };
const LAT = +arg("lat"), LON = +arg("lon"), HALF = +arg("half", 2), WIDE = +arg("wide", 20), DAYS = +arg("days", 90);
const OUT = arg("out", "probe-out"), TAG = arg("tag", "imagery");
if (!Number.isFinite(LAT) || !Number.isFinite(LON)) { console.error("give --lat and --lon"); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const engine = I.createEngine();
const say = text => console.log(text);
const mb = bytes => (bytes / 1048576).toFixed(2) + " MB";
const dayOf = p => new Date(p.time).toISOString().slice(0, 16) + "Z";
const failures = [];

// one step: its time, what it downloaded, and its own failure without stopping the rest
async function step(name, work) {
  const t0 = Date.now(), b0 = engine.stats.bytes, r0 = engine.stats.requests;
  try {
    const result = await work();
    say(`OK   ${name} · ${((Date.now() - t0) / 1000).toFixed(1)} s · ${mb(engine.stats.bytes - b0)} · ${engine.stats.requests - r0} requests`);
    return result;
  } catch (e) {
    failures.push(name);
    say(`FAIL ${name} · ${((Date.now() - t0) / 1000).toFixed(1)} s · ${e && (e.code || e.name)}: ${e && e.message}`);
    return null;
  }
}

/* ---------- pictures: RGBA -> a small PNG ---------- */
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
const crc32 = buf => { let c = 0xffffffff; for (let i = 0; i < buf.length; i++) c = CRC[(c ^ buf[i]) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type), data]), crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
// rgba: n x n picture; `over`: an optional n x n overlay with its own alpha; kept at most `max` points wide
function savePicture(name, rgba, n, over, max = 420) {
  const k = Math.max(1, Math.ceil(n / max)), w = Math.floor(n / k);
  const raw = Buffer.alloc((w * 3 + 1) * w);
  for (let y = 0; y < w; y++) {
    raw[y * (w * 3 + 1)] = 0;
    for (let x = 0; x < w; x++) {
      const i = ((y * k) * n + x * k) * 4, o = y * (w * 3 + 1) + 1 + x * 3;
      let r = rgba[i], g = rgba[i + 1], b = rgba[i + 2];
      if (over && over[i + 3]) { const a = over[i + 3] / 255; r = r * (1 - a) + over[i] * a; g = g * (1 - a) + over[i + 1] * a; b = b * (1 - a) + over[i + 2] * a; }
      raw[o] = r; raw[o + 1] = g; raw[o + 2] = b;
    }
  }
  const head = Buffer.alloc(13); head.writeUInt32BE(w, 0); head.writeUInt32BE(w, 4); head[8] = 8; head[9] = 2;
  const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", head), chunk("IDAT", zlib.deflateSync(raw, { level: 9 })), chunk("IEND", Buffer.alloc(0))]);
  const file = path.join(OUT, `${TAG}_${name}.png`);
  fs.writeFileSync(file, png);
  say(`     picture ${file} (${w} x ${w}, ${(png.length / 1024).toFixed(0)} KB)`);
}
const scalars = o => Object.fromEntries(Object.entries(o || {}).filter(([, v]) => ["number", "string", "boolean"].includes(typeof v))
  .map(([k, v]) => [k, typeof v === "number" && !Number.isInteger(v) ? +v.toFixed(4) : v]));
const stat = values => {              // of a band or an index: how many points hold a number, and its middle values
  const v = Array.from(values).filter(x => x === x).sort((a, b) => a - b);
  const q = f => v.length ? +v[Math.min(v.length - 1, Math.floor(f * v.length))].toFixed(4) : null;
  return { n: v.length, of: values.length, q05: q(0.05), q50: q(0.5), q95: q(0.95) };
};

(async () => {
  const to = Date.now(), from = to - DAYS * 864e5;
  say(`imagery check · ${LAT}, ${LON} · box ${2 * HALF} km, wide box ${2 * WIDE} km · last ${DAYS} days · engine version ${I.VERSION}`);
  const grid = I.makeGrid(LAT, LON, HALF);
  say(`grid ${grid.size} x ${grid.size} points of ${grid.res} m`);

  /* ---------- 1. which passes ---------- */
  const passes = await step("list of passes", () => engine.listPasses(grid, from, to)) || [];
  const kinds = {};
  for (const p of passes) { const k = `${p.sensor || "sentinel"}/${p.platform || "?"}`; kinds[k] = (kinds[k] || 0) + 1; }
  say(`     ${passes.length} passes: ${JSON.stringify(kinds)}`);
  if (passes[0]) say(`     a pass holds: ${Object.keys(passes[0]).join(", ")}`);
  for (const p of passes.slice(0, 14)) say(`     ${dayOf(p)} ${JSON.stringify(scalars(p)).slice(0, 230)}`);

  /* ---------- 2. how clear each is over the box itself ---------- */
  const own = passes.filter(p => p.sensor !== "landsat").slice(0, 10);
  await step(`sky of the newest ${own.length} Sentinel-2 passes`, async () => {
    await Promise.all(own.map(p => engine.assessPass(p, grid)));
    for (const p of own) say(`     ${dayOf(p)} grade ${I.grade(p)} ${JSON.stringify(scalars(p.sky))}`);
  });
  const pair = I.pickPair(own);
  say(`     pair: before ${pair.before ? dayOf(pair.before) : "-"} · after ${pair.after ? dayOf(pair.after) : "-"} · same track: ${I.sameView(pair.before, pair.after)}`);

  /* ---------- 3. the two pictures at full detail, and what the viewer works out ---------- */
  let before = null, after = null;
  if (pair.after) after = await step("after: the scene at full detail", () => engine.loadScene(pair.after, grid, "full"));
  if (pair.before) before = await step("before: the scene at full detail", () => engine.loadScene(pair.before, grid, "full"));
  if (after) {
    say(`     after bands: ${JSON.stringify(Object.fromEntries(["red", "green", "blue", "nir08", "swir16", "swir22"].map(b => [b, stat(after[b])])))}`);
    const stretch = I.stretchOf(before ? [before, after] : [after], ["red", "green", "blue"]);
    savePicture("after_color", I.comboImage(after, "color", stretch), grid.size);
    if (before) savePicture("before_color", I.comboImage(before, "color", stretch), grid.size);
    savePicture("after_swir", I.comboImage(after, "swir"), grid.size);
    savePicture("after_fire", I.comboImage(after, "fire"), grid.size);
    savePicture("after_nbr", I.comboImage(after, "nbr"), grid.size);
    say(`     NBR of after: ${JSON.stringify(stat(I.indexOf(after, "nbr")))}`);
    const hot = I.hotSpots(after);
    say(`     hot points in after: ${JSON.stringify(scalars(hot))} ${Array.isArray(hot) ? hot.length : (hot.spots || hot.points || []).length}`);
  }
  if (before && after) {
    await step("change between the two", async () => {
      const change = I.changeOf(before, after);
      say(`     change: ${JSON.stringify(scalars(change))}`);
      const color = I.comboImage(after, "color", I.stretchOf([before, after], ["red", "green", "blue"]));
      savePicture("change_over_after", color, grid.size, I.changeImage(change));
      const burn = I.burnOf(before, after, change);
      say(`     burn layer: ${JSON.stringify(scalars(burn))} classes (ha) ${JSON.stringify(Array.from(burn.classHa).map(x => +x.toFixed(2)))}`);
      savePicture("burn_over_after", color, grid.size, I.burnImage(burn));
    });
  }

  /* ---------- 4. band 8 on demand (the 10 m near infrared): false colour and NDVI ---------- */
  if (pair.after) {
    const veg = await step("band 8 on demand (false colour, NDVI)", () => engine.loadBands(pair.after, grid, ["nir", "red", "green"], "full"));
    if (veg) {
      say(`     band 8 came from: ${JSON.stringify(veg.from)} · ${JSON.stringify(stat(veg.nir))}`);
      savePicture("after_veg", I.comboImage(veg, "veg"), grid.size);
      say(`     NDVI: ${JSON.stringify(stat(I.indexOf(veg, "ndvi")))}`);
      savePicture("after_ndvi", I.comboImage(veg, "ndvi"), grid.size);
    }
    await step("the same bands again (from memory)", () => engine.loadBands(pair.after, grid, ["nir", "red", "green"], "full"));
    say(`     memory: ${JSON.stringify(engine.memory())}`);
  }

  /* ---------- 5. a wide box: several tiles of one pass joined ---------- */
  if (WIDE > 0) {
    const wide = I.makeGrid(LAT, LON, WIDE);
    say(`wide grid ${wide.size} x ${wide.size} points of ${wide.res} m`);
    const list = await step("wide box: list of passes", () => engine.listPasses(wide, to - 30 * 864e5, to)) || [];
    const sentinel = list.filter(p => p.sensor !== "landsat");
    say(`     ${list.length} passes, of them joined from several tiles: ${sentinel.filter(p => p.parts && p.parts.length > 1).length}`);
    for (const p of sentinel.slice(0, 6)) say(`     ${dayOf(p)} parts ${p.parts ? p.parts.length : 1} ${JSON.stringify(scalars(p)).slice(0, 200)}`);
    const some = sentinel.slice(0, 5);
    await step(`wide box: sky of ${some.length} passes`, async () => {
      await Promise.all(some.map(p => engine.assessPass(p, wide)));
      for (const p of some) say(`     ${dayOf(p)} grade ${I.grade(p)} ${JSON.stringify(scalars(p.sky))}`);
    });
    const best = I.pickPair(some).after || some[0];
    if (best) {
      const scene = await step(`wide box: colour picture of ${dayOf(best)} (parts ${best.parts ? best.parts.length : 1})`, () => engine.loadBands(best, wide, ["red", "green", "blue"], "fast"));
      if (scene) { say(`     red: ${JSON.stringify(stat(scene.red))}`); savePicture("wide_color", I.comboImage(scene, "color"), wide.size); }
    }
  }

  /* ---------- 6. Landsat 8/9 as an extra date ---------- */
  const landsat = passes.filter(p => p.sensor === "landsat");
  say(`Landsat passes: ${landsat.length}`);
  if (landsat[0]) {
    const p = landsat[0];
    await step(`Landsat ${dayOf(p)}: sky`, async () => { await engine.assessPass(p, grid); say(`     grade ${I.grade(p)} ${JSON.stringify(scalars(p.sky))}`); });
    const scene = await step(`Landsat ${dayOf(p)}: scene`, () => engine.loadScene(p, grid, "full"));
    if (scene) {
      say(`     bands: ${JSON.stringify(Object.fromEntries(["red", "nir08", "swir22"].map(b => [b, stat(scene[b])])))}`);
      savePicture("landsat_color", I.comboImage(scene, "color"), grid.size);
      savePicture("landsat_nbr", I.comboImage(scene, "nbr"), grid.size);
    }
  }

  say(`total: ${mb(engine.stats.bytes)} in ${engine.stats.requests} requests · memory ${JSON.stringify(engine.memory())}`);
  say(failures.length ? `FAILED STEPS: ${failures.join(" | ")}` : "all steps passed");
  process.exit(failures.length ? 1 : 0);
})().catch(e => { console.error("imagery check stopped:", e); process.exit(2); });
