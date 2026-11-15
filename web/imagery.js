// Rasad imagery engine («قبل وبعد»): reads free Sentinel-2 pictures (ESA Copernicus, 10 m) for
// any place, inside the browser. The list of passes comes from the Earth Search index and the
// pixels straight from the Cloud-Optimized GeoTIFFs on AWS, by HTTP range reads of only the
// tiles that cover the box. No server, no account, no key. Nothing here touches the page: the
// viewer is in beforeafter.js, and the tests run this file in Node with a made-up archive.
//
//   listPasses  the satellite passes over a box, newest first, one entry per pass
//   assessPass  how much of the box each pass really shows (cloud over the box itself)
//   pickPair    the default "before" and "after"
//   loadScene   the bands of one pass, resampled to a north-up grid that fits a web map
//   loadBands   the same, but only the bands asked for, kept in memory within a byte budget
//   colorImage / swirImage / changeOf / changeImage / hotSpots   what the viewer shows
//   comboImage  any band combination or index (NBR, NDVI) as a picture, with its legend's ramp
//   burnOf / burnImage   the drop of the burn ratio between two dates, in the usual four classes
//   inspect     the numbers of one point; pairJob: the comparison as one job for a background worker
(function (root, factory) {
  const api = factory();
  // the whole engine as text: the viewer starts a background worker from it (no second file to load)
  api.source = "(" + factory.toString() + ")()";
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RasadImagery = api;
})(typeof self !== "undefined" ? self : globalThis, function () {
"use strict";

const VERSION = 2;
const SEARCH_URL = "https://earth-search.aws.element84.com/v1";
// the reprocessed archive first; the older one fills the years the new one does not hold yet
const COLLECTIONS = ["sentinel-2-c1-l2a", "sentinel-2-l2a"];
const C1_FILES = "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/sentinel-2-c1-l2a/";
// asset name in the index -> file name in the archive
const BAND_FILE = {red:"B04", green:"B03", blue:"B02", nir08:"B8A", swir16:"B11", swir22:"B12", scl:"SCL"};
// read only when a view asks for it: the wide near-infrared band, the only infrared one at 10 m
const EXTRA_FILE = {nir: "B08"};
const REFLECTANCE = ["red", "green", "blue", "nir08", "swir16", "swir22"];
// What each band is on each satellite: [its name there, the middle of the band in nanometres,
// metres per point]. Landsat 8/9 (OLI) has no second near-infrared band: «nir» is its band 5 too.
const SENSORS = {
  sentinel2: {name: "Sentinel-2", instrument: "MSI", metres: 10,
    bands: {blue: ["B02", 490, 10], green: ["B03", 560, 10], red: ["B04", 665, 10], nir: ["B08", 842, 10],
      nir08: ["B8A", 865, 20], swir16: ["B11", 1610, 20], swir22: ["B12", 2190, 20]}},
  landsat: {name: "Landsat", instrument: "OLI", metres: 30,
    bands: {blue: ["B2", 482, 30], green: ["B3", 561, 30], red: ["B4", 655, 30], nir: ["B5", 865, 30],
      nir08: ["B5", 865, 30], swir16: ["B6", 1609, 30], swir22: ["B7", 2201, 30]}},
};
const sensorOf = pass => SENSORS[pass && pass.sensor === "landsat" ? "landsat" : "sentinel2"];
// The views: three bands as red, green and blue, or one ratio of two bands drawn with a colour ramp.
const COMBOS = {
  color: {bands: ["red", "green", "blue"]},
  swir: {bands: ["swir22", "nir08", "red"]},            // heat and burnt ground stand out, plants are green
  veg: {bands: ["nir", "red", "green"]},                // plants are red: the classic false colour
  fire: {bands: ["swir22", "swir16", "red"]},           // flames glow through smoke, burnt ground is dark red-brown
  nbr: {index: ["nir08", "swir22"]},                    // burn ratio: high over healthy plants, low over burnt ground
  ndvi: {index: ["nir", "red"]},                        // plant cover
};
// Landsat 8 and 9 (USGS, 30 m, every 8 days between them) add dates between Sentinel-2's passes.
// Their files are on Microsoft's Planetary Computer: free, but each request carries a short-lived
// token that the service hands out to anyone. They reach that archive two to three weeks late.
const LANDSAT_URL = "https://planetarycomputer.microsoft.com/api";
const LANDSAT = "landsat-c2-l2";
const SIGNED_HOST = /^https:\/\/[a-z0-9]+\.blob\.core\.windows\.net\//;
const D2R = Math.PI / 180, R_MERC = 6378137;

class ImageryError extends Error {
  // code: offline | timeout | source (the archive answered badly) | format | none | aborted
  constructor(code, message) { super(message || code); this.name = "ImageryError"; this.code = code; }
}

/* ------------------------------------------------------------------ UTM (WGS84) */
// Sentinel-2 tiles are in UTM. Krüger series to the third order: millimetres inside a zone and
// still centimetres a few degrees outside it (tiles near a zone edge come in both zones).
const UTM = (() => {
  const a = 6378137, f = 1 / 298.257223563, k0 = 0.9996;
  const n = f / (2 - f), n2 = n * n, n3 = n2 * n;
  const A = a / (1 + n) * (1 + n2 / 4 + n2 * n2 / 64);
  const al = [n / 2 - 2 * n2 / 3 + 5 * n3 / 16, 13 * n2 / 48 - 3 * n3 / 5, 61 * n3 / 240];
  const be = [n / 2 - 2 * n2 / 3 + 37 * n3 / 96, n2 / 48 + n3 / 15, 17 * n3 / 480];
  const de = [2 * n - 2 * n2 / 3 - 2 * n3, 7 * n2 / 3 - 8 * n3 / 5, 56 * n3 / 15];
  const c = 2 * Math.sqrt(n) / (1 + n);
  function forward(lat, lon, zone, south) {
    const s = Math.sin(lat * D2R), dl = (lon - (zone * 6 - 183)) * D2R;
    const t = Math.sinh(Math.atanh(s) - c * Math.atanh(c * s));
    const xi = Math.atan2(t, Math.cos(dl)), eta = Math.atanh(Math.sin(dl) / Math.sqrt(1 + t * t));
    let e = eta, nn = xi;
    for (let j = 1; j <= 3; j++) {
      e += al[j - 1] * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta);
      nn += al[j - 1] * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta);
    }
    return [500000 + k0 * A * e, (south ? 1e7 : 0) + k0 * A * nn];
  }
  function inverse(E, N, zone, south) {
    const xi = (N - (south ? 1e7 : 0)) / (k0 * A), eta = (E - 500000) / (k0 * A);
    let x1 = xi, e1 = eta;
    for (let j = 1; j <= 3; j++) {
      x1 -= be[j - 1] * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta);
      e1 -= be[j - 1] * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta);
    }
    const chi = Math.asin(Math.sin(x1) / Math.cosh(e1));
    let phi = chi;
    for (let j = 1; j <= 3; j++) phi += de[j - 1] * Math.sin(2 * j * chi);
    return [phi / D2R, zone * 6 - 183 + Math.atan2(Math.sinh(e1), Math.cos(x1)) / D2R];
  }
  return {forward, inverse};
})();

// the projection of a tile from what the index says about it
function projectionOf(props) {
  const code = String(props["proj:code"] || props["proj:epsg"] || "").replace(/^EPSG:/i, "");
  let m = /^32([67])(\d\d)$/.exec(code);
  if (m) return {zone: +m[2], south: m[1] === "7"};
  m = /^MGRS-(\d{1,2})([C-X])/.exec(String(props["grid:code"] || ""));
  if (m) return {zone: +m[1], south: m[2] < "N"};
  throw new ImageryError("format", "unknown projection");
}

/* ------------------------------------------------------------------ the picture grid */
// A square, north-up grid in the web map's own projection (so it lies exactly on the map),
// `halfKm` to each side of the point. Pixels are 10 m on the ground, or larger for a big box.
function makeGrid(lat, lon, halfKm, maxPx = 800) {
  if (!(Math.abs(lat) < 80) || !(Math.abs(lon) <= 180) || !(halfKm > 0)) throw new ImageryError("format", "bad place");
  const span = 2 * halfKm * 1000;
  const res = Math.max(10, span / maxPx);
  const size = Math.max(8, Math.round(span / res));
  const k = 1 / Math.cos(lat * D2R);                       // map metres per ground metre here
  const mx0 = R_MERC * lon * D2R, my0 = R_MERC * Math.log(Math.tan(Math.PI / 4 + lat * D2R / 2));
  const half = size * res * k / 2;
  const latOf = my => (2 * Math.atan(Math.exp(my / R_MERC)) - Math.PI / 2) / D2R;
  const lonOf = mx => mx / R_MERC / D2R;
  return {
    lat, lon, halfKm, res, size, step: res * k, mx0: mx0 - half, my0: my0 + half,
    south: latOf(my0 - half), north: latOf(my0 + half), west: lonOf(mx0 - half), east: lonOf(mx0 + half),
    latOf, lonOf, cache: new Map(),
  };
}
// where on the earth is grid pixel (x, y), fractions allowed (0,0 = top-left corner)
function gridPoint(grid, x, y) {
  return [grid.latOf(grid.my0 - y * grid.step), grid.lonOf(grid.mx0 + x * grid.step)];
}
// where in the grid is a place: [x, y] in pixels
function gridPixel(grid, lat, lon) {
  const mx = R_MERC * lon * D2R, my = R_MERC * Math.log(Math.tan(Math.PI / 4 + lat * D2R / 2));
  return [(mx - grid.mx0) / grid.step, (grid.my0 - my) / grid.step];
}
// UTM easting and northing of every pixel centre, for one zone. Computed on a coarse lattice and
// filled in between (the projection is smooth: the error is far below a millimetre).
function gridUTM(grid, proj) {
  const key = proj.zone + (proj.south ? "S" : "N");
  if (grid.cache.has(key)) return grid.cache.get(key);
  const n = grid.size, STEP = 16, m = Math.ceil((n - 1) / STEP) + 1;
  const le = new Float64Array(m * m), ln = new Float64Array(m * m);
  for (let j = 0; j < m; j++) for (let i = 0; i < m; i++) {
    const [la, lo] = gridPoint(grid, i * STEP + 0.5, j * STEP + 0.5);
    const [e, nn] = UTM.forward(la, lo, proj.zone, proj.south);
    le[j * m + i] = e; ln[j * m + i] = nn;
  }
  const E = new Float64Array(n * n), N = new Float64Array(n * n);
  for (let y = 0; y < n; y++) {
    const j = Math.min(m - 2, Math.floor(y / STEP)), fy = (y - j * STEP) / STEP;
    for (let x = 0; x < n; x++) {
      const i = Math.min(m - 2, Math.floor(x / STEP)), fx = (x - i * STEP) / STEP;
      const a = j * m + i, b = a + 1, c = a + m, d = c + 1;
      const w00 = (1 - fx) * (1 - fy), w10 = fx * (1 - fy), w01 = (1 - fx) * fy, w11 = fx * fy;
      E[y * n + x] = le[a] * w00 + le[b] * w10 + le[c] * w01 + le[d] * w11;
      N[y * n + x] = ln[a] * w00 + ln[b] * w10 + ln[c] * w01 + ln[d] * w11;
    }
  }
  const out = {E, N};
  grid.cache.set(key, out);
  return out;
}

