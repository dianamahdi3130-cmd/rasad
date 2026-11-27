#!/usr/bin/env python3
"""The exam: what a version of Rasad would post, replayed on the real events it stored.

The owner's wish of 27 Nov 2026: no change reaches the channel before it is tried on every past
event, not on the one case it was made for. This is that trial. It never posts, never reads the
network and never writes the data branches.

What it replays (level 1)
- Every stored incident (the memory of the `state` branch, kept in `exam/events/`) is cut at each of
  its own sighting times, in order. At each cut the version's own `brain.finalize` judges it and the
  engine's gates for a first post are asked (`first_post_gate`, the same tests as `engine.run_cycle`).
  The first cut that passes is the moment that version would have posted it.
- The facts an incident was given when it was made (place, facility, earlier days of heat, the
  closer look's answer...) are kept as stored. A change to how those facts are computed, to how
  sightings are joined into incidents, or to the finders' thresholds needs the later levels
  (sightings replayed as a stream, kept pictures).
- The first sightings told from one picture («رصد حراري أولي») are replayed from their stored records.

Commands
  python tools/exam.py snapshot                  keep the live memory's events (from origin/state)
  python tools/exam.py run [--code DIR] [--json OUT] [--quiet]
                                                 what the code in DIR would post (default: here)
  python tools/exam.py compare REF               this checkout against another version (a git ref)

Read `exam/README.md` for what the numbers mean.
"""
import argparse
import copy
import datetime as dt
import glob
import gzip
import json
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS = os.path.join(HERE, "exam", "events")
CASES = os.path.join(HERE, "exam", "cases.json")

# stored fields that only describe a version's words or media: dropped from a snapshot
DROP = {"clip", "snap", "clip_style", "clip_updated_at", "clip_note", "clip_tried", "clip_max_frp",
        "clip_peak_time", "clip_video_pending", "media_job", "media_result_at", "reading", "satpic",
        "_pos", "start_text", "last_text", "sources_text", "category_text", "evidence", "agreement",
        "sources", "headline", "reason", "confidence", "confidence_level", "confidence_label", "severity",
        "size"}
# what an incident's sightings decide; rebuilt at every cut
DERIVED = {"first", "last", "n", "srcs", "families", "observations", "geo_slots", "own_night_slots",
           "polar", "max_frp", "frp_series", "passes"}
# minutes from a sighting to the check that can post it (sources' usual delays; NASA by NOAA's files)
DELAY_MIN = {"mtg_spot": 12, "mtg_day_spot": 15, "mtg_frp": 20, "iodc_fire": 25, "MODIS_NRT": 180}


def parse_time(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def km(a_lat, a_lon, b_lat, b_lon):
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    d = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b_lon - a_lon) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, d)))


def baghdad(t):
    return (parse_time(t) + dt.timedelta(hours=3)).strftime("%d %b %H:%M")


# ---------------------------------------------------------------------------------------------
# the events

def snapshot(out_dir=EVENTS):
    """Keep the live memory's incidents and first-sighting records (read from origin/state)."""
    def show(path):
        return subprocess.run(["git", "-C", HERE, "show", f"origin/state:{path}"], check=True,
                              capture_output=True).stdout
    subprocess.run(["git", "-C", HERE, "fetch", "-q", "origin", "state"], check=False)
    incidents = json.loads(show("incidents.json"))
    state = json.loads(show("state.json"))
    early = (state.get("early_night") or {})
    early = list(early.values()) if isinstance(early, dict) else list(early)
    keep_early = [{k: r.get(k) for k in ("id", "first", "last", "lat", "lon", "sent_at", "promoted_to",
                                         "origin", "observations", "place")}
                  for r in early if isinstance(r, dict)]
    slim = [{k: v for k, v in inc.items() if k not in DROP} for inc in incidents]
    taken = dt.datetime.now(dt.timezone.utc)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, taken.strftime("%Y%m%d-%H%M") + ".json.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"taken": iso(taken), "incidents": slim, "early": keep_early,
                   "thread_log": state.get("thread_log") or []}, f, ensure_ascii=False, separators=(",", ":"))
    first = min((i["first"] for i in slim), default="-")
    print(f"kept {len(slim)} incidents ({first[:10]} to {taken.date()}) and {len(keep_early)} first-sighting "
          f"records in {os.path.relpath(path, HERE)}")
    return path


