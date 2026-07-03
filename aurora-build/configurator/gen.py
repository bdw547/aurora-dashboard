#!/usr/bin/env python3
"""Aurora Phase 2 — layout.json -> on-device LVGL firmware generator.

Reads the page-builder's layout.json and emits the device UI: a dynamic nav
rail, one LVGL page per layout page (+ sub-pages), grid-positioned cards wired
to Home Assistant via homeassistant.action, and per-card state sensors.

It reuses the hand-built hardware/font/style base from aurora.yaml (everything
except the UI), splicing in the generated `lvgl:` + state `sensor:`/`text_sensor:`
blocks, and writes a self-contained devices/.../aurora-gen.yaml.

    python3 gen.py            # assemble aurora-gen.yaml from layout.json
    python3 gen.py --check    # generate + structurally validate (no write)
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUT_JSON = os.path.join(HERE, "layout.json")
AURORA = os.path.normpath(os.path.join(
    HERE, "..", "..", "devices", "guition-esp32-p4-jc1060p470", "aurora.yaml"))
OUT = os.path.normpath(os.path.join(
    HERE, "..", "..", "devices", "guition-esp32-p4-jc1060p470", "aurora-gen.yaml"))

# ---- grid geometry (Direction-A spec: 6x5, cell 140x100, gutter 14) ----
COLS, ROWS = 6, 5
CELLW, CELLH, GUT = 140, 100, 14
X0 = 94            # nav rail 74 + 20 page inset
Y0, Y0H = 22, 96   # grid top: no header / with top-bar header
NAV_X = 8

# nav icon name -> MDI unicode glyph present in the f_icon font subset
NAV_GLYPH = {
    "home-variant": "\\U000F02DC", "home": "\\U000F02DC", "sofa": "\\U000F04B9",
    "lightbulb": "\\U000F0335", "thermostat": "\\U000F0393", "thermometer": "\\U000F050F",
    "music": "\\U000F075A", "shield-home": "\\U000F068A", "wifi": "\\U000F0928",
    "cog": "\\U000F0493", "remote-tv": "\\U000F0502", "speaker-multiple": "\\U000F04C4",
    "spotify": "\\U000F075A", "blinds": "\\U000F081A", "camera": "\\U000F0502",
    "weather-partly-cloudy": "\\U000F0595", "playlist-music": "\\U000F075A",
    "television": "\\U000F0502", "desk": "\\U000F1239", "bed": "\\U000F02E3",
    "door": "\\U000F081A", "stairs": "\\U000F04CD", "tree": "\\U000F0531",
    "garage": "\\U000F06D9", "silverware-fork-knife": "\\U000F0A70",
    "speaker": "\\U000F075A", "arrow-right-bold": "\\U000F0142", "image": "\\U000F0379",
}
FALLBACK_GLYPH = "\\U000F0142"  # chevron-right (shortcuts default to "go to")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_") or "x"


def esc(s):
    return '"' + str(s).replace('"', '\\"') + '"'


def rect(card, header):
    gx, gy, gw, gh = card["x"], card["y"], card["w"], card["h"]
    y0 = Y0H if header else Y0
    x = X0 + (gx - 1) * (CELLW + GUT)
    y = y0 + (gy - 1) * (CELLH + GUT)
    w = gw * CELLW + (gw - 1) * GUT
    h = gh * CELLH + (gh - 1) * GUT
    return x, y, w, h


# ---- low-level widget emitters (children placed under a card's `widgets:`) ----
def lbl(text, x, y, font="f_body", color="0xF3F5F8", wid=None, align=None, width=None, long=None, height=None, text_align=None):
    parts = []
    if wid:
        parts.append("id: " + wid)
    if align:
        parts.append("align: " + align)
    parts.append("text: " + esc(text))
    parts.append("x: %d" % x)
    parts.append("y: %d" % y)
    if width:
        parts.append("width: %d" % width)
    if height:
        parts.append("height: %d" % height)
    if text_align:                 # center text within the label's width box
        parts.append("text_align: " + text_align)
    if long:                       # "dot" + a one-line height -> ellipsis like the web .ct title
        parts.append("long_mode: " + long)
    parts.append("text_font: " + font)
    parts.append("text_color: " + color)
    return "              - label: { %s }\n" % ", ".join(parts)


def title(name, w, x=50, y=16):
    """Card title (web .ct parity): the web uses a fixed ~15px title with a
    one-line ellipsis. Use f_body on narrow (1-wide) cards and f_title on wider
    ones, set a one-line height, and ellipsize rather than wrap/overflow."""
    wide = w >= 280
    f = "f_title" if wide else "f_body"
    return lbl(name, x, y, f, width=w - x - 14, long="dot", height=36 if wide else 22)


def btn(x, y, w, h, label_glyph, action, bg="0x161B24", color="0xF3F5F8", radius=12, font="f_body"):
    return (
        "              - button:\n"
        "                  x: %d\n                  y: %d\n                  width: %d\n                  height: %d\n"
        "                  bg_color: %s\n                  radius: %d\n                  pad_all: 0\n                  scrollable: false\n"
        "                  widgets: [label: { text: %s, align: center, text_font: %s, text_color: %s }]\n"
        "                  on_click: [%s]\n"
        % (x, y, w, h, bg, radius, esc(label_glyph), font, color, action)
    )


def ha(action, entity, extra=""):
    data = "entity_id: %s%s" % (entity, (", " + extra) if extra else "")
    return "homeassistant.action: { action: %s, data: { %s } }" % (action, data)


def card_obj(x, y, w, h, inner, on_click=None, bg=None):
    oc = ("\n            clickable: true\n            on_click: [%s]" % on_click) if on_click else ""
    bgline = ("\n            bg_color: %s" % bg) if bg else ""    # override st_glass (e.g. "on" state)
    return (
        "        - obj:\n"
        "            x: %d\n            y: %d\n            width: %d\n            height: %d\n"
        "            styles: st_glass\n            pad_all: 0\n            clip_corner: true%s\n            scrollable: false%s\n"
        "            widgets:\n%s" % (x, y, w, h, bgline, oc, inner)
    )


# ---- per-card emitters: return (widgets[str], sensors[str], text_sensors[str]) ----
# Card icons — all codepoints confirmed present in the baked f_icon font.
CARD_ICON = {
    "light": "\\U000F0335", "light_t": "\\U000F0336", "switch": "\\U000F06A5",
    "outletgroup": "\\U000F06A5", "fan": "\\U000F0210", "cover": "\\U000F081A",
    "climate": "\\U000F0393", "sensor": "\\U000F050F", "binary": "\\U000F050F",
    "lock": "\\U000F033E", "camera": "\\U000F0502", "weather": "\\U000F0599",
    "scene": "\\U000F04CE", "script": "\\U000F0425", "media": "\\U000F075A",
    "spotify": "\\U000F0AC6", "sonos": "\\U000F04C4", "speakers": "\\U000F04C4",
    "volume": "\\U000F057E", "volumes": "\\U000F04C4",
    "sonos_sources": "\\U000F075A", "group": "\\U000F1253", "lightgroup": "\\U000F1253",
    "person": "\\U000F02DC", "tvremote": "\\U000F0502", "vacuum": "\\U000F050F",
    "alarm": "\\U000F068A",
    # Spotify / Sonos media cards
    "playlist": "\\U000F075A", "sonos_fav": "\\U000F04CE", "songlist": "\\U000F075A",
    "sonos_library": "\\U000F125F",
    "spotify_playlists": "\\U000F075A", "spotify_tracks": "\\U000F075A",
    # TV control cards (purple family)
    "tv_sources": "\\U000F0502", "tv_dpad": "\\U000F0297", "tv_transport": "\\U000F040A",
    "tv_channel": "\\U000F0502", "tv_volume": "\\U000F057E", "tv_trackpad": "\\U000F0297",
    "shortcuts": "\\U000F04CE",
}


def ic(ck, x=14, y=14, color="0x2ED5B8"):
    g = CARD_ICON.get(ck, "\\U000F0493")
    return ("              - label: { text: \"%s\", x: %d, y: %d, text_font: f_icon, text_color: %s }\n"
            % (g, x, y, color))


def c_toggle(card, x, y, w, h, base):
    e = card.get("entity", "")
    sid = base + "_st"
    inner = ic(card["ck"], color="0x2ED5B8")
    inner += title(card.get("name", "Switch"), w, x=14, y=48)
    inner += lbl("--", 14, -14, "f_small", "0x2ED5B8", wid=sid, align="bottom_left")
    on = ha("homeassistant.toggle", e) if e else None
    ts = []
    if e:
        ts.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
            "      - lvgl.label.update:\n          id: %s\n"
            "          text: !lambda 'return x == \"on\" ? std::string(\"On\") : std::string(\"Off\");'\n"
            "          text_color: !lambda 'return x == \"on\" ? lv_color_hex(0x2ED5B8) : lv_color_hex(0x868CA0);'\n"
            % (sid, e, sid))
    return [card_obj(x, y, w, h, inner, on)], [], ts


def c_light(card, x, y, w, h, base):
    e = card.get("entity", "")
    gw, gh = card["w"], card["h"]
    icon = CARD_ICON.get(card["ck"], "\\U000F0335")
    name = card.get("name", "Light")
    sldid, pct, fillid, pwrid = base + "_sld", base + "_pct", base + "_fill", base + "_pwr"
    bri = 74
    tog = ("homeassistant.action: { action: light.toggle, data: { entity_id: " + e + " } }") if e else "lvgl.page.show: page_home"

    if gh == 1:                                           # short/wide tile: title + inline slider + %
        inner = ic(card["ck"], color="0xF2B84B")
        inner += title(name, w)
        if e:
            inner += ("              - slider:\n                  id: " + sldid + "\n                  x: 14\n                  y: 56\n                  width: " + str(w - 28) + "\n"
                      "                  min_value: 0\n                  max_value: 100\n                  value: 0\n"
                      "                  on_release:\n                    - homeassistant.action:\n                        action: light.turn_on\n"
                      "                        data: { entity_id: " + e + ", brightness_pct: !lambda 'return std::to_string((int) lv_slider_get_value(id(" + sldid + ")));' }\n")
        inner += lbl("--%", 14, -12, "f_head", "0x2ED5B8", wid=pct, align="bottom_left")
        s = []
        if e:
            s.append("  - platform: homeassistant\n    id: ha_" + base + "_b\n    entity_id: " + e + "\n    attribute: brightness\n    on_value:\n"
                     "      - lvgl.slider.update: { id: " + sldid + ", value: !lambda 'return (int)(x/2.55);' }\n"
                     "      - lvgl.label.update: { id: " + pct + ", text: !lambda 'return std::to_string((int)(x/2.55)) + \"%\";' }\n")
        return [card_obj(x, y, w, h, inner, None)], s, []

    # Whole-card dimmer (drag to dim; vertical fill for portrait, horizontal otherwise)
    # + an on/off button at the bottom. Transparent slider owns the drag region above it.
    vertical = gh > gw
    btnh = 46
    regb = h - btnh - 12                                  # dimmer region bottom (button sits below)
    if vertical:
        fh0 = int(regb * bri / 100)
        inner = ("              - obj: { id: " + fillid + ", x: 0, y: " + str(regb - fh0) + ", width: " + str(w) +
                 ", height: " + str(fh0) + ", bg_color: 0x2ED5B8, bg_opa: 55%, border_width: 0, radius: 12, pad_all: 0, scrollable: false }\n")
    else:
        inner = ("              - obj: { id: " + fillid + ", x: 0, y: 0, width: " + str(int(w * bri / 100)) +
                 ", height: " + str(regb) + ", bg_color: 0x2ED5B8, bg_opa: 55%, border_width: 0, radius: 12, pad_all: 0, scrollable: false }\n")
    inner += lbl(icon, 0, 18, "f_icon", "0xF2B84B", align="top_mid")
    inner += lbl(name, 0, 52, "f_title", "0xF3F5F8", align="top_mid", width=w - 20, text_align="center", long="dot", height=26)
    inner += lbl(str(bri) + "%", 0, -(btnh + 16), "f_head", "0xF3F5F8", wid=pct, align="bottom_mid")
    sld = ("              - slider:\n                  id: " + sldid + "\n                  x: 0\n                  y: 0\n                  width: " + str(w) + "\n                  height: " + str(regb) + "\n"
           "                  bg_opa: 0%\n                  min_value: 0\n                  max_value: 100\n                  value: " + str(bri) + "\n"
           "                  indicator: { bg_opa: 0% }\n                  knob: { bg_opa: 0% }\n"
           "                  on_value:\n")
    if vertical:
        sld += "                    - lvgl.widget.update: { id: " + fillid + ", height: !lambda 'return (int)(lv_slider_get_value(id(" + sldid + ")) * " + str(regb) + " / 100.0);' }\n"
        sld += "                    - lvgl.widget.update: { id: " + fillid + ", y: !lambda 'return (int)(" + str(regb) + " - lv_slider_get_value(id(" + sldid + ")) * " + str(regb) + " / 100.0);' }\n"
    else:
        sld += "                    - lvgl.widget.update: { id: " + fillid + ", width: !lambda 'return (int)(lv_slider_get_value(id(" + sldid + ")) * " + str(w) + " / 100.0);' }\n"
    sld += "                    - lvgl.label.update: { id: " + pct + ", text: !lambda 'return std::to_string((int) lv_slider_get_value(id(" + sldid + "))) + \"%\";' }\n"
    if e:
        sld += "                  on_release:\n                    - homeassistant.action: { action: light.turn_on, data: { entity_id: " + e + ", brightness_pct: !lambda 'return (int) lv_slider_get_value(id(" + sldid + "));' } }\n"
    inner += sld
    inner += ("              - button:\n                  id: " + pwrid + "\n                  x: 10\n                  y: " + str(h - btnh - 8) + "\n                  width: " + str(w - 20) + "\n                  height: " + str(btnh) + "\n"
              "                  bg_color: 0x161B24\n                  border_color: 0x23262F\n                  border_width: 1\n                  radius: 12\n                  pad_all: 0\n                  scrollable: false\n"
              "                  widgets: [label: { text: \"\\U000F0425\", align: center, text_font: f_icon, text_color: 0xF2B84B }]\n"
              "                  on_click: [" + tog + "]\n")
    s, t = [], []
    if e:
        rb = ("  - platform: homeassistant\n    id: ha_" + base + "_b\n    entity_id: " + e + "\n    attribute: brightness\n    on_value:\n"
              "      - lvgl.slider.update: { id: " + sldid + ", value: !lambda 'return (int)(x/2.55);' }\n")
        if vertical:
            rb += "      - lvgl.widget.update: { id: " + fillid + ", height: !lambda 'return (int)((x/2.55) * " + str(regb) + " / 100.0);' }\n"
            rb += "      - lvgl.widget.update: { id: " + fillid + ", y: !lambda 'return (int)(" + str(regb) + " - (x/2.55) * " + str(regb) + " / 100.0);' }\n"
        else:
            rb += "      - lvgl.widget.update: { id: " + fillid + ", width: !lambda 'return (int)((x/2.55) * " + str(w) + " / 100.0);' }\n"
        rb += "      - lvgl.label.update: { id: " + pct + ", text: !lambda 'return std::to_string((int)(x/2.55)) + \"%\";' }\n"
        s.append(rb)                                      # brightness = numeric sensor
        # on/off state is a TEXT value -> text_sensor (not the numeric sensor list)
        t.append("  - platform: homeassistant\n    id: ha_" + base + "_s\n    entity_id: " + e + "\n    on_value:\n"
                 "      - lvgl.widget.update: { id: " + pwrid + ", bg_color: !lambda 'return x == \"on\" ? lv_color_hex(0x241C08) : lv_color_hex(0x161B24);' }\n")
    return [card_obj(x, y, w, h, inner)], s, t


SENSOR_ICON_COLOR = {"temp": "0xF2685A", "humid": "0x4FA8F5", "illum": "0xF2B84B",
                     "lux": "0xF2B84B", "power": "0xF2B84B", "watt": "0xF2B84B",
                     "energy": "0xF2B84B", "batt": "0x2ED5B8"}


def _sensor_color(card):
    ck = card.get("ck", "")
    if ck in ("person", "binary"):
        return "0x4FA8F5"
    if ck == "alarm":
        return "0xF2685A"
    key = (card.get("device_class") or card.get("entity") or card.get("name") or "").lower()
    for k, c in SENSOR_ICON_COLOR.items():
        if k in key:
            return c
    return "0xF2685A"


def c_sensor(card, x, y, w, h, base):
    e = card.get("entity", "")
    vid = base + "_v"
    inner = ic(card["ck"], color=_sensor_color(card))          # icon top, per-domain color
    inner += lbl("--", 14, -32, "f_title", "0xF3F5F8", wid=vid, align="bottom_left")  # value (bottom stack)
    inner += lbl(card.get("name", "Sensor"), 14, -12, "f_small", "0x868CA0", align="bottom_left")
    ts = []
    if e:
        ts.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return x;' }\n" % (vid, e, vid))
    return [card_obj(x, y, w, h, inner)], [], ts


def _setbox(bx, by, bw, bh, label, temp, accent, bg, vid, e, heat_vid, cool_vid):
    """A HEAT TO / COOL TO setpoint box with working +/- (climate.set_temperature).
    vid = this box's value label id; heat_vid/cool_vid = both setpoint label ids
    (sent together as target_temp_low/high)."""
    s = ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: %s, "
         "border_color: %s, border_width: 1, radius: 12, pad_all: 0, scrollable: false }\n"
         % (bx, by, bw, bh, bg, accent))
    s += lbl(label, bx, by + 12, "f_small", accent, width=bw, text_align="center")
    s += lbl(temp, bx, by + bh // 2 - 4, "f_head", accent, wid=vid, width=bw, text_align="center", height=34)
    setT = ("\n                    - homeassistant.action:\n                        action: climate.set_temperature\n"
            "                        data: { entity_id: %s, target_temp_low: !lambda 'return atof(lv_label_get_text(id(%s)));', target_temp_high: !lambda 'return atof(lv_label_get_text(id(%s)));' }"
            % (e, heat_vid, cool_vid)) if e else ""
    for glyph, delta, gx in (("\\U000F0374", -1, bx + 12), ("\\U000F0415", 1, bx + bw - 52)):
        s += ("              - button:\n                  x: %d\n                  y: %d\n                  width: 40\n                  height: 40\n"
              "                  bg_opa: 0%%\n                  radius: 10\n                  pad_all: 0\n                  scrollable: false\n"
              "                  widgets: [label: { text: \"%s\", align: center, text_font: f_icon, text_color: %s }]\n"
              "                  on_click:\n                    - lambda: |-\n"
              "                        int v = atoi(lv_label_get_text(id(%s))) + (%d);\n"
              "                        char b[8]; snprintf(b, sizeof(b), \"%%d\\u00B0\", v); lv_label_set_text(id(%s), b);%s\n"
              % (gx, by + bh // 2 - 6, glyph, accent, vid, delta, vid, setT))
    return s


CLIMATE_MODES = [("\\U000F0425", "Off", "off", "0x868CA0"),
                 ("\\U000F0238", "Heat", "heat", "0xF2B84B"),
                 ("\\U000F0717", "Cool", "cool", "0x4FA8F5"),
                 ("\\U000F04E2", "Auto", "auto", "0x2ED5B8")]


def c_climate(card, x, y, w, h, base):
    """Thermostat card (image): header + mode badge, big current temp, HEAT/COOL
    setpoint boxes, and a Cool/Heat/Auto/Off mode row."""
    e = card.get("entity", "")
    tid = base + "_t"
    sel_mode = "heat"                                    # demo (no HA state feed)
    inner = ic(card["ck"], color="0x2ED5B8")
    inner += lbl(card.get("name", "Climate"), 50, 14, "f_title", width=w - 60, long="dot", height=26)
    heat_vid, cool_vid = base + "_hi", base + "_lo"
    s = []
    if e:                                                # current temp label (tid) exists in BOTH branches
        s.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: current_temperature\n    on_value:\n"
                 "      - lvgl.label.update: { id: %s, text: !lambda 'return std::to_string((int)x) + \"\\u00B0\";' }\n"
                 % (tid, e, tid))
    if w < 380 or h < 240:                               # compact: name (top) + big live temp (bottom), no overlap
        inner += lbl("71\\u00B0", 0, -8, "f_display", "0xF3F5F8", wid=tid, align="bottom_mid")
        return [card_obj(x, y, w, h, inner)], s, []
    inner += lbl("now 71\\u00B0 \\u00B7 41% RH", 50, 44, "f_small", "0x868CA0")   # rich branch only
    if e:                                                # setpoint readbacks only where HEAT/COOL boxes (hi/lo) exist
        s.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: target_temp_low\n    on_value:\n"
                 "      - lvgl.label.update: { id: %s, text: !lambda 'return std::to_string((int)x) + \"\\u00B0\";' }\n"
                 % (heat_vid, e, heat_vid))
        s.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: target_temp_high\n    on_value:\n"
                 "      - lvgl.label.update: { id: %s, text: !lambda 'return std::to_string((int)x) + \"\\u00B0\";' }\n"
                 % (cool_vid, e, cool_vid))
    mode_y = h - 62
    box_x = int(w * 0.40)
    box_w = w - box_x - 16
    top, gap = 64, 16
    box_h = (mode_y - top - gap) // 2 - 22        # short setpoint boxes with a gap between
    # current temp centered between the left edge and the setpoint boxes
    inner += lbl("71\\u00B0", 0, -6, "f_display", "0xF3F5F8", wid=tid, align="left_mid", width=box_x, text_align="center")
    inner += _setbox(box_x, top, box_w, box_h, "HEAT TO", "68\\u00B0", "0xF2B84B", "0x241C08", heat_vid, e, heat_vid, cool_vid)
    inner += _setbox(box_x, top + box_h + gap, box_w, box_h, "COOL TO", "74\\u00B0", "0x4FA8F5", "0x0F1A2B", cool_vid, e, heat_vid, cool_vid)
    mbw = (w - 28 - 3 * 8) // 4
    for i, (g, lab, mode, acc) in enumerate(CLIMATE_MODES):
        mx = 14 + i * (mbw + 8)
        selm = (mode == sel_mode)
        act = ("homeassistant.action: { action: climate.set_hvac_mode, data: { entity_id: %s, hvac_mode: \"%s\" } }" % (e, mode)) if e else "lvgl.page.show: page_home"
        inner += ("              - button:\n"
                  "                  x: %d\n                  y: %d\n                  width: %d\n                  height: 50\n"
                  "                  bg_color: %s\n                  radius: 12\n                  pad_all: 0\n                  scrollable: false\n"
                  "                  on_click: [%s]\n"
                  "                  widgets:\n"
                  "                    - obj:\n"
                  "                        align: center\n                        width: SIZE_CONTENT\n                        height: SIZE_CONTENT\n"
                  "                        bg_opa: 0\n                        border_width: 0\n                        pad_all: 0\n                        scrollable: false\n"
                  "                        layout: { type: flex, flex_flow: ROW, flex_align_cross: center, pad_column: 8 }\n"
                  "                        widgets:\n"
                  "                          - label: { text: \"%s\", text_font: f_icon, text_color: %s }\n"
                  "                          - label: { text: \"%s\", text_font: f_body, text_color: %s }\n"
                  % (mx, mode_y, mbw, (acc if selm else "0x10141C"), act, g,
                     ("0x0A0B0F" if selm else "0x868CA0"), lab, ("0x0A0B0F" if selm else "0xC2C7D2")))
    return [card_obj(x, y, w, h, inner)], s, []


ACTION_ACCENT = {"scene": "0x2ED5B8", "script": "0xB06CFF", "button": "0x4FA8F5", "input_button": "0x4FA8F5"}


def c_action(card, x, y, w, h, base):
    e = card.get("entity", "")
    ck = card.get("ck", "scene")
    dom = e.split(".")[0] if "." in e else ck
    act = {"scene": "scene.turn_on", "script": "script.turn_on", "button": "button.press",
           "input_button": "input_button.press"}.get(dom, "homeassistant.toggle")
    acc = ACTION_ACCENT.get(ck) or ACTION_ACCENT.get(dom, "0x2ED5B8")
    inner = ic(ck, color=acc)                                 # icon top-left, per-type accent
    inner += title(card.get("name", "Scene"), w, x=14, y=48)
    on = ha(act, e) if e else None
    return [card_obj(x, y, w, h, inner, on)], [], []


# Album art plumbing: c_media registers (size, image_widget_id, entity) here; assemble()
# emits one online_image decoder per distinct size + a sp_nowplaying_image_url readback
# per entity that set_url+updates every used decoder. Host builds skip art (no decoders).
ART_IMAGES = []
ART_ENABLED = True


def c_media(card, x, y, w, h, base):
    """Media / Spotify / Sonos now-playing card. Volume slider on every card >= 3 cells (image 1)."""
    e = card.get("entity", "")
    ck = card["ck"]
    tid = base + "_t"
    aid = base + "_a"
    sld = base + "_vol"
    gw, gh = card["w"], card["h"]
    cells = gw * gh
    has_vol = cells >= 3
    nowplaying = gh >= 3 and gw >= 2
    prev_g, play_g, next_g, vol_g = "\\U000F04AE", "\\U000F03E4", "\\U000F04AD", "\\U000F057E"  # pause glyph (demo = playing)
    if ck == "sonos":
        subtxt = "Kitchen \\u00B7 Sonos"
    elif nowplaying:
        subtxt = "Playlist \\u00B7 Late Night Drive"
    else:
        subtxt = "NOW PLAYING"
    prev = ha("media_player.media_previous_track", e) if e else "lvgl.page.show: page_home"
    plpz = ha("media_player.media_play_pause", e) if e else "lvgl.page.show: page_home"
    nxt = ha("media_player.media_next_track", e) if e else "lvgl.page.show: page_home"

    def art(ax, ay, aw, ah, real=0):
        s = ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x1B1E27, "
             "radius: 12, border_width: 0, pad_all: 0, scrollable: false }\n" % (ax, ay, aw, ah))
        s += lbl("\\U000F075A", ax + (aw - 30) // 2, ay + (ah - 34) // 2, "f_icon", "0x5D6470")  # placeholder under the art
        if real and e and ART_ENABLED:                    # live album art on top (device build only)
            img_id = "%s_art" % base
            ix = ax + (aw - real) // 2
            iy = ay + (ah - real) // 2
            s += ("              - image: { id: %s, x: %d, y: %d, src: gen_art_%d }\n" % (img_id, ix, iy, real))
            ART_IMAGES.append((real, img_id, e))
        return s

    def transport_center(ty):
        if w < 200:
            small, big, gap = 38, 46, 8
        else:
            small, big, gap = 46, 56, 14
        total = small * 2 + big + gap * 2
        sx = (w - total) // 2
        yo = (big - small) // 2
        s = btn(sx, ty + yo, small, small, prev_g, prev, radius=small // 2, font="f_icon")
        s += btn(sx + small + gap, ty, big, big, play_g, plpz, bg="0x2ED5B8", color="0x06231D", radius=big // 2, font="f_icon")
        s += btn(sx + small + gap + big + gap, ty + yo, small, small, next_g, nxt, radius=small // 2, font="f_icon")
        return s

    def vol_slider(vy):
        st = lbl(vol_g, 14, vy - 2, "f_icon", "0x868CA0")     # glyph vertically centered on the track
        st += vol_slider_yaml(sld, 48, vy + 6, w - 62, 55, e)
        return st

    ts = []
    if e:
        ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: media_title\n    on_value:\n"
                  "      - lvgl.label.update: { id: %s, text: !lambda 'return x.empty() ? std::string(\"Nothing playing\") : x;' }\n" % (tid, e, tid))
        ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: media_artist\n    on_value:\n"
                  "      - lvgl.label.update: { id: %s, text: !lambda 'return x;' }\n" % (aid, e, aid))
        if has_vol:
            ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: volume_level\n    on_value:\n"
                      "      - lvgl.slider.update: { id: %s, value: !lambda 'return (int)(atof(x.c_str()) * 100);' }\n" % (sld, e, sld))

    inner = ""
    # ---- 1x1: art + title + play ----
    if gw == 1 and gh == 1:
        inner += art(14, 14, 40, 40)
        inner += lbl("Midnight City", 62, 18, "f_body", "0xF3F5F8", wid=tid, width=w - 74, long="dot", height=20)
        inner += lbl("M83", 62, 44, "f_small", "0x868CA0", wid=aid, width=w - 74, long="dot", height=16)
        inner += btn(w - 46, h - 46, 34, 34, play_g, plpz, bg="0x2ED5B8", color="0x06231D", radius=17, font="f_icon")
        return [card_obj(x, y, w, h, inner)], [], ts
    # ---- h==1 row (2x1, 3x1): title + artist only (no subtitle line) ----
    if gh == 1:
        cy = h // 2
        bw, g = 40, 6
        inner += art(14, (h - 56) // 2, 56, 56)
        nx = w - 14 - bw
        px = nx - (bw + g)
        vx = px - (bw + g)
        inner += btn(vx, cy - bw // 2, bw, bw, prev_g, prev, radius=bw // 2, font="f_icon")
        inner += btn(px, cy - bw // 2, bw, bw, play_g, plpz, bg="0x2ED5B8", color="0x06231D", radius=bw // 2, font="f_icon")
        inner += btn(nx, cy - bw // 2, bw, bw, next_g, nxt, radius=bw // 2, font="f_icon")
        if has_vol:
            txtw = 108
            inner += lbl("Midnight City", 82, 26, "f_body", "0xF3F5F8", wid=tid, width=txtw, long="dot", height=20)
            inner += lbl("M83", 82, 50, "f_small", "0x868CA0", wid=aid, width=txtw if has_vol else tw, long="dot", height=16)
            vsx = 82 + txtw + 12
            inner += lbl(vol_g, vsx, cy - 6, "f_icon", "0x868CA0")
            svx = vsx + 30
            inner += vol_slider_yaml(sld, svx, cy - 4, (vx - 12) - svx, 55, e)
        else:
            tw = (vx - 12) - 82
            inner += lbl("Midnight City", 82, 26, "f_body", "0xF3F5F8", wid=tid, width=tw, long="dot", height=20)
            inner += lbl("M83", 82, 50, "f_small", "0x868CA0", wid=aid, width=txtw if has_vol else tw, long="dot", height=16)
        return [card_obj(x, y, w, h, inner)], [], ts
    # ---- w==1 tall narrow (1x2, 1x3): art on top ----
    if gw == 1:
        asz = w - 28
        arth = min(asz, h - 118)
        inner += art(14, 14, asz, arth)
        ty0 = 14 + arth + 8
        inner += lbl(subtxt, 14, ty0, "f_small", "0x2ED5B8", width=w - 28, long="dot")
        inner += lbl("Midnight City", 14, ty0 + 16, "f_body", "0xF3F5F8", wid=tid, width=w - 28, long="dot")
        inner += lbl("M83", 14, ty0 + 38, "f_small", "0x868CA0", wid=aid, width=w - 28, long="dot", height=16)
        inner += transport_center(ty0 + 56)
        if has_vol:
            inner += vol_slider(h - 34)
        return [card_obj(x, y, w, h, inner)], [], ts
    # ---- now-playing (>=2 wide, >=3 tall): big art + progress ----
    if nowplaying:
        if gw >= 3:                                    # art beside the title block
            inner += art(14, 14, 110, 110, real=108)
            inner += lbl(subtxt, 136, 20, "f_small", "0x2ED5B8", width=w - 150, long="dot", height=16)
            inner += lbl("Midnight City", 136, 40, "f_track", "0xF3F5F8", wid=tid, width=w - 150, long="dot", height=32)
            inner += lbl("M83", 136, 86, "f_body", "0x868CA0", wid=aid, width=w - 150, long="dot", height=18)
            py = 152
            tport = py + 40
        else:                                          # narrow: art on top, full-width title
            inner += art(14, 14, w - 28, 108, real=108)
            inner += lbl(subtxt, 14, 130, "f_small", "0x2ED5B8", width=w - 28, long="dot", height=16)
            inner += lbl("Midnight City", 14, 146, "f_track", "0xF3F5F8", wid=tid, width=w - 28, long="dot", height=32)
            inner += lbl("M83", 14, 180, "f_body", "0x868CA0", wid=aid, width=w - 28, long="dot", height=18)
            py = 202
            tport = 226
        inner += ("              - obj: { x: 14, y: %d, width: %d, height: 6, bg_color: 0x23262F, radius: 3, border_width: 0, pad_all: 0, scrollable: false }\n" % (py, w - 28))
        inner += ("              - obj: { x: 14, y: %d, width: %d, height: 6, bg_color: 0x2ED5B8, radius: 3, border_width: 0, pad_all: 0, scrollable: false }\n" % (py, int((w - 28) * 0.42)))
        inner += lbl("1:38", 14, py + 10, "f_mono", "0x2ED5B8")
        inner += lbl("-2:25", -14, py + 10, "f_mono", "0x868CA0", align="top_right")
        inner += transport_center(tport)
        inner += vol_slider(h - 34)
        return [card_obj(x, y, w, h, inner)], [], ts
    # ---- medium (2x2, 3x2) ----
    inner += art(14, 14, 58, 58, real=58)
    inner += lbl(subtxt, 82, 18, "f_small", "0x2ED5B8", width=w - 96, long="dot", height=16)
    inner += lbl("Midnight City", 82, 36, "f_title", "0xF3F5F8", wid=tid, width=w - 96, long="dot", height=28)
    inner += lbl("M83", 82, 68, "f_small", "0x868CA0", wid=aid, width=w - 96, long="dot", height=16)
    inner += transport_center(h - 108)
    inner += vol_slider(h - 40)
    return [card_obj(x, y, w, h, inner)], [], ts


def c_fan(card, x, y, w, h, base):
    e = card.get("entity", "")
    gw, gh = card["w"], card["h"]
    if gw * gh <= 2:                                  # small: icon + centered label, card colored when on
        on = True                                     # demo (no HA); real per-entity state is TODO
        act = ha("fan.toggle", e) if e else "lvgl.page.show: page_home"
        col = "0x2ED5B8" if on else "0xC2C7D2"
        inner = lbl(CARD_ICON.get(card["ck"], "\\U000F0210"), 0, -20, "f_icon", col, align="center")
        inner += lbl(card.get("name", "Fan"), 0, 24, "f_body", col, align="center", width=w - 24, text_align="center", long="dot")
        return [card_obj(x, y, w, h, inner, act, bg=("0x0F3D34" if on else None))], [], []
    inner = ic(card["ck"], color="0x2ED5B8")
    inner += title(card.get("name", "Fan"), w, x=14, y=48)   # larger: Off / Low / Med / High segments
    speeds = ["Off", "Low", "Med", "High"]
    n = len(speeds); pad = 14; sw2 = (w - pad * 2 - (n - 1) * 8) // n; sy = h - 58
    for i, s in enumerate(speeds):
        sel = (i == 2)                                       # demo: Med selected
        act = ha("fan.toggle", e) if e else "lvgl.page.show: page_home"
        inner += btn(pad + i * (sw2 + 8), sy, sw2, 46, s, act, font="f_small",
                     bg=("0x2ED5B8" if sel else "0x0F1117"), color=("0x06231D" if sel else "0xC2C7D2"))
    return [card_obj(x, y, w, h, inner)], [], []


def c_cover(card, x, y, w, h, base):
    """Cover: Open / Stop / Close buttons (icon + label), like the web."""
    e = card.get("entity", "")
    inner = ic(card["ck"], color="0x2ED5B8")
    if card["w"] == 1 and card["h"] == 1:
        inner += lbl(card.get("name", "Cover"), 0, 0, "f_body", "0x2ED5B8",
                     align="center", width=w - 20, text_align="center", long="dot")
        return [card_obj(x, y, w, h, inner)], [], []
    inner += title(card.get("name", "Cover"), w)
    rows = [("\\U000F0143", "Open", "cover.open_cover", "0xC2C7D2"),
            ("\\U000F04DB", "Stop", "cover.stop_cover", "0xC2C7D2"),
            ("\\U000F0140", "Close", "cover.close_cover", "0xC2C7D2")]
    top, gap = 58, 8
    bh = (h - top - 14 - 2 * gap) // 3
    for i, (g, txt, svc, col) in enumerate(rows):
        cy = top + i * (bh + gap)
        act = ha(svc, e) if e else "lvgl.page.show: page_home"
        inner += ("              - button:\n"
                  "                  x: 14\n                  y: %d\n                  width: %d\n                  height: %d\n"
                  "                  bg_color: 0x161B24\n                  radius: 12\n                  pad_all: 0\n                  scrollable: false\n"
                  "                  on_click: [%s]\n"
                  "                  widgets:\n"
                  "                    - obj:\n"
                  "                        align: center\n                        width: SIZE_CONTENT\n                        height: SIZE_CONTENT\n"
                  "                        bg_opa: 0\n                        border_width: 0\n                        pad_all: 0\n                        scrollable: false\n"
                  "                        layout: { type: flex, flex_flow: ROW, flex_align_cross: center, pad_column: 10 }\n"
                  "                        widgets:\n"
                  "                          - label: { text: \"%s\", text_font: f_icon, text_color: %s }\n"
                  "                          - label: { text: \"%s\", text_font: f_body, text_color: %s }\n"
                  % (cy, w - 28, bh, act, g, col, txt, col))
    return [card_obj(x, y, w, h, inner)], [], []


def c_lock(card, x, y, w, h, base):
    e = card.get("entity", ""); sid = base + "_st"
    gw, gh = card["w"], card["h"]
    inner = ic(card["ck"], color="0x2ED5B8")
    ts = []
    if e:
        ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
                  "      - lvgl.label.update:\n          id: %s\n"
                  "          text: !lambda 'return x == \"locked\" ? std::string(\"Locked\") : std::string(\"Unlocked\");'\n"
                  "          text_color: !lambda 'return x == \"locked\" ? lv_color_hex(0x2ED5B8) : lv_color_hex(0xF2685A);'\n" % (sid, e, sid))
    if gw >= 2 and gh >= 2:
        # battery readout (top-right) + title + state + Lock/Unlock action row (Card Library §05)
        inner += lbl("\\U000F0079", -58, 18, "f_iconsm", "0x868CA0", align="top_right")
        inner += lbl("87%", -14, 19, "f_mono", "0x868CA0", align="top_right")
        inner += title(card.get("name", "Lock"), w, x=14, y=52)
        inner += lbl("--", 14, 84, "f_small", "0x2ED5B8", wid=sid)
        by = h - 60
        bw = (w - 28 - 8) // 2
        lockA = ha("lock.lock", e) if e else "lvgl.page.show: page_home"
        unlockA = ha("lock.unlock", e) if e else "lvgl.page.show: page_home"
        inner += btn(14, by, bw, 46, "Lock", lockA, bg="0x2ED5B8", color="0x06231D")          # demo: locked
        inner += btn(14 + bw + 8, by, bw, 46, "Unlock", unlockA, bg="0x161B24", color="0xC2C7D2")
        return [card_obj(x, y, w, h, inner)], [], ts
    inner += title(card.get("name", "Lock"), w, x=14, y=48)
    inner += lbl("--", 14, -12, "f_small", "0x2ED5B8", wid=sid, align="bottom_left")
    on = ha("lock.unlock", e) if e else None
    return [card_obj(x, y, w, h, inner, on)], [], ts


# Full-forecast demo data (pre-baked; glyphs confirmed in f_wxicon/f_icon)
WX_HOURLY = [("Now", "\\U000F0594", "72"), ("10P", "\\U000F0594", "71"), ("11P", "\\U000F0590", "70"),
             ("12A", "\\U000F0590", "70"), ("1A", "\\U000F0594", "69"), ("2A", "\\U000F0594", "69"), ("3A", "\\U000F0594", "68")]
WX_DAILY = [("Today", "\\U000F0599", "74", "96"), ("Sat", "\\U000F0590", "75", "95"), ("Sun", "\\U000F0599", "76", "97"),
            ("Mon", "\\U000F0597", "72", "88"), ("Tue", "\\U000F0596", "71", "85"), ("Wed", "\\U000F0599", "73", "93")]
WX_STATS = [("HUMIDITY", "57%", "0x4FA8F5"), ("WIND", "6 mph", "0xF3F5F8"), ("UV INDEX", "0 Low", "0xF3F5F8"),
            ("PRESSURE", "30.1", "0xF3F5F8"), ("SUNRISE", "6:32a", "0xF2B84B"), ("SUNSET", "8:37p", "0xF2685A")]


def _wx_temp_readback(base, e, tid):
    return ("  - platform: homeassistant\n    id: ha_%s_wt\n    entity_id: %s\n    attribute: temperature\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'char b[12]; snprintf(b, sizeof(b), \"%%.0f\\u00B0\", x); return std::string(b);' }\n"
            % (base, e, tid))


def c_weather(card, x, y, w, h, base):
    e = card.get("entity", "")
    tid, cid = base + "_t", base + "_c"
    if w < 620 or h < 400:                               # compact: icon + temp + condition
        inner = "              - label: { text: \"\\U000F0599\", x: 14, y: 14, text_font: f_wxicon, text_color: 0xF2B84B }\n"
        inner += lbl("72\\u00B0", -16, 20, "f_display", "0xF3F5F8", wid=tid, align="top_right")
        inner += lbl("Sunny", 14, -12, "f_body", "0x2ED5B8", wid=cid, align="bottom_left")
        return [card_obj(x, y, w, h, inner)], ([_wx_temp_readback(base, e, tid)] if e else []), []
    # large: full forecast (hero + hourly + daily + stats), values pre-baked
    pad = 14
    hh = 128
    inner = ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x141F38, bg_grad_color: 0x0E1524, bg_grad_dir: VER, border_width: 0, radius: 16, pad_all: 0, scrollable: false }\n" % (pad, pad, w - 2 * pad, hh))
    inner += "              - label: { text: \"\\U000F0594\", x: %d, y: %d, text_font: f_wxicon, text_color: 0x8FA6FF }\n" % (pad + 26, pad + 26)
    inner += lbl("72\\u00B0", pad + 150, pad + 10, "f_display", "0xF3F5F8", wid=tid)
    inner += lbl("Clear \\u00B7 Feels like 73\\u00B0", pad + 152, pad + 72, "f_body", "0xC2C7D2", wid=cid, width=340, long="dot", height=20)
    inner += lbl("PRE-BAKED SKY", -(pad + 4), pad + 14, "f_micro", "0x5D6470", align="top_right")
    inner += lbl("Home", -(pad + 4), pad + 34, "f_title", "0xF3F5F8", align="top_right")
    inner += lbl("Friday \\u00B7 9:41 PM", -(pad + 4), pad + 70, "f_small", "0x868CA0", align="top_right")
    inner += lbl("High 96\\u00B0 \\u00B7 Low 74\\u00B0", -(pad + 4), pad + 92, "f_small", "0x868CA0", align="top_right")
    hy, ht, gap = pad + hh + 12, 116, 10
    n = len(WX_HOURLY)
    tw = (w - 2 * pad - (n - 1) * gap) // n
    for i, (hl, g, tp) in enumerate(WX_HOURLY):
        hx = pad + i * (tw + gap)
        bg = "0x11201C" if i == 0 else "0x14161C"
        inner += ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: %s, border_width: 0, radius: 12, pad_all: 0, scrollable: false, widgets: ["
                  "label: { text: %s, align: top_mid, y: 12, text_font: f_small, text_color: 0x868CA0 }, "
                  "label: { text: \"%s\", align: center, text_font: f_icon, text_color: 0x8FA6FF }, "
                  "label: { text: \"%s\\u00B0\", align: bottom_mid, y: -14, text_font: f_body, text_color: 0xF3F5F8 }] }\n"
                  % (hx, hy, tw, ht, bg, esc(hl), g, tp))
    dy = hy + ht + 14
    dh = h - dy - pad
    dax = int((w - 2 * pad) * 0.60) + pad                # right edge of the daily column
    rn = len(WX_DAILY)
    rh = dh // rn
    for i, (dn, g, lo, hi) in enumerate(WX_DAILY):
        ry = dy + i * rh
        ly = ry + (rh - 18) // 2
        inner += lbl(dn, pad, ly, "f_body", "0xF3F5F8", width=58)
        inner += "              - label: { text: \"%s\", x: %d, y: %d, text_font: f_icon, text_color: 0xF2B84B }\n" % (g, pad + 64, ry + (rh - 30) // 2)
        inner += lbl(lo + "\\u00B0", pad + 106, ly, "f_body", "0x868CA0", width=42)
        barx = pad + 152
        inner += "              - obj: { x: %d, y: %d, width: %d, height: 8, bg_color: 0x4FA8F5, bg_grad_color: 0xF2B84B, bg_grad_dir: HOR, border_width: 0, radius: 4, pad_all: 0, scrollable: false }\n" % (barx, ry + (rh - 8) // 2, dax - 48 - barx)
        inner += lbl(hi + "\\u00B0", dax - 44, ly, "f_body", "0xF3F5F8", width=42)
    inner += "              - obj: { x: %d, y: %d, width: 1, height: %d, bg_color: 0x23262F, border_width: 0, pad_all: 0, scrollable: false }\n" % (dax + 8, dy, dh - 6)
    sx0, scols, sgap, srows = dax + 24, 2, 10, 3
    sw = (w - pad - sx0 - (scols - 1) * sgap) // scols
    srh = (dh - (srows - 1) * sgap) // srows
    for i, (lt, val, col) in enumerate(WX_STATS):
        cx = sx0 + (i % scols) * (sw + sgap)
        cy = dy + (i // scols) * (srh + sgap)
        inner += ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x14161C, border_width: 0, radius: 12, pad_all: 0, scrollable: false, widgets: ["
                  "label: { text: %s, x: 14, y: 14, text_font: f_micro, text_color: 0x868CA0 }, "
                  "label: { text: %s, x: 14, y: 32, text_font: f_title, text_color: %s }] }\n"
                  % (cx, cy, sw, srh, esc(lt), esc(val), col))
    return [card_obj(x, y, w, h, inner)], ([_wx_temp_readback(base, e, tid)] if e else []), []


def c_camera(card, x, y, w, h, base):
    inner = ("              - obj: { x: 8, y: 8, width: %d, height: %d, bg_color: 0x10141C, "
             "border_width: 0, radius: 12, pad_all: 0, scrollable: false }\n" % (w - 16, h - 16))
    inner += ic(card["ck"], x=20, y=20, color="0x2A3346")
    # LIVE pill (red chip + white dot) bottom-left
    inner += ("              - obj: { x: 20, y: %d, width: 58, height: 24, bg_color: 0xF2685A, radius: 8, "
              "border_width: 0, pad_all: 0, scrollable: false, widgets: ["
              "obj: { x: 9, align: left_mid, width: 7, height: 7, bg_color: 0xFFFFFF, radius: 4, border_width: 0, pad_all: 0, scrollable: false }, "
              "label: { text: \"LIVE\", x: 22, align: left_mid, text_font: f_micro, text_color: 0xFFFFFF } ] }\n" % (h - 44))
    inner += lbl(card.get("name", "Camera"), 20, -14, "f_body", "0xF3F5F8", align="bottom_left")
    return [card_obj(x, y, w, h, inner)], [], []


def _ename(e, fallback):
    return ((e.split(".")[-1] if "." in e else e).replace("_", " ")) if e else fallback


# domain -> (f_icon glyph, color, demo value) for status-group tiles (web GICON/GVAL)
GROUP_DOMAIN = {
    "light": ("\\U000F0335", "0xF2B84B", "On"),
    "switch": ("\\U000F06A5", "0x2ED5B8", "On"),
    "lock": ("\\U000F033E", "0x2ED5B8", "Locked"),
    "binary_sensor": ("\\U000F0583", "0x4FA8F5", "Clear"),
    "sensor": ("\\U000F050F", "0xF2685A", "72\\u00B0"),
    "cover": ("\\U000F081A", "0x4FA8F5", "Open"),
    "fan": ("\\U000F0210", "0x2ED5B8", "Med"),
    "person": ("\\U000F0004", "0x4FA8F5", "Home"),
    "climate": ("\\U000F0393", "0xF2B84B", "72\\u00B0"),
    "media_player": ("\\U000F075A", "0x2ED5B8", "Idle"),
}

# Status-group live value: domain -> (C++ text expr from `x`, active-bool expr, active color).
# `x` is the entity's std::string state (homeassistant text_sensor). Domains not listed
# show the raw state in the default text color.
GROUP_VAL = {
    "binary_sensor": ("x == \"on\" ? std::string(\"Active\") : std::string(\"Clear\")", "x == \"on\"", "0x4FA8F5"),
    "lock":          ("x == \"locked\" ? std::string(\"Locked\") : std::string(\"Unlocked\")", "x == \"locked\"", "0xF2685A"),
    "cover":         ("x == \"open\" ? std::string(\"Open\") : (x == \"closed\" ? std::string(\"Closed\") : x)", "x == \"open\"", "0x4FA8F5"),
    "person":        ("x == \"home\" ? std::string(\"Home\") : std::string(\"Away\")", "x == \"home\"", "0x4FA8F5"),
    "light":         ("x == \"on\" ? std::string(\"On\") : std::string(\"Off\")", "x == \"on\"", "0xF2B84B"),
    "switch":        ("x == \"on\" ? std::string(\"On\") : std::string(\"Off\")", "x == \"on\"", "0x2ED5B8"),
    "fan":           ("x == \"on\" ? std::string(\"On\") : std::string(\"Off\")", "x == \"on\"", "0x2ED5B8"),
}


def c_group(card, x, y, w, h, base):
    """lightgroup: big lightbulb tiles in a w x max(2,h-1) grid (web .lggrid).
    group: status tiles with domain icon + value + name (web .ggrid / image)."""
    ents = card.get("entities", [])
    gw, gh = card["w"], card["h"]
    if card["ck"] == "lightgroup":
        cols = max(1, gw)
        rows = max(2, gh - 1)
        cap = cols * rows
        on_n = sum(1 for i in range(min(len(ents), cap)) if i % 2 == 0)
        inner = ic(card["ck"], color="0xF2B84B")
        inner += lbl("%s \\u00B7 %d on \\u00B7 %d/%d" % (card.get("name", "Lights"), on_n, len(ents), cap),
                     50, 22, "f_body", "0x868CA0", width=w - 64, long="dot", height=24)
        pad, gap, top = 14, 12, 58
        bw = (w - pad * 2 - (cols - 1) * gap) // cols
        bh = (h - top - pad - (rows - 1) * gap) // rows
        ts = []
        for i in range(cap):
            e = ents[i] if i < len(ents) else None
            on = (e is not None) and (i % 2 == 0)
            cx = pad + (i % cols) * (bw + gap)
            cy = top + (i // cols) * (bh + gap)
            gcol = "0xF2B84B" if on else "0x6B7280"
            bg = "0x211B0A" if on else "0x0F1117"
            tileid, iconid = "%s_lt%d" % (base, i), "%s_li%d" % (base, i)
            click = (", clickable: true, on_click: [%s]" % ha("homeassistant.toggle", e)) if e else ""
            inner += ("              - obj: { id: %s, x: %d, y: %d, width: %d, height: %d, bg_color: %s, "
                      "border_width: 0, radius: 14, pad_all: 0, scrollable: false%s, widgets: ["
                      "label: { id: %s, text: \"\\U000F0335\", align: center, y: -14, text_font: f_icon, text_color: %s }, "
                      "label: { text: %s, align: bottom_mid, y: -10, width: %d, long_mode: dot, text_align: center, text_font: f_small, text_color: 0xC2C7D2 }] }\n"
                      % (tileid, cx, cy, bw, bh, bg, click, iconid, gcol, esc(_ename(e, "Light")), bw - 12))
            if e:                                          # live on/off state -> recolor tile + bulb
                ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
                          "      - lvgl.widget.update: { id: %s, bg_color: !lambda 'return x == \"on\" ? lv_color_hex(0x211B0A) : lv_color_hex(0x0F1117);' }\n"
                          "      - lvgl.label.update: { id: %s, text_color: !lambda 'return x == \"on\" ? lv_color_hex(0xF2B84B) : lv_color_hex(0x6B7280);' }\n"
                          % (iconid, e, tileid, iconid))
        return [card_obj(x, y, w, h, inner)], [], ts
    # group (status): domain icon (left) + value + name tiles
    cols = 2
    rows = max(1, card["h"])
    cap = cols * rows
    pad, gap, top = 14, 10, 56
    bw = (w - pad * 2 - (cols - 1) * gap) // cols
    bh = (h - top - pad - (rows - 1) * gap) // rows
    vfont = "f_title" if bw >= 150 else "f_body"        # big value on wide tiles, fit on narrow
    vh, vy = (30, -12) if vfont == "f_title" else (20, -10)
    inner = ic(card["ck"], color="0x2ED5B8")
    inner += lbl("%s \\u00B7 %d/%d" % (card.get("name", "Group"), len(ents), cap),
                 50, 22, "f_body", "0x868CA0", width=w - 64, long="dot", height=24)
    ts = []
    for i in range(cap):
        e = ents[i] if i < len(ents) else None
        cx = pad + (i % cols) * (bw + gap)
        cy = top + (i // cols) * (bh + gap)
        if e:
            dom = e.split(".")[0]
            glyph, gcol, val = GROUP_DOMAIN.get(dom, ("\\U000F0493", "0x868CA0", "On"))
            valid = "%s_gv%d" % (base, i)
            inner += ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x0F1117, "
                      "border_width: 0, radius: 12, pad_all: 0, scrollable: false, widgets: ["
                      "label: { text: \"%s\", x: 12, align: left_mid, text_font: f_icon, text_color: %s }, "
                      "label: { id: %s, text: \"%s\", x: 50, y: %d, align: left_mid, width: %d, height: %d, long_mode: dot, text_font: %s, text_color: 0xEEF0F6 }, "
                      "label: { text: %s, x: 50, y: 13, align: left_mid, width: %d, long_mode: dot, text_font: f_small, text_color: 0x868CA0 }] }\n"
                      % (cx, cy, bw, bh, glyph, gcol, valid, val, vy, bw - 56, vh, vfont, esc(_ename(e, "Entity")), bw - 56))
            texpr, actp, acol = GROUP_VAL.get(dom, ("x", "false", "0xEEF0F6"))   # live state -> value label
            ts.append("  - platform: homeassistant\n    id: ha_%s_gs%d\n    entity_id: %s\n    on_value:\n"
                      "      - lvgl.label.update: { id: %s, text: !lambda 'return %s;' }\n"
                      "      - lvgl.label.update: { id: %s, text_color: !lambda 'return (%s) ? lv_color_hex(%s) : lv_color_hex(0xEEF0F6);' }\n"
                      % (base, i, e, valid, texpr, valid, actp, acol))
        else:
            inner += ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x0F1117, "
                      "border_color: 0x2A2E38, border_width: 1, radius: 12, pad_all: 0, scrollable: false, widgets: ["
                      "label: { text: \"+\", align: center, text_font: f_head, text_color: 0x4A5160 }] }\n"
                      % (cx, cy, bw, bh))
    return [card_obj(x, y, w, h, inner)], [], ts


def c_outlet(card, x, y, w, h, base):
    """Outlet cells (web .ocell2): a label + a circular power button per outlet,
    laid out in columns (wide card) or rows (tall card)."""
    ents = card.get("entities", [])
    gw, gh = card["w"], card["h"]
    if gw == 1 and gh == 1:                      # single outlet: icon + centered label, card colored when on
        e = ents[0] if ents else ""
        on = True                                # demo (distinct color from the fan)
        act = ha("homeassistant.toggle", e) if e else "lvgl.page.show: page_home"
        col = "0xF2B84B" if on else "0xC2C7D2"
        inner = lbl(CARD_ICON.get(card["ck"], "\\U000F06A5"), 0, -20, "f_icon", col, align="center")
        inner += lbl(_ename(e, "Outlet"), 0, 24, "f_body", col, align="center", width=w - 24, text_align="center", long="dot")
        return [card_obj(x, y, w, h, inner, act, bg=("0x3A2E0A" if on else None))], [], []
    inner = ic(card["ck"], color="0x2ED5B8")
    inner += title(card.get("name", "Outlets"), w)
    horiz = gw > gh
    cells = (ents if ents else [""])[:max(1, (gw if horiz else gh))]
    n = len(cells)
    top, pad, gap = 58, 14, 10
    if horiz:
        cw = (w - pad * 2 - (n - 1) * gap) // n
        ch = h - top - pad
    else:
        cw = w - pad * 2
        ch = (h - top - pad - (n - 1) * gap) // n
    for i, e in enumerate(cells):
        cx = pad + (i * (cw + gap) if horiz else 0)
        cy = top + (0 if horiz else i * (ch + gap))
        on = (i % 2 == 0)
        act = ha("homeassistant.toggle", e) if e else "lvgl.page.show: page_home"
        ps = max(40, min(64, cw - 24, ch - 44))
        inner += ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x0F1117, "
                  "border_width: 0, radius: 12, pad_all: 0, scrollable: false }\n" % (cx, cy, cw, ch))
        inner += lbl(_ename(e, "S%d" % (i + 1)), cx, cy + 10, "f_small", "0xC2C7D2", width=cw, text_align="center", long="dot", height=18)
        inner += btn(cx + (cw - ps) // 2, cy + (ch - ps) // 2 + 10, ps, ps, "\\U000F0425", act,
                     bg=("0x123F30" if on else "0x1A1F29"), color=("0x2ED5B8" if on else "0x6B7280"),
                     radius=ps // 2, font="f_icon")
    return [card_obj(x, y, w, h, inner)], [], []


BTN_ICON = {"speakers": "\\U000F04C3", "sonos_sources": "\\U000F075A", "tv_sources": "\\U000F0502"}


def c_btngrid(card, x, y, w, h, base):
    """Grid of selectable tiles (icon + name), first highlighted — web .spk grid.
    Columns follow the count (web: 1-row=n, 10=5, 6=3, else 2)."""
    ents = card.get("entities", [])
    inner = ic(card["ck"], color="0x2ED5B8")
    inner += lbl(card.get("name", "Select"), 50, 16, "f_small", "0x868CA0")
    gw, gh = card["w"], card["h"]
    n = max(1, len(ents))
    cols = n if gh == 1 else (5 if n == 10 else (3 if n == 6 else 2))
    cols = max(1, min(cols, n))
    rows = max(1, (n + cols - 1) // cols)
    pad, gap, top = 14, 10, 52
    bw = (w - pad * 2 - (cols - 1) * gap) // cols
    bh = (h - top - pad - (rows - 1) * gap) // rows
    bi = BTN_ICON.get(card["ck"], "\\U000F075A")
    src = card["ck"] in ("sonos_sources", "tv_sources")
    for i, e in enumerate(ents[:n]):
        nm = (e.split(".")[-1] if "." in e else e).replace("_", " ")
        cx = pad + (i % cols) * (bw + gap)
        cy = top + (i // cols) * (bh + gap)
        sel = (i == 0)
        bg = "0x2ED5B8" if sel else "0x10141C"
        fg = "0x06231D" if sel else "0xC2C7D2"
        act = (_src(e, nm) if src else ha("media_player.toggle", e)) if e else "lvgl.page.show: page_home"
        inner += ("              - button:\n"
                  "                  x: %d\n                  y: %d\n                  width: %d\n                  height: %d\n"
                  "                  bg_color: %s\n                  radius: 12\n                  pad_all: 0\n                  scrollable: false\n"
                  "                  on_click: [%s]\n"
                  "                  widgets:\n"
                  "                    - label: { text: \"%s\", x: 14, align: left_mid, text_font: f_icon, text_color: %s }\n"
                  "                    - label: { text: %s, x: 48, align: left_mid, width: %d, long_mode: dot, text_font: f_body, text_color: %s }\n"
                  % (cx, cy, bw, bh, bg, act, bi, fg, esc(nm), bw - 56, fg))
    return [card_obj(x, y, w, h, inner)], [], []


def _tvbtn(bx, by, w_, h_, glyph, e, button, **kw):
    act = ("homeassistant.action: { action: webostv.button, data: { entity_id: %s, button: %s } }"
           % (e, button)) if e else "lvgl.page.show: page_home"
    kw.setdefault("font", "f_icon")
    return btn(bx, by, w_, h_, glyph, act, **kw)


def _toggle_btn(bx, by, bw, bh, glyph, show_id, hide_id):
    """Bar button that toggles overlay `show_id` (and always hides sibling `hide_id`)."""
    return (
        "              - button:\n"
        "                  x: %d\n                  y: %d\n                  width: %d\n                  height: %d\n"
        "                  bg_color: 0x161B24\n                  radius: 12\n                  pad_all: 0\n                  scrollable: false\n"
        "                  widgets: [label: { text: %s, align: center, text_font: f_icon, text_color: 0xF3F5F8 }]\n"
        "                  on_click:\n"
        "                    - lambda: |-\n"
        "                        lv_obj_add_flag(id(%s), LV_OBJ_FLAG_HIDDEN);\n"
        "                        if (lv_obj_has_flag(id(%s), LV_OBJ_FLAG_HIDDEN)) lv_obj_clear_flag(id(%s), LV_OBJ_FLAG_HIDDEN);\n"
        "                        else lv_obj_add_flag(id(%s), LV_OBJ_FLAG_HIDDEN);\n"
        % (bx, by, bw, bh, esc(glyph), hide_id, show_id, show_id, show_id)
    )


def _dpad(inner, e, w, h):
    cx, cy, s = w // 2, h // 2 + 10, 50
    okA = ("homeassistant.action: { action: webostv.button, data: { entity_id: %s, button: ENTER } }" % e) if e else "lvgl.page.show: page_home"
    inner += _tvbtn(cx - 25, cy - 78, s, s, "\\U000F0143", e, "UP")
    inner += _tvbtn(cx - 78, cy - 25, s, s, "\\U000F0141", e, "LEFT")
    inner += btn(cx - 32, cy - 32, 64, 64, "OK", okA, bg="0x2ED5B8", color="0x06231D", radius=32)
    inner += _tvbtn(cx + 28, cy - 25, s, s, "\\U000F0142", e, "RIGHT")
    inner += _tvbtn(cx - 25, cy + 28, s, s, "\\U000F0140", e, "DOWN")
    return inner


def c_tv_dpad(card, x, y, w, h, base):
    e = card.get("entity", "")
    inner = ic(card["ck"], color="0xB06CFF") + lbl(card.get("name", "Navigate"), 50, 16, "f_small", "0x868CA0")
    inner = _dpad(inner, e, w, h)
    return [card_obj(x, y, w, h, inner)], [], []


def c_tv_transport(card, x, y, w, h, base):
    e = card.get("entity", "")
    inner = ic(card["ck"], color="0xB06CFF") + lbl(card.get("name", "Transport"), 50, 16, "f_small", "0x868CA0")
    items = [("\\U000F0141", "BACK", None), ("\\U000F02DC", "HOME", None),
             ("\\U000F04AE", None, "media_player.media_previous_track"),
             ("\\U000F040A", None, "media_player.media_play_pause"),
             ("\\U000F04AD", None, "media_player.media_next_track")]
    n = len(items); pad = 14; bw = (w - pad * 2 - (n - 1) * 8) // n; by = h - pad - 52
    for i, (g, bn, svc) in enumerate(items):
        act = (("homeassistant.action: { action: webostv.button, data: { entity_id: %s, button: %s } }" % (e, bn)) if bn else (ha(svc, e))) if e else "lvgl.page.show: page_home"
        main = (i == 3)
        inner += btn(pad + i * (bw + 8), by, bw, 52, g, act, font="f_icon",
                     bg=("0x2ED5B8" if main else "0x161B24"), color=("0x06231D" if main else "0xF3F5F8"))
    return [card_obj(x, y, w, h, inner)], [], []


def c_tv_channel(card, x, y, w, h, base):
    e = card.get("entity", "")
    inner = ic(card["ck"], color="0xB06CFF") + lbl(card.get("name", "Channel"), 50, 16, "f_small", "0x868CA0")
    up = ("homeassistant.action: { action: webostv.button, data: { entity_id: %s, button: CHANNELUP } }" % e) if e else "lvgl.page.show: page_home"
    dn = ("homeassistant.action: { action: webostv.button, data: { entity_id: %s, button: CHANNELDOWN } }" % e) if e else "lvgl.page.show: page_home"
    bh = (h - 66) // 2 - 4
    inner += btn(14, 52, w - 28, bh, "CH +", up)
    inner += btn(14, 52 + bh + 8, w - 28, bh, "CH -", dn)
    return [card_obj(x, y, w, h, inner)], [], []


def c_tv_volume(card, x, y, w, h, base):
    e = card.get("entity", "")
    inner = ic(card["ck"], color="0xB06CFF") + lbl(card.get("name", "Volume"), 50, 16, "f_small", "0x868CA0")
    rows = [("VOL +", "media_player.volume_up", ""), ("Mute", "media_player.volume_mute", 'is_volume_muted: "true"'),
            ("VOL -", "media_player.volume_down", "")]
    bh = (h - 66) // 3 - 4; yy = 52
    for t, svc, extra in rows:
        act = ha(svc, e, extra) if e else "lvgl.page.show: page_home"
        inner += btn(14, yy, w - 28, bh, t, act)
        yy += bh + 6
    return [card_obj(x, y, w, h, inner)], [], []


def c_tv_trackpad(card, x, y, w, h, base):
    inner = ic(card["ck"], color="0xB06CFF") + lbl(card.get("name", "Trackpad"), 50, 16, "f_small", "0x868CA0")
    inner += ("              - obj: { x: 14, y: 50, width: %d, height: %d, bg_color: 0x0F1117, "
              "border_color: 0x23262F, border_width: 1, radius: 12, pad_all: 0, scrollable: false }\n" % (w - 28, h - 64))
    inner += lbl("Tap \\u00B7 Swipe", 0, 4, "f_body", "0x5D6470", align="center")
    return [card_obj(x, y, w, h, inner)], [], []


def _chan(e, up):
    bn = "CHANNELUP" if up else "CHANNELDOWN"
    return ("homeassistant.action: { action: webostv.button, data: { entity_id: %s, button: %s } }"
            % (e, bn)) if e else "lvgl.page.show: page_home"


def _wbtn(e, button):
    return ("homeassistant.action: { action: webostv.button, data: { entity_id: %s, button: %s } }"
            % (e, button)) if e else "lvgl.page.show: page_home"


def _src(e, src):
    return ("homeassistant.action: { action: media_player.select_source, data: { entity_id: %s, source: %s } }"
            % (e, esc(src))) if e else "lvgl.page.show: page_home"


# App name -> (badge color, short mark). Names MUST match the TV's source_list
# for media_player.select_source to launch them.
APP_CATALOG = {
    "Netflix": ("0xE50914", "N"), "YouTube": ("0xFF0000", "Y"), "YouTube TV": ("0xFF0000", "TV"),
    "Disney+": ("0x113CCF", "D"), "Spotify": ("0x1DB954", "S"), "Plex": ("0xE5A00D", "P"),
    "Hulu": ("0x1CE783", "H"), "Prime Video": ("0x00A8E1", "P"), "Max": ("0x7B2BF9", "M"),
    "HBO Max": ("0x7B2BF9", "M"), "Apple TV": ("0x3A3A3C", "A"), "Peacock": ("0x05AC3F", "P"),
    "Paramount+": ("0x0064FF", "P"), "Showtime": ("0xC10000", "SHO"), "STARZ": ("0x000000", "SZ"),
    "ESPN": ("0xD50A0A", "E"), "Prime": ("0x00A8E1", "P"),
}
TV_APPS_DEFAULT = ["Netflix", "YouTube", "YouTube TV", "Disney+", "Showtime"]
TV_APPS_MAX = 8
TV_SOURCES = ["HDMI 1", "Apple TV", "Roku", "Cable"]


def c_tvremote(card, x, y, w, h, base):
    """Full LG remote, matching the web `remote` card. Wide cards (>=6 cells)
    get the apps sidebar; large cards get source chips + VOL/d-pad/CH + the full
    transport bar; small cards fall back to d-pad + 3 transport buttons."""
    e = card.get("entity", "")
    inner = ""
    powA = ("homeassistant.action: { action: media_player.toggle, data: { entity_id: %s } }" % e) if e else "lvgl.page.show: page_home"
    rich = w >= 560 and h >= 320
    if not rich:                       # compact: icon + title + d-pad + 3 transport
        inner = ic(card["ck"], color="0xB06CFF") + lbl("LG OLED", 50, 12, "f_title")
        inner += btn(w - 66, 14, 52, 46, "\\U000F0425", powA, bg="0x2a1414", color="0xF2685A", font="f_icon")
        inner = _dpad(inner, e, w, h - 30)
        items = [("\\U000F04AE", "media_player.media_previous_track"),
                 ("\\U000F040A", "media_player.media_play_pause"),
                 ("\\U000F04AD", "media_player.media_next_track")]
        bw = 56
        for i, (g, svc) in enumerate(items):
            inner += btn(14 + i * (bw + 8), h - 66, bw, 52, g, ha(svc, e) if e else "lvgl.page.show: page_home",
                         font="f_icon", bg=("0x2ED5B8" if i == 1 else "0x161B24"), color=("0x06231D" if i == 1 else "0xF3F5F8"))
        return [card_obj(x, y, w, h, inner)], [], []

    sidebar = w >= 820
    mx = 180 if sidebar else 14            # main-area left edge
    mw = w - mx - 14
    # --- apps sidebar (pre-baked launch tiles) ---
    if sidebar:
        inner += lbl("APPS \\u00B7 PRE-BAKED", 16, 14, "f_micro", "0x5D6470")
        ay = 40
        apps = (card.get("apps") or TV_APPS_DEFAULT)[:TV_APPS_MAX]
        na = max(1, len(apps))
        ah = (h - ay - 14 - (na - 1) * 8) // na
        for i, nm in enumerate(apps):
            col, ltr = APP_CATALOG.get(nm, ("0x555555", (nm[:1] or "?").upper()))
            ty = ay + i * (ah + 8)
            sel = (i == 0)
            inner += (
                "              - button:\n"
                "                  x: 14\n                  y: %d\n                  width: 152\n                  height: %d\n"
                "                  bg_color: %s\n                  radius: 14\n                  pad_all: 0\n                  scrollable: false\n"
                "                  widgets:\n"
                "                    - obj: { x: 14, align: left_mid, width: 32, height: 32, bg_color: %s, radius: 8, pad_all: 0, scrollable: false, widgets: [label: { text: \"%s\", align: center, text_font: f_body, text_color: 0xFFFFFF }] }\n"
                "                    - label: { text: \"%s\", x: 56, align: left_mid, width: 88, long_mode: dot, text_font: f_body, text_color: 0xFFFFFF }\n"
                "                  on_click: [%s]\n"
                % (ty, ah, (col if sel else "0x10141C"), col, ltr, nm, _src(e, nm)))
    # --- header ---
    inner += lbl("\\U000F0502", mx, 14, "f_icon", "0xB06CFF")
    inner += lbl("LG OLED", mx + 42, 12, "f_title")
    inner += lbl("Living Room \\u00B7 HDMI 1", mx + 42, 48, "f_small", "0x868CA0")
    inner += btn(w - 66, 14, 52, 46, "\\U000F0425", powA, bg="0x161B24", color="0xF2685A", font="f_icon")
    # --- source chips (selected = teal fill + ink, mono label) ---
    cx = mx
    for i, s in enumerate(TV_SOURCES):
        sel = (i == 0)
        inner += btn(cx, 76, 104, 36, s, _src(e, s), radius=10, font="f_mono",
                     bg=("0x2ED5B8" if sel else "0x14161C"), color=("0x06231D" if sel else "0x868CA0"))
        cx += 112
    # --- center band: VOL | d-pad | CH ---
    band_top, band_bot = 124, h - 86
    dcy = (band_top + band_bot) // 2
    dcx = mx + mw // 2
    okA = _wbtn(e, "ENTER")
    # No disc — borderless chevrons around a wide teal OK pill (tvremote.png)
    sw, sh, okw, okh = 84, 68, 176, 66
    vgap = (band_bot - band_top) // 2 - sh // 2 - 6
    inner += _tvbtn(dcx - sw // 2, dcy - vgap - sh // 2, sw, sh, "\\U000F0143", e, "UP", bg="0x14161C", font="f_bigicon")
    inner += _tvbtn(dcx - okw // 2 - 14 - sw, dcy - sh // 2, sw, sh, "\\U000F0141", e, "LEFT", bg="0x14161C", font="f_bigicon")
    inner += btn(dcx - okw // 2, dcy - okh // 2, okw, okh, "OK", okA, bg="0x2ED5B8", color="0x06231D", radius=okh // 2, font="f_title")
    inner += _tvbtn(dcx + okw // 2 + 14, dcy - sh // 2, sw, sh, "\\U000F0142", e, "RIGHT", bg="0x14161C", font="f_bigicon")
    inner += _tvbtn(dcx - sw // 2, dcy + vgap - sh // 2, sw, sh, "\\U000F0140", e, "DOWN", bg="0x14161C", font="f_bigicon")
    # VOL column (left of d-pad)
    vx = mx + 12
    inner += _tvbtn(vx, dcy - 72, 76, 60, "\\U000F075D", e, "VOLUMEUP")
    inner += lbl("VOL", vx, dcy - 6, "f_micro", "0x868CA0", width=76, text_align="center")
    inner += _tvbtn(vx, dcy + 14, 76, 60, "\\U000F075E", e, "VOLUMEDOWN")
    # CH column (right of d-pad)
    hx = mx + mw - 88
    inner += btn(hx, dcy - 72, 76, 60, "\\U000F0143", _chan(e, True), font="f_icon")
    inner += lbl("CH", hx, dcy - 6, "f_micro", "0x868CA0", width=76, text_align="center")
    inner += btn(hx, dcy + 14, 76, 60, "\\U000F0140", _chan(e, False), font="f_icon")
    # --- bottom transport bar. Pad toggles the page-level LG trackpad overlay
    #     (_tv_trackpad_overlay) and engages the gesture engine via g_tp_active;
    #     the overlay itself carries the move pad + scroll strip. ---
    bar = [("\\U000F004D", "BACK"), ("\\U000F02DC", "HOME"), ("\\U000F0297", "pad"),
           ("\\U000F04AE", "prev"), ("\\U000F040A", "play"), ("\\U000F04AD", "next"),
           ("\\U000F0211", "FASTFORWARD"), ("\\U000F075F", "MUTE")]
    media_acts = {"prev": "media_player.media_previous_track", "play": "media_player.media_play_pause",
                  "next": "media_player.media_next_track"}
    n = len(bar); bw = (mw - (n - 1) * 8) // n; by = h - 80
    for i, (g, key) in enumerate(bar):
        bx = mx + i * (bw + 8)
        if key == "pad":                              # open the dedicated trackpad page (the proven gesture path)
            inner += btn(bx, by, bw, 62, g, "lvgl.page.show: page_trackpad",
                         font="f_icon", bg="0x161B24", color="0x2ED5B8")
            continue
        if key in media_acts:
            act = ha(media_acts[key], e) if e else "lvgl.page.show: page_home"
        elif key == "MUTE":
            act = ha("media_player.volume_mute", e, 'is_volume_muted: "true"') if e else "lvgl.page.show: page_home"
        else:
            act = _wbtn(e, key)
        main = (key == "play")
        inner += btn(bx, by, bw, 62, g, act, font="f_icon",
                     bg=("0x2ED5B8" if main else "0x161B24"), color=("0x06231D" if main else "0xF3F5F8"))
    return [card_obj(x, y, w, h, inner)], [], []


def c_playlist(card, x, y, w, h, base):
    e = card.get("entity", "")
    pl = card.get("pl") or card.get("name", "Playlist")
    acc = "0x2ED5B8" if card["ck"].startswith("sonos") else "0x1DB954"   # Sonos teal / Spotify green
    on = ("homeassistant.action: { action: media_player.media_play, data: { entity_id: %s } }" % e) if e else None
    if card["w"] == 1 and card["h"] == 1:                 # compact tile: centered icon + small label
        inner = lbl(CARD_ICON.get(card["ck"], "\\U000F075A"), 0, 16, "f_icon", acc, align="top_mid")
        inner += lbl(pl, 0, -12, "f_small", "0xF3F5F8", align="bottom_mid", width=w - 16, text_align="center", long="dot", height=16)
        return [card_obj(x, y, w, h, inner, on)], [], []
    inner = ic(card["ck"], color=acc) + lbl(pl, 50, 16, "f_title", width=w - 64, height=26, long="dot")
    inner += lbl("Tap to play", 14, -12, "f_small", "0x868CA0", align="bottom_left")
    return [card_obj(x, y, w, h, inner, on)], [], []


def c_songlist(card, x, y, w, h, base):
    inner = ic(card["ck"], color=("0x2ED5B8" if card["ck"].startswith("sonos") else "0x1DB954")) + lbl(card.get("name", "Tracks"), 50, 16, "f_small", "0x868CA0")
    songs = ["Midnight City", "Instant Crush", "Dreams", "Redbone", "Holocene", "Lovely Day", "Electric Feel"]
    yy = 52
    for s in songs[: max(1, (h - 52) // 42)]:
        inner += ("              - obj: { x: 14, y: %d, width: %d, height: 38, bg_color: 0x0F1117, "
                  "border_width: 0, radius: 8, pad_all: 0, scrollable: false, widgets: [label: { text: %s, x: 12, "
                  "y: 10, text_font: f_body, text_color: 0xF3F5F8 }] }\n" % (yy, w - 28, esc(s)))
        yy += 42
    return [card_obj(x, y, w, h, inner)], [], []


def c_spot_playlists(card, x, y, w, h, base):
    """Spotify playlist dropdown, bound to sensor.aurora_spotify_playlists (names +
    uris). Reuses aurora.yaml's g_pl_uris (uris string) + g_spot_ctx (selected uri).
    Picking a playlist stores its URI in g_spot_ctx and loads its tracks via the
    aurora_spotify_load_playlist script (which repopulates the songs card). Refresh
    re-pulls the playlists sensor. Compact 2x1/3x1 = micro-label + dropdown row.
    NB: LVGL C calls need the widget's ->obj (id(x) is the ESPHome wrapper)."""
    ddid = base + "_dd"
    compact = h <= 110
    # pick i -> uris[i]: split g_pl_uris on newline (char 10), stash in g_spot_ctx
    ext = ("std::string all = id(g_pl_uris); int idx = lv_dropdown_get_selected(id(" + ddid + ")->obj); "
           "size_t s = 0; for (int k = 0; k < idx; k++) { size_t nl = all.find((char)10, s); "
           "if (nl == std::string::npos) { s = all.size(); break; } s = nl + 1; } "
           "size_t e2 = all.find((char)10, s); std::string uri = all.substr(s, e2 == std::string::npos ? std::string::npos : e2 - s); "
           "id(g_spot_ctx) = uri;")
    dd_y = 40 if compact else 52
    dropdown = ("              - dropdown:\n                  id: " + ddid + "\n                  x: 14\n                  y: " + str(dd_y) + "\n                  width: " + str(w - 28) + "\n                  height: 46\n"
                "                  options: [\"Loading playlists\"]\n"
                "                  bg_color: 0x0F1117\n                  border_color: 0x23262F\n                  border_width: 1\n                  radius: 10\n                  text_color: 0xF3F5F8\n"
                "                  dropdown_list:\n                    bg_color: 0x14161C\n                    border_color: 0x23262F\n                    border_width: 1\n                    radius: 12\n                    text_color: 0xF3F5F8\n"
                "                  on_value:\n"
                "                    - lambda: '" + ext + "'\n"
                "                    - if:\n"
                "                        condition:\n"
                "                          lambda: 'return !id(g_spot_ctx).empty();'\n"
                "                        then:\n"
                "                          - homeassistant.action:\n                              action: script.aurora_spotify_load_playlist\n                              data:\n"
                "                                playlist_uri: !lambda 'return id(g_spot_ctx);'\n")
    inner = lbl("PLAYLIST", 14, 16, "f_micro", "0x868CA0")
    inner += ("              - button:\n                  x: %d\n                  y: 10\n                  width: 40\n                  height: 30\n"
              "                  bg_color: 0x161B24\n                  radius: 10\n                  pad_all: 0\n                  scrollable: false\n"
              "                  widgets: [label: { text: \"\\U000F0450\", align: center, text_font: f_iconsm, text_color: 0x1DB954 }]\n"
              "                  on_click: [homeassistant.action: { action: script.aurora_spotify_refresh_playlists }]\n" % (w - 54))
    inner += dropdown
    if not compact:
        inner += lbl("Pick a playlist to load its songs", 14, dd_y + 56, "f_small", "0x5D6470", width=w - 28, long="dot")
    ts = ["  - platform: homeassistant\n    id: ha_" + base + "_pn\n    entity_id: sensor.aurora_spotify_playlists\n    attribute: names\n    on_value:\n"
          "      - lambda: 'lv_dropdown_set_options(id(" + ddid + ")->obj, x.c_str());'\n",
          "  - platform: homeassistant\n    id: ha_" + base + "_pu\n    entity_id: sensor.aurora_spotify_playlists\n    attribute: uris\n    on_value:\n"
          "      - lambda: 'id(g_pl_uris) = x;'\n"]
    return [card_obj(x, y, w, h, inner)], [], ts


