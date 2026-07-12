FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies
COPY requirements.api.txt .
RUN pip install --no-cache-dir -r requirements.api.txt

# Application code
COPY feature_store/ ./feature_store/
COPY serving/ ./serving/
COPY skew/ ./skew/
COPY lineage/ ./lineage/
COPY configs/ ./configs/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

EXPOSE 8080

# Cloud Run injects $PORT (default 8080) and requires the container to listen on it,
# so use shell form to expand it (exec form ["uvicorn",...] would not). `exec` makes
# uvicorn PID 1 for correct signal handling. Falls back to 8080 locally.
# Single worker: the app shares one DuckDB/MotherDuck connection (feature_store/connections.py).
# Multiple uvicorn workers would each open their own MotherDuck connection, burning the
# free-tier compute-hours quota faster for no benefit in this demo.
CMD exec uvicorn serving.main:app --host 0.0.0.0 --port ${PORT:-8080}
