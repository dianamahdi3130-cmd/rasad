"use strict";

// The app's sign-in library (web/access.js) against the real door (worker/src/index.js). Each
// "browser" below is the library loaded afresh, with a stand-in for fetch (a cookie jar in front
// of the door) and for the place where the device's key pair is kept. No network.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test, before } = require("node:test");
const door = require("./support/door.js");

const { BASE, ADMIN_CODE } = door;
const MIN = 60e3, DAY = 86400e3;
const LIB = path.join(__dirname, "../web/access.js");
const WRONG = "RSD-AAAAA-BBBBB-CCCCC-DDDDD";
const tick = ms => new Promise(done => setTimeout(done, ms));
const until = async (what, ms = 2000) => { for (const end = Date.now() + ms; !what() && Date.now() < end;) await tick(5); return what(); };

let Hub, serverNormalize;
let clock = Date.parse("2026-11-15T09:00:00Z");          // the door's clock
before(async () => { ({ Hub, normalizeKey: serverNormalize } = await door.loadDoor(() => clock)); });
globalThis.fetch = () => { throw new Error("the tests reach nothing outside"); };

const remembered = new Map();                             // this origin's localStorage
Object.defineProperty(globalThis, "localStorage", { configurable: true,
  value: { getItem: k => (remembered.has(k) ? remembered.get(k) : null), setItem: (k, v) => { remembered.set(k, String(v)); } } });

