"""
test_ssim.py — Tests for the SSIM Visual Regression Module

- 30 clean-pair test: JPEG compression artifacts should NOT trigger regression (<5% FPR)
- Deliberate 1px CSS shift should flag as regression
- Identical images should produce perfect score
"""

import os
import tempfile
import pytest
import numpy as np
from PIL import Image

# Allow running from repo root or sandbox-validator/
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ssim import compute_ssim, SSIM_THRESHOLD


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory for test images."""
    with tempfile.TemporaryDirectory() as d:
        yield d


def _make_test_image(width: int = 1280, height: int = 720, seed: int = 42) -> Image.Image:
    """Generate a deterministic test image with varied content (simulates a web page)."""
    rng = np.random.RandomState(seed)
    # Create a base image with blocks of colour (simulates UI elements)
    img = np.zeros((height, width, 3), dtype=np.uint8)
    # Header bar
    img[0:60, :] = [33, 37, 41]
    # Sidebar
    img[60:, 0:240] = [248, 249, 250]
    # Content area with subtle gradients
    for y in range(60, height):
        img[y, 240:] = [255 - (y - 60) // 8 % 30, 250, 252]
    # Random "text" blocks
    for _ in range(20):
        x = rng.randint(260, width - 200)
        y = rng.randint(80, height - 40)
        w = rng.randint(60, 180)
        h = rng.randint(8, 16)
        img[y:y+h, x:x+w] = [50, 50, 50]
    return Image.fromarray(img, "RGB")


def _save_with_jpeg_artefacts(img: Image.Image, path: str, quality: int = 85) -> str:
    """Save image as JPEG (introducing compression artefacts), then reload as PNG."""
    jpeg_path = path.replace(".png", ".jpg")
    img.save(jpeg_path, "JPEG", quality=quality)
    reloaded = Image.open(jpeg_path).convert("RGB")
    reloaded.save(path, "PNG")
    os.remove(jpeg_path)
    return path


class TestSSIMCleanPairs:
    """30 clean-pair test: JPEG artefacts should NOT trigger false positives."""

    def _generate_pair(self, tmp_dir: str, seed: int, quality: int = 85) -> tuple[str, str]:
        """Generate a baseline PNG and a JPEG-artefacted 'current' PNG."""
        base_img = _make_test_image(seed=seed)
        baseline_path = os.path.join(tmp_dir, f"baseline-{seed}.png")
        current_path = os.path.join(tmp_dir, f"current-{seed}.png")
        base_img.save(baseline_path, "PNG")
        _save_with_jpeg_artefacts(base_img, current_path, quality=quality)
        return baseline_path, current_path

    def test_30_clean_pairs_fpr_below_5_percent(self, tmp_dir: str) -> None:
        """False positive rate across 30 clean-pair comparisons must be < 5%."""
        false_positives = 0
        num_pairs = 30

        for i in range(num_pairs):
            # Vary JPEG quality between 75-95 to simulate real-world variance
            quality = 75 + (i * 2) % 21
            baseline_path, current_path = self._generate_pair(tmp_dir, seed=i + 100, quality=quality)
            result = compute_ssim(baseline_path, current_path, event_id=f"clean-{i}")
            if result["visual_regression"]:
                false_positives += 1

        fpr = false_positives / num_pairs
        assert fpr < 0.05, (
            f"False positive rate {fpr:.2%} ({false_positives}/{num_pairs}) "
            f"exceeds 5% threshold"
        )

    def test_clean_pair_ssim_above_threshold(self, tmp_dir: str) -> None:
        """A single clean pair (quality=90) should have SSIM well above threshold."""
        baseline_path, current_path = self._generate_pair(tmp_dir, seed=999, quality=90)
        result = compute_ssim(baseline_path, current_path, event_id="clean-single")
        assert result["ssim_score"] >= SSIM_THRESHOLD
        assert result["visual_regression"] is False


class TestSSIMRegression:
    """Deliberate visual changes must be detected as regression."""

    def test_1px_css_shift_flags_regression(self, tmp_dir: str) -> None:
        """A 1px vertical shift of the content area should be caught."""
        base_img = _make_test_image(seed=42)
        shifted_img = _make_test_image(seed=42)

        # Shift the entire image down by 1px (simulates CSS margin change)
        arr = np.array(shifted_img)
        shifted_arr = np.zeros_like(arr)
        shifted_arr[1:, :, :] = arr[:-1, :, :]
        shifted_img = Image.fromarray(shifted_arr, "RGB")

        baseline_path = os.path.join(tmp_dir, "baseline-shift.png")
        current_path = os.path.join(tmp_dir, "current-shift.png")
        base_img.save(baseline_path, "PNG")
        shifted_img.save(current_path, "PNG")

        result = compute_ssim(baseline_path, current_path, event_id="shift-test")
        assert result["visual_regression"] is True, (
            f"1px shift was not detected: ssim_score={result['ssim_score']}"
        )
        assert result["visual_diff_pct"] > 0, "Diff percentage should be non-zero"

    def test_colour_change_flags_regression(self, tmp_dir: str) -> None:
        """Changing a large UI region's colour should flag regression."""
        base_img = _make_test_image(seed=42)
        modified_img = _make_test_image(seed=42)

        arr = np.array(modified_img)
        # Change header + sidebar + part of content (>30% of image)
        arr[0:60, :] = [255, 0, 0]
        arr[60:, 0:240] = [0, 0, 255]
        arr[60:360, 240:] = [200, 100, 50]
        modified_img = Image.fromarray(arr, "RGB")

        baseline_path = os.path.join(tmp_dir, "baseline-colour.png")
        current_path = os.path.join(tmp_dir, "current-colour.png")
        base_img.save(baseline_path, "PNG")
        modified_img.save(current_path, "PNG")

        result = compute_ssim(baseline_path, current_path, event_id="colour-test")
        assert result["visual_regression"] is True
        assert result["visual_diff_pct"] > 0.01  # At least 1% of pixels changed


