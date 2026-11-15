---
name: rasad-telegram
description: The rules for everything Rasad posts to its Telegram channel (alert text, updates, replies, videos, wording the owner insists on) and where that code lives. Use before changing or adding any channel message, alert condition or update, and when the owner reports a garbled or wrong post.
---

# Rasad channel posts

## Where
- Text: `brain.alert_message(inc, cfg, update=…, threaded=…, intensity=…, video_update=…, delayed=…)`
  with `_strength_lines`, `mw_ar`, `bidi_safe`, `app_link`, `links`.
- Sending: `notify.tg_send` / `notify.deliver` (HTML `parse_mode`; a reply carries
  `reply_parameters` with `allow_sending_without_reply`; text longer than a video caption's 1024
  characters goes out as the video with the first line, then the full text as a reply to it).
- The confidence number and its reasons: `rasad/assess.py` (bands follow the verdict:
  «مؤكد» 75-99, «مرجّح» 50-74, «يحتاج تأكيداً» below 50; a plant's unusual heat has its own
  range 40-90 and its own label «الثقة بأنه خارج المعتاد», because there the heat is certain and
  the doubt is whether it is outside the plant's habits).
- When: `engine.run_cycle` (its inner `post`), `brain.alert_worthy`,
  `updates.intensity_update_due`; thresholds in `config.yaml` → `alerts`.
- The before/after satellite picture: `satpic.caption` (text), `satpic.render` (picture), posted
  by `engine.run_cycle` as a reply; `brain.video_note` says whether a video can be expected.
- Tests: `tests/test_alert_updates.py`, `test_updates.py`, `test_clip_updates.py`, `test_satpic.py`. Every new or
  changed message gets a test that checks its exact text.

## Rules the owner set (keep all of them)
1. Never garbled: user text goes through `esc()` (HTML) and every Latin run through `bidi_safe()`,
   so names, brackets and numbers stay in place in every Telegram app.
2. Fire power in Latin letters: `61 MW`, `8.8 MW` (`mw_ar`), never «ميغاواط» in posts.
3. «الخليج الفارسي», never «الخليج العربي».
4. Times in Baghdad time (`local_text`, offset from `config.yaml` → `local_time`); add the date
   only when it is not today.
5. Updates are replies to the first post of that fire (`tg_msg`), say only what changed, and do
   not repeat the headline, coordinates or links.
6. «فتح الحدث في التطبيق» opens the app on that incident: `app_link` → `?e=<id>#<id>`.
7. Only a confirmed fire is called «مؤكد»; other verdicts are not named in the post. Since
   15 Nov 2026 (the owner asked for a clear confidence level with its reasons on every alert) a
   first post also carries `🎯 الثقة: 62% (متوسطة) · الخطورة: مرتفع` (`assess.confidence_line`) and,
   when the fire is not confirmed, `🔎 <the strongest reason>` (`assess.main_reason`); updates do
   not repeat them.
8. The day finder never posts on its own; the sea finder alone is at most «مرجّح».
9. Recovered past events and readings older than `max_age_hours` (8) appear in the app only.
   A late reading for a fire already posted goes as a reply marked as a late update.
10. Intensity update: peak ≥ 1.5 × the peak at the last post and at least +1 MW, 30 minutes or
    more after the last post, within 24 hours.
11. A video is attached only when the pictures really show the fire; one that is ready after the
    alert goes as a reply to it. An old video is never presented as covering a newer measurement.
13. No «سبب التأخر» line in any post (the owner removed it on 12 Nov 2026), even when the first
    report comes hours after the fire started. A fire NASA alone saw is not promised a video.
14. The before/after picture says «تغيّر مرصود» or «لا تغيّر واضح», never a cause, never «مؤكد»,
    and always that each point is 10 m. One per fire, as a reply; not for fires at open sea.
15. A watched place (`config.yaml` → `watch.places`) gets its own post, not a reply, only when a
    new clear pass shows a patch of change: `satpic.watch_caption`. It names the place, the two
    dates and the patch, says the change may be natural, and never a cause.
16. «حرارة غير معتادة في <منشأة>» (a plant's usual flare burning harder) goes out only when the
    reading clearly beats the plant's own habit (`memory.unusual_record_ratio`, 2× the second-highest
    day of the month, today left out: `Memory.habit`), and one episode is posted once (not again for
    the same plant within `unusual_repeat_days` unless `unusual_repeat_ratio` times what was posted):
    16 of the first 25 posts were the same few plants surging to levels they reach every few days.
    The owner's worry (15 Nov 2026): a plant that is attacked must not be silenced by its own flare.
    The bar is therefore a habit, not one spike, and never today's own earlier readings.
17. A fire on land far from every mapped facility is posted when it is confirmed and there was no
    heat there or around for a month, in the dry Gulf countries (`brain.new_heat`): the headline
    stays «حريق جديد في <الدولة>», and one line says why it is posted and what it may be, never a
    cause: «🗺 لا منشأة مسجّلة في الخريطة هنا… قد يكون موقعاً غير مرسوم (محطة ضخ، بئر، خط أنابيب).»
    Its post says so in one line (`brain.surge_note`): the flare is usual, the level is a record,
    and pictures cannot tell operational flaring from a fire. Never a cause.
17. A first sea sighting from ONE picture («رصد حراري أولي») is posted only when unmistakable
    (`strong`: a rise of 15 with a flame glow of 4). A fainter speck waits for its second picture
    and then goes out as a normal alert.
12. A post that was sent is never sent again: after sending, the memory is saved at once
    (`.state_urgent` → `branches.sh save-state-if-due`).

## Checking a message without posting
In a test or a Python shell: build an incident (see the helpers in `tests/test_alert_updates.py`),
call `alert_message(...)` and print it; `deliver(cfg, None, text, media, send=False)` prints what
would be sent. A development session has no Telegram token; never add one, and never run
`python -m rasad test-telegram` or a `cycle` without `--no-send`.
