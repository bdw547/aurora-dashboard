# Morning handoff — Aurora dashboard

Worked through the night on three things: (1) the screen-mirroring question, (2) full analysis of your Aurora design + real entities, (3) a grounded implementation plan and the first real build assets. Everything lives in `aurora-build/`.

## TL;DR
- **Screen mirroring (AirPlay/Cast) to the panel: not feasible.** Proprietary licensed protocols + no DIY receiver path. The panel stays a *control* surface (it *can* show HA camera feeds). Full reasoning → `01-airplay-cast-verdict.md`.
- **Your Aurora design is fully understood** — all 6 screens (Home, Lights, Climate, Media, Security, Network), exact tokens, and mapped to your **real** HA entities → `03-entity-bindings.md`.
- **🎉 A real, flashable custom firmware now exists and PASSES `esphome config`:**
  `devices/guition-esp32-p4-jc1060p470/aurora.yaml`. It's **standalone** (stock ESPHome only — no espcontrol coupling): ESP32-P4 + C6 hosted WiFi + `mipi_dsi` JC1060P470 display + GT911 touch + `esp_ldo` + baked aurora background + LVGL nav rail with **6 pages** (Home, Lights, Climate, Media, Security, Network). Live-wired to your real entities so far:
  - **Lights** → `light.living_room_main` (brightness arc, toggle, HA state sync).
  - **Media** → `media_player.living_room` Sonos (title/artist labels, prev/play/next, volume), zone buttons (Kitchen/Dining-Juke), and a **webOS TV remote** (`media_player.lg_g3_living_room_2`: D-pad/OK, vol, Netflix) via `webostv.button`.
  - **Security** → `lock.front_door`/`lock.back_door` (Lock/Unlock) + `person.ben` presence.
  - Climate + Network are titled stubs (Climate has no HA entity; Network bindings are next).
  - Remaining polish: full 8-light list, Sora font (gfonts), proper Sonos grouping (join/unjoin vs the v1 play/pause), MDI icon glyphs for nav/controls.
- Also delivered: blueprint (`02-implementation-blueprint.md`), baked bg (`assets/aurora_bg_1024x600.png`), and a richer Lights reference (`aurora_lights.reference.yaml`).
- **Stock espcontrol is flashed and working** (you confirmed) — hardware/WiFi/HA path proven. ✅

## How to flash the Aurora firmware
This validates today; to put it on the panel:
1. Real WiFi creds are already in `devices/guition-esp32-p4-jc1060p470/secrets.yaml` (confirm they're yours).
2. Compile (first build pulls ESP-IDF, ~15-40 min): `cd ~/espcontrol && source .venv/bin/activate && esphome compile devices/guition-esp32-p4-jc1060p470/aurora.yaml`
3. Browser-flash the resulting factory `.bin` once via [esptool-js](https://espressif.github.io/esptool-js/) (usbipd kept failing). Then **OTA** for every update after: `esphome run devices/guition-esp32-p4-jc1060p470/aurora.yaml --device <panel-ip>`.

## What I did NOT do, and why
I did not produce a finished, compiled, flashed Aurora firmware. Honest reasons:
1. **Can't flash unattended** — the first custom flash needs you at the PC (browser/esptool-js), and visual correctness can only be judged on the glass.
2. **Display coupling** — the panel's display/touch/LVGL live *inside* espcontrol's C++ component (no stock `display:`/`lvgl:` to point at), so the custom UI must either fork espcontrol's LVGL layer or be rebuilt standalone. That's an architecture decision (below) best made with you, and iterated on hardware.
Building ~6 richly-styled screens blind and wrong would waste more of your time than a precise blueprint + validated assets + a head-start scaffold. So that's what I made.

## Decisions I need from you (batched, as requested)
1. ~~**Architecture:** Route A vs B.~~ **RESOLVED** — Route B (clean standalone) turned out fully feasible: the display is stock `mipi_dsi`/`JC1060P470` and WiFi is stock `esp32_hosted`, so `aurora.yaml` reuses the proven hardware with zero espcontrol coupling and already validates. We'll continue on Route B.
2. **Climate screen:** your HA export has **no `climate.` entity** (the design noted this too). Add the ecobee integration so a `climate.*` exists, drop Climate for v1, or make it read-only? 
3. **Scenes:** only `scene.smart_bridge_2_front_exterior_lights` exists. Create Morning/Movie/Dinner/Good Night scenes in HA, or map those buttons to scripts/automations?
4. **Now-playing source:** bind the Media hero to `media_player.living_room` (Sonos) or `media_player.spotify_ben_walton`?
5. **Color controls:** most of your lights are **Lutron Caséta dimmers (no color)**. Confirm which bulbs are color/CT-capable so I only show the swatches/warm-cool slider where they work (e.g. `light.office_shelves`).
6. **"Master Ceiling"** → which entity? (`light.master_bedroom_ceiling_fan_light`?)
7. **TV remote:** confirm the webOS **source names** (Apple TV / Roku / Xbox strings) and how you want **power-on** (Wake-on-LAN?). Remote target = `media_player.lg_g3_living_room_2`.
8. **Theme note:** the Aurora design uses a **teal→purple** accent — that *supersedes* the champagne-gold restyle I did earlier on stock espcontrol (that restyle was for the old button-grid look; harmless, still valid). Confirm we're going full Aurora.

## Next steps when you're back (fast path)
1. You pick Route A/B + answer the decisions above.
2. I wire the real entities and compile in WSL (`esphome … compile`).
3. You browser-flash the `.bin` once via esptool-js; then we iterate **OTA**, screen by screen (Lights → Media+remote → Security → Network → Home → Climate).

## Loose end
- `DesignSync`/claude_design MCP never authorized (it needs an interactive subscription `/design-login` the unattended session can't complete). Moot — your zip gave me everything, so this isn't blocking.

## File index (`aurora-build/`)
- `00-MORNING-HANDOFF.md` — this file
- `01-airplay-cast-verdict.md` — mirroring feasibility
- `02-implementation-blueprint.md` — architecture, tokens, per-screen LVGL spec, build order
- `03-entity-bindings.md` — design → your real entities (with gaps flagged)
- `aurora_lights.reference.yaml` — Lights screen + theme LVGL scaffold
- `assets/aurora_bg_1024x600.png` — baked aurora background
