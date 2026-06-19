# Aurora dashboard → ESP32-P4 panel: implementation blueprint

Target: Guition JC1060P470 (1024×600, ESP32-P4). Source of truth: `Aurora.dc.html` (6 screens: Home, Lights, Climate, Media, Security, Network).

## 1. Architecture decision (important)

The panel's display + touch + LVGL are initialized **inside espcontrol's C++ component** (`components/espcontrol/` + `mipi_rgb`), not via a stock ESPHome `display:`/`lvgl:` block. There is no standalone `lvgl:` we can just point at a display. So two routes:

- **Route A — Fork espcontrol, swap its UI (recommended to start).** Keep espcontrol's *proven* hardware/connectivity/display-init packages; replace its `device/lvgl.yaml` pages (and bypass the button-grid) with our Aurora pages, and add `homeassistant` sensors + `lvgl` action wiring. Lowest risk to get correct pixels on the panel, because the hard JD9165 MIPI-DSI + GT911 + C6-WiFi bring-up is already solved. Downside: we're working *with* espcontrol's component, so we must keep its expected scaffolding happy.
- **Route B — Clean standalone ESPHome config.** Use ESPHome-native `mipi_dsi` display + `gt911` touch + `lvgl` + the ESP-Hosted/C6 WiFi setup, dropping espcontrol entirely. Cleanest long-term and fully ours, BUT requires confirming ESPHome's native MIPI-DSI driver supports the JD9165 and porting the exact panel timings/pins (extract from `components/mipi_rgb` + `device/device.yaml`). Bigger up-front risk.

**Recommendation:** prototype on **Route A** to validate the look on glass fast, then migrate to **Route B** for a clean, maintainable, fully-custom firmware once the panel timings are confirmed. (Question for you in the handoff.)

## 2. Design tokens (extracted from Aurora.dc.html)

- **Font:** Sora (weights 400/500/600/700). Build a role set (≈6 sizes) to limit flash: `display 58`, `xl 34/40`, `title 24/26`, `body 14`, `small 12`, `micro 11`. Compile via ESPHome `font:` (gfonts source or bundled TTF). Glyphs: digits, A–Z/a–z, `°%·–+:`, arrows.
- **Background:** `#08090d` base with aurora glow → use the **baked PNG** `aurora-build/assets/aurora_bg_1024x600.png` (already generated) as the screen background image. LVGL can't do live `blur`/`backdrop-filter`, so baking is the correct technique. (Per-screen variants can be baked too.)
- **Accent gradient:** linear 135° `#2ED5B8` (teal) → `#7B6CFF` (purple). Used for: logo, active nav pill, primary play button, avatar, "on" toggles. LVGL: `bg_grad` 2-stop.
- **Glass cards:** design uses `rgba(20,23,32,.66)` + backdrop-blur. No live blur → use solid **`#141720` at ~92% opacity** over the baked bg, `border rgba(255,255,255,.08)` (≈`#2A2E38` hairline), `radius 22`, soft shadow.
- **Text:** primary `#EEF0F6`; secondary `#8A8F9E` (≈white 55%); muted `#6E727E` (≈40%).
- **State colors:** on/active teal `#2ED5B8`; warn/unlocked `#FF9F6B`; cool/AC `#53AAFF`; light-warm `#FFB13D`→`#FFE08A`.
- **Color swatch palette (Lights):** `#FFD9A0 #FF9F6B #FF6B8A #B06BFF #7B6CFF #53AAFF #2ED5B8 #FFF3DF`.
- **Radii:** cards 22–24, list rows 14, pills/buttons 11–16, toggles 14–16.
- **Left nav rail:** 74px wide; icon buttons 46×46 r14; active = accent gradient + glow; inactive = `rgba(255,255,255,.05)`.

## 3. LVGL translation techniques (web → embedded)

| Design feature | LVGL approach |
|---|---|
| Brightness/climate **conic ring** (270° arc) | `lv_arc` — start 135°, sweep to value; set arc color to the light hex / mode color; thick rounded ends; center label for `%`/`°` |
| Horizontal **slider with gradient fill** | `lv_slider` styled, or a bar + knob; indicator `bg_grad` |
| **Gradient buttons / toggles** | `bg_grad_color` 2-stop (teal→purple); toggle = `lv_obj` w/ animated knob via `lv_anim` |
| **Backdrop blur glass** | not available → solid translucent panel over baked bg |
| **Aurora glow blobs** | baked into background PNG (done) |
| **SVG line icons** | map to **MDI glyph font** equivalents (home, lightbulb, thermometer, disc/play, shield, wifi, lock, chevrons) — espcontrol already ships an MDI glyph font + `icons.h` |
| Album art / camera | `online_image`/`artwork_image` JPEG (espcontrol has this path) |
| Eq bars / pulse dots (decorative) | optional `lv_anim` on height/opacity; safe to omit for v1 |

