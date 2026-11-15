# Rasad (رصد) · ALFAQAAR FIRMS

Fire monitor for the Gulf region. Every ~2.5 minutes it reads free satellite fire data (European
Meteosat pictures, NASA FIRMS), judges each fire, posts alerts to a Telegram channel and feeds a
phone web app. It runs unattended on GitHub Actions; a private copy of the app runs on Cloudflare.

## Working with the owner
- Reply in Arabic, in plain words. The owner is not a developer: say what changes for them.
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
| `rasad/geocollect.py` | which European pictures each check reads (never-read first, then settled second reads) |
| `rasad/seawatch.py`, `rasad/daywatch.py` | Rasad's own sea finder (night, can alert): reads the Fire Temperature picture at **0.25 km per pixel in tiles** over ten sea boxes, red = 3.8 micron, green/blue = flame glow; and the experimental day finder (first light to last light, sun above −6°, its two boxes at 0.5 km; never alerts alone) |
| `rasad/earlyalerts.py`, `preliminary.py`, `earlyclip.py` | the first sea sighting from one picture («رصد حراري أولي»): posted at once only when unmistakable (`strong`), shown in the app otherwise; its own short video |
| `rasad/pictureworker.py`, `thermal_history.py` | background fetching of the finders' pictures (tiles) into `.rasad-pictures/`; "heat seen here before" for the sea finder |
| `rasad/clip.py`, `twopanel.py`, `mediaqueue.py` | the thermal clips, rendered by a separate media worker |
| `rasad/s2.py`, `rasad/satpic.py` | Sentinel-2 reader (same engine as `web/imagery.js`, numpy) and the before/after pictures for the channel: under an alert when a clear pass comes, and for the watched places of `config.yaml` → `watch.places` when a new clear pass shows a patch of change (queue, worker, picture, captions) |
| `rasad/smoke.py` | smoke as a second witness, **silent for now**: by day, a streak in the 500 m visible pictures counts only when new, anchored at the heat, one-directional in 3+ pictures and not cold. Recorded on the incident for the app; never posted, never part of a verdict |
| `rasad/flares.py` | a plant's flare far busier than usual, **silent**: Meteosat-12 symbol in 8+ of the last 3 hours' pictures with 3+ readings at least twice the usual and 5 MW above it. Kept apart from incidents, sent in `meta.flare_activity`, shown in the app as «نشاط حراري أعلى من المعتاد (تجريبي)» with its own thermal video (made by the media worker: `ask_videos`/`take_videos`); never posted. Reviewed nights are replayed once from `rasad/reviews/flares_*.json` |
| `rasad/notify.py`, `rasad/updates.py` | Telegram sending (HTML, replies under the first post); intensity updates |
| `rasad/geodb.py` | facilities and places from OpenStreetMap (Overpass, Nominatim) |
| `rasad/backfill.py`, `repairs.py`, `acquisitions.py` | recovery of missed past slots; reviewed one-off corrections; grouping pixels of one pass |
| `rasad/main.py` | CLI: `cycle`, `media-worker`, `picture-worker`, `satpic-worker`, `satpic` (the picture for any place by hand), `maintain-geo`, `cloud-publish`; diagnosis: `probe`, `pictures`, `seascan`, `hotspots`, `reclip`, `grab` (keep a night's pictures), `layers`, `gibs-layers`, `imagery-check` (the app's imagery engine on the real archives: `tools/imagery_check.js`) |
| `web/index.html` | the app: one page, styles and main script (Arabic RTL, Leaflet 1.9.4), rebuilt on 15 Nov 2026 as a map-first platform. The map fills the window; one panel (docked beside it from 760 px, a bottom sheet with peek/half/full on phones) holds the tabs الأحداث / المؤشرات / الأرشيف / مواقعي / المصادر and an incident's details (confidence dial, reasons, who saw it, strength chart, printable «تقرير الحدث»); a time slider; backgrounds: map, satellite, NASA's daily picture and its false-colour and night pictures (`NASA_VIEWS`, GIBS tiles); dark and light themes; the sign-in screen and the manager's panel (`RasadAccess`). `web/sw.js` holds the cache name |
| `web/imagery.js`, `web/beforeafter.js` | the satellite-imagery browser («صور الأقمار · قبل وبعد»): Sentinel-2 pictures (10 m) of any place read in the phone itself (engine, no page code) and its screen, with Landsat 8/9 (30 m, from Microsoft's Planetary Computer, signed by a free token) as extra dates. The place is chosen on the map (a point, a drawn rectangle up to 80 km, or «صوّر ما أراه»); a date strip with clear/partly/cloudy marks; one picture, split, blink or side by side; band views (natural, short-wave, false colour, fire) and the NBR/NDVI indices; the change layer, the «أثر احتراق محتمل» layer (dNBR classes), hot points, a point inspector, area measuring; a report as an image or a printed page. Nothing on the server. Design and limits: `docs/plans/before-after.md` |
| `worker/src/index.js`, `web/access.js` | private app on Cloudflare (Worker `rasad` + Durable Object `Hub`): one **access key per subscriber** tied to one device (a key pair the browser cannot export; the device proves itself every 15 minutes), validity period, forced sign-out, device-change requests, the manager's panel API, data/clips/archive/tool files only for an active subscription, ingest. `access.js` (`RasadAccess`) is the page's only way to the door. Design, API table and limits: `docs/plans/access-keys.md`; the owner's steps: `docs/private-app-setup.md` |
| `scripts/loop.sh`, `scripts/branches.sh` | the ~5.5-hour watching loop; saving to the data branches |
| `.github/workflows/` | `rasad.yml` monitor, `probe.yml` diagnosis on real data (can be started by hand on any branch: it then follows that branch's `tools/probe-request.json`), `deploy-worker.yml` private app, `tests.yml` every test on pull requests and work branches |
| `config.yaml` | regions and alert rules (Arabic comments); defaults are in `engine.DEFAULTS` |
| `docs/` | `reviews/` checked cases; `plans/` agreed designs (start with `plans/before-after.md`) |

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
  (`.rasad-media/`, `.rasad-satpic/`, `.rasad-smoke/`); the check alone posts and owns the incidents. The check and the picture worker read the same European server: while a check reads the official fire products it says so (`util.official_reads`) and the worker waits, and the sea finder takes at most 45 s of a check (measured live on 15 Nov 2026: without both, the official reads fell 40 minutes behind and checks took two minutes). cron-job.org starts the workflow every 10 minutes so the next run is
  always queued. `cancel-in-progress: false`: a check is never cut in half (an alert could go twice).
- A push to `main` touching `web/`, `rasad/`, `scripts/`, `config.yaml`, `requirements.txt` or
  `rasad.yml` makes the loop stop after its current check and the queued run start with the new
  code. So `main` must only receive tested code, in as few pushes as possible.
- A push to `main` touching `worker/` or `web/` redeploys the private app (`deploy-worker.yml`).
- Data branches, written only by `scripts/branches.sh` as `rasad-bot`, never by hand:
  `state` (memory: `state.json`, `incidents.json`, `memory.json`, `geo/`), `live` (the app's
  `data/meta.json`, `data/incidents.json`), `gh-pages` (public app), `probe` (diagnosis results).
  Once the private app is live, `state` holds only the sealed `state.enc` and `live`/`gh-pages` close.

## Rules that must keep holding
- One fire = one incident record; ids unique (`tests/test_no_duplicates.py`).
- Verdicts: «مؤكد» = two satellite families, or three consecutive pictures; «مرجّح» = two
  consecutive pictures or a clear NASA detection; «يحتاج تأكيداً» = one sighting. The sea finder
  alone is at most «مرجّح». The day finder never alerts on its own. One faint NASA pixel at night
  at sea (under 2 MW) waits for a second look (unmapped platforms flare like that).
- The confidence number (`assess.py`) lives inside its verdict's band («مؤكد» 75-99, «مرجّح» 50-74,
  «يحتاج تأكيداً» under 50) and never says a cause. Smoke and the flare markers are not counted in it.
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
- A first sea sighting from one picture is posted only when `strong` (a rise of 15 with a flame
  glow of 4); a fainter one waits for its second picture.
- A confirmed fire on land where no heat was seen for a month, neither there nor around
  (`known_days` and `area_days` 0), in the dry countries of the Gulf (`alerts.new_heat_countries`)
  is posted even far from every mapped facility, from its fourth picture in a row or at 20 MW
  (`brain.new_heat`); its post says the map holds no facility there (`new_heat_note`). The case:
  the pumping-station fire of 15 Nov 2026 south of Judah, confirmed at once and never posted
  (`docs/reviews/judah_pumping_station_20261115.md`). Farm countries stay out of this rule.
- The before/after picture goes out once per alerted fire on land, as a reply to its alert, when
  a clear Sentinel-2 pass comes (within `satpic.max_days`); the fire is kept in memory until then.
- A watched place is posted only for a new clear pass with a connected patch of change of at
  least `watch.min_patch_ha`, once, as its own post; its first clear pass is the starting point
  and is never posted. What was handled is in the memory (`state["watch"]`).
- Smoke findings are silent: `inc["smoke"]` is shown in the app as «دخان محتمل (تجريبي)» and
  counted by the live check; nothing may post it or let it change a verdict until its record over
  real fires has been reviewed with the owner (`docs/reviews/smoke_and_satellites_20261110.md`).
- Flare markers are silent: `meta.flare_activity` is shown in the app only; it is never an incident,
  never posted, never part of a verdict, and never trains the usual heat, until its record has been
  reviewed with the owner (`docs/reviews/yanbu_shedgum_20261112.md`).
- No «سبب التأخر» line in any post (the owner removed it on 12 Nov 2026).
- Recovered past events appear in the app but are never posted as news. An alert is never sent
  twice (the memory is saved at once after anything is sent).
- Every network call inside a check has a time budget (EUMETView is often slow). No unbounded waits.
- Bump when you change: `RULES` (importance rules) · `CLIP_STYLE` (clip look) · `SEA_CHECK`
  (sea finder's closer look) · `TIDY` (re-tidy stored incidents), all in `engine.py`; the `CACHE`
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
