"""Install the starter notebooks that are missing, including in existing team workspaces.

Examples are not copied here: a team opens one from the portal, which copies it into examples/ then
(user_portal/notebook_files.py). Files a team already has always stay as they are.
"""

from pathlib import Path


def seed_workspace(work: Path):
    template = Path(__file__).parent / "template.py"
    for name in ("workspace.py", "shared_workspace.py"):
        try:
            with (work / name).open("x") as target:
                target.write(template.read_text())
        except FileExistsError:
            # Users own their copies. Never replace edits with the bundled version.
            pass