## 4. Per-screen widget trees (build spec)

Common: full-screen `lv_img` background (baked PNG) on every page + a 74px nav rail overlay (or a shared top layer). Page switching via `lvgl.page.show`.

### Lights (hero — build first; scaffold in `lights_screen.lvgl.yaml`)
- Left column 266px: title "Lights" + subtitle; vertical list of light rows (dot + name/room + bri%/Off). Selected row = lighter bg + hairline.
- Right glass panel: header (name/room + on/off toggle); centered **`lv_arc`** brightness ring (212px) with big `%` + "Brightness"; brightness **slider**; COLOR swatch row (8 circles); WARM↔COOL temperature slider.
- Bindings: list from the 8 lights (entity-bindings doc); arc/slider ↔ `brightness_pct`; swatches ↔ `light.turn_on rgb_color`; CT slider ↔ `color_temp` (only for color/CT-capable bulbs).

### Climate (blocked on entity)
- Big 268px `lv_arc` ring (mode color), "Now 73° / 72° / Cooling to 72°"; − / + setpoint buttons; Cool/Heat/Auto/Off segmented; Humidity + Outdoor stat cards; Fan Auto/On; Comfort Home/Away/Sleep.
- ❌ no `climate.` entity — see handoff question. Outdoor temp can bind to `weather.forecast_home`.

### Media (Sonos + TV remote)
- Left 452px card: album-art block, track/artist, progress bar (1:38/4:12), transport row (shuffle/prev/**play-pause gradient**/next/repeat), volume slider. Bind to `media_player.living_room` (Sonos).
- Right: SPEAKERS list with per-zone toggles (Sonos + Juke zones) → join/unjoin; UP NEXT queue.
- TV remote panel (from media.png): source buttons, D-pad+OK, VOL±, Netflix/YouTube → `media_player.lg_g3_living_room_2` + `webostv.button`/`webostv.command`.

### Security
- Two lock cards (front/back) with ring icon, status color, Lock/Unlock button → `lock.front_door`/`lock.back_door`. Presence card (`person.ben`). Occupancy/sensor list → ZHA motion/contact + battery.

### Network
- Internet speed card, SSID client-count cards, Synology storage bar + temps, Access-Points list. Bind to UniFi + Synology sensors. "This Panel" → own WiFi + `binary_sensor.espcontrol_7inch_d39c62_online`.

### Home (dashboard landing)
- Big clock + greeting + weather/presence/secured chips; now-playing mini card; climate mini; doors&sensors; scenes grid. Scenes ❌ (only one scene entity exists — create scenes or map to scripts).

## 5. Fonts & glyphs plan
- ESPHome `font:` entries for Sora at the consolidated sizes. Include only needed glyph ranges to save flash.
- Icons: reuse espcontrol's MDI glyph font; pick MDI codepoints matching each SVG (lightbulb `\U000F0335`, thermostat, play/pause, shield, wifi, lock/lock-open, chevrons, etc.).

## 6. Build order (phased, each: edit → `esphome config` → flash via esptool-js → OTA after)
1. Theme + baked bg + nav rail + page-switch skeleton.
2. **Lights** screen (real bindings).
3. Media + Sonos zones + TV remote.
4. Security (locks/presence/sensors).
5. Network (UniFi/Synology).
6. Home landing.
7. Climate (once a `climate.` entity exists).

## 7. Flashing (recap from earlier)
- First flash of *our* firmware = browser via [esptool-js](https://espressif.github.io/esptool-js/) with the compiled factory `.bin` (usbipd kept failing; stock browser install already proved the hardware). Then **OTA over WiFi** for every iteration after.
- Compile in WSL: `esphome -s espcontrol_component_url file:///home/bdw547/espcontrol compile devices/guition-esp32-p4-jc1060p470/dev.yaml` (first build pulls ESP-IDF; long).
