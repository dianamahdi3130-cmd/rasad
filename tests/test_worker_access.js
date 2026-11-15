"use strict";

// The private app's door (worker/src/index.js): the real Worker module, real Request objects, a
// stand-in for the Durable Object's storage and Node's own WebCrypto playing the devices.
// No network: the one outside call the Worker makes (Telegram) lands in `telegram` below.

const assert = require("node:assert/strict");
const { test, before } = require("node:test");
const door = require("./support/door.js");

const { BASE, ADMIN_CODE, INGEST_KEY } = door;
const MIN = 60e3, DAY = 86400e3;
const COOKIE = /^fs=[0-9a-f]{64}; Path=\/; HttpOnly; Secure; SameSite=Strict; Max-Age=34560000$/;
const WRONG = "RSD-AAAAA-BBBBB-CCCCC-DDDDD";

let Hub, worker, normalizeKey, isPublicPath, PUBLIC_FILES;
let clock = Date.parse("2026-11-15T09:00:00Z");          // the door's clock: the tests move it
const telegram = [];                                      // what would have been sent to the manager
globalThis.fetch = async (url, init) => {
  telegram.push({ ...JSON.parse(init.body), to: String(url) });
  return new Response('{"ok":true,"result":{"username":"rasad_test_bot"}}');
};

before(async () => {
  const mod = await door.loadDoor(() => clock);
  ({ Hub, normalizeKey, isPublicPath, PUBLIC_FILES } = mod);
  worker = mod.default;
});
const world = env => door.world(Hub, env);

const b64url = buf => Buffer.from(buf).toString("base64url");
const sha256 = async text => Buffer.from(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text))).toString("hex");
let serial = 0;

// A browser on one device: its key pair (the private half cannot be exported), its cookie jar,
// and the network it is on right now.
class Phone {
  static async on(w, opts = {}) {
    const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
    const jwk = await crypto.subtle.exportKey("jwk", pair.publicKey);
    const phone = new Phone();
    Object.assign(phone, { w, pair, pub: { kty: jwk.kty, crv: jwk.crv, x: jwk.x, y: jwk.y }, cookie: null,
      ip: `10.9.${++serial}.1`, geo: { city: "Basra", country: "IQ" },
      ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1" }, opts);
    phone.id = await sha256(JSON.stringify({ crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y }));
    return phone;
  }
  request(method, url, body, headers = {}) {
    const h = { "cf-connecting-ip": this.ip, "x-geo": JSON.stringify(this.geo), "user-agent": this.ua, ...headers };
    if (this.cookie) h.cookie = `theme=dark; fs=${this.cookie}`;
    if (body !== undefined && !(body instanceof Uint8Array)) { h["content-type"] = "application/json"; body = JSON.stringify(body); }
    return new Request(BASE + url, { method, headers: h, body });
  }
  async take(res) {                      // keep the cookie like a browser, and read the answer
    const set = res.headers.get("set-cookie");
    if (set) { this.setCookie = set; this.cookie = set.match(/^fs=([0-9a-f]*)/)[1] || null; }
    const isJson = (res.headers.get("content-type") || "").includes("json");
    return { status: res.status, headers: res.headers, json: isJson ? await res.json() : null,
             bytes: isJson ? null : new Uint8Array(await res.arrayBuffer()) };
  }
  async call(method, url, body, headers) { return this.take(await this.w.hub.fetch(this.request(method, url, body, headers))); }
  get(url, headers) { return this.call("GET", url, undefined, headers); }
  post(url, body = {}, headers) { return this.call("POST", url, body, headers); }
  async sign(text) {
    return b64url(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, this.pair.privateKey, new TextEncoder().encode(text)));
  }
  async nonce() { return (await this.post("/api/nonce")).json.nonce; }
  async login(key, extra = {}) {
    const nonce = await this.nonce();
    return this.post("/api/login", { key, pub: this.pub, nonce, sig: await this.sign("rasad-login:" + nonce), ...extra });
  }
  async prove(nonce) {
    nonce = nonce || await this.nonce();
    return this.post("/api/prove", { nonce, sig: await this.sign("rasad-prove:" + nonce) });
  }
  async answer(url) { const r = await this.get(url); return [r.status, r.json && r.json.error]; }
}

async function setup(env) {             // a fresh door with the manager signed in
  const w = world(env);
  const boss = await Phone.on(w, { geo: { city: "Baghdad", country: "IQ" } });
  assert.equal((await boss.login(ADMIN_CODE)).status, 200);
  return { w, boss };
}
async function subscriber(boss, body) { // the manager makes a key
  const r = await boss.post("/api/admin/sub/create", body);
  assert.equal(r.status, 200, JSON.stringify(r.json));
  return r.json;
}
async function signedIn(w, boss, body) { // a subscriber with a key, signed in on a phone
  const made = await subscriber(boss, body);
  const phone = await Phone.on(w);
  assert.equal((await phone.login(made.key)).status, 200);
  return { ...made, phone };
}
async function panel(boss) {
  if ((await boss.answer("/api/me"))[1] === "proof") assert.equal((await boss.prove()).status, 200);   // the manager's own 15 minutes
  const r = await boss.get("/api/admin/state");
  assert.equal(r.status, 200, JSON.stringify(r.json));
  return r.json;
}
const subOf = async (boss, id) => (await panel(boss)).subs.find(s => s.id === id);
const events = async boss => (await panel(boss)).log.map(e => e.event).reverse();      // oldest first
function ingest(w, method, url, body, key = INGEST_KEY) {
  return w.hub.fetch(new Request(BASE + url, { method, body, headers: key ? { "x-ingest-key": key } : {} }));
}

// ---------------------------------------------------------------- keys and the first device
test("the manager makes a key under the subscriber's name; the first sign-in ties it to that device", async () => {
  const { w, boss } = await setup();
  const made = await subscriber(boss, { name: "  علي   حسن ", days: 30, note: "قسم الإطفاء" });
  assert.match(made.key, /^RSD(-[A-HJ-NP-Z2-9]{5}){4}$/, "20 symbols, none of them 0/O/1/I");
  assert.match(made.id, /^[0-9a-f]{12}$/);

  let sub = await subOf(boss, made.id);
  assert.deepEqual({ name: sub.name, note: sub.note, status: sub.status, hint: sub.key_hint, max: sub.max_devices, devices: sub.devices,
    expires: sub.expires, online: sub.online },
  { name: "علي حسن", note: "قسم الإطفاء", status: "waiting", hint: made.key.slice(-4), max: 1, devices: [],
    expires: clock + 30 * DAY, online: false });

  const ali = await Phone.on(w);
  const r = await ali.login(made.key, { label: "آيفون · التطبيق المثبّت" });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json, { ok: true, me: { id: made.id, name: "علي حسن", role: "subscriber", expires: clock + 30 * DAY }, server_time: clock });
  assert.match(ali.setCookie, COOKIE);

  const me = await ali.get("/api/me");
  assert.equal(me.status, 200);
  assert.deepEqual(me.json, { id: made.id, name: "علي حسن", role: "subscriber", expires: clock + 30 * DAY, server_time: clock });
  assert.equal(me.headers.get("x-proof-left"), "900");
  assert.equal(me.headers.get("cache-control"), "no-store");

  sub = await subOf(boss, made.id);
  assert.equal(sub.status, "active");
  assert.deepEqual(sub.devices, [{ id: ali.id, label: "آيفون · التطبيق المثبّت", bound_at: clock, last_seen: clock }],
    "the device is named by the SHA-256 of its canonical public key");
  assert.equal(sub.last_login, clock);

  // the key itself is not kept anywhere, and nothing secret reaches the panel
  const stored = JSON.stringify([...w.storage.rows]), shown = JSON.stringify(await panel(boss));
  for (const secret of [made.key, made.key.replace(/-/g, "").slice(3), ali.cookie, boss.cookie, ADMIN_CODE]) {
    assert.ok(!stored.includes(secret) && !shown.includes(secret));
  }
  assert.ok(!/key_hash|"pub"|device_id|refused/.test(shown));
  assert.ok(!shown.includes(await sha256(made.key)), "not even the key's fingerprint");
});

