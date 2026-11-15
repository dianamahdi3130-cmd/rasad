"use strict";
// The before/after imagery engine (web/imagery.js) against a made-up archive: no network.

const assert = require("node:assert/strict");
const test = require("node:test");
const vm = require("node:vm");
const zlib = require("node:zlib");
const I = require("../web/imagery.js");
const {makeArchive, makeCog, SEARCH, BASE, FIELD, LANDSAT_API, LANDSAT_FILES} = require("./support/archive.js");

const PLACE = {lat: 26.0, lon: 50.0};
const FROM = Date.parse("2026-08-01T00:00:00Z"), TO = Date.parse("2026-11-10T00:00:00Z");
const near = (a, b, eps, what) => assert.ok(Math.abs(a - b) <= eps, `${what || "value"}: ${a} is not within ${eps} of ${b}`);
function setup(passes, options = {}) {
  const archive = makeArchive({...PLACE, passes});
  const engine = I.createEngine({fetch: archive.fetch, ...options});
  return {archive, engine, grid: I.makeGrid(PLACE.lat, PLACE.lon, 1)};
}
const code = async promise => { try { await promise; return "ok"; } catch (e) { return e.code || String(e); } };

/* ---------- projection ---------- */
test("UTM matches reference values, also at the zone edges 42°E, 48°E, 54°E and 60°E", () => {
  const reference = [              // from PROJ (pyproj), EPSG:326xx
    [26.5, 42.0, 37, 799033.456, 2934552.841], [26.5, 42.0, 38, 200966.544, 2934552.841],
    [30.0, 48.0, 38, 789409.653, 3322575.904], [30.0, 48.0, 39, 210590.347, 3322575.904],
    [26.0, 54.0, 39, 800318.075, 2879132.534], [26.0, 54.0, 40, 199681.925, 2879132.534],
    [25.3, 60.0, 40, 802078.143, 2801548.764], [25.3, 60.0, 41, 197921.857, 2801548.764],
    [12.1017, 35.5192, 36, 774215.726, 1339063.906], [33.3, 44.4, 38, 444140.545, 3684706.356],
    [26.2361, 50.0393, 39, 404045.627, 2902186.496],
  ];
  for (const [lat, lon, zone, e, n] of reference) {
    const [E, N] = I.UTM.forward(lat, lon, zone);
    near(E, e, 0.002, `easting ${lat},${lon} zone ${zone}`); near(N, n, 0.002, `northing ${lat},${lon} zone ${zone}`);
    const [la, lo] = I.UTM.inverse(E, N, zone);
    near(la, lat, 1e-8, "latitude back"); near(lo, lon, 1e-8, "longitude back");
  }
  near(I.UTM.forward(-4.5, 39, 37, true)[1], 9502604.535, 0.002, "southern hemisphere");
});

test("the projection of a tile is read from either archive's wording", () => {
  assert.deepEqual(I.projectionOf({"proj:code": "EPSG:32639"}), {zone: 39, south: false});
  assert.deepEqual(I.projectionOf({"proj:epsg": 32736}), {zone: 36, south: true});
  assert.deepEqual(I.projectionOf({"grid:code": "MGRS-40RCN"}), {zone: 40, south: false});
  assert.throws(() => I.projectionOf({}), /projection/);
});

test("the picture grid is a north-up square of the asked width, 10 m pixels, larger for big boxes", () => {
  const g = I.makeGrid(26, 50, 2);
  assert.equal(g.size, 400); assert.equal(g.res, 10);
  near((g.north - g.south) * 111320 / 1.0, 4000, 25, "height in metres");
  near((g.east - g.west) * 111320 * Math.cos(26 * Math.PI / 180), 4000, 25, "width in metres");
  const [x, y] = I.gridPixel(g, 26, 50);
  near(x, 200, 1e-6); near(y, 200, 1e-6);
  const [lat, lon] = I.gridPoint(g, 0, 0);
  near(lat, g.north, 1e-12); near(lon, g.west, 1e-12);
  const big = I.makeGrid(26, 50, 10);
  assert.equal(big.size, 800); assert.equal(big.res, 25);
  assert.throws(() => I.makeGrid(95, 50, 2), /place/);
  // the filled-in UTM grid agrees with projecting every pixel directly
  const {E, N} = I.gridUTM(g, {zone: 39, south: false});
  for (const [px, py] of [[0, 0], [399, 0], [123, 257], [399, 399], [17, 390]]) {
    const [la, lo] = I.gridPoint(g, px + 0.5, py + 0.5), [e, n] = I.UTM.forward(la, lo, 39);
    near(E[py * 400 + px], e, 0.001, "easting of a pixel"); near(N[py * 400 + px], n, 0.001, "northing of a pixel");
  }
});

/* ---------- decompression ---------- */
test("the built-in decompressor agrees with zlib on every kind of block", () => {
  const texts = [
    Buffer.alloc(0), Buffer.from("a"), Buffer.alloc(70000, 7),
    Buffer.from(Array.from({length: 50000}, (_, i) => (i * 7919) % 251)),
    Buffer.from(Array.from({length: 120000}, (_, i) => Math.floor(Math.sin(i / 37) * 100 + 128 + (i % 5)))),
    Buffer.from(Array.from({length: 40000}, () => Math.floor(Math.random() * 256))),
  ];
  for (const raw of texts) for (const level of [0, 1, 6, 9]) for (const strategy of [zlib.constants.Z_DEFAULT_STRATEGY, zlib.constants.Z_FIXED]) {
    const packed = zlib.deflateSync(raw, {level, strategy});
    assert.deepEqual(Buffer.from(I.inflate(new Uint8Array(packed), raw.length)), raw, `level ${level}, ${raw.length} bytes`);
  }
  const packed = new Uint8Array(zlib.deflateSync(Buffer.alloc(5000, 3)));
  assert.throws(() => I.inflate(packed, 4000), /damaged/);                      // more data than the tile can hold
  assert.throws(() => I.inflate(packed.subarray(0, 12), 5000), /damaged/);      // cut short
  assert.throws(() => I.inflate(new Uint8Array([1, 2, 3, 4, 5, 6, 7]), 10), /zlib/);
});

/* ---------- reading the pictures ---------- */
test("a window is read from the right tiles and pixels, across tile borders, with reflectance scaling", async () => {
  const {archive, engine, grid} = setup([{date: "2026-11-04"}]);
  const [pass] = await engine.listPasses(grid, FROM, TO);
  const scene = await engine.loadScene(pass, grid, "full");
  assert.equal(scene.red.length, 200 * 200);
  // the made-up ground is a plane: every pixel must equal it at that pixel's own place
  const {E, N} = I.gridUTM(grid, pass.proj);
  for (const band of ["blue", "green", "red", "nir08", "swir16", "swir22"]) {
    for (const i of [0, 199, 200 * 100 + 100, 200 * 199, 200 * 200 - 1, 200 * 57 + 131]) {
      near(scene[band][i], BASE[band] + 2e-5 * (E[i] - archive.Ec) + 1e-5 * (N[i] - archive.Nc), 6e-5, `${band} at pixel ${i}`);
    }
  }
  assert.ok(scene.scl.every(c => c === 5));
  // the box sits on the corner of four 10 m tiles: all four were fetched, and nothing but ranges
  const ranges = archive.calls.filter(c => c.url.endsWith("B04.tif")).map(c => c.range);
  assert.equal(ranges.length, 5, "one header read and four tiles");
  assert.ok(archive.calls.filter(c => /\.tif$/.test(c.url)).every(c => /^bytes=\d+-\d+$/.test(c.range)));
});

