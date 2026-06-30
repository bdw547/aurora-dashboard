#!/usr/bin/env python3
"""Aurora Configurator (v1) — a local, no-code entity-rebinding wizard.

Run it from the repo with ESPHome's venv active:

    python3 aurora-build/configurator/serve.py

then open http://localhost:8765 in a browser. It reads the `ent_*` entity
slots from the panel firmware, lists your Home Assistant entities (you supply
your HA URL + a long-lived access token), lets you map each slot to one of your
entities, writes the choices back into the firmware, and flashes the panel
over WiFi.

Phase 2 (planned): a drag-and-drop home-screen builder with live preview.
"""
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# Prefer the esphome from the same venv that launched us; fall back to PATH.
_VENV_ESPHOME = os.path.join(os.path.dirname(sys.executable), "esphome")
ESPHOME = _VENV_ESPHOME if os.path.exists(_VENV_ESPHOME) else "esphome"
YAML = os.path.normpath(os.path.join(
    HERE, "..", "..", "devices", "guition-esp32-p4-jc1060p470", "aurora.yaml"))
PORT = 8765

# Slot metadata: which HA domain each binding expects + a friendly label/group.
SLOTS = [
    ("ent_light_living",         "light",        "Lights",   "Living Room — Main"),
    ("ent_light_kitchen",        "light",        "Lights",   "Kitchen"),
    ("ent_light_dining",         "light",        "Lights",   "Dining Room"),
    ("ent_light_office_shelves", "light",        "Lights",   "Office — Shelves"),
    ("ent_light_office_main",    "light",        "Lights",   "Office — Main"),
    ("ent_light_office_desk",    "light",        "Lights",   "Office — Desk Lamp"),
    ("ent_light_reading",        "light",        "Lights",   "Master — Reading Lamp"),
    ("ent_light_master",         "light",        "Lights",   "Master — Ceiling Light"),
    ("ent_light_patio",          "light",        "Lights",   "Patio Lights"),
    ("ent_fan_living",           "fan",          "Fans",     "Living Room — Ceiling Fan"),
    ("ent_fan_office",           "fan",          "Fans",     "Office — Ceiling Fan"),
    ("ent_switch_putting_green", "switch",       "Switches", "Putting Green"),
    ("ent_lock_front",           "lock",         "Security", "Front Door lock"),
    ("ent_lock_back",            "lock",         "Security", "Back Door lock"),
    ("ent_media_spotify",        "media_player", "Media",    "Spotify (SpotifyPlus)"),
    ("ent_media_tv",             "media_player", "Media",    "LG TV (webOS)"),
    ("ent_person",               "person",       "Presence", "Primary person"),
    ("ent_weather",              "weather",      "Climate",  "Weather entity"),
    ("ent_nas_status",           "sensor",       "Network",  "Synology — status"),
    ("ent_nas_used",             "sensor",       "Network",  "Synology — volume used"),
]


def read_slots():
    text = open(YAML, encoding="utf-8").read()
    out = []
    for var, domain, group, label in SLOTS:
        m = re.search(rf'^  {var}:\s*"([^"]*)"', text, re.M)
        out.append({"var": var, "domain": domain, "group": group,
                    "label": label, "value": m.group(1) if m else ""})
    return out


def write_bindings(bindings):
    text = open(YAML, encoding="utf-8").read()
    n = 0
    for var, ent in bindings.items():
        if not re.fullmatch(r"[a-z_]+\.[A-Za-z0-9_]+", ent or ""):
            continue  # skip empty/invalid
        new, c = re.subn(rf'^(  {re.escape(var)}:\s*)"[^"]*"',
                         rf'\g<1>"{ent}"', text, count=1, flags=re.M)
        if c:
            text = new
            n += 1
    open(YAML, "w", encoding="utf-8").write(text)
    return n


# ----- Phase 2: home-screen 2x2 grid builder (drag-and-drop reorder) -----
HOME_CELLS = [(412, 104), (716, 104), (412, 338), (716, 338)]  # TL, TR, BL, BR
GRID_START = "# >>> AURORA_HOME_GRID"
GRID_END = "# <<< AURORA_HOME_GRID"

CARD_META = {
    "climate": {"name": "Climate", "hint": "Outdoor temp + condition"},
    "lights":  {"name": "Lights",  "hint": "Tap to control"},
    "doors":   {"name": "Doors & Sensors", "hint": "Locks + presence"},
    "quick":   {"name": "Quick",   "hint": "Rooms / Media / Network / Library"},
    "media":   {"name": "Media",   "hint": "Now playing"},
    "weather": {"name": "Weather", "hint": "Forecast & radar"},
}
# Cards whose widget ids are referenced by HA sensor handlers elsewhere (live
# temp/condition/locks/presence). They must stay present or those lambdas hit an
# undefined id and the build fails — so every layout must include them.
REQUIRED_CARDS = ("climate", "doors")

CARD_TEMPLATES = {
    "climate": '''        - obj:
            # aurora-card: climate
            x: {X}
            y: {Y}
            width: 288
            height: 218
            styles: st_glass
            scrollable: false
            clickable: true
            on_click: [lvgl.page.show: page_weather]
            widgets:
              - label: {{ text: "CLIMATE", x: 4, y: 2, text_font: f_small, text_color: 0x8A8F9E }}
              - label: {{ id: lbl_home_wx_icon, text: "", align: top_right, x: -6, y: 0, text_font: f_icon, text_color: 0xFFCE54 }}
              - label: {{ id: lbl_home_temp_big, text: "--", x: 2, y: 46, text_font: f_display, text_color: 0xEEF0F6 }}
              - label: {{ id: lbl_home_cond, text: "--", x: 4, y: 138, text_font: f_body, text_color: 0x2ED5B8 }}
              - label: {{ text: "Outdoor", x: 4, y: 168, text_font: f_small, text_color: 0x8A8F9E }}
''',
    "lights": '''        - obj:
            # aurora-card: lights
            x: {X}
            y: {Y}
            width: 288
            height: 218
            styles: st_glass
            scrollable: false
            clickable: true
            on_click: [lvgl.page.show: page_lights]
            widgets:
              - label: {{ text: "\\U000F0335", x: 8, y: 8, text_font: f_icon, text_color: 0xFFCE54 }}
              - label: {{ text: "Lights", x: 50, y: 16, text_font: f_title, text_color: 0xEEF0F6 }}
              - label: {{ text: "Tap to control", x: 8, y: 170, text_font: f_small, text_color: 0x8A8F9E }}
''',
    "doors": '''        - obj:
            # aurora-card: doors
            x: {X}
            y: {Y}
            width: 288
            height: 218
            styles: st_glass
            scrollable: false
            clickable: true
            on_click: [lvgl.page.show: page_security]
            widgets:
              - label: {{ text: "DOORS & SENSORS", x: 4, y: 2, text_font: f_small, text_color: 0x8A8F9E }}
              - label: {{ text: "Front Door", x: 4, y: 50, text_font: f_body, text_color: 0xEEF0F6 }}
              - label: {{ id: lbl_home_lock_front, text: "--", x: -4, y: 50, align: top_right, text_font: f_body, text_color: 0x8A8F9E }}
              - label: {{ text: "Back Door", x: 4, y: 92, text_font: f_body, text_color: 0xEEF0F6 }}
              - label: {{ id: lbl_home_lock_back, text: "--", x: -4, y: 92, align: top_right, text_font: f_body, text_color: 0x8A8F9E }}
              - label: {{ text: "Presence", x: 4, y: 134, text_font: f_body, text_color: 0xEEF0F6 }}
              - label: {{ id: lbl_home_presence2, text: "--", x: -4, y: 134, align: top_right, text_font: f_body, text_color: 0x2ED5B8 }}
''',
    "quick": '''        - obj:
            # aurora-card: quick
            x: {X}
            y: {Y}
            width: 288
            height: 218
            styles: st_glass
            pad_all: 12
            scrollable: false
            widgets:
              - label: {{ text: "QUICK", x: 2, y: 0, text_font: f_small, text_color: 0x8A8F9E }}
              - button:
                  x: 0
                  y: 30
                  width: 122
                  height: 62
                  bg_color: 0x161B24
                  scrollable: false
                  widgets: [label: {{ text: "Rooms", align: center, text_font: f_body, text_color: 0xEEF0F6 }}]
                  on_click: [lvgl.page.show: page_rooms]
              - button:
                  x: 132
                  y: 30
                  width: 122
                  height: 62
                  bg_color: 0x161B24
                  scrollable: false
                  widgets: [label: {{ text: "Media", align: center, text_font: f_body, text_color: 0xEEF0F6 }}]
                  on_click: [lvgl.page.show: page_media]
              - button:
                  x: 0
                  y: 102
                  width: 122
                  height: 62
                  bg_color: 0x161B24
                  scrollable: false
                  widgets: [label: {{ text: "Network", align: center, text_font: f_body, text_color: 0xEEF0F6 }}]
                  on_click: [lvgl.page.show: page_network]
              - button:
                  x: 132
                  y: 102
                  width: 122
                  height: 62
                  bg_color: 0x161B24
                  scrollable: false
                  widgets: [label: {{ text: "Library", align: center, text_font: f_body, text_color: 0xEEF0F6 }}]
                  on_click: [lvgl.page.show: page_library]
''',
    "media": '''        - obj:
            # aurora-card: media
            x: {X}
            y: {Y}
            width: 288
            height: 218
            styles: st_glass
            scrollable: false
            clickable: true
            on_click: [lvgl.page.show: page_media]
            widgets:
              - label: {{ text: "\\U000F075A", x: 8, y: 8, text_font: f_icon, text_color: 0x7B6CFF }}
              - label: {{ text: "Media", x: 50, y: 16, text_font: f_title, text_color: 0xEEF0F6 }}
              - label: {{ text: "Now playing", x: 8, y: 170, text_font: f_small, text_color: 0x8A8F9E }}
''',
    "weather": '''        - obj:
            # aurora-card: weather
            x: {X}
            y: {Y}
            width: 288
            height: 218
            styles: st_glass
            scrollable: false
            clickable: true
            on_click: [lvgl.page.show: page_weather]
            widgets:
              - label: {{ text: "\\U000F0599", x: 8, y: 8, text_font: f_icon, text_color: 0xFFCE54 }}
              - label: {{ text: "Weather", x: 50, y: 16, text_font: f_title, text_color: 0xEEF0F6 }}
              - label: {{ text: "Forecast", x: 8, y: 170, text_font: f_small, text_color: 0x8A8F9E }}
''',
}


