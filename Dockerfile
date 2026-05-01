FROM python:3.12-slim

RUN pip install pdm

WORKDIR /app

COPY pyproject.toml pdm.lock ./
RUN pdm install --prod --no-self --no-editable

COPY alembic.ini ./
COPY alembic/ alembic/
COPY app/ app/
COPY entrypoint.sh ./

ENTRYPOINT ["./entrypoint.sh"]
