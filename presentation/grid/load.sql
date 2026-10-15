-- Load the SCADA export into grid-sensors, as yourself, with the DuckDB CLI:
--   cd .local/presentation && uv run ../../user_portal/client/iceberg_connect.py shell db-… --write < ../../presentation/grid/load.sql
CREATE SCHEMA IF NOT EXISTS lakehouse.grid_sensors;
CREATE TABLE lakehouse.grid_sensors.substation AS FROM 'scada-export/substation.parquet';
CREATE TABLE lakehouse.grid_sensors.feeder AS FROM 'scada-export/feeder.parquet';
CREATE TABLE lakehouse.grid_sensors.feeder_load AS FROM 'scada-export/feeder_load.parquet';
SELECT 'feeder_load' AS "table", count(*) AS "rows" FROM lakehouse.grid_sensors.feeder_load;
