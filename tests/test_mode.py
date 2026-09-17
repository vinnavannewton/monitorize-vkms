"""Tests for monitorize_vkms.mode parser using standard unittest."""

from __future__ import annotations

import unittest

from monitorize_vkms.mode import parse_mode


class TestModeParser(unittest.TestCase):
    def test_parse_mode_standard(self):
        self.assertEqual(parse_mode("1920x1080"), (1920, 1080, 60.0))
        self.assertEqual(parse_mode("2560x1440"), (2560, 1440, 60.0))
        self.assertEqual(parse_mode("3840x2160"), (3840, 2160, 60.0))

    def test_parse_mode_with_refresh(self):
        self.assertEqual(parse_mode("2340x1080@60"), (2340, 1080, 60.0))
        self.assertEqual(parse_mode("2340x1600@120"), (2340, 1600, 120.0))
        self.assertEqual(parse_mode("1920x1080@144"), (1920, 1080, 144.0))
        self.assertEqual(parse_mode("1920x1080@59.94"), (1920, 1080, 59.94))

    def test_parse_mode_case_and_whitespace(self):
        self.assertEqual(parse_mode("  2340X1080@60  "), (2340, 1080, 60.0))
        self.assertEqual(parse_mode("1920 x 1080 @ 60"), (1920, 1080, 60.0))

    def test_parse_mode_invalid_format(self):
        for invalid in ("invalid", "1920", "1920x", "x1080", "1920x1080@"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_mode(invalid)

    def test_parse_mode_out_of_range(self):
        for out_of_range in (
            "0x1080",
            "5000x1080",
            "1920x0",
            "1920x5000",
            "1920x1080@10",
            "1920x1080@300",
        ):
            with self.subTest(out_of_range=out_of_range):
                with self.assertRaises(ValueError):
                    parse_mode(out_of_range)


if __name__ == "__main__":
    unittest.main()
