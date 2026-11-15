// ALFAQAAR FIRMS — the private door of the Rasad app (a Cloudflare Worker).
//
// Nothing with data leaves here without a valid, active subscription:
//   /api/nonce, /api/login   a subscriber signs in with the access key the manager made for them. The
//                            key is tied to the device that used it first: the browser holds a key
//                            pair it cannot export, and signs a fresh number from here to show it
//   /api/prove               every 15 minutes the device signs again and gets a new cookie, so a
//                            cookie copied to another device stops working
//   /api/me, /api/data       who is signed in; the incidents
//   /api/archive[/YYYY-MM]   past incidents, one file per month
//   /clips/<file>            the satellite clips and stills
//   /api/admin/...           the manager's panel: subscribers, keys, devices, who has the app open, the log
//   /api/ingest/...          the monitor on GitHub pushes fresh data and clips here (secret key)
//   /tg/<secret>             Telegram tells us the manager's chat, so sign-ins reach the manager
//   any other path           the app's files: the page and the sign-in script are public (they hold no
//                            data), every other file needs a session too
// The manager is told on Telegram who opened the app, from which city, and how many have it open.
// Design, limits and the API table: docs/plans/access-keys.md

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
                       "x-content-type-options": "nosniff" };
const CHUNK = 1_900_000;                 // Durable Object rows hold at most 2 MB
const VISIT_GAP = 20 * 60e3;             // a new "opened the app" after 20 minutes away
const ONLINE_FOR = 150e3;                // counted as having the app open for 2.5 minutes after a refresh
const LOG_DAYS = 45;
const PROOF_FOR = 15 * 60e3;             // a session works for 15 minutes after its device last proved itself
const GRACE = 60e3;                      // after a proof the old cookie still works for a minute (requests on their way)
const NONCE_FOR = 2 * 60e3;              // a number to sign is good once, for two minutes
const NONCES_A_MINUTE = 20;              // numbers handed to one network in a minute
const FAIL_MAX = 5, FAIL_FOR = 15 * 60e3;              // wrong keys from one network: five, then a 15-minute wait
const PENDING_MAX = 3, PENDING_FOR = 7 * 86400e3;      // devices waiting for the manager: the newest three, for a week
const REFUSED_FOR = 86400e3;             // a device the manager refused cannot ask again for a day
const COOKIE_DAYS = 400;                 // browsers keep a cookie 400 days at most; every proof renews it
const KEY_ABC = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";    // no 0/O and no 1/I: a key can be read out over the phone
const MANAGER = "admin";

// ------------------------------------------------------------------ the app's files
// Served to anyone: the page itself, the sign-in script and what a browser needs to install the app.
export const PUBLIC_FILES = ["/", "/index.html", "/access.js", "/sw.js", "/manifest.webmanifest", "/favicon.ico"];
export function isPublicPath(path) {
  if (PUBLIC_FILES.includes(path)) return true;
  // plain names only (nothing encoded, no ".."), so a public folder can never lead to another file
  if (!/^\/[A-Za-z0-9._\/-]+$/.test(path) || path.includes("..") || path.includes("//")) return false;
  return path.startsWith("/icons/") || /^\/brand-[^/]+$/.test(path);
}
const SHIELD = { "x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "x-frame-options": "DENY",
                 "strict-transport-security": "max-age=31536000" };
function shielded(res, personal) {       // the same file, with the browser's safety switches on
  const out = new Response(res.body, res);
  for (const [k, v] of Object.entries(SHIELD)) out.headers.set(k, v);
  if (personal) out.headers.set("cache-control", "private, no-cache");
  return out;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;
    if (p === "/api/ping") return new Response("ok", { headers: { "cache-control": "no-store" } });
    const hub = env.HUB.get(env.HUB.idFromName("main"));
    if (p.startsWith("/api/") || p.startsWith("/clips/") || p.startsWith("/tg/")) {
      const cf = request.cf || {};
      const headers = new Headers(request.headers);
      headers.set("x-geo", JSON.stringify({ city: cf.city || "", region: cf.region || "", country: cf.country || "" }));
      return hub.fetch(new Request(request, { headers }));
    }
    if (isPublicPath(p)) return shielded(await env.ASSETS.fetch(request), false);
    // every other file of the app (its tools) is for a signed-in subscriber: the store decides.
    // (An active subscription on its own device is enough here, without the 15-minute proof the
    // data needs: the files hold no data, and a page cannot sign anything before it has loaded.)
    const cookie = request.headers.get("cookie") || "";
    let why = "login";
    if (/(?:^|;\s*)fs=[0-9a-f]{64}(?:;|$)/.test(cookie)) {
      const check = await hub.fetch(new Request(new URL("/api/check", url), { headers: { cookie } }));
      if (check.status === 200) return shielded(await env.ASSETS.fetch(request), true);
      why = ((await check.json().catch(() => null)) || {}).error || "login";
    }
    return json({ error: why }, 401);
  },
};

// ------------------------------------------------------------------ helpers
const enc = new TextEncoder();
const json = (obj, status = 200, extra = {}) => new Response(JSON.stringify(obj), { status, headers: { ...JSON_HEADERS, ...extra } });
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
const sha = async text => hex(await crypto.subtle.digest("SHA-256", enc.encode(text)));
const randHex = n => hex(crypto.getRandomValues(new Uint8Array(n)));
const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

