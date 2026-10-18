PYTHON ?= python3
PYTEST ?= $(PYTHON) -m pytest
COMFYUI ?= $(HOME)/ComfyUI
TARGET := $(COMFYUI)/custom_nodes/comfy-8bfhost-bridge-nodes

.PHONY: test lint format install integration-test clean

test:
	$(PYTEST) -q

lint:
	ruff check comfy_8bfhost_bridge tests __init__.py
	ruff format --check comfy_8bfhost_bridge tests __init__.py

format:
	ruff format comfy_8bfhost_bridge tests __init__.py

# Copies the nodes into a ComfyUI checkout; set COMFYUI to point at another one.
install:
	@test -d "$(COMFYUI)/custom_nodes" || { echo "No ComfyUI at $(COMFYUI); set COMFYUI." >&2; exit 1; }
	rm -rf "$(TARGET)" && mkdir -p "$(TARGET)"
	cp -r __init__.py comfy_8bfhost_bridge LICENSE README.md pyproject.toml "$(TARGET)"
	find "$(TARGET)" -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "Installed to $(TARGET). Restart ComfyUI."

# ComfyUI, 8bf-host and the test filter in Docker; see integration-test/README.md.
COMPOSE := docker compose -f integration-test/docker-compose.yml

integration-test:
	$(COMPOSE) build
	$(COMPOSE) up --abort-on-container-exit --exit-code-from tests; \
	status=$$?; $(COMPOSE) down --volumes; exit $$status

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