/* ------------------------------------------------------------------ inflate (zlib) */
// The tiles are zlib-compressed. Browsers have a native decompressor (DecompressionStream); this
// plain one is the fallback for phones without it, and what the tests check against Node's zlib.
const LEN_BASE = [3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258];
const LEN_EXTRA = [0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0];
const DIST_BASE = [1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577];
const DIST_EXTRA = [0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13];
const CL_ORDER = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15];
// a lookup table for one Huffman code: index = the next `bits` bits, value = symbol << 4 | length
function huffTable(lengths, count) {
  let bits = 0;
  for (let i = 0; i < count; i++) if (lengths[i] > bits) bits = lengths[i];
  if (!bits) return {table: new Uint16Array(1), bits: 0};
  const table = new Uint16Array(1 << bits), blCount = new Uint16Array(16), next = new Uint16Array(16);
  for (let i = 0; i < count; i++) blCount[lengths[i]]++;
  blCount[0] = 0;
  for (let b = 1, code = 0; b <= bits; b++) { code = (code + blCount[b - 1]) << 1; next[b] = code; }
  for (let s = 0; s < count; s++) {
    const len = lengths[s];
    if (!len) continue;
    let code = next[len]++, rev = 0;
    for (let i = 0; i < len; i++) { rev = (rev << 1) | (code & 1); code >>= 1; }
    for (let i = rev; i < table.length; i += 1 << len) table[i] = (s << 4) | len;
  }
  return {table, bits};
}
let FIXED = null;
function fixedTables() {
  if (FIXED) return FIXED;
  const l = new Uint8Array(288), d = new Uint8Array(30).fill(5);
  for (let i = 0; i < 288; i++) l[i] = i < 144 ? 8 : i < 256 ? 9 : i < 280 ? 7 : 8;
  return FIXED = {lit: huffTable(l, 288), dist: huffTable(d, 30)};
}
function inflate(src, size) {
  if (src.length < 6 || (src[0] & 15) !== 8 || ((src[0] << 8) | src[1]) % 31) throw new ImageryError("format", "not zlib data");
  const out = new Uint8Array(size);
  let pos = 2, op = 0, buf = 0, cnt = 0, last = 0;
  const bad = () => { throw new ImageryError("format", "damaged tile"); };
  const bits = n => {
    while (cnt < n) { buf |= src[pos++] << cnt; cnt += 8; }
    const v = buf & ((1 << n) - 1); buf >>>= n; cnt -= n; return v;
  };
  while (!last) {
    last = bits(1);
    const type = bits(2);
    if (type === 0) {
      buf = 0; cnt = 0;
      const len = src[pos] | (src[pos + 1] << 8); pos += 4;
      if (op + len > size || pos + len > src.length) bad();
      out.set(src.subarray(pos, pos + len), op); pos += len; op += len;
      continue;
    }
    let lit, dist;
    if (type === 1) ({lit, dist} = fixedTables());
    else if (type === 2) {
      const hlit = bits(5) + 257, hdist = bits(5) + 1, hclen = bits(4) + 4;
      const cl = new Uint8Array(19);
      for (let i = 0; i < hclen; i++) cl[CL_ORDER[i]] = bits(3);
      const ct = huffTable(cl, 19), lens = new Uint8Array(hlit + hdist);
      for (let i = 0; i < hlit + hdist;) {
        while (cnt < ct.bits) { buf |= src[pos++] << cnt; cnt += 8; }
        const e = ct.table[buf & ((1 << ct.bits) - 1)], l = e & 15, s = e >>> 4;
        if (!l) bad();
        buf >>>= l; cnt -= l;
        if (s < 16) lens[i++] = s;
        else {
          const prev = s === 16 ? (i ? lens[i - 1] : bad()) : 0;
          let rep = s === 16 ? 3 + bits(2) : s === 17 ? 3 + bits(3) : 11 + bits(7);
          if (i + rep > hlit + hdist) bad();
          while (rep--) lens[i++] = prev;
        }
      }
      lit = huffTable(lens, hlit); dist = huffTable(lens.subarray(hlit), hdist);
    } else bad();
    const lt = lit.table, lm = (1 << lit.bits) - 1, dt = dist.table, dm = (1 << dist.bits) - 1;
    for (;;) {
      while (cnt < 15) { buf |= src[pos++] << cnt; cnt += 8; }
      let e = lt[buf & lm], l = e & 15, s = e >>> 4;
      if (!l) bad();
      buf >>>= l; cnt -= l;
      if (s < 256) { if (op >= size) bad(); out[op++] = s; continue; }
      if (s === 256) break;
      s -= 257;
      if (s > 28) bad();
      const len = LEN_BASE[s] + (LEN_EXTRA[s] ? bits(LEN_EXTRA[s]) : 0);
      while (cnt < 15) { buf |= src[pos++] << cnt; cnt += 8; }
      e = dt[buf & dm]; l = e & 15; s = e >>> 4;
      if (!l || s > 29) bad();
      buf >>>= l; cnt -= l;
      const d = DIST_BASE[s] + (DIST_EXTRA[s] ? bits(DIST_EXTRA[s]) : 0);
      if (d > op || op + len > size) bad();
      if (d >= len) { out.set(out.subarray(op - d, op - d + len), op); op += len; }
      else for (let i = 0; i < len; i++, op++) out[op] = out[op - d];
    }
  }
  if (op !== size) bad();
  return out;
}
async function decompress(bytes, size) {
  if (typeof DecompressionStream === "function" && typeof Response === "function" && typeof Blob === "function") {
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("deflate"));
      const out = new Uint8Array(await new Response(stream).arrayBuffer());
      if (out.length === size) return out;
    } catch (e) { /* fall through to the plain decoder */ }
  }
  return inflate(bytes, size);
}

