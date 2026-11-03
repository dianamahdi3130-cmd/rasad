// ALFAQAAR FIRMS — the private door of the Rasad app (a Cloudflare Worker).
//
// The app's files are public (they hold no data); everything with data goes through here:
//   /api/login           staff sign in with their own name and password; the first sign-in ties the
//                        account to that browser, and the account then works only there
//   /api/me, /api/data   who is signed in; the incidents (only with a valid session)
//   /clips/<file>        the satellite clips and stills (only with a valid session)
//   /api/admin/...       the manager's panel: accounts, who has the app open now, the access log
//   /api/ingest/...      the monitor on GitHub pushes fresh data and clips here (secret key)
//   /tg/<secret>         Telegram tells us the manager's chat, so sign-ins reach the manager
// The manager is told on Telegram who opened the app, from which city, and how many have it open.

const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" };
const CHUNK = 1_900_000;                 // Durable Object rows hold at most 2 MB
const VISIT_GAP = 20 * 60e3;             // a new "opened the app" after 20 minutes away
const ONLINE_FOR = 150e3;                // counted as having the app open for 2.5 minutes after a refresh
const LOG_DAYS = 45;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;
    if (p === "/api/ping") return new Response("ok", { headers: { "cache-control": "no-store" } });
    if (p.startsWith("/api/") || p.startsWith("/clips/") || p.startsWith("/tg/")) {
      const cf = request.cf || {};
      const headers = new Headers(request.headers);
      headers.set("x-geo", JSON.stringify({ city: cf.city || "", region: cf.region || "", country: cf.country || "" }));
      return env.HUB.get(env.HUB.idFromName("main")).fetch(new Request(request, { headers }));
    }
    return env.ASSETS.fetch(request);
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
function normName(s) {                   // usernames: letters (Arabic or Latin), digits, _ . -
  s = String(s || "").trim().toLowerCase().replace(/\s+/g, "_");
  return /^[\p{L}\p{N}_.\-]{2,32}$/u.test(s) ? s : "";
}
function newPassword() {                 // easy to read out and type, ~50 bits
  const abc = "abcdefghjkmnpqrstuvwxyz23456789";
  const b = crypto.getRandomValues(new Uint8Array(10));
  return [...b].map(x => abc[x % abc.length]).join("").replace(/^(.{5})/, "$1-");
}
function sessionCookie(token, maxAge = 400 * 86400) {
  return `fs=${token}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`;
}

