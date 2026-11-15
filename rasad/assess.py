"""How sure are we, and why: a confidence score with its reasons for every incident.

The verdict («مؤكد» / «مرجّح» / «يحتاج تأكيداً», brain.verdict_of) stays the rule the channel
follows. This module adds, on top of it and never against it:
  * the kind of event: a fire, a plant whose usual flare burns harder than ever seen there, or a
    first speck still waiting for a second look;
  * a confidence number (0-100) with the plain reasons for and against, so a reader sees what
    the system leaned on instead of trusting a label;
  * how serious the event would be if real (place and strength);
  * who saw it and when (every source with its first and last sighting).
Nothing here posts anything. Smoke findings and the silent flare markers are not counted: their
records are still under review with the owner.
"""
from __future__ import annotations

from .util import parse_time

OFFICIAL = {"mtg_frp": "mtg", "iodc_fire": "msg_iodc"}
NASA = frozenset({"viirs", "modis", "landsat"})
LEVELS = (("عالية", 75), ("متوسطة", 50), ("منخفضة", 0))
# the number never contradicts the verdict: each verdict has its own band, the number refines it
BAND = {"مؤكد": (75, 99), "مرجّح": (50, 74), "يحتاج تأكيداً": (5, 49)}
CATEGORY_AR = {"fire": "حريق", "industrial": "نشاط حراري في منشأة", "candidate": "بقعة حرارية أولية"}
# What the number is about. At a plant the heat itself is certain (its verdict says so); the number says
# how sure we are that it is outside the plant's habits, so it has its own label and its own range.
CONFIDENCE_AR = {"fire": "الثقة", "industrial": "الثقة بأنه خارج المعتاد", "candidate": "الثقة"}
SOURCE_AR = {
    "mtg_frp": "Meteosat-12 · منتج الحرائق الرسمي",
    "iodc_fire": "Meteosat-9 فوق المحيط الهندي · منتج الحرائق الرسمي",
    "mtg_spot": "باحث رصد الليلي · صور Meteosat-12",
    "mtg_day_spot": "باحث رصد النهاري (تجريبي) · صور Meteosat-12",
    "VIIRS_NOAA20_NRT": "NOAA-20 · VIIRS (ناسا)",
    "VIIRS_NOAA21_NRT": "NOAA-21 · VIIRS (ناسا)",
    "VIIRS_SNPP_NRT": "Suomi-NPP · VIIRS (ناسا)",
    "MODIS_NRT": "Terra/Aqua · MODIS (ناسا)",
}


def level_of(score):
    return next(name for name, low in LEVELS if score >= low)


def _mw(v):
    return (f"{v:.1f}" if v < 10 else f"{v:.0f}") + " MW"


def _count(n, one, two, few, many=None):
    """Arabic counting: one, two, 3-10 with the plural, 11 and more with the singular form."""
    if n == 1:
        return one
    if n == 2:
        return two
    return f"{n} {few}" if 3 <= n <= 10 or many is None else f"{n} {many}"


def _polar_grade(p):
    """NASA's own confidence as 3 clear / 2 ordinary / 1 weak (VIIRS: h/n/l, MODIS: 0-100)."""
    c = str(p.get("conf") if p.get("conf") is not None else "").strip().lower()
    if c in ("h", "high"):
        return 3
    if c in ("n", "nominal"):
        return 2
    if c in ("l", "low"):
        return 1
    try:
        v = float(c)
    except ValueError:
        return 2 if (p.get("frp") or 0) >= 10 else 1
    return 3 if v >= 80 else 2 if v >= 40 else 1


def _slots(inc, src):
    """Distinct picture times in which one source saw the incident."""
    rows = [o["t"] for o in inc.get("observations") or [] if isinstance(o, dict) and o.get("src") == src]
    if not rows and set(inc.get("srcs") or []) == {src}:   # records older than the sighting list
        rows = list(inc.get("geo_slots") or [])
    return sorted(set(rows))


