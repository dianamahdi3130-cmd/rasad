---
name: rasad-ship
description: Take a finished change in the Rasad repository to the live monitor safely - tests, version bumps, commit, push, pull request, merge, then confirm the monitor picked it up. Use whenever code is ready to go live or the owner asks to publish, deploy or apply a change.
---

# Ship a change to Rasad

`main` is live: a push there that touches `web/ rasad/ scripts/ config.yaml requirements.txt
.github/workflows/rasad.yml` restarts the monitor with the new code (after its current check),
and `worker/` or `web/` redeploys the private app. So ship once, tested.

## 1. Before committing
1. `python -m pytest -q` and `for f in tests/*.js; do node "$f"; done` — everything passes.
   A new behaviour gets its own test (the tests are fast; no network).
2. Version bumps, if the change needs them:
   - `web/` changed → new `CACHE` name in `web/sw.js` (otherwise phones keep the old app), and
     the same number in `?v=` of `imagery.js` and `beforeafter.js` in `index.html` and `sw.js`.
   - importance or alert rules → `RULES`; clip look → `CLIP_STYLE`; sea finder's closer look →
     `SEA_CHECK`; stored incidents need tidying again → `TIDY` (all in `rasad/engine.py`).
3. `git diff --stat` holds only what you meant to change (no `data/`, `_state/`, `probe-out/`).

## 2. Commit and push
```bash
[ "$(git config user.email)" = "noreply@anthropic.com" ] || { git config user.email noreply@anthropic.com; git config user.name Claude; }
git switch -c <short-branch-name>          # if still on main
git add -A && git commit                   # English, plain words: what the owner or channel will notice
git push -u origin HEAD
```
Never set the git user to `rasad-bot`, never force-push `main`.

## 3. Pull request and merge
- `gh pr create --fill` (or the session's own PR button). If `gh` is refused, give the owner
  `https://github.com/dianamahdi3130-cmd/rasad/compare/main...<branch>`.
- Merge when the owner wants the change live and the tests pass: `gh pr merge <n> --squash`
  (or ask the owner to press Merge). One merge per finished change, not one per small fix.

## 4. After the merge (about 5 minutes)
1. `gh run list -L 4 --workflow rasad.yml`: the running job stops after its check
   ("new code on main" in its log) and the queued one starts with the new code. Changes outside
   the watched paths (docs, `.claude/`, tests) do not restart anything.
2. When the new run has done a check: `python3 .claude/skills/rasad-live-check/check.py`.
   Fresh data, unique ids, sources `ok`.
3. If `worker/` or `web/` changed: `gh run list -L 2 --workflow deploy-worker.yml` succeeded
   (it does nothing until the Cloudflare secrets exist).

## 5. Tell the owner (Arabic, short)
What changed for them or the channel, that the tests passed, and that the monitor is running the
new code. Offer the one natural next step if there is one.