function sameText(a, b) {                // constant-time comparison of two strings
  const x = enc.encode(String(a)), y = enc.encode(String(b));
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] || 0) ^ (y[i] || 0);
  return diff === 0;
}
function cookieValue(req, name) {
  const m = (req.headers.get("cookie") || "").match(new RegExp("(?:^|;\\s*)" + name + "=([^;]+)"));
  return m ? m[1] : null;
}
function geoOf(req) {
  let g = {};
  try { g = JSON.parse(req.headers.get("x-geo") || "{}"); } catch {}
  return { city: g.city || "", region: g.region || "", country: g.country || "", ip: req.headers.get("cf-connecting-ip") || "" };
}
function netOf(ip) {                     // IPv4 as it is; IPv6 by its /64, because one line or phone owns a whole /64
  ip = String(ip || "").toLowerCase();
  if (!ip.includes(":")) return ip || "unknown";
  const [head, tail] = ip.split("::");
  const a = head ? head.split(":") : [], b = tail ? tail.split(":") : [];
  const groups = tail === undefined ? a : [...a, ...Array(Math.max(0, 8 - a.length - b.length)).fill("0"), ...b];
  return groups.slice(0, 4).map(g => g.replace(/^0+(?=.)/, "")).join(":") + "::/64";
}
async function bodyOf(req) {             // a small JSON object, or {} for anything else
  if (Number(req.headers.get("content-length") || 0) > 20000) return {};
  try {
    const text = await req.text();
    const v = text.length <= 20000 ? JSON.parse(text) : null;
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch { return {}; }
}
const COUNTRY_AR = { IQ: "العراق", IR: "إيران", SA: "السعودية", AE: "الإمارات", KW: "الكويت", QA: "قطر", BH: "البحرين",
  OM: "عُمان", YE: "اليمن", JO: "الأردن", SY: "سوريا", LB: "لبنان", TR: "تركيا", EG: "مصر", DE: "ألمانيا",
  GB: "بريطانيا", US: "أمريكا", NL: "هولندا", FR: "فرنسا", SE: "السويد" };
const place = g => [g.city, COUNTRY_AR[g.country] || g.country].filter(Boolean).join("، ") || "مكان غير معروف";
function deviceKind(ua) {
  ua = ua || "";
  const os = /iPhone/.test(ua) ? "آيفون" : /iPad/.test(ua) ? "آيباد" : /Android/.test(ua) ? "أندرويد"
    : /Windows/.test(ua) ? "ويندوز" : /Mac OS X/.test(ua) ? "ماك" : "جهاز";
  const br = /CriOS|Chrome/.test(ua) && !/Edg/.test(ua) ? "Chrome" : /Edg/.test(ua) ? "Edge" : /Firefox|FxiOS/.test(ua) ? "Firefox"
    : /Telegram/.test(ua) ? "Telegram" : /Safari/.test(ua) ? "Safari" : "متصفح";
  return `${os} · ${br}`;
}
function labelOf(given, req) {           // how a device is shown to the manager: the app's own words, else the browser's name
  const s = typeof given === "string"
    ? given.replace(/[<>&"'`\\\p{Cc}\p{Cf}]/gu, "").replace(/\s+/g, " ").trim().slice(0, 40) : "";
  return s || deviceKind(req.headers.get("user-agent"));
}
const tidyText = (s, max) => (typeof s === "string" ? s : "").replace(/\p{Cc}/gu, " ").replace(/\s+/g, " ").trim().slice(0, max);
// what was typed in the key field, without the invisible direction marks that copy and paste adds
const typedText = s => (typeof s === "string" ? s.slice(0, 200) : "").replace(/\p{Cf}/gu, "").trim();
function sessionCookie(token, maxAge = COOKIE_DAYS * 86400) {
  return `fs=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`;
}

// ---------------- access keys: RSD-XXXXX-XXXXX-XXXXX-XXXXX
function newKey() {                      // 20 symbols of 5 bits each: 100 bits (256 is a multiple of 32, so no symbol is favoured)
  const b = crypto.getRandomValues(new Uint8Array(20));
  return "RSD-" + [...b].map(x => KEY_ABC[x & 31]).join("").match(/.{5}/g).join("-");
}
export function normalizeKey(text) {     // however it was typed or pasted -> "RSD-…" exactly as issued; "" when it is not a key
  let s = String(text ?? "").toUpperCase()
    .replace(/[٠-٩]/g, d => String(d.charCodeAt(0) - 0x660))
    .replace(/[۰-۹]/g, d => String(d.charCodeAt(0) - 0x6f0))
    .replace(/[^A-Z0-9]/g, "");
  if (s.length === 23 && s.startsWith("RSD")) s = s.slice(3);
  if (s.length !== 20 || [...s].some(c => !KEY_ABC.includes(c))) return "";
  return "RSD-" + s.match(/.{5}/g).join("-");
}

// ---------------- a device is a key pair the browser cannot export; here we only ever see its public half
function bytesOf(b64u) {                 // base64url text -> bytes (null when it is anything else)
  if (typeof b64u !== "string" || !/^[A-Za-z0-9_-]+$/.test(b64u)) return null;
  try {
    const bin = atob(b64u.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64u.length % 4)) % 4));
    return Uint8Array.from(bin, c => c.charCodeAt(0));
  } catch { return null; }
}
const b64uOf = bytes => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
function publicKeyOf(pub) {              // a P-256 public key as its four members in the canonical order, or null
  if (!pub || typeof pub !== "object" || pub.kty !== "EC" || pub.crv !== "P-256") return null;
  const x = bytesOf(pub.x), y = bytesOf(pub.y);
  return x && y && x.length === 32 && y.length === 32 ? { crv: "P-256", kty: "EC", x: b64uOf(x), y: b64uOf(y) } : null;
}
const deviceIdOf = pub => sha(JSON.stringify(pub));     // the device's name here: the fingerprint of its public key
async function signedBy(pub, text, sig) {               // ECDSA P-256 / SHA-256, the signature as raw r||s in base64url
  const raw = bytesOf(sig);
  if (!raw || raw.length !== 64) return false;
  try {
    const key = await crypto.subtle.importKey("jwk", pub, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, raw, enc.encode(text));
  } catch { return false; }
}

