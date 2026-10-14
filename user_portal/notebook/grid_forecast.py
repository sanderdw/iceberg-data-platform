"""A synthetic MV load forecast as Polars LazyFrames, published to Iceberg with Polars' own sink.

`grid_forecast.product.yaml` is the only source of the product's meaning: datasets, columns and
their docs, keys, relationships, metrics and quality checks. This module implements the rows.
"""

import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import yaml
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import BooleanType, DoubleType, IntegerType, NestedField, StringType

from user_portal.notebook.connection import polars_storage_options
from user_portal.notebook.duckdb_connection import EXAMPLE_PROPERTY

EXAMPLE = "09_polars_grid_forecast_write"
PRODUCER = "iceberg-data-platform."
# Contract type: Iceberg type, Polars dtype and Ossie datatype.
TYPES = {
    "string": (StringType(), pl.String, "String"),
    "int": (IntegerType(), pl.Int32, "Integer"),
    "double": (DoubleType(), pl.Float64, "Float"),
    "boolean": (BooleanType(), pl.Boolean, "Boolean"),
}
SEVERITY = [(150, "critical"), (120, "high"), (100, "moderate")]

# Six fictional substations; each profile says how much of an asset's rating (percent, in 2040)
# the baseline and each spreading model take. Home batteries lower the feed-in peak.
SUBSTATIONS = [
    ("SS-ALD", "Example Alderbrook", "Alderbrook downtown", "North", 52.371, 4.896, "urban", 10),
    ("SS-BIR", "Example Birchfield", "Birchfield suburbs", "North", 52.452, 4.812, "suburban", 10),
    ("SS-CED", "Example Cedar Flats", "Cedar Flats countryside", "East", 52.512, 6.083, "rural_solar", 20),
    ("SS-DUN", "Example Dunmore", "Dunmore industrial park", "East", 52.221, 6.893, "industrial", 20),
    ("SS-ELM", "Example Elm Harbor", "Elm Harbor port", "South", 51.924, 4.478, "port", 10),
    ("SS-FEN", "Example Fenwick", "Fenwick farmland", "South", 51.689, 5.302, "rural_farms", 20),
]
PROFILES = {
    "urban": {"offtake": 50, "feed_in": 4, "SM-EV": 26, "SM-HP": 18, "SM-BE": 6, "SM-PV": 14, "SM-BAT": 4},
    "suburban": {"offtake": 44, "feed_in": 8, "SM-EV": 20, "SM-HP": 30, "SM-BE": 4, "SM-PV": 30, "SM-BAT": 8},
    "rural_solar": {"offtake": 30, "feed_in": 22, "SM-EV": 8, "SM-HP": 10, "SM-BE": 4, "SM-PV": 95, "SM-BAT": 12},
    "industrial": {"offtake": 56, "feed_in": 6, "SM-EV": 10, "SM-HP": 4, "SM-BE": 38, "SM-PV": 20, "SM-BAT": 2},
    "port": {"offtake": 50, "feed_in": 10, "SM-EV": 16, "SM-HP": 8, "SM-BE": 24, "SM-PV": 40, "SM-BAT": 6},
    "rural_farms": {"offtake": 34, "feed_in": 28, "SM-EV": 6, "SM-HP": 12, "SM-BE": 8, "SM-PV": 70, "SM-BAT": 10},
}
SECTORS = {
    "urban": ["data_center", "hospital", "bus_depot", "logistics"],
    "suburban": ["logistics", "cold_storage", "hospital"],
    "rural_solar": ["solar_farm", "greenhouse"],
    "industrial": ["manufacturing", "data_center", "logistics"],
    "port": ["cold_storage", "logistics", "manufacturing", "bus_depot"],
    "rural_farms": ["greenhouse", "solar_farm", "cold_storage"],
}
# id, name, technology, direction, assumption, S-curve midpoint and steepness, sign.
SPREADING_MODELS = [
    ("SM-EV", "EV charging", "electric_vehicles", "offtake",
     "Electric cars and vans charging at home and at work; adoption grows fastest before 2032.", 2031, 0.45, 1),
    ("SM-HP", "Heat pumps", "heat_pumps", "offtake",
     "Homes replace gas heating with heat pumps; growth speeds up after 2030.", 2034, 0.40, 1),
    ("SM-BE", "Business electrification", "business_electrification", "offtake",
     "Businesses move processes and fleets from fuel to electricity.", 2032, 0.35, 1),
    ("SM-PV", "Rooftop solar", "solar_pv", "feed_in",
     "Rooftop solar on homes and businesses; adoption is already high.", 2029, 0.40, 1),
    ("SM-BAT", "Home batteries", "home_batteries", "feed_in",
     "Home batteries store solar power at midday and so lower the feed-in peak.", 2035, 0.50, -1),
]
# Six assets per feeder: position, type, and how strongly the profile applies (percent).
POSITIONS = [("C1", "cable", 100), ("C2", "cable", 100), ("C3", "cable", 100),
             ("T1", "transformer", 115), ("T2", "transformer", 115), ("R1", "mv_room", 45)]