def _card_type(block):
    # Generated cards carry an explicit "# aurora-card: <type>" marker; fall
    # back to page-link detection for any legacy block written before markers.
    m = re.search(r"#\s*aurora-card:\s*(\w+)", block)
    if m and m.group(1) in CARD_TEMPLATES:
        return m.group(1)
    if "page_climate" in block: return "climate"
    if "page_lights" in block: return "lights"
    if "page_security" in block: return "doors"
    if "page_rooms" in block: return "quick"   # quick card carries a page_media button
    if "page_weather" in block: return "weather"
    if "page_media" in block: return "media"
    return "quick"


def read_home_layout():
    text = open(YAML, encoding="utf-8").read()
    region = text.split(GRID_START, 1)[1].split(GRID_END, 1)[0]
    # split into obj blocks; map each to (cell-index, type) via its x/y
    blocks = ["        - obj:" + b for b in region.split("        - obj:") if b.strip()]
    layout = [None, None, None, None]
    for b in blocks:
        mx = re.search(r"x:\s*(\d+)", b)
        my = re.search(r"y:\s*(\d+)", b)
        if not (mx and my):
            continue
        pos = (int(mx.group(1)), int(my.group(1)))
        if pos in HOME_CELLS:
            layout[HOME_CELLS.index(pos)] = _card_type(b)
    # default if anything missing
    default = ["climate", "lights", "doors", "quick"]
    return [layout[i] or default[i] for i in range(4)]


def write_home_layout(order):
    # 4 cells, each filled by a distinct catalog card. Distinctness avoids
    # duplicate LVGL widget ids from the id-bearing cards (climate/doors).
    if len(order) != 4 or any(k not in CARD_TEMPLATES for k in order):
        raise ValueError("layout must list 4 cards from the catalog")
    if len(set(order)) != 4:
        raise ValueError("each home card may be used at most once")
    missing = [c for c in REQUIRED_CARDS if c not in order]
    if missing:
        raise ValueError("required cards missing (other screens use their state): "
                         + ", ".join(missing))
    text = open(YAML, encoding="utf-8").read()
    cards = "".join(CARD_TEMPLATES[order[i]].format(X=HOME_CELLS[i][0], Y=HOME_CELLS[i][1])
                    for i in range(4))
    head, rest = text.split(GRID_START, 1)
    _, tail = rest.split(GRID_END, 1)
    text = (head + GRID_START + " (generated by the configurator — do not hand-edit)\n"
            + cards + "        " + GRID_END + tail)
    open(YAML, "w", encoding="utf-8").write(text)
    return order


# ===========================================================================
# Dynamic rooms (Phase 1) — generate room pages / picker / state sensors from
# rooms.json into AURORA_ROOM_* marker regions, mirroring the home-grid pattern.
# ===========================================================================
ROOMS_JSON = os.path.join(HERE, "rooms.json")
ROOM_TYPES = {"light", "fan", "switch", "sensor", "lock", "climate", "media", "cover"}
ROOM_MAX_ENTITIES = 6                 # column geometry: x=100+140*i, i in 0..5
ROOM_X0, ROOM_PITCH = 100, 140        # entity-card columns on a room page
PICK_Y0, PICK_PITCH = 110, 78         # room buttons on the picker
ROOM_MAX = 6                          # picker rows that fit (y=110+78*5=500<600)
# (start-marker, end-marker, indent-of-the-end-marker) per generated region.
ROOM_MARKERS = {
    "picker":  ("# >>> AURORA_ROOM_PICKER", "# <<< AURORA_ROOM_PICKER", "              "),
    "pages":   ("# >>> AURORA_ROOM_PAGES",  "# <<< AURORA_ROOM_PAGES",  "    "),
    "bri":     ("# >>> AURORA_ROOM_STATE_SENSOR", "# <<< AURORA_ROOM_STATE_SENSOR", "  "),
    "text":    ("# >>> AURORA_ROOM_STATE_TEXT", "# <<< AURORA_ROOM_STATE_TEXT", "  "),
    "counts":  ("# >>> AURORA_ROOM_COUNTS", "# <<< AURORA_ROOM_COUNTS", "  "),
}
TYPE_ICON = {"fan": "\\U000F0210", "switch": "\\U000F0425"}  # power-button glyphs


def _fmt(tmpl, **kw):
    """Token substitution (avoids YAML brace-escaping that str.format needs)."""
    for k, v in kw.items():
        tmpl = tmpl.replace("%" + k + "%", str(v))
    return tmpl


def slug(eid):
    return re.sub(r"[^a-z0-9_]", "_", (eid or "").lower())


# ---- templates (consistent room-scoped ids: <S> = <roomid>_<entity-slug>) ----
PICK_TMPL = """              - button:
                  x: 26
                  y: %Y%
                  width: 772
                  height: 64
                  styles: st_glass
                  widgets:
                    - label: { text: "%ICON%", align: left_mid, x: 8, text_font: f_icon, text_color: 0x2ED5B8 }
                    - label: { text: "%NAME%", align: left_mid, x: 60, text_font: f_title, text_color: 0xEEF0F6 }
                    - label: { id: lbl_roomcount_%ID%, text: "", align: right_mid, x: -16, text_font: f_small, text_color: 0x8A8F9E }
                  on_click: [lvgl.page.show: page_room_%ID%]
"""

PAGE_HEAD_TMPL = """    - id: page_room_%ID%
      bg_opa: 0
      on_load:
        - lvgl.widget.update: { id: nav_home, bg_color: 0x10121A }
        - lvgl.widget.update: { id: nav_rooms, bg_color: 0x2ED5B8 }
        - lvgl.widget.update: { id: nav_lights, bg_color: 0x10121A }
        - lvgl.widget.update: { id: nav_climate, bg_color: 0x10121A }
        - lvgl.widget.update: { id: nav_media, bg_color: 0x10121A }
        - lvgl.widget.update: { id: nav_security, bg_color: 0x10121A }
        - lvgl.widget.update: { id: nav_network, bg_color: 0x10121A }
        - lvgl.widget.update: { id: nav_settings, bg_color: 0x10121A }
      widgets:
        - image: { src: img_aurora_bg, x: 0, y: 0 }
        - label: { text: "%NAME%", x: 100, y: 26, text_font: f_title, text_color: 0xEEF0F6 }
        - button:
            align: top_right
            x: -12
            y: 12
            width: 104
            height: 44
            radius: 8
            bg_color: 0x1B2230
            border_width: 2
            border_color: 0x2ED5B8
            widgets: [label: { text: "Back", align: center, text_font: f_body, text_color: 0x2ED5B8 }]
            on_click: [lvgl.page.show: page_rooms]
"""

