# Heat patterns («أنماط الحرارة»): telling unusual heat at energy sites from their daily flares

Status, 16 Nov 2026:
- **Step 1 is live:** the labels are in the app only.
- **Step 2 is live for two rules** (see «Step 2» below): heat that comes back to a quiet place and
  burns on, and sun glint. The owner asked that the tool decide this professionally.
- **Still app-only:** «واسعة الانتشار» and «عدة مواقع في وقت متقارب».

Code: `rasad/patterns.py`. Tests: `tests/test_patterns.py` (this week's real events and NOAA's real passes).

## Why
The owner asked, after a competitor posted the Abqaiq fire of 15 Nov 2026, for "a smart system that
recognises fires and their pattern, and the fire caused by an attack". Rasad had seen that fire 11 hours
before the competitor's video (incident `20261115-0930_26.164_49.752`: three satellite families, 33 MW,
confidence 99%) and did not post it: the facility map had nothing within 25.6 km, the spot had heat on
1 Nov (`known_days` 1, and `brain.new_heat` wants 0), and away from facilities only 100 MW is news.

## What satellites can and cannot say (research of 16 Nov 2026)
30 events in the region and 30 elsewhere: strikes, accidents and fires at oil and gas sites. 43 claims
were checked; none was refuted and 3 remained uncertain.
- **No signal identifies an attack.** A pumping station burning after a drone strike is as hot as a flare.
  A refinery accident (Ras Laffan, Abadan) has the same profile as a strike. Many strikes leave no heat
  satellites see: Khor Mor (8 Nov 2026, inside Rasad's area), Bapco, Ras Tanura, the Hormuz tankers.
  A strike can even show as heat disappearing when a plant shuts down.
- **What separates heat sources in the data:**
  - How often the exact spot burns. Steady flares appear on 77-95% of sampled dates in NOAA's archive
    since Feb 2024; Judah and Abqaiq north appear on 0%.
  - Its strength against the site's own habit (`Memory.habit`).
  - The footprint of one NASA pass: the number of 375 m pixels, their spread, and the sum of their
    distinct MW.
  - How long it lasts across families.
  - The mapped facilities, solar parks included.
- **What does not separate fires from flares:** onset, flicker, peak MW, day/night share, BT4/BT11,
  NOAA's tag distance.
- **The multi-site signature.** New heat at separate places within hours is the one sign of
  coordinated waves: Abqaiq and Khurais 2019, the Tehran depots in Mar 2026, the East-West pipeline in
  Sep 2026. It is never read as a cause.

## The labels (step 1: silent)
These apply to every land event at or near an energy site: a mapped facility within 10 km, a plant
surge, a spot NOAA tags as oil and gas (`FP_PersistentAnomalyCategory` 1), or anywhere in the dry Gulf
countries (`alerts.new_heat_countries`). They are checked in this order:

| Key | Label | Rule (settings in `engine.DEFAULTS["patterns"]`) |
|---|---|---|
| `solar` | انعكاس محتمل في محطة شمسية | Only daytime NASA pixels, no Meteosat sighting, a mapped solar plant within 3 km (or NOAA tag 3), and a Meteosat picture of the place read successfully within ±20 min of the pass (`geo_read_at_pass`) |
| `wide` | حرارة واسعة الانتشار | The widest VIIRS pass has ≥8 pixels, ≥1.5 km between the farthest two, ≥50 MW of distinct strengths, and ≥3× the place's usual footprint (`usual_px`). A second look (another pass, or 2 Meteosat pictures) must come within 3 h |
| `surge` | اشتداد في موقع حرارة معتاد | The existing `unusual` kind (a steady site far above its own habit) |
| `new_sustained` | حرارة جديدة مستمرة | Confirmed, kind `new`, a dry Gulf country, and one of three paths: (a) today's new-heat rule without its one-picture 20 MW path (`known_days` 0, `area_days` 0, ≥5 MW, ≥4 pictures); (b) `known_days` ≤2, quiet for ≥7 days before (`quiet_days`), `area_days` ≤2, ≥10 MW, ≥2 families, ≥6 h and ≥12 pictures; (b') the same as (b) with NOAA's oil/gas tag, ≥3 h and no picture count |
| `short` | حرارة قصيرة أو متقطعة | Brief (under 3 h and fewer than 4 pictures), or a place that burned again within the week or on other days of the month |
| `busy` | حرارة في منطقة نشطة | Longer heat in an area that burns often (`area_days` ≥ 2) |
| `unclear` | حرارة دون نمط واضح | Anything else in scope: not confirmed yet, or outside the dry Gulf countries |
| tag | عدة مواقع في وقت متقارب | A `new_sustained` or `wide` event when another one, 5-500 km away, started within 3 h. Both starts must be known (`onset_known`: a picture of the same product, read 10-40 min before, showed nothing). After a gap in the readings every place seems to start at once. Plant surges never count. Events older than two days keep their tag and are still found as a partner, so a pair never turns one-sided |

