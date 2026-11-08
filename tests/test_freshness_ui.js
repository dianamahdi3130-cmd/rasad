"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).find(value => value.includes("const S ="));
assert.ok(script, "application script exists");
const declarations = script.split("/* ---------- events ---------- */")[0];
const NOW = Date.parse("2026-11-08T16:00:00Z");

function status(meta){
  const elements = new Map();
  const context = vm.createContext({
    Date: class extends Date { static now(){ return NOW; } },
    document: {querySelector(selector){
      if (!elements.has(selector)) elements.set(selector, {innerHTML:"", hidden:false});
      return elements.get(selector);
    }},
    localStorage: {getItem(){ return null; }},
    location: {hostname:"localhost", pathname:"/"},
  });
  vm.runInContext(declarations + "\nglobalThis.statusTest = {S, renderStatus};", context);
  context.statusTest.S.meta = meta;
  context.statusTest.renderStatus();
  return {markup:elements.get("#status").innerHTML, learning:elements.get("#learning")};
}

const complete = {
  generated:"2026-11-08T15:59:00Z", scan_started:"2026-11-08T15:47:00Z",
  scan_finished:"2026-11-08T15:59:00Z", scan_duration_s:720,
  scan_partial:false, sources:{mtg:{ok:true,latest:"2026-11-08T15:50:00Z"}},
};

test("completed collection uses finish time while showing duration separately", () => {
  const {markup, learning} = status(complete);
  assert.match(markup, /dot ok/);
  assert.match(markup, /آخر فحص مكتمل قبل دقيقة/);
  assert.match(markup, /بدأ الفحص 18:47/);
  assert.match(markup, /استغرق 720 ثانية/);
  assert.doesNotMatch(markup, /آخر فحص مكتمل قبل 13/);
  assert.equal(learning.hidden, true);
});

test("recent partial collection cannot look fully healthy", () => {
  const {markup} = status({...complete,scan_partial:true});
  assert.match(markup, /dot warn/);
  assert.match(markup, /فحص جزئي/);
  assert.doesNotMatch(markup, /dot ok|يراقب/);
});

test("legacy feed is labeled as data time without inventing a completed check", () => {
  const {markup} = status({generated:"2026-11-08T15:55:00Z",learning:true});
  assert.match(markup, /آخر بيانات قبل 5 دقائق/);
  assert.doesNotMatch(markup, /آخر فحص مكتمل/);
});

test("old feed reports delayed data rather than claiming the process stopped", () => {
  const {markup} = status({...complete,scan_finished:"2026-11-08T15:30:00Z"});
  assert.match(markup, /dot bad/);
  assert.match(markup, /البيانات متأخرة/);
  assert.match(markup, /آخر فحص مكتمل قبل 30 دقيقة/);
  assert.doesNotMatch(markup, /متوقف/);
});

test("partial status remains explicit when its feed is also stale", () => {
  const {markup} = status({...complete,scan_finished:"2026-11-08T15:30:00Z",scan_partial:true});
  assert.match(markup, /فحص جزئي · البيانات متأخرة/);
  assert.match(markup, /dot bad/);
});

test("freshly completed check does not hide old source observations", () => {
  const {markup} = status({...complete,sources:{mtg:{ok:false,latest:"2026-11-08T14:45:00Z"}}});
  assert.match(markup, /dot warn/);
  assert.match(markup, /المصادر متأخرة/);
  assert.match(markup, /أحدث وقت بالمصادر قبل ساعة/);
  assert.match(markup, /آخر فحص مكتمل قبل دقيقة/);
});

test("invalid and future dates never produce a healthy status or NaN text", () => {
  for (const generated of [undefined,"invalid","2026-11-08T16:30:00Z"]){
    const {markup} = status({generated,scan_finished:"invalid"});
    assert.match(markup, /dot warn/);
    assert.match(markup, /وقت آخر فحص غير متاح/);
    assert.doesNotMatch(markup, /dot ok|NaN|يراقب/);
  }
});

test("invalid finish time falls back to an honest legacy data label", () => {
  const {markup} = status({...complete,scan_finished:"invalid"});
  assert.match(markup, /آخر بيانات قبل دقيقة/);
  assert.doesNotMatch(markup, /آخر فحص مكتمل/);
});
