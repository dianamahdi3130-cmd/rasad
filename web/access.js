// Rasad: the sign-in side of the private app (subscriber access keys).
//
// The page loads this file first and talks to the door (worker/src/index.js) only through it:
//   const st = await RasadAccess.boot();           // {mode:"public"} on the open site, else {mode:"private", me|null, reason}
//   const r  = await RasadAccess.login(typedKey);  // {ok:true, me} or {ok:false, error}: show RasadAccess.errorText(r.error)
//   RasadAccess.onLocked = reason => …;            // signed out by the manager, the key ran out, …: hide the data, show the gate
//   RasadAccess.watch();                           // notices that within half a minute, even when no request is due
//   const res = await RasadAccess.fetch("api/data");   // like fetch; proves this device again when the door asks
//
// This device's identity is a key pair made by the browser and kept in its own storage (IndexedDB
// "rasad-access"). No script, this one included, can read or copy the private half: it can only
// sign. The access key itself is never stored here. Design and limits: docs/plans/access-keys.md
(function (root) {
  "use strict";

  const RENEW_UNDER = 300;               // prove again when fewer than this many seconds are left
  const LOCKS = ["login", "logout", "disabled", "expired", "proof"];    // reasons that close the app
  const TEXT = {
    missing: "أدخل مفتاح الاشتراك.",
    wrong: "المفتاح غير صحيح. تأكد منه ثم أعد المحاولة.",
    disabled: "هذا الاشتراك موقوف. راجع المدير.",
    expired: "انتهت مدة هذا الاشتراك. راجع المدير لتجديده.",
    device: "هذا المفتاح مرتبط بجهاز أو متصفح آخر. أُرسل طلب إلى المدير، وبعد موافقته أدخل المفتاح مرة أخرى هنا.",
    refused: "رفض المدير استخدام هذا المفتاح على هذا الجهاز أو المتصفح. راجع المدير.",
    wait: "محاولات كثيرة. حاول بعد ربع ساعة.",
    proof: "تعذّر التحقق من هذا الجهاز. أدخل مفتاح الاشتراك من جديد.",
    login: "أدخل مفتاح الاشتراك للدخول.",
    logout: "أنهى المدير هذه الجلسة. أدخل مفتاح الاشتراك للدخول من جديد.",
    forbidden: "هذه الصفحة للمدير فقط.",
    unsupported: "هذا المتصفح لا يدعم الدخول الآمن. حدّثه، أو افتح التطبيق في Safari أو Chrome خارج التصفح الخاص.",
    storage: "تعذّر حفظ هوية هذا الجهاز في المتصفح. أغلق التطبيق وافتحه من جديد، وتأكد أنك خارج التصفح الخاص.",
    network: "لا اتصال بالإنترنت. أعد المحاولة.",
    server: "تعذّر الاتصال بالمنصة الآن. حاول بعد قليل.",
    // answers of the manager's panel
    name: "اكتب اسم المشترك.",
    exists: "يوجد مشترك بهذا الاسم. أضف إليه ما يميّزه.",
    days: "عدد الأيام غير صالح.",
    expires: "تاريخ الانتهاء غير صالح.",
    "no such subscriber": "هذا المشترك لم يعد موجوداً.",
    "no such device": "هذا الجهاز لم يعد مرتبطاً بالمفتاح.",
    "no such request": "هذا الطلب لم يعد قائماً.",
    "no bot": "لم يُجهَّز بوت تلكرام للإشعارات بعد.",
  };
  const EVENTS = {                       // the manager's log
    login: "دخل", open: "فتح التطبيق", failed: "مفتاح خاطئ", blocked: "محاولة من جهاز آخر (رُفضت)",
    requested: "طلب دخول من جهاز جديد", approved: "وافق المدير على جهازه الجديد", rejected: "رفض المدير جهازه الجديد",
    created: "أُنشئ مفتاحه", rekeyed: "مفتاح جديد", updated: "عُدّل اشتراكه", disabled: "أُوقف", enabled: "أُعيد تفعيله",
    expired: "محاولة دخول بعد انتهاء المدة", refused: "محاولة دخول والاشتراك موقوف", forced_logout: "أخرجه المدير",
    unbound: "فُكّ ارتباط جهازه", deleted: "حُذف",
  };
  const STATUS = { active: "فعّال", waiting: "بانتظار أول دخول", expired: "منتهٍ", disabled: "موقوف" };

  // ---------- small helpers with no browser in them (the tests use them too)
  const KEY_ABC = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  function normalizeKey(text) {          // however it was typed or pasted -> "RSD-XXXXX-XXXXX-XXXXX-XXXXX"; "" when it is not a key
    let s = String(text == null ? "" : text).toUpperCase()
      .replace(/[٠-٩]/g, d => String(d.charCodeAt(0) - 0x660))
      .replace(/[۰-۹]/g, d => String(d.charCodeAt(0) - 0x6f0))
      .replace(/[^A-Z0-9]/g, "");
    if (s.length === 23 && s.startsWith("RSD")) s = s.slice(3);
    if (s.length !== 20 || [...s].some(c => !KEY_ABC.includes(c))) return "";
    return "RSD-" + s.match(/.{5}/g).join("-");
  }
  function b64url(bytes) {
    let bin = "";
    for (const b of new Uint8Array(bytes)) bin += String.fromCharCode(b);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  // the public half of the key pair, its members in the one agreed order
  const canonicalJwk = jwk => JSON.stringify({ crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y });
  async function thumbprint(jwk) {       // the device's name in the manager's panel: SHA-256 of the canonical public key
    const hash = await subtle().digest("SHA-256", new TextEncoder().encode(canonicalJwk(jwk)));
    return [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, "0")).join("");
  }
  const errorText = code => TEXT[code] || "تعذّر الدخول. حاول مرة أخرى.";
  const eventText = code => EVENTS[code] || String(code || "");
  const statusText = code => STATUS[code] || String(code || "");
  const fault = reason => Object.assign(new Error(reason), { reason });

  function deviceLabel() {               // how this device is named to the manager
    const nav = root.navigator || {}, ua = nav.userAgent || "";
    const os = /iPhone/.test(ua) ? "آيفون" : /iPad/.test(ua) || (/Macintosh/.test(ua) && nav.maxTouchPoints > 1) ? "آيباد"
      : /Android/.test(ua) ? "أندرويد" : /Windows/.test(ua) ? "ويندوز" : /Mac OS X/.test(ua) ? "ماك" : /Linux/.test(ua) ? "لينكس" : "جهاز";
    let installed = nav.standalone === true;
    try { installed = installed || root.matchMedia("(display-mode: standalone)").matches; } catch (e) {}
    const browser = installed ? "التطبيق المثبّت" : /Telegram/i.test(ua) ? "متصفح تلكرام" : /Edg/.test(ua) ? "Edge"
      : /SamsungBrowser/.test(ua) ? "Samsung" : /FxiOS|Firefox/.test(ua) ? "Firefox" : /CriOS|Chrome/.test(ua) ? "Chrome"
      : /Safari/.test(ua) ? "Safari" : "متصفح";
    return os + " · " + browser;
  }

  // ---------- where the key pair lives: IndexedDB "rasad-access", store "keys", id "device"
  const TAKEN = {};
  const idb = {
    open() {
      return new Promise((done, fail) => {
        let late = false;                // some iPhones never answer the first time: give up after five seconds
        const timer = root.setTimeout(() => { late = true; fail(fault("storage")); }, 5000);
        let rq;
        try { rq = root.indexedDB.open("rasad-access", 1); } catch (e) { root.clearTimeout(timer); fail(e); return; }
        rq.onupgradeneeded = () => {
          if (!rq.result.objectStoreNames.contains("keys")) rq.result.createObjectStore("keys", { keyPath: "id" });
        };
        rq.onsuccess = () => { root.clearTimeout(timer); if (late) rq.result.close(); else done(rq.result); };
        rq.onerror = () => { root.clearTimeout(timer); fail(rq.error || fault("storage")); };
      });
    },
    async run(mode, work) {
      const db = await idb.open();
      try {
        return await new Promise((done, fail) => {
          const tx = db.transaction("keys", mode), rq = work(tx.objectStore("keys"));
          let value;
          rq.onsuccess = () => { value = rq.result; };
          rq.onerror = e => {            // the id is taken: another tab stored its pair a moment ago
            if (rq.error && rq.error.name === "ConstraintError") { e.preventDefault(); value = TAKEN; }
          };
          tx.oncomplete = () => done(value);
          tx.onabort = () => fail(tx.error || rq.error || fault("storage"));
        });
      } finally { db.close(); }
    },
    get: () => idb.run("readonly", s => s.get("device")).then(v => v || null),
    add: rec => idb.run("readwrite", s => s.add(rec)).then(v => v !== TAKEN),
  };

  const real = { fetch: (path, init) => root.fetch(path, init), store: idb, budget: 20000 };
  const env = Object.assign({}, real);
  const subtle = () => root.crypto && root.crypto.subtle;
  const supported = () => !!(subtle() && typeof subtle().generateKey === "function" && (env.store !== idb || root.indexedDB));

  let pair = null;                       // this device's key pair, once read or made
  let proving = null;                    // a proof under way: everyone waits for the same one
  let provedAt = 0;                      // when the last proof finished
  let lastOk = 0;                        // when the door last confirmed the session
  let skew = 0;                          // the door's clock minus this device's
  let silent = false;                    // boot() answers by its result, not through onLocked
  let watcher = null;

  function device() {                    // read from the browser's storage, or made once and kept there
    if (!pair) pair = loadPair().catch(e => { pair = null; throw e; });
    return pair;
  }
  async function loadPair() {
    let rec;
    try { rec = await env.store.get(); } catch (e) { throw fault("storage"); }
    if (rec && rec.privateKey && rec.jwk) return rec;
    let made;
    try {
      // false = not extractable: the private key can sign, but nothing can read it out of the browser
      const keys = await subtle().generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
      const jwk = await subtle().exportKey("jwk", keys.publicKey);
      made = { id: "device", privateKey: keys.privateKey, publicKey: keys.publicKey,
               jwk: { crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y }, created: Date.now() };
    } catch (e) { throw fault("unsupported"); }
    try {
      if (await env.store.add(made)) return made;
      return await env.store.get();      // another tab made one first: every tab must use the same pair
    } catch (e) { throw fault("storage"); }
  }
  async function sign(text) {            // ECDSA P-256 / SHA-256, raw r||s, base64url
    const d = await device();
    try {
      return b64url(await subtle().sign({ name: "ECDSA", hash: "SHA-256" }, d.privateKey, new TextEncoder().encode(text)));
    } catch (e) { throw fault("unsupported"); }
  }

  // ---------- talking to the door
  const send = (path, init) => env.fetch(path, Object.assign({ cache: "no-store" }, init, { credentials: "same-origin" }));
  function within() {                    // this library's own questions give up after 20 seconds: one that hangs must not hang the app
    try { return root.AbortSignal.timeout(env.budget); } catch (e) {}
    try {
      const stop = new root.AbortController();
      root.setTimeout(() => stop.abort(), env.budget);
      return stop.signal;
    } catch (e) { return undefined; }
  }
  const ask = path => send(path, { signal: within() });
  const post = (path, body) => send(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}), signal: within() });
  async function bodyOf(res) {           // the JSON of an answer; null when it is something else (a static host's 404 page)
    if (!/json/i.test(res.headers.get("content-type") || "")) return null;
    try { return await res.clone().json(); } catch (e) { return null; }
  }
  async function freshNonce() {
    const res = await post("api/nonce"), b = await bodyOf(res);
    if (b && typeof b.nonce === "string") return b.nonce;
    throw fault(res.status === 429 ? "wait" : "server");
  }
  function lock(reason) {
    api.me = null;
    if (!silent && typeof api.onLocked === "function") { try { api.onLocked(reason); } catch (e) {} }
  }
  function confirmed(res, sent) {        // the door answered a signed-in request
    if (!res.headers.has("x-proof-left")) return;
    lastOk = Date.now();
    // an answer to a request that left before the last proof still shows the old cookie's time: skip it
    if (api.me && sent >= provedAt && Number(res.headers.get("x-proof-left")) < RENEW_UNDER) prove();
  }

  // Show the door that this is still the same device: sign a fresh number. The door answers with a
  // new cookie. {ok:true}, or {ok:false, reason} where a reason in LOCKS means the session is over.
  function renew(nonce) {
    if (!proving) proving = renewOnce(nonce).then(r => { proving = null; return r; });
    return proving;
  }
  async function renewOnce(nonce) {
    for (let turn = 0; turn < 2; turn++) {
      let res, b;
      try {
        const n = (turn === 0 && nonce) || await freshNonce();
        res = await post("api/prove", { nonce: n, sig: await sign("rasad-prove:" + n) });
        b = await bodyOf(res);
      } catch (e) {                      // no network is not the end of a session; a lost key pair is
        return { ok: false, reason: e && (e.reason === "storage" || e.reason === "unsupported") ? "proof" : (e && e.reason) || "network" };
      }
      if (res.status === 200) { provedAt = lastOk = Date.now(); return { ok: true }; }
      if (res.status === 401) return { ok: false, reason: (b && b.error) || "login" };
      if (!(res.status === 400 && b && b.error === "proof")) return { ok: false, reason: "server" };
      // the number ran out (the phone slept in between): once more with a new one
    }
    return { ok: false, reason: "proof" };       // this browser no longer holds the key pair the door knows
  }
  async function prove() {
    const r = await renew();
    if (!r.ok && LOCKS.includes(r.reason)) lock(r.reason);
    return r.ok;
  }

  // Ask the door who is signed in:
  //   {me}             signed in
  //   {out: reason}    the door says there is no session, or that it is over (a reason from LOCKS)
  //   {door, reason}   could not find out ("network" | "server" | "wait"); door = it did answer as the door
  async function check() {
    for (let turn = 0; turn < 2; turn++) {
      const sent = Date.now();
      let res;
      try { res = await ask("api/me"); } catch (e) { return { door: false, reason: "network" }; }
      const b = await bodyOf(res);
      if (res.status === 200 && b && b.id) {
        api.me = { id: b.id, name: b.name, role: b.role, expires: b.expires == null ? null : b.expires };
        if (typeof b.server_time === "number") skew = b.server_time - Date.now();
        confirmed(res, sent);
        return { me: api.me };
      }
      if (!(res.status === 401 && b && typeof b.error === "string")) return { door: false, reason: "server" };   // not the door's answer
      // "nobody is signed in" while this app believes somebody is: it may be the answer to a
      // request that left with the cookie before the last renewal, so the door is asked once more
      if (b.error === "login" && api.me && turn === 0) continue;
      if (b.error !== "proof") return { out: b.error };
      const r = await renew(b.nonce);
      if (!r.ok) return LOCKS.includes(r.reason) ? { out: r.reason } : { door: true, reason: r.reason };
    }
    return { out: "proof" };
  }

  async function boot() {
    silent = true;
    try {
      const st = await check();
      // No answer from a door means the open site, unless this address was the private app before:
      // then the door is only out of reach right now, and nothing public must be shown.
      let before = false;
      try { before = root.localStorage.getItem("rasad-door") === "1"; } catch (e) {}
      if (!st.me && !st.out && !st.door && !before) return { mode: "public" };
      try { root.localStorage.setItem("rasad-door", "1"); } catch (e) {}
      if (st.me) return { mode: "private", me: st.me };
      api.me = null;
      return { mode: "private", me: null, reason: st.out || st.reason };
    } finally { silent = false; }
  }

  async function login(key) {
    const typed = String(key == null ? "" : key).trim();
    if (!typed) return { ok: false, error: "missing" };
    if (!supported()) return { ok: false, error: "unsupported" };
    try {
      const pub = (await device()).jwk;
      for (let turn = 0; ; turn++) {
        const nonce = await freshNonce();
        const res = await post("api/login", { key: typed, pub, nonce, sig: await sign("rasad-login:" + nonce), label: deviceLabel() });
        const b = (await bodyOf(res)) || {};
        if (res.status === 200 && b.ok && b.me) {
          api.me = { id: b.me.id, name: b.me.name, role: b.me.role, expires: b.me.expires == null ? null : b.me.expires };
          provedAt = lastOk = Date.now();
          if (typeof b.server_time === "number") skew = b.server_time - Date.now();
          try { root.localStorage.setItem("rasad-door", "1"); } catch (e) {}
          // ask the browser not to clear this site's storage on its own (the key pair lives there);
          // Firefox would show a question for it, so it is left alone
          try { if (!/Firefox/.test(root.navigator.userAgent)) root.navigator.storage.persist().catch(() => {}); } catch (e) {}
          return { ok: true, me: api.me };
        }
        if (b.error === "proof" && turn === 0) continue;         // the number ran out on a slow network: once more
        // the manager already refused this device: nothing was sent to them this time
        return { ok: false, error: b.error === "device" && b.requested === false ? "refused" : b.error || "server" };
      }
    } catch (e) {
      return { ok: false, error: (e && e.reason) || "network" };
    }
  }

  async function logout() {              // this device's session ends; its key pair stays, so the key works here again
    try {
      const res = await post("api/logout");
      if (res.status !== 200) return { ok: false, error: "server" };
    } catch (e) { return { ok: false, error: "network" }; }
    api.me = null;
    return { ok: true };
  }

  // fetch with the session: proves this device when the door asks, then asks again
  async function guardedFetch(path, init) {
    let sent = Date.now(), res = await send(path, init);
    for (let turn = 0; turn < 3 && res.status === 401; turn++) {
      const b = await bodyOf(res);
      if (!b || typeof b.error !== "string") return res;         // somebody else's 401
      if (turn === 2) { lock(b.error); return res; }
      if (b.error === "proof") {
        const r = await renew(b.nonce);
        if (!r.ok) { if (LOCKS.includes(r.reason)) lock(r.reason); return res; }
      } else {
        // Before the screen is locked the door is asked once more who is signed in: the answer to a
        // request that left just before the cookie was renewed must not sign anyone out.
        const st = await check();
        if (!st.me) { if (st.out) lock(st.out); return res; }
      }
      sent = Date.now();
      res = await send(path, init);
    }
    confirmed(res, sent);
    return res;
  }

  async function admin(path, body) {     // the manager's panel: GET with no body, POST with one; always answers with JSON
    try {
      const res = await guardedFetch("api/admin/" + String(path).replace(/^\/+/, ""), body === undefined ? undefined
        : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      return (await bodyOf(res)) || { error: "server" };
    } catch (e) { return { error: "network" }; }
  }

  // While the app is open: when nothing has confirmed the session for `seconds`, ask the door. A
  // sign-out by the manager, a stopped key or the end of validity then closes the screen within
  // that time. Nothing is asked while the app is in the background; it asks at once on return.
  function watch(seconds) {
    const every = (Number(seconds) > 0 ? Number(seconds) : 30) * 1000, step = Math.min(1000, every / 3);
    if (watcher) watcher();
    let busy = false, nudged = 0;
    const hidden = () => !!(root.document && root.document.hidden);
    const look = async force => {
      if (busy || !api.me || hidden()) return;
      const now = Date.now();
      // the period has run out by the door's clock: ask now, not at the next turn (at most every five seconds)
      const ended = api.me.expires != null && now + skew >= api.me.expires && now - nudged >= 5000;
      if (!force && !ended && now - lastOk < every - step) return;
      if (ended) nudged = now;
      busy = true;
      try {
        const st = await check();
        if (st.out) lock(st.out);
      } finally { busy = false; }
    };
    const timer = root.setInterval(() => { look(false); }, step);
    if (timer && timer.unref) timer.unref();
    const shown = () => { look(true); };
    if (root.document && root.document.addEventListener) root.document.addEventListener("visibilitychange", shown);
    watcher = () => {
      root.clearInterval(timer);
      if (root.document && root.document.removeEventListener) root.document.removeEventListener("visibilitychange", shown);
      watcher = null;
    };
    return watcher;
  }

  const api = {
    me: null,                            // {id, name, role, expires} of who is signed in, or null
    onLocked: null,                      // set it: called with the reason when the session ends
    boot, login, logout, prove, fetch: guardedFetch, admin, watch,
    errorText, eventText, statusText, normalizeKey, deviceLabel,
    util: { b64url, canonicalJwk, thumbprint },
    _use(parts) {                        // tests only: another fetch or key store, and a clean slate
      Object.assign(env, real, parts);
      if (watcher) watcher();
      pair = proving = null;
      provedAt = lastOk = skew = 0;
      api.me = api.onLocked = null;
    },
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.RasadAccess = api;
})(typeof self !== "undefined" ? self : globalThis);