CARD_LIGHT = """        - obj:
            x: %X%
            y: 80
            width: 120
            height: 432
            bg_opa: 0
            border_width: 0
            pad_all: 0
            scrollable: false
            widgets:
              - label: { text: "%L%", x: 0, y: 0, width: 120, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }
              - label: { id: pct_sld_room_%S%, text: "OFF", x: 0, y: 24, width: 120, text_align: center, text_font: f_body, text_color: 0x8A8F9E }
              - slider:
                  id: sld_room_%S%
                  x: 0
                  y: 52
                  width: 120
                  height: 268
                  min_value: 0
                  max_value: 100
                  value: 0
                  radius: 24
                  bg_color: 0x12141C
                  bg_opa: 100%
                  border_width: 2
                  border_color: 0x2A2F3A
                  indicator:
                    bg_color: 0xF2C94C
                    radius: 24
                  knob:
                    bg_color: 0xFFFFFF
                    pad_left: -24
                    pad_right: -24
                    pad_top: -56
                    pad_bottom: -56
                    radius: 2
                  on_release:
                    - if:
                        condition:
                          lambda: 'return x > 0;'
                        then:
                          - homeassistant.action:
                              action: light.turn_on
                              data:
                                entity_id: %E%
                                brightness_pct: !lambda 'return std::to_string((int) x);'
                        else:
                          - homeassistant.action: { action: light.turn_off, data: { entity_id: %E% } }
                  on_value:
                    - lvgl.label.update: { id: pct_sld_room_%S%, text: !lambda 'int v=(int)x; char b[8]; if(v<=0) return std::string("OFF"); snprintf(b,sizeof(b),"%d%%",v); return std::string(b);' }
              - button:
                  id: pwr_ic_%S%
                  align: bottom_mid
                  y: 0
                  width: 120
                  height: 100
                  radius: 24
                  bg_color: 0x12141C
                  border_width: 2
                  border_color: 0x2A2F3A
                  widgets: [label: { id: ic_%S%, text: "\\U000F0425", align: center, text_font: f_icon, text_color: 0x8A8F9E }]
                  on_click: [homeassistant.action: { action: light.toggle, data: { entity_id: %E% } }]
"""

CARD_TOGGLE = """        - obj:
            x: %X%
            y: 80
            width: 120
            height: 432
            bg_opa: 0
            border_width: 0
            pad_all: 0
            scrollable: false
            widgets:
              - label: { text: "%L%", x: 0, y: 0, width: 120, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }
              - label: { id: lbl_st_room_%S%, text: "Off", x: 0, y: 24, width: 120, text_align: center, text_font: f_body, text_color: 0x8A8F9E }
              - obj:
                  id: trk_room_%S%
                  x: 0
                  y: 52
                  width: 120
                  height: 268
                  radius: 24
                  bg_color: 0x12141C
                  border_width: 2
                  border_color: 0x2A2F3A
                  clickable: false
                  scrollable: false
              - slider:
                  id: sld_room_%S%
                  x: 0
                  y: 96
                  width: 120
                  height: 180
                  min_value: 0
                  max_value: 100
                  value: 0
                  bg_opa: 0%
                  indicator:
                    bg_opa: 0%
                  knob:
                    bg_color: 0x56C5DD
                    pad_left: -6
                    pad_right: -6
                    pad_top: -16
                    pad_bottom: -16
                    radius: 18
                  on_release:
                    - if:
                        condition:
                          lambda: 'return x >= 50;'
                        then:
                          - homeassistant.action: { action: %ACT%.turn_on, data: { entity_id: %E% } }
                          - lvgl.slider.update: { id: sld_room_%S%, value: 100 }
                        else:
                          - homeassistant.action: { action: %ACT%.turn_off, data: { entity_id: %E% } }
                          - lvgl.slider.update: { id: sld_room_%S%, value: 0 }
              - button:
                  id: pwr_ic_%S%
                  align: bottom_mid
                  y: 0
                  width: 120
                  height: 100
                  radius: 24
                  bg_color: 0x12141C
                  border_width: 2
                  border_color: 0x2A2F3A
                  widgets: [label: { id: ic_%S%, text: "%TICON%", align: center, text_font: f_icon, text_color: 0x8A8F9E }]
                  on_click: [homeassistant.action: { action: %ACT%.toggle, data: { entity_id: %E% } }]
"""

# Read-only sensor card: live value + unit in a panel, no control (#21).
CARD_SENSOR = """        - obj:
            x: %X%
            y: 80
            width: 120
            height: 432
            bg_opa: 0
            border_width: 0
            pad_all: 0
            scrollable: false
            widgets:
              - label: { text: "%L%", x: 0, y: 0, width: 120, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }
              - obj:
                  x: 0
                  y: 52
                  width: 120
                  height: 360
                  radius: 24
                  bg_color: 0x12141C
                  border_width: 2
                  border_color: 0x2A2F3A
                  clickable: false
                  scrollable: false
                  widgets:
                    - label: { id: val_room_%S%, text: "--", align: center, y: -14, width: 112, text_align: center, text_font: f_head, text_color: 0xEEF0F6 }
                    - label: { id: unit_room_%S%, text: "", align: center, y: 26, width: 112, text_align: center, text_font: f_small, text_color: 0x8A8F9E }
"""

# Lock card: state label + a big lock/unlock toggle button (#21).
CARD_LOCK = """        - obj:
            x: %X%
            y: 80
            width: 120
            height: 432
            bg_opa: 0
            border_width: 0
            pad_all: 0
            scrollable: false
            widgets:
              - label: { text: "%L%", x: 0, y: 0, width: 120, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }
              - label: { id: lbl_st_room_%S%, text: "--", x: 0, y: 24, width: 120, text_align: center, text_font: f_body, text_color: 0x8A8F9E }
              - button:
                  id: pwr_ic_%S%
                  x: 0
                  y: 52
                  width: 120
                  height: 360
                  radius: 24
                  bg_color: 0x12141C
                  border_width: 2
                  border_color: 0x2A2F3A
                  widgets: [label: { id: ic_%S%, text: "\\U000F033E", align: center, text_font: f_icon, text_color: 0x8A8F9E }]
                  on_click: [homeassistant.action: { action: lock.toggle, data: { entity_id: %E% } }]
"""

# Climate card: target-temp slider (50-90F) + current temp readout (#21).
CARD_CLIMATE = """        - obj:
            x: %X%
            y: 80
            width: 120
            height: 432
            bg_opa: 0
            border_width: 0
            pad_all: 0
            scrollable: false
            widgets:
              - label: { text: "%L%", x: 0, y: 0, width: 120, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }
              - label: { id: pct_sld_room_%S%, text: "--°", x: 0, y: 24, width: 120, text_align: center, text_font: f_body, text_color: 0x8A8F9E }
              - slider:
                  id: sld_room_%S%
                  x: 0
                  y: 52
                  width: 120
                  height: 268
                  min_value: 50
                  max_value: 90
                  value: 70
                  radius: 24
                  bg_color: 0x12141C
                  bg_opa: 100%
                  border_width: 2
                  border_color: 0x2A2F3A
                  indicator:
                    bg_color: 0x4F91FF
                    radius: 24
                  knob:
                    bg_color: 0xFFFFFF
                    pad_left: -24
                    pad_right: -24
                    pad_top: -56
                    pad_bottom: -56
                    radius: 2
                  on_release:
                    - homeassistant.action:
                        action: climate.set_temperature
                        data:
                          entity_id: %E%
                          temperature: !lambda 'return std::to_string((int) x);'
                  on_value:
                    - lvgl.label.update: { id: pct_sld_room_%S%, text: !lambda 'char b[8]; snprintf(b, sizeof(b), "%d°", (int) x); return std::string(b);' }
              - label: { id: lbl_cur_room_%S%, text: "", align: bottom_mid, y: -28, width: 120, text_align: center, text_font: f_small, text_color: 0x8A8F9E }
"""

# Media card: now-playing title + play/pause + prev/next transport (#21).
CARD_MEDIA = """        - obj:
            x: %X%
            y: 80
            width: 120
            height: 432
            bg_opa: 0
            border_width: 0
            pad_all: 0
            scrollable: false
            widgets:
              - label: { text: "%L%", x: 0, y: 0, width: 120, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }
              - label: { id: lbl_title_room_%S%, text: "--", x: 4, y: 24, width: 112, long_mode: dot, text_align: center, text_font: f_small, text_color: 0x8A8F9E }
              - button:
                  id: pwr_ic_%S%
                  x: 0
                  y: 52
                  width: 120
                  height: 200
                  radius: 24
                  bg_color: 0x12141C
                  border_width: 2
                  border_color: 0x2A2F3A
                  widgets: [label: { id: ic_%S%, text: "\\U000F040A", align: center, text_font: f_icon, text_color: 0xEEF0F6 }]
                  on_click: [homeassistant.action: { action: media_player.media_play_pause, data: { entity_id: %E% } }]
              - button:
                  x: 0
                  y: 262
                  width: 58
                  height: 60
                  radius: 18
                  bg_color: 0x161B24
                  border_width: 0
                  widgets: [label: { text: "\\U000F04AE", align: center, text_font: f_icon, text_color: 0xEEF0F6 }]
                  on_click: [homeassistant.action: { action: media_player.media_previous_track, data: { entity_id: %E% } }]
              - button:
                  x: 62
                  y: 262
                  width: 58
                  height: 60
                  radius: 18
                  bg_color: 0x161B24
                  border_width: 0
                  widgets: [label: { text: "\\U000F04AD", align: center, text_font: f_icon, text_color: 0xEEF0F6 }]
                  on_click: [homeassistant.action: { action: media_player.media_next_track, data: { entity_id: %E% } }]
"""