def load_events(paths=None):
    """All kept snapshots, the newest copy of each incident and record."""
    paths = sorted(paths or glob.glob(os.path.join(EVENTS, "*.json.gz")))
    incidents, early, taken = {}, {}, []
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            snap = json.load(f)
        taken.append(snap.get("taken"))
        for inc in snap.get("incidents") or []:
            incidents[inc["id"]] = inc
        for rec in snap.get("early") or []:
            if rec.get("id"):
                early[rec["id"]] = rec
    return {"incidents": sorted(incidents.values(), key=lambda i: i["first"]),
            "early": sorted(early.values(), key=lambda r: r.get("first") or ""), "taken": taken}


def load_cases(path=CASES):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("cases") or []
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------------------------------------
# one version's decisions

def import_version(code_dir):
    """The rasad package and config of the version in code_dir (this process imports one version)."""
    sys.path.insert(0, os.path.abspath(code_dir))
    from rasad import brain, engine, seawatch  # noqa: E402
    cfg = engine.load_config(os.path.join(code_dir, "config.yaml"),
                             state_dir=tempfile.mkdtemp(prefix="exam-state-"),
                             site_dir=tempfile.mkdtemp(prefix="exam-site-"))
    return brain, seawatch, cfg


def cut(stored, t):
    """The incident as it stood once its sightings up to time t were in."""
    inc = copy.deepcopy({k: v for k, v in stored.items() if k not in DERIVED})
    obs = [o for o in stored.get("observations") or [] if o.get("t") and o["t"] <= t]
    polar = [p for p in stored.get("polar") or [] if p.get("t") and p["t"] <= t]
    times = [o["t"] for o in obs] + [p["t"] for p in polar]
    inc["first"] = min(times) if times else stored["first"]
    inc["last"] = max(times) if times else stored["first"]
    inc["observations"] = copy.deepcopy(obs)
    inc["polar"] = copy.deepcopy(polar)
    inc["n"] = len(obs) or len(polar)
    inc["geo_slots"] = sorted({s for s in stored.get("geo_slots") or [] if s <= t})
    night = [s for s in stored.get("own_night_slots") or [] if s <= t]
    if night or "own_night_slots" in stored:
        inc["own_night_slots"] = night
    srcs, fams = [], []
    for row in sorted(obs + polar, key=lambda r: r["t"]):
        if row.get("src") and row["src"] not in srcs:
            srcs.append(row["src"])
        if row.get("family") and row["family"] not in fams:
            fams.append(row["family"])
    inc["srcs"], inc["families"] = srcs, fams
    series = [r for r in stored.get("frp_series") or [] if r and r[0] <= t]
    inc["frp_series"] = series
    readings = [float(r["frp"]) for r in obs + polar if r.get("frp") is not None] + [float(r[1]) for r in series]
    inc["max_frp"] = max(readings) if readings else None
    if stored.get("passes") is not None:
        inc["passes"] = [p for p in stored["passes"] if p.get("t") and p["t"] <= t]
    if inc.get("flame_glow") and "mtg_spot" not in srcs:
        inc.pop("flame_glow")
    # set after the post, when several first sightings were tied to one incident: not a test it met
    inc.pop("early_promotion_ambiguous", None)
    return inc


def first_post_gate(brain, seawatch, inc, cfg):
    """The engine's tests for a first post (engine.run_cycle, step 4), asked of a finalized incident.
    None = it would be posted now; otherwise the reason it is not."""
    sw = cfg.get("seawatch") or {}
    if inc.get("priority") != "high":
        return "not important (priority)"
    if inc.get("status") == "held":
        return "held"
    if inc.get("early_promotion_ambiguous"):
        return "ambiguous first sighting"
    if brain.day_requires_confirmation(inc):
        return "day finder alone"
    if brain.association_requires_confirmation(inc):
        return "uncertain association"
    if inc.get("recovered_historical"):
        return "recovered from the past"
    if brain.VERDICT_RANK[inc["verdict"]] < brain.VERDICT_RANK.get(cfg["alerts"]["min_verdict"], 1):
        return f"verdict {inc['verdict']}"
    finder = getattr(seawatch, "FINDER_SOURCES", frozenset({"mtg_spot", "mtg_day_spot"}))
    if not sw.get("alerts") and set(inc.get("srcs") or []) <= finder:
        return "sea finder on trial"
    return None