FEEDERS_PER_SUBSTATION = 8


def load_contract(directory):
    """Read the copy beside the notebook, including the user's local edits."""
    return yaml.safe_load((Path(directory) / "grid_forecast.product.yaml").read_text())


def fields(dataset):
    return dataset["fields"]


def polars_schema(dataset):
    return {**{name: TYPES[f["type"]][1] for name, f in fields(dataset).items()}, "synthetic": pl.Boolean}


def iceberg_schema(dataset):
    """Every column is optional, like Polars columns; its doc is the contract's description."""
    columns = [*fields(dataset).items(), ("synthetic", {"type": "boolean", "description": "Always true: synthetic."})]
    return Schema(*(
        NestedField(i, name, TYPES[f["type"]][0], required=False, doc=f.get("description"))
        for i, (name, f) in enumerate(columns, start=1)
    ))


def partition_spec(schema, dataset):
    return PartitionSpec(*(
        PartitionField(schema.find_field(name).field_id, 1000 + i, IdentityTransform(), name)
        for i, name in enumerate(dataset.get("partition_by", []))
    ))


def adoption(year, midpoint, steepness, start, end):
    """Share of 2040's growth reached in `year`, in permille: an S-curve from 0 in `start` to 1000 in `end`."""
    def curve(y):
        return 1 / (1 + math.exp(-steepness * (y - midpoint)))
    return round(1000 * (curve(year) - curve(start)) / (curve(end) - curve(start)))


