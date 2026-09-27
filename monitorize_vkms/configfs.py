"""Names for the Monitorize-owned VKMS configfs namespace."""

from pathlib import Path


CONFIGFS_SUBSYSTEM = "monitorize-vkms"
CONFIGFS_MOUNT = Path("/sys/kernel/config")
CONFIGFS_INSTANCE = CONFIGFS_MOUNT / CONFIGFS_SUBSYSTEM / "monitorize"
