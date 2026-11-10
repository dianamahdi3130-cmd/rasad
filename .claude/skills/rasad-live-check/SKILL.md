---
name: rasad-live-check
description: Check that the live Rasad monitor is healthy, or look up one incident or what was found near a place and time, without reading the large data files. Use after a merge to main, when the owner says alerts or videos stopped, or when asked whether a fire was detected.
---

# Rasad live check

The monitor's data sits on two branches: `live` (what the app shows) and `state` (the full
memory, 1–2 MB files). Never `git show` or Read those files whole: run the script, it prints
about 30 lines.

```bash
python3 .claude/skills/rasad-live-check/check.py                      # overall health
python3 .claude/skills/rasad-live-check/check.py --id 20261108-0140_25.935_49.702
python3 .claude/skills/rasad-live-check/check.py --near 26.36 55.73 --km 30 \
        --start 2026-11-09T05:00Z --end 2026-11-09T09:00Z               # was this fire caught?
```
Add `--no-fetch` on a second run to skip fetching. Run it from the repository root.

The runs themselves: `gh run list -L 6 --workflow rasad.yml`. One run should be `in_progress`
and usually one `pending` (the next one, queued every 10 minutes; older pending runs show as
`cancelled`, which is normal). Logs are readable only after a run ends:
`gh run view <id> --log | grep -E "check [0-9]+ took|failed|exceeded|Traceback|new code" | tail -20`.

## Reading the output
- **check finished** older than ~10 minutes → the loop is not publishing: look at the runs.
- **partial scan: yes** is normal while the European server is slow; the unread pictures are
  read in the next checks. Worry only if `pending_recent` keeps growing for an hour.
- **`[time budget used up]`** means a check stopped reading at its time limit, by design.
- **firms latest** 2–4 hours old is normal (NASA's delay).
- **ids unique** must be `yes` in both the app and the memory. `NO` means duplicate incident
  records are back: see `tests/test_no_duplicates.py` and `engine.tidy_incidents`.
- **versions … ← differs** right after a merge means a one-time migration will run in the next check.
- **symbols waiting for their strength**: European fire symbols whose MW could not be read yet.
  A few per check are asked again; they are dropped after 24 hours.
- **memory: sealed** or **no live branch**: the private app is on. Then judge health from the
  runs and their logs; the data itself is only on Cloudflare.

## Telling the owner
In Arabic, two or three lines: is the monitor running, how fresh the data is, anything wrong and
what you will do about it. No raw numbers they cannot act on.
