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
    "music": "\\U000F075A", "shield-home": "\\U000F068A", "wifi": "\\U000F092A",
    "cog": "\\U000F0493", "remote-tv": "\\U000F0395", "speaker-multiple": "\\U000F04C3",
    "spotify": "\\U000F0511", "blinds": "\\U000F00B1", "camera": "\\U000F0100",
    "weather-partly-cloudy": "\\U000F0595",
}
FALLBACK_GLYPH = "\\U000F02DC"


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
def lbl(text, x, y, font="f_body", color="0xF3F5F8", wid=None, align=None, width=None):
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
    parts.append("text_font: " + font)
    parts.append("text_color: " + color)
    return "              - label: { %s }\n" % ", ".join(parts)


def btn(x, y, w, h, label_glyph, action, bg="0x161B24", color="0xF3F5F8", radius=12, font="f_body"):
    return (
        "              - button:\n"
        "                  x: %d\n                  y: %d\n                  width: %d\n                  height: %d\n"
        "                  bg_color: %s\n                  radius: %d\n                  scrollable: false\n"
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
        "            styles: st_glass\n            scrollable: false%s\n"
        "            widgets:\n%s" % (x, y, w, h, oc, inner)
    )


# ---- per-card emitters: return (widgets[str], sensors[str], text_sensors[str]) ----
def c_toggle(card, x, y, w, h, base):
    e = card.get("entity", "")
    sid = base + "_st"
    inner = lbl(card.get("name", "Switch"), 14, 14, "f_title" if h >= 2 else "f_body")
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
    inner = lbl(card.get("name", "Light"), 14, 12, "f_title")
    if e:
        inner += (
            "              - slider:\n                  id: %s\n                  x: 14\n                  y: 56\n                  width: %d\n"
            "                  min_value: 0\n                  max_value: 100\n                  value: 0\n"
            "                  on_release:\n                    - homeassistant.action:\n                        action: light.turn_on\n"
            "                        data: { entity_id: %s, brightness_pct: !lambda 'return (int) id(%s).get_value();' }\n"
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
    inner = lbl(card.get("name", "Sensor"), 14, -12, "f_small", "0x868CA0", align="bottom_left")
    inner += lbl("--", 14, 12, "f_head", "0xF3F5F8", wid=vid)
    ts = []
    if e:
        ts.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return x;' }\n" % (vid, e, vid))
    return [card_obj(x, y, w, h, inner)], [], ts


def c_climate(card, x, y, w, h, base):
    e = card.get("entity", "")
    tid = base + "_t"
    inner = lbl(card.get("name", "Climate"), 14, 12, "f_small", "0x868CA0")
    inner += lbl("72\\u00B0", 0, 0, "f_display", "0xF3F5F8", wid=tid, align="center")
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
    inner = lbl("NOW PLAYING", 14, 12, "f_small", "0x2ED5B8")
    inner += lbl("--", 14, 40, "f_title", "0xF3F5F8", wid=tid, width=w - 28)
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


def c_shortcuts(card, x, y, w, h, pagemap, base):
    inner = ""
    sc = card.get("shortcuts", [])
    n = max(1, min(len(sc), card["w"] * card["h"]))
    cols = card["w"]
    rows = max(1, (n + cols - 1) // cols)
    pad = 12
    bw = (w - pad * 2 - (cols - 1) * 8) // cols
    bh = (h - pad * 2 - (rows - 1) * 8) // rows
    for i in range(n):
        s = sc[i] if i < len(sc) else {}
        tgt = s.get("target", "")
        label = s.get("label", "Open")
        cx = pad + (i % cols) * (bw + 8)
        cy = pad + (i // cols) * (bh + 8)
        act = "lvgl.page.show: page_home"
        if tgt.startswith("page:"):
            pid = pagemap.get(tgt[5:])
            if pid:
                act = "lvgl.page.show: %s" % pid
        inner += btn(cx, cy, bw, bh, label, act)
    return [card_obj(x, y, w, h, inner)], [], []


def c_generic(card, x, y, w, h, base):
    inner = lbl(card.get("name", card.get("ck", "Card")), 0, 0, "f_body", "0x868CA0", align="center")
    return [card_obj(x, y, w, h, inner)], [], []


CTRL = {
    "switch": c_toggle, "light_t": c_toggle, "light": c_light, "sensor": c_sensor,
    "binary": c_sensor, "person": c_sensor, "vacuum": c_sensor, "alarm": c_sensor,
    "climate": c_climate, "scene": c_action, "script": c_action, "media": c_media,
    "spotify": c_media, "sonos": c_media,
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
                first = layout.get("nav", [{}])[0].get("page")
                htext = "Good evening, Ben" if key == first else page.get("title", "Aurora")
                widgets += ("        - label: { text: %s, x: 96, y: 16, text_font: f_head, text_color: 0xF3F5F8 }\n"
                            "        - label: { text: \"72\\u00B0\", align: top_right, x: -16, y: 22, text_font: f_title, text_color: 0xF2B84B }\n"
                            % esc(htext))
            for card in cards:
                ws, ss, ts = emit_card(card, header_on, pagemap)
                widgets += "".join(ws)
                sens += ss
                txt += ts
            # Next affordance if a following sub-page exists
            if si < len(subs) - 1:
                nxt = "%s_%d" % (pagemap[key], si + 1)
                widgets += btn(884, 540, 110, 44, "Next \\U000F0142", "lvgl.page.show: %s" % nxt, font="f_body")
            pages_yaml += (
                "    - id: %s\n      bg_color: 0x0A0B0F\n      widgets:\n%s" % (pid, widgets))
    return pages_yaml, sens, txt


def build_lvgl(layout):
    pagemap = {key: "page_" + slug(key) for key in layout.get("pages", {})}
    nav = gen_nav(layout, pagemap)
    pages, sens, txt = gen_pages(layout, pagemap)
    return nav, pages, sens, txt, pagemap


# ---- base extraction: keep hardware/font/style sections, drop UI bindings ----
KEEP = ["substitutions", "esphome", "esp32", "psram", "esp_ldo", "esp32_hosted",
        "wifi", "api", "ota", "safe_mode", "logger", "output", "light",
        "external_components", "i2c", "touchscreen", "display", "http_request",
        "image", "font", "globals", "number"]


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
    out = assemble(layout)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
