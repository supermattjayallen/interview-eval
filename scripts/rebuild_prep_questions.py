#!/usr/bin/env python3
"""Rebuild the prep_questions catalog from the raw question bank."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import init_database, prep_question_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-polish all questions even when display text already exists",
    )
    args = parser.parse_args()

    if not prep_question_store.is_enabled():
        print("DATABASE_URL is not set. Add it to .env and try again.", file=sys.stderr)
        return 1

    init_database()
    stats = prep_question_store.rebuild_all(force_polish=args.force)
    print(
        "Prep catalog rebuild complete: "
        f"{stats['aggregates']} aggregates, "
        f"{stats['polished']} polished, "
        f"{stats['skipped']} skipped, "
        f"{stats['errors']} errors"
    )
    print(f"Prep questions in catalog: {prep_question_store.count_prep_questions()}")
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
