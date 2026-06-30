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
    "cog": "\\U000F0493", "remote-tv": "\\U000F0502", "speaker-multiple": "\\U000F075A",
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
def lbl(text, x, y, font="f_body", color="0xF3F5F8", wid=None, align=None, width=None, long=None, height=None):
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


def card_obj(x, y, w, h, inner, on_click=None):
    oc = ("\n            clickable: true\n            on_click: [%s]" % on_click) if on_click else ""
    return (
        "        - obj:\n"
        "            x: %d\n            y: %d\n            width: %d\n            height: %d\n"
        "            styles: st_glass\n            pad_all: 0\n            scrollable: false%s\n"
        "            widgets:\n%s" % (x, y, w, h, oc, inner)
    )


# ---- per-card emitters: return (widgets[str], sensors[str], text_sensors[str]) ----
# Card icons — all codepoints confirmed present in the baked f_icon font.
CARD_ICON = {
    "light": "\\U000F0335", "light_t": "\\U000F0336", "switch": "\\U000F06A5",
    "outletgroup": "\\U000F06A5", "fan": "\\U000F0210", "cover": "\\U000F081A",
    "climate": "\\U000F0393", "sensor": "\\U000F050F", "binary": "\\U000F050F",
    "lock": "\\U000F033E", "camera": "\\U000F0502", "weather": "\\U000F0599",
    "scene": "\\U000F04CE", "script": "\\U000F0425", "media": "\\U000F075A",
    "spotify": "\\U000F075A", "sonos": "\\U000F075A", "speakers": "\\U000F075A",
    "sonos_sources": "\\U000F075A", "group": "\\U000F1253", "lightgroup": "\\U000F1253",
    "person": "\\U000F02DC", "tvremote": "\\U000F0502", "vacuum": "\\U000F050F",
    "alarm": "\\U000F068A",
    # Spotify / Sonos media cards
    "playlist": "\\U000F075A", "sonos_fav": "\\U000F04CE", "songlist": "\\U000F075A",
    "sonos_library": "\\U000F125F",
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
    inner = ic(card["ck"], color="0xF2B84B")
    inner += title(card.get("name", "Switch"), w, x=14, y=48)
    inner += lbl("--", 14, -14, "f_small", "0x2ED5B8", wid=sid, align="bottom_left")
    on = ha("homeassistant.toggle", e) if e else None
    ts = []
    if e:
        ts.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return x == \"on\" ? std::string(\"On\") : std::string(\"Off\");' }\n"
            % (sid, e, sid))
    return [card_obj(x, y, w, h, inner, on)], [], ts


def c_light(card, x, y, w, h, base):
    e = card.get("entity", "")
    sld, pct = base + "_sld", base + "_pct"
    inner = ic(card["ck"], color="0xF2B84B")
    inner += title(card.get("name", "Light"), w)
    if e:
        inner += (
            "              - slider:\n                  id: %s\n                  x: 14\n                  y: 56\n                  width: %d\n"
            "                  min_value: 0\n                  max_value: 100\n                  value: 0\n"
            "                  on_release:\n                    - homeassistant.action:\n                        action: light.turn_on\n"
            "                        data: { entity_id: %s, brightness_pct: !lambda 'return std::to_string((int) lv_slider_get_value(id(%s)));' }\n"
            % (sld, w - 28, e, sld))
    inner += lbl("--%", 14, -12, "f_head", "0x2ED5B8", wid=pct, align="bottom_left")
    s = []
    if e:
        s.append(
            "  - platform: homeassistant\n    id: ha_%s_b\n    entity_id: %s\n    attribute: brightness\n    on_value:\n"
            "      - lvgl.slider.update: { id: %s, value: !lambda 'return (int)(x/2.55);' }\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return std::to_string((int)(x/2.55)) + \"%%\";' }\n"
            % (base, e, sld, pct))
    return [card_obj(x, y, w, h, inner, None)], s, []