SPOT_MAX_TRACKS = 50


def c_spot_tracks(card, x, y, w, h, base):
    """Spotify song list: a scrolling column of tap-to-play rows bound to
    sensor.aurora_spotify_tracks (names, one "Track — Artist" per line). Tapping
    row i plays position i within the loaded playlist (g_spot_ctx) via the
    aurora_spotify_play_track script. Rows are pre-built (hand-built sng_0..49
    pattern) and shown/hidden by the populate lambda."""
    inner = ic("spotify_tracks", color="0x1DB954")
    inner += lbl("TRACKS \\u00B7 TAP TO PLAY", 50, 18, "f_micro", "0x868CA0")
    rh, gap = 44, 6
    list_y = 48
    list_h = h - list_y - 12
    # scrollable list container (rows overflow -> swipe to scroll)
    inner += ("              - obj:\n                  id: %s_lst\n                  x: 14\n                  y: %d\n                  width: %d\n                  height: %d\n"
              "                  bg_opa: 0\n                  border_width: 0\n                  radius: 0\n                  pad_all: 0\n"
              % (base, list_y, w - 28, list_h))
    inner += "                  widgets:\n"
    for i in range(SPOT_MAX_TRACKS):
        inner += ("                    - button:\n                        id: %s_r%d\n                        x: 0\n                        y: %d\n"
                  "                        width: %d\n                        height: %d\n"
                  "                        bg_color: 0x0F1117\n                        radius: 8\n                        pad_all: 0\n                        scrollable: false\n"
                  "                        hidden: true\n"
                  "                        widgets:\n"
                  "                          - label: { id: %s_l%d, text: \"\", x: 12, align: left_mid, width: %d, long_mode: dot, text_font: f_body, text_color: 0xF3F5F8 }\n"
                  "                        on_click:\n"
                  "                          - homeassistant.action:\n                              action: script.aurora_spotify_play_track\n                              data:\n"
                  "                                context_uri: !lambda 'return id(g_spot_ctx);'\n"
                  "                                position: \"%d\"\n"
                  % (base, i, i * (rh + gap), w - 32, rh, base, i, w - 56, i))
    inner += ("                    - label: { id: %s_empty, text: \"Pick a playlist to load songs\", align: top_mid, y: 12, text_font: f_small, text_color: 0x5D6470 }\n" % base)
    # populate rows from the newline-joined names (hand-built array-split pattern)
    larr = ", ".join("id(%s_l%d)" % (base, i) for i in range(SPOT_MAX_TRACKS))
    rarr = ", ".join("id(%s_r%d)" % (base, i) for i in range(SPOT_MAX_TRACKS))
    ts = ["  - platform: homeassistant\n    id: ha_" + base + "_tn\n    entity_id: sensor.aurora_spotify_tracks\n    attribute: names\n    on_value:\n"
          "      then:\n"
          "        - lambda: |-\n"
          "            const std::string &str = x;\n"
          "            lv_obj_t* L[" + str(SPOT_MAX_TRACKS) + "] = { " + larr + " };\n"
          "            lv_obj_t* R[" + str(SPOT_MAX_TRACKS) + "] = { " + rarr + " };\n"
          "            int idx = 0; size_t st = 0;\n"
          "            for (size_t i = 0; i <= str.size() && idx < " + str(SPOT_MAX_TRACKS) + "; i++) {\n"
          "              if (i == str.size() || str[i] == '\\n') {\n"
          "                std::string tok = str.substr(st, i - st);\n"
          "                if (!tok.empty()) {\n"
          "                  lv_label_set_text(L[idx], tok.c_str());\n"
          "                  lv_obj_clear_flag(R[idx], LV_OBJ_FLAG_HIDDEN);\n"
          "                  idx++;\n"
          "                }\n"
          "                st = i + 1;\n"
          "              }\n"
          "            }\n"
          "            for (int j = idx; j < " + str(SPOT_MAX_TRACKS) + "; j++) lv_obj_add_flag(R[j], LV_OBJ_FLAG_HIDDEN);\n"
          "            if (idx > 0) lv_obj_add_flag(id(" + base + "_empty), LV_OBJ_FLAG_HIDDEN);\n"
          "            else lv_obj_clear_flag(id(" + base + "_empty), LV_OBJ_FLAG_HIDDEN);\n"]
    return [card_obj(x, y, w, h, inner)], [], ts


