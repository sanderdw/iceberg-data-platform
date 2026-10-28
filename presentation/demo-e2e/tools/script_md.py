"""Write script/script.md, the readable voice-over, from edit/cut.json.

    python3 tools/script_md.py                    # script/script.md
    python3 tools/script_md.py --voice gemini-en  # script/script-en.md, from edit/cut-en.json
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tts"))
from variant import LANG, cut_file  # noqa: E402

EN = LANG == "en"
cut = json.loads(cut_file().read_text())
intro = ("Voice-over script, per chapter. Source: `edit/cut-en.json` (edit it there). Brackets show what is on screen."
         if EN else
         "Script van de voice-over, per hoofdstuk. Bron: `edit/cut.json` (pas daar aan). Tussen haken staat wat je in beeld ziet.")
cards = {"title": "Title", "end": "End"} if EN else {"title": "Titel", "end": "Einde"}
lines = [f"# {cut['title']}", "", intro, ""]
for chapter in cut["chapters"]:
    if not any(shot.get("beats") for shot in chapter["shots"]):
        continue  # the title card has no voice
    head = f"{chapter['number']} · {chapter['title']}" if chapter.get("number") else cards[chapter.get("card") or chapter["shots"][0]["card"]]
    lines += ["", f"## {head}", ""]
    for shot in chapter["shots"]:
        where = f"{'diagram' if EN else 'schema'}: {shot['diagram']}" if "diagram" in shot else shot.get("clip") or shot.get("card", "")
        for beat in shot.get("beats", []):
            lines.append(f"- *[{where}]* {beat['text']}")
(ROOT / "script" / ("script-en.md" if EN else "script.md")).write_text("\n".join(lines) + "\n")