def c_sensor(card, x, y, w, h, base):
    e = card.get("entity", "")
    vid = base + "_v"
    inner = ic(card["ck"], color="0xF2685A")
    inner += lbl("--", 14, 48, "f_head", "0xF3F5F8", wid=vid)
    inner += lbl(card.get("name", "Sensor"), 14, -12, "f_small", "0x868CA0", align="bottom_left")
    ts = []
    if e:
        ts.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return x;' }\n" % (vid, e, vid))
    return [card_obj(x, y, w, h, inner)], [], ts


def c_climate(card, x, y, w, h, base):
    e = card.get("entity", "")
    tid = base + "_t"
    inner = ic(card["ck"], color="0xF2B84B")
    inner += lbl(card.get("name", "Climate"), 50, 16, "f_small", "0x868CA0")
    inner += lbl("72\\u00B0", 0, -10, "f_display", "0xF3F5F8", wid=tid, align="center")
    if e:
        inner += btn(14, h - 60, 56, 46, "\\U000F0374", ha("climate.set_temperature", e,
                     "temperature: !lambda 'return id(%s_cur)-1;'" % base) if False else
                     "homeassistant.action: { action: climate.set_temperature, data: { entity_id: %s } }" % e,
                     font="f_icon")
        inner += btn(w - 70, h - 60, 56, 46, "\\U000F0415",
                     "homeassistant.action: { action: climate.set_temperature, data: { entity_id: %s } }" % e,
                     font="f_icon")
    s = []
    if e:
        s.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: temperature\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return std::to_string((int)x) + \"\\u00B0\";' }\n"
            % (tid, e, tid))
    return [card_obj(x, y, w, h, inner)], s, []


def c_action(card, x, y, w, h, base):
    e = card.get("entity", "")
    dom = e.split(".")[0] if "." in e else "scene"
    act = {"scene": "scene.turn_on", "script": "script.turn_on", "button": "button.press",
           "input_button": "input_button.press"}.get(dom, "homeassistant.toggle")
    inner = lbl(card.get("name", "Scene"), 0, 0, "f_body", align="center")
    on = ha(act, e) if e else None
    return [card_obj(x, y, w, h, inner, on)], [], []


