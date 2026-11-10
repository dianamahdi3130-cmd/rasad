#!/usr/bin/env bash
# Watch without pause: one GitHub job runs a check every few minutes for almost six hours,
# instead of starting a new machine (and installing everything again) for every check.
#  - private app (RASAD_CLOUD_URL set): after each check the data and new clips go to the app on
#    Cloudflare; the memory is stored sealed; the public copies on GitHub are closed;
#  - otherwise (until the private app is live): the data goes out on the "live" branch and the
#    site is published when a clip is new, or every ~10 minutes;
#  - when new code reaches main, the loop stops so the next run takes over with that code.
set -uo pipefail

EVERY=${RASAD_EVERY_SECONDS:-150}        # start a check this often (a long check is followed at once)
MINUTES=${RASAD_LOOP_MINUTES:-340}       # GitHub stops a job after 6 hours
GEO=${RASAD_GEO_BUDGET:-45}              # seconds per background facility-map pass
CYCLE_LIMIT=${RASAD_SCAN_TIMEOUT_SECONDS:-210}
GEO_LIMIT=${RASAD_GEO_TIMEOUT_SECONDS:-60}
LOCKED=${RASAD_CLOUD_URL:+1}
WATCHED="web rasad scripts config.yaml requirements.txt .github/workflows/rasad.yml"
start_sha=$(git rev-parse HEAD)
end=$((SECONDS + MINUTES * 60))
n=0
closed=""
media_pid=""
satpic_pid=""
geo_pid=""
last_geo=-600

cleanup_workers() {
  local pid
  for pid in "$media_pid" "$satpic_pid" "$geo_pid"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  for pid in "$media_pid" "$satpic_pid" "$geo_pid"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
  done
}
trap cleanup_workers EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# Workers write only staging media or the facility database. The scanner alone owns incidents.
# GNU timeout forwards shutdown to the worker process group, including an active ffmpeg child.
start_media_worker() {
  local left=$(( (end - SECONDS) / 60 ))
  [ "$left" -gt 0 ] || return 0
  timeout --signal=TERM --kill-after=5s "$((left * 60))s" \
    python -m rasad --state _state --site _site media-worker --interval 2 --max-minutes "$left" \
    >> .rasad-media-worker.log 2>&1 &
  media_pid=$!
}
start_media_worker

# The before/after satellite pictures for alerted fires: reads the free Sentinel-2 archive in the
# background, one job at a time, each with its own deadline. The scanner posts the result.
start_satpic_worker() {
  local left=$(( (end - SECONDS) / 60 ))
  [ "$left" -gt 0 ] || return 0
  timeout --signal=TERM --kill-after=5s "$((left * 60))s" \
    python -m rasad --state _state --site _site satpic-worker --interval 60 --max-minutes "$left" \
    >> .rasad-satpic-worker.log 2>&1 &
  satpic_pid=$!
}
start_satpic_worker

while [ "$SECONDS" -lt "$end" ]; do
  t0=$SECONDS
  n=$((n + 1))
  echo "::group::check $n ($(date -u +%H:%M:%S) UTC)"
  # A failed/expired check keeps the previous published data; it cannot hold the loop indefinitely.
  if ! timeout --signal=TERM --kill-after=5s "${CYCLE_LIMIT}s" \
      env RASAD_BACKGROUND_MEDIA=1 python -m rasad --state _state --site _site cycle; then
    echo "check $n failed or exceeded ${CYCLE_LIMIT}s; keeping the last completed feed"
  fi
  if [ -n "$LOCKED" ]; then
    if python -m rasad --state _state --site _site cloud-publish; then
      # the private app has everything: now the public copies can go
      [ -z "$closed" ] && bash scripts/branches.sh lock-public && closed=1
    else
      echo "sending to the private app failed"
    fi
    bash scripts/branches.sh save-state-if-due || echo "saving the memory failed"
  else
    bash scripts/branches.sh save-state-if-due || echo "saving the memory failed"
    bash scripts/branches.sh save-live || echo "publishing the live data failed"
    bash scripts/branches.sh save-site-if-due || echo "publishing the site failed"
  fi
  echo "::endgroup::"
  echo "check $n took $((SECONDS - t0)) s"

  # the video worker must keep running: without it alerts go out with "video in preparation" only
  if [ -n "$media_pid" ] && ! kill -0 "$media_pid" 2>/dev/null; then
    echo "the video worker stopped; starting it again (last lines of its log:)"
    tail -n 5 .rasad-media-worker.log 2>/dev/null || true
    wait "$media_pid" 2>/dev/null || true
    start_media_worker
  fi
  if [ -n "$satpic_pid" ] && ! kill -0 "$satpic_pid" 2>/dev/null; then
    echo "the satellite picture worker stopped; starting it again (last lines of its log:)"
    tail -n 5 .rasad-satpic-worker.log 2>/dev/null || true
    wait "$satpic_pid" 2>/dev/null || true
    start_satpic_worker
  fi

  # Slow Overpass queries never delay publishing or the next satellite check.
  if [ $((SECONDS - last_geo)) -ge 600 ] && { [ -z "$geo_pid" ] || ! kill -0 "$geo_pid" 2>/dev/null; }; then
    [ -n "$geo_pid" ] && wait "$geo_pid" 2>/dev/null || true
    timeout --signal=TERM --kill-after=5s "${GEO_LIMIT}s" \
      python -m rasad --state _state --site _site maintain-geo --budget "$GEO" \
      > .rasad-geo-worker.log 2>&1 &
    geo_pid=$!
    last_geo=$SECONDS
  fi

  # stop only for a change the watching uses (the same paths that start a new run when pushed):
  # anything else (notes, the diagnosis requests) must not leave a gap in the watching
  if git fetch -q origin main 2>/dev/null; then
    new_sha=$(git rev-parse FETCH_HEAD)
    if [ "$new_sha" != "$start_sha" ]; then
      if ! git diff --quiet "$start_sha" "$new_sha" -- $WATCHED 2>/dev/null; then
        echo "new code on main: stopping so the next run uses it"
        break
      fi
      start_sha=$new_sha
    fi
  fi
  wait=$((EVERY - (SECONDS - t0)))
  [ "$wait" -gt 0 ] && sleep "$wait"
done
echo "done after $n checks"
