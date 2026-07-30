---
title: Web Configurator
description: "Aurora's no-code web configurator: map the panel to your Home Assistant entities, design screens by drag-and-drop, and flash wirelessly — never touch YAML."
---

# Web configurator

Aurora ships a **no-code web configurator** that runs locally on your build machine. Point it at your Home Assistant, lay out screens by drag-and-drop, and flash the panel — never touch a line of YAML.

![The Aurora web configurator — a card palette, a live 1024×600 device preview on a 6×5 grid, and an inspector for binding each card to a Home Assistant entity](../marketing/configurator.png)

## Start it

```bash
source ~/aurora-venv/bin/activate        # the venv with esphome installed
python3 aurora-build/configurator/serve.py
```

Open **<http://localhost:8765>** and sign in with the default password **`Admin`**.

::: warning Change the password right away
⚙ Settings → **Change password**. The configurator is reachable by other devices on your network, and it stores a Home Assistant access token. Everything it stores (HA URL, token, panel IP, password hash) lives locally in `aurora-build/configurator/config.json`, which is gitignored.
:::

## Connect to Home Assistant

1. Enter your **HA URL** — use the IP address, not `.local` (e.g. `http://192.168.1.10:8123`).
2. Create a **long-lived access token**: in Home Assistant, click your user name (bottom-left) → **Security** tab → scroll to **Long-lived access tokens** → **Create token** → copy it into the configurator.
3. Enter your **panel's IP address** (from your router or HA's ESPHome device page).

## What each part does

- **Entity-rebind wizard** — reads the panel's entity slots, lists *your* Home Assistant entities, and lets you map each slot to one of yours: lights, locks, media, presence, weather, and the rest.
- **Rooms wizard** — add, rename and reassign rooms and their lights, fans and switches. Room pages, the room picker and state sensors are generated for you (saved to `rooms.json`).
- **Drag-and-drop page builder** — arrange cards on a 6×5 grid per page with a live preview that matches the panel pixel-for-pixel. Browse everything you can place in the **[Card Library](/cards/)**. Your layout is saved to `layout.json`.
- **Flash button** — runs the whole `layout.json → aurora-gen.yaml → build → OTA` pipeline and sends the personalized firmware to the panel wirelessly.

Your design lives in `layout.json` and `rooms.json` on your computer, so you can tweak and re-flash any time.

## The same pipeline, by hand

The Flash button is just running these — useful if you prefer the terminal:

```bash
python3 aurora-build/configurator/gen.py            # layout.json -> aurora-gen.yaml
python3 aurora-build/configurator/gen.py --check    # generate + validate, no write
esphome run devices/guition-esp32-p4-jc1060p470/aurora-gen.yaml --device <panel-ip>
```

`gen.py` reuses the hand-built hardware/font/style base from `aurora.yaml` and splices in the generated pages and state sensors, producing a self-contained `aurora-gen.yaml`.

## Preview without a panel

Render the generated UI in a desktop window (real firmware, via ESPHome's host platform):

```bash
./aurora-build/configurator/emulate.sh --live
```
