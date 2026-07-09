# Guition ESP32-P4 JC1060P470 (7")

7-inch 1024x600 touchscreen panel that runs EspControl firmware for Home Assistant. A fixed 3x5 grid of 15 configurable buttons lets you control lights, switches, fans, and other smart home devices with a single tap. The display also shows a live clock, indoor/outdoor temperature, and includes a screensaver with adjustable brightness.

After the initial install, everything is configured through the built-in web page — no coding or file editing required.

## Quick links

- **Full documentation:** [jtenniswood.github.io/espcontrol](https://jtenniswood.github.io/espcontrol/)
- **Install guide:** [jtenniswood.github.io/espcontrol/install](https://jtenniswood.github.io/espcontrol/install)
- **Web UI guide:** [jtenniswood.github.io/espcontrol/web-ui](https://jtenniswood.github.io/espcontrol/web-ui)

## Features

- **15 buttons** (3x5 grid) — control any Home Assistant device
- **Drag-and-drop ordering** — rearrange buttons from your browser
- **Automatic icons** — or choose from hundreds of icons manually
- **Custom labels** — name buttons however you like
- **Indoor and outdoor temperature** in the top bar
- **Live clock** synced from NTP, with Home Assistant as a fallback
- **Screensaver** with adjustable idle timeout and optional presence sensor to wake
- **Day/night brightness** — adjusts automatically based on sunrise and sunset
- **Over-the-air updates** — automatic or manual
- **WiFi setup** — on-screen guide if the network is unavailable

## Live camera streaming (Aurora)

The Aurora firmware shows Home Assistant cameras as a real live feed via the
`mjpeg_stream` component (`components/mjpeg_stream/`): MJPEG is pulled on a
background FreeRTOS task, JPEG frames are decoded by the P4's hardware JPEG
decoder and scaled by the PPA, and only a buffer swap happens on the UI
thread — the panel stays fully responsive while streaming.

- **Default source:** HA's `/api/camera_proxy_stream/<entity>` (derived
  automatically from the camera's `entity_picture` token). HA serves this at
  roughly 2 fps for stream-backed cameras.
- **Full frame rate:** set the `cam_stream_url_override` substitution to a
  go2rtc MJPEG URL (e.g. `http://<ha-ip>:1984/api/stream.mjpeg?src=camera.front_door`)
  for 10+ fps.
- **Fallback:** if the stream errors, the UI drops to the snapshot path and
  the pill shows `SNAP` instead of `LIVE`; it reconnects automatically with
  backoff, and HA token rotation heals 401s.

### How many cameras can this device stream?

Per 1080p stream: ~6.5 MB PSRAM (JPEG accumulator + full-res decode buffer +
double-buffered scaled frames), 1 TCP socket (of 16 LWIP sockets shared with
the HA API, web server, and RTSP), 6–10 Mbps WiFi, and ~15 ms/frame on the
single hardware JPEG engine.

**Practical ceiling: 2 concurrent 1080p streams at 5–10 fps** (WiFi
throughput on the C6 SDIO link is the binding constraint). 3–4 streams are
feasible only at ≤720p / ≤5 fps. The shipped config runs 1 stream, started
when a camera page is visible and stopped when it isn't.

### Audio (future)

Not currently possible: the board exposes a bare speaker connector but has no
onboard codec or amplifier, and MJPEG carries no audio track. Adding audio
would require an external I2S DAC/amp (e.g. MAX98357A) wired to the GPIO FPC,
`i2s_audio` + `speaker` components, and an RTSP/WebRTC client (go2rtc source)
with AAC/PCM decode — a separate project.

## Where to buy

- **Panel:** [AliExpress](https://s.click.aliexpress.com/e/_c335W0r5) (~£40)
- **Desk stand** (3D printable): [MakerWorld](https://makerworld.com/en/models/2387421-guition-esp32p4-jc1060p470-7inch-screen-desk-mount#profileId-2614995)
