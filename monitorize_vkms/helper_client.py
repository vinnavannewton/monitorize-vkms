"""Invoke the monitorize-vkms privileged helper via Polkit."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

from monitorize_vkms.errors import HelperError, PolkitDenied


HELPER_PATH = Path("/usr/libexec/monitorize-vkms/monitorize-vkms-helper")
VALID_OPERATIONS = ("create", "create-custom", "destroy", "status", "capability")


def helper_response(
    operation: str,
    timeout: float = 60.0,
    edid: bytes | None = None,
) -> dict:
    """Call the privileged helper via pkexec and return its JSON response."""
    if operation not in VALID_OPERATIONS:
        raise ValueError(f"Unsupported helper operation: {operation}")
    if operation == "create-custom" and edid is None:
        raise ValueError("create-custom requires an EDID payload.")
    if not HELPER_PATH.is_file() or not os.access(HELPER_PATH, os.X_OK):
        raise HelperError(
            "The monitorize-vkms helper is not installed. "
            "Run: sudo ./install.sh"
        )
    pkexec = shutil.which("pkexec")
    if not pkexec:
        raise HelperError(
            "Polkit (pkexec) is required. Install polkit for your distribution."
        )

    command = [pkexec, str(HELPER_PATH), operation]
    if edid is not None:
        command.append(base64.b64encode(edid).decode("ascii"))

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HelperError(
            f"Helper timed out waiting for authorization."
        ) from exc
    except OSError as exc:
        raise HelperError(f"Could not start the helper: {exc}") from exc

    # Parse JSON from last non-empty stdout line
    response = None
    for line in reversed(result.stdout.splitlines()):
        try:
            response = json.loads(line)
            break
        except (TypeError, json.JSONDecodeError):
            continue

    if result.returncode or not isinstance(response, dict) or not response.get("success"):
        detail = ""
        if isinstance(response, dict):
            detail = str(response.get("message") or "")
        detail = detail or result.stderr.strip()
        if result.returncode in (126, 127) or "not authorized" in detail.lower():
            raise PolkitDenied(
                "Administrator authorization is required to manage the virtual display."
            )
        raise HelperError(detail or f"Helper operation '{operation}' failed.")

    return response
