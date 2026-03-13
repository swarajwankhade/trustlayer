# TrustLayer MVP Runbook

This runbook is for local/dev deployment and day-to-day MVP operations.

## 1) Prerequisites

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)
- Docker + Docker Compose
- Postgres (via `infra/docker-compose.yml` or external instance)
- Redis (via `infra/docker-compose.yml` or external instance)

## 2) Required Environment Variables

Use `.env.example` as baseline.

```bash
APP_ENV=development
APP_NAME=TrustLayer
SERVICE_VERSION=0.1.0
API_HOST=0.0.0.0
API_PORT=8000
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer
API_KEY=dev-api-key
REDIS_URL=redis://localhost:6379/0
ACTION_RATE_LIMIT_PER_MINUTE=120
```

Important variables:
- `DATABASE_URL`: Postgres connection string (required for DB-backed endpoints/migrations/tests).
- `API_KEY`: required in `X-API-Key` header for `/v1/*` endpoints.
- `REDIS_URL`: Redis connection string for exposure + rate counters.
- `SERVICE_VERSION`: value returned by `/version` and response header.
- `ACTION_RATE_LIMIT_PER_MINUTE`: safety throttle threshold.

## 3) Local Startup Flow

1. Start infrastructure:
```bash
cd infra
docker compose up -d
cd ..
```

2. Install dependencies:
```bash
uv sync --dev
```

3. Run migrations:
```bash
uv run alembic upgrade head
```

4. Start API:
```bash
API_KEY=dev-api-key \
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer \
REDIS_URL=redis://localhost:6379/0 \
PYTHONPATH=backend \
uv run uvicorn app.main:app --reload
```

5. Open dashboard:
- [http://localhost:8000/admin](http://localhost:8000/admin)

6. Run tests:
```bash
PYTHONPATH=backend uv run pytest -q
```

## 4) Demo Flow

1. Bootstrap baseline demo state:
```bash
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer \
PYTHONPATH=backend \
uv run python scripts/bootstrap_demo.py
```

2. Run demo requests:
```bash
API_KEY=dev-api-key \
PYTHONPATH=backend \
uv run python scripts/demo_requests.py
```

3. Validate service health/readiness/version:
```bash
curl -s http://localhost:8000/health
curl -s -i http://localhost:8000/ready
curl -s http://localhost:8000/version
```

4. Inspect outcomes:
- Dashboard: [http://localhost:8000/admin](http://localhost:8000/admin)
- Admin API snapshot: `GET /v1/admin/dashboard`

## 5) Policy Rollout Guidance (Safe Sequence)

Suggested sequence for production-like safety:

1. Validate policy rules:
- `POST /v1/admin/policies/validate`

2. Create policy:
- `POST /v1/admin/policies`

3. Optional controlled rollout:
- Enable `observe_only=true` via `POST /v1/admin/killswitch`
- Send representative traffic and inspect would-decisions.

4. Activate policy:
- `POST /v1/admin/policies/{policy_id}/activate`

5. Monitor:
- `GET /v1/admin/metrics/decisions`
- `GET /v1/admin/metrics/exposure`
- `GET /v1/admin/decisions`
- `/admin` dashboard Recent Decisions + metrics cards

## 6) Operational Checks

Core checks:
- `GET /health`: process liveness (`200` expected).
- `GET /ready`: dependency readiness (`200` if Postgres + Redis healthy, else `503`).
- `GET /version`: service identity/version.
- `GET /admin`: operator dashboard.

Where to inspect behavior quickly:
- Recent decisions and controls: `/admin`
- Decision history API: `GET /v1/admin/decisions`
- Decision detail/replay:
  - `GET /v1/admin/decisions/{event_id}`
  - `POST /v1/admin/decisions/{event_id}/replay`

## 7) Local Reset / Recovery

Reset local/dev data:
```bash
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer \
REDIS_URL=redis://localhost:6379/0 \
PYTHONPATH=backend \
uv run python scripts/reset_dev_data.py
```

Re-bootstrap demo baseline:
```bash
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer \
PYTHONPATH=backend \
uv run python scripts/bootstrap_demo.py
```

If infra is stale, restart containers:
```bash
cd infra
docker compose down
docker compose up -d
cd ..
```

Then rerun migrations and bootstrap.

## 8) Common Issues

- Invalid API key (`401`):
  - Confirm `API_KEY` server env and `X-API-Key` header/dashboard key match exactly.

- `DATABASE_URL` not set:
  - DB-dependent routes, migrations, and many tests will fail.
  - Export `DATABASE_URL` before running app/migrations/tests.

- No active policy:
  - Engine falls back to allow baseline with diagnostic reason codes.
  - Create + activate policy via admin APIs/dashboard.

- Replay unavailable for some events:
  - Replay requires stored `policy_id` + `policy_version` and valid action payload.
  - Older/malformed rows may return clear replay errors.
