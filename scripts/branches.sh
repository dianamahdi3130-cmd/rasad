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
    git push -q -f origin "$commit:refs/heads/$branch"
    echo "$branch: saved"
  )
}

case "${1:-}" in
  restore)
    restore state _state
    restore gh-pages _site
    ;;
  save)
    mkdir -p _site
    cp -r web/. _site/
    touch _site/.nojekyll
    save state _state "memory $(date -u +%FT%TZ)"
    save gh-pages _site "app $(date -u +%FT%TZ)"
    ;;
  *)
    echo "usage: $0 restore|save" >&2
    exit 1
    ;;
esac
