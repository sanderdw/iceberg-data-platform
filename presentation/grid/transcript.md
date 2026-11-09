# Agent session: load the SCADA export, then the semantic-model skill

The deck's "02 · Your computer" and "Build semantic models with your coding agent" slides are
built from this session. It ran against the local demo stack, signed in as `sander` (writer in
grid-planning). The commands and their output are real and shortened. sander's prompts and
answers were written for the demo.

- `SM` is `uv run .agents/skills/semantic-model/scripts/semantic_model.py`.
- `$DB` is the `grid-sensors` development database (`db-…`).
- MCP calls went to the user portal's `/mcp` with sander's own token.

Files:
- Work files: `semantic-models/grid_sensors/feeder_load.json` and `feeder_load.questions.sql`.
- The export: `grid_sensors.py` writes it. `load.sql` loads it.

## 0 · The ask

> **sander:** The SCADA export of the last two weeks is in scada-export/. Load it into
> grid-sensors and help me build a semantic model for it. Outage-response and the
> Conversational BI chat should be able to answer congestion questions from it.

## Load

`list_databases` (MCP) shows sander as writer in grid-planning, with `grid-sensors` (development).

```
$ uv run iceberg_connect.py login
Open http://localhost:8080/realms/iceberg/device?user_code=… and confirm the code ….
Signed in.
$ uv run iceberg_connect.py shell $DB --write < load.sql
┌─────────────┬───────┐
│    table    │ rows  │
├─────────────┼───────┤
│ feeder_load │ 32256 │
└─────────────┴───────┘
```

## 1–2 · Connect and scope

The skill reuses the sign-in and the MCP tools. It asks four questions together, each with a
suggested answer:

| Question | Suggested | sander |
| --- | --- | --- |
| Which namespace and tables? | `grid_sensors`: substation, feeder, feeder_load | Yes, all three |
| New model or an existing one? | New: `list_semantic_models` finds none | New |
| Who will use it, and for what? | Planners and a chat assistant | Planners in grid-planning, outage-response through a share, and Conversational BI |
| Model name? | `feeder_load` | `feeder_load` |

## 3 · Profile

```
$ SM profile $DB grid_sensors substation feeder feeder_load
## grid_sensors.feeder_load: 32,256 rows
- measured_at (TIMESTAMP WITH TIME ZONE): 0.00% null, min 2026-09-13 22:00:00+00, max 2026-09-27 21:45:00+00
- load_kw (DOUBLE): 0.07% null, min -417.6, max 1920.6
- utilisation_pct (DOUBLE): 0.07% null, min -69.6, max 282.4
- cable_temp_c (DOUBLE): 0.07% null, min -999.0, max 53.0
- quality_flag (VARCHAR): 0.00% null, ~4 distinct
  values: 'OK' (31,830), 'ESTIMATED' (299), 'SUSPECT' (103), 'MISSING' (24)
## grid_sensors.feeder: 24 rows
- rated_capacity_kw (DOUBLE): 0.00% null, min 360.0, max 680.0
- cable_type (VARCHAR): values: 'GPLK 95 Cu' (9), 'XLPE 150 Al' (8), 'XLPE 240 Al' (7)
```

What the agent noted for the interview:
- a load of 1,920.6 kW on feeders rated at most 680 kW
- negative load down to -417.6 kW
- a cable temperature of -999
- 24 rows with NULL measurements
- four quality codes
- timestamps in UTC that start at 22:00, which is local midnight

## 4 · Interview

**Round 1: the questions.** sander's six, in their own words:
1. Which feeders ran above their rating last week, and for how many hours?
2. How did the daily peak load per substation develop over the last two weeks?
3. How many hours of reverse power flow did each rural feeder have?
4. What is the average utilisation per region?
5. What share of the readings per feeder is suspect or missing?
6. Which cable types ran hottest?

**Round 2: grain, keys, time.**

| Question | Suggested | sander |
| --- | --- | --- |
| Grain of feeder_load? | One row per feeder per quarter-hour, key feeder_id + measured_at | Yes |
| What does measured_at mark? | Start of the interval, UTC | Start, UTC. We think in Amsterdam time, but UTC days are fine if the answer says so |
| What is "last week"? | The 7 days up to the newest reading | The last full week: 21 to 27 September |
| Joins? | feeder_load → feeder → substation | Yes |

**Round 3: metrics.**

| Question | Suggested | sander |
| --- | --- | --- |
| When is a feeder overloaded? | utilisation_pct > 100 | Yes, above its rated capacity. Count it in hours |
| Which readings count? | OK only | OK and ESTIMATED. SUSPECT failed the SCADA plausibility check: never count it |
| Negative load? | Reverse power flow | Yes, solar feed-in. We call it teruglevering |
| Average utilisation of a feeder with feed-in? | Signed average | Absolute: feed-in loads the cable too |

