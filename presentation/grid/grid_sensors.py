# /// script
# requires-python = ">=3.10"
# dependencies = ["pyarrow>=20"]
# ///
"""Synthetic SCADA export of a fictional grid operator, for the deck's demo.

    uv run presentation/grid/grid_sensors.py .local/presentation/scada-export

It writes three Parquet files: `substation` (6 rows), `feeder` (24) and `feeder_load`: one row per
feeder per quarter-hour for two weeks, in UTC. load.sql loads them into the namespace
`grid_sensors` with the DuckDB CLI, as the signed-in user. The data is deterministic and on
purpose not clean, so the semantic-model interview has something to settle: evening peaks on EV
and heat-pump feeders that run above their rating, reverse power flow at midday on rural solar
feeders, a comms outage with NULL load, a broken temperature sensor reporting -999, and SUSPECT
spikes from the head-end. Every name is invented.
"""

import argparse
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Europe/Amsterdam")
START = datetime(2026, 9, 14, tzinfo=LOCAL)  # a Monday, local midnight
DAYS = 14

SUBSTATIONS = [
    ("SS-01", "Example Harbour", "Harbour"), ("SS-02", "Example Dockside", "Harbour"),
    ("SS-03", "Example Northgate", "North"), ("SS-04", "Example Millbrook", "North"),
    ("SS-05", "Example Lowfield", "Polder"), ("SS-06", "Example Reedmoor", "Polder"),
]
# Customer mix per substation's four feeders; the planners' own words.
MIX = {
    "SS-01": ["commercial", "commercial", "residential", "mixed"],
    "SS-02": ["commercial", "mixed", "residential", "residential"],
    "SS-03": ["residential", "residential", "residential", "mixed"],
    "SS-04": ["residential", "residential", "mixed", "rural"],
    "SS-05": ["rural", "rural", "rural", "residential"],
    "SS-06": ["rural", "rural", "mixed", "residential"],
}
# Feeders where heat pumps and EV chargers arrived faster than the cable was planned for.
CROWDED = {"FD-0302", "FD-0401", "FD-0303", "FD-0204"}
STREETS = ["Canal", "Mill", "Dyke", "Station", "Church", "Meadow", "Willow", "Harbour", "School", "Orchard"]
BROKEN_TEMPERATURE = ("FD-0103", 6 * 96, 8 * 96)  # sensor reports -999 for two days
COMMS_OUTAGE = ("FD-0502", 10 * 96 + 40, 10 * 96 + 64)  # six hours without data