test("the fast quality reads the half-size overview and still lands on the same ground", async () => {
  const {archive, engine, grid} = setup([{date: "2026-11-04", burn: true}]);
  const [pass] = await engine.listPasses(grid, FROM, TO);
  const fast = await engine.loadScene(pass, grid, "fast");
  const before = engine.stats.bytes;
  assert.equal(fast.res, 20);
  const full = await engine.loadScene(pass, grid, "full");
  assert.equal(full.res, 10);
  assert.ok(engine.stats.bytes - before > 0, "the sharper picture needs more data");
  const [x, y] = I.gridPixel(grid, ...I.UTM.inverse(archive.Ec + 300, archive.Nc + 200, archive.zone));
  const at = Math.floor(y) * 200 + Math.floor(x), away = 200 * 20 + 20;
  for (const s of [fast, full]) {
    assert.ok(s.red[at] < 0.16, "the burnt square is dark where it should be");
    assert.ok(s.red[away] > 0.25);
  }
  // 20 m bands were already in memory: not fetched twice
  assert.equal(archive.calls.filter(c => c.url.endsWith("B12.tif")).length, 2, "header and one tile, once");
});

test("where a picture has nothing (swath edge) the grid is empty, not zero", async () => {
  const {engine, grid} = setup([{date: "2026-11-04", footprint: "west"}]);
  const [pass] = await engine.listPasses(grid, FROM, TO);
  near(pass.cover, 0.5, 0.07, "share of the box covered");
  const scene = await engine.loadScene(pass, grid, "full");
  assert.ok(Number.isNaN(scene.red[200 * 100 + 190]), "east half empty");
  assert.ok(scene.red[200 * 100 + 10] > 0.2, "west half filled");
  assert.equal(I.colorImage(scene)[(200 * 100 + 190) * 4 + 3], 0, "empty pixels are transparent");
  assert.equal(I.colorImage(scene)[(200 * 100 + 10) * 4 + 3], 255);
});

test("a TIFF layout the reader does not know is refused, not misread", async () => {
  const cog = makeCog({size: 64, tile: 64, res: 10, x0: 0, y0: 0, value: () => 5, levels: 1});
  const big = Buffer.from(cog); big.write("MM");
  for (const [file, pattern] of [[big, /TIFF/], [Buffer.from("not a tiff at all, sorry"), /TIFF/]]) {
    const engine = I.createEngine({fetch: async () => ({ok: true, status: 206, body: null, arrayBuffer: async () => file.buffer.slice(file.byteOffset, file.byteOffset + file.length)})});
    await assert.rejects(engine.openCog("https://x/y.tif"), pattern);
  }
});

/* ---------- the list of passes ---------- */
test("passes: one entry per satellite pass, newest first, the reprocessed archive preferred", async () => {
  const {engine, grid, archive} = setup([
    {date: "2026-10-20"}, {date: "2026-11-04", platform: "sentinel-2c"},
    {date: "2026-11-04", platform: "sentinel-2c", collection: "sentinel-2-l2a"},     // the same pass in the older archive
    {date: "2026-11-04", platform: "sentinel-2c", tile: "39RYY", footprint: "west"}, // a neighbouring tile that cuts the box
    {date: "2026-09-01", collection: "sentinel-2-l2a"},                              // only in the older archive
  ]);
  archive.pageSize = 2;                                                              // the index answers in pages
  const passes = await engine.listPasses(grid, FROM, TO);
  assert.deepEqual(passes.map(p => p.date), ["2026-11-04", "2026-10-20", "2026-09-01"]);
  assert.equal(passes[0].grid, "39RXX"); assert.equal(passes[0].collection, "sentinel-2-c1-l2a"); assert.equal(passes[0].cover, 1);
  assert.equal(passes[2].collection, "sentinel-2-l2a");
  assert.equal(archive.calls.filter(c => c.url === SEARCH + "/search").length, 3, "three pages");
  const asked = JSON.parse(archive.calls[0].body);
  assert.deepEqual(asked.fields.exclude.includes("assets"), true, "the long list of files is not downloaded");
  assert.equal(asked.bbox.length, 4);
  assert.deepEqual(await engine.listPasses(grid, Date.parse("2020-01-01"), Date.parse("2020-02-01")), []);
});

test("a pass in the older archive is read through its own record (other addresses, other scaling)", async () => {
  const {engine, grid, archive} = setup([{date: "2026-09-01", collection: "sentinel-2-l2a", offset: 0}]);
  const [pass] = await engine.listPasses(grid, FROM, TO);
  const scene = await engine.loadScene(pass, grid, "fast");
  near(scene.red[200 * 100 + 100], BASE.red, 2e-3, "reflectance with that archive's offset");
  assert.equal(archive.calls.filter(c => c.url.includes("/items/")).length, 1);
});

test("if the files are not where the name says, the record is asked once and the picture still loads", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04", realDir: "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/moved/"}]);
  const [pass] = await engine.listPasses(grid, FROM, TO);
  const sky = await engine.assessPass(pass, grid);
  assert.equal(sky.hidden, 0);
  assert.equal(archive.calls.filter(c => c.url.includes("/items/")).length, 1);
  assert.ok((await engine.loadScene(pass, grid, "fast")).red[0] > 0.2);
  assert.equal(archive.calls.filter(c => c.url.includes("/items/")).length, 1, "not asked again");
});

test("Landsat passes are listed beside Sentinel-2's and read with their token, scale and quality bits", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04"}, {date: "2026-10-20", sensor: "landsat"},
    {date: "2026-10-28", sensor: "landsat", cloud: "all"}]);
  const passes = await engine.listPasses(grid, FROM, TO);
  assert.deepEqual(passes.map(p => [p.date, p.sensor || "sentinel"]), [["2026-11-04", "sentinel"], ["2026-10-28", "landsat"], ["2026-10-20", "landsat"]]);
  const [, cloudy, clear] = passes;
  assert.equal((await engine.assessPass(clear, grid)).hidden, 0);
  near((await engine.assessPass(cloudy, grid)).hidden, 1, 1e-9, "cloud from the quality bits");
  const scene = await engine.loadScene(clear, grid, "fast");
  assert.equal(scene.res, 30, "30 m whatever was asked");
  const {E, N} = I.gridUTM(grid, clear.proj);
  for (const band of ["blue", "red", "nir08", "swir22"]) for (const i of [0, 200 * 100 + 100, 200 * 200 - 1])
    near(scene[band][i], BASE[band] + 2e-5 * (E[i] - archive.Ec) + 1e-5 * (N[i] - archive.Nc), 5e-5, `${band} at pixel ${i}`);
  assert.ok(scene.scl.every(c => c === 5));
  assert.equal(archive.tokens, 1, "the token is asked for once, however many tiles start together");
  const files = archive.calls.filter(c => c.url.startsWith(LANDSAT_FILES));
  assert.ok(files.length >= 14 && files.every(c => c.url.endsWith("?sig=test") && c.range), "every file request is signed and by range");
  assert.deepEqual([...I.qaClasses(new Uint8Array([1, 0, 64, 8, 2, 16, 4, 192]))], [0, 0, 5, 9, 9, 3, 10, 6]);
});

test("if Landsat's index does not answer, the Sentinel-2 passes are still listed; it can be switched off", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04"}, {date: "2026-10-20", sensor: "landsat"}]);
  archive.fail = url => url.startsWith(LANDSAT_API) ? "down" : null;
  assert.deepEqual((await engine.listPasses(grid, FROM, TO)).map(p => p.date), ["2026-11-04"]);
  archive.fail = null;
  const plain = I.createEngine({fetch: archive.fetch, landsat: false});
  archive.calls.length = 0;
  assert.deepEqual((await plain.listPasses(grid, FROM, TO)).map(p => p.date), ["2026-11-04"]);
  assert.ok(archive.calls.every(c => !c.url.startsWith(LANDSAT_API)));
});