How it is shown:
- **In the app:** the event carries `pattern` = {key, label, reasons, also}. The reasons list ends with
  one neutral line, «النمط: <label> (<reasons>). صور الأقمار ترى الحرارة ولا ترى سببها». It takes the last of
  the eight places, so no earlier line moves. `assess.main_reason`, and so every post, reads only `+` lines.
- **In the archive:** `pattern` (the key).
- **In the live check:** the label counts.

Facts kept for the labels:
- **When an incident is made:**
  - `quiet_days`: days since the place last had heat, from the memory.
  - `usual_px`: the place's usual pixels per pass. It sums the 3×3 cells (~5 km) around the place, from
    `state["footprint_days"]`, which records every VIIRS pass, routine flares included. It is None for
    the first 7 days, so nothing is called wide while it is still being learnt. Big fields show many
    pixels every night: West Qurna 7-30 and Rumaila 20-50. Summing the cells leans high on purpose.
  - `solar_km`.
  - `geo_read_at_pass` or `onset_known`, from `state["slots"]`, which keeps only 6 h.
- **Per NASA pass:** `inc["passes"]`, written as each pixel arrives (`patterns.note_pass`). A pass is one
  satellite within 10 min. It holds the pixel count, the farthest pair, the distinct MW, and NOAA's tag
  `pa`. `inc["polar"]` keeps only 20 readings, too few for a wide pass.
- **Events made before this version:** their `quiet_days` is measured once, along with `memory_days`
  (a reason never claims more days than the memory holds). Until then, such an event counts as quiet
  only if no earlier day had heat. On the first check of a version the passes of every stored event
  are rebuilt from its NASA readings and the event is labelled (`patterns.label_stored`). Bump `PATTERNS`
  in `engine.py` when the rules change, so the stored events are labelled again.
- **A wide pass stays wide** (`inc["wide"]`) once it was seen with its second look. On a fire that
  burns for days, passes beyond twelve and pictures beyond sixty are dropped, and the label must not
  fall back.