# Cover card: open / stop / close buttons + state label (#21).
CARD_COVER = """        - obj:
            x: %X%
            y: 80
            width: 120
            height: 432
            bg_opa: 0
            border_width: 0
            pad_all: 0
            scrollable: false
            widgets:
              - label: { text: "%L%", x: 0, y: 0, width: 120, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }
              - label: { id: lbl_st_room_%S%, text: "--", x: 0, y: 24, width: 120, text_align: center, text_font: f_body, text_color: 0x8A8F9E }
              - button:
                  x: 0
                  y: 52
                  width: 120
                  height: 140
                  radius: 24
                  bg_color: 0x12141C
                  border_width: 2
                  border_color: 0x2A2F3A
                  widgets: [label: { text: "\\U000F0143", align: center, text_font: f_icon, text_color: 0xEEF0F6 }]
                  on_click: [homeassistant.action: { action: cover.open_cover, data: { entity_id: %E% } }]
              - button:
                  x: 0
                  y: 196
                  width: 120
                  height: 72
                  radius: 18
                  bg_color: 0x161B24
                  border_width: 0
                  widgets: [label: { text: "\\U000F03E4", align: center, text_font: f_icon, text_color: 0x8A8F9E }]
                  on_click: [homeassistant.action: { action: cover.stop_cover, data: { entity_id: %E% } }]
              - button:
                  x: 0
                  y: 272
                  width: 120
                  height: 140
                  radius: 24
                  bg_color: 0x12141C
                  border_width: 2
                  border_color: 0x2A2F3A
                  widgets: [label: { text: "\\U000F0140", align: center, text_font: f_icon, text_color: 0xEEF0F6 }]
                  on_click: [homeassistant.action: { action: cover.close_cover, data: { entity_id: %E% } }]
"""

BRI_SENSOR_TMPL = """  - platform: homeassistant
    id: ha_roombri_%S%
    entity_id: %E%
    attribute: brightness
    on_value:
      then:
        - lvgl.slider.update:
            id: sld_room_%S%
            value: !lambda 'return (x == x) ? (int) roundf(x / 2.55f) : 0;'
        - lvgl.label.update:
            id: pct_sld_room_%S%
            text: !lambda 'int v=(x==x)?(int)roundf(x/2.55f):0; char b[8]; if(v<=0) return std::string("OFF"); snprintf(b,sizeof(b),"%d%%",v); return std::string(b);'
"""

# Climate feeders (numeric attributes): target setpoint + current temperature.
CLIMATE_TGT_TMPL = """  - platform: homeassistant
    id: ha_roomtgt_%S%
    entity_id: %E%
    attribute: temperature
    on_value:
      then:
        - lvgl.slider.update: { id: sld_room_%S%, value: !lambda 'return (x == x) ? (int) x : 70;' }
        - lvgl.label.update: { id: pct_sld_room_%S%, text: !lambda 'if (x != x) return std::string("--°"); char b[8]; snprintf(b, sizeof(b), "%d°", (int) x); return std::string(b);' }
"""

CLIMATE_CUR_TMPL = """  - platform: homeassistant
    id: ha_roomcur_%S%
    entity_id: %E%
    attribute: current_temperature
    on_value:
      then:
        - lvgl.label.update: { id: lbl_cur_room_%S%, text: !lambda 'if (x != x) return std::string(""); char b[16]; snprintf(b, sizeof(b), "now %d°", (int) x); return std::string(b);' }
"""

LIGHT_TEXT_TMPL = """  - platform: homeassistant
    id: ha_roomst_%S%
    entity_id: %E%
    on_value:
      then:
        - if:
            condition:
              lambda: 'return x == std::string("on");'
            then:
              - lvgl.widget.update: { id: pwr_ic_%S%, bg_color: 0x2563EB }
              - lvgl.label.update: { id: ic_%S%, text_color: 0xFFFFFF }
            else:
              - lvgl.widget.update: { id: pwr_ic_%S%, bg_color: 0x12141C }
              - lvgl.label.update: { id: ic_%S%, text_color: 0x8A8F9E }
"""

TOGGLE_TEXT_TMPL = """  - platform: homeassistant
    id: ha_roomst_%S%
    entity_id: %E%
    on_value:
      then:
        - lvgl.slider.update: { id: sld_room_%S%, value: !lambda 'return x == std::string("on") ? 100 : 0;' }
        - lvgl.label.update: { id: lbl_st_room_%S%, text: !lambda 'return x == std::string("on") ? std::string("On") : std::string("Off");' }
        - lvgl.widget.update: { id: pwr_ic_%S%, bg_color: !lambda 'return x == std::string("on") ? lv_color_hex(0x2563EB) : lv_color_hex(0x12141C);' }
        - lvgl.label.update: { id: ic_%S%, text_color: !lambda 'return x == std::string("on") ? lv_color_hex(0xFFFFFF) : lv_color_hex(0x8A8F9E);' }
"""

# Sensor card feeders: live state -> value label, unit_of_measurement -> unit.
SENSOR_VAL_TMPL = """  - platform: homeassistant
    id: ha_roomval_%S%
    entity_id: %E%
    on_value:
      then:
        - lvgl.label.update: { id: val_room_%S%, text: !lambda 'return x.empty() ? std::string("--") : x;' }
"""

SENSOR_UNIT_TMPL = """  - platform: homeassistant
    id: ha_roomunit_%S%
    entity_id: %E%
    attribute: unit_of_measurement
    on_value:
      then:
        - lvgl.label.update: { id: unit_room_%S%, text: !lambda 'return x;' }
"""

# Lock feeder: locked -> green + lock glyph; otherwise dim + lock-open glyph.
LOCK_TEXT_TMPL = """  - platform: homeassistant
    id: ha_roomst_%S%
    entity_id: %E%
    on_value:
      then:
        - lvgl.label.update:
            id: lbl_st_room_%S%
            text: !lambda 'std::string r=x; if(!r.empty()) r[0]=toupper(r[0]); return r;'
        - if:
            condition:
              lambda: 'return x == std::string("locked");'
            then:
              - lvgl.widget.update: { id: pwr_ic_%S%, bg_color: 0x1E7F4F }
              - lvgl.label.update: { id: ic_%S%, text: "\\U000F033E", text_color: 0xFFFFFF }
            else:
              - lvgl.widget.update: { id: pwr_ic_%S%, bg_color: 0x12141C }
              - lvgl.label.update: { id: ic_%S%, text: "\\U000F033F", text_color: 0xFF9F6B }
"""

# Media feeders: playing-state -> play/pause glyph; media_title -> title label.
MEDIA_STATE_TMPL = """  - platform: homeassistant
    id: ha_roomst_%S%
    entity_id: %E%
    on_value:
      then:
        - if:
            condition:
              lambda: 'return x == std::string("playing");'
            then:
              - lvgl.label.update: { id: ic_%S%, text: "\\U000F03E4" }
            else:
              - lvgl.label.update: { id: ic_%S%, text: "\\U000F040A" }
"""

MEDIA_TITLE_TMPL = """  - platform: homeassistant
    id: ha_roomtitle_%S%
    entity_id: %E%
    attribute: media_title
    on_value:
      then:
        - lvgl.label.update: { id: lbl_title_room_%S%, text: !lambda 'return x.empty() ? std::string("--") : x;' }
"""

# Cover feeder: state -> capitalized label (Open / Closed / Opening / Closing).
COVER_TEXT_TMPL = """  - platform: homeassistant
    id: ha_roomst_%S%
    entity_id: %E%
    on_value:
      then:
        - lvgl.label.update:
            id: lbl_st_room_%S%
            text: !lambda 'std::string r=x; if(!r.empty()) r[0]=toupper(r[0]); return r;'
"""

COUNT_SENSOR_TMPL = """  - platform: homeassistant
    id: ha_count_%ID%
    entity_id: sensor.aurora_room_%ID%
    on_value:
      then:
        - lvgl.label.update: { id: lbl_roomcount_%ID%, text: !lambda 'int v=(int)x; if(v<=0) return std::string("All off"); char b[16]; snprintf(b,sizeof(b),"%d on",v); return std::string(b);' }
"""


def validate_rooms(data):
    rooms = (data or {}).get("rooms", [])
    if not isinstance(rooms, list) or not rooms:
        raise ValueError("at least one room is required")
    # No room-count cap: the on-device picker scrolls, so any number is fine.
    seen_ids, seen_widgets = set(), set()
    for r in rooms:
        rid = r.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", rid):
            raise ValueError(f"room id '{rid}' must be a lowercase slug")
        if rid in seen_ids:
            raise ValueError(f"duplicate room id '{rid}'")
        seen_ids.add(rid)
        if not r.get("name"):
            raise ValueError(f"room '{rid}' needs a name")
        ents = r.get("entities", [])
        if len(ents) > ROOM_MAX_ENTITIES:
            raise ValueError(f"room '{rid}' has >{ROOM_MAX_ENTITIES} entities")
        for e in ents:
            eid, typ = e.get("entity_id", ""), e.get("type", "")
            if not re.fullmatch(r"[a-z_]+\.[A-Za-z0-9_]+", eid):
                raise ValueError(f"bad entity_id '{eid}' in room '{rid}'")
            if typ not in ROOM_TYPES:
                raise ValueError(f"unsupported type '{typ}' in room '{rid}' (Phase 1: {sorted(ROOM_TYPES)})")
            wid = f"{rid}_{slug(eid)}"
            if wid in seen_widgets:
                raise ValueError(f"entity {eid} appears twice in room '{rid}'")
            seen_widgets.add(wid)
    return rooms


