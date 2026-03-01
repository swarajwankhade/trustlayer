#!/usr/bin/env bash
set -euo pipefail

export API_KEY="${API_KEY:-dev-secret}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://trustlayer:trustlayer@localhost:5432/trustlayer}"
export PYTHONPATH="${PYTHONPATH:-backend}"

uv run uvicorn app.main:app --reload
