"""
ssim.py — SSIM Visual Regression Module

Compares baseline vs post-patch screenshots using Structural Similarity Index.
Returns regression metrics consumed by the Sandbox Validator orchestrator.
"""

from skimage.metrics import structural_similarity as ssim
from PIL import Image, ImageChops
import numpy as np
import os
import uuid


# SSIM below this threshold flags visual regression (<5% FPR on clean patches)
SSIM_THRESHOLD = 0.98

# Pixel difference intensity threshold (10/255 ≈ 0.039)
PIXEL_DIFF_THRESHOLD = 10


def compute_ssim(baseline_path: str, current_path: str, event_id: str | None = None) -> dict:
    """
    Compare two screenshots using SSIM.

    Args:
        baseline_path: Path to the baseline (pre-patch) screenshot.
        current_path: Path to the current (post-patch) screenshot.
        event_id: Optional event ID for naming the diff image.

    Returns:
        dict with keys:
            ssim_score (float): SSIM value 0–1 (1 = identical).
            visual_diff_pct (float): Fraction of pixels exceeding diff threshold.
            diff_image_path (str): Path to saved diff overlay image.
            visual_regression (bool): True if ssim_score < SSIM_THRESHOLD.
    """
    baseline_img = Image.open(baseline_path).convert("RGB")
    current_img = Image.open(current_path).convert("RGB")

    # Resize current to match baseline if dimensions differ
    if baseline_img.size != current_img.size:
        current_img = current_img.resize(baseline_img.size, Image.LANCZOS)

    baseline_gray = np.array(baseline_img.convert("L"))
    current_gray = np.array(current_img.convert("L"))

    # Compute SSIM
    ssim_score = float(ssim(baseline_gray, current_gray))

    # Compute pixel-level diff percentage
    diff_img = ImageChops.difference(baseline_img, current_img)
    diff_array = np.array(diff_img)
    # A pixel is "changed" if ANY channel exceeds the threshold
    changed_pixels = np.any(diff_array > PIXEL_DIFF_THRESHOLD, axis=2)
    total_pixels = changed_pixels.size
    visual_diff_pct = float(np.sum(changed_pixels) / total_pixels)

    # Save diff overlay image
    eid = event_id or uuid.uuid4().hex[:12]
    diff_image_path = os.path.join("/tmp", f"ssim-diff-{eid}.png")
    diff_img.save(diff_image_path)

    return {
        "ssim_score": round(ssim_score, 6),
        "visual_diff_pct": round(visual_diff_pct, 6),
        "diff_image_path": diff_image_path,
        "visual_regression": ssim_score < SSIM_THRESHOLD,
    }
