"""Safe COSMIC output transactions through its native protocol client."""

import json
import re
import shutil
import subprocess
import time

from .errors import CompositorError


_OUTPUT = re.compile(r'^output\s+("(?:[^"\\]|\\.)*")\s+enabled=#(true|false)\s*\{')


def _heads(document: str) -> dict[str, bool]:
    """Read only the top-level output identity and enabled flag from COSMIC KDL."""
    heads = {}
    for line in document.splitlines():
        if not line.startswith("output "):
            continue
        match = _OUTPUT.match(line)
        if not match:
            raise CompositorError("COSMIC returned an invalid output state.")
        try:
            name = json.loads(match.group(1))
        except ValueError as exc:
            raise CompositorError("COSMIC returned an invalid output name.") from exc
        if name in heads:
            raise CompositorError(f"COSMIC reported duplicate output {name}.")
        heads[name] = match.group(2) == "true"
    return heads


def set_enabled(name: str, enabled: bool, timeout: float = 5.0) -> bool:
    """Change exactly one COSMIC head after a protocol acknowledgement and fresh query."""
    executable = shutil.which("cosmic-randr")
    if not executable:
        raise CompositorError("COSMIC output management is unavailable: cosmic-randr is missing.")
    deadline = time.monotonic() + timeout

    def run(*args: str) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CompositorError(f"Timed out confirming {name} enabled={enabled} in COSMIC.")
        try:
            result = subprocess.run(
                [executable, *args], capture_output=True, text=True,
                timeout=remaining, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompositorError(f"Timed out waiting for COSMIC output management of {name}.") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise CompositorError(f"COSMIC output management failed for {name}: {exc}") from exc
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
            raise CompositorError(f"COSMIC rejected output management of {name}: {detail}")
        return result.stdout

    def snapshot() -> dict[str, bool]:
        return _heads(run("list", "--kdl"))

    heads = snapshot()
    while name not in heads:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not enabled:
            raise CompositorError(f"Monitorize output {name} was not exposed by COSMIC.")
        time.sleep(min(0.1, remaining))
        heads = snapshot()

    if heads[name] is enabled:
        return True
    if not enabled and not any(state for head, state in heads.items() if head != name):
        raise CompositorError("Refusing to disable COSMIC's last active output.")

    # cosmic-randr waits for the compositor's succeeded/failed/cancelled event.
    run("enable" if enabled else "disable", name)
    while True:
        heads = snapshot()
        if name not in heads:
            raise CompositorError(f"Monitorize output {name} disappeared from COSMIC state.")
        if heads[name] is enabled:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CompositorError(f"Timed out confirming {name} enabled={enabled} in COSMIC.")
        time.sleep(min(0.1, remaining))
