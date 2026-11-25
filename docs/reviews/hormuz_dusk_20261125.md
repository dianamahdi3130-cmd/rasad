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
