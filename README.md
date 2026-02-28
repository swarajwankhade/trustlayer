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
uv run uvicorn backend.app.main:app --reload
```

Run tests:

```bash
uv run pytest
```
