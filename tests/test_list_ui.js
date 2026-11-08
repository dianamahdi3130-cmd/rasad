"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

// Run the actual application declarations and renderer, without starting its network
// polling or registering browser events. A missing map exercises the real list fallback.
const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const inline = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).find(script => script.includes("const S ="));
assert.ok(inline, "application script exists");
const declarations = inline.split("/* ---------- events ---------- */")[0];

function application(items = []) {
  const elements = new Map();
  const now = Date.parse("2026-11-08T00:00:00Z");
  const context = vm.createContext({
    Date: class extends Date { static now() { return now; } },
    document: {
      querySelector(selector) {
        if (!elements.has(selector)) elements.set(selector, {innerHTML: ""});
        return elements.get(selector);
      },
    },
    localStorage: {getItem() { return null; }},
    location: {hostname: "localhost", pathname: "/"},
  });
  vm.runInContext(declarations + "\nglobalThis.listTest = {S, visible, render, cardStrengthText};", context);
  const api = context.listTest;
  api.S.items = items;
  return {api, elements};
}

function incident(id, first, last = first, extra = {}) {
  return {id, first, last, headline: "حريق في البحر", where: "قرب الساحل",
    verdict: "يحتاج تأكيداً", priority: "high", regions: ["gulf"], ...extra};
}

function cardIds(markup) {
  return [...markup.matchAll(/class="card"[^>]*data-id="([^"]+)"/g)].map(match => match[1]);
}

test("real card renderer orders new starts before older confirmed fires with newer observations", () => {
  const items = [
    incident("old-confirmed", "2026-11-07T18:00:00Z", "2026-11-07T23:59:00Z",
      {verdict: "مؤكد", max_frp: 181}),
    incident("newest", "2026-11-07T23:00:00Z", "2026-11-07T23:00:00Z", {max_frp: 51}),
    incident("middle", "2026-11-07T21:00:00Z", "2026-11-07T23:50:00Z"),
  ];
  const {api, elements} = application(items);
  api.render();
  const cards = elements.get("#list").innerHTML;
  assert.deepEqual(cardIds(cards), ["newest", "middle", "old-confirmed"]);
  assert.match(cards, /أعلى شدة 181 MW/);
  assert.match(cards, /بدأ قبل ساعة/);
  assert.deepEqual(items.map(item => item.id), ["old-confirmed", "newest", "middle"],
    "rendering must not reorder the source feed");
});

test("invalid start dates fall back to last observation, with stable last-time and id ties", () => {
  const {api, elements} = application([
    incident("z", "2026-11-07T22:00:00Z", "2026-11-07T22:30:00Z"),
    incident("b", "2026-11-07T22:00:00Z", "2026-11-07T23:00:00Z"),
    incident("a", "2026-11-07T22:00:00Z", "2026-11-07T23:00:00Z"),
    incident("unknown-start", "invalid", "2026-11-07T23:10:00Z"),
  ]);
  api.render();
  assert.deepEqual(cardIds(elements.get("#list").innerHTML), ["unknown-start", "a", "b", "z"]);
  assert.match(elements.get("#list").innerHTML, /آخر رصد قبل 50 دقيقة/);
});

test("sorting preserves region, importance, and selected time-period filters", () => {
  const {api} = application([
    incident("visible", "2026-11-07T22:00:00Z"),
    incident("low", "2026-11-07T23:00:00Z", undefined, {priority: "low"}),
    incident("other-region", "2026-11-07T23:30:00Z", undefined, {regions: ["iraq"]}),
    incident("older", "2026-11-06T22:00:00Z"),
  ]);
  api.S.region = "gulf";
  assert.deepEqual([...api.visible()].map(item => item.id), ["visible"]);
  api.S.important = false;
  assert.deepEqual([...api.visible()].map(item => item.id), ["low", "visible"]);
  assert.deepEqual([...api.visible(72)].map(item => item.id), ["low", "visible", "older"]);
});

test("cards show the stored peak rather than the latest smaller measurement", () => {
  const {api, elements} = application([incident("peak", "2026-11-07T23:00:00Z", undefined,
    {max_frp: 181, frp_series: [["2026-11-07T23:00:00Z", 164, "mtg"]]})]);
  api.render();
  assert.match(elements.get("#list").innerHTML, /class="strength">أعلى شدة 181 MW/);
  assert.doesNotMatch(elements.get("#list").innerHTML, /164 MW/);
});

test("cards recover the peak from valid measurements when the aggregate is missing", () => {
  const {api, elements} = application([incident("series", "2026-11-07T23:00:00Z", undefined,
    {max_frp: null, frp_series: [["t1", 51], ["t2", 164], ["t3", 53], ["bad", "NaN"]]})]);
  api.render();
  assert.match(elements.get("#list").innerHTML, /أعلى شدة 164 MW/);
});

test("missing or invalid power is explicitly unavailable, never invented as zero", () => {
  for (const value of [undefined, null, "", "  ", false, true, -2, NaN, Infinity, "NaN", "Infinity"]){
    const {api, elements} = application([incident("unknown", "2026-11-07T23:00:00Z", undefined,
      {max_frp: value, size: "صغير"})]);
    api.render();
    const cards = elements.get("#list").innerHTML;
    assert.match(cards, /class="strength">الشدة غير متاحة/);
    assert.doesNotMatch(cards, /0 MW|NaN MW|Infinity MW/);
  }
});

test("finite sub-megawatt and legitimate zero measurements remain visible", () => {
  const {api, elements} = application([
    incident("small", "2026-11-07T23:00:00Z", undefined, {max_frp: "0.73"}),
    incident("zero", "2026-11-07T22:00:00Z", undefined, {max_frp: 0}),
  ]);
  api.render();
  const cards = elements.get("#list").innerHTML;
  assert.match(cards, /أعلى شدة 0.7 MW/);
  assert.match(cards, /أعلى شدة 0 MW/);
});
