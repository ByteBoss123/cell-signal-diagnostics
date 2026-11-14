"""Unify NASA and CALCE samples into one long telemetry table.

Schema (one row per sample):
  source, cell_id, cycle, phase ('charge' | 'discharge' | 'rest'), seg_start (timestamp),
  t_s (seconds since segment start), voltage_v, current_a (discharge negative), temp_c, ir_ohm

`cycle` counts discharges: the charge that precedes discharge k belongs to cycle k.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LOAD_A = 0.05  # |current| below this is treated as rest


def _phase(current: pd.Series) -> np.ndarray:
    return np.where(current < -LOAD_A, "discharge", np.where(current > LOAD_A, "charge", "rest"))


def from_nasa(samples: pd.DataFrame, ops: pd.DataFrame) -> pd.DataFrame:
    ops = ops.sort_values(["cell_id", "op_idx"]).copy()
    ops["cycle"] = ops.groupby("cell_id")["op_type"].transform(lambda t: (t == "discharge").cumsum())
    # a charge belongs to the next discharge
    ops.loc[ops.op_type == "charge", "cycle"] += 1
    df = samples.merge(ops[["cell_id", "op_idx", "cycle", "start_ts"]], on=["cell_id", "op_idx"], how="left")
    # Phase comes from the operation label, gated by load current: a charge op can briefly read
    # negative current at hand-off, and those samples must not be counted as discharge.
    ph = _phase(df["current_a"])
    df["phase"] = np.where((df.op_type == "discharge") & (ph == "discharge"), "discharge",
                           np.where((df.op_type == "charge") & (ph == "charge"), "charge", "rest"))
    df["seg_start"] = df["start_ts"]
    df["ir_ohm"] = np.nan
    # NASA reports one capacity per discharge op; carry it on the rows for cross-checking
    df = df.merge(ops[["cell_id", "op_idx", "reported_capacity_ah"]], on=["cell_id", "op_idx"], how="left").rename(columns={"reported_capacity_ah": "ref_cap_ah"})
    df = df[df["cycle"] >= 1]
    return df[["source", "cell_id", "cycle", "op_idx", "phase", "seg_start", "t_s", "voltage_v", "current_a", "temp_c", "ir_ohm", "ref_cap_ah"]]


def from_calce(samples: pd.DataFrame) -> pd.DataFrame:
    df = samples.sort_values(["cell_id", "ts"]).copy()
    df["phase"] = _phase(df["current_a"])
    # Arbin cycle k = charge then discharge, matching the NASA convention.
    df["op_idx"] = df["cycle"]
    g = df.groupby(["cell_id", "cycle"])
    df["seg_start"] = g["ts"].transform("min")
    # Date_Time has 1 s resolution; Test_Time(s) is sub-second and monotonic within a workbook
    df["t_s"] = df["test_time_s"] - g["test_time_s"].transform("min")
    df["temp_c"] = np.nan
    df["ir_ohm"] = df["ir_ohm"].where(df["ir_ohm"] > 0)
    # Arbin Discharge_Capacity accumulates within a workbook; per-cycle value = max - min over the cycle
    g = df.groupby(["cell_id", "cycle"])["arbin_dis_ah"]
    df["ref_cap_ah"] = g.transform("max") - g.transform("min")
    return df[["source", "cell_id", "cycle", "op_idx", "phase", "seg_start", "t_s", "voltage_v", "current_a", "temp_c", "ir_ohm", "ref_cap_ah"]]


def unify(nasa_samples: pd.DataFrame, nasa_ops: pd.DataFrame, calce_samples: pd.DataFrame) -> pd.DataFrame:
    out = pd.concat([from_nasa(nasa_samples, nasa_ops), from_calce(calce_samples)], ignore_index=True)
    out["cycle"] = out["cycle"].astype("int32")
    return out
