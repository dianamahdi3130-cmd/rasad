# Before / after imagery («قبل وبعد»)

Status: **phase 1 is built** (10 Nov 2026), in the browser: `web/imagery.js` (engine) and
`web/beforeafter.js` (screen). The first decision below was settled by a real test: Earth Search
and both picture buckets answer with `Access-Control-Allow-Origin: *`, range reads included, so
there is no worker, no queue and no server work. See «What was built» at the end; the rest of
this page is the design as agreed, kept for the reasons behind it.

## Goal
Show what changed on the ground at **any place the owner picks**, with or without a fire: an
airport, base, port or facility that was attacked (many attacks leave no fire NASA would list),
or a fire's site. Compare a clear picture before with a clear picture after. Entry points:
1. the map: «اختر موقعاً» anywhere (the main use);
2. an incident card: the same viewer, already centred and dated on that incident.

## What free imagery can show
- Sentinel-2 L2A (ESA, free): 10 m colour and near-infrared, 20 m short-wave infrared (SWIR),
  a new pass every few days; skies over the Gulf are clear most of the year.
- Shows: burn scars and scorch marks, burnt tanks, destroyed hangars and other large buildings,
  large craters, smoke, large spills (sometimes), the size of the changed area.
- Does not show: damage to one small building, one aircraft or one vehicle, or small craters
  (10 m pixels). That needs paid sub-metre commercial pictures, bought per event (phase 3).
- A ship moves: at sea a single recent picture (smoke, slick, the ship) is more useful than a pair.
- App wording: «تغيّر مرصود», never «ضرر مؤكد», with the same caution as the verdicts. Never a cause.

## Phase 1: free, no account
Source: Earth Search STAC `https://earth-search.aws.element84.com/v1`, collection
`sentinel-2-c1-l2a` (Cloud-Optimized GeoTIFFs on
`e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com`). Free but without a service
guarantee: when it is down the app says so and the request can be repeated.

**First decision, settle it with a real test in the first session (network access needed):**
can the app read Earth Search and the COGs **directly in the browser** (CORS headers on both)?
- **Yes** → build the viewer client-side: instant, for any place, no queue and no server work, and
  it works on the public app and the private app alike (geotiff.js + proj4js, pinned versions).
  Keep the reading to a small window (a few MB per comparison) so it stays fast on phones.
- **No** → a background imagery worker on GitHub (below); answers in minutes.

