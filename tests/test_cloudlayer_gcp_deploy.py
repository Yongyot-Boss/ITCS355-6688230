"""Lab 3 adapter contract tests; all remote Vertex and GCS calls are mocked."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import mlflow
import mlflow.sklearn

from cloudlayer.gcp import GcpAdapter
from src import config


def test_registered_version_loads_its_logged_model_output(monkeypatch):
    adapter = GcpAdapter(config.load(strict=False))
    logged_model_uri = "models:/m-logged-model"
    class Client:
        def __init__(self, tracking_uri):
            assert tracking_uri == adapter.cfg.mlflow_tracking_uri

        def get_model_version(self, name, version):
            assert (name, version) == ("registered-name", "1")
            return SimpleNamespace(source="runs:/run-id/model", run_id="run-id")

        def get_run(self, run_id):
            assert run_id == "run-id"
            return SimpleNamespace(
                outputs=SimpleNamespace(
                    model_outputs=[SimpleNamespace(model_id="logged-model")]
                )
            )

        def get_logged_model(self, model_id):
            assert model_id == "logged-model"
            return SimpleNamespace(model_uri=logged_model_uri)

    monkeypatch.setattr(mlflow, "set_tracking_uri", lambda uri: None)
    monkeypatch.setattr("mlflow.tracking.MlflowClient", Client)

    assert adapter._registered_model_source("models:/registered-name/1") == (
        logged_model_uri,
        "1",
    )


def test_deploy_configures_vertex_routes_and_returns_endpoint(monkeypatch):
    adapter = GcpAdapter(config.load(strict=False))
    monkeypatch.setenv("SERVING_IMAGE_URI", "asia-southeast1-docker.pkg.dev/p/r/i@sha256:abc")
    monkeypatch.setattr(
        adapter,
        "_stage_model_artifact",
        lambda model_ref, endpoint: ("gs://bucket/models/3/model.joblib", "3"),
    )
    monkeypatch.setattr(
        adapter,
        "_wait_vertex_operation",
        lambda operation: {
            "name": "projects/project/locations/region/models/model"
            if operation["name"] == "model-upload"
            else "projects/project/locations/region/endpoints/endpoint"
        },
    )
    requests = []

    def vertex_request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        if path.endswith("models:upload"):
            return {"name": "model-upload"}
        if path.endswith("/endpoints"):
            return {"name": "endpoint-create"}
        return {"name": "deploy-operation"}

    monkeypatch.setattr(adapter, "_vertex_request", vertex_request)

    endpoint_name = adapter.deploy("models:/model/3", "endpoint", "n1-standard-2")

    model_spec = requests[0][2]["json"]["model"]
    assert model_spec["containerSpec"]["healthRoute"] == "/ready"
    assert model_spec["containerSpec"]["predictRoute"] == "/predict"
    assert model_spec["containerSpec"]["env"][-1] == {
        "name": "MODEL_VERSION",
        "value": "3",
    }
    assert requests[2][1].endswith("/endpoints/endpoint:deployModel")
    assert endpoint_name.endswith("/endpoints/endpoint")


def test_invoke_uses_vertex_raw_predict_and_decodes_service_response(monkeypatch):
    adapter = GcpAdapter(config.load(strict=False))
    expected = {"probability": 0.73, "model_version": "3"}
    output = base64.b64encode(json.dumps(expected).encode()).decode()
    request = {}

    def vertex_request(method, path, **kwargs):
        request.update(method=method, path=path, **kwargs)
        return {"output": output}

    monkeypatch.setattr(adapter, "_vertex_request", vertex_request)
    payload = {"temp_c": 78.4}

    assert adapter.invoke("endpoint", payload) == expected
    assert request["path"].endswith("/endpoints/endpoint:rawPredict")
    assert request["json"] == payload


def test_set_traffic_split_updates_only_known_deployed_models(monkeypatch):
    adapter = GcpAdapter(config.load(strict=False))
    calls = []
    endpoint = {
        "deployedModels": [{"id": "good"}, {"id": "candidate"}],
        "trafficSplit": {"good": 90, "candidate": 10},
    }

    def vertex_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "GET":
            return endpoint
        assert method == "PATCH"
        assert kwargs["params"] == {"updateMask": "traffic_split"}
        assert kwargs["json"] == {"trafficSplit": {"good": 100, "candidate": 0}}
        endpoint["trafficSplit"] = kwargs["json"]["trafficSplit"]
        return {"name": "update-operation"}

    monkeypatch.setattr(adapter, "_vertex_request", vertex_request)
    monkeypatch.setattr(adapter, "_wait_vertex_operation", lambda operation: {})

    assert adapter.set_traffic_split("endpoint", {"good": 100, "candidate": 0}) == {
        "good": 100,
        "candidate": 0,
    }
    assert [call[0] for call in calls] == ["GET", "PATCH", "GET"]
