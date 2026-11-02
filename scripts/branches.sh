#!/usr/bin/env bash
# Keeps the engine's memory on the "state" branch and the published app on "gh-pages".
# Each branch is restarted once a day so old clips do not pile up in the history.
set -euo pipefail

export GIT_AUTHOR_NAME="rasad-bot" GIT_AUTHOR_EMAIL="rasad-bot@users.noreply.github.com"
export GIT_COMMITTER_NAME="rasad-bot" GIT_COMMITTER_EMAIL="rasad-bot@users.noreply.github.com"

restore() {
  local branch="$1" dir="$2"
  git worktree remove --force "$dir" 2>/dev/null || rm -rf "$dir"
  if git fetch -q --depth=1 origin "+refs/heads/$branch:refs/remotes/origin/$branch" 2>/dev/null; then
    git worktree add -q --detach "$dir" "origin/$branch"
  else
    git worktree add -q --detach "$dir" HEAD
    (cd "$dir" && git checkout -q --orphan "$branch" && git rm -rfq . && git clean -fdq)
  fi
}

save() {
  local branch="$1" dir="$2" msg="$3"
  (
    cd "$dir"
    git add -A
    local tree parent=""
    tree=$(git write-tree)
    if git rev-parse -q --verify HEAD >/dev/null; then
      if [ "$(git rev-parse HEAD^{tree})" = "$tree" ]; then
        echo "$branch: nothing new"
        exit 0
      fi
      if [ "$(git log -1 --format=%cs HEAD)" = "$(date -u +%F)" ]; then
        parent="-p HEAD"
      fi
    fi
    # shellcheck disable=SC2086
    commit=$(git commit-tree "$tree" $parent -m "$msg")
    # GitHub sometimes refuses a push for a moment (e.g. while the site is being deployed): retry
    for attempt in 1 2 3; do
      git push -q -f origin "$commit:refs/heads/$branch" && break
      [ "$attempt" = 3 ] && exit 1
      sleep 10
    done
    git update-ref --no-deref HEAD "$commit"  # the next save in the same job builds on this one
    echo "$branch: saved"
  )
}

# The app's two data files on their own small branch, replaced on every check (no history):
# the app reads them from there at once, while GitHub Pages rebuilds the site only now and then.
save_live() {
  [ -s _site/data/meta.json ] && [ -s _site/data/incidents.json ] || { echo "live: no data yet"; return 0; }
  local meta inc data tree commit
  meta=$(git hash-object -w _site/data/meta.json)
  inc=$(git hash-object -w _site/data/incidents.json)
  data=$(printf '100644 blob %s\tincidents.json\n100644 blob %s\tmeta.json\n' "$inc" "$meta" | git mktree)
  tree=$(printf '040000 tree %s\tdata\n' "$data" | git mktree)
  commit=$(git commit-tree "$tree" -m "live $(date -u +%FT%TZ)")
  for attempt in 1 2 3; do
    git push -q -f origin "$commit:refs/heads/live" && { echo "live: saved"; return 0; }
    sleep 5
  done
  return 1
}

site_files() {
  mkdir -p _site
  cp -r web/. _site/
  touch _site/.nojekyll
}

# Publish the site when something besides the data changed (a new clip, the app itself), or
# every ~10 minutes: GitHub Pages builds a site at most about 10 times an hour.
save_site_if_due() {
  site_files
  local stamp=.site_pushed last=0 other
  [ -f "$stamp" ] && last=$(cat "$stamp")
  other=$(cd _site && git status --porcelain | grep -v ' data/' || true)
  if [ -n "$other" ] || [ $(( $(date +%s) - last )) -ge 540 ]; then
    save gh-pages _site "app $(date -u +%FT%TZ)" && date +%s > "$stamp"
  else
    echo "gh-pages: only data changed, published on the live branch"
  fi
}

# The memory goes to GitHub at once after anything was sent to the channel (so a restart never
# sends it twice), otherwise every ~10 minutes: the files are large to push after every check.
save_state_if_due() {
  local stamp=.state_pushed last=0
  [ -f "$stamp" ] && last=$(cat "$stamp")
  if [ -f .state_urgent ] || [ $(( $(date +%s) - last )) -ge 540 ]; then
    save state _state "memory $(date -u +%FT%TZ)" && date +%s > "$stamp" && rm -f .state_urgent
  else
    echo "state: kept on the runner for now"
  fi
}

case "${1:-}" in
  restore)
    restore state _state
    restore gh-pages _site
    ;;
  save)
    site_files
    save state _state "memory $(date -u +%FT%TZ)"
    save gh-pages _site "app $(date -u +%FT%TZ)"
    save_live || true
    ;;
  save-state)
    save state _state "memory $(date -u +%FT%TZ)"
    ;;
  save-state-if-due)
    save_state_if_due
    ;;
  save-live)
    save_live
    ;;
  save-site-if-due)
    save_site_if_due
    ;;
  *)
    echo "usage: $0 restore|save|save-state|save-state-if-due|save-live|save-site-if-due" >&2
    exit 1
    ;;
esac