test("a key works however it is typed or pasted", async () => {
  const { w, boss } = await setup();
  const { key } = await subscriber(boss, { name: "Noor" });
  const noor = await Phone.on(w);
  const arabicDigits = key.replace(/\d/g, d => String.fromCharCode(0x660 + Number(d)));
  const persianDigits = key.replace(/\d/g, d => String.fromCharCode(0x6f0 + Number(d)));
  const typed = [key, key.toLowerCase(), "  " + key.replace(/-/g, " ") + "\n", key.replace(/-/g, ""), key.slice(4),
    key.slice(4).replace(/-/g, "").toLowerCase(), "‏‫" + key + "‬", arabicDigits, persianDigits, key.replace(/-/g, "–")];
  for (const text of typed) {
    assert.equal(normalizeKey(text), key, JSON.stringify(text));
    assert.equal((await noor.login(text)).status, 200, JSON.stringify(text));
    clock += 10e3;
  }
  for (const text of ["", "RSD", key.slice(0, -1), key + "A", key.slice(0, -1) + "0", key.slice(0, -1) + "I", "RSD-" + key, null, undefined, 42]) {
    assert.equal(normalizeKey(text), "", JSON.stringify(text));
  }
});

// ---------------------------------------------------------------- wrong keys
test("a wrong key is refused; five of them from one network make it wait a quarter of an hour", async () => {
  const { w, boss } = await setup();
  const { key } = await subscriber(boss, { name: "Huda" });
  const huda = await Phone.on(w);
  assert.equal((await huda.login(key)).status, 200);

  const guesser = await Phone.on(w, { ip: "7.7.7.7" });
  for (let i = 0; i < 5; i++) {
    const r = await guesser.login(WRONG);
    assert.deepEqual([r.status, r.json], [401, { error: "wrong" }]);
    assert.equal(guesser.cookie, null);
  }
  assert.deepEqual([(await guesser.login(WRONG.slice(0, -1) + "E")).status, (await guesser.login("not a key at all")).status], [429, 429]);

  // while it waits, even the right key or the manager's code does not open from a device that is not known yet
  const second = await subscriber(boss, { name: "Zainab" });
  const zainab = await Phone.on(w, { ip: "7.7.7.7" });
  const r = await zainab.login(second.key);
  assert.deepEqual([r.status, r.json], [429, { error: "wait" }]);
  assert.equal((await (await Phone.on(w, { ip: "7.7.7.7" })).login(ADMIN_CODE)).status, 429);
  assert.equal((await subOf(boss, second.id)).status, "waiting", "nothing was bound meanwhile");

  // but a device already tied to its key is not shut out by a neighbour on the same network
  huda.ip = "7.7.7.7";
  assert.equal((await huda.login(key)).status, 200);
  // and other networks never notice
  zainab.ip = "8.8.4.4";
  assert.equal((await zainab.login(second.key)).status, 200);

  clock += 15 * MIN + 1;
  assert.equal((await guesser.login(WRONG)).status, 401, "the wait is over");
  assert.equal((await events(boss)).filter(e => e === "failed").length, 6);
});

test("wrong keys are counted for a whole IPv6 /64, not for each address in it", async () => {
  const { w } = await setup();
  for (let i = 1; i <= 5; i++) {
    const p = await Phone.on(w, { ip: `2001:db8:12:3400::${i}` });
    assert.equal((await p.login(WRONG)).status, 401);
  }
  assert.equal((await (await Phone.on(w, { ip: "2001:db8:12:3400:aaaa:bbbb:cccc:dddd" })).login(WRONG)).status, 429);
  assert.equal((await (await Phone.on(w, { ip: "2001:db8:12:3401::1" })).login(WRONG)).status, 401);
});

// ---------------------------------------------------------------- one key, one device
test("the same key on a second device is refused and waits for the manager; approval moves the key there", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Sara" });

  const laptop = await Phone.on(w, { geo: { city: "Erbil", country: "IQ" }, ua: "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36" });
  let r = await laptop.login(key);
  assert.deepEqual([r.status, r.json], [403, { error: "device", requested: true }]);
  assert.equal(laptop.cookie, null, "no session for it");

  let sub = await subOf(boss, id);
  assert.equal(sub.devices.length, 1);
  assert.equal(sub.pending.length, 1);
  assert.deepEqual({ ...sub.pending[0], rid: "x" }, { rid: "x", label: "ويندوز · Chrome", t: clock, city: "Erbil", country: "IQ" });

  clock += MIN;
  r = await laptop.login(key);               // asking again does not pile up requests
  assert.deepEqual([r.status, r.json], [403, { error: "device", requested: true }]);
  sub = await subOf(boss, id);
  assert.equal(sub.pending.length, 1);
  assert.equal((await phone.get("/api/data")).status, 200, "the first device is not disturbed");

  r = await boss.post("/api/admin/sub/approve", { id, rid: sub.pending[0].rid });
  assert.equal(r.status, 200);
  assert.deepEqual(r.json.sub.devices.map(d => d.id), [laptop.id], "with one device allowed, the new one takes its place");
  assert.deepEqual(r.json.sub.pending, []);

  // the old device's session is dead at once, and its key no longer opens there
  for (const url of ["/api/me", "/api/data", "/clips/a.mp4", "/api/archive"]) {
    assert.deepEqual(await phone.answer(url), [401, "logout"], url);
  }
  assert.equal((await phone.prove()).status, 401);
  assert.equal((await laptop.login(key)).status, 200);
  assert.equal((await laptop.get("/api/data")).status, 200);
  assert.deepEqual((await phone.login(key)).json, { error: "device", requested: true });

  assert.deepEqual((await events(boss)).filter(e => /requested|blocked|approved/.test(e)),
    ["requested", "blocked", "approved", "requested"]);
});

test("a device the manager refused cannot keep asking; at most three requests wait, each for a week", async () => {
  const { w, boss } = await setup();
  const { id, key } = await signedIn(w, boss, { name: "Omar" });

  const friend = await Phone.on(w);
  assert.equal((await friend.login(key)).json.requested, true);
  let sub = await subOf(boss, id);
  assert.equal((await boss.post("/api/admin/sub/reject", { id, rid: sub.pending[0].rid })).status, 200);
  assert.deepEqual((await subOf(boss, id)).pending, []);
  const again = await friend.login(key);
  assert.deepEqual([again.status, again.json], [403, { error: "device", requested: false }]);
  assert.deepEqual((await subOf(boss, id)).pending, [], "no new request for the manager");
  clock += DAY + 1;
  assert.equal((await friend.login(key)).json.requested, true, "a day later it may ask once more");

  for (let i = 0; i < 3; i++) {
    clock += 1000;
    await (await Phone.on(w)).login(key, { label: `جهاز ${i}` });
  }
  sub = await subOf(boss, id);
  assert.deepEqual(sub.pending.map(q => q.label), ["جهاز 0", "جهاز 1", "جهاز 2"], "the newest three are kept");
  assert.equal((await boss.post("/api/admin/sub/approve", { id, rid: "000000000000" })).status, 404);

  clock += 7 * DAY;                           // a request nobody answered for a week is gone
  assert.deepEqual((await subOf(boss, id)).pending, []);
  assert.equal((await boss.post("/api/admin/sub/approve", { id, rid: sub.pending[0].rid })).status, 404);
  assert.equal((await subOf(boss, id)).devices.length, 1);
});