**Round 4: data quality, owner, sensitivity.**

| Question | Suggested | sander |
| --- | --- | --- |
| cable_temp_c = -999? | Broken sensor, treat as missing | Yes |
| MISSING rows? | Comms outage | Yes, the measurements are NULL |
| Owner and refresh? | grid-planning, daily | grid-planning; daily SCADA export at 06:00 UTC |
| Personal data? | None at feeder level | Internal, no personal data |

**Playback** (sander confirmed it):

| Term | Definition | Field or SQL |
| --- | --- | --- |
| Valid reading | quality_flag OK or ESTIMATED | `quality_flag IN ('OK', 'ESTIMATED')` |
| Overload hours | valid quarter-hours above rating, / 4 | `count(*) FILTER (WHERE utilisation_pct > 100 AND valid) / 4.0` |
| Reverse flow hours | valid quarter-hours with negative load, / 4 | `count(*) FILTER (WHERE load_kw < 0 AND valid) / 4.0` |
| Utilisation | absolute load / rated capacity | `avg(abs(utilisation_pct))` |
| Last week | 21 to 27 September 2026, UTC days | filter on `FEEDER_LOAD.measured_date` |

## 5 · Draft

```
$ SM draft $DB grid_sensors substation feeder feeder_load --name feeder_load \
    > semantic-models/grid_sensors/feeder_load.json
```

The draft was filled in with:
- 3 datasets with their primary keys
- relationships `feeder` (FEEDER_LOAD → FEEDER) and `substation` (FEEDER → SUBSTATION)
- the derived time field `measured_date` (Date)
- 7 metrics: overload_hours, peak_load_kw, max_utilisation_pct, avg_utilisation_pct, reverse_flow_hours, invalid_reading_pct and max_cable_temp_c
- instructions for Time, Grain, Missing values, Owner and Refresh, Classification, "last week" and how to answer
- synonyms (overbelasting, teruglevering, streng)
- the six questions as examples

## 6 · Check

```
$ SM check $DB grid_sensors feeder_load.json --questions feeder_load.questions.sql
Keys and relationships
  ok    FEEDER_LOAD key (feeder_id, measured_at) is unique: 0 violations
  ok    feeder: every FEEDER_LOAD finds its FEEDER: 0 violations
  ok    substation: every FEEDER finds its SUBSTATION: 0 violations
Questions
  ok    Which feeders ran above their rating last week, and for how many hours?
        name | overload_hours
        Dockside-4 Church | 6.5
        Northgate-2 Mill | 5.75
        Northgate-3 Station | 5.0
        Millbrook-1 Mill | 4.25
  ok    How did the daily peak load per substation develop over the last two weeks?
        Example Dockside | 2026-09-13 | 142.0
        Example Dockside | 2026-09-14 | 430.8
        …
0 errors, 0 failed checks, 0 warnings.
```

## 7 · Review

The agent shows the answers. The check passes, but the daily trend starts with a UTC day,
13 September, that peaks at 142 kW: that day holds only the first two hours of local
14 September. sander: "Leave that day out of trends." The agent adds an instruction and a filter
to question 2, and the check runs again: `0 errors, 0 failed checks, 0 warnings.` sander approves.

## 8 · Publish and verify

```
$ SM publish $DB grid_sensors feeder_load.json --name feeder_load --questions feeder_load.questions.sql
0 errors, 0 failed checks, 0 warnings.
created grid_sensors.feeder_load, entity version 1
```

`describe_semantic_model` (MCP) reports:
- 7 metrics, all on FEEDER_LOAD
- FEEDER fields reachable via `feeder`, SUBSTATION fields via `feeder` → `substation`
- `problems: []`

Each question is replayed through `query_semantic_model`:
- Five give the same answer as the check on the first try.
- The daily-peak call was refused: `Order by an output column: SUBSTATION.name, FEEDER_LOAD.measured_date:day, peak_load_kw.` With those column names in `order_by`, it returns 84 rows, the same as the check.

The agent closes with:

- Overload hours per feeder last week: 4 feeders, Dockside-4 Church first with 6.5 h
- Daily peak load per substation, 14 to 27 September
- Reverse power flow per rural feeder: Lowfield-3 Willow 77.75 h
- Average utilisation per region: Harbour 31.6 %, North 30.4 %, Polder 22.5 %
- Suspect or missing readings per feeder: Lowfield-2 Church 1.9 %
- Hottest cable type: GPLK 95 Cu, 53.0 °C at 120.3 % utilisation