let serial = 0;
function browser(w, opts = {}) {
  const b = { cookie: null, pair: null, offline: false, calls: [], locked: [], ip: `10.8.${++serial}.1`,
    ua: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36", ...opts };
  b.fetch = async (url, init = {}) => {
    b.calls.push(`${init.method || "GET"} ${url}`);
    if (b.hook) await b.hook(url, init);
    if (b.offline) throw new TypeError("Failed to fetch");
    if (b.hangs && b.hangs(url)) {         // the network swallows this one: only the caller's own time limit ends it
      await new Promise((_, fail) => init.signal && init.signal.addEventListener("abort", () => fail(init.signal.reason)));
    }
    assert.equal(init.credentials, "same-origin", "the cookie must go along");
    assert.equal(init.cache, "no-store");
    const headers = { ...init.headers, "cf-connecting-ip": b.ip, "x-geo": '{"city":"Basra","country":"IQ"}', "user-agent": b.ua };
    const cookie = b.sendOnce || b.cookie;                 // sendOnce: a request that left earlier, with the cookie of that time
    b.sendOnce = null;
    if (cookie) headers.cookie = "fs=" + cookie;
    const req = new Request(`${BASE}/${url}`, { method: init.method || "GET", headers, body: init.body });
    const res = await (b.host ? b.host(req) : w.hub.fetch(req));
    const set = res.headers.get("set-cookie");
    if (set) b.cookie = set.match(/^fs=([0-9a-f]*)/)[1] || null;
    return res;
  };
  // where this browser keeps the device's key pair (several tabs share it)
  b.store = opts.store || { pair: null, async get() { return this.pair; }, async add(rec) { if (this.pair) return false; this.pair = rec; return true; } };
  // The page is loaded (again): the library starts from nothing but what the browser kept.
  // realStorage: the library's own IndexedDB code instead of the test's simple store.
  b.open = (realStorage = false) => {
    delete require.cache[require.resolve(LIB)];
    b.app = require(LIB);
    b.app._use({ fetch: b.fetch, ...(realStorage ? {} : { store: b.store }), ...(b.budget ? { budget: b.budget } : {}) });
    b.app.onLocked = reason => b.locked.push(reason);
    return b.app;
  };
  b.open();
  return b;
}
async function setup() {                // a door, and the manager signed in on a browser of their own
  remembered.clear();
  const w = door.world(Hub);
  const boss = browser(w);
  assert.deepEqual(await boss.app.login(ADMIN_CODE), { ok: true, me: { id: "admin", name: "المدير", role: "admin", expires: null } });
  return { w, boss };
}
async function signedIn(w, boss, body) {
  const made = await boss.app.admin("sub/create", body);
  assert.equal(made.ok, true, JSON.stringify(made));
  const phone = browser(w);
  assert.equal((await phone.app.login(made.key)).ok, true);
  return { ...made, phone };
}

// ---------------------------------------------------------------- helpers and texts
test("the small helpers agree with the door", async () => {
  const { w, boss } = await setup();
  const { key, id, phone } = await signedIn(w, boss, { name: "Helper" });
  const samples = [key, key.toLowerCase(), key.replace(/-/g, " "), key.slice(4), " ‏" + key + "\n", key.replace(/\d/g, d => String.fromCharCode(0x660 + +d)),
    key.slice(0, -1), key + "X", "RSD-0000O-IIIII-11111-AAAAA", "", null, undefined, "hello", 12345];
  for (const s of samples) assert.equal(phone.app.normalizeKey(s), serverNormalize(s), JSON.stringify(s));
  assert.equal(phone.app.normalizeKey(key.toLowerCase().replace(/-/g, "")), key);

  const { b64url, canonicalJwk, thumbprint } = phone.app.util;
  assert.equal(b64url(new Uint8Array([251, 255, 254, 0, 1])), "-__-AAE");
  assert.equal(b64url(new Uint8Array([1, 2, 3]).buffer), "AQID");
  assert.equal(canonicalJwk({ y: "Y", ext: true, x: "X", kty: "EC", key_ops: ["verify"], crv: "P-256" }), '{"crv":"P-256","kty":"EC","x":"X","y":"Y"}');
  const shown = (await boss.app.admin("state")).subs.find(s => s.id === id).devices[0];
  assert.equal(await thumbprint(phone.store.pair.jwk), shown.id, "the device's name in the panel is the fingerprint of its public key");
  assert.equal(shown.label, "جهاز · متصفح", "the label the library sent (Node is no phone)");
  assert.equal(phone.app.deviceLabel(), "جهاز · متصفح");
});

test("every answer of the door has an Arabic sentence, and every event of the log a name", async () => {
  const { w, boss } = await setup();
  const app = boss.app, unknown = app.errorText("something else");
  assert.equal(unknown, "تعذّر الدخول. حاول مرة أخرى.");
  const source = fs.readFileSync(path.join(__dirname, "../worker/src/index.js"), "utf8");
  const machine = ["key", "json", "month", "not found"];                 // answers only the monitor or a mistyped address gets
  const codes = [...new Set([...source.matchAll(/error: "([^"]+)"/g)].map(m => m[1]))].filter(c => !machine.includes(c));
  assert.ok(codes.length >= 15, codes.join());
  for (const code of [...codes, "refused", "unsupported", "storage", "network", "logout", "login", "expired"]) {
    const text = app.errorText(code);
    assert.ok(text !== unknown && /[؀-ۿ]/.test(text) && !/[a-z]{4,}/.test(text.replace(/Safari|Chrome/g, "")), code + ": " + text);
  }
  assert.equal(new Set(codes.map(app.errorText)).size, codes.length, "no two answers share a sentence");

  // one of everything the manager's log can hold
  const { id, key, phone } = await signedIn(w, boss, { name: "Everything", days: 5 });
  await phone.app.fetch("api/data");
  await browser(w).app.login(WRONG);
  const second = browser(w), third = browser(w);
  await second.app.login(key);
  await second.app.login(key);
  const rid = () => boss.app.admin("state").then(st => st.subs[0].pending[0].rid);
  await boss.app.admin("sub/reject", { id, rid: await rid() });
  await third.app.login(key);
  await boss.app.admin("sub/approve", { id, rid: await rid() });
  await boss.app.admin("sub/update", { id, note: "x" });
  await boss.app.admin("sub/disable", { id, disabled: true });
  await third.app.login(key);
  await boss.app.admin("sub/disable", { id, disabled: false });
  await boss.app.admin("sub/logout", { id });
  const rekeyed = await boss.app.admin("sub/rekey", { id });
  await boss.app.admin("sub/unbind", { id });
  await boss.app.admin("sub/update", { id, expires: clock - 1 });
  await third.app.login(rekeyed.key);
  await boss.app.admin("sub/delete", { id });
  const seen = [...new Set((await boss.app.admin("state")).log.map(e => e.event))].sort();
  assert.deepEqual(seen, ["approved", "blocked", "created", "deleted", "disabled", "enabled", "expired", "failed", "forced_logout", "login",
    "open", "refused", "rejected", "rekeyed", "requested", "unbound", "updated"]);
  for (const event of seen) assert.ok(/[؀-ۿ]/.test(app.eventText(event)), event);
  assert.equal(app.eventText("brand_new"), "brand_new");
  assert.deepEqual(["active", "waiting", "expired", "disabled"].map(app.statusText), ["فعّال", "بانتظار أول دخول", "منتهٍ", "موقوف"]);
});

// ---------------------------------------------------------------- opening the app
test("boot tells the open site from the private app, and who is signed in", async () => {
  const { w, boss } = await setup();
  remembered.clear();

  // a static host has no door: a 404 page, or the same page for every address
  const pages = browser(w, { host: async () => new Response("<html>404</html>", { status: 404, headers: { "content-type": "text/html" } }) });
  assert.deepEqual(await pages.app.boot(), { mode: "public" });
  const spa = browser(w, { host: async () => new Response("<html>app</html>", { headers: { "content-type": "text/html" } }) });
  assert.deepEqual(await spa.app.boot(), { mode: "public" });
  const down = browser(w, { offline: true });
  assert.deepEqual(await down.app.boot(), { mode: "public" }, "no network on an address never seen as private: the open site's saved copy");
  assert.deepEqual([pages.locked, spa.app.me, [...remembered]], [[], null, []]);

  // the private app, nobody signed in
  const phone = browser(w);
  assert.deepEqual(await phone.app.boot(), { mode: "private", me: null, reason: "login" });
  assert.deepEqual(phone.locked, [], "boot answers by its result, not through onLocked");
  assert.deepEqual(phone.calls, ["GET api/me"]);

  const made = await boss.app.admin("sub/create", { name: "Boot", days: 3 });
  const me = { id: made.id, name: "Boot", role: "subscriber", expires: clock + 3 * DAY };
  assert.deepEqual(await phone.app.login("  " + made.key.toLowerCase() + " "), { ok: true, me });
  assert.deepEqual(phone.app.me, me);

  // the page is opened again later: still signed in, after proving itself without a word
  phone.open();
  assert.equal(phone.app.me, null);
  clock += 2 * 60 * MIN;
  assert.deepEqual(await phone.app.boot(), { mode: "private", me });
  assert.deepEqual(phone.calls.slice(-3), ["GET api/me", "POST api/prove", "GET api/me"], "the number to sign came with the door's answer");
  assert.deepEqual(phone.app.me, me);

  // once an address has been the private app, a door out of reach is never taken for the open site
  phone.open();
  phone.offline = true;
  assert.deepEqual(await phone.app.boot(), { mode: "private", me: null, reason: "network" });
  phone.offline = false;
  phone.host = async () => new Response("error code: 1101", { status: 500 });
  assert.deepEqual(await phone.app.boot(), { mode: "private", me: null, reason: "server" });
  phone.host = null;
  assert.deepEqual(await phone.app.boot(), { mode: "private", me });

  // the end of the period, found at the door
  clock += 3 * DAY;
  phone.open();
  assert.deepEqual(await phone.app.boot(), { mode: "private", me: null, reason: "expired" });
  assert.deepEqual(phone.locked, []);
});

test("signing in: each refusal comes back as a code with its sentence", async () => {
  const { w, boss } = await setup();
  const { id, key } = await signedIn(w, boss, { name: "Codes" });
  const other = browser(w);
  assert.deepEqual(await other.app.login(""), { ok: false, error: "missing" });
  assert.deepEqual(await other.app.login("   "), { ok: false, error: "missing" });
  assert.deepEqual(other.calls, [], "nothing is sent for an empty field");
  assert.deepEqual(await other.app.login(WRONG), { ok: false, error: "wrong" });
  assert.deepEqual(other.calls, ["POST api/nonce", "POST api/login"]);
  assert.deepEqual(await other.app.login(key), { ok: false, error: "device" });
  assert.match(other.app.errorText("device"), /أُرسل طلب إلى المدير/);
  const rid = (await boss.app.admin("state")).subs[0].pending[0].rid;
  await boss.app.admin("sub/reject", { id, rid });
  assert.deepEqual(await other.app.login(key), { ok: false, error: "refused" }, "the manager said no: nothing was sent this time");
  assert.equal(other.app.me, null);

  await boss.app.admin("sub/disable", { id, disabled: true });
  assert.deepEqual(await other.app.login(key), { ok: false, error: "disabled" });
  await boss.app.admin("sub/update", { id, expires: clock - 1 });
  await boss.app.admin("sub/disable", { id, disabled: false });
  assert.deepEqual(await other.app.login(key), { ok: false, error: "expired" });

  other.offline = true;
  assert.deepEqual(await other.app.login(key), { ok: false, error: "network" });
  other.offline = false;
  for (let i = 0; i < 4; i++) await other.app.login(WRONG);
  assert.deepEqual(await other.app.login(WRONG), { ok: false, error: "wait" });
  assert.deepEqual(other.locked, [], "a refused sign-in is an answer, not a lock");

  // a number that ran out on a slow network is asked for again by itself
  const slow = browser(w);
  let delayed = 0;
  slow.hook = url => { if (url === "api/login" && !delayed++) clock += 2 * MIN + 1; };
  const { key: fresh } = await boss.app.admin("sub/create", { name: "Slow line" });
  assert.equal((await slow.app.login(fresh)).ok, true);
  assert.deepEqual(slow.calls, ["POST api/nonce", "POST api/login", "POST api/nonce", "POST api/login"]);

  // a browser without the needed parts, or one that cannot keep the key pair
  const saved = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: {} });
  try { assert.deepEqual(await browser(w).app.login(key), { ok: false, error: "unsupported" }); }
  finally { Object.defineProperty(globalThis, "crypto", saved); }
  const broken = browser(w);
  broken.store.get = async () => { throw new Error("QuotaExceededError"); };
  assert.deepEqual(await broken.app.login(key), { ok: false, error: "storage" });
  const readOnly = browser(w);
  readOnly.store.add = async () => { throw new Error("blocked"); };
  assert.deepEqual(await readOnly.app.login(key), { ok: false, error: "storage" });
  assert.deepEqual([broken.calls, readOnly.calls], [[], []], "the key is not sent from a browser that cannot keep its identity");
});

// ---------------------------------------------------------------- staying signed in
test("fetch proves the device when the door asks, and before the 15 minutes run out", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Fetcher" });
  let res = await phone.app.fetch("api/data?t=1");
  assert.deepEqual([res.status, await res.json()], [200, { meta: null, items: [] }]);

  // away for an hour: the first request is refused for proof, proved, and asked again, all inside fetch
  clock += 60 * MIN;
  phone.calls.length = 0;
  let cookie = phone.cookie;
  res = await phone.app.fetch("api/data?t=2");
  assert.equal(res.status, 200);
  assert.deepEqual(phone.calls, ["GET api/data?t=2", "POST api/prove", "GET api/data?t=2"]);
  assert.notEqual(phone.cookie, cookie, "with a new cookie");
  assert.deepEqual(phone.locked, []);

  // while the app is in use: with less than five minutes left it proves again on its own
  clock += 9 * MIN;
  phone.calls.length = 0;
  cookie = phone.cookie;
  assert.equal((await phone.app.fetch("api/data")).status, 200);
  await tick(20);
  assert.deepEqual(phone.calls, ["GET api/data"], "six minutes are left: nothing to do");
  clock += 2 * MIN;
  assert.equal((await phone.app.fetch("api/data")).status, 200);
  assert.ok(await until(() => phone.cookie !== cookie), "renewed in the background");
  assert.deepEqual(phone.calls, ["GET api/data", "GET api/data", "POST api/nonce", "POST api/prove"]);
  assert.equal(await phone.app.prove(), true, "and by hand");

  // a POST with a body is sent again whole, once
  clock += 20 * MIN;
  const made = await boss.app.admin("sub/create", { name: "After a pause" });
  assert.equal(made.ok, true);
  assert.deepEqual(boss.calls.slice(-3), ["POST api/admin/sub/create", "POST api/prove", "POST api/admin/sub/create"]);
  assert.equal((await boss.app.admin("state")).subs.length, 2, "made once, not twice");

  // no network while proving is not the end of the session
  clock += 20 * MIN;
  phone.hook = url => { if (url === "api/prove") throw new TypeError("Failed to fetch"); };
  assert.equal((await phone.app.fetch("api/data")).status, 401);
  assert.deepEqual([phone.locked, phone.app.me.name], [[], "Fetcher"]);
  phone.hook = null;
  assert.equal((await phone.app.fetch("api/data")).status, 200);
  phone.offline = true;
  await assert.rejects(phone.app.fetch("api/data"), TypeError, "like fetch itself");
});