// ------------------------------------------------------------------ the store (one Durable Object)
export class Hub {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
    this.store = ctx.storage;
    this.online = new Map();             // username -> {t, city, country, display, role}
  }

  async fetch(req) {
    const url = new URL(req.url), p = url.pathname, m = req.method;
    try {
      if (p.startsWith("/tg/")) return await this.telegramUpdate(req, p);
      if (p.startsWith("/api/ingest/")) return await this.ingest(req, p, m);
      if (p === "/api/login" && m === "POST") return await this.login(req);
      const me = await this.session(req);
      if (!me) return json({ error: "login" }, 401);
      if (p === "/api/me") return json({ username: me.username, display: me.display, role: me.role });
      if (p === "/api/data") { await this.seen(me, req); return await this.data(); }
      if (p.startsWith("/clips/") && (m === "GET" || m === "HEAD")) return await this.clip(req, decodeURIComponent(p.slice(7)));
      if (p.startsWith("/api/admin/")) {
        if (me.role !== "admin") return json({ error: "forbidden" }, 403);
        return await this.admin(req, p, m, me);
      }
      return json({ error: "not found" }, 404);
    } catch (e) {
      return json({ error: "server", detail: String((e && e.message) || e) }, 500);
    }
  }

  // ---------------- big values in pieces (a row holds at most 2 MB)
  async putBig(key, bytes, type) {
    const old = await this.store.get(key + "#meta");
    const n = Math.ceil(bytes.byteLength / CHUNK) || 1;
    const parts = {};
    for (let i = 0; i < n; i++) parts[`${key}#${i}`] = bytes.slice(i * CHUNK, (i + 1) * CHUNK);
    await this.store.put(parts);
    await this.store.put(key + "#meta", { n, size: bytes.byteLength, type, t: Date.now() });
    if (old && old.n > n) await this.store.delete([...Array(old.n - n)].map((_, i) => `${key}#${n + i}`));
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

  // ---------------- accounts and sessions
  async user(name) { return name ? await this.store.get("user:" + name) : null; }
  async saveUser(u) { await this.store.put("user:" + u.username, u); }
  async session(req) {
    const token = cookieValue(req, "fs");
    if (!token || !/^[0-9a-f]{64}$/.test(token)) return null;
    const s = await this.store.get("sess:" + await sha(token));
    if (!s) return null;
    const u = await this.user(s.username);
    if (!u || u.disabled || u.device !== s.device) return null;  // stopped, or its device was reset
    return u;
  }
  async dropSessions(username) {
    const all = await this.store.list({ prefix: "sess:" });
    const gone = [...all].filter(([, s]) => s.username === username).map(([k]) => k);
    if (gone.length) await this.store.delete(gone);
  }
  async passwordHash(salt, password) { return sha(`${salt}:${password}`); }

  async login(req) {
    const geo = geoOf(req), ua = req.headers.get("user-agent") || "";
    const b = await req.json().catch(() => ({}));
    const username = normName(b.username), password = String(b.password || ""), device = String(b.device || "");
    if (!username || !password || !/^[0-9a-f]{64}$/.test(device)) return json({ error: "missing" }, 400);
    const failKey = "fail:" + username;
    const fails = (await this.store.get(failKey)) || { n: 0, t: 0 };
    if (fails.n >= 5 && Date.now() - fails.t < 15 * 60e3) return json({ error: "wait" }, 429);
    const failed = async () => {
      const f = { n: (Date.now() - fails.t < 15 * 60e3 ? fails.n : 0) + 1, t: Date.now() };
      await this.store.put(failKey, f);
      await this.log("failed", username, geo, ua);
      if (f.n === 3) await this.notify(`⚠️ ثلاث محاولات دخول خاطئة بالاسم <b>${esc(username)}</b>\n📍 ${esc(place(geo))} · ${esc(deviceKind(ua))}`);
      return json({ error: "wrong" }, 401);
    };
    const dh = await sha(device);
    let u = await this.user(username);
    if (username === "admin") {
      if (!this.env.ADMIN_CODE || !sameText(password, this.env.ADMIN_CODE)) return failed();
      u = u || { username: "admin", display: "المدير", role: "admin", created: Date.now() };
      if (u.device && u.device !== dh) {   // the manager on a new device: allowed, the old one is signed out
        await this.dropSessions("admin");
        await this.notify(`🔐 دخول المدير من جهاز جديد\n📍 ${esc(place(geo))} · ${esc(deviceKind(ua))}\nإن لم تكن أنت فغيّر رمز المدير فوراً.`);
      }
      u.device = dh;
      u.bound_at = Date.now();
      u.agent = deviceKind(ua);
    } else {
      if (!u || u.disabled || !sameText(await this.passwordHash(u.salt, password), u.hash)) return failed();
      if (u.device && u.device !== dh) {
        await this.log("blocked", username, geo, ua);
        await this.notify(`⛔ محاولة دخول بحساب <b>${esc(u.display)}</b> من جهاز آخر (رُفضت)\n📍 ${esc(place(geo))} · ${esc(deviceKind(ua))}`);
        return json({ error: "device" }, 403);
      }
      if (!u.device) {
        u.device = dh;
        u.bound_at = Date.now();
        u.agent = deviceKind(ua);
        await this.notify(`🔐 <b>${esc(u.display)}</b> سجّل الدخول لأول مرة، وارتبط حسابه بهذا الجهاز\n📍 ${esc(place(geo))} · ${esc(u.agent)}`);
      }
    }
    await this.saveUser(u);
    await this.store.delete(failKey);
    const token = randHex(32);
    await this.store.put("sess:" + await sha(token), { username, device: dh, t: Date.now() });
    await this.log("login", username, geo, ua);
    return json({ ok: true, role: u.role, display: u.display }, 200, { "set-cookie": sessionCookie(token) });
  }

  // ---------------- who has the app open
  onlineList() {
    const now = Date.now(), out = [];
    for (const [username, v] of this.online) if (now - v.t < ONLINE_FOR) out.push({ username, ...v });
    return out.sort((a, b) => a.since - b.since);
  }
  async seen(u, req) {
    const now = Date.now(), geo = geoOf(req);
    const prev = this.online.get(u.username);
    const lastSeen = prev ? prev.t : (u.last_seen || 0);
    const since = prev && now - prev.t < VISIT_GAP ? prev.since : now;
    this.online.set(u.username, { t: now, since, city: geo.city, country: geo.country, display: u.display, role: u.role });
    if (now - lastSeen >= VISIT_GAP) {     // a new visit
      u.last_open = now;
      u.city = geo.city;
      u.country = geo.country;
      u.last_seen = now;
      await this.saveUser(u);
      await this.log("open", u.username, geo, req.headers.get("user-agent") || "");
      if (u.role !== "admin") {
        const n = this.onlineList().length;
        await this.notify(`👤 <b>${esc(u.display)}</b> فتح التطبيق\n📍 ${esc(place(geo))}\n👥 مفتوح الآن لدى ${n === 1 ? "شخص واحد" : n === 2 ? "شخصين" : n + " أشخاص"}`);
      }
    } else if (now - (u.last_seen || 0) > 5 * 60e3) {
      u.last_seen = now;
      await this.saveUser(u);
    }
  }

  // ---------------- data and clips
  async data() {
    const d = await this.getBig("data");
    const body = d ? d.bytes : enc.encode('{"meta":null,"items":[]}');
    return new Response(body, { headers: JSON_HEADERS });
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
      return json({ ok: true, removed });
    }
    return json({ error: "not found" }, 404);
  }

  // ---------------- the manager's panel
  async admin(req, p, m, me) {
    const body = m === "POST" ? await req.json().catch(() => ({})) : {};
    if (p === "/api/admin/state") {
      const users = [...(await this.store.list({ prefix: "user:" })).values()].map(u => ({
        username: u.username, display: u.display, role: u.role, disabled: !!u.disabled, bound: !!u.device,
        bound_at: u.bound_at || null, agent: u.agent || "", last_open: u.last_open || null, city: u.city || "",
        country: u.country || "", created: u.created || null }));
      const log = [...(await this.store.list({ prefix: "log:", reverse: true, limit: 80 })).values()];
      return json({ users, online: this.onlineList(), log, telegram: !!(await this.store.get("cfg:admin_chat")) });
    }
    if (p === "/api/admin/add" && m === "POST") {
      const username = normName(body.username);
      if (!username || username === "admin") return json({ error: "name" }, 400);
      if (await this.user(username)) return json({ error: "exists" }, 409);
      const password = newPassword(), salt = randHex(16);
      await this.saveUser({ username, display: String(body.display || body.username).trim().slice(0, 40) || username,
        role: "staff", salt, hash: await this.passwordHash(salt, password), device: null, created: Date.now() });
      await this.log("added", username, geoOf(req), "");
      return json({ ok: true, username, password });
    }
    if (p === "/api/admin/telegram" && m === "POST") return await this.telegramLink(req);
    const u = await this.user(normName(body.username));
    if (!u || u.role === "admin") return json({ error: "no such account" }, 404);
    if (p === "/api/admin/reset" && m === "POST") {        // a new password and a new device
      const password = newPassword();
      u.salt = randHex(16);
      u.hash = await this.passwordHash(u.salt, password);
      u.device = null;
      u.bound_at = null;
      u.agent = "";
      await this.saveUser(u);
      await this.dropSessions(u.username);
      this.online.delete(u.username);
      await this.log("reset", u.username, geoOf(req), "");
      return json({ ok: true, username: u.username, password });
    }
    if (p === "/api/admin/disable" && m === "POST") {
      u.disabled = !!body.disabled;
      await this.saveUser(u);
      if (u.disabled) { await this.dropSessions(u.username); this.online.delete(u.username); }
      await this.log(u.disabled ? "disabled" : "enabled", u.username, geoOf(req), "");
      return json({ ok: true });
    }
    if (p === "/api/admin/delete" && m === "POST") {
      await this.dropSessions(u.username);
      this.online.delete(u.username);
      await this.store.delete("user:" + u.username);
      await this.log("deleted", u.username, geoOf(req), "");
      return json({ ok: true });
    }
    return json({ error: "not found" }, 404);
  }

  async log(event, username, geo, ua) {
    const t = Date.now();
    await this.store.put(`log:${String(t).padStart(14, "0")}-${randHex(3)}`,
      { t, event, username, city: geo.city || "", country: geo.country || "", agent: ua ? deviceKind(ua) : "" });
  }
  async pruneLog() {
    const cut = `log:${String(Date.now() - LOG_DAYS * 86400e3).padStart(14, "0")}`;
    const old = await this.store.list({ prefix: "log:", end: cut, limit: 500 });
    if (old.size) await this.store.delete([...old.keys()]);
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
    await this.store.put("cfg:tg_code", { code, t: Date.now() });
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
    if (want && Date.now() - want.t < 30 * 60e3 && text === `/start ${want.code}` && msg.chat && msg.chat.type === "private") {
      await this.store.put("cfg:admin_chat", msg.chat.id);
      await this.store.delete("cfg:tg_code");
      await this.tg("sendMessage", { chat_id: msg.chat.id, parse_mode: "HTML",
        text: "✅ <b>تم ربط إشعارات ALFAQAAR FIRMS</b>\nستصلك هنا رسالة كلما فتح أحد الكادر التطبيق، مع مدينته وعدد من يفتحه الآن، وأي محاولة دخول مرفوضة." });
    }
    return new Response("ok");
  }
}