/* ---------- the sky and the default pair ---------- */
test("cloud is judged over the box itself, and the default pair is the nearest clear passes", async () => {
  const {engine, grid} = setup([
    {date: "2026-09-10"}, {date: "2026-10-05"}, {date: "2026-10-25", cloud: "all"},
    {date: "2026-11-02", cloud: "all"}, {date: "2026-11-04", cloud: "west"}, {date: "2026-11-09"},
  ]);
  const passes = await engine.listPasses(grid, FROM, TO);
  await Promise.all(passes.map(p => engine.assessPass(p, grid)));
  const by = d => passes.find(p => p.date === d);
  assert.equal(by("2026-11-09").sky.hidden, 0); assert.ok(I.isClear(by("2026-11-09")));
  near(by("2026-11-02").sky.hidden, 1, 1e-9); assert.equal(I.grade(by("2026-11-02")), 2);
  near(by("2026-11-04").sky.cloud, 0.5, 0.02, "half the box under cloud"); assert.equal(I.grade(by("2026-11-04")), 2);
  // an event on 28 October: before = 5 Oct (25 Oct is cloudy), after = 9 Nov (2 and 4 Nov are cloudy)
  let pair = I.pickPair(passes, {start: "2026-10-28T10:00:00Z", end: "2026-10-29T02:00:00Z"});
  assert.equal(pair.before.date, "2026-10-05"); assert.equal(pair.after.date, "2026-11-09");
  // no event: the newest clear pass and the clear one before it
  pair = I.pickPair(passes, null);
  assert.equal(pair.after.date, "2026-11-09"); assert.equal(pair.before.date, "2026-10-05");
  // an event after the last pass: nothing "after" yet
  pair = I.pickPair(passes, {start: "2026-11-09T12:00:00Z"});
  assert.equal(pair.before.date, "2026-11-09"); assert.equal(pair.after, null);
  // nothing clear at all
  const cloudy = setup([{date: "2026-11-02", cloud: "all"}, {date: "2026-11-04", cloud: "all"}]);
  const only = await cloudy.engine.listPasses(cloudy.grid, FROM, TO);
  await Promise.all(only.map(p => cloudy.engine.assessPass(p, cloudy.grid)));
  assert.deepEqual(I.pickPair(only, null), {before: null, after: null});
});

test("the default pair comes from one track of the satellite when a clear pass of it is near", () => {
  const sky = hidden => ({cloud: hidden, shadow: 0, cirrus: 0, nodata: 0, water: 0, hidden});
  const pass = (stamp, hidden = 0, extra = {}) => ({time: Date.parse(stamp), date: stamp.slice(0, 10), cover: 1, sky: sky(hidden), ...extra});
  // Kuwait City, 14 and 15 November 2026: yesterday's clear pass is from the track beside today's
  // (07:31 against 07:41) and showed 50 ha of "change" in tower shadows
  const today = pass("2026-11-15T07:41:36Z"), beside = pass("2026-11-14T07:31:48Z"), same = pass("2026-11-10T07:41:38Z");
  let pair = I.pickPair([today, beside, pass("2026-11-12T07:31:40Z"), same, pass("2026-10-31T07:41:20Z")], null);
  assert.equal(pair.after, today); assert.equal(pair.before, same, "four days older, but seen from the same side");
  // a pass of the same track that is cloudy, or more than twelve days older, does not take the place
  assert.equal(I.pickPair([today, beside, pass("2026-11-10T07:41:38Z", 0.9)], null).before, beside);
  assert.equal(I.pickPair([today, beside, pass("2026-10-31T07:41:20Z")], null).before, beside);
  // the nearest clear pass already of the same track: nothing changes
  assert.equal(I.pickPair([today, pass("2026-11-13T07:41:10Z"), same], null).before.date, "2026-11-13");
  // around an event: «after» is the first clear pass after it, «before» follows its track
  const event = {start: "2026-11-11T12:00:00Z", end: "2026-11-11T18:00:00Z"};
  const after = pass("2026-11-12T07:31:40Z"), near = pass("2026-11-10T07:41:38Z"), alike = pass("2026-11-09T07:31:50Z");
  pair = I.pickPair([today, beside, after, near, alike], event);
  assert.equal(pair.after, after); assert.equal(pair.before, alike);
  assert.equal(I.pickPair([today, after, near], event).before, near, "no pass of that track before the event: the nearest clear one");
  assert.equal(I.pickPair([near, alike], event).before, near, "nothing after the event yet: the nearest clear one");
  // what "the same view" means: the same kind of satellite at the same time of day, also across midnight
  assert.equal(I.sameView(today, same), true); assert.equal(I.sameView(today, beside), false);
  assert.equal(I.sameView(today, pass("2026-11-10T07:41:30Z", 0, {sensor: "landsat"})), false);
  assert.equal(I.sameView(pass("2026-11-15T23:59:30Z"), pass("2026-11-10T00:01:00Z")), true);
  assert.equal(I.sameView(today, null), false);
});

test("ground that is called cloud on most dates stops counting as cloud; shadow needs cloud", () => {
  const n = 400, passes = [];
  for (let k = 0; k < 8; k++) {
    const scl = new Uint8Array(n).fill(5);
    for (let i = 0; i < 60; i++) scl[i] = 9;                    // a white roof, "cloud" every time
    if (k === 3) for (let i = 200; i < 300; i++) scl[i] = 9;    // one real cloud
    passes.push({scl, sky: I.skyOf(scl), cover: 1});
  }
  near(passes[0].sky.cloud, 0.15, 1e-9);
  assert.equal(I.refineSky(passes), true);
  assert.equal(passes[0].sky.cloud, 0); assert.ok(I.isClear(passes[0]));
  near(passes[3].sky.cloud, 0.25, 1e-9, "the real cloud stays");
  assert.equal(I.refineSky(passes.slice(0, 3)), false, "too few passes to tell");
  const water = new Uint8Array(n).fill(6); water.fill(3, 0, 100);              // dark water classed as shadow, no cloud anywhere
  assert.equal(I.skyOf(water).shadow, 0);
  const real = new Uint8Array(n).fill(5); real.fill(9, 0, 40); real.fill(3, 40, 80);
  near(I.skyOf(real).hidden, 0.2, 1e-9);
});

/* ---------- what changed ---------- */
function flatScene(grid, tweak) {
  const n = grid.size * grid.size, s = {grid, scl: new Uint8Array(n).fill(5), res: 10};
  for (const b of ["blue", "green", "red", "nir08", "swir16", "swir22"]) {
    s[b] = new Float32Array(n);
    for (let i = 0; i < n; i++) s[b][i] = BASE[b] + 0.004 * Math.sin(i * 12.9898 + b.length);     // a little texture
  }
  if (tweak) tweak(s, n);
  return s;
}
test("changed area: a darkened 100 m square is found with its size, noise and lone pixels are not", () => {
  const grid = I.makeGrid(26, 50, 1), size = grid.size;
  const before = flatScene(grid);
  const after = flatScene(grid, s => {
    for (const b of ["blue", "green", "red", "nir08", "swir16", "swir22"]) {
      for (let y = 50; y < 60; y++) for (let x = 120; x < 130; x++) s[b][y * size + x] *= 0.4;      // 10 x 10 pixels = 1 hectare
      s[b][150 * size + 30] *= 0.3;                                                                 // one stray pixel
      for (let i = 0; i < s[b].length; i++) s[b][i] *= 1.06;                                        // a brighter day overall
    }
  });
  const c = I.changeOf(before, after);
  assert.equal(c.changedPx, 100); near(c.areaHa, 1.0, 1e-9); near(c.darkHa, 1.0, 1e-9); assert.equal(c.brightHa, 0);
  assert.equal(c.mask[55 * size + 125], 1); assert.equal(c.mask[150 * size + 30], 0);
  assert.equal(c.compared, 1); assert.equal(c.clouds, false);
  const layer = I.changeImage(c);
  assert.deepEqual([...layer.slice((55 * size + 125) * 4, (55 * size + 125) * 4 + 3)], [255, 59, 48], "darker is drawn red");
  assert.equal(layer[(20 * size + 20) * 4 + 3], 0, "unchanged ground stays see-through");
  assert.equal(I.changeOf(before, flatScene(grid)).changedPx, 0, "identical dates: nothing");
  const brighter = I.changeOf(after, before);
  near(brighter.brightHa, 1.0, 1e-9, "the other way round it is a brightening");
});