test("a sign-out by the manager, a stopped key or the end of the period close the app within the watch time", async () => {
  const { w, boss } = await setup();
  const cases = [
    ["logout", id => boss.app.admin("sub/logout", { id })],
    ["disabled", id => boss.app.admin("sub/disable", { id, disabled: true })],
    ["expired", id => boss.app.admin("sub/update", { id, expires: clock - 1 })],
    ["logout", id => boss.app.admin("sub/unbind", { id })],
    ["logout", id => boss.app.admin("sub/rekey", { id })],
    ["login", id => boss.app.admin("sub/delete", { id })],
  ];
  const people = [];
  for (const [i] of cases.entries()) people.push(await signedIn(w, boss, { name: "Watched " + i }));
  await Promise.all(cases.map(async ([reason, act], i) => {       // six open apps at once, each watching the door
    const { id, phone } = people[i];
    const stop = phone.app.watch(0.045);
    assert.ok(await until(() => phone.calls.includes("GET api/me")), "it does ask");
    assert.deepEqual(phone.locked, [], "nothing happens while all is well");
    assert.equal((await act(id)).ok, true);
    assert.ok(await until(() => phone.locked.length > 0), reason);
    assert.deepEqual(phone.locked, [reason]);
    assert.equal(phone.app.me, null);
    const asked = phone.calls.length;
    await tick(120);
    assert.equal(phone.calls.length, asked, "a closed app stops asking");
    assert.deepEqual(phone.locked, [reason], "and says it once");
    stop();
  }));

  // a request of the page itself finds out just the same, and the page still gets the door's answer
  const { id, phone } = await signedIn(w, boss, { name: "Polling" });
  await boss.app.admin("sub/logout", { id });
  const res = await phone.app.fetch("api/data");
  assert.deepEqual([res.status, await res.json(), phone.locked, phone.app.me], [401, { error: "logout" }, ["logout"], null]);

  // the watch does not close anything because the network is away
  const flaky = await signedIn(w, boss, { name: "Tunnel" });
  const end = flaky.phone.app.watch(0.045);
  flaky.phone.offline = true;
  await tick(150);
  flaky.phone.offline = false;
  assert.deepEqual([flaky.phone.locked, flaky.phone.app.me.name], [[], "Tunnel"]);
  end();
});