/* ------------------------------------------------------------------ the engine */
function createEngine(options = {}) {
  const doFetch = options.fetch || ((...a) => fetch(...a));
  const searchUrl = options.searchUrl || SEARCH_URL;
  const landsatUrl = options.landsat === false ? null : (options.landsatUrl || LANDSAT_URL);
  const unpack = options.decompress || decompress;
  const stats = {bytes: 0, requests: 0};
  const BUDGET = {json: 25000, head: 20000, tile: 45000, ...(options.budgets || {})};   // ms, per request

  /* ---------- network: every request has a time budget and one retry ---------- */
  let active = 0;
  const waiting = [];
  const MAX_ACTIVE = options.concurrency || 6;
  async function slot() {
    if (active >= MAX_ACTIVE) await new Promise(res => waiting.push(res));
    active++;
  }
  function release() { active--; const next = waiting.shift(); if (next) next(); }
  async function request(url, init, budget, signal, read) {
    let lastError = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      await slot();
      if (signal && signal.aborted) { release(); throw new ImageryError("aborted"); }
      const ctl = new AbortController();
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; ctl.abort(); }, budget);
      const onAbort = () => ctl.abort();
      if (signal) signal.addEventListener("abort", onAbort, {once: true});
      try {
        const res = await doFetch(url, {...init, signal: ctl.signal});
        if (res.status === 404 || res.status === 403) throw new ImageryError("missing", "HTTP " + res.status);
        if (!res.ok) throw new ImageryError("source", "HTTP " + res.status);
        const value = await read(res);
        stats.requests++;
        return value;
      } catch (e) {
        if (signal && signal.aborted) throw new ImageryError("aborted");
        if (e instanceof ImageryError && e.code === "missing") throw e;
        lastError = timedOut ? new ImageryError("timeout", "no answer in time")
          : e instanceof ImageryError ? e
          : new ImageryError(typeof navigator !== "undefined" && navigator.onLine === false ? "offline" : "source", String(e && e.message || e));
      } finally {
        clearTimeout(timer);
        if (signal) signal.removeEventListener("abort", onAbort);
        release();
      }
    }
    throw lastError;
  }
  // the token for Landsat's files: asked for once, used until shortly before it runs out
  let token = null, asking = null;
  async function signed(url, signal) {
    if (!SIGNED_HOST.test(url)) return url;
    if (!token || token.until - Date.now() < 5 * 60e3) {
      // many tiles start at once: they all wait for the one request (the service limits how often it is asked)
      if (!asking) {
        asking = getJSON(`${landsatUrl}/sas/v1/token/${LANDSAT}`, null, null).then(d => {
          if (!d || !d.token) throw new ImageryError("source", "no token");
          token = {value: d.token, until: Date.parse(d["msft:expiry"]) || Date.now() + 30 * 60e3};
        }).finally(() => { asking = null; });
      }
      await asking;
      if (signal && signal.aborted) throw new ImageryError("aborted");
    }
    return url + "?" + token.value;
  }
  async function getRange(url, start, end, signal, budget = BUDGET.tile) {
    const address = await signed(url, signal);
    return request(address, {headers: {Range: `bytes=${start}-${end}`}}, budget, signal, async res => {
      if (res.status !== 206) {                          // never download a whole 200 MB file by accident
        try { if (res.body) res.body.cancel(); } catch (e) { /* nothing to cancel */ }
        throw new ImageryError("source", "range not served");
      }
      const bytes = new Uint8Array(await res.arrayBuffer());
      stats.bytes += bytes.length;
      return bytes;
    });
  }
  async function getJSON(url, body, signal) {
    const init = body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {};
    return request(url, init, BUDGET.json, signal, async res => {
      const text = await res.text();
      stats.bytes += text.length;
      try { return JSON.parse(text); } catch (e) { throw new ImageryError("source", "bad answer"); }
    });
  }

  /* ---------- what is kept in memory: file headers, decoded tiles, finished bands ---------- */
  // One piece of work shared by everyone who asks for it while it runs, and kept afterwards within a
  // byte budget (least recently used dropped first). Each caller can give up on its own (its
  // signal): the work itself is cancelled only when nobody is left waiting for it, so a view the
  // user has left stops downloading while a view still open keeps what it shares with it.
  function makeStore(budget) {
    const map = new Map();
    const store = {map, bytes: 0, budget, dropped: 0};
    const drop = (key, e) => { if (map.get(key) === e) { map.delete(key); store.bytes -= e.bytes; } };
    store.get = (key, signal, bytes, start) => {
      if (signal && signal.aborted) return Promise.reject(new ImageryError("aborted"));
      let e = map.get(key);
      if (e) { map.delete(key); map.set(key, e); }          // now the most recently used
      else {
        const ctl = new AbortController();
        e = {ctl, bytes, users: 0, pinned: false, done: false};
        e.promise = start(ctl.signal);
        const mine = e;
        e.promise.then(() => { mine.done = true; }, () => drop(key, mine));
        map.set(key, e); store.bytes += bytes;
        for (const [k, old] of map) {                        // forget what was used longest ago
          if (store.bytes <= store.budget || k === key) break;
          map.delete(k); store.bytes -= old.bytes; store.dropped++;
        }
      }
      if (e.done) return e.promise;
      if (!signal) { e.pinned = true; return e.promise; }
      const entry = e;
      entry.users++;
      return new Promise((resolve, reject) => {
        const leave = () => {
          reject(new ImageryError("aborted"));
          if (--entry.users === 0 && !entry.pinned && !entry.done) { drop(key, entry); entry.ctl.abort(); }
        };
        signal.addEventListener("abort", leave, {once: true});
        entry.promise.then(v => { signal.removeEventListener("abort", leave); resolve(v); },
          err => { signal.removeEventListener("abort", leave); reject(err); });
      });
    };
    store.clear = () => { map.clear(); store.bytes = 0; };
    return store;
  }

  /* ---------- Cloud-Optimized GeoTIFF: header once, then only the tiles asked for ---------- */
  const HEAD_BYTES = 8192;                     // the directory of these files ends near 4 KB
  const TYPE_SIZE = {1:1, 2:1, 3:2, 4:4, 5:8, 6:1, 7:1, 8:2, 9:4, 10:8, 11:4, 12:8};
  const cogs = makeStore(Infinity);                         // url -> the opened file (a few KB each)
  const tiles = makeStore(options.tileCache || 56e6);       // decoded tiles of the files
  const bands = makeStore(options.bandCache || 96e6);       // finished bands on a picture's grid
  const openCog = (url, signal) => cogs.get(url, signal, 0, sig => readHeader(url, sig));
  async function readHeader(url, signal) {
    // Landsat's files have many small tiles: their directory is longer
    const first = SIGNED_HOST.test(url) ? 4 * HEAD_BYTES : HEAD_BYTES;
    const chunks = [{start: 0, bytes: await getRange(url, 0, first - 1, signal, BUDGET.head)}];
    const view = async (off, len) => {             // a DataView on [off, off+len) of the file
      let c = chunks.find(c => off >= c.start && off + len <= c.start + c.bytes.length);
      if (!c) {
        c = {start: off, bytes: await getRange(url, off, off + Math.max(len, HEAD_BYTES) - 1, signal, BUDGET.head)};
        if (c.bytes.length < len) throw new ImageryError("format", "short file");
        chunks.push(c);
      }
      return new DataView(c.bytes.buffer, c.bytes.byteOffset + off - c.start, len);
    };
    const h = await view(0, 8);
    if (h.getUint16(0) !== 0x4949 || h.getUint16(2, true) !== 42) throw new ImageryError("format", "not a little-endian TIFF");
    const levels = [];
    let geo = null, off = h.getUint32(4, true);
    for (let guard = 0; off && guard < 24; guard++) {
      const n = (await view(off, 2)).getUint16(0, true);
      const dir = await view(off + 2, n * 12 + 4);
      const tags = {};
      for (let i = 0; i < n; i++) {
        const p = i * 12, tag = dir.getUint16(p, true), type = dir.getUint16(p + 2, true), count = dir.getUint32(p + 4, true);
        const size = TYPE_SIZE[type];
        if (!size || ![254, 256, 257, 258, 259, 277, 284, 317, 322, 323, 324, 325, 339, 33550, 33922].includes(tag)) continue;
        const data = size * count <= 4 ? new DataView(dir.buffer, dir.byteOffset + p + 8, 4) : await view(dir.getUint32(p + 8, true), size * count);
        const values = type === 12 ? new Float64Array(count) : new Array(count);
        for (let k = 0; k < count; k++) {
          values[k] = type === 3 ? data.getUint16(k * 2, true) : type === 4 ? data.getUint32(k * 4, true)
            : type === 12 ? data.getFloat64(k * 8, true) : data.getUint8(k * size);
        }
        tags[tag] = values;
      }
      off = dir.getUint32(n * 12, true);
      const one = t => tags[t] ? tags[t][0] : undefined;
      if ((one(254) || 0) & 4) continue;                                 // a transparency mask, not a picture
      const lv = {width: one(256), height: one(257), bits: one(258), spp: one(277) || 1, tileW: one(322), tileH: one(323),
        offsets: tags[324], counts: tags[325], predictor: one(317) || 1, compression: one(259) || 1};
      if (!lv.tileW || !lv.offsets || !lv.counts) throw new ImageryError("format", "not a tiled TIFF");
      if (![1, 8, 32946].includes(lv.compression) || ![8, 16].includes(lv.bits) || (one(284) || 1) !== 1 || (one(339) || 1) !== 1 || lv.predictor > 2)
        throw new ImageryError("format", "unsupported TIFF layout");
      lv.across = Math.ceil(lv.width / lv.tileW);
      if (!levels.length) {
        const s = tags[33550], t = tags[33922];
        if (!s || !t) throw new ImageryError("format", "no georeference");
        geo = {x0: t[3] - t[0] * s[0], y0: t[4] + t[1] * s[1], sx: s[0], sy: s[1]};
      }
      lv.sx = levels.length ? geo.sx * levels[0].width / lv.width : geo.sx;      // metres per pixel at this level
      lv.sy = levels.length ? geo.sy * levels[0].height / lv.height : geo.sy;
      levels.push(lv);
    }
    if (!levels.length) throw new ImageryError("format", "empty TIFF");
    return {url, levels, x0: geo.x0, y0: geo.y0};
  }
  // the coarsest level that is still at least as sharp as asked (metres per pixel)
  function levelFor(cog, res) {
    let best = 0;
    for (let i = 1; i < cog.levels.length; i++) if (cog.levels[i].sx <= res * 1.02) best = i;
    return best;
  }
  function readTile(cog, li, tx, ty, signal) {
    const key = cog.url + "|" + li + "|" + tx + "|" + ty;
    const lv = cog.levels[li], idx = ty * lv.across + tx;
    const samples = lv.tileW * lv.tileH * lv.spp, raw = samples * lv.bits / 8;
    return tiles.get(key, signal, raw, async sig => {
      const count = lv.counts[idx], offset = lv.offsets[idx];
      if (!count) return lv.bits === 16 ? new Uint16Array(samples) : new Uint8Array(samples);   // an empty tile
      const packed = await getRange(cog.url, offset, offset + count - 1, sig);
      const bytes = lv.compression === 1 ? packed : await unpack(packed, raw);
      if (bytes.length < raw) throw new ImageryError("format", "short tile");
      let data;
      if (lv.bits === 16) {
        data = bytes.byteOffset % 2 === 0 ? new Uint16Array(bytes.buffer, bytes.byteOffset, samples) : new Uint16Array(bytes.slice(0, raw).buffer);
      } else data = bytes;
      if (lv.predictor === 2) {                         // rows stored as differences from the pixel before
        const w = lv.tileW * lv.spp, s = lv.spp;
        for (let r = 0; r < lv.tileH; r++) for (let i = r * w + s, end = (r + 1) * w; i < end; i++) data[i] += data[i - s];
      }
      return data;
    });
  }
  // One band of a file resampled onto the grid. `linear` blends the four nearest pixels (pictures),
  // otherwise the nearest pixel is taken (classes). Returns Float32Array with NaN where the file
  // has nothing (reflectance = number * scale + offset), or Uint8Array for classes.
  async function sampleBand(url, grid, proj, opt, signal) {
    const cog = await openCog(url, signal);
    const li = levelFor(cog, opt.res || grid.res), lv = cog.levels[li];
    const {E, N} = gridUTM(grid, proj), n = grid.size, total = n * n;
    // the window of the file that the grid needs
    let c0 = Infinity, c1 = -Infinity, r0 = Infinity, r1 = -Infinity;
    for (const i of [0, n - 1, total - n, total - 1, (total >> 1) + (n >> 1)]) {
      const c = (E[i] - cog.x0) / lv.sx, r = (cog.y0 - N[i]) / lv.sy;
      if (c < c0) c0 = c; if (c > c1) c1 = c; if (r < r0) r0 = r; if (r > r1) r1 = r;
    }
    const wx0 = Math.max(0, Math.floor(c0) - 2), wy0 = Math.max(0, Math.floor(r0) - 2);
    const wx1 = Math.min(lv.width - 1, Math.ceil(c1) + 2), wy1 = Math.min(lv.height - 1, Math.ceil(r1) + 2);
    const out = opt.linear ? new Float32Array(total) : new Uint8Array(total);
    if (opt.linear) out.fill(NaN);
    if (wx1 < wx0 || wy1 < wy0) return out;            // the grid is outside this tile
    const ww = wx1 - wx0 + 1, wh = wy1 - wy0 + 1;
    const win = lv.bits === 16 ? new Uint16Array(ww * wh) : new Uint8Array(ww * wh);
    const jobs = [];
    for (let ty = Math.floor(wy0 / lv.tileH); ty <= Math.floor(wy1 / lv.tileH); ty++)
      for (let tx = Math.floor(wx0 / lv.tileW); tx <= Math.floor(wx1 / lv.tileW); tx++)
        jobs.push(readTile(cog, li, tx, ty, signal).then(data => {
          const ox = tx * lv.tileW, oy = ty * lv.tileH;
          const xa = Math.max(wx0, ox), xb = Math.min(wx1, ox + lv.tileW - 1);
          for (let y = Math.max(wy0, oy), ye = Math.min(wy1, oy + lv.tileH - 1); y <= ye; y++) {
            const from = ((y - oy) * lv.tileW + (xa - ox)) * lv.spp;
            if (lv.spp === 1) win.set(data.subarray(from, from + xb - xa + 1), (y - wy0) * ww + xa - wx0);
            else for (let x = xa; x <= xb; x++) win[(y - wy0) * ww + x - wx0] = data[from + (x - xa) * lv.spp];
          }
        }));
    await Promise.all(jobs);
    const scale = opt.scale ?? 1, offset = opt.offset ?? 0;
    for (let i = 0; i < total; i++) {
      const c = (E[i] - cog.x0) / lv.sx - wx0, r = (cog.y0 - N[i]) / lv.sy - wy0;
      if (!opt.linear) {
        const x = Math.floor(c), y = Math.floor(r);
        if (x >= 0 && y >= 0 && x < ww && y < wh) out[i] = win[y * ww + x];
        continue;
      }
      if (c < 0 || r < 0 || c >= ww || r >= wh) continue;          // outside the file
      const fx = c - 0.5, fy = r - 0.5;
      let x = Math.floor(fx), y = Math.floor(fy), ax = fx - x, ay = fy - y;
      if (x < 0) { x = 0; ax = 0; } else if (x > ww - 2) { x = Math.max(0, ww - 2); ax = ww > 1 ? 1 : 0; }
      if (y < 0) { y = 0; ay = 0; } else if (y > wh - 2) { y = Math.max(0, wh - 2); ay = wh > 1 ? 1 : 0; }
      const p = y * ww + x, q = wh > 1 ? p + ww : p, dx = ww > 1 ? 1 : 0;
      const a = win[p], b = win[p + dx], d = win[q], e = win[q + dx];
      if (a && b && d && e) out[i] = (a * (1 - ax) * (1 - ay) + b * ax * (1 - ay) + d * (1 - ax) * ay + e * ax * ay) * scale + offset;
      else {                                            // at the edge of the swath: the nearest pixel, if it holds data
        const v = win[(ay < 0.5 ? p : q) + (ax < 0.5 ? 0 : dx)];
        if (v) out[i] = v * scale + offset;
      }
    }
    return out;
  }

  /* ---------- the index: which passes cover the box ---------- */
  const FIELDS = {
    include: ["id", "collection", "geometry", "properties.datetime", "properties.eo:cloud_cover", "properties.platform",
      "properties.grid:code", "properties.proj:code", "properties.proj:epsg"],
    exclude: ["assets", "links", "stac_extensions", "stac_version", "bbox", "type"],
  };
  function inRing(ring, lon, lat) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function inGeometry(geom, lon, lat) {
    if (!geom) return false;
    const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
    return polys.some(p => p.length && inRing(p[0], lon, lat) && !p.slice(1).some(hole => inRing(hole, lon, lat)));
  }
  // the share of the box inside the picture's real footprint (a pass can cut the box at the swath edge)
  function coverage(geom, grid) {
    const K = 9;
    let hit = 0;
    for (let j = 0; j < K; j++) for (let i = 0; i < K; i++) {
      const [la, lo] = gridPoint(grid, (i + 0.5) / K * grid.size, (j + 0.5) / K * grid.size);
      if (inGeometry(geom, lo, la)) hit++;
    }
    return hit / (K * K);
  }
  // the same 9 x 9 points, one by one: which of them a footprint holds
  function cells(geom, grid) {
    const K = 9, out = new Uint8Array(K * K);
    for (let j = 0; j < K; j++) for (let i = 0; i < K; i++) {
      const [la, lo] = gridPoint(grid, (i + 0.5) / K * grid.size, (j + 0.5) / K * grid.size);
      if (inGeometry(geom, lo, la)) out[j * K + i] = 1;
    }
    return out;
  }
  // A box wider than one picture tile (the tiles are about 110 km wide): the other tiles of the same
  // pass fill in what the best one leaves out. `pass.parts` then lists them, the best first, and
  // `pass.cover` is what they hold together. A box inside one tile is left exactly as it was.
  const FULL = 0.985;
  function joinTiles(pass, best, others, grid) {
    const have = cells(best.geometry, grid), parts = [best];
    for (const t of others) {
      if (parts.some(p => (p.grid && p.grid === t.grid) || p.id === t.id)) continue;   // the same tile from the other archive
      const c = cells(t.geometry, grid);
      let adds = 0;
      for (let i = 0; i < c.length; i++) if (c[i] && !have[i]) adds++;
      if (!adds) continue;
      for (let i = 0; i < c.length; i++) if (c[i]) have[i] = 1;
      parts.push(t);
    }
    if (parts.length < 2) return;
    pass.parts = parts.map(t => { const part = {...t}; delete part.geometry; return part; });
    pass.cover = have.reduce((sum, x) => sum + x, 0) / have.length;
  }
  const iso = t => new Date(t).toISOString().slice(0, 19) + "Z";
  // The passes over the box between two times, newest first. One entry per satellite per day; when
  // several overlapping tiles hold the same pass, the one that covers the box best is kept.
  async function listPasses(grid, from, to, signal) {
    // Landsat is an extra: if its index does not answer, the Sentinel-2 passes are still listed
    const [own, extra] = await Promise.all([listSentinel(grid, from, to, signal),
      landsatUrl ? listLandsat(grid, from, to, signal).catch(e => { if (e.code === "aborted") throw e; return []; }) : []]);
    return own.concat(extra).sort((a, b) => b.time - a.time);
  }
  async function listLandsat(grid, from, to, signal) {
    const body = {collections: [LANDSAT], bbox: [grid.west, grid.south, grid.east, grid.north],
      datetime: iso(from) + "/" + iso(to), limit: 100, query: {platform: {in: ["landsat-8", "landsat-9"]}}};
    const d = await getJSON(landsatUrl + "/stac/v1/search", body, signal);
    if (!d || !Array.isArray(d.features)) throw new ImageryError("source", "bad answer");
    const groups = new Map();
    for (const it of d.features) {
      const p = it.properties || {}, a = it.assets || {};
      let proj;
      try { proj = projectionOf(p); } catch (e) { continue; }
      if (!it.id || !p.datetime || !a.qa_pixel || REFLECTANCE.some(name => !a[name] || !/^https:/.test(a[name].href || ""))) continue;
      const cover = coverage(it.geometry, grid);
      if (cover <= 0) continue;
      const assets = {qa: {href: a.qa_pixel.href}};
      for (const name of REFLECTANCE) {
        const band = (a[name]["raster:bands"] || [])[0] || {};
        assets[name] = {href: a[name].href, scale: band.scale ?? 2.75e-5, offset: band.offset ?? -0.2};
      }
      const key = p.datetime.slice(0, 10) + "|" + (p.platform || "landsat");
      const pass = {key, date: p.datetime.slice(0, 10), datetime: p.datetime, time: Date.parse(p.datetime), platform: p.platform || "landsat",
        id: it.id, collection: LANDSAT, sensor: "landsat", grid: "", proj, cover, tileCloud: p["eo:cloud_cover"], sky: null, assets, assetsChecked: true,
        geometry: it.geometry};
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(pass);
    }
    const out = [];
    for (const g of groups.values()) {
      // the first of the best-covering pictures, as before; a second one of the same day joins it for a wide box
      const best = g.reduce((a, b) => b.cover > a.cover ? b : a), geometry = best.geometry;
      const pass = {...best};
      delete pass.geometry;
      if (best.cover < FULL && g.length > 1) joinTiles(pass, {...best, geometry}, g.filter(t => t !== best).sort((a, b) => b.cover - a.cover), grid);
      out.push(pass);
    }
    return out;
  }
  async function listSentinel(grid, from, to, signal) {
    const body = {collections: COLLECTIONS, bbox: [grid.west, grid.south, grid.east, grid.north],
      datetime: iso(from) + "/" + iso(to), limit: 500, fields: FIELDS};
    const items = [];
    let next = body;
    for (let page = 0; next && page < 6; page++) {
      const d = await getJSON(searchUrl + "/search", next, signal);
      if (!d || !Array.isArray(d.features)) throw new ImageryError("source", "bad answer");
      items.push(...d.features);
      const link = (d.links || []).find(l => l.rel === "next" && l.body);
      next = link && d.features.length ? (link.merge ? {...body, ...link.body} : link.body) : null;
    }
    const groups = new Map();
    for (const it of items) {
      const p = it.properties || {};
      if (!it.id || !p.datetime) continue;
      let proj;
      try { proj = projectionOf(p); } catch (e) { continue; }
      const tile = {id: it.id, collection: it.collection, datetime: p.datetime, platform: p.platform || "",
        grid: String(p["grid:code"] || "").replace(/^MGRS-/, ""), proj, tileCloud: p["eo:cloud_cover"],
        cover: coverage(it.geometry, grid), geometry: it.geometry};
      if (tile.cover <= 0) continue;
      const key = p.datetime.slice(0, 10) + "|" + tile.platform;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(tile);
    }
    // prefer one and the same tile for every date (identical pixels to compare), where it covers as well
    const weight = new Map();
    for (const g of groups.values()) for (const t of g) weight.set(t.grid, (weight.get(t.grid) || 0) + t.cover);
    const rank = t => t.cover * 1000 + (t.collection === COLLECTIONS[0] ? 0.5 : 0) + (weight.get(t.grid) || 0) / 1e4;
    const passes = [];
    for (const [key, g] of groups) {
      // the same picture is often in both archives: keep the reprocessed one
      const best = g.reduce((a, b) => rank(b) > rank(a) ? b : a);
      const pass = {key, date: best.datetime.slice(0, 10), datetime: best.datetime, time: Date.parse(best.datetime),
        platform: best.platform, id: best.id, collection: best.collection, grid: best.grid, proj: best.proj,
        cover: best.cover, tileCloud: best.tileCloud, sky: null};
      if (best.cover < FULL && g.length > 1) joinTiles(pass, best, g.filter(t => t !== best).sort((a, b) => rank(b) - rank(a)), grid);
      passes.push(pass);
    }
    passes.sort((a, b) => b.time - a.time);
    return passes;
  }

  /* ---------- where the files of a pass are ---------- */
  // The reprocessed archive keeps a fixed layout, so the addresses follow from the name; if that
  // ever fails (or for the older archive) the full record is asked for.
  function guessAssets(pass) {
    const m = /^(\d{1,2})([A-Z])([A-Z]{2})$/.exec(pass.grid);
    if (pass.collection !== COLLECTIONS[0] || !m) return null;
    const dir = `${C1_FILES}${+m[1]}/${m[2]}/${m[3]}/${pass.datetime.slice(0, 4)}/${+pass.datetime.slice(5, 7)}/${pass.id}/`;
    const assets = {};
    for (const [name, file] of Object.entries({...BAND_FILE, ...EXTRA_FILE})) assets[name] = {href: dir + file + ".tif", scale: 1e-4, offset: -0.1};
    return assets;
  }
  async function fetchAssets(pass, signal) {
    const d = await getJSON(`${searchUrl}/collections/${pass.collection}/items/${pass.id}`, null, signal);
    const assets = {};
    for (const name of [...Object.keys(BAND_FILE), ...Object.keys(EXTRA_FILE)]) {
      const a = d && d.assets && d.assets[name];
      if (!a || !/^https:/.test(a.href || "")) {
        if (name in EXTRA_FILE) continue;                  // a pass without the extra band is still a pass
        throw new ImageryError("source", "pass without " + name);
      }
      const band = (a["raster:bands"] || [])[0] || {};
      assets[name] = {href: a.href, scale: band.scale ?? 1e-4, offset: band.offset ?? 0};
    }
    return assets;
  }
  // run `work(assets)`; when the guessed addresses turn out wrong, once more with the real record
  async function withAssets(pass, signal, work) {
    if (!pass.assets) pass.assets = guessAssets(pass) || await fetchAssets(pass, signal);
    try { return await work(pass.assets); }
    catch (e) {
      if (!(e instanceof ImageryError) || e.code !== "missing" || pass.assetsChecked) throw e.code === "missing" ? new ImageryError("source", e.message) : e;
      pass.assetsChecked = true;
      pass.assets = await fetchAssets(pass, signal);
      return work(pass.assets);
    }
  }

  /* ---------- the sky over the box ---------- */
  // ESA's scene classes on a 20 m grid of the box (cheap: a few kilobytes per pass).
  async function assessPass(pass, grid, signal) {
    if (pass.sky) return pass.sky;
    const g = skyGrid(grid);
    // each tile that holds a part of the pass (one, unless the box is wider than a tile)
    const got = await Promise.all(partsOf(pass).map(part => part.sensor === "landsat"
      ? sampleBand(part.assets.qa.href, g, part.proj, {res: Math.max(30, g.res)}, signal).then(qaClasses)
      : withAssets(part, signal, a => sampleBand(a.scl.href, g, part.proj, {res: Math.max(20, g.res)}, signal))));
    pass.scl = got.reduce((into, from) => fill(into, from, true));
    return pass.sky = skyOf(pass.scl);
  }
  // 20 m points up to a 20 km box, as ever; a wider box is judged on 500 x 500 points (enough to
  // tell how much of it is under cloud, and a hundred times less to read and to keep per pass)
  function skyGrid(grid) {
    if (!grid.sky) grid.sky = makeGrid(grid.lat, grid.lon, grid.halfKm, grid.halfKm <= 10 ? Math.max(8, Math.round(grid.halfKm * 100)) : 500);
    return grid.sky;
  }
  const partsOf = pass => pass.parts || [pass];
  // what the first tile of a pass does not hold is taken from the next one
  function fill(into, from, classes) {
    if (classes) { for (let i = 0; i < into.length; i++) if (!into[i]) into[i] = from[i]; }
    else for (let i = 0; i < into.length; i++) if (into[i] !== into[i]) into[i] = from[i];
    return into;
  }

  /* ---------- one pass as a picture, band by band ---------- */
  const NATIVE = {red: 10, green: 10, blue: 10, nir: 10, nir08: 20, swir16: 20, swir22: 20};
  const gridKey = g => [g.lat.toFixed(5), g.lon.toFixed(5), g.halfKm, g.size].join(",");
  // One band of a pass on the picture's grid, read once and kept (within the byte budget) for every
  // view that asks for it again. `res` is the sharpness asked for; a band is never read sharper than
  // the satellite took it, so the 20 m bands of a quick look and of the full picture are one copy.
  function band(pass, grid, name, res, signal) {
    const landsat = pass.sensor === "landsat", classes = name === "scl";
    const real = classes ? Math.max(landsat ? 30 : 20, grid.res) : Math.max(res, landsat ? 30 : NATIVE[name]);
    const key = [pass.collection, pass.id, gridKey(grid), name, real].join("|");
    return bands.get(key, signal, grid.size * grid.size * (classes ? 1 : 4), async sig => {
      const got = await Promise.all(partsOf(pass).map(part => {
        if (classes && part.sensor === "landsat") return sampleBand(part.assets.qa.href, grid, part.proj, {res: real}, sig).then(qaClasses);
        return withAssets(part, sig, a => {
          const file = a[name];
          if (!file) throw new ImageryError("noband", "pass without " + name);
          return sampleBand(file.href, grid, part.proj, classes ? {res: real} : {res: real, linear: true, scale: file.scale, offset: file.offset}, sig);
        });
      }));
      return got.reduce((into, from) => fill(into, from, classes));
    });
  }
  // The bands asked for, and the scene classes, of one pass: "fast" reads nothing sharper than 20 m,
  // "full" the grid's own sharpness. «nir» (band 8, the only infrared band at 10 m) is worth its
  // download only for a sharp picture: otherwise, on Landsat, and where a pass has no such file,
  // band 8A stands in for it. `from` says which band each name really is.
  async function loadBands(pass, grid, names, quality, signal) {
    const landsat = pass.sensor === "landsat";
    const res = quality === "fast" ? Math.max(20, grid.res) : grid.res;
    const scene = {pass, grid, quality, res: Math.max(res, landsat ? 30 : 10), from: {}};
    await Promise.all([...new Set([...names, "scl"])].map(async name => {
      let real = name === "nir" && (landsat || res >= 20) ? "nir08" : name, data;
      try { data = await band(pass, grid, real, res, signal); }
      catch (e) {
        if (real !== "nir" || !["noband", "missing"].includes(e.code)) throw e;
        real = "nir08";
        data = await band(pass, grid, real, res, signal);
      }
      scene[name] = data; scene.from[name] = real;
    }));
    return scene;
  }

  /* ---------- one pass as a picture ---------- */
  // quality "fast": everything at 20 m (a quarter of the download), "full": the grid's own sharpness.
  async function loadScene(pass, grid, quality, signal) {
    if (pass.parts) return loadBands(pass, grid, REFLECTANCE, quality, signal);
    const res = quality === "fast" ? Math.max(20, grid.res) : grid.res;
    return withAssets(pass, signal, async a => {
      const landsat = pass.sensor === "landsat";
      const jobs = REFLECTANCE.map(name => sampleBand(a[name].href, grid, pass.proj,
        {res, linear: true, scale: a[name].scale, offset: a[name].offset}, signal));
      jobs.push(landsat ? sampleBand(a.qa.href, grid, pass.proj, {res: 30}, signal).then(qaClasses)
        : sampleBand(a.scl.href, grid, pass.proj, {res: 20}, signal));
      const got = await Promise.all(jobs);
      const scene = {pass, grid, quality, res: Math.max(res, landsat ? 30 : 10), scl: got[REFLECTANCE.length]};
      REFLECTANCE.forEach((name, i) => scene[name] = got[i]);
      return scene;
    });
  }

  return {stats, listPasses, assessPass, loadScene, loadBands, openCog, sampleBand, skyGrid,
    // what the decoded tiles and the finished bands hold now, against their budgets (bytes)
    memory: () => ({tiles: tiles.bytes, tileBudget: tiles.budget, bands: bands.bytes, bandBudget: bands.budget,
      held: tiles.map.size + bands.map.size, dropped: tiles.dropped + bands.dropped}),
    forget() { cogs.clear(); tiles.clear(); bands.clear(); }};
}

