#!/usr/bin/env python3
"""Capture card-library emulator pages using their on-load identity markers."""

import json
import os
import pty
import re
import select
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "card-library"
MANIFEST = OUT / "manifest.json"
BINARY = OUT / ".esphome" / "build" / "aurora-emul" / ".pioenvs" / "aurora-emul" / "program"
MARKER = re.compile(r"CARD_LIBRARY\|(\d{4})\|([^|]+)\|(\d+)x(\d+)")

def main():
    for command in ("Xvfb", "import", "convert"):
        if not shutil.which(command):
            raise SystemExit(f"missing required command: {command}")
    if not BINARY.exists():
        raise SystemExit(f"compile {OUT / 'aurora-emul.yaml'} first")
    records = {r["index"]: r for r in json.loads(MANIFEST.read_text())["records"]}
    target = OUT / "emulator"
    target.mkdir(parents=True, exist_ok=True)
    focused = OUT / "emulator-focused"
    focused.mkdir(parents=True, exist_ok=True)
    for directory in (target, focused):
        for image in directory.glob("*.png"):
            image.unlink()
    display_number = next(n for n in range(101, 121) if not Path(f"/tmp/.X11-unix/X{n}").exists())
    display = f":{display_number}"
    xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", "1024x600x24", "-ac", "-nolisten", "tcp"])
    emulator = None
    master = None
    seen = set()
    try:
        time.sleep(0.5)
        env = os.environ | {"DISPLAY": display, "SDL_VIDEODRIVER": "x11", "SDL_RENDER_DRIVER": "software"}
        master, slave = pty.openpty()
        emulator = subprocess.Popen(
            [str(BINARY)],
            cwd=OUT,
            env=env,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
        os.close(slave)
        buffer = ""
        deadline = time.monotonic() + 600
        while len(seen) < len(records) and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 1)
            if not ready:
                if emulator.poll() is not None:
                    raise RuntimeError(f"emulator exited with {emulator.returncode}")
                continue
            try:
                buffer += os.read(master, 65536).decode(errors="replace")
            except OSError:
                break
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                match = MARKER.search(line)
                if not match:
                    continue
                index = int(match.group(1))
                if index in seen:
                    continue
                record = records[index]
                expected = (record["ck"], record["w"], record["h"])
                actual = (match.group(2), int(match.group(3)), int(match.group(4)))
                if actual != expected:
                    raise RuntimeError(f"marker mismatch at {index}: {actual} != {expected}")
                time.sleep(0.25)
                name = f"{index:04d}_{record['ck']}_{record['w']}x{record['h']}.png"
                full_image = target / name
                subprocess.run(["import", "-display", display, "-window", "root", str(full_image)], check=True)
                width = record["w"] * 140 + (record["w"] - 1) * 14
                height = record["h"] * 100 + (record["h"] - 1) * 14
                subprocess.run([
                    "convert", str(full_image), "-crop", f"{width}x{height}+94+22",
                    "+repage", str(focused / name),
                ], check=True)
                seen.add(index)
                print(f"[{len(seen):03d}/{len(records)}] {name}", flush=True)
        if len(seen) != len(records):
            missing = sorted(set(records) - seen)
            raise RuntimeError(f"captured {len(seen)} of {len(records)}; missing {missing[:10]}")
    finally:
        if emulator:
            emulator.terminate()
            try:
                emulator.wait(timeout=5)
            except subprocess.TimeoutExpired:
                emulator.kill()
        if master is not None:
            os.close(master)
        xvfb.terminate()
        xvfb.wait(timeout=5)

if __name__ == "__main__":
    main()
