FROM python:3.11-slim@sha256:45a610eb30c258a202b7000414e1ade0c3549cf5ce1ca16c369e0da116d0fa89

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN useradd --create-home --uid 10001 runner
WORKDIR /app
COPY requirements-serve.txt ./
RUN pip install --no-cache-dir -r requirements-serve.txt

COPY --chown=runner:runner src/ ./src/
COPY --chown=runner:runner cloudlayer/ ./cloudlayer/
COPY --chown=runner:runner monitoring/drift.py monitoring/drift_alert.py ./monitoring/

USER runner
ENTRYPOINT ["python", "-m", "monitoring.drift_alert"]
