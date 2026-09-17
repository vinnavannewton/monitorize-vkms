"""Tests for monitorize_vkms.edid generation and validation using standard unittest."""

from __future__ import annotations

import unittest

from monitorize_vkms.edid import (
    EDID_HEADER,
    EdidError,
    generate_cvt_timing,
    generate_edid,
    parse_preferred_timing,
    parse_range_pixel_clock_mhz,
    validate_edid,
)


class TestEdidGenerator(unittest.TestCase):
    def test_generate_edid_valid_modes(self):
        modes = [
            (1920, 1080, 60.0),
            (2340, 1080, 60.0),
            (2340, 1600, 60.0),
            (2560, 1440, 60.0),
            (1920, 1200, 60.0),
            (1280, 720, 50.0),
            (800, 600, 75.0),
        ]
        for width, height, refresh in modes:
            with self.subTest(mode=f"{width}x{height}@{refresh}"):
                edid = generate_edid(width, height, refresh)
                self.assertEqual(len(edid), 128)
                self.assertEqual(edid[:8], EDID_HEADER)
                self.assertEqual(sum(edid) % 256, 0)
                self.assertEqual(edid[126], 0)  # No extensions

                # Verify descriptor contains MONITORIZE
                self.assertIn(b"MONITORIZE", edid[72:90])

                # Validate range descriptor
                max_mhz = parse_range_pixel_clock_mhz(edid)
                self.assertGreater(max_mhz, 0)

                # Validate DTD round-trip
                timing = parse_preferred_timing(edid)
                self.assertEqual(timing.width, width)
                self.assertEqual(timing.height, height)
                self.assertLess(abs(timing.refresh_hz - refresh), 0.5)

    def test_edid_validation_failures(self):
        valid = generate_edid(1920, 1080, 60.0)

        # Wrong length
        with self.assertRaises(EdidError):
            validate_edid(valid[:127])

        # Wrong header
        bad_header = bytearray(valid)
        bad_header[0] = 0xFF
        bad_header[127] = (-sum(bad_header[:127])) & 0xFF
        with self.assertRaises(EdidError):
            validate_edid(bytes(bad_header))

        # Bad checksum
        corrupt = bytearray(valid)
        corrupt[10] ^= 0x01
        with self.assertRaises(EdidError):
            validate_edid(bytes(corrupt))

        # Extension blocks present
        ext = bytearray(valid)
        ext[126] = 1
        ext[127] = (-sum(ext[:127])) & 0xFF
        with self.assertRaises(EdidError):
            validate_edid(bytes(ext))

    def test_generate_edid_invalid_parameters(self):
        invalid_params = [
            (0, 1080, 60.0),
            (1920, 0, 60.0),
            (5000, 1080, 60.0),
            (1920, 1080, 10.0),
            (1920, 1080, 300.0),
        ]
        for width, height, refresh in invalid_params:
            with self.subTest(param=(width, height, refresh)):
                with self.assertRaises(EdidError):
                    generate_edid(width, height, refresh)

    def test_generate_edid_pixel_clock_limit(self):
        # Modes whose standard CVT timing exceeds 655.35 MHz Detailed Timing limit
        for width, height, refresh in [(3840, 2160, 60.0), (2560, 1440, 144.0)]:
            with self.subTest(mode=f"{width}x{height}@{refresh}"):
                with self.assertRaises(EdidError):
                    generate_edid(width, height, refresh)


if __name__ == "__main__":
    unittest.main()