Steps (either way):
1. **Request**: `{lat, lon, half_km (default 2–3 km), before?, after?, incident_id?}`.
2. **Scenes**: list the passes over the box with little cloud over the box itself (scene
   `eo:cloud_cover`, then the `scl` classification band over the box) and let the owner pick;
   default before = newest clear pass before the date asked (or the incident's `first`), after =
   the newest clear pass (or the first after the incident's `last`).
3. **Read only the window** around the point from the COGs (HTTP range reads). Sentinel-2 tiles
   are in UTM (zones 36–41 across the regions): convert the box to the tile's projection.
   Bands: B04/B03/B02 colour; B12/B8A/B04 SWIR view; B8A/B12 for NBR.
4. **Show**: colour before/after, SWIR before/after, and a change layer (SWIR brightness change,
   or dNBR where there is vegetation; transparent where nothing changed), with the dates, the
   cloud over the box and the changed area in hectares (an estimate).

If the worker route is needed: build it like `mediaqueue.py` (job files, tokens, locks,
`result.json`, retry with backoff), start it from `scripts/loop.sh` like the media worker, never
inside a check; write `before.webp`, `after.webp`, `before_swir.webp`, `after_swir.webp`,
`change.webp` (each ≲150 KB) and `imagery.json`; publish them like the clips (site `clips/`,
private app `/api/ingest/clip/…` and `/clips/…`). Requests for any place then come from the
private app (Worker) into a queue the GitHub loop reads, so they need the private app to be live.
Reader to choose by install time on GitHub Actions: `rasterio` (wheels bundle GDAL) or
`tifffile` + `imagecodecs`; projection with `pyproj` or `rasterio.warp`.

## In the app (`web/index.html`)
- Map: «اختر موقعاً» → tap → box size → list of clear passes (dates) → before/after.
- Incident card: button «قبل وبعد» opening the same viewer on that place and dates.
- Viewer: slider between before and after; «ألوان طبيعية» / «الأشعة تحت الحمراء القصيرة» /
  «خريطة التغيّر»; dates and changed area; save as a portrait picture without the logo, like the
  photo button. Bump `CACHE` in `web/sw.js`.
- Places the owner checks often (airports, bases, ports) can be saved as a list to reopen.

## Phase 2: instant and server-side, optional
Copernicus Data Space Sentinel Hub Process API called by the Worker (OAuth client kept as a
Worker secret): any place in seconds, evalscripts for colour, SWIR and NBR. Free tier: 10,000
requests and 10,000 processing units a month, 300 a minute.

## Phase 3
Sentinel-1 radar (through smoke and cloud, at night; oil slicks, ships, large structural change).
Commercial sub-metre pictures per order for major events (airports, bases).

## Tests first
Scene choice (cloud over the box, nearest dates, nothing available); box ↔ pixel window and UTM
zone edges (42°E, 48°E, 54°E, 60°E); changed area on a synthetic array; the source being down;
app: map pick, button, slider, status text.

## Open questions for the owner
Who may use it (today: everyone who can open the app); whether the channel gets a before/after
picture for major events (not built: nothing here posts to the channel).

## What was built (phase 1)
- **Places**: «🛰 قبل وبعد» on the map → pick on the map, search a name (Nominatim), type or paste
  coordinates, saved places (a watch list that shows each place's newest pass and marks one newer
  than the last visit), recently opened. Every incident has the same button (box 4 km wide, 6 km
  when the position is approximate).
- **Passes**: a light index request (no asset lists, ~2 KB for four months) over both archives:
  `sentinel-2-c1-l2a` first, `sentinel-2-l2a` for the years it does not hold yet. Listed around
  the event (±75 days), around the dates of a link (±30 days), else the last four months; «تواريخ
  أقدم/أحدث» add four months. Cloud is judged over the box itself from the `SCL` band (a few KB per
  pass); pixels called cloud on most dates are bright ground and stop counting (`refineSky`).
- **Default pair**: newest clear pass before the event's start and first clear pass after its last
  sighting; without an event the newest clear pass and the clear one before it. A picked place can
  be given the event's day. Settled as soon as the nearer passes are checked (`choose`).
- **Reading**: own small COG reader (header, then only the tiles under the box, by range; zlib by
  the browser's `DecompressionStream`, a plain decoder as fallback), own UTM maths (checked against
  PROJ), output on a north-up web-map grid. No third-party library. First everything at 20 m
  (fast), then the colour bands at 10 m; «توفير البيانات» stops at 20 m. One comparison is about
  8 MB at 20 m and 15–25 MB at 10 m (a box on the corner of four file tiles costs the most):
  the tiles are 10 km wide and cannot be read in part.
- **Views**: natural colour, short-wave infrared (B12/B8A/B04), change layer (five bands, the later
  date first brought to the earlier one's brightness; darker = red, brighter = cyan; lone pixels
  dropped) with the area; flames at the moment of the picture (B12 far above B11 and B8A), told
  apart from those already there before (gas flares). Cloud on one date only is left out with a
  margin and the share compared is stated.
- **Tools**: divider, two-finger zoom, double tap, blink, distance between two taps with the
  coordinates, portrait picture without the logo, link that reopens the same place and dates.
- Checked on real events: a burnt building in Sudan (8 Nov 2026), gas flares at Basra, the Beirut
  port explosion of 2020 from the older archive.

## Not built yet
Phases 2 and 3 above; a before/after picture for the channel; a time-lapse through all clear
passes; partial reading of a tile (would cut the download for boxes near a tile corner).
