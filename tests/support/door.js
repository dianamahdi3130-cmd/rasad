"use strict";

// Stand-ins for the tests of the private app's door (worker/src/index.js): the Durable Object's
// storage, a door built on it, and the Worker module itself with its clock in the test's hands.

const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const BASE = "https://rasad.test";
const ADMIN_CODE = "the manager's code for the tests";
const INGEST_KEY = "ingest-key-for-the-tests";

// Durable Object storage: values are copied in and out (so a change that was never saved is
// lost, as in the real thing), keys come back sorted, and one call takes at most 128 rows.
class Storage {
  constructor() { this.rows = new Map(); }
  async get(key) {
    if (!Array.isArray(key)) return this.rows.has(key) ? structuredClone(this.rows.get(key)) : undefined;
    const out = new Map();
    for (const k of key) if (this.rows.has(k)) out.set(k, structuredClone(this.rows.get(k)));
    return out;
  }
  async put(key, value) {
    const pairs = typeof key === "string" ? [[key, value]] : Object.entries(key);
    assert.ok(pairs.length <= 128, "at most 128 rows in one put");
    for (const [k, v] of pairs) this.rows.set(k, structuredClone(v));
  }
  async delete(key) {
    if (!Array.isArray(key)) return this.rows.delete(key);
    assert.ok(key.length <= 128, "at most 128 rows in one delete");
    return key.filter(k => this.rows.delete(k)).length;
  }
  async list({ prefix = "", reverse = false, limit = Infinity, start, end } = {}) {
    const keys = [...this.rows.keys()].sort()
      .filter(k => k.startsWith(prefix) && (start === undefined || k >= start) && (end === undefined || k < end));
    if (reverse) keys.reverse();
    return new Map(keys.slice(0, limit).map(k => [k, structuredClone(this.rows.get(k))]));
  }
  keys(prefix) { return [...this.rows.keys()].filter(k => k.startsWith(prefix)); }
}

// The Worker module (an ES module, so it is imported, not required); `now` is the door's clock.
async function loadDoor(now) {
  const mod = await import(pathToFileURL(path.join(__dirname, "../../worker/src/index.js")).href);
  mod.Hub.now = now;
  return mod;
}

// One door: a store, the secrets, and what it would have done after answering (waitUntil).
function world(Hub, env = {}) {
  const storage = new Storage(), waits = [];
  const make = e => new Hub({ storage, waitUntil: p => waits.push(p) }, e);
  const w = { storage, waits, env: { ADMIN_CODE, INGEST_KEY, ...env } };
  w.hub = make(w.env);
  w.redeploy = e => { w.env = { ...w.env, ...e }; w.hub = make(w.env); };     // the same store with new code or secrets
  return w;
}

module.exports = { BASE, ADMIN_CODE, INGEST_KEY, Storage, loadDoor, world };
