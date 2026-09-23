"""Static notebook tables with no filtering, selection or download controls."""

from html import escape

import marimo as mo
import pandas as pd


def plain_table(data, *, label=None):
    if not isinstance(data, pd.DataFrame):
        data = data.to_pandas() if hasattr(data, "to_pandas") else pd.DataFrame(data)
    caption = f"<p><strong>{escape(label)}</strong></p>" if label else ""
    return mo.Html(caption + '<div style="overflow-x:auto">' + data.to_html(index=False, border=0) + "</div>")
