#!/usr/bin/env bash
# Starts the demo terminal: a tmux session (own socket) served by ttyd on :7681, recorded by asciinema.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../recordings"
mkdir -p "$OUT"
tmux -L demo -f "$HERE/tmux.conf" has-session -t demo 2>/dev/null || \
  tmux -L demo -f "$HERE/tmux.conf" new-session -d -s demo -x 150 -y 40 "bash --rcfile '$HERE/demo.bashrc'"
CAST="$OUT/session-$(date +%Y%m%d-%H%M%S).cast"
exec ttyd -p 7681 -i 127.0.0.1 -W -m 1 \
  -t fontFamily="DejaVu Sans Mono" -t fontSize=18 -t lineHeight=1.15 -t cursorBlink=false -t rendererType=dom -t unicodeVersion=11 \
  -t 'theme={"background":"#000000","foreground":"#E8E8E8","cursor":"#E8E8E8","selectionBackground":"#333333"}' \
  -t disableLeaveAlert=true -t disableResizeOverlay=true -t titleFixed=Terminal \
  asciinema rec --overwrite -q "$CAST" -c "tmux -L demo attach -t demo"