def generate():
    import pyarrow as pa

    rng = random.Random(42)
    start_utc = START.astimezone(UTC)
    substations, feeders, loads = [], [], []
    for substation_id, name, region in SUBSTATIONS:
        substations.append({"substation_id": substation_id, "name": name, "region": region,
                            "voltage_kv": 10.0, "transformer_mva": rng.choice([20.0, 31.5, 40.0])})
    clouds = [rng.uniform(0.25, 1.0) for _ in range(DAYS)]
    for s_index, (substation_id, _, _) in enumerate(SUBSTATIONS):
        for f_index, mix in enumerate(MIX[substation_id]):
            feeder_id = f"FD-{s_index + 1:02d}{f_index + 1:02d}"
            old = rng.random() < 0.35
            rated = rng.choice([360, 400, 450] if old else [520, 600, 680])
            feeders.append({
                "feeder_id": feeder_id, "substation_id": substation_id,
                "name": f"{SUBSTATIONS[s_index][1].removeprefix('Example ')}-{f_index + 1} {rng.choice(STREETS)}",
                "customer_mix": mix, "rated_capacity_kw": float(rated),
                "cable_type": "GPLK 95 Cu" if old else rng.choice(["XLPE 150 Al", "XLPE 240 Al"]),
                "commissioned_year": rng.randint(1964, 1989) if old else rng.randint(1995, 2022),
            })
            pv = {"rural": rng.uniform(0.42, 0.62), "residential": rng.uniform(0.15, 0.3),
                  "mixed": rng.uniform(0.1, 0.25), "commercial": rng.uniform(0.05, 0.15)}[mix]
            crowded = feeder_id in CROWDED
            for interval in range(DAYS * 96):
                at = start_utc + timedelta(minutes=15 * interval)
                local = at.astimezone(LOCAL)
                hour = local.hour + local.minute / 60
                weekend = local.weekday() >= 5
                day = interval // 96
                morning = math.exp(-0.5 * ((hour - 7.5) / 1.0) ** 2)
                evening = math.exp(-0.5 * ((hour - 18.75) / 1.7) ** 2)
                office = 1.0 if 8 <= hour < 18 and not weekend else 0.0
                if mix == "commercial":
                    share = 0.18 + 0.52 * office + 0.08 * morning
                elif mix == "rural":
                    share = 0.16 + 0.10 * morning + 0.30 * evening
                else:
                    share = 0.20 + 0.14 * morning + (0.42 if mix == "residential" else 0.30) * evening + 0.12 * office
                if crowded:
                    # EV chargers after work, heat pumps through the evening; the second week was colder.
                    share += (0.2 + 0.018 * day) * math.exp(-0.5 * ((hour - 19.5) / 1.4) ** 2) + (0.05 if day >= 7 else 0.0)
                if weekend and mix != "commercial":
                    share *= 1.08
                daylight = max(0.0, math.sin(math.pi * (hour - 7.5) / 12.0)) if 7.5 <= hour <= 19.5 else 0.0
                share -= pv * daylight * clouds[day]
                load = rated * share * rng.uniform(0.95, 1.05)
                temperature = 14.0 + 3.0 * math.sin(math.pi * (hour - 9) / 12) + 26.0 * (max(load, 0) / rated) ** 2 + rng.uniform(-0.6, 0.6)
                quality = "OK"
                if rng.random() < 0.003:
                    load *= rng.uniform(2.5, 4.0)  # head-end spike, flagged by the SCADA plausibility check
                    quality = "SUSPECT"
                elif rng.random() < 0.01:
                    quality = "ESTIMATED"  # gap filled by the head-end from neighbouring intervals
                utilisation = 100.0 * load / rated
                voltage = 10500 - 260 * (load / rated) + rng.uniform(-25, 25)
                feeder, first, last = COMMS_OUTAGE
                if feeder_id == feeder and first <= interval < last:
                    load_kw = utilisation_pct = voltage_v = None
                    temperature, quality = None, "MISSING"
                else:
                    load_kw, utilisation_pct, voltage_v = round(load, 1), round(utilisation, 1), round(voltage, 0)
                feeder, first, last = BROKEN_TEMPERATURE
                if feeder_id == feeder and first <= interval < last:
                    temperature = -999.0
                loads.append({"feeder_id": feeder_id, "measured_at": at, "load_kw": load_kw,
                              "utilisation_pct": utilisation_pct, "voltage_v": voltage_v,
                              "cable_temp_c": None if temperature is None else round(temperature, 1),
                              "quality_flag": quality})
    timestamp = pa.timestamp("us", tz="UTC")
    return {
        "substation": pa.Table.from_pylist(substations, schema=pa.schema([
            ("substation_id", pa.string()), ("name", pa.string()), ("region", pa.string()),
            ("voltage_kv", pa.float64()), ("transformer_mva", pa.float64())])),
        "feeder": pa.Table.from_pylist(feeders, schema=pa.schema([
            ("feeder_id", pa.string()), ("substation_id", pa.string()), ("name", pa.string()),
            ("customer_mix", pa.string()), ("rated_capacity_kw", pa.float64()), ("cable_type", pa.string()),
            ("commissioned_year", pa.int32())])),
        "feeder_load": pa.Table.from_pylist(loads, schema=pa.schema([
            ("feeder_id", pa.string()), ("measured_at", timestamp), ("load_kw", pa.float64()),
            ("utilisation_pct", pa.float64()), ("voltage_v", pa.float64()), ("cable_temp_c", pa.float64()),
            ("quality_flag", pa.string())])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", help="where to write the Parquet files")
    args = parser.parse_args()
    out = Path(args.directory)
    out.mkdir(parents=True, exist_ok=True)
    import pyarrow.parquet as pq

    for name, data in generate().items():
        pq.write_table(data, out / f"{name}.parquet")
        print(f"{out / name}.parquet: {data.num_rows:,} rows")


if __name__ == "__main__":
    main()
