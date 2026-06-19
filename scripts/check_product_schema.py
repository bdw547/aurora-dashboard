#!/usr/bin/env python3
"""Validate EspControl product source files together."""

from __future__ import annotations

import sys

from product_schema import ProductSchemaError, validate_product_sources


def main() -> int:
    try:
        results = validate_product_sources()
    except ProductSchemaError as exc:
        print(f"ERROR: {exc}")
        return 1

    failed = False
    for source, errors in results.items():
        if not errors:
            continue
        failed = True
        print(f"ERROR: {source} failed validation:")
        for error in errors:
            print(f"  - {error}")

    if failed:
        return 1

    print("Product schema sources passed validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