// Landsat's quality bits (the low eight: nothing, cloud margin, cirrus, cloud, cloud shadow, snow,
// clear, water) as the scene classes the rest of this file works with.
function qaClasses(qa) {
  const out = new Uint8Array(qa.length);
  for (let i = 0; i < qa.length; i++) {
    const q = qa[i];
    out[i] = q & 1 || !q ? 0 : q & 10 ? 9 : q & 16 ? 3 : q & 4 ? 10 : q & 128 ? 6 : 5;
  }
  return out;
}

/* ------------------------------------------------------------------ the sky */
// ESA scene classes: 0 nothing, 1 defective, 2 dark, 3 cloud shadow, 4 vegetation, 5 bare,
// 6 water, 7 unclassified, 8 cloud (medium), 9 cloud (high), 10 thin cirrus, 11 snow
function skyOf(scl, ignore) {
  let cloud = 0, shadow = 0, cirrus = 0, nodata = 0, water = 0, n = scl.length;
  for (let i = 0; i < n; i++) {
    const c = scl[i];
    if (c === 0 || c === 1) nodata++;
    else if (ignore && ignore[i]) continue;
    else if (c === 8 || c === 9) cloud++;
    else if (c === 3) shadow++;
    else if (c === 10) cirrus++;
    else if (c === 6) water++;
  }
  if (cloud < n * 0.002) shadow = 0;      // no cloud, no cloud shadow: the class is then dark water or terrain
  const f = v => v / n;
  return {cloud: f(cloud), shadow: f(shadow), cirrus: f(cirrus), nodata: f(nodata), water: f(water),
    // what hides the ground: cloud and its shadow fully, thin cirrus by half
    hidden: f(cloud + shadow + cirrus * 0.5 + nodata)};
}
// Bright ground (salt flats, white roofs, sand) is often classed as cloud. A real cloud moves: a
// pixel called cloud in most of the passes is ground, and is no longer counted against any pass.
function refineSky(passes) {
  const seen = passes.filter(p => p.scl);
  if (seen.length < 6) return false;
  const n = seen[0].scl.length, times = new Uint16Array(n);
  for (const p of seen) { if (p.scl.length !== n) return false; for (let i = 0; i < n; i++) { const c = p.scl[i]; if (c === 8 || c === 9 || c === 3 || c === 10) times[i]++; } }
  const ignore = new Uint8Array(n), limit = seen.length * 0.5;
  let any = false;
  for (let i = 0; i < n; i++) if (times[i] > limit) { ignore[i] = 1; any = true; }
  if (any) for (const p of seen) p.sky = skyOf(p.scl, ignore);
  return any;
}
const CLEAR = 0.08;       // a pass is "clear" when less than this share of the box is hidden
const isClear = p => p.cover >= 0.985 && p.sky && p.sky.hidden <= CLEAR;
// How good a pass is for looking at the ground: 0 clear, 1 partly, 2 poor, 3 not assessed yet
function grade(p) {
  if (!p.sky) return 3;
  if (isClear(p)) return 0;
  return p.cover >= 0.6 && p.sky.hidden <= 0.35 ? 1 : 2;
}
// The default pair. With an event: the newest clear pass before it started and the first clear
// pass after its last sighting. Without one: the newest clear pass and the clear pass before it.
// `passes` newest first. Returns {before, after} (either can be null) from the assessed passes.
function pickPair(passes, event) {
  const usable = passes.filter(p => p.sky);
  const best = list => list.find(isClear) || list.filter(p => grade(p) === 1)[0] || null;
  if (event && event.start) {
    const start = Date.parse(event.start), end = Date.parse(event.end || event.start);
    const before = best(usable.filter(p => p.time < start));
    const later = usable.filter(p => p.time > end).reverse();          // oldest first
    return {before, after: best(later)};
  }
  const after = best(usable);
  const before = after ? best(usable.filter(p => p.time < after.time - 36e5)) : null;
  return {before, after};
}

