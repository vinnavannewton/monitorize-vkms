"""Parse WxH[@Hz] mode strings."""

from __future__ import annotations

import re

_MODE_RE = re.compile(
    r"^(\d+)\s*[xX]\s*(\d+)(?:\s*@\s*(\d+(?:\.\d+)?))?\s*$"
)

DEFAULT_REFRESH_HZ = 60.0
MIN_DIMENSION = 1
MAX_DIMENSION = 4095
MIN_REFRESH = 24.0
MAX_REFRESH = 240.0


def parse_mode(mode_string: str) -> tuple[int, int, float]:
    """Parse a mode string like ``2340x1080@60`` into *(width, height, refresh)*.

    The ``@Hz`` part is optional and defaults to 60 Hz.

    Raises:
        ValueError: If the string is malformed or values are out of range.
    """
    match = _MODE_RE.fullmatch(str(mode_string).strip())
    if not match:
        raise ValueError(
            f"Invalid mode string: {mode_string!r}. "
            f"Expected format: WIDTHxHEIGHT or WIDTHxHEIGHT@HZ "
            f"(e.g. 1920x1080, 2340x1080@60)."
        )
    width = int(match.group(1))
    height = int(match.group(2))
    refresh = float(match.group(3)) if match.group(3) else DEFAULT_REFRESH_HZ

    if not MIN_DIMENSION <= width <= MAX_DIMENSION:
        raise ValueError(
            f"Width {width} is out of range ({MIN_DIMENSION}–{MAX_DIMENSION})."
        )
    if not MIN_DIMENSION <= height <= MAX_DIMENSION:
        raise ValueError(
            f"Height {height} is out of range ({MIN_DIMENSION}–{MAX_DIMENSION})."
        )
    if not MIN_REFRESH <= refresh <= MAX_REFRESH:
        raise ValueError(
            f"Refresh rate {refresh} Hz is out of range ({MIN_REFRESH}–{MAX_REFRESH})."
        )
    return width, height, refresh
