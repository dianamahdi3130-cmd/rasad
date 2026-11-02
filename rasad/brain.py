"""The judgement layer: remembers normal heat, groups detections into incidents,
decides how sure we are, and writes everything in plain Arabic."""
from __future__ import annotations

import datetime as dt
import math
import re
import statistics

from .util import esc, haversine_km, local_text, parse_time, size_word

CELL = 0.02  # ~2 km memory cells

FAMILY_AR = {"mtg": "أوروبي", "msg_iodc": "أوروبي ثانٍ", "viirs": "أمريكي", "modis": "أمريكي قديم",
             "landsat": "أمريكي عالي الدقة"}
VERDICT_RANK = {"يحتاج تأكيداً": 0, "مرجّح": 1, "مؤكد": 2}
VERDICT_ICON = {"مؤكد": "🔴", "مرجّح": "🟠", "يحتاج تأكيداً": "🟡"}
PLACE_AR = {"sea": "في البحر", "coast": "على الساحل", "land": "على اليابسة", "unknown": ""}


# ----------------------------------------------------------------- memory of normal heat
class Memory:
    """Per ~2 km cell: the days on which heat was seen and that day's strongest fire (MW)."""

    def __init__(self, cells):
        self.cells = cells  # {"i,j": {"YYYY-MM-DD": max_frp}}

    @staticmethod
    def key(lat, lon):
        return f"{math.floor(lat / CELL)},{math.floor(lon / CELL)}"

    def record(self, lat, lon, day, frp):
        c = self.cells.setdefault(self.key(lat, lon), {})
        c[day] = max(c.get(day, 0.0), float(frp or 0.0))

    def profile(self, lat, lon, before_day, window_days, reach=1):
        """(number of earlier days with heat here, typical daily strongest fire).
        reach = how many ~2 km cells around the point count as 'here'."""
        i, j = math.floor(lat / CELL), math.floor(lon / CELL)
        lo = (dt.date.fromisoformat(before_day) - dt.timedelta(days=window_days)).isoformat()
        per_day = {}
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                for day, frp in self.cells.get(f"{i + di},{j + dj}", {}).items():
                    if lo <= day < before_day:
                        per_day[day] = max(per_day.get(day, 0.0), frp)
        vals = [v for v in per_day.values() if v > 0]
        return len(per_day), (statistics.median(vals) if vals else None)

    def days_known(self, before_day, window_days):
        """How many earlier days the memory actually holds (0 = still learning)."""
        lo = (dt.date.fromisoformat(before_day) - dt.timedelta(days=window_days)).isoformat()
        return len({d for c in self.cells.values() for d in c if lo <= d < before_day})

    def prune(self, today, keep_days):
        lo = (dt.date.fromisoformat(today) - dt.timedelta(days=keep_days)).isoformat()
        for k in list(self.cells):
            c = {d: v for d, v in self.cells[k].items() if d >= lo}
            if c:
                self.cells[k] = c
            else:
                del self.cells[k]


# ----------------------------------------------------------------- incidents
def new_incident(det, kind):
    return {"id": f"{det['t'][:16].replace('-', '').replace(':', '').replace('T', '-')}"
                  f"_{det['lat']:.3f}_{det['lon']:.3f}",
            "kind": kind, "first": det["t"], "last": det["t"], "lat": det["lat"], "lon": det["lon"],
            "best": det["kind"], "families": [], "geo_slots": [], "polar": [], "max_frp": None,
            "n": 0, "status": "new", "alerted_verdict": None}


def add_to_incident(inc, det):
    inc["first"] = min(inc["first"], det["t"])
    inc["last"] = max(inc["last"], det["t"])
    inc["n"] += 1
    srcs = inc.setdefault("srcs", [])
    if det["src"] not in srcs:
        srcs.append(det["src"])
    if det["family"] not in inc["families"]:
        inc["families"].append(det["family"])
    if det["kind"] == "geo" and det["t"] not in inc["geo_slots"]:
        inc["geo_slots"].append(det["t"])
        inc["geo_slots"] = sorted(inc["geo_slots"])[-60:]
    if det["kind"] == "polar":
        inc["polar"].append({"t": det["t"], "frp": det.get("frp"), "conf": det.get("conf"),
                             "night": det.get("night")})
        inc["polar"] = inc["polar"][-20:]
        if inc["best"] != "polar":  # NASA's position is accurate to a few hundred metres
            inc["lat"], inc["lon"], inc["best"] = det["lat"], det["lon"], "polar"
    if det.get("frp") is not None:
        inc["max_frp"] = max(inc["max_frp"] or 0.0, det["frp"])
        series = inc.setdefault("frp_series", [])
        row = [det["t"], round(float(det["frp"]), 1), det["family"]]
        if row not in series:
            series.append(row)
            inc["frp_series"] = sorted(series)[-40:]