/* ------------------------------------------------------------------ pictures */
function quantiles(values, qs) {
  const s = Float32Array.from(values).sort();
  return qs.map(q => s.length ? s[Math.min(s.length - 1, Math.max(0, Math.round(q * (s.length - 1))))] : NaN);
}
const cloudy = c => c === 8 || c === 9 || c === 3 || c === 0 || c === 1;
// One brightness range for both dates (so a real darkening still looks darker): the 0.5 % and
// 99.5 % points of the three bands together, ignoring cloud.
function stretchOf(scenes, bands) {
  const sample = [];
  for (const s of scenes) {
    if (!s) continue;
    const n = s[bands[0]].length, step = Math.max(1, Math.floor(n / 20000));
    for (let i = 0; i < n; i += step) {
      if (cloudy(s.scl[i])) continue;
      for (const b of bands) { const v = s[b][i]; if (v === v) sample.push(v); }
    }
  }
  if (sample.length < 30) return {lo: 0, hi: 0.4};
  let [lo, hi] = quantiles(sample, [0.005, 0.995]);
  lo = Math.max(0, Math.min(lo, 0.15));
  if (hi - lo < 0.12) hi = lo + 0.12;                  // flat scenes (open sea) are not blown up into noise
  return {lo, hi};
}
const GAMMA = new Uint8Array(1025);
for (let i = 0; i <= 1024; i++) GAMMA[i] = Math.round(255 * Math.pow(i / 1024, 1 / 1.7));
function composite(scene, bands, stretch) {
  const n = scene.grid.size * scene.grid.size, out = new Uint8ClampedArray(n * 4);
  const [R, G, B] = bands.map(b => scene[b]), k = 1024 / (stretch.hi - stretch.lo), lo = stretch.lo;
  for (let i = 0, o = 0; i < n; i++, o += 4) {
    const r = R[i], g = G[i], b = B[i];
    if (r !== r || g !== g || b !== b) continue;       // nothing here: left transparent
    out[o] = GAMMA[r <= lo ? 0 : Math.min(1024, ((r - lo) * k) | 0)];
    out[o + 1] = GAMMA[g <= lo ? 0 : Math.min(1024, ((g - lo) * k) | 0)];
    out[o + 2] = GAMMA[b <= lo ? 0 : Math.min(1024, ((b - lo) * k) | 0)];
    out[o + 3] = 255;
  }
  return out;
}
const COLOR_BANDS = ["red", "green", "blue"];
const SWIR_BANDS = ["swir22", "nir08", "red"];          // heat and burnt ground stand out, plants are green
const colorImage = (scene, stretch) => composite(scene, COLOR_BANDS, stretch || stretchOf([scene], COLOR_BANDS));
const swirImage = (scene, stretch) => composite(scene, SWIR_BANDS, stretch || stretchOf([scene], SWIR_BANDS));

