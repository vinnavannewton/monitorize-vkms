"""Tests for monitorize_vkms.cli argument handling and commands using standard unittest."""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from monitorize_vkms.cli import main


class TestCli(unittest.TestCase):
    def test_cli_help(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            with self.assertRaises(SystemExit) as ctx:
                main(["--help"])
            self.assertEqual(ctx.exception.code, 0)
        self.assertIn("Manage standalone Monitorize VKMS virtual displays", f.getvalue())

    def test_cli_version(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            with self.assertRaises(SystemExit) as ctx:
                main(["--version"])
            self.assertEqual(ctx.exception.code, 0)
        self.assertIn("monitorize-vkms", f.getvalue())

    def test_cli_no_args(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main([])
        self.assertEqual(ret, 0)
        self.assertIn("usage: monitorize-vkms", f.getvalue())

    def test_cli_status_json(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main(["status", "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(f.getvalue())
        self.assertTrue(data["success"])
        self.assertIn("version", data)
        self.assertIn("kernel_module", data)
        self.assertIn("topology", data)
        self.assertIn("drm", data)

    def test_cli_list_json(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main(["list", "--json"])
        self.assertEqual(ret, 0)
        data = json.loads(f.getvalue())
        self.assertTrue(data["success"])
        self.assertIn("card", data)
        self.assertIn("connectors", data)
        self.assertIsInstance(data["connectors"], list)

    def test_cli_doctor_json(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main(["doctor", "--json"])
        self.assertIn(ret, (0, 1))
        data = json.loads(f.getvalue())
        self.assertIn("all_passed", data)
        self.assertIn("checks", data)
        self.assertGreaterEqual(len(data["checks"]), 8)

    def test_cli_create_invalid_mode_json(self):
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            ret = main(["create", "not-a-mode", "--json"])
        self.assertEqual(ret, 1)
        data = json.loads(f.getvalue())
        self.assertFalse(data["success"])
        self.assertEqual(data["error_type"], "invalid_mode")


if __name__ == "__main__":
    unittest.main()
