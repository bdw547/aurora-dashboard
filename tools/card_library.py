#!/usr/bin/env python3
"""Generate the Aurora card/size screenshot library.

This uses the builder catalog as the source of truth, creates isolated LVGL
host pages for every catalog span, and writes a manifest used by the web
preview capture step.  The live configurator layout is never modified.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "aurora-build" / "configurator"
GEN_PATH = CONFIG / "gen.py"
BUILDER = CONFIG / "builder.html"
OUT = ROOT / "artifacts" / "card-library"
FIXTURE = OUT / "fixture-layout.json"
HOST_YAML = OUT / "aurora-emul.yaml"
MANIFEST = OUT / "manifest.json"
INDEX = OUT / "index.html"
INDEX_TEMPLATE = ROOT / "tools" / "card_library_index.html"


def catalog() -> dict:
    """Evaluate the JS catalog exactly as the builder does."""
    js = r'''
const fs = require("fs"), vm = require("vm");
const html = fs.readFileSync(process.argv[1], "utf8");
const start = html.indexOf("const V=");
const end = html.indexOf("const CATS");
const ctx = {};
vm.runInNewContext(html.slice(start, end) + "; globalThis.result = C;", ctx);
process.stdout.write(JSON.stringify(ctx.result));
'''
    result = subprocess.run(
        ["node", "-e", js, str(BUILDER)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def load_generator():
    spec = importlib.util.spec_from_file_location("aurora_gen", GEN_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


ENTITY_BY_DOMAIN = {
    "light": "light.demo_living_room",
    "switch": "switch.demo_outlet",
    "fan": "fan.demo_ceiling",
    "cover": "cover.demo_blinds",
    "climate": "climate.demo_thermostat",
    "media_player": "media_player.demo_spotify",
    "lock": "lock.demo_front_door",
    "camera": "camera.demo_front_door",
    "alarm_control_panel": "alarm_control_panel.demo_alarm",
    "binary_sensor": "binary_sensor.demo_motion",
    "sensor": "sensor.demo_temperature",
    "weather": "weather.forecast_home",
    "person": "person.demo",
    "vacuum": "vacuum.demo_robot",
    "scene": "scene.demo_evening",
    "script": "script.demo_scene",
}

MULTI_ENTITIES = [
    "light.demo_living_room",
    "light.demo_kitchen",
    "light.demo_office",
    "switch.demo_outlet",
    "sensor.demo_temperature",
    "binary_sensor.demo_motion",
]


def sample_card(ck: str, definition: dict, w: int, h: int, index: int) -> dict:
    domain = definition.get("domain", "")
    entity = ENTITY_BY_DOMAIN.get(domain, "")
    card = {
        "id": f"library_{index:04d}_{ck}_{w}x{h}",
        "ck": ck,
        "name": definition.get("label", ck),
        "x": 1,
        "y": 1,
        "w": w,
        "h": h,
    }
    if entity:
        card["entity"] = entity
    if definition.get("multi"):
        card["entities"] = MULTI_ENTITIES[: max(3, min(6, w * h))]
    if ck == "shortcuts":
        card["shortcuts"] = [
            {"label": "Living Room", "icon": "sofa", "target": "page:library"},
            {"label": "Kitchen", "icon": "silverware-fork-knife", "target": "page:library"},
            {"label": "Office", "icon": "desk", "target": "page:library"},
            {"label": "Garage", "icon": "garage", "target": "page:library"},
            {"label": "Weather", "icon": "weather-partly-cloudy", "target": "page:library"},
            {"label": "Music", "icon": "spotify", "target": "page:library"},
        ]
    if ck == "light":
        card["rgb"] = True
        card["icon"] = "lightbulb"
    if ck in {"tv_sources", "sonos_sources"}:
        card["entities"] = [
            "media_player.demo_tv",
            "HDMI 1",
            "Nintendo Switch",
            "Spotify",
            "YouTube",
            "Plex",
        ]
    if ck == "tv_apps":
        card["sources"] = ["Netflix", "YouTube", "Plex", "Spotify", "Prime Video"]
    if ck in {"playlist", "sonos_fav"}:
        card["playlist"] = "Demo Favorites"
    if ck in {"tv_app"}:
        card["source"] = "Netflix"
    if ck == "camera":
        card["entity"] = "camera.demo_front_door"
    if ck in {"chart", "sensor", "binary", "person", "vacuum"}:
        card["unit"] = "°C" if ck == "sensor" else ""
    return card


def fixture_layout(catalog_data: dict) -> tuple[dict, list[dict]]:
    pages = {}
    records = []
    index = 0
    for ck, definition in catalog_data.items():
        for w, h in definition.get("spans", []):
            index += 1
            card = sample_card(ck, definition, w, h, index)
            key = "library" if index == 1 else f"library_{index:04d}_{ck}_{w}x{h}"
            pages[key] = {
                "title": f"{definition.get('label', ck)} · {w}×{h}",
                "type": "custom",
                "header": {"on": False},
                "subpages": [[card]],
            }
            records.append({
                "index": index,
                "page": key,
                "ck": ck,
                "label": definition.get("label", ck),
                "w": w,
                "h": h,
                "min": definition.get("min", [1, 1]),
                "id": card["id"],
            })
    layout = {
        "install_room": "Library",
        "nav": [{"id": "library", "icon": "view-grid-outline", "label": "Card library", "page": next(iter(pages))}],
        "pages": pages,
    }
    return layout, records


def inject_capture_markers(host: str, records: list[dict], gen) -> str:
    """Add an authoritative log marker whenever a fixture page is displayed."""
    for record in records:
        page_id = "page_" + gen.slug(record["page"])
        start = host.find(f"    - id: {page_id}\n")
        if start < 0:
            raise RuntimeError(f"could not locate generated page {page_id}")
        end = host.find("\n    - id:", start + 1)
        if end < 0:
            end = len(host)
        block = host[start:end]
        marker = (
            f"CARD_LIBRARY|{record['index']:04d}|{record['ck']}|"
            f"{record['w']}x{record['h']}"
        )
        action = (
            "        - logger.log:\n"
            "            level: WARN\n"
            f'            format: "{marker}"\n'
        )
        if "      on_load:\n" in block:
            block = block.replace("      on_load:\n", "      on_load:\n" + action, 1)
        else:
            line_end = block.find("\n") + 1
            block = block[:line_end] + "      on_load:\n" + action + block[line_end:]
        host = host[:start] + block + host[end:]
    return host


def write_host_yaml(layout: dict, records: list[dict]) -> None:
    gen = load_generator()
    host = gen.host_assemble(layout)
    host = inject_capture_markers(host, records, gen)
    host += """