test("cloud on one date is not reported as change, and what could not be compared is stated", () => {
  const grid = I.makeGrid(26, 50, 1), size = grid.size;
  const cloudAt = (x, y) => x >= 20 && x < 80 && y >= 20 && y < 80;
  const after = flatScene(grid, s => {
    for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (cloudAt(x, y)) {
      const i = y * size + x;
      if (x >= 24 && x < 76 && y >= 24 && y < 76) s.scl[i] = 9;           // the classes miss the cloud's edge
      for (const b of ["blue", "green", "red", "nir08", "swir16", "swir22"]) s[b][i] = 0.7;
    }
  });
  const c = I.changeOf(flatScene(grid), after);
  assert.equal(c.changedPx, 0); assert.equal(c.clouds, true);
  assert.ok(c.compared < 0.9 && c.compared > 0.6, "about a seventh of the box was left out, with a margin");
  // the same "cloud" on both dates is bright ground: it is compared
  assert.equal(I.changeOf(after, after).compared, 1);
});

test("flames: only what glows in the short-wave infrared is flagged, as one spot with its place", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04", flame: true}, {date: "2026-10-30"}]);
  const passes = await engine.listPasses(grid, FROM, TO);
  const [withFlame, without] = await Promise.all(passes.map(p => engine.loadScene(p, grid, "full")));
  const spots = I.hotSpots(withFlame);
  assert.equal(spots.length, 1);
  const [lat, lon] = I.UTM.inverse(archive.Ec - 400, archive.Nc - 400, archive.zone);
  near(spots[0].lat, lat, 3e-4, "latitude of the flame"); near(spots[0].lon, lon, 3e-4, "longitude of the flame");
  assert.ok(spots[0].pixels >= 4 && spots[0].pixels <= 40);
  assert.deepEqual(I.hotSpots(without), [], "bright sand is not a flame");
});

test("both dates share one brightness range, so a real darkening still looks darker", () => {
  const grid = I.makeGrid(26, 50, 1);
  const a = flatScene(grid), b = flatScene(grid, s => { for (let i = 0; i < 2000; i++) s.red[i] = s.green[i] = s.blue[i] = 0.05; });
  const stretch = I.stretchOf([a, b], I.COLOR_BANDS);
  const ia = I.colorImage(a, stretch), ib = I.colorImage(b, stretch);
  assert.ok(ib[0] < ia[0] - 60, "the darkened pixels are clearly darker");
  assert.equal(ia[4 * 30000], ib[4 * 30000], "unchanged pixels look the same on both dates");
  assert.deepEqual(I.stretchOf([], I.COLOR_BANDS), {lo: 0, hi: 0.4});
});

/* ---------- when the archive misbehaves ---------- */
test("the archive being down, slow or wrong gives a named failure after one retry, never a hang", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04"}], {budgets: {json: 40, head: 40, tile: 40}});
  archive.fail = () => "down";
  assert.equal(await code(engine.listPasses(grid, FROM, TO)), "source");
  assert.equal(archive.calls.filter(c => c.url === SEARCH + "/search").length, 2, "tried twice");
  archive.fail = () => 503;
  assert.equal(await code(engine.listPasses(grid, FROM, TO)), "source");
  archive.fail = () => "hang";
  const started = Date.now();
  assert.equal(await code(engine.listPasses(grid, FROM, TO)), "timeout");
  assert.ok(Date.now() - started < 1500, "the time budget ended the wait");
  archive.fail = null;
  const [pass] = await engine.listPasses(grid, FROM, TO);
  archive.fail = url => url.endsWith(".tif") ? "norange" : null;        // a server that would send the whole file
  assert.equal(await code(engine.assessPass(pass, grid)), "source");
  archive.fail = url => url.endsWith("B12.tif") ? 500 : null;
  assert.equal(await code(engine.loadScene(pass, grid, "fast")), "source");
  archive.fail = null;
  assert.ok((await engine.loadScene(pass, grid, "fast")).swir22[0] > 0.2, "and it works again once the archive is back");
});

test("leaving a view cancels its downloads, and the next view starts them again", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04"}]);
  const [pass] = await engine.listPasses(grid, FROM, TO);
  archive.fail = url => url.endsWith(".tif") ? "hang" : null;
  const ctl = new AbortController();
  const first = code(engine.loadScene(pass, grid, "fast", ctl.signal));
  await new Promise(r => setTimeout(r, 20));
  ctl.abort();
  assert.equal(await first, "aborted");
  archive.fail = null;
  const scene = await engine.loadScene(pass, grid, "fast", new AbortController().signal);
  assert.ok(scene.red[0] > 0.2);
});

test("a download two views share is cancelled only when both have left it", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04"}]);
  const [pass] = await engine.listPasses(grid, FROM, TO);
  let release = null;
  const held = new Promise(r => { release = r; });
  const real = archive.fetch;
  const slow = I.createEngine({fetch: async (url, init) => { if (/B04\.tif$/.test(url)) await held; return real(url, init); }});
  const one = new AbortController(), two = new AbortController();
  const first = code(slow.loadBands(pass, grid, ["red"], "fast", one.signal));
  const second = slow.loadBands(pass, grid, ["red"], "fast", two.signal);
  await new Promise(r => setTimeout(r, 10));
  one.abort();                                           // the first view is left: the second still gets its picture
  assert.equal(await first, "aborted");
  release();
  assert.ok((await second).red[0] > 0.2);
  assert.equal(archive.calls.filter(c => c.url.endsWith("B04.tif")).length, 2, "one header and one tile: nothing was started twice");
  assert.equal(await code(slow.loadBands(pass, grid, ["red"], "fast", one.signal)), "aborted", "a view already left starts nothing");
});

/* ---------- only the bands a view needs, kept in memory within a budget ---------- */
test("bands are read only when asked for, once, and shared between the quick look and the full picture", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04"}]);
  const [pass] = await engine.listPasses(grid, FROM, TO);
  const files = () => [...new Set(archive.calls.filter(c => /\.tif$/.test(c.url)).map(c => c.url.split("/").pop()))].sort();
  const quick = await engine.loadBands(pass, grid, ["red", "green", "blue"], "fast");
  assert.deepEqual(files(), ["B02.tif", "B03.tif", "B04.tif", "SCL.tif"], "natural colour needs three bands and the scene classes");
  assert.equal(quick.res, 20); assert.equal(quick.swir22, undefined);
  assert.ok(quick.scl.every(c => c === 5));
  // the infrared view: only what is missing is fetched, and at 20 m band 8A stands in for band 8
  let before = archive.calls.length;
  const infra = await engine.loadBands(pass, grid, ["swir22", "nir", "red"], "fast");
  assert.deepEqual([...new Set(archive.calls.slice(before).map(c => c.url.split("/").pop()))].sort(), ["B12.tif", "B8A.tif"]);
  assert.equal(infra.red, quick.red, "the same array, not a second copy");
  assert.deepEqual({...infra.from}, {swir22: "swir22", nir: "nir08", red: "red", scl: "scl"});
  // the full picture: the 10 m bands are read sharper, band 8 itself now; the 20 m band is the copy already held
  before = archive.calls.length;
  const full = await engine.loadBands(pass, grid, ["swir22", "nir", "red"], "full");
  assert.deepEqual([...new Set(archive.calls.slice(before).map(c => c.url.split("/").pop()))].sort(), ["B04.tif", "B08.tif"]);
  assert.equal(full.res, 10); assert.equal(full.from.nir, "nir");
  assert.equal(full.swir22, infra.swir22); assert.notEqual(full.red, infra.red);
  const {E, N} = I.gridUTM(grid, pass.proj), at = 200 * 57 + 131;
  near(full.nir[at], BASE.nir + 2e-5 * (E[at] - archive.Ec) + 1e-5 * (N[at] - archive.Nc), 6e-5, "band 8 at a pixel");
  // asked again: nothing is downloaded
  before = archive.calls.length;
  await engine.loadBands(pass, grid, ["swir22", "nir", "red", "green", "blue"], "fast");
  assert.equal(archive.calls.length, before);
  assert.deepEqual(Object.keys(await engine.loadScene(pass, grid, "fast")).sort(),
    ["blue", "green", "grid", "nir08", "pass", "quality", "red", "res", "scl", "swir16", "swir22"], "the whole-scene call is as it was");
});

