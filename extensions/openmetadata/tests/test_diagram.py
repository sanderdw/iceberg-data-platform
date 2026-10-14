import base64
import re

from openmetadata_sync import diagram
from openmetadata_sync import mapping as m
from tests.conftest import CUSTOMERS, ORDERS, REVENUE, listing


def drawn(item):
    found = m.constraints("iceberg", item)
    text = diagram.markdown("iceberg", item, found)
    encoded = re.search(r"base64,([A-Za-z0-9+/=]+)\)", text)
    return text, base64.b64decode(encoded.group(1)).decode() if encoded else ""


def test_the_diagram_shows_listed_tables_and_their_keys():
    text, svg = drawn(listing())
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    # Only tables in a relationship; the referring table left of the one it refers to.
    assert "sales.orders" in svg and "sales.customers" in svg and "report" not in svg
    x = {name: float(re.search(rf'<text x="([0-9.]+)"[^>]*>{name}<', svg).group(1))
         for name in ("sales.orders", "sales.customers")}
    assert x["sales.orders"] < x["sales.customers"]
    assert svg.count("marker-end") == 1 and ">PK<" in svg and ">FK<" in svg
    assert ">lines<" in svg and "more columns" not in svg
    assert text.startswith("### Tables and keys")


def test_names_are_escaped_and_no_keys_means_no_diagram():
    odd = {**CUSTOMERS, "name": "<b>&co"}
    model = {**REVENUE, "models": [{**REVENUE["models"][0], "datasets": [
        {**d, "table": {"namespace": ["sales"], "name": "<b>&co"}} if d["name"] == "CUSTOMERS" else d
        for d in REVENUE["models"][0]["datasets"]]}]}
    _, svg = drawn(listing(objects=[ORDERS, odd, model]))
    assert "&lt;b&gt;&amp;co" in svg and "<b>" not in svg
    assert drawn(listing(objects=[ORDERS, CUSTOMERS])) == ("", "")
