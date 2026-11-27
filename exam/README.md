# The exam

**Why.** The owner's wish of 27 Nov 2026: no change reaches the channel before it is tried on every past
event, not only on the one case it was made for. Changes made from one case had broken other cases.

## What it does

`tools/exam.py` replays what a version of Rasad would have posted.

- **The events.** It uses the real events Rasad stored: `exam/events/`, one snapshot of the live
  memory per file.
- **How it decides.** Each event is cut at each of its own sighting times. At every cut, the version's
  own rules judge it (`brain.finalize`), and the engine's tests for a first post are asked. The first
  cut that passes is when that version would have posted it.
- **First sightings.** The «رصد حراري أولي» posts sent live are replayed from their kept records.
- **What it never does.** It never posts, never reads the network and never writes the data branches.

```
python tools/exam.py snapshot        # keep the live memory's events (read from origin/state)
python tools/exam.py run             # what this checkout would post, and the known cases
python tools/exam.py compare REF     # this checkout against another version: every change listed
```

## The numbers

- **posts:** what would go to the channel: posts at sea, and first sightings among them.
- **possibly the same fire:** a post at sea within drifting distance of an earlier one in the day
  before (5 km + 3 km per hour, at most 20 km).
  - It is a guess. A second ship on the same route looks the same.
  - The known cases settle the ones we know.
- **posted here, not live / posted live, not here:** against what the channel really got.
  - Run on the live version, both lists are empty: on 28 Nov it reproduced all 35 live posts.
  - Its post times agree with the live ones to the minute in most cases, half of them within one
    minute.
- **cases** (`exam/cases.json`): truths taken from the reviews in `docs/reviews/`.
  - Each case is a place and a time, with what should have happened there: a post, exactly one
    post, or no post.
  - `open` cases are shown and never scored.

## What it cannot see yet

- **Level 1, this one.** The facts an event was given when it was made are kept as stored: its place,
  facility, earlier days of heat, and the closer look's answer. So a change to these cannot be
  judged here:
  - how those facts are computed;
  - how sightings are joined into events;
  - the finders' thresholds.
- **Sightings that never became an event** are not in the memory. Example: heat called usual and
  dropped.
- **Long events** keep only their last 120 sightings, so their early hours are missing.
- **First sightings that were not sent live** are not judged again: their strength is not all kept.
- **Next.** Level 2 replays the sightings as a stream through the real joining rules. Level 3
  replays kept pictures through the finders.

## The rule

A change goes to the channel only if, on the exam:
- it does more good than harm on the whole list of events;
- it breaks no case that passed.

Every difference it makes is read, event by event, before the merge.
