#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import uuid
from urllib import error, request

BASE_URL = os.getenv("TRUSTLAYER_BASE_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("API_KEY") or os.getenv("ADMIN_API_KEY", "dev-secret")


def post(path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json", "X-API-Key": API_KEY},
        method="POST",
    )
    with request.urlopen(req) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        return resp.getcode(), body


def main() -> int:
    print(f"Running TrustLayer demo requests against {BASE_URL}")

    refund_req = {
        "request_id": f"demo-refund-{uuid.uuid4()}",
        "user_id": "demo-user-1",
        "ticket_id": "ticket-demo-1",
        "refund_amount_cents": 5000,
        "currency": "USD",
        "model_version": "demo-v1",
        "metadata": {"source": "demo-script"},
    }
    credit_req = {
        "request_id": f"demo-credit-{uuid.uuid4()}",
        "user_id": "demo-user-2",
        "ticket_id": "ticket-demo-2",
        "credit_amount_cents": 3000,
        "currency": "USD",
        "credit_type": "courtesy",
        "model_version": "demo-v1",
        "metadata": {"source": "demo-script"},
    }
    blocked_refund_req = {
        "request_id": f"demo-block-{uuid.uuid4()}",
        "user_id": "demo-user-3",
        "ticket_id": "ticket-demo-3",
        "refund_amount_cents": 15000,
        "currency": "USD",
        "model_version": "demo-v1",
        "metadata": {"source": "demo-script"},
    }
    simulation_req = {
        "action_type": "refund",
        "refund": {
            "user_id": "demo-user-sim",
            "refund_amount_cents": 8000,
            "currency": "USD",
            "metadata": {"source": "demo-script"},
        },
        "exposure_override": {
            "daily_total_amount_cents": 0,
            "per_user_daily_count": 0,
            "per_user_daily_amount_cents": 0,
            "financial_total_amount_cents": 15000,
        },
    }

    calls = [
        ("refund_allow", "/v1/actions/refund", refund_req),
        ("credit_allow", "/v1/actions/credit", credit_req),
        ("refund_block", "/v1/actions/refund", blocked_refund_req),
        ("simulate_refund", "/v1/admin/simulate", simulation_req),
    ]

    for label, path, payload in calls:
        try:
            code, body = post(path, payload)
            print(f"{label}: status={code} decision={body.get('decision')} reason_codes={body.get('reason_codes')}")
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8")
            print(f"{label}: failed status={exc.code} body={response_body}")
            return 1
        except Exception as exc:
            print(f"{label}: failed ({exc})")
            return 1

    print("Demo flow complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
