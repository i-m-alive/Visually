FROM python:3.11-slim AS base
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY auth_service/ ./auth_service/
COPY agent_service/ ./agent_service/
COPY shared/alembic.ini ./alembic.ini

ENV PYTHONPATH=/app

CMD ["uvicorn", "agent_service.main:app", "--host", "0.0.0.0", "--port", "8001", "--reload"]
