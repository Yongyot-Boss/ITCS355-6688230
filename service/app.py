"""Lab 3 — inference service.

Provider-neutral by construction: the model arrives through the adapter, and the same
container image deploys to SageMaker, Azure ML, or Vertex AI. Route paths differ per
platform; that difference belongs in cloudlayer/, never here.

Run locally:  uvicorn service.app:app --port 8080
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mlflow.exceptions import MlflowException

from service.schemas import (
    BatchRequest,
    BatchResponse,
    HealthResponse,
    PredictRequest,
    PredictResponse,
    ReadyResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
log = logging.getLogger("service")

STATE: dict[str, Any] = {"model": None, "version": os.environ.get("MODEL_VERSION", "unknown")}


def _log_event(event: str, **fields: Any) -> None:
    log.info(
        "%s",
        json.dumps(
            {
                "ts": datetime.now(UTC).isoformat(),
                "level": "INFO",
                "event": event,
                **fields,
            },
            separators=(",", ":"),
        ),
    )


def _load_model():
    """Load once, at startup. Never per request.

    Loading per request is the commonest cause of a p99 that looks nothing like p50, and
    it is the first thing to check when your latency distribution has a long tail.
    """
    name = os.environ.get("MODEL_REGISTRY_NAME")
    version = os.environ.get("MODEL_VERSION")
    if name and version:
        import mlflow.sklearn  # imported lazily so tests can run without a registry

        mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
        return mlflow.sklearn.load_model(f"models:/{name}/{version}")

    artifact_uri = os.environ.get("MODEL_ARTIFACT_URI")
    if artifact_uri:
        import joblib
        import mlflow

        artifact_path = mlflow.artifacts.download_artifacts(artifact_uri=artifact_uri)
        return joblib.load(artifact_path)

    # Fallback for local development and tests only. Submitting this is not acceptable:
    # your deployed service must load a registered version.
    from pathlib import Path

    import joblib

    path = Path(os.environ.get("MODEL_PATH", "reports/model.joblib"))
    if not path.exists():
        raise RuntimeError(
            "No model available. Set MODEL_REGISTRY_NAME and MODEL_VERSION, or MODEL_PATH."
        )
    return joblib.load(path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        STATE["model"] = _load_model()
        _log_event("model_loaded", model_version=STATE["version"])
    except (ImportError, MlflowException, OSError, RuntimeError, TypeError, ValueError) as exc:
        STATE["model"] = None
        log.error(
            "%s",
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "level": "ERROR",
                    "event": "model_load_failed",
                    "error": str(exc),
                    "model_version": STATE["version"],
                },
                separators=(",", ":"),
            ),
        )
    yield
    STATE["model"] = None


app = FastAPI(title="ITCS355 inference", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            {"detail": exc.errors(), "model_version": str(STATE["version"])}
        ),
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "model_version": str(STATE["version"])},
        headers=exc.headers,
    )


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    started = time.perf_counter()
    response = await call_next(request)
    latency_ms = (time.perf_counter() - started) * 1000
    response.headers["x-request-id"] = request_id
    response.headers["x-model-version"] = str(STATE["version"])
    _log_event(
        "request_complete",
        request_id=request_id,
        path=request.url.path,
        status=response.status_code,
        latency_ms=round(latency_ms, 2),
        model_version=str(STATE["version"]),
    )
    return response


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness. The process is up. Says nothing about whether it can serve."""
    return HealthResponse(status="alive", model_version=str(STATE["version"]))


@app.get("/ready", response_model=ReadyResponse)
def ready() -> ReadyResponse | JSONResponse:
    """Readiness. The model is loaded and can score.

    These two are genuinely different, and confusing them causes a specific production
    failure: traffic routed to a container whose model has not finished loading. All three
    providers distinguish them, and Quiz 3 asks about it.
    """
    if STATE["model"] is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": "model not loaded",
                "model_version": str(STATE["version"]),
            },
        )
    return ReadyResponse(status="ready", model_version=str(STATE["version"]))


def _score(rows: list[dict]) -> list[float]:
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    import pandas as pd

    from src.data import FEATURES

    frame = pd.DataFrame(rows)[FEATURES]
    return [float(p) for p in STATE["model"].predict_proba(frame)[:, 1]]


@app.post("/predict", response_model=PredictResponse)
def predict(payload: PredictRequest) -> PredictResponse:
    score = _score([payload.model_dump()])[0]
    return PredictResponse(probability=score, model_version=str(STATE["version"]))


@app.post("/predict/batch", response_model=BatchResponse)
def predict_batch(payload: BatchRequest) -> BatchResponse:
    scores = _score([row.model_dump() for row in payload.rows])
    return BatchResponse(probabilities=scores, model_version=str(STATE["version"]))
