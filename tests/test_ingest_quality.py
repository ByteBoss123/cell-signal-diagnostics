import duckdb
import pandas as pd
import pytest

from cellsig import quality, warehouse
from cellsig.ingest import calce
from conftest import synthetic_op


def _book(path, start, cycles):
    rows = []
    t = 0.0
    for c in range(1, cycles + 1):
        for k in range(5):
            t += 30
            rows.append({"Data_Point": len(rows) + 1, "Test_Time(s)": t, "Date_Time": start + pd.to_timedelta(float(t), unit="s"),
                         "Step_Time(s)": 0, "Step_Index": 7, "Cycle_Index": c, "Current(A)": -1.1, "Voltage(V)": 4.0 - 0.1 * k,
                         "Discharge_Capacity(Ah)": 0.01 * len(rows), "Internal_Resistance(Ohm)": 0.09})
    with pd.ExcelWriter(path) as w:
        pd.DataFrame({"x": [1]}).to_excel(w, sheet_name="Info", index=False)
        pd.DataFrame(rows).to_excel(w, sheet_name="Channel_1-008", index=False)


def test_calce_renumbers_cycles_and_drops_duplicate_workbook(tmp_path):
    d = tmp_path / "CS2_99"
    d.mkdir()
    _book(d / "b_second.xlsx", pd.Timestamp("2010-02-01"), 3)
    _book(d / "a_first.xlsx", pd.Timestamp("2010-01-01"), 2)
    _book(d / "c_copy.xlsx", pd.Timestamp("2010-02-01"), 3)          # exact copy of b_second
    cell, dropped = calce.load_cell(d)
    assert sorted(cell.cycle.unique()) == [1, 2, 3, 4, 5]               # 2 + 3, ordered by first timestamp
    assert len(dropped) == 1 and dropped[0]["duplicate_of"] == "b_second.xlsx"


def test_glitch_rows_are_quarantined_not_used(tmp_path):
    op = synthetic_op()
    op.loc[0, "voltage_v"] = 8.39                                     # NASA-style sensor glitch
    stg = tmp_path / "staging"
    warehouse.write_staging(op, pd.DataFrame({"cell_id": ["B0005"], "start_ts": [pd.Timestamp("2020-01-01")], "re_ohm": [0.05], "rct_ohm": [0.07]}),
                            pd.DataFrame(columns=["cell_id", "file", "duplicate_of", "rows"]), stg)
    con = warehouse.build(tmp_path / "w.duckdb", stg)
    assert con.execute("SELECT count(*) FROM quarantine_telemetry").fetchone()[0] == 1
    assert con.execute("SELECT max(voltage_v) FROM stg_telemetry").fetchone()[0] < 4.6
    # 1 glitch in ~360 rows is far above the 0.01% budget, so the critical gate must stop the run
    with pytest.raises(quality.QualityError, match="quarantine_under_0.01pct"):
        quality.run_checks(con, quality.telemetry_checks())


def test_critical_check_failure_stops_pipeline():
    con = duckdb.connect()
    con.execute("CREATE TABLE x AS SELECT 1 AS a")
    with pytest.raises(quality.QualityError):
        quality.run_checks(con, [quality.Check("always_bad", "SELECT 1")])