test("the watch rests while the app is in the background and asks at once when it comes back", async () => {
  const { w, boss } = await setup();
  const { id, phone } = await signedIn(w, boss, { name: "Pocket" });
  const page = { hidden: true, on: {}, addEventListener(type, fn) { this.on[type] = fn; }, removeEventListener(type) { delete this.on[type]; } };
  globalThis.document = page;
  try {
    const stop = phone.app.watch(0.045);
    phone.calls.length = 0;
    await tick(150);
    assert.deepEqual(phone.calls, [], "in the pocket: no questions");
    await boss.app.admin("sub/logout", { id });
    page.hidden = false;
    page.on.visibilitychange();
    assert.ok(await until(() => phone.locked.length > 0, 1000));
    assert.deepEqual(phone.locked, ["logout"]);
    stop();
    assert.deepEqual(page.on, {}, "stopping takes the listener away");
  } finally { delete globalThis.document; }
});

test("a question to the door that never gets an answer is given up after a while, and the app goes on", async () => {
  const { w, boss } = await setup();
  const made = await boss.app.admin("sub/create", { name: "Stalled" });
  const phone = browser(w, { budget: 60 });               // 60 ms here; 20 seconds in the app
  assert.equal((await phone.app.login(made.key)).ok, true);
  const awake = setInterval(() => {}, 20);                // (Node would otherwise stop while only a time limit is pending)
  try {
    let swallowed = 0;
    phone.hangs = url => url === "api/me" && swallowed++ === 0;
    const stop = phone.app.watch(0.045);
    assert.ok(await until(() => phone.calls.filter(c => c === "GET api/me").length >= 2), "the watch asked again after the lost question");
    assert.deepEqual([phone.locked, phone.app.me.name], [[], "Stalled"]);
    await boss.app.admin("sub/logout", { id: made.id });
    assert.ok(await until(() => phone.locked.length > 0), "and still notices a sign-out");
    stop();

    // the same for a proof, a sign-out and a sign-in
    const again = browser(w, { budget: 60, store: phone.store });
    assert.equal((await again.app.login(made.key)).ok, true);
    clock += 20 * MIN;
    swallowed = 0;
    again.hangs = url => url === "api/prove" && swallowed++ === 0;
    assert.equal((await again.app.fetch("api/data")).status, 401);
    assert.deepEqual(again.locked, [], "a proof that got no answer is not the end of the session");
    assert.equal((await again.app.fetch("api/data")).status, 200);
    again.hangs = url => url === "api/logout";
    assert.deepEqual(await again.app.logout(), { ok: false, error: "network" });
    assert.equal(again.app.me.name, "Stalled");
    const stuck = browser(w, { budget: 60, hangs: url => url === "api/nonce" });
    assert.deepEqual(await stuck.app.login(made.key), { ok: false, error: "network" });
  } finally { clearInterval(awake); }
});