def delay(inc, t):
    rows = [r for r in (inc.get("observations") or []) + (inc.get("polar") or []) if r.get("t") == t]
    src = rows[0].get("src") if rows else ""
    if src.startswith("VIIRS"):
        night = any(r.get("night") for r in rows)
        return 45 if night else 25
    return DELAY_MIN.get(src, 20)


def decide(events, brain, seawatch, cfg):
    incidents = events["incidents"]
    # the version's own final verdicts, for the "confirmed fire close by" road of a sea verdict
    final = {}
    for stored in incidents:
        inc = cut(stored, stored["last"])
        inc.pop("near_confirmed", None)
        brain.finalize(inc, cfg)
        final[stored["id"]] = inc
    others = list(final.values())
    out = {}
    for stored in incidents:
        nc = brain.nearby_confirmed(final[stored["id"]], others) if stored.get("place") == "sea" else None
        times = sorted({r["t"] for r in (stored.get("observations") or []) + (stored.get("polar") or [])
                        if r.get("t")}) or [stored["last"]]
        post_t, why = None, None
        if stored.get("status") == "late":
            # judged too old to be news live (it reached the tests hours after it began: an outage,
            # a late source): any version would have met it as late
            times, why = [], "too old when judged (live)"
        for t in times:
            inc = cut(stored, t)
            if nc is not None:
                inc["near_confirmed"] = nc
            else:
                inc.pop("near_confirmed", None)
            brain.finalize(inc, cfg)
            why = first_post_gate(brain, seawatch, inc, cfg)
            if why is None:
                post_t = iso(parse_time(t) + dt.timedelta(minutes=delay(inc, t)))
                break
        f = final[stored["id"]]
        out[stored["id"]] = {
            "id": stored["id"], "posted": post_t is not None, "post_t": post_t, "why_not": why,
            "verdict": f.get("verdict"), "priority": f.get("priority"), "place": stored.get("place"),
            "kind": stored.get("kind"), "lat": stored["lat"], "lon": stored["lon"], "first": stored["first"],
            "last": stored["last"], "headline": f.get("headline"), "max_frp": f.get("max_frp"),
            "live": stored.get("status"), "live_at": stored.get("alerted_at"),
        }
    early = early_posts(events, out, cfg)
    for rec in early:
        target = out.get(rec.get("promoted_to") or "")
        if rec["posted"] and target is not None and target["posted"] and rec["post_t"] <= target["post_t"]:
            # its first sighting went out first: the incident joins that thread and does not post again.
            # A first sighting sent after the incident's own post is a second post (27 Nov, 23:00).
            target["posted_by_early"] = rec["id"]
    return {"incidents": out, "early": early}


def early_posts(events, out, cfg):
    """Which kept first-sighting records this version would send («رصد حراري أولي»).

    Live, a first sighting is held back only when that very sighting (same source, same picture) is
    already part of an incident (`earlyalerts._incident_for`); it never asks whether a posted fire is
    near. A first picture is not yet part of one, so a record sent live is sent again here while the
    feature is on. Records never sent live are not judged: their strength is not all kept."""
    sw = cfg.get("seawatch") or {}
    ea = {"enabled": True, **(sw.get("early_alerts") or {})}
    on = bool(sw.get("enabled") and sw.get("alerts") and ea.get("enabled"))
    res = []
    for rec in events["early"]:
        if not rec.get("sent_at"):
            continue
        res.append({"id": rec["id"], "lat": rec.get("lat"), "lon": rec.get("lon"), "first": rec.get("first"),
                    "post_t": rec.get("sent_at"), "promoted_to": rec.get("promoted_to"), "live": True,
                    "posted": on, "why_not": None if on else "first sightings switched off"})
    return res