test("with two devices allowed a second one signs in without approval; a third must ask", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Maryam", max_devices: 2 });
  const tablet = await Phone.on(w), third = await Phone.on(w), fourth = await Phone.on(w), fifth = await Phone.on(w);
  assert.equal((await tablet.login(key)).status, 200);
  let sub = await subOf(boss, id);
  assert.deepEqual(sub.devices.map(d => d.id), [phone.id, tablet.id]);
  assert.deepEqual(sub.pending, []);
  assert.deepEqual([(await phone.get("/api/data")).status, (await tablet.get("/api/data")).status], [200, 200]);

  assert.deepEqual((await third.login(key)).json, { error: "device", requested: true });
  assert.deepEqual((await fourth.login(key)).json, { error: "device", requested: true });
  sub = await subOf(boss, id);

  // one more place is opened: the approved device joins the two
  assert.equal((await boss.post("/api/admin/sub/update", { id, max_devices: 3 })).json.sub.max_devices, 3);
  let r = await boss.post("/api/admin/sub/approve", { id, rid: sub.pending[0].rid });
  assert.deepEqual(r.json.sub.devices.map(d => d.id), [phone.id, tablet.id, third.id]);
  assert.equal((await third.login(key)).status, 200);
  assert.equal((await phone.get("/api/me")).status, 200);

  // all places taken: approving another one replaces the old devices
  r = await boss.post("/api/admin/sub/approve", { id, rid: sub.pending[1].rid });
  assert.deepEqual(r.json.sub.devices.map(d => d.id), [fourth.id]);
  for (const old of [phone, tablet, third]) assert.deepEqual(await old.answer("/api/me"), [401, "logout"]);
  assert.equal((await fourth.login(key)).status, 200);

  // "replace" does the same on request even when there is room
  assert.equal((await boss.post("/api/admin/sub/update", { id, max_devices: 1 })).status, 200);
  assert.deepEqual((await fifth.login(key)).json, { error: "device", requested: true });
  assert.equal((await boss.post("/api/admin/sub/update", { id, max_devices: 3 })).status, 200);
  sub = await subOf(boss, id);
  r = await boss.post("/api/admin/sub/approve", { id, rid: sub.pending[0].rid, replace: true });
  assert.deepEqual(r.json.sub.devices.map(d => d.id), [fifth.id]);
  assert.deepEqual(await fourth.answer("/api/data"), [401, "logout"]);
});

// ---------------------------------------------------------------- nothing without a session
test("data, clips, the archive and the manager's panel answer nobody without a valid session", async () => {
  const { w, boss } = await setup();
  const body = JSON.stringify({ meta: { generated: "2026-11-15T09:00:00Z" }, items: [{ id: "f1" }] });
  assert.equal((await ingest(w, "POST", "/api/ingest/data", body)).status, 200);
  assert.equal((await ingest(w, "PUT", "/api/ingest/clip/f1.mp4", new Uint8Array([1, 2, 3, 4, 5, 6]))).status, 200);

  const nobody = await Phone.on(w);
  const urls = ["/api/data", "/api/me", "/clips/f1.mp4", "/api/archive", "/api/archive/2026-11", "/api/admin/state", "/api/check", "/api/nothing"];
  for (const url of urls) assert.deepEqual(await nobody.answer(url), [401, "login"], url);
  assert.equal((await nobody.post("/api/admin/sub/create", { name: "x" })).status, 401);
  nobody.cookie = "f".repeat(64);             // a made-up cookie
  for (const url of urls) assert.deepEqual(await nobody.answer(url), [401, "login"], url);
  nobody.cookie = "not-a-cookie";
  assert.deepEqual(await nobody.answer("/api/data"), [401, "login"]);

  const { phone } = await signedIn(w, boss, { name: "Layla" });
  const data = await phone.get("/api/data");
  assert.equal(data.status, 200);
  assert.deepEqual(data.json, JSON.parse(body));
  assert.equal(data.headers.get("x-proof-left"), "900");
  const clip = await phone.get("/clips/f1.mp4");
  assert.deepEqual([clip.status, clip.headers.get("content-type"), [...clip.bytes]], [200, "video/mp4", [1, 2, 3, 4, 5, 6]]);
  const part = await phone.get("/clips/f1.mp4", { range: "bytes=2-3" });
  assert.deepEqual([part.status, part.headers.get("content-range"), [...part.bytes]], [206, "bytes 2-3/6", [3, 4]]);
  assert.equal((await phone.get("/clips/missing.mp4")).status, 404);
  assert.equal((await phone.get("/clips/%E0%A4%A.mp4")).status, 404);
});

test("the manager's panel is for the manager alone", async () => {
  const { w, boss } = await setup();
  const { id, phone } = await signedIn(w, boss, { name: "Yusuf" });
  assert.deepEqual(await phone.answer("/api/admin/state"), [403, "forbidden"]);
  const actions = ["create", "update", "disable", "logout", "rekey", "unbind", "approve", "reject", "delete"];
  for (const act of actions) {
    const r = await phone.post("/api/admin/sub/" + act, { id, name: "x", disabled: false });
    assert.deepEqual([r.status, r.json], [403, { error: "forbidden" }], act);
  }
  assert.equal((await phone.post("/api/admin/telegram")).status, 403);
  assert.equal((await subOf(boss, id)).status, "active", "nothing happened");

  const me = await boss.get("/api/me");
  assert.deepEqual(me.json, { id: "admin", name: "المدير", role: "admin", expires: null, server_time: clock });
  const st = await panel(boss);
  assert.deepEqual(Object.keys(st).sort(), ["code_weak", "log", "now", "online", "subs", "telegram"]);
  assert.deepEqual([st.now, st.telegram, st.code_weak], [clock, false, false]);
  assert.deepEqual(Object.keys(st.subs[0]).sort(), ["city", "country", "created", "devices", "disabled", "expires", "id", "key_hint", "last_login",
    "last_open", "last_seen", "max_devices", "name", "note", "online", "pending", "status"]);
  assert.equal((await boss.post("/api/admin/sub/logout", { id: "aaaaaaaaaaaa" })).status, 404);
  assert.equal((await boss.post("/api/admin/sub/explode", { id })).status, 404);
  assert.equal((await boss.get("/api/admin/sub/delete")).status, 404, "an action is never a GET");
});

// ---------------------------------------------------------------- the device proves itself
test("after 15 minutes the session asks the device to prove itself again, and only then goes on", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Hassan" });
  clock += 10 * MIN;
  assert.equal((await phone.get("/api/data")).headers.get("x-proof-left"), "300");
  clock += 5 * MIN;

  const stale = await phone.get("/api/data");
  assert.equal(stale.status, 401);
  assert.equal(stale.json.error, "proof");
  assert.match(stale.json.nonce, /^[0-9a-f]{32}$/);
  assert.equal((await phone.get("/api/me")).json.error, "proof");
  assert.deepEqual((await phone.get("/clips/f1.mp4")).json, { error: "proof" }, "a picture or a video cannot sign: no number for it");
  assert.deepEqual((await phone.get("/api/check")).json, { ok: true }, "the app's own files do not wait for the proof");

  const before = phone.cookie;
  const proved = await phone.prove(stale.json.nonce);
  assert.deepEqual([proved.status, proved.json, proved.headers.get("x-proof-left")], [200, { ok: true }, "900"]);
  assert.match(phone.setCookie, COOKIE);
  assert.notEqual(phone.cookie, before, "every proof brings a new cookie");
  assert.equal((await phone.get("/api/data")).status, 200);
  assert.equal((await phone.get("/api/me")).headers.get("x-proof-left"), "900");

  // proving again a few seconds later (a second tab) changes nothing
  clock += 5000;
  const current = phone.cookie, twice = await phone.prove();
  assert.deepEqual([twice.status, twice.headers.get("set-cookie"), phone.cookie], [200, null, current]);
});