test("the end of the period is noticed when its time comes, not at the next slow poll", async () => {
  const { w, boss } = await setup();
  const start = Date.now(), base = clock;
  Hub.now = () => base + (Date.now() - start);            // for this test the door's clock runs
  try {
    const { phone } = await signedIn(w, boss, { name: "Ends soon", expires: Hub.now() + 300 });
    const stop = phone.app.watch(3600);                    // by its own rhythm it would ask the door in 40 minutes
    assert.ok(await until(() => phone.locked.length > 0, 4000), "closed when the time came");
    stop();
    assert.deepEqual(phone.locked, ["expired"]);
    assert.deepEqual(phone.calls.slice(2), ["GET api/me"], "one question, asked because the period was over");
  } finally { Hub.now = () => clock; }
});

test("a copy of the cookie in another browser locks itself out; the real device carries on", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Owner" });
  const copy = browser(w);
  copy.cookie = phone.cookie;
  assert.deepEqual((await copy.app.boot()).me.name, "Owner", "inside the 15 minutes the copy still opens");
  clock += 16 * MIN;
  const res = await copy.app.fetch("api/data");
  assert.equal(res.status, 401);
  assert.deepEqual(copy.locked, ["proof"], "it cannot sign as the owner's device");
  assert.equal(copy.app.me, null);
  assert.deepEqual(await copy.app.boot(), { mode: "private", me: null, reason: "proof" });
  assert.equal((await phone.app.fetch("api/data")).status, 200);
  assert.deepEqual(phone.locked, []);
});

