"""An ER diagram of a listing's tables and keys, for the description of its data product.

OpenMetadata's open-source UI draws no ER diagram (Collate does) and doesn't render Mermaid, but
it shows a Markdown image with a data URI. So the diagram is an SVG drawn here from the primary and
foreign keys this extension publishes, with a table of the same relationships as its text version.
"""

import base64
from xml.sax.saxutils import escape

from . import mapping as m

BOX, GAP_X, GAP_Y, HEAD, ROW, PAD, LOOP = 230, 90, 28, 30, 20, 16, 40
ROWS = 8
NAME = 28


def relationships(prefix, listing, found):
    """(table, columns, referred table, referred columns) for every foreign key between listed tables."""
    tables = {m.table_fqn(prefix, listing, o["namespace"], o["name"]): m.key(o["namespace"], o["name"])
              for o in listing["objects"] if o["kind"] == "table"}
    edges = []
    for k, items in sorted(found.items()):
        for c in items:
            if c["constraintType"] != "FOREIGN_KEY":
                continue
            target = next((fqn for fqn in tables if all(r.startswith(fqn + ".") for r in c["referredColumns"])), None)
            if target:
                edges.append((k, tuple(c["columns"]), tables[target],
                              tuple(r[len(target) + 1:].strip('"') for r in c["referredColumns"])))
    return edges


def short(text):
    return text if len(text) <= NAME else text[:NAME - 1] + "…"


def label(k):
    namespace, name = k
    return ".".join([*namespace, name])


def layout(listing, found, edges):
    """Boxes per table: tables that refer to others on the left, the tables they refer to on the right."""
    columns_of = {m.key(o["namespace"], o["name"]): [c["name"] for c in o["columns"]]
                  for o in listing["objects"] if o["kind"] == "table"}
    nodes = sorted({k for s, _, t, _ in edges for k in (s, t)})
    targets = {k: {t for s, _, t, _ in edges if s == k and t != k} for k in nodes}
    ranks = {}

    def rank(k, seen=()):
        if k in ranks:
            return ranks[k]
        if k in seen:
            return 0
        ranks[k] = value = 1 + max((rank(t, (*seen, k)) for t in targets[k]), default=-1)
        return value

    top = max(rank(k) for k in nodes)
    boxes, heights = {}, {}
    for k in nodes:
        primary = next((c["columns"] for c in found.get(k, []) if c["constraintType"] == "PRIMARY_KEY"), [])
        foreign = [c for s, cols, _, _ in edges if s == k for c in cols]
        rows = [("PK FK" if c in foreign else "PK", c) for c in primary]
        rows += [("FK", c) for c in dict.fromkeys(foreign) if c not in primary]
        rest = [("", c) for c in columns_of.get(k, []) if c not in primary and c not in foreign]
        shown = rows + rest[:max(0, ROWS - len(rows))]
        if len(rows) + len(rest) > len(shown):
            shown.append(("", f"+ {len(rows) + len(rest) - len(shown)} more columns"))
        column = top - rank(k)
        y = heights.get(column, PAD)
        height = HEAD + ROW * len(shown) + 8
        boxes[k] = {"x": PAD + column * (BOX + GAP_X), "y": y, "height": height, "rows": shown}
        heights[column] = y + height + GAP_Y
    width = PAD * 2 + (top + 1) * BOX + top * GAP_X + LOOP
    height = max(heights.values()) - GAP_Y + PAD
    return boxes, width, height


def row_y(box, column):
    index = next((i for i, (_, name) in enumerate(box["rows"]) if name == column), None)
    if index is None:
        return box["y"] + HEAD / 2
    return box["y"] + HEAD + ROW * index + ROW / 2 + 4


def svg(listing, found, edges):
    boxes, width, height = layout(listing, found, edges)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        'font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif" font-size="12">',
        '<defs><marker id="key" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
        'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#64748b"/></marker></defs>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    for s, cols, t, refs in edges:
        a, b = boxes[s], boxes[t]
        y1, y2 = row_y(a, cols[0]), row_y(b, refs[0])
        if b["x"] > a["x"]:
            x1, x2 = a["x"] + BOX, b["x"]
            middle = (x1 + x2) / 2
            path = f"M{x1},{y1} C{middle},{y1} {middle},{y2} {x2},{y2}"
        elif b["x"] < a["x"]:
            x1, x2 = a["x"], b["x"] + BOX
            middle = (x1 + x2) / 2
            path = f"M{x1},{y1} C{middle},{y1} {middle},{y2} {x2},{y2}"
        else:
            # A self-reference, or two tables in one column: a loop on the right.
            x = a["x"] + BOX
            path = f"M{x},{y1} C{x + LOOP},{y1} {x + LOOP},{y2} {x},{y2}"
        parts.append(f'<path d="{path}" fill="none" stroke="#64748b" stroke-width="1.4" marker-end="url(#key)"/>')
    for k, box in boxes.items():
        x, y = box["x"], box["y"]
        parts.append(f'<rect x="{x}" y="{y}" width="{BOX}" height="{box["height"]}" rx="6" fill="#ffffff" '
                     'stroke="#c3cfdf"/>')
        parts.append(f'<path d="M{x},{y + HEAD} V{y + 6} Q{x},{y} {x + 6},{y} H{x + BOX - 6} Q{x + BOX},{y} '
                     f'{x + BOX},{y + 6} V{y + HEAD} Z" fill="#eaf1fb" stroke="#c3cfdf"/>')
        parts.append(f'<text x="{x + 10}" y="{y + 20}" font-weight="600" fill="#1f2937">'
                     f'{escape(short(label(k)))}</text>')
        for i, (kind, name) in enumerate(box["rows"]):
            base = y + HEAD + ROW * i + ROW / 2 + 8
            if kind:
                parts.append(f'<text x="{x + 10}" y="{base}" font-size="10" font-weight="700" '
                             f'fill="#2563eb">{kind}</text>')
            color = "#6b7280" if name.startswith("+ ") else "#1f2937"
            parts.append(f'<text x="{x + 52}" y="{base}" fill="{color}">{escape(short(name))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def cell(text):
    return text.replace("|", "\\|")


def markdown(prefix, listing, found):
    """The diagram and a table of the same relationships, or nothing without foreign keys."""
    edges = relationships(prefix, listing, found)
    if not edges:
        return ""
    image = base64.b64encode(svg(listing, found, edges).encode()).decode()
    rows = [f"| {cell(label(s))} | {cell(', '.join(cols))} | {cell(label(t))} ({cell(', '.join(refs))}) |"
            for s, cols, t, refs in edges]
    return "\n\n".join([
        "### Tables and keys",
        f"![Tables and keys of {cell(listing['name'])}](data:image/svg+xml;base64,{image})",
        "| Table | Foreign key | Refers to |\n| --- | --- | --- |\n" + "\n".join(rows),
    ])
