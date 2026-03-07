# TrustLayer

AI Execution Control Plane for AI-initiated financial actions.

TrustLayer sits between AI systems and real-world execution systems and decides whether an action should be:

- ALLOW
- ESCALATE
- BLOCK

Current MVP scope:

- Refund authorization
- Credit authorization
- Deterministic policy engine
- Exposure tracking with Redis
- Immutable decision ledger in Postgres
- Idempotent request handling
- Kill switch for automation safety

## Architecture

TrustLayer follows this model:

AI Systems → TrustLayer → Real-World Execution

TrustLayer acts as a deterministic authorization boundary for probabilistic AI systems.

## Current action endpoints

- `POST /v1/actions/refund`
- `POST /v1/actions/credit`

## Local development

### Start infrastructure
```bash
cd infra
docker compose up -d