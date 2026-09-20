"""Bounded output-management transactions using wlr-randr's protocol client."""

import json
import shutil
import subprocess
import time

from .errors import CompositorError


def set_enabled(name, enabled, timeout=5.0):
    """Change only the named head and require an explicit confirmed state.

    wlr-randr waits for the configuration succeeded/failed/cancelled event.
    A fresh query additionally checks that the compositor reports the change.
    This acknowledges output state, not completion of internal renderer cleanup.
    """
    executable = shutil.which("wlr-randr")
    if not executable:
        raise CompositorError("Install wlr-randr with --json support for safe Wayland output management.")
    deadline = time.monotonic() + timeout

    def run(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CompositorError(f"Timed out waiting for {name} enabled={enabled}.")
        try:
            result = subprocess.run(
                [executable, *args], capture_output=True, text=True,
                timeout=remaining, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise CompositorError(f"Output-management request failed: {exc}") from exc
        if result.returncode:
            raise CompositorError(
                "Output management unavailable or rejected: "
                + (result.stderr.strip() or result.stdout.strip() or str(result.returncode))
            )
        return result.stdout

    def snapshot():
        try:
            heads = json.loads(run("--json"))
        except ValueError as exc:
            raise CompositorError("wlr-randr did not return valid JSON output state.") from exc
        if not isinstance(heads, list) or any(
            not isinstance(head, dict) or not isinstance(head.get("name"), str)
            or type(head.get("enabled")) is not bool for head in heads
        ):
            raise CompositorError("wlr-randr returned an invalid output state.")
        matches = [head for head in heads if head["name"] == name]
        if len(matches) != 1:
            raise CompositorError(f"Cannot uniquely identify {name} in compositor output state.")
        return heads, matches[0]

    heads, target = snapshot()
    if target["enabled"] is enabled:
        return True
    if not enabled and not any(h["name"] != name and h["enabled"] for h in heads):
        raise CompositorError("Refusing to disable the compositor's last active output.")
    run("--output", name, "--on" if enabled else "--off")
    while True:
        _heads, target = snapshot()
        if target["enabled"] is enabled:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CompositorError(f"Timed out confirming {name} enabled={enabled}.")
        time.sleep(min(0.1, remaining))
