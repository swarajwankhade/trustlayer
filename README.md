# TrustLayer

AI execution control plane for AI-initiated financial actions (refunds and credits).

TrustLayer sits between AI systems and real-world execution systems and returns:
- `ALLOW`
- `ESCALATE`
- `BLOCK`

## Local Development

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

### 3) Run database migrations
```bash
uv run alembic upgrade head
```

### 4) Start API server
```bash
API_KEY=dev-api-key DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer REDIS_URL=redis://localhost:6379/0 PYTHONPATH=backend uv run uvicorn app.main:app --reload
```

### 5) Run tests
```bash
PYTHONPATH=backend uv run pytest -q
```

## Demo Flow

### Seed bootstrap/demo data
```bash
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer PYTHONPATH=backend uv run python scripts/bootstrap_demo.py
```

### Run a few demo requests (refund allow, credit allow, blocked refund, simulation)
```bash
API_KEY=dev-api-key uv run python scripts/demo_requests.py
```

### Optional: reset local/demo data
```bash
DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer REDIS_URL=redis://localhost:6379/0 PYTHONPATH=backend uv run python scripts/reset_dev_data.py
```

## Notes

- `scripts/bootstrap_demo.py` ensures kill switch exists and creates a default demo policy if that default policy does not already exist.
- `scripts/reset_dev_data.py` is local/dev only. It clears decision events, policies, resets kill switch, and removes Redis `exposure:*` keys.
- `scripts/demo_requests.py` assumes the API server is running on `http://127.0.0.1:8000`.
