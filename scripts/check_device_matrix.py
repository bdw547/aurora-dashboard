#!/usr/bin/env python3
"""Smoke tests for scripts/device_matrix.py."""

from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import device_matrix


def run_ok(args: list[str]) -> object:
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = device_matrix.main(args)
    assert code == 0, f"{args} exited {code}"
    return json.loads(stdout.getvalue())


def run_fails(args: list[str]) -> None:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        code = device_matrix.main(args)
    assert code != 0, f"{args} unexpectedly passed"


def manifest_data() -> dict:
    return device_matrix.load_manifest()


def test_release_matrix_shape() -> None:
    matrix = run_ok(["release"])
    include = matrix.get("include")
    assert isinstance(include, list) and include

    for entry in include:
        assert set(entry) == {"device", "slug", "chip"}
        assert entry["device"] == entry["slug"]
        assert entry["chip"] in device_matrix.VALID_CHIP_FAMILIES


def test_nightly_matrix_includes_every_manifest_slug() -> None:
    matrix = run_ok(["nightly"])
    assert matrix == {
        "include": [{"slug": slug} for slug in manifest_data()["devices"].keys()]
    }


def test_pr_matrix_includes_every_manifest_slug() -> None:
    matrix = run_ok(["pr"])
    assert matrix == {
        "include": [{"slug": slug} for slug in manifest_data()["devices"].keys()]
    }


def test_release_matrix_includes_every_manifest_slug() -> None:
    matrix = run_ok(["release"])
    assert [entry["slug"] for entry in matrix["include"]] == list(manifest_data()["devices"].keys())


def test_missing_chip_metadata_fails() -> None:
    data = copy.deepcopy(manifest_data())
    first_slug = next(iter(data["devices"]))
    del data["devices"][first_slug]["firmware"]["build"]["chip"]

    with TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "manifest.json"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        run_fails(["--manifest", str(manifest), "release"])


def main() -> int:
    tests = [
        test_release_matrix_shape,
        test_nightly_matrix_includes_every_manifest_slug,
        test_pr_matrix_includes_every_manifest_slug,
        test_release_matrix_includes_every_manifest_slug,
        test_missing_chip_metadata_fails,
    ]
    for test in tests:
        test()
    print("Device matrix checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
