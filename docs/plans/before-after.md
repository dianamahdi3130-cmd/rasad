# Before / after imagery («قبل وبعد»)

Status: **phase 1 is built** (10 Nov 2026), in the browser: `web/imagery.js` (engine) and
`web/beforeafter.js` (screen), and grown into an imagery browser with analysis tools on 15 Nov
2026 (see «The imagery browser» near the end). The first decision below was settled by a real test: Earth Search
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

## The channel's picture (built 10 Nov 2026, the owner asked for it)
`rasad/s2.py` is the same engine in Python (numpy; same results on the Sudan case, 2.53 ha) and
`rasad/satpic.py` the queue, worker, picture and caption. After an alert on land the check writes
a job; the worker (started by `scripts/loop.sh`) looks every 3 hours for the first clear pass after
the fire's last sighting, compares it with the newest clear pass before the fire and writes a
portrait JPEG; the check posts it once as a reply to the alert, remembers it (`inc["satpic"]`) and
the app opens the incident's comparison on the same two dates. No clear pass within 12 days: the
request is closed quietly. By hand: `python -m rasad satpic --lat … --lon … --start …Z`.

## Added on 11 Nov 2026 (the owner asked for all three)
- **Daily picture on the map**: a third background, NASA's GIBS tiles (250 m), morning (Terra)
  and early afternoon (NOAA-20), a day at a time. Shows the scene and large smoke, not facilities.
- **Landsat 8/9 in the viewer**: extra dates (30 m) from Microsoft's Planetary Computer, read like
  the Sentinel-2 files but signed with a free token. They are in the list for choosing by hand;
  the default pair stays on Sentinel-2. In that archive they are 2–3 weeks late, so they help for
  older comparisons, not for a fire of this week. A pair of two different satellites is said so.
- **Watched places** (`config.yaml` → `watch.places`, read by the monitor): every 3 hours the
  worker looks for a new clear Sentinel-2 pass over each place and compares it with the clear
  pass before it; the check posts the picture only if the largest connected patch of change is at
  least `min_patch_ha` (0.5 ha). The first clear pass is the starting point. Ports change with
  every ship: raise `min_patch_ha` there, or expect a post with most passes.

- **A search field at the top of the app** (the owner did not find the one inside the chooser):
  any place by name or coordinates; each result opens the comparison or shows on the map with a
  pin. A bar offers to reload when the site has a newer version than the open page (`APP_V`).
- **The comparison as a film** («🎬 فيديو»): about ten seconds, 720 × 900, recorded in the phone
  with `MediaRecorder` (MP4 where the browser can, else WebM): «قبل», the divider sweeps to «بعد»,
  the change is marked, and back. `filmPlan` is the script; a browser that cannot record says so.

