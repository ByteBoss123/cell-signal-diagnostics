"""Parse CALCE CS2 cells (CS2_35..CS2_38; 1.1 Ah LiCoO2 prismatic) from Arbin .xlsx exports.

Each cell is split across ~25 weekly workbooks whose Cycle_Index restarts at 1. Workbooks are
ordered by their first timestamp and cycles are renumbered globally. Exact duplicate workbooks
(same sample rows exported twice under different names) are detected by content hash and dropped;
the drop is reported, not silent.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

RATED_AH = 1.1
CUTOFF_V = 2.7

COLS = {"Test_Time(s)": "test_time_s", "Date_Time": "ts", "Step_Index": "step",
        "Cycle_Index": "file_cycle", "Current(A)": "current_a", "Voltage(V)": "voltage_v",
        "Discharge_Capacity(Ah)": "arbin_dis_ah", "Internal_Resistance(Ohm)": "ir_ohm"}


def _read_book(path: Path) -> pd.DataFrame:
    book = pd.ExcelFile(path)
    sheets = [s for s in book.sheet_names if s.startswith("Channel")]
    df = pd.concat([book.parse(s) for s in sheets], ignore_index=True)[list(COLS)].rename(columns=COLS)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _content_hash(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


def load_cell(cell_dir: Path) -> tuple[pd.DataFrame, list[dict]]:
    books, dropped, seen = [], [], {}
    for p in sorted(cell_dir.glob("*.xlsx")):
        df = _read_book(p)
        h = _content_hash(df)
        if h in seen:
            dropped.append({"cell_id": cell_dir.name, "file": p.name, "duplicate_of": seen[h], "rows": len(df)})
            continue
        seen[h] = p.name
        df["file"] = p.name
        books.append(df)
    books.sort(key=lambda d: d["ts"].iloc[0])
    out, offset = [], 0
    for df in books:
        df = df.copy()
        df["cycle"] = df["file_cycle"] + offset
        offset = int(df["cycle"].max())
        out.append(df)
    cell = pd.concat(out, ignore_index=True)
    cell["cell_id"] = cell_dir.name
    return cell, dropped


def load(raw_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    cells, dropped = [], []
    for d in sorted(Path(raw_dir).glob("CS2_*")):
        if d.is_dir():
            c, drop = load_cell(d)
            cells.append(c)
            dropped += drop
    if not cells:
        raise FileNotFoundError(f"no CALCE CS2_* folders under {raw_dir}")
    s = pd.concat(cells, ignore_index=True)
    s["source"] = "CALCE"
    return s, pd.DataFrame(dropped, columns=["cell_id", "file", "duplicate_of", "rows"])
