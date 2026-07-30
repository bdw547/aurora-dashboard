---
title: Setup Guide
description: "From a blank computer to a working Aurora panel — every step copy-paste, no coding knowledge required. Windows/WSL and Linux."
---

# Setup guide

This guide takes you from a blank computer to a working Aurora panel, **no coding knowledge required**. Every step is copy-paste. If anything goes wrong, jump to [Troubleshooting](#troubleshooting) at the bottom — the most common errors are listed there with their fixes.

**What you need before starting:**

- The Guition **JC1060P470C** 7″ panel and a **USB-C data cable**.
- **Home Assistant** already running on your network.
- A computer to build from: **Linux**, or **Windows 11 with WSL** (see Step 0).
- About 30–45 minutes (most of it is waiting for the first build).

## Step 0 — Windows users: install WSL

Aurora builds on Linux. On Windows 11, WSL gives you Linux inside Windows — open **PowerShell as Administrator** and run:

```powershell
wsl --install
```

Reboot when asked, open the new **Ubuntu** app, and create a username/password when prompted. Do every remaining step inside that Ubuntu window. (Already on Linux? Skip to Step 1.)

## Step 1 — Install the basic tools

Copy-paste this one command (it will ask for your password):

```bash
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip libusb-1.0-0
```

::: warning Don't skip libusb
`libusb-1.0-0` matters — without it the firmware build fails partway through with an error about `libusb-1.0.so.0`.
:::

**Optional** — if you also want the on-screen panel preview (the emulator, Step 9):

```bash
sudo apt install -y libsdl2-2.0-0 imagemagick xvfb
```

## Step 2 — Download Aurora

```bash
git clone https://github.com/bdw547/aurora-dashboard.git
cd ~/aurora-dashboard
```

## Step 3 — Install ESPHome

ESPHome is the tool that turns Aurora into firmware for the panel. Install it in its own "virtual environment" (a self-contained folder, so it can't conflict with anything else):

```bash
python3 -m venv ~/aurora-venv
source ~/aurora-venv/bin/activate
pip install esphome
```

::: tip Remember this
`source ~/aurora-venv/bin/activate` must be run **once in every new terminal window** before using `esphome` or the configurator. If you ever see `esphome: command not found`, this is why. (Your prompt shows `(aurora-venv)` when it's active.)
:::

## Step 4 — Tell Aurora your WiFi

The panel needs your WiFi details to get online. Create its private secrets file:

```bash
nano devices/guition-esp32-p4-jc1060p470/secrets.yaml
```

Type these two lines (with **your** network name and password), then press `Ctrl+O`, `Enter` to save and `Ctrl+X` to exit:

```yaml
wifi_ssid: "Your Network Name"
wifi_password: "your-wifi-password"
```

This file is **never uploaded anywhere** — it's listed in `.gitignore`, so git ignores it even if you later share your copy of the project.

## Step 5 — Build the firmware

```bash
esphome compile devices/guition-esp32-p4-jc1060p470/aurora.yaml
```

The **first** build downloads a full compiler toolchain and takes **15–30 minutes**; later builds take a few minutes. Success looks like:

```
INFO Successfully compiled program.
```

## Step 6 — First flash (USB, one time only)

Only the very first flash needs a cable — after this, all updates are wireless.

1. Plug the panel into your computer with the USB-C cable.
2. On **Windows/WSL**: open <https://web.esphome.io> in **Chrome or Edge on Windows** (the browser can reach the USB port directly — no WSL setup needed). On Linux: same site, in Chrome/Chromium.
3. Click **Connect**, pick the serial port that appears, then **Install** → choose the file:

   ```
   devices/guition-esp32-p4-jc1060p470/.esphome/build/aurora-panel/build/firmware.factory.bin
   ```

   > From Windows, your Ubuntu files are at `\\wsl$\Ubuntu\home\<your-username>\aurora-dashboard\...` in the file picker.

4. Wait for it to finish, unplug, and power the panel from any USB-C charger. It shows an **AURORA** splash while it joins WiFi.

## Step 7 — Add it to Home Assistant

Home Assistant should pop up a notification: **Settings → Devices & Services → ESPHome → "Aurora Panel" → Configure**. Accept it.

Then find the panel's IP address (**Settings → Devices & Services → ESPHome → Aurora Panel**, or your router's device list) and write it down — you'll need it for wireless updates and for the configurator.

## Step 8 — Make it *yours* (the web configurator)

Out of the box the dashboard points at the author's home. The **web configurator** rebinds every control to *your* Home Assistant entities and lets you redesign the screens by drag-and-drop — no file editing.

Follow the **[Web configurator guide](/setup/configurator)** — it covers connecting to Home Assistant, the entity and rooms wizards, the drag-and-drop builder, and one-click flashing.

Some screens (Spotify library, notifications, calendar, TV trackpad) also need a small package installed on the Home Assistant side — see **[Home Assistant packages](/setup/home-assistant)**.

## Step 9 (optional) — Preview on your computer

You can render the real panel UI in a window on your desktop (WSL on Windows 11, or any Linux desktop) without flashing anything:

```bash
./aurora-build/configurator/emulate.sh --live
```

Note the folder — the script lives in `aurora-build/configurator/`, not the project root. Close the window or press `Ctrl+C` to stop. (Needs the optional packages from Step 1.)

## Updating later

```bash
cd ~/aurora-dashboard
git pull
source ~/aurora-venv/bin/activate
esphome run devices/guition-esp32-p4-jc1060p470/aurora.yaml --device <panel-ip>
```

Or just open the configurator and press **Flash** again.

## Keeping your details private

Everything sensitive stays on your computer and out of git automatically:

| File | Contains | Protected how |
|---|---|---|
| `devices/…/secrets.yaml` | Your WiFi name + password | gitignored |
| `aurora-build/configurator/config.json` | Your HA access token, panel IP, configurator password | gitignored |

Two habits keep it that way: **don't move those two files elsewhere**, and **change the configurator's default password**. If you ever think your HA token leaked, delete it in Home Assistant (Security tab → your tokens) and create a new one.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `esphome: command not found` | Run `source ~/aurora-venv/bin/activate` first (needed in every new terminal). |
| `esp_video_camera requires the esp-idf framework.` | Your copy of `aurora.yaml` predates the `toolchain: esp-idf` setting — update with `git pull`, or run esphome with `--toolchain esp-idf`. |
| `libusb-1.0.so.0: cannot open shared object file` / `ESP-IDF … framework installation failure` | `sudo apt install libusb-1.0-0`, then build again. |
| `./emulate.sh: No such file or directory` | The script is in a subfolder: `./aurora-build/configurator/emulate.sh` |
| First build is extremely slow | Normal — it downloads a full compiler toolchain once. Later builds are much faster. |
| web.esphome.io can't see the panel | Use Chrome or Edge (Firefox/Safari lack Web Serial). Use a USB-C **data** cable, not a charge-only one. |
| Panel boots but controls do nothing | The entities aren't yours yet — run the [configurator's entity wizard](/setup/configurator). |
| OTA update fails with "connection reset by peer" | Just retry — it's usually transient. |
| OTA succeeds but the panel runs the old build | The ESP32-P4's OTA boot-confirm is flaky and can roll back to the previous partition. Re-flash until it sticks, or flash over USB-serial (`esphome run … --device /dev/ttyACM0`), which bypasses the rollback. |
