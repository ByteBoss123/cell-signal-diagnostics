"""Fleet view of rejects: cells on the same test rig share chamber, chargers and schedule.
A reject that lands within a window of a reject on another cell of the same rig points to a
shared (test-side) cause; a lone reject points to the cell itself.

Significance: rotate each cell's reject times by a random offset inside that cell's own test
span (keeps each cell's reject count and spacing, breaks cross-cell timing) and recount."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _sec(ts: pd.Series) -> np.ndarray:
    return pd.to_datetime(ts).astype("datetime64[ns]").astype("int64").to_numpy(dtype=float) / 1e9


def _shared(times: dict[str, np.ndarray], window_s: float) -> dict[str, np.ndarray]:
    out = {}
    for cell, t in times.items():
        others = np.concatenate([v for c, v in times.items() if c != cell] or [np.array([])])
        out[cell] = np.array([bool(len(others)) and np.abs(others - x).min() <= window_s for x in t], dtype=bool)
    return out


def label(m: pd.DataFrame, window_h: float) -> pd.Series:
    out = pd.Series(False, index=m.index)
    rej = m[m["reject"]]
    for _, g in rej.groupby("source"):
        times = {c: _sec(d["dis_start"]) for c, d in g.groupby("cell_id")}
        flags = _shared(times, window_h * 3600)
        for c, d in g.groupby("cell_id"):
            out.loc[d.index] = flags[c]
    return out


def permutation_test(m: pd.DataFrame, window_h: float, n_perm: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    observed = int(label(m, window_h).sum())
    span = m.groupby("cell_id")["dis_start"].agg(["min", "max"])
    rej = m[m["reject"]]
    null = []
    for _ in range(n_perm):
        total = 0
        for _, g in rej.groupby("source"):
            times = {}
            for c, d in g.groupby("cell_id"):
                lo, hi = _sec(pd.Series([span.loc[c, "min"], span.loc[c, "max"]]))
                t = _sec(d["dis_start"])
                times[c] = lo + (t - lo + rng.uniform(0, hi - lo)) % (hi - lo)
            total += sum(int(v.sum()) for v in _shared(times, window_h * 3600).values())
        null.append(total)
    null = np.asarray(null)
    return {"shared_rejects": observed, "rejects": int(m["reject"].sum()),
            "null_mean": round(float(null.mean()), 1), "null_p95": float(np.quantile(null, 0.95)),
            "p_value": round(float((1 + (null >= observed).sum()) / (1 + n_perm)), 4)}
