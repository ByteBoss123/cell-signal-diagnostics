"""End-to-end regression on the real data. Runs when staged Parquet exists (CI e2e job fetches
and ingests it); set CI_REQUIRE_DATA=1 to fail instead of skip when it is missing."""
import json
import os
from pathlib import Path

import pytest

from cellsig import pipeline

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "data" / "staging"


@pytest.fixture(scope="module")
def metrics(tmp_path_factory):
    if not (STAGING / "telemetry_nasa.parquet").exists():
        if os.environ.get("CI_REQUIRE_DATA") == "1":
            pytest.fail("staged data missing: run scripts/fetch_data.sh && cellsig ingest")
        pytest.skip("staged data not present")
    out = tmp_path_factory.mktemp("out")
    return pipeline.run(STAGING, out, ROOT / "config" / "params.yaml")


def test_matches_golden(metrics):
    golden = json.loads((ROOT / "tests" / "golden_v1.json").read_text())
    got = json.loads(json.dumps({k: metrics[k] for k in golden}, default=pipeline._json))
    assert got == golden


def test_holdout_flag_rate_close_to_target(metrics):
    lo, hi = metrics["detector"]["holdout_flag_rate_ci95"]
    assert lo <= 0.01 <= hi


def test_every_incomplete_charge_explains_its_dip(metrics):
    c = metrics["context"]
    assert c["incomplete_charge_with_dip"] == c["incomplete_charge_cycles"] > 100
