"use strict";
// A made-up Sentinel-2 archive for the tests: a few passes over one place, served by a fake
// `fetch`: the index answers as JSON and the pictures are real Cloud-Optimized GeoTIFFs (tiled,
// zlib-compressed, with the row predictor and an overview), read by range like the real ones.
const zlib = require("node:zlib");
const I = require("../../web/imagery.js");

const FILES = "https://e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com/sentinel-2-c1-l2a/";
const OLD_FILES = "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/";
const SEARCH = "https://earth-search.aws.element84.com/v1";
const BANDS = {B02: "blue", B03: "green", B04: "red", B8A: "nir08", B11: "swir16", B12: "swir22", SCL: "scl"};
const BASE = {blue: 0.20, green: 0.25, red: 0.30, nir08: 0.35, swir16: 0.40, swir22: 0.38};

// A tiled TIFF. `value(x, y)` gives the number of pixel (x, y) of the full picture; the overviews
// take every second, fourth... pixel. Georeferenced with its top-left corner at (x0, y0).
function makeCog({size, tile, res, x0, y0, bits = 16, value, levels = 2}) {
  const bytesPer = bits / 8, plans = [];
  for (let k = 0; k < levels; k++) {
    const f = 1 << k, w = Math.ceil(size / f), across = Math.ceil(w / tile), blocks = [];
    for (let ty = 0; ty < across; ty++) for (let tx = 0; tx < across; tx++) {
      const data = bits === 16 ? new Uint16Array(tile * tile) : new Uint8Array(tile * tile);
      let any = false;
      for (let y = 0; y < tile; y++) for (let x = 0; x < tile; x++) {
        const px = tx * tile + x, py = ty * tile + y;
        if (px < w && py < w) { const v = value(px * f, py * f); data[y * tile + x] = v; if (v) any = true; }
      }
      for (let y = 0; y < tile; y++) for (let x = tile - 1; x > 0; x--) data[y * tile + x] -= data[y * tile + x - 1];   // predictor 2
      blocks.push(any ? zlib.deflateSync(Buffer.from(data.buffer, 0, tile * tile * bytesPer)) : Buffer.alloc(0));     // empty tiles are left out
    }
    plans.push({w, blocks});
  }
  const entries = k => 15 + (k === 0 ? 1 : 0);
  const dirSize = k => 2 + entries(k) * 12 + 4 + (plans[k].blocks.length > 1 ? plans[k].blocks.length * 8 : 0) + (k === 0 ? 72 : 0);
  let pos = 8;
  const dirAt = plans.map((p, k) => { const at = pos; pos += dirSize(k); return at; });
  const tileAt = plans.map(p => p.blocks.map(b => { const at = pos; pos += b.length; return at; }));
  const out = Buffer.alloc(pos);
  out.write("II"); out.writeUInt16LE(42, 2); out.writeUInt32LE(dirAt[0], 4);
  plans.forEach((p, k) => {
    const n = entries(k), count = p.blocks.length;
    let at = dirAt[k], extra = at + 2 + n * 12 + 4;
    out.writeUInt16LE(n, at); at += 2;
    const entry = (tag, type, cnt, val) => { out.writeUInt16LE(tag, at); out.writeUInt16LE(type, at + 2); out.writeUInt32LE(cnt, at + 4);
      if (type === 3 && cnt === 1) out.writeUInt16LE(val, at + 8); else out.writeUInt32LE(val, at + 8); at += 12; };
    const longs = (tag, values) => {
      if (count === 1) return entry(tag, 4, 1, values[0]);
      entry(tag, 4, count, extra);
      values.forEach(v => { out.writeUInt32LE(v, extra); extra += 4; });
    };
    entry(254, 4, 1, k ? 1 : 0); entry(256, 4, 1, p.w); entry(257, 4, 1, p.w); entry(258, 3, 1, bits); entry(259, 3, 1, 8);
    entry(262, 3, 1, 1); entry(277, 3, 1, 1); entry(284, 3, 1, 1); entry(317, 3, 1, 2); entry(322, 3, 1, tile); entry(323, 3, 1, tile);
    longs(324, p.blocks.map((b, i) => b.length ? tileAt[k][i] : 0)); longs(325, p.blocks.map(b => b.length));
    entry(339, 3, 1, 1);
    entry(k === 0 ? 33550 : 42113, k === 0 ? 12 : 2, k === 0 ? 3 : 1, k === 0 ? extra : 48);       // overviews: a harmless NoData tag
    if (k === 0) {
      [res, res, 0].forEach(v => { out.writeDoubleLE(v, extra); extra += 8; });
      entry(33922, 12, 6, extra);
      [0, 0, 0, x0, y0, 0].forEach(v => { out.writeDoubleLE(v, extra); extra += 8; });
    }
    out.writeUInt32LE(k + 1 < plans.length ? dirAt[k + 1] : 0, at);
    p.blocks.forEach((b, i) => b.copy(out, tileAt[k][i]));
  });
  return out;
}

