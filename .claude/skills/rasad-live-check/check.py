#!/usr/bin/env python3
"""Rasad health in about 30 lines, read from the data branches (the big files never reach the chat).

From the repository root:
  python3 .claude/skills/rasad-live-check/check.py                 overall health
  python3 .claude/skills/rasad-live-check/check.py --id ID         one incident, every stored copy
  python3 .claude/skills/rasad-live-check/check.py --near LAT LON [--km 30]
        [--start 2026-11-09T06:00Z --end 2026-11-09T09:00Z | --hours 48]
                                                                   incidents around a place and time
  add --no-fetch to reuse the branches already fetched
"""
import argparse
import collections
import datetime as dt
import json
import math
import re
import subprocess
import sys
from pathlib import Path

UTC = dt.timezone.utc
NOW = dt.datetime.now(UTC)
BRANCHES = ("live", "state")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def fetch():
    for b in BRANCHES:
        ref = f"refs/remotes/origin/{b}"
        if git("ls-remote", "--exit-code", "origin", f"refs/heads/{b}").returncode == 0:
            git("fetch", "-q", "--depth=1", "origin", f"+refs/heads/{b}:{ref}")
        else:  # the branch is gone (private app on): do not report stale data
            git("update-ref", "-d", ref)


def have(b):
    return git("rev-parse", "-q", "--verify", f"refs/remotes/origin/{b}").returncode == 0


def load(b, path):
    out = subprocess.run(["git", "show", f"origin/{b}:{path}"], capture_output=True)
    return json.loads(out.stdout) if out.returncode == 0 else None


def pushed(b):
    t = git("log", "-1", "--format=%cI", f"origin/{b}").stdout.strip()
    return when(t) if t else "?"


def parse(t):
    if not t:
        return None
    try:
        return dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def age(t):
    p = parse(t)
    if p is None:
        return "-"
    m = (NOW - p).total_seconds() / 60
    return f"{m:.0f} min" if m < 90 else (f"{m / 60:.1f} h" if m < 48 * 60 else f"{m / 1440:.1f} days")


def when(t):
    p = parse(t)
    if p is None:
        return "-"
    stamp = p.strftime("%H:%MZ") if (NOW - p) < dt.timedelta(hours=20) else p.strftime("%Y-%m-%d %H:%MZ")
    return f"{stamp} ({age(t)} ago)"


