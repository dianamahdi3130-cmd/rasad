# Plan: one fire at sea is one fire (identity over time)

**Status, 25 Nov 2026 evening: not built. Two features are switched off until it is.**

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