test("an answer to a request sent just before the cookie was renewed does not sign anyone out", async () => {
  const { w, boss } = await setup();
  const { phone } = await signedIn(w, boss, { name: "Racer" });
  const first = phone.cookie;
  clock += 11 * MIN;
  assert.equal(await phone.app.prove(), true);
  clock += 11 * MIN;
  assert.equal(await phone.app.prove(), true);     // the first cookie is gone at the door by now
  phone.calls.length = 0;
  phone.sendOnce = first;                          // a request that left with it long ago arrives only now
  const res = await phone.app.fetch("api/data");
  assert.equal(res.status, 200);
  assert.deepEqual(phone.calls, ["GET api/data", "GET api/me", "GET api/data"], "the door is asked who is signed in, then the request goes again");
  assert.deepEqual(phone.locked, []);

  // the same for the watch's own question
  const stop = phone.app.watch(0.05);
  phone.calls.length = 0;
  phone.sendOnce = first;
  assert.ok(await until(() => phone.calls.length >= 2), "the stale answer arrived and the door was asked once more");
  await tick(30);
  stop();
  assert.deepEqual([phone.sendOnce, phone.locked, phone.app.me.name], [null, [], "Racer"]);
});

// ---------------------------------------------------------------- the manager, signing out, the key pair
test("the manager's calls go through one function, and it is no use to a subscriber", async () => {
  const { w, boss } = await setup();
  const { id, phone } = await signedIn(w, boss, { name: "Member", days: 7 });
  const st = await boss.app.admin("state");
  assert.deepEqual([st.subs.length, st.subs[0].name, st.subs[0].status, st.now], [1, "Member", "active", clock]);
  assert.equal((await boss.app.admin("/sub/update", { id, add_days: 3 })).sub.expires, clock + 10 * DAY);
  assert.deepEqual(boss.calls.slice(-2), ["GET api/admin/state", "POST api/admin/sub/update"]);
  assert.deepEqual(await boss.app.admin("sub/create", { name: "Member" }), { error: "exists" });
  assert.deepEqual(await boss.app.admin("sub/create", {}), { error: "name" });

  assert.deepEqual(await phone.app.admin("state"), { error: "forbidden" });
  assert.deepEqual(await phone.app.admin("sub/delete", { id }), { error: "forbidden" });
  assert.deepEqual(phone.locked, [], "refused, not signed out");
  assert.deepEqual(await browser(w).app.admin("state"), { error: "login" });
  boss.offline = true;
  assert.deepEqual(await boss.app.admin("state"), { error: "network" });
});

