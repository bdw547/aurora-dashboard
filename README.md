# Aurora Dashboard

A custom, high-end **Home Assistant touch dashboard** for the **Guition ESP32‑P4 7‑inch panel** (model **JC1060P470C**, 1024×600). Aurora is a standalone [ESPHome](https://esphome.io) + LVGL firmware with a hand-designed dark/glass UI — a clock + greeting home screen, per-room controls, a full Spotify experience (now-playing with album art, room/zone selection, a browsable library), climate, security, network status, an LG webOS TV remote, and device settings.

> **Heritage / credit:** Aurora began as a fork of [jtenniswood/espcontrol](https://github.com/jtenniswood/espcontrol) and reuses its proven hardware bring-up (display, touch, WiFi). The dashboard UI here (`devices/guition-esp32-p4-jc1060p470/aurora.yaml`) is a complete, independent rewrite and no longer uses the espcontrol button-grid engine. See `LICENSE`/`NOTICE` for upstream attribution.

---

## Screens

| Screen | What it does |
|---|---|
| **Home** | Live clock + greeting, weather/presence/secured chips, a large Now-Playing card (album art + transport), and a 2×2 grid (Climate, Lights, Doors & Sensors, Quick nav). |
| **Rooms** | Pick a room → control that room's lights (tap to toggle, slider to dim), fans (with spin animation), and switches. |
| **Lights** | Selectable light list with a brightness arc + on/off. |
| **Media** | Spotify (SpotifyPlus): now-playing with progress bar + elapsed/remaining, play/pause/skip/volume, an 8-room speaker selector, and a Library (playlist → scrollable track list → tap to play). |
| **Climate** | Outdoor temperature, condition, humidity, wind (from `weather.forecast_home`). |
| **Security** | Front/back door lock state + control, presence. |
| **Network** | Panel WiFi signal, Synology status. |
| **Settings** | Display brightness, screen timeout, wake-on-presence, screen saver. |
| **TV Remote** | Full LG webOS remote (D-pad, Power/Home/Back/Exit/Menu/Info, volume/mute, channel, media transport, app shortcuts) — reached from the Living Room view. |

---

## Hardware

- **Panel:** Guition **JC1060P470C_I_W** — ESP32‑P4 + ESP32‑C6, 7" 1024×600 IPS, JD9165 driver (MIPI‑DSI), GT911 capacitive touch, 32 MB PSRAM / 16 MB flash. (The `C` variant also has a MIPI‑CSI camera — not used by this firmware; see *Roadmap*.)
- **Power:** USB‑C (5 V).
- A computer with a USB‑C cable for the **first** flash (after that, updates are wireless / OTA).

---

## Home Assistant prerequisites

Aurora controls **your** Home Assistant entities, so you need HA running on your LAN. Depending on which screens you want fully working, install/confirm:

| Feature | Requires |
|---|---|
| Media (Spotify) | The **SpotifyPlus** integration (via HACS: `thlucas1/homeassistantcomponent_spotifyplus`), authenticated to your Spotify Premium account. |
| Media **Library** (playlist/track browsing) | The Aurora HA package **`aurora-build/aurora_spotify_library.yaml`** installed in HA (see *Spotify Library setup* below). |
| TV Remote | The **webOS TV** (`webostv`) integration for your LG TV. |
| Climate | A weather entity (default `weather.forecast_home`). |
| Lights / Fans / Locks / Presence / NAS | Your own `light.*`, `fan.*`, `switch.*`, `lock.*`, `person.*`, `sensor.*` entities. |

> **Important:** the entity IDs in `aurora.yaml` are currently **specific to the author's home** (e.g. `light.living_room_main`, `media_player.spotifyplus_ben_walton`, `lock.front_door`). To use Aurora in *your* home you must rebind them — see **[Customizing for your home](#customizing-for-your-home)**. (A no-code web configurator to do this for you is on the [roadmap](#roadmap).)

---

## Quick start

### 1. Get the toolchain

On the machine you'll build from (Linux / WSL recommended):

```bash
python3 -m venv ~/aurora-venv
source ~/aurora-venv/bin/activate
pip install esphome
```

### 2. Clone

```bash
git clone https://github.com/bdw547/aurora-dashboard.git
cd aurora-dashboard
```

### 3. WiFi & secrets

Create `devices/guition-esp32-p4-jc1060p470/secrets.yaml` with your WiFi:

```yaml
wifi_ssid: "Your Network"
wifi_password: "your-password"
```

This file is **gitignored** — never commit it. SSID/password do **not** need quotes unless they contain special characters, but quoting is safest.

### 4. First flash (USB, one time)

The very first flash must be over USB (after that it's wireless). Easiest path — a browser:

1. Build the firmware: `esphome compile devices/guition-esp32-p4-jc1060p470/aurora.yaml`
2. This produces `…/.esphome/build/aurora-panel/.pioenvs/aurora-panel/firmware.factory.bin`.
3. Plug the panel into your computer via USB‑C, open **<https://web.esphome.io>** in Chrome/Edge, click **Connect**, pick the serial port, **Install**, and choose that `firmware.factory.bin`.

> Notes: Flash Mode/Frequency/Size = "keep" is fine. If a USB flash *seems* to do nothing, run `esphome clean …` then recompile so the `factory.bin` is regenerated (PlatformIO sometimes skips re-merging it). The first boot briefly shows an "AURORA" splash while WiFi + HA connect.

### 5. Add to Home Assistant

After it boots and joins WiFi, HA should auto-discover it as **Aurora Panel** under **Settings → Devices & Services → ESPHome** — click **Configure** to add it. Note its IP address (e.g. `10.0.0.174`); you'll use it for updates.

### 6. Updates from now on (OTA — no USB)

```bash
esphome run devices/guition-esp32-p4-jc1060p470/aurora.yaml --device 10.0.0.174
```

---

## Customizing for your home

All UI and bindings live in **`devices/guition-esp32-p4-jc1060p470/aurora.yaml`**. After any edit:

```bash
esphome config devices/guition-esp32-p4-jc1060p470/aurora.yaml                       # validate (fast)
esphome run    devices/guition-esp32-p4-jc1060p470/aurora.yaml --device <panel-ip>   # build + OTA
```

> `esphome config` does **not** type-check `!lambda` C++ — only a full `run`/`compile` catches those.

**Rebinding to your entities:** search the file for the author's entity IDs and replace them with yours. The main ones:

- Lights/fans/switches: `light.living_room_main`, `fan.living_room_pendant`, `switch.outdoor_patio_putting_green`, etc.
- Media: `media_player.spotifyplus_ben_walton`
- TV: `media_player.lg_g3_living_room_2`
- Locks: `lock.front_door`, `lock.back_door`
- Presence: `person.ben`
- Weather: `weather.forecast_home`
- NAS: `sensor.walton_synology_volume_1_status`
- Spotify room/zone names (your Spotify Connect device names): the `src_btn_*` buttons on the Media page.

**Common patterns in the file:**
- **Pages** live under `lvgl: → pages:`; the persistent left nav rail is in `lvgl: → top_layer:`.
- A control reads HA state via a `sensor:`/`text_sensor:` (platform `homeassistant`) and acts via `homeassistant.action`.
- Per-entity runtime state is kept in `globals:` (`std::map` for light brightness/on-state, `g_room` for the selected Spotify zone, etc.).
- Icons are Material Design Icon glyphs from the `f_icon` font; a glyph must be added to that font's `glyphs:` list before it will render.

**Tips / gotchas learned building this:**
- `homeassistant.action` data values must be **strings** (use `std::to_string`/`snprintf`; quote booleans like `play: "true"`).
- This DSI panel ignores a static `rotation:` — it's set at runtime in `on_boot`.
- `logger.log` defaults to DEBUG; with `logger: level: INFO` you won't see DEBUG lines.

---

## Spotify Library setup (HA package)

The Media **Library** (browse playlists → tracks → tap to play in a room) needs a small HA package, because the panel can't browse Spotify directly — Home Assistant fetches the data and exposes it as sensors.

1. Copy **`aurora-build/aurora_spotify_library.yaml`** to your HA config at `packages/aurora_spotify_library.yaml`.
2. In `configuration.yaml` (once): 
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
3. Edit the entity in that file if your SpotifyPlus entity isn't `media_player.spotifyplus_ben_walton`.
4. Check config → **Restart HA**.
5. Run the action **`script.aurora_spotify_refresh_playlists`** once to populate your playlists.

The package provides `sensor.aurora_spotify_playlists` / `sensor.aurora_spotify_tracks` and the `aurora_spotify_load_playlist` / `aurora_spotify_play_track` scripts the panel calls. (Lists are capped at Spotify's per-fetch limit of 50.)

---

## Project layout (Aurora-relevant)

```
devices/guition-esp32-p4-jc1060p470/
  aurora.yaml          ← the entire Aurora firmware (pages, bindings, logic)
  secrets.yaml         ← your WiFi (gitignored; you create this)
aurora-build/
  aurora_spotify_library.yaml   ← HA package for the Spotify Library
  assets/              ← baked background + fan animation frames
```

Everything else in the repo is inherited from upstream espcontrol and is not used by the Aurora firmware.

---

## Roadmap

- **No-code web configurator** — a drag-and-drop dashboard builder with live preview, so anyone can point Aurora at their own Home Assistant, rebind entities, and rearrange the home screen without editing YAML. (Design in progress.)
- **Camera (JC1060P470C):** the board's MIPI-CSI OV5647 camera builds and the sensor is detected, but ESPHome's MIPI-CSI camera support is still pre-release; parked pending the camera's XCLK pin + upstream support. Tracked on the `camera-experiment` branch.

---

## Troubleshooting

- **OTA "connection reset by peer":** retry — usually transient. (WiFi `fast_connect` + `power_save_mode: none` are enabled to minimize this.)
- **A control does nothing but the clock/lights still work:** the entity ID in `aurora.yaml` doesn't match your HA entity — rebind it.
- **Spotify plays but you can't control it / switch rooms:** the target must be an *available* Spotify Connect device, and not "restricted" (Sonos/Roku/Chromecast can be started but not controlled via the API).