def category_of(inc):
    if inc.get("kind") == "unusual":
        return "industrial"
    if inc.get("preliminary") or inc.get("kind") == "thermal_candidate":
        return "candidate"
    return "fire"


def sources_of(inc):
    """Every source that saw it, with its first and last sighting, how many, and its strongest."""
    out = {}
    for o in inc.get("observations") or []:
        if not isinstance(o, dict) or not o.get("src") or not o.get("t"):
            continue
        row = out.setdefault(o["src"], {"key": o["src"], "name": SOURCE_AR.get(o["src"], o["src"]),
                                        "family": o.get("family"), "first": o["t"], "last": o["t"],
                                        "n": 0, "max_frp": None})
        row["first"], row["last"] = min(row["first"], o["t"]), max(row["last"], o["t"])
        row["n"] += 1
        if o.get("frp") is not None:
            row["max_frp"] = round(max(row["max_frp"] or 0.0, float(o["frp"])), 1)
    if not out:   # older records: only the family names and the picture times are known
        for src in inc.get("srcs") or []:
            slots = _slots(inc, src) or [inc["first"]]
            out[src] = {"key": src, "name": SOURCE_AR.get(src, src), "family": None,
                        "first": slots[0], "last": slots[-1], "n": len(slots), "max_frp": None}
    return sorted(out.values(), key=lambda r: r["first"])


def timeline_of(inc, limit=60):
    """The sightings in order: [time, source key, MW or None]. The newest are kept."""
    rows = [[o["t"], o["src"], None if o.get("frp") is None else round(float(o["frp"]), 1)]
            for o in inc.get("observations") or [] if isinstance(o, dict) and o.get("src") and o.get("t")]
    return sorted(rows, key=lambda r: r[0])[-limit:]


def agreement_of(sources):
    fams = {s.get("family") for s in sources if s.get("family")}
    if len(fams) >= 2:
        first = min(sources, key=lambda s: s["first"])
        later = min((s for s in sources if s.get("family") != first.get("family")), key=lambda s: s["first"])
        gap = (parse_time(later["first"]) - parse_time(first["first"])).total_seconds() / 60
        after = "في الوقت نفسه" if gap < 10 else f"بعد {gap:.0f} دقيقة" if gap < 90 else f"بعد {gap / 60:.1f} ساعة"
        return (f"اتفقت {_count(len(fams), 'عائلة', 'عائلتان', 'عائلات')} من الأقمار: "
                f"أول من رآه {first['name']}، ثم {later['name']} {after}")
    if len(sources) >= 2:
        return "رصدته أكثر من أداة على صور القمر نفسه؛ لم يؤكده قمر مستقل بعد"
    if sources:
        return f"مصدر واحد حتى الآن: {sources[0]['name']}"
    return ""


def severity_of(inc, cfg, category=None):
    """How serious it would be if real: the place it burns in and how hard. A ship or a plant on
    fire matters at any strength; an unconfirmed faint speck does not rank high anywhere."""
    category = category or category_of(inc)
    mw = float(inc.get("max_frp") or 0)
    f = inc.get("facility") or {}
    at_facility = f.get("km") is not None and f["km"] <= float(cfg["alerts"].get("at_facility_km", 2))
    at_sea = inc.get("place") in ("sea", "coast")
    likely = inc.get("verdict") in ("مؤكد", "مرجّح")
    if category == "industrial":
        return "مرتفع" if mw >= 100 else "متوسط"
    if mw >= 200 or (mw >= 50 and (at_sea or at_facility)):
        return "حرج"
    if mw >= 50 or (at_sea and likely and inc.get("priority") == "high") or (at_facility and mw >= 10):
        return "مرتفع"
    if mw >= 10 or ((at_sea or at_facility) and inc.get("priority") == "high"):
        return "متوسط"
    return "منخفض"