def c_shortcuts(card, x, y, w, h, pagemap, base):
    """Grid of icon+label tiles (one per grid cell), matching the builder's
    .scbtn: MDI icon on top, label below. Empty slots show a + outline."""
    inner = ""
    sc = card.get("shortcuts", [])
    cols, rows = card["w"], card["h"]
    n = cols * rows
    pad, gap = 12, 8
    bw = (w - pad * 2 - (cols - 1) * gap) // cols
    bh = (h - pad * 2 - (rows - 1) * gap) // rows
    for i in range(n):
        s = sc[i] if i < len(sc) else None
        cx = pad + (i % cols) * (bw + gap)
        cy = pad + (i // cols) * (bh + gap)
        if s:
            glyph = NAV_GLYPH.get(s.get("icon", ""), FALLBACK_GLYPH)
            tgt = s.get("target", "")
            act = "lvgl.page.show: page_home"
            if tgt.startswith("page:"):
                pid = pagemap.get(tgt[5:])
                if pid:
                    act = "lvgl.page.show: %s" % pid
            elif tgt.startswith("special:"):
                act = "lvgl.page.show: page_%s" % tgt.split(":")[1]
            inner += (
                "              - button:\n"
                "                  x: %d\n                  y: %d\n                  width: %d\n                  height: %d\n"
                "                  bg_color: 0x161B24\n                  radius: 14\n                  pad_all: 0\n                  scrollable: false\n"
                "                  widgets:\n"
                "                    - label: { text: \"%s\", align: center, y: -13, text_font: f_icon, text_color: 0x2ED5B8 }\n"
                "                    - label: { text: %s, align: center, y: 17, width: %d, text_align: center, text_font: f_body, text_color: 0xF3F5F8 }\n"
                "                  on_click: [%s]\n"
                % (cx, cy, bw, bh, glyph, esc(s.get("label", "Open")), bw - 10, act))
        else:
            inner += (
                "              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x0F1117, "
                "border_color: 0x2A2E38, border_width: 1, radius: 14, pad_all: 0, scrollable: false, "
                "widgets: [label: { text: \"\\U000F0415\", align: center, text_font: f_icon, text_color: 0x4A5160 }] }\n"
                % (cx, cy, bw, bh))
    return [card_obj(x, y, w, h, inner)], [], []


def c_volume(card, x, y, w, h, base):
    """Media volume: a volume slider + mute button (media_player.volume_set/mute)."""
    e = card.get("entity", "")
    sld = base + "_vol"
    inner = ic(card["ck"], color="0x2ED5B8")
    inner += title(card.get("name", "Volume"), w)
    mute = ha("media_player.volume_mute", e, 'is_volume_muted: "true"') if e else "lvgl.page.show: page_home"
    if card["h"] == 1:                                    # compact 2x1: slider + mute side by side
        sy = h - 32
        if e:
            inner += vol_slider_yaml(sld, 14, sy, w - 70, 40, e)
        inner += btn(w - 52, sy - 10, 40, 34, "\\U000F075F", mute, font="f_icon", bg="0x161B24")
    else:
        if e:
            inner += vol_slider_yaml(sld, 14, h // 2 - 4, w - 28, 40, e)
        inner += btn((w - 120) // 2, h - 58, 120, 44, "\\U000F075F", mute, font="f_icon")
    s = []
    if e:
        s.append("  - platform: homeassistant\n    id: ha_%s_v\n    entity_id: %s\n    attribute: volume_level\n    on_value:\n"
                 "      - lvgl.slider.update: { id: %s, value: !lambda 'return (int)(x * 100);' }\n"
                 % (base, e, sld))
    return [card_obj(x, y, w, h, inner)], s, []


def c_volumes(card, x, y, w, h, base):
    """Per-speaker volumes: a labeled volume slider for each entity, set separately."""
    ents = card.get("entities", []) or [""]
    inner = ic(card["ck"], color="0x2ED5B8")
    inner += lbl(card.get("name", "Speaker Volumes"), 50, 16, "f_small", "0x868CA0")
    top, gap = 52, 12
    n = max(1, len(ents))
    rh = (h - top - 14 - (n - 1) * gap) // n
    s = []
    for i, e in enumerate(ents):
        nm = (e.split(".")[-1] if "." in e else e).replace("_", " ") or ("Speaker %d" % (i + 1))
        ry = top + i * (rh + gap)
        sld = base + "_v%d" % i
        inner += lbl(nm, 14, ry, "f_body", "0xEEF0F6", width=w - 28, long="dot", height=20)
        if e:
            inner += vol_slider_yaml(sld, 14, ry + 28, w - 28, 40, e)
            s.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: volume_level\n    on_value:\n"
                     "      - lvgl.slider.update: { id: %s, value: !lambda 'return (int)(x * 100);' }\n" % (sld, e, sld))
    return [card_obj(x, y, w, h, inner)], s, []


def _vset(e, sld):
    """on_release block that pushes a slider's 0-100 value to media_player.volume_set."""
    if not e:
        return ""
    return ("\n                  on_release:\n                    - homeassistant.action:\n"
            "                        action: media_player.volume_set\n"
            "                        data: { entity_id: %s, volume_level: !lambda 'char b[8]; snprintf(b, sizeof(b), \"%%.2f\", lv_slider_get_value(id(%s)) / 100.0); return std::string(b);' }" % (e, sld))


def _join(master, member):
    # ESPHome homeassistant.action data values must be strings (no YAML lists); HA coerces a
    # single entity_id string into the group_members list. Grouping stays best-effort.
    return ("homeassistant.action: { action: media_player.join, data: { entity_id: %s, group_members: %s } }"
            % (master, member)) if (master and member) else "lvgl.page.show: page_home"


def _unjoin(e):
    return ("homeassistant.action: { action: media_player.unjoin, data: { entity_id: %s } }" % e) if e else "lvgl.page.show: page_home"


def vol_slider_yaml(sld, sx, sy, sw, value, e):
    """A teal-accented volume slider (matches the web volume bars + seek bar) that
    pushes to media_player.volume_set. Teal indicator/knob keeps one accent per surface."""
    return ("              - slider:\n                  id: %s\n                  x: %d\n                  y: %d\n                  width: %d\n"
            "                  bg_color: 0x23262F\n                  bg_opa: 100%%\n"
            "                  min_value: 0\n                  max_value: 100\n                  value: %d\n"
            "                  indicator:\n                    bg_color: 0x2ED5B8\n"
            "                  knob:\n                    bg_color: 0x2ED5B8%s\n"
            % (sld, sx, sy, sw, value, _vset(e, sld)))


DEFAULT_SPKS = ["media_player.living_room", "media_player.kitchen", "media_player.office", "media_player.patio"]
SPK_DEMO_VOL = [42, 28, 55, 35, 60, 22, 48, 30]


def c_speakers(card, x, y, w, h, base):
    """Multi-room speakers: per-speaker volume + Join/Leave grouping (images 2 & 3).
    Demo groups the first half of the entities (first = SOURCE); the rest are joinable."""
    ents = card.get("entities", []) or DEFAULT_SPKS
    n = len(ents)
    gcount = max(1, (n + 1) // 2)
    master = ents[0]
    sg = "\\U000F04C3"

    def nm(i):
        e = ents[i]
        return (e.split(".")[-1] if "." in e else e).replace("_", " ") if e else ("Speaker %d" % (i + 1))

    ts = []
    ss = []

    def sensor(i, sld):
        if ents[i]:
            ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: volume_level\n    on_value:\n"
                      "      - lvgl.slider.update: { id: %s, value: !lambda 'return (int)(atof(x.c_str()) * 100);' }\n" % (sld, ents[i], sld))

    # ---- 1x1 compact status tile ----
    if card["w"] == 1 and card["h"] == 1:
        inner = lbl(sg, 14, 14, "f_icon", "0x2ED5B8")
        inner += lbl(str(SPK_DEMO_VOL[0]), -14, 16, "f_mono", "0x2ED5B8", align="top_right")
        inner += lbl(nm(0), 14, -32, "f_body", "0xF3F5F8", align="bottom_left", width=w - 28, long="dot", height=20)
        inner += lbl("LINKED", 14, -12, "f_micro", "0x868CA0", align="bottom_left")
        return [card_obj(x, y, w, h, inner)], [], []

    # ---- h==1 compact row: one speaker + inline volume ----
    if card["h"] == 1:
        cy = h // 2
        sld = base + "_v0"
        v = SPK_DEMO_VOL[0]
        inner = lbl(sg, 14, cy - 12, "f_icon", "0x2ED5B8")
        inner += lbl(nm(0), 46, 20, "f_body", "0xF3F5F8", width=150, long="dot", height=20)
        inner += lbl("LINKED", 46, 48, "f_micro", "0x868CA0")
        vx = min(220, w // 2)
        inner += lbl("\\U000F057E", vx, cy - 6, "f_icon", "0x868CA0")
        inner += vol_slider_yaml(sld, vx + 30, cy - 4, w - (vx + 30) - 52, v, ents[0])
        inner += lbl(str(v), -14, cy - 10, "f_mono", "0x2ED5B8", align="top_right")
        sensor(0, sld)
        return [card_obj(x, y, w, h, inner)], [], ts

    # ---- link list (design: ACTIVE·GROUPED teal rows + AVAILABLE '+' rows) ----
    # One live-styled row per speaker; state from the entity's group_members attr.
    # Tap a row: grouped -> unjoin (leave); solo -> join to the card's master zone.
    master = card.get("entity") or ents[0]
    inner = lbl(sg, 14, 14, "f_icon", "0x2ED5B8")
    inner += lbl("SPEAKERS \\u00B7 TAP TO LINK OR LEAVE", 50, 20, "f_micro", "0x868CA0")
    list_y = 48
    gap = 8
    rh = max(56, min(78, (h - list_y - 14 - (n - 1) * gap) // max(1, n)))
    scroll_h = h - list_y - 12
    # scrollable when the rows overflow the card
    inner += ("              - obj:\n                  x: 14\n                  y: %d\n                  width: %d\n                  height: %d\n"
              "                  bg_opa: 0\n                  border_width: 0\n                  radius: 0\n                  pad_all: 0\n"
              "                  widgets:\n" % (list_y, w - 28, scroll_h))
    rw = w - 32
    for i, e in enumerate(ents):
        rowid, plusid, lnkid, sldv = "%s_rw%d" % (base, i), "%s_pl%d" % (base, i), "%s_lk%d" % (base, i), "%s_v%d" % (base, i)
        icid, nmid = "%s_ic%d" % (base, i), "%s_nm%d" % (base, i)
        ry = i * (rh + gap)
        tog = ("                        on_click:\n"
               "                          - if:\n"
               "                              condition:\n"
               "                                lambda: 'return id(g_spk_grouped).count(\"%s\") && id(g_spk_grouped)[\"%s\"];'\n"
               "                              then:\n"
               "                                - homeassistant.action: { action: media_player.unjoin, data: { entity_id: %s } }\n"
               "                              else:\n"
               "                                - homeassistant.action: { action: media_player.join, data: { entity_id: %s, group_members: %s } }\n"
               % (e, e, e, master, e)) if e else ""
        inner += ("                    - obj:\n                        id: %s\n                        x: 0\n                        y: %d\n                        width: %d\n                        height: %d\n"
                  "                        bg_color: 0x14161C\n                        border_color: 0x23262F\n                        border_width: 1\n                        radius: 12\n"
                  "                        pad_all: 0\n                        clickable: true\n                        scrollable: false\n%s"
                  "                        widgets:\n"
                  "                          - label: { id: %s, text: \"%s\", x: 14, y: 10, text_font: f_iconsm, text_color: 0x5D6470 }\n"
                  "                          - label: { id: %s, text: %s, x: 42, y: 9, width: %d, long_mode: dot, text_font: f_body, text_color: 0xC2C7D2 }\n"
                  "                          - label: { id: %s, text: \"\\U000F0415\", align: top_right, x: -14, y: 8, text_font: f_icon, text_color: 0x2ED5B8 }\n"
                  "                          - label: { id: %s, text: \"LINKED\", align: top_right, x: -14, y: 12, text_font: f_micro, text_color: 0x2ED5B8, hidden: true }\n"
                  % (rowid, ry, rw, rh, tog, icid, sg, nmid, esc(nm(i)), rw - 150, plusid, lnkid))
        if e and rh >= 56:
            inner += ("                          - slider:\n                              id: %s\n                              x: 42\n                              y: %d\n                              width: %d\n                              height: 8\n"
                      "                              bg_color: 0x23262F\n                              bg_opa: 100%%\n"
                      "                              min_value: 0\n                              max_value: 100\n                              value: 30\n"
                      "                              indicator:\n                                bg_color: 0x2ED5B8\n"
                      "                              knob:\n                                bg_color: 0x2ED5B8\n"
                      "                              on_release:\n                                - homeassistant.action:\n"
                      "                                    action: media_player.volume_set\n"
                      "                                    data: { entity_id: %s, volume_level: !lambda 'char b[8]; snprintf(b, sizeof(b), \"%%.2f\", lv_slider_get_value(id(%s)) / 100.0); return std::string(b);' }\n"
                      % (sldv, rh - 22, rw - 120, e, sldv))
        if e:
            # live volume (numeric sensor)
            ss.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: volume_level\n    on_value:\n"
                      "      - lvgl.slider.update: { id: %s, value: !lambda 'return (int)(x * 100);' }\n" % (sldv, e, sldv))
            # live group state (group_members list attr as text) -> restyle row in place
            ts.append("  - platform: homeassistant\n    id: ha_%s_g%d\n    entity_id: %s\n    attribute: group_members\n    on_value:\n"
                      "      then:\n"
                      "        - lambda: |-\n"
                      "            bool g = x.find(',') != std::string::npos;\n"
                      "            id(g_spk_grouped)[\"%s\"] = g;\n"
                      "            lv_obj_set_style_bg_color(id(%s), lv_color_hex(g ? 0x101D1C : 0x14161C), 0);\n"
                      "            lv_obj_set_style_border_color(id(%s), lv_color_hex(g ? 0x2A5048 : 0x23262F), 0);\n"
                      "            lv_obj_set_style_text_color(id(%s), lv_color_hex(g ? 0x2ED5B8 : 0x5D6470), 0);\n"
                      "            lv_obj_set_style_text_color(id(%s), lv_color_hex(g ? 0xF3F5F8 : 0xC2C7D2), 0);\n"
                      "            if (g) { lv_obj_add_flag(id(%s), LV_OBJ_FLAG_HIDDEN); lv_obj_clear_flag(id(%s), LV_OBJ_FLAG_HIDDEN); }\n"
                      "            else { lv_obj_clear_flag(id(%s), LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(id(%s), LV_OBJ_FLAG_HIDDEN); }\n"
                      % (base, i, e, e, rowid, rowid, icid, nmid, plusid, lnkid, plusid, lnkid))
    return [card_obj(x, y, w, h, inner)], ss, ts


def c_generic(card, x, y, w, h, base):
    inner = ic(card.get("ck", ""), color="0x868CA0")
    inner += lbl(card.get("name", card.get("ck", "Card")), 0, 8, "f_body", "0x868CA0", align="center")
    return [card_obj(x, y, w, h, inner)], [], []


CTRL = {
    "switch": c_toggle, "light_t": c_toggle, "light": c_light, "sensor": c_sensor,
    "binary": c_sensor, "person": c_sensor, "vacuum": c_sensor, "alarm": c_sensor,
    "climate": c_climate, "scene": c_action, "script": c_action, "media": c_media,
    "spotify": c_media, "sonos": c_media, "fan": c_fan, "cover": c_cover,
    "lock": c_lock, "weather": c_weather, "camera": c_camera, "group": c_group,
    "lightgroup": c_group, "outletgroup": c_outlet, "speakers": c_speakers,
    "sonos_sources": c_btngrid, "tv_sources": c_btngrid,
    "tv_dpad": c_tv_dpad, "tv_transport": c_tv_transport, "tv_channel": c_tv_channel,
    "tv_volume": c_tv_volume, "tv_trackpad": c_tv_trackpad, "tvremote": c_tvremote,
    "playlist": c_playlist, "sonos_fav": c_playlist, "songlist": c_songlist,
    "sonos_library": c_songlist, "volume": c_volume, "volumes": c_volumes,
    "spotify_playlists": c_spot_playlists, "spotify_tracks": c_spot_tracks,
}


def emit_card(card, header, pagemap):
    x, y, w, h = rect(card, header)
    base = "g_" + slug(card.get("id", "c"))
    ck = card.get("ck", "")
    if ck == "shortcuts":
        return c_shortcuts(card, x, y, w, h, pagemap, base)
    fn = CTRL.get(ck, c_generic)
    return fn(card, x, y, w, h, base)


def gen_nav(layout, pagemap):
    out = ""
    nav = layout.get("nav", [])[:7]
    for i, n in enumerate(nav):
        g = NAV_GLYPH.get(n.get("icon", ""), FALLBACK_GLYPH)
        pid = pagemap.get(n.get("page", ""), "page_home")
        nid = slug(n.get("id", str(i)))
        # all default inactive; the per-page on_load highlights the current one
        out += (
            "            - button:\n                id: nav_%s\n                align: top_mid\n                y: %d\n"
            "                width: 58\n                height: 58\n                radius: 14\n                bg_color: 0x2ED5B8\n                bg_opa: 0%%\n"
            "                widgets: [label: { id: nav_%s_i, text: \"%s\", align: center, text_font: f_icon, text_color: 0x5D6470 }]\n"
            "                on_click: [lvgl.page.show: %s]\n"
            % (nid, 14 + i * 68, nid, g, pid))
    # Settings (always present) -> settings page
    out += (
        "            - button:\n                id: nav_settings\n                align: bottom_mid\n                y: -14\n"
        "                width: 58\n                height: 58\n                radius: 14\n                bg_color: 0x2ED5B8\n                bg_opa: 0%\n"
        "                widgets: [label: { id: nav_settings_i, text: \"\\U000F0493\", align: center, text_font: f_icon, text_color: 0x5D6470 }]\n"
        "                on_click: [lvgl.page.show: page_settings]\n")
    return out


# top-bar status chips (glyph present in f_icon, demo value, color)
HCHIP = {
    "user": ("\\U000F02DC", "Ben", "0x868CA0"),
    "time": ("", "10:42 PM", "0x868CA0"), "date": ("", "Sun Jun 29", "0x868CA0"),
    "weather_current": ("\\U000F0599", "72\\u00B0", "0xF2B84B"),
    "weather_today": ("\\U000F0599", "H78 L61", "0xF2B84B"),
    "weather_tomorrow": ("\\U000F0595", "Tmrw 74\\u00B0", "0xF2B84B"),
    "secured": ("\\U000F068A", "Secured", "0x2ED5B8"),
    "networking": ("\\U000F0928", "Online", "0x2ED5B8"),
    "wifi": ("\\U000F0928", "Strong", "0x2ED5B8"),
    "ethernet": ("\\U000F0928", "Wired", "0x2ED5B8"),
    "sensor": ("\\U000F050F", "72\\u00B0", "0xF2685A"),
    "lights_on": ("\\U000F0335", "3 on", "0xF2B84B"),
    "fans_on": ("\\U000F0210", "2 on", "0x2ED5B8"),
}


def gen_header(key, page, layout, pid):
    hdr = page.get("header") or {}
    left = hdr.get("left", "greeting")
    first = layout.get("nav", [{}])[0].get("page")
    greet = "Good evening, Ben" if key == first else page.get("title", "Aurora")
    room = layout.get("install_room") or "Home"          # matches the builder (blank -> Home)
    tid, did = "hct_" + pid, "hcd_" + pid                 # live clock/date label ids (per page)
    out, clocks = "", []
    if left == "time":
        out += "        - label: { id: %s, text: \"10:42 PM\", x: 96, y: 12, text_font: f_display, text_color: 0xF3F5F8 }\n" % tid
        clocks.append((tid, "time"))
    elif left == "date":
        out += "        - label: { id: %s, text: \"Sunday\", x: 96, y: 10, text_font: f_head, text_color: 0xF3F5F8 }\n" % did
        out += "        - label: { id: %s, text: \"June 29\", x: 96, y: 50, text_font: f_body, text_color: 0x868CA0 }\n" % (did + "b")
        clocks += [(did, "dow"), (did + "b", "date_long")]
    elif left == "time_date":
        out += "        - label: { id: %s, text: \"10:42 PM\", x: 96, y: 10, text_font: f_head, text_color: 0xF3F5F8 }\n" % tid
        out += "        - label: { id: %s, text: \"Sunday, June 29\", x: 96, y: 50, text_font: f_body, text_color: 0x868CA0 }\n" % did
        clocks += [(tid, "time"), (did, "date_full")]
    else:                                                # greeting (+ time, + date unless greeting_nd)
        out += "        - label: { text: %s, x: 96, y: 14, text_font: f_h1, text_color: 0xF3F5F8 }\n" % esc(greet)
        # sub-line: teal room icon + configured room + live time (+ date). Flex row so it
        # lays out regardless of room-name length; no room-switcher chevron.
        sub = ("        - obj:\n            x: 94\n            y: 50\n            width: SIZE_CONTENT\n            height: SIZE_CONTENT\n"
               "            bg_opa: 0\n            border_width: 0\n            pad_all: 0\n            scrollable: false\n"
               "            layout: { type: flex, flex_flow: ROW, flex_align_cross: center, pad_column: 8 }\n"
               "            widgets:\n"
               "              - label: { text: \"\\U000F04B9\", text_font: f_iconsm, text_color: 0x2ED5B8 }\n"
               "              - label: { text: %s, text_font: f_body, text_color: 0xC2C7D2 }\n"
               "              - label: { text: \"\\u00B7\", text_font: f_body, text_color: 0x868CA0 }\n"
               "              - label: { id: %s, text: \"10:42 PM\", text_font: f_mono, text_color: 0x868CA0 }\n"
               % (esc(room), tid))
        clocks.append((tid, "time"))
        if left != "greeting_nd":
            sub += ("              - label: { text: \"\\u00B7\", text_font: f_body, text_color: 0x868CA0 }\n"
                    "              - label: { id: %s, text: \"Sun Jun 29\", text_font: f_mono, text_color: 0x868CA0 }\n" % did)
            clocks.append((did, "date"))
        out += sub
    # status chips as bordered pills (design: #14161C bg, #23262F border, radius 12)
    for i, item in enumerate((hdr.get("right") or [])[:4]):
        g, t, col = HCHIP.get(item, ("", item, "0x868CA0"))
        base_x = -(20 + i * 126)
        icon_w = ("                    - label: { text: \"%s\", text_font: f_iconsm, text_color: %s }\n" % (g, col)) if g else ""
        out += (
            "        - obj:\n"
            "            align: top_right\n            x: %d\n            y: 18\n            width: 118\n            height: 40\n"
            "            bg_color: 0x14161C\n            border_color: 0x23262F\n            border_width: 1\n            radius: 12\n"
            "            pad_all: 0\n            scrollable: false\n"
            "            widgets:\n"
            "              - obj:\n"
            "                  align: center\n                  width: SIZE_CONTENT\n                  height: SIZE_CONTENT\n"
            "                  bg_opa: 0\n                  border_width: 0\n                  pad_all: 0\n                  scrollable: false\n"
            "                  layout: { type: flex, flex_flow: ROW, flex_align_cross: center, pad_column: 8 }\n"
            "                  widgets:\n%s"
            "                    - label: { text: %s, text_font: f_body, text_color: 0xF3F5F8 }\n"
            % (base_x, icon_w, esc(t)))
    return out, clocks


def _nav_onload(layout, active):
    navids = [slug(n.get("id", "")) for n in layout.get("nav", [])] + ["settings"]
    return "      on_load:\n" + "".join(
        ("        - lvgl.widget.update: { id: nav_%s, bg_opa: %s }\n"
         "        - lvgl.label.update: { id: nav_%s_i, text_color: %s }\n")
        % (nid, ("100%" if nid == active else "0%"), nid, ("0x06231D" if nid == active else "0x5D6470"))
        for nid in navids)


def gen_settings_page(layout):
    """Settings: brightness, screen timeout, motion wake + screensaver, restart."""
    onload = _nav_onload(layout, "settings")
    onload += ("        - lambda: |-\n"
               "            if (id(g_wake_presence)) lv_obj_add_state(id(set_motion), LV_STATE_CHECKED);\n"
               "            if (id(g_screensaver)) lv_obj_add_state(id(set_saver), LV_STATE_CHECKED);\n"
               "            if (id(g_cam_wake)) lv_obj_add_state(id(set_cwake), LV_STATE_CHECKED);\n")
    w = "        - image: { src: img_aurora_bg, x: 0, y: 0 }\n"
    w += "        - label: { text: \"Settings\", x: 94, y: 18, text_font: f_h1, text_color: 0xF3F5F8 }\n"
    w += "        - label: { text: \"AURORA PANEL \\u00B7 10.0.0.174\", x: 94, y: 58, text_font: f_micro, text_color: 0x868CA0 }\n"
    # --- Brightness card ---
    bri = lbl("DISPLAY", 20, 16, "f_micro", "0x868CA0")
    bri += lbl("Brightness", 20, 34, "f_title", "0xF3F5F8", height=26)
    bri += ("              - slider:\n                  id: set_bri\n                  x: 20\n                  y: 78\n                  width: 408\n"
            "                  bg_color: 0x23262F\n                  min_value: 5\n                  max_value: 100\n                  value: 80\n"
            "                  indicator:\n                    bg_color: 0x2ED5B8\n                  knob:\n                    bg_color: 0x2ED5B8\n"
            "                  on_release:\n                    - light.turn_on: { id: display_backlight, brightness: !lambda 'return lv_slider_get_value(id(set_bri)) / 100.0f;' }\n")
    w += card_obj(94, 96, 448, 116, bri)
    # --- Screen timeout card ---
    to = lbl("SCREEN TIMEOUT", 20, 16, "f_micro", "0x868CA0")
    opts = [("Never", 0), ("1 min", 60000), ("5 min", 300000), ("15 min", 900000)]
    tbw = (448 - 40 - 3 * 8) // 4
    for i, (lab, ms) in enumerate(opts):
        sel = (ms == 300000)
        to += ("              - button:\n                  id: set_to%d\n                  x: %d\n                  y: 54\n                  width: %d\n                  height: 46\n"
               "                  bg_color: %s\n                  radius: 10\n                  pad_all: 0\n                  scrollable: false\n"
               "                  widgets: [label: { text: \"%s\", align: center, text_font: f_small, text_color: %s }]\n"
               "                  on_click:\n                    - lambda: 'id(g_timeout_ms) = %d;'\n"
               % (i, 20 + i * (tbw + 8), tbw, ("0x2ED5B8" if sel else "0x0F1117"), lab, ("0x06231D" if sel else "0xC2C7D2"), ms))
    w += card_obj(94, 224, 448, 116, to)
    # --- Behavior card: motion wake + screensaver ---
    beh = lbl("BEHAVIOR", 20, 16, "f_micro", "0x868CA0")
    beh += lbl("Motion wake", 20, 50, "f_body", "0xF3F5F8", height=22)
    beh += ("              - switch:\n                  id: set_motion\n                  align: top_right\n                  x: -20\n                  y: 46\n"
            "                  on_value:\n                    - lambda: 'id(g_wake_presence) = x;'\n")
    beh += lbl("Screensaver", 20, 104, "f_body", "0xF3F5F8", height=22)
    beh += ("              - switch:\n                  id: set_saver\n                  align: top_right\n                  x: -20\n                  y: 100\n"
            "                  on_value:\n                    - lambda: 'id(g_screensaver) = x;'\n")
    beh += lbl("Approach wake (camera)", 20, 158, "f_body", "0xF3F5F8", height=22)
    beh += ("              - switch:\n                  id: set_cwake\n                  align: top_right\n                  x: -20\n                  y: 154\n"
            "                  on_value:\n                    - lambda: 'id(g_cam_wake) = x;'\n")
    w += card_obj(560, 96, 370, 206, beh)
    # --- Restart ---
    w += ("        - button:\n            x: 560\n            y: 318\n            width: 370\n            height: 56\n"
          "            bg_color: 0x2a1414\n            radius: 14\n            scrollable: false\n"
          "            widgets: [label: { text: \"Restart Panel\", align: center, text_font: f_body, text_color: 0xF2685A }]\n"
          "            on_click: [button.press: btn_restart_panel]\n")
    w += "        - label: { text: \"Guition JC1060P470 \\u00B7 web UI on :80\", x: 94, y: 356, text_font: f_small, text_color: 0x5D6470 }\n"
    return "    - id: page_settings\n      bg_color: 0x0A0B0F\n      scrollable: false\n%s      widgets:\n%s" % (onload, w)


def gen_trackpad_page(layout, back_pid, active):
    """Dedicated LG trackpad page — a verbatim clone of the PROVEN hand-built
    page_trackpad (aurora.yaml): a full page whose pad/strip sit at exactly the
    gesture engine's default zones (move pad 96,120..856,470; scroll strip
    872,120..1004,470), entered via the remote's Pad button. on_load re-asserts
    the canonical zone globals + engages the engine (g_tp_active); on_unload
    disengages. Surfaces are clickable:false, same as the hand-built page —
    the raw touchscreen handlers see every touch regardless."""
    onload = _nav_onload(layout, active)
    onload += ("        - lambda: |-\n"
               "            id(g_tp_px1) = 96;  id(g_tp_py1) = 120; id(g_tp_px2) = 856;  id(g_tp_py2) = 470;\n"
               "            id(g_tp_sx1) = 872; id(g_tp_sy1) = 120; id(g_tp_sx2) = 1004; id(g_tp_sy2) = 470;\n"
               "            id(g_tp_active) = true;\n")
    w = "        - image: { src: img_aurora_bg, x: 0, y: 0 }\n"
    w += "        - label: { text: \"Trackpad\", x: 96, y: 26, text_font: f_title, text_color: 0xF3F5F8 }\n"
    w += "        - label: { text: \"LG Magic pointer \\u00B7 drag to move, tap to click\", x: 96, y: 60, text_font: f_small, text_color: 0x868CA0 }\n"
    w += ("        - button:\n            align: top_right\n            x: -12\n            y: 12\n            width: 132\n            height: 48\n"
          "            bg_color: 0x161B24\n            border_color: 0x2ED5B8\n            border_width: 2\n            radius: 12\n"
          "            pad_all: 0\n            scrollable: false\n"
          "            widgets: [label: { text: \"Buttons\", align: center, text_font: f_body, text_color: 0x2ED5B8 }]\n"
          "            on_click: [lvgl.page.show: %s]\n" % back_pid)
    w += ("        - obj:\n            x: 96\n            y: 120\n            width: 760\n            height: 350\n"
          "            bg_color: 0x10121A\n            bg_opa: 60%\n            radius: 22\n            border_width: 1\n            border_color: 0x2A5048\n"
          "            pad_all: 0\n            scrollable: false\n            clickable: false\n"
          "            widgets:\n"
          "              - label: { text: \"\\U000F0297\", align: center, y: -34, text_font: f_bigicon, text_color: 0x2ED5B8 }\n"
          "              - label: { text: \"Drag to move  \\u00B7  tap to click\", align: center, y: 40, text_font: f_body, text_color: 0x868CA0 }\n")
    w += ("        - obj:\n            x: 872\n            y: 120\n            width: 132\n            height: 350\n"
          "            bg_color: 0x10121A\n            bg_opa: 60%\n            radius: 22\n            border_width: 1\n            border_color: 0x2A5048\n"
          "            pad_all: 0\n            scrollable: false\n            clickable: false\n"
          "            widgets:\n"
          "              - label: { text: \"\\U000F0143\", align: top_mid, y: 20, text_font: f_bigicon, text_color: 0x4FA8F5 }\n"
          "              - label: { text: \"SCROLL\", align: center, text_font: f_micro, text_color: 0x868CA0 }\n"
          "              - label: { text: \"\\U000F0140\", align: bottom_mid, y: -20, text_font: f_bigicon, text_color: 0x4FA8F5 }\n")
    return ("    - id: page_trackpad\n      bg_color: 0x0A0B0F\n      scrollable: false\n%s"
            "      on_unload:\n        - lambda: 'id(g_tp_active) = false;'\n"
            "      widgets:\n%s" % (onload, w))


def gen_pages(layout, pagemap):
    pages_yaml, sens, txt, clocks = "", [], [], []
    tp_page = None                                        # (back_pid, active nav) of the tvremote page
    for key, page in layout.get("pages", {}).items():
        hdr = page.get("header") or {}
        header_on = bool(hdr.get("on"))
        subs = page.get("subpages", [[]])
        for si, cards in enumerate(subs):
            pid = pagemap[key] if si == 0 else "%s_%d" % (pagemap[key], si)
            widgets = "        - image: { src: img_aurora_bg, x: 0, y: 0 }\n"
            if header_on:
                hdr_yaml, clk = gen_header(key, page, layout, pid)
                widgets += hdr_yaml
                clocks += clk
            has_tv = any(c.get("ck") == "tvremote" for c in cards)
            for card in cards:
                ws, ss, ts = emit_card(card, header_on, pagemap)
                widgets += "".join(ws)
                sens += ss
                txt += ts
            # Next affordance if a following sub-page exists
            if si < len(subs) - 1:
                nxt = "%s_%d" % (pagemap[key], si + 1)
                widgets += btn(884, 540, 110, 44, "Next", "lvgl.page.show: %s" % nxt, font="f_body")
            active = next((slug(n.get("id", "")) for n in layout.get("nav", []) if n.get("page") == key), None)
            onload = _nav_onload(layout, active)
            if has_tv and tp_page is None:                # remember where the remote lives (Pad links here back)
                tp_page = (pid, active)
            pages_yaml += (
                "    - id: %s\n      bg_color: 0x0A0B0F\n      scrollable: false\n%s      widgets:\n%s" % (pid, onload, widgets))
    if tp_page is not None:                               # dedicated trackpad page (hand-built clone)
        pages_yaml += gen_trackpad_page(layout, tp_page[0], tp_page[1])
    pages_yaml += gen_settings_page(layout)
    return pages_yaml, sens, txt, clocks


def build_lvgl(layout):
    pagemap = {key: "page_" + slug(key) for key in layout.get("pages", {})}
    nav = gen_nav(layout, pagemap)
    pages, sens, txt, clocks = gen_pages(layout, pagemap)
    return nav, pages, sens, txt, pagemap, clocks


# ---- base extraction: keep hardware/font/style sections, drop UI bindings ----
KEEP = ["substitutions", "esphome", "esp32", "psram", "esp_ldo", "esp32_hosted",
        "wifi", "api", "ota", "safe_mode", "logger", "web_server", "output", "light",
        "external_components", "i2c", "touchscreen", "display", "http_request",
        "image", "font", "globals", "number", "button", "time",
        "ov02c10_support", "esp_video_camera"]   # onboard camera (HA entity + RTSP :8554)


def scrub_lvgl_actions(text):
    """Remove lvgl.* actions (and their nested params) from the kept base —
    they reference the old UI widgets that the generated pages replace. Then
    drop any on_<event>: automation left with no remaining actions."""
    lines = text.splitlines(keepends=True)
    out, i = [], 0
    while i < len(lines):
        m = re.match(r"^(\s*)-\s*lvgl\.", lines[i])
        if m:
            indent = len(m.group(1))
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() == "" or (len(nxt) - len(nxt.lstrip())) > indent:
                    i += 1
                else:
                    break
            continue
        out.append(lines[i])
        i += 1
    # drop now-empty on_<event>: keys (next real line at <= indent)
    lines, out, i = out, [], 0
    while i < len(lines):
        m = re.match(r"^(\s*)on_[a-z_]+:\s*$", lines[i])
        if m:
            indent = len(m.group(1))
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j >= len(lines) or (len(lines[j]) - len(lines[j].lstrip())) <= indent:
                i += 1   # empty automation — skip the on_* line
                continue
        out.append(lines[i])
        i += 1
    return "".join(out)


def split_sections(text):
    """Split a top-level ESPHome YAML into {name: text} preserving order."""
    secs, cur, name = [], [], None
    for line in text.splitlines(keepends=True):
        m = re.match(r"^([a-z][a-z0-9_]*):", line)
        if m:
            if name is not None:
                secs.append((name, "".join(cur)))
            name, cur = m.group(1), [line]
        else:
            cur.append(line)
    if name is not None:
        secs.append((name, "".join(cur)))
    return secs


def style_defs(lvgl_text):
    """Pull the style_definitions block out of the original lvgl: section.
    Stops at the first 2-space-indented line (next key or a comment)."""
    m = re.search(r"\n  style_definitions:\n(.*?)(?=\n  \S)", lvgl_text, re.S)
    if not m:
        return ""
    block = "  style_definitions:\n" + m.group(1)
    return block if block.endswith("\n") else block + "\n"


# LG Magic-remote pointer bridge. aurora.yaml carries this flush interval, but the
# generator drops all `interval:` blocks (they reference dropped ids), so re-inject
# the standalone g_tp_* -> pyscript.lg_pointer_* flush the TV trackpad depends on.
# References only globals that survive into the generated build (g_tp_*) + HA services.
TP_FLUSH_INTERVAL = (
    "\ninterval:\n"
    "  - interval: 50ms\n"
    "    then:\n"
    "      - if:\n"
    "          condition:\n"
    "            lambda: 'return id(g_tp_active) && (id(g_tp_dx) != 0 || id(g_tp_dy) != 0);'\n"
    "          then:\n"
    "            - lambda: 'ESP_LOGI(\"tp\", \"flush move dx=%d dy=%d\", id(g_tp_dx), id(g_tp_dy));'\n"
    "            - homeassistant.action:\n"
    "                action: pyscript.lg_pointer_move\n"
    "                data:\n"
    "                  dx: !lambda 'return std::to_string(id(g_tp_dx) * 2);'\n"
    "                  dy: !lambda 'return std::to_string(id(g_tp_dy) * 2);'\n"
    "            - lambda: 'id(g_tp_dx) = 0; id(g_tp_dy) = 0;'\n"
    "      - if:\n"
    "          condition:\n"
    "            lambda: 'return id(g_tp_active) && abs(id(g_tp_scroll)) >= 10;'\n"
    "          then:\n"
    "            - homeassistant.action:\n"
    "                action: pyscript.lg_pointer_scroll\n"
    "                data:\n"
    "                  dy: !lambda 'return std::to_string(id(g_tp_scroll) / 10);'\n"
    "            - lambda: 'id(g_tp_scroll) = id(g_tp_scroll) % 10;'\n"
)

# Night wake-on-approach (9pm-6am), verbatim from the hand-built build: while asleep
# and g_cam_wake is on, the onboard camera's frame-diff motion wakes the panel; motion
# keeps it awake; 60s of stillness sleeps it again. All refs survive generation once
# esp_video_camera is KEPT (aurora_cam, g_* globals, ha_time, motion_threshold,
# display_backlight, page_home).
CAM_WAKE_INTERVAL = (
    "  - interval: 500ms\n"
    "    then:\n"
    "      - if:\n"
    "          condition:\n"
    "            lambda: |-\n"
    "              if (!id(g_cam_wake) || !id(g_screen_off)) return false;\n"
    "              auto t = id(ha_time).now();\n"
    "              if (!t.is_valid()) return false;\n"
    "              if (!(t.hour >= 21 || t.hour < 6)) return false;\n"
    "              return id(aurora_cam).get_motion_level() >= (int) id(motion_threshold).state;\n"
    "          then:\n"
    "            - lambda: 'id(g_screen_off) = false; lv_display_trigger_activity(lv_display_get_default());'\n"
    "            - light.turn_on:\n"
    "                id: display_backlight\n"
    "                brightness: !lambda 'return id(g_bri) / 100.0f;'\n"
    "            - lvgl.page.show: page_home\n"
    "      - if:\n"
    "          condition:\n"
    "            lambda: |-\n"
    "              if (!id(g_cam_wake) || id(g_screen_off)) return false;\n"
    "              auto t = id(ha_time).now();\n"
    "              if (!t.is_valid()) return false;\n"
    "              if (!(t.hour >= 21 || t.hour < 6)) return false;\n"
    "              return id(aurora_cam).get_motion_level() >= (int) id(motion_threshold).state;\n"
    "          then:\n"
    "            - lambda: 'lv_display_trigger_activity(lv_display_get_default());'\n"
    "      - if:\n"
    "          condition:\n"
    "            lambda: |-\n"
    "              if (!id(g_cam_wake) || id(g_screen_off)) return false;\n"
    "              auto t = id(ha_time).now();\n"
    "              if (!t.is_valid()) return false;\n"
    "              if (!(t.hour >= 21 || t.hour < 6)) return false;\n"
    "              return lv_display_get_inactive_time(lv_display_get_default()) > 60000;\n"
    "          then:\n"
    "            - lambda: 'id(g_screen_off) = true;'\n"
    "            - light.turn_off: display_backlight\n"
)


# Live header clock/date. strftime format + whether to strip a leading zero, per kind.
CLOCK_FMT = {
    "time": ("%I:%M %p", True), "time_hm": ("%I:%M", True), "date": ("%a %b %d", False),
    "date_full": ("%A, %B %d", False), "dow": ("%A", False), "date_long": ("%B %d", False),
}


def clock_items(clocks):
    """A single interval: item that repaints every live header time/date label from
    ha_time (the HA time source kept in the generated build). Appended to the same
    interval: list as TP_FLUSH_INTERVAL — device build only (host has no ha_time)."""
    if not clocks:
        return ""
    ups = ""
    for cid, kind in clocks:
        fmt, strip0 = CLOCK_FMT.get(kind, CLOCK_FMT["time"])
        lam = ("auto t = id(ha_time).now(); if (!t.is_valid()) return std::string(\"\"); "
               "char b[24]; t.strftime(b, sizeof(b), \"" + fmt + "\"); std::string s(b);")
        if strip0:
            lam += " if (!s.empty() && s[0] == 48) s.erase(0, 1);"   # 48 = '0'; avoid a single-quote in the YAML scalar
        lam += " return s;"
        ups += "      - lvgl.label.update: { id: " + cid + ", text: !lambda '" + lam + "' }\n"
    return "  - interval: 5s\n    then:\n" + ups


# --- Screensaver subsystem (regenerated). The hand-built one lives on a page the
# generator drops; only the g_ss_* globals + ss_base/ss_count/ha_base substitutions
# survive. Re-inject the photo decoders, the ss_next picker, the HA photo-list sensor,
# a 1s cycle interval, on_idle (enter), and a generated page_screensaver (tap to wake).
SS_ONLINE_IMAGE = (
    "\nonline_image:\n"
    "  - id: ss_image\n    url: \"http://127.0.0.1/none.jpg\"\n    format: JPEG\n    type: RGB565\n    resize: 1024x600\n    update_interval: never\n"
    "    on_download_finished:\n      - lvgl.image.update: { id: ss_photo, src: ss_image }\n    on_error:\n      - logger.log: \"SS: photo download error\"\n"
    "  - id: ss_image_png\n    url: \"http://127.0.0.1/none.png\"\n    format: PNG\n    type: RGB565\n    resize: 1024x600\n    update_interval: never\n"
    "    on_download_finished:\n      - lvgl.image.update: { id: ss_photo, src: ss_image_png }\n    on_error:\n      - logger.log: \"SS: png download error\"\n"
)
SS_SCRIPT = (
    "\nscript:\n"
    "  - id: ss_next\n    mode: restart\n    then:\n"
    "      - lambda: |-\n"
    "          std::string base = std::string(\"$ha_base\") + \"$ss_base/\";\n"
    "          std::string fname;\n"
    "          int n = (int) id(g_ss_files).size();\n"
    "          if (n > 0) {\n"
    "            if (id(g_ss_i) >= n) id(g_ss_i) = 0;\n"
    "            fname = id(g_ss_files)[id(g_ss_i)];\n"
    "            id(g_ss_i) = (id(g_ss_i) + 1) % n;\n"
    "          } else {\n"
    "            id(ss_idx) = (id(ss_idx) % $ss_count) + 1;\n"
    "            fname = std::to_string(id(ss_idx)) + \".jpg\";\n"
    "          }\n"
    "          std::string url = base;\n"
    "          for (size_t i = 0; i < fname.size(); i++) { if (fname[i] == ' ') url += \"%20\"; else url += fname[i]; }\n"
    "          id(g_ss_url) = url;\n"
    "          std::string lo = fname;\n"
    "          for (size_t i = 0; i < lo.size(); i++) lo[i] = tolower(lo[i]);\n"
    "          id(g_ss_is_png) = (lo.size() >= 4 && lo.compare(lo.size() - 4, 4, \".png\") == 0);\n"
    "      - if:\n          condition:\n            lambda: 'return id(g_ss_is_png);'\n          then:\n"
    "            - online_image.set_url: { id: ss_image_png, url: !lambda 'return id(g_ss_url);' }\n            - component.update: ss_image_png\n"
    "          else:\n            - online_image.set_url: { id: ss_image, url: !lambda 'return id(g_ss_url);' }\n            - component.update: ss_image\n"
)
SS_TEXT_SENSOR = (
    "  - platform: homeassistant\n    id: ha_ss_list\n    entity_id: sensor.aurora_screensaver\n    attribute: list\n    on_value:\n      then:\n"
    "        - lambda: |-\n"
    "            id(g_ss_files).clear();\n"
    "            std::string s = x, cur;\n"
    "            for (size_t i = 0; i < s.size(); i++) {\n"
    "              if (s[i] == '|') { if (!cur.empty()) id(g_ss_files).push_back(cur); cur.clear(); }\n"
    "              else cur += s[i];\n"
    "            }\n"
    "            if (!cur.empty()) id(g_ss_files).push_back(cur);\n"
    "            id(g_ss_i) = 0;\n"
)
SS_INTERVAL_ITEM = (
    "  - interval: 1s\n    then:\n"
    "      - if:\n          condition:\n            lambda: 'return id(g_ss_showing);'\n          then:\n"
    "            - lambda: 'id(g_ss_elapsed) += 1;'\n"
    "            - if:\n                condition:\n                  lambda: |-\n"
    "                    int secs = (int) id(ss_seconds).state;\n                    if (secs < 5) secs = 30;\n                    return id(g_ss_elapsed) >= secs;\n"
    "                then:\n                  - lambda: 'id(g_ss_elapsed) = 0;'\n                  - script.execute: ss_next\n"
)
SS_ONIDLE = (
    "  on_idle:\n    timeout: !lambda 'return id(g_timeout_ms) == 0 ? 86400000 : id(g_timeout_ms);'\n    then:\n"
    "      - if:\n          condition:\n            lambda: 'return id(g_timeout_ms) > 0;'\n          then:\n"
    "            - if:\n                condition:\n                  lambda: 'return id(g_screensaver);'\n                then:\n                  - lvgl.page.show: page_screensaver\n"
    "                else:\n                  - light.turn_off: display_backlight\n"
)


def gen_screensaver_page():
    """Full-screen photo screensaver: ss_photo (fed by the online_image decoders),
    a dim scrim, live clock/date + demo temp. Any tap wakes to home."""
    return (
        "    - id: page_screensaver\n      bg_color: 0x000000\n      bg_opa: 100%\n"
        "      on_load:\n"
        "        - lvgl.widget.update: { id: nav_rail, hidden: true }\n"
        "        - lambda: 'id(g_ss_showing) = true; id(g_ss_elapsed) = 0; id(g_ss_i) = 0;'\n"
        "        - script.execute: ss_next\n"
        "      on_unload:\n"
        "        - lvgl.widget.update: { id: nav_rail, hidden: false }\n"
        "        - lambda: 'id(g_ss_showing) = false;'\n"
        "      widgets:\n"
        "        - image: { id: ss_photo, src: ss_image, align: center }\n"
        "        - button: { id: ss_wake, x: 0, y: 0, width: 1024, height: 600, bg_opa: 0%, border_width: 0, radius: 0, on_click: [lvgl.page.show: page_home] }\n"
        "        - obj: { id: ss_scrim, align: bottom_mid, x: 0, y: 0, width: 1024, height: 184, bg_color: 0x000000, bg_opa: 50%, border_width: 0, radius: 0, pad_all: 0, scrollable: false, clickable: false }\n"
        "        - label: { id: lbl_ss_time, text: \"9:41\", align: bottom_left, x: 44, y: -86, text_font: f_display, text_color: 0xFFFFFF }\n"
        "        - label: { id: lbl_ss_date, text: \"\", align: bottom_left, x: 48, y: -36, text_font: f_body, text_color: 0xC8CCD6 }\n"
        "        - label: { id: lbl_ss_temp, text: \"72\\u00B0\", align: bottom_right, x: -44, y: -62, text_font: f_title, text_color: 0xFFFFFF }\n"
        "        - label: { id: lbl_ss_wx_icon, text: \"\\U000F0599\", align: bottom_right, x: -46, y: -104, text_font: f_icon, text_color: 0xFFFFFF }\n"
    )


def assemble(layout):
    with open(AURORA, encoding="utf-8") as f:
        secs = split_sections(f.read())
    lvgl_text = dict(secs).get("lvgl", "")
    keep_text = "".join(t for n, t in secs if n in KEEP)
    # scrub references to dropped UI scripts + lvgl widget actions in the base
    keep_text = re.sub(r"(?m)^[ \t]*-?[ \t]*script\.(execute|stop):.*\n", "", keep_text)
    keep_text = scrub_lvgl_actions(keep_text)
    ART_IMAGES.clear()
    nav, pages, sens, txt, _, clocks = build_lvgl(layout)
    clocks += [("lbl_ss_time", "time_hm"), ("lbl_ss_date", "date_full")]   # screensaver clock
    pages += gen_screensaver_page()
    txt.append(SS_TEXT_SENSOR)
    # album art: one decoder per art size in use; each entity's sp_nowplaying_image_url
    # (SpotifyPlus; absolute scdn JPEG) set_urls + updates every decoder on track change.
    art_items = ""
    if ART_IMAGES:
        sizes = sorted({s for s, _, _ in ART_IMAGES})
        for s in sizes:
            ups = "".join("      - lvgl.image.update: { id: %s, src: gen_art_%d }\n" % (iid, s)
                          for sz, iid, _ in ART_IMAGES if sz == s)
            art_items += ("  - id: gen_art_%d\n    url: \"http://127.0.0.1/none.jpg\"\n    format: JPEG\n    type: RGB565\n"
                          "    resize: %dx%d\n    update_interval: never\n"
                          "    on_download_finished:\n%s"
                          "    on_error:\n      - logger.log: \"ART %d: download error\"\n" % (s, s, s, ups, s))
        for n_i, ent in enumerate(sorted({e for _, _, e in ART_IMAGES})):
            acts = "".join("              - online_image.set_url: { id: gen_art_%d, url: !lambda 'return x;' }\n"
                           "              - component.update: gen_art_%d\n" % (s, s) for s in sizes)
            txt.append("  - platform: homeassistant\n    id: ha_gen_art_%d\n    entity_id: %s\n    attribute: sp_nowplaying_image_url\n"
                       "    on_value:\n      then:\n        - if:\n            condition:\n"
                       "              lambda: 'return x.rfind(\"http\", 0) == 0;'\n            then:\n%s"
                       % (n_i, ent, acts))
    # interval: list = trackpad flush + camera night-wake + screensaver cycle + clock repaint
    out = keep_text + TP_FLUSH_INTERVAL + CAM_WAKE_INTERVAL + SS_INTERVAL_ITEM + clock_items(clocks)
    out += SS_ONLINE_IMAGE + art_items + SS_SCRIPT
    if sens:
        out += "\nsensor:\n" + "".join(sens)
    out += "\ntext_sensor:\n" + "".join(txt)
    out += ("\nlvgl:\n"
            "  buffer_size: 25%\n"
            + SS_ONIDLE                                   # enter screensaver on idle timeout
            + style_defs(lvgl_text)
            + "  top_layer:\n      widgets:\n"
            "      - obj:\n          id: nav_rail\n          x: 0\n          y: 0\n          width: 74\n          height: 600\n"
            "          bg_color: 0x0C0D12\n          bg_opa: 90%\n          border_width: 0\n          radius: 0\n          pad_all: 0\n          widgets:\n"
            + nav
            + "  pages:\n" + pages)
    return out


EMUL = os.path.join(os.path.dirname(AURORA), "aurora-emul.yaml")


def host_assemble(layout):
    """Emit a host+SDL desktop build of the generated UI for screenshotting.
    Same LVGL pages/cards/fonts/styles as the device, but on the `host`
    platform with an SDL window instead of the ESP32-P4 hardware. Drops the
    HA-backed state sensors (no live data on the desktop) and the background
    image; a bare api: keeps the cards' homeassistant.action refs valid."""
    with open(AURORA, encoding="utf-8") as f:
        secs = split_sections(f.read())
    lvgl_text = dict(secs).get("lvgl", "")
    keep = "".join(t for n, t in secs if n in ("substitutions", "globals", "font"))
    keep = re.sub(r"(?m)^[ \t]*-?[ \t]*script\.(execute|stop):.*\n", "", keep)
    keep = scrub_lvgl_actions(keep)
    global ART_ENABLED
    ART_ENABLED = False                                  # host build: no online_image decoders
    try:
        nav, pages, _sens, _txt, _, _clocks = build_lvgl(layout)   # host has no ha_time -> no clock interval
    finally:
        ART_ENABLED = True
    pages = re.sub(r"(?m)^\s*- image: \{ src: img_aurora_bg.*\n", "", pages)
    # host build has no display_backlight light / restart button — stub those local actions
    pages = re.sub(r"light\.turn_on: \{ id: display_backlight[^}]*\}", "logger.log: emul", pages)
    pages = pages.replace("button.press: btn_restart_panel", "logger.log: emul")
    return (
        "# AUTO-GENERATED host/SDL emulator build of layout.json — DO NOT EDIT.\n"
        "esphome:\n  name: aurora-emul\n\n"
        "host:\n\n"
        "api:\n\n"
        "logger:\n  level: WARN\n\n"
        + keep
        + "\ndisplay:\n  - platform: sdl\n    id: emul_display\n"
          "    dimensions:\n      width: 1024\n      height: 600\n    update_interval: 1s\n"
        + "\ntouchscreen:\n  - platform: sdl\n    display: emul_display\n"
        + "\nlvgl:\n  displays: [emul_display]\n  buffer_size: 100%\n"
        + style_defs(lvgl_text)
        + "  top_layer:\n      widgets:\n"
          "      - obj:\n          id: nav_rail\n          x: 0\n          y: 0\n          width: 74\n          height: 600\n"
          "          bg_color: 0x0C0D12\n          bg_opa: 90%\n          border_width: 0\n          radius: 0\n          pad_all: 0\n          widgets:\n"
        + nav
        + "  pages:\n" + pages
    )


def _loader():
    import yaml

    class L(yaml.SafeLoader):
        pass
    L.add_multi_constructor("!", lambda loader, suffix, node: None)
    L.add_constructor("!secret", lambda loader, node: "secret")
    L.add_constructor("!lambda", lambda loader, node: "lambda")
    return yaml, L


def fragment(layout):
    """Just the generated lvgl + state sensors (no base) — for validating codegen."""
    nav, pages, sens, txt, _, _clocks = build_lvgl(layout)
    frag = ("lvgl:\n  top_layer:\n    widgets:\n      - obj:\n          widgets:\n" + nav
            + "  pages:\n" + pages)
    if sens:
        frag += "\nsensor:\n" + "".join(sens)
    if txt:
        frag += "\ntext_sensor:\n" + "".join(txt)
    return frag, len(sens), len(txt)


def validate(text):
    yaml, L = _loader()
    doc = yaml.load(text, Loader=L)
    assert "lvgl" in doc and "pages" in doc["lvgl"], "missing lvgl.pages"
    return len(doc["lvgl"]["pages"])


def main():
    with open(LAYOUT_JSON, encoding="utf-8") as f:
        layout = json.load(f)
    if "--check" in sys.argv:
        frag, ns, nt = fragment(layout)
        try:
            npages = validate(frag)
        except Exception as e:  # noqa: BLE001
            mark = getattr(e, "problem_mark", None)
            if mark:
                lines = frag.splitlines()
                ctx = "\n".join("  %4d| %s" % (i + 1, lines[i])
                                for i in range(max(0, mark.line - 3), min(len(lines), mark.line + 2)))
                print("YAML ERROR at line %d: %s\n%s" % (mark.line + 1, e, ctx))
            else:
                print("ERROR: %s" % e)
            sys.exit(1)
        print("OK: %d pages, %d state sensors, %d text_sensors (generated YAML parses)" % (npages, ns, nt))
        return
    if "--host" in sys.argv:
        out = host_assemble(layout)
        if "--cycle" in sys.argv:   # auto-advance pages so a harness can screenshot each
            out += "\ninterval:\n  - interval: 4s\n    then:\n      - lvgl.page.next:\n"
        with open(EMUL, "w", encoding="utf-8") as f:
            f.write(out)
        print("wrote %s" % EMUL)
        return
    out = assemble(layout)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