test("a pass without band 8, and Landsat, show the plant views with their own near-infrared band", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04", noNir: true}, {date: "2026-10-20", sensor: "landsat"}]);
  const [bare, landsat] = await engine.listPasses(grid, FROM, TO);
  const a = await engine.loadBands(bare, grid, ["nir", "red"], "full");
  assert.equal(a.from.nir, "nir08"); assert.ok(a.nir[0] > 0.2);
  assert.equal(archive.calls.filter(c => c.url.includes("/items/")).length, 1, "the record was asked once: it lists no band 8");
  assert.deepEqual(I.comboNames("veg", a), ["B8A", "B04", "B03"]);
  const b = await engine.loadBands(landsat, grid, ["nir", "red", "green"], "full");
  assert.equal(b.from.nir, "nir08"); assert.equal(b.res, 30);
  assert.deepEqual(I.comboNames("veg", b), ["B5", "B4", "B3"]);
  assert.deepEqual(I.comboNames("fire", b), ["B7", "B6", "B4"]);
  assert.deepEqual(I.comboNames("nbr", b), ["B5", "B7"]);
  // a failure that is not «no such band» is not papered over
  archive.fail = url => url.endsWith("B08.tif") ? 500 : null;
  const {engine: other, grid: g2} = setup([{date: "2026-11-04"}]);
  const flaky = makeArchive({...PLACE, passes: [{date: "2026-11-04"}]});
  flaky.fail = url => url.endsWith("B08.tif") ? 500 : null;
  const e3 = I.createEngine({fetch: flaky.fetch}), [p3] = await e3.listPasses(g2, FROM, TO);
  assert.equal(await code(e3.loadBands(p3, g2, ["nir"], "full")), "source");
  assert.ok(other);
});

test("what is kept in memory stays within its byte budget, the least recently used dropped first", async () => {
  const one = 200 * 200 * 4;                                // one band of this grid
  const {engine, grid, archive} = setup([{date: "2026-11-04"}], {bandCache: 4 * one + 200 * 200, tileCache: 3 * 256 * 256 * 2});
  const [pass] = await engine.listPasses(grid, FROM, TO);
  const first = await engine.loadBands(pass, grid, ["red", "green", "blue"], "fast");
  assert.equal(engine.memory().bands, 3 * one + 200 * 200, "three bands and the classes");
  await engine.loadBands(pass, grid, ["swir22", "swir16"], "fast");
  const m = engine.memory();
  assert.ok(m.bands <= m.bandBudget, "bands within the budget"); assert.ok(m.tiles <= m.tileBudget, "tiles within the budget");
  assert.ok(m.dropped > 0);
  // what was dropped is simply read again when asked for (the oldest went first: the classes and red)
  const before = archive.calls.length;
  const again = await engine.loadBands(pass, grid, ["red", "swir16"], "fast");
  assert.notEqual(again.red, first.red); assert.ok(archive.calls.length > before);
  near(again.red[777], first.red[777], 1e-9, "and holds the same numbers");
  engine.forget();
  assert.equal(engine.memory().bands, 0); assert.equal(engine.memory().tiles, 0);
});

/* ---------- boxes wider than one tile, and coarse overviews ---------- */
test("a box across two tiles of one pass is put together, also across a UTM zone edge", async () => {
  const place = {lat: 26, lon: 53.99};                      // zone 39, a few hundred metres from zone 40
  const archive = makeArchive({...place, passes: [
    {date: "2026-11-04", tile: "39RYH", footprint: "west"}, {date: "2026-11-04", tile: "40RBN", footprint: "east", zone: 40},
    {date: "2026-11-04", tile: "39RYH", footprint: "west", collection: "sentinel-2-l2a"},         // the same tile in the older archive
    {date: "2026-10-25"}, {date: "2026-10-15", tile: "39RYH", footprint: "west"}]});
  const engine = I.createEngine({fetch: archive.fetch}), grid = I.makeGrid(place.lat, place.lon, 1);
  const [joined, whole, half] = await engine.listPasses(grid, FROM, TO);
  assert.deepEqual(joined.parts.map(p => [p.grid, p.proj.zone, p.collection]), [["39RYH", 39, "sentinel-2-c1-l2a"], ["40RBN", 40, "sentinel-2-c1-l2a"]]);
  assert.equal(joined.cover, 1); assert.equal(joined.grid, "39RYH");
  assert.equal(whole.parts, undefined, "a box inside one tile is left as it was");
  near(half.cover, 0.5, 0.07); assert.equal(half.parts, undefined, "no second tile that day: half a picture, said so");
  const sky = await engine.assessPass(joined, grid);
  assert.equal(sky.nodata, 0); assert.ok(I.isClear(joined), "judged over both tiles together");
  near((await engine.assessPass(half, grid)).nodata, 0.5, 0.05);
  const scene = await engine.loadBands(joined, grid, ["red", "swir22"], "full");
  const {E, N} = I.gridUTM(grid, whole.proj);
  for (const band of ["red", "swir22"]) for (let i = 0; i < scene[band].length; i += 37)
    near(scene[band][i], BASE[band] + 2e-5 * (E[i] - archive.Ec) + 1e-5 * (N[i] - archive.Nc), 2.5e-4, `${band} at pixel ${i}`);
  assert.ok(scene.scl.every(c => c === 5), "the classes too");
  const files = archive.calls.filter(c => c.url.endsWith("B04.tif")).map(c => c.url);
  assert.ok(files.some(u => u.includes("/39/R/YH/")) && files.some(u => u.includes("/40/R/BN/")), "both tiles were read");
  assert.equal((await engine.loadScene(joined, grid, "fast")).red.some(v => v !== v), false, "the whole-scene call joins them too");
});

test("a wide box is read from the coarse overviews: far fewer points, the same ground", async () => {
  const archive = makeArchive({...PLACE, levels: 4, passes: [{date: "2026-11-04", burn: true}]});
  const engine = I.createEngine({fetch: archive.fetch});
  const wide = I.makeGrid(PLACE.lat, PLACE.lon, 2, 50);     // 4 km on 50 points: 80 m each, as a 40 km box has 100 m
  assert.equal(wide.res, 80); assert.equal(wide.size, 50);
  const [pass] = await engine.listPasses(wide, FROM, TO);
  const scene = await engine.loadBands(pass, wide, ["red", "swir22"], "full");
  assert.equal(scene.res, 80);
  const {E, N} = I.gridUTM(wide, pass.proj);
  for (const band of ["red", "swir22"]) for (const i of [0, 49, 50 * 25 + 25, 50 * 49, 2499, 50 * 11 + 40])
    near(scene[band][i], BASE[band] + 2e-5 * (E[i] - archive.Ec) + 1e-5 * (N[i] - archive.Nc), 6e-5, `${band} at pixel ${i}`);
  // the 80 m level of each file was read (one small tile), never the 10 m one
  const red = archive.calls.filter(c => c.url.endsWith("B04.tif"));
  assert.equal(red.length, 2, "one header and one tile");
  const cog = await engine.openCog(red[0].url);
  assert.deepEqual(cog.levels.map(l => Math.round(l.sx)), [10, 20, 40, 80]);
  const [, tile] = red.map(c => c.range.replace("bytes=", "").split("-").map(Number));
  assert.equal(tile[0], cog.levels[3].offsets[0], "the tile of the coarsest level");
  // the sky over boxes wider than 20 km is judged on 500 x 500 points, the others on 20 m as ever
  assert.equal(engine.skyGrid(I.makeGrid(26, 50, 40)).size, 500);
  assert.equal(engine.skyGrid(I.makeGrid(26, 50, 20)).res, 80);
  assert.equal(engine.skyGrid(I.makeGrid(26, 50, 10)).res, 20);
  assert.equal(engine.skyGrid(I.makeGrid(26, 50, 2)).size, 200);
  const big = I.makeGrid(26, 50, 40);
  assert.equal(big.size, 800); assert.equal(big.res, 100);
});

