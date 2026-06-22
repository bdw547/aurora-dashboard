# ov02c10_support

Makes the Guition JC1060P470's on-board **OmniVision OV02C10** MIPI-CSI camera
work with the `esp_video_camera` external component on the `camera-experiment`
branch.

## Why this exists

`esp_video_camera` (youkorr fork, PR #16944) drives the ESP32-P4 MIPI-CSI port
through Espressif's `esp_video` 2.2.0 stack, which depends on
`espressif/esp_cam_sensor` 2.2.*. The **public** `esp_cam_sensor` 2.2.0 release
does not contain an OV02C10 driver — its sensor list is os02n10, os04c10,
ov5647, sc2336, sc202cs, … but **no ov02c10**. The OV02C10 driver only exists
in the board vendor's `esp_cam_sensor` fork (1.2.1 and 2.0.1).

## What it does

`esp_cam_sensor/` here is a verbatim copy of the registry `esp_cam_sensor`
2.2.0, with the `sensors/ov02c10/` driver grafted in-tree from the vendor's
2.0.1 reference (`JC1060P470C_I_W_Y/.../video_lcd_display/components/
espressif__esp_cam_sensor`). Sibling drivers are source-identical between 2.0.1
and 2.2.0 (`ov5647.c` diffs 0 lines), and the only `esp_cam_sensor` header
changes across those versions are doc typos + 4 additive struct fields, so the
2.0.1 ov02c10 driver compiles against 2.2.0 unchanged.

Graft points in `esp_cam_sensor/`:
- `CMakeLists.txt` — `if(CONFIG_CAMERA_OV02C10)` source block + `-u ov02c10_detect`
  auto-detect link directive (mirrors every other in-tree sensor).
- `Kconfig` — `rsource "sensors/ov02c10/Kconfig.ov02c10"`.
- `sensors/ov02c10/` — driver source, headers, and `cfg/ov02c10_default.json`
  IPA tuning file.

`__init__.py` registers this copy as a local ESP-IDF component via
`esp32.add_idf_component(path=...)`. A root `path:` dependency overrides the
registry version `esp_video` would download, because the vendored version
(2.2.0) satisfies esp_video's `2.2.*` constraint.

## Hardware facts (JC1060P470)

- Sensor: OV02C10, MIPI-CSI, RAW10 1920×1080@30, internal 24 MHz clock.
- SCCB control bus shared with GT911 touch: `bus_a` (SDA GPIO7 / SCL GPIO8).
- No external XCLK GPIO, no reset pin, no pwdn pin → `enable_xclk: false`.

## Status

Compiles clean under the native ESP-IDF toolchain (`.venv-dev`). **Runtime
sensor bring-up is unverified** — pending a flash on the physical panel.
