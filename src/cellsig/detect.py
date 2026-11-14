"""Context-aware outlier rejection for per-cycle cell signals.

1. Context rules label cycles whose signal shift has a test-side explanation
   (incomplete charge, truncated discharge, too few samples). These are excluded from the
   baseline and are never reported as cell rejects.
2. For the rest, each signal is compared with a causal baseline (rolling median of the
   previous accepted cycles) and scaled by a rolling MAD of accepted residuals.
3. Only the adverse direction counts (capacity or mean voltage down, resistance or heating up).
   A cycle is a cell reject when its worst adverse z exceeds k.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MAD_TO_SIGMA = 1.4826


def context_labels(m: pd.DataFrame, cfg: dict) -> pd.Series:
    rules = cfg["context_rules"]
    rated = m["source"].map({k: v["rated_ah"] for k, v in cfg["cells"].items()})
    cutoff = m.apply(lambda r: _cutoff(cfg, r.source, r.cell_id), axis=1)
    label = pd.Series("ok", index=m.index)
    label[m["n_loaded"] < rules["min_loaded_samples"]] = "too_few_samples"
    label[(label == "ok") & (m["v_min"] > cutoff + rules["truncated_discharge_v"])] = "truncated_discharge"
    label[(label == "ok") & (m["chg_i_end_a"] > rules["incomplete_charge_c_rate"] * rated)] = "incomplete_charge"
    return label


def recovery_window(m: pd.DataFrame, cfg: dict) -> pd.Series:
    """True for the cycles right after a long rest. Capacity temporarily recovers after a rest
    and decays back over a few cycles; those cycles are still scored but kept out of the
    baseline history so they neither drag the expected value up nor inflate the noise scale."""
    rules = cfg["context_rules"]
    out = pd.Series(False, index=m.index)
    for _, g in m.groupby("cell_id"):
        g = g.sort_values("cycle")
        start = (g["rest_before_h"] > rules["long_rest_h"]).to_numpy()
        flag = np.zeros(len(g), dtype=bool)
        for i in np.flatnonzero(start):
            flag[i:i + rules["rest_recovery_cycles"]] = True
        out.loc[g.index] = flag
    return out


def _cutoff(cfg: dict, source: str, cell_id: str) -> float:
    c = cfg["cells"][source]["discharge_cutoff_v"]
    return c[cell_id] if isinstance(c, dict) else c


def _local_linear(xs: np.ndarray, ys: np.ndarray, x_new: float) -> float:
    """Theil-Sen line through the recent accepted points, evaluated one step ahead.
    A plain rolling median lags a fading signal by about half the window."""
    i, j = np.triu_indices(len(xs), 1)
    dx = xs[j] - xs[i]
    ok = dx != 0
    slope = np.median((ys[j] - ys[i])[ok] / dx[ok]) if ok.any() else 0.0
    return float(np.median(ys - slope * xs) + slope * x_new)


def baselines_cell(g: pd.DataFrame, signals: dict, cfg: dict) -> pd.DataFrame:
    """Causal expected value and noise scale for every cycle and signal of one cell.
    Only accepted (context == ok) cycles feed the history."""
    det = cfg["detector"]
    g = g.sort_values("cycle")
    accepted = (g["context"].eq("ok") & ~g["in_recovery"]).to_numpy()
    cyc = g["cycle"].to_numpy(dtype=float)
    out = pd.DataFrame(index=g.index)
    for sig in signals:
        x = g[sig].to_numpy(dtype=float)
        base = np.full(len(x), np.nan)
        scale = np.full(len(x), np.nan)
        hx, hy, hres = [], [], []
        for i in range(len(x)):
            if len(hy) >= det["warmup_cycles"]:
                w = det["baseline_window"]
                base[i] = _local_linear(np.asarray(hx[-w:]), np.asarray(hy[-w:]), cyc[i])
                r = np.asarray(hres[-det["scale_window"]:])
                mad = MAD_TO_SIGMA * np.median(np.abs(r - np.median(r))) if len(r) >= 5 else 0.0
                scale[i] = max(mad, det["scale_floor"][sig])
            if accepted[i] and np.isfinite(x[i]):
                if np.isfinite(base[i]):
                    hres.append(x[i] - base[i])
                hx.append(cyc[i])
                hy.append(x[i])
        out[f"base_{sig}"] = base
        out[f"scale_{sig}"] = scale
    return out


def baselines(m: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    parts = [baselines_cell(g, cfg["detector"]["signals"][src], cfg)
             for (src, _cell), g in m.groupby(["source", "cell_id"], sort=False)]
    return pd.concat(parts)


def zscores(values: pd.DataFrame, base: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Adverse-direction z for each signal, plus the worst one. `values` and `base` share an index
    and must carry a `source` column; injected values reuse the original baselines."""
    sigs = sorted({s for d in cfg["detector"]["signals"].values() for s in d})
    out = pd.DataFrame(index=values.index)
    for sig in sigs:
        sign = values["source"].map(lambda src, sig=sig: cfg["detector"]["signals"][src].get(sig, np.nan))
        if f"base_{sig}" in base:
            out[f"z_{sig}"] = sign * (values[sig] - base[f"base_{sig}"]) / base[f"scale_{sig}"]
        else:
            out[f"z_{sig}"] = np.nan
    z = out[[f"z_{s}" for s in sigs]]
    out["score"] = z.max(axis=1, skipna=True)
    worst = z.fillna(-np.inf).idxmax(axis=1).str.removeprefix("z_")
    out["worst_signal"] = worst.where(out["score"].notna())
    return out


def score(m: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    m = m.assign(in_recovery=recovery_window(m, cfg))
    base = baselines(m, cfg)
    return m.join(base).join(zscores(m, base.reindex(m.index), cfg))


def naive_capacity_z(m: pd.DataFrame, cfg: dict) -> pd.Series:
    """Baseline rule a plain SPC chart would apply: two-sided capacity z against a rolling
    median of ALL previous cycles, no context rules."""
    det = cfg["detector"]
    out = pd.Series(np.nan, index=m.index)
    for _, g in m.groupby("cell_id"):
        g = g.sort_values("cycle")
        x = g["cap_ah"]
        base = x.shift(1).rolling(det["baseline_window"], min_periods=det["warmup_cycles"]).median()
        res = x - base
        mad = res.shift(1).rolling(det["scale_window"], min_periods=5).apply(lambda r: MAD_TO_SIGMA * np.median(np.abs(r - np.median(r))), raw=True)
        out.loc[g.index] = (res / mad.clip(lower=det["scale_floor"]["cap_ah"])).abs()
    return out


def pick_threshold(clean_scores: np.ndarray, target_frr: float) -> float:
    s = clean_scores[np.isfinite(clean_scores)]
    return float(np.quantile(s, 1 - target_frr))
