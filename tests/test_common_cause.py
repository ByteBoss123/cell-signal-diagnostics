import pandas as pd

from cellsig import common_cause


def frame(rows):
    df = pd.DataFrame(rows, columns=["source", "cell_id", "dis_start", "reject"])
    df["dis_start"] = pd.to_datetime(df["dis_start"])
    return df


def test_reject_shared_within_window_is_common_cause():
    m = frame([
        ("CALCE", "A", "2010-01-01 08:00", True),
        ("CALCE", "B", "2010-01-01 20:00", True),     # 12 h later on another cell: shared
        ("CALCE", "C", "2010-01-05 08:00", True),     # alone
        ("NASA", "D", "2010-01-01 09:00", True),      # same time but another rig: not shared
    ])
    assert common_cause.label(m, 24).tolist() == [True, True, False, False]


def test_permutation_test_finds_clustered_rejects():
    days = pd.date_range("2010-01-01", periods=200, freq="D")
    rows = [("CALCE", c, str(d), False) for c in "ABC" for d in days]
    hits = days[[20, 80, 140]].shift(1, freq="h")  # every cell rejects on the same three days
    rows += [("CALCE", c, str(d), True) for d in hits for c in "ABC"]
    res = common_cause.permutation_test(frame(rows), 24, 300, 0)
    assert res["shared_rejects"] == 9 and res["p_value"] < 0.01
