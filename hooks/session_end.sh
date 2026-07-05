#!/usr/bin/env bash
# Claude Code SessionEnd hook: stage every session transcript into the brain.
#
# Install — add to ~/.claude/settings.json:
#   {
#     "hooks": {
#       "SessionEnd": [
#         { "hooks": [ { "type": "command",
#             "command": "/path/to/brain-tools/hooks/session_end.sh" } ] }
#       ]
#     }
#   }
#
# The hook receives JSON on stdin containing transcript_path. We only STAGE
# (phase 1); mining decisions/mistakes/patterns happens in the next
# integration pass (phase 2), so this stays fast and never blocks session end.
set -euo pipefail

payload="$(cat)"
transcript="$(printf '%s' "$payload" | python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("transcript_path",""))')"

if [[ -n "$transcript" && -f "$transcript" ]]; then
  brain ingest sessions "$transcript" >> /tmp/brain-session-hook.log 2>&1 || true
fi