// spec: {lat, lon, passes: [{date, platform, tile, collection, cloud: "all"|"west", burn, flame,
//        footprint: "west" (the picture stops in the middle of the box), offset, scale}]}
function makeArchive(spec) {
  const zone = Math.floor((spec.lon + 180) / 6) + 1, [Ec, Nc] = I.UTM.forward(spec.lat, spec.lon, zone);
  const x0 = Math.round((Ec - 2560) / 20) * 20, y0 = Math.round((Nc + 2560) / 20) * 20;
  const passes = spec.passes.map((p, i) => {
    const tile = p.tile || "39RXX", sat = p.platform || "sentinel-2b", collection = p.collection || "sentinel-2-c1-l2a";
    const id = p.id || `${sat.slice(-2).toUpperCase().replace("2", "S2")}_T${tile}_${p.date.replace(/-/g, "")}T072146_L2A`;
    const dir = collection === "sentinel-2-c1-l2a"
      ? `${FILES}${+tile.slice(0, 2)}/${tile[2]}/${tile.slice(3)}/${p.date.slice(0, 4)}/${+p.date.slice(5, 7)}/${id}/`
      : `${OLD_FILES}${id}/`;
    return {...p, tile, platform: sat, collection, id, dir: p.dir || dir, datetime: p.date + "T07:22:3" + (i % 10) + ".000000Z",
      scale: p.scale ?? 1e-4, offset: p.offset ?? (collection === "sentinel-2-c1-l2a" ? -0.1 : 0)};
  });
  // what the ground looks like at (E, N) for a pass and a band
  function ground(p, band, E, N) {
    const west = E < Ec;
    if (p.footprint === "west" && !west) return null;
    if (band === "scl") return p.cloud === "all" || (p.cloud === "west" && west) ? 9 : 5;
    if (p.cloud === "all" || (p.cloud === "west" && west)) return 0.8;
    let v = BASE[band] + 2e-5 * (E - Ec) + 1e-5 * (N - Nc);
    if (p.burn && Math.abs(E - Ec - 300) < 50 && Math.abs(N - Nc - 200) < 50) v *= 0.4;
    if (p.flame && Math.abs(E - Ec + 400) < 20 && Math.abs(N - Nc + 400) < 20) v = band === "swir22" ? 1.2 : band === "swir16" ? 0.6 : 0.3;
    return v;
  }
  const files = new Map();
  function fileFor(url) {
    if (files.has(url)) return files.get(url);
    const p = passes.find(q => url.startsWith(q.dir)), band = p && BANDS[url.slice(p.dir.length).replace(".tif", "")];
    let buf = null;
    if (p && band && !(p.missing || []).includes(band)) {
      const res = ["blue", "green", "red"].includes(band) ? 10 : 20;
      buf = makeCog({size: 5120 / res, tile: 256, res, x0, y0, bits: band === "scl" ? 8 : 16, value: (x, y) => {
        const v = ground(p, band, x0 + (x + 0.5) * res, y0 - (y + 0.5) * res);
        return v == null ? 0 : band === "scl" ? v : Math.round((v - p.offset) / p.scale);
      }});
    }
    files.set(url, buf);
    return buf;
  }
  const ring = (w, e) => {            // the footprint of a picture in lon/lat
    const c = [[w, 2560], [e, 2560], [e, -2560], [w, -2560], [w, 2560]].map(([dx, dy]) => I.UTM.inverse(Ec + dx, Nc + dy, zone));
    return {type: "Polygon", coordinates: [c.map(([la, lo]) => [lo, la])]};
  };
  const feature = p => ({id: p.id, collection: p.collection, geometry: ring(-2560, p.footprint === "west" ? 0 : 2560),
    properties: {datetime: p.datetime, platform: p.platform, "eo:cloud_cover": p.cloud ? 80 : 1, "grid:code": "MGRS-" + p.tile,
      [p.collection === "sentinel-2-c1-l2a" ? "proj:code" : "proj:epsg"]: p.collection === "sentinel-2-c1-l2a" ? "EPSG:326" + zone : 32600 + zone}});
  const archive = {passes, calls: [], fail: null, pageSize: 0, Ec, Nc, x0, y0, zone, ground};
  const answer = (status, body, headers = {}) => ({ok: status >= 200 && status < 300, status, headers: {get: k => headers[k.toLowerCase()] ?? null},
    body: {cancel() {}}, arrayBuffer: async () => body.buffer.slice(body.byteOffset, body.byteOffset + body.length), text: async () => body.toString("utf8")});
  archive.fetch = (url, init = {}) => new Promise((resolve, reject) => {
    const range = init.headers && init.headers.Range;
    archive.calls.push({url, range, body: init.body});
    const aborted = () => reject(Object.assign(new Error("aborted"), {name: "AbortError"}));
    if (init.signal) { if (init.signal.aborted) return aborted(); init.signal.addEventListener("abort", aborted, {once: true}); }
    const how = archive.fail && archive.fail(url);
    if (how === "hang") return;
    if (how === "down") return reject(new TypeError("fetch failed"));
    if (typeof how === "number") return resolve(answer(how, Buffer.from("no")));
    if (url === SEARCH + "/search") {
      const q = JSON.parse(init.body), [from, to] = q.datetime.split("/").map(Date.parse);
      const all = passes.filter(p => q.collections.includes(p.collection) && Date.parse(p.datetime) >= from && Date.parse(p.datetime) <= to);
      const start = q.next || 0, size = archive.pageSize || all.length || 1;
      const links = start + size < all.length ? [{rel: "next", method: "POST", merge: true, body: {next: start + size}}] : [];
      return resolve(answer(200, Buffer.from(JSON.stringify({type: "FeatureCollection", features: all.slice(start, start + size).map(feature), links}))));
    }
    const item = /\/collections\/([^/]+)\/items\/(.+)$/.exec(url);
    if (item) {
      const p = passes.find(q => q.id === item[2] && q.collection === item[1]);
      if (!p) return resolve(answer(404, Buffer.from("no")));
      const assets = {};
      for (const [file, name] of Object.entries(BANDS)) assets[name] = {href: (p.realDir || p.dir) + file + ".tif",
        "raster:bands": name === "scl" ? [{nodata: 0}] : [{scale: p.scale, offset: p.offset, nodata: 0}]};
      return resolve(answer(200, Buffer.from(JSON.stringify({...feature(p), assets}))));
    }
    const moved = passes.find(p => p.realDir && url.startsWith(p.realDir));
    const buf = fileFor(moved ? url.replace(moved.realDir, moved.dir) : url);
    if (!buf || (passes.some(p => p.realDir && url.startsWith(p.dir)))) return resolve(answer(404, Buffer.from("no")));
    if (how === "norange" || !range) return resolve(answer(200, buf));
    const [a, b] = range.replace("bytes=", "").split("-").map(Number);
    resolve(answer(206, buf.subarray(a, Math.min(buf.length, b + 1))));
  });
  return archive;
}

module.exports = {makeCog, makeArchive, SEARCH, FILES, BASE};