test("signing out ends the session; the device keeps its identity and the key works there again", async () => {
  const { w, boss } = await setup();
  const { id, key, phone } = await signedIn(w, boss, { name: "Leaver" });
  const pair = phone.store.pair;
  phone.offline = true;
  assert.deepEqual(await phone.app.logout(), { ok: false, error: "network" });
  assert.equal(phone.app.me.name, "Leaver", "still signed in: the door was not told");
  phone.offline = false;
  assert.deepEqual(await phone.app.logout(), { ok: true });
  assert.deepEqual([phone.app.me, phone.cookie, phone.locked], [null, null, []]);
  assert.deepEqual(await phone.app.boot(), { mode: "private", me: null, reason: "login" });
  assert.equal((await phone.app.login(key)).ok, true);
  assert.equal(phone.store.pair, pair);
  const sub = (await boss.app.admin("state")).subs.find(s => s.id === id);
  assert.deepEqual([sub.devices.length, sub.pending.length], [1, 0]);
});

test("the device's key pair is made once, cannot be exported, and nothing else is kept in the browser", async () => {
  const { w, boss } = await setup();
  const { key, phone } = await signedIn(w, boss, { name: "Keeper" });
  const pair = phone.store.pair;
  assert.deepEqual(Object.keys(pair).sort(), ["created", "id", "jwk", "privateKey", "publicKey"]);
  assert.deepEqual([pair.id, pair.privateKey.extractable, pair.privateKey.type, pair.privateKey.algorithm.name, pair.privateKey.algorithm.namedCurve],
    ["device", false, "private", "ECDSA", "P-256"]);
  await assert.rejects(crypto.subtle.exportKey("jwk", pair.privateKey));
  await assert.rejects(crypto.subtle.exportKey("pkcs8", pair.privateKey));
  assert.deepEqual(Object.keys(pair.jwk), ["crv", "kty", "x", "y"], "only the public half is ever sent");
  assert.ok(!JSON.stringify([...remembered]).includes(key) && !JSON.stringify(pair.jwk).includes(key), "the access key is not kept");
  assert.deepEqual([...remembered], [["rasad-door", "1"]]);

  // opened again: the same pair, so the same device
  phone.open();
  await phone.app.boot();
  assert.equal((await phone.app.login(key)).ok, true);
  assert.equal(phone.store.pair, pair);

  // two tabs opened together still end up with one pair
  const tabA = browser(w), tabB = browser(w, { store: tabA.store });
  const { id, key: shared } = await boss.app.admin("sub/create", { name: "Two tabs" });
  assert.deepEqual((await Promise.all([tabA.app.login(shared), tabB.app.login(shared)])).map(r => r.ok), [true, true]);
  const sub = (await boss.app.admin("state")).subs.find(s => s.id === id);
  assert.deepEqual([sub.devices.length, sub.pending.length], [1, 0]);
});

