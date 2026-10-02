"""Check threshold math and accounting before interpreting OCR experiments."""

import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from binarization_experiment import (
    otsu_threshold,
    threshold_image,
    condition,
    overlaps,
    summarize,
)


class BinarizationTests(unittest.TestCase):
    def test_otsu_separates_two_intensity_modes_including_threshold_bin(self):
        a = np.array([[80, 80, 220, 220]], dtype=np.uint8)
        self.assertTrue(80 < otsu_threshold(a) < 220)
        b, _ = threshold_image(Image.fromarray(a), "otsu")
        self.assertEqual(np.asarray(b).tolist(), [[0, 0, 255, 255]])

    def test_constant_background_does_not_become_ink(self):
        for method in ["otsu", "sauvola-61", "sauvola-91"]:
            b, _ = threshold_image(Image.new("L", (100, 50), 240), method)
            self.assertTrue(np.all(np.asarray(b) == 255))

    def test_sauvola_uses_local_contrast_and_records_parameters(self):
        a = np.full((101, 101), 230, dtype=np.uint8)
        a[45:55, 45:55] = 110
        b, p = threshold_image(Image.fromarray(a), "sauvola-61")
        self.assertEqual(np.asarray(b)[50, 50], 0)
        self.assertEqual(np.asarray(b)[0, 0], 255)
        self.assertEqual(p, dict(window=61, k=0.2, r=127.5, border="reflect"))

    def test_fading_is_deterministic_and_does_not_mutate_input(self):
        image = Image.new("L", (50, 50), 100)
        before = image.tobytes()
        self.assertEqual(
            condition(image, "faded-uneven").tobytes(),
            condition(image, "faded-uneven").tobytes(),
        )
        self.assertEqual(image.tobytes(), before)
        self.assertGreater(
            condition(image, "faded-uneven").getpixel((49, 0)),
            condition(image, "faded-uneven").getpixel((0, 0)),
        )

    def test_target_match_requires_majority_of_full_marker_not_one_digit(self):
        self.assertTrue(overlaps((10, 10, 30, 30), (10, 10, 30, 30)))
        self.assertFalse(overlaps((10, 10, 19, 30), (10, 10, 30, 30)))

    def test_summary_separates_target_readings_from_control_noise(self):
        results = [
            dict(
                condition="original",
                method="otsu",
                expected=6,
                detected=True,
                expected_read=False,
                oracle_readings=["6"],
                extra_numeric_regions=1,
            ),
            dict(
                condition="original",
                method="otsu",
                expected=None,
                detected=False,
                expected_read=False,
                oracle_readings=[],
                extra_numeric_regions=2,
            ),
        ]
        result = next(
            r
            for r in summarize(results)
            if r["condition"] == "original" and r["method"] == "otsu"
        )
        self.assertEqual(
            result,
            dict(
                condition="original",
                method="otsu",
                positives=1,
                detected=1,
                expected_read=0,
                oracle_expected_read=1,
                controls=1,
                controls_with_numeric=1,
                extra_numeric_regions=3,
            ),
        )


if __name__ == "__main__":
    unittest.main()
