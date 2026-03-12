# TrustLayer

TrustLayer is an AI execution control plane for financial automation.

It sits between AI systems and execution systems and authorizes:
- `POST /v1/actions/refund`
- `POST /v1/actions/credit`

Decisions are deterministic and returned as:
- `ALLOW`
- `ESCALATE`
- `BLOCK`

## MVP Capabilities

- Refund + credit governance with shared authorization pipeline
- Deterministic policy engine (caps + near-cap escalation)
- Redis exposure tracking and combined financial caps
- Idempotency by `request_id`
- Append-only decision ledger in Postgres
- Kill switch and observe-only runtime controls
- Admin APIs for policies, decisions, metrics, simulation, dashboard, and export

## Service Endpoints

- `GET /health` liveness
- `GET /ready` readiness (Postgres + Redis)
- `GET /version` service identity/version

## Local Setup

### 1) Start infrastructure
```bash
cd infra
docker compose up -d
cd ..
```

### 2) Install dependencies
```bash
uv sync --dev
```

### 3) Run migrations
```bash
uv run alembic upgrade head
```

### 4) Run API
```bash
API_KEY=dev-api-key \
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer \
REDIS_URL=redis://localhost:6379/0 \
PYTHONPATH=backend \
uv run uvicorn app.main:app --reload
```

### 5) Run tests
```bash
PYTHONPATH=backend uv run pytest -q
```

## Demo Flow

### Seed demo baseline
```bash
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer \
PYTHONPATH=backend \
uv run python scripts/bootstrap_demo.py
```

### Run demo requests
```bash
API_KEY=dev-api-key uv run python scripts/demo_requests.py
```

### Reset local/dev data
```bash
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer \
REDIS_URL=redis://localhost:6379/0 \
PYTHONPATH=backend \
uv run python scripts/reset_dev_data.py
```

## Important Admin Endpoints

- Policies:
  - `GET /v1/admin/policies`
  - `POST /v1/admin/policies`
  - `POST /v1/admin/policies/validate`
  - `POST /v1/admin/policies/{policy_id}/activate`
  - `GET /v1/admin/policies/active`
- Runtime controls:
  - `GET /v1/admin/killswitch`
  - `POST /v1/admin/killswitch`
- Decisions:
  - `GET /v1/admin/decisions`
  - `GET /v1/admin/decisions/export`
  - `GET /v1/admin/decisions/{event_id}`
  - `POST /v1/admin/decisions/{event_id}/replay`
- Operations:
  - `POST /v1/admin/simulate`
  - `GET /v1/admin/metrics/decisions`
  - `GET /v1/admin/metrics/exposure`
  - `GET /v1/admin/dashboard`

## Notes

- Action and admin endpoints require `X-API-Key`.
- Rate guard is configured by `ACTION_RATE_LIMIT_PER_MINUTE`.
- Service version defaults to `0.1.0` and can be overridden by `SERVICE_VERSION`.