/* ---------- band combinations and indices ---------- */
test("each view names its bands on each satellite, and says when a satellite cannot show it", () => {
  const s2 = {pass: {}, nir: [1], from: {nir: "nir"}}, quick = {pass: {}, from: {nir: "nir08"}}, l9 = {pass: {sensor: "landsat"}};
  assert.deepEqual(I.comboNames("color", s2), ["B04", "B03", "B02"]);
  assert.deepEqual(I.comboNames("swir", s2), ["B12", "B8A", "B04"]);
  assert.deepEqual(I.comboNames("veg", s2), ["B08", "B04", "B03"]);
  assert.deepEqual(I.comboNames("veg", quick), ["B8A", "B04", "B03"], "the quick look says which band it really shows");
  assert.deepEqual(I.comboNames("fire", s2), ["B12", "B11", "B04"]);
  assert.deepEqual(I.comboNames("nbr", s2), ["B8A", "B12"]);
  assert.deepEqual(I.comboNames("ndvi", s2), ["B08", "B04"]);
  assert.deepEqual(I.comboNames("color", l9), ["B4", "B3", "B2"]);
  assert.deepEqual(I.comboNames("swir", l9), ["B7", "B5", "B4"]);
  assert.deepEqual(I.comboNames("ndvi", l9), ["B5", "B4"]);
  assert.deepEqual(I.comboBands("veg", {nir: [1]}), ["nir", "red", "green"]);
  assert.deepEqual(I.comboBands("veg", {}), ["nir08", "red", "green"]);
  for (const combo of Object.keys(I.COMBOS)) { assert.ok(I.comboAvailable(combo, {})); assert.ok(I.comboAvailable(combo, {sensor: "landsat"})); }
  // a record without one of the bands (it happens in the older archive): that view is closed, the others stay
  const poor = {assets: {red: {}, green: {}, blue: {}, nir08: {}, swir22: {}, scl: {}}};
  assert.equal(I.comboAvailable("fire", poor), false); assert.equal(I.comboAvailable("swir", poor), true); assert.equal(I.comboAvailable("ndvi", poor), true);
  assert.equal(I.comboAvailable("thermal", {}), false);
  assert.throws(() => I.comboBands("thermal", {}), /unknown view/);
  assert.equal(I.sensorOf({sensor: "landsat"}).instrument, "OLI"); assert.equal(I.sensorOf({}).instrument, "MSI");
});

function sceneOf(grid, values, scl = 5) {
  const n = grid.size * grid.size, s = {grid, scl: new Uint8Array(n).fill(scl), res: grid.res, pass: {}};
  for (const [b, v] of Object.entries(values)) s[b] = new Float32Array(n).fill(v);
  return s;
}
test("NBR and NDVI: the ratio of two bands, nothing where there is nothing to measure", () => {
  const grid = I.makeGrid(26, 50, 0.1), n = grid.size * grid.size;                // 20 x 20 pixels
  const s = sceneOf(grid, FIELD.plants);
  near(I.indexOf(s, "ndvi")[5], (0.45 - 0.05) / 0.50, 1e-6, "NDVI of plants");
  near(I.indexOf(s, "nbr")[5], (0.45 - 0.10) / 0.55, 1e-6, "NBR of plants");
  near(I.indexOf(sceneOf(grid, FIELD.burnt), "nbr")[5], (0.12 - 0.22) / 0.34, 1e-6, "NBR of burnt ground");
  // band 8 is used where the scene holds it, else band 8A
  s.nir = new Float32Array(n).fill(0.55);
  near(I.indexOf(s, "ndvi")[0], 0.5 / 0.6, 1e-6);
  s.red[3] = NaN; s.nir[4] = 0.004; s.red[4] = 0.005;
  const v = I.indexOf(s, "ndvi");
  assert.ok(Number.isNaN(v[3]), "no data"); assert.ok(Number.isNaN(v[4]), "two bands near zero give no ratio");
  // the picture: a fixed ramp (the same colour for the same value on every date), cloud and nothing set apart
  s.scl[6] = 9; s.scl[7] = 0; s.scl[8] = 6;
  const img = I.indexImage(v, "ndvi", s.scl), px = i => [...img.slice(i * 4, i * 4 + 4)];
  assert.deepEqual(px(0), [...I.rampColor("ndvi", 0.5 / 0.6), 255]);
  assert.deepEqual(px(6), [...I.MASK_COLORS.cloud, 255], "cloud is grey, whatever its number");
  assert.equal(px(7)[3], 0, "no data stays see-through"); assert.equal(px(3)[3], 0);
  assert.deepEqual(px(8), [...I.rampColor("ndvi", 0.5 / 0.6), 255], "water keeps its NDVI");
  assert.deepEqual([...I.indexImage(I.indexOf(s, "nbr"), "nbr", s.scl).slice(32, 35)], I.MASK_COLORS.water, "but has no burn ratio");
  assert.deepEqual([...I.comboImage(s, "ndvi").slice(0, 4)], px(0));
  // the ramps: lightest at «nothing to see», darker to both sides, the ends held beyond the range
  const light = c => 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  for (const [kind, mid] of [["ndvi", 0.1], ["nbr", 0]]) {
    const r = I.RAMPS[kind];
    for (let x = r.min; x < r.max - 0.05; x += 0.05) {
      const a = light(I.rampColor(kind, x)), b = light(I.rampColor(kind, x + 0.05));
      assert.ok(x + 0.05 <= mid + 1e-9 ? b >= a - 1 : x >= mid - 1e-9 ? b <= a + 1 : true, `${kind} lightness around ${x.toFixed(2)}`);
    }
    assert.deepEqual(I.rampColor(kind, -5), I.rampColor(kind, r.min)); assert.deepEqual(I.rampColor(kind, 5), I.rampColor(kind, r.max));
  }
  assert.ok(I.rampColor("ndvi", 0.7)[1] > I.rampColor("ndvi", 0.7)[0] + 60, "plants are green");
  assert.ok(I.rampColor("nbr", -0.4)[0] > I.rampColor("nbr", -0.4)[2] + 100, "burnt ground is red-brown");
});

