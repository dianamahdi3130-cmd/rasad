#!/bin/bash
# A cloud session starts on a fresh machine: install the Python packages quietly, so the tests run
# at once. Prints one line only (whatever a SessionStart hook prints is read into the conversation).
# Local sessions skip this.
[ "$CLAUDE_CODE_REMOTE" = "true" ] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
log=/tmp/rasad-pip.log
if pip install -q -r requirements.txt >"$log" 2>&1 \
    || pip install -q --break-system-packages -r requirements.txt >>"$log" 2>&1; then
  echo "rasad: Python packages ready"
else
  echo "rasad: installing the Python packages failed (details in $log)"
fi
exit 0
