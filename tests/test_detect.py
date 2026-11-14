import numpy as np
import pandas as pd
import pytest

from cellsig import detect


def cell_frame(n=80, fade=0.003, noise=0.0, seed=0, source="NASA", cell="B0005"):
    rng = np.random.default_rng(seed)
    cyc = np.arange(1, n + 1)
    return pd.DataFrame({
        "source": source, "cell_id": cell, "cycle": cyc,
        "cap_ah": 2.0 - fade * cyc + rng.normal(0, noise, n),
        "v_mean": 3.5 - 0.001 * cyc + rng.normal(0, noise / 4, n),
        "dcir_ohm": 0.10 + 0.0002 * cyc, "temp_rise_c": 15.0,
        "n_loaded": 150, "v_min": 2.7, "chg_i_end_a": 0.05, "rest_before_h": 3.0,
    })


def test_local_linear_tracks_a_fading_signal_without_lag(cfg):
    m = cell_frame(noise=0.0)
    m["context"] = "ok"
    out = detect.score(m, cfg)
    resid = (out["cap_ah"] - out["base_cap_ah"]).dropna()
    assert resid.abs().max() < 1e-9                       # a rolling median would lag by ~10 cycles of fade


def test_baseline_is_causal(cfg):
    m = cell_frame(noise=0.01)
    m["context"] = "ok"
    a = detect.score(m, cfg)
    m2 = m.copy()
    m2.loc[m2.cycle == 70, "cap_ah"] -= 0.5               # change the future
    b = detect.score(m2, cfg)
    early = a.cycle < 70
    pd.testing.assert_series_equal(a.loc[early, "base_cap_ah"], b.loc[early, "base_cap_ah"])


def test_only_adverse_direction_scores(cfg):
    m = cell_frame(noise=0.005)
    m["context"] = "ok"
    up, down = m.copy(), m.copy()
    up.loc[up.cycle == 60, "cap_ah"] += 0.2
    down.loc[down.cycle == 60, "cap_ah"] -= 0.2
    zu = detect.score(up, cfg).set_index("cycle").loc[60, "z_cap_ah"]
    zd = detect.score(down, cfg).set_index("cycle").loc[60, "z_cap_ah"]
    assert zu < 0 < zd


def test_artifacts_do_not_enter_the_baseline(cfg):
    m = cell_frame(noise=0.005)
    m["context"] = "ok"
    m.loc[m.cycle.between(40, 49), ["cap_ah", "context"]] = [1.0, "incomplete_charge"]
    out = detect.score(m, cfg).set_index("cycle")
    assert out.loc[50, "base_cap_ah"] == pytest.approx(2.0 - 0.003 * 50, abs=0.01)


def test_context_rules(cfg):
    m = cell_frame(n=4)
    m.loc[0, "chg_i_end_a"] = 0.55                         # still at CC current when charge stopped
    m.loc[1, "v_min"] = 3.9                                # stopped well above 2.7 V cutoff
    m.loc[2, "n_loaded"] = 5
    labels = detect.context_labels(m, cfg).tolist()
    assert labels == ["incomplete_charge", "truncated_discharge", "too_few_samples", "ok"]


def test_threshold_hits_target_rate():
    s = np.arange(1000.0)
    k = detect.pick_threshold(s, 0.01)
    assert (s > k).mean() == pytest.approx(0.01, abs=0.001)