test("the key pair is kept in IndexedDB \"rasad-access\", store \"keys\", id \"device\"", async () => {
  const { w, boss } = await setup();
  const { key } = await boss.app.admin("sub/create", { name: "Real storage" });
  const idb = fakeIndexedDB();
  Object.defineProperty(globalThis, "indexedDB", { configurable: true, value: idb });
  const own = true;                           // the library's own storage this time, not the test's
  try {
    const phone = browser(w);
    phone.open(own);
    assert.equal((await phone.app.login(key)).ok, true);
    assert.deepEqual([...idb.dbs.keys()], ["rasad-access"]);
    const kept = idb.dbs.get("rasad-access").stores.get("keys");
    assert.deepEqual([...kept.keys()], ["device"]);
    assert.equal(kept.get("device").privateKey.extractable, false);
    assert.equal(idb.handles, 0, "every connection is closed again");

    // a new page load reads the same pair back and can still sign with it
    phone.open(own);
    assert.equal((await phone.app.boot()).me.name, "Real storage");
    clock += 20 * MIN;
    assert.equal((await phone.app.fetch("api/data")).status, 200);

    // a second tab that lost the race for the first write adopts the pair that is there
    idb.hideOnce = true;
    const late = browser(w);
    late.open(own);
    assert.equal((await late.app.login(key)).ok, true, "same pair, same device: no approval needed");
    assert.deepEqual([kept.size, idb.handles], [1, 0]);

    idb.broken = true;                         // private browsing on some phones: the storage refuses
    const priv = browser(w);
    priv.open(own);
    assert.deepEqual(await priv.app.login(key), { ok: false, error: "storage" });
  } finally { delete globalThis.indexedDB; }
  const bare = browser(w);
  bare.open(own);
  assert.deepEqual(await bare.app.login(key), { ok: false, error: "unsupported" }, "a browser without IndexedDB");
});

// A small IndexedDB: enough of the real one's manners (everything answers later, through events;
// a duplicate id is a ConstraintError that aborts the transaction unless its event is stopped).
function fakeIndexedDB() {
  const idb = { dbs: new Map(), handles: 0, hideOnce: false, broken: false };
  idb.open = (name, version) => {
    assert.equal(version, 1);
    const rq = { result: undefined, error: null };
    setImmediate(() => {
      if (idb.broken) { rq.error = new Error("The operation is insecure"); rq.onerror && rq.onerror({}); return; }
      let db = idb.dbs.get(name);
      const fresh = !db;
      if (fresh) idb.dbs.set(name, (db = { stores: new Map() }));
      idb.handles++;
      rq.result = {
        objectStoreNames: { contains: n => db.stores.has(n) },
        createObjectStore(n, opts) { assert.ok(fresh, "only while upgrading"); assert.deepEqual(opts, { keyPath: "id" }); db.stores.set(n, new Map()); },
        close() { idb.handles--; },
        transaction(n, mode) {
          const rows = db.stores.get(n), tx = { error: null }, jobs = [];
          let failed = false;
          const request = work => {
            const r = { result: undefined, error: null };
            jobs.push(() => {
              try { r.result = work(); r.onsuccess && r.onsuccess({}); }
              catch (error) {
                let stopped = false;
                r.error = error;
                r.onerror && r.onerror({ preventDefault() { stopped = true; } });
                if (!stopped) { failed = true; tx.error = error; }
              }
            });
            return r;
          };
          tx.objectStore = () => ({
            get: id => request(() => {
              if (idb.hideOnce) { idb.hideOnce = false; return undefined; }    // the other tab has not written yet
              return rows.has(id) ? structuredClone(rows.get(id)) : undefined;
            }),
            add: value => request(() => {
              assert.equal(mode, "readwrite");
              if (rows.has(value.id)) throw Object.assign(new Error("Key already exists"), { name: "ConstraintError" });
              rows.set(value.id, structuredClone(value));
              return value.id;
            }),
          });
          setImmediate(() => { jobs.forEach(job => job()); setImmediate(() => (failed ? tx.onabort && tx.onabort({}) : tx.oncomplete && tx.oncomplete({}))); });
          return tx;
        },
      };
      if (fresh && rq.onupgradeneeded) rq.onupgradeneeded({});
      rq.onsuccess && rq.onsuccess({});
    });
    return rq;
  };
  return idb;
}
