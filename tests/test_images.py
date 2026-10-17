import numpy as np
import pytest

from comfy_8bfhost_bridge import images


def test_8bit_samples_round_trip():
    frame = np.array([[[0.0, 0.5, 1.0], [0.2, 1.5, -0.1]]], dtype=np.float32)
    data = images.to_samples(frame, 1)
    assert data == bytes([0, 128, 255, 51, 255, 0])
    back = images.from_samples(data, 2, 1, 3, 1)
    assert back.dtype == np.float32
    assert np.allclose(back, np.clip(frame, 0, 1), atol=1 / 255)


def test_16bit_samples_are_big_endian():
    frame = np.array([[[1.0, 0.0, 256 / 65535]]], dtype=np.float32)
    data = images.to_samples(frame, 2)
    assert data == b"\xff\xff\x00\x00\x01\x00"
    assert np.allclose(images.from_samples(data, 1, 1, 3, 2), frame, atol=1e-6)


def test_nan_and_infinity_are_clamped():
    frame = np.array([[[np.nan, np.inf, -np.inf]]], dtype=np.float32)
    assert images.to_samples(frame, 1) == bytes([0, 255, 0])
    assert images.mask_bounds(np.array([[np.nan, 0.0]])) is None
    with pytest.raises(ValueError, match="1 or 2 bytes"):
        images.to_samples(frame, 4)


def test_mask_bounds():
    mask = np.zeros((4, 5), dtype=np.float32)
    assert images.mask_bounds(mask) is None
    mask[1, 2] = 1.0
    mask[2, 3] = 0.3
    assert images.mask_bounds(mask) == (2, 1, 2, 2)
    # Values that round to 0 in the 8-bit mask select nothing.
    assert images.mask_bounds(np.full((2, 2), 0.001)) is None


def test_mask_to_gray():
    assert images.mask_to_gray(np.array([[1.0, 0.0, 0.5]])) == bytes([255, 0, 128])


def test_pam_round_trip(tmp_path):
    path = str(tmp_path / "image.pam")
    data = bytes(range(18))
    images.write_pam(path, 3, 2, 3, 1, data)
    assert (
        open(path, "rb")
        .read()
        .startswith(b"P7\nWIDTH 3\nHEIGHT 2\nDEPTH 3\nMAXVAL 255\nTUPLTYPE RGB\nENDHDR\n")
    )
    assert images.read_pam(path) == (3, 2, 3, 1, data)

    images.write_pam(path, 1, 1, 4, 2, bytes(8))
    assert images.read_pam(path) == (1, 1, 4, 2, bytes(8))


def test_pam_comments_are_skipped(tmp_path):
    path = tmp_path / "image.pam"
    path.write_bytes(
        b"P7\n# a comment\nWIDTH 1\nHEIGHT 1\nDEPTH 2\nMAXVAL 255\n"
        b"TUPLTYPE GRAYSCALE_ALPHA\nENDHDR\n\x10\x20"
    )
    assert images.read_pam(str(path)) == (1, 1, 2, 1, b"\x10\x20")


@pytest.mark.parametrize(
    "content, message",
    [
        (b"P6\n1 1\n255\n\0\0\0", "not a PAM"),
        (b"P7\nWIDTH 1\n", "no PAM header end"),
        (b"P7\nWIDTH 1\nENDHDR\n", "incomplete"),
        (b"P7\nWIDTH 1\nHEIGHT 1\nDEPTH 4\nMAXVAL 255\nENDHDR\n\0", "expected 4"),
        (b"P7\nWIDTH 0\nHEIGHT 1\nDEPTH 3\nMAXVAL 255\nENDHDR\n", "cannot be read"),
        (b"P7\nWIDTH 1\nHEIGHT 1\nDEPTH 7\nMAXVAL 255\nENDHDR\n", "cannot be read"),
        (b"P7\nWIDTH 1\nHEIGHT 1\nDEPTH 1\nMAXVAL 1023\nENDHDR\n\0\0", "MAXVAL 1023"),
    ],
)
def test_broken_pam_files(tmp_path, content, message):
    path = tmp_path / "broken.pam"
    path.write_bytes(content)
    with pytest.raises(ValueError, match=message):
        images.read_pam(str(path))


def test_mask_is_a_pgm(tmp_path):
    path = tmp_path / "mask.pgm"
    images.write_pgm(str(path), 2, 1, b"\xff\x00")
    assert path.read_bytes() == b"P5\n2 1\n255\n\xff\x00"