def find_incident(incidents, det, merge_km, merge_hours):
    t = parse_time(det["t"])
    best, best_d = None, merge_km
    for inc in incidents:
        if abs((t - parse_time(inc["last"])).total_seconds()) > merge_hours * 3600 \
                and abs((t - parse_time(inc["first"])).total_seconds()) > merge_hours * 3600:
            continue
        d = haversine_km(det["lat"], det["lon"], inc["lat"], inc["lon"])
        if d <= best_d:
            best, best_d = inc, d
    return best


def count_ar(n, one, two, many):
    return one if n == 1 else two if n == 2 else f"{n} {many}"


def verdict_of(inc):
    fams = inc["families"]
    slots = len(inc["geo_slots"])
    names = "، ".join(sorted({FAMILY_AR.get(f, f) for f in fams}))
    if len(fams) >= 2:
        return "مؤكد", f"رصده {count_ar(len(fams), 'قمر', 'قمران مختلفان', 'أقمار مختلفة')} ({names})"
    if slots >= 3:
        span = (parse_time(inc["geo_slots"][-1]) - parse_time(inc["geo_slots"][0])).total_seconds() / 60
        return "مؤكد", f"ظهر في {slots} صور متتالية خلال {span:.0f} دقيقة"
    if slots == 2:
        span = (parse_time(inc["geo_slots"][-1]) - parse_time(inc["geo_slots"][0])).total_seconds() / 60
        return "مرجّح", ("ظهر في صورتين متتاليتين" if span <= 15 else f"ظهر في صورتين خلال {span:.0f} دقيقة")
    nc = inc.get("near_confirmed")
    ctx = (f"، وقبل {hours_ar(nc['hours'])} رُصد حريق مؤكد آخر في البحر على بعد {nc['km']:.0f} كم"
           if nc else "")
    strong = any(p.get("conf") in ("h", "high") or (p.get("frp") or 0) >= 10 for p in inc["polar"])
    if strong:
        return "مرجّح", "رصده قمر أمريكي بوضوح" + ctx
    if open_sea_fire(inc):
        night = any(p.get("night") for p in inc["polar"])
        return "مرجّح", ("رصده قمر أمريكي " + ("ليلاً " if night else "") + "في عرض البحر، بعيداً عن أي "
                         "منصة نفطية معروفة، ولم تُسجَّل حرارة في هذا المكان من قبل" + ctx)
    if nc and inc.get("place") == "sea" and any(_clear(p) for p in inc["polar"]):
        return "مرجّح", "رصده قمر أمريكي في البحر" + ctx
    return "يحتاج تأكيداً", "رصدة واحدة حتى الآن، وننتظر الصورة التالية"


def _clear(p):
    """A NASA detection that is not marked low confidence (VIIRS: l/n/h; MODIS: 0-100)."""
    c = str(p.get("conf") or "").strip().lower()
    if c in ("l", "low"):
        return False
    return int(c) >= 40 if c.isdigit() else True


def open_sea_fire(inc):
    """Out at sea, away from platforms and with no earlier heat there, a NASA hotspot has few
    innocent explanations: at night none (no sun glint, no fields, no flare stacks); by day it has
    to be a clear detection of some strength (ships hit in the Strait of Hormuz burn by day too)."""
    if inc.get("place") != "sea" or inc.get("kind") != "new" or (inc.get("known_days") or 0) > 0:
        return False
    f = inc.get("facility")
    if f and f.get("nearest_km", f["km"]) <= 3:
        return False
    return any(_clear(p) and (p.get("night") or (p.get("frp") or 0) >= 5) for p in inc["polar"])


def nearby_confirmed(inc, incidents, km=25.0, hours=24.0):
    """A confirmed fire at sea close by in the hours before this one (another ship hit nearby)."""
    if inc.get("place") != "sea":
        return None
    t0 = parse_time(inc["first"])
    best = None
    for o in incidents:
        if o is inc or o.get("verdict") != "مؤكد" or o.get("place") != "sea":
            continue
        gap = (t0 - parse_time(o["last"])).total_seconds() / 3600
        if not 0 < gap <= hours:
            continue
        d = haversine_km(inc["lat"], inc["lon"], o["lat"], o["lon"])
        if d <= km and (best is None or d < best["km"]):
            best = {"km": round(d, 1), "hours": round(gap, 1), "id": o["id"]}
    return best


