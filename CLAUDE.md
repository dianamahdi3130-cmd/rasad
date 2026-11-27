# Rasad (رصد) · ALFAQAAR FIRMS

Fire monitor for the Gulf region. Every ~2.5 minutes it reads free satellite fire data (European
Meteosat pictures, NASA FIRMS), judges each fire, posts alerts to a Telegram channel and feeds a
phone web app. It runs unattended on GitHub Actions; a private copy of the app runs on Cloudflare.

## Working with the owner
- Reply in Arabic, in plain words. The owner is not a developer: say what changes for them.
  **Every message to the owner is in Arabic, with no exception**: answers, short progress notes
  between steps, waiting notes, questions. Never one English sentence (the owner's instruction of
  25 Nov 2026, repeated many times). Code, commits and docs stay in English.
- **No hot fixes to what is posted.** On 25 Nov 2026 ten changes went live in two days, each from one
  case; an independent review that evening found real defects in the day's three sea changes, two
  already visible in the channel, and the owner asked "how long do we keep mending, and are the fixes
  the cause?". Since then: a new rule that posts, or changes what is a new fire, first runs silent
  (app only) for days; it is replayed on kept real pictures and on the stored events; and it gets an
  independent adversarial review before the merge. A wrong or doubled post in the channel is still
  mended at once, by switching the feature off in `config.yaml` first.
- **Change nothing unless the owner asks (27 Nov 2026).** His words: «انت اتعبتني بالتعديلات وكل تعديل
  يولد اخطا».
  - **The exam (28 Nov 2026).** Every change to what is posted is run through it first: `python
    tools/exam.py compare <ref>` (`exam/README.md`). It replays the posting decision on every stored
    event, and every difference is read before the merge. Known truths go in `exam/cases.json`.
    Run `python tools/exam.py snapshot` weekly: the live memory keeps only about ten days.
  - Change nothing that decides or words what is posted unless he asks for that change.
  - Do not ask him one question per case. Keep each miss or wrong post in `docs/reviews/`, and bring
    them once a week as one short list with one plan. The first list is due on 4 Dec 2026.
  - A wrong or doubled post is still switched off at once, and he is told in one line.
- An analysis for the owner says what the pictures show and how sure it is, and what they cannot tell.
  Look at everything first (the whole days, `s2-look`), not a few hours: three answers about Hormuz
  on 25 Nov were guesses that the next question overturned.
- Secrets live only in GitHub repository secrets: `FIRMS_MAP_KEY`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `RASAD_ADMIN_CODE` (the
  manager's code = the manager's own access key) and a cron-job.org token; the optional
  repository variable `RASAD_DOMAIN` is the owner's own address for the private app. Never ask
  for their values, print them, or write them anywhere. As of 15 Nov 2026 the Cloudflare secrets
  are not added yet: the app is still the public GitHub Pages site.
- Nothing is posted to the channel from a development session (no Telegram token here, and keep
  it that way). Preview messages with `deliver(..., send=False)` or the tests.

