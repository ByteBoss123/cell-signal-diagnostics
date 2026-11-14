"""End-to-end run: staged Parquet telemetry -> DuckDB marts -> context rules -> detector ->
fault-injection test -> metrics, tables and run manifest."""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from . import __version__, common_cause, detect, inject, quality, warehouse

SIGNAL_COLS = ["cap_ah", "v_mean", "dcir_ohm", "temp_rise_c"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def injection_test(con, m: pd.DataFrame, cfg: dict, cells: list[str], k: float) -> pd.DataFrame:
    """Inject every fault size into sampled clean cycles of `cells`, recompute signals with the
    production SQL, score against the original causal baselines."""
    icfg = cfg["injection"]
    targets = inject.sample_targets(m, cells, icfg["cycles_per_cell"], icfg["seed"])
    con.register("inj_targets", targets)
    ops = con.execute("""
        SELECT t.* FROM stg_telemetry t
        JOIN (SELECT d.source, d.cell_id, d.cycle, min(d.op_idx) AS op_idx
              FROM stg_telemetry d JOIN inj_targets USING (source, cell_id, cycle)
              WHERE d.phase = 'discharge' GROUP BY ALL) o
        USING (source, cell_id, cycle, op_idx)""").df()
    inj = inject.build_injected(ops, targets, icfg["faults"], lambda s, c: detect._cutoff(cfg, s, c))
    inj["cell_id"] = inj["cell_id"] + "|" + inj["variant"]
    con.register("inj_telemetry", inj.drop(columns="variant"))
    sig = warehouse.discharge_signals(con, "inj_telemetry")
    sig[["cell_id", "variant"]] = sig["cell_id"].str.split("|", expand=True)
    key = ["source", "cell_id", "cycle"]
    base = sig[key].merge(m[key + [c for c in m.columns if c.startswith(("base_", "scale_"))]], on=key, how="left")
    vals = sig[SIGNAL_COLS + ["source"]]
    z = detect.zscores(vals, base, cfg)
    out = pd.concat([sig[key + ["variant"]], z], axis=1)
    out[["fault", "size"]] = out["variant"].str.split("=", expand=True)
    out["size"] = out["size"].astype(float)
    out["detected"] = out["score"] > k
    return out


def run(staging_dir: Path, out_dir: Path, cfg_path: Path, db_path: Path | None = None) -> dict:
    started = datetime.now(UTC)
    cfg = load_config(cfg_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tables").mkdir(exist_ok=True)
    db_path = db_path or out_dir / "warehouse.duckdb"
    con = warehouse.build(db_path, staging_dir)
    dq = quality.run_checks(con, quality.telemetry_checks())

    m = con.execute("SELECT * FROM mart_cycle_signals ORDER BY source, cell_id, cycle").df()
    m["context"] = detect.context_labels(m, cfg)
    m = detect.score(m, cfg)
    ok = m["context"].eq("ok")
    cal = m["cell_id"].isin(cfg["split"]["calibration"])
    hold = m["cell_id"].isin(cfg["split"]["holdout"])
    scored = m["score"].notna()

    k = detect.pick_threshold(m.loc[cal & ok & scored, "score"].to_numpy(), cfg["detector"]["target_false_reject_rate"])
    m["reject"] = ok & (m["score"] > k)
    cc = cfg["common_cause"]
    m["shared_cause"] = common_cause.label(m, cc["window_h"])
    cc_test = common_cause.permutation_test(m, cc["window_h"], cc["n_perm"], cc["seed"])
    m["naive_z"] = detect.naive_capacity_z(m, cfg)
    m["naive_flag"] = m["naive_z"] > k

    # capacity shortfall vs the causal baseline, for every cycle (artifacts included)
    m["cap_resid_ah"] = m["cap_ah"] - m["base_cap_ah"]

    tel = con.execute("""SELECT count(*) AS rows, count(DISTINCT cell_id) AS cells,
                         count(DISTINCT source) AS sources FROM stg_telemetry""").fetchone()

    # --- signal validation -------------------------------------------------------------
    val = m[m["ref_cap_ah"].notna() & (m["n_loaded"] >= 20)]
    cap_err = (val["cap_ah"] - val["ref_cap_ah"]).abs()
    imp = con.execute("""
        WITH e AS (SELECT cell_id, start_ts, re_ohm, rct_ohm FROM nasa_impedance)
        SELECT c.cell_id, c.cycle, c.dcir_ohm, e.re_ohm, e.rct_ohm
        FROM mart_cycle_signals c ASOF JOIN e ON c.cell_id = e.cell_id AND c.dis_start >= e.start_ts
        WHERE c.source = 'NASA' AND e.re_ohm BETWEEN 0.01 AND 0.5""").df() if _has(con, "nasa_impedance") else pd.DataFrame()
    dcir_re_r = float(np.corrcoef(imp["dcir_ohm"], imp["re_ohm"] + imp["rct_ohm"])[0, 1]) if len(imp) > 10 else None
    calce = m[m["source"] == "CALCE"]
    # in CALCE files the first loaded sample sits exactly 0.200 V below rest in most cycles
    # (consistent with a voltage-change logging trigger), so the step-load DCIR proxy is not usable there
    dcir_sat = int((calce["dcir_ohm"] * calce["i_dis_a"]).round(3).eq(0.2).sum())

    # --- context findings ----------------------------------------------------------------
    inc = m["context"].eq("incomplete_charge")
    dip = m["cap_resid_ah"] < -0.03
    long_rest = m["rest_before_h"] > cfg["context_rules"]["long_rest_h"]
    nf = m["naive_flag"]

    # --- injection test on held-out cells -------------------------------------------------
    inj = injection_test(con, m, cfg, cfg["split"]["holdout"], k)
    inj_rows = []
    for (fault, size, src), g in inj.groupby(["fault", "size", "source"]):
        lo, hi = wilson(int(g["detected"].sum()), len(g))
        inj_rows.append({"fault": fault, "size": size, "source": src, "n": len(g),
                         "detection_rate": round(g["detected"].mean(), 3), "ci95_lo": lo, "ci95_hi": hi,
                         "top_signal": g.loc[g["detected"], "worst_signal"].mode().iloc[0] if g["detected"].any() else None})
    inj_table = pd.DataFrame(inj_rows)
    hold_flags = m.loc[hold & ok & scored, "score"] > k
    cal_flags = m.loc[cal & ok & scored, "score"] > k

    metrics = {
        "telemetry": {"rows": int(tel[0]), "cells": int(tel[1]), "sources": int(tel[2]),
                      "cycles": len(m), "duplicate_workbooks_dropped": _dup_count(con)},
        "signal_validation": {
            "capacity_abs_err_ah_median": {s: round(float(cap_err[val.source == s].median()), 4) for s in ["NASA", "CALCE"]},
            "capacity_abs_err_ah_p99": {s: round(float(cap_err[val.source == s].quantile(.99)), 4) for s in ["NASA", "CALCE"]},
            "capacity_cycles_compared": int(len(val)),
            "nasa_dcir_vs_eis_r": round(dcir_re_r, 3) if dcir_re_r is not None else None,
            "nasa_dcir_eis_pairs": int(len(imp)),
            "calce_dcir_logger_limited_cycles": dcir_sat, "calce_cycles": int(len(calce)),
        },
        "context": {
            "labels": m["context"].value_counts().to_dict(),
            "incomplete_charge_cycles": int(inc.sum()),
            "incomplete_charge_with_dip": int((inc & dip).sum()),
            "complete_charge_dips": int((~inc & m["context"].eq("ok") & dip).sum()),
            "incomplete_charge_median_shortfall_ah": round(float(m.loc[inc, "cap_resid_ah"].median()), 4),
            "long_rest_cycles": int(long_rest.sum()),
            "long_rest_capacity_above_baseline": int((long_rest & (m["cap_resid_ah"] > 0)).sum()),
            "long_rest_median_gain_ah": round(float(m.loc[long_rest, "cap_resid_ah"].median()), 4),
        },
        "naive_vs_gated": {
            "naive_two_sided_capacity_flags": int(nf.sum()),
            "naive_flags_by_context": m.loc[nf, "context"].value_counts().to_dict(),
            "naive_flags_after_long_rest": int((nf & long_rest).sum()),
            "naive_flags_explained": int((nf & (~m["context"].eq("ok") | long_rest)).sum()),
            "gated_rejects": int(m["reject"].sum()),
        },
        "common_cause": cc_test | {"window_h": cc["window_h"],
                                   "single_cell_rejects": int((m["reject"] & ~m["shared_cause"]).sum())},
        "detector": {
            "k": round(k, 3),
            "calibration_flag_rate_unmodified": round(float(cal_flags.mean()), 4), "calibration_n": int(len(cal_flags)),
            "holdout_flag_rate_unmodified": round(float(hold_flags.mean()), 4), "holdout_n": int(len(hold_flags)),
            "holdout_flag_rate_ci95": wilson(int(hold_flags.sum()), len(hold_flags)),
            "injected_cycles": int(inj[["source", "cell_id", "cycle"]].drop_duplicates().shape[0]),
            "injected_variants": int(len(inj)),
        },
        "injection": inj_table.to_dict(orient="records"),
    }

    m.to_csv(out_dir / "tables" / "cycle_signals_scored.csv", index=False)
    inj_table.to_csv(out_dir / "tables" / "injection_detection.csv", index=False)
    inj.to_csv(out_dir / "tables" / "injection_cycles.csv", index=False)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=1, default=_json))

    manifest = {
        "cellsig_version": __version__, "python": platform.python_version(),
        "started_utc": started.isoformat(), "finished_utc": datetime.now(UTC).isoformat(),
        "params_sha256": _sha(cfg_path),
        "staged_sha256": {p.name: _sha(p) for p in sorted(Path(staging_dir).glob("*.parquet"))},
        "data_quality": quality.as_records(dq),
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=1))
    con.close()
    return metrics


def _has(con, table: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]).fetchone()[0])


def _dup_count(con) -> int | None:
    return int(con.execute("SELECT count(*) FROM ingest_dropped").fetchone()[0]) if _has(con, "ingest_dropped") else None


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    return str(o)