def mw(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "- MW"
    return (f"{v:.1f}" if v < 10 else f"{v:.0f}") + " MW"


def code_versions():
    text = Path("rasad/engine.py").read_text(encoding="utf-8")
    keys = {"RULES": "rules", "CLIP_STYLE": "clip_style", "SEA_CHECK": "sea_check", "TIDY": "tidy"}
    out = {}
    for const, key in keys.items():
        m = re.search(rf"^{const}\s*=\s*(\d+)", text, re.M)
        out[key] = int(m.group(1)) if m else None
    return out


def unique(items):
    ids = [i.get("id") for i in items]
    dup = [k for k, n in collections.Counter(ids).items() if n > 1]
    return "yes" if not dup else f"NO ({len(dup)} ids repeated, e.g. {dup[0]})"


def source_line(key, s):
    state = "    " if "ok" not in s else ("ok  " if s["ok"] else "FAIL")
    bits = [f"{key:<13}", state, f"latest {when(s.get('latest'))}"]
    for k, v in s.items():
        if isinstance(v, bool) or not isinstance(v, int) or v == 0:
            continue
        bits.append(f"{k} {v}")
    if s.get("budget_exhausted"):
        bits.append("[time budget used up]")
    if s.get("scan_complete") is False:
        bits.append("[scan incomplete]")
    if s.get("daylight") is False:
        bits.append("[night: resting]")
    return "    " + "  ".join(bits)


def overview():
    if have("live"):
        meta = load("live", "data/meta.json") or {}
        feed = load("live", "data/incidents.json") or {}
        items = feed.get("items", feed if isinstance(feed, list) else [])
        print(f"app data (live branch, pushed {pushed('live')})")
        print(f"  check finished {when(meta.get('scan_finished') or meta.get('generated'))}"
              f" · took {meta.get('scan_duration_s', '?')} s · partial scan: {'yes' if meta.get('scan_partial') else 'no'}"
              f" · learning: {'yes' if meta.get('learning') else 'no'} · telegram: {'on' if meta.get('telegram') else 'OFF'}")
        print("  sources:")
        for k, s in (meta.get("sources") or {}).items():
            if isinstance(s, dict):
                print(source_line(k, s))
        verdicts = collections.Counter(i.get("verdict") for i in items)
        print(f"  incidents in the app: {len(items)} · ids unique: {unique(items)} · "
              + " · ".join(f"{k} {v}" for k, v in verdicts.most_common()))
        print(f"  videos: being made {sum(1 for i in items if i.get('clip_video_pending'))}"
              f" · to refresh {sum(1 for i in items if i.get('clip_needs_refresh'))}"
              f" · with a note {sum(1 for i in items if i.get('clip_note'))}"
              f" · none {sum(1 for i in items if not i.get('clip'))}")
        smoke = [i for i in items if i.get("smoke")]
        sat = [i for i in items if (i.get("satpic") or {}).get("after")]
        print(f"  smoke (silent): checked {len(smoke)} · seen {sum(1 for i in smoke if i['smoke'].get('seen'))}"
              f" · a streak in too few pictures {sum(1 for i in smoke if not i['smoke'].get('seen') and i['smoke'].get('frames'))}"
              f" · before/after pictures sent {len(sat)}")
        for i in [i for i in smoke if i["smoke"].get("seen")][:6]:
            print(f"    smoke seen {when(i['smoke'].get('at'))}  {i['smoke'].get('length_km')} km  "
                  f"{i.get('verdict')}  {str(i.get('headline'))[:50]}  [{i.get('id')}]")
        print("  newest:")
        for i in sorted(items, key=lambda i: i.get("first") or "", reverse=True)[:5]:
            print(f"    {when(i.get('first'))}  {i.get('verdict')}  {mw(i.get('max_frp'))}  "
                  f"{str(i.get('headline'))[:60]}  [{i.get('id')}]")
    else:
        print("app data: no live branch (the private app is on: its data is only on Cloudflare)")

    if not have("state"):
        print("memory: no state branch")
        return
    if load("state", "state.json") is None and git("cat-file", "-e", "origin/state:state.enc").returncode == 0:
        print(f"memory: sealed (state.enc, pushed {pushed('state')}); only the GitHub run can open it")
        return
    st = load("state", "state.json") or {}
    incs = load("state", "incidents.json") or []
    print(f"memory (state branch, pushed {pushed('state')})")
    print(f"  incidents {len(incs)} · ids unique: {unique(incs)}"
          f" · sent to the channel {sum(1 for i in incs if i.get('alerted_at'))}"
          f" · recovered {sum(1 for i in incs if i.get('recovered_historical'))}")
    sp = st.get("strength_pending") or {}
    times = sorted(d.get("t", "") for d in sp.values())
    span = f" (newest {when(times[-1])}, oldest {when(times[0])})" if times else ""
    print(f"  symbols waiting for their strength {len(sp)}{span}")
    print(f"  sea candidates {len(st.get('sea_candidates') or {})}"
          f" · missing pictures {len(st.get('missing_pictures') or {})}"
          f" · last run {when(st.get('last_run'))}")
    code = code_versions()
    print("  versions (stored / code): " + " · ".join(
        f"{k} {st.get(k)}/{v}" + ("" if st.get(k) == v else " ← differs (migration due)") for k, v in code.items()))


def records():
    if have("state") and load("state", "state.json") is not None:
        return load("state", "incidents.json") or [], "memory"
    if have("live"):
        feed = load("live", "data/incidents.json") or {}
        return feed.get("items", []), "app data"
    sys.exit("no readable incidents (the memory is sealed and the live branch is gone)")


def show_id(want):
    incs, where = records()
    found = [i for i in incs if i.get("id") == want]
    if not found:
        sys.exit(f"{want}: not in the {where}")
    print(f"{want}: {len(found)} stored record(s) in the {where}")
    for n, i in enumerate(found, 1):
        fac = i.get("facility") or {}
        series = i.get("frp_series") or []
        print(f"  [{n}] {when(i.get('first'))} → {when(i.get('last'))}")
        print(f"      verdict {i.get('verdict')} (sent as {i.get('alerted_verdict')}) · kind {i.get('kind')}"
              f" · priority {i.get('priority')} · status {i.get('status')}")
        print(f"      place {i.get('place')} · {str(i.get('where'))[:70]}")
        if fac:
            print(f"      facility {str(fac.get('label') or fac.get('cat'))[:50]} · {fac.get('km')} km")
        print(f"      sightings {i.get('n')} · observations {len(i.get('observations') or [])}"
              f" · pictures {len(i.get('geo_slots') or [])} · families {i.get('families')}"
              f" · max {mw(i.get('max_frp'))} · strength readings {len(series)}")
        sent = i.get("alerted_at")
        print(f"      channel: {'sent ' + when(sent) + ' (post ' + str(i.get('tg_msg')) + ')' if sent else 'not sent'}"
              f"{' · recovered past event' if i.get('recovered_historical') else ''}")
        clip = i.get("clip")
        print(f"      video: {clip or 'none'}"
              f"{' · being made' if i.get('clip_video_pending') else ''}"
              f"{' · to refresh' if i.get('clip_needs_refresh') else ''}"
              f"{' · note: ' + str(i.get('clip_note'))[:80] if i.get('clip_note') else ''}")
        print(f"      headline: {str(i.get('headline'))[:90]}")
        if i.get("reason"):
            print(f"      reason: {str(i.get('reason'))[:90]}")


def km_between(a_lat, a_lon, b_lat, b_lon):
    r = math.radians
    h = (math.sin(r(b_lat - a_lat) / 2) ** 2
         + math.cos(r(a_lat)) * math.cos(r(b_lat)) * math.sin(r(b_lon - a_lon) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))


def near(lat, lon, km, start, end):
    incs, where = records()
    rows = []
    for i in incs:
        try:
            d = km_between(lat, lon, float(i["lat"]), float(i["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        first, last = parse(i.get("first")), parse(i.get("last")) or parse(i.get("first"))
        if d <= km and first and first <= end and last >= start:
            rows.append((first, d, i))
    print(f"{len(rows)} incident(s) within {km:g} km of {lat}, {lon} between "
          f"{start:%Y-%m-%d %H:%MZ} and {end:%Y-%m-%d %H:%MZ} ({where})")
    for first, d, i in sorted(rows, key=lambda r: r[0])[:25]:
        print(f"  {first:%m-%d %H:%MZ}→{parse(i.get('last')):%H:%MZ}  {d:4.1f} km  {i.get('verdict')}"
              f"  {mw(i.get('max_frp'))}  {'sent' if i.get('alerted_at') else '-'}  [{i.get('id')}]"
              f"  {str(i.get('headline'))[:50]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id")
    ap.add_argument("--near", nargs=2, type=float, metavar=("LAT", "LON"))
    ap.add_argument("--km", type=float, default=30)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    if not Path("rasad/engine.py").exists():
        sys.exit("run this from the repository root")
    if not a.no_fetch:
        fetch()
    if a.id:
        show_id(a.id)
    elif a.near:
        end = parse(a.end) or NOW
        start = parse(a.start) or end - dt.timedelta(hours=a.hours)
        near(a.near[0], a.near[1], a.km, start, end)
    else:
        overview()


if __name__ == "__main__":
    main()
