# Ras Tanura, 25 Nov 2026: a blaze no official product saw

**What happened.** The owner reported an attack on Ras Tanura. Rasad had posted nothing, and its
memory held only the refinery's usual flares: 1-15 MW a day, a reading on 30 of 31 days.

**What the pictures show.** We read Meteosat-12's Fire Temperature picture at 0.25 km
(`probe` and `pictures` through the probe workflow). At **26.63-26.64 N, 50.15-50.17 E** there was a
blaze. That is the south-east end of Ras Tanura, about 3 km from the mapped port and 9 km from the
refinery, by the terminal and its tanks. All times below are Baghdad.

| Picture | Red of the picture (rise) | Flame glow (1.6/2.2 micron) | 10.5 micron at the spot |
|---|---|---|---|
| 04:30 | nothing | nothing | as before |
| 04:40 | 212 | 62 | about the same |
| 04:50 | 207 | 68 | 10 levels colder |
| 05:00 | 210 | 53 | 12 levels colder |
| 05:10 | 77 | 25 | colder |
| 05:20 | fading | – | colder (still) |

- Meteosat-9 over the Indian Ocean showed the heat at the same place and time.
- The 10.5 micron picture is drawn bright where it is cold: at night the land is brighter than the
  sea there. It turned **colder** over the blaze and stayed so. That is the smoke rising.

**Why Rasad missed it.**
1. The official fire symbols (Meteosat-12 FRP and IODC) had nothing within 12 km in any picture.
2. NASA did not pass: its night pass was four hours earlier.
3. Rasad's own finder read only the seas. This shore was not even in its boxes.

**What changed** (`seawatch` boxes with `"land": True`, switched by `seawatch.sites`).
- **The sites read.** Ten boxes over the great energy sites are now read at night:
  - Saudi Arabia: the east coast with Abqaiq, Yanbu, Jeddah and Jizan.
  - Bahrain.
  - Qatar: Ras Laffan and Mesaieed.
  - Kuwait: Ahmadi, Shuaiba and Zour.
  - The UAE: Ruwais and Habshan, Jebel Ali, and Fujairah.
- **What counts on land.** Each box is read the way the seas are, by the same pictures, the same
  pairs and the same closer look with a video. On land a spot counts only with:
  - a clear rise (`site_min_rise` 20); and
  - a flame's glow in the short waves (`site_min_glow` 4). A lamp, a hot roof or a chimney has none.
- **What is left out.**
  - A source that was hot before (a flare).
  - A spot within 1 km of a flare that NASA's long-term map knows (`site_flare_km`). A flare
    coming out of a cloud looks new; a flare's real surge is the «حرارة غير معتادة» rule's.
  - A spot inside the halo of a stronger source within 3 km.
- **The 10.5 micron check on land.** It is asked only when the glow is faint (under
  `site_sure_glow` 20), and then only a sharp warming rejects: a cloud going away from something hot
  it had hidden. A blaze's cold smoke does not reject, because no cloud glows at night.
- **The open sea** in a site box is left to the sea boxes.
- **Posting.** A site's blaze is news from its second picture («مرجّح»: Rasad's finder alone is
  never more), whatever the facility map says about distances. Its place's usual heat (the flares
  around) does not make it routine. It is never a first sighting from one picture.

**Replayed.**
- **Ras Tanura, the night of the blaze** (kept cut `tests/fixtures/site_ras_tanura_20261125.npz`):
  - found at 04:40, 04:50, 05:00 and 05:10;
  - its pair from 04:50 would have been posted at about 05:00 with its video.
- **Ordinary nights, found nothing:**
  - the evening before at Ras Tanura (`site_ras_tanura_evening_20261124.npz`);
  - a whole night at Abqaiq (`site_abqaiq_night_20261124.npz`).
- Whole-night replays of all the sites: see below.
