#!/usr/bin/env python3
"""Validate a peppy_remote tree is Android-capable before packing.

Exit codes:
  0 = pass
  1 = fail (messages on stderr)
  2 = usage error
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional


REQUIRED_LIB = (
    "peppy_common.py",
    "peppy_version.py",
    "peppy_network.py",
    "peppy_persist.py",
    "peppy_receivers.py",
    "peppy_spectrum.py",
    "peppy_smb.py",
    "peppy_asset.py",
    "peppy_wizard_cli.py",
    "peppy_wizard_gui.py",
)

REQUIRED_HANDLERS = (
    "volumio_peppymeter.py",
    "volumio_configfileparser.py",
)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def parse_client_version(common_py: str) -> Optional[str]:
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', common_py, re.M)
    return m.group(1) if m else None


def validate_tree(root: str, expect_screensaver_version: Optional[str] = None) -> List[str]:
    """Return list of error strings; empty means OK."""
    errors: List[str] = []
    root = os.path.abspath(root)

    entry = os.path.join(root, "peppy_remote.py")
    if not os.path.isfile(entry):
        errors.append(f"Missing peppy_remote.py under {root}")
        return errors

    lib_dir = os.path.join(root, "lib")
    common = os.path.join(lib_dir, "peppy_common.py")
    if not os.path.isfile(common):
        errors.append(
            "Missing lib/peppy_common.py — this tree is not a modular remote client."
        )
        return errors

    common_txt = _read_text(common)
    if "def is_android(" not in common_txt:
        errors.append(
            "This client cannot run on Android (no is_android() support). "
            "Choose GitHub source in get-android, or upgrade the remote first."
        )

    for name in REQUIRED_LIB:
        if not os.path.isfile(os.path.join(lib_dir, name)):
            errors.append(f"Missing lib/{name}")

    ss = os.path.join(root, "screensaver")
    if not os.path.isdir(ss):
        errors.append("Missing screensaver/ directory (handlers + engines).")
        return errors

    for name in REQUIRED_HANDLERS:
        if not os.path.isfile(os.path.join(ss, name)):
            errors.append(f"Missing screensaver/{name}")

    if not os.path.isdir(os.path.join(ss, "peppymeter")):
        errors.append("Missing screensaver/peppymeter/ (PeppyMeter engine).")
    if not os.path.isdir(os.path.join(ss, "spectrum")):
        errors.append("Missing screensaver/spectrum/ (PeppySpectrum engine).")

    client_ver = parse_client_version(common_txt)
    if expect_screensaver_version and client_ver:
        # Fail closed on major.minor mismatch; patch may differ
        def maj_min(v: str):
            parts = v.strip().split(".")
            if len(parts) < 2:
                return None
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                return None

        a, b = maj_min(client_ver), maj_min(expect_screensaver_version)
        if a and b and a != b:
            errors.append(
                f"Version footlock mismatch: client {client_ver} vs "
                f"screensaver ref {expect_screensaver_version} "
                f"(major.minor must match)."
            )

    return errors


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Validate peppy_remote tree for Android pack")
    p.add_argument("root", help="Path to peppy_remote tree root")
    p.add_argument(
        "--expect-version",
        default=None,
        help="Optional screensaver/client semver to footlock-check (major.minor)",
    )
    p.add_argument(
        "--print-version",
        action="store_true",
        help="On success, print client __version__ to stdout",
    )
    args = p.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"ERROR: not a directory: {args.root}", file=sys.stderr)
        return 2

    errors = validate_tree(args.root, expect_screensaver_version=args.expect_version)
    if errors:
        print("VALIDATION FAILED — will not pack this tree for Android:", file=sys.stderr)
        for e in errors:
            print(f"  • {e}", file=sys.stderr)
        print(
            "\nFix: run get-android with GitHub source, or upgrade/repair the local install.",
            file=sys.stderr,
        )
        return 1

    if args.print_version:
        common = os.path.join(args.root, "lib", "peppy_common.py")
        ver = parse_client_version(_read_text(common)) or "unknown"
        print(ver)
    else:
        print(f"OK: Android-capable tree at {os.path.abspath(args.root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