def grid_rows():
    """Substations, feeders, assets and large customers: small, fixed and fictional."""
    substations, feeders, assets, customers = [], [], [], []
    for s, (sid, name, area, region, lat, lon, profile, kv) in enumerate(SUBSTATIONS):
        substations.append({"substation_id": sid, "name": name, "service_area": area, "region": region,
                            "latitude": lat, "longitude": lon})
        place = name.removeprefix("Example ")
        for n in range(1, FEEDERS_PER_SUBSTATION + 1):
            f = s * FEEDERS_PER_SUBSTATION + n - 1
            fid = f"FD-{sid[3:]}-{n:02d}"
            feeders.append({"feeder_id": fid, "substation_id": sid, "name": f"{place} feeder {n:02d}",
                            "voltage_kv": kv})
            angle = (n - 1) * math.pi / 4 + 0.2
            for p, (pos, kind, _) in enumerate(POSITIONS):
                i = f * len(POSITIONS) + p
                rated_kw = {"cable": 6000 * kv // 10, "transformer": 630 if i % 2 == 0 else 400,
                            "mv_room": 4000 * kv // 10}[kind]
                distance = 0.004 * (p + 1) + 0.002
                assets.append({
                    "asset_id": f"AS-{sid[3:]}-{n:02d}-{pos}", "feeder_id": fid,
                    "name": f"{place} {n:02d} {kind.replace('mv_room', 'MV room')} {pos}", "asset_type": kind,
                    "rated_kw": rated_kw, "commissioning_year": 1972 + (i * 7) % 50,
                    "latitude": round(lat + distance * math.sin(angle), 5),
                    "longitude": round(lon + distance * 1.6 * math.cos(angle), 5),
                    "profile": profile, "index": i,
                })
            # Large customers connect to the feeder's MV room: most are connected, some still to come.
            room, sector = f"AS-{sid[3:]}-{n:02d}-R1", SECTORS[profile][(f + f // 3) % len(SECTORS[profile])]
            for status in ("connected", "future"):
                if (status == "connected" and f % 3 == 2) or (status == "future" and f % 4 != 1 and f % 7 != 3):
                    continue
                offtake = 100 if sector == "solar_farm" else (
                    1500 + (f * 190) % 2500 if sector == "data_center" else 500 + (f * 170) % 1500)
                feed_in = (2000 + (f * 130) % 2000 if sector == "solar_farm" else
                           1000 + (f * 90) % 800 if sector == "greenhouse" else 0)
                if status == "future":
                    offtake += 0 if sector == "solar_farm" else 600 + (f * 230) % 1800
                    feed_in = feed_in * 3 // 2
                customers.append({
                    "asset_id": room, "sector": sector, "status": status,
                    "name": f"Example {sector.replace('_', ' ')} {len(customers) + 1}",
                    "offtake_kw": offtake, "feed_in_kw": feed_in,
                    "planned_year": 2027 + (f * 7 + f // 6) % 9 if status == "future" else None,
                })
    for number, customer in enumerate(customers, start=1):
        customer["customer_id"] = f"LC-{number:04d}"
    return substations, feeders, assets, customers


def generate_grid_forecast(contract):
    """Deterministic LazyFrames for every dataset in the contract; nothing is collected yet.

    All power is computed in whole kW, so every sum is exact whatever order Polars' streaming engine
    adds rows in; MW and utilization are derived once, at the end.
    """
    horizon = contract["horizon"]
    first, last = horizon["first_year"], horizon["last_year"]
    substations, feeders, assets, customers = grid_rows()
    years = pl.LazyFrame({"year": range(first, last + 1)}, schema={"year": pl.Int32})
    asset = pl.LazyFrame(assets).with_columns(
        type_pct=pl.col("asset_type").replace_strict({kind: pct for _, kind, pct in POSITIONS}),
        spread_pct=70 + (pl.col("index") * 37) % 61,
        base_pct=85 + (pl.col("index") * 29) % 31,
    )
    profiles = pl.LazyFrame([{"profile": name, "model_id": model, "weight_pct": weight}
                             for name, weights in PROFILES.items() for model, weight in weights.items()])
    models = pl.LazyFrame([
        {"model_id": m, "name": name, "technology": tech, "direction": direction, "assumption": assumption,
         "sign": sign} for m, name, tech, direction, assumption, _, _, sign in SPREADING_MODELS
    ])
    curves = pl.LazyFrame([
        {"model_id": m, "year": year, "permille": adoption(year, mid, k, horizon["reference_year"], last)}
        for m, *_, mid, k, _ in SPREADING_MODELS for year in range(first, last + 1)
    ], schema={"model_id": pl.String, "year": pl.Int32, "permille": pl.Int64})
    contribution = (
        asset.join(models.select("model_id", "direction", "sign"), how="cross", maintain_order="left_right")
        .join(profiles, on=["profile", "model_id"])
        .join(curves, on="model_id")
        .with_columns(contribution_kw=pl.col("sign") * (
            pl.col("rated_kw") * pl.col("weight_pct") * pl.col("type_pct") * pl.col("spread_pct")
            * pl.col("permille") // 1_000_000_000))
    )
    active = (
        pl.LazyFrame(customers, schema_overrides={"planned_year": pl.Int32})
        .join(years, how="cross")
        .filter((pl.col("status") == "connected") | (pl.col("year") >= pl.col("planned_year")))
        .unpivot(["offtake_kw", "feed_in_kw"], index=["asset_id", "year"], variable_name="direction",
                 value_name="customer_kw")
        .with_columns(pl.col("direction").str.strip_suffix("_kw"))
        .group_by("asset_id", "year", "direction").agg(pl.col("customer_kw").sum())
    )
    spreading = contribution.group_by("asset_id", "year", "direction").agg(spreading_kw=pl.col("contribution_kw").sum())
    baseline = (
        asset.join(profiles.filter(pl.col("model_id").is_in(["offtake", "feed_in"])), on="profile")
        .select("asset_id", "rated_kw", direction="model_id", baseline_kw=pl.col("rated_kw") * pl.col("weight_pct")
                * pl.col("type_pct") * pl.col("base_pct") // 1_000_000)
    )
    forecast = (
        baseline.join(years, how="cross")
        .join(spreading, on=["asset_id", "year", "direction"], how="left")
        .join(active, on=["asset_id", "year", "direction"], how="left")
        .with_columns(pl.col("spreading_kw", "customer_kw").fill_null(0))
        .with_columns(peak_kw=pl.col("baseline_kw") + pl.col("spreading_kw") + pl.col("customer_kw"))
        .with_columns(
            *(pl.col(f"{name}_kw") / 1000 for name in ("baseline", "spreading", "customer", "peak")),
            utilization_pct=(100 * pl.col("peak_kw") / pl.col("rated_kw")).round(1),
        )
        .rename({f"{name}_kw": f"{name}_mw" for name in ("baseline", "spreading", "customer", "peak")})
    )
    asset = asset.with_columns(rated_mw=pl.col("rated_kw") / 1000)
    frames = {
        "SUBSTATION": pl.LazyFrame(substations),
        "FEEDER": pl.LazyFrame(feeders),
        "ASSET": asset,
        "LOAD_FORECAST": forecast,
        "SPREADING_MODEL": models,
        "SPREADING_CONTRIBUTION": contribution.with_columns(contribution_mw=pl.col("contribution_kw") / 1000),
        "LARGE_CUSTOMER": pl.LazyFrame(customers, schema_overrides={"planned_year": pl.Int32}).with_columns(
            *(pl.when(pl.col("status") == status).then(pl.col(f"{direction}_kw") / 1000).alias(f"{prefix}_{direction}_mw")
              for status, prefix in (("connected", "contracted"), ("future", "requested"))
              for direction in ("offtake", "feed_in"))),
    }
    frames["CONGESTION"] = derive_congestion(frames["LOAD_FORECAST"], frames["ASSET"], contract)
    datasets = contract["datasets"]
    return {
        name: frame.select(*(pl.col(c).cast(t) for c, t in polars_schema(datasets[name]).items() if c != "synthetic"),
                           synthetic=pl.lit(True)).sort(datasets[name]["primary_key"])
        for name, frame in frames.items()
    }


def derive_congestion(load_forecast, asset, contract):
    """The precomputed congestion outlook: per asset and direction, when congestion starts and how bad it gets."""
    threshold = contract["congestion_threshold_pct"]
    congested = pl.col("utilization_pct") >= threshold
    severity = pl.when(pl.col("peak_utilization_pct") >= SEVERITY[0][0]).then(pl.lit(SEVERITY[0][1]))
    for bound, name in SEVERITY[1:]:
        severity = severity.when(pl.col("peak_utilization_pct") >= bound).then(pl.lit(name))
    return (
        load_forecast.join(asset.select("asset_id", "rated_mw"), on="asset_id")
        .group_by("asset_id", "direction")
        .agg(
            first_congestion_year=pl.col("year").filter(congested).min(),
            peak_utilization_pct=pl.col("utilization_pct").max(),
            peak_year=pl.col("year").sort_by("utilization_pct", "year", descending=[True, False]).first(),
            congested_years=congested.sum(),
            overload_mw=(pl.col("peak_mw") - pl.col("rated_mw")).max().clip(lower_bound=0).round(3),
        )
        .with_columns(severity=severity.otherwise(pl.lit("none")))
    )


def collect_product(frames):
    """Run every plan at once, so checks, previews and writes all see the same rows."""
    return dict(zip(frames, pl.collect_all(frames.values()), strict=True))


def quality_report(tables, contract):
    """Execute the reviewed SQL checks in the contract with Polars SQL, over one table per dataset."""
    sql = pl.SQLContext(tables, eager=True)
    return [{"check": check["name"], "violations": sql.execute(check["sql"]).item()}
            for check in contract["quality_checks"]]


def require_quality(report):
    failed = [row["check"] for row in report if row["violations"] != 0]
    if failed:
        raise ValueError("Grid forecast quality checks failed: " + "; ".join(failed))


def context(item, *keys):
    """Ossie `ai_context` from the keys of a contract entry that it has."""
    found = {key: item[key] for key in keys if item.get(key)}
    return {"ai_context": found} if found else {}


def field_entry(name, field):
    entry = {"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]},
             "datatype": TYPES[field["type"]][2]}
    for key in ("label", "description"):
        if field.get(key):
            entry[key] = field[key]
    if field.get("dimension"):
        entry["dimension"] = {"is_time": field["dimension"] == "time"}
    return {**entry, **context(field, "synonyms", "examples")}


def semantic_model(contract, namespace, catalog="lakehouse"):
    """The product as one Apache Ossie semantic model, ready to store in Polaris.

    Everything comes from the contract: datasets point at the tables notebook 09 publishes,
    relationships state the joins, metrics carry the agreed SQL and the AI context the product facts.
    Notebook 10 needs nothing else.
    """
    datasets, concepts = contract["datasets"], set(contract["concepts"])
    columns = {name: set(fields(d)) for name, d in datasets.items()}
    for name, dataset in datasets.items():
        for key in [*dataset["primary_key"], *dataset.get("partition_by", [])]:
            if key not in columns[name]:
                raise ValueError(f"{name} has no field {key}.")
        for field, entry in fields(dataset).items():
            if entry.get("label") and entry["label"] not in concepts:
                raise ValueError(f"The label of {name}.{field} is not a concept in the contract.")
            if entry["type"] not in TYPES:
                raise ValueError(f"{name}.{field} has an unknown type {entry['type']!r}.")
    for r in contract["relationships"]:
        if not set(r["from_columns"]) <= columns.get(r["from"], set()) or \
                not set(r["to_columns"]) <= columns.get(r["to"], set()):
            raise ValueError(f"Relationship {r['name']} uses a field that is not in the contract.")
    for metric, entry in contract["metrics"].items():
        for dataset, column in re.findall(r"\b([A-Z][A-Z_]*)\.([a-z_][a-z0-9_]*)", entry["sql"]):
            if column not in columns.get(dataset, set()):
                raise ValueError(f"Metric {metric} reads {dataset}.{column}, which is not in the contract.")
    return {
        "name": "mv_load_forecast",
        "description": f"{' '.join(contract['description'].split())} Grain: {' '.join(contract['grain'].split())}",
        "ai_context": {
            "instructions": "\n".join(
                [
                    f"Time: {' '.join(contract['time_convention'].split())}",
                    f"Grain: {' '.join(contract['grain'].split())}",
                    f"Missing values: {' '.join(contract['null_policy'].split())}",
                    f"Owner: {contract['owner']}. Refresh: {contract['refresh']}",
                    f"Classification: {contract['classification']}",
                    f"Scope: {' '.join(contract['scope'].split())}",
                    *contract["ai_guidance"],
                ]
            ),
        },
        "datasets": [
            {
                "name": name,
                "source": ".".join([catalog, *namespace, d["table"]]),
                "description": " ".join(d["description"].split()),
                "primary_key": d["primary_key"],
                **({"ai_context": {"instructions": " ".join(d["instructions"].split())}} if d.get("instructions") else {}),
                "fields": [field_entry(field, entry) for field, entry in fields(d).items()],
            }
            for name, d in datasets.items()
        ],
        "relationships": [
            {key: r[key] for key in ("name", "from", "to", "from_columns", "to_columns")}
            for r in contract["relationships"]
        ],
        "metrics": [
            {
                "name": name,
                "description": f"{metric['description']} Unit: {metric['unit']}.",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": metric["sql"]}]},
            }
            for name, metric in contract["metrics"].items()
        ],
    }


def table_properties(name, contract, namespace, report):
    """The table comment, this example's ownership and the Bridge producer conventions."""
    dataset, now = contract["datasets"][name], datetime.now(UTC).isoformat(timespec="seconds")
    passed = sum(row["violations"] == 0 for row in report)
    properties = {
        "comment": " ".join(dataset["description"].split()),
        EXAMPLE_PROPERTY: EXAMPLE,
        PRODUCER + "producer": "notebook",
        PRODUCER + "producer.version": f"polars {pl.__version__}",
        PRODUCER + "producer.node": f"{EXAMPLE}.{name}",
        PRODUCER + "producer.run-at": now,
        PRODUCER + "quality.status": "pass" if passed == len(report) else "fail",
        PRODUCER + "quality.summary": f"{passed} of {len(report)} contract checks passed",
        PRODUCER + "quality.checked-at": now,
    }
    inputs = [contract["datasets"][source]["table"] for source in dataset.get("derived_from", [])]
    if inputs:
        properties[PRODUCER + "lineage.inputs"] = json.dumps(
            [{"database": os.environ["ICEBERG_DATABASE"], "namespace": list(namespace), "name": table}
             for table in inputs])
    return properties


def shape(table):
    return ([(f.name, str(f.field_type)) for f in table.schema().fields],
            [table.schema().find_column_name(f.source_id) for f in table.spec().fields])


def publish_grid_forecast(catalog, tables, contract, namespace, report):
    """Write every table with Polars' Iceberg sink, after checking that this example owns them all.

    A rerun overwrites the rows of the same tables, so their identity, snapshot history and data
    shares survive; only a table whose columns or partitioning changed in the contract is recreated.
    Each table is a separate Iceberg commit.
    """
    require_quality(report)
    datasets = contract["datasets"]
    identifiers = {name: (*namespace, d["table"]) for name, d in datasets.items()}
    for identifier in identifiers.values():
        if catalog.table_exists(identifier) and \
                catalog.load_table(identifier).properties.get(EXAMPLE_PROPERTY) != EXAMPLE:
            raise RuntimeError(f"{identifier[-1]} already exists and is not owned by this example. "
                               "Choose another NAMESPACE.")
    catalog.create_namespace_if_not_exists(tuple(namespace))
    results = []
    for name, identifier in identifiers.items():
        dataset, properties = datasets[name], table_properties(name, contract, namespace, report)
        schema = iceberg_schema(dataset)
        spec = partition_spec(schema, dataset)
        expected = ([(f.name, str(f.field_type)) for f in schema.fields], dataset.get("partition_by", []))
        table = catalog.load_table(identifier) if catalog.table_exists(identifier) else None
        if table is not None and shape(table) != expected:
            catalog.drop_table(identifier)
            table = None
        if table is None:
            table = catalog.create_table(identifier, schema=schema, partition_spec=spec, properties=properties)
        else:
            with table.transaction() as transaction:
                transaction.set_properties(properties)
                with transaction.update_schema() as update:
                    for field in schema.fields:
                        update.update_column(field.name, doc=field.doc)
            table = catalog.load_table(identifier)
        tables[name].lazy().sink_iceberg(
            table,
            # Overwrite replaces the rows in one commit; a new table has none to replace.
            mode="overwrite" if table.current_snapshot() else "append",
            snapshot_properties={EXAMPLE_PROPERTY: EXAMPLE, "iceberg-data-platform.product-version": contract["version"]},
            storage_options=polars_storage_options(table),
        )
        table = catalog.load_table(identifier)
        results.append({
            "dataset": name, "table": ".".join(["lakehouse", *identifier]), "rows": tables[name].height,
            "partitioned by": ", ".join(dataset.get("partition_by", [])) or "-",
            "snapshot": str(table.current_snapshot().snapshot_id),
        })
    return results
