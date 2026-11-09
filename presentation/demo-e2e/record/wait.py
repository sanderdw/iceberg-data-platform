"""Waits until the demo terminal shows a pattern (regex) in its last lines; prints the match context.

    python3 record/wait.py "\\[y/N\\]" 600
"""

import re
import subprocess
import sys
import time

pattern, timeout = re.compile(sys.argv[1], re.M), float(sys.argv[2]) if len(sys.argv) > 2 else 600
end = time.time() + timeout
while time.time() < end:
    screen = subprocess.run(["tmux", "-L", "demo", "capture-pane", "-p", "-J", "-t", "demo", "-S", "-60"],
                            capture_output=True, text=True).stdout
    if pattern.search(screen):
        print("\n".join(screen.rstrip().splitlines()[-12:]))
        sys.exit(0)
    time.sleep(1)
print(screen)
sys.exit(1)
