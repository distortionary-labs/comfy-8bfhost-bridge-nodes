"""ComfyUI images to and from the image files 8bf-host reads and writes.

ComfyUI passes images as float arrays of shape (height, width, channels)
with values from 0 to 1, and masks as (height, width) with 1 meaning
selected. PAM files store integer samples, 16-bit ones big-endian.
"""

import numpy as np

TUPLE_TYPES = {1: "GRAYSCALE", 2: "GRAYSCALE_ALPHA", 3: "RGB", 4: "RGB_ALPHA"}


def to_samples(frame, sample_bytes):
    """Convert a float frame to PAM sample bytes, 1 or 2 bytes per sample.

    Values outside 0..1 are clipped, and NaN becomes 0, since casting NaN
    to an integer gives an arbitrary value.
    """
    if sample_bytes not in (1, 2):
        raise ValueError("samples are 1 or 2 bytes, not %r" % (sample_bytes,))
    maxval = 255 if sample_bytes == 1 else 65535
    dtype = np.uint8 if sample_bytes == 1 else ">u2"
    values = np.nan_to_num(np.asarray(frame, dtype=np.float32), nan=0.0, posinf=1.0, neginf=0.0)
    scaled = np.clip(values, 0.0, 1.0) * maxval
    return np.rint(scaled).astype(dtype).tobytes()


def from_samples(data, width, height, channels, sample_bytes):
    """Convert PAM sample bytes back to a float32 frame."""
    dtype = np.uint8 if sample_bytes == 1 else ">u2"
    maxval = 255.0 if sample_bytes == 1 else 65535.0
    samples = np.frombuffer(data, dtype=dtype).reshape(height, width, channels)
    return samples.astype(np.float32) / maxval


def mask_to_gray(mask):
    """Convert a float mask to 8-bit grayscale bytes, white being selected."""
    return to_samples(mask, 1)


def mask_bounds(mask):
    """Return ``(x, y, width, height)`` around the selected pixels, or None."""
    values = np.nan_to_num(np.asarray(mask, dtype=np.float32), nan=0.0, posinf=1.0, neginf=0.0)
    selected = np.rint(np.clip(values, 0.0, 1.0) * 255) > 0
    rows = np.flatnonzero(selected.any(axis=1))
    columns = np.flatnonzero(selected.any(axis=0))
    if rows.size == 0:
        return None
    y, x = int(rows[0]), int(columns[0])
    return x, y, int(columns[-1]) - x + 1, int(rows[-1]) - y + 1


def write_pam(path, width, height, channels, sample_bytes, data):
    header = "P7\nWIDTH %d\nHEIGHT %d\nDEPTH %d\nMAXVAL %d\nTUPLTYPE %s\nENDHDR\n" % (
        width,
        height,
        channels,
        255 if sample_bytes == 1 else 65535,
        TUPLE_TYPES[channels],
    )
    with open(path, "wb") as handle:
        handle.write(header.encode("ascii"))
        handle.write(data)


def read_pam(path):
    """Return ``(width, height, channels, bytes per sample, data)``."""
    with open(path, "rb") as handle:
        content = handle.read()
    if not content.startswith(b"P7\n"):
        raise ValueError("%s is not a PAM file" % path)
    end = content.find(b"ENDHDR\n")
    if end < 0:
        raise ValueError("%s has no PAM header end" % path)
    fields = {}
    for line in content[3:end].split(b"\n"):
        parts = line.split(None, 1)
        if len(parts) == 2 and not parts[0].startswith(b"#"):
            fields[parts[0].decode("ascii")] = parts[1].strip()
    try:
        width = int(fields["WIDTH"])
        height = int(fields["HEIGHT"])
        channels = int(fields["DEPTH"])
        maxval = int(fields["MAXVAL"])
    except (KeyError, ValueError) as exc:
        raise ValueError("%s has an incomplete PAM header" % path) from exc
    if width <= 0 or height <= 0 or channels not in TUPLE_TYPES:
        raise ValueError(
            "%s is %dx%d with %d channels, which cannot be read" % (path, width, height, channels)
        )
    # Other maxvals would need rescaling; 8bf-host only writes these two.
    if maxval not in (255, 65535):
        raise ValueError("%s has MAXVAL %d; only 255 and 65535 are supported" % (path, maxval))
    sample_bytes = 1 if maxval == 255 else 2
    data = content[end + len(b"ENDHDR\n") :]
    expected = width * height * channels * sample_bytes
    if len(data) != expected:
        raise ValueError("%s holds %d bytes of pixels, expected %d" % (path, len(data), expected))
    return width, height, channels, sample_bytes, data


def write_pgm(path, width, height, data):
    """Write an 8-bit grayscale image, used for selection masks."""
    with open(path, "wb") as handle:
        handle.write(b"P5\n%d %d\n255\n" % (width, height))
        handle.write(data)
