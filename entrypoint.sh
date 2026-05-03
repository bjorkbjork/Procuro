#!/bin/sh
set -e
uv run alembic upgrade head
exec uv run python -m app.main
