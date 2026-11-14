"""README figures, rendered only from outputs/ (no recomputation)."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

INK, MUTED, GRID = "#1f2937", "#6b7280", "#e5e7eb"
BLUE, ORANGE, RED, GREEN = "#2563eb", "#ea580c", "#dc2626", "#16a34a"


def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=8)


def render(out: Path) -> list[Path]:
    fig_dir = out / "figures"
    fig_dir.mkdir(exist_ok=True)
    m = pd.read_csv(out / "tables" / "cycle_signals_scored.csv")
    inj = pd.read_csv(out / "tables" / "injection_detection.csv")
    metrics = json.loads((out / "metrics.json").read_text())
    paths = []

    # 1. one CALCE cell: incomplete charges explain the capacity dips
    g = m[m.cell_id == "CS2_35"]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    _style(ax)
    ok = g.context == "ok"
    ax.plot(g.cycle[ok], g.cap_ah[ok], ".", ms=2.5, color=BLUE, label="normal cycle")
    inc = g.context == "incomplete_charge"
    ax.plot(g.cycle[inc], g.cap_ah[inc], "o", ms=4, mfc="none", color=ORANGE, label="charge ended before CV taper")
    rej = g["reject"]
    ax.plot(g.cycle[rej], g.cap_ah[rej], "x", ms=5, color=RED, label="cell reject (gated detector)")
    ax.set_xlabel("cycle", color=MUTED, fontsize=9)
    ax.set_ylabel("discharge capacity (Ah)", color=MUTED, fontsize=9)
    ax.set_title("CS2_35: capacity dips line up with incomplete charges, not cell damage", color=INK, fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8)
    paths.append(_save(fig, fig_dir / "capacity_dips.png"))

    # 2. naive alarms broken down by explanation
    nv = metrics["naive_vs_gated"]["naive_flags_by_context"]
    fig, ax = plt.subplots(figsize=(6, 2.6))
    _style(ax)
    order = ["ok", "incomplete_charge", "truncated_discharge", "too_few_samples"]
    labels = ["no test-side cause", "incomplete charge", "truncated discharge", "too few samples"]
    vals = [nv.get(k, 0) for k in order]
    ax.barh(labels[::-1], vals[::-1], color=[BLUE, ORANGE, MUTED, MUTED][::-1])
    for y, v in enumerate(vals[::-1]):
        ax.text(v + 2, y, str(v), va="center", fontsize=8, color=INK)
    ax.set_title(f"Naive two-sided capacity alarms: {sum(vals)} total", color=INK, fontsize=10, loc="left")
    paths.append(_save(fig, fig_dir / "naive_alarms.png"))

    # 3. detection vs fault size on held-out cells
    faults = [("resistance_rise_mohm", "resistance rise (mΩ)"), ("thermal_rise_c", "extra heating (°C)"),
              ("capacity_fade_pct", "capacity loss (%)"), ("voltage_offset_mv", "voltage sensor offset (mV)")]
    fig, axes = plt.subplots(1, 4, figsize=(11, 2.8), sharey=True)
    for ax, (f, lab) in zip(axes, faults, strict=True):
        _style(ax)
        for src, col in (("NASA", BLUE), ("CALCE", ORANGE)):
            d = inj[(inj.fault == f) & (inj.source == src)].sort_values("size")
            if len(d):
                ax.errorbar(d["size"], d.detection_rate, yerr=[d.detection_rate - d.ci95_lo, d.ci95_hi - d.detection_rate],
                            marker="o", ms=4, lw=1.2, capsize=2, color=col, label=src)
        ax.set_xlabel(lab, color=MUTED, fontsize=8)
        ax.set_ylim(-0.03, 1.03)
    axes[0].set_ylabel("detected (held-out cells)", color=MUTED, fontsize=9)
    axes[0].legend(frameon=False, fontsize=8)
    k = metrics["detector"]
    fig.suptitle(f"Injected faults caught at a {k['holdout_flag_rate_unmodified']:.1%} flag rate on unmodified cycles",
                 color=INK, fontsize=10, x=0.01, ha="left")
    paths.append(_save(fig, fig_dir / "injection_detection.png"))
    # 4. reject timeline: shared vs single-cell
    m["dis_start"] = pd.to_datetime(m["dis_start"])
    fig, axes = plt.subplots(1, 2, figsize=(11, 2.8), gridspec_kw={"width_ratios": [1, 2.4]})
    for ax, src in zip(axes, ["NASA", "CALCE"], strict=True):
        _style(ax)
        d = m[m.source == src]
        cells = sorted(d.cell_id.unique())
        for y, c in enumerate(cells):
            g = d[d.cell_id == c]
            ax.plot([g.dis_start.min(), g.dis_start.max()], [y, y], color=GRID, lw=6, solid_capstyle="butt")
            r = g[g["reject"]]
            ax.plot(r.dis_start[r.shared_cause], [y] * int(r.shared_cause.sum()), "o", ms=5, color=ORANGE)
            ax.plot(r.dis_start[~r.shared_cause], [y] * int((~r.shared_cause).sum()), "x", ms=6, color=RED)
        ax.set_yticks(range(len(cells)), cells)
        ax.tick_params(axis="x", labelrotation=30)
        ax.set_title(src, color=MUTED, fontsize=9, loc="left")
    cc = metrics["common_cause"]
    fig.suptitle(f"{cc['shared_rejects']} of {cc['rejects']} rejects land within {cc['window_h']} h of a reject on another cell "
                 f"of the same rig (chance: {cc['null_mean']}, p = {cc['p_value']})", color=INK, fontsize=10, x=0.01, ha="left")
    paths.append(_save(fig, fig_dir / "reject_timeline.png"))
    return paths


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
