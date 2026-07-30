---
title: Home Assistant Packages
description: "The small Home Assistant packages that unlock Aurora's Spotify library, notification center, calendar, TV trackpad, and live camera — with copy-paste install steps."
---

# Home Assistant packages

Aurora controls **your** Home Assistant entities, so most screens only need the integrations you already have (`light.*`, `lock.*`, `person.*`, a weather entity…). A few of the richer screens need a small **package** installed on the HA side, because the panel can't talk to Spotify, Google, or your TV's pointer socket directly — Home Assistant does, and the package packs the data into sensors the panel reads.

**Enable package loading once** in your `configuration.yaml`:

```yaml
homeassistant:
  packages: !include_dir_named packages
```

Then install whichever of these you want:

| Feature | Requires |
|---|---|
| Spotify player & speaker picker | The **SpotifyPlus** integration (HACS: `thlucas1/homeassistantcomponent_spotifyplus`), authenticated to Spotify Premium |
| Spotify **library** (browse playlists/tracks), queue card, saved-track heart | The `aurora_spotify_library.yaml` package below |
| Notification center | The `aurora_notifications.yaml` package below |
| Calendar cards | The `aurora_calendar.yaml` package below |
| TV remote | The **webOS TV** integration for your LG TV |
| TV **trackpad** (cursor / scroll) | The LG pointer bridge below |
| Weather radar card | Nothing — works out of the box (public RainViewer tiles) |

## Spotify library

The Media **Library** (browse playlists → tracks → tap to play in a room) needs this package — Home Assistant fetches the data and exposes it as sensors.

1. Copy `aurora-build/aurora_spotify_library.yaml` to your HA config at `packages/aurora_spotify_library.yaml`.
2. Edit the `media_player.spotifyplus_*` entity in that file to match yours.
3. Check config → **Restart HA**.
4. Run the action **`script.aurora_spotify_refresh_playlists`** once to populate your playlists.

The package also powers the Spotify queue card and saved-track heart — queue and favorite state refresh automatically when the active track changes. In panel Settings you can also choose **Spotify** as the screensaver mode.

## Notification center

Keeps the five newest panel alerts in Home Assistant, restores them after an HA restart, and wakes the panel for `warning` or `critical` alerts (informational alerts stay in the queue without waking the screen).

1. Copy `aurora-build/aurora_notifications.yaml` to `packages/aurora_notifications.yaml`.
2. Check config → restart HA.
3. Add **Notifications** as a card or top-bar item in the [configurator](/setup/configurator).

Send an alert from any automation:

```yaml
actions:
  - action: script.aurora_notify
    data:
      title: Front door
      message: Someone is at the door.
      severity: warning
      camera: true
```

An alert can optionally show an action button (limited to the `light`, `switch`, `lock`, `cover`, `fan`, `script`, `scene`, `button`, and `input_boolean` domains):

```yaml
actions:
  - action: script.aurora_notify
    data:
      title: Garage left open
      message: The garage has been open for 15 minutes.
      severity: critical
      action_label: Close
      action: cover.close_cover
      target: cover.garage_door
```

`script.aurora_notifications_clear` clears the queue — the panel's **Clear all** button calls the same script.

## Calendar

The calendar cards (month grid, week board, agenda list) show every calendar you've linked in Home Assistant.

1. Link your calendars in HA (both produce `calendar.*` entities):
   - **Google** — Settings → Devices & Services → Add Integration → **Google Calendar**, complete the OAuth flow, then enable the per-calendar entities you want.
   - **Apple / iCloud** — Add Integration → **CalDAV** with URL `https://caldav.icloud.com`, your Apple ID email, and an *app-specific password* from [account.apple.com](https://account.apple.com).
2. Copy `aurora-build/aurora_calendar.yaml` to `packages/aurora_calendar.yaml`.
3. Edit the `entity_id:` list under `&aurora_cal_entities` in that file to your `calendar.*` entities — the one edit point; each calendar gets its own accent color on the panel.
4. Check config → restart HA.
5. Add a **Calendar** card in the configurator (Info group). Tap any date in the month grid for that day's events; the **‹ ›** buttons browse months; events refresh every 15 minutes or via the card's refresh button.

## LG Magic-Remote trackpad

The TV remote's **trackpad page** (a real drag-to-move cursor + scroll wheel) needs the **LG pointer bridge** — a pyscript module that opens webOS's Magic-Remote pointer socket, which the standard `webostv` integration can't. Install it from `aurora-build/lg_pointer_bridge/` — that folder's README has the steps; the TV's host and client key are discovered automatically from your `webostv` integration.

## Camera & wake-on-approach

Nothing to install on the HA side beyond adding a **Generic Camera**: the panel's onboard camera hardware-encodes H.264 and serves RTSP directly at

```
rtsp://<panel-ip>:8554/cam
```

The same frames drive the panel's night-time **wake-on-approach** — configurable on the panel under Settings.
