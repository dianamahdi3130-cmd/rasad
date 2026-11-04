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
GEO=${RASAD_GEO_BUDGET:-45}              # seconds per check for growing the facility map
LOCKED=${RASAD_CLOUD_URL:+1}
WATCHED="web rasad scripts config.yaml requirements.txt .github/workflows/rasad.yml"
start_sha=$(git rev-parse HEAD)
end=$((SECONDS + MINUTES * 60))
n=0
closed=""

while [ "$SECONDS" -lt "$end" ]; do
  t0=$SECONDS
  n=$((n + 1))
  echo "::group::check $n ($(date -u +%H:%M:%S) UTC)"
  python -m rasad --state _state --site _site cycle --auto-geo --geo-budget "$GEO" || echo "check $n failed"
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
