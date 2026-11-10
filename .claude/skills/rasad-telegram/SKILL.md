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
- When: `engine.run_cycle` (its inner `post`), `brain.alert_worthy`,
  `updates.intensity_update_due`; thresholds in `config.yaml` → `alerts`.
- Tests: `tests/test_alert_updates.py`, `test_updates.py`, `test_clip_updates.py`. Every new or
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
7. Only a confirmed fire is called «مؤكد»; other grades are not named in the post.
8. The day finder never posts on its own; the sea finder alone is at most «مرجّح».
9. Recovered past events and readings older than `max_age_hours` (8) appear in the app only.
   A late reading for a fire already posted goes as a reply marked as a late update.
10. Intensity update: peak ≥ 1.5 × the peak at the last post and at least +1 MW, 30 minutes or
    more after the last post, within 24 hours.
11. A video is attached only when the pictures really show the fire; one that is ready after the
    alert goes as a reply to it. An old video is never presented as covering a newer measurement.
12. A post that was sent is never sent again: after sending, the memory is saved at once
    (`.state_urgent` → `branches.sh save-state-if-due`).

## Checking a message without posting
In a test or a Python shell: build an incident (see the helpers in `tests/test_alert_updates.py`),
call `alert_message(...)` and print it; `deliver(cfg, None, text, media, send=False)` prints what
would be sent. A development session has no Telegram token; never add one, and never run
`python -m rasad test-telegram` or a `cycle` without `--no-send`.
