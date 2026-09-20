"""Test optional DRM API detection without installing or loading a module."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BackgroundColorProbeTest(unittest.TestCase):
    def run_probe(self, error='', atomic_missing=False):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            kernel = directory / 'kernel'
            kernel.mkdir()
            (kernel / 'Makefile').touch()
            executable = directory / 'make'
            executable.write_text(f'#!{sys.executable}\n' + '''import os, pathlib, sys
module = pathlib.Path(next(arg[2:] for arg in sys.argv if arg.startswith('M=')))
source = (module / 'probe.c').read_text()
if module.name == 'background':
    assert 'module_init(probe_init)' in source
    assert 'drm_crtc_attach_background_color_property(NULL)' in source
    assert all('DRM_ARGB64_GET' + channel in source for channel in 'RGB')
    error = os.environ.get('PROBE_ERROR', '')
else:
    error = "probe.c:7: error: invalid use of undefined type 'struct drm_atomic_commit'" if os.environ.get('ATOMIC_MISSING') else ''
if error:
    print(error, file=sys.stderr)
    sys.exit(1)
''')
            executable.chmod(0o755)
            header = directory / 'features.h'
            header.write_text('previous header\n')
            env = dict(os.environ, PATH=f'{directory}:/usr/bin:/bin', PROBE_ERROR=error,
                       ATOMIC_MISSING='1' if atomic_missing else '')
            result = subprocess.run(['/bin/sh', str(ROOT / 'scripts/probe-drm-atomic-api.sh'),
                                     '--kdir', str(kernel), '--output', str(header)],
                                    env=env, capture_output=True, text=True)
            return result, header.read_text()

    def test_supported_api(self):
        result, header = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('VKMS_OOT_HAS_DRM_BACKGROUND_COLOR 1', header)

    def test_missing_optional_api_uses_fallback(self):
        errors = [
            "probe.c:9: error: implicit declaration of function 'drm_crtc_attach_background_color_property'",
            "probe.c:10: error: 'struct drm_crtc_state' has no member named 'background_color'",
            "probe.c:10: error: implicit declaration of function 'DRM_ARGB64_GETR'",
            'ERROR: modpost: "drm_crtc_attach_background_color_property" [probe.ko] undefined!',
        ]
        for error in errors:
            with self.subTest(error=error):
                result, header = self.run_probe(error)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('VKMS_OOT_HAS_DRM_BACKGROUND_COLOR 0', header)

    def test_unrelated_build_failure_is_not_hidden(self):
        result, header = self.run_probe('fatal error: linux/module.h: No such file or directory')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('failed for reasons other than an absent API', result.stderr)
        self.assertEqual(header, 'previous header\n')

    def test_atomic_type_probe_remains_independent(self):
        result, header = self.run_probe(atomic_missing=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('VKMS_OOT_HAS_DRM_ATOMIC_COMMIT 0', header)
        self.assertIn('VKMS_OOT_HAS_DRM_BACKGROUND_COLOR 1', header)


if __name__ == '__main__':
    unittest.main()
