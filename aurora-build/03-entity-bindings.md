# Aurora → your real Home Assistant entities

Mapped from `uploads/hass_entities.csv`. ✅ = confident match, ⚠️ = needs your confirmation, ❌ = no entity exists yet.

## Lights screen (left list + detail)
The design featured 8 lights; here are the real matches:

| Design label | Real entity | Conf |
|---|---|---|
| Living Room | `light.living_room_main` | ✅ |
| Office Shelves | `light.office_shelves` (+ `light.office_shelves_2`) | ✅ |
| Kitchen | `light.kitchen_kitchen` | ✅ |
| Reading Lamp | `light.reading_lamp` | ✅ |
| Patio Lights | `light.outdoor_patio_back_porch_lights` | ✅ |
| Office Main | `light.office_main_lights` | ✅ |
| Dining Chandelier | `light.dining_room_chandelier` | ✅ |
| Master Ceiling | `light.master_bedroom_ceiling_fan_light` | ⚠️ pick the right one |

- Brightness arc/slider → `light` `brightness` attr; toggle → `light.toggle`; brightness set → `light.turn_on` with `brightness_pct`.
- **Color swatches / warm-cool slider**: most of your lights are **Lutron Caséta dimmers (no color)**. Color/CT controls only apply to `light.office_shelves` (Tuya) and a few SmartThings/Hue-type bulbs. Plan: show color row **only when the selected light supports it** (`supported_color_modes`), else hide it. ⚠️ confirm which bulbs are color-capable.
- You have ~40 lights total — we'll show a curated, room-grouped set (the Lumen grid variant scales better for "show everything").

## Media screen
| Element | Real entity | Conf |
|---|---|---|
| Now-playing / transport / volume | `media_player.living_room` (Sonos) | ⚠️ or `media_player.spotify_ben_walton` |
| Speaker: Living Room (Sonos Era 300) | `media_player.living_room` | ✅ |
| Speaker: Kitchen (Sonos One) | `media_player.kitchen` | ✅ |
| Speaker: Dining (Juke Zone) | `media_player.living_room_juke_audio_dining_room_zone` | ✅ |
| Speaker: Office (Juke Zone) | `media_player.living_room_juke_audio_office_zone` | ✅ |
| Speaker: Patio (Juke Zone) | `media_player.living_room_juke_audio_patio_zone` | ✅ |

- Multiroom toggles → Sonos: `media_player.join`/`unjoin`; Juke zones: per-zone `media_player.turn_on/off` or grouping. ⚠️ confirm grouping semantics you want.

## TV remote (Media screen, right panel)
| Element | Real entity | Conf |
|---|---|---|
| TV | `media_player.lg_g3_living_room_2` (webOS) | ✅ |
| D-pad / OK / back / home | `webostv.button` service (UP/DOWN/LEFT/RIGHT/ENTER/BACK/HOME) | ✅ |
| Volume ± / mute | `media_player.volume_up/down/mute` | ✅ |
| Source buttons (Apple TV, Roku, Xbox…) | `media_player.select_source` | ⚠️ confirm exact source names |
| App shortcuts (Netflix, YouTube) | `webostv.command` (launch app) | ✅ |
| Power | `media_player.turn_off` (+ Wake-on-LAN to turn on) | ⚠️ |

## Locks / Security
| Element | Real entity | Conf |
|---|---|---|
| Front Door | `lock.front_door` (Schlage) | ✅ |
| Back Door | `lock.back_door` (Schlage) | ✅ |
| Presence "Ben home" | `person.ben` | ✅ |
| Motion sensors | `binary_sensor.motion_sensor_a/b/c_occupancy` (ZHA) | ✅ |
| Door contact | `binary_sensor.door_sensor_1_opening` (ZHA) | ✅ |
| Lock battery / auto-lock | Schlage battery sensors + `select.front_door_auto_lock_time` | ✅ |

## Home screen
| Element | Real entity | Conf |
|---|---|---|
| Weather chip "94° Austin" | `weather.forecast_home` (met) | ✅ |
| Outdoor temp (Climate card) | `weather.forecast_home` temperature attr | ✅ |
| Presence | `person.ben` | ✅ |
| Scenes (Morning/Movie/Dinner/Good Night) | ❌ only `scene.smart_bridge_2_front_exterior_lights` exists | ❌ create scenes in HA, or map to scripts/automations |

## Climate screen
❌ **No `climate.` entity in your export.** The design itself notes "No thermostat in HA export — sample ecobee data."
- Options: (a) add the ecobee integration in HA so a `climate.*` entity exists, then bind the ring/setpoint/modes; (b) drop the Climate screen for v1; (c) keep it read-only with outdoor temp from `weather.forecast_home`.
- ⚠️ **Decision needed.**

## Network screen
UniFi + Synology entities exist and map well:
- Download/Upload → `sensor.walton_synology_download_throughput` / `_upload_throughput` (NAS) — ⚠️ for *WAN* speed you'll want the UniFi WAN throughput sensors instead (present under `unifi`).
- Synology volume → `sensor.walton_synology_volume_1_used_space` / `_volume_used` / `_status` / `_average_disk_temp`.
- Access points / clients → UniFi device + client-count sensors.
- "This Panel" → `binary_sensor.espcontrol_7inch_d39c62_online` + the panel's own WiFi signal.

## Fans (bonus, present in export)
`fan.living_room_pendant`, `fan.office_ceiling_fan`, `switch.outdoor_patio_ceiling_fan`.
