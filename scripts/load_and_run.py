"""CLI: load an outstanding Excel file and evaluate every rule."""
import argparse, json, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import open_pool, close_pool          # noqa: E402
from app.ingest import load_snapshot              # noqa: E402
from app.rules import run_rules                   # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("excel")
    ap.add_argument("--type", default="daily", choices=["daily", "monthly"])
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: file print date)")
    ap.add_argument("--skip-rules", action="store_true")
    a = ap.parse_args()

    open_pool()
    try:
        loaded = load_snapshot(a.excel, date.fromisoformat(a.date) if a.date else None, a.type)
        print(json.dumps(loaded, indent=2))
        if not a.skip_rules:
            print(json.dumps(run_rules(loaded["snapshot_id"]), indent=2, default=str))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