test("a copied cookie is of no use on another device: it cannot prove without the private key", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Karrar" });
  const thief = await Phone.on(w, { ip: "45.45.45.45", geo: { city: "Elsewhere", country: "NL" } });
  thief.cookie = phone.cookie;

  clock += 16 * MIN;
  const stale = await thief.get("/api/data");
  assert.equal(stale.json.error, "proof");
  // its own key pair does not match the device the session belongs to
  let r = await thief.prove(stale.json.nonce);
  assert.deepEqual([r.status, r.json], [400, { error: "proof" }]);
  // a signature the real device made for another purpose is not a proof
  const n1 = await thief.nonce();
  r = await thief.post("/api/prove", { nonce: n1, sig: await phone.sign("rasad-login:" + n1) });
  assert.equal(r.status, 400);
  // and a real proof cannot be used a second time
  const n2 = await phone.nonce(), sig = await phone.sign("rasad-prove:" + n2);
  assert.equal((await phone.post("/api/prove", { nonce: n2, sig })).status, 200);
  r = await thief.post("/api/prove", { nonce: n2, sig });
  assert.deepEqual([r.status, r.json], [400, { error: "proof" }]);
  for (const bad of [{}, { nonce: n2 }, { nonce: "zz", sig }, { nonce: await thief.nonce(), sig: "AAAA" }, { nonce: await thief.nonce(), sig: 7 }]) {
    assert.equal((await thief.post("/api/prove", bad)).status, 400);
  }
  assert.deepEqual(await thief.answer("/api/data"), [401, "proof"]);
  assert.equal((await phone.get("/api/data")).status, 200, "the real device carries on");
});

test("a copy of the cookie stops working when the real device proves again, even while that device keeps the app open", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Abbas" });
  const copy = await Phone.on(w);
  copy.cookie = phone.cookie;
  clock += 11 * MIN;
  assert.equal((await copy.get("/api/data")).status, 200, "inside the 15 minutes a copy still reads");
  assert.equal((await phone.prove()).status, 200);

  clock += 59e3;                              // requests that were on their way still get through
  assert.equal((await copy.get("/api/data")).status, 200);
  clock += 2e3;
  assert.deepEqual(await copy.answer("/api/data"), [401, "proof"]);
  assert.equal((await copy.prove()).status, 400);
  assert.equal((await phone.get("/api/data")).status, 200);

  clock += 11 * MIN;                          // at the next proof the old cookie is gone for good
  assert.equal((await phone.prove()).status, 200);
  assert.deepEqual(await copy.answer("/api/data"), [401, "login"]);
  assert.equal(w.storage.keys("sess:").length, 3, "the manager's session, this one and the one before it");
});

test("a proof whose answer was lost on a bad network does not sign the device out", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Mustafa" });
  clock += 11 * MIN;
  const kept = phone.cookie;
  assert.equal((await phone.prove()).status, 200);
  phone.cookie = kept;                        // the new cookie never arrived

  clock += 5 * MIN;
  assert.deepEqual(await phone.answer("/api/data"), [401, "proof"]);
  assert.equal((await phone.prove()).status, 200, "the old cookie is still good for proving");
  assert.notEqual(phone.cookie, kept);
  assert.equal((await phone.get("/api/data")).status, 200);
  clock += 11 * MIN;
  assert.equal((await phone.prove()).status, 200);
  assert.equal((await phone.get("/api/data")).status, 200);
});

test("two tabs of one browser proving at the same moment both end with a cookie that works", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Tabs" });
  clock += 11 * MIN;
  const shared = phone.cookie;
  const tab = () => Object.assign(Object.create(Phone.prototype), phone, { cookie: shared });
  const tabs = [tab(), tab(), tab()];
  assert.deepEqual((await Promise.all(tabs.map(t => t.prove()))).map(r => r.status), [200, 200, 200]);
  assert.equal(new Set([shared, ...tabs.map(t => t.cookie)]).size, 4, "each answer brought its own new cookie");
  // whichever answer the browser kept last, its cookie is good; the shared old one has its last minute
  for (const t of [...tabs, tab()]) assert.equal((await t.get("/api/data")).status, 200);
  clock += 11 * MIN;
  for (const t of tabs) assert.equal((await t.prove()).status, 200, "and each can be renewed in turn");
  assert.deepEqual(await tab().answer("/api/data"), [401, "login"], "the old cookie is gone");
});

test("two devices racing to be the first on a fresh key: only one ends up bound", async () => {
  const { w, boss } = await setup();
  const { id, key } = await subscriber(boss, { name: "Race" });
  const a = await Phone.on(w), b = await Phone.on(w);
  const answers = (await Promise.all([a.login(key), b.login(key)])).map(r => r.status);
  assert.ok(answers.includes(200), answers.join());
  const sub = await subOf(boss, id);
  assert.equal(sub.devices.length, 1);
  const works = [(await a.get("/api/data")).status, (await b.get("/api/data")).status];
  assert.deepEqual(works.filter(s => s === 200).length, 1, "one device has the data, the other does not: " + works.join());
  assert.equal(sub.devices[0].id, works[0] === 200 ? a.id : b.id);
});

test("changing network changes nothing: the session follows the device, not the address", async () => {
  const { w, boss } = await setup();
  const { id, phone } = await signedIn(w, boss, { name: "Rania" });
  const networks = [["37.236.1.9", "Baghdad"], ["2a02:6b8:c1f:2202::17", "Najaf"], ["185.11.4.200", "Basra"], ["149.255.1.1", "Erbil"]];
  for (const [ip, city] of networks) {
    Object.assign(phone, { ip, geo: { city, country: "IQ" } });
    clock += 6 * MIN;
    assert.equal((await phone.get("/api/me")).status, 200, ip);
    assert.equal((await phone.get("/api/data")).status, 200, ip);
    assert.equal((await phone.prove()).status, 200, ip);
  }
  const sub = await subOf(boss, id);
  assert.deepEqual([sub.devices.length, sub.pending.length, sub.status], [1, 0, "active"]);
});

// ---------------------------------------------------------------- validity
test("a key with a period stops by itself the moment the period ends, and works again when renewed", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Ahmed", days: 1 });
  clock += DAY - 1000;
  assert.equal((await phone.prove()).status, 200);
  assert.equal((await phone.get("/api/data")).status, 200, "one second before the end");

  clock += 1000;
  for (const url of ["/api/data", "/api/me", "/clips/f1.mp4", "/api/archive", "/api/check"]) assert.deepEqual(await phone.answer(url), [401, "expired"], url);
  assert.deepEqual((await phone.prove()).json, { error: "expired" });
  const r = await phone.login(key);
  assert.deepEqual([r.status, r.json], [403, { error: "expired" }]);
  assert.equal((await subOf(boss, id)).status, "expired");
  assert.ok((await events(boss)).includes("expired"));

  // renewed a few hours later: the same device carries on without typing the key again
  clock += 3 * 60 * MIN;
  assert.deepEqual(await phone.answer("/api/data"), [401, "expired"]);
  assert.equal((await boss.prove()).status, 200);
  const renewed = await boss.post("/api/admin/sub/update", { id, add_days: 30 });
  assert.deepEqual([renewed.json.sub.status, renewed.json.sub.expires], ["active", clock + 30 * DAY], "counted from now, the old period being over");
  assert.deepEqual(await phone.answer("/api/data"), [401, "proof"]);
  assert.equal((await phone.prove()).status, 200);
  assert.equal((await phone.get("/api/data")).status, 200);
  assert.equal((await phone.get("/api/me")).json.expires, clock + 30 * DAY);

  // a renewal before the end is added to what is left; an end can be set or removed outright
  assert.equal((await boss.post("/api/admin/sub/update", { id, add_days: 10 })).json.sub.expires, clock + 40 * DAY);
  assert.equal((await boss.post("/api/admin/sub/update", { id, expires: null })).json.sub.expires, null);
  assert.equal((await boss.post("/api/admin/sub/update", { id, expires: clock + 5 * DAY })).json.sub.expires, clock + 5 * DAY);
  assert.equal((await boss.post("/api/admin/sub/update", { id, expires: clock - 1 })).json.sub.status, "expired");
  assert.deepEqual(await phone.answer("/api/data"), [401, "expired"]);
  for (const bad of [{ expires: Math.floor(clock / 1000) }, { expires: "tomorrow" }, { add_days: 0 }, { add_days: "7" }, { add_days: 99999 }, {}, { name: "  " }]) {
    assert.equal((await boss.post("/api/admin/sub/update", { id, ...bad })).status, 400, JSON.stringify(bad));
  }
});