## The replay on 9-16 Nov 2026 (`tests/test_patterns.py` → `WeekLabels`)
- **Abqaiq north** (15 Nov): `new_sustained`. Its last heat was 14 days before (1 Nov), it was seen in 60
  pictures over 22 h, and NOAA tags it oil and gas. Posted by step 2's path (b) it would have gone at
  about 15:00-15:15 UTC (12 pictures over 6 h), and by (b') at about 13:15 UTC: 10.5 to 12.5 hours
  before the competitor's video (01:45 UTC on 16 Nov).
- **Judah pumping station and the new heat near Qurayyah** (10 Nov): `new_sustained`, path (a).
- **Harad** (15 Nov): `short`. One 49 MW picture and one NASA pixel, then nothing.
- **Bateen Liwa** (13 Nov): `busy`. 5 pictures in an area with heat on 21 days of the month.
- **The «حريق جديد في الإمارات» post of 11 Nov, 141.6 MW:** `solar`. It was 15 NOAA-20 day pixels in
  one pass, 1.4 km from the mapped Mohammed bin Rashid solar park (Phase 3 - Field A). Meteosat was read
  at 09:20 and 09:30 UTC and showed nothing. Most likely sun glint on the panels.
- **Shedgum and Ahmadi:** `surge`.
- **Uyun** (one NASA pixel) `short` and **Bateen Liwa** `busy`. They were posted because they are at
  mapped facilities, a rule that is unchanged.
- **All 1,047 stored events (16 Nov):** labelling them takes 0.02 s and changes no verdict, priority,
  headline or confidence. 297 get a label: 266 `short`, 19 `surge`, 5 `new_sustained`, 5 `busy` and 2
  `unclear`; the rest are at sea or out of scope. The memory grows by about 0.25 MB (the passes) and
  `footprint_days` by about 0.3 MB after a month.

Before the merge an independent review checked the change from four sides:
- **Silence and cost:** nothing found.
- **Labels:** ten findings, all fixed with a test each:
  - a wide fire lost its label after a day;
  - the tag turned one-sided;
  - stored events had no label;
  - long events were called «قصيرة»;
  - the reasons claimed 30 days the memory did not hold;
  - «بدأت للتو»;
  - «خلال 0 دقيقة»;
  - a start counted as known from another product's reading.
- **`wide`:** no event this week. It is pinned on NOAA's real Abqaiq pass of 26 Jul 2026 (27 pixels over
  3.0 km, 142 MW) and on a West Qurna night that is not wide: 9 pixels over 6.6 km, every night.

## Step 2 (posting): what went live on 16 Nov 2026
The owner said they want the tool to be professional and smart in telling fires apart and sending
them, and that they do not know these things well. The decisions were therefore taken on the data.

**Live:**
- **Road 'b' of `patterns.new_heat_path`:** quiet for a week (`quiet_days` ≥ 7, and no other event
  there in the day before), heat on at most 2 earlier days here and at most 1 around, ≥10 MW, two
  families. Only far from mapped facilities (beyond `near_facility_km`); near one, the facility rules
  decide as before. It must burn on in one stretch (`stretches`: a gap over 3.5 h breaks it), with
  ≥6 h and ≥12 pictures, or ≥3 h and ≥4 pictures where NOAA tags the place oil and gas. Each stretch
  is judged on its own pictures. Two snapshots hours apart lack pictures, and two bursts six hours
  apart are two stretches.
  - Rasad's own reading gaps (none at all 18:45-20:50 UTC on 15 Nov) and Meteosat's weakness for
    small fires by day are not breaks: a third check found that a 1.5 h limit would have missed a
    Judah-like fire and delayed Abqaiq by hours.
  - Abqaiq north: gaps of 3.2, 2.3 and 3 h, one stretch of 22 h with 69 pictures.
  - Headline: «حرارة جديدة مستمرة [في موقع نفط وغاز] في <الدولة>».
  - Note: «🗺 لا منشأة مسجّلة في الخريطة هنا. لم تُرصد حرارة في هذا المكان منذ N يوماً، ورُصدت فيه
    على مدى نحو H ساعة. [تصنّف NOAA هذه النقطة موقع نفط وغاز.] صور الأقمار ترى الحرارة ولا ترى سببها.»
    Where no earlier day had heat it says «خلال N يوماً», with N the days the memory holds.
  - Replay on all 1,072 stored events (9-16 Nov): only Abqaiq north changed.
  - The rebuilt facility map landed at 14:29 UTC the same day. It holds Abqaiq GOSP-3, 1.4 km from that
    heat, so Abqaiq north became news under the existing facility rule. In the replay on the newest
    memory (1,076 events) the new road changes nothing.
  - The road remains for places the map does not hold.
