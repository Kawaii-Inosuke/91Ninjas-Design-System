"""Image comparison for render tests."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def mismatch(a: Path, b: Path, *, blur: float = 1.0, threshold: int = 48, diff_out: Path | None = None) -> float:
    """Percent of pixels that differ by more than `threshold` (0-255) after a light blur.
    The blur absorbs anti-aliasing differences between renderers; real shifts still show."""
    ia, ib = (Image.open(p).convert("RGB") for p in (a, b))
    assert ia.size == ib.size, f"size differs: {ia.size} vs {ib.size}"
    A, B = (np.asarray(i.filter(ImageFilter.GaussianBlur(blur)), dtype=int) for i in (ia, ib))
    d = np.abs(A - B).max(axis=2)
    if diff_out:
        base = (np.asarray(ia, dtype=int) * 0.3 + 178).astype("uint8")
        Image.fromarray(np.where(d[..., None] > threshold, [255, 0, 0], base).astype("uint8")).save(diff_out)
    return float((d > threshold).mean() * 100)
