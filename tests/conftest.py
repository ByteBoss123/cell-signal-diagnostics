from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def cfg():
    return yaml.safe_load((ROOT / "config" / "params.yaml").read_text())


def synthetic_op(cell="B0005", source="NASA", cycle=1, i=2.0, minutes=60, step_s=10.0, v_rest=4.2,
                 r=0.1, slope=0.01, t0=25.0, heat=10.0):
    """One discharge op: 2 rest samples, then a constant-current load with a linear voltage fall."""
    rest_t = np.array([0.0, step_s])
    load_t = np.arange(2 * step_s, 2 * step_s + minutes * 60 + 1e-9, step_s)
    frac = (load_t - load_t[0]) / (load_t[-1] - load_t[0])
    rows = pd.DataFrame({
        "t_s": np.r_[rest_t, load_t],
        "voltage_v": np.r_[[v_rest, v_rest], v_rest - i * r - slope * (load_t - load_t[0]) / 60],
        "current_a": np.r_[[0.0, 0.0], np.full(len(load_t), -i)],
        "temp_c": np.r_[[t0, t0], t0 + heat * frac],
    })
    rows["phase"] = np.where(rows.current_a < -0.05, "discharge", "rest")
    rows = rows.assign(source=source, cell_id=cell, cycle=cycle, op_idx=cycle,
                       seg_start=pd.Timestamp("2020-01-01") + pd.to_timedelta(3.0 * cycle, unit="h"), ir_ohm=np.nan, ref_cap_ah=np.nan)
    return rows[["source", "cell_id", "cycle", "op_idx", "phase", "seg_start", "t_s", "voltage_v", "current_a", "temp_c", "ir_ohm", "ref_cap_ah"]]
