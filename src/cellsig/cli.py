"""cellsig ingest | run"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cellsig")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest", help="parse raw NASA .mat and CALCE .xlsx into staged Parquet")
    i.add_argument("--raw", type=Path, default=Path("data/raw"))
    i.add_argument("--staging", type=Path, default=Path("data/staging"))
    r = sub.add_parser("run", help="build marts, score cycles, run the fault-injection test")
    r.add_argument("--staging", type=Path, default=Path("data/staging"))
    r.add_argument("--out", type=Path, default=Path("outputs"))
    r.add_argument("--config", type=Path, default=Path("config/params.yaml"))
    f = sub.add_parser("figures", help="render README figures from outputs/")
    f.add_argument("--out", type=Path, default=Path("outputs"))
    a = ap.parse_args(argv)

    if a.cmd == "ingest":
        from . import telemetry, warehouse
        from .ingest import calce, nasa
        ns, no, ni = nasa.load(a.raw / "nasa")
        cs, dropped = calce.load(a.raw / "calce")
        tel = telemetry.unify(ns, no, cs)
        paths = warehouse.write_staging(tel, ni, dropped, a.staging)
        print(json.dumps({"rows": len(tel), "files": [p.name for p in paths], "duplicate_workbooks_dropped": len(dropped)}))
    elif a.cmd == "run":
        from . import pipeline
        m = pipeline.run(a.staging, a.out, a.config)
        print(json.dumps({k: m[k] for k in ("telemetry", "detector")}, indent=1))
    elif a.cmd == "figures":
        from . import figures
        figures.render(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
