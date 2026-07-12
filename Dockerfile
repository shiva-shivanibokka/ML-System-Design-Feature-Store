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
ENV PORT=7860

EXPOSE 7860

# Single worker: the app shares one DuckDB/MotherDuck connection (feature_store/connections.py).
# Multiple uvicorn workers would each open their own MotherDuck connection, burning the
# free-tier compute-hours quota faster for no benefit in this demo.
CMD ["uvicorn", "serving.main:app", "--host", "0.0.0.0", "--port", "7860"]
