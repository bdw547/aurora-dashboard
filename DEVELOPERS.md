# Developing EspControl

Developer documentation has moved into the topic-based pages under
[`dev-docs/`](dev-docs/README.md).

Start with the [EspControl Developer Reference](dev-docs/README.md), then use the
topic pages for architecture, card types, web configurator work, firmware,
devices, checks, and release-sensitive files.

---

## Aurora dashboard (this fork's active product)

Most of `dev-docs/` documents the **upstream espcontrol button-grid engine**
(`components/espcontrol/`, `common/config/card_contract.json`, `src/webserver/`).
This fork's shipping dashboard is **Aurora** — an independent ESPHome + LVGL rewrite
that does **not** use that engine. If you're working on Aurora, start here instead:

- [`README.md`](README.md) — features, hardware, build/flash, and the web configurator.
- `devices/guition-esp32-p4-jc1060p470/aurora.yaml` — the hand-built firmware base (hardware, fonts, styles, globals, camera, screensaver).
- `aurora-build/configurator/` — the no-code configurator: `serve.py` (local server + flash), `builder.html` (drag-drop page builder), `gen.py` (`layout.json` → `aurora-gen.yaml`).
- **Adding an Aurora card type:** write a `c_<type>()` emitter and register it in the `CTRL` dict in `gen.py` — *not* the `card_contract.json` / `button_grid_<type>.h` recipe the engine docs describe.
