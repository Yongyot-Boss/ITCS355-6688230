# ITCS355 Lab 1
# `make reproduce` is the one command a grader runs. Keep it working.

SHELL := /bin/bash
IMAGE ?= itcs355-lab1
TAG   ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
PLATFORM ?= linux/amd64
SEED ?= 20260101
PYTHON ?= $(if $(VIRTUAL_ENV),$(VIRTUAL_ENV)/bin/python,$(if $(wildcard .venv/bin/python),.venv/bin/python,python))

.PHONY: help setup cloud-check data test portability-audit train train-remote image image-push reproduce verify clean teardown \
	tune compare register-model reload-check serve serve-image deploy smoke loadtest canary-traffic rollback-traffic drift inject-drift pipeline cost swap-check llm-eval llm-gate

help:
	@grep -E "^[a-zA-Z_-]+:.*?## .*$$" $(MAKEFILE_LIST) | awk -F":.*?## " "{printf \"  %-20s %s\\n\", \$$1, \$$2}"

setup: ## Install dependencies and print environment status
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt
	@echo "environment ok"

cloud-check: ## Resolve the eight capability slots
	python scripts/cloud_check.py

data: ## Generate the default dataset (deterministic)
	$(PYTHON) scripts/make_dataset.py --seed $(SEED)

test: ## Run data contract and split property tests
	pytest -q tests/

portability-audit: ## Fail if provider strings leak into src/
	python scripts/portability_audit.py

train: ## Train locally, outside the container
	python -m src.train --seed $(SEED) --metrics-out reports/metrics.json

train-remote: data image ## Submit the training image to managed Vertex AI compute
	$(PYTHON) -c "from src import config; from cloudlayer.factory import get_adapter; cfg=config.load(); adapter=get_adapter(cfg); adapter.upload('data/raw/sensors.csv', 'raw/sensors.csv'); image=adapter.push_image('$(IMAGE):$(TAG)'); job=adapter.submit_training(image, {'seed': $(SEED), 'instance': '$${TRAIN_INSTANCE:-e2-standard-4}', 'service_account': cfg.identity_ref, 'blob_uri': cfg.blob_uri, 'labels': cfg.tags(1)}); print(job); print(adapter.wait_training(job))"

image: ## Build the training image for linux/amd64
	docker buildx build --platform $(PLATFORM) --build-arg GIT_COMMIT=$$(git rev-parse HEAD 2>/dev/null || echo unknown) -t $(IMAGE):$(TAG) --load .

image-push: image ## Push to CONTAINER_REGISTRY via your adapter
	python -c "from src import config; from cloudlayer.factory import get_adapter; \
	print(get_adapter(config.load()).push_image(\"$(IMAGE):$(TAG)\"))"

reproduce: image ## THE ONE COMMAND. Grader runs this.
	docker run --rm \
	  -v "$$PWD/reports:/app/reports" \
	  --user "$$(id -u):$$(id -g)" \
	  -e MLFLOW_TRACKING_URI=sqlite:////app/reports/mlflow.db \
	  --entrypoint /bin/sh \
	  $(IMAGE):$(TAG) -c "python scripts/make_dataset.py --seed $(SEED) && \
	  python -m src.train --seed $(SEED) --metrics-out /app/reports/metrics.json"

verify: ## Check the produced metric against the README claim
	python scripts/verify_metric.py

teardown: ## Delete every resource tagged course=itcs355 for this lab
	$(PYTHON) -c "from src import config; from cloudlayer.factory import get_adapter; \
	cfg=config.load(); print(get_adapter(cfg).teardown(cfg.tags($(TEARDOWN_LAB))))"

clean: ## Remove local artifacts
	rm -rf mlruns mlartifacts mlflow.db reports/metrics.json .pytest_cache

# --- Lab 2 -------------------------------------------------------------------
tune: ## Budgeted hyperparameter study (>=12 trials)
	python -m src.tune --trials 12 --budget-thb 150

compare: ## Rank runs by metric and by cost per point
	python scripts/compare_runs.py --experiment itcs355-lab2

register-model: ## Register a Lab 2 run with version-level lineage
	python scripts/register_model.py --run-id $(RUN_ID) --training-job-id $(TRAINING_JOB_ID) --image-digest $(IMAGE_DIGEST)