test("making a key: a name is needed and unique, a period is in days or an end date, or there is no end", async () => {
  const { boss } = await setup();
  const open = await subscriber(boss, { name: "بلا نهاية" });
  assert.equal(open.sub.expires, null);
  assert.equal((await subscriber(boss, { name: "Half a day", days: 0.5 })).sub.expires, clock + DAY / 2);
  assert.equal((await subscriber(boss, { name: "Until", expires: clock + 3 * DAY, max_devices: 9 })).sub.max_devices, 3);
  const bad = [[{}, "name"], [{ name: "" }, "name"], [{ name: 5 }, "name"], [{ name: "a", days: -3 }, "days"], [{ name: "a", days: "30" }, "days"],
    [{ name: "a", expires: 1763197200 }, "expires"], [{ name: "بلا نهاية" }, "exists"], [{ name: "HALF A DAY" }, "exists"]];
  for (const [body, error] of bad) {
    const r = await boss.post("/api/admin/sub/create", body);
    assert.deepEqual([r.status >= 400, r.json.error], [true, error], JSON.stringify(body));
  }
  assert.equal((await panel(boss)).subs.length, 3);
  const keys = new Set();
  for (let i = 0; i < 40; i++) keys.add((await subscriber(boss, { name: "k" + i })).key);
  assert.equal(keys.size, 40);
  assert.ok([..."ABCDEFGHJKLMNPQRSTUVWXYZ23456789"].every(c => [...keys].join("").includes(c)), "the whole alphabet is in use");
});

// ---------------------------------------------------------------- the manager's switches
test("stopping a key shuts the subscriber out at once; starting it again needs a fresh sign-in", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Fatima" });
  assert.equal((await phone.get("/api/data")).status, 200);
  const off = await boss.post("/api/admin/sub/disable", { id, disabled: true });
  assert.deepEqual([off.status, off.json.sub.status, off.json.sub.disabled], [200, "disabled", true]);
  for (const url of ["/api/data", "/api/me", "/clips/f1.mp4", "/api/archive/2026-11"]) assert.deepEqual(await phone.answer(url), [401, "disabled"], url);
  assert.deepEqual((await phone.prove()).json, { error: "disabled" });
  const r = await phone.login(key);
  assert.deepEqual([r.status, r.json], [403, { error: "disabled" }]);
  assert.equal((await boss.post("/api/admin/sub/disable", { id })).status, 400, "on or off must be said");

  assert.equal((await boss.post("/api/admin/sub/disable", { id, disabled: false })).json.sub.status, "active");
  assert.deepEqual(await phone.answer("/api/data"), [401, "logout"], "the old session does not come back");
  assert.equal((await phone.login(key)).status, 200, "the key and the device are as before");
  assert.equal((await phone.get("/api/data")).status, 200);
  assert.deepEqual((await events(boss)).filter(e => /abled|refused/.test(e)), ["disabled", "refused", "enabled"]);
});

test("the manager can sign a subscriber out at any moment; the key and the device stay", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Ibrahim" });
  assert.equal((await phone.get("/api/data")).status, 200);
  assert.equal((await boss.post("/api/admin/sub/logout", { id })).status, 200);
  for (let i = 0; i < 2; i++) {
    for (const url of ["/api/data", "/api/me", "/clips/f1.mp4"]) assert.deepEqual(await phone.answer(url), [401, "logout"], url);
  }
  assert.deepEqual((await phone.prove()).json, { error: "logout" });
  assert.equal((await phone.login(key)).status, 200, "a fresh sign-in on the same device needs no approval");
  assert.equal((await phone.get("/api/data")).status, 200);
  const sub = await subOf(boss, id);
  assert.deepEqual([sub.devices.length, sub.pending.length], [1, 0]);
  assert.ok((await events(boss)).includes("forced_logout"));
});

test("a new key kills the old one and every session; the device stays", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Zahra" });
  await (await Phone.on(w)).login(key);       // somebody else got hold of the key and is waiting to be approved
  const r = await boss.post("/api/admin/sub/rekey", { id });
  assert.equal(r.status, 200);
  assert.match(r.json.key, /^RSD(-[A-HJ-NP-Z2-9]{5}){4}$/);
  assert.notEqual(r.json.key, key);
  assert.deepEqual([r.json.sub.key_hint, r.json.sub.pending, r.json.sub.devices.length], [r.json.key.slice(-4), [], 1]);

  assert.deepEqual(await phone.answer("/api/data"), [401, "logout"]);
  assert.deepEqual((await phone.login(key)).json, { error: "wrong" });
  assert.equal((await phone.login(r.json.key)).status, 200);
  assert.deepEqual((await (await Phone.on(w)).login(r.json.key)).json, { error: "device", requested: true });
  assert.equal(w.storage.keys("key:").length, 1, "the old key's fingerprint is gone");
});

test("the manager can untie one device or all of them", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Tariq", max_devices: 2 });
  const tablet = await Phone.on(w);
  await tablet.login(key);
  assert.equal((await boss.post("/api/admin/sub/unbind", { id, device_id: "nope" })).status, 404);

  let r = await boss.post("/api/admin/sub/unbind", { id, device_id: phone.id });
  assert.deepEqual(r.json.sub.devices.map(d => d.id), [tablet.id]);
  assert.deepEqual(await phone.answer("/api/data"), [401, "logout"]);
  assert.equal((await tablet.get("/api/data")).status, 200, "the other device is not touched");

  r = await boss.post("/api/admin/sub/unbind", { id });
  assert.deepEqual([r.json.sub.devices, r.json.sub.status], [[], "waiting"]);
  assert.deepEqual(await tablet.answer("/api/data"), [401, "logout"]);
  const next = await Phone.on(w);
  assert.equal((await next.login(key)).status, 200, "the key waits for its next device");
  assert.deepEqual((await subOf(boss, id)).devices.map(d => d.id), [next.id]);
  assert.deepEqual(await tablet.answer("/api/data"), [401, "logout"]);
});

test("a removed subscriber is gone: sessions, key and record", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Bilal" });
  assert.deepEqual((await boss.post("/api/admin/sub/delete", { id })).json, { ok: true });
  assert.deepEqual(await phone.answer("/api/data"), [401, "login"]);
  assert.deepEqual((await phone.login(key)).json, { error: "wrong" });
  assert.deepEqual([(await panel(boss)).subs.length, w.storage.keys("key:").length, w.storage.keys("sub:").length], [0, 0, 0]);
  assert.equal(w.storage.keys("sess:").length, 1, "only the manager's session is left");
});

