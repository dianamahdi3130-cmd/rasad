---
name: rasad-sources
description: Reference for Rasad's satellite and map data sources (EUMETView layers, NASA FIRMS, OpenStreetMap) with their timing and limits, and the procedure to find out why a fire was missed, including real-data checks through the probe workflow when the session's network is blocked. Use when changing detection, debugging a missed or false fire, or adding a new imagery source.
---

# Rasad data sources

## European pictures: EUMETView WMS (no key)
`https://view.eumetsat.int/geoserver/wms`, WMS 1.1.1, EPSG:4326. Often slow: every request in a
check goes through `WMS.budget(...)`; a check reads at most `scan_max_images` (20) pictures.

| Layer | Use | Cadence |
|---|---|---|
| `mtg_fd:frp` | Meteosat-12 (MTG) fire symbols; MW via GetFeatureInfo (`fetch_frp`) | 10 min |
| `msg_iodc:fire` | Meteosat over the Indian Ocean, fire symbols, **no MW** | 15 min |
| `mtg_fd:rgb_firetemperature` | Rasad's own sea finder (night) and the day finder | 10 min |
| `mtg_fd:ir105_hrfi` | picture clock and cloud check for the finders | 10 min |
| `mtg_fd:vis06_hrfi` | visible, 500 m: the silent smoke check (`rasad/smoke.py`), daylight only | 10 min |
| `msg_iodc:ir039`, `mtg_fd:rgb_geocolour` | the clip's thermal and colour panels | 15 / 10 min |
| `osmgray:land_polygons` | land/sea mask in pictures | static |

- Pictures appear late: a slot is read when published and read again once settled
  (`settle_minutes` 25). Never-read slots go first (`geocollect._unread`), then settled re-reads.
- Missing pictures are holes (`Availability`), retried later; hours after an outage are caught up
  (`catchup_hours`). "فحص جزئي" in the app = some pictures still unread, normal when slow.
- WMS pictures are display colours, not calibrated temperatures: never turn brightness into MW
  or Kelvin.

## NASA FIRMS (key: secret `FIRMS_MAP_KEY`, sent in the URL path)
`https://firms.modaps.eosdis.nasa.gov`, sources `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`,
`VIIRS_SNPP_NRT`, `MODIS_NRT`, two per check in rotation, 25 s budget. About 3 hours late but
positions to ~0.5 km; 10 days of history teach the usual heat (`Memory`) on the first run.

## Places: OpenStreetMap
Overpass (`overpass-api.de`, then `overpass.kumi.systems`) for facilities, weekly, in the
background worker (`maintain-geo`); Nominatim reverse geocoding with a budget per check.

## Before/after pictures: Sentinel-2 (no key, read by the app itself)
Index: Earth Search STAC `earth-search.aws.element84.com/v1`, collections `sentinel-2-c1-l2a`
(recent, files on `e84-earth-search-sentinel-data`) and `sentinel-2-l2a` (older years, files on
`sentinel-cogs`). Both send CORS headers, so `web/imagery.js` reads them in the browser. A pass
every 2–5 days around 07:20 UTC, 10 m colour, 20 m infrared and scene classes (`SCL`). Reflectance
= number × 0.0001 − 0.1 in the first collection; the second says its own scale and offset in the
item (`raster:bands`). File tiles are 10.24 km wide (1024 px at 10 m, 512 px at 20 m), so a box
costs 1 to 4 tiles per band. To try a place from a session with network: `node -e` with
`require("./web/imagery.js").createEngine()` (`listPasses`, `assessPass`, `loadScene`).

Landsat 8/9 (30 m, a pass every 8 days between them, in the archive 2–3 weeks late): index
`planetarycomputer.microsoft.com/api/stac/v1` (`landsat-c2-l2`), files on
`landsateuwest.blob.core.windows.net`, each request signed with the token from
`/api/sas/v1/token/landsat-c2-l2` (free, no account, ~45 minutes; ask once, not per tile: the
service answers 429 otherwise). Reflectance = number × 0.0000275 − 0.2; cloud from `QA_PIXEL` bits.
The app only; the channel's pictures use Sentinel-2.

## NASA's daily picture (app map background, no key)
GIBS tiles `gibs.earthdata.nasa.gov/wmts/epsg3857/best/<layer>/default/<YYYY-MM-DD>/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`,
zoom 9 at most (250 m). `MODIS_Terra_CorrectedReflectance_TrueColor` (morning, out by ~10 UTC) and
`VIIRS_NOAA20_CorrectedReflectance_TrueColor` (early afternoon, out by ~14 UTC). A day not
published yet answers 404. `VIIRS_SNPP_…` no longer answers (checked 11 Nov 2026).

## Why was a fire missed? (in this order)
1. Get the place and the time in UTC (Baghdad = UTC+3). A channel post's time is when it was
   posted, not when the satellite saw it.
2. `python3 .claude/skills/rasad-live-check/check.py --near LAT LON --km 30 --start … --end …`:
   was an incident made? If yes, its verdict and why it was not sent (`--id`): older than
   `max_age_hours`, below `min_verdict`, usual heat at a known flare, recovered past event…
3. If nothing was made, look at what the satellites saw. With network access to
   `view.eumetsat.int`: `python -m rasad --state /tmp/st --site /tmp/si probe --lat … --lon …
   --start …Z --end …Z`. Without it, use the probe workflow:
   - edit `tools/probe-request.json`, e.g. `{"mode": "steps", "steps": [["probe", "--lat", "26.36",
     "--lon", "55.73", "--start", "2026-11-09T05:00Z", "--end", "2026-11-09T07:00Z"]]}`
     (step commands: `probe`, `pictures`, `seascan`, `hotspots`, `restest`, `reclip`);
   - send it from a throw-away branch, so the request never reaches `main` (a push of that file
     to `main` would start a probe run too); this does not touch the monitor:
     `git switch -c probe-tmp && git commit -am "probe: <what>" && git push -f origin HEAD:diag`
     then `git switch - && git branch -D probe-tmp`;
   - after ~5 minutes: `git fetch -q --depth=1 origin +refs/heads/probe:refs/remotes/origin/probe`
     then `git show origin/probe:step1.log | tail -40` (pictures are PNG files on that branch).
4. Write the finding as a short case in `docs/reviews/` (Arabic) and turn the cause into a test
   before changing detection.

Known blind spots: pixels a few km wide (small or short fires), clouds, sun glint by day, fires
inside the memory of usual flares, NASA's ~3-hour delay, hours when EUMETView serves no pictures.
At sunrise the Fire Temperature picture fills with small sunlit clouds that look like hot spots
(101 in one picture on 11 Nov 2026, `docs/reviews/hormuz_dawn_20261111.md`): a speck that moves
kilometres between two pictures or is gone in the next one is a cloud. The day finder covers
first light to last light (sun above -6°); before that change dawn and dusk had no sea finder.

## Hosts for a development environment (Custom network access)
Real data: `view.eumetsat.int`, `firms.modaps.eosdis.nasa.gov`, `overpass-api.de`,
`overpass.kumi.systems`, `nominatim.openstreetmap.org`. Before/after imagery
(`docs/plans/before-after.md`): `earth-search.aws.element84.com`,
`e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com`, `sentinel-cogs.s3.us-west-2.amazonaws.com`,
`planetarycomputer.microsoft.com`, `landsateuwest.blob.core.windows.net`, `gibs.earthdata.nasa.gov`,
and later `sh.dataspace.copernicus.eu`, `identity.dataspace.copernicus.eu`.
Never `api.telegram.org`: nothing may reach the channel from development.
