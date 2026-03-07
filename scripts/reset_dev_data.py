#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings
from app.db.session import get_session_factory
from app.devtools.service import reset_dev_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset local TrustLayer dev/demo data.")
    parser.add_argument("--updated-by", default="dev-reset", help="updated_by value for kill switch reset.")
    args = parser.parse_args()

    settings = get_settings()
    session_factory = get_session_factory()

    with session_factory() as db:
        result = reset_dev_data(db, redis_url=settings.redis_url, updated_by=args.updated_by)

    print("Reset complete")
    print(f"decision_events_deleted={result.decision_events_deleted}")
    print(f"policies_deleted={result.policies_deleted}")
    print(f"redis_keys_deleted={result.redis_keys_deleted}")
    print(f"kill_switch_enabled={result.kill_switch_enabled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