test("the false-colour views are drawn from their own three bands, with one brightness range for both dates", () => {
  const grid = I.makeGrid(26, 50, 0.1);
  const plants = sceneOf(grid, FIELD.plants), burnt = sceneOf(grid, FIELD.burnt);
  for (let i = 0; i < 200; i++) for (const b of Object.keys(BASE)) if (plants[b]) { plants[b][i] = BASE[b]; burnt[b][i] = BASE[b]; }
  const veg = I.comboBands("veg", plants), stretch = I.stretchOf([plants, burnt], veg);
  const a = I.comboImage(plants, "veg", stretch), b = I.comboImage(burnt, "veg", stretch), at = 4 * 300;
  assert.ok(a[at] > a[at + 1] + 80 && a[at] > a[at + 2] + 80, "plants are red in the false-colour view");
  assert.ok(b[at] < a[at] - 60, "burnt plants are not");
  assert.deepEqual([...a.slice(0, 4)], [...b.slice(0, 4)], "unchanged ground looks the same on both dates");
  const fire = I.comboImage(burnt, "fire", I.stretchOf([plants, burnt], I.comboBands("fire", burnt)));
  assert.ok(fire[at] > fire[at + 2] + 60, "burnt ground is red-brown in the fire view");
  // the stronger display stretch narrows the range; sharpening keeps flat ground flat and steepens an edge
  const wide = I.stretchOf([plants, burnt], I.COLOR_BANDS), tight = I.stretchRange([plants, burnt], I.COLOR_BANDS);
  assert.ok(tight.hi - tight.lo <= wide.hi - wide.lo);
  assert.deepEqual(I.stretchRange([], I.COLOR_BANDS), {lo: 0, hi: 0.4});
  const n = 8, img = new Uint8ClampedArray(n * n * 4);
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) img.set(x < 4 ? [100, 100, 100, 255] : [140, 140, 140, 255], (y * n + x) * 4);
  const flat = img[(3 * n + 1) * 4];
  I.sharpen(img, n, 0.5);
  assert.equal(img[(3 * n + 1) * 4], flat, "flat ground is untouched");
  assert.ok(img[(3 * n + 3) * 4] < 100 && img[(3 * n + 4) * 4] > 140, "the edge is steeper");
  assert.equal(img[(3 * n + 3) * 4 + 3], 255);
});

/* ---------- burnt ground between two dates ---------- */
function burnPair(tweak) {
  const grid = I.makeGrid(26, 50, 0.5), size = grid.size, n = size * size;         // 100 x 100 pixels of 10 m
  const make = () => { const s = sceneOf(grid, FIELD.plants, 4); for (const b of Object.keys(FIELD.plants)) for (let i = 0; i < n; i++) s[b][i] += 0.004 * Math.sin(i * 12.9898 + b.length); return s; };
  const before = make(), after = make();
  const paint = (s, kind, x0, y0, x1, y1) => { for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) for (const b of Object.keys(FIELD[kind])) s[b][y * size + x] = FIELD[kind][b]; };
  if (tweak) tweak({before, after, paint, size, n});
  return {grid, size, before, after};
}
test("dNBR: burnt ground is sorted into the four usual classes, each with its area", () => {
  const {before, after, size} = burnPair(({after, paint}) => {
    paint(after, "burnt", 20, 20, 40, 40);                    // 20 x 20 pixels = 4 hectares, severe
    paint(after, "singed", 60, 20, 70, 30);                   // 10 x 10 pixels = 1 hectare, moderate
    paint(after, "burnt", 80, 80, 81, 81);                    // one stray pixel
  });
  const burn = I.burnOf(before, after);
  assert.deepEqual(I.BURN_STEPS, [0.1, 0.27, 0.44, 0.66]);
  near(burn.dnbr[30 * size + 30], 0.636 + 0.294, 0.02, "dNBR of the burnt square");
  near(burn.dnbr[25 * size + 65], 0.636 - 0.277, 0.02, "dNBR of the singed square");
  assert.equal(burn.cls[30 * size + 30], 4); assert.equal(burn.cls[25 * size + 65], 2);
  assert.equal(burn.cls[80 * size + 80], 0, "a lone pixel is not counted");
  assert.equal(burn.cls[5 * size + 5], 0);
  assert.deepEqual(burn.classPx, [0, 0, 100, 0, 400]);
  near(burn.classHa[4], 4.0, 1e-9, "severe"); near(burn.classHa[2], 1.0, 1e-9, "moderate"); near(burn.areaHa, 5.0, 1e-9);
  assert.equal(burn.compared, 1); near(burn.share, 0.05, 1e-9); near(burn.offset, 0, 0.01);
  // the layer: light to dark with the class, see-through elsewhere
  const img = I.burnImage(burn), rgb = i => [...img.slice(i * 4, i * 4 + 3)], hex = c => [1, 3, 5].map(k => parseInt(c.slice(k, k + 2), 16));
  assert.deepEqual(rgb(30 * size + 30), hex(I.BURN_COLORS[3])); assert.deepEqual(rgb(25 * size + 65), hex(I.BURN_COLORS[1]));
  assert.equal(img[(5 * size + 5) * 4 + 3], 0);
  const light = c => { const [r, g, b] = hex(c); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  assert.ok(I.BURN_COLORS.every((c, i) => i === 0 || light(c) < light(I.BURN_COLORS[i - 1]) - 15), "each class clearly darker than the one below it");
  // the same two dates the other way round: plants grew back, nothing burnt
  assert.equal(I.burnOf(after, before).areaHa, 0);
  // identical dates: nothing; and the job for the background worker gives the same numbers
  assert.equal(I.burnOf(before, before).areaHa, 0);
  const pick = s => ({scl: s.scl, blue: s.blue, green: s.green, red: s.red, nir08: s.nir08, swir16: s.swir16, swir22: s.swir22});
  const job = I.pairJob({size, res: 10, before: pick(before), after: pick(after)});
  assert.deepEqual(job.burn.classPx, burn.classPx); assert.equal(job.change.changedPx, I.changeOf(before, after).changedPx);
  assert.equal(job.change.grid, undefined); assert.equal(I.pairBuffers(job).length, 7);
});

test("dNBR counts patches, not specks: half a hectare that holds a core of the moderate class", () => {
  // What real pairs with no fire between them showed on 15 Nov 2026: ground that only dried near
  // Basra (8.5 ha, all of the lowest class) and tower shadows in Kuwait City (specks of a few pixels).
  const dried = {nir: 0.40, nir08: 0.40, swir22: 0.157};               // the burn ratio 0.2 lower: the lowest class
  const {before, after, size} = burnPair(({after, paint, size}) => {
    const fill = (x0, y0, x1, y1) => { for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) for (const b of Object.keys(dried)) after[b][y * size + x] = dried[b]; };
    fill(10, 10, 40, 40);                                              // 9 ha that only dried
    paint(after, "singed", 60, 10, 63, 13);                            // a speck of the moderate class: 9 pixels
    paint(after, "burnt", 70, 10, 74, 14);                             // a severe speck: 16 pixels, 0.16 ha
    fill(60, 60, 70, 70); paint(after, "singed", 62, 62, 67, 67);      // a scar: 1 ha with a quarter-hectare core
    fill(20, 60, 28, 68); paint(after, "singed", 22, 62, 25, 65);      // big enough, but its core is 9 pixels
  });
  const burn = I.burnOf(before, after), at = (x, y) => burn.cls[y * size + x];
  near(burn.dnbr[25 * size + 25], 0.2, 0.02, "the dried field is measured as it is");
  assert.equal(at(25, 25), 0, "ground that only dried is not a burn");
  assert.equal(at(61, 11), 0, "a speck is not a patch"); assert.equal(at(71, 11), 0, "however dark");
  assert.equal(at(23, 63), 0, "a patch without a real core");
  assert.equal(at(64, 64), 2); assert.equal(at(61, 61), 1, "the fringe of a real scar is counted with it");
  assert.deepEqual(burn.classPx, [0, 75, 25, 0, 0]); near(burn.areaHa, 1.0, 1e-9);
  assert.deepEqual([I.BURN_PATCH_HA, I.BURN_CORE_HA], [0.5, 0.2]);
});

