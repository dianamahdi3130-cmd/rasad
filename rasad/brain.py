"""The judgement layer: remembers normal heat, groups detections into incidents,
decides how sure we are, and writes everything in plain Arabic."""
from __future__ import annotations

import datetime as dt
import math
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
        return "مرجّح", "ظهر في صورتين متتاليتين"
    strong = any(p.get("conf") in ("h", "high") or (p.get("frp") or 0) >= 10 for p in inc["polar"])
    if strong:
        return "مرجّح", "رصده قمر أمريكي بوضوح"
    if open_sea_night_fire(inc):
        return "مرجّح", ("رصده قمر أمريكي ليلاً في عرض البحر، بعيداً عن أي منصة نفطية معروفة، "
                         "ولم تُسجَّل حرارة في هذا المكان من قبل")
    return "يحتاج تأكيداً", "رصدة واحدة حتى الآن، وننتظر الصورة التالية"


def open_sea_night_fire(inc):
    """At night, out at sea, away from platforms and with no earlier heat there, a NASA hotspot
    has few innocent explanations (no sun glint, no fields, no flare stacks): treat it as likely."""
    if inc.get("place") != "sea" or inc.get("kind") != "new" or (inc.get("known_days") or 0) > 0:
        return False
    f = inc.get("facility")
    if f and f.get("nearest_km", f["km"]) <= 3:
        return False
    return any(p.get("night") and p.get("conf") not in ("l", "low") for p in inc["polar"])


def headline(inc, cfg):
    f = inc.get("facility")
    region = inc.get("region_name")
    near = float(cfg["alerts"]["near_facility_km"])
    if inc["kind"] == "unusual":
        return f"حرارة غير معتادة في {f['label']}" if f else "حرارة غير معتادة في موقع صناعي"
    if inc["place"] == "sea":
        return "حريق في البحر" + (f" قرب {region}" if region else "")
    if f and f["km"] < 0.8:
        return f"حريق في {f['label']}"
    if f and f["km"] <= near:
        return f"حريق قرب {f['label']}"
    if inc["place"] == "coast":
        return "حريق على الساحل" + (f" في {inc['country']}" if inc.get("country") else "")
    return "حريق جديد" + (f" في {inc['country']}" if inc.get("country") else "")


def alert_worthy(inc, cfg):
    A = cfg["alerts"]
    f = inc.get("facility")
    return (inc["kind"] == "unusual" or inc["place"] in ("sea", "coast")
            or bool(f and f["km"] <= float(A["near_facility_km"]))
            or (inc.get("max_frp") or 0) >= float(A["big_fire_mw"]))


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


def alert_message(inc, cfg, update=False):
    lines = []
    if update:
        lines.append(f"{VERDICT_ICON[inc['verdict']]} <b>تحديث: الحكم الآن «{inc['verdict']}»</b>")
    lines.append(f"{VERDICT_ICON[inc['verdict']]} <b>{esc(inc['headline'])}</b>")
    lines.append(f"📍 {esc(inc['where'])}")
    f = inc.get("facility")
    lines.append("🏭 أقرب منشأة طاقة: " + (esc(f["text"]) if f else
                                           f"لا توجد ضمن {cfg['geodata']['facility_max_km']} كم"))
    lines.append(f"🕒 بدأ: {inc['start_text']}")
    if inc.get("size"):
        lines.append(f"📏 الحجم: {inc['size']}")
    lines.append(f"⚖️ الحكم: <b>{inc['verdict']}</b>، {esc(inc['reason'])}")
    lk = links(inc["lat"], inc["lon"], inc["place"] in ("sea", "coast"))
    lines.append(f"🗺 <code>{inc['lat']:.4f},{inc['lon']:.4f}</code> · "
                 + " · ".join(f'<a href="{u}">{k}</a>' for k, u in lk.items()))
    if cfg.get("app_url"):
        lines.append(f'<a href="{cfg["app_url"]}#{inc["id"]}">افتح في التطبيق</a>')
    return "\n".join(lines)