/* ------------------------------------------------------------------ band combinations and indices */
// The bands a view needs from a scene. «nir» is band 8 where the scene holds it, else band 8A.
function comboBands(combo, scene) {
  const c = COMBOS[combo];
  if (!c) throw new ImageryError("format", "unknown view " + combo);
  return (c.bands || c.index).map(b => b === "nir" && !(scene && scene.nir) ? "nir08" : b);
}
// the names of those bands on the satellite that took the scene, as "B12", "B8A" (for the legend)
function comboNames(combo, scene) {
  const sensor = sensorOf(scene && scene.pass);
  return comboBands(combo, scene).map(b => {
    const real = scene && scene.from && scene.from[b] ? scene.from[b] : b;
    return (sensor.bands[real] || ["?"])[0];
  });
}
// can this satellite show this view at all (every band of it exists there)
function comboAvailable(combo, pass) {
  const c = COMBOS[combo], sensor = sensorOf(pass);
  if (!c) return false;
  return (c.bands || c.index).every(b => sensor.bands[b] && (b === "nir" || !pass || !pass.assets || !!pass.assets[b]));
}
const hasBands = (scene, names) => !!scene && !!scene.scl && names.every(b => !!scene[b]);
// A stronger display stretch (the 2 % and 98 % points instead of 0.5 % and 99.5 %): more contrast
// from the same numbers. It adds no detail; the brightest and darkest few per cent are clipped.
function stretchRange(scenes, bands, qlo = 0.02, qhi = 0.98) {
  const sample = [];
  for (const s of scenes) {
    if (!s) continue;
    const n = s[bands[0]].length, step = Math.max(1, Math.floor(n / 20000));
    for (let i = 0; i < n; i += step) {
      if (cloudy(s.scl[i])) continue;
      for (const b of bands) { const v = s[b][i]; if (v === v) sample.push(v); }
    }
  }
  if (sample.length < 30) return {lo: 0, hi: 0.4};
  let [lo, hi] = quantiles(sample, [qlo, qhi]);
  lo = Math.max(0, Math.min(lo, 0.2));
  if (hi - lo < 0.1) hi = lo + 0.1;
  return {lo, hi};
}
// Sharpen a picture in place (each point pushed away from the mean of its four neighbours). Display
// only: edges that are in the data look crisper, nothing that is not in the data appears.
function sharpen(rgba, n, amount = 0.45) {
  const src = new Uint8ClampedArray(rgba), row = n * 4;
  for (let y = 1; y < n - 1; y++) for (let x = 1; x < n - 1; x++) {
    const o = y * row + x * 4;
    if (!src[o + 3] || !src[o - 4 + 3] || !src[o + 4 + 3] || !src[o - row + 3] || !src[o + row + 3]) continue;
    for (let c = 0; c < 3; c++) {
      const p = src[o + c];
      rgba[o + c] = p + amount * (4 * p - src[o + c - 4] - src[o + c + 4] - src[o + c - row] - src[o + c + row]);
    }
  }
  return rgba;
}
// A ratio of two bands, (a - b) / (a + b), between -1 and 1; nothing where either band has nothing
// or both are too dark to mean anything.
function indexOf(scene, kind, names) {
  const [A, B] = (names || comboBands(kind, scene)).map(b => scene[b]), n = A.length, out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const a = A[i], b = B[i], s = a + b;
    out[i] = s > 0.02 ? (a - b) / s : NaN;              // also NaN where a or b is NaN
  }
  return out;
}
// The colour ramps of the indices: [value, colour] stops, lightest at «nothing to see» and darker to
// both sides (lightness runs one way on each arm, so the order survives colour blindness and print).
const RAMPS = {
  // plant cover: slate below zero (water), pale where there are no plants, darker green with more plants
  ndvi: {min: -0.2, max: 0.9, stops: [[-0.2, "#33415c"], [0, "#7d8597"], [0.1, "#ddd8cc"], [0.25, "#c3dc97"], [0.45, "#7fbf5a"], [0.65, "#2f9140"], [0.9, "#0d5a27"]]},
  // burn ratio: red-brown where it is low (burnt ground), pale around zero (bare ground), teal over healthy plants
  nbr: {min: -0.6, max: 0.8, stops: [[-0.6, "#4a0d12"], [-0.42, "#a3221a"], [-0.27, "#e2572b"], [-0.13, "#f4b76b"], [0, "#e2dfd8"], [0.22, "#a3d6cc"], [0.45, "#4aaf9f"], [0.8, "#0d5f57"]]},
};
const MASK_COLORS = {cloud: [174, 182, 196], water: [34, 48, 74]};
const rgbOf = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const LUTS = {};
function rampTable(kind) {
  if (LUTS[kind]) return LUTS[kind];
  const r = RAMPS[kind], t = new Uint8Array(256 * 3), stops = r.stops.map(([v, c]) => [v, rgbOf(c)]);
  for (let k = 0; k < 256; k++) {
    const v = r.min + (r.max - r.min) * k / 255;
    let j = 0;
    while (j < stops.length - 2 && v > stops[j + 1][0]) j++;
    const [v0, c0] = stops[j], [v1, c1] = stops[j + 1], f = Math.min(1, Math.max(0, (v - v0) / (v1 - v0)));
    for (let c = 0; c < 3; c++) t[k * 3 + c] = Math.round(c0[c] + (c1[c] - c0[c]) * f);
  }
  return LUTS[kind] = t;
}
// the colour of one value of an index, as [r, g, b]
function rampColor(kind, value) {
  const r = RAMPS[kind], t = rampTable(kind), k = Math.round(Math.min(1, Math.max(0, (value - r.min) / (r.max - r.min))) * 255) * 3;
  return [t[k], t[k + 1], t[k + 2]];
}
// An index as a picture. Cloud is drawn flat grey (it hides the ground: its number means nothing);
// for the burn ratio water is drawn flat too (two near-zero bands give a random ratio).
function indexImage(values, kind, scl) {
  const r = RAMPS[kind], t = rampTable(kind), n = values.length, out = new Uint8ClampedArray(n * 4), k = 255 / (r.max - r.min);
  for (let i = 0, o = 0; i < n; i++, o += 4) {
    const c = scl ? scl[i] : 5, v = values[i];
    if (c === 0 || c === 1) continue;                    // nothing here: left transparent
    let rgb = null;
    if (c === 8 || c === 9) rgb = MASK_COLORS.cloud;
    else if (c === 6 && kind === "nbr") rgb = MASK_COLORS.water;
    else if (v !== v) continue;
    if (rgb) { out[o] = rgb[0]; out[o + 1] = rgb[1]; out[o + 2] = rgb[2]; }
    else {
      const j = (v <= r.min ? 0 : v >= r.max ? 255 : ((v - r.min) * k + 0.5) | 0) * 3;
      out[o] = t[j]; out[o + 1] = t[j + 1]; out[o + 2] = t[j + 2];
    }
    out[o + 3] = 255;
  }
  return out;
}
// Any view of a scene as a picture: three bands (with the brightness range given, or the scene's
// own), or an index with its fixed ramp (the same colours on every date, so two dates compare).
function comboImage(scene, combo, stretch) {
  if (COMBOS[combo].index) return indexImage(indexOf(scene, combo), combo, scene.scl);
  const names = comboBands(combo, scene);
  return composite(scene, names, stretch || stretchOf([scene], names));
}

