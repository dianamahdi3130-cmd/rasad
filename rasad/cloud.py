"""Send the app's data and clips to the private app on Cloudflare (instead of a public website).

The monitor (GitHub Actions) calls this after every check. Only the app behind sign-in can read
what is sent here; nothing with data is published on GitHub any more."""
from __future__ import annotations

import json
from pathlib import Path

import requests

from .util import log


def publish(site_dir, url, key, timeout=60):
    """Push data/meta.json + data/incidents.json, then any clip or still the cloud does not have
    yet, then tell it which clips are still in use (the others are removed there)."""
    site = Path(site_dir)
    meta_p, inc_p = site / "data" / "meta.json", site / "data" / "incidents.json"
    if not meta_p.exists() or not inc_p.exists():
        log.warning("cloud: no data to send yet")
        return False
    url = url.rstrip("/")
    s = requests.Session()
    s.headers["x-ingest-key"] = key
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    items = json.loads(inc_p.read_text(encoding="utf-8")).get("items", [])
    body = json.dumps({"meta": meta, "items": items}, ensure_ascii=False, separators=(",", ":")).encode()
    r = s.post(f"{url}/api/ingest/data", data=body, headers={"content-type": "application/json"}, timeout=timeout)
    r.raise_for_status()
    want = set()   # every clip an incident still points to (kept there even when not on this runner)
    for it in items + list(meta.get("flare_activity") or []):   # incidents and the flare markers
        for k in ("clip", "snap"):
            v = it.get(k)
            if v and v.startswith("clips/"):
                want.add(v[len("clips/"):])
    have = set(s.get(f"{url}/api/ingest/clips", timeout=timeout).json().get("clips", []))
    sent = 0
    for name in sorted(want - have):
        p = site / "clips" / name
        if not p.exists() or p.stat().st_size > 40_000_000:
            continue
        r = s.put(f"{url}/api/ingest/clip/{name}", data=p.read_bytes(), timeout=180)
        if r.ok:
            sent += 1
        else:
            log.warning("cloud: a clip was refused (%s)", r.status_code)
    r = s.post(f"{url}/api/ingest/keep", json={"clips": sorted(want)}, timeout=timeout)
    removed = r.json().get("removed", 0) if r.ok else 0
    log.warning("cloud: data sent (%d incidents), %d new clip file(s), %d old removed", len(items), sent, removed)
    return True