def _fire_evidence(inc, cfg):
    score, ev = 0, []

    def add(points, sign, text):
        nonlocal score
        score += points
        ev.append({"s": sign, "text": text})

    fams = set(inc.get("families") or [])
    # the official European fire products
    seen_official = []
    for src, family in OFFICIAL.items():
        n = len(_slots(inc, src))
        if not n:
            continue
        seen_official.append(family)
        name = "Meteosat-12" if src == "mtg_frp" else "Meteosat-9 فوق المحيط الهندي"
        add(30 if n == 1 else 50 if n == 2 else 65, "+",
            f"منتج الحرائق الرسمي لقمر {name} وضع رمز حريق في {_count(n, 'صورة واحدة', 'صورتين', 'صور', 'صورة')}")
    # NASA's polar satellites
    polar = [p for p in inc.get("polar") or [] if isinstance(p, dict)]
    if polar:
        best = max(_polar_grade(p) for p in polar)
        add({3: 50, 2: 35, 1: 10}[best], "+" if best > 1 else "·",
            {3: "رصدته أقمار ناسا بثقة عالية", 2: "رصدته أقمار ناسا بثقة عادية",
             1: "رصدته أقمار ناسا بثقة منخفضة فقط"}[best])
        passes = sorted({p["t"][:13] for p in polar if p.get("t")})
        if len(passes) >= 2:
            add(10, "+", f"تكرر في {_count(len(passes), 'مرور', 'مرورين', 'مرورات', 'مروراً')} لأقمار ناسا")
        if any(p.get("night") for p in polar) and best > 1:
            add(5, "+", "رُصد ليلاً، فلا انعكاس شمس يشبه الحريق")
    # Rasad's own finders on the Meteosat-12 pictures
    night = len(set(inc.get("own_night_slots") or [])) or len(_slots(inc, "mtg_spot"))
    if night:
        add(20 if night == 1 else 35 if night == 2 else 45, "+",
            f"باحث رصد وجد بقعة حرارية ثابتة ليلاً في {_count(night, 'صورة واحدة', 'صورتين متتاليتين', 'صور متتالية', 'صورة متتالية')}")
        if inc.get("flame_glow"):
            add(15, "+", "ظهر وهج لهب في القناتين القصيرتين (1.6 و2.2 ميكرومتر)، وهذا لا تصنعه الغيوم ليلاً")
    day = len(_slots(inc, "mtg_day_spot"))
    if day:
        add(10, "·", "الباحث النهاري التجريبي وجد بقعة؛ ضوء الشمس قد يخدعه فلا يكفي وحده")
    if len(fams) >= 2:
        add(15, "+", "أكثر من قمر مستقل رأى الحرارة في المكان نفسه")
    mw = float(inc.get("max_frp") or 0)
    if mw >= 100:
        add(10, "+", f"شدة عالية: {_mw(mw)}")
    elif mw >= 30:
        add(6, "+", f"شدة واضحة: {_mw(mw)}")
    if inc.get("near_confirmed"):
        add(5, "+", "رُصد حريق مؤكد آخر في البحر قريباً منه قبل ساعات")
    # what speaks against it
    known = int(inc.get("known_days") or 0)
    if known >= 1:
        add(-8 if known == 1 else -20, "-",
            f"سُجّلت حرارة في هذا المكان في {_count(known, 'يوم واحد', 'يومين', 'أيام', 'يوماً')} من الشهر الماضي")
    f = inc.get("facility") or {}
    if inc.get("place") == "sea" and f.get("code") == "platform" and (f.get("nearest_km", f.get("km")) or 99) <= 3:
        add(-20, "-", "ملاصق لمنصة نفطية: قد تكون شعلتها")
    area = int(inc.get("area_days") or 0)
    if inc.get("place") == "land" and area >= int(cfg["alerts"].get("busy_area_days", 2)) \
            and not (f.get("km") is not None and f["km"] <= float(cfg["alerts"].get("at_facility_km", 2))):
        add(-15, "-", "منطقة تتكرر فيها الحرارة (حرق زراعي أو شعلات قريبة)")
    if inc.get("unseen_close"):
        add(-35, "-", "لم تظهر البقعة بوضوح عند تكبير الصور")
    if inc.get("association_uncertain"):
        add(-15, "-", "قريب من حدث سابق ولم يتضح إن كان امتداداً له")
    sightings = max(int(inc.get("n") or 0), len(inc.get("observations") or []))
    if sightings <= 1 and not polar:
        add(-5, "·", "رصدة واحدة حتى الآن")
    return score, ev


