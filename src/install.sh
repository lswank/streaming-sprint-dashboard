#!/usr/bin/env bash
# Install the streaming-sprint-dashboard skill into one agent host.
#
#   ./install.sh claude|codex|grok [--dest DIR] [--force]
#
# Default destinations follow each host's own convention and honour the
# environment variable that host uses to relocate its config:
#   claude  ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills
#   codex   ${CODEX_HOME:-$HOME/.codex}/skills
#   grok    ${GROK_HOME:-$HOME/.grok}/skills
#
# Nothing is ever deleted: --force moves the existing copy aside and tells you
# where it went.
set -euo pipefail

SKILL="streaming-sprint-dashboard"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${1:-}"
DEST=""
FORCE=0
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="${2:-}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$HOST" in
  claude) DEFAULT="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills" ;;
  codex)  DEFAULT="${CODEX_HOME:-$HOME/.codex}/skills" ;;
  grok)   DEFAULT="${GROK_HOME:-$HOME/.grok}/skills" ;;
  *) echo "usage: $0 claude|codex|grok [--dest DIR] [--force]" >&2; exit 2 ;;
esac
DEST="${DEST:-$DEFAULT}"

SRC="$HERE/$HOST/$SKILL"
[ -d "$SRC" ] || { echo "missing $SRC; run this from the unzipped package" >&2; exit 1; }

TARGET="$DEST/$SKILL"
if [ -e "$TARGET" ] && [ "$FORCE" -eq 0 ]; then
  echo "$TARGET already exists. Pass --force to move it aside and install this copy." >&2
  exit 1
fi

mkdir -p "$DEST"
if [ -e "$TARGET" ]; then
  BACKUP="$TARGET.previous.$(date +%Y%m%d-%H%M%S)"
  mv "$TARGET" "$BACKUP"
  echo "moved the previous copy to $BACKUP"
fi
# WHY a staged copy: a half-copied skill directory would load and misbehave, so
# the new tree lands beside the target and swaps in with one mv.
STAGE="$TARGET.incoming.$$"
cp -R "$SRC" "$STAGE"
mv "$STAGE" "$TARGET"

if python3 -m unittest discover -s "$TARGET/tests" -t "$TARGET" -q >/dev/null 2>&1; then
  echo "tests pass"
else
  echo "WARNING: the tests did not pass. Run them yourself:"
  echo "  python3 -m unittest discover -s \"$TARGET/tests\" -t \"$TARGET\""
fi

cat <<MSG

Installed $SKILL for $HOST:
  $TARGET

See the page before trusting it, in about five seconds:
  python3 "$TARGET/scripts/sprint.py" demo ./sprint-demo
  python3 "$TARGET/scripts/sprint.py" serve ./sprint-demo --open

The second command serves until you stop it with Ctrl-C. To keep the terminal:
  nohup python3 "$TARGET/scripts/sprint.py" serve ./sprint-demo > serve.log 2>&1 &
MSG