// ---------------------------------------------------------------- the manager
test("the manager signs in with the manager's code from any device; a new device signs the old one out", async () => {
  const { w, boss } = await setup({ TG_BOT_TOKEN: "TOKEN" });
  await w.storage.put("cfg:admin_chat", 99);
  telegram.length = 0;
  assert.deepEqual((await (await Phone.on(w)).login("not the manager's code")).json, { error: "wrong" });
  assert.equal((await boss.login("  ‏" + ADMIN_CODE + " ")).status, 200, "pasted with a stray mark and spaces");
  assert.equal(telegram.length, 0, "the same device again is no news");

  const laptop = await Phone.on(w, { ua: "Mozilla/5.0 (Macintosh; Intel Mac OS X 14) Safari/605.1.15" });
  const r = await laptop.login(ADMIN_CODE);
  assert.deepEqual(r.json, { ok: true, me: { id: "admin", name: "المدير", role: "admin", expires: null }, server_time: clock });
  assert.deepEqual(await boss.answer("/api/admin/state"), [401, "logout"]);
  assert.equal((await laptop.get("/api/admin/state")).status, 200);
  await Promise.all(w.waits);
  assert.equal(telegram.length, 1);
  assert.match(telegram[0].text, /دخول المدير من جهاز جديد/);
  assert.match(telegram[0].text, /ماك · Safari/);

  // the manager's sessions need the same proof as everyone's
  clock += 16 * MIN;
  assert.deepEqual(await laptop.answer("/api/admin/state"), [401, "proof"]);
  const copy = await Phone.on(w);
  copy.cookie = laptop.cookie;
  assert.equal((await copy.prove()).status, 400);
  assert.equal((await laptop.prove()).status, 200);
  assert.equal((await laptop.get("/api/admin/state")).status, 200);

  // a new manager's code ends every session opened with the old one
  w.redeploy({ ADMIN_CODE: "a different, longer manager code" });
  assert.deepEqual(await laptop.answer("/api/admin/state"), [401, "logout"]);
  assert.deepEqual((await laptop.login(ADMIN_CODE)).json, { error: "wrong" });
  assert.equal((await laptop.login("a different, longer manager code")).status, 200);
  assert.equal((await laptop.get("/api/admin/state")).json.code_weak, false);
  w.redeploy({ ADMIN_CODE: "short" });
  assert.equal((await laptop.login("short")).status, 200);
  assert.equal((await laptop.get("/api/admin/state")).json.code_weak, true, "the panel can warn about a short code");
  w.redeploy({ ADMIN_CODE: "" });
  assert.deepEqual(await laptop.answer("/api/me"), [401, "logout"]);
  assert.deepEqual((await laptop.login("short")).json, { error: "wrong" }, "no code set: nobody is the manager");
});

// ---------------------------------------------------------------- the sign-in itself
test("a sign-in needs a fresh number signed by the very key pair it presents", async () => {
  const { w, boss } = await setup();
  const { key } = await subscriber(boss, { name: "Dina" });
  const dina = await Phone.on(w), other = await Phone.on(w);
  const send = body => dina.post("/api/login", body);
  const good = async () => { const nonce = await dina.nonce(); return { key, pub: dina.pub, nonce, sig: await dina.sign("rasad-login:" + nonce) }; };

  for (const drop of ["key", "pub", "nonce", "sig"]) {
    const body = await good();
    delete body[drop];
    assert.deepEqual([(await send(body)).status, (await send(body)).json], [400, { error: "missing" }], drop);
  }
  const shapes = [{ kty: "RSA", n: "AQAB", e: "AQAB" }, { ...dina.pub, crv: "P-384" }, { ...dina.pub, x: "AAAA" }, { ...dina.pub, y: "!" }, "pub", null];
  for (const pub of shapes) assert.deepEqual((await send({ ...(await good()), pub })).json, { error: "missing" }, JSON.stringify(pub));
  assert.equal((await dina.call("POST", "/api/login", new TextEncoder().encode("{not json"))).status, 400);
  assert.equal((await send({ ...(await good()), key: "x".repeat(30000) })).status, 400, "an oversized body is not read");

  let body = await good();
  assert.deepEqual((await send({ ...body, sig: await other.sign("rasad-login:" + body.nonce) })).json, { error: "proof" }, "signed by another key");
  body = await good();
  assert.deepEqual((await send({ ...body, sig: await dina.sign("rasad-prove:" + body.nonce) })).json, { error: "proof" }, "signed for another purpose");
  body = await good();
  assert.deepEqual((await send({ ...body, nonce: "0".repeat(32) })).json, { error: "proof" }, "a number this door never gave");
  body = await good();
  clock += 2 * MIN + 1;
  assert.deepEqual((await send(body)).json, { error: "proof" }, "a number older than two minutes");
  body = await good();
  assert.equal((await send(body)).status, 200);
  assert.deepEqual((await send(body)).json, { error: "proof" }, "a number is good once");
  assert.equal((await subscriberCount(boss)), 1);

  // the same public key written differently is still the same device
  const y = dina.pub.y, tail = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const twin = y.slice(0, -1) + tail[tail.indexOf(y.slice(-1)) | 1];   // other padding bits, same 32 bytes
  body = await good();
  assert.equal((await send({ ...body, pub: { ...dina.pub, y: twin, alg: "ES256", ext: true } })).status, 200);
  assert.equal((await panel(boss)).subs[0].devices.length, 1);

  // a request posted by another site is refused before anything else
  const cross = await dina.post("/api/nonce", {}, { origin: "https://evil.example" });
  assert.deepEqual([cross.status, cross.json], [403, { error: "forbidden" }]);
  assert.equal((await dina.post("/api/nonce", {}, { origin: BASE })).status, 200);
});
const subscriberCount = async boss => (await panel(boss)).subs.length;

test("numbers to sign are rationed for each network and old ones are cleared away", async () => {
  const { w } = await setup();
  const p = await Phone.on(w);
  for (let i = 0; i < 20; i++) assert.equal((await p.post("/api/nonce")).status, 200);
  const r = await p.post("/api/nonce");
  assert.deepEqual([r.status, r.json], [429, { error: "wait" }]);
  assert.equal((await (await Phone.on(w)).post("/api/nonce")).status, 200, "another network is not held up");
  clock += MIN;
  assert.equal((await p.post("/api/nonce")).status, 200);
  assert.ok(w.storage.keys("nonce:").length >= 22);
  clock += 6 * MIN;
  assert.equal((await p.post("/api/nonce")).status, 200);
  assert.equal(w.storage.keys("nonce:").length, 1, "only the newest is left");
});

test("signing out ends the session on the door, and the device can sign in again", async () => {
  const { w, boss } = await setup();
  const { key, phone } = await signedIn(w, boss, { name: "Samir" });
  const old = phone.cookie;
  const out = await phone.post("/api/logout");
  assert.deepEqual([out.status, out.json, phone.cookie], [200, { ok: true }, null]);
  assert.match(phone.setCookie, /^fs=; Path=\/; HttpOnly; Secure; SameSite=Strict; Max-Age=0$/);
  phone.cookie = old;
  assert.deepEqual(await phone.answer("/api/data"), [401, "login"], "the old cookie is dead on the door too");
  assert.equal((await phone.login(key)).status, 200);
  assert.equal((await (await Phone.on(w)).post("/api/logout")).status, 200, "signing out twice or without a session is harmless");
});

// ---------------------------------------------------------------- what the manager sees and is told
test("the panel shows who has the app open and when each subscriber was last seen", async () => {
  const { w, boss } = await setup();
  const { id, phone } = await signedIn(w, boss, { name: "Nadia" });
  clock += MIN;
  phone.geo = { city: "Karbala", country: "IQ" };
  await phone.get("/api/data");
  let st = await panel(boss);
  assert.deepEqual(st.online.map(o => [o.id, o.name, o.role, o.city, o.since]), [[id, "Nadia", "subscriber", "Karbala", clock]]);
  let sub = st.subs[0];
  assert.deepEqual([sub.online, sub.last_open, sub.last_seen, sub.city, sub.country, sub.devices[0].last_seen], [true, clock, clock, "Karbala", "IQ", clock]);

  clock += 6 * MIN;                           // still the same visit: seen again, not "opened" again
  await phone.get("/api/data");
  sub = await subOf(boss, id);
  assert.deepEqual([sub.last_open, sub.last_seen], [clock - 6 * MIN, clock]);
  clock += 151e3;
  sub = await subOf(boss, id);
  assert.equal(sub.online, false, "two and a half minutes without a refresh: not open any more");
  assert.deepEqual((await events(boss)).filter(e => e === "open").length, 1);

  const row = (await panel(boss)).log.find(e => e.event === "open");
  assert.deepEqual(row, { t: clock - 151e3 - 6 * MIN, event: "open", id, name: "Nadia", city: "Karbala", country: "IQ", device: "آيفون · Safari" });
});

