"""DuckDB layers: raw telemetry (Parquet) -> staging view -> per-cycle signal mart.

Every per-cycle signal is computed in SQL from the raw samples, so the mart can be rebuilt
from Parquet alone and audited line by line.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

DISCHARGE_SQL = """
WITH dis_op AS (            -- the op that holds each cycle's discharge
    SELECT source, cell_id, cycle, min(op_idx) AS op_idx
    FROM {src} WHERE phase = 'discharge' GROUP BY ALL
),
rows AS (                   -- all samples of that op (rest before/after load included)
    SELECT t.* FROM {src} t JOIN dis_op USING (source, cell_id, cycle, op_idx)
),
loaded AS (
    SELECT *,
        t_s - lag(t_s) OVER w AS dt,
        (abs(current_a) + abs(lag(current_a) OVER w)) / 2 AS i_avg,
        (abs(current_a) * voltage_v + abs(lag(current_a) OVER w) * lag(voltage_v) OVER w) / 2 AS p_avg
    FROM rows WHERE phase = 'discharge'
    WINDOW w AS (PARTITION BY source, cell_id, cycle ORDER BY t_s)
),
agg AS (
    SELECT source, cell_id, cycle,
        any_value(seg_start) AS seg_start,
        min(t_s) AS t_load0, max(t_s) AS t_load1,
        count(*) AS n_loaded,
        sum(dt * i_avg) / 3600 AS cap_ah,
        sum(dt * p_avg) / 3600 AS energy_wh,
        avg(voltage_v) AS v_mean,
        min(voltage_v) AS v_min,
        median(abs(current_a)) AS i_dis_a,
        arg_min(voltage_v, t_s) AS v_load0,
        arg_min(abs(current_a), t_s) AS i_load0,
        arg_min(temp_c, t_s) AS temp_load0,
        max(temp_c) AS temp_max,
        any_value(ref_cap_ah) AS ref_cap_ah
    FROM loaded GROUP BY ALL
),
rest_before AS (            -- last open-circuit sample before the load is applied
    SELECT r.source, r.cell_id, r.cycle, arg_max(r.voltage_v, r.t_s) AS v_rest
    FROM rows r JOIN agg a USING (source, cell_id, cycle)
    WHERE r.phase = 'rest' AND r.t_s < a.t_load0 GROUP BY ALL
),
ir AS (
    SELECT source, cell_id, cycle, median(ir_ohm) AS ir_ohm
    FROM {src} WHERE ir_ohm IS NOT NULL GROUP BY ALL
)
SELECT a.source, a.cell_id, a.cycle,
    a.seg_start + to_microseconds(CAST(a.t_load0 * 1e6 AS BIGINT)) AS dis_start,
    a.seg_start + to_microseconds(CAST(a.t_load1 * 1e6 AS BIGINT)) AS dis_end,
    a.t_load1 - a.t_load0 AS dur_s,
    a.n_loaded, a.cap_ah, a.energy_wh, a.v_mean, a.v_min, a.i_dis_a,
    rb.v_rest,
    (rb.v_rest - a.v_load0) / nullif(a.i_load0, 0) AS dcir_ohm,
    a.temp_max - a.temp_load0 AS temp_rise_c,
    a.temp_max,
    ir.ir_ohm,
    a.ref_cap_ah
FROM agg a
LEFT JOIN rest_before rb USING (source, cell_id, cycle)
LEFT JOIN ir USING (source, cell_id, cycle)
"""

CHARGE_SQL = """
WITH c AS (
    SELECT source, cell_id, cycle, op_idx, t_s, voltage_v, current_a,
        t_s - lag(t_s) OVER w AS dt,
        (current_a + lag(current_a) OVER w) / 2 AS i_avg
    FROM {src} WHERE phase = 'charge'
    WINDOW w AS (PARTITION BY source, cell_id, cycle, op_idx ORDER BY t_s)
)
SELECT source, cell_id, cycle,
    sum(dt * i_avg) / 3600 AS chg_ah,
    max(t_s) - min(t_s) AS chg_s,
    arg_max(current_a, t_s) AS chg_i_end_a,   -- current at charge termination; CC current = CV never tapered
    max(voltage_v) AS chg_v_max
FROM c GROUP BY ALL
"""

REST_GAP_SQL = """
CREATE OR REPLACE TABLE mart_cycle_signals AS
SELECT *,
    date_diff('second', lag(dis_end) OVER (PARTITION BY source, cell_id ORDER BY cycle), dis_start) / 3600.0 AS rest_before_h
FROM mart_cycle_signals
ORDER BY source, cell_id, cycle
"""


VALID = "voltage_v BETWEEN 0.5 AND 4.6 AND abs(current_a) <= 6 AND t_s >= 0"


def write_staging(telemetry: pd.DataFrame, impedance: pd.DataFrame, dropped: pd.DataFrame, staging_dir: Path) -> list[Path]:
    staging_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for src, part in telemetry.groupby("source"):
        p = staging_dir / f"telemetry_{src.lower()}.parquet"
        part.to_parquet(p, index=False)
        paths.append(p)
    for name, df in (("nasa_impedance", impedance), ("ingest_dropped", dropped)):
        p = staging_dir / f"{name}.parquet"
        df.to_parquet(p, index=False)
        paths.append(p)
    return paths


def discharge_signals(con: duckdb.DuckDBPyConnection, src: str) -> pd.DataFrame:
    """Per-cycle discharge signals for any relation with the telemetry schema (used for fault injection too)."""
    return con.execute(DISCHARGE_SQL.format(src=src)).df()


def build(db_path: Path, staging_dir: Path) -> duckdb.DuckDBPyConnection:
    staging_dir = Path(staging_dir)
    con = duckdb.connect(str(db_path))
    con.execute(f"CREATE OR REPLACE VIEW raw_telemetry AS SELECT * FROM read_parquet('{staging_dir / 'telemetry_*.parquet'}')")
    for side in ("nasa_impedance", "ingest_dropped"):
        p = staging_dir / f"{side}.parquet"
        if p.exists():
            con.execute(f"CREATE OR REPLACE TABLE {side} AS SELECT * FROM read_parquet('{p}')")
    # Physically impossible readings (sensor glitches, e.g. NASA rows at 8.4 V or 0 V) go to quarantine
    con.execute(f"CREATE OR REPLACE TABLE quarantine_telemetry AS SELECT * FROM raw_telemetry WHERE NOT coalesce({VALID}, false)")
    con.execute(f"CREATE OR REPLACE VIEW stg_telemetry AS SELECT * FROM raw_telemetry WHERE coalesce({VALID}, false)")
    con.execute("CREATE OR REPLACE TABLE mart_discharge AS " + DISCHARGE_SQL.format(src="stg_telemetry"))
    con.execute("CREATE OR REPLACE TABLE mart_charge AS " + CHARGE_SQL.format(src="stg_telemetry"))
    con.execute("""CREATE OR REPLACE TABLE mart_cycle_signals AS
        SELECT d.*, c.chg_ah, c.chg_s, c.chg_i_end_a, c.chg_v_max
        FROM mart_discharge d LEFT JOIN mart_charge c USING (source, cell_id, cycle)""")
    con.execute(REST_GAP_SQL)
    return con
