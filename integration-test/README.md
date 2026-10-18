# Integration tests

These tests run the nodes inside a real ComfyUI, with a real 8bf-host, in Docker. They load no AI model. Each test queues a small workflow of ComfyUI's built-in nodes around the 8bf nodes (load an image, filter it, save it) through ComfyUI's HTTP API and checks the pixels that come back.

From the repository root:

```console
$ make integration-test
```

This needs Docker with Compose on an x86-64 machine. The first run builds the image, which compiles 8bf-host from source and installs the CPU build of torch, and takes several minutes; later runs reuse the cached layers.

## What the image holds

- ComfyUI at the tag in `COMFYUI_REF`, started with `--cpu`.
- 8bf-host built with `cargo build --release` from `EIGHTBF_HOST_REF` of its repository. Release builds for Linux are static and cannot load native `.so` filters, so the image builds its own.
- The test filter from `tests/fixtures/testfilter.c`, compiled as `Tests/Invert` and `Tests/Error` into `ComfyUI/models/8bf`.
- The nodes, copied into `custom_nodes` as `make install` does.

## Choosing versions

Both refs are build arguments, read from the environment:

```console
$ EIGHTBF_HOST_REF=v0.1.0 COMFYUI_REF=v0.41.3 make integration-test
```

`EIGHTBF_HOST_REF` defaults to `main`, so a rebuild picks up 8bf-host's latest commit only when Docker's cache for that layer is invalidated. Pass `--no-cache` to `docker compose build` to force it.

## Running ComfyUI by hand

To look at the running ComfyUI, or rerun the tests without rebuilding:

```console
$ docker compose -f integration-test/docker-compose.yml up -d comfyui
$ docker compose -f integration-test/docker-compose.yml run --rm tests
$ docker compose -f integration-test/docker-compose.yml logs comfyui
$ docker compose -f integration-test/docker-compose.yml down
```

ComfyUI runs with `COMFY_8BFHOST_BRIDGE_DEBUG=1`, so its log shows every 8bf-host command and that command's output.
