# Plan: one fire at sea is one fire (identity over time)

**Status, 25 Nov 2026 evening: not built. Two features are switched off until it is.**

## Phase 1, shipped 25 Nov 2026 night: the app only

- The owner looked at the app, saw four markers off Khasab and asked "why four fires?".
- `rasad/chains.py` now ties the records of one heat for the app, and the app draws one marker with
  the path the heat took; an unsettled tie is a dashed line and says «غير محسوم».
- Nothing in the channel changed. The channel is phase 2, with everything below.
- The rules it uses, in the owner's words:
  - the same fire: seen from one record to the next without a break;
  - another fire: two at once, or further than a burning hull can drift;
  - not settled: a gap of hours, then heat within drifting distance.

## Phase 2, silent step, shipped 25 Nov 2026 night: the channel decides and records, posts nothing new

- The owner said "start phase 2". By the rule of that day it starts silent.
- At the first post of a fire at sea, `chains.decide` says what the channel should do, and the engine
  records it (`inc["thread"]`, `state["thread_log"]`, the log line «THREAD (silent) …»). The post
  itself goes out as before.
  - **A reply under the older fire's post** («ما زالت النار مشتعلة», `chains.reply_message`): the
    older fire's heat was seen in at least three pictures of the hour before, the newest within 5 km.
    One followed picture never proves it (the review's first defect).
  - **A post of its own, said as not settled:** within drifting distance after a gap, or a second
    peak 2.5-5 km from the older fire's own spot in the same picture (one streak or two ships).
  - **A plain post:** everything else, and an older fire seen more than 5 km away in the same picture.
- Replayed on the six posts off Khasab as the tool stored them: five would be «غير محسوم», none a
  reply. The tool held no run of sightings before any of them, because the follow record only began
  that evening. With the follow record running, the day passes of NASA would be replies.
- The wording «رُصد حريق مؤكد آخر» is now «رُصد حريق مؤكد»: the tool does not say "another".
- **To switch it on** (`chains.thread_post: true`), first:
  1. read `state["thread_log"]` after some days against what was posted;
  2. build the acting side: the reply instead of the post, the threaded record's updates and reading
     under the older post, one owner of a followed heat, a sudden strong rise beside a posted fire
     told as a reply;
  3. an independent review.

## Why

- One burning ship off Khasab was posted six times in two days as a new fire
  (`docs/reviews/hormuz_one_source_20261125.md`).
- Three quick changes of 25 Nov tried to mend parts of this: the steady rule (#47), the follow reply
  (#49), and a continuity rule that was never merged.
- An independent review the same evening found real defects in all three, and two of them were
  already visible in the channel:
  - the follow reply «ما زالت الحرارة ظاهرة» went out twice for the same heat, under posts 4 and 6;
  - «قراءة رصد» said the heat was gone eight minutes after a follow reply said it was still there.
- The owner asked that day: "how long do we keep mending, and are the fixes themselves the cause?"
  For that day the answer was yes.

## What is switched off, and how to switch it back

| Feature | Setting in `config.yaml` | State |
|---|---|---|
| Steady heat at sea (`seawatch.steady_spots`) | `seawatch.steady_min_above` | 0 (was 8): off |
| Follow reply (`follow.py`) | `follow.post` | false: it follows and records, posts nothing |
| First sighting from one picture (`earlyalerts`) | `seawatch.early_alerts.enabled` | false since 27 Nov: it posted the Khasab ship again at 23:00 |
| Continuity through the followed track (`brain._followed_km`) | – | reverted on the work branch, never on `main` |

Neither is switched back before the work below is done and replayed.

## Defects the review confirmed (each reproduced with the project's own functions)

1. **A followed row proved identity too easily.** Any single row of `follow.track` within 5 km joined a
   sighting to the old fire, even one picture after hours of nothing. A new fire 13 km away got no
   post of its own.
2. **The first-sighting path never asked.** `earlyalerts.sync` calls `public_candidates` with an empty
   incident list, so a strong first sighting on a posted fire's heat is still posted on its own.
3. **The steady rule's back-filled sightings make a second incident.** When the finder loses the
   fire, or the hottest pixel moves to the other end of the 4 km streak, the two back-filled rows sit
   more than 2.5 km from the fire's own rows of those pictures (`_elsewhere_same_picture`), and a new
   incident is posted.
4. **NASA's pixel is matched to the fire's first position only.** `_track_km` is not applied to polar
   detections, so a pixel on a fire that has drifted 13 km is a new incident, even while the finder
   holds the fire.

## A miss of the other kind (Khasab, 26-27 Nov 2026, `docs/reviews/khasab_20261126.md`)

- New heat at sea off Khasab, about a day after the earlier fire was last seen.
- NASA saw it at 01:10 with two night pixels of 3.2 and 3.7 MW. It was never posted.
- `brain.open_sea_fire` refuses a spot with `known_days` above 0, and the earlier ship was that one
  day. In open water the tool's own measure of usual heat is "on most days" (`engine.usual_heat`).
- That same refusal is also what keeps a lingering ship from a daily new post. So the change belongs
  here, with "where the fire is now" and a line saying a fire burned near the spot in the days before.

## Further findings, reproduced by the reviewers but not checked a second time

- **Several records, one heat.** Each posted record of one source is followed on its own: several
  replies for one heat (seen live).
- **`steady_spots` can raise on a partly drawn picture** (`np.nanargmax` of an all-NaN window). The
  finder then fails for those picture times on every check.
- **The steady rule and its closer look disagree.** The rule tells from 8 above the sea; the look shows
  steady heat only from 25 (`static_min`).
- **`steady_max` 3 is used up by platform flares.** In a box with three flares an open-water fire is
  never reached.
- **A steady source is a new incident every evening** (more than 12 hours of daylight between).
- **`reading.py` and `brain.near_posted` do not know the followed track.**
- **«حريق مؤكد آخر» judges.** The older nearby-fire line says «رُصد حريق مؤكد آخر…» (`reading.py`,
  `brain.verdict_of`, `brain.alert_message`, `assess.py`). «آخر» asserts a different fire, which the
  pictures cannot tell; the owner read it as the tool's own verdict (25 Nov, screenshot of three
  readings). The wording must be neutral: a confirmed fire was seen X km away, Y hours before.
- **Two posts of one heat get two readings** with different durations (20:10-20:50 and 17:40-22:00
  for the same heat off Khasab), and «ثم لم تعد تظهر» only means the finder stopped seeing it.
- **`follow.look` takes another posted fire's heat for its own** when two burn within reach.
- **The follow reply calls `_remember_post`**, which can swallow a strength update.
- **`rec["from"]` in the reply is never renewed** after the fire's own sightings resume.
- **The follow track keeps 24 rows** (under four hours); late FIRMS pixels need more.
- **A post of the steady rule says «البداية»** for what is only the first dark picture.
- **`patterns.longterm_words` says «لا حرارة هنا»** from a map that only holds cells hot on two or
  more sampled dates.
- **The wells and pipelines parts slow the facility map's rebuild** (304 parts, a failure ends the
  run).

The reviewers' reproductions are kept in `docs/plans/sea_fire_identity_scenarios/` (plain scripts,
not tests; some need the reverted `_followed_km`). Each should become a test of the new design.

## What to build, as one piece

1. **Where the fire is now.** One helper gives a posted sea fire's present position: its own
   sightings of the last hour, or a followed run that began at least three pictures before and passed
   the drift tests. Every rule asks this helper: `find_incident` for finder spots and for NASA pixels,
   the first-sighting path, `near_posted`, `reading`.
2. **One owner of a heat.** When two posted records sit on the same heat, the older post owns it; the
   newer is marked as the same source and gets no replies of its own.
3. **Two fires stay two.** A sighting joins a fire only where that fire's heat demonstrably was. Heat
   elsewhere in the same picture, beyond the streak length, is another fire.
4. **The steady rule,** if it comes back: pairs instead of back-filled rows, a NaN guard, rejected
   peaks not counted, the look judged by the rule's own measure, and a bridge over daylight.

## How it ships

- First silent (app only) for at least a week, counted against what was posted.
- Replayed on kept pictures of 23-25 Nov off Khasab (`grab`), which hold the whole case: the shore
  fire, the two sunrises, the faint morning, the streak, NASA's five passes.
- An independent review before the merge, as on 25 Nov. That review is what stopped the continuity
  rule from going live.
