# Aurora screen mockups

Standalone HTML renders of every Aurora panel screen at the device's native
**1024×600**, built to feed into Claude Design for UI refinement. These are
visual mockups (demo data, exact panel coords/colors) — not the firmware. The
real UI lives in [`../../devices/guition-esp32-p4-jc1060p470/aurora.yaml`](../../devices/guition-esp32-p4-jc1060p470/aurora.yaml).

## Files

Each `*.html` is a self-contained page (CDN Sora + Material Design Icons, dark
`.aufr` frame). `png/*.png` are the same screens rendered at 2× via Playwright.

| Screen | File | Firmware page |
|---|---|---|
| Home dashboard | `home.html` | `page_home` |
| Rooms picker | `rooms.html` | `page_rooms` |
| Living Room (room detail) | `living.html` | `page_room_living` |
| Lights | `lights.html` | `page_lights` |
| Climate | `climate.html` | `page_climate` |
| Media (now playing) | `media.html` | `page_media` |
| Security | `security.html` | `page_security` |
| Network | `network.html` | `page_network` |
| Photo Library | `library.html` | `page_library` |
| Weather | `weather.html` | `page_weather` |
| Settings | `settings.html` | `page_settings` |
| Screensaver | `screensaver.html` | `page_screensaver` |
| TV Remote | `tvremote.html` | `page_tvremote` |
| Sonos multi-zone | `sonos.html` | `page_sonos` |
| Doorbell (fullscreen) | `doorbell.html` | `page_doorbell` |
| All card types (reference) | `allcards.html` | room-card archetypes |

## Re-rendering the PNGs

```bash
node /tmp/shot/render.js \
  /home/bdw547/espcontrol/aurora-build/mockups \
  /home/bdw547/espcontrol/aurora-build/mockups/png
```

Renders every `*.html` in this folder to `png/<name>.png` at viewport
1024×600, deviceScaleFactor 2 (Playwright + Chromium).
