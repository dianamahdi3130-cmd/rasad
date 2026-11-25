# Hormuz, 23-25 Nov 2026: one burning ship, posted six times as a new fire

**The owner's questions** (25 Nov, after the sixth post):
- Is this last fire new?
- A ship cannot burn from one day to the next, so how can it be the same one?
- If it has been there since yesterday, why was it not sent before?

Earlier answers in this session were guesses from a few hours of pictures. This review reads every
picture of the three days and the one Sentinel-2 pass.

## What was posted

All six posts were headed «حريق في البحر قرب مضيق هرمز». Times are Baghdad time.

| Post | Sent | What made it | Where |
|---|---|---|---|
| 1 | 24 Nov 01:54 | night finder and NASA (3-7 MW) | 26.35 N, 56.29 E, moving north-west |
| 2 | 24 Nov 12:03 | NASA's day pass (5.5 MW) | 26.39 N, 56.19 E |
| 3 | 25 Nov 00:10 | night finder and NASA (3.4 MW) | 26.41 N, 56.26 E, moving south |
| 4 | 25 Nov 13:25 | NASA's day pass (8.9 MW) | 26.29 N, 56.14 E |
| 5 | 25 Nov 18:01 | night finder | 26.32 N, 56.26 E |
| 6 | 25 Nov 21:05 | the steady rule | 26.34 N, 56.26 E |

## What the pictures show

**Sentinel-2, 10 m** (`python -m rasad s2-look`, new):
- 13 Nov: the shore north of Khasab at 26.296 N, 56.336 E is empty.
- 18 Nov: cloud over the spot. NASA saw 5.4 MW about 1 km away that night (not posted: one sighting).
- **23 Nov, 10:02:** a hull about 200 m long lies against that shore and burns.
  - The flame covers 129 pixels of 10 m in the short-wave infrared, saturated.
  - Black smoke runs north-west for more than 15 km over the strait.
- There was no pass on 24 or 25 Nov.

**Meteosat-12, every 10 minutes** (`hotspots` over 56.05-56.45 E, 26.22-26.50 N, 23 Nov 13:00 to
25 Nov 21:30):
- From sunset on 23 Nov until 22:00 nothing at sea stands above the water around it.
- **23 Nov 22:10:** heat appears 3 km off that shore, at 26.29 N, 56.31 E, and moves north-north-west
  at 1.5-2 km an hour.
- It is then in the pictures the whole time, with no gap longer than about two hours (around the two
  sunrises, when the 3.8 micron picture is full of moving sunlit artifacts).
- It moves back and forth inside about 20 km:
  - west and south by night and in the morning, furthest west at about noon;
  - east and north-east in the afternoon and evening.
- NASA's 375 m pixels sit on the track at four times: 24 Nov 01:09 and 02:06, 24 Nov 11:41,
  25 Nov 01:47, 25 Nov 13:02.
- Two sources were never seen at the same time.

## Reading

- **Most likely one burning ship.** It burned at the shore on the morning of 23 Nov, its heat stayed at
  the shore until about 21:10 that evening, then left it where and when the track at sea begins, and it
  has drifted, still burning, for two days.
- A ship can burn for days: the fire was 3-9 MW the whole time, a slow fire, not a blaze.
- So posts 2 to 6 were most likely not new fires.

**An independent analyst** was given the raw data without this reading and asked to refute it
(workflow of 25 Nov evening):

| Question | Its answer |
|---|---|
| One continuous source at sea, 23 Nov 22:10 to 25 Nov 21:30? | 90% |
| Is it the hull Sentinel-2 shows at the shore? | 82% |
| Several ship fires one after another | 5%: no join needs more than 4.2 km/h, nothing is left behind |
| A fixed platform or flare | refuted: positions span 20 km |
| Sunrise or cloud artifacts stitched into a track | 2%: NASA confirms the day track within 0.4-1.0 km |
| Two sources at the same time | 3%: every NASA pass shows one cluster |

**What the data cannot tell:**
- the cause, and the ship's identity;
- whether the hull at the shore is the hull at sea (the next clear Sentinel-2 pass would settle it);
- whether a second ship within about 3 km of the first burned at any time (Meteosat cannot separate
  them);
- whether other ships were hit without a lasting fire, or outside the box that was read;
- whether the other channels' "new ship" reports are this fire.

**Weak points:**
- 25 Nov 04:00-08:20: 4.3 hours with no solid sighting; the heat comes back 7 km away and stronger.
- Around the sunrise of 24 Nov the track rests on a colour test alone.
- The link to the shore rests on timing, place and direction. There is no picture of the hull leaving.

## Why it was posted six times

- The finders look for heat that is new. A fire that burns on is found again only when it flares or
  when NASA passes.
- Each time it was found again, its record had ended more than 6 km away or more than 12 hours
  before (`incidents.merge_km`, `merge_hours`), so a new record was made, and a new post.
- Post 6 was the far end of the streak the fire is drawn as (see `hormuz_dusk_20261125.md`).

## What was done

- **Nothing new was switched on.** A continuity rule was written and replayed on the real pictures:
  it joined posts 3, 4, 5 and 6 to post 1. The independent review then found two real defects in it
  (a new fire could be swallowed; the first-sighting path was not covered), so it was reverted
  before any merge.
- **Two features of the same day were switched off** (`config.yaml`): the steady rule and the follow
  reply. The review found real defects in both, and two showed in the channel that evening.
- **The work is planned as one piece:** `docs/plans/sea_fire_identity.md`.
- **Diagnosis kept:** `python -m rasad s2-look` lists every Sentinel-2 pass over a place with its
  glowing spots and keeps a picture of each. It is what settled this case.