class TestSSIMEdgeCases:
    """Edge cases and output validation."""

    def test_identical_images(self, tmp_dir: str) -> None:
        """Identical images should produce SSIM = 1.0 and no regression."""
        img = _make_test_image(seed=77)
        path_a = os.path.join(tmp_dir, "identical-a.png")
        path_b = os.path.join(tmp_dir, "identical-b.png")
        img.save(path_a, "PNG")
        img.save(path_b, "PNG")

        result = compute_ssim(path_a, path_b, event_id="identical")
        assert result["ssim_score"] == 1.0
        assert result["visual_diff_pct"] == 0.0
        assert result["visual_regression"] is False

    def test_diff_image_saved(self, tmp_dir: str) -> None:
        """Diff overlay image should be saved to /tmp/."""
        img = _make_test_image(seed=88)
        path_a = os.path.join(tmp_dir, "diff-a.png")
        path_b = os.path.join(tmp_dir, "diff-b.png")
        img.save(path_a, "PNG")
        img.save(path_b, "PNG")

        result = compute_ssim(path_a, path_b, event_id="difftest")
        assert os.path.exists(result["diff_image_path"])
        assert "ssim-diff-difftest" in result["diff_image_path"]

    def test_output_keys(self, tmp_dir: str) -> None:
        """Output dict must contain all expected keys."""
        img = _make_test_image(seed=11)
        path_a = os.path.join(tmp_dir, "keys-a.png")
        path_b = os.path.join(tmp_dir, "keys-b.png")
        img.save(path_a, "PNG")
        img.save(path_b, "PNG")

        result = compute_ssim(path_a, path_b)
        assert set(result.keys()) == {
            "ssim_score", "visual_diff_pct", "diff_image_path", "visual_regression"
        }

    def test_different_size_images(self, tmp_dir: str) -> None:
        """Images of different sizes should still be compared (current resized to match baseline)."""
        baseline = _make_test_image(width=1280, height=720, seed=42)
        current = _make_test_image(width=1024, height=768, seed=42)

        path_a = os.path.join(tmp_dir, "size-a.png")
        path_b = os.path.join(tmp_dir, "size-b.png")
        baseline.save(path_a, "PNG")
        current.save(path_b, "PNG")

        result = compute_ssim(path_a, path_b, event_id="resize")
        assert 0 <= result["ssim_score"] <= 1
        assert 0 <= result["visual_diff_pct"] <= 1