// ---------------- a subscriber's state
const isExpired = u => u.expires != null && Hub.now() >= u.expires;
const statusOf = u => u.disabled ? "disabled" : isExpired(u) ? "expired" : u.devices.length ? "active" : "waiting";
const meOf = u => ({ id: u.id, name: u.name, role: u.id === MANAGER ? "admin" : "subscriber", expires: u.expires ?? null });
const devicesMax = v => Math.min(3, Math.max(1, Math.round(Number(v)) || 1));
const validTime = v => typeof v === "number" && Number.isFinite(v) && v > 1e12;     // milliseconds, not seconds
const validDays = v => typeof v === "number" && Number.isFinite(v) && v !== 0 && Math.abs(v) <= 3660;

// ------------------------------------------------------------------ the store (one Durable Object)
export class Hub {
  static now = () => Date.now();         // the tests move this clock

  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.store = ctx.storage;
    this.online = new Map();             // subscriber id -> {t, since, city, country, name, role}
    this.asked = new Map();              // network -> {n, t}: numbers to sign handed out in the last minute
    this.swept = 0;
    this.tidied = 0;
    this.logged = 0;
  }

  async fetch(req) {
    const url = new URL(req.url), p = url.pathname, m = req.method;
    try {
      if (p.startsWith("/tg/")) return await this.telegramUpdate(req, p);
      if (p.startsWith("/api/ingest/")) return await this.ingest(req, p, m);
      // the cookie is SameSite=Strict already; this also refuses a form posted from another site
      const from = req.headers.get("origin");
      if (m === "POST" && from && from !== url.origin) return json({ error: "forbidden" }, 403);
      if (p === "/api/nonce" && m === "POST") return await this.nonce(req);
      if (p === "/api/login" && m === "POST") return await this.login(req);
      if (p === "/api/logout" && m === "POST") return await this.logout(req);
      if (p === "/api/prove" && m === "POST") return await this.prove(req);
      if (p === "/api/check") {            // the Worker asks before it serves one of the app's files (no proof needed)
        const file = await this.session(req, true);
        return file.error ? json({ error: file.error }, 401) : json({ ok: true });
      }
      const s = await this.session(req);
      if (s.error) return await this.deny(s.error, p.startsWith("/api/"));
      const res = await this.serve(req, p, m, s);
      res.headers.set("x-proof-left", String(Math.floor(s.left / 1000)));    // the app proves again before this runs out
      return res;
    } catch (e) {
      // the monitor is told what went wrong (it holds the ingest key); nobody else is
      const detail = p.startsWith("/api/ingest/") ? { detail: String((e && e.message) || e) } : {};
      return json({ error: "server", ...detail }, 500);
    }
  }

  async serve(req, p, m, s) {            // everything behind a valid, proven session
    if (p === "/api/me") return json({ ...meOf(s.who), server_time: Hub.now() });
    if (p === "/api/data") { await this.seen(s, req); return await this.data(); }
    if (p === "/api/archive" && m === "GET") return json({ months: await this.archiveMonths() });
    if (p.startsWith("/api/archive/") && m === "GET") return await this.archive(p.slice("/api/archive/".length));
    if (p.startsWith("/clips/") && (m === "GET" || m === "HEAD")) {
      let name = "";
      try { name = decodeURIComponent(p.slice(7)); } catch {}
      return await this.clip(req, name);
    }
    if (p.startsWith("/api/admin/")) {
      if (s.row.role !== "admin") return json({ error: "forbidden" }, 403);
      return await this.admin(req, p, m);
    }
    return json({ error: "not found" }, 404);
  }

  // ---------------- big values in pieces (a row holds at most 2 MB)
  async putBig(key, bytes, type) {
    const old = await this.store.get(key + "#meta");
    const n = Math.ceil(bytes.byteLength / CHUNK) || 1;
    const parts = {};
    for (let i = 0; i < n; i++) parts[`${key}#${i}`] = bytes.slice(i * CHUNK, (i + 1) * CHUNK);
    await this.store.put(parts);
    const meta = { n, size: bytes.byteLength, type, t: Hub.now() };
    await this.store.put(key + "#meta", meta);
    if (old && old.n > n) await this.store.delete([...Array(old.n - n)].map((_, i) => `${key}#${n + i}`));
    return meta;
  }
  async getBig(key) {
    const meta = await this.store.get(key + "#meta");
    if (!meta) return null;
    const parts = await this.store.get([...Array(meta.n)].map((_, i) => `${key}#${i}`));
    const out = new Uint8Array(meta.size);
    let at = 0;
    for (let i = 0; i < meta.n; i++) {
      const part = new Uint8Array(parts.get(`${key}#${i}`) || new ArrayBuffer(0));
      out.set(part, at);
      at += part.byteLength;
    }
    return { meta, bytes: out };
  }
  async dropBig(key) {
    const meta = await this.store.get(key + "#meta");
    if (!meta) return;
    await this.store.delete([key + "#meta", ...[...Array(meta.n)].map((_, i) => `${key}#${i}`)]);
  }
  async dropKeys(keys) {                 // one call removes at most 128 rows
    for (let i = 0; i < keys.length; i += 128) await this.store.delete(keys.slice(i, i + 128));
  }

  // ---------------- numbers to sign (each is good once, for two minutes)
  async nonce(req) {
    const t = Hub.now(), net = netOf(geoOf(req).ip), a = this.asked.get(net);
    const first = !a || t - a.t >= 60e3;
    if (this.asked.size > 5000) this.asked.clear();
    this.asked.set(net, first ? { n: 1, t } : { n: a.n + 1, t: a.t });
    if (!first && a.n >= NONCES_A_MINUTE) return json({ error: "wait" }, 429);
    return json({ nonce: await this.newNonce() });
  }
  async newNonce() {
    const n = randHex(16);
    await this.store.put("nonce:" + n, Hub.now());
    if (Hub.now() - this.swept > 5 * 60e3) await this.sweep();
    return n;
  }
  async useNonce(n) {                    // gone at the first use, whether the signature that came with it is good or not
    if (typeof n !== "string" || !/^[0-9a-f]{32}$/.test(n)) return false;
    const t = await this.store.get("nonce:" + n);
    if (t === undefined) return false;
    await this.store.delete("nonce:" + n);
    return Hub.now() - t <= NONCE_FOR;
  }
  async sweep() {                        // numbers nobody signed in time, and counts of wrong keys that have run out
    const t = this.swept = Hub.now(), gone = [];
    for (const [k, at] of await this.store.list({ prefix: "nonce:", limit: 1000 })) if (t - at > NONCE_FOR) gone.push(k);
    for (const [k, f] of await this.store.list({ prefix: "fail:", limit: 1000 })) if (t - f.t > FAIL_FOR) gone.push(k);
    await this.dropKeys(gone);
  }
  async fails(net) {                     // wrong keys from this network in the last quarter of an hour
    const f = await this.store.get("fail:" + net);
    return f && Hub.now() - f.t < FAIL_FOR ? f : { n: 0, t: 0 };
  }

  // ---------------- subscribers, the manager, sessions
  async manager() {
    return (await this.store.get("cfg:manager"))
      || { id: MANAGER, name: "المدير", devices: [], epoch: 0, created: Hub.now(), expires: null };
  }
  async holder(id) { return id === MANAGER ? await this.manager() : await this.store.get("sub:" + id); }
  async save(u) { await this.store.put(u.id === MANAGER ? "cfg:manager" : "sub:" + u.id, u); }
  codeTag() {                            // changes with the manager's code, so sessions opened with an old code end with it
    return (this.tag ||= sha("manager-code:" + (this.env.ADMIN_CODE || "")));
  }

  // A session row is only a pointer: on every request the subscriber's record decides whether it
  // still counts, so a stop, an end of validity or a forced sign-out works at once.
  async session(req, unproven = false) {
    const token = cookieValue(req, "fs");
    if (!token || !/^[0-9a-f]{64}$/.test(token)) return { error: "login" };
    const hash = await sha(token);
    const row = await this.store.get("sess:" + hash);
    if (!row) return { error: "login" };
    const who = await this.holder(row.sub);
    if (!who) return { error: "logout" };                // the subscription was removed
    if (who.disabled) return { error: "disabled" };
    if (isExpired(who)) return { error: "expired" };
    const dev = who.devices.find(d => d.id === row.device);
    if (!dev || row.epoch !== who.epoch) return { error: "logout" };     // device removed, or signed out by the manager
    if (row.role === "admin" && who.tag !== (await this.codeTag())) return { error: "logout" };
    const left = row.proof_until - Hub.now();
    if (left <= 0 && !unproven) return { error: "proof" };               // the device must sign again first
    return { who, row, hash, dev, left: Math.max(0, left) };
  }
  async deny(why, withNonce = false) {
    const body = { error: why };
    if (why === "proof" && withNonce) body.nonce = await this.newNonce();   // saves the app a round trip
    return json(body, 401);
  }
  async dropSessions(match) {            // rare (a sign-in, a removed subscriber), so a walk over the rows is fine
    const all = await this.store.list({ prefix: "sess:" });
    await this.dropKeys([...all].filter(([, s]) => match(s)).map(([k]) => k));
  }

  async login(req) {
    const geo = geoOf(req), b = await bodyOf(req), t = Hub.now();
    const typed = typedText(b.key), pub = publicKeyOf(b.pub);
    if (!typed || !pub || !b.nonce || !b.sig) return json({ error: "missing" }, 400);
    // first the device shows that it holds the private half of the key pair it presents
    if (!(await this.useNonce(b.nonce)) || !(await signedBy(pub, "rasad-login:" + b.nonce, b.sig))) return json({ error: "proof" }, 400);
    const device = await deviceIdOf(pub), label = labelOf(b.label, req);
    const asManager = !!this.env.ADMIN_CODE && sameText(typed, typedText(this.env.ADMIN_CODE));
    const key = asManager ? "" : normalizeKey(typed);
    const keyHash = key ? await sha(key) : "", tag = asManager ? await this.codeTag() : "";
    const token = randHex(32), tokenHash = await sha(token);
    // from here on only the store is awaited, so no other request slips in between reading and saving
    const net = netOf(geo.ip), fails = await this.fails(net);
    const id = keyHash ? await this.store.get("key:" + keyHash) : null;
    const who = asManager ? await this.manager() : id ? await this.store.get("sub:" + id) : null;
    let dev = who ? who.devices.find(d => d.id === device) : null;
    // too many wrong keys from this network: only a device already tied to the key it shows gets through
    if (fails.n >= FAIL_MAX && !dev) return json({ error: "wait" }, 429);
    if (!who) {
      await this.store.put("fail:" + net, { n: fails.n + 1, t });
      await this.log("failed", null, geo, label);
      if (fails.n + 1 === 3) await this.notify(`⚠️ ثلاث محاولات دخول بمفتاح خاطئ من الشبكة نفسها\n📍 ${esc(place(geo))} · ${esc(label)}`);
      return json({ error: "wrong" }, 401);
    }
    if (asManager) {
      if (!dev) {                        // the manager on a new device: allowed, and their other device is signed out
        if (who.devices.length) await this.notify(`🔐 دخول المدير من جهاز جديد\n📍 ${esc(place(geo))} · ${esc(label)}\nإن لم تكن أنت فغيّر رمز المدير فوراً.`);
        dev = { id: device, label, bound_at: t, last_seen: t, pub };
        who.devices = [dev];
        who.epoch++;
      }
      who.tag = tag;
    } else {
      if (who.disabled) { await this.log("refused", who, geo, label); return json({ error: "disabled" }, 403); }
      if (isExpired(who)) { await this.log("expired", who, geo, label); return json({ error: "expired" }, 403); }
      if (!dev) {
        if (who.devices.length >= who.max_devices) return await this.otherDevice(who, { device, pub, label }, geo);
        dev = { id: device, label, bound_at: t, last_seen: t, pub };
        who.devices.push(dev);           // the first device, or one more within the number the manager allowed
        who.pending = who.pending.filter(q => q.device_id !== device);
        await this.notify(who.devices.length === 1
          ? `🔐 <b>${esc(who.name)}</b> دخل بمفتاحه لأول مرة، وارتبط المفتاح بهذا الجهاز\n📍 ${esc(place(geo))} · ${esc(label)}`
          : `📱 <b>${esc(who.name)}</b> ربط جهازاً آخر بمفتاحه (${who.devices.length} من ${who.max_devices} مسموح بها)\n📍 ${esc(place(geo))} · ${esc(label)}`);
      }
    }
    // one session per device: its earlier cookies end here
    await this.dropSessions(s => s.sub === who.id && s.device === device);
    await this.store.put("sess:" + tokenHash,
      { sub: who.id, role: meOf(who).role, device, epoch: who.epoch, t, proof_until: t + PROOF_FOR });
    Object.assign(dev, { label, last_seen: t });
    Object.assign(who, { last_login: t, city: geo.city, country: geo.country });
    await this.save(who);
    await this.log("login", who, geo, label);
    return json({ ok: true, me: meOf(who), server_time: t }, 200,
      { "set-cookie": sessionCookie(token), "x-proof-left": String(PROOF_FOR / 1000) });
  }

  // The key is tied to other device(s): refused here, and the manager is asked once.
  async otherDevice(who, d, geo) {
    const t = Hub.now();
    who.pending = who.pending.filter(q => t - q.t < PENDING_FOR);
    if ((who.refused || []).some(r => r.id === d.device && t - r.t < REFUSED_FOR)) {   // the manager said no to this device today
      await this.log("blocked", who, geo, d.label);
      return json({ error: "device", requested: false }, 403);
    }
    const asked = who.pending.find(q => q.device_id === d.device);
    if (asked) Object.assign(asked, { t, label: d.label, city: geo.city, country: geo.country });
    else who.pending = [...who.pending, { rid: randHex(6), device_id: d.device, pub: d.pub, label: d.label, t,
      city: geo.city, country: geo.country }].slice(-PENDING_MAX);
    await this.save(who);
    await this.log(asked ? "blocked" : "requested", who, geo, d.label);
    if (!asked) await this.notify(`⛔ طلب دخول بمفتاح <b>${esc(who.name)}</b> من جهاز آخر (لم يُسمح له)\n📍 ${esc(place(geo))} · ${esc(d.label)}\nإن كان المشترك نفسه قد غيّر جهازه فوافق على الطلب من لوحة المدير، وإلا فارفضه.`);
    return json({ error: "device", requested: true }, 403);
  }

  // The device signs a fresh number and gets a new cookie. The old cookie works one more minute
  // (requests already on their way), then only for proving again in case this answer was lost on a
  // bad network. So a cookie copied out of the device is useless: its copy cannot sign.
  async prove(req) {
    const b = await bodyOf(req);
    const s = await this.session(req, true);             // also a session whose 15 minutes have passed
    if (s.error) return await this.deny(s.error);
    if (!(await this.useNonce(b.nonce)) || !(await signedBy(s.dev.pub, "rasad-prove:" + b.nonce, b.sig))) return json({ error: "proof" }, 400);
    if (s.left > PROOF_FOR - 30e3) {                     // proven seconds ago (another tab did it): nothing to renew
      return json({ ok: true }, 200, { "x-proof-left": String(Math.floor(s.left / 1000)) });
    }
    const t = Hub.now(), token = randHex(32), hash = await sha(token);
    const row = await this.store.get("sess:" + s.hash);  // read again: only the store is awaited from here
    if (!row) return await this.deny("login");
    if (row.prev) await this.store.delete("sess:" + row.prev);   // the cookie before this one: the device has moved on
    await this.store.put({
      ["sess:" + hash]: { ...row, proof_until: t + PROOF_FOR, prev: s.hash },
      ["sess:" + s.hash]: { ...row, prev: null, proof_until: Math.min(row.proof_until, t + GRACE) },
    });
    return json({ ok: true }, 200, { "set-cookie": sessionCookie(token), "x-proof-left": String(PROOF_FOR / 1000) });
  }

  async logout(req) {
    const token = cookieValue(req, "fs");
    if (token && /^[0-9a-f]{64}$/.test(token)) {
      const k = "sess:" + await sha(token), row = await this.store.get(k);
      if (row) {
        await this.store.delete(row.prev ? [k, "sess:" + row.prev] : [k]);
        this.online.delete(row.sub);
      }
    }
    return json({ ok: true }, 200, { "set-cookie": sessionCookie("", 0) });
  }

  // ---------------- who has the app open
  onlineList() {
    const now = Hub.now(), out = [];
    for (const [id, v] of this.online) if (now - v.t < ONLINE_FOR) out.push({ id, ...v });
    return out.sort((a, b) => a.since - b.since);
  }
  async seen(s, req) {
    const now = Hub.now(), geo = geoOf(req), u = s.who;
    const prev = this.online.get(u.id);
    const lastSeen = prev ? prev.t : (u.last_seen || 0);
    const since = prev && now - prev.t < VISIT_GAP ? prev.since : now;
    this.online.set(u.id, { t: now, since, city: geo.city, country: geo.country, name: u.name, role: s.row.role });
    if (now - lastSeen >= VISIT_GAP) {     // a new visit
      Object.assign(u, { last_open: now, last_seen: now, city: geo.city, country: geo.country });
      s.dev.last_seen = now;
      await this.save(u);
      await this.log("open", u, geo, s.dev.label);
      if (s.row.role !== "admin") {
        const n = this.onlineList().length;
        await this.notify(`👤 <b>${esc(u.name)}</b> فتح التطبيق\n📍 ${esc(place(geo))}\n👥 مفتوح الآن لدى ${n === 1 ? "شخص واحد" : n === 2 ? "شخصين" : n + " أشخاص"}`);
      }
    } else if (now - (u.last_seen || 0) > 5 * 60e3) {
      u.last_seen = s.dev.last_seen = now;
      await this.save(u);
    }
  }

  // ---------------- data, the archive and clips
  async data() {
    const d = await this.getBig("data");
    const body = d ? d.bytes : enc.encode('{"meta":null,"items":[]}');
    return new Response(body, { headers: JSON_HEADERS });
  }
  async archiveMonths() {                // newest first, from one small row: listing never loads the archives themselves
    const index = (await this.store.get("cfg:archives")) || {};
    return Object.keys(index).sort().reverse().map(month => ({ month, size: index[month].size, t: index[month].t }));
  }
  async archive(month) {
    const a = /^\d{4}-\d{2}$/.test(month) ? await this.getBig("archive:" + month) : null;
    return a ? new Response(a.bytes, { headers: JSON_HEADERS }) : json({ error: "not found" }, 404);
  }
  async clip(req, name) {
    if (!/^[\w.\-]+\.(mp4|jpg|png)$/.test(name)) return json({ error: "not found" }, 404);
    const c = await this.getBig("clip:" + name);
    if (!c) return json({ error: "not found" }, 404);
    const size = c.bytes.byteLength;
    const base = { "content-type": c.meta.type || "application/octet-stream", "accept-ranges": "bytes",
                   "cache-control": "private, max-age=86400" };
    const range = (req.headers.get("range") || "").match(/^bytes=(\d*)-(\d*)$/);
    if (range) {                         // Safari plays video only with byte ranges
      let start = range[1] === "" ? Math.max(0, size - Number(range[2])) : Number(range[1]);
      let end = range[1] !== "" && range[2] !== "" ? Math.min(Number(range[2]), size - 1) : size - 1;
      if (start >= size || start > end) return new Response(null, { status: 416, headers: { ...base, "content-range": `bytes */${size}` } });
      return new Response(req.method === "HEAD" ? null : c.bytes.slice(start, end + 1), { status: 206,
        headers: { ...base, "content-range": `bytes ${start}-${end}/${size}`, "content-length": String(end - start + 1) } });
    }
    return new Response(req.method === "HEAD" ? null : c.bytes, { headers: { ...base, "content-length": String(size) } });
  }

  async ingest(req, p, m) {
    if (!this.env.INGEST_KEY || !sameText(req.headers.get("x-ingest-key") || "", this.env.INGEST_KEY)) return json({ error: "key" }, 401);
    if (p === "/api/ingest/data" && m === "POST") {
      const body = new Uint8Array(await req.arrayBuffer());
      JSON.parse(new TextDecoder().decode(body));     // refuse anything that is not JSON
      await this.putBig("data", body, "application/json");
      return json({ ok: true, size: body.byteLength });
    }
    if (p.startsWith("/api/ingest/archive/") && m === "PUT") {   // past incidents, one file per month
      const month = p.slice("/api/ingest/archive/".length);
      if (!/^\d{4}-\d{2}$/.test(month)) return json({ error: "month" }, 400);
      const body = new Uint8Array(await req.arrayBuffer());
      try { JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body)); } catch { return json({ error: "json" }, 400); }
      const meta = await this.putBig("archive:" + month, body, "application/json");
      const index = (await this.store.get("cfg:archives")) || {};
      index[month] = { size: meta.size, t: meta.t };
      await this.store.put("cfg:archives", index);
      return json({ ok: true, size: body.byteLength });
    }
    if (p.startsWith("/api/ingest/clip/") && m === "PUT") {
      const name = decodeURIComponent(p.slice("/api/ingest/clip/".length));
      if (!/^[\w.\-]+\.(mp4|jpg|png)$/.test(name)) return json({ error: "name" }, 400);
      const type = name.endsWith(".mp4") ? "video/mp4" : name.endsWith(".png") ? "image/png" : "image/jpeg";
      await this.putBig("clip:" + name, new Uint8Array(await req.arrayBuffer()), type);
      return json({ ok: true });
    }
    if (p === "/api/ingest/clips" && m === "GET") {
      const metas = await this.store.list({ prefix: "clip:" });
      const names = [...metas.keys()].filter(k => k.endsWith("#meta")).map(k => k.slice(5, -5));
      return json({ clips: names });
    }
    if (p === "/api/ingest/keep" && m === "POST") {   // clips no incident uses any more are removed
      const keep = new Set(((await req.json()).clips) || []);
      const metas = await this.store.list({ prefix: "clip:" });
      let removed = 0;
      for (const k of metas.keys()) {
        if (!k.endsWith("#meta")) continue;
        const name = k.slice(5, -5);
        if (!keep.has(name)) { await this.dropBig("clip:" + name); removed++; }
      }
      await this.pruneLog();
      await this.tidy();
      return json({ ok: true, removed });
    }
    return json({ error: "not found" }, 404);
  }

  // ---------------- the manager's panel
  view(u, now) {                         // a subscriber as the panel sees it: no key, no hash, no public keys
    const on = this.online.get(u.id);
    return { id: u.id, name: u.name, note: u.note || "", status: statusOf(u), disabled: !!u.disabled, created: u.created,
      expires: u.expires ?? null, key_hint: u.key_hint, max_devices: u.max_devices,
      devices: u.devices.map(d => ({ id: d.id, label: d.label, bound_at: d.bound_at, last_seen: d.last_seen })),
      pending: u.pending.filter(q => now - q.t < PENDING_FOR).map(q => ({ rid: q.rid, label: q.label, t: q.t, city: q.city, country: q.country })),
      last_login: u.last_login || null, last_seen: u.last_seen || null, last_open: u.last_open || null,
      city: u.city || "", country: u.country || "", online: !!on && now - on.t < ONLINE_FOR };
  }
  async admin(req, p, m) {
    const body = m === "POST" ? await bodyOf(req) : {};
    const geo = geoOf(req), t = Hub.now();
    if (p === "/api/admin/state" && m === "GET") {
      const subs = [...(await this.store.list({ prefix: "sub:" })).values()]
        .sort((a, b) => b.created - a.created).map(u => this.view(u, t));
      const log = [...(await this.store.list({ prefix: "log:", reverse: true, limit: 100 })).values()];
      return json({ subs, online: this.onlineList(), log, telegram: !!(await this.store.get("cfg:admin_chat")), now: t,
        code_weak: String(this.env.ADMIN_CODE || "").length < 12 });
    }
    if (m !== "POST") return json({ error: "not found" }, 404);
    if (p === "/api/admin/telegram") return await this.telegramLink(req);
    if (p === "/api/admin/sub/create") return await this.createSub(body, geo);
    const act = (p.match(/^\/api\/admin\/sub\/(update|disable|logout|rekey|unbind|approve|reject|delete)$/) || [])[1];
    if (!act) return json({ error: "not found" }, 404);
    const fresh = act === "rekey" ? newKey() : "", freshHash = fresh ? await sha(fresh) : "";
    const id = typeof body.id === "string" && /^[0-9a-f]{12}$/.test(body.id) ? body.id : "";
    const u = id ? await this.store.get("sub:" + id) : null;      // only the store is awaited from here to the save
    if (!u) return json({ error: "no such subscriber" }, 404);
    let device = "";                     // the device an action was about, for the log

    if (act === "update") {
      if (!["name", "note", "expires", "add_days", "max_devices"].some(k => body[k] !== undefined)) return json({ error: "missing" }, 400);
      if (body.name !== undefined) {
        const name = tidyText(body.name, 60);
        if (!name) return json({ error: "name" }, 400);
        if (await this.nameTaken(name, id)) return json({ error: "exists" }, 409);
        u.name = name;
      }
      if (body.note !== undefined) u.note = tidyText(body.note, 200);
      if (body.expires !== undefined) {
        if (body.expires !== null && !validTime(body.expires)) return json({ error: "expires" }, 400);
        u.expires = body.expires;
      }
      if (body.add_days !== undefined) {   // a renewal counts from the end of the running period, or from now when it is over
        if (!validDays(body.add_days)) return json({ error: "days" }, 400);
        u.expires = Math.round((u.expires != null && u.expires > t ? u.expires : t) + body.add_days * 86400e3);
      }
      if (body.max_devices !== undefined) u.max_devices = devicesMax(body.max_devices);
    } else if (act === "disable") {
      if (typeof body.disabled !== "boolean") return json({ error: "disabled" }, 400);
      if (u.disabled === body.disabled) return json({ ok: true, sub: this.view(u, t) });
      u.disabled = body.disabled;
      if (u.disabled) { u.epoch++; this.online.delete(id); }     // its sessions end now and stay ended after a restart
    } else if (act === "logout") {
      u.epoch++;                         // every session of this subscriber ends; the key and the device stay
      this.online.delete(id);
    } else if (act === "rekey") {
      await this.store.delete("key:" + u.key_hash);
      await this.store.put("key:" + freshHash, id);
      Object.assign(u, { key_hash: freshHash, key_hint: fresh.slice(-4), pending: [], refused: [] });
      u.epoch++;
      this.online.delete(id);
    } else if (act === "unbind") {
      if (body.device_id != null) {
        const d = u.devices.find(x => x.id === body.device_id);
        if (!d) return json({ error: "no such device" }, 404);
        device = d.label;
        u.devices = u.devices.filter(x => x !== d);              // that device's sessions end with it
      } else {
        device = u.devices.map(d => d.label).join("، ");
        u.devices = [];
        u.epoch++;
      }
      if (!u.devices.length) this.online.delete(id);
    } else if (act === "approve" || act === "reject") {
      const q = u.pending.find(x => x.rid === body.rid && t - x.t < PENDING_FOR);
      if (!q) return json({ error: "no such request" }, 404);
      device = q.label;
      u.pending = u.pending.filter(x => x !== q);
      u.refused = (u.refused || []).filter(r => r.id !== q.device_id && t - r.t < REFUSED_FOR);
      if (act === "reject") u.refused = [...u.refused, { id: q.device_id, t }].slice(-10);
      else if (!u.devices.some(d => d.id === q.device_id)) {
        const d = { id: q.device_id, label: q.label, bound_at: t, last_seen: null, pub: q.pub };
        // it takes the place of the old device(s) when asked to, or when the allowed number is reached
        u.devices = body.replace === true || u.devices.length >= u.max_devices ? [d] : [...u.devices, d];
      }
    } else if (act === "delete") {
      await this.dropSessions(s => s.sub === id);
      await this.store.delete(["sub:" + id, "key:" + u.key_hash]);
      this.online.delete(id);
      await this.log("deleted", u, geo, "");
      return json({ ok: true });
    }
    await this.save(u);
    const event = { update: "updated", disable: u.disabled ? "disabled" : "enabled", logout: "forced_logout", rekey: "rekeyed",
      unbind: "unbound", approve: "approved", reject: "rejected" }[act];
    await this.log(event, u, geo, device);
    return json({ ok: true, sub: this.view(u, t), ...(fresh ? { key: fresh } : {}) });
  }
  async nameTaken(name, except) {
    const all = await this.store.list({ prefix: "sub:" });
    return [...all.values()].some(u => u.id !== except && u.name.toLowerCase() === name.toLowerCase());
  }
  async createSub(body, geo) {
    const t = Hub.now(), name = tidyText(body.name, 60);
    if (!name) return json({ error: "name" }, 400);
    let expires = null;                  // no end, unless a period or an end is given
    if (body.days != null) {
      if (!validDays(body.days) || body.days < 0) return json({ error: "days" }, 400);
      expires = Math.round(t + body.days * 86400e3);
    } else if (body.expires != null) {
      if (!validTime(body.expires)) return json({ error: "expires" }, 400);
      expires = body.expires;
    }
    const key = newKey(), keyHash = await sha(key);
    if (await this.nameTaken(name, "")) return json({ error: "exists" }, 409);
    let id = randHex(6);
    while (await this.store.get("sub:" + id)) id = randHex(6);
    // the key itself is not kept: only its fingerprint, and its last four symbols to tell keys apart
    const u = { id, name, note: tidyText(body.note, 200), created: t, key_hint: key.slice(-4), key_hash: keyHash, expires,
      disabled: false, max_devices: devicesMax(body.max_devices), devices: [], pending: [], refused: [], epoch: 0,
      last_login: null, last_seen: null, last_open: null, city: "", country: "" };
    await this.store.put({ ["sub:" + id]: u, ["key:" + keyHash]: id });
    await this.log("created", u, geo, "");
    return json({ ok: true, id, key, sub: this.view(u, t) });
  }

  async log(event, who, geo, device) {
    const t = Hub.now();
    this.logged = (this.logged + 1) % 1e4;       // rows of the same millisecond stay in the order they happened
    await this.store.put(`log:${String(t).padStart(14, "0")}-${String(this.logged).padStart(4, "0")}${randHex(2)}`,
      { t, event, id: who ? who.id : "", name: who ? who.name : "", city: geo.city || "", country: geo.country || "", device: device || "" });
  }
  async pruneLog() {
    const cut = `log:${String(Hub.now() - LOG_DAYS * 86400e3).padStart(14, "0")}`;
    const old = await this.store.list({ prefix: "log:", end: cut, limit: 500 });
    if (old.size) await this.dropKeys([...old.keys()]);
  }
  async tidy() {                         // twice a day: sessions whose cookie cannot exist any more, and other leftovers
    const t = Hub.now();
    if (t - this.tidied < 12 * 3600e3) return;
    this.tidied = t;
    await this.dropSessions(s => t - s.proof_until > COOKIE_DAYS * 86400e3);
    await this.sweep();
  }

  // ---------------- Telegram: the manager's notifications
  async tg(method, payload) {
    if (!this.env.TG_BOT_TOKEN) return null;
    const r = await fetch(`https://api.telegram.org/bot${this.env.TG_BOT_TOKEN}/${method}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
    return r.json().catch(() => null);
  }
  async notify(html) {
    const chat = await this.store.get("cfg:admin_chat");
    if (!chat) return;
    this.ctx.waitUntil(this.tg("sendMessage", { chat_id: chat, text: html, parse_mode: "HTML",
      link_preview_options: { is_disabled: true } }));
  }
  async telegramLink(req) {
    if (!this.env.TG_BOT_TOKEN || !this.env.TG_SECRET) return json({ error: "no bot" }, 400);
    const origin = new URL(req.url).origin;
    await this.tg("setWebhook", { url: `${origin}/tg/${this.env.TG_SECRET}`, secret_token: this.env.TG_SECRET,
      allowed_updates: ["message"], drop_pending_updates: true });
    const me = await this.tg("getMe", {});
    const code = randHex(8);
    await this.store.put("cfg:tg_code", { code, t: Hub.now() });
    return json({ link: `https://t.me/${me && me.result ? me.result.username : ""}?start=${code}` });
  }
  async telegramUpdate(req, p) {
    const secret = this.env.TG_SECRET || "";
    if (!secret || p !== `/tg/${secret}` || !sameText(req.headers.get("x-telegram-bot-api-secret-token") || "", secret)) {
      return new Response("no", { status: 403 });
    }
    const upd = await req.json().catch(() => ({}));
    const msg = upd.message || {};
    const want = await this.store.get("cfg:tg_code");
    const text = String(msg.text || "");
    if (want && Hub.now() - want.t < 30 * 60e3 && text === `/start ${want.code}` && msg.chat && msg.chat.type === "private") {
      await this.store.put("cfg:admin_chat", msg.chat.id);
      await this.store.delete("cfg:tg_code");
      await this.tg("sendMessage", { chat_id: msg.chat.id, parse_mode: "HTML",
        text: "✅ <b>تم ربط إشعارات ALFAQAAR FIRMS</b>\nستصلك هنا رسالة كلما فتح أحد المشتركين التطبيق، مع مدينته وعدد من يفتحه الآن، وكل طلب دخول من جهاز جديد وأي محاولة دخول مرفوضة." });
    }
    return new Response("ok");
  }
}
