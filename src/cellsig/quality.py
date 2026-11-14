"""Declarative data-quality checks. A failed 'critical' check stops the pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import duckdb


@dataclass
class Check:
    name: str
    sql: str                 # must return a single integer: number of violating rows
    severity: str = "critical"


@dataclass
class Result:
    name: str
    severity: str
    violations: int

    @property
    def passed(self) -> bool:
        return self.violations == 0


class QualityError(RuntimeError):
    pass


def run_checks(con: duckdb.DuckDBPyConnection, checks: list[Check]) -> list[Result]:
    results = [Result(c.name, c.severity, int(con.execute(c.sql).fetchone()[0])) for c in checks]
    con.execute("CREATE OR REPLACE TABLE dq_results (name VARCHAR, severity VARCHAR, violations BIGINT, passed BOOLEAN)")
    con.executemany("INSERT INTO dq_results VALUES (?, ?, ?, ?)", [(r.name, r.severity, r.violations, r.passed) for r in results])
    failed = [r for r in results if r.severity == "critical" and not r.passed]
    if failed:
        raise QualityError("critical data-quality checks failed: " + ", ".join(f"{r.name}={r.violations}" for r in failed))
    return results


def as_records(results: list[Result]) -> list[dict]:
    return [asdict(r) | {"passed": r.passed} for r in results]


def telemetry_checks(rated_tol_pct: float = 2.0) -> list[Check]:
    return [
        Check("telemetry_quarantined_rows", "SELECT count(*) FROM quarantine_telemetry", severity="warn"),
        Check("quarantine_under_0.01pct", """SELECT CASE WHEN (SELECT count(*) FROM quarantine_telemetry)
                 > 0.0001 * (SELECT count(*) FROM raw_telemetry) THEN 1 ELSE 0 END"""),
        Check("telemetry_duplicate_samples", """SELECT count(*) - count(DISTINCT (source, cell_id, op_idx, phase, t_s))
                                               FROM stg_telemetry"""),
        Check("mart_unique_cycle", "SELECT count(*) - count(DISTINCT (source, cell_id, cycle)) FROM mart_cycle_signals"),
        Check("mart_capacity_present", "SELECT count(*) FROM mart_cycle_signals WHERE cap_ah IS NULL OR cap_ah <= 0"),
        # Coulomb counting must agree with the instrument's own capacity within tol% of rated capacity
        Check("capacity_matches_instrument", f"""SELECT count(*) FROM mart_cycle_signals
              WHERE ref_cap_ah IS NOT NULL AND n_loaded >= 20
                AND abs(cap_ah - ref_cap_ah) > {rated_tol_pct / 100} * CASE source WHEN 'NASA' THEN 2.0 ELSE 1.1 END"""),
        Check("calce_dcir_logger_limited", """SELECT count(*) FROM mart_cycle_signals
              WHERE source = 'CALCE' AND round(dcir_ohm * i_dis_a, 3) = 0.200""", severity="warn"),
    ]
