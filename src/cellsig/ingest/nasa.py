"""Parse NASA PCoE Li-ion aging files (B0005, B0006, B0007, B0018) into long telemetry rows.

Each .mat holds an ordered list of operations (charge, discharge, impedance). We keep the
raw sample rows for charge and discharge, and one row per impedance sweep (Re, Rct).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.io as sio

RATED_AH = 2.0
# Discharge cutoff voltages stated in the dataset README.
CUTOFF_V = {"B0005": 2.7, "B0006": 2.5, "B0007": 2.2, "B0018": 2.5}


def _matlab_datevec(v) -> datetime:
    y, mo, d, h, mi, s = (float(x) for x in v)
    return datetime(int(y), int(mo), int(d), int(h), int(mi)) + timedelta(seconds=s)


def parse_file(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (samples, operations, impedance) for one NASA cell file."""
    cell = path.stem
    ops = sio.loadmat(path, simplify_cells=True)[cell]["cycle"]
    samples, op_rows, imp_rows = [], [], []
    for op_idx, op in enumerate(ops):
        kind = op["type"]
        start = _matlab_datevec(op["time"])
        d = op["data"]
        if kind == "impedance":
            imp_rows.append({"cell_id": cell, "op_idx": op_idx, "start_ts": start,
                             "re_ohm": float(np.real(d["Re"])), "rct_ohm": float(np.real(d["Rct"]))})
            continue
        t = np.asarray(d["Time"], dtype=float)
        frame = pd.DataFrame({
            "cell_id": cell, "op_idx": op_idx, "op_type": kind,
            "t_s": t,
            "voltage_v": np.asarray(d["Voltage_measured"], dtype=float),
            "current_a": np.asarray(d["Current_measured"], dtype=float),
            "temp_c": np.asarray(d["Temperature_measured"], dtype=float),
        })
        samples.append(frame)
        op_rows.append({"cell_id": cell, "op_idx": op_idx, "op_type": kind, "start_ts": start,
                        "ambient_c": float(op["ambient_temperature"]),
                        "reported_capacity_ah": float(d["Capacity"]) if kind == "discharge" and np.size(d.get("Capacity", [])) else np.nan,
                        "n_samples": len(t)})
    return pd.concat(samples, ignore_index=True), pd.DataFrame(op_rows), pd.DataFrame(imp_rows)


def load(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    parts = [parse_file(p) for p in sorted(Path(raw_dir).glob("B00*.mat"))]
    if not parts:
        raise FileNotFoundError(f"no NASA .mat files under {raw_dir}")
    s, o, i = (pd.concat(x, ignore_index=True) for x in zip(*parts, strict=True))
    s["source"] = o["source"] = i["source"] = "NASA"
    return s, o, i
