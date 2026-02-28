# TrustLayer

AI Execution Control Plane for Refund Autonomy.

## Task 1 setup

Start local dependencies:

```bash
docker compose -f infra/docker-compose.yml up -d
```

Install the backend environment:

```bash
uv sync --group dev
```

Run the API:

```bash
uv run uvicorn app.main:app --app-dir backend --reload
```

Run tests:

```bash
uv run pytest
```

## Database migrations

Set the local database URL:

```bash
export DATABASE_URL=postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer
```

Run migrations:

```bash
uv run alembic upgrade head
```
