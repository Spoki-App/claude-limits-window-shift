#!/bin/sh
set -e
REPO="${CLWS_REPO:-https://github.com/Spoki-App/claude-limits-window-shift}"
DIR="${CLWS_DIR:-$HOME/claude-limits-window-shift}"
SKILLS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
SKILL="$SKILLS/limits-window-shift"
CLI="$DIR/claude/skills/limits-window-shift/window_shift.py"
command -v git >/dev/null && command -v python3 >/dev/null || { echo "Needs git and python3 (macOS: xcode-select --install), then run this again."; exit 1; }

echo "claude-limits-window-shift: installing the Claude Code skill"
if [ -d "$DIR/.git" ]; then git -C "$DIR" fetch -q --tags origin; else git clone -q "$REPO" "$DIR"; fi
REF="${CLWS_REF:-$(git -C "$DIR" tag -l 'v*' --sort=-v:refname | head -n 1)}"
git -C "$DIR" -c advice.detachedHead=false checkout -q "${REF:-main}"
mkdir -p "$SKILLS"
if [ -L "$SKILL" ] || [ ! -e "$SKILL" ]; then
  ln -sfn "$DIR/claude/skills/limits-window-shift" "$SKILL"
  echo "OK version $(cat "$DIR/VERSION"), skill linked at $SKILL"
else
  echo "WARNING: $SKILL is a real folder, not replaced. Remove it and run this again to update."
fi

if [ "$#" -eq 0 ]; then
  echo "Next: python3 -I \"$CLI\" setup --start 09:00 --end 18:00 && python3 -I \"$CLI\" install"
  exit 0
fi
python3 -I "$CLI" setup "$@"
exec python3 -I "$CLI" install
