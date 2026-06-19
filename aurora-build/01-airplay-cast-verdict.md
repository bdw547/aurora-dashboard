# Can this panel mirror a TV/phone screen (AirPlay / Google Cast / Miracast)?

**Short answer: No — not as a screen/video mirroring *receiver*.** This panel should stay a *control* surface. It can, however, display Home Assistant **camera feeds** (see "What it *can* do").

## Why mirroring isn't feasible on this device

There are two independent blockers; either one alone is fatal.

### 1. The protocols are proprietary and licensed (the decisive blocker)
- **AirPlay screen mirroring** uses Apple's private mirroring protocol (FairPlay-encrypted H.264 over a closed handshake). There is **no open implementation** you can run as a receiver. Open-source AirPlay work on ESP32 is **audio-only** (e.g. `squeezelite-esp32`, shairport-style) — never video/screen.
- **Google Cast (Chromecast)** is a closed Google platform. You cannot build a DIY Cast *receiver*; Google only ships the receiver in licensed hardware. Home Assistant cannot turn an ESPHome display into a cast target either.
- **Miracast** is the only "open-ish" one, but it needs a full Wi-Fi-Direct stack + the Miracast session protocol + real-time H.264 decode — none of which exists for ESPHome or this panel.

### 2. The hardware isn't built for receiving a live video stream
- The ESP32-P4 has hardware **H.264 *encode*** (~1080p30, aimed at *cameras*) and **MJPEG encode/decode**. Real-time **H.264/H.265 *decode*** of an arbitrary device's screen output is not a supported, robust path.
- Wi-Fi on this board runs through the **ESP32-C6 co-processor over ESP-Hosted** — fine for HA control traffic, not for sustained HD video throughput.

Even if the codec story were perfect, blocker #1 still stops it.

## What it *can* do (useful alternatives)
- **Show a Home Assistant `camera.` feed** on the panel (doorbell, security cam). The P4 decodes **MJPEG**, and HA can serve camera snapshots/MJPEG. This is a "view the front door" tile — not phone/TV mirroring, but genuinely useful. espcontrol already has an `image`/camera card and an `artwork_image` JPEG path we can build on.
- **Control casting from the panel**: start/stop/redirect what plays on your real Cast/AirPlay targets (LG webOS TV `media_player.lg_g3_living_room_2`, `media_player.guest_room_tv` cast, Sonos) via HA — which is exactly what the Aurora **Media** screen already does.
- **Audio AirPlay** is feasible on ESP32 — but as *separate* speaker hardware/firmware, not on this display.

## If true screen mirroring is a hard requirement
Use a purpose-built receiver on a real HDMI display: an **Apple TV** (AirPlay), a **Chromecast/Google TV** (Cast), or a **Miracast dongle**. The panel then becomes the *remote/control* for it — which the design already supports.

## Sources
- [Espressif ESP32-P4 product page](https://www.espressif.com/en/products/socs/esp32-p4)
- [ESP H.264 usage guide (Espressif Developer Portal)](https://developer.espressif.com/blog/2025/07/esp-h264-use-tips/)
- [squeezelite-esp32 (AirPlay *audio* on ESP32)](https://github.com/sle118/squeezelite-esp32)
- [AirPlay vs Miracast vs Chromecast overview](https://www.avaccess.com/blogs/guides/airplay-vs-miracast-vs-chromecast/)