def hours_ar(h):
    n = int(round(h))
    if n < 1:
        return "أقل من ساعة"
    return "ساعة" if n == 1 else "ساعتين" if n == 2 else f"{n} ساعات" if n <= 10 else f"{n} ساعة"


def headline(inc, cfg):
    f = inc.get("facility")
    region = inc.get("region_name")
    near = float(cfg["alerts"]["near_facility_km"])
    if inc["kind"] == "unusual":
        return f"حرارة غير معتادة في {f['label']}" if f else "حرارة غير معتادة في موقع صناعي"
    if f and f["km"] < 0.8:
        # new heat right on a platform is often a flare being lit: say heat, not fire
        return f"حرارة عند {f['label']}" if f.get("code") == "platform" else f"حريق في {f['label']}"
    if inc["place"] == "sea":
        return "حريق في البحر" + (f" قرب {region}" if region else "")
    if f and f["km"] <= near:
        return f"حريق قرب {f['label']}"
    if inc["place"] == "coast":
        return "حريق على الساحل" + (f" في {inc['country']}" if inc.get("country") else "")
    return "حريق جديد" + (f" في {inc['country']}" if inc.get("country") else "")


def alert_worthy(inc, cfg):
    """Important = what the watch is for: fires at sea or in ports, at energy facilities,
    and big fires. Small fires a few km from a facility are usually fields or rubbish."""
    A = cfg["alerts"]
    f = inc.get("facility")
    frp = inc.get("max_frp") or 0
    big = float(A["big_fire_mw"])
    has_geo = bool(inc["geo_slots"])
    # a faint single speck (hot exhaust, a small flare) is not a fire worth a look on its own
    faint = not has_geo and frp < float(A.get("faint_mw", 3))
    weak_only = (not has_geo and bool(inc["polar"]) and frp < 5
                 and all(p.get("conf") in ("l", "low") for p in inc["polar"]))
    seen_before = (inc.get("known_days") or 0) > 0
    if inc["kind"] == "unusual":
        return True
    if inc["place"] in ("sea", "coast"):
        return not weak_only and not (faint and seen_before)
    if f and f["km"] <= float(A.get("at_facility_km", 2)):
        return not faint and not weak_only
    # away from facilities, a fire where fires are common (fields burnt every season, flare
    # fields) is not news, however big: Khuzestan's sugar-cane burning is the classic case
    busy = (inc.get("area_days") or 0) >= int(A.get("busy_area_days", 2))
    if busy:
        return False
    if f and f["km"] <= float(A["near_facility_km"]) and frp >= float(A.get("near_facility_min_mw", big)):
        return True
    return frp >= float(A.get("big_fire_far_mw", big)) and in_focus(inc, cfg)


def in_focus(inc, cfg):
    """Big fires far from any facility matter only in the countries being watched
    (the boxes of the Red Sea and Iraq also cover Ethiopia, Sudan, Syria, Turkey...)."""
    names = cfg["alerts"].get("focus_countries") or []
    country = inc.get("country") or ""
    return not names or not country or any(n in country for n in names)


def finalize(inc, cfg):
    """Fill the display fields the app and the alerts use."""
    inc["verdict"], inc["reason"] = verdict_of(inc)
    if inc["kind"] == "unusual" and inc.get("typical_frp") and inc.get("max_frp"):
        ratio = round(inc["max_frp"] / max(inc["typical_frp"], 1.0))
        times = count_ar(ratio, "ضعف", "ضعفين", "أضعاف") if ratio <= 10 else f"{ratio} ضعفاً"
        inc["reason"] = f"في هذا المكان حرارة يومية معتادة، لكنها الآن أعلى بنحو {times}. " + inc["reason"]
    inc["size"] = size_word(inc.get("max_frp"))
    inc["headline"] = headline(inc, cfg)
    inc["priority"] = "high" if alert_worthy(inc, cfg) else "low"
    inc["start_text"] = local_text(parse_time(inc["first"]), cfg)
    inc["last_text"] = local_text(parse_time(inc["last"]), cfg)
    inc["sources_text"] = "، ".join(sorted({FAMILY_AR.get(f, f) for f in inc["families"]}))
    return inc


def links(lat, lon, sea):
    out = {"الخريطة": f"https://www.google.com/maps?q={lat:.5f},{lon:.5f}&t=k"}
    if sea:
        out["السفن"] = f"https://www.marinetraffic.com/en/ais/home/centerx:{lon:.3f}/centery:{lat:.3f}/zoom:11"
    out["ناسا"] = f"https://firms.modaps.eosdis.nasa.gov/map/#d:24hrs;@{lon:.3f},{lat:.3f},11.0z"
    return out


_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9.'&/\- ]*[A-Za-z0-9.]|[A-Za-z]")


