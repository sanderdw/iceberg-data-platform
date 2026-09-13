"""Install missing starter notebooks, including in existing team workspaces."""

from pathlib import Path


def seed_workspace(work: Path):
    source = Path(__file__).parent
    starters = {"workspace.py": source / "template.py"}
    starters.update({p.name: p for p in (source / "examples").glob("*.py")})
    for name, original in starters.items():
        try:
            with (work / name).open("x") as target:
                target.write(original.read_text())
        except FileExistsError:
            # Users own their copies. Never replace edits with the bundled version.
            pass
