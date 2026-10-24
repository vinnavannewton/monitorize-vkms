"""Exercise isolated Python from both installed worker layouts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parent.parent

class WorkerEntrypointTests(unittest.TestCase):
    def test_both_installed_layouts_import_and_execute_worker_with_isolated_python(self):
        if os.geteuid()==0:self.skipTest('Compositor workers intentionally reject root.')
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            package=base/'usr/lib/monitorize-vkms/monitorize_vkms'
            shutil.copytree(ROOT/'monitorize_vkms',package,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            wrapper=base/'usr/libexec/monitorize-vkms/monitorize-vkms-helper'
            wrapper.parent.mkdir(parents=True);shutil.copyfile(package/'helper.py',wrapper)
            # Replace only compositor effects in this fixture. Real helper imports,
            # argument parsing, isolated path resolution and worker dispatch run.
            (package/'compositor.py').write_text("def get_compositor_snapshot(): return 'GNOME', {}\ndef activate_in_compositor(*args): pass\ndef deactivate_in_compositor(*args): pass\ndef detect_compositor(): return 'GNOME'\n")
            for entry in (wrapper,package/'helper.py'):
                with self.subTest(entry=entry):
                    result=subprocess.run([sys.executable,'-I',str(entry),'--worker'],input='{"action":"snapshot"}',text=True,capture_output=True,timeout=5,cwd='/')
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertTrue(json.loads(result.stdout)['success'])