- **`patterns.solar_glint`:** NASA's daytime pixels only, ≥50 MW (Meteosat shows real heat that
  strong), within 3 km of a mapped solar plant, while a Meteosat picture of the place read within
  20 min showed nothing. Such heat is not posted.
  - Never applied to a plant's surge, or at an energy facility (one within 2 km, or nearer than the
    panels). Meteosat misses real daytime fires of 10-40 MW, and 140 mapped energy facilities have
    panels within 3 km: Bahrain's Awali field, Taweelah, Al Ain power station.
  - NOAA's panel tag may stand in for the map only in the app's label.
  - The facts are kept only for events made from 16 Nov on.

A second independent review found that the first version of these two rules went too far. It could
silence a real fire at a plant near panels, post relit heat 2-10 km from a plant as «حريق قرب …»,
call two snapshots hours apart «مستمرة», and say «عادت» where no heat had been. All four are fixed
above and pinned in `tests/test_patterns.py` → `Posting`.

**Not taken:** requiring 4 pictures for a strong single burst.
- Two different satellites already agree when the verdict is «مؤكد».
- Strong fires are the ones that matter most in a strike, and waiting would delay them 30-40 minutes.
- Harad (15 Nov) stays a post under the strong road. It is labelled «حرارة قصيرة أو متقطعة».

**The video line changed:**
- Old: «🎞 الفيديو قيد التجهيز؛ سيُرسل عند توفر صور واضحة للرصد.»
- New: «🎞 يُرسل الفيديو هنا إن ظهر الحدث بوضوح في الصور.»
- The Taqah alert of 16 Nov was promised a video that could not come.

**Still to come, after a record:** the wide note at plants, the multi-site line, and the atlas.