test("the manager is told on Telegram about first sign-ins, other devices, wrong keys and visits", async () => {
  const { w, boss } = await setup({ TG_BOT_TOKEN: "TOKEN" });
  await w.storage.put("cfg:admin_chat", 99);
  telegram.length = 0;
  const { key } = await subscriber(boss, { name: "Ali <b>", max_devices: 2 });
  const phone = await Phone.on(w, { geo: { city: "Najaf", country: "IQ" } });
  await phone.login(key);
  await phone.get("/api/data");
  await (await Phone.on(w)).login(key);
  const third = await Phone.on(w);
  await third.login(key, { label: "<img src=x onerror=alert(1)>" });
  await third.login(key);
  const guesser = await Phone.on(w);
  for (let i = 0; i < 4; i++) await guesser.login(WRONG);
  await Promise.all(w.waits);

  const texts = telegram.map(m => m.text);
  assert.equal(texts.length, 5, texts.join("\n---\n"));
  assert.match(texts[0], /دخل بمفتاحه لأول مرة/);
  assert.match(texts[0], /النجف|Najaf، العراق/);
  assert.match(texts[1], /فتح التطبيق[\s\S]*شخص واحد/);
  assert.match(texts[2], /ربط جهازاً آخر بمفتاحه \(2 من 2/);
  assert.match(texts[3], /طلب دخول بمفتاح[\s\S]*لوحة المدير/);
  assert.match(texts[4], /ثلاث محاولات دخول بمفتاح خاطئ/);
  assert.ok(texts.every(t => t.includes("Ali &lt;b&gt;") || !t.includes("Ali")), "names are escaped");
  assert.ok(!texts.join("").includes("<img"), "and a device cannot name itself with markup");
  assert.ok(telegram.every(m => m.chat_id === 99 && m.parse_mode === "HTML" && m.to === "https://api.telegram.org/botTOKEN/sendMessage"));
  assert.ok(!texts.join("").includes(key));
});

test("linking the manager's Telegram works as before", async () => {
  const { w, boss } = await setup();
  assert.deepEqual((await boss.post("/api/admin/telegram")).json, { error: "no bot" });
  w.redeploy({ TG_BOT_TOKEN: "TOKEN", TG_SECRET: "hook-secret" });
  assert.equal((await panel(boss)).telegram, false);
  telegram.length = 0;
  const { link } = (await boss.post("/api/admin/telegram")).json;
  assert.match(link, /^https:\/\/t\.me\/rasad_test_bot\?start=[0-9a-f]{16}$/);
  assert.deepEqual([telegram[0].to, telegram[0].url, telegram[0].secret_token],
    ["https://api.telegram.org/botTOKEN/setWebhook", BASE + "/tg/hook-secret", "hook-secret"]);
  const update = (text, secret = "hook-secret", path = "/tg/hook-secret") => w.hub.fetch(new Request(BASE + path, { method: "POST",
    headers: { "x-telegram-bot-api-secret-token": secret, origin: "https://api.telegram.org" },
    body: JSON.stringify({ message: { text, chat: { id: 4242, type: "private" } } }) }));
  assert.equal((await update("/start " + link.split("=")[1], "guess")).status, 403);
  assert.equal((await update("/start " + link.split("=")[1], "hook-secret", "/tg/other")).status, 403);
  assert.equal((await update("/start wrong-code")).status, 200);
  assert.equal((await panel(boss)).telegram, false);
  assert.equal((await update("/start " + link.split("=")[1])).status, 200);
  assert.equal((await panel(boss)).telegram, true);
  assert.match(telegram[telegram.length - 1].text, /تم ربط إشعارات/);
  assert.equal(telegram[telegram.length - 1].chat_id, 4242);
});

// ---------------------------------------------------------------- the monitor's side
test("the monitor's routes work with its key only, and the monthly archive reaches signed-in subscribers", async () => {
  const { w, boss } = await setup();
  const routes = [["POST", "/api/ingest/data", "{}"], ["PUT", "/api/ingest/clip/a.jpg", "x"], ["GET", "/api/ingest/clips"],
    ["POST", "/api/ingest/keep", '{"clips":[]}'], ["PUT", "/api/ingest/archive/2026-11", "{}"]];
  for (const [method, url, body] of routes) {
    for (const key of ["", "wrong", INGEST_KEY + "x"]) {
      const r = await ingest(w, method, url, body, key);
      assert.deepEqual([r.status, await r.json()], [401, { error: "key" }], url);
    }
  }
  const { phone } = await signedIn(w, boss, { name: "Reader" });
  assert.equal((await phone.call("PUT", "/api/ingest/archive/2026-11", { stolen: true })).status, 401, "a session is not the monitor's key");

  // data and clips, as before
  let r = await ingest(w, "POST", "/api/ingest/data", '{"meta":{"n":1},"items":[]}');
  assert.deepEqual(await r.json(), { ok: true, size: 27 });
  assert.equal((await ingest(w, "POST", "/api/ingest/data", "not json")).status, 500);
  for (const name of ["a.mp4", "b.jpg", "c.png"]) assert.equal((await ingest(w, "PUT", "/api/ingest/clip/" + name, "bytes")).status, 200);
  assert.equal((await ingest(w, "PUT", "/api/ingest/clip/evil.html", "x")).status, 400);
  assert.deepEqual((await (await ingest(w, "GET", "/api/ingest/clips")).json()).clips.sort(), ["a.mp4", "b.jpg", "c.png"]);
  assert.deepEqual(await (await ingest(w, "POST", "/api/ingest/keep", '{"clips":["a.mp4"]}')).json(), { ok: true, removed: 2 });
  assert.equal((await phone.get("/clips/b.jpg")).status, 404);
  assert.deepEqual((await phone.get("/api/data")).json, { meta: { n: 1 }, items: [] });

  // the archive: one JSON file a month
  assert.deepEqual((await phone.get("/api/archive")).json, { months: [] });
  const october = JSON.stringify({ month: "2026-10", items: [{ id: "old-1", headline: "حريق" }] });
  r = await ingest(w, "PUT", "/api/ingest/archive/2026-10", october);
  assert.deepEqual([r.status, await r.json()], [200, { ok: true, size: Buffer.byteLength(october) }]);
  clock += 1000;
  const big = JSON.stringify({ month: "2026-11", pad: "ن".repeat(1_200_000) });      // more than one 2 MB row
  assert.equal((await ingest(w, "PUT", "/api/ingest/archive/2026-11", big)).status, 200);
  assert.equal((await ingest(w, "PUT", "/api/ingest/archive/2025-12", "[]")).status, 200);
  for (const [month, body] of [["2026-1", "{}"], ["nov", "{}"], ["2026-11x", "{}"], ["2026-11/x", "{}"], ["", "{}"], ["2026-12", "{oops"],
    ["2026-12", ""], ["2026-12", new Uint8Array([0x22, 0xff, 0x22])]]) {
    const bad = await ingest(w, "PUT", "/api/ingest/archive/" + month, body);
    assert.ok(bad.status === 400 || bad.status === 404, `${month} ${bad.status}`);
  }
  assert.equal((await ingest(w, "POST", "/api/ingest/archive/2026-12", "{}")).status, 404, "PUT only");

  const list = await phone.get("/api/archive");
  assert.equal(list.status, 200);
  assert.deepEqual(list.json, { months: [{ month: "2026-11", size: Buffer.byteLength(big), t: clock },
    { month: "2026-10", size: Buffer.byteLength(october), t: clock - 1000 }, { month: "2025-12", size: 2, t: clock }] });
  assert.equal(list.headers.get("x-proof-left"), "899");
  assert.deepEqual((await phone.get("/api/archive/2026-10")).json, JSON.parse(october));
  assert.deepEqual((await phone.get("/api/archive/2026-11")).json, JSON.parse(big));
  for (const month of ["2026-09", "2026-1", "x", "2026-10/extra"]) {
    const missing = await phone.get("/api/archive/" + month);
    assert.deepEqual([missing.status, missing.json], [404, { error: "not found" }], month);
  }
  assert.equal((await boss.get("/api/archive/2026-10")).status, 200, "the manager reads it too");

  // a month sent again replaces the old file, pieces and all
  assert.equal((await ingest(w, "PUT", "/api/ingest/archive/2026-11", '{"small":true}')).status, 200);
  assert.deepEqual((await phone.get("/api/archive/2026-11")).json, { small: true });
  assert.deepEqual(w.storage.keys("archive:2026-11").sort(), ["archive:2026-11#0", "archive:2026-11#meta"]);

  const nobody = await Phone.on(w);
  assert.deepEqual([await nobody.answer("/api/archive"), await nobody.answer("/api/archive/2026-10")], [[401, "login"], [401, "login"]]);
  clock += 16 * MIN;
  assert.deepEqual(await phone.answer("/api/archive/2026-10"), [401, "proof"], "the same proof rule as the data");
});

test("old leftovers are cleared while the monitor works: the log after 45 days, sessions whose cookie is gone", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Old" });
  for (let i = 0; i < 300; i++) await w.hub.log("failed", null, { city: "", country: "" }, "");
  assert.ok(w.storage.keys("log:").length > 300);
  clock += 46 * DAY;
  assert.equal((await ingest(w, "POST", "/api/ingest/keep", '{"clips":[]}')).status, 200);
  assert.equal(w.storage.keys("log:").length, 0);
  assert.equal(w.storage.keys("sess:").length, 2, "an idle session is kept as long as its cookie can live");
  assert.equal((await phone.prove()).status, 200, "and still works after a month and a half away");

  clock += 401 * DAY;
  assert.equal((await ingest(w, "POST", "/api/ingest/keep", '{"clips":[]}')).status, 200);
  assert.deepEqual(w.storage.keys("sess:").length, 0);
  assert.deepEqual(await phone.answer("/api/me"), [401, "login"]);
});