def read_rooms():
    return json.load(open(ROOMS_JSON, encoding="utf-8"))


def write_rooms(data):
    validate_rooms(data)
    json.dump(data, open(ROOMS_JSON, "w", encoding="utf-8"), indent=2)


def gen_picker(rooms):
    # Buttons stack at y = 78*i inside the scrollable room_scroll container in
    # page_rooms, so the list scrolls for any number of rooms.
    return "".join(_fmt(PICK_TMPL, Y=78 * i, ICON=r["icon"], NAME=r["name"], ID=r["id"])
                   for i, r in enumerate(rooms))


def _card(e, i):
    s = f'{e["_rid"]}_{slug(e["entity_id"])}'
    x = ROOM_X0 + ROOM_PITCH * i
    if e["type"] == "light":
        return _fmt(CARD_LIGHT, X=x, S=s, E=e["entity_id"], L=e["label"])
    if e["type"] == "sensor":
        return _fmt(CARD_SENSOR, X=x, S=s, L=e["label"])
    if e["type"] == "lock":
        return _fmt(CARD_LOCK, X=x, S=s, E=e["entity_id"], L=e["label"])
    if e["type"] == "climate":
        return _fmt(CARD_CLIMATE, X=x, S=s, E=e["entity_id"], L=e["label"])
    if e["type"] == "media":
        return _fmt(CARD_MEDIA, X=x, S=s, E=e["entity_id"], L=e["label"])
    if e["type"] == "cover":
        return _fmt(CARD_COVER, X=x, S=s, E=e["entity_id"], L=e["label"])
    return _fmt(CARD_TOGGLE, X=x, S=s, E=e["entity_id"], L=e["label"],
                ACT=e["type"], TICON=TYPE_ICON[e["type"]])


def gen_pages(rooms):
    out = ""
    for r in rooms:
        cards = "".join(_card(dict(e, _rid=r["id"]), i) for i, e in enumerate(r["entities"]))
        out += _fmt(PAGE_HEAD_TMPL, ID=r["id"], NAME=r["name"]) + cards
    return out


def gen_bri(rooms):
    out = ""
    for r in rooms:
        for e in r["entities"]:
            s = f'{r["id"]}_{slug(e["entity_id"])}'
            if e["type"] == "light":
                out += _fmt(BRI_SENSOR_TMPL, S=s, E=e["entity_id"])
            elif e["type"] == "climate":
                out += _fmt(CLIMATE_TGT_TMPL, S=s, E=e["entity_id"])
                out += _fmt(CLIMATE_CUR_TMPL, S=s, E=e["entity_id"])
    return out


def gen_text(rooms):
    out = ""
    for r in rooms:
        for e in r["entities"]:
            s = f'{r["id"]}_{slug(e["entity_id"])}'
            t = e["type"]
            if t == "light":
                out += _fmt(LIGHT_TEXT_TMPL, S=s, E=e["entity_id"])
            elif t == "sensor":
                out += _fmt(SENSOR_VAL_TMPL, S=s, E=e["entity_id"])
                out += _fmt(SENSOR_UNIT_TMPL, S=s, E=e["entity_id"])
            elif t == "lock":
                out += _fmt(LOCK_TEXT_TMPL, S=s, E=e["entity_id"])
            elif t == "media":
                out += _fmt(MEDIA_STATE_TMPL, S=s, E=e["entity_id"])
                out += _fmt(MEDIA_TITLE_TMPL, S=s, E=e["entity_id"])
            elif t == "climate":
                pass  # climate feeders are numeric -> emitted by gen_bri
            elif t == "cover":
                out += _fmt(COVER_TEXT_TMPL, S=s, E=e["entity_id"])
            else:
                out += _fmt(TOGGLE_TEXT_TMPL, S=s, E=e["entity_id"], ACT=t)
    return out


def gen_counts(rooms):
    return "".join(_fmt(COUNT_SENSOR_TMPL, ID=r["id"]) for r in rooms)


def _replace_region(text, key, payload):
    start, end, indent = ROOM_MARKERS[key]
    if start not in text or end not in text:
        raise ValueError(f"marker {start} / {end} missing from aurora.yaml")
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    return (head + start + " (generated by the configurator — do not hand-edit)\n"
            + payload + indent + end + tail)


def write_rooms_to_yaml():
    rooms = validate_rooms(read_rooms())
    text = open(YAML, encoding="utf-8").read()
    for key, gen in (("picker", gen_picker), ("pages", gen_pages),
                     ("bri", gen_bri), ("text", gen_text), ("counts", gen_counts)):
        text = _replace_region(text, key, gen(rooms))
    open(YAML, "w", encoding="utf-8").write(text)
    return {"rooms": len(rooms),
            "entities": sum(len(r["entities"]) for r in rooms)}


