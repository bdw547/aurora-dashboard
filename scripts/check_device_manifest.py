#!/usr/bin/env python3
"""Validate devices/manifest.json before generators consume it."""

from __future__ import annotations

import sys

from device_profiles import DEVICE_MANIFEST, DeviceProfileError, load_manifest_data, rel, validate_manifest_data


def main() -> int:
    try:
        data = load_manifest_data(DEVICE_MANIFEST)
    except DeviceProfileError as exc:
        print(f"ERROR: {exc}")
        return 1

    errors = validate_manifest_data(data)
    if errors:
        print(f"ERROR: {rel(DEVICE_MANIFEST)} failed validation:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"{rel(DEVICE_MANIFEST)} passed validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
