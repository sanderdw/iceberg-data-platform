"""Types text into the demo terminal at a human pace.

    python3 record/type.py "curl -fsSL ... | sh"      # types, no Enter
    python3 record/type.py --enter "claude"           # types, then Enter
    python3 record/type.py --keys Enter               # tmux key names only
"""

import random
import subprocess
import sys
import time

TMUX = ["tmux", "-L", "demo"]


def send(*args):
    subprocess.run([*TMUX, "send-keys", "-t", "demo", *args], check=True)


args = sys.argv[1:]
if args and args[0] == "--keys":
    for key in args[1:]:
        send(key)
        time.sleep(0.35)
    sys.exit()
enter = args and args[0] == "--enter"
text = " ".join(args[1:] if enter else args)
for char in text:
    send("-l", char)
    pause = random.uniform(0.035, 0.085)
    if char in " /|-.":
        pause += random.uniform(0.02, 0.08)
    time.sleep(pause)
if enter:
    time.sleep(0.45)
    send("Enter")
