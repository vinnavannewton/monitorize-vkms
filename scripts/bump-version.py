#!/usr/bin/env python3
"""Synchronize Monitorize VKMS package versions from VERSION."""

from __future__ import annotations

import os
import re
import stat
import sys
import tempfile
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))?\Z")
MAINTAINER = "vinnavannewton <vinnavannewton@proton.me>"


class BumpError(Exception):
    pass


def field(text: str, relative: str, pattern: str, label: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise BumpError(f"expected exactly one {label} field in {relative}")
    return matches[0]


def replace_field(text: str, match: re.Match[str], new: str) -> str:
    return text[:match.start(2)] + new + text[match.end(2):]


def prepare(new: str, now: datetime) -> tuple[str, dict[str, str]]:
    if not VERSION.fullmatch(new):
        raise BumpError("version must be MAJOR.MINOR or MAJOR.MINOR.PATCH")
    old = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION.fullmatch(old):
        raise BumpError(f"invalid current VERSION: {old!r}")
    if old == new:
        raise BumpError(f"version is already {new}")
    changes = {"VERSION": new + "\n"}
    fields = (
        ("dkms.conf", r'^(PACKAGE_VERSION=")([^"]+)(")$', "PACKAGE_VERSION"),
        ("packaging/rpm/monitorize-vkms.spec", r"^(Version:[ \t]+)(\S+)([ \t]*)$", "Version"),
        ("packaging/arch/PKGBUILD", r"^(pkgver=)([^\s#]+)(.*)$", "pkgver"),
        ("packaging/deb/debian/postrm", r"^(\s*dkms remove -m monitorize-vkms -v )(\S+)( --all[ \t]*)$", "DKMS removal version"),
    )
    for relative, pattern, label in fields:
        content = (ROOT / relative).read_text(encoding="utf-8")
        match = field(content, relative, pattern, label)
        if match.group(2) != old:
            raise BumpError(f"{relative} has {label}={match.group(2)}; expected {old}. Fix the mismatch before bumping.")
        content = replace_field(content, match, new)
        if relative == "dkms.conf":
            # Older checkouts also repeat the release in this comment.
            content = content.replace(f"# DKMS package metadata for monitorize-vkms {old}.",
                                      "# DKMS package metadata for monitorize-vkms.")
        elif relative == "packaging/rpm/monitorize-vkms.spec":
            release = field(content, relative, r"^(Release:[ \t]+)(\S+)([ \t]*)$", "Release")
            if not re.fullmatch(r"[1-9][0-9]*%\{\?dist\}", release.group(2)):
                raise BumpError(f"invalid Release in {relative}: {release.group(2)}")
            content = replace_field(content, release, "1%{?dist}")
        elif relative == "packaging/arch/PKGBUILD":
            release = field(content, relative, r"^(pkgrel=)([^\s#]+)(.*)$", "pkgrel")
            if not re.fullmatch(r"[1-9][0-9]*", release.group(2)):
                raise BumpError(f"invalid pkgrel in {relative}: {release.group(2)}")
            content = replace_field(content, release, "1")
        changes[relative] = content

    relative = "packaging/deb/debian/changelog"
    history = (ROOT / relative).read_text(encoding="utf-8")
    header = re.match(r"monitorize-vkms \(([^)]+)\) (\S+); urgency=(\S+)\n", history)
    if header is None or not re.fullmatch(re.escape(old) + r"-[1-9][0-9]*", header.group(1)):
        raise BumpError(f"{relative} latest entry must match VERSION ({old})")
    entry = (f"monitorize-vkms ({new}-1) {header.group(2)}; urgency={header.group(3)}\n\n"
             f"  * Release Monitorize VKMS {new}.\n\n"
             f" -- {MAINTAINER}  {format_datetime(now)}\n\n")
    changes[relative] = entry + history
    return old, changes


def write_all(changes: dict[str, str]) -> None:
    staged: dict[str, Path] = {}
    originals = {relative: (ROOT / relative).read_bytes() for relative in changes}
    written: list[str] = []
    try:
        for relative, content in changes.items():
            destination = ROOT / relative
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent,
                                             prefix=".bump-version-", delete=False) as temporary:
                staged[relative] = Path(temporary.name)
                temporary.write(content)
            staged[relative].chmod(stat.S_IMODE(destination.stat().st_mode))
        for relative, temporary in staged.items():
            os.replace(temporary, ROOT / relative)
            written.append(relative)
    except OSError:
        for relative in written:
            (ROOT / relative).write_bytes(originals[relative])
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) != 2 or not VERSION.fullmatch(sys.argv[1]):
        print("Usage: ./scripts/bump-version.py NEW_VERSION (MAJOR.MINOR or MAJOR.MINOR.PATCH, for example, 1.1 or 1.0.1)", file=sys.stderr)
        return 2
    try:
        old, changes = prepare(sys.argv[1], datetime.now().astimezone())
        write_all(changes)
    except (BumpError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Monitorize VKMS version bump\n  old: {old}\n  new: {sys.argv[1]}\n\nUpdated:")
    for relative in changes:
        print(f"  {relative}")
    print(f"\nVersion synchronized successfully: {sys.argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