reload-check: ## Load the registered model by version and score rows
	$(PYTHON) scripts/reload_check.py --name $(MODEL_REGISTRY_NAME) --version $(VERSION)

# --- Lab 3 -------------------------------------------------------------------
serve: ## Run the inference service locally on :8080
	python scripts/export_model.py --out reports/model.joblib
	MODEL_PATH=reports/model.joblib MODEL_VERSION=local uvicorn service.app:app --port 8080

serve-image: ## Build the serving image
	docker buildx build --platform $(PLATFORM) -f service/Dockerfile.serve -t itcs355-serve:$(TAG) --load .

MODEL_REF ?=
ENDPOINT ?= itcs355-lab3
INSTANCE ?= n1-standard-2
TEARDOWN_LAB ?= 3

deploy: serve-image ## Push the serving image and deploy MODEL_REF to Vertex AI
	@test -n "$(MODEL_REF)" || (echo "Set MODEL_REF=models:/<name>/<version>" >&2; exit 2)
	$(PYTHON) -c "import os; from src import config; from cloudlayer.factory import get_adapter; cfg=config.load(); adapter=get_adapter(cfg); image=adapter.push_image('itcs355-serve:$(TAG)'); os.environ['SERVING_IMAGE_URI']=image; print(adapter.deploy('$(MODEL_REF)', '$(ENDPOINT)', '$(INSTANCE)'))"

smoke: ## Invoke ENDPOINT with three known predictions
	@test -n "$(ENDPOINT)" || (echo "Set ENDPOINT to the Vertex endpoint ID" >&2; exit 2)
	$(PYTHON) scripts/smoke.py --endpoint "$(ENDPOINT)"

loadtest: ## Load test at three concurrency levels
	@for vus in 1 10 50; do \
	  echo "=== $$vus VUs ==="; \
	  k6 run -e TARGET=$(TARGET) -e VUS=$$vus loadtest/k6.js || true; \
	done

CANARY_SPLIT ?=
canary-traffic: ## Apply CANARY_SPLIT=DEPLOYED_MODEL_ID=90,DEPLOYED_MODEL_ID=10 and save evidence
	@test -n "$(CANARY_SPLIT)" || (echo "Set CANARY_SPLIT=<old-id>=90,<new-id>=10" >&2; exit 2)
	$(PYTHON) scripts/canary_traffic.py --endpoint "$(ENDPOINT)" --split "$(CANARY_SPLIT)"

rollback-traffic: ## Apply CANARY_SPLIT=<good-id>=100,<bad-id>=0 and save rollback evidence
	@test -n "$(CANARY_SPLIT)" || (echo "Set CANARY_SPLIT=<good-id>=100,<bad-id>=0" >&2; exit 2)
	$(PYTHON) scripts/canary_traffic.py --endpoint "$(ENDPOINT)" --split "$(CANARY_SPLIT)"

# --- Lab 4 -------------------------------------------------------------------
inject-drift: ## Shift a feature's distribution on purpose
	python scripts/inject_drift.py --feature temp_c --mode shift --magnitude 6

drift: ## Score drift against the reference window
	python -m monitoring.drift --current data/current.csv

# --- Lab 5 -------------------------------------------------------------------
pipeline: ## Compile pipeline/pipeline.yaml for your provider
	python -c "from cloudlayer.pipelines import compile_for; from src import config; \
	compile_for(config.load().provider)"

llm-eval: ## Run the LLM golden set against recorded responses (offline, free)
	python scripts/llm_eval.py --out reports/llm_eval-baseline.json

llm-gate: ## Prove the gate fails on a degraded set — expected to exit non-zero
	python scripts/llm_eval.py --out reports/llm_eval-baseline.json >/dev/null
	python scripts/llm_eval.py --responses evals/fixtures/triage-regressed.jsonl \
	  --out reports/llm_eval.json --baseline reports/llm_eval-baseline.json

cost: ## Build the cost report
	python scripts/cost_report.py --estimate $(EST) --actual $(ACT) --rps $(RPS) --instance $(INSTANCE)

swap-check: ## Prove the portability seam against a second provider
	python scripts/portability_swap_check.py --second-provider $(SECOND)
