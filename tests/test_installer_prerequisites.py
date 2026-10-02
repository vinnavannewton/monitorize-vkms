"""Exercise prerequisite checks without running the installer or changing the host."""

from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "install.sh").read_text()


def function(name):
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}", SOURCE, re.M | re.S)
    assert match is not None
    return match.group()


class InstallerPrerequisitesTest(unittest.TestCase):
    def run_check(self, *, absent=(), headers=True, efi=False, distro="arch"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build = root / "build"
            build.mkdir()
            if headers:
                (build / "Makefile").touch()
            efi_path = root / "efi"
            if efi:
                efi_path.mkdir()
            check = function("check_prerequisites").replace(
                "/sys/firmware/efi", shlex.quote(str(efi_path))
            )
            script = f"""
                set -euo pipefail
                KERNEL=test-kernel
                KERNEL_BUILD_DIR={shlex.quote(str(build))}
                DISTRO_FAMILY={shlex.quote(distro)}
                log() {{ :; }}
                die() {{ printf '%s\\n' "$*" >&2; exit 1; }}
                command() {{
                    if [[ $1 == -v ]]; then
                        case " $ABSENT " in *" $2 "*) return 1 ;; esac
                        return 0
                    fi
                    builtin command "$@"
                }}
                {function('print_dependency_help')}
                {check}
                check_prerequisites
            """
            return subprocess.run(
                ["/bin/bash", "-c", script],
                env={"ABSENT": " ".join(absent)}, capture_output=True, text=True,
                check=False,
            )

    def test_missing_commands(self):
        for missing in ("dkms", "gcc", "make", "python3", "pkexec", "systemctl", "modprobe"):
            with self.subTest(missing=missing):
                result = self.run_check(absent=(missing,))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Missing prerequisites: {missing}", result.stderr)
                self.assertIn("before any system change", result.stderr)

    def test_missing_kernel_build_makefile(self):
        result = self.run_check(headers=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("build/Makefile", result.stderr)

    def test_efi_requires_mokutil(self):
        result = self.run_check(efi=True, absent=("mokutil",))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mokutil (required on EFI systems)", result.stderr)

    def test_non_efi_does_not_require_mokutil(self):
        self.assertEqual(self.run_check(absent=("mokutil",)).returncode, 0)

    def test_distro_hints_and_unknown_family(self):
        hints = {
            "arch": "linux-lts-headers",
            "fedora": "kernel-devel-test-kernel",
            "debian": "linux-headers-test-kernel",
            "suse": "kernel-default-devel",
        }
        for distro, hint in hints.items():
            with self.subTest(distro=distro):
                result = self.run_check(absent=("dkms",), distro=distro)
                self.assertIn(hint, result.stderr)
                self.assertIn("never invoke a package manager", result.stderr)
        result = self.run_check(absent=("dkms",), distro="unknown")
        self.assertIn("Install the missing prerequisites yourself", result.stderr)
        self.assertNotIn("sudo apt", result.stderr)


if __name__ == "__main__":
    unittest.main()