/* ------------------------------------------------------------------ what changed */
const CHANGE_BANDS = ["blue", "green", "red", "nir08", "swir22"];
const CHANGE_MIN = 0.045;       // reflectance: smaller differences are haze, sun angle and sensor noise
// Compare two scenes on the same grid. The later picture is first brought to the earlier one's
// overall brightness (sun height and haze differ between dates), then every pixel's change across
// five bands is measured. A pixel counts as changed when it is clearly above both a fixed floor
// and the scene's own noise, and is not alone. Cloud that is there on one date only is left out.
function changeOf(before, after) {
  const grid = before.grid, n = grid.size, total = n * n;
  if (after.grid.size !== n) throw new ImageryError("format", "different grids");
  // 1. what cannot be compared: no data, or cloud, its shadow or haze on one date only (cloud
  //    "seen" on both dates at the same pixel is bright ground, see refineSky). ESA's classes miss
  //    cloud edges and most shadows, so on a date that has cloud, anything that whitened like cloud
  //    or dimmed like a shadow (all bands down, the infrared most) is left out too, with a margin.
  const veil = new Uint8Array(total), skip = new Uint8Array(total);
  const isCloud = c => c === 8 || c === 9;
  const cloudShare = (s, o) => { let c = 0; for (let i = 0; i < total; i++) if (isCloud(s.scl[i]) && !isCloud(o.scl[i])) c++; return c / total; };
  const cloudsIn = [cloudShare(before, after) > 0.003, cloudShare(after, before) > 0.003];
  const sides = [[before, after, cloudsIn[0]], [after, before, cloudsIn[1]]];
  for (let i = 0; i < total; i++) {
    const a = before.scl[i], b = after.scl[i];
    if (a === 0 || b === 0 || a === 1 || b === 1 || before.red[i] !== before.red[i] || after.red[i] !== after.red[i]) { skip[i] = 1; continue; }
    const ca = a === 8 || a === 9 || (a === 3 && cloudsIn[0]), cb = b === 8 || b === 9 || (b === 3 && cloudsIn[1]);
    if (ca !== cb || (a === 10) !== (b === 10)) { veil[i] = 1; continue; }
    for (const [s, o, clouds] of sides) {
      if (!clouds) continue;
      const bl = s.blue[i], rd = s.red[i], obl = o.blue[i];
      if (bl > 0.2 && bl - obl > 0.05 && bl > rd * 0.72) { veil[i] = 1; break; }                 // whitened
      const rb = bl / Math.max(obl, 0.01), rr = rd / Math.max(o.red[i], 0.01),
        rn = s.nir08[i] / Math.max(o.nir08[i], 0.01), rs = s.swir22[i] / Math.max(o.swir22[i], 0.01);
      // shaded (on land only: water darkens and brightens by itself with tide and wind)
      if (o.nir08[i] > 0.1 && rb < 0.9 && rr < 0.85 && rn < 0.85 && rs < 0.85 && rs <= rb + 0.03 && rn <= rb + 0.03) { veil[i] = 1; break; }
    }
  }
  // the margin goes around real cloud (a solid patch), not around lone flagged roofs
  const wide = dilate(solid(veil, n, Math.max(2, Math.round(60 / grid.res))), n, Math.max(1, Math.round(120 / grid.res)));
  let valid = 0;
  for (let i = 0; i < total; i++) { if (wide[i] || veil[i]) skip[i] = 1; if (!skip[i]) valid++; }
  const mag = new Float32Array(total), dir = new Float32Array(total);
  const out = {grid, mag, dir, mask: new Uint8Array(total), skip, valid, compared: valid / total, clouds: cloudsIn[0] || cloudsIn[1],
    threshold: CHANGE_MIN, changedPx: 0, darkPx: 0, brightPx: 0, areaHa: 0, darkHa: 0, brightHa: 0, share: 0};
  if (valid < 50) return out;
  const step = Math.max(1, Math.floor(total / 30000));
  for (const band of CHANGE_BANDS) {
    const A = before[band], B = after[band], sa = [], sb = [];
    for (let i = 0; i < total; i += step) if (!skip[i]) { sa.push(A[i]); sb.push(B[i]); }
    const ma = quantiles(sa, [0.5])[0], mb = quantiles(sb, [0.5])[0];
    const gain = mb > 0.01 && ma > 0.01 ? Math.min(1.3, Math.max(0.77, ma / mb)) : 1;
    for (let i = 0; i < total; i++) {
      if (skip[i]) continue;
      const d = B[i] * gain - A[i];
      mag[i] += d * d; dir[i] += d;
    }
  }
  const sample = [];
  for (let i = 0; i < total; i++) {
    if (skip[i]) continue;
    mag[i] = Math.sqrt(mag[i] / CHANGE_BANDS.length); dir[i] /= CHANGE_BANDS.length;
    if (i % step === 0) sample.push(mag[i]);
  }
  const [med, q75] = quantiles(sample, [0.5, 0.75]);
  const T = out.threshold = Math.max(CHANGE_MIN, med + 6 * (q75 - med));
  const hot = new Uint8Array(total);
  for (let i = 0; i < total; i++) if (!skip[i] && mag[i] > T) hot[i] = 1;
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const i = y * n + x;
    if (!hot[i]) continue;
    let near = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const xx = x + dx, yy = y + dy;
      if (xx >= 0 && yy >= 0 && xx < n && yy < n && hot[yy * n + xx]) near++;
    }
    if (near < 4) continue;                              // a lone pixel or a thin line: noise or a moving car
    out.mask[i] = 1; out.changedPx++;
    if (dir[i] < 0) out.darkPx++; else out.brightPx++;
  }
  const ha = grid.res * grid.res / 1e4;
  out.areaHa = out.changedPx * ha; out.darkHa = out.darkPx * ha; out.brightHa = out.brightPx * ha;
  out.share = out.changedPx / valid;
  return out;
}
// the set pixels that sit in a well-filled neighbourhood (at least 40 % of the square of radius r)
function solid(mask, n, r) {
  const sum = new Int32Array((n + 1) * (n + 1)), out = new Uint8Array(mask.length);
  for (let y = 0; y < n; y++) for (let x = 0, row = 0; x < n; x++) {
    row += mask[y * n + x];
    sum[(y + 1) * (n + 1) + x + 1] = sum[y * (n + 1) + x + 1] + row;
  }
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    if (!mask[y * n + x]) continue;
    const x0 = Math.max(0, x - r), x1 = Math.min(n, x + r + 1), y0 = Math.max(0, y - r), y1 = Math.min(n, y + r + 1);
    const count = sum[y1 * (n + 1) + x1] - sum[y0 * (n + 1) + x1] - sum[y1 * (n + 1) + x0] + sum[y0 * (n + 1) + x0];
    if (count >= 0.4 * (x1 - x0) * (y1 - y0)) out[y * n + x] = 1;
  }
  return out;
}
// every pixel within `r` pixels (a square) of a set pixel
function dilate(mask, n, r) {
  const tmp = new Uint8Array(mask.length), out = new Uint8Array(mask.length);
  for (let pass = 0; pass < 2; pass++) {
    const src = pass ? tmp : mask, dst = pass ? out : tmp, step = pass ? n : 1;
    for (let line = 0; line < n; line++) {
      const base = pass ? line : line * n;
      let reach = -1;
      for (let k = 0; k < n; k++) if (src[base + k * step]) {
        for (let j = Math.max(reach + 1, k - r), e = Math.min(n - 1, k + r); j <= e; j++) dst[base + j * step] = 1;
        reach = Math.min(n - 1, k + r);
      }
    }
  }
  return out;
}
// The change as a see-through layer: darker than before in warm red, brighter in cyan.
function changeImage(change) {
  const n = change.mask.length, out = new Uint8ClampedArray(n * 4), T = change.threshold;
  for (let i = 0, o = 0; i < n; i++, o += 4) {
    if (!change.mask[i]) continue;
    const strength = Math.min(1, (change.mag[i] - T) / (2 * T));
    if (change.dir[i] < 0) { out[o] = 255; out[o + 1] = 59; out[o + 2] = 48; }
    else { out[o] = 34; out[o + 1] = 211; out[o + 2] = 238; }
    out[o + 3] = 120 + 120 * strength;
  }
  return out;
}