test("dNBR leaves out cloud, shadow, water, missing data and flames, and takes out a drift of the whole box", () => {
  const {before, after, size} = burnPair(({before, after, paint, size}) => {
    paint(after, "burnt", 20, 28, 40, 48);                    // clear of the cloud's margin (120 m)
    for (let y = 0; y < 10; y++) for (let x = 0; x < size; x++) after.scl[y * size + x] = 9;                 // a cloud bank: rows 0-9
    for (let y = 90; y < size; y++) for (let x = 0; x < size; x++) before.scl[y * size + x] = 6;             // water: rows 90-99
    for (let y = 50; y < 60; y++) for (let x = 0; x < 10; x++) after.nir08[y * size + x] = NaN;              // no data
    for (let y = 50; y < 54; y++) for (let x = 50; x < 54; x++) { const i = y * size + x; after.swir22[i] = 1.2; after.swir16[i] = 0.6; after.nir08[i] = 0.3; }   // a flame
    for (let y = 70; y < 80; y++) for (let x = 70; x < 80; x++) { const i = y * size + x; after.nir08[i] = 0.02; after.swir22[i] = 0.01; }   // deep shadow or a pond the classes missed
  });
  const burn = I.burnOf(before, after);
  assert.equal(burn.skip[5 * size + 50], 2, "cloud"); assert.equal(burn.skip[95 * size + 50], 3, "water");
  assert.equal(burn.skip[55 * size + 5], 1, "no data"); assert.equal(burn.skip[52 * size + 52], 4, "a flame");
  assert.equal(burn.skip[56 * size + 52], 4, "and its glow, 30 m around"); assert.equal(burn.skip[60 * size + 52], 0);
  assert.equal(burn.skip[75 * size + 75], 3, "too dark to measure");
  assert.ok(Number.isNaN(burn.dnbr[5 * size + 50]));
  near(burn.classHa[4], 4.0, 1e-9, "the burnt square is still found"); near(burn.areaHa, 4.0, 1e-9, "and nothing else");
  assert.ok(burn.compared < 0.8 && burn.compared > 0.6, "what could be compared is stated: " + burn.compared);
  assert.equal(burn.clouds, true);
  const img = I.burnImage(burn);
  assert.ok(img[(5 * size + 1) * 4 + 3] > 0 && img[(5 * size + 2) * 4 + 3] === 0, "thin lines over what cloud hid");
  // cloud on BOTH dates at the same place is compared by changeOf (bright ground), not by the burn ratio
  const both = burnPair(({before, after, size}) => { for (let i = 0; i < size * 10; i++) before.scl[i] = after.scl[i] = 9; });
  assert.equal(I.changeOf(both.before, both.after).compared, 1); near(I.burnOf(both.before, both.after).compared, 0.9, 1e-9);
  // the whole box a little drier on the later date: not a burn
  const drier = burnPair(({after, n}) => { for (let i = 0; i < n; i++) { after.nir08[i] -= 0.03; after.swir22[i] += 0.02; } });
  const d = I.burnOf(drier.before, drier.after);
  assert.ok(d.offset > 0.05 && d.offset <= 0.08, "the drift is taken out: " + d.offset); assert.equal(d.areaHa, 0);
  // nothing to compare at all: an empty answer, no failure
  const none = burnPair(({after, n}) => after.scl.fill(9));
  assert.equal(I.burnOf(none.before, none.after).valid, 0);
  assert.throws(() => I.burnOf(before, sceneOf(I.makeGrid(26, 50, 0.3), FIELD.plants)), /different grids/);
});

test("the burn classes of a real reading: a burnt field with a singed edge, from the files", async () => {
  const {engine, grid, archive} = setup([{date: "2026-11-04", veg: true, scar: true}, {date: "2026-10-20", veg: true}]);
  const [after, before] = await engine.listPasses(grid, FROM, TO);
  const names = [...I.REFLECTANCE, "nir"];
  const [b, a] = await Promise.all([engine.loadBands(before, grid, names, "full"), engine.loadBands(after, grid, names, "full")]);
  const burn = I.burnOf(b, a);
  near(burn.classHa[4], 1.0, 0.45, "severe: the burnt hectare (its edge blurs at 20 m)");
  near(burn.areaHa, 2.56, 0.9, "all classes: the burnt middle and its singed ring");
  assert.ok(burn.classHa[2] + burn.classHa[3] > 0.6, "the ring is moderate or high");
  // the middle of the field, 250 m west and 350 m north of the place
  const [x, y] = I.gridPixel(grid, ...I.UTM.inverse(archive.Ec - 250, archive.Nc + 350, archive.zone)), at = Math.floor(y) * 200 + Math.floor(x);
  near(I.indexOf(b, "ndvi")[at], 0.8, 0.01, "NDVI of the field before");
  near(I.indexOf(a, "nbr")[at], -0.294, 0.02, "NBR of its burnt middle after");
  assert.equal(burn.cls[at], 4);
  const point = I.inspect(a, x, y);
  near(point.bands.swir22, 0.22, 1e-3); near(point.bands.nir, 0.12, 1e-3); assert.equal(point.scl, 4);
});

/* ---------- one point ---------- */
test("a point is read off with its coordinates, every band held, the indices and the scene class", () => {
  const {nir, ...six} = FIELD.plants;                        // a scene as the quick look holds it: no band 8
  const grid = I.makeGrid(26, 50, 0.5), s = sceneOf(grid, six, 4);
  const p = I.inspect(s, 30.7, 12.2);
  assert.equal(nir, 0.45);
  assert.equal(p.x, 30); assert.equal(p.y, 12); assert.equal(p.scl, 4);
  const [lat, lon] = I.gridPoint(grid, 30.5, 12.5);
  near(p.lat, lat, 1e-12); near(p.lon, lon, 1e-12);
  near(p.bands.red, 0.05, 1e-6); near(p.bands.swir22, 0.10, 1e-6); assert.equal(p.bands.nir, undefined, "band 8 is not held here");
  near(p.ndvi, 0.8, 1e-6); near(p.nbr, 0.35 / 0.55, 1e-6);
  s.nir = new Float32Array(s.red.length).fill(0.55); s.from = {nir: "nir"};
  near(I.inspect(s, 0, 0).ndvi, 0.5 / 0.6, 1e-6, "with band 8, NDVI is taken from it");
  s.from = {nir: "nir08"};
  assert.equal(I.inspect(s, 0, 0).bands.nir, undefined, "a stand-in is not listed as a band of its own");
  s.red[0] = NaN;
  const empty = I.inspect(s, 0, 0);
  assert.equal(empty.bands.red, null); assert.equal(empty.ndvi, null);
  assert.equal(I.inspect(s, -1, 5), null); assert.equal(I.inspect(s, 5, 100), null);
});

/* ---------- the background worker ---------- */
test("the engine as text starts a worker that answers the comparison with the same numbers", () => {
  const sent = [], scope = {postMessage: (m, transfer) => sent.push({m, transfer})};
  const context = vm.createContext({self: scope});
  vm.runInContext(`self.RasadImagery = ${I.source}; self.RasadImagery.serve(self);`, context);
  assert.equal(typeof scope.onmessage, "function");
  const {before, after, size} = burnPair(({after, paint}) => paint(after, "burnt", 20, 20, 40, 40));
  const pick = s => ({scl: s.scl, blue: s.blue, green: s.green, red: s.red, nir08: s.nir08, swir16: s.swir16, swir22: s.swir22});
  scope.onmessage({data: {id: 7, size, res: 10, before: pick(before), after: pick(after)}});
  const {m, transfer} = sent[0];
  assert.equal(m.id, 7); assert.equal(transfer.length, 7);
  const here = I.pairJob({size, res: 10, before: pick(before), after: pick(after)});
  assert.equal(m.result.change.changedPx, here.change.changedPx); assert.deepEqual([...m.result.burn.classPx], here.burn.classPx);
  near(m.result.burn.classHa[4], 4.0, 1e-9);
  scope.onmessage({data: {id: 8, size, res: 10, before: {}, after: {}}});
  assert.equal(sent[1].m.id, 8); assert.match(sent[1].m.error, /./, "a failure comes back as a message, not as a dead worker");
  assert.equal(I.VERSION, 2);
});
