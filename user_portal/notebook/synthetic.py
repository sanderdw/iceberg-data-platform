"""Deterministic fictional data for the bundled notebooks: quarter-hour energy and sensor events."""

from datetime import UTC


def generate_energy_data(homes=40, days=7):
    import math
    import random
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    import pyarrow as pa

    rng = random.Random(42)
    local_tz = ZoneInfo("Europe/Amsterdam")
    start_utc = datetime(2026, 9, 1, tzinfo=local_tz).astimezone(UTC)
    streets = ["Example Solar Street", "Example Wind Avenue", "Example Energy Lane", "Example Light Road"]
    cloud_factors = [rng.uniform(0.35, 0.95) for _ in range(days)]
    rows = []

    for home_index in range(homes):
        home_id = f"SYN-{home_index + 1:03d}"
        street = streets[home_index // 10]
        house_number = home_index % 10 + 1
        occupants = rng.randint(1, 5)
        solar_kwp = round(rng.uniform(2.5, 6.5), 2) if rng.random() < 0.65 else 0.0
        home_factor = rng.uniform(0.8, 1.25)
        for interval in range(days * 96):
            timestamp = start_utc + timedelta(minutes=15 * interval)
            local_time = timestamp.astimezone(local_tz)
            hour = local_time.hour + local_time.minute / 60
            morning = math.exp(-0.5 * ((hour - 7.5) / 1.1) ** 2)
            evening = math.exp(-0.5 * ((hour - 19.0) / 1.8) ** 2)
            weekend_factor = 1.12 if local_time.weekday() >= 5 else 1.0
            load_kw = 0.14 + 0.045 * occupants + 0.35 * morning + 0.60 * evening
            consumption = round(load_kw * home_factor * weekend_factor * rng.uniform(0.85, 1.15) * 0.25, 4)
            daylight = max(0.0, math.sin(math.pi * (hour - 7.0) / 13.0)) if 7 <= hour <= 20 else 0.0
            generation = round(
                solar_kwp * daylight * cloud_factors[interval // 96] * rng.uniform(0.92, 1.0) * 0.25, 4
            )
            rows.append(
                {
                    "timestamp_utc": timestamp,
                    "home_id": home_id,
                    "neighborhood": "Fictional Solar District",
                    "city": "Example City",
                    "street": street,
                    "house_number": house_number,
                    "postal_code": "0000 ZZ",
                    "address": f"{street} {house_number}, 0000 ZZ Example City",
                    "occupants": occupants,
                    "solar_capacity_kwp": solar_kwp,
                    "consumption_kwh": consumption,
                    "generation_kwh": generation,
                    "grid_draw_kwh": round(max(consumption - generation, 0.0), 4),
                    "grid_export_kwh": round(max(generation - consumption, 0.0), 4),
                    "synthetic": True,
                }
            )

    arrow_schema = pa.schema(
        [
            ("timestamp_utc", pa.timestamp("us", tz="UTC")),
            ("home_id", pa.string()),
            ("neighborhood", pa.string()),
            ("city", pa.string()),
            ("street", pa.string()),
            ("house_number", pa.int32()),
            ("postal_code", pa.string()),
            ("address", pa.string()),
            ("occupants", pa.int32()),
            ("solar_capacity_kwp", pa.float64()),
            ("consumption_kwh", pa.float64()),
            ("generation_kwh", pa.float64()),
            ("grid_draw_kwh", pa.float64()),
            ("grid_export_kwh", pa.float64()),
            ("synthetic", pa.bool_()),
        ]
    )
    energy_data = pa.Table.from_pylist(rows, schema=arrow_schema)
    assert energy_data.num_rows == homes * days * 96
    assert len({(r["home_id"], r["timestamp_utc"]) for r in rows}) == len(rows)
    assert all(
        abs(r["consumption_kwh"] - r["generation_kwh"] - r["grid_draw_kwh"] + r["grid_export_kwh"]) < 1e-8
        for r in rows
    )

    return energy_data


def generate_sensor_events(devices=12, events=40):
    """Fictional sensor events that need Iceberg v3: free-form payloads, nanosecond timestamps, locations."""
    import json
    import random

    import pyarrow as pa

    rng = random.Random(42)
    start_ns = 1_790_000_000 * 1_000_000_000  # 2026-09-21T14:13:20Z
    sites = ["Example Plant North", "Example Plant South", "Example Depot"]
    rows = []
    for device_index in range(devices):
        device_id = f"SENSOR-{device_index + 1:03d}"
        site = sites[device_index % len(sites)]
        # Fictional coordinates on a small grid; WKT text becomes GEOMETRY in DuckDB.
        location = f"POINT ({5.0 + device_index % len(sites) / 10:.4f} {52.0 + device_index // len(sites) / 100:.4f})"
        clock_ns = start_ns + device_index * 1_000
        for _ in range(events):
            # Bursts arrive within one microsecond: microsecond precision would merge them.
            clock_ns += rng.randint(120, 880) if rng.random() < 0.4 else rng.randint(40_000, 900_000)
            kind = rng.choices(["temperature", "vibration", "door", "fault"], weights=[5, 3, 2, 1])[0]
            if kind == "temperature":
                payload = {"kind": kind, "celsius": round(rng.uniform(17.0, 84.0), 2), "unit": "C"}
            elif kind == "vibration":
                payload = {"kind": kind, "axes": {a: round(rng.uniform(0.0, 4.0), 3) for a in "xyz"}}
            elif kind == "door":
                payload = {"kind": kind, "open": rng.random() < 0.5, "badge": rng.randint(1000, 9999)}
            else:
                payload = {
                    "kind": kind,
                    "code": rng.choice(["E17", "E42", "E90"]),
                    "details": {"retries": rng.randint(0, 5), "tags": rng.sample(["power", "bus", "heat"], 2)},
                }
            rows.append(
                {
                    "event_id": len(rows) + 1,
                    "device_id": device_id,
                    "site": site,
                    "location_wkt": location,
                    "measured_at": clock_ns,
                    "payload": json.dumps(payload),
                    "synthetic": True,
                }
            )

    arrow_schema = pa.schema(
        [
            ("event_id", pa.int64()),
            ("device_id", pa.string()),
            ("site", pa.string()),
            ("location_wkt", pa.string()),
            ("measured_at", pa.timestamp("ns")),
            ("payload", pa.string()),
            ("synthetic", pa.bool_()),
        ]
    )
    sensor_events = pa.Table.from_pylist(rows, schema=arrow_schema)
    assert sensor_events.num_rows == devices * events
    assert len({(r["device_id"], r["measured_at"]) for r in rows}) == len(rows)
    # The nanosecond digits carry information: truncating to microseconds collides events.
    assert len({(r["device_id"], r["measured_at"] // 1_000) for r in rows}) < len(rows)
    return sensor_events