/* ------------------------------------------------------------------ flames at the moment of the picture */
// Something hot enough to glow in the short-wave infrared (a fire, or a gas flare): band 12 far
// above bands 11 and 8A, as nothing that only reflects sunlight is. Returns groups of touching
// pixels, the largest first, each with its centre.
function hotSpots(scene) {
  const grid = scene.grid, n = grid.size, total = n * n;
  const s22 = scene.swir22, s16 = scene.swir16, nir = scene.nir08;
  const flag = new Uint8Array(total);
  for (let i = 0; i < total; i++) {
    const a = s22[i], b = s16[i], c = nir[i];
    if (a !== a || b !== b || c !== c) continue;
    const lowB = Math.max(b, 0.01), lowC = Math.max(c, 0.01);
    if ((a >= 0.15 && a / lowB >= 1.4 && a / lowC >= 1.4) || (b >= 0.5 && b / lowC >= 2 && a >= 0.5)) flag[i] = 1;
  }
  const spots = [], stack = [];
  for (let s = 0; s < total; s++) {
    if (flag[s] !== 1) continue;
    let count = 0, sx = 0, sy = 0, peak = 0;
    stack.push(s); flag[s] = 2;
    while (stack.length) {
      const i = stack.pop(), x = i % n, y = (i - x) / n;
      count++; sx += x; sy += y; if (s22[i] > peak) peak = s22[i];
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const xx = x + dx, yy = y + dy, j = yy * n + xx;
        if (xx >= 0 && yy >= 0 && xx < n && yy < n && flag[j] === 1) { flag[j] = 2; stack.push(j); }
      }
    }
    const x = sx / count + 0.5, y = sy / count + 0.5, [lat, lon] = gridPoint(grid, x, y);
    spots.push({x, y, lat, lon, pixels: count, areaHa: count * grid.res * grid.res / 1e4, peak});
  }
  spots.sort((a, b) => b.pixels - a.pixels);
  return spots;
}

/* ------------------------------------------------------------------ burnt ground between two dates */
// The burn ratio NBR = (B8A - B12) / (B8A + B12) is high over healthy plants and drops where they
// burn. dNBR = NBR before - NBR after, sorted into the usual four steps (after Key and Benson, as
// the USGS uses them): 0.10 low, 0.27 moderate, 0.44 high, 0.66 severe. It is a sign of burnt
// ground, not a proof: harvest, ploughing, drying and flooding lower the ratio as well.
const BURN_STEPS = [0.1, 0.27, 0.44, 0.66];
const BURN_COLORS = ["#ffd750", "#ff8a1f", "#e8302a", "#8a1c55"];      // light to dark: low, moderate, high, severe
const DARK = 0.06;                 // both bands together below this: water or deep shadow, the ratio means nothing
function glowing(s, i) {            // the same test as hotSpots: band 12 far above bands 11 and 8A
  const a = s.swir22[i], b = s.swir16[i], c = s.nir08[i];
  if (a !== a || b !== b || c !== c) return false;
  const lowB = Math.max(b, 0.01), lowC = Math.max(c, 0.01);
  return (a >= 0.15 && a / lowB >= 1.4 && a / lowC >= 1.4) || (b >= 0.5 && b / lowC >= 2 && a >= 0.5);
}
// Compare the burn ratio of two scenes on the same grid. Left out (`skip`): 1 no data, 2 cloud, its
// shadow or haze on either date (everything `changeOf` leaves out, and cloud on both dates too),
// 3 water or too dark, 4 a flame at the moment of either picture (its glow is not reflectance).
// A drift of the whole box between the dates (season, sun height) is taken out, up to 0.08; a pixel
// counts when it is not alone. `change` is the result of changeOf for the same pair, if at hand.
function burnOf(before, after, change) {
  const grid = before.grid, n = grid.size, total = n * n;
  if (after.grid.size !== n) throw new ImageryError("format", "different grids");
  const base = change || changeOf(before, after);
  const skip = new Uint8Array(total), dnbr = new Float32Array(total).fill(NaN), cls = new Uint8Array(total);
  const classPx = [0, 0, 0, 0, 0];
  const out = {grid, dnbr, cls, skip, valid: 0, compared: 0, offset: 0, steps: BURN_STEPS, classPx, classHa: [0, 0, 0, 0, 0],
    burntPx: 0, areaHa: 0, share: 0, clouds: base.clouds};
  const an = before.nir08, as = before.swir22, bn = after.nir08, bs = after.swir22;
  const cloudsIn = [before, after].map(s => { let c = 0; for (let i = 0; i < total; i++) if (s.scl[i] === 8 || s.scl[i] === 9) c++; return c > total * 0.003; });
  let glow = new Uint8Array(total), anyGlow = false;
  if (before.swir16 && after.swir16) {
    for (let i = 0; i < total; i++) if (glowing(before, i) || glowing(after, i)) { glow[i] = 1; anyGlow = true; }
    if (anyGlow) glow = dilate(glow, n, Math.max(1, Math.round(30 / grid.res)));
  }
  const hidden = c => c === 8 || c === 9 || c === 10;
  let valid = 0;
  for (let i = 0; i < total; i++) {
    const a = before.scl[i], b = after.scl[i], s0 = an[i] + as[i], s1 = bn[i] + bs[i];
    if (a === 0 || a === 1 || b === 0 || b === 1 || s0 !== s0 || s1 !== s1) skip[i] = 1;
    else if (base.skip[i] || hidden(a) || hidden(b) || (a === 3 && cloudsIn[0]) || (b === 3 && cloudsIn[1])) skip[i] = 2;
    else if (a === 6 || b === 6 || s0 < DARK || s1 < DARK) skip[i] = 3;
    else if (glow[i]) skip[i] = 4;
    else { dnbr[i] = (an[i] - as[i]) / s0 - (bn[i] - bs[i]) / s1; valid++; }
  }
  out.valid = valid; out.compared = valid / total;
  if (valid < 50) return out;
  const step = Math.max(1, Math.floor(total / 30000)), sample = [];
  for (let i = 0; i < total; i += step) if (!skip[i]) sample.push(dnbr[i]);
  const drift = sample.length ? quantiles(sample, [0.5])[0] : 0;
  const offset = out.offset = Math.min(0.08, Math.max(-0.08, drift));
  const raw = new Uint8Array(total), [s1, s2, s3, s4] = BURN_STEPS;
  for (let i = 0; i < total; i++) {
    if (skip[i]) continue;
    const v = dnbr[i] -= offset;
    raw[i] = v >= s4 ? 4 : v >= s3 ? 3 : v >= s2 ? 2 : v >= s1 ? 1 : 0;
  }
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const i = y * n + x;
    if (!raw[i]) continue;
    let near = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const xx = x + dx, yy = y + dy;
      if (xx >= 0 && yy >= 0 && xx < n && yy < n && raw[yy * n + xx]) near++;
    }
    if (near < 4) continue;                              // a lone pixel or a thin line: noise
    cls[i] = raw[i]; classPx[raw[i]]++; out.burntPx++;
  }
  const ha = grid.res * grid.res / 1e4;
  out.classHa = classPx.map(px => px * ha);
  out.areaHa = out.burntPx * ha; out.share = out.burntPx / valid;
  return out;
}
// The burn classes as a see-through layer; where cloud or its shadow left nothing to compare, thin
// diagonal lines say so.
function burnImage(burn) {
  const n = burn.grid.size, out = new Uint8ClampedArray(n * n * 4), colors = BURN_COLORS.map(rgbOf);
  for (let y = 0, i = 0, o = 0; y < n; y++) for (let x = 0; x < n; x++, i++, o += 4) {
    const c = burn.cls[i];
    if (c) { const rgb = colors[c - 1]; out[o] = rgb[0]; out[o + 1] = rgb[1]; out[o + 2] = rgb[2]; out[o + 3] = 225; }
    else if (burn.skip[i] === 2 && (x + y) % 6 === 0) { out[o] = 205; out[o + 1] = 212; out[o + 2] = 224; out[o + 3] = 120; }
  }
  return out;
}

/* ------------------------------------------------------------------ the numbers of one point */
// What a scene holds at grid pixel (x, y): the reflectance of every band it has, the two indices
// and the scene class. Null outside the picture.
function inspect(scene, x, y) {
  const n = scene.grid.size, px = Math.floor(x), py = Math.floor(y);
  if (!(px >= 0 && py >= 0 && px < n && py < n)) return null;
  const i = py * n + px, [lat, lon] = gridPoint(scene.grid, px + 0.5, py + 0.5), bands = {};
  for (const name of ["blue", "green", "red", "nir", "nir08", "swir16", "swir22"]) {
    if (!scene[name] || (name === "nir" && scene.from && scene.from.nir !== "nir")) continue;      // band 8A standing in is listed once
    const v = scene[name][i];
    bands[name] = v === v ? v : null;
  }
  const ratio = (a, b) => a != null && b != null && a + b > 0.02 ? (a - b) / (a + b) : null;
  return {x: px, y: py, lat, lon, bands, scl: scene.scl ? scene.scl[i] : null,
    ndvi: ratio(bands.nir ?? bands.nir08, bands.red), nbr: ratio(bands.nir08, bands.swir22)};
}

/* ------------------------------------------------------------------ the comparison as one job */
// Everything that compares two dates, in one call with plain arrays in and out, so the viewer can
// hand it to a background worker and the screen stays smooth. m: {size, res, before, after}, each
// side holding its bands and scene classes. The results come back without their grid.
function pairJob(m) {
  const grid = {size: m.size, res: m.res}, before = {...m.before, grid}, after = {...m.after, grid};
  const change = changeOf(before, after), burn = burnOf(before, after, change);
  delete change.grid; delete burn.grid;
  return {change, burn};
}
const pairBuffers = r => [r.change.mag, r.change.dir, r.change.mask, r.change.skip, r.burn.dnbr, r.burn.cls, r.burn.skip].map(a => a.buffer);
// the worker's side of the job (the viewer builds the worker from `source`, see the top of the file)
function serve(scope) {
  scope.onmessage = e => {
    const m = e.data;
    try { const result = pairJob(m); scope.postMessage({id: m.id, result}, pairBuffers(result)); }
    catch (err) { scope.postMessage({id: m.id, error: String(err && err.message || err)}); }
  };
}

return {VERSION, ImageryError, UTM, projectionOf, makeGrid, gridPoint, gridPixel, gridUTM, inflate, decompress,
  createEngine, skyOf, refineSky, isClear, grade, pickPair, stretchOf, colorImage, swirImage, changeOf, changeImage,
  hotSpots, qaClasses, COLOR_BANDS, SWIR_BANDS, CLEAR, quantiles,
  SENSORS, COMBOS, RAMPS, MASK_COLORS, BURN_STEPS, BURN_COLORS, REFLECTANCE, sensorOf, comboBands, comboNames, comboAvailable,
  hasBands, stretchRange, sharpen, bandsImage: composite, indexOf, indexImage, rampColor, comboImage, burnOf, burnImage, inspect,
  pairJob, pairBuffers, serve};
});
