"""The nodes inside a running ComfyUI, driven through its HTTP API.

ComfyUI runs in another container (see docker-compose.yml) with 8bf-host
and the test filter installed. Each test uploads an image, queues a small
workflow of built-in nodes around the 8bf nodes, and checks what comes back.
"""

import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import numpy as np
import pytest
from PIL import Image

URL = os.environ.get("COMFYUI_URL", "http://localhost:8188").rstrip("/")
TIMEOUT = 120.0


def _request(path, data=None, headers=None):
    request = urllib.request.Request(URL + path, data=data, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _get_json(path):
    return json.loads(_request(path))


def upload(pixels):
    """Upload an RGB or RGBA image to ComfyUI's input folder; return its name."""
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="PNG")
    name = "8bf-test-%s.png" % uuid.uuid4().hex
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            b"--%s\r\n" % boundary.encode(),
            b'Content-Disposition: form-data; name="image"; filename="%s"\r\n' % name.encode(),
            b"Content-Type: image/png\r\n\r\n",
            buffer.getvalue(),
            b"\r\n--%s--\r\n" % boundary.encode(),
        ]
    )
    headers = {"Content-Type": "multipart/form-data; boundary=%s" % boundary}
    return json.loads(_request("/upload/image", body, headers))["name"]


def queue(prompt):
    """Queue a workflow and wait for it; return its history entry."""
    body = json.dumps({"prompt": prompt, "client_id": uuid.uuid4().hex}).encode()
    try:
        reply = json.loads(_request("/prompt", body, {"Content-Type": "application/json"}))
    except urllib.error.HTTPError as exc:
        raise AssertionError("ComfyUI refused the workflow: %s" % exc.read().decode()) from None
    prompt_id = reply["prompt_id"]
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        history = _get_json("/history/%s" % prompt_id)
        if prompt_id in history and history[prompt_id]["status"].get("completed") is not None:
            entry = history[prompt_id]
            if entry["status"]["status_str"] in ("success", "error"):
                return entry
        time.sleep(0.2)
    raise AssertionError("The workflow did not finish in %g seconds." % TIMEOUT)


def saved_images(entry, node_id):
    """The images a SaveImage node wrote, as uint8 arrays."""
    assert entry["status"]["status_str"] == "success", error_message(entry)
    images = []
    for info in entry["outputs"][node_id]["images"]:
        query = urllib.parse.urlencode(
            {"filename": info["filename"], "subfolder": info["subfolder"], "type": info["type"]}
        )
        data = _request("/view?" + query)
        images.append(np.asarray(Image.open(io.BytesIO(data)).convert("RGB")))
    return images


def error_message(entry):
    for event, data in entry["status"].get("messages", []):
        if event == "execution_error":
            return data.get("exception_message", "")
    return ""


def gradient(height=16, width=24):
    y, x = np.mgrid[0:height, 0:width]
    pixels = np.stack([x * 10, y * 15, (x + y) * 6], axis=-1)
    return pixels.clip(0, 255).astype(np.uint8)


def filter_workflow(image_name, node="EightBfFilter", filter="Tests/Invert", **inputs):
    """LoadImage -> 8bf node -> SaveImage, with extra nodes or inputs merged in."""
    values = {
        "image": ["1", 0],
        "filter": filter,
        "settings": "defaults",
        "depth": "8-bit",
    }
    values.update(inputs)
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "2": {"class_type": node, "inputs": values},
        "3": {
            "class_type": "SaveImage",
            "inputs": {"images": ["2", 0], "filename_prefix": "8bf-test-" + uuid.uuid4().hex},
        },
    }


@pytest.fixture(scope="module")
def object_info():
    return _get_json("/object_info")


def test_nodes_are_registered(object_info):
    assert object_info["EightBfFilter"]["display_name"] == "8bf Filter"
    assert object_info["EightBfFilterByName"]["display_name"] == "8bf Filter (by Name)"
    assert object_info["EightBfFilter"]["category"] == "image/filters/8bf"


def test_filter_list_comes_from_8bf_host(object_info):
    # The test filter sits in ComfyUI/models/8bf, which the nodes add to
    # 8bf-host's folders.
    choices = object_info["EightBfFilter"]["input"]["required"]["filter"][0]
    assert sorted(choices) == ["Tests/Error", "Tests/Invert"]


def test_invert_8bit():
    pixels = gradient()
    entry = queue(filter_workflow(upload(pixels)))
    (out,) = saved_images(entry, "3")
    assert np.array_equal(out, 255 - pixels)


def test_invert_16bit_by_name():
    pixels = gradient()
    workflow = filter_workflow(
        upload(pixels), node="EightBfFilterByName", filter="Tests/Invert", depth="16-bit"
    )
    (out,) = saved_images(queue(workflow), "3")
    # 16-bit samples go through the 8-bit fallback and back to 8-bit PNG.
    assert np.abs(out.astype(int) - (255 - pixels.astype(int))).max() <= 1


def test_batch_is_filtered_frame_by_frame():
    pixels = gradient()
    workflow = filter_workflow(upload(pixels), image=["4", 0])
    workflow["4"] = {"class_type": "RepeatImageBatch", "inputs": {"image": ["1", 0], "amount": 3}}
    images = saved_images(queue(workflow), "3")
    assert len(images) == 3
    for out in images:
        assert np.array_equal(out, 255 - pixels)


def test_mask_limits_the_filter():
    pixels = gradient()
    # LoadImage's mask is 1 - alpha: transparent pixels are selected.
    alpha = np.full(pixels.shape[:2], 255, dtype=np.uint8)
    alpha[:, : pixels.shape[1] // 2] = 0
    rgba = np.dstack([pixels, alpha])
    workflow = filter_workflow(upload(rgba), mask=["1", 1])
    (out,) = saved_images(queue(workflow), "3")
    half = pixels.shape[1] // 2
    assert np.array_equal(out[:, :half], 255 - pixels[:, :half])
    assert np.array_equal(out[:, half:], pixels[:, half:])


def test_last_used_runs_after_a_first_run():
    pixels = gradient()
    name = upload(pixels)
    saved_images(queue(filter_workflow(name)), "3")
    (out,) = saved_images(queue(filter_workflow(name, settings="last used")), "3")
    assert np.array_equal(out, 255 - pixels)


def test_filter_errors_reach_comfyui():
    entry = queue(filter_workflow(upload(gradient()), filter="Tests/Error"))
    assert entry["status"]["status_str"] == "error"
    assert "failed on purpose" in error_message(entry)


def test_unknown_filter_name_fails():
    workflow = filter_workflow(
        upload(gradient()), node="EightBfFilterByName", filter="Tests/Missing"
    )
    entry = queue(workflow)
    assert entry["status"]["status_str"] == "error"
    assert error_message(entry)
