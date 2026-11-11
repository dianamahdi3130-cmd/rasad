# Rasad (رصد) · ALFAQAAR FIRMS

Fire monitor for the Gulf region. Every ~2.5 minutes it reads free satellite fire data (European
Meteosat pictures, NASA FIRMS), judges each fire, posts alerts to a Telegram channel and feeds a
phone web app. It runs unattended on GitHub Actions; a private copy of the app runs on Cloudflare.

## Working with the owner
- Reply in Arabic, in plain words. The owner is not a developer: say what changes for them.
- Secrets live only in GitHub repository secrets: `FIRMS_MAP_KEY`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `RASAD_ADMIN_CODE` (and a
  cron-job.org token). Never ask for their values, print them, or write them anywhere.
- Nothing is posted to the channel from a development session (no Telegram token here, and keep
  it that way). Preview messages with `deliver(..., send=False)` or the tests.

## Map
| Path | What it is |
|---|---|
| `rasad/engine.py` | one check, `run_cycle`: collect → judge → alert → publish; `DEFAULTS` config; version constants |
| `rasad/brain.py` | incidents: `find_incident`, `add_to_incident`, `verdict_of`, `headline`, `alert_message` (channel text), `Memory` (usual heat) |
| `rasad/sources.py`, `rasad/wms.py` | EUMETView fire symbols (`detect_geo_layer`, `fetch_frp`), NASA FIRMS (`firms_fetch`); WMS client with time budgets, `Availability` |
| `rasad/geocollect.py` | which European pictures each check reads (never-read first, then settled second reads) |
| `rasad/seawatch.py`, `rasad/daywatch.py` | Rasad's own sea finder (night, can alert) and experimental day finder (never alerts alone) |
| `rasad/clip.py`, `twopanel.py`, `mediaqueue.py` | the thermal clips, rendered by a separate media worker |
| `rasad/s2.py`, `rasad/satpic.py` | Sentinel-2 reader (same engine as `web/imagery.js`, numpy) and the before/after pictures for the channel: under an alert when a clear pass comes, and for the watched places of `config.yaml` → `watch.places` when a new clear pass shows a patch of change (queue, worker, picture, captions) |
| `rasad/smoke.py` | smoke as a second witness, **silent for now**: by day, a streak in the 500 m visible pictures counts only when new, anchored at the heat, one-directional in 3+ pictures and not cold. Recorded on the incident for the app; never posted, never part of a verdict |
| `rasad/notify.py`, `rasad/updates.py` | Telegram sending (HTML, replies under the first post); intensity updates |
| `rasad/geodb.py` | facilities and places from OpenStreetMap (Overpass, Nominatim) |
| `rasad/backfill.py`, `repairs.py`, `acquisitions.py` | recovery of missed past slots; reviewed one-off corrections; grouping pixels of one pass |
| `rasad/main.py` | CLI: `cycle`, `media-worker`, `satpic-worker`, `satpic` (the picture for any place by hand), `maintain-geo`, `probe`, `pictures`, `seascan`, `hotspots`, `reclip`, `cloud-publish` |
| `web/index.html` | the app's page, styles and main script (Arabic RTL, Leaflet 1.9.4). The map has three backgrounds: map, satellite, and NASA's daily picture (`DAILY`, GIBS tiles, 250 m). `web/sw.js` holds the cache name |
| `web/imagery.js`, `web/beforeafter.js` | «قبل وبعد»: Sentinel-2 pictures (10 m) of any place read in the phone itself (engine, no page code) and its screen, with Landsat 8/9 (30 m, from Microsoft's Planetary Computer, signed by a free token) as extra dates. Nothing on the server. Design and limits: `docs/plans/before-after.md` |
| `worker/src/index.js` | private app on Cloudflare (Worker + Durable Object `Hub`): logins bound to a device, data, clips, admin, ingest |
| `scripts/loop.sh`, `scripts/branches.sh` | the ~5.5-hour watching loop; saving to the data branches |
| `.github/workflows/` | `rasad.yml` monitor, `probe.yml` diagnosis on real data, `deploy-worker.yml` private app |
| `config.yaml` | regions and alert rules (Arabic comments); defaults are in `engine.DEFAULTS` |
| `docs/` | `reviews/` checked cases; `plans/` agreed designs (start with `plans/before-after.md`) |

Big files: read by line range or grep, never whole: `engine.py` (61 KB), `web/index.html` (66 KB),
`brain.py`, `seawatch.py`, `twopanel.py` (≈35 KB each), `web/imagery.js`, `web/beforeafter.js`
(≈50 KB each). App functions to grep in `index.html`: `load`, `render`, `drawMarkers`, `route`,
`showFocus`, `openDetail`, `renderPicture`, `presentPicture`, `startApp`, `openAdmin`; in
`beforeafter.js`: `openViewer`, `start`, `tick`, `loadPair`, `rebuild`, `paint`, `renderFind`,
`renderShot` (picture), `filmPlan`/`takeFilm` (video), `BA.find` (the top search field);
in `imagery.js`: `listPasses`, `assessPass`, `loadScene`, `sampleBand`, `changeOf`, `hotSpots`.

## Commands
- Tests, ~3 seconds, all must pass: `python -m pytest -q` and `for f in tests/*.js; do node "$f"; done`
- The JS tests use a made-up Sentinel-2 archive (`tests/support/archive.js`): no network.
- A check without posting: `python -m rasad --state /tmp/st --site /tmp/si cycle --no-send`
  (needs network to EUMETView/NASA; often blocked in a development session).
- Real-data diagnosis when the network is blocked: the probe workflow (skill `rasad-sources`).
- Health of the live monitor: skill `rasad-live-check` (`python3 .claude/skills/rasad-live-check/check.py`).

## How it runs (do not break it)
- `rasad.yml` runs `scripts/loop.sh` for ~5.5 hours: a check every 150 s plus a media worker, a
  satellite-picture worker (it also runs the silent smoke checks) and a facility-map worker. Workers only write staging files
  (`.rasad-media/`, `.rasad-satpic/`, `.rasad-smoke/`); the check alone posts and owns the incidents. cron-job.org starts the workflow every 10 minutes so the next run is
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
  alone is at most «مرجّح». The day finder never alerts on its own.
- The before/after picture goes out once per alerted fire on land, as a reply to its alert, when
  a clear Sentinel-2 pass comes (within `satpic.max_days`); the fire is kept in memory until then.
- A watched place is posted only for a new clear pass with a connected patch of change of at
  least `watch.min_patch_ha`, once, as its own post; its first clear pass is the starting point
  and is never posted. What was handled is in the memory (`state["watch"]`).
- Smoke findings are silent: `inc["smoke"]` is shown in the app as «دخان محتمل (تجريبي)» and
  counted by the live check; nothing may post it or let it change a verdict until its record over
  real fires has been reviewed with the owner (`docs/reviews/smoke_and_satellites_20261110.md`).
- Recovered past events appear in the app but are never posted as news. An alert is never sent
  twice (the memory is saved at once after anything is sent).
- Every network call inside a check has a time budget (EUMETView is often slow). No unbounded waits.
- Bump when you change: `RULES` (importance rules) · `CLIP_STYLE` (clip look) · `SEA_CHECK`
  (sea finder's closer look) · `TIDY` (re-tidy stored incidents), all in `engine.py`; the `CACHE`
  name in `web/sw.js` whenever `web/` changes, and with it the same number in `?v=` of
  `imagery.js` and `beforeafter.js` (in `index.html` and in `sw.js`; a test checks they agree).
- Text: «الخليج الفارسي» (never «الخليج العربي»); fire power written `MW` in Latin letters
  (`brain.mw_ar`); times in Baghdad time (UTC+3); Latin names wrapped by `brain.bidi_safe`.
  Channel rules in skill `rasad-telegram`.
- Before/after pictures say «تغيّر مرصود», never a cause and never «ضرر مؤكد»; cloud on one of
  the two dates is said, not counted as change. Every request to the archive has a time budget.
- App: opens on the last 24 hours (3-day and week options); the photo button saves a portrait
  image without the logo; the private app admits only the manager and invited staff.

## Git
- Work on a branch, open a pull request, merge to `main` only with all tests passing (skill
  `rasad-ship`). Never force-push `main`.
- Commits are authored as Claude <noreply@anthropic.com>. Never set the git user to `rasad-bot`
  (only `branches.sh` uses it, for the data branches).
- Commit messages in English, plain words, saying what the owner or the channel will notice.

## Compact instructions
Keep: the task, files changed, test results, decisions taken and still open. Drop: logs, JSON dumps.
