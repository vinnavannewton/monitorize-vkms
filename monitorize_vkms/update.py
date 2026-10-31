"""Update a user-owned source checkout, then invoke the existing installer."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


SOURCE_RECORD = Path("/usr/lib/monitorize-vkms/source-checkout")
OFFICIAL_REMOTES = {
    "https://github.com/vinnavannewton/monitorize-vkms",
    "https://github.com/vinnavannewton/monitorize-vkms.git",
    "git@github.com:vinnavannewton/monitorize-vkms.git",
    "ssh://git@github.com/vinnavannewton/monitorize-vkms.git",
}


def update(source: str | None = None, *, reinstall: bool = False, uninstall: bool = False) -> int:
    """Keep Git unprivileged and elevate only the installer."""
    command = "uninstall" if uninstall else "reinstall" if reinstall else "update"
    script = "uninstall.sh" if uninstall else "install.sh"
    try:
        if os.geteuid() == 0:
            raise ValueError(f"Run monitorize-vkms {command} as your normal user, without sudo. "
                             "Only the installer will request sudo.")
        git = shutil.which("git")
        sudo = shutil.which("sudo")
        if not git or not sudo:
            raise ValueError("git and sudo are required. Install them with your system package manager.")
        if source is None:
            if not SOURCE_RECORD.is_file():
                raise ValueError("No source checkout was recorded by this installation. "
                                 f"Run: monitorize-vkms {command} --source /path/to/monitorize-vkms")
            source = SOURCE_RECORD.read_text().rstrip("\n")
        checkout = Path(source).expanduser().resolve(strict=True)
        if not checkout.is_dir() or checkout.stat().st_uid != os.geteuid():
            raise ValueError("The source checkout must be a directory owned by your user.")

        def inspect(*args: str) -> str:
            return subprocess.run(
                [git, "-C", str(checkout), *args], check=True,
                capture_output=True, text=True, timeout=15,
            ).stdout.strip()

        if Path(inspect("rev-parse", "--show-toplevel")).resolve() != checkout:
            raise ValueError("The source path must be the root of the Git checkout.")
        if inspect("remote", "get-url", "origin") not in OFFICIAL_REMOTES:
            raise ValueError("origin must point to the official vinnavannewton/monitorize-vkms repository.")
        if inspect("branch", "--show-current") != "main":
            raise ValueError("Switch the source checkout to main before updating.")
        if inspect("status", "--porcelain", "--untracked-files=normal"):
            raise ValueError("The source checkout has local changes or untracked files. "
                             "Save them before updating; nothing has been discarded.")
        if not (checkout / script).is_file():
            raise ValueError(f"The source checkout is missing {script}.")

        if not uninstall:
            print("If updating your system/kernel too, update and reboot first, then run monitorize-vkms reinstall.\n"
              "Matching headers are required for the running kernel; the installer checks them.\n"
              "System updates and reboots remain manual.", flush=True)
        if not reinstall and not uninstall:
            print(f"Updating source in {checkout}...", flush=True)
            # Fetch separately so local commits can be rejected even when a pull
            # would consider a locally-ahead branch already up to date.
            subprocess.run([git, "-C", str(checkout), "fetch", "origin", "main"],
                           check=True, timeout=120)
            if inspect("rev-list", "--count", "FETCH_HEAD..HEAD") != "0":
                raise ValueError("The checkout contains local commits. Resolve them before updating.")
            if inspect("rev-parse", "HEAD") == inspect("rev-parse", "FETCH_HEAD"):
                print("Source is already up to date; verifying/reinstalling installed files.", flush=True)
            # Equivalent to git pull --ff-only, integrating the checked commit.
            subprocess.run([git, "-C", str(checkout), "merge", "--ff-only", "FETCH_HEAD"],
                           check=True, timeout=30)
        print(f"Running {script} with sudo...", flush=True)
        result = subprocess.run([sudo, "--", str(checkout / script)], cwd=checkout)
        if result.returncode:
            print("Uninstall failed. Resolve the reported error before retrying." if uninstall else
                  f"{command.capitalize()} failed. Resolve the reported error and run monitorize-vkms reinstall to retry installation.", file=sys.stderr)
            return result.returncode if result.returncode > 0 else 1
        if uninstall:
            print('Monitorize VKMS uninstalled. Reboot to finish removal of the running module.')
            return 0
        print(f"Monitorize VKMS {'reinstalled' if reinstall else 'updated'}. "
              "Reboot to activate the installed module.")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"{command.capitalize()} stopped: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\n{command.capitalize()} interrupted. Check the output above before retrying.", file=sys.stderr)
        return 130
