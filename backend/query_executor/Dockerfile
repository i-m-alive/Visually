FROM python:3.11-slim AS base
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ ./shared/
COPY query_executor/ ./query_executor/

ENV PYTHONPATH=/app

CMD ["uvicorn", "query_executor.main:app", "--host", "0.0.0.0", "--port", "8002"]
