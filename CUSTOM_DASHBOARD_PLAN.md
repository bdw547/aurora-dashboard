# Custom Dashboard — Feature Design (JC1060P470 fork)

> **⚠️ Historical design doc — superseded by Aurora.** This is an early feature
> exploration written against the **upstream espcontrol button-grid engine**
> (`components/espcontrol/`, `common/config/card_contract.json`, `scripts/build.py`).
> The dashboard was ultimately built as **Aurora** — an independent ESPHome + LVGL
> rewrite (`devices/guition-esp32-p4-jc1060p470/aurora.yaml`) that does **not** use that
> engine — so the file paths and the "how a card type is wired" recipe below no longer
> describe the shipping product. In Aurora, card types are `c_*` functions in the `CTRL`
> dict of `aurora-build/configurator/gen.py`. Several features below have since **shipped
> in Aurora** (see the per-feature status notes and the **[README](README.md)** / the
> configurator's `gen.py`). Kept for history, not as current build guidance.

Grounded in code exploration. Target device: `devices/guition-esp32-p4-jc1060p470/`.
Firmware = ESPHome + header-only C++/LVGL in `components/espcontrol/`.

## How a card type is wired (the recipe, confirmed)

1. `common/config/card_contract.json` — declare the card type + its options.
2. `scripts/build.py` regenerates `components/espcontrol/button_grid_contract_generated.h`
   (firmware) and the web-configurator JS bundles.
3. `components/espcontrol/button_grid_config.h` — parse saved compact config → `ParsedCfg`.
4. `components/espcontrol/button_grid_grid.h` — **visual setup dispatch** is an
   `if (p.type == "...")` chain (weather `:337`, vacuum `:395`, media `:416`) calling
   `setup_<type>_card(...)`; **runtime/subscription dispatch** is a parallel chain (~`:1146`).
5. `components/espcontrol/button_grid_modal.h` — add a value to `ControlModalKind`
   (enum `:16-30`, 13 kinds today) for cards that open a full-screen modal; open via
   `control_modal_open_shell(...)` (`:482`).
6. New card behaviour lives in a new `button_grid_<type>.h`, included from `button_grid.h`.

Theme/restyle knobs: device `packages.yaml` substitutions override the shared
`common/theme/button.yaml` defaults (colors, `radius`, `padding`, `main_page_card_gap`,
font roles). Background is `bg_color` in `device/lvgl.yaml:126`.

## Feature 1 — Network status (LOW effort; partly built)

Today (`components/espcontrol/network_status.h`): a signal-banded Wi-Fi icon in the
clock bar + a tap modal (`network_status_open_modal` `:131`) showing device name, IP,
firmware. SSID/signal-%/gateway/MAC/uptime are available from ESPHome but not shown.

Plan: enrich `network_status_open_modal` content (add SSID, RSSI %, gateway, uptime
rows via the existing `network_status_add_center_label` helper) and restyle the modal.
No new card type, no parser/contract changes. Files: `network_status.h` only.

## Feature 2 — Restyle / theme (LOW effort; STARTED)

Done as a first pass in `devices/guition-esp32-p4-jc1060p470/packages.yaml`:
`radius 10→18`, `card_gap 10→14`, `button_control_color → 0x1B2030` (slate),
`button_on_text_color → 0x0E1116`. Next: background in `device/lvgl.yaml`, optional
soft shadow, per-card accent colors. All reversible token edits.

## Feature 3 — Weather animation (MEDIUM effort)

> **Status: partially shipped in Aurora.** A rich full-forecast weather card shipped
> (gen.py `c_weather`). The animated-primitives idea below is still unbuilt.

Today: static MDI glyph. `weather_icon_for_state()` maps ~29 conditions to glyphs
(`button_grid_config.h:1498`); `subscribe_weather_state()` updates the label
(`button_grid_subscriptions.h:194`); `setup_weather_card()` builds it
(`button_grid_cards.h:392`).

Constraint: `lv_gif`/`lv_animimg` widgets are NOT in the build, but `lv_anim` IS
(used for button fades, `button_grid_cards.h:505`). So animate by driving LVGL
properties of drawn primitives/labels: drifting cloud labels, falling-rain lines
(animate y + opacity), pulsing/rotating sun. Implement as a new
`setup_weather_animated_card()` (or a `weather` option `animated`) that builds a small
object tree and starts `lv_anim_t` timelines keyed off the condition string from the
existing subscription. No new HA bindings. Files: new `button_grid_weather.h`, dispatch
in `button_grid_grid.h`, option in `card_contract.json`.
(Alternative: enable `lv_gif` in the LVGL build + ship condition GIFs — heavier flash/RAM.)

## Feature 4 — Sonos / media library browsing (MEDIUM-HIGH effort)

> **Status: SHIPPED in Aurora** — as gen.py cards, not the C++ `MEDIA_BROWSE` modal below.
> Sources via `sonos_sources`/`tv_sources` + `media_player.select_source`; a full Spotify
> suite `spotify_playlists` / `spotify_tracks` (scrollable tap-to-play) / `spotify_speakers`
> (dynamic Connect-speaker picker reading `source_list`); Sonos favorites via `sonos_fav`.

Today: `media` card = transport + volume arc + now-playing (art via `artwork_image`
component, `button_grid_image.h` `ImageCardCtx`). Services used:
`media_player.media_play_pause|previous|next|volume_set|media_seek`
(`button_grid_actions.h:436-472`).

Missing: source/favorites/playlist browsing. Plan:
- Add `MEDIA_BROWSE` to `ControlModalKind`.
- New `MediaBrowseCtx` + `setup_media_browse_card()` / `subscribe_media_browse_state()`
  paralleling the existing media functions (`button_grid_media.h:437-491`, `:518-544`).
- HA: subscribe `source_list` attribute; call `media_player.select_source` (simple,
  works great for Sonos favorites exposed as sources) and/or `media_player.play_media`
  + `browse_media` for full library trees.
- Reuse `ImageCardCtx`/`artwork_image` for thumbnails.
- Wire setup/subscribe in `button_grid_grid.h`; new modal open in `button_grid_actions.h`.
Start with **source-list selection** (1 attribute + 1 service) before full browse trees.

## Feature 5 — TV remote (HIGH effort; fully net-new)

> **Status: SHIPPED in Aurora** — built in gen.py, not as the C++ `remote` card / `REMOTE_CONTROL`
> modal below. `c_tvremote` gives a full LG webOS remote (D-pad, transport, volume/mute, channel,
> app shortcuts that highlight the running app) and `gen_trackpad_page` a Magic-Remote trackpad
> (cursor + scroll + Back/Home/Volume) over a pyscript pointer bridge (`pyscript.lg_pointer_button`).

Confirmed net-new: no `remote.` usage anywhere in firmware/contract.

Plan: new `remote` card type (full recipe above). Card opens a `REMOTE_CONTROL` modal
with a D-pad, OK/back/home, volume ±, channel ±, power, and input/source buttons.
Buttons call HA services: `remote.send_command` (`command`, `device`) for IR/CEC remotes,
or `media_player.*` for media-player-backed TVs (webOS/Android TV). Config options:
entity, button-set preset, custom command list. Files: `card_contract.json`,
`button_grid_contract_generated.h` (regen), `button_grid_config.h`, new
`button_grid_remote.h`, `button_grid.h`, `button_grid_grid.h` (both dispatches),
`button_grid_modal.h` (`REMOTE_CONTROL`), `button_grid_actions.h` (service calls),
plus web-configurator regen via `scripts/build.py`.

## Recommended build order

1. Restyle (in progress) — instant visual payoff, lowest risk.
2. Network status enrichment — contained, single file.
3. Weather animation — self-contained visual feature, no HA plumbing.
4. Sonos source/favorites selection → then full library browse.
5. TV remote — largest, exercises the full add-a-card pipeline.

Each feature: edit → `scripts/build.py` (if contract/manifest touched) → `esphome config`
to parse-check → flash to panel over OTA → verify on screen. Visual verification
requires the physical panel (no LVGL desktop simulator here).
