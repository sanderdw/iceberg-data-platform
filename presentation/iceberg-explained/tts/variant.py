"""Which voice-over a script works on: `--voice gemini` for the Gemini version, `--voice gemini-en` for the
English one (edit/cut-en.json), nothing for ElevenLabs.

The ElevenLabs version keeps the plain paths; another voice gets its own copy of every voice-dependent
file, named after it (durations-gemini.json, edl-gemini.json, public/voice/gemini/, demo-e2e-gemini.mp4).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIANT = sys.argv[sys.argv.index("--voice") + 1] if "--voice" in sys.argv else ""
if VARIANT not in ("", "elevenlabs", "gemini", "gemini-en"):
    raise SystemExit(f"unknown voice: {VARIANT}")
VARIANT = "" if VARIANT == "elevenlabs" else VARIANT
LANG = "en" if VARIANT.endswith("-en") else "nl"


def variant_file(path):
    """path, or path with -<variant> before its suffix."""
    path = Path(path)
    return path.with_name(f"{path.stem}-{VARIANT}{path.suffix}") if VARIANT else path


def voice_dir():
    return ROOT / "public" / "voice" / VARIANT if VARIANT else ROOT / "public" / "voice"


def cut_file():
    """The script of this variant: edit/cut-en.json for English, edit/cut.json otherwise."""
    return ROOT / "edit" / ("cut-en.json" if LANG == "en" else "cut.json")
