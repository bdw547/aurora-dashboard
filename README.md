# Aurora Dashboard

Aurora is an ESPHome + LVGL touch dashboard for the Guition 7-inch ESP32-P4 panel. It gives Home Assistant a wall-panel interface with rooms, lights, climate, Spotify, an LG TV remote, camera support, a photo screensaver, settings, and optional haptic feedback.

This README is written for the easiest path first. If you have never built firmware before, start with the browser installer and the web configurator. If you are comfortable with WSL and ESPHome, the manual serial, OTA, and emulator sections are here too.

![Aurora panel hero](docs/marketing/hero.png)

## Which Setup Path Should I Use?

| Goal | Best path |
| --- | --- |
| Install a supported EspControl display with the fewest steps | Use the standard browser installer guide in [docs/getting-started/install.md](docs/getting-started/install.md). |
| Build and customize Aurora for the 7-inch Guition ESP32-P4 panel | Follow this README. |
| Change rooms, cards, and Home Assistant entities without editing YAML | Use the [web configurator](#web-configurator). |
| Update an already-flashed panel | Use [OTA flashing](#ota-flashing-after-the-first-install). |
| Test the UI before flashing hardware | Use the [emulator](#run-the-emulator). |

## What You Need

- A Guition `JC1060P470C_I_W` 7-inch ESP32-P4 panel for Aurora.
- A USB-C cable that carries data, not charge-only power.
- A computer. Windows with WSL2 Ubuntu works well.
- Chrome or Edge for the first browser-based flash.
- Home Assistant running on the same network.
- Wi-Fi name and password for the panel.
- Optional: Pimoroni PIM452 DRV2605L breakout and an LRA haptic motor.

## Hardware Notes

Aurora currently targets this panel:

- Guition `JC1060P470C_I_W`
- ESP32-P4 with ESP32-C6 radio
- 7-inch 1024 x 600 IPS display
- GT911 capacitive touch
- OV02C10 camera
- 32 MB PSRAM / 16 MB flash
- USB-C power and flashing

Other EspControl device packages live in `devices/` and `builds/`, but the full Aurora dashboard and haptic configuration are currently built around the 7-inch Guition ESP32-P4 panel.

## Quick Start: First Install

This route keeps the first setup as simple as possible: install tools once, build the firmware, flash it from the browser, then finish setup in Home Assistant.

### 1. Clone The Repository

In WSL Ubuntu:

```bash
git clone https://github.com/bdw547/aurora-dashboard.git ~/espcontrol
cd ~/espcontrol
```

The `~/espcontrol` folder name is intentional. Some helper scripts assume that path.

### 2. Install ESPHome

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
python3 -m venv ~/aurora-venv
source ~/aurora-venv/bin/activate
pip install --upgrade pip
pip install esphome
```

When you open a new WSL terminal later, run this again before ESPHome commands:

```bash
source ~/aurora-venv/bin/activate
cd ~/espcontrol
```

### 3. Add Wi-Fi Secrets

Create this file:

```text
devices/guition-esp32-p4-jc1060p470/secrets.yaml
```

Put your Wi-Fi details in it:

```yaml
wifi_ssid: "Your WiFi Name"
wifi_password: "your-wifi-password"
```

`secrets.yaml` is ignored by git, so your Wi-Fi password should not be committed.

### 4. Build The Firmware

```bash
esphome compile devices/guition-esp32-p4-jc1060p470/aurora.yaml
```

The factory image is created under:

```text
devices/guition-esp32-p4-jc1060p470/.esphome/build/aurora-panel/.pioenvs/aurora-panel/firmware.factory.bin
```

### 5. Flash The Panel From The Browser

1. Plug the panel into your computer with USB-C.
2. Open [https://web.esphome.io](https://web.esphome.io) in Chrome or Edge.
3. Click **Connect**.
4. Pick the panel serial port.
5. Click **Install**.
6. Choose the `firmware.factory.bin` file from the build folder above.
7. Wait for the flash and first boot to finish.

The first boot can take a little while because Wi-Fi and Home Assistant discovery are starting for the first time.

### 6. Add The Panel In Home Assistant

In Home Assistant:

1. Go to **Settings > Devices & services**.
2. Look for the discovered ESPHome device, usually `Aurora Panel`.
3. Click **Configure**.
4. Note the panel IP address. You will use it for OTA updates.

## Web Configurator

The web configurator is the best path for people who do not want to hand-edit YAML. It runs locally on your computer, opens in a browser, and helps map Aurora cards to your own Home Assistant entities.

Start it from WSL:

```bash
source ~/aurora-venv/bin/activate
cd ~/espcontrol
python3 aurora-build/configurator/serve.py
```

Then open:

```text
http://localhost:8765
```

Use it to:

- Connect to your Home Assistant URL with a long-lived access token.
- Pick your own lights, fans, switches, locks, sensors, media players, and weather entity.
- Arrange cards in the visual layout.
- Generate the Aurora YAML pieces used by the firmware.
- Run the local build and OTA flow from the browser interface.

The configurator writes generated files such as `layout.json` and `aurora-gen.yaml`. Review generated changes before committing anything.

## OTA Flashing After The First Install

After the first USB flash, normal updates can be wireless.

```bash
source ~/aurora-venv/bin/activate
cd ~/espcontrol
esphome run devices/guition-esp32-p4-jc1060p470/aurora.yaml --device <panel-ip>
```

Example:

```bash
esphome run devices/guition-esp32-p4-jc1060p470/aurora.yaml --device 192.168.1.42
```

Use OTA when:

- The panel is already on Wi-Fi.
- Home Assistant can see it.
- You are changing layout, entity bindings, UI behavior, or normal firmware settings.

Use USB serial again when:

- The panel no longer joins Wi-Fi.
- OTA cannot connect.
- You changed something that prevents booting.
- You are recovering from a bad flash.

## Flash Through Serial In WSL

Browser flashing is easiest, but serial flashing from WSL is useful for recovery and repeat testing.

### 1. Install usbipd-win On Windows

Install `usbipd-win` from the official project if it is not already installed:

```text
https://github.com/dorssel/usbipd-win
```

### 2. Attach The USB Device To WSL

Open PowerShell as Administrator:

```powershell
usbipd list
usbipd bind --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
```

Replace `<BUSID>` with the bus ID shown by `usbipd list`.

### 3. Find The Serial Port In WSL

In WSL:

```bash
ls /dev/ttyACM* /dev/ttyUSB*
```

Common ports are `/dev/ttyACM0` or `/dev/ttyUSB0`.

### 4. Flash Over Serial

```bash
source ~/aurora-venv/bin/activate
cd ~/espcontrol
esphome run devices/guition-esp32-p4-jc1060p470/aurora.yaml --device /dev/ttyACM0
```

If your device shows up as `/dev/ttyUSB0`, use that instead.

### 5. Detach When Finished

Back in PowerShell:

```powershell
usbipd detach --busid <BUSID>
```

## Run The Emulator

The emulator lets you render Aurora on your computer before flashing hardware. It is useful for checking layout, screenshots, and visual changes.

From WSL:

```bash
cd ~/espcontrol
./emulate.sh
```

Useful modes:

```bash
./emulate.sh          # build the host emulator and save a screenshot to ~/aurora_emul.png
./emulate.sh --all    # cycle through screens and save screenshots to ~/emul_shots/
./emulate.sh --live   # open a clickable SDL window with WSLg
```

If dependencies are missing:

```bash
sudo apt update
sudo apt install -y libsdl2-dev imagemagick xvfb
```

Notes:

- `--live` needs WSLg or a Linux desktop session.
- The emulator uses ESPHome's host platform, so it is excellent for layout checks but not a perfect hardware test.
- If the script cannot find ESPHome, activate the same Python environment you use for builds or update the script path to match your local venv.

## Add Haptic Feedback With PIM452 DRV2605

Aurora includes support for the Pimoroni PIM452 DRV2605L breakout on the 7-inch Guition ESP32-P4 panel.

### Parts

- Pimoroni PIM452 DRV2605L breakout
- LRA haptic motor recommended
- Short wires for I2C and power

### Wiring

| PIM452 pin | Panel connection |
| --- | --- |
| `VIN` / `VCC` | `3.3V` |
| `GND` | `GND` |
| `SDA` | `GPIO07` |
| `SCL` | `GPIO08` |

Use 3.3V, keep wires short, and avoid adding extra I2C pull-ups unless you have confirmed they are needed.

### Firmware Configuration

The Aurora 7-inch config already contains the haptic component:

```yaml
drv2605:
  id: haptic
  i2c_id: bus_a
  actuator: lra
```

It uses the shared I2C bus:

```yaml
i2c:
  - id: bus_a
    sda: GPIO07
    scl: GPIO08
    scan: true
    frequency: 100kHz
    sda_pullup_enabled: false
    scl_pullup_enabled: false
```

Touch feedback is already wired into the Aurora UI. The Settings screen also includes haptic controls and a test buzz button.

If you are adapting haptics to another display, first confirm that the display exposes a usable I2C bus and that the pins do not conflict with touch, camera, display, or other peripherals.

## Home Assistant Features

Aurora can be used lightly with just common Home Assistant entities, or fully with the optional integrations below.

| Feature | Home Assistant requirement |
| --- | --- |
| Lights, fans, switches, covers, locks | Matching Home Assistant entities such as `light.*`, `fan.*`, `switch.*`, `cover.*`, `lock.*` |
| Weather and climate cards | A weather entity, defaulting to `weather.forecast_home` |
| Spotify controls | SpotifyPlus integration through HACS |
| Spotify library browsing | `aurora-build/aurora_spotify_library.yaml` installed in Home Assistant |
| Notification center | `aurora-build/aurora_notifications.yaml` installed in Home Assistant |
| LG TV remote | Home Assistant `webostv` integration |
| LG Magic Remote style trackpad | `aurora-build/lg_pointer_bridge/` pyscript helper |
| Camera and wake-on-approach | The onboard panel camera support in the Aurora firmware |

## Manual Customization

If you prefer editing files directly, the main Aurora config is:

```text
devices/guition-esp32-p4-jc1060p470/aurora.yaml
```

After edits, validate first:

```bash
esphome config devices/guition-esp32-p4-jc1060p470/aurora.yaml
```

Then flash OTA:

```bash
esphome run devices/guition-esp32-p4-jc1060p470/aurora.yaml --device <panel-ip>
```

`esphome config` is fast, but it does not fully compile C++ lambdas. Use `esphome compile` or `esphome run` before trusting a larger change.

## Standard EspControl Install Docs

This repository also includes general setup docs for the broader EspControl project:

- [Install guide](docs/getting-started/install.md)
- [Manual ESPHome setup](docs/getting-started/manual-esphome-setup.md)
- [Troubleshooting](docs/getting-started/troubleshooting.md)

Use those docs when you are installing one of the standard generated device packages instead of the custom Aurora dashboard.

## Troubleshooting

| Problem | Try this |
| --- | --- |
| Browser cannot see the panel | Use a data-capable USB-C cable, try another USB port, and use Chrome or Edge. |
| WSL cannot see the serial port | Run `usbipd list`, attach the device to WSL, then check `/dev/ttyACM*` and `/dev/ttyUSB*`. |
| OTA cannot connect | Confirm the panel IP, check that it is on Wi-Fi, and try USB serial if it no longer boots correctly. |
| Build cannot find secrets | Confirm `devices/guition-esp32-p4-jc1060p470/secrets.yaml` exists and has `wifi_ssid` and `wifi_password`. |
| Emulator opens no window | Use `./emulate.sh` for screenshot mode first, then install SDL dependencies and use `--live` with WSLg. |
| Haptics do not buzz | Check PIM452 power, ground, SDA/SCL wiring, and confirm the motor type matches the configured `actuator`. |
| Home Assistant entities show unavailable | Rebind the panel to your own entity IDs with the web configurator or by editing `aurora.yaml`. |

## Project Map

| Path | Purpose |
| --- | --- |
| `devices/guition-esp32-p4-jc1060p470/aurora.yaml` | Main Aurora firmware config |
| `aurora-build/configurator/` | Local web configurator and YAML generator |
| `components/drv2605/` | DRV2605 haptic component |
| `docs/getting-started/` | General EspControl installation docs |
| `docs/marketing/` | README images and screenshots |
| `builds/` | Generated ESPHome package entrypoints for supported displays |
| `emulate.sh` | Aurora LVGL host emulator helper |

## Credit

Aurora began as a fork of [jtenniswood/espcontrol](https://github.com/jtenniswood/espcontrol) and keeps the upstream hardware bring-up work while adding the Aurora dashboard experience, configurator flow, Home Assistant integrations, and haptic support.

See [LICENSE](LICENSE) and [NOTICE](NOTICE) for license and attribution details.