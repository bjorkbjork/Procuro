FROM python:3.12-slim-bookworm

RUN pip install pdm

WORKDIR /app

COPY pyproject.toml pdm.lock ./
RUN pdm install --prod --no-self --no-editable

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json* ./
RUN npm install --omit=dev

COPY alembic.ini ./
COPY alembic/ alembic/
COPY app/ app/
COPY entrypoint.sh ./

ENTRYPOINT ["./entrypoint.sh"]
