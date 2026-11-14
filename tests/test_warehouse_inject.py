import duckdb
import pytest

from cellsig import inject, warehouse
from conftest import synthetic_op


def signals(df):
    con = duckdb.connect()
    con.register("t", df)
    return warehouse.discharge_signals(con, "t").iloc[0]


def test_coulomb_count_matches_constant_current():
    s = signals(synthetic_op(i=2.0, minutes=60))
    assert s.cap_ah == pytest.approx(2.0, abs=1e-6)          # 2 A for exactly 1 h
    assert s.dcir_ohm == pytest.approx(0.1, abs=1e-9)         # step drop / step current
    assert s.temp_rise_c == pytest.approx(10.0, abs=1e-9)


def test_capacity_fade_removes_the_requested_share():
    op = synthetic_op()
    base = signals(op).cap_ah
    faded = signals(inject.apply(op, "capacity_fade_pct", 8, cutoff=2.5)).cap_ah
    assert faded / base == pytest.approx(0.92, abs=0.005)


def test_resistance_rise_shows_up_exactly_in_dcir():
    op = synthetic_op()
    s = signals(inject.apply(op, "resistance_rise_mohm", 20, cutoff=2.5))
    assert s.dcir_ohm == pytest.approx(0.1 + 0.020, abs=1e-9)


def test_resistance_rise_ends_discharge_at_cutoff():
    op = synthetic_op(slope=0.02)                 # falls to 4.0 - 1.2 = 2.8 V after 60 min
    s = signals(inject.apply(op, "resistance_rise_mohm", 200, cutoff=2.6))   # -0.4 V pushes it below 2.6
    assert s.v_min < 2.6 + 0.03 and s.cap_ah < 2.0


def test_voltage_offset_leaves_dcir_and_lowers_mean_voltage():
    op = synthetic_op()
    a, b = signals(op), signals(inject.apply(op, "voltage_offset_mv", 40, cutoff=2.0))
    assert b.dcir_ohm == pytest.approx(a.dcir_ohm, abs=1e-9)
    assert a.v_mean - b.v_mean == pytest.approx(0.040, abs=1e-9)


def test_thermal_ramp_adds_to_temperature_rise():
    op = synthetic_op()
    s = signals(inject.apply(op, "thermal_rise_c", 2, cutoff=2.0))
    assert s.temp_rise_c == pytest.approx(12.0, abs=1e-9)


def test_unknown_fault_rejected():
    with pytest.raises(ValueError):
        inject.apply(synthetic_op(), "magic", 1, cutoff=2.0)
