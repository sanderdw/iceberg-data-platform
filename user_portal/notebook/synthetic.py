"""Deterministic fictional quarter-hour energy data for the bundled notebooks."""

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
