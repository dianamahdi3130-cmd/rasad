"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const html = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
const inline = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).find(value => value.includes("const S ="));
const declarations = inline.split("/* ---------- events ---------- */")[0];

function application(extra = {}) {
  const elements = new Map();
  const now = Date.parse("2026-11-13T19:30:00Z");
  const context = vm.createContext({
    window: {},
    Date: class extends Date { static now() { return now; } },
    document: {querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, {innerHTML:""});
      return elements.get(selector);
    }},
    localStorage: {getItem() { return null; }},
    location: {hostname:"localhost", pathname:"/"},
  });
  vm.runInContext(declarations + "\nglobalThis.previewUI={S,render,openDetail};", context);
  const api = context.previewUI;
  const item = {id:"candidate-night", preliminary:true, kind:"thermal_candidate",
    headline:"بقعة حرارية أولية في البحر قرب مضيق هرمز", where:"في البحر قرب مضيق هرمز",
    verdict:"يحتاج تأكيداً", reason:"إشارة حرارية أولية ظهرت ليلاً؛ ننتظر رصداً إضافياً لتقييمها",
    priority:"high", regions:["hormuz"], first:"2026-11-13T18:50:00Z", last:"2026-11-13T18:50:00Z",
    start_text:"2026-11-13 الساعة 21:50 (بغداد)", last_text:"2026-11-13 الساعة 21:50 (بغداد)",
    lat:26.3716, lon:56.2988, place:"sea", best:"geo", max_frp:null, frp_series:null,
    size:null, facility:null, clip:null, snap:null,
    clip_note:"رصد حراري أولي قيد التحقق؛ لا يتوفر فيديو لهذه البقعة حالياً", ...extra};
  api.S.items = [item];
  return {api,elements,item};
}

test("first night spot is visible under the important filter without invented MW or confirmation", () => {
  const {api,elements} = application();
  api.S.important = true;
  api.S.region = "hormuz";
  api.render();
  const list = elements.get("#list").innerHTML;
  assert.match(list, /data-id="candidate-night"/);
  assert.match(list, /بقعة حرارية أولية/);
  assert.match(list, /الشدة غير متاحة/);
  assert.doesNotMatch(list, /\d+(?:\.\d+)? MW|badge v-sure/);
});

test("preliminary details distinguish missing review from a checked absence of nearby facilities", () => {
  const {api,elements} = application();
  api.openDetail("candidate-night");
  const detail = elements.get("#detail").innerHTML;
  assert.match(detail, /لم تُراجع المنشآت القريبة بعد/);
  assert.match(detail, /غير مقاسة حالياً/);
  assert.match(detail, /لا يتوفر فيديو لهذه البقعة حالياً/);
  assert.doesNotMatch(detail, /لا توجد منشأة طاقة قريبة|يجري تجهيز|badge v-sure|<video/);
  assert.equal(elements.get("#sheet").hidden, false);
});

test("ordinary reviewed incidents retain their original facts and video behavior", () => {
  const {api,elements} = application({preliminary:false, verdict:"مؤكد", reason:"رصده قمران",
    max_frp:47, size:"كبير", clip:"clips/fire.mp4", snap:"clips/fire.jpg"});
  api.openDetail("candidate-night");
  const detail = elements.get("#detail").innerHTML;
  assert.match(detail, /لا توجد منشأة طاقة قريبة/);
  assert.match(detail, /47 MW/);
  assert.match(detail, /badge v-sure/);
  assert.match(detail, /<video src="clips\/fire.mp4"/);
  assert.doesNotMatch(detail, /لم تُراجع المنشآت القريبة بعد/);
});

test("the first Telegram link still opens its incident after confirmation", () => {
  const {api,elements} = application({id:"confirmed-fire", early_ids:["candidate-night"],
    preliminary:false, verdict:"مؤكد", reason:"رصده قمران", clip:"clips/fire.mp4"});
  api.openDetail("candidate-night");
  assert.match(elements.get("#detail").innerHTML, /<video src="clips\/fire.mp4"/);
  assert.equal(elements.get("#sheet").hidden, false);
});