## The imagery browser (15 Nov 2026, the owner asked for "the Copernicus Browser, simpler and faster, with analysis tools")
The same two files (`web/imagery.js` engine, `web/beforeafter.js` screen), still nothing on the
server. The screen now brings its own stylesheet (`<style id="ba-style">`, added once by
`BA.init`, written with the page's CSS variables) and makes all its markup inside `#baSheet`.

- **The place, without typing**: tap a point (4 km box), **draw an area** on the map, or **«صوّر ما
  أراه»** (the map as it is on screen). Drawing does not fight with moving the map: a switch
  «تحريك / تحديد» says what a finger does; in «تحديد» a drag draws the rectangle (or two taps set two
  corners), then the map moves again and four handles adjust it. The engine reads square boxes of
  fixed sizes, half-width 1, 2, 3, 5, 10 and now **20 and 40 km**: a rectangle becomes the smallest
  box that holds it (`boxOfRect`, shown dashed on the map, its size said), the map view the nearest
  one by area (`boxOfView`). A picture never has more than 800 × 800 points (400 with «توفير
  البيانات» on a wide box), so a wide box is read from the files' coarser overviews and the screen
  says the true size of one point (10 m up to 6 km, 12.5 m at 10 km, 25, 50, 100 m at 20, 40,
  80 km). Boxes of 40 and 80 km open on one picture. `BA.here()` and `BA.draw()` are there for the
  page's own buttons.
- **A box wider than one tile**: the tiles of the same pass are joined (`pass.parts`, also across
  a UTM zone edge); the sky of boxes wider than 20 km is judged on 500 × 500 points. A box inside
  one tile is read exactly as before.
- **Dates**: one strip of all passes, Sentinel-2 and Landsat together, newest first: the day, the
  satellite and a dot for the sky (clear / partly / cloudy, cloudy ones dimmed but still to choose);
  «أقدم» at its end lists four more months without touching the pictures on screen. Two cards hold
  «قبل» and «بعد» (a tap on a card says which of them a tapped date goes to), each with «أقدم /
  أحدث»; quick choices: «أحدث صورة صافية», «قبل الحدث وبعده» / «آخر صورتين صافيتين».
- **Four ways to show**: one picture, the divider, the blink, side by side (two panes that move
  together: beside each other on a wide screen, one above the other on a phone). Links carry it:
  `…,s=single|blink|side,c=swir|veg|fire|nbr|ndvi,o=change|burn` after the dates (also after an
  incident id); a link without them opens as it always did.
- **Bands** (Sentinel-2 / Landsat 8-9 OLI): natural colour B04·B03·B02 / B4·B3·B2; short-wave
  infrared B12·B8A·B04 / B7·B5·B4; plants (false colour) B08·B04·B03 / B5·B4·B3; fires and burnt
  ground B12·B11·B04 / B7·B6·B4; **NBR** = (B8A − B12)/(B8A + B12) / (B5 − B7)/(B5 + B7); **NDVI**
  = (B08 − B04)/(B08 + B04) / (B5 − B4)/(B5 + B4). Band 8 (the only infrared band at 10 m) is
  fetched only for a sharp plant view; until it arrives, at 20 m or coarser, and where a pass has no
  such file, band 8A stands in and the screen says so. A view a satellite cannot show is closed
  with a word why (`comboAvailable`). The indices are drawn with fixed ramps (lightest where there
  is nothing to see, darker to both sides) and a legend under the picture; the label on the
  picture gives the true sharpness of the view (a 20 m band stays 20 m in a 10 m picture).
  «تحسين العرض» is a stronger contrast stretch and a sharpening filter, display only.
- **Analysis**: the change layer and the flames as before; **«أثر احتراق محتمل» (dNBR)** = NBR
  before − NBR after in the usual classes (0.10 low, 0.27 moderate, 0.44 high, 0.66 severe) with
  the area of each (`burnOf`): left out are no data, cloud, its shadow and haze on either date
  (everything `changeOf` leaves out, and cloud on both dates), water and deep shadow, flames and
  30 m around them; a drift of the whole box between the dates is taken out (up to 0.08); a pixel
  counts when it is not alone. It is worded as a sign, never a cause. A **point inspector** (tap or
  hover: coordinates to copy, reflectance of every band on each date, NDVI, NBR, dNBR, scene
  class), the distance measure and an **area measure** (corners by taps). One card, «ملخص
  التحليل», holds the results, the dates with satellite and clearness, and the standing cautions.
- **Out**: the picture and the film as before, and a **report**: one tall picture (A4 portrait,
  1240 × 1754, twice that where the phone can) and the same on paper («طباعة / PDF»: a section
  only the printer sees, then `window.print()`), both drawn from one model (`reportModel`): brand,
  place, coordinates and box, the pictures with date, Baghdad and UTC time, satellite, sensor,
  sharpness and clearness, the layers with legend and areas, the summary, the source, when it was
  made, the cautions.
- **Speed**: each band is asked for on its own and kept in memory within a byte budget
  (`engine.loadBands`, 96 MB of bands and 56 MB of tiles, half on a phone with little memory), so
  the picture appears as soon as its three bands are there (first at 20 m, then the rest, then
  10 m), a view switches without a download, and a download two views share is cancelled only when
  both have left it. The comparison of two dates (change and dNBR) runs in a background worker
  made from the engine's own text (`I.source`, no second file), and on the main thread where a
  worker cannot start.

Not checked against the real archives yet (the session that built it had no network; the tests
and the screenshots use the made-up archive): the address and name of band 8 (`B08.tif`, asset
`nir`; if the guess is wrong the record is asked, and band 8A stands in), the overview sizes and
tile sizes of the real files for 40 and 80 km boxes (how many megabytes a wide box costs), joining
real neighbouring tiles of one pass, and Landsat at 60-240 m overviews.

## Not built yet
Phases 2 and 3 above (sub-metre commercial pictures need a paid order per event); a time-lapse
through all clear passes; partial reading of a tile (would cut the download for boxes near a tile
corner); the picture inside the private app's clip store; the drawn rectangle shown inside the
square picture; the zoom and position of the view inside a link.