// ---------------------------------------------------------------- the app's files
test("which files are public is one short list; everything else is behind the door", () => {
  assert.deepEqual(PUBLIC_FILES, ["/", "/index.html", "/access.js", "/sw.js", "/manifest.webmanifest", "/favicon.ico"]);
  const open = ["/", "/index.html", "/access.js", "/sw.js", "/manifest.webmanifest", "/favicon.ico", "/icons/icon-192.png",
    "/icons/apple-touch-icon.png", "/icons/sub/x.png", "/brand-logo.png", "/brand-card.png", "/brand-mark.svg"];
  const closed = ["/imagery.js", "/beforeafter.js", "/data/incidents.json", "/data/meta.json", "/index.htm", "/Index.html", "/INDEX.HTML",
    "/access.js.map", "/access.js/", "/sw.js.bak", "/icons", "/icons/../imagery.js", "/icons/..%2fimagery.js", "/icons/%2e%2e/imagery.js",
    "/icons/a%20b.png", "/icons//x.png", "//icons/x.png", "/icons\\..\\imagery.js", "/brand-x/secret.js", "/brand-/../imagery.js", "/brandy.js",
    "/x/brand-logo.png", "/.env", "/worker.js", "", "icons/x.png", "/icons/ي.png"];
  for (const p of open) assert.equal(isPublicPath(p), true, p);
  for (const p of closed) assert.equal(isPublicPath(p), false, p);
});

test("the Worker serves public files to anyone and the app's tools only to a signed-in subscriber", async () => {
  const { w, boss } = await setup();
  const { id, phone } = await signedIn(w, boss, { name: "Viewer" });
  const served = [];
  const env = {
    HUB: { idFromName: name => name, get: name => ({ fetch: req => { assert.equal(name, "main"); return w.hub.fetch(req); } }) },
    ASSETS: { fetch: async req => {
      served.push(new URL(req.url).pathname);
      return new Response("file " + new URL(req.url).pathname, { headers: { "content-type": "text/javascript", "cache-control": "public, max-age=0, must-revalidate", etag: '"abc"' } });
    } },
  };
  const ask = async (url, who, cf) => {
    const req = who ? who.request("GET", url) : new Request(BASE + url);
    if (cf) Object.defineProperty(req, "cf", { value: cf });
    const res = await worker.fetch(req, env);
    const text = await res.text();
    return { status: res.status, headers: res.headers, text, json: (res.headers.get("content-type") || "").includes("json") ? JSON.parse(text) : null };
  };

  for (const url of ["/", "/index.html", "/access.js", "/sw.js", "/manifest.webmanifest", "/icons/icon-192.png", "/brand-logo.png"]) {
    const r = await ask(url);
    assert.deepEqual([r.status, r.text], [200, "file " + url], url);
    assert.deepEqual([r.headers.get("x-content-type-options"), r.headers.get("referrer-policy"), r.headers.get("x-frame-options")],
      ["nosniff", "no-referrer", "DENY"], url);
    assert.equal(r.headers.get("cache-control"), "public, max-age=0, must-revalidate");
    assert.equal(r.headers.get("etag"), '"abc"');
  }

  served.length = 0;
  for (const url of ["/imagery.js?v=17", "/beforeafter.js", "/data/incidents.json", "/icons/..%2fimagery.js", "/anything/else"]) {
    const r = await ask(url);
    assert.deepEqual([r.status, r.json], [401, { error: "login" }], url);
    assert.equal(r.headers.get("cache-control"), "no-store");
  }
  const forged = await Phone.on(w);
  forged.cookie = "a".repeat(64);
  assert.equal((await ask("/imagery.js", forged)).status, 401);
  assert.deepEqual(served, [], "the files were not even looked up");

  const tool = await ask("/imagery.js?v=17", phone);
  assert.deepEqual([tool.status, tool.text], [200, "file /imagery.js"]);
  assert.equal(tool.headers.get("cache-control"), "private, no-cache");
  assert.deepEqual([tool.headers.get("x-content-type-options"), tool.headers.get("x-frame-options")], ["nosniff", "DENY"]);

  // A returning subscriber's page loads its tools before it can sign anything, so the files ask
  // for the active subscription on its device, not for the 15-minute proof the data needs.
  clock += 16 * MIN;
  assert.equal((await ask("/imagery.js", phone)).status, 200);
  assert.deepEqual((await ask("/api/data", phone)).json.error, "proof");
  assert.equal((await boss.prove()).status, 200);
  await boss.post("/api/admin/sub/disable", { id, disabled: true });
  assert.deepEqual((await ask("/imagery.js", phone)).json, { error: "disabled" });
  await boss.post("/api/admin/sub/disable", { id, disabled: false });
  assert.deepEqual((await ask("/beforeafter.js", phone)).json, { error: "logout" });
  await boss.post("/api/admin/sub/update", { id, expires: clock - 1 });
  assert.equal((await phone.login((await boss.post("/api/admin/sub/rekey", { id })).json.key)).status, 403);
  assert.deepEqual((await ask("/imagery.js", phone)).json, { error: "expired" });

  // the door's own routes pass through, with the visitor's city from Cloudflare and never from the visitor
  assert.equal((await ask("/api/ping")).text, "ok");
  assert.deepEqual((await ask("/api/me")).json, { error: "login" });
  const liar = await Phone.on(w, { geo: { city: "Fake City", country: "XX" } });
  const req = liar.request("POST", "/api/login", { key: WRONG, pub: liar.pub, nonce: await liar.nonce(), sig: "x" });
  Object.defineProperty(req, "cf", { value: { city: "Mosul", country: "IQ" } });
  assert.equal((await worker.fetch(req, env)).status, 400);
  const nonce = await liar.nonce();
  const real = liar.request("POST", "/api/login", { key: WRONG, pub: liar.pub, nonce, sig: await liar.sign("rasad-login:" + nonce) });
  Object.defineProperty(real, "cf", { value: { city: "Mosul", country: "IQ" } });
  assert.equal((await worker.fetch(real, env)).status, 401);
  const failed = (await panel(boss)).log.find(e => e.event === "failed");
  assert.deepEqual([failed.city, failed.country], ["Mosul", "IQ"]);
});