## Step 2 as first proposed (kept for reference)
1. `brain.new_heat`:
   - Add path (b) and (b'), the Abqaiq type.
   - Ask 4 or more pictures in path (a). This drops the one-picture 20 MW path; Harad would not be posted.
   - Settings: `alerts.new_heat_quiet_days`, `new_heat_max_known_days`, `new_heat_sustained_hours`,
     `new_heat_sustained_pictures`.
   - Headline «حرارة جديدة مستمرة في موقع نفط وغاز» when NOAA tags the place, with a line that says
     what is known and «صور الأقمار ترى الحرارة ولا ترى سببها».
2. `alert_worthy` False for `solar`, so sun glint is not posted.
3. `wide`: its line replaces `surge_note` at a plant. It is posted in the focus countries at any distance.
4. Bump `RULES`. Add exact-text tests, and update CLAUDE.md and the `rasad-telegram` skill.

What it costs, said to the owner:
- Path (b) will sometimes post operational burning at unmapped oil sites. The Abqaiq-north spot itself
  is tagged oil/gas by NOAA's 2017 mask.
- Path (a) already posts burns that look like vegetation: Sakaka and Muhafir this week.
- The thresholds of (b) rest on one event.

## Later (research, silent)
- A long-term atlas: NOAA's archive since Feb 2024, sampled dates, giving the share of days a spot
  burns. Steady flares burn on 77-95% of dates; quiet places on 0%.
- The night flame temperature from the VIIRS SDR files.
- The app's pattern chip and archive filter (`web/`, a cache bump).
- Replies under a first post: «عادت الحرارة إلى الموقع نفسه» and «لم تُرصد حرارة منذ …».

## Limits to keep in mind
- **No pattern means an attack.** «حرارة واسعة الانتشار» and «عدة مواقع في وقت متقارب» can be read as
  hints. That is why they stay silent until reviewed, and every line says the pictures do not show the cause.
- **NOAA's persistent-anomaly mask is from 2017.** It misses newer plants, and it tags a plant's own
  flare even during a strike. It may add evidence; it must never take an alert away.
- **Rasad read about 56% of Meteosat slots on 10-16 Nov.** That delays picture counts and creates false
  simultaneous starts; hence `onset_known`.
- **The solar rule depends on the open map's solar parks** and on Meteosat being read at the time.


## Signals worth attention («مؤشرات تستحق الانتباه», 16 Nov 2026, app only)
The owner asked: "how do I know this heat is unusual and comes from an attack?" Satellites cannot say
why; they can say what is unusual about the heat.

Each event on land at or near an energy site gets a short list in the app (`patterns.signals_of`,
`inc["signals"]`). It appears under the verdict, and a badge on the card counts the warnings. Every list
ends with «هذه علامات تستحق النظر وليست حكماً …». Nothing is posted and no decision changes.

**Warnings («يستحق الانتباه», counted):**
- **Heat at a spot of the site that rarely burns.**
  - The test: NASA's VIIRS pixel (375 m) has, within 500 m, no cell hot on more than 2% of the
    long-term map's dates, and Rasad's own record saw heat there on at most 2 days.
  - It is counted only for confirmed or likely heat of 5 MW or more. A faint or unconfirmed speck gets
    the same words as a neutral line.
  - MODIS (1 km) is too coarse for this test.
- **A sudden strong start at a quiet place:** 20 MW or more within half an hour of the first Meteosat
  sighting, while a picture of the same product read 10-40 minutes before showed nothing. It is decided
  once and kept (`start_mw`).
- **Several places at once** (the pattern tag).
- **Heat spread wide in one pass** (`inc["wide"]`).

**Calm lines («يشبه المعتاد»):** heat at a spot that burns on many days, heat in a place with heat on
most days of the month (a plant's surge), and a level the place reached earlier this month (only when
it is not above it now).

**Wait lines:** only Meteosat or MODIS saw it, so the spot is not known yet; or the odd spot is too
faint to count.

**The long-term map of usual heat** (`rasad/data/heat_atlas.json.gz`, `tools/heat_atlas.py`):
- What it holds: NOAA-20/21 fire pixels, read from NOAA's public files on one date in five from 5 Mar
  2024 to 10 Nov 2026 (197 dates). Each 0.005° cell (~500 m) records how many dates it was hot on;
  15,989 cells were hot on 2 dates or more.
- How it is read: only the pixel lists are read, with range requests. In separate processes it takes
  about 10 minutes; h5py lets one thread read at a time.
- Rebuild: every few months. Between rebuilds, Rasad keeps its own day-by-day record of NASA's spots
  near energy sites (`state["spots"]`), so a flare lit after the build is learnt within days.

**Checked before shipping.**
- **Real attacks:** the study's 21 attacks since March 2024 with NASA pixels at the site, each tested
  against the map without the dates within 20 days of it (as the live tool will have it).
  - At 500 m the odd-spot warning lit at **14 of 21**: Hodeidah, Fujairah, Tehran 2026, Salalah,
    Khurais, Abqaiq (Apr and Jul 2026), Lavan, Jazan, Haifa, Khor Mor 2025, Mina al-Ahmadi ×2, and
    South Pars 2025.
  - At 750 m it lit at 10 of 21; at 400 m at the same 14, with more false alarms.
- **Missed:**
  - Ras Laffan, South Pars 2026, Bapco ×2 and Sarsang: the heat fell on usual flare spots.
  - Ras Isa and a Tehran fuel depot: the facility map holds no site there. Next step: fuel depots and
    tank farms in `FACILITY_QUERY`.
- **Accidents:** an accident looks the same. The Shahid Rajaee port explosion and the Barzan blast
  also lit.
- **Ordinary days:** on 15-16 Nov 2026, 4.3% of NASA passes over a mapped facility had a pixel at a
  rare spot.
- **Stored events:** on the 1,074 stored events of 9-16 Nov, the counted warning appears on 5. Four
  are energy sites: Abqaiq north at GOSP-3 (33 MW), Ahmadi's surge (123 MW), Kuwait's GC-17 and a new
  heat in Kuwait. The fifth is Al-Uyun.
- **Not a signal yet:** the change in the before/after picture. It is measured over the whole frame,
  not at the heat, so it would light at almost every event. Next step: the patch at the heat.