# ---------------------------------------------------------------------------------------------
# the scorecard

def posts(result):
    rows = []
    for d in result["incidents"].values():
        if d["posted"] and not d.get("posted_by_early"):
            rows.append({"id": d["id"], "t": d["post_t"], "lat": d["lat"], "lon": d["lon"], "place": d["place"],
                         "what": d.get("headline") or "", "early": False})
    for r in result["early"]:
        if r["posted"]:
            rows.append({"id": r["id"], "t": r["post_t"], "lat": r["lat"], "lon": r["lon"], "place": "sea",
                         "what": "رصد حراري أولي", "early": True})
    return sorted(rows, key=lambda r: r["t"])


def same_fire_at_sea(a, b, base_km=5.0, drift_kmh=3.0, max_km=20.0, hours=24.0):
    """Two posts at sea that could be one burning hull: the later within drifting distance of the
    earlier, within a day. A guess: a second ship on the same route looks the same."""
    gap = (parse_time(b["t"]) - parse_time(a["t"])).total_seconds() / 3600
    return 0 <= gap <= hours and km(a["lat"], a["lon"], b["lat"], b["lon"]) <= min(max_km, base_km + drift_kmh * gap)


def score(result, cases):
    P = posts(result)
    sea = [p for p in P if p["place"] in ("sea", "coast")]
    repeat = []
    for i, p in enumerate(sea):
        prior = [q for q in sea[:i] if same_fire_at_sea(q, p)]
        if prior:
            repeat.append((p, prior[-1]))
    inc = result["incidents"].values()
    live_posted = {d["id"] for d in inc if d["live"] in ("alerted",)}
    now_posted = {d["id"] for d in inc if d["posted"]}
    card = {
        "posts": len(P), "posts_sea": len(sea), "posts_first_sightings": sum(p["early"] for p in P),
        "sea_posts_maybe_same_fire": len(repeat),
        "incidents": len(result["incidents"]), "posted_incidents": len(now_posted),
        "live_posted_incidents": len(live_posted),
        "added_vs_live": sorted(now_posted - live_posted), "removed_vs_live": sorted(live_posted - now_posted),
    }
    card["cases"] = [judge_case(c, result, P) for c in cases]
    card["repeats"] = [{"post": p["id"], "after": q["id"], "km": round(km(q["lat"], q["lon"], p["lat"], p["lon"]), 1),
                        "hours": round((parse_time(p["t"]) - parse_time(q["t"])).total_seconds() / 3600, 1)}
                       for p, q in repeat]
    return card


def judge_case(case, result, P):
    """A case: an area and a time, what should have happened there, how sure we are of it."""
    lat, lon, r = case["where"]
    t0, t1 = parse_time(case["from"]), parse_time(case["to"])
    inside = [p for p in P if km(lat, lon, p["lat"], p["lon"]) <= r and t0 <= parse_time(p["t"]) <= t1]
    want = case["truth"]
    first = inside[0]["t"] if inside else None
    if want == "no_post":
        ok = not inside
    elif want == "post":
        ok = bool(inside) and (not case.get("post_by") or parse_time(first) <= parse_time(case["post_by"]))
    elif want == "one_post":
        ok = len(inside) == 1 and (not case.get("post_by") or parse_time(first) <= parse_time(case["post_by"]))
    else:
        ok = None       # not settled: shown, never scored
    return {"case": case["id"], "truth": want, "sure": case.get("sure", "likely"), "posts": len(inside),
            "first_post": first, "ok": ok}


