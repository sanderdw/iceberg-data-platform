"""Which voice-over a script works on: the Dutch Gemini version by default (`--voice gemini`), or the
English one with `--voice gemini-en` (edit/cut-en.json).

Each voice gets its own copy of every voice-dependent file, named after it (durations-gemini.json,
edl-gemini.json, public/voice/gemini/).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIANT = sys.argv[sys.argv.index("--voice") + 1] if "--voice" in sys.argv else "gemini"
if VARIANT not in ("gemini", "gemini-en"):
    raise SystemExit(f"unknown voice: {VARIANT}")
LANG = "en" if VARIANT.endswith("-en") else "nl"


def variant_file(path):
    """path with -<variant> before its suffix."""
    path = Path(path)
    return path.with_name(f"{path.stem}-{VARIANT}{path.suffix}")


def voice_dir():
    return ROOT / "public" / "voice" / VARIANT


def cut_file():
    """The script of this variant: edit/cut-en.json for English, edit/cut.json otherwise."""
    return ROOT / "edit" / ("cut-en.json" if LANG == "en" else "cut.json")
