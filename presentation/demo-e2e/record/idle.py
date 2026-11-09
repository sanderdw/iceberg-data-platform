"""Waits until Claude Code in the demo terminal is idle (no spinner line, no 'esc to interrupt') or asks something."""

import re
import subprocess
import sys
import time

timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 900
end, calm = time.time() + timeout, 0
while time.time() < end:
    screen = subprocess.run(["tmux", "-L", "demo", "capture-pane", "-p", "-t", "demo"], capture_output=True, text=True).stdout
    asking = "Enter to select" in screen or "Do you want" in screen
    busy = "esc to interrupt" in screen
    calm = calm + 1 if (not busy or asking) else 0
    if calm >= 3:
        print("\n".join(l for l in screen.splitlines() if l.strip())[-3000:])
        sys.exit(0)
    time.sleep(2)
print("timeout")
sys.exit(1)