## Map
| Path | What it is |
|---|---|
| `rasad/engine.py` | one check, `run_cycle`: collect → judge → alert → publish (`public_item` = what the app gets); `DEFAULTS` config; version constants |
| `rasad/brain.py` | incidents: `find_incident`, `add_to_incident`, `verdict_of`, `headline`, `alert_message` (channel text), `surge_note`, `Memory` (usual heat, `peak` = a place's own record) |
| `rasad/assess.py` | on top of the verdict, never against it: the kind of event (`fire` / `industrial` / `candidate`), a confidence number 0-100 with its reasons (`evidence`), seriousness, who saw it when (`sources`, `timeline`, `agreement`) |
| `rasad/archive.py` | the long-term record the app searches: events worth keeping, one small file per month (`<state>/archive/`, copied to `<site>/data/archive/` and sent to the private app) |
| `rasad/sources.py`, `rasad/wms.py` | EUMETView fire symbols (`detect_geo_layer`, `fetch_frp`), NASA FIRMS (`firms_fetch`); WMS client with time budgets, `Availability` |
| `rasad/noaafire.py` | NASA's NOAA-20/21 fire pixels from NOAA's own public files (AWS, no account), about 25 min after a day pass and 45 after a night pass instead of ~3 h through FIRMS; same pixels (99% match, `docs/reviews/noaa_files_20261116.md`), entered as FIRMS rows of the same source; a later FIRMS copy is its twin (`Twins`), never a second sighting. On in `config.yaml` (`noaa.enabled`), off in `DEFAULTS` (tests) |
| `rasad/geocollect.py` | which European pictures each check reads (never-read first, then settled second reads) |
| `rasad/seawatch.py`, `rasad/daywatch.py` | Rasad's own sea finder (night, can alert): reads the Fire Temperature picture at **0.25 km per pixel in tiles** over ten sea boxes, red = 3.8 micron, green/blue = flame glow; since 25 Nov 2026 also ten boxes over the great energy sites on land (`"land": True`, switched by `seawatch.sites`, `live_boxes`); and the experimental day finder (first light to last light, sun above −6°, its two boxes at 0.5 km; never alerts alone) |
| `rasad/daysteady.py` | by day, a place at sea that stays clearly hotter than the sea around it in 4 pictures in a row (40 min), not warmer at 10.5 micron (sun-warmed land is), not a platform, not usual heat (atlas), not steady on 2 earlier days (`state["sea_day_steady_days"]`). Its rows join the day finder's (`mtg_day_spot`): shown in the app, never posted alone. Replayed 18-21 Nov 2026: what it finds at sea are flares (Zakum, Das Island, a new site off the UAE); the 20 Nov Muscat spot did not show at 3.8 micron |
| `rasad/earlyalerts.py`, `preliminary.py`, `earlyclip.py` | the first sea sighting from one picture («رصد حراري أولي»): posted at once only when unmistakable (`strong`), shown in the app otherwise; its own short video |
| `rasad/pictureworker.py`, `thermal_history.py` | background fetching of the finders' pictures (tiles) into `.rasad-pictures/`; "heat seen here before" for the sea finder |
| `rasad/clip.py`, `twopanel.py`, `mediaqueue.py` | the thermal clips, rendered by a separate media worker; pictures read and judged over 40 km, shown twice as near, centred on the fire (`clip.view_zoom`, the owner's wish of 22 Nov 2026); each picture carries its own reading, and the held last picture says «أعلى شدة» with the highest reading of the video's time when the last one is lower (`twopanel._hold_peak`; the Jafurah video of 25 Nov 2026 ended on «9.4 MW» for a 64 MW event) |
| `rasad/s2.py`, `rasad/satpic.py` | Sentinel-2 reader (same engine as `web/imagery.js`, numpy) and the before/after pictures for the channel: under an alert when a clear pass comes, and for the watched places of `config.yaml` → `watch.places` when a new clear pass shows a patch of change (queue, worker, picture, captions) |
| `rasad/patterns.py` | heat patterns («أنماط الحرارة»), **silent for now**: a label for heat on land at or near energy sites that describes the heat, never its cause: «حرارة جديدة مستمرة», «اشتداد في موقع حرارة معتاد», «حرارة واسعة الانتشار» (many pixels over km in one NASA pass, against the place's own usual footprint), «حرارة قصيرة أو متقطعة», «حرارة في منطقة نشطة», «حرارة دون نمط واضح», «انعكاس محتمل في محطة شمسية», and the tag «عدة مواقع في وقت متقارب». Facts kept when an incident is made (`quiet_days`, `usual_px`, `solar_km`, `geo_read_at_pass`, `onset_known`) and per NASA pass (`inc["passes"]`, NOAA's oil/gas tag `pa`); shown in the app as the neutral reasons line «النمط: …» and `inc["pattern"]`. Design, replay and the next steps: `docs/plans/heat_patterns.md` |
| `rasad/data/heat_atlas.json.gz`, `tools/heat_atlas.py` | the long-term map of usual heat: per ~500 m cell, on how many of 197 sampled dates (Mar 2024 - Nov 2026) NOAA-20/21 saw heat there; rebuilt by `python tools/heat_atlas.py` (range reads of NOAA's public files, ~10 min). Read by `patterns.signals_of`; between rebuilds Rasad's own record `state["spots"]` learns new flares near energy sites |
| `rasad/smoke.py` | smoke as a second witness, **silent for now**: by day, a streak in the 500 m visible pictures counts only when new, anchored at the heat, one-directional in 3+ pictures and not cold. Recorded on the incident for the app; never posted, never part of a verdict |
| `rasad/flares.py` | a plant's flare far busier than usual, **silent**: Meteosat-12 symbol in 8+ of the last 3 hours' pictures with 3+ readings at least twice the usual and 5 MW above it. Kept apart from incidents, sent in `meta.flare_activity`, shown in the app as «نشاط حراري أعلى من المعتاد (تجريبي)» with its own thermal video (made by the media worker: `ask_videos`/`take_videos`); never posted. Reviewed nights are replayed once from `rasad/reviews/flares_*.json` |
| `rasad/notify.py`, `rasad/updates.py` | Telegram sending (HTML, replies under the first post); intensity updates |
| `rasad/follow.py` | **silent since 25 Nov 2026** (`follow.post: false`, see `docs/plans/sea_fire_identity.md`). A posted fire at sea followed after the finders lose it: each check reads the newest picture around its last sighting, the warmest spot of open sea within the distance a hull drifts since then (3 km + 3 km/h, at most 20); three pictures in a row (each within 3 km of the one before, the run no faster than a hull drifts, the 3.8 micron red ahead of both short waves by `red_margin` 2.5: sunlit clouds and glint are as bright in the short waves) go out once as a reply «ما زالت الحرارة ظاهرة قرب موضع هذا الحريق» with how far from the last sighting. Replayed: the Hormuz fire of 25 Nov told at 09:20 Baghdad time; four fireless seas on 22 and 25 Nov, nothing. Never an incident, never a verdict, never a cause. `python -m rasad follow --lat .. --lon .. --seen .. --end ..` replays it. On in `config.yaml`, off in `DEFAULTS` |
| `rasad/chains.py` | one fire at sea, one marker, **the app only** (since 25 Nov 2026, phase 1 of `docs/plans/sea_fire_identity.md`): at publish time it adds `chain` and `path` to the app's items of fires at sea and never touches an incident, a verdict or a post. The same fire = the older record seen at most an hour before within 5 km, or both in the same picture within 5 km (`chain.root`, `n`, `since`); another fire = the same picture further apart, or beyond a hull's drift (nothing written); not settled = a longer gap within drifting distance (`chain.maybe`, said as «غير محسوم»). Sightings are a record's own, where its heat was followed to (`follow.track`), and tracks read by hand from the real pictures (`rasad/reviews/sea_track_*.json`). In the app: `chainView` (one marker where the heat is now, older records of the fire keep a small dot with their time), `drawTrails` (solid path, dashed unsettled tie), `chainHTML` (the line on the card and the details). **The channel, silent since 25 Nov 2026 night** (`chains.thread_post` false): at each first post at sea `chains.decide` records in `inc["thread"]` and `state["thread_log"]` whether it would be a reply under an older posted fire (its heat seen in 3+ pictures of the hour before, within 5 km), a post said as «غير محسوم», or a plain post; nothing is posted differently. Not switched on before the log is read, the acting side is built and reviewed (`docs/plans/sea_fire_identity.md`) |
| `rasad/reading.py` | «قراءة رصد»: once a posted event has run its course (quiet an hour, or burning 6 hours) one reply under its post and a section of its page in the app: duration and stretches, strength and its shape, smoke, the spot in NASA's long-term map, how far heat at sea moved, an earlier fire close by, and what the facts look like at a plant («يشبه حرقاً طارئاً للغاز…» / «يشبه حريقاً…»); never a cause (`FORBIDDEN`). On in `config.yaml`, off in `DEFAULTS` (tests) |
| `rasad/geodb.py` | facilities and places from OpenStreetMap (Overpass, Nominatim). `FACILITY_QUERY` is what the map is asked for (also industrial land named or operated as an oil or gas site: `OIL_NAMES`); bump `GEO_VERSION` when it changes, the map is then built again in the background, part by part (`build_geodata`: each part of a tile saved at once, a part that fails twice asked as its four quarters, a server's 'runtime error' is a failure; the public servers took 40-80 s per request on 16 Nov 2026). `python -m rasad osm-near --lat .. --lon ..` shows what the open map holds at a place and what the facility map takes of it. Facilities kept by hand in `config.yaml` → `geodata.extra_facilities` count at once (`extra_facilities`); `geodata.names` gives the right name and country of a place the map names wrongly or in English (`site_names`: Shaybah Refinery → «حقل شيبة»), and an event within 3 km is then told by it («في حقل شيبة (السعودية)», not by the nearest town). Since `GEO_VERSION` 4 the open map's oil and gas wells and pipelines are kept too, in their own file `geo/field.json` (`FIELD_QUERY`, asked in 2-degree boxes, a box given up after 6 failures; water pipes left out) and told in a post («🛢 في الخريطة المفتوحة: …», `geo.field_near`, `inc["field"]`); they are never facilities and decide nothing |
| `rasad/backfill.py`, `repairs.py`, `acquisitions.py` | recovery of missed past slots; reviewed one-off corrections; grouping pixels of one pass |
| `rasad/main.py` | CLI: `cycle`, `media-worker`, `picture-worker`, `satpic-worker`, `satpic` (the picture for any place by hand), `maintain-geo`, `cloud-publish`; diagnosis: `probe`, `pictures`, `seascan`, `hotspots`, `reclip`, `grab` (keep a night's pictures), `follow` (replay following a sea fire), `s2-look` (every Sentinel-2 pass over a place: glowing spots, bright objects on the water, a picture of each; it showed the burning hull at the shore off Khasab, `docs/reviews/hormuz_one_source_20261125.md`), `layers`, `gibs-layers`, `imagery-check` (the app's imagery engine on the real archives: `tools/imagery_check.js`) |
| `web/index.html` | the app: one page, styles and main script (Arabic RTL, Leaflet 1.9.4), rebuilt on 15 Nov 2026 as a map-first platform; since 22 Nov two sections in the top bar (`#secNav`: «الحرائق» and «صور الأقمار», keys 1 and 2, `setSection`), a distance scale and the pointer's coordinates on the map. The map fills the window; one panel (docked beside it from 760 px, a bottom sheet with peek/half/full on phones) holds the tabs الأحداث / المؤشرات / الأرشيف / مواقعي / المصادر and an incident's details (confidence dial, reasons, who saw it, strength chart, printable «تقرير الحدث»); a time slider; backgrounds: map, satellite, NASA's daily picture and its false-colour and night pictures (`NASA_VIEWS`, GIBS tiles); dark and light themes; the sign-in screen and the manager's panel (`RasadAccess`). `web/sw.js` holds the cache name |
| `web/imagery.js`, `web/beforeafter.js` | the satellite-imagery browser («صور الأقمار · قبل وبعد»): Sentinel-2 pictures (10 m) of any place read in the phone itself (engine, no page code) and its screen, with Landsat 8/9 (30 m, from Microsoft's Planetary Computer, signed by a free token) as extra dates. The place is chosen on the map (a point, a drawn rectangle up to 80 km, or «صوّر ما أراه»); a date strip with clear/partly/cloudy marks; one picture, split, blink or side by side; band views (natural, short-wave, false colour, fire) and the NBR/NDVI indices; the change layer, the «أثر احتراق محتمل» layer (dNBR classes), hot points, a point inspector, area measuring; a report as an image or a printed page; a film that is the screen itself (`drawPanes`, shared with `paint`: the display mode, zoom and pan, layer, rings), 1920 points wide from a wide screen, 1080 from a phone (`filmSize`, `filmPlan(mode)`). The place is chosen in the section's workspace in the panel beside the map (`#baHome`: a tap on the map opens its pictures). Since 24 Nov: marks (`v.notes`, a thin rectangle or an arrow in a chosen colour, kept in lat/lon, drawn by `drawNote` on screen, film and photo), the place's ticks can be hidden (`v.ticks`), the area moves with the edge arrows or a drag past the edge (`moveArea`, same dates), and «تفاصيل عالية الدقة» (`BA.hires`) opens the spot on the app's satellite map at zoom 18 (Esri, about 0.5 m, undated). The film carries the name the owner types (`v.filmTitle`), no satellite line, English labels («BEFORE · 20 NOV 2026», `dateEn`) and smooth enlargement of lightly sharpened pictures (`sharpened`, also in the photo); the Copernicus credit stays, as its licence asks. Nothing on the server. Design and limits: `docs/plans/before-after.md` |
| `worker/src/index.js`, `web/access.js` | private app on Cloudflare (Worker `rasad` + Durable Object `Hub`): one **access key per subscriber** tied to one device (a key pair the browser cannot export; the device proves itself every 15 minutes), validity period, forced sign-out, device-change requests, the manager's panel API, data/clips/archive/tool files only for an active subscription, ingest. `access.js` (`RasadAccess`) is the page's only way to the door. Design, API table and limits: `docs/plans/access-keys.md`; the owner's steps: `docs/private-app-setup.md` |
| `scripts/loop.sh`, `scripts/branches.sh` | the ~5.5-hour watching loop; saving to the data branches |
| `.github/workflows/` | `rasad.yml` monitor, `probe.yml` diagnosis on real data (can be started by hand on any branch: it then follows that branch's `tools/probe-request.json`), `deploy-worker.yml` private app, `tests.yml` every test on pull requests and work branches |
| `config.yaml` | regions and alert rules (Arabic comments); defaults are in `engine.DEFAULTS` |
| `tools/exam.py`, `exam/` | the exam: what any version would post, replayed on the stored real events (`exam/events/`, snapshots of the live memory) and the known cases (`exam/cases.json`); `compare <ref>` lists every event whose decision changes. Level 1 keeps each event's facts as stored; levels 2 (sightings as a stream) and 3 (kept pictures) are to come (`exam/README.md`) |
| `docs/` | `reviews/` checked cases; `plans/` agreed designs (start with `plans/before-after.md`; the open one for fires at sea is `plans/sea_fire_identity.md`) |

Big files: read by line range or grep, never whole: `web/index.html` (260 KB), `web/beforeafter.js`
(180 KB), `web/imagery.js` (76 KB), `engine.py` (61 KB), `worker/src/index.js` (45 KB), `brain.py`,
`seawatch.py`, `twopanel.py` (≈35 KB each). App functions to grep in `index.html`: `boot`,
`startApp`, `load`, `render`, `cardHTML`, `drawMarkers`, `route`/`setRoute`, `setTab`, `sheetTo`,
`showFocus`, `openDetail`/`detailHTML`, `reportHTML`, `dashStats`, `timelineView`, `archiveFilter`,
`drawWatch`, `showGate`/`signIn`/`lock`, `openAdmin`/`renderAdmin`, `loadTools`, `presentPicture`;
in `beforeafter.js`: `BA.go`/`BA.route`, `BA.here`/`BA.draw`/`BA.startPick` (the place from the
map), `openViewer`, `loadPair`, `refresh`, `picture`, `paint`, `renderStrip`, `renderReport`,
`printReport`, `reportModel`, `renderShot` (picture), `filmPlan`/`takeFilm` (video), `BA.find`;
in `imagery.js`: `listPasses`, `assessPass`, `loadScene`, `loadBands`, `sampleBand`, `changeOf`,
`hotSpots`, `comboImage`, `indexOf`, `burnOf`, `inspect`.

## Commands
- Tests, all must pass: `python -m pytest -q` (~10 s; `pip install pytest` once if missing) and
  `for f in tests/*.js; do node "$f"; done` (~75 s; the imagery screen's file alone takes ~50 s).
  GitHub runs them too on every pull request.
- The JS tests use a made-up Sentinel-2 archive (`tests/support/archive.js`): no network.
- A check without posting: `python -m rasad --state /tmp/st --site /tmp/si cycle --no-send`
  (needs network to EUMETView/NASA; often blocked in a development session).
- Real-data diagnosis when the network is blocked: the probe workflow (skill `rasad-sources`).
- Health of the live monitor: skill `rasad-live-check` (`python3 .claude/skills/rasad-live-check/check.py`).

## How it runs (do not break it)
- `rasad.yml` runs `scripts/loop.sh` for ~5.5 hours: a check every 150 s plus a media worker, a
  satellite-picture worker (it also runs the silent smoke checks) and a facility-map worker. Workers only write staging files
  (`.rasad-media/`, `.rasad-satpic/`, `.rasad-smoke/`); the check alone posts and owns the incidents. The check and the picture worker read the same European server: while a check reads the official fire products it says so (`util.official_reads`) and the worker waits, and the sea finder takes at most 45 s of a check (measured live on 15 Nov 2026: without both, the official reads fell 40 minutes behind and checks took two minutes). Between checks the loop starts the next one at once, once per reason, when a video a post waits
  for is made (`.rasad-media/awaited`, written by the check) or a night picture of the priority seas
  is out and unread (`sea-ready`, one tiny request every 30 s): on 22 Nov 2026 a first sighting's
  video was ready 19 s after its post and went out 2.5 minutes later. cron-job.org starts the workflow every 10 minutes so the next run is
  always queued. `cancel-in-progress: false`: a check is never cut in half (an alert could go twice).
- A push to `main` touching `web/`, `rasad/`, `scripts/`, `config.yaml`, `requirements.txt` or
  `rasad.yml` makes the loop stop after its current check and the queued run start with the new
  code. So `main` must only receive tested code, in as few pushes as possible.
- A push to `main` touching `worker/` or `web/` redeploys the private app (`deploy-worker.yml`). A real
  deploy moves the tag `private-app-deployed`; a watching loop that still publishes on GitHub stops
  when it moves (`scripts/loop.sh`), so the next run finds the private app and sends to it within
  minutes of the owner's first deploy. The sealed memory's key and the ingest key come from the
  Cloudflare API token: changing that token starts the memory afresh (the owner is told not to).
- Data branches, written only by `scripts/branches.sh` as `rasad-bot`, never by hand:
  `state` (memory: `state.json`, `incidents.json`, `memory.json`, `geo/`), `live` (the app's
  `data/meta.json`, `data/incidents.json`), `gh-pages` (public app), `probe` (diagnosis results).
  Once the private app is live, `state` holds only the sealed `state.enc` and `live`/`gh-pages` close.

## Rules that must keep holding
- One fire = one incident record; ids unique (`tests/test_no_duplicates.py`). A fire at sea can move:
  a sighting within 3 km of the fire's own sighting of the last hour continues it, however far from
  where it began (`brain._track_km`; Hormuz, 23-24 Nov 2026: a burning ship drifted 10 km in 5.5 hours
  and was posted a second time once past 6 km). And one fire is in one place at a time: a sea finder
  spot of the same picture more than 2.5 km from the fire's own spot is another fire, however close
  (`brain._elsewhere_same_picture`: two ships hit near each other within the hour are two fires).
- Verdicts: «مؤكد» = two satellite families, or three consecutive pictures; «مرجّح» = two
  consecutive pictures or a clear NASA detection; «يحتاج تأكيداً» = one sighting. The sea finder
  alone is at most «مرجّح». The day finder never alerts on its own. One faint NASA pixel at night
  at sea (under 2 MW) waits for a second look (unmapped platforms flare like that).
- The confidence number (`assess.py`) lives inside its verdict's band («مؤكد» 75-99, «مرجّح» 50-74,
  «يحتاج تأكيداً» under 50) and never says a cause. Smoke seen adds 5 inside the band; flare markers are not counted.
  At a plant («حرارة غير معتادة») the heat itself is certain, so the number says how far outside the
  plant's habits it is (40-90) under its own label «الثقة بأنه خارج المعتاد» (`confidence_label`).
- Picture layers are asked for at 0.25 km per pixel (never coarser than 0.3): the server answers a
  coarser request from a blurred copy one picture behind, and small fires vanish in it
  (`docs/reviews/hormuz_ras_al_khaimah_20261114.md`). The sea finder's numbers were tuned on real
  nights kept as cuts in `tests/fixtures/sea_*.npz` (`tests/test_sea_real_nights.py`): change a
  threshold only with those tests and a replay of whole nights (skill `rasad-sources`: `grab`).
- A plant's usual flare is posted as «حرارة غير معتادة» only when a reading beats twice the
  plant's own habit: the second-highest day of the month, today left out (`Memory.habit`,
  `memory.unusual_record_ratio`), so that neither one earlier spike nor a fire growing through the
  day or over several days silences the plant. One episode is posted once: not again for the same
  plant within `unusual_repeat_days` unless `unusual_repeat_ratio` times what was posted. Its post
  carries `surge_note`. Replay any change on the real week in `tests/test_assess.py` (`PlantRecord`:
  Shedgum 109→94→80 MW stays one post, Ahmadi 49→66→85→110 MW is posted).
- Heat seen before at a place is its usual heat, not news (`memory.persistent_min_days`), but in open
  water (`open_sea` in `run_cycle`: at sea, no facility or platform within 5 km, no platform field of
  `patterns.platform_area`) only when it burns on most days: a strait where ships burned on a few days
  of the month stays news (`engine.usual_heat`; Hormuz, 23 Nov 2026: 6 earlier days made a ship fire
  seen in 13 pictures «usual», and it never got past its first sighting). At platforms the plain rule
  holds: asked of them too, Zakum and Zirku's usual flares were posted as fires the same night.
  The sea finder's own record of earlier nights (`state["sea_sparkle"]`, 14 nights, ~5 km cells)
  follows the same rule (`seawatch.sparkle_nights`, also for first sightings): specks on 3 earlier
  nights make a pair «a flare, not news» only among platforms; in open water it takes 7 of the 14.
  Hormuz, 24 Nov 2026, 25 km north of Khasab: the ship fire of the night before and specks on three
  other nights held a burning ship's pairs from 22:30 to 00:08 Baghdad time; another channel posted
  it at 00:10, Rasad at 00:10 too (`docs/reviews/hormuz_khasab_20261124.md`).
- **Switched off on 27 Nov 2026** (`seawatch.early_alerts.enabled: false`). At 23:00 it posted the
  heat of the Khasab ship, posted at 18:30, as a new «رصد حراري أولي». It never asks whether a posted
  fire is near: it holds back only a sighting already in an incident from the same picture
  (`earlyalerts._incident_for`), which a first picture never is. (`match_km` and `match_minutes` only
  group first sightings among themselves.) New fires now go out from their second picture. On the
  exam of 28 Nov, the seven first sightings it sent from 21 to 27 Nov were:
  - one gain: the Khasab ship of 23 Nov, 1.5 hours before its normal post;
  - one repeat, or a second tanker (not settled);
  - one most likely a platform flare in Qatar's North Field, before `platform_min_rise`;
  - two of 21 Nov whose truth is not known;
  - two only 1 and 7 minutes ahead of the normal post.

  It stays off until the exam and the owner decide. The rest of this rule describes it when on.
- A first sea sighting from one picture is posted only when `strong` (a rise of 15 with a flame
  glow of 4); a fainter one waits for its second picture. In a field of platforms NASA's long-term map
  knows (`patterns.platform_area`: a usual flare within 6 km, or the spot hot on 3+ dates) one picture
  needs a rise of `early_alerts.platform_min_rise` (30), and sea posts there say so in one line
  (`patterns.platform_words`, «🛢 في منطقة منصات نفط وغاز…»). The case: Qatar's North Field, 24 Nov 2026.
- **Switched off on 25 Nov 2026** (`steady_min_above: 0`; defects and the plan in `docs/plans/sea_fire_identity.md`): heat lit before dark is «hot before» to every rule that looks for new heat. At sea a spot at least
  `seawatch.steady_min_above` (8, levels of the picture's red) above the clear sea in three dark
  pictures in a row, in open water (no platform, no platform field of NASA's map, no heat there on
  earlier days), is told as three sightings (`seawatch.steady_spots`, rows with `steady`): «مرجّح»
  at most, its closer look first, never a first sighting. Replayed: the Hormuz ship fire of 23-24
  Nov at any level 6-12; a whole ordinary night over Hormuz and the southern Gulf at 8: only
  platform fields, all left out. Off in `DEFAULTS` (0). The case: Hormuz, 25 Nov 2026, warm spot
  at sea from 15:10 Baghdad time; the night finder's first pictures found nothing. A strong source
  is drawn as a streak about 4 km long east to west, so a steady spot within `steady_found_km` (6)
  of a spot the finder found in the same picture is that fire, not another (Hormuz, 25 Nov 2026,
  20:10: the west end of a fire being followed was posted as a new one).
- A first post at sea near a fire already posted says so, from the posted fire's sighting at that
  time (or its last): «📌 على بعد 4 كم من حريق في البحر نُشر عند 18:01 وما زال ظاهراً في الصور», or
  «… من آخر موضع لحريق … آخر رصد له قبل 10 ساعات: قد تكون النار نفسها وقد انجرفت» (`brain.near_posted`,
  within 5 km + 3 km/h, at most 20 km and 12 hours; any verdict). It replaces the older line about
  a confirmed fire nearby (`near_confirmed`), which is kept for the reason text.
- The great energy sites on land are read by the same finder at night (`seawatch` boxes with
  `"land": True`, `seawatch.sites`): Saudi Arabia's east coast with Abqaiq, Bahrain, Ras Laffan and
  Mesaieed, Kuwait's Ahmadi/Shuaiba/Zour, Ruwais and Habshan, Jebel Ali, Fujairah, Yanbu, Jeddah, Jizan.
  - **What counts.** On land only a flame counts: a rise of `site_min_rise` (20) with a glow in the
    short waves of `site_min_glow` (4).
  - **What is left out.** A source hot before; a usual flare of NASA's long-term map within
    `site_flare_km` (1); the halo of a stronger source within 3 km.
  - **Clouds.** The 10.5 micron check is asked only when the glow is under `site_sure_glow` (20), and
    then only a sharp warming rejects (a cloud going away). A blaze's smoke rises cold, and no cloud
    glows at night.
  - **Posting.** A site's blaze is news from its second picture («مرجّح», `brain.site_blaze`),
    whatever the facility distances; the flares around never make it routine (`d["site"]` in
    `run_cycle`). Never a first sighting from one picture; the open sea in a site box is the sea
    boxes'.
  - **The case.** Ras Tanura, 25 Nov 2026: a blaze at the terminal from 04:40 to 05:10 Baghdad time
    was in every Meteosat-12 picture and in no official fire symbol (`docs/reviews/ras_tanura_20261125.md`;
    cuts `tests/fixtures/site_*.npz`). Replayed: found in four pictures; two whole ordinary nights
    at all ten sites gave nothing.
- What the sea finder finds alone is posted only once its closer look at the pictures shows it
  (the alert then carries its video): `engine` → `waits_for_closer_look`, `seawatch.closer_look_first`.
  Not shown, it is doubted and stays in the app; without an answer within
  `seawatch.closer_look_wait_min` (15) it goes out as text as before. Two of the finder's first
  three alerts were doubted by that look a minute after they were posted
  (`docs/reviews/taqah_sea_speck_20261116.md`).
- A confirmed fire on land where no heat was seen for a month, neither there nor around
  (`known_days` and `area_days` 0), in the dry countries of the Gulf (`alerts.new_heat_countries`)
  is posted even far from every mapped facility, from its fourth picture in a row or at 20 MW
  (`brain.new_heat`); its post says the map holds no facility there (`new_heat_note`). The case:
  the pumping-station fire of 15 Nov 2026 south of Judah, confirmed at once and never posted
  (`docs/reviews/judah_pumping_station_20261115.md`). Farm countries stay out of this rule.
  Since 16 Nov 2026 (the owner asked for the tool to decide this professionally) a second road
  (`patterns.new_heat_path` 'b'): heat that comes back to a place quiet for a week (at most 2
  earlier days of heat there, at most 1 around, no other event there in the day before) and burns
  on in one stretch (a gap over 3.5 h breaks it): 6 hours and 12 pictures with two families, or 3
  hours and 4 pictures where NOAA tags the place oil and gas; only beyond `near_facility_km`. Its headline says heat, not fire:
  «حرارة جديدة مستمرة [في موقع نفط وغاز] في <الدولة>», and its note says how long the place was
  quiet, how long it burns and «صور الأقمار ترى الحرارة ولا ترى سببها». The case: Abqaiq north,
  15 Nov (`docs/plans/heat_patterns.md`); replayed on all 1,072 stored events it changes only that one.
- NASA's daytime pixels on a mapped solar plant (within 3 km, or NOAA's panel tag) while a Meteosat
  picture read at that time showed nothing are sun glint, not posted (`patterns.solar_glint`,
  `alerts.solar_glint_stop`): the «حريق جديد في الإمارات» post of 11 Nov 2026 was one.
- A first sighting's video keeps the channel's look: when the usual video cannot make out the speck
  by itself it is drawn centred where the finder saw it (`info["trust_center"]`, `twopanel.render`);
  the old close-up (`earlyclip.render`) only when there are no pictures for it (Hormuz, 25 Nov 2026:
  «تم ارسال فيديو بقالب قديم»). The closer look never trusts: it keeps its refusal.
- A first alert sent before its video promises it only as far as it can be kept: «🎞 يُرسل الفيديو
  هنا إن ظهر الحدث بوضوح في الصور.» (`brain.video_note`); some events never show clearly enough.
- The before/after picture goes out once per alerted fire on land, as a reply to its alert, when
  a clear Sentinel-2 pass comes (within `satpic.max_days`); the fire is kept in memory until then.
- A watched place is posted only for a new clear pass with a connected patch of change of at
  least `watch.min_patch_ha`, once, as its own post; its first clear pass is the starting point
  and is never posted. What was handled is in the memory (`state["watch"]`).
- The owner made every trial feature official on 22 Nov 2026 ("جميع الميزات التجريبية يجب اكمالها وجعله
  معتمدة"); none is marked «تجريبي» any more. What each now does, and what it still may not do:
- Smoke (`smoke.py`): seen from a fire that was posted, it goes out once as a reply under that post
  («💨 دخان مرئي من موضع الحرارة», `smoke.due_post`/`smoke.message`, `inc["smoke_posted"]`) and adds 5
  inside the verdict's band of the confidence number. It never changes a verdict. Its record when
  made official: 117 fires checked, smoke on 3, all real heat (Abqaiq and Ras Kanatib confirmed fires).
- Heat patterns: `inc["pattern"]` and its «النمط: …» line (the last of the eight reasons, neutral
  '·', never read by `main_reason`) are shown in the app; a first alert also says the label in one
  line («📈 النمط: …», `brain.pattern_lines`) for «حرارة جديدة مستمرة», «حرارة واسعة الانتشار» and
  «اشتداد في موقع حرارة معتاد» (not on a «حرارة غير معتادة» post, which says it already), with «عدة
  مواقع في وقت متقارب» when tagged. The label never decides whether or what posts
  (`tests/test_patterns.py` → `Silent`): that is decided by the two roads above (`new_heat_path`,
  `solar_glint`), shared with the labels. No label or post names a cause: never «هجوم», «استهداف» or
  «ضربة».
- Signals worth attention: `inc["signals"]` (`patterns.signals_of`) lists facts with their numbers.
  Warnings, counted on the card: heat at a spot of the site that rarely burns (VIIRS pixel within
  500 m of no spot hot on over 2% of the long-term map's dates, confirmed or likely and 5 MW or more),
  a sudden strong start, several places at once, heat spread wide. Calm and wait lines: everything
  else. Each list ends with «هذه علامات تستحق النظر وليست حكماً …». A first alert carries the first
  warning as one line («⚠️ علامة تستحق الانتباه: …»); never a cause, never part of a verdict.
  Since 24 Nov 2026 (the owner's decision) one warning decides a post: a **confirmed** fire on land in
  `alerts.new_heat_countries` carrying «نقطة نادراً ما تشتعل» (`odd_spot`) or «بدأت فجأة وبقوة»
  (`sudden`) is news wherever it is (`brain.sign_worthy`, `alerts.sign_alerts`), with no floor but
  the sign's own (5 MW; the owner asked that a ~7 MW fire like the Jizan power plant's is not left
  out). Replayed on the 985 stored events of 15-24 Nov: 5 more posts (Minagish 12 MW, beside GC-17
  13 MW, near the Wasit power plant 9 MW, a sudden 22 MW start in Saudi Arabia, Bu Hasa 52 MW).
  Checked on 21 real attacks since 2024: 14 lit (`docs/plans/heat_patterns.md`). On the 29 posts
  made before 22 Nov, 9 would have carried a pattern or sign line.
- Flare markers: `meta.flare_activity` is shown in the app («نشاط حراري أعلى من المعتاد»); it is
  never an incident, never posted (a plant's strong surge is posted by the «حرارة غير معتادة» rule),
  never part of a verdict, and never trains the usual heat (`docs/reviews/yanbu_shedgum_20261112.md`).
- Heat posted as heat stays heat in its updates: «تحديث: الحرارة مؤكدة الآن» for a «حرارة غير معتادة» or
  «حرارة …» post (`alert_message`, update), never «الحريق مؤكد» (Shaybah, 25 Nov 2026). A video
  the first post still owes goes with the next update once it is made (`clip_video_pending`).
- «قراءة رصد» (`reading.py`, the owner's wish of 25 Nov 2026: "analyse like you") is facts with
  their numbers and one lean in the channel's own words, only at a plant: an emergency gas flare
  (short, strong from the first readings then down, ended, no smoke) or a fire (smoke, hours, wide);
  otherwise «لا تكفي القياسات…», and at sea no lean at all. It ends with «قراءة آلية من قياسات
  الأقمار؛ الأقمار ترى الحرارة ولا ترى سببها.» and is dropped whole if a cause word slips in.
- What lies around heat on land and what NASA saw there before are told, and decide nothing. Wells and
  pipelines of the open map within 3 km: «🛢 في الخريطة المفتوحة: بئر نفط أو غاز على بعد 1.1 كم…»
  (`patterns.field_words`); a new-heat post then says «لا منشأة كبيرة في الخريطة هنا» instead of «قد
  يكون موقعاً غير مرسوم». NASA's long-term record of the spot: «سجل ناسا منذ آذار 2024: حرارة في هذه
  النقطة في 6 تواريخ من 197» (`patterns.longterm_words`). The case: the Jafurah gas field, 25 Nov
  2026, a 64 MW blaze 1.1 km from wells, posted as «لا منشأة مسجّلة… لم تُرصد حرارة… خلال شهر»
  (`docs/reviews/jafurah_20261125.md`).
- (Silent since 25 Nov 2026, `follow.post: false`.) A posted fire at sea that the finders lost is followed (`follow.py`): «ما زالت الحرارة ظاهرة قرب
  موضع هذا الحريق» once, under its post, after three pictures in a row within drifting distance; a
  platform or a usual flare of NASA's map is never taken for it. It says how far from the last
  sighting and «قد تكون النار نفسها وقد انجرفت مع التيار», never that it is the same fire.
- No «سبب التأخر» line in any post (the owner removed it on 12 Nov 2026).
- Recovered past events appear in the app but are never posted as news. An alert is never sent
  twice (the memory is saved at once after anything is sent).
- A first alert is news only while its newest evidence is younger than `alerts.max_age_hours`. A
  fire that became important only because the rules or the facility map changed must also have
  started within that time (`engine.too_old_for_first_alert`): a heat source burning all day is
  not announced the moment the map learns its facility; it is marked late and shown in the app.
- A NASA pixel counts once whichever road it came by: NOAA's file first, FIRMS hours later
  (`noaafire.Twins`: same source, one to one, 4 minutes and 0.7 km). The same satellite is never a
  second family.
- Every network call inside a check has a time budget (EUMETView is often slow). No unbounded waits.
- Bump when you change: `RULES` (importance rules) · `CLIP_STYLE` (clip look) · `SEA_CHECK`
  (sea finder's closer look) · `TIDY` (re-tidy stored incidents) · `PATTERNS` (heat patterns: label
  every stored event again), all in `engine.py`; the `CACHE`
  name in `web/sw.js` whenever `web/` changes, and with it the same number in `index.html`
  (`APP_V`, `access.js?v=` in its script tag, `TOOLS`) and in `LATER` of `sw.js` (a test checks
  they agree).
- Text: «الخليج الفارسي» (never «الخليج العربي»); fire power written `MW` in Latin letters
  (`brain.mw_ar`); times in Baghdad time (UTC+3); Latin names wrapped by `brain.bidi_safe`; a sea
  is not near itself: «حريق في مياه الخليج الفارسي», but «في البحر قرب الإمارات» (`brain.at_sea`).
  Channel rules in skill `rasad-telegram`.
- Before/after pictures say «تغيّر مرصود», never a cause and never «ضرر مؤكد»; cloud on one of
  the two dates is said, not counted as change. Every request to the archive has a time budget.
  The imagery browser's burn layer is the usual dNBR classes under the name «أثر احتراق محتمل»
  (possible, never certain); the channel's pictures do not use it. Two things were measured on real
  pairs with no fire between them (`docs/reviews/imagery_real_pairs_20261115.md`) and must stay:
  «before» comes from the same track of the satellite as «after» when a clear pass of it is at most
  12 days older (`sameView` in `imagery.js`, `choose` in `beforeafter.js`, `s2.before_for` for the
  channel's fire pictures), and a burn patch counts only from half a hectare with a core of the
  moderate class (`BURN_PATCH_HA`, `BURN_CORE_HA`). Check changes to either with `imagery-check`.
- App: opens on the last 24 hours (3-day and week options); the photo button saves a portrait
  image without the logo; the map stays the main thing (panels never cover it whole, and
  Back/Escape always returns to it without a reload); the private app admits only the manager and
  subscribers holding a valid key on their own device (never weaken: cookie flags, the proof
  window, the allowlist of public files `isPublicPath`, constant-time comparisons). On the private
  app the page adds the imagery scripts only after a sign-in, `sw.js` never keeps `/api/` or
  `/clips/`, and what is typed in the key field goes to the door untouched (the door tidies a key
  itself and compares the manager's code letter by letter).

## Git
- Work on a branch, open a pull request, merge to `main` only with all tests passing (skill
  `rasad-ship`). Never force-push `main`.
- Commits are authored as Claude <noreply@anthropic.com>. Never set the git user to `rasad-bot`
  (only `branches.sh` uses it, for the data branches).
- Commit messages in English, plain words, saying what the owner or the channel will notice.

## Compact instructions
Keep: the task, files changed, test results, decisions taken and still open. Drop: logs, JSON dumps.