def bidi_safe(text):
    """Keep Latin names (OpenStreetMap names) whole inside Arabic lines: each Latin run is set
    apart (FSI ... PDI) and followed by a right-to-left mark, so brackets and numbers around it
    stay in place in every Telegram app."""
    return _LATIN.sub(lambda m: "\u2068" + m.group(0) + "\u2069\u200f", str(text or ""))


SOURCE_TG = {"viirs": "ناسا", "modis": "ناسا", "landsat": "لاندسات", "mtg": "ميتيوسات الأوروبي",
             "msg_iodc": "ميتيوسات الأوروبي"}


def app_link(cfg, inc):
    """Opens the app straight on this incident (the query survives every Telegram app)."""
    base = cfg["app_url"].split("#")[0].split("?")[0]
    return f"{base}?e={inc['id']}#{inc['id']}"


def mw_ar(v):
    return (f"{v:.1f}" if v < 10 else f"{v:.0f}") + " MW"


def alert_message(inc, cfg, update=False, threaded=False):
    """The channel post: a bold headline, one fact per line with a label, the coordinates to copy
    and the links on their own lines. Only a confirmed fire is called "مؤكد"; other grades are not
    named. An update that is sent as a reply to the first post only says what changed."""
    sure = inc["verdict"] == "مؤكد"
    lt = cfg["local_time"]
    off = dt.timedelta(hours=float(lt["offset_hours"]))
    start = parse_time(inc["first"]) + off
    now_local = parse_time(inc["last"]) + off
    when = f"{start:%H:%M} بتوقيت {lt['name']}" + ("" if start.date() == now_local.date() else f" · {start:%Y/%m/%d}")
    lines = []
    if update:
        lines.append("🔄 <b>تحديث: الحريق مؤكد الآن</b>" if sure else "🔄 <b>تحديث</b>")
        if not threaded:
            lines.append(f"<b>{esc(bidi_safe(inc['headline']))}</b>")
        if sure:
            lines.append(f"✅ {esc(bidi_safe(inc['reason']))}")
        if inc.get("max_frp"):
            lines.append(f"🔥 <b>الشدة:</b> {mw_ar(inc['max_frp'])}")
        lines.append(f"🕒 <b>آخر رصد:</b> {local_text(parse_time(inc['last']), cfg, with_date=False)}")
        if not threaded:
            lines.append(f"<code>{inc['lat']:.4f}, {inc['lon']:.4f}</code>")
        if cfg.get("app_url"):
            lines.append(f'<a href="{app_link(cfg, inc)}">فتح الحدث في التطبيق</a>')
        return "\n".join(lines)
    lines.append(f"{'🔴' if sure else '🟠'} <b>{esc(bidi_safe(inc['headline']))}</b>")
    if sure:
        lines.append(f"✅ <b>مؤكد</b> · {esc(bidi_safe(inc['reason']))}")
    lines.append("")
    lines.append(f"📍 <b>المكان:</b> {esc(bidi_safe(inc['where']))}")
    lines.append(f"🕒 <b>البداية:</b> {when}")
    if inc.get("max_frp"):
        lines.append(f"🔥 <b>الشدة:</b> {mw_ar(inc['max_frp'])}" + (f" · {inc['size']}" if inc.get("size") else ""))
    f = inc.get("facility")
    if f:
        lines.append(f"🏭 <b>أقرب منشأة:</b> {esc(bidi_safe(f.get('label') or f['cat']))} · على بعد {f['km']:.0f} كم")
    else:
        lines.append(f"🏭 <b>أقرب منشأة:</b> لا منشأة طاقة ضمن {cfg['geodata']['facility_max_km']} كم")
    names = []
    for fam in inc["families"]:
        n = SOURCE_TG.get(fam, FAMILY_AR.get(fam, fam))
        if n not in names:
            names.append(n)
    if names:
        lines.append(f"🛰 <b>رصدته:</b> {' و'.join(names)}")
    nc = inc.get("near_confirmed")
    if nc and not sure:
        lines.append(f"📌 قبل {hours_ar(nc['hours'])} رُصد حريق مؤكد آخر في البحر على بعد {nc['km']:.0f} كم")
    lines.append("")
    lines.append(f"<code>{inc['lat']:.4f}, {inc['lon']:.4f}</code>")
    lk = links(inc["lat"], inc["lon"], inc["place"] in ("sea", "coast"))
    lines.append(" | ".join(f'<a href="{u}">{k}</a>' for k, u in lk.items()))
    if cfg.get("app_url"):
        lines.append(f'📲 <a href="{app_link(cfg, inc)}">فتح الحدث في التطبيق</a>')
    return "\n".join(lines)
