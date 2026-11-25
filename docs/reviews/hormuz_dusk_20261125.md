# Hormuz at sunset, 25 Nov 2026: heat at sea in the hours no finder reads

**What happened.**
- Another channel showed a fire at sea in the Strait of Hormuz at about 16:40 Baghdad time and said a new ship had been hit.
- Rasad had posted NASA's sighting at 13:02, 6 km to the west, but nothing after it.

**What the pictures show** (`hotspots` through the probe workflow, Meteosat-12 at 0.25 km):
- A warm spot at sea at 26.30-26.32 N, 56.19-56.24 E, about 13 km north-west of Khasab.
- It was the warmest place at sea in every picture from 15:10 to 16:50, 7-10 above the sea around it.
- It moved about 5 km east in that time.
- From 17:00, in full dark, nothing at sea stood above the sea around it.

**Why nothing was posted.**
- By day Rasad posts from the official fire symbols and NASA only. Neither had a symbol there.
- The day finder only shows in the app, and it was two hours behind: it last read the 14:40 picture.
- The night finder starts at a sun 6° below the horizon. Its first pictures (16:40, 16:50) found nothing, because it looks for heat that is new against 30-90 minutes before. A fire lit in daylight is "hot before" to it.

**Change: steady heat at sea** (`seawatch.steady_spots`, `steady_min_above` 8).
- A spot counts when it stands at least 8 above the clear sea around it in three dark pictures in a row, in open water: no mapped platform, no platform field of NASA's long-term map, no heat there on earlier days.
- It is told as three sightings, «مرجّح» at most. Its closer look and video come before the post, as for everything the sea finder sees alone.

**Replayed** (`seascan --steady 12`, which logs every candidate from 6 up):

| Night | Boxes | Above the clear sea in the three pictures | Told in open water at 8 |
|---|---|---|---|
| 23-24 Nov, the ship fire off Khasab | hormuz_oman | 10-21 | the fire, in its pictures |
| 21-22 Nov, ordinary | hormuz_oman, gulf_south | flares of the platform fields (Zakum and others): 40-150, all left out | nothing |
| 25 Nov, 16:50-17:50 | hormuz_oman | nothing above 6 | nothing |

**Still open.**
- The hours between sunset and full dark (sun 0° to -6°) are read by the day finder alone. It needs six pictures per reading and falls hours behind when the European server is slow.
- Its backlog is the next thing to fix.

## The whole day: one object, or a new fire? (`hotspots`, 00:00-10:30 UTC)

Warmest spot at sea in each Meteosat-12 picture over 55.95-56.45 E, 26.15-26.50 N. All times Baghdad.

| Baghdad time | Where | Above the sea around | What it is |
|---|---|---|---|
| 23:30-03:30 (night) | 26.41 → 26.35 N, about 56.25 E | posted, «مؤكد» (NASA 3.4 MW at 01:47) | the fire 22 km north of Khasab, drifting south about 1.3 km/h |
| 03:00-04:00 | 26.37 N, 56.25 E | 4-6 | the same, at the end of its incident |
| 04:10-05:40 | – | nothing above 3 | fainter than the picture can tell |
| 05:50-06:00 (dawn) | 26.355 N, 56.246 E | 3-4 | 1.5 km from where it was |
| 06:20-06:50 | 26.36 N, 56.29 E | 3 | faint, 4 km east |
| 07:00-08:00 | – | nothing | – |
| 08:10-13:30 | 26.335 N, 56.179 E → 26.29 N, 56.13 E | 4-12 in every picture | a fire, 7 km west-south-west of the dawn spot, drifting west-south-west 1.5 km/h; NASA 8.9 MW at 13:02 |
| 15:10-17:50 | → 26.32 N, 56.26 E | 7-15 | drifting east 2.5 km/h, ending 4 km from the night track |

- **Most likely the same burning object (a ship) as the night before.**
  - The fire was faint or hidden for about two hours at dawn.
  - It then went on 7 km from where it was last seen, moving the same way, at the speed of the strait's currents.
  - A new fire 7 km away is possible, but less likely.
- **The pictures cannot tell the cause.**
- **By day it was in every picture from 08:10, but it was posted only when NASA passed (13:02).**
  - Its rows are the day finder's, which never posts alone.
  - A day spot that carries on a posted night fire's track (within some km and hours) could be told as that fire still burning, under its post. This is a proposal, not a change.

## Following a posted fire (`follow.py`, replayed with `python -m rasad follow`)

How it works:
- After a posted sea fire goes quiet for 30 minutes, each check reads the newest picture around its
  last sighting.
- It takes the warmest spot of open sea there, at least 4 above the 12 km around, within the
  distance a hull drifts since then.
- Three pictures in a row go out once as a reply under the post.

| Replay | Result |
|---|---|
| The Hormuz fire, from its last sighting (03:30 Baghdad) | 01:00 UTC one weak sighting; then from 05:30 UTC the fire in every picture, drifting west; reply at 06:20 UTC (09:20 Baghdad), «على بعد 7 كم غرب…» |
| Central Gulf (25.9 N, 53.3 E), 25 Nov | a speck running 3 km east per picture: refused by the speed rule |
| Northern Gulf (28.5 N, 50.0 E), 25 Nov | sunlit clouds after sunrise for hours (green and blue brighter than red): refused |
| Gulf of Oman side of Musandam (26.3 N, 56.45 E), 22 Nov | the same after sunrise: refused |
| Red Sea (20.0 N, 38.5 E), 25 Nov | the same, all day: refused |
| Gulf of Oman (25.6 N, 57.0 E), 25 Nov | nothing |

**What tells heat from sunlight.** The red (3.8 micron) against the two short waves (1.6 and 2.2
micron), each against the 12 km around:
- the fire: red 7-33, green and blue 0-10;
- sunlit clouds and glint: green and blue as bright as the red, or brighter.
The red must lead both by 2.5 (`red_margin`).

## 20:10 Baghdad: «حريق في البحر قرب مضيق هرمز», posted again

- The fire posted at 18:01 (incident 1440) was in every picture until 20:50, drifting north-east to
  26.345 N, 56.31 E.
- At 20:10 the steady rule found a spot 4 km west of it in the same picture (26.343 N, 56.263 E).
- By the rule «one fire is in one place at a time», a spot more than 2.5 km away is another fire, so
  it was posted as one, with only «قبل 17 ساعة رُصد حريق مؤكد آخر في البحر على بعد 4 كم».

**What the pictures show** (`clip` and `hotspots`, 16:00-18:20 UTC):
- One hot streak about 4 km long, east to west, and no second spot anywhere in the box.
- A strong source is drawn so (Jafurah the same evening: 3.5-3.8 km).
- The 20:10 spot was the streak's west end.

**What changed.**
- A steady spot within 6 km of a spot the finder found in the same picture is that fire
  (`steady_found_km`).
- A first post at sea near a posted fire says so (`brain.near_posted`): «📌 على بعد 4 كم من حريق في
  البحر نُشر عند 18:01 وما زال ظاهراً في الصور».
- The fire's track, 24-25 Nov (Baghdad time):

| Time | Where |
|---|---|
| 23:30 | 26.41 N, 56.26 E |
| 03:30 | 26.35 N, 56.24 E |
| 08:10 | 26.34 N, 56.18 E |
| 13:02 | 26.29 N, 56.14 E |
| 17:40 | 26.32 N, 56.26 E |
| 20:50 | 26.35 N, 56.31 E |

  It stayed within about 15 km for 21 hours, moving back and forth with the strait's tides.