def ha_entities(url, token):
    req = urllib.request.Request(
        url.rstrip("/") + "/api/states",
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        states = json.load(r)
    ents = []
    for s in states:
        eid = s.get("entity_id", "")
        ents.append({"entity_id": eid, "domain": eid.split(".")[0],
                     "name": (s.get("attributes") or {}).get("friendly_name", eid)})
    return sorted(ents, key=lambda e: e["entity_id"])


def ha_areas(url, token):
    """Return {area_id: {name, entities[]}} via the HA template API (areas come
    from HA's area registry, so the builder's room options match HA)."""
    tmpl = ('[{% for a in areas() %}{"id": {{ a|tojson }}, "name": '
            '{{ area_name(a)|tojson }}, "entities": {{ area_entities(a)|tojson }}}'
            '{{ "," if not loop.last }}{% endfor %}]')
    req = urllib.request.Request(
        url.rstrip("/") + "/api/template",
        data=json.dumps({"template": tmpl}).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        arr = json.loads(r.read().decode() or "[]")
    return {a["id"]: {"name": a["name"], "entities": a["entities"]} for a in arr}


# --- page-builder layout persistence (layout.json next to this file) ---
LAYOUT_JSON = os.path.join(HERE, "layout.json")
BUILDER_HTML = os.path.join(HERE, "builder.html")


def read_layout():
    try:
        with open(LAYOUT_JSON, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def write_layout(data):
    with open(LAYOUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return True


# --- admin config + auth (config.json holds HA url/token + panel IP; gitignored) ---
import hashlib as _hashlib
import secrets as _secrets

CONFIG_JSON = os.path.join(HERE, "config.json")
SESSIONS = set()   # valid session tokens, in-memory (cleared on restart)


def _sha(s):
    return _hashlib.sha256((s or "").encode()).hexdigest()


def read_config():
    try:
        with open(CONFIG_JSON, encoding="utf-8") as f:
            c = json.load(f)
    except Exception:  # noqa: BLE001
        c = {}
    c.setdefault("password_sha256", _sha("Admin"))   # default login password: Admin
    c.setdefault("ha_url", "")
    c.setdefault("ha_token", "")
    c.setdefault("panel_ip", "")
    return c


def write_config(c):
    with open(CONFIG_JSON, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)
    try:
        os.chmod(CONFIG_JSON, 0o600)   # holds the HA token — restrict perms
    except Exception:  # noqa: BLE001
        pass
    return True


def ensure_config():
    if not os.path.exists(CONFIG_JSON):
        write_config(read_config())


LOGIN_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Aurora - Sign in</title><meta name=viewport content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#070809;color:#F3F5F8;font-family:system-ui,Segoe UI,sans-serif}
.box{width:340px;background:#101116;border:1px solid #23262F;border-radius:16px;padding:26px}
h1{font-size:20px;margin:0 0 4px}h1 b{background:linear-gradient(90deg,#2ED5B8,#B06CFF);-webkit-background-clip:text;background-clip:text;color:transparent}
p{color:#868CA0;font-size:13px;margin:0 0 18px}
input{width:100%;padding:11px 12px;background:#0F1117;border:1px solid #23262F;border-radius:10px;color:#F3F5F8;font:inherit;margin-bottom:12px}
button{width:100%;padding:11px;background:#2ED5B8;color:#06231D;border:0;border-radius:10px;font-weight:700;font-size:14px;cursor:pointer}
.err{color:#F2685A;font-size:12px;min-height:16px;margin-top:8px}
</style></head><body>
<div class="box"><h1>Aurora <b>Configurator</b></h1><p>Enter the access password to continue.</p>
<input id=pw type=password placeholder="Password" autofocus>
<button id=go>Sign in</button><div class="err" id=err></div></div>
<script>
async function login(){const pw=document.getElementById('pw').value;const e=document.getElementById('err');e.textContent='';
 const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
 if(r.ok){location.reload()}else{e.textContent='Incorrect password.'}}
document.getElementById('go').onclick=login;
document.getElementById('pw').addEventListener('keydown',ev=>{if(ev.key==='Enter')login()});
</script></body></html>"""


# --- flash job (async, log captured for polling) ---
FLASH = {"running": False, "log": "", "done": False, "ok": False}


def _device_compile_time(host):
    """Best-effort read of the panel's running compilation_time via the API."""
    try:
        import asyncio
        from aioesphomeapi import APIClient

        async def go():
            cli = APIClient(host, 6053, "")
            await cli.connect(login=True)
            di = await cli.device_info()
            await cli.disconnect()
            return di.compilation_time

        return asyncio.run(go())
    except Exception:  # noqa: BLE001
        return None


def flash_job(device):
    # OTA on this panel sometimes rolls back at boot-confirm, so after an OTA
    # upload we read the panel's running compile time and re-flash until it
    # matches the build. Serial uploads (/dev/tty*) stick first try — no retry.
    FLASH.update(running=True, log="", done=False, ok=False)
    import re as _re
    import time
    cwd = os.path.join(HERE, "..", "..")

    def run(cmd):
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, cwd=cwd)
        for line in proc.stdout:
            FLASH["log"] += line
        proc.wait()
        return proc.returncode

    try:
        FLASH["log"] += "== Compiling ==\n"
        if run([ESPHOME, "compile", YAML]) != 0:
            FLASH["log"] += "\n[compile failed]\n"
            FLASH.update(running=False, done=True, ok=False)
            return
        m = _re.search(r"build_time_str=(\d\S* \S+ \S+)", FLASH["log"])
        target = m.group(1).strip() if m else None
        is_serial = "/" in device          # /dev/ttyACM0 = serial; else OTA (IP)
        attempts = 1 if is_serial else 4
        ok = False
        for i in range(1, attempts + 1):
            FLASH["log"] += f"\n== Upload attempt {i}/{attempts} -> {device} ==\n"
            if run([ESPHOME, "upload", YAML, "--device", device]) != 0:
                FLASH["log"] += "[upload error]\n"
                continue
            if is_serial:
                ok = True
                break
            FLASH["log"] += "verifying the panel booted the new build...\n"
            live = None
            for _ in range(12):
                time.sleep(4)
                live = _device_compile_time(device)
                if live:
                    break
            if live is None:
                FLASH["log"] += "(couldn't reach the panel API to verify; upload reported OK)\n"
                ok = True
                break
            FLASH["log"] += f"panel build: {live}  |  target: {target}\n"
            if target and live.strip() == target:
                FLASH["log"] += "MATCH — the new build is running.\n"
                ok = True
                break
            FLASH["log"] += "MISMATCH — OTA rolled back; re-flashing...\n"
        FLASH["ok"] = ok
        if not ok:
            FLASH["log"] += ("\n[!] Could not confirm the new build after "
                             f"{attempts} tries. If this persists, flash over USB/serial.\n")
    except Exception as e:  # noqa: BLE001
        FLASH["log"] += f"\n[error] {e}\n"
    FLASH.update(running=False, done=True)


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", headers=None):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or "{}")

    def _authed(self):
        for part in self.headers.get("Cookie", "").split(";"):
            p = part.strip()
            if p.startswith("aurora_session=") and p.split("=", 1)[1] in SESSIONS:
                return True
        return False

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path in ("/", "/builder"):
            if not self._authed():
                return self._send(200, LOGIN_PAGE, "text/html")
            if self.path == "/":
                return self._send(200, PAGE, "text/html")
            try:
                with open(BUILDER_HTML, "rb") as f:
                    return self._send(200, f.read(), "text/html")
            except Exception as e:  # noqa: BLE001
                return self._send(500, "builder.html missing: " + str(e), "text/plain")
        if not self._authed():
            return self._send(401, json.dumps({"error": "auth required"}))
        if self.path == "/api/config":
            c = read_config()
            return self._send(200, json.dumps({"ha_url": c["ha_url"], "panel_ip": c["panel_ip"], "has_token": bool(c["ha_token"])}))
        if self.path == "/api/layout":
            return self._send(200, json.dumps(read_layout()))
        if self.path == "/api/slots":
            return self._send(200, json.dumps(read_slots()))
        if self.path == "/api/home":
            return self._send(200, json.dumps({"order": read_home_layout(), "meta": CARD_META}))
        if self.path == "/api/rooms":
            return self._send(200, json.dumps(read_rooms()))
        if self.path == "/api/flash-status":
            return self._send(200, json.dumps(FLASH))
        return self._send(404, "{}")

    def do_POST(self):
        try:
            d = self._json()
            if self.path == "/api/login":
                if _sha(d.get("password", "")) == read_config()["password_sha256"]:
                    tok = _secrets.token_urlsafe(24)
                    SESSIONS.add(tok)
                    return self._send(200, json.dumps({"ok": True}),
                                      headers={"Set-Cookie": "aurora_session=%s; Path=/; HttpOnly; SameSite=Strict" % tok})
                return self._send(403, json.dumps({"error": "incorrect password"}))
            if not self._authed():
                return self._send(401, json.dumps({"error": "auth required"}))
            if self.path == "/api/logout":
                for part in self.headers.get("Cookie", "").split(";"):
                    p = part.strip()
                    if p.startswith("aurora_session="):
                        SESSIONS.discard(p.split("=", 1)[1])
                return self._send(200, json.dumps({"ok": True}),
                                  headers={"Set-Cookie": "aurora_session=; Path=/; Max-Age=0"})
            if self.path == "/api/change-password":
                c = read_config()
                if _sha(d.get("current", "")) != c["password_sha256"]:
                    return self._send(403, json.dumps({"error": "current password is wrong"}))
                if not d.get("new"):
                    return self._send(400, json.dumps({"error": "new password is empty"}))
                c["password_sha256"] = _sha(d["new"])
                write_config(c)
                return self._send(200, json.dumps({"ok": True}))
            if self.path == "/api/config":
                c = read_config()
                if "ha_url" in d:
                    c["ha_url"] = (d["ha_url"] or "").strip()
                if "panel_ip" in d:
                    c["panel_ip"] = (d["panel_ip"] or "").strip()
                if d.get("ha_token"):           # only overwrite the token if a new one is provided
                    c["ha_token"] = d["ha_token"].strip()
                write_config(c)
                return self._send(200, json.dumps({"ok": True, "has_token": bool(c["ha_token"])}))
            if self.path == "/api/entities":
                c = read_config()
                return self._send(200, json.dumps(ha_entities(d.get("url") or c["ha_url"], d.get("token") or c["ha_token"])))
            if self.path == "/api/ha/areas":
                c = read_config()
                return self._send(200, json.dumps(ha_areas(d.get("url") or c["ha_url"], d.get("token") or c["ha_token"])))
            if self.path == "/api/layout":
                return self._send(200, json.dumps({"saved": write_layout(d)}))
            if self.path == "/api/save":
                return self._send(200, json.dumps({"saved": write_bindings(d["bindings"])}))
            if self.path == "/api/home":
                return self._send(200, json.dumps({"order": write_home_layout(d["order"])}))
            if self.path == "/api/rooms":
                write_rooms(d)                       # validate + persist rooms.json
                return self._send(200, json.dumps({"saved": True, **write_rooms_to_yaml()}))
            if self.path == "/api/flash":
                dev = d.get("device") or read_config()["panel_ip"]
                if not dev:
                    return self._send(400, json.dumps({"error": "no panel IP set"}))
                if not FLASH["running"]:
                    threading.Thread(target=flash_job, args=(dev,), daemon=True).start()
                return self._send(200, json.dumps({"started": True}))
        except Exception as e:  # noqa
            return self._send(500, json.dumps({"error": str(e)}))
        return self._send(404, "{}")


PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>Aurora Configurator</title><meta name=viewport content="width=device-width,initial-scale=1">
<link rel=stylesheet href="https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.min.css">
<style>
:root{--bg:#08090d;--card:#141720;--hair:#2a2e38;--text:#eef0f6;--t2:#8a8f9e;--teal:#2ed5b8;--purple:#7b6cff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,Segoe UI,sans-serif}
header{padding:22px 28px;border-bottom:1px solid var(--hair)}h1{margin:0;font-size:22px}
h1 span{background:linear-gradient(90deg,var(--teal),var(--purple));-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--t2);font-size:13px;margin-top:4px}main{max-width:900px;margin:0 auto;padding:24px 28px}
.card{background:var(--card);border:1px solid var(--hair);border-radius:16px;padding:18px 20px;margin-bottom:18px}
.card h2{margin:0 0 12px;font-size:14px;letter-spacing:.06em;color:var(--t2);text-transform:uppercase}
label{display:block;font-size:12px;color:var(--t2);margin:10px 0 4px}
input,select{width:100%;padding:10px 12px;background:#10121a;border:1px solid var(--hair);border-radius:10px;color:var(--text);font:inherit}
.row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.slot{display:grid;grid-template-columns:200px 1fr;gap:12px;align-items:center;padding:8px 0;border-top:1px solid var(--hair)}
.slot .l{font-size:13px}.slot .l small{display:block;color:var(--t2);font-size:11px}
button{background:linear-gradient(135deg,var(--teal),var(--purple));color:#06070a;border:0;border-radius:11px;padding:11px 18px;font:inherit;font-weight:600;cursor:pointer}
button.ghost{background:#10121a;color:var(--text);border:1px solid var(--hair)}
.bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
pre{background:#06070a;border:1px solid var(--hair);border-radius:10px;padding:12px;max-height:240px;overflow:auto;font-size:12px;white-space:pre-wrap}
.ok{color:var(--teal)}.err{color:#ff6b6b}.muted{color:var(--t2)}
.grp{font-size:11px;letter-spacing:.08em;color:var(--purple);text-transform:uppercase;margin:16px 0 2px}
.preview{background:#06070a;border:1px solid var(--hair);border-radius:14px;padding:14px;display:flex;gap:14px}
.np{width:120px;flex:none;border-radius:12px;background:linear-gradient(135deg,#ff7a4d,#c0277a 55%,#6a2fb5);display:flex;align-items:flex-end;padding:10px;color:#fff;font-size:11px}
.hgrid{flex:1;display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:12px}
.hcell{background:var(--card);border:1px solid var(--hair);border-radius:14px;padding:14px;min-height:96px;cursor:grab;transition:.12s}
.hcell:hover{border-color:var(--teal)}.hcell.drag{opacity:.4}.hcell.over{border-color:var(--purple);background:#1a1d28}
.hcell .n{font-weight:600}.hcell .h{color:var(--t2);font-size:12px;margin-top:4px}.hcell .d{color:var(--t2);font-size:11px;margin-top:8px}
.hcell .cardsel{margin-top:10px;padding:6px 8px;font-size:12px;cursor:pointer}
.room{border:1px solid var(--hair);border-radius:12px;padding:12px 14px;margin:10px 0;background:#10121a}
.room .rh{display:grid;grid-template-columns:1fr 120px 150px 90px;gap:8px;align-items:end}
.ent{display:grid;grid-template-columns:110px 1fr 1fr 56px;gap:8px;align-items:center;margin-top:6px}
.x{background:#2a1416;color:#ff9b9b;border:1px solid #50232a}
.sm{font-size:11px;color:var(--t2);margin:6px 0 2px}
.mdig{font-family:'Material Design Icons';font-size:22px;line-height:1}
.iconsw{background:#10121a;border:1px solid var(--hair);border-radius:10px;color:var(--teal);width:100%;height:42px;cursor:pointer;padding:0}
.iconsw:hover{border-color:var(--teal)}
#iconpop{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);align-items:center;justify-content:center;z-index:50}
.iconpop-box{background:var(--card);border:1px solid var(--hair);border-radius:16px;padding:16px;max-width:560px;width:90%;max-height:80vh;overflow:auto}
.iconpop-hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;color:var(--t2);font-size:13px}
.iconpop-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}
.iconopt{background:#10121a;border:1px solid var(--hair);border-radius:10px;color:var(--text);padding:8px 4px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:4px}
.iconopt:hover{border-color:var(--teal)}.iconopt .mdig{font-size:26px;color:var(--teal)}.iconopt small{font-size:10px;color:var(--t2)}
</style></head><body>
<header style="display:flex;justify-content:space-between;align-items:center"><div><h1><span>Aurora</span> Configurator</h1><div class=sub>Point the panel at your Home Assistant — no code.</div></div><a href="/builder" style="text-decoration:none"><button>Open Page Builder &rarr;</button></a></header>
<main>
<div class=card><h2>1 · Connect to Home Assistant</h2>
<div class=row><div><label>HA URL <span class=muted>(use the IP, not .local)</span></label><input id=url placeholder="http://10.0.0.50:8123"></div>
<div><label>Long-lived access token <span class=muted>(HA → profile → bottom)</span></label><input id=token type=password placeholder="paste token"></div></div>
<div style="margin-top:12px" class=bar><button onclick=connect()>Load my entities</button><span id=cmsg class=muted></span></div></div>

<div class=card id=slotcard style=display:none><h2>2 · Map entities</h2><div id=slots></div></div>

<div class=card><h2>3 · Home screen layout <span class=muted>(pick a card per cell, then drag to rearrange)</span></h2>
<div class=preview><div class=np>Now&nbsp;Playing<br><small>(fixed)</small></div><div id=homegrid class=hgrid></div></div></div>

<div class=card id=roomcard style=display:none><h2>4 · Rooms <span class=muted>(add / rename / assign entities — any number of rooms; up to 6 entities each)</span></h2>
<div id=roomlist></div>
<div style="margin-top:10px" class=bar><button class=ghost onclick=addRoom()>+ Add room</button><button onclick=saveRooms()>Save rooms</button><span id=rmsg class=muted></span></div>
<div class=muted style="margin-top:6px;font-size:12px">Saving regenerates the firmware source. Then click “Save &amp; flash panel” below to push the changes to the device.</div></div>

<div class=card id=flashcard><h2>5 · Save &amp; flash</h2>
<div class=row><div><label>Panel IP address <span class=muted>(also the device's web page)</span></label><input id=device placeholder="10.0.0.174"></div><div></div></div>
<div style="margin-top:12px" class=bar><button class=ghost onclick=save()>Save bindings</button><button onclick=flash()>Save &amp; flash panel</button><button class=ghost onclick=opendev()>Open device page ↗</button><span id=fmsg class=muted></span></div>
<pre id=flog style=display:none></pre></div>
</main>
<div id=iconpop onclick="if(event.target===this)closeIconPicker()"><div class=iconpop-box><div class=iconpop-hd><span>Pick a room icon</span><button class=ghost onclick=closeIconPicker()>✕</button></div><div class=iconpop-grid></div></div></div>
<script>
let SLOTS=[],ENTS=[],HORDER=[],HMETA={};
async function j(u,o){const r=await fetch(u,o);if(!r.ok)throw new Error((await r.json()).error||r.status);return r.json()}
async function boot(){SLOTS=await j('/api/slots');loadHome();
 restoreCreds();[url_,token_,device_].forEach(el=>el&&el.addEventListener('change',saveCreds));
 if(url_.value&&token_.value)connect();}
function saveCreds(){try{localStorage.setItem('aurora_url',url_.value.trim());localStorage.setItem('aurora_token',token_.value.trim());localStorage.setItem('aurora_device',device_.value.trim());}catch(e){}}
function restoreCreds(){try{const u=localStorage.getItem('aurora_url'),t=localStorage.getItem('aurora_token'),d=localStorage.getItem('aurora_device');if(u)url_.value=u;if(t)token_.value=t;if(d)device_.value=d;}catch(e){}}
async function loadHome(){const r=await j('/api/home');HORDER=r.order;HMETA=r.meta;renderHome()}
function renderHome(){const cat=Object.keys(HMETA);
 homegrid.innerHTML=HORDER.map((k,i)=>{
  const used=HORDER.filter((_,j)=>j!==i);
  const opts=cat.map(c=>`<option value="${c}" ${c===k?'selected':''} ${used.includes(c)?'disabled':''}>${HMETA[c].name}</option>`).join('');
  return `<div class=hcell draggable=true data-i="${i}"><div class=n>${HMETA[k].name}</div><div class=h>${HMETA[k].hint}</div>`
   +`<select class=cardsel onmousedown="event.stopPropagation()" onclick="event.stopPropagation()" onchange="pickCard(${i},this.value)">${opts}</select>`
   +`<div class=d>cell ${i+1}</div></div>`}).join('');
 document.querySelectorAll('.hcell').forEach(c=>{
  c.ondragstart=e=>{e.dataTransfer.setData('i',c.dataset.i);c.classList.add('drag')};
  c.ondragend=()=>c.classList.remove('drag');
  c.ondragover=e=>{e.preventDefault();c.classList.add('over')};
  c.ondragleave=()=>c.classList.remove('over');
  c.ondrop=e=>{e.preventDefault();c.classList.remove('over');const a=+e.dataTransfer.getData('i'),b=+c.dataset.i;[HORDER[a],HORDER[b]]=[HORDER[b],HORDER[a]];renderHome()}})}
function pickCard(i,v){HORDER[i]=v;renderHome()}
async function saveHome(){await j('/api/home',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order:HORDER})})}
async function connect(){
 const url=url_.value.trim(),token=token_.value.trim();saveCreds();cmsg.textContent='Loading…';cmsg.className='muted';
 try{ENTS=await j('/api/entities',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,token})});
  cmsg.textContent='Loaded '+ENTS.length+' entities ✓';cmsg.className='ok';render();slotcard.style.display='';flashcard.style.display='';await loadRooms();renderRooms();roomcard.style.display=''}
 catch(e){cmsg.textContent='Failed: '+e.message+' (check URL/token & that this PC can reach HA)';cmsg.className='err'}}
function render(){let g='',h='';for(const s of SLOTS){if(s.group!=g){g=s.group;h+=`<div class=grp>${g}</div>`}
  const opts=ENTS.filter(e=>e.domain==s.domain).map(e=>`<option value="${e.entity_id}" ${e.entity_id==s.value?'selected':''}>${e.name} — ${e.entity_id}</option>`).join('');
  h+=`<div class=slot><div class=l>${s.label}<small>${s.domain}</small></div><select data-v="${s.var}"><option value="">— none —</option>${opts}</select></div>`}
 slots.innerHTML=h}
function bindings(){const b={};document.querySelectorAll('#slots select').forEach(x=>b[x.dataset.v]=x.value);return b}
async function save(){fmsg.className='muted';fmsg.textContent='Saving…';try{const r=await j('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bindings:bindings()})});fmsg.textContent='Saved '+r.saved+' bindings ✓';fmsg.className='ok'}catch(e){fmsg.textContent=e.message;fmsg.className='err'}}
async function flash(){
 if(!HORDER.includes('climate')||!HORDER.includes('doors')){fmsg.textContent='Keep the Climate and Doors cards — other screens use their live state.';fmsg.className='err';return}
 await save();await saveHome();const device=device_.value.trim();if(!device){fmsg.textContent='Enter the panel IP';fmsg.className='err';return}
 flog.style.display='';flog.textContent='';fmsg.textContent='Building + flashing…';fmsg.className='muted';
 await j('/api/flash',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device})});
 const t=setInterval(async()=>{const s=await j('/api/flash-status');flog.textContent=s.log;flog.scrollTop=flog.scrollHeight;
  if(s.done){clearInterval(t);fmsg.textContent=s.ok?'Flashed ✓':'Flash failed — see log';fmsg.className=s.ok?'ok':'err'}},1500)}
function opendev(){let ip=device_.value.trim();if(!ip){fmsg.textContent='Enter the panel IP first';fmsg.className='err';return}if(!/^https?:\/\//.test(ip))ip='http://'+ip;window.open(ip,'_blank')}
// ---- Rooms wizard ----
let ROOMS=[];const RTYPES=['light','fan','switch','sensor','lock','climate','media','cover'];
// Room-icon picker. Each entry is [label, MDI codepoint]; the codepoints match
// glyphs added to the panel's f_icon font, so the device renders them too.
const ICONS=[['Living','F04B9'],['Sofa','F156E'],['Bedroom','F02E3'],['Master','F0FD2'],['Nursery','F068F'],['Dining','F0A70'],['Kitchen','F0290'],['Stove','F04DE'],['Counter','F181C'],['Coffee','F0176'],['Bath','F09A0'],['Toilet','F09AB'],['Spa','F0828'],['Office','F1239'],['Computer','F0379'],['Building','F0991'],['Game','F0297'],['Books','F125F'],['Laundry','F072A'],['Garage','F06D9'],['Car','F010B'],['Workshop','F1064'],['Gym','F01E6'],['Stairs','F04CD'],['Entry','F081A'],['Yard','F0531'],['Garden','F09F0'],['Patio','F0E45'],['Pool','F0606'],['Pets','F03E9'],['Dog','F0A43'],['Cat','F011B'],['TV','F0502'],['Home','F02DC'],['Star','F04CE'],['Lights','F1253']];
function iconCode(s){const m=(s||'').match(/F[0-9A-Fa-f]{4,5}$/);return m?m[0].toUpperCase():'F04B9';}
function iconGlyph(s){return `<span class=mdig>&#x${iconCode(s)};</span>`;}
let iconForRoom=-1;
function openIconPicker(ri){syncRooms();iconForRoom=ri;
 document.querySelector('#iconpop .iconpop-grid').innerHTML=ICONS.map(([lbl,code])=>`<button type=button class=iconopt title="${lbl}" onclick="pickIcon('${code}')"><span class=mdig>&#x${code};</span><small>${lbl}</small></button>`).join('');
 iconpop.style.display='flex';}
function pickIcon(code){if(iconForRoom>=0)ROOMS[iconForRoom].icon='\\U000'+code;iconpop.style.display='none';iconForRoom=-1;renderRooms();}
function closeIconPicker(){iconpop.style.display='none';iconForRoom=-1;}
function esc(s){return (s||'').replace(/"/g,'&quot;')}
function slugify(s){return ((s||'').toLowerCase().replace(/[^a-z0-9_]/g,'_').replace(/^_+/,'')||'room')}
async function loadRooms(){try{const r=await j('/api/rooms');ROOMS=(r&&r.rooms)||[]}catch(e){ROOMS=[]}}
function entOpts(type,cur){const list=ENTS.filter(e=>e.domain===type);
 let o=list.map(e=>`<option value="${e.entity_id}" ${e.entity_id===cur?'selected':''}>${e.name} — ${e.entity_id}</option>`).join('');
 if(cur&&!list.some(e=>e.entity_id===cur))o=`<option value="${cur}" selected>${cur}</option>`+o;
 return `<option value="">— select ${type} —</option>`+o}
function renderRooms(){roomlist.innerHTML=ROOMS.map((r,ri)=>`
 <div class=room data-ri="${ri}" data-icon="${esc(r.icon)}">
  <div class=rh>
   <div><div class=sm>Room name</div><input class=r-name value="${esc(r.name)}"></div>
   <div><div class=sm>id (slug)</div><input class=r-id value="${esc(r.id)}"></div>
   <div><div class=sm>icon</div><button type=button class=iconsw onclick="openIconPicker(${ri})">${iconGlyph(r.icon)}</button></div>
   <div><div class=sm>&nbsp;</div><button class="ghost x" onclick="removeRoom(${ri})">Remove</button></div>
  </div>
  <div class=sm>Entities (${(r.entities||[]).length}/6)</div>
  ${(r.entities||[]).map((e,ei)=>`<div class=ent data-ri="${ri}" data-ei="${ei}">
    <select class=e-type onchange="onType(${ri},${ei})">${RTYPES.map(t=>`<option ${t===e.type?'selected':''}>${t}</option>`).join('')}</select>
    <select class=e-id>${entOpts(e.type||'light',e.entity_id)}</select>
    <input class=e-label placeholder=Label value="${esc(e.label)}">
    <button class="ghost x" onclick="removeEntity(${ri},${ei})">✕</button></div>`).join('')}
  <div style="margin-top:8px"><button class=ghost onclick="addEntity(${ri})" ${(r.entities||[]).length>=6?'disabled':''}>+ Add entity</button></div>
 </div>`).join('')||'<div class=muted>No rooms yet — add one.</div>'}
function syncRooms(){ROOMS=[...roomlist.querySelectorAll('.room')].map(rb=>({
  id:rb.querySelector('.r-id').value.trim(),name:rb.querySelector('.r-name').value.trim(),icon:rb.dataset.icon||'\\U000F04B9',
  entities:[...rb.querySelectorAll('.ent')].map(eb=>({type:eb.querySelector('.e-type').value,entity_id:eb.querySelector('.e-id').value.trim(),label:eb.querySelector('.e-label').value.trim()}))}))}
function onType(ri,ei){syncRooms();renderRooms()}
function addRoom(){syncRooms();const n='Room '+(ROOMS.length+1);ROOMS.push({id:slugify(n),name:n,icon:'\\U000F04B9',entities:[]});renderRooms()}
function removeRoom(ri){syncRooms();ROOMS.splice(ri,1);renderRooms()}
function addEntity(ri){syncRooms();if((ROOMS[ri].entities||[]).length>=6){rmsg.textContent='Max 6 entities per room';rmsg.className='err';return}ROOMS[ri].entities.push({type:'light',entity_id:'',label:''});renderRooms()}
function removeEntity(ri,ei){syncRooms();ROOMS[ri].entities.splice(ei,1);renderRooms()}
async function saveRooms(){syncRooms();rmsg.className='muted';rmsg.textContent='Saving…';
 try{const r=await j('/api/rooms',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:1,rooms:ROOMS})});
  rmsg.textContent='Saved '+r.rooms+' rooms / '+r.entities+' entities ✓ — now Save & flash';rmsg.className='ok'}
 catch(e){rmsg.textContent='Error: '+e.message;rmsg.className='err'}}
window.url_=document.getElementById('url');window.token_=document.getElementById('token');window.device_=document.getElementById('device');
boot()
</script></body></html>"""

if __name__ == "__main__":
    ensure_config()
    print(f"Aurora Configurator → http://localhost:{PORT}  (firmware: {YAML})")
    print("Access is password-gated. Default password: Admin — change it from the configurator.")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