def report(card, title):
    out = [f"== {title}",
           f"posts: {card['posts']} (at sea {card['posts_sea']}, first sightings {card['posts_first_sightings']}); "
           f"at sea possibly the same fire as an earlier post: {card['sea_posts_maybe_same_fire']}",
           f"incidents: {card['incidents']}, posted: {card['posted_incidents']} (live: {card['live_posted_incidents']})"]
    if card["added_vs_live"]:
        out.append(f"posted here, not live ({len(card['added_vs_live'])}): " + ", ".join(card["added_vs_live"][:12])
                   + (" ..." if len(card["added_vs_live"]) > 12 else ""))
    if card["removed_vs_live"]:
        out.append(f"posted live, not here ({len(card['removed_vs_live'])}): " + ", ".join(card["removed_vs_live"][:12])
                   + (" ..." if len(card["removed_vs_live"]) > 12 else ""))
    scored = [c for c in card["cases"] if c["ok"] is not None]
    out.append(f"cases passed: {sum(c['ok'] for c in scored)} of {len(scored)}")
    for c in card["cases"]:
        mark = {True: "pass", False: "FAIL", None: "open"}[c["ok"]]
        when = f", first {baghdad(c['first_post'])} Baghdad" if c["first_post"] else ""
        out.append(f"  [{mark}] {c['case']}: wants {c['truth']} ({c['sure']}), {c['posts']} post(s){when}")
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# commands

def run(code_dir=HERE, events=None, quiet=False):
    brain, seawatch, cfg = import_version(code_dir)
    ev = events or load_events()
    result = decide(ev, brain, seawatch, cfg)
    card = score(result, load_cases())
    if not quiet:
        print(report(card, f"version in {os.path.relpath(os.path.abspath(code_dir), HERE) or '.'}"))
    return result, card


def _run_elsewhere(code_dir, out):
    subprocess.run([sys.executable, os.path.abspath(__file__), "run", "--code", code_dir, "--json", out, "--quiet"],
                   check=True, cwd=HERE)
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def compare(ref):
    """This checkout against the version at a git ref: every incident whose decision changes."""
    tmp = tempfile.mkdtemp(prefix="exam-")
    base_dir = os.path.join(tmp, "base")
    subprocess.run(["git", "-C", HERE, "worktree", "add", "-q", "--detach", base_dir, ref], check=True)
    try:
        base = _run_elsewhere(base_dir, os.path.join(tmp, "base.json"))
        here = _run_elsewhere(HERE, os.path.join(tmp, "here.json"))
    finally:
        subprocess.run(["git", "-C", HERE, "worktree", "remove", "--force", base_dir], check=False)
    print(report(base["card"], f"base: {ref}"))
    print(report(here["card"], "this checkout"))
    changed = []
    for iid, h in here["result"]["incidents"].items():
        b = base["result"]["incidents"].get(iid)
        if b is None:
            continue
        hb = (h["posted"] and not h.get("posted_by_early"), h["post_t"])
        bb = (b["posted"] and not b.get("posted_by_early"), b["post_t"])
        if hb != bb:
            changed.append((iid, b, h))
    eb = {r["id"]: r for r in base["result"]["early"]}
    early_changed = [(r, eb.get(r["id"])) for r in here["result"]["early"]
                     if eb.get(r["id"]) and eb[r["id"]]["posted"] != r["posted"]]
    print(f"== changes: {len(changed)} incident(s), {len(early_changed)} first sighting(s)")
    for iid, b, h in sorted(changed, key=lambda x: x[0]):
        def say(d):
            if d["posted"] and d.get("posted_by_early"):
                return "posted as its first sighting"
            return f"posted {baghdad(d['post_t'])}" if d["posted"] else f"not posted ({d['why_not']})"
        print(f"  {iid} [{h['place']}, {h['verdict']}, {h.get('max_frp') or '-'} MW, live {h['live']}] "
              f"{h.get('headline') or ''}: base {say(b)} -> here {say(h)}")
    for r, b in early_changed:
        print(f"  first sighting {r['id']}: base {'sent' if b['posted'] else 'not sent'} -> here "
              f"{'sent' if r['posted'] else 'not sent (' + str(r['why_not']) + ')'}")
    return base, here, changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    r = sub.add_parser("run")
    r.add_argument("--code", default=HERE)
    r.add_argument("--json")
    r.add_argument("--quiet", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("ref")
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        snapshot()
    elif a.cmd == "run":
        result, card = run(a.code, quiet=a.quiet)
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"result": result, "card": card}, f, ensure_ascii=False)
    else:
        compare(a.ref)


if __name__ == "__main__":
    main()
