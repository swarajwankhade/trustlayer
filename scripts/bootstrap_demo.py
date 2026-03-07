#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import get_session_factory
from app.devtools.service import bootstrap_demo_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed local demo bootstrap data.")
    parser.add_argument("--no-activate", action="store_true", help="Do not activate the selected policy.")
    parser.add_argument("--created-by", default="dev-bootstrap", help="created_by value for new demo policy.")
    args = parser.parse_args()

    session_factory = get_session_factory()
    with session_factory() as db:
        result = bootstrap_demo_data(
            db,
            activate_policy=not args.no_activate,
            created_by=args.created_by,
        )

    print("Bootstrap complete")
    print(f"created_kill_switch={result.created_kill_switch}")
    print(f"created_policy={result.created_policy}")
    print(f"activated_policy={result.activated_policy}")
    print(f"policy_id={result.policy_id}")
    print(f"policy_version={result.policy_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
