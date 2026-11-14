"""Inject physically motivated faults into raw discharge samples, then recompute signals
with the same SQL used for production data. Ground truth is known by construction.

Faults (applied to the loaded part of one discharge):
  capacity_fade_pct     drop the tail of the discharge so delivered Ah falls by X%
  resistance_rise_mohm  V -= |I| * dR on loaded samples, then end the discharge at the cutoff
  voltage_offset_mv     sensor offset: every sample of the op reads X mV low, ends at the cutoff
  thermal_rise_c        extra self-heating that ramps from 0 to X degC over the discharge
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _loaded(op: pd.DataFrame) -> pd.Series:
    return op["phase"].eq("discharge")


def _cut_at_cutoff(op: pd.DataFrame, cutoff: float) -> pd.DataFrame:
    load = _loaded(op)
    below = load & (op["voltage_v"] < cutoff)
    if not below.any():
        return op
    first = op.index[below.to_numpy().argmax()]
    t_end = op.loc[first, "t_s"]
    # drop loaded samples after the cutoff crossing (keep the crossing sample)
    return op[~(load & (op["t_s"] > t_end))]


def apply(op: pd.DataFrame, fault: str, size: float, cutoff: float) -> pd.DataFrame:
    op = op.sort_values("t_s").copy()
    load = _loaded(op)
    if fault == "capacity_fade_pct":
        lt = op.loc[load]
        dt = lt["t_s"].diff().fillna(0)
        ah = (dt * lt["current_a"].abs()).cumsum() / 3600
        keep_until = ah.iloc[-1] * (1 - size / 100)
        drop = lt.index[ah > keep_until]
        return op.drop(index=drop[1:])  # keep one sample past the target so the integral is not short
    if fault == "resistance_rise_mohm":
        op.loc[load, "voltage_v"] -= op.loc[load, "current_a"].abs() * size / 1000
        return _cut_at_cutoff(op, cutoff)
    if fault == "voltage_offset_mv":
        op["voltage_v"] -= size / 1000
        return _cut_at_cutoff(op, cutoff)
    if fault == "thermal_rise_c":
        t = op.loc[load, "t_s"]
        frac = (t - t.min()) / max(t.max() - t.min(), 1e-9)
        op.loc[load, "temp_c"] += size * frac
        return op
    raise ValueError(fault)


def build_injected(telemetry_ops: pd.DataFrame, targets: pd.DataFrame, faults: dict, cutoff_of) -> pd.DataFrame:
    """telemetry_ops: raw rows of the target discharge ops. targets: (source, cell_id, cycle).
    Returns injected rows with an extra `variant` column, cycle renumbered per variant via `variant`."""
    rows = []
    keyed = telemetry_ops.groupby(["source", "cell_id", "cycle"])
    for (src, cell, cyc) in targets[["source", "cell_id", "cycle"]].itertuples(index=False):
        op = keyed.get_group((src, cell, cyc))
        for fault, sizes in faults.items():
            if fault == "thermal_rise_c" and src != "NASA":
                continue
            for size in sizes:
                inj = apply(op, fault, float(size), cutoff_of(src, cell))
                inj = inj.assign(variant=f"{fault}={size}")
                rows.append(inj)
    out = pd.concat(rows, ignore_index=True)
    return out


def sample_targets(m: pd.DataFrame, cells: list[str], n: int, seed: int) -> pd.DataFrame:
    """Clean, scored cycles from the given cells (context ok, past warm-up)."""
    pool = m[m["cell_id"].isin(cells) & m["context"].eq("ok") & m["score"].notna()]
    rng = np.random.default_rng(seed)
    picks = []
    for _, g in pool.groupby("cell_id"):
        idx = rng.choice(g.index.to_numpy(), size=min(n, len(g)), replace=False)
        picks.append(g.loc[np.sort(idx)])
    return pd.concat(picks)[["source", "cell_id", "cycle"]]
