FROM python:3.12-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend:/app

WORKDIR /app

COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir --require-hashes --index-url "$PIP_INDEX_URL" -r /app/backend/requirements.lock \
    && python -m pip uninstall --yes pip \
    && rm -rf /root/.cache /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12

COPY backend /app/backend
COPY adapters /app/adapters
COPY agents /app/agents
COPY workers /app/workers

WORKDIR /app/backend

RUN useradd --create-home --uid 10001 autofish \
    && mkdir -p /app/logs \
    && chown -R autofish:autofish /app

USER autofish

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
