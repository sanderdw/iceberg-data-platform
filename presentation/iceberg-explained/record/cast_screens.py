# /// script
# requires-python = ">=3.12"
# dependencies = ["pyte>=0.8"]
# ///
"""Screen text of the demo terminal at given clip offsets, for the transcript.

    uv run record/cast_screens.py h4-ingest@133 h6-semantic@40
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.argv, wanted = sys.argv[:1] + ["x"], sys.argv[1:]
import cast_find  # noqa: E402

REC = cast_find.REC
targets = []
for item in wanted:
    clip, offset = item.split("@")
    info = json.loads((REC / f"{clip}.json").read_text())
    targets.append((info["start"] + float(offset), item))
targets.sort()
i = 0
for t, screen in cast_find.replay():
    while i < len(targets) and t >= targets[i][0]:
        print(f"\n### {targets[i][1]}\n\n```text")
        print("\n".join(l.rstrip() for l in screen.display if l.strip()))
        print("```")
        i += 1
    if i == len(targets):
        break
