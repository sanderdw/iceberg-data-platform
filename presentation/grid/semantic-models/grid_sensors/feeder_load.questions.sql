-- Q: Which feeders ran above their rating last week, and for how many hours?
-- query_semantic_model: {"metrics": ["overload_hours"], "dimensions": [{"field": "FEEDER.name"}], "filters": [{"field": "FEEDER_LOAD.measured_date", "op": "between", "value": ["2026-09-21", "2026-09-27"]}, {"metric": "overload_hours", "op": ">", "value": 0}], "order_by": [{"name": "overload_hours", "desc": true}]}
SELECT FEEDER.name,
       count(*) FILTER (WHERE FEEDER_LOAD.utilisation_pct > 100 AND FEEDER_LOAD.quality_flag IN ('OK', 'ESTIMATED')) / 4.0 AS overload_hours
FROM FEEDER_LOAD JOIN FEEDER ON FEEDER_LOAD.feeder_id = FEEDER.feeder_id
WHERE CAST(FEEDER_LOAD.measured_at AS DATE) BETWEEN DATE '2026-09-21' AND DATE '2026-09-27'
GROUP BY FEEDER.name
HAVING overload_hours > 0
ORDER BY overload_hours DESC;

-- Q: How did the daily peak load per substation develop over the last two weeks?
-- query_semantic_model: {"metrics": ["peak_load_kw"], "dimensions": [{"field": "SUBSTATION.name"}, {"field": "FEEDER_LOAD.measured_date", "grain": "day"}], "filters": [{"field": "FEEDER_LOAD.measured_date", "op": ">=", "value": "2026-09-14"}], "order_by": [{"name": "SUBSTATION.name"}, {"name": "FEEDER_LOAD.measured_date:day"}]}
SELECT SUBSTATION.name, CAST(FEEDER_LOAD.measured_at AS DATE) AS measured_date,
       max(FEEDER_LOAD.load_kw) FILTER (WHERE FEEDER_LOAD.quality_flag IN ('OK', 'ESTIMATED')) AS peak_load_kw
FROM FEEDER_LOAD
JOIN FEEDER ON FEEDER_LOAD.feeder_id = FEEDER.feeder_id
JOIN SUBSTATION ON FEEDER.substation_id = SUBSTATION.substation_id
WHERE CAST(FEEDER_LOAD.measured_at AS DATE) >= DATE '2026-09-14'
GROUP BY 1, 2
ORDER BY 1, 2;

-- Q: How many hours of reverse power flow did each rural feeder have?
-- query_semantic_model: {"metrics": ["reverse_flow_hours"], "dimensions": [{"field": "FEEDER.name"}], "filters": [{"field": "FEEDER.customer_mix", "op": "=", "value": "rural"}], "order_by": [{"name": "reverse_flow_hours", "desc": true}]}
SELECT FEEDER.name,
       count(*) FILTER (WHERE FEEDER_LOAD.load_kw < 0 AND FEEDER_LOAD.quality_flag IN ('OK', 'ESTIMATED')) / 4.0 AS reverse_flow_hours
FROM FEEDER_LOAD JOIN FEEDER ON FEEDER_LOAD.feeder_id = FEEDER.feeder_id
WHERE FEEDER.customer_mix = 'rural'
GROUP BY FEEDER.name
ORDER BY reverse_flow_hours DESC;

-- Q: What is the average utilisation per region?
-- query_semantic_model: {"metrics": ["avg_utilisation_pct", "max_utilisation_pct"], "dimensions": [{"field": "SUBSTATION.region"}], "order_by": [{"name": "avg_utilisation_pct", "desc": true}]}
SELECT SUBSTATION.region,
       avg(abs(FEEDER_LOAD.utilisation_pct)) FILTER (WHERE FEEDER_LOAD.quality_flag IN ('OK', 'ESTIMATED')) AS avg_utilisation_pct,
       max(FEEDER_LOAD.utilisation_pct) FILTER (WHERE FEEDER_LOAD.quality_flag IN ('OK', 'ESTIMATED')) AS max_utilisation_pct
FROM FEEDER_LOAD
JOIN FEEDER ON FEEDER_LOAD.feeder_id = FEEDER.feeder_id
JOIN SUBSTATION ON FEEDER.substation_id = SUBSTATION.substation_id
GROUP BY SUBSTATION.region
ORDER BY avg_utilisation_pct DESC;

-- Q: What share of the readings per feeder is suspect or missing?
-- query_semantic_model: {"metrics": ["invalid_reading_pct"], "dimensions": [{"field": "FEEDER.name"}], "order_by": [{"name": "invalid_reading_pct", "desc": true}], "limit": 5}
SELECT FEEDER.name,
       100.0 * count(*) FILTER (WHERE FEEDER_LOAD.quality_flag IN ('SUSPECT', 'MISSING')) / nullif(count(*), 0) AS invalid_reading_pct
FROM FEEDER_LOAD JOIN FEEDER ON FEEDER_LOAD.feeder_id = FEEDER.feeder_id
GROUP BY FEEDER.name
ORDER BY invalid_reading_pct DESC
LIMIT 5;

-- Q: Which cable types ran hottest?
-- query_semantic_model: {"metrics": ["max_cable_temp_c", "max_utilisation_pct"], "dimensions": [{"field": "FEEDER.cable_type"}], "order_by": [{"name": "max_cable_temp_c", "desc": true}]}
SELECT FEEDER.cable_type,
       max(FEEDER_LOAD.cable_temp_c) FILTER (WHERE FEEDER_LOAD.cable_temp_c > -100 AND FEEDER_LOAD.quality_flag IN ('OK', 'ESTIMATED')) AS max_cable_temp_c,
       max(FEEDER_LOAD.utilisation_pct) FILTER (WHERE FEEDER_LOAD.quality_flag IN ('OK', 'ESTIMATED')) AS max_utilisation_pct
FROM FEEDER_LOAD JOIN FEEDER ON FEEDER_LOAD.feeder_id = FEEDER.feeder_id
GROUP BY FEEDER.cable_type
ORDER BY max_cable_temp_c DESC;
