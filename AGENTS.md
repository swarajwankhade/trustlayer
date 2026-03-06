# TrustLayer MVP — Codex Instructions

## Mission
Build TrustLayer v1: AI Execution Control Plane for AI‑initiated financial actions (refunds and credits).

TrustLayer sits between AI agents and real‑world systems (billing, refunds, credits, account systems).
It evaluates proposed actions (refunds or credit adjustments) and returns ALLOW / ESCALATE / BLOCK.
It writes immutable decision logs.

## Frozen Scope (MVP — Do NOT expand)

IN SCOPE:
- POST /v1/actions/refund
- POST /v1/actions/credit
- Policy engine (caps + near-cap escalation)
- Exposure tracking (Redis)
- Basic anomaly detection (optional MVP component)
- Append-only decision logs (Postgres)
- Kill switch
- Minimal admin endpoints
- Minimal dashboard

OUT OF SCOPE:
- Interpretability
- Fraud ML
- Multi-industry support
- Enterprise SSO
- Complex DSL
- Payment execution

## Tech Stack
- Python 3.12
- FastAPI
- Postgres
- Redis
- Alembic
- pytest

## Documentation Context
Before implementing tasks, always read:
- docs/tech-spec.md
- docs/architecture.md
- docs/roadmap.md

These documents describe the current MVP and the long‑term architecture direction.

Development decisions should align with the long‑term goal:

AI Systems → TrustLayer → Real‑World Execution

TrustLayer should evolve into the mandatory execution authorization layer for AI systems.

## Engineering Rules
- Always follow the current MVP scope defined in docs/tech-spec.md
- Small steps.
- Tests required.
- Deterministic decisions (no probabilistic behavior in authorization).
- Safe fallback: if Redis unavailable → ESCALATE.
- Never expand scope without explicit instruction.
- Use uv for dependency management.