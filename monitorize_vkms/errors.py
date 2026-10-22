"""Error hierarchy for monitorize-vkms."""

from __future__ import annotations


class MonitorizeVkmsError(RuntimeError):
    """Base error for all monitorize-vkms operations."""


class BootstrapNotReady(MonitorizeVkmsError):
    """The persistent VKMS topology has not been bootstrapped."""


class HelperError(MonitorizeVkmsError):
    """The privileged helper failed."""


class ManagedDisplayError(HelperError):
    """The output belongs to the authenticated remote-login coordinator."""


class PolkitDenied(HelperError):
    """Polkit authorization was denied or unavailable."""


class CompositorError(MonitorizeVkmsError):
    """Compositor activation or deactivation failed."""


class EdidError(MonitorizeVkmsError, ValueError):
    """A requested mode cannot be represented safely in a base-block EDID."""
