# ComfyUI on the CPU with the nodes installed, a dynamically linked 8bf-host
# and the test filter in models/8bf. No AI model is downloaded: the workflows
# the tests queue only load, filter and save images.
#
# Build from the repository root (docker-compose.yml does this).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# 8bf-host, built from source. Release builds for Linux are static and
# cannot load native .so filters, so the test filter needs this build.
FROM rust:1-bookworm AS host
ARG EIGHTBF_HOST_REPO=https://github.com/distortionary-labs/8bf-host.git
ARG EIGHTBF_HOST_REF=main
WORKDIR /src
RUN git init -q . \
 && git fetch -q --depth 1 "$EIGHTBF_HOST_REPO" "$EIGHTBF_HOST_REF" \
 && git checkout -q FETCH_HEAD \
 # The toolchain file pulls in cross-compilation targets this build does not need.
 && rm -f rust-toolchain.toml \
 && cargo build --release --locked --bin 8bf-host \
 && cp target/release/8bf-host /usr/local/bin/8bf-host

# The test filter from tests/fixtures, built as "Tests/Invert" and "Tests/Error".
FROM ${PYTHON_IMAGE} AS filters
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pytest
COPY tests/conftest.py /src/tests/
COPY tests/fixtures/ /src/tests/fixtures/
COPY integration-test/build_filters.py /src/
RUN python /src/build_filters.py /filters

FROM ${PYTHON_IMAGE}
ARG COMFYUI_REF=v0.41.3
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
RUN git clone -q --depth 1 --branch "$COMFYUI_REF" https://github.com/comfyanonymous/ComfyUI.git /ComfyUI
WORKDIR /ComfyUI
# CPU wheels first, so ComfyUI's requirements do not pull in CUDA.
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt pytest

COPY --from=host /usr/local/bin/8bf-host /usr/local/bin/8bf-host
COPY --from=filters /filters/ /ComfyUI/models/8bf/
# The same files `make install` copies.
COPY __init__.py LICENSE README.md pyproject.toml /ComfyUI/custom_nodes/comfy-8bfhost-bridge-nodes/
COPY comfy_8bfhost_bridge/ /ComfyUI/custom_nodes/comfy-8bfhost-bridge-nodes/comfy_8bfhost_bridge/
COPY integration-test/tests/ /integration-test/tests/

EXPOSE 8188
CMD ["python", "main.py", "--cpu", "--listen", "0.0.0.0", "--port", "8188"]