# Screenshot-library runner: page identity comes from the on_load marker above.
interval:
  - interval: 1s
    startup_delay: 2s
    then:
      - lvgl.page.next:
"""
    OUT.mkdir(parents=True, exist_ok=True)
    HOST_YAML.write_text(
        "# Generated by tools/card_library.py\n" + host,
        encoding="utf-8",
    )


def write_dashboard(records: list[dict]) -> None:
    html = INDEX_TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__COUNT__", str(len(records)))
    html = html.replace("__RECORDS__", json.dumps(records, separators=(",", ":")))
    INDEX.write_text(html, encoding="utf-8")


def main() -> None:
    data, records = fixture_layout(catalog())
    OUT.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    write_host_yaml(data, records)
    MANIFEST.write_text(json.dumps({
        "generated_from": str(BUILDER.relative_to(ROOT)),
        "count": len(records),
        "records": records,
        "emulator_dir": "emulator",
        "web_config_dir": "web-config",
    }, indent=2) + "\n", encoding="utf-8")
    write_dashboard(records)
    print(f"generated {len(records)} card-size fixtures")
    print(f"fixture: {FIXTURE}")
    print(f"host yaml: {HOST_YAML}")
    print(f"dashboard: {INDEX}")


if __name__ == "__main__":
    main()