def c_media(card, x, y, w, h, base):
    e = card.get("entity", "")
    tid = base + "_t"
    inner = ic(card["ck"], color="0xB06CFF")
    inner += lbl("NOW PLAYING", 50, 16, "f_small", "0x2ED5B8")
    inner += lbl("--", 14, 52, "f_title", "0xF3F5F8", wid=tid, width=w - 28)
    if e and h >= 2:
        bw, by = 52, h - 64
        inner += btn(w // 2 - 90, by, bw, bw, "\\U000F04AE", ha("media_player.media_previous_track", e), radius=26, font="f_icon")
        inner += btn(w // 2 - 26, by - 6, 56, 56, "\\U000F040A", ha("media_player.media_play_pause", e), bg="0x2ED5B8", color="0x06231D", radius=28, font="f_icon")
        inner += btn(w // 2 + 38, by, bw, bw, "\\U000F04AD", ha("media_player.media_next_track", e), radius=26, font="f_icon")
    ts = []
    if e:
        ts.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: media_title\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return x.empty() ? std::string(\"Nothing playing\") : x;' }\n"
            % (tid, e, tid))
    return [card_obj(x, y, w, h, inner)], [], ts


def c_fan(card, x, y, w, h, base):
    e = card.get("entity", ""); sid = base + "_st"
    inner = ic(card["ck"])
    inner += title(card.get("name", "Fan"), w, x=14, y=48)
    inner += lbl("--", 14, -12, "f_small", "0x2ED5B8", wid=sid, align="bottom_left")
    on = ha("fan.toggle", e) if e else None
    ts = []
    if e:
        ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
                  "      - lvgl.label.update: { id: %s, text: !lambda 'return x == \"on\" ? std::string(\"On\") : std::string(\"Off\");' }\n" % (sid, e, sid))
    return [card_obj(x, y, w, h, inner, on)], [], ts


def c_cover(card, x, y, w, h, base):
    e = card.get("entity", "")
    inner = ic(card["ck"], color="0x4FA8F5")
    inner += title(card.get("name", "Cover"), w)
    if e and (w >= 2 or h >= 2):
        half = (w - 36) // 2
        inner += btn(14, h - 60, half, 46, "\\U000F0143", ha("cover.open_cover", e), font="f_icon")
        inner += btn(22 + half, h - 60, half, 46, "\\U000F0140", ha("cover.close_cover", e), font="f_icon")
    return [card_obj(x, y, w, h, inner)], [], []


def c_lock(card, x, y, w, h, base):
    e = card.get("entity", ""); sid = base + "_st"
    inner = ic(card["ck"])
    inner += title(card.get("name", "Lock"), w, x=14, y=48)
    inner += lbl("--", 14, -12, "f_small", "0x2ED5B8", wid=sid, align="bottom_left")
    on = ha("lock.unlock", e) if e else None
    ts = []
    if e:
        ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
                  "      - lvgl.label.update: { id: %s, text: !lambda 'return x == \"locked\" ? std::string(\"Locked\") : std::string(\"Unlocked\");' }\n" % (sid, e, sid))
    return [card_obj(x, y, w, h, inner, on)], [], ts


def c_weather(card, x, y, w, h, base):
    inner = "              - label: { text: \"\\U000F0599\", x: 14, y: 14, text_font: f_wxicon, text_color: 0xF2B84B }\n"
    inner += lbl("72\\u00B0", -16, 20, "f_display", "0xF3F5F8", align="top_right")
    inner += lbl("Sunny", 14, -12, "f_body", "0x2ED5B8", align="bottom_left")
    return [card_obj(x, y, w, h, inner)], [], []


def c_camera(card, x, y, w, h, base):
    inner = ("              - obj: { x: 8, y: 8, width: %d, height: %d, bg_color: 0x10141C, "
             "border_width: 0, radius: 12, pad_all: 0, scrollable: false }\n" % (w - 16, h - 16))
    inner += ic(card["ck"], x=20, y=20, color="0x2A3346")
    inner += lbl("LIVE", 20, -18, "f_small", "0xF2685A", align="bottom_left")
    inner += lbl(card.get("name", "Camera"), 72, -18, "f_small", "0x868CA0", align="bottom_left")
    return [card_obj(x, y, w, h, inner)], [], []


def c_group(card, x, y, w, h, base):
    """Grid of entity tiles (not a text list). `lightgroup` tiles are tappable
    toggles (amber); `group` tiles are status readouts (teal)."""
    ents = card.get("entities", [])
    is_lights = card["ck"] == "lightgroup"
    accent = "0xF2B84B" if is_lights else "0x2ED5B8"
    inner = ic(card["ck"], color=accent)
    inner += lbl(card.get("name", "Lights" if is_lights else "Group"), 50, 16, "f_title", width=w - 64)
    cols = 2 if w >= 280 else 1
    pad, gap, top, bh = 14, 8, 56, 46
    rows_fit = max(1, (h - top - pad + gap) // (bh + gap))
    cap = cols * rows_fit
    bw = (w - pad * 2 - (cols - 1) * gap) // cols
    for i, e in enumerate(ents[:cap]):
        nm = (e.split(".")[-1] if "." in e else e).replace("_", " ")
        cx = pad + (i % cols) * (bw + gap)
        cy = top + (i // cols) * (bh + gap)
        bg = "0x1A1606" if is_lights else "0x0F1117"
        state_txt = "Off" if is_lights else "On"
        click = (", clickable: true, on_click: [%s]" % ha("homeassistant.toggle", e)) if (is_lights and e) else ""
        inner += ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: %s, "
                  "border_width: 0, radius: 10, pad_all: 0, scrollable: false%s, widgets: ["
                  "label: { text: %s, x: 10, y: 6, width: %d, height: 18, long_mode: dot, text_font: f_small, text_color: 0xEEF0F6 }, "
                  "label: { text: \"%s\", x: 10, y: -6, align: bottom_left, text_font: f_small, text_color: %s }] }\n"
                  % (cx, cy, bw, bh, bg, click, esc(nm), bw - 20, state_txt, accent))
    return [card_obj(x, y, w, h, inner)], [], []


def c_outlet(card, x, y, w, h, base):
    ents = card.get("entities", [])
    inner = ic(card["ck"])
    inner += title(card.get("name", "Outlets"), w)
    yy = 56
    for e in (ents or [""])[: max(1, h)]:
        nm = (e.split(".")[-1] if "." in e else e).replace("_", " ") or "Outlet"
        on = ha("homeassistant.toggle", e) if e else "lvgl.page.show: page_home"
        inner += btn(14, yy, w - 28, 44, nm, on, bg="0x13201d")
        yy += 52
    return [card_obj(x, y, w, h, inner)], [], []


def c_btngrid(card, x, y, w, h, base):
    ents = card.get("entities", [])
    inner = ic(card["ck"])
    inner += lbl(card.get("name", "Select"), 50, 16, "f_small", "0x868CA0")
    n = max(1, len(ents))
    cols = 2 if w >= 2 else 1
    rows = max(1, (n + cols - 1) // cols)
    pad = 12
    bw = (w - pad * 2 - (cols - 1) * 8) // cols
    bh = max(34, min(44, (h - 56 - pad - (rows - 1) * 8) // rows))
    for i, e in enumerate(ents[:n]):
        nm = (e.split(".")[-1] if "." in e else e).replace("_", " ")
        cx = pad + (i % cols) * (bw + 8)
        cy = 52 + (i // cols) * (bh + 8)
        on = ha("media_player.media_play_pause", e) if e else "lvgl.page.show: page_home"
        inner += btn(cx, cy, bw, bh, nm, on, bg="0x13201d")
    return [card_obj(x, y, w, h, inner)], [], []


def _tvbtn(bx, by, w_, h_, glyph, e, button, **kw):
    act = ("homeassistant.action: { action: webostv.button, data: { entity_id: %s, button: %s } }"
           % (e, button)) if e else "lvgl.page.show: page_home"
    return btn(bx, by, w_, h_, glyph, act, font="f_icon", **kw)


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


def c_tvremote(card, x, y, w, h, base):
    e = card.get("entity", "")
    inner = ic(card["ck"], color="0xB06CFF") + lbl("LG OLED", 50, 14, "f_title")
    powA = ("homeassistant.action: { action: media_player.toggle, data: { entity_id: %s } }" % e) if e else "lvgl.page.show: page_home"
    inner += btn(w - 66, 14, 52, 46, "\\U000F0425", powA, bg="0x2a1414", color="0xF2685A", font="f_icon")
    inner = _dpad(inner, e, w, h - 30)
    items = [("\\U000F04AE", "media_player.media_previous_track"),
             ("\\U000F040A", "media_player.media_play_pause"),
             ("\\U000F04AD", "media_player.media_next_track")]
    by = h - 66; bw = 56
    for i, (g, svc) in enumerate(items):
        inner += btn(14 + i * (bw + 8), by, bw, 52, g, ha(svc, e) if e else "lvgl.page.show: page_home",
                     font="f_icon", bg=("0x2ED5B8" if i == 1 else "0x161B24"), color=("0x06231D" if i == 1 else "0xF3F5F8"))
    return [card_obj(x, y, w, h, inner)], [], []


def c_playlist(card, x, y, w, h, base):
    e = card.get("entity", "")
    pl = card.get("pl") or card.get("name", "Playlist")
    inner = ic(card["ck"], color="0x1DB954") + lbl(pl, 50, 16, "f_title", width=w - 64)
    inner += lbl("Tap to play", 14, -12, "f_small", "0x868CA0", align="bottom_left")
    on = ("homeassistant.action: { action: media_player.media_play, data: { entity_id: %s } }" % e) if e else None
    return [card_obj(x, y, w, h, inner, on)], [], []


def c_songlist(card, x, y, w, h, base):
    inner = ic(card["ck"], color="0x1DB954") + lbl(card.get("name", "Tracks"), 50, 16, "f_small", "0x868CA0")
    songs = ["Midnight City", "Instant Crush", "Dreams", "Redbone", "Holocene", "Lovely Day", "Electric Feel"]
    yy = 52
    for s in songs[: max(1, (h - 52) // 42)]:
        inner += ("              - obj: { x: 14, y: %d, width: %d, height: 38, bg_color: 0x0F1117, "
                  "border_width: 0, radius: 8, pad_all: 0, scrollable: false, widgets: [label: { text: %s, x: 12, "
                  "y: 10, text_font: f_body, text_color: 0xF3F5F8 }] }\n" % (yy, w - 28, esc(s)))
        yy += 42
    return [card_obj(x, y, w, h, inner)], [], []


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
                "                    - label: { text: \"%s\", align: center, y: -16, text_font: f_icon, text_color: 0x2ED5B8 }\n"
                "                    - label: { text: %s, align: center, y: 22, width: %d, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }\n"
                "                  on_click: [%s]\n"
                % (cx, cy, bw, bh, glyph, esc(s.get("label", "Open")), bw - 10, act))
        else:
            inner += (
                "              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x0F1117, "
                "border_color: 0x2A2E38, border_width: 1, radius: 14, pad_all: 0, scrollable: false, "
                "widgets: [label: { text: \"\\U000F0415\", align: center, text_font: f_icon, text_color: 0x4A5160 }] }\n"
                % (cx, cy, bw, bh))
    return [card_obj(x, y, w, h, inner)], [], []


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
    "lightgroup": c_group, "outletgroup": c_outlet, "speakers": c_btngrid,
    "sonos_sources": c_btngrid, "tv_sources": c_btngrid,
    "tv_dpad": c_tv_dpad, "tv_transport": c_tv_transport, "tv_channel": c_tv_channel,
    "tv_volume": c_tv_volume, "tv_trackpad": c_tv_trackpad, "tvremote": c_tvremote,
    "playlist": c_playlist, "sonos_fav": c_playlist, "songlist": c_songlist,
    "sonos_library": c_songlist,
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
        out += (
            "            - button:\n                id: nav_%s\n                align: top_mid\n                y: %d\n"
            "                width: 58\n                height: 58\n                radius: 14\n                bg_color: %s\n"
            "                widgets: [label: { text: \"%s\", align: center, text_font: f_icon, text_color: 0xF3F5F8 }]\n"
            "                on_click: [lvgl.page.show: %s]\n"
            % (slug(n.get("id", str(i))), 14 + i * 68, "0x2ED5B8" if i == 0 else "0x10121A", g, pid))
    # Settings (always present)
    out += (
        "            - button:\n                id: nav_settings\n                align: bottom_mid\n                y: -14\n"
        "                width: 58\n                height: 58\n                radius: 14\n                bg_color: 0x10121A\n"
        "                widgets: [label: { text: \"\\U000F0493\", align: center, text_font: f_icon, text_color: 0xF3F5F8 }]\n")
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


def gen_header(key, page, layout):
    hdr = page.get("header") or {}
    left = hdr.get("left", "greeting")
    first = layout.get("nav", [{}])[0].get("page")
    greet = "Good evening, Ben" if key == first else page.get("title", "Aurora")
    out = ""
    if left == "time":
        out += "        - label: { text: \"10:42 PM\", x: 96, y: 12, text_font: f_display, text_color: 0xF3F5F8 }\n"
    elif left == "date":
        out += "        - label: { text: \"Sunday\", x: 96, y: 10, text_font: f_head, text_color: 0xF3F5F8 }\n"
        out += "        - label: { text: \"June 29\", x: 96, y: 50, text_font: f_body, text_color: 0x868CA0 }\n"
    elif left == "time_date":
        out += "        - label: { text: \"10:42 PM\", x: 96, y: 10, text_font: f_head, text_color: 0xF3F5F8 }\n"
        out += "        - label: { text: \"Sunday, June 29\", x: 96, y: 50, text_font: f_body, text_color: 0x868CA0 }\n"
    else:
        sub = "Living Room \\u00B7 10:42 PM" + (" \\u00B7 Sun Jun 29" if left == "greeting" else "")
        out += "        - label: { text: %s, x: 96, y: 12, text_font: f_head, text_color: 0xF3F5F8 }\n" % esc(greet)
        out += "        - label: { text: \"%s\", x: 96, y: 52, text_font: f_body, text_color: 0x868CA0 }\n" % sub
    for i, item in enumerate((hdr.get("right") or [])[:4]):
        g, t, col = HCHIP.get(item, ("", item, "0x868CA0"))
        base_x = -(24 + i * 122)
        if g:
            out += "        - label: { text: \"%s\", align: top_right, x: %d, y: 22, text_font: f_icon, text_color: %s }\n" % (g, base_x - 64, col)
        out += "        - label: { text: %s, align: top_right, x: %d, y: 26, text_font: f_body, text_color: %s }\n" % (esc(t), base_x, col)
    return out


def gen_pages(layout, pagemap):
    pages_yaml, sens, txt = "", [], []
    for key, page in layout.get("pages", {}).items():
        hdr = page.get("header") or {}
        header_on = bool(hdr.get("on"))
        subs = page.get("subpages", [[]])
        for si, cards in enumerate(subs):
            pid = pagemap[key] if si == 0 else "%s_%d" % (pagemap[key], si)
            widgets = "        - image: { src: img_aurora_bg, x: 0, y: 0 }\n"
            if header_on:
                widgets += gen_header(key, page, layout)
            for card in cards:
                ws, ss, ts = emit_card(card, header_on, pagemap)
                widgets += "".join(ws)
                sens += ss
                txt += ts
            # Next affordance if a following sub-page exists
            if si < len(subs) - 1:
                nxt = "%s_%d" % (pagemap[key], si + 1)
                widgets += btn(884, 540, 110, 44, "Next \\U000F0142", "lvgl.page.show: %s" % nxt, font="f_body")
            navids = [slug(n.get("id", "")) for n in layout.get("nav", [])]
            active = next((slug(n.get("id", "")) for n in layout.get("nav", []) if n.get("page") == key), None)
            onload = "      on_load:\n" + "".join(
                "        - lvgl.widget.update: { id: nav_%s, bg_color: %s }\n"
                % (nid, "0x2ED5B8" if nid == active else "0x10121A") for nid in navids)
            pages_yaml += (
                "    - id: %s\n      bg_color: 0x0A0B0F\n%s      widgets:\n%s" % (pid, onload, widgets))
    return pages_yaml, sens, txt


def build_lvgl(layout):
    pagemap = {key: "page_" + slug(key) for key in layout.get("pages", {})}
    nav = gen_nav(layout, pagemap)
    pages, sens, txt = gen_pages(layout, pagemap)
    return nav, pages, sens, txt, pagemap


# ---- base extraction: keep hardware/font/style sections, drop UI bindings ----
KEEP = ["substitutions", "esphome", "esp32", "psram", "esp_ldo", "esp32_hosted",
        "wifi", "api", "ota", "safe_mode", "logger", "web_server", "output", "light",
        "external_components", "i2c", "touchscreen", "display", "http_request",
        "image", "font", "globals", "number", "button"]


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


def assemble(layout):
    with open(AURORA, encoding="utf-8") as f:
        secs = split_sections(f.read())
    lvgl_text = dict(secs).get("lvgl", "")
    keep_text = "".join(t for n, t in secs if n in KEEP)
    # scrub references to dropped UI scripts + lvgl widget actions in the base
    keep_text = re.sub(r"(?m)^[ \t]*-?[ \t]*script\.(execute|stop):.*\n", "", keep_text)
    keep_text = scrub_lvgl_actions(keep_text)
    nav, pages, sens, txt, _ = build_lvgl(layout)
    out = keep_text
    if sens:
        out += "\nsensor:\n" + "".join(sens)
    if txt:
        out += "\ntext_sensor:\n" + "".join(txt)
    out += ("\nlvgl:\n"
            "  buffer_size: 25%\n"
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
    nav, pages, _sens, _txt, _ = build_lvgl(layout)
    pages = re.sub(r"(?m)^\s*- image: \{ src: img_aurora_bg.*\n", "", pages)
    return (
        "# AUTO-GENERATED host/SDL emulator build of layout.json — DO NOT EDIT.\n"
        "esphome:\n  name: aurora-emul\n\n"
        "host:\n\n"
        "api:\n\n"
        "logger:\n  level: WARN\n\n"
        + keep
        + "\ndisplay:\n  - platform: sdl\n    id: emul_display\n"
          "    dimensions:\n      width: 1024\n      height: 600\n    update_interval: 1s\n"
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
    nav, pages, sens, txt, _ = build_lvgl(layout)
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