def _industrial_evidence(inc, cfg):
    score, ev = 45, []
    known = int(inc.get("known_days") or 0)
    ev.append({"s": "·", "text": "في هذا الموقع حرارة معتادة (شعلة أو مصنع) ظهرت في "
               f"{_count(known, 'يوم واحد', 'يومين', 'أيام', 'يوماً')} من الشهر الماضي"})
    mw, typical, record = float(inc.get("max_frp") or 0), inc.get("typical_frp"), inc.get("site_record")
    if record:
        ratio = mw / max(float(record), 0.1)
        score += min(25, int(8 * max(0.0, ratio - 1)))
        ev.append({"s": "+", "text": f"الشدة {_mw(mw)}، وأعلى ما سُجّل هنا قبلها خلال شهر {_mw(float(record))}"})
    elif typical:
        ev.append({"s": "+", "text": f"الشدة {_mw(mw)}، والمعتاد هنا نحو {_mw(float(typical))}"})
    fams = set(inc.get("families") or [])
    if len(fams) >= 2:
        score += 15
        ev.append({"s": "+", "text": "قاسه أكثر من قمر"})
    slots = len(inc.get("geo_slots") or [])
    if slots >= 3:
        score += 10
        ev.append({"s": "+", "text": f"استمر في {_count(slots, 'صورة', 'صورتين', 'صور متتالية', 'صورة متتالية')}"})
    ev.append({"s": "-", "text": "قد يكون حرق غاز تشغيلياً أو حريقاً: صور الأقمار لا تفرّق بينهما"})
    return score, ev


def assess(inc, cfg):
    """Fill the assessment fields of one incident (after its verdict is known)."""
    category = category_of(inc)
    if category == "industrial":
        score, ev = _industrial_evidence(inc, cfg)
        low, high = 40, 90
    elif category == "candidate":
        # a first night speck of the sea finder, shown before a second picture agrees
        score, low, high = 30, 5, 49
        ev = [{"s": "+", "text": "باحث رصد وجد بقعة حرارية جديدة ليلاً في صورة واحدة للقمر Meteosat-12"},
              {"s": "·", "text": "ننتظر صورة ثانية أو قمراً آخر قبل الحكم عليها"}]
    else:
        score, ev = _fire_evidence(inc, cfg)
        low, high = BAND.get(inc.get("verdict"), (5, 99))   # never contradict the rule-based verdict
    if category == "candidate":
        high = min(high, 49)
    score = int(max(low, min(high, score)))
    inc["category"] = category
    inc["category_text"] = CATEGORY_AR[category]
    inc["confidence"] = score
    inc["confidence_level"] = level_of(score)
    inc["confidence_label"] = CONFIDENCE_AR[category]
    inc["evidence"] = ev[:8]
    inc["severity"] = severity_of(inc, cfg, category)
    sources = sources_of(inc)
    if not sources and category == "candidate":   # the app's preview carries no sighting list
        sources = [{"key": "mtg_spot", "name": SOURCE_AR["mtg_spot"], "family": "mtg",
                    "first": inc["first"], "last": inc["last"], "n": 1, "max_frp": None}]
    inc["sources"] = sources
    inc["agreement"] = agreement_of(sources)
    return inc


def confidence_line(inc):
    """For a channel post: the confidence number with its level, and how serious it would be."""
    if inc.get("confidence") is None:
        return ""
    line = f"{inc['confidence']}% ({inc['confidence_level']})"
    if inc.get("severity"):
        line += f" · الخطورة: {inc['severity']}"
    return line


def main_reason(inc):
    """The strongest thing that speaks for the event, in one sentence (empty when none)."""
    return next((e["text"] for e in inc.get("evidence") or [] if e.get("s") == "+"), "")
