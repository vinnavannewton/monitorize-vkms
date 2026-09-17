"""monitorize-vkms: standalone virtual display tool."""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    """Read version from the VERSION file shipped alongside the package."""
    for candidate in (
        Path(__file__).resolve().parent.parent / "VERSION",
        Path("/usr/lib/monitorize-vkms/VERSION"),
    ):
        try:
            return candidate.read_text().strip()
        except OSError:
            continue
    return "0.0.0"


__version__ = _read_version()
