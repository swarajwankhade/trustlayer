# TrustLayer MVP — Codex Instructions

## Mission
Build TrustLayer v1: AI Execution Control Plane for refund autonomy.

TrustLayer sits between AI agent and billing system.
It evaluates refund actions and returns ALLOW / ESCALATE / BLOCK.
It writes immutable decision logs.

## Frozen Scope (Do NOT expand)

IN SCOPE:
- POST /v1/actions/refund
- Policy engine (caps + near-cap escalation)
- Exposure tracking (Redis)
- Anomaly detection (statistical)
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

## Engineering Rules
- Small steps.
- Tests required.
- Deterministic decisions.
- Safe fallback: if Redis unavailable → ESCALATE.
- Never expand scope without explicit instruction.
- Use uv for dependency management.