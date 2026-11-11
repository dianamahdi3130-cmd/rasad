"use strict";
// The before/after imagery engine (web/imagery.js) against a made-up archive: no network.

const assert = require("node:assert/strict");
const test = require("node:test");
const zlib = require("node:zlib");
const I = require("../web/imagery.js");
const {makeArchive, makeCog, SEARCH, BASE, LANDSAT_API, LANDSAT_FILES} = require("./support/archive.js");

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
