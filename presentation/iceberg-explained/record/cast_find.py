# /// script
# requires-python = ">=3.12"
# dependencies = ["pyte>=0.8"]
# ///
"""Finds when text first appears on the demo terminal's screen, as an offset in each terminal clip.

    uv run record/cast_find.py "Laad de Rick" "Inschakelen" ...
    uv run record/cast_find.py --dump 1792512600 1792512700 10    # screen text every 10 s in a wall-time window

Replays the asciinema cast through a terminal emulator, so the times are when a viewer could read it.
"""

import glob
import json
import re
import sys
from pathlib import Path

import pyte

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "recordings"


class Screen(pyte.Screen):
    """pyte's screen, tolerant of the private-mode device reports that Claude Code sends."""

    def report_device_status(self, *args, **kwargs):
        pass

    def select_graphic_rendition(self, *attrs, **kwargs):
        super().select_graphic_rendition(*attrs)


def replay():
    path = sorted(glob.glob(str(REC / "session-*.cast")))[-1]
    lines = open(path).read().splitlines()
    header = json.loads(lines[0])
    screen = Screen(header["term"]["cols"], header["term"]["rows"])
    stream = pyte.ByteStream(screen)
    t = float(header["timestamp"])
    for line in lines[1:]:
        if not line.startswith("["):
            continue
        interval, kind, data = json.loads(line)
        t += interval
        if kind == "r":
            cols, rows = map(int, data.split("x"))
            screen.resize(rows, cols)
        elif kind == "o":
            stream.feed(data.encode())
            yield t, screen


def clips():
    out = {}
    for f in REC.glob("*.json"):
        info = json.loads(f.read_text())
        if isinstance(info, dict) and info.get("page") == "term":
            out[info["clip"]] = info
    return out


def where(t, known):
    for name, c in known.items():
        if c["start"] <= t <= c["start"] + c["duration"]:
            return f"{name}@{t - c['start']:.1f}"
    return "-"


def main():
    known = clips()
    if sys.argv[1] == "--dump":
        start, end, step = map(float, sys.argv[2:5])
        nxt = start
        for t, screen in replay():
            if t >= nxt and t <= end:
                print(f"===== {where(t, known)} wall {t:.1f}")
                print("\n".join(l.rstrip() for l in screen.display if l.strip()))
                nxt = t + step
        return
    patterns = [re.compile(p) for p in sys.argv[1:]]
    found = {}
    for t, screen in replay():
        text = "\n".join(screen.display)
        for p in patterns:
            if p.pattern not in found and p.search(text):
                found[p.pattern] = t
        if len(found) == len(patterns):
            break
    for p in patterns:
        t = found.get(p.pattern)
        print(f"{p.pattern[:50]:52} {where(t, known) if t else 'not found'}")


if __name__ == "__main__":
    main()
