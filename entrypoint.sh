#!/bin/sh
set -e
pdm run alembic upgrade head
exec pdm run python -m app.main
