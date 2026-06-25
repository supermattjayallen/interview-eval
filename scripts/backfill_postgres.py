#!/usr/bin/env python3
"""Backfill PostgreSQL from saved analysis JSON files in data/results/."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.db import init_database
from app.db.question_bank import question_bank_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        default=settings.results_dir,
        help="Directory containing interview-analysis-*.json files",
    )
    args = parser.parse_args()

    if not question_bank_store.is_enabled():
        print("DATABASE_URL is not set. Add it to .env and try again.", file=sys.stderr)
        return 1

    init_database()
    stats = question_bank_store.backfill_from_directory(args.results_dir)
    print(
        f"Backfill complete: {stats['imported']} imported, "
        f"{stats['skipped']} skipped, {stats['errors']} errors "
        f"({stats['files']} files scanned)"
    )
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
