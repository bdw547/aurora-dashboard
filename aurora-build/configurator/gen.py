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

# ---- comprehensive icon library (shared with the web picker via mdi-meta.json) ----
# Resolve ANY Material Design Icon name to its device glyph, and remember which
# icons a layout used so assemble() can embed exactly those into the f_icon font
# (the device can't bake all ~7,400 glyphs; it only needs the ones in use).
MDI_META = os.path.join(HERE, "mdi-meta.json")


def _load_mdi():
    try:
        with open(MDI_META, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for m in meta:
        n, cp = m.get("name"), m.get("codepoint")
        if n and cp:
            out[n] = cp.rjust(8, "0").upper()  # "F0599" -> "000F0599"
    return out


MDI_CP = _load_mdi()   # icon name -> 8-hex codepoint
USED_ICON_CP = set()   # 8-hex codepoints referenced by the layout being built
USED_ICONSM_CP = set()  # codepoints card emitters need in the small (18px) f_iconsm font


def glyph_for(name, fallback=FALLBACK_GLYPH):
    r"""MDI name -> "\U000F...." device glyph, recording it for font embedding."""
    cp = MDI_CP.get(name or "")
    if not cp:  # unknown name: try the legacy explicit map, else fall back
        g = NAV_GLYPH.get(name or "", fallback)
        cp = g[2:].rjust(8, "0").upper()
        USED_ICON_CP.add(cp)
        return g
    USED_ICON_CP.add(cp)
    return "\\U" + cp


def _inject_glyphs(font_text, font_id, cps):
    """Append any of `cps` not already embedded to the `font_id` `glyphs:` list."""
    if not cps:
        return font_text
    i = font_text.find("id: " + font_id + "\n")
    g = font_text.find("glyphs:", i) if i >= 0 else -1
    if g < 0:
        return font_text
    nxt = font_text.find("\n  - ", g)          # start of the next font entry
    end = nxt if nxt != -1 else len(font_text)
    have = {m.upper() for m in re.findall(r"\\U(000[0-9A-Fa-f]{5})", font_text[g:end])}
    add = sorted(cp for cp in cps if cp.upper() not in have)
    if not add:
        return font_text
    ins = "".join('      - "\\U%s"  # auto (layout icon)\n' % cp for cp in add)
    return font_text[:end] + "\n" + ins.rstrip("\n") + font_text[end:]


def inject_used_glyphs(font_text):
    """Append used-but-not-embedded icons to the f_icon / f_iconsm glyph lists."""
    font_text = _inject_glyphs(font_text, "f_icon", USED_ICON_CP)
    return _inject_glyphs(font_text, "f_iconsm", USED_ICONSM_CP)


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


def btn(x, y, w, h, label_glyph, action, bg="0x161B24", color="0xF3F5F8", radius=12, font="f_body", lid=None):
    idpart = ("id: %s, " % lid) if lid else ""           # id on the label so a readback can update its glyph/text
    return (
        "              - button:\n"
        "                  x: %d\n                  y: %d\n                  width: %d\n                  height: %d\n"
        "                  bg_color: %s\n                  radius: %d\n                  pad_all: 0\n                  scrollable: false\n"
        "                  widgets: [label: { %stext: %s, align: center, text_font: %s, text_color: %s }]\n"
        "                  on_click: [%s]\n"
        % (x, y, w, h, bg, radius, idpart, esc(label_glyph), font, color, action)
    )


def ha(action, entity, extra=""):
    data = "entity_id: %s%s" % (entity, (", " + extra) if extra else "")
    return "homeassistant.action: { action: %s, data: { %s } }" % (action, data)


def card_obj(x, y, w, h, inner, on_click=None, bg=None, oid=None):
    oc = ("\n            clickable: true\n            on_click: [%s]" % on_click) if on_click else ""
    bgline = ("\n            bg_color: %s" % bg) if bg else ""    # override st_glass (e.g. "on" state)
    idline = ("\n            id: %s" % oid) if oid else ""        # so a state readback can recolor the whole card
    return (
        "        - obj:%s\n"
        "            x: %d\n            y: %d\n            width: %d\n            height: %d\n"
        "            styles: st_glass\n            pad_all: 0\n            clip_corner: true%s\n            scrollable: false%s\n"
        "            widgets:\n%s" % (idline, x, y, w, h, bgline, oc, inner)
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
    "spotify_speakers": "\\U000F04C4", "spotify_speaker": "\\U000F04C3",
    # TV control cards (purple family)
    "tv_sources": "\\U000F0502", "tv_dpad": "\\U000F0297", "tv_transport": "\\U000F040A",
    "tv_channel": "\\U000F0502", "tv_volume": "\\U000F057E", "tv_trackpad": "\\U000F0297",
    "shortcuts": "\\U000F04CE",
}


def ic(ck, x=14, y=14, color="0x2ED5B8", glyph=None, wid=None):
    g = glyph or CARD_ICON.get(ck, "\\U000F0493")
    idpart = ("id: %s, " % wid) if wid else ""
    return ("              - label: { %stext: \"%s\", x: %d, y: %d, text_font: f_icon, text_color: %s }\n"
            % (idpart, g, x, y, color))


def card_glyph(card, default_glyph):
    """Per-card icon override (any MDI name, auto-embedded) or the card's default."""
    name = card.get("icon")
    return glyph_for(name) if name else default_glyph


# Switch sub-type -> (MDI icon name, accent). Lets a switch entity present as what
# it actually controls (outlet / light / fan / generic). All stay simple on/off.
SWITCH_TYPES = {
    "switch": ("toggle-switch", "0x2ED5B8"), "outlet": ("power-plug", "0x2ED5B8"),
    "light": ("lightbulb", "0xE6A62B"), "fan": ("fan", "0x2ED5B8"),
    "generic": ("power", "0x2ED5B8"),
}


def c_toggle(card, x, y, w, h, base):
    e = card.get("entity", "")
    gw, gh = card["w"], card["h"]
    sid, oid, iid = base + "_st", base + "_c", base + "_i"
    st = card.get("stype")
    if st in SWITCH_TYPES:
        icon_name, acc = SWITCH_TYPES[st]
    elif card["ck"] == "light_t":
        icon_name, acc = "lightbulb", "0xE6A62B"                  # amber light toggle
    else:
        icon_name, acc = "toggle-switch", "0x2ED5B8"              # teal switch
    glyph = card_glyph(card, glyph_for(icon_name))
    lit = _darken(acc, 0.22)
    on = ha("homeassistant.toggle", e) if e else None            # domain-agnostic (switch/light/fan)
    if gw == 1 and gh == 1:                                       # compact 1x1: centered icon + state
        inner = lbl(glyph, 0, 20, "f_icon", acc, wid=iid, align="top_mid")
        inner += lbl("--", 0, -14, "f_body", acc, wid=sid, align="bottom_mid")
    else:                                                        # icon top-left, name, state (aligned stack)
        inner = ic(card["ck"], color=acc, glyph=glyph, wid=iid)
        inner += title(card.get("name", "Switch"), w, x=14, y=48)
        inner += lbl("--", 14, -14, "f_small", acc, wid=sid, align="bottom_left")
    ts = []
    if e:
        # whole card lights up when on / dims when off (fixes "stays the same color")
        ts.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return x == \"on\" ? std::string(\"On\") : std::string(\"Off\");', text_color: !lambda 'return x == \"on\" ? lv_color_hex(%s) : lv_color_hex(0x6B7280);' }\n"
            "      - lvgl.label.update: { id: %s, text_color: !lambda 'return x == \"on\" ? lv_color_hex(%s) : lv_color_hex(0x4A5160);' }\n"
            "      - lvgl.widget.update: { id: %s, bg_color: !lambda 'return x == \"on\" ? lv_color_hex(%s) : lv_color_hex(0x0E1116);' }\n"
            % (sid, e, sid, acc, iid, acc, oid, lit))
    return [card_obj(x, y, w, h, inner, on, oid=oid)], [], ts


# Fill-strip palette (Aurora "Light + Spotify Styles" handoff): warm amber light.
STRIP_CARD_BG, STRIP_CARD_BD = "0x14161C", "0x2B2410"
STRIP_TRACK, STRIP_GRAD_LO, STRIP_GRAD_HI = "0x0E1014", "0x5C4A14", "0xE6A62B"
STRIP_PWR_ON, STRIP_PWR_BD_ON = "0x2B2410", "0xE6A62B"
STRIP_PWR_OFF, STRIP_PWR_BD_OFF, STRIP_PWR_IC_OFF = "0x1B1E26", "0x2B2F3A", "0x5A6070"


def _strip_card(x, y, w, h, radius, inner):
    """Custom card shell for the fill-strip light styles (#14161C / #2B2410 border)."""
    return ("        - obj:\n"
            "            x: %d\n            y: %d\n            width: %d\n            height: %d\n"
            "            bg_color: %s\n            border_color: %s\n            border_width: 1\n            radius: %d\n"
            "            pad_all: 0\n            clip_corner: true\n            scrollable: false\n"
            "            widgets:\n%s" % (x, y, w, h, STRIP_CARD_BG, STRIP_CARD_BD, radius, inner))


def _strip_readbacks(base, e, sldid, fillid, gripid, pct, pwrid, pwic, axis, extent):
    """HA brightness (resize/move fill+grip+%) + on/off (recolor power, hide fill)."""
    dim = "width" if axis == "h" else "height"
    # brightness attr is null/NaN when the light is off -> guard so % never shows a
    # garbage int (design item: "off / 0% shows a long number"). b = clamped 0..100.
    b = "(isnan(x)?0.0f:(x/2.55f))"
    ext = str(extent)
    s = ["  - platform: homeassistant\n    id: ha_" + base + "_b\n    entity_id: " + e + "\n    attribute: brightness\n    on_value:\n"
         "      - lvgl.slider.update: { id: " + sldid + ", value: !lambda 'return (int)" + b + ";' }\n"
         "      - lvgl.widget.update: { id: " + fillid + ", " + dim + ": !lambda 'return (int)(" + b + " * " + ext + " / 100.0f);' }\n"]
    if axis == "v":  # bottom-anchored fill also moves its top edge; grip tracks the top
        s[0] += ("      - lvgl.widget.update: { id: " + fillid + ", y: !lambda 'return (int)(" + ext + " - " + b + " * " + ext + " / 100.0f);' }\n"
                 "      - lvgl.widget.update: { id: " + gripid + ", y: !lambda 'return (int)(" + ext + " - " + b + " * " + ext + " / 100.0f) - 3;' }\n")
    else:
        s[0] += "      - lvgl.widget.update: { id: " + gripid + ", x: !lambda 'return (int)(" + b + " * " + ext + " / 100.0f) - 3;' }\n"
    s[0] += "      - lvgl.label.update: { id: " + pct + ", text: !lambda 'int p=(int)" + b + "; if(p<0)p=0; if(p>100)p=100; return std::to_string(p) + \"%\";' }\n"
    t = ["  - platform: homeassistant\n    id: ha_" + base + "_s\n    entity_id: " + e + "\n    on_value:\n"
         "      - lvgl.widget.update: { id: " + pwrid + ", bg_color: !lambda 'return x == \"on\" ? lv_color_hex(" + STRIP_PWR_ON + ") : lv_color_hex(" + STRIP_PWR_OFF + ");', border_color: !lambda 'return x == \"on\" ? lv_color_hex(" + STRIP_PWR_BD_ON + ") : lv_color_hex(" + STRIP_PWR_BD_OFF + ");' }\n"
         "      - lvgl.label.update: { id: " + pwic + ", text_color: !lambda 'return x == \"on\" ? lv_color_hex(" + STRIP_PWR_BD_ON + ") : lv_color_hex(" + STRIP_PWR_IC_OFF + ");' }\n"
         "      - lvgl.label.update: { id: " + pct + ", text: !lambda 'return x == \"on\" ? std::string(lv_label_get_text(id(" + pct + "))) : std::string(\"Off\");' }\n"
         "      - lvgl.widget.update: { id: " + fillid + ", bg_opa: !lambda 'return x == \"on\" ? 255 : 0;' }\n"]
    return s, t


def c_light(card, x, y, w, h, base):
    e = card.get("entity", "")
    gw, gh = card["w"], card["h"]
    icon = card_glyph(card, CARD_ICON.get(card["ck"], "\\U000F0335"))
    name = card.get("name", "Light")
    sldid, pct, fillid, pwrid = base + "_sld", base + "_pct", base + "_fill", base + "_pwr"
    gripid, pwic = base + "_grip", base + "_pi"
    bri = 74
    tog = ("homeassistant.action: { action: light.toggle, data: { entity_id: " + e + " } }") if e else "lvgl.page.show: page_home"

    if gh == 1:                                           # WIDE FILL STRIP (2x1..6x1)
        pbs, th = 48, 60
        pbx, pby = 12, (h - pbs) // 2
        tx, ty = 12 + pbs + 12, (h - th) // 2
        tw = w - tx - 12
        fw0 = int(tw * bri / 100)
        pwr = ("              - button:\n                  id: " + pwrid + "\n                  x: " + str(pbx) + "\n                  y: " + str(pby) + "\n                  width: 48\n                  height: 48\n"
               "                  bg_color: " + STRIP_PWR_ON + "\n                  border_color: " + STRIP_PWR_BD_ON + "\n                  border_width: 2\n                  radius: 24\n                  pad_all: 0\n                  scrollable: false\n"
               "                  widgets: [label: { id: " + pwic + ", text: \"\\U000F0425\", align: center, text_font: f_icon, text_color: " + STRIP_PWR_BD_ON + " }]\n"
               "                  on_click: [" + tog + "]\n")
        trk = ("              - obj:\n                  x: " + str(tx) + "\n                  y: " + str(ty) + "\n                  width: " + str(tw) + "\n                  height: " + str(th) + "\n"
               "                  bg_color: " + STRIP_TRACK + "\n                  radius: 11\n                  clip_corner: true\n                  border_width: 0\n                  pad_all: 0\n                  scrollable: false\n                  widgets:\n"
               "                    - obj: { id: " + fillid + ", x: 0, y: 0, width: " + str(fw0) + ", height: " + str(th) + ", bg_color: " + STRIP_GRAD_LO + ", bg_grad_color: " + STRIP_GRAD_HI + ", bg_grad_dir: HOR, border_width: 0, radius: 0, pad_all: 0, scrollable: false }\n"
               "                    - obj: { id: " + gripid + ", x: " + str(fw0 - 3) + ", y: 8, width: 6, height: " + str(th - 16) + ", bg_color: 0xFFFFFF, radius: 3, border_width: 0, pad_all: 0, scrollable: false }\n"
               "                    - label: { text: " + esc(name) + ", x: 16, align: left_mid, width: " + str(max(40, tw - 96)) + ", long_mode: dot, height: 40, text_font: f_body, text_color: 0xFFFFFF }\n"
               "                    - label: { id: " + pct + ", text: \"" + str(bri) + "%\", x: -16, align: right_mid, text_font: f_head, text_color: 0xFFFFFF }\n")
        if e:
            trk += ("                    - slider:\n                        id: " + sldid + "\n                        x: 0\n                        y: 0\n                        width: " + str(tw) + "\n                        height: " + str(th) + "\n"
                    "                        bg_opa: 0%\n                        min_value: 0\n                        max_value: 100\n                        value: " + str(bri) + "\n                        indicator: { bg_opa: 0% }\n                        knob: { bg_opa: 0% }\n"
                    "                        on_value:\n"
                    "                          - lvgl.widget.update: { id: " + fillid + ", width: !lambda 'return (int)(lv_slider_get_value(id(" + sldid + ")) * " + str(tw) + " / 100.0);' }\n"
                    "                          - lvgl.widget.update: { id: " + gripid + ", x: !lambda 'return (int)(lv_slider_get_value(id(" + sldid + ")) * " + str(tw) + " / 100.0) - 3;' }\n"
                    "                          - lvgl.label.update: { id: " + pct + ", text: !lambda 'return std::to_string((int) lv_slider_get_value(id(" + sldid + "))) + \"%\";' }\n"
                    "                        on_release:\n                          - homeassistant.action: { action: light.turn_on, data: { entity_id: " + e + ", brightness_pct: !lambda 'return (int) lv_slider_get_value(id(" + sldid + "));' } }\n")
        s, t = _strip_readbacks(base, e, sldid, fillid, gripid, pct, pwrid, pwic, "h", tw) if e else ([], [])
        return [_strip_card(x, y, w, h, 14, pwr + trk)], s, t

    if gw == 1 and gh >= 2:                               # TALL FILL STRIP (1x2..1x5)
        pbs = 44
        pbx, pby = (w - pbs) // 2, h - 12 - pbs
        tx, ty, tw = 12, 12, w - 24
        th = pby - 12 - 12
        fh0 = int(th * bri / 100)
        trk = ("              - obj:\n                  x: " + str(tx) + "\n                  y: " + str(ty) + "\n                  width: " + str(tw) + "\n                  height: " + str(th) + "\n"
               "                  bg_color: " + STRIP_TRACK + "\n                  radius: 11\n                  clip_corner: true\n                  border_width: 0\n                  pad_all: 0\n                  scrollable: false\n                  widgets:\n"
               "                    - obj: { id: " + fillid + ", x: 0, y: " + str(th - fh0) + ", width: " + str(tw) + ", height: " + str(fh0) + ", bg_color: " + STRIP_GRAD_LO + ", bg_grad_color: " + STRIP_GRAD_HI + ", bg_grad_dir: VER, border_width: 0, radius: 0, pad_all: 0, scrollable: false }\n"
               "                    - obj: { id: " + gripid + ", x: 8, y: " + str(th - fh0 - 3) + ", width: " + str(tw - 16) + ", height: 6, bg_color: 0xFFFFFF, radius: 3, border_width: 0, pad_all: 0, scrollable: false }\n"
               "                    - label: { text: \"" + icon + "\", x: 11, y: 11, text_font: f_icon, text_color: 0xFFFFFF }\n"
               "                    - label: { text: " + esc(name) + ", x: 11, y: " + str(th - 58) + ", width: " + str(tw - 22) + ", long_mode: dot, text_font: f_small, text_color: 0xFFFFFF }\n"
               "                    - label: { id: " + pct + ", text: \"" + str(bri) + "%\", x: 11, y: " + str(th - 40) + ", text_font: f_head, text_color: 0xFFFFFF }\n")
        if e:
            trk += ("                    - slider:\n                        id: " + sldid + "\n                        x: 0\n                        y: 0\n                        width: " + str(tw) + "\n                        height: " + str(th) + "\n"
                    "                        bg_opa: 0%\n                        min_value: 0\n                        max_value: 100\n                        value: " + str(bri) + "\n                        indicator: { bg_opa: 0% }\n                        knob: { bg_opa: 0% }\n"
                    "                        on_value:\n"
                    "                          - lvgl.widget.update: { id: " + fillid + ", height: !lambda 'return (int)(lv_slider_get_value(id(" + sldid + ")) * " + str(th) + " / 100.0);' }\n"
                    "                          - lvgl.widget.update: { id: " + fillid + ", y: !lambda 'return (int)(" + str(th) + " - lv_slider_get_value(id(" + sldid + ")) * " + str(th) + " / 100.0);' }\n"
                    "                          - lvgl.widget.update: { id: " + gripid + ", y: !lambda 'return (int)(" + str(th) + " - lv_slider_get_value(id(" + sldid + ")) * " + str(th) + " / 100.0) - 3;' }\n"
                    "                          - lvgl.label.update: { id: " + pct + ", text: !lambda 'return std::to_string((int) lv_slider_get_value(id(" + sldid + "))) + \"%\";' }\n"
                    "                        on_release:\n                          - homeassistant.action: { action: light.turn_on, data: { entity_id: " + e + ", brightness_pct: !lambda 'return (int) lv_slider_get_value(id(" + sldid + "));' } }\n")
        pwr = ("              - button:\n                  id: " + pwrid + "\n                  x: " + str(pbx) + "\n                  y: " + str(pby) + "\n                  width: 44\n                  height: 44\n"
               "                  bg_color: " + STRIP_PWR_ON + "\n                  border_color: " + STRIP_PWR_BD_ON + "\n                  border_width: 2\n                  radius: 22\n                  pad_all: 0\n                  scrollable: false\n"
               "                  widgets: [label: { id: " + pwic + ", text: \"\\U000F0425\", align: center, text_font: f_icon, text_color: " + STRIP_PWR_BD_ON + " }]\n"
               "                  on_click: [" + tog + "]\n")
        s, t = _strip_readbacks(base, e, sldid, fillid, gripid, pct, pwrid, pwic, "v", th) if e else ([], [])
        return [_strip_card(x, y, w, h, 16, trk + pwr)], s, t

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


# Sensor sub-types: name -> (MDI icon name, unit appended in the device snprintf
# format [%% = percent, ° = degree], decimals, accent color). The icon name
# is resolved + auto-embedded via glyph_for().
SENSOR_TYPES = {
    "temperature": ("thermometer",    "\\u00b0", 0, "0xF2685A"),
    "humidity":    ("water-percent",  "%%",      0, "0x4FA8F5"),
    "battery":     ("battery",        "%%",      0, "0x2ED5B8"),
    "percentage":  ("gauge",          "%%",      0, "0x2ED5B8"),   # CPU / load / memory
    "power":       ("flash",          "W",       0, "0xF2B84B"),
    "energy":      ("lightning-bolt", " kWh",    1, "0xF2B84B"),
    "illuminance": ("brightness-5",   " lx",     0, "0xF2B84B"),
    "pressure":    ("gauge-low",      " hPa",    0, "0x8FA6FF"),
    "voltage":     ("sine-wave",      "V",       1, "0xF2B84B"),
    "current":     ("current-ac",     "A",       2, "0xF2B84B"),
    "co2":         ("molecule-co2",   " ppm",    0, "0xF2B84B"),
    "speed":       ("speedometer",    "",        0, "0x2ED5B8"),
    "generic":     ("gauge",          "",        0, "0xF2685A"),
}

_STYPE_INFER = [("temp", "temperature"), ("\\u00b0", "temperature"), ("humid", "humidity"),
                ("batt", "battery"), ("cpu", "percentage"), ("mem", "percentage"),
                ("load", "percentage"), ("%", "percentage"), ("energy", "energy"),
                ("kwh", "energy"), ("power", "power"), ("watt", "power"),
                ("illum", "illuminance"), ("lux", "illuminance"), ("press", "pressure"),
                ("co2", "co2"), ("volt", "voltage"), ("current", "current"), ("speed", "speed")]


def _stype(card):
    st = card.get("stype")
    if st in SENSOR_TYPES:
        return st
    key = " ".join(str(card.get(k, "")) for k in ("device_class", "unit", "name", "entity")).lower()
    for sub, t in _STYPE_INFER:
        if sub in key:
            return t
    return "generic"


def _darken(hexstr, f=0.16):
    v = int(hexstr, 16)
    r, g, b = (v >> 16) & 255, (v >> 8) & 255, v & 255
    return "0x%06X" % ((int(r * f) << 16) | (int(g * f) << 8) | int(b * f))


def _rule_lines(rules, defcol):
    """C++ if-chain that sets `c` (accent) + `bg` (card tint) from thresholds; last match wins."""
    out = ""
    for rl in rules or []:
        if rl.get("val") is None:
            continue
        op = rl.get("op", ">=")
        op = op if op in (">=", ">", "<=", "<", "==") else ">="
        col = rl.get("color", defcol)
        out += ("          if (v %s %s) { c = lv_color_hex(%s); bg = lv_color_hex(%s); }\n"
                % (op, ("%g" % float(rl["val"])), col, _darken(col)))
    return out


def c_sensor(card, x, y, w, h, base):
    """Numeric sensor: type-driven icon + unit + decimals, optional threshold coloring."""
    e = card.get("entity", "")
    icon_name, unit, dec, defcol = SENSOR_TYPES.get(_stype(card), SENSOR_TYPES["generic"])
    glyph = card_glyph(card, glyph_for(icon_name))
    vid, oid = base + "_v", base + "_c"
    rules = card.get("rules")
    inner = ic(card["ck"], color=defcol, glyph=glyph)
    inner += lbl("--", 14, -32, "f_title", "0xF3F5F8", wid=vid, align="bottom_left")
    inner += lbl(card.get("name", "Sensor"), 14, -12, "f_small", "0x868CA0", align="bottom_left")
    ts = []
    if e:
        fmt = "%." + str(dec) + "f" + unit
        color_body = ""
        if rules:
            color_body = ("          lv_color_t c = lv_color_hex(" + defcol + "); lv_color_t bg = lv_color_hex(0x161B24);\n"
                          + _rule_lines(rules, defcol)
                          + "          lv_obj_set_style_text_color(L, c, 0); lv_obj_set_style_bg_color(id(" + oid + "), bg, 0);\n")
        body = ("          lv_obj_t* L = id(" + vid + ");\n"
                "          if (x.empty() || x == \"unknown\" || x == \"unavailable\") { lv_label_set_text(L, \"--\"); return; }\n"
                "          float v = atof(x.c_str());\n"
                "          char b[24]; snprintf(b, sizeof(b), \"" + fmt + "\", v); lv_label_set_text(L, b);\n"
                + color_body)
        ts.append("  - platform: homeassistant\n    id: ha_" + vid + "\n    entity_id: " + e
                  + "\n    on_value:\n      - lambda: |-\n" + body)
    return [card_obj(x, y, w, h, inner, oid=(oid if rules else None))], [], ts


# Binary sensor / presence: device-class-aware label + icon, and the WHOLE card
# lights up when active / dims when inactive (e.g. occupancy).
BINARY_LABELS = {  # device_class -> (active text, inactive text, active color)
    "occupancy": ("Occupied", "Empty", "0x2ED5B8"), "motion": ("Motion", "Clear", "0x2ED5B8"),
    "presence": ("Present", "Away", "0x2ED5B8"), "door": ("Open", "Closed", "0xF2B84B"),
    "window": ("Open", "Closed", "0xF2B84B"), "garage_door": ("Open", "Closed", "0xF2B84B"),
    "moisture": ("Wet", "Dry", "0x4FA8F5"), "smoke": ("Smoke", "Clear", "0xF2685A"),
    "gas": ("Gas", "Clear", "0xF2685A"), "connectivity": ("Online", "Offline", "0x2ED5B8"),
    "problem": ("Problem", "OK", "0xF2685A"), "sound": ("Sound", "Quiet", "0x2ED5B8"),
}
BINARY_ICON = {
    "occupancy": "account", "motion": "motion-sensor", "presence": "home-account",
    "door": "door", "window": "window-closed-variant", "garage_door": "garage",
    "moisture": "water", "smoke": "smoke-detector", "gas": "gas-cylinder",
    "connectivity": "wifi", "problem": "alert-circle", "sound": "ear-hearing",
}


def c_binary(card, x, y, w, h, base):
    e = card.get("entity", "")
    if card.get("ck") == "person":
        atxt, itxt, acol0, icon_name, active = "Home", "Away", "0x2ED5B8", "home-account", 'x == "home"'
    else:
        dc = (card.get("device_class") or card.get("bclass") or "").lower()
        atxt, itxt, acol0 = BINARY_LABELS.get(dc, ("Active", "Clear", "0x4FA8F5"))
        icon_name = BINARY_ICON.get(dc, "motion-sensor")
        active = 'x == "on"'
    acol = card.get("activeColor", acol0)
    glyph = card_glyph(card, glyph_for(icon_name))
    vid, iid, oid = base + "_v", base + "_i", base + "_c"
    inner = ic(card["ck"], color=acol, glyph=glyph, wid=iid)
    inner += lbl(itxt, 14, -32, "f_title", "0xF3F5F8", wid=vid, align="bottom_left")
    inner += lbl(card.get("name", "Sensor"), 14, -12, "f_small", "0x868CA0", align="bottom_left")
    ts = []
    if e:
        lit = _darken(acol, 0.22)
        body = ("          bool on = (" + active + ");\n"
                "          lv_obj_set_style_bg_color(id(" + oid + "), on ? lv_color_hex(" + lit + ") : lv_color_hex(0x0B0C10), 0);\n"
                "          lv_obj_set_style_text_color(id(" + iid + "), on ? lv_color_hex(" + acol + ") : lv_color_hex(0x4A5160), 0);\n"
                "          lv_obj_set_style_text_color(id(" + vid + "), on ? lv_color_hex(" + acol + ") : lv_color_hex(0x6B7280), 0);\n"
                "          lv_label_set_text(id(" + vid + "), on ? \"" + atxt + "\" : \"" + itxt + "\");\n")
        ts.append("  - platform: homeassistant\n    id: ha_" + vid + "\n    entity_id: " + e
                  + "\n    on_value:\n      - lambda: |-\n" + body)
    return [card_obj(x, y, w, h, inner, oid=oid)], [], ts


def c_state(card, x, y, w, h, base):
    """Raw-state text card (vacuum / alarm): unchanged legacy behavior."""
    e = card.get("entity", "")
    vid = base + "_v"
    col = "0xF2685A" if card.get("ck") == "alarm" else "0x4FA8F5"
    inner = ic(card["ck"], color=col, glyph=card_glyph(card, None))
    inner += lbl("--", 14, -32, "f_title", "0xF3F5F8", wid=vid, align="bottom_left")
    inner += lbl(card.get("name", "Sensor"), 14, -12, "f_small", "0x868CA0", align="bottom_left")
    ts = []
    if e:
        ts.append(
            "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda 'return x;' }\n" % (vid, e, vid))
    return [card_obj(x, y, w, h, inner)], [], ts


# One column of the sparkline (a thin vertical lv_bar): id, x, y, width, height, accent.
BAR_TMPL = (
    "              - bar:\n"
    "                  id: %s\n"
    "                  x: %d\n                  y: %d\n                  width: %d\n                  height: %d\n"
    "                  min_value: 0\n                  max_value: 100\n                  value: 0\n"
    "                  bg_color: 0x1A1F2A\n                  bg_opa: 55%%\n                  radius: 2\n                  pad_all: 0\n                  scrollable: false\n"
    "                  indicator:\n                    bg_color: %s\n                    radius: 2\n"
)


def c_chart(card, x, y, w, h, base):
    """Live trend chart: N vertical bars the panel samples from the sensor over time
    (a scrolling column sparkline). ESPHome's LVGL has no `chart`/`line` widget, so
    we build it from `bar`s (LV_USE_BAR) + a static ring buffer, updated on_value."""
    e = card.get("entity", "")
    st = _stype(card)
    icon_name, unit, dec, defcol = SENSOR_TYPES.get(st, SENSOR_TYPES["generic"])
    glyph = card_glyph(card, glyph_for(icon_name))
    vid = base + "_v"
    pad, ctop = 14, 84
    cw, ch = w - 2 * pad, h - 84 - pad
    n = max(8, min(40, cw // 10))
    pitch = cw / float(n)
    bw = max(3, int(pitch) - 2)
    inner = ic(card["ck"], color=defcol, glyph=glyph)
    inner += lbl(card.get("name", "Sensor"), 48, 18, "f_small", "0x868CA0", width=w - 62, long="dot", height=20)
    inner += lbl("--", 14, 40, "f_head", defcol, wid=vid, height=34, width=w - 28)
    bar_ids = []
    for i in range(n):
        bid = base + "_b%d" % i
        bar_ids.append(bid)
        inner += BAR_TMPL % (bid, pad + int(round(i * pitch)), ctop, bw, ch, defcol)
    ts = []
    if e:
        fmt = "%." + str(dec) + "f" + unit
        fixed = st in ("humidity", "battery", "percentage")
        ptr = ", ".join("id(%s)" % b for b in bar_ids)
        N, N1 = str(n), str(n - 1)
        body = (
            "          static float buf[" + N + "]; static int filled = 0;\n"
            "          if (x.empty() || x == \"unknown\" || x == \"unavailable\") return;\n"
            "          float v = atof(x.c_str());\n"
            "          for (int i = 0; i < " + N1 + "; i++) buf[i] = buf[i+1];\n"
            "          buf[" + N1 + "] = v; if (filled < " + N + ") filled++;\n"
            "          float mn = v, mx = v;\n"
            "          for (int i = " + N + " - filled; i < " + N + "; i++) { if (buf[i] < mn) mn = buf[i]; if (buf[i] > mx) mx = buf[i]; }\n"
            + ("          mn = 0; mx = 100;\n" if fixed else "")
            + "          float span = mx - mn; if (span < 0.001f) span = 1;\n"
            "          lv_obj_t* bars[" + N + "] = { " + ptr + " };\n"
            "          for (int i = 0; i < " + N + "; i++) {\n"
            "            int32_t p = (i < " + N + " - filled) ? 0 : (int32_t)((buf[i] - mn) / span * 100.0f);\n"
            "            if (p < 0) p = 0; if (p > 100) p = 100;\n"
            "            lv_bar_set_value(bars[i], p, LV_ANIM_OFF);\n"
            "          }\n"
            "          char b[24]; snprintf(b, sizeof(b), \"" + fmt + "\", v); lv_label_set_text(id(" + vid + "), b);\n")
        ts.append("  - platform: homeassistant\n    id: ha_" + vid + "\n    entity_id: " + e
                  + "\n    on_value:\n      - lambda: |-\n" + body)
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


# Album art plumbing: c_media/c_spotify_art register (size, image_widget_id, entity)
# here; assemble() emits one entity_picture readback per entity whose action queues
# id(cam_stream).fetch_still(url, widget, size, size) for each of that entity's art
# widgets — download + HW JPEG decode + PPA scale run on the mjpeg_stream worker
# task (never the main loop), latest-wins per widget. Art image widgets start
# hidden over the ss_image placeholder src; the component unhides each one when
# its first still is applied. Host builds skip art (no mjpeg_stream component).
ART_IMAGES = []
ART_ENABLED = True
EXTRA_CLOCKS = []   # (label_id, kind) live-clock bindings contributed by card emitters
# Live camera plumbing: the FIRST camera card registers (entity, inner_w, inner_h,
# base) here (gen_pages appends the hosting page id); assemble() emits the
# mjpeg_stream instance + pill on_state + entity_picture readback + fullscreen page.
# One entry max — the device supports 2 concurrent streams, we ship 1.
CAM_CARDS = []


def c_media(card, x, y, w, h, base):
    """Media / Spotify / Sonos now-playing card. Volume slider on every card >= 3 cells (image 1)."""
    e = card.get("entity", "")
    ck = card["ck"]
    tid = base + "_t"
    aid = base + "_a"
    sld = base + "_vol"
    ppid = base + "_pp"                                   # play/pause button glyph (updated from media state)
    gw, gh = card["w"], card["h"]
    cells = gw * gh
    has_vol = cells >= 3
    nowplaying = gh >= 3 and gw >= 2
    prev_g, play_g, next_g, vol_g = "\\U000F04AE", "\\U000F03E4", "\\U000F04AD", "\\U000F057E"  # pause glyph (demo = playing)
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
            # src is a required schema key: point at ss_image and start hidden
            # (camera-widget pattern) — the stills channel unhides on first art.
            s += ("              - image: { id: %s, x: %d, y: %d, src: ss_image, hidden: true }\n" % (img_id, ix, iy))
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
        s += btn(sx + small + gap, ty, big, big, play_g, plpz, bg="0x2ED5B8", color="0x06231D", radius=big // 2, font="f_icon", lid=ppid)
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
        # media_player state: when it is NOT actively playing, force the honest
        # "Nothing playing" text (media_title/artist attrs are absent when idle,
        # so their readbacks never fire to clear the last-played track).
        ts.append("  - platform: homeassistant\n    id: ha_%s_st\n    entity_id: %s\n    on_value:\n"
                  "      - lvgl.label.update: { id: %s, text: !lambda 'return (x == \"playing\" || x == \"paused\" || x == \"buffering\") ? std::string(lv_label_get_text(id(%s))) : std::string(\"Nothing playing\");' }\n"
                  "      - lvgl.label.update: { id: %s, text: !lambda 'return (x == \"playing\" || x == \"paused\" || x == \"buffering\") ? std::string(lv_label_get_text(id(%s))) : std::string(\"\");' }\n"
                  "      - lvgl.label.update: { id: %s, text: !lambda 'return x == \"playing\" ? std::string(\"\\U000F03E4\") : std::string(\"\\U000F040A\");' }\n"
                  % (base, e, tid, tid, aid, aid, ppid))
        if has_vol:
            ts.append("  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    attribute: volume_level\n    on_value:\n"
                      "      - lvgl.slider.update: { id: %s, value: !lambda 'return (int)(atof(x.c_str()) * 100);' }\n" % (sld, e, sld))

    inner = ""
    # ---- 1x1: art + title + play ----
    if gw == 1 and gh == 1:
        inner += art(14, 14, 40, 40)
        inner += lbl("Nothing playing",62, 18, "f_body", "0xF3F5F8", wid=tid, width=w - 74, long="dot", height=20)
        inner += lbl("",62, 44, "f_small", "0x868CA0", wid=aid, width=w - 74, long="dot", height=16)
        inner += btn(w - 46, h - 46, 34, 34, play_g, plpz, bg="0x2ED5B8", color="0x06231D", radius=17, font="f_icon", lid=ppid)
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
        inner += btn(px, cy - bw // 2, bw, bw, play_g, plpz, bg="0x2ED5B8", color="0x06231D", radius=bw // 2, font="f_icon", lid=ppid)
        inner += btn(nx, cy - bw // 2, bw, bw, next_g, nxt, radius=bw // 2, font="f_icon")
        if has_vol:
            txtw = 108
            inner += lbl("Nothing playing",82, 26, "f_body", "0xF3F5F8", wid=tid, width=txtw, long="dot", height=20)
            inner += lbl("",82, 50, "f_small", "0x868CA0", wid=aid, width=txtw if has_vol else tw, long="dot", height=16)
            vsx = 82 + txtw + 12
            inner += lbl(vol_g, vsx, cy - 6, "f_icon", "0x868CA0")
            svx = vsx + 30
            inner += vol_slider_yaml(sld, svx, cy - 4, (vx - 12) - svx, 55, e)
        else:
            tw = (vx - 12) - 82
            inner += lbl("Nothing playing",82, 26, "f_body", "0xF3F5F8", wid=tid, width=tw, long="dot", height=20)
            inner += lbl("",82, 50, "f_small", "0x868CA0", wid=aid, width=txtw if has_vol else tw, long="dot", height=16)
        return [card_obj(x, y, w, h, inner)], [], ts
    # ---- w==1 tall narrow (1x2, 1x3): art on top ----
    if gw == 1:
        asz = w - 28
        arth = min(asz, h - 118)
        inner += art(14, 14, asz, arth)
        ty0 = 14 + arth + 8
        inner += lbl(subtxt, 14, ty0, "f_small", "0x2ED5B8", width=w - 28, long="dot")
        inner += lbl("Nothing playing",14, ty0 + 16, "f_body", "0xF3F5F8", wid=tid, width=w - 28, long="dot")
        inner += lbl("",14, ty0 + 38, "f_small", "0x868CA0", wid=aid, width=w - 28, long="dot", height=16)
        inner += transport_center(ty0 + 56)
        if has_vol:
            inner += vol_slider(h - 34)
        return [card_obj(x, y, w, h, inner)], [], ts
    # ---- now-playing (>=2 wide, >=3 tall): big art + progress ----
    if nowplaying:
        if gw >= 3:                                    # art beside the title block
            inner += art(14, 14, 110, 110, real=108)
            inner += lbl(subtxt, 136, 20, "f_small", "0x2ED5B8", width=w - 150, long="dot", height=16)
            inner += lbl("Nothing playing",136, 40, "f_track", "0xF3F5F8", wid=tid, width=w - 150, long="dot", height=32)
            inner += lbl("",136, 86, "f_body", "0x868CA0", wid=aid, width=w - 150, long="dot", height=18)
            py = 152
            tport = py + 40
        else:                                          # narrow: art on top, full-width title
            inner += art(14, 14, w - 28, 108, real=108)
            inner += lbl(subtxt, 14, 130, "f_small", "0x2ED5B8", width=w - 28, long="dot", height=16)
            inner += lbl("Nothing playing",14, 146, "f_track", "0xF3F5F8", wid=tid, width=w - 28, long="dot", height=32)
            inner += lbl("",14, 180, "f_body", "0x868CA0", wid=aid, width=w - 28, long="dot", height=18)
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
    inner += lbl("Nothing playing", 82, 36, "f_title", "0xF3F5F8", wid=tid, width=w - 96, long="dot", height=28)
    inner += lbl("",82, 68, "f_small", "0x868CA0", wid=aid, width=w - 96, long="dot", height=16)
    inner += transport_center(h - 108)
    inner += vol_slider(h - 40)
    return [card_obj(x, y, w, h, inner)], [], ts


def c_fan(card, x, y, w, h, base):
    e = card.get("entity", "")
    gw, gh = card["w"], card["h"]
    if gw * gh <= 2:                                  # small: icon + centered label, card lights when on
        act = ha("homeassistant.toggle", e) if e else "lvgl.page.show: page_home"   # works for fan.* or switch.* fans
        oid, iid, nlid, acc = base + "_c", base + "_i", base + "_n", "0x2ED5B8"
        glyph = card_glyph(card, CARD_ICON.get(card["ck"], "\\U000F0210"))
        inner = lbl(glyph, 0, -20, "f_icon", acc, wid=iid, align="center")
        inner += lbl(card.get("name", "Fan"), 0, 24, "f_body", acc, wid=nlid, align="center", width=w - 24, text_align="center", long="dot")
        ts = []
        if e:  # real fan state: whole card lights up when on / dims when off
            lit = _darken(acc, 0.22)
            ts.append(
                "  - platform: homeassistant\n    id: ha_%s\n    entity_id: %s\n    on_value:\n"
                "      - lvgl.widget.update: { id: %s, bg_color: !lambda 'return x == \"on\" ? lv_color_hex(%s) : lv_color_hex(0x0E1116);' }\n"
                "      - lvgl.label.update: { id: %s, text_color: !lambda 'return x == \"on\" ? lv_color_hex(%s) : lv_color_hex(0x4A5160);' }\n"
                "      - lvgl.label.update: { id: %s, text_color: !lambda 'return x == \"on\" ? lv_color_hex(%s) : lv_color_hex(0x6B7280);' }\n"
                % (base, e, oid, lit, iid, acc, nlid, acc))
        return [card_obj(x, y, w, h, inner, act, oid=oid)], [], ts
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


# HA weather condition (entity STATE) -> (f_wxicon glyph, label). Drives the hero
# icon + condition text live, so the card isn't stuck on the pre-baked night glyph.
WX_COND = [("clear-night", "\\U000F0594", "Clear"), ("sunny", "\\U000F0599", "Sunny"),
           ("partlycloudy", "\\U000F0595", "Partly Cloudy"), ("cloudy", "\\U000F0590", "Cloudy"),
           ("pouring", "\\U000F0596", "Pouring"), ("rainy", "\\U000F0597", "Rain"),
           ("snowy", "\\U000F0598", "Snow"), ("snowy-rainy", "\\U000F067F", "Sleet"),
           ("fog", "\\U000F0591", "Fog"), ("hail", "\\U000F0592", "Hail"),
           ("lightning", "\\U000F0593", "Lightning"), ("lightning-rainy", "\\U000F067E", "Storms"),
           ("windy", "\\U000F059D", "Windy"), ("windy-variant", "\\U000F059D", "Windy"),
           ("exceptional", "\\U000F0026", "Alert")]


def _wx_cond_readback(base, e, hid, cid):
    """Map the weather entity state -> hero glyph + condition label (device build)."""
    gifs = "".join("if (x == \"%s\") return std::string(\"%s\"); " % (c, g) for c, g, _ in WX_COND)
    tifs = "".join("if (x == \"%s\") return std::string(\"%s\"); " % (c, l) for c, _, l in WX_COND)
    return ("  - platform: homeassistant\n    id: ha_%s_cond\n    entity_id: %s\n    on_value:\n"
            "      - lvgl.label.update: { id: %s, text: !lambda '%sreturn std::string(\"\\U000F0599\");' }\n"
            "      - lvgl.label.update: { id: %s, text: !lambda '%sreturn x;' }\n"
            % (base, e, hid, gifs, cid, tifs))


def c_weather(card, x, y, w, h, base):
    e = card.get("entity", "")
    tid, cid, hid = base + "_t", base + "_c", base + "_h"
    if w < 620 or h < 400:                               # compact: icon + temp + condition
        inner = "              - label: { id: " + hid + ", text: \"\\U000F0599\", x: 14, y: 14, text_font: f_wxicon, text_color: 0xF2B84B }\n"
        inner += lbl("72\\u00B0", -16, 20, "f_display", "0xF3F5F8", wid=tid, align="top_right")
        inner += lbl("Sunny", 14, -12, "f_body", "0x2ED5B8", wid=cid, align="bottom_left")
        return [card_obj(x, y, w, h, inner)], ([_wx_temp_readback(base, e, tid)] if e else []), ([_wx_cond_readback(base, e, hid, cid)] if e else [])
    # large: full forecast (hero + hourly + daily + stats), values pre-baked
    pad = 14
    hh = 128
    inner = ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: 0x141F38, bg_grad_color: 0x0E1524, bg_grad_dir: VER, border_width: 0, radius: 16, pad_all: 0, scrollable: false }\n" % (pad, pad, w - 2 * pad, hh))
    inner += "              - label: { id: %s, text: \"\\U000F0599\", x: %d, y: %d, text_font: f_wxicon, text_color: 0xF2B84B }\n" % (hid, pad + 26, pad + 26)
    inner += lbl("72\\u00B0", pad + 150, pad + 10, "f_display", "0xF3F5F8", wid=tid)
    inner += lbl("Sunny", pad + 152, pad + 72, "f_body", "0xC2C7D2", wid=cid, width=340, long="dot", height=20)
    inner += lbl("Home", -(pad + 4), pad + 34, "f_title", "0xF3F5F8", align="top_right")
    wtime = base + "_clk"
    inner += lbl("Friday \\u00B7 9:41 PM", -(pad + 4), pad + 70, "f_small", "0x868CA0", wid=wtime, align="top_right")
    EXTRA_CLOCKS.append((wtime, "dow_time"))            # live device time (fixes "stuck 9pm")
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
    return [card_obj(x, y, w, h, inner)], ([_wx_temp_readback(base, e, tid)] if e else []), ([_wx_cond_readback(base, e, hid, cid)] if e else [])


# --- Weather decomposed into selectable component cards (share the weather entity;
# current temp + condition are live, forecast/stats are pre-baked demo like the 6x5) ---
def c_wx_current(card, x, y, w, h, base):
    """Current conditions — mirrors page_weather: amber condition icon (left), the
    condition as the big title + 'Outdoor conditions', and the temp with H/L on the
    right. Temp + condition bind live to the weather entity."""
    e = card.get("entity", "")
    tid, cid, hid = base + "_t", base + "_c", base + "_h"
    cy = h // 2
    inner = "              - label: { id: %s, text: \"\\U000F0599\", x: 16, align: left_mid, text_font: f_wxicon, text_color: 0xFFCE54 }\n" % hid
    inner += lbl("Sunny", 118, cy - 34, "f_head", "0xEEF0F6", wid=cid, width=max(60, w - 250), long="dot", height=40)
    inner += lbl("Outdoor conditions", 120, cy + 12, "f_small", "0x8A8F9E")
    inner += lbl("72\\u00B0", -16, cy - 44, "f_display", "0xEEF0F6", wid=tid, align="top_right")
    inner += lbl("H 78\\u00B0", -16, cy + 6, "f_body", "0xC8CCD6", wid=base + "_hi", align="top_right")
    inner += lbl("L 61\\u00B0", -16, cy + 32, "f_small", "0x8A8F9E", wid=base + "_lo", align="top_right")
    ts_s = [_wx_temp_readback(base, e, tid)] if e else []
    ts_t = [_wx_cond_readback(base, e, hid, cid)] if e else []
    return [card_obj(x, y, w, h, inner)], ts_s, ts_t


def c_wx_hourly(card, x, y, w, h, base):
    """Hourly forecast strip (pre-baked demo forecast)."""
    pad, gap = 12, 8
    n = len(WX_HOURLY)
    tw = (w - 2 * pad - (n - 1) * gap) // n
    inner = ""
    for i, (hl, g, tp) in enumerate(WX_HOURLY):
        hx = pad + i * (tw + gap)
        bg = "0x11201C" if i == 0 else "0x161B24"
        inner += ("              - obj: { x: %d, y: %d, width: %d, height: %d, bg_color: %s, border_width: 0, radius: 12, pad_all: 0, scrollable: false, widgets: ["
                  "label: { text: %s, align: top_mid, y: 10, text_font: f_small, text_color: 0x868CA0 }, "
                  "label: { text: \"%s\", align: center, text_font: f_icon, text_color: 0x8FA6FF }, "
                  "label: { text: \"%s\\u00B0\", align: bottom_mid, y: -10, text_font: f_body, text_color: 0xF3F5F8 }] }\n"
                  % (hx, pad, tw, h - 2 * pad, bg, esc(hl), g, tp))
    return [card_obj(x, y, w, h, inner)], [], []


def c_wx_daily(card, x, y, w, h, base):
    """Weekly forecast — mirrors page_weather: day / amber condition icon / high / low
    as centered columns across the card (pre-baked demo forecast)."""
    n = len(WX_DAILY)
    pad = 12
    pitch = (w - 2 * pad) // n
    top = pad + 4
    inner = ""
    for i, (dn, g, lo, hi) in enumerate(WX_DAILY):
        cx = pad + i * pitch
        inner += "              - label: { text: %s, x: %d, y: %d, width: %d, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }\n" % (esc(dn), cx, top, pitch)
        inner += "              - label: { text: \"%s\", x: %d, y: %d, width: %d, text_align: center, text_font: f_icon, text_color: 0xFFCE54 }\n" % (g, cx, top + 30, pitch)
        inner += "              - label: { text: \"%s\\u00B0\", x: %d, y: %d, width: %d, text_align: center, text_font: f_body, text_color: 0xEEF0F6 }\n" % (hi, cx, top + 70, pitch)
        inner += "              - label: { text: \"%s\\u00B0\", x: %d, y: %d, width: %d, text_align: center, text_font: f_small, text_color: 0x8A8F9E }\n" % (lo, cx, top + 96, pitch)
    return [card_obj(x, y, w, h, inner)], [], []


def c_wx_stats(card, x, y, w, h, base):
    """Weather stats — mirrors page_weather: Air pressure / Humidity / Wind speed rows
    (label left, value right). Values bind live to the weather entity."""
    e = card.get("entity", "")
    pid, huid, wiid = base + "_pr", base + "_hu", base + "_wi"
    rows = [("Air pressure", "30.05 inHg", pid), ("Humidity", "44%", huid), ("Wind speed", "7 mph NW", wiid)]
    pad = 14
    rh = (h - 2 * pad) // len(rows)
    inner = ""
    for i, (lab, val, vid) in enumerate(rows):
        ry = pad + i * rh + (rh - 20) // 2
        inner += lbl(lab, 14, ry, "f_body", "0xC8CCD6")
        inner += lbl(val, -14, ry, "f_body", "0xEEF0F6", wid=vid, align="top_right")
    ss = []
    if e:
        ss.append("  - platform: homeassistant\n    id: ha_%s_pr\n    entity_id: %s\n    attribute: pressure\n    on_value:\n      - lvgl.label.update: { id: %s, text: !lambda 'char b[20]; snprintf(b, sizeof(b), \"%%.2f inHg\", x); return std::string(b);' }\n" % (base, e, pid))
        ss.append("  - platform: homeassistant\n    id: ha_%s_hu\n    entity_id: %s\n    attribute: humidity\n    on_value:\n      - lvgl.label.update: { id: %s, text: !lambda 'char b[8]; snprintf(b, sizeof(b), \"%%.0f%%%%\", x); return std::string(b);' }\n" % (base, e, huid))
        ss.append("  - platform: homeassistant\n    id: ha_%s_wi\n    entity_id: %s\n    attribute: wind_speed\n    on_value:\n      - lvgl.label.update: { id: %s, text: !lambda 'char b[16]; snprintf(b, sizeof(b), \"%%.0f mph\", x); return std::string(b);' }\n" % (base, e, wiid))
    return [card_obj(x, y, w, h, inner)], ss, []


def c_camera(card, x, y, w, h, base):
    """Live camera card (aurora.yaml btn_cam_card pattern): a tappable black inner
    button hosting the mjpeg_stream target-0 image + a status pill driven by the
    stream's on_state. Tap -> generated page_camera_full. Only the FIRST camera
    card goes live; extras and host builds (SDL has no mjpeg_stream component)
    keep the static placeholder."""
    e = card.get("entity", "")
    iw, ih = w - 16, h - 16
    if e and ART_ENABLED and not CAM_CARDS:
        CAM_CARDS.append((e, iw, ih, base))
        # src is a required schema key: point at ss_image (a stale screensaver
        # photo would show through, so start hidden — the on_state LIVE branch
        # unhides once mjpeg_stream has re-pointed the widget at a real frame).
        inner = ("              - button:\n"
                 "                  x: 8\n                  y: 8\n                  width: %d\n                  height: %d\n"
                 "                  bg_color: 0x000000\n                  radius: 12\n                  clip_corner: true\n"
                 "                  pad_all: 0\n                  scrollable: false\n"
                 "                  widgets:\n"
                 "                    - image: { id: %s_cam, src: ss_image, align: center, hidden: true }\n"
                 "                    - obj: { id: %s_pill, x: 12, y: %d, width: 78, height: 24, bg_color: 0x2A2F3A, radius: 8, "
                 "border_width: 0, pad_all: 0, scrollable: false, clickable: false, widgets: ["
                 "label: { id: %s_pill_lbl, text: \"...\", align: center, text_font: f_micro, text_color: 0xFFFFFF } ] }\n"
                 "                  on_click: [lvgl.page.show: page_camera_full]\n"
                 % (iw, ih, base, base, ih - 36, base))
        inner += lbl(card.get("name", "Camera"), 20, -14, "f_body", "0xF3F5F8", align="bottom_left")
        return [card_obj(x, y, w, h, inner)], [], []
    # ---- static placeholder (extra cameras / host build / no entity) ----
    inner = ""
    if e and ART_ENABLED and CAM_CARDS:
        inner += "              # %s: multi-camera live view not yet supported (1 stream shipped) — static placeholder\n" % e
    inner += ("              - obj: { x: 8, y: 8, width: %d, height: %d, bg_color: 0x10141C, "
              "border_width: 0, radius: 12, pad_all: 0, scrollable: false }\n" % (iw, ih))
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
    "Paramount+": ("0x0064FF", "P"), "STARZ": ("0x000000", "SZ"),
    "ESPN": ("0xD50A0A", "E"), "Prime": ("0x00A8E1", "P"),
}
TV_APPS_DEFAULT = ["Netflix", "YouTube", "YouTube TV", "Disney+", "Hulu"]
TV_APPS_MAX = 8
TV_SOURCES = ["HDMI 1", "Apple TV", "Roku", "Cable"]


def c_tvremote(card, x, y, w, h, base):
    """Full LG remote, matching the web `remote` card. Wide cards (>=6 cells)
    get the apps sidebar; large cards get source chips + VOL/d-pad/CH + the full
    transport bar; small cards fall back to d-pad + 3 transport buttons."""
    e = card.get("entity", "")
    inner = ""
    ts = []                                              # source-highlight readback (rich+sidebar)
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
        ups = ""                                          # live source-highlight lines
        for i, nm in enumerate(apps):
            col, ltr = APP_CATALOG.get(nm, ("0x555555", (nm[:1] or "?").upper()))
            ty = ay + i * (ah + 8)
            appid = "%s_app%d" % (base, i)
            inner += (
                "              - button:\n"
                "                  id: %s\n"
                "                  x: 14\n                  y: %d\n                  width: 152\n                  height: %d\n"
                "                  bg_color: 0x10141C\n                  radius: 14\n                  pad_all: 0\n                  scrollable: false\n"
                "                  widgets:\n"
                "                    - obj: { x: 14, align: left_mid, width: 32, height: 32, bg_color: %s, radius: 8, pad_all: 0, scrollable: false, widgets: [label: { text: \"%s\", align: center, text_font: f_body, text_color: 0xFFFFFF }] }\n"
                "                    - label: { text: \"%s\", x: 56, align: left_mid, width: 88, long_mode: dot, text_font: f_body, text_color: 0xFFFFFF }\n"
                "                  on_click: [%s]\n"
                % (appid, ty, ah, col, ltr, nm, _src(e, nm)))
            if e:                                         # highlight this tile when it IS the TV's current source
                ups += ("      - lvgl.widget.update: { id: " + appid +
                        ", bg_color: !lambda 'return x == \"" + nm + "\" ? lv_color_hex(" + col + ") : lv_color_hex(0x10141C);' }\n")
        if e and ups:
            ts.append("  - platform: homeassistant\n    id: ha_" + base + "_src\n    entity_id: " + e +
                      "\n    attribute: source\n    on_value:\n" + ups)
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
    return [card_obj(x, y, w, h, inner)], [], ts


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
              "                  widgets: [label: { text: \"%s\", align: center, text_font: f_icon, text_color: 0x1DB954 }]\n"
              "                  on_click: [homeassistant.action: { action: script.aurora_spotify_refresh_playlists }]\n" % (w - 54, glyph_for("reload")))
    inner += dropdown
    if not compact:
        inner += lbl("Pick a playlist to load its songs", 14, dd_y + 56, "f_small", "0x5D6470", width=w - 28, long="dot")
    ts = ["  - platform: homeassistant\n    id: ha_" + base + "_pn\n    entity_id: sensor.aurora_spotify_playlists\n    attribute: names\n    on_value:\n"
          "      - lambda: 'lv_dropdown_set_options(id(" + ddid + ")->obj, x.c_str());'\n",
          "  - platform: homeassistant\n    id: ha_" + base + "_pu\n    entity_id: sensor.aurora_spotify_playlists\n    attribute: uris\n    on_value:\n"
          "      - lambda: 'id(g_pl_uris) = x;'\n"]
    return [card_obj(x, y, w, h, inner)], [], ts


SPOT_MAX_TRACKS = 50


def _card_rows(card, default, hi, lo=4):
    """Per-card pre-built row count (builder 'rows' option), clamped. Fewer rows =
    lighter generated firmware (see the builder capacity meter); more rows = longer
    lists. lo floor keeps a usable list; hi caps it (tracks: SpotifyPlus fetch = 50)."""
    try:
        n = int(card.get("rows"))
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def c_spot_tracks(card, x, y, w, h, base):
    """Spotify song list: a scrolling column of tap-to-play rows bound to
    sensor.aurora_spotify_tracks (names, one "Track — Artist" per line). Tapping
    row i plays position i within the loaded playlist (g_spot_ctx) via the
    aurora_spotify_play_track script. Rows are pre-built and shown/hidden by the
    populate lambda; the count is the per-card 'rows' option (default/cap 50)."""
    n = _card_rows(card, SPOT_MAX_TRACKS, SPOT_MAX_TRACKS)
    inner = ic("spotify_tracks", color="0x1DB954")
    inner += lbl("TRACKS \\u00B7 TAP TO PLAY", 50, 18, "f_micro", "0x868CA0")
    inner += ("              - button:\n                  x: " + str(w - 54) + "\n                  y: 10\n                  width: 40\n                  height: 30\n"
              "                  bg_color: 0x161B24\n                  radius: 10\n                  pad_all: 0\n                  scrollable: false\n"
              "                  widgets: [label: { text: \"" + glyph_for("reload") + "\", align: center, text_font: f_icon, text_color: 0x1DB954 }]\n"
              "                  on_click:\n                    - if:\n                        condition:\n                          lambda: 'return !id(g_spot_ctx).empty();'\n"
              "                        then:\n                          - homeassistant.action:\n                              action: script.aurora_spotify_load_playlist\n                              data:\n                                playlist_uri: !lambda 'return id(g_spot_ctx);'\n")
    rh, gap = 44, 6
    list_y = 48
    list_h = h - list_y - 12
    # scrollable list container (rows overflow -> swipe to scroll)
    inner += ("              - obj:\n                  id: %s_lst\n                  x: 14\n                  y: %d\n                  width: %d\n                  height: %d\n"
              "                  bg_opa: 0\n                  border_width: 0\n                  radius: 0\n                  pad_all: 0\n"
              % (base, list_y, w - 28, list_h))
    inner += "                  widgets:\n"
    for i in range(n):
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
                  "                                device: !lambda 'return id(g_spot_dev);'\n"
                  % (base, i, i * (rh + gap), w - 32, rh, base, i, w - 56, i))
    inner += ("                    - label: { id: %s_empty, text: \"Pick a playlist to load songs\", align: top_mid, y: 12, text_font: f_small, text_color: 0x5D6470 }\n" % base)
    # populate rows from the newline-joined names (hand-built array-split pattern)
    larr = ", ".join("id(%s_l%d)" % (base, i) for i in range(n))
    rarr = ", ".join("id(%s_r%d)" % (base, i) for i in range(n))
    ts = ["  - platform: homeassistant\n    id: ha_" + base + "_tn\n    entity_id: sensor.aurora_spotify_tracks\n    attribute: names\n    on_value:\n"
          "      then:\n"
          "        - lambda: |-\n"
          "            const std::string &str = x;\n"
          "            lv_obj_t* L[" + str(n) + "] = { " + larr + " };\n"
          "            lv_obj_t* R[" + str(n) + "] = { " + rarr + " };\n"
          # only populate once a playlist has been chosen this session; ignore the
          # stale track list HA replays on connect (design: list stays empty until pick)
          "            if (id(g_spot_ctx).empty()) {\n"
          "              for (int j = 0; j < " + str(n) + "; j++) lv_obj_add_flag(R[j], LV_OBJ_FLAG_HIDDEN);\n"
          "              lv_obj_clear_flag(id(" + base + "_empty), LV_OBJ_FLAG_HIDDEN);\n"
          "              return;\n"
          "            }\n"
          "            int idx = 0; size_t st = 0;\n"
          "            for (size_t i = 0; i <= str.size() && idx < " + str(n) + "; i++) {\n"
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
          "            for (int j = idx; j < " + str(n) + "; j++) lv_obj_add_flag(R[j], LV_OBJ_FLAG_HIDDEN);\n"
          "            if (idx > 0) lv_obj_add_flag(id(" + base + "_empty), LV_OBJ_FLAG_HIDDEN);\n"
          "            else lv_obj_clear_flag(id(" + base + "_empty), LV_OBJ_FLAG_HIDDEN);\n"]
    return [card_obj(x, y, w, h, inner)], [], ts


SPOT_MAX_SPEAKERS = 14


def _spk_arrays(base, n):
    L = ", ".join("id(%s_sl%d)" % (base, i) for i in range(n))
    B = ", ".join("id(%s_sr%d)" % (base, i) for i in range(n))
    return L, B


def _indent(lines, spaces):
    pad = " " * spaces
    return "".join(pad + ln + "\n" for ln in lines)


def _spk_hl_lines(base, n):
    """Recolor each visible speaker row: the one whose name == g_spot_dev is the
    picked target (Spotify green), the rest dark."""
    L, B = _spk_arrays(base, n)
    return [
        "lv_obj_t* HL[%d] = { %s };" % (n, L),
        "lv_obj_t* HB[%d] = { %s };" % (n, B),
        "const std::string &sel = id(g_spot_dev);",
        "for (int i = 0; i < %d; i++) {" % n,
        "  if (lv_obj_has_flag(HB[i], LV_OBJ_FLAG_HIDDEN)) continue;",
        "  bool on = (sel == lv_label_get_text(HL[i]));",
        "  lv_obj_set_style_bg_color(HB[i], lv_color_hex(on ? 0x1DB954 : 0x0F1117), 0);",
        "  lv_obj_set_style_text_color(HL[i], lv_color_hex(on ? 0x06231D : 0xF3F5F8), 0);",
        "}",
    ]


def c_spot_speakers(card, x, y, w, h, base):
    """Spotify Connect speaker picker. Loads the available speakers straight from
    the SpotifyPlus media_player's `source_list` attribute (no HA-side helper
    needed) and renders one tap-to-select row per speaker. Tapping a row stores
    its name in g_spot_dev (the target the song list plays onto via play_track's
    `device`) and transfers any current playback there via select_source. The
    picked row is highlighted; g_spot_dev defaults to the active `source`.
    source_list arrives as a quoted list string ("['Kitchen', 'Office', ...]"),
    so rows are parsed by pulling each quoted token (single- or double-quoted)."""
    e = card.get("entity") or "media_player.spotifyplus_ben_walton"
    n = _card_rows(card, SPOT_MAX_SPEAKERS, 24)
    inner = ic("speakers", color="0x1DB954")
    inner += lbl("PLAY ON \\u00B7 TAP A SPEAKER", 50, 18, "f_micro", "0x868CA0")
    # refresh: re-poll the media_player so a newly-woken speaker shows up
    inner += ("              - button:\n                  x: %d\n                  y: 10\n                  width: 40\n                  height: 30\n"
              "                  bg_color: 0x161B24\n                  radius: 10\n                  pad_all: 0\n                  scrollable: false\n"
              "                  widgets: [label: { text: \"%s\", align: center, text_font: f_icon, text_color: 0x1DB954 }]\n"
              "                  on_click: [homeassistant.action: { action: homeassistant.update_entity, data: { entity_id: %s } }]\n" % (w - 54, glyph_for("reload"), e))
    rh, gap = 44, 6
    list_y = 48
    list_h = h - list_y - 12
    inner += ("              - obj:\n                  id: %s_lst\n                  x: 14\n                  y: %d\n                  width: %d\n                  height: %d\n"
              "                  bg_opa: 0\n                  border_width: 0\n                  radius: 0\n                  pad_all: 0\n"
              % (base, list_y, w - 28, list_h))
    inner += "                  widgets:\n"
    for i in range(n):
        tap = ["id(g_spot_dev) = std::string(lv_label_get_text(id(%s_sl%d)));" % (base, i)]
        tap += _spk_hl_lines(base, n)
        inner += ("                    - button:\n                        id: %s_sr%d\n                        x: 0\n                        y: %d\n"
                  "                        width: %d\n                        height: %d\n"
                  "                        bg_color: 0x0F1117\n                        radius: 8\n                        pad_all: 0\n                        scrollable: false\n"
                  "                        hidden: true\n"
                  "                        widgets:\n"
                  "                          - label: { id: %s_sl%d, text: \"\", x: 12, align: left_mid, width: %d, long_mode: dot, text_font: f_body, text_color: 0xF3F5F8 }\n"
                  "                        on_click:\n"
                  "                          - lambda: |-\n"
                  "%s"
                  "                          - homeassistant.action:\n                              action: media_player.select_source\n                              data:\n"
                  "                                entity_id: %s\n"
                  "                                source: !lambda 'return id(g_spot_dev);'\n"
                  % (base, i, i * (rh + gap), w - 32, rh, base, i, w - 56, _indent(tap, 30), e))
    inner += ("                    - label: { id: %s_empty, text: \"Loading speakers\", align: top_mid, y: 12, text_font: f_small, text_color: 0x5D6470 }\n" % base)
    # source_list -> (re)populate rows, then highlight the picked target
    pop = [
        "const std::string &str = x;",
    ]
    L, B = _spk_arrays(base, n)
    pop += [
        "lv_obj_t* PL[%d] = { %s };" % (n, L),
        "lv_obj_t* PB[%d] = { %s };" % (n, B),
        "int idx = 0; char q = 0; std::string cur;",
        "for (size_t i = 0; i < str.size() && idx < %d; i++) {" % n,
        "  char c = str[i];",
        "  if (q == 0) { if (c == '\\'' || c == '\"') { q = c; cur.clear(); } }",
        "  else if (c == q) {",
        "    lv_label_set_text(PL[idx], cur.c_str());",
        "    lv_obj_clear_flag(PB[idx], LV_OBJ_FLAG_HIDDEN);",
        "    idx++; q = 0;",
        "  } else cur += c;",
        "}",
        "for (int j = idx; j < %d; j++) lv_obj_add_flag(PB[j], LV_OBJ_FLAG_HIDDEN);" % n,
        "if (idx > 0) lv_obj_add_flag(id(%s_empty), LV_OBJ_FLAG_HIDDEN);" % base,
        "else lv_obj_clear_flag(id(%s_empty), LV_OBJ_FLAG_HIDDEN);" % base,
    ]
    pop += _spk_hl_lines(base, n)
    src = ["if (id(g_spot_dev).empty()) id(g_spot_dev) = x;"] + _spk_hl_lines(base, n)
    ts = [
        "  - platform: homeassistant\n    id: ha_%s_sl\n    entity_id: %s\n    attribute: source_list\n    on_value:\n      then:\n        - lambda: |-\n%s"
        % (base, e, _indent(pop, 12)),
        "  - platform: homeassistant\n    id: ha_%s_sc\n    entity_id: %s\n    attribute: source\n    on_value:\n      then:\n        - lambda: |-\n%s"
        % (base, e, _indent(src, 12)),
    ]
    return [card_obj(x, y, w, h, inner)], [], ts


def c_spot_speaker(card, x, y, w, h, base):
    """Single-speaker Spotify "Play on" tile (1x1+). Assign ONE speaker by its
    Spotify Connect source name (exactly as it appears in the SpotifyPlus
    media_player's source_list). Tapping transfers playback there via
    media_player.select_source and marks it the play-track target (g_spot_dev);
    the tile lights green while it is the player's active `source`. Drop several
    tiles for a curated set of cast targets. The source name is carried in a
    hidden label so the tap + highlight lambdas never embed it (dodges C++/YAML
    string escaping of speaker names with quotes/apostrophes)."""
    e = card.get("entity") or "media_player.spotifyplus_ben_walton"
    spk = card.get("speaker") or card.get("name") or "Speaker"
    disp = card.get("name") or spk
    oid, iid, srcid = base + "_c", base + "_i", base + "_src"
    acc = "0x1DB954"
    lit = _darken(acc, 0.22)
    inner = lbl(CARD_ICON["spotify_speaker"], 0, 20, "f_icon", acc, wid=iid, align="top_mid")
    inner += lbl(disp, 0, -12, "f_small", "0xF3F5F8", align="bottom_mid",
                 width=w - 14, text_align="center", long="dot", height=16)
    inner += ("              - label: { id: %s, text: %s, hidden: true, x: 0, y: 0, "
              "text_font: f_micro, text_color: 0x000000 }\n" % (srcid, esc(spk)))
    # tap: mark this the target, transfer playback there, light self immediately.
    tap = ("id(g_spot_dev) = std::string(lv_label_get_text(id(%s))); "
           "lv_obj_set_style_bg_color(id(%s), lv_color_hex(%s), 0); "
           "lv_obj_set_style_text_color(id(%s), lv_color_hex(0xFFFFFF), 0);"
           % (srcid, oid, lit, iid))
    on = ("lambda: '%s', homeassistant.action: { action: media_player.select_source, "
          "data: { entity_id: %s, source: !lambda 'return id(g_spot_dev);' } }" % (tap, e))
    # highlight = truth from the player's `source` attr: green when it is us.
    hl = [
        "bool on = (std::string(x) == std::string(lv_label_get_text(id(%s))));" % srcid,
        "lv_obj_set_style_bg_color(id(%s), lv_color_hex(on ? %s : 0x0E1116), 0);" % (oid, lit),
        "lv_obj_set_style_text_color(id(%s), lv_color_hex(on ? 0xFFFFFF : %s), 0);" % (iid, acc),
    ]
    ts = ["  - platform: homeassistant\n    id: ha_%s_src\n    entity_id: %s\n    attribute: source\n"
          "    on_value:\n      then:\n        - lambda: |-\n%s" % (base, e, _indent(hl, 12))]
    return [card_obj(x, y, w, h, inner, on, oid=oid)], [], ts


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
            glyph = glyph_for(s.get("icon", ""))
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


def c_spotify_art(card, x, y, w, h, base):
    """Album-art Spotify control, 1:1 with the web preview (builder.html
    `spotify_art`): the art panel fills the left column and a SOLID metadata
    strip (LVGL-safe, no blur) overlays its bottom — title / artist / progress
    bar / prev-play-next row, each in its own band — plus a 44px green vertical
    volume fill-rail with live percent readout and a mute button below it on
    the right. All geometry is anchored off (w, h) so every allowed span
    (3x3 .. 6x5) lays out without overlap: the strip is a fixed-height content
    band (fonts don't scale) and the art + rail absorb the rest. Art = single
    JPEG still fetched + HW-decoded on the mjpeg_stream task (fetch_still);
    its square edge is min(art column w, h) so it fills the column under the
    strip."""
    e = card.get("entity", "")
    tid, aid, ppid, pgid = base + "_t", base + "_a", base + "_pp", base + "_pg"
    sld, fillid, gripid, vpid = base + "_vs", base + "_vf", base + "_vg", base + "_vp"
    GN, GN_LO = "0x1DB954", "0x0E7A37"
    prev = ha("media_player.media_previous_track", e) if e else "lvgl.page.show: page_home"
    plpz = ha("media_player.media_play_pause", e) if e else "lvgl.page.show: page_home"
    nxt = ha("media_player.media_next_track", e) if e else "lvgl.page.show: page_home"
    mute = ha("media_player.volume_mute", e, 'is_volume_muted: "true"') if e else "lvgl.page.show: page_home"
    # transport + volume glyphs render at the preview's ~15-19px -> small icon font
    for cp in ("000F04AE", "000F04AD", "000F03E4", "000F040A", "000F057E", "000F075F"):
        USED_ICONSM_CP.add(cp)
    # Web-preview geometry (builder.html draws the 6x5 grid 1:1 at device px):
    # card pad 14, art|rail gap 8, 44px volume rail, 30px mute button 8 below it.
    pad, gap, vw, mut = 14, 8, 44, 30
    ax = ay = pad
    aw = w - 2 * pad - gap - vw                           # art column width
    ah = h - 2 * pad                                      # art column height
    vx = ax + aw + gap
    vth = ah - gap - mut                                  # volume track height (mute btn below)
    tp_h = 105              # metadata strip: fixed-height band (web ~96 at its 13/11px text)
    tpy = ay + ah - tp_h
    trkw = aw - 20                                        # progress track (10px side insets)
    vol0 = 65
    real = min(aw, ah)      # square art edge: fills the column; the strip overlays its bottom

    # --- art panel (fills the left column; placeholder note centered like the web) ---
    inner = ("              - obj: { x: " + str(ax) + ", y: " + str(ay) + ", width: " + str(aw) + ", height: " + str(ah) + ", bg_color: 0x1B1E27, radius: 10, clip_corner: true, border_width: 0, pad_all: 0, scrollable: false }\n")
    inner += lbl("\\U000F075A", ax + (aw - 30) // 2, ay + (ah - 34) // 2, "f_icon", "0x5D6470")
    if e and ART_ENABLED:
        img_id = base + "_art"
        # ss_image placeholder src + hidden (camera-widget pattern): the stills
        # channel unhides the widget when the first fetched art is applied.
        inner += "              - image: { id: " + img_id + ", x: " + str(ax + (aw - real) // 2) + ", y: " + str(ay + (ah - real) // 2) + ", src: ss_image, hidden: true }\n"
        ART_IMAGES.append((real, img_id, e))
    # --- solid metadata strip overlaid on the art bottom; fixed bands never overlap:
    # title 8..28, artist 30..46, progress 52..55, transport 63..97, bottom pad 8 ---
    inner += "              - obj: { x: " + str(ax) + ", y: " + str(tpy) + ", width: " + str(aw) + ", height: " + str(tp_h) + ", bg_color: 0x0B0D11, radius: 0, border_width: 0, pad_all: 0, scrollable: false }\n"
    inner += lbl("Nothing playing", ax + 10, tpy + 8, "f_body", "0xF3F5F8", wid=tid, width=trkw, long="dot", height=20)
    inner += lbl("", ax + 10, tpy + 30, "f_small", "0xAEB4C2", wid=aid, width=trkw, long="dot", height=16)
    pgy = tpy + 52
    inner += "              - obj: { x: " + str(ax + 10) + ", y: " + str(pgy) + ", width: " + str(trkw) + ", height: 3, bg_color: 0x2A2D36, radius: 2, border_width: 0, pad_all: 0, scrollable: false }\n"
    inner += "              - obj: { id: " + pgid + ", x: " + str(ax + 10) + ", y: " + str(pgy) + ", width: " + str(max(1, int(trkw * 0.4))) + ", height: 3, bg_color: " + GN + ", radius: 2, border_width: 0, pad_all: 0, scrollable: false" + (", hidden: true" if e else "") + " }\n"
    # transport row (web: 34px play circle, bare 19px prev/next icons, 14px visual gaps)
    bty = tpy + 63
    cx = ax + aw // 2
    inner += btn(cx - 57, bty, 34, 34, "\\U000F04AE", prev, bg="0x0B0D11", color="0xFFFFFF", radius=17, font="f_iconsm")
    inner += btn(cx - 17, bty, 34, 34, "\\U000F03E4", plpz, bg=GN, color="0x052E16", radius=17, font="f_iconsm", lid=ppid)
    inner += btn(cx + 23, bty, 34, 34, "\\U000F04AD", nxt, bg="0x0B0D11", color="0xFFFFFF", radius=17, font="f_iconsm")
    # --- green vertical volume fill-rail (right) + mute button below ---
    fh0 = vth * vol0 // 100
    trk = ("              - obj:\n                  x: " + str(vx) + "\n                  y: " + str(ay) + "\n                  width: " + str(vw) + "\n                  height: " + str(vth) + "\n"
           "                  bg_color: 0x0E1014\n                  radius: 9\n                  clip_corner: true\n                  border_width: 0\n                  pad_all: 0\n                  scrollable: false\n                  widgets:\n"
           "                    - obj: { id: " + fillid + ", x: 0, y: " + str(vth - fh0) + ", width: " + str(vw) + ", height: " + str(fh0) + ", bg_color: " + GN_LO + ", bg_grad_color: " + GN + ", bg_grad_dir: VER, border_width: 0, radius: 0, pad_all: 0, scrollable: false }\n"
           "                    - obj: { id: " + gripid + ", x: 5, y: " + str(vth - fh0 - 3) + ", width: " + str(vw - 10) + ", height: 5, bg_color: 0xFFFFFF, radius: 3, border_width: 0, pad_all: 0, scrollable: false }\n"
           "                    - label: { text: \"\\U000F057E\", align: top_mid, y: 6, text_font: f_iconsm, text_color: 0xFFFFFF }\n"
           "                    - label: { id: " + vpid + ", text: \"" + str(vol0) + "\", align: bottom_mid, y: -6, text_font: f_body, text_color: 0xFFFFFF }\n")
    if e:
        trk += ("                    - slider:\n                        id: " + sld + "\n                        x: 0\n                        y: 0\n                        width: " + str(vw) + "\n                        height: " + str(vth) + "\n"
                "                        bg_opa: 0%\n                        min_value: 0\n                        max_value: 100\n                        value: " + str(vol0) + "\n                        indicator: { bg_opa: 0% }\n                        knob: { bg_opa: 0% }\n"
                "                        on_value:\n"
                "                          - lvgl.widget.update: { id: " + fillid + ", height: !lambda 'return (int)(lv_slider_get_value(id(" + sld + ")) * " + str(vth) + " / 100.0f);' }\n"
                "                          - lvgl.widget.update: { id: " + fillid + ", y: !lambda 'return (int)(" + str(vth) + " - lv_slider_get_value(id(" + sld + ")) * " + str(vth) + " / 100.0f);' }\n"
                "                          - lvgl.widget.update: { id: " + gripid + ", y: !lambda 'return (int)(" + str(vth) + " - lv_slider_get_value(id(" + sld + ")) * " + str(vth) + " / 100.0f) - 3;' }\n"
                "                          - lvgl.label.update: { id: " + vpid + ", text: !lambda 'return std::to_string((int) lv_slider_get_value(id(" + sld + ")));' }\n"
                "                        on_release:\n                          - homeassistant.action:\n                              action: media_player.volume_set\n"
                "                              data: { entity_id: " + e + ", volume_level: !lambda 'char b[8]; snprintf(b, sizeof(b), \"%.2f\", lv_slider_get_value(id(" + sld + ")) / 100.0); return std::string(b);' }\n")
    inner += trk
    inner += "              - button: { x: " + str(vx + (vw - mut) // 2) + ", y: " + str(ay + vth + gap) + ", width: " + str(mut) + ", height: " + str(mut) + ", bg_color: 0x1B1E26, border_color: 0x2B2F3A, border_width: 1, radius: " + str(mut // 2) + ", pad_all: 0, scrollable: false, widgets: [label: { text: \"\\U000F075F\", align: center, text_font: f_iconsm, text_color: 0x868CA0 }], on_click: [" + mute + "] }\n"

    ss, ts = [], []
    if e:
        ts.append("  - platform: homeassistant\n    id: ha_" + tid + "\n    entity_id: " + e + "\n    attribute: media_title\n    on_value:\n"
                  "      - lvgl.label.update: { id: " + tid + ", text: !lambda 'return x.empty() ? std::string(\"Nothing playing\") : x;' }\n")
        ts.append("  - platform: homeassistant\n    id: ha_" + aid + "\n    entity_id: " + e + "\n    attribute: media_artist\n    on_value:\n"
                  "      - lvgl.label.update: { id: " + aid + ", text: !lambda 'return x;' }\n")
        # not actively playing -> honest empty state: "Nothing playing", blank
        # artist, empty progress (title/artist attrs vanish when idle, so their
        # readbacks never fire to clear the last-played track).
        ts.append("  - platform: homeassistant\n    id: ha_" + base + "_st\n    entity_id: " + e + "\n    on_value:\n"
                  "      - lvgl.label.update: { id: " + tid + ", text: !lambda 'return (x == \"playing\" || x == \"paused\" || x == \"buffering\") ? std::string(lv_label_get_text(id(" + tid + "))) : std::string(\"Nothing playing\");' }\n"
                  "      - lvgl.label.update: { id: " + aid + ", text: !lambda 'return (x == \"playing\" || x == \"paused\" || x == \"buffering\") ? std::string(lv_label_get_text(id(" + aid + "))) : std::string(\"\");' }\n"
                  "      - lvgl.label.update: { id: " + ppid + ", text: !lambda 'return x == \"playing\" ? std::string(\"\\U000F03E4\") : std::string(\"\\U000F040A\");' }\n"
                  "      - if:\n"
                  "          condition: { lambda: 'return x == \"playing\" || x == \"paused\" || x == \"buffering\";' }\n"
                  "          then: [lvgl.widget.show: " + pgid + "]\n"
                  "          else: [lvgl.widget.hide: " + pgid + "]\n")
        ts.append("  - platform: homeassistant\n    id: ha_" + base + "_v\n    entity_id: " + e + "\n    attribute: volume_level\n    on_value:\n"
                  "      - lvgl.slider.update: { id: " + sld + ", value: !lambda 'return (int)(atof(x.c_str()) * 100);' }\n"
                  "      - lvgl.widget.update: { id: " + fillid + ", height: !lambda 'return (int)(atof(x.c_str()) * " + str(vth) + ");' }\n"
                  "      - lvgl.widget.update: { id: " + fillid + ", y: !lambda 'return (int)(" + str(vth) + " - atof(x.c_str()) * " + str(vth) + ");' }\n"
                  "      - lvgl.widget.update: { id: " + gripid + ", y: !lambda 'return (int)(" + str(vth) + " - atof(x.c_str()) * " + str(vth) + ") - 3;' }\n"
                  "      - lvgl.label.update: { id: " + vpid + ", text: !lambda 'return std::to_string((int)(atof(x.c_str()) * 100));' }\n")
        # live progress: media_position / media_duration ratio -> green fill width
        ss.append("  - platform: homeassistant\n    id: ha_" + base + "_dur\n    entity_id: " + e + "\n    attribute: media_duration\n")
        ss.append("  - platform: homeassistant\n    id: ha_" + base + "_pos\n    entity_id: " + e + "\n    attribute: media_position\n    on_value:\n"
                  "      - lvgl.widget.update: { id: " + pgid + ", width: !lambda 'float d = id(ha_" + base + "_dur).state; if (isnan(d) || d < 1.0f || isnan(x) || x < 0.0f) return 1; int px = (int)(" + str(trkw) + " * x / d); return px < 1 ? 1 : (px > " + str(trkw) + " ? " + str(trkw) + " : px);' }\n")
    return [card_obj(x, y, w, h, inner)], ss, ts


def c_generic(card, x, y, w, h, base):
    inner = ic(card.get("ck", ""), color="0x868CA0")
    inner += lbl(card.get("name", card.get("ck", "Card")), 0, 8, "f_body", "0x868CA0", align="center")
    return [card_obj(x, y, w, h, inner)], [], []


CTRL = {
    "switch": c_toggle, "light_t": c_toggle, "light": c_light, "sensor": c_sensor,
    "binary": c_binary, "person": c_binary, "vacuum": c_state, "alarm": c_state,
    "chart": c_chart,
    "climate": c_climate, "scene": c_action, "script": c_action, "media": c_media,
    "spotify": c_media, "sonos": c_media, "fan": c_fan, "cover": c_cover,
    "spotify_art": c_spotify_art,
    "lock": c_lock, "weather": c_weather, "camera": c_camera, "group": c_group,
    "wx_current": c_wx_current, "wx_hourly": c_wx_hourly, "wx_daily": c_wx_daily, "wx_stats": c_wx_stats,
    "lightgroup": c_group, "outletgroup": c_outlet, "speakers": c_speakers,
    "sonos_sources": c_btngrid, "tv_sources": c_btngrid,
    "tv_dpad": c_tv_dpad, "tv_transport": c_tv_transport, "tv_channel": c_tv_channel,
    "tv_volume": c_tv_volume, "tv_trackpad": c_tv_trackpad, "tvremote": c_tvremote,
    "playlist": c_playlist, "sonos_fav": c_playlist, "songlist": c_songlist,
    "sonos_library": c_songlist, "volume": c_volume, "volumes": c_volumes,
    "spotify_playlists": c_spot_playlists, "spotify_tracks": c_spot_tracks,
    "spotify_speakers": c_spot_speakers, "spotify_speaker": c_spot_speaker,
}


def emit_card(card, header, pagemap):
    x, y, w, h = rect(card, header)
    base = "g_" + slug(card.get("id", "c"))
    ck = card.get("ck", "")
    if ck == "shortcuts":
        return c_shortcuts(card, x, y, w, h, pagemap, base)
    fn = CTRL.get(ck, c_generic)
    ws, ss, ts = fn(card, x, y, w, h, base)
    # Generic availability: grey out the whole card when its entity is
    # unavailable/unknown. `opa` on the card obj cascades to every child, so the
    # card visibly fades to "disabled". Actions on a dead entity are harmless HA
    # no-ops, so we fade rather than hard-block input.
    e = card.get("entity", "")
    if e and ws and ws[0].startswith("        - obj:"):
        m = re.match(r"        - obj:\n            id: (\S+)\n", ws[0])
        if m:
            cid = m.group(1)
        else:
            cid = base + "_card"
            ws = [ws[0].replace("        - obj:\n", "        - obj:\n            id: " + cid + "\n", 1)] + ws[1:]
        ts = list(ts) + [
            "  - platform: homeassistant\n    id: ha_" + base + "_av\n    entity_id: " + e + "\n    on_value:\n"
            "      - lvgl.widget.update: { id: " + cid + ", opa: !lambda 'return (x == \"unavailable\" || x == \"unknown\") ? 90 : 255;' }\n"]
    return ws, ss, ts


def gen_nav(layout, pagemap):
    out = ""
    nav = layout.get("nav", [])[:7]
    for i, n in enumerate(nav):
        g = glyph_for(n.get("icon", ""))
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
               "            if (id(g_cam_wake)) lv_obj_add_state(id(set_cwake), LV_STATE_CHECKED);\n"
               "            { lv_obj_t* HB[5] = { id(set_hap0),id(set_hap1),id(set_hap2),id(set_hap3),id(set_hap4) };\n"
               "              lv_obj_t* HL[5] = { id(set_hap0_l),id(set_hap1_l),id(set_hap2_l),id(set_hap3_l),id(set_hap4_l) };\n"
               "              for (int k=0;k<5;k++){ bool on=(k==id(g_haptic_level)); lv_obj_set_style_bg_color(HB[k], lv_color_hex(on?0x2ED5B8:0x0F1117),0); lv_obj_set_style_text_color(HL[k], lv_color_hex(on?0x06231D:0xC2C7D2),0); } }\n")
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
    # --- Haptics card: touch-feedback strength (Off/Low/Med/High/Max) ---
    hlevels = [("Off", 0), ("Low", 1), ("Med", 2), ("High", 3), ("Max", 4)]
    hbw = (448 - 40 - 4 * 8) // 5
    hap = lbl("HAPTICS", 20, 16, "f_micro", "0x868CA0")
    hap += lbl("Touch feedback", 20, 34, "f_title", "0xF3F5F8", height=26)
    for i, (lab, lvl) in enumerate(hlevels):
        body = ("id(g_haptic_level) = %d; id(haptic).set_level(%d);\n"
                "                        lv_obj_t* HB[5] = { id(set_hap0),id(set_hap1),id(set_hap2),id(set_hap3),id(set_hap4) };\n"
                "                        lv_obj_t* HL[5] = { id(set_hap0_l),id(set_hap1_l),id(set_hap2_l),id(set_hap3_l),id(set_hap4_l) };\n"
                "                        for (int k=0;k<5;k++){ bool on=(k==id(g_haptic_level)); lv_obj_set_style_bg_color(HB[k], lv_color_hex(on?0x2ED5B8:0x0F1117),0); lv_obj_set_style_text_color(HL[k], lv_color_hex(on?0x06231D:0xC2C7D2),0); }\n"
                "                        id(haptic).click();" % (lvl, lvl))
        hap += ("              - button:\n                  id: set_hap%d\n                  x: %d\n                  y: 70\n                  width: %d\n                  height: 44\n"
                "                  bg_color: 0x0F1117\n                  radius: 10\n                  pad_all: 0\n                  scrollable: false\n"
                "                  widgets: [label: { id: set_hap%d_l, text: \"%s\", align: center, text_font: f_small, text_color: 0xC2C7D2 }]\n"
                "                  on_click:\n                    - lambda: |-\n                        %s\n"
                % (i, 20 + i * (hbw + 8), hbw, i, lab, body))
    w += card_obj(94, 352, 448, 116, hap)
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
    w += "        - label: { text: \"Guition JC1060P470 \\u00B7 web UI on :80\", x: 94, y: 576, text_font: f_small, text_color: 0x5D6470 }\n"
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
    # Pad/scroll shrink to h320 (bottom 440) to seat a Back/Home row + a volume row
    # underneath; the gesture zones MUST track the visual rects, so on_load re-asserts
    # them with the matching bottom edge (440).
    onload += ("        - lambda: |-\n"
               "            id(g_tp_px1) = 96;  id(g_tp_py1) = 120; id(g_tp_px2) = 856;  id(g_tp_py2) = 440;\n"
               "            id(g_tp_sx1) = 872; id(g_tp_sy1) = 120; id(g_tp_sx2) = 1004; id(g_tp_sy2) = 440;\n"
               "            id(g_tp_active) = true;\n")
    w = "        - image: { src: img_aurora_bg, x: 0, y: 0 }\n"
    w += "        - label: { text: \"Trackpad\", x: 96, y: 26, text_font: f_title, text_color: 0xF3F5F8 }\n"
    w += "        - label: { text: \"LG Magic pointer \\u00B7 drag to move, tap to click\", x: 96, y: 60, text_font: f_small, text_color: 0x868CA0 }\n"
    w += ("        - button:\n            align: top_right\n            x: -12\n            y: 12\n            width: 132\n            height: 48\n"
          "            bg_color: 0x161B24\n            border_color: 0x2ED5B8\n            border_width: 2\n            radius: 12\n"
          "            pad_all: 0\n            scrollable: false\n"
          "            widgets: [label: { text: \"Buttons\", align: center, text_font: f_body, text_color: 0x2ED5B8 }]\n"
          "            on_click: [lvgl.page.show: %s]\n" % back_pid)
    w += ("        - obj:\n            x: 96\n            y: 120\n            width: 760\n            height: 320\n"
          "            bg_color: 0x10121A\n            bg_opa: 60%\n            radius: 22\n            border_width: 1\n            border_color: 0x2A5048\n"
          "            pad_all: 0\n            scrollable: false\n            clickable: false\n"
          "            widgets:\n"
          "              - label: { text: \"\\U000F0297\", align: center, y: -30, text_font: f_bigicon, text_color: 0x2ED5B8 }\n"
          "              - label: { text: \"Drag to move  \\u00B7  tap to click\", align: center, y: 36, text_font: f_body, text_color: 0x868CA0 }\n")
    w += ("        - obj:\n            x: 872\n            y: 120\n            width: 132\n            height: 320\n"
          "            bg_color: 0x10121A\n            bg_opa: 60%\n            radius: 22\n            border_width: 1\n            border_color: 0x2A5048\n"
          "            pad_all: 0\n            scrollable: false\n            clickable: false\n"
          "            widgets:\n"
          "              - label: { text: \"\\U000F0143\", align: top_mid, y: 20, text_font: f_bigicon, text_color: 0x4FA8F5 }\n"
          "              - label: { text: \"SCROLL\", align: center, text_font: f_micro, text_color: 0x868CA0 }\n"
          "              - label: { text: \"\\U000F0140\", align: bottom_mid, y: -20, text_font: f_bigicon, text_color: 0x4FA8F5 }\n")
    # Remote buttons below the pad (outside the gesture zone y>440), all sent over the
    # proven pointer socket via the lg_pointer bridge. Row 1: labeled Back + Home.
    # Row 2: Volume down / Mute / Volume up (VOLUMEDOWN/MUTE/VOLUMEUP button names).
    def _remote_btn(bx, bw, by, glyph, label, name, gcol="0xF3F5F8"):
        if label:
            wid = ("              - label: { text: \"%s\", x: 40, align: left_mid, text_font: f_icon, text_color: %s }\n"
                   "              - label: { text: \"%s\", x: 88, align: left_mid, text_font: f_title, text_color: 0xF3F5F8 }\n"
                   % (glyph, gcol, label))
        else:
            wid = "              - label: { text: \"%s\", align: center, text_font: f_icon, text_color: %s }\n" % (glyph, gcol)
        return ("        - button:\n            x: %d\n            y: %d\n            width: %d\n            height: 60\n"
                "            bg_color: 0x161B24\n            border_color: 0x23262F\n            border_width: 1\n            radius: 14\n"
                "            pad_all: 0\n            scrollable: false\n"
                "            widgets:\n%s"
                "            on_click: [homeassistant.action: { action: pyscript.lg_pointer_button, data: { name: %s } }]\n"
                % (bx, by, bw, wid, name))
    w += _remote_btn(96, 450, 452, "\\U000F004D", "Back", "BACK")
    w += _remote_btn(554, 450, 452, "\\U000F02DC", "Home", "HOME")
    w += _remote_btn(96, 297, 520, "\\U000F075E", "", "VOLUMEDOWN", gcol="0x2ED5B8")
    w += _remote_btn(401, 297, 520, "\\U000F075F", "", "MUTE", gcol="0x2ED5B8")
    w += _remote_btn(706, 298, 520, "\\U000F075D", "", "VOLUMEUP", gcol="0x2ED5B8")
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
            cam_n = len(CAM_CARDS)                        # detect a live camera card emitted on THIS sub-page
            for card in cards:
                ws, ss, ts = emit_card(card, header_on, pagemap)
                widgets += "".join(ws)
                sens += ss
                txt += ts
            # Sub-page paging affordances. These MUST be emitted as PAGE-LEVEL widgets at
            # 8-space indent: btn() emits 14-space card-inner YAML, which would nest inside
            # the last card's widgets and never render as a page button (the original bug).
            # Prev starts at x=94 to clear the left nav rail; both float on the bottom strip.
            if si > 0:                                    # Prev -> previous sub-page (sub-page 1's prev is the base page id)
                prv = pagemap[key] if si == 1 else "%s_%d" % (pagemap[key], si - 1)
                widgets += ("        - button:\n            x: 94\n            y: 540\n            width: 128\n            height: 46\n"
                            "            bg_color: 0x161B24\n            border_color: 0x2ED5B8\n            border_width: 1\n            radius: 12\n"
                            "            pad_all: 0\n            scrollable: false\n"
                            "            widgets: [label: { text: \"Prev\", align: center, text_font: f_body, text_color: 0x2ED5B8 }]\n"
                            "            on_click: [lvgl.page.show: %s]\n" % prv)
            if si < len(subs) - 1:                        # Next -> following sub-page
                nxt = "%s_%d" % (pagemap[key], si + 1)
                widgets += ("        - button:\n            x: 802\n            y: 540\n            width: 128\n            height: 46\n"
                            "            bg_color: 0x161B24\n            border_color: 0x2ED5B8\n            border_width: 1\n            radius: 12\n"
                            "            pad_all: 0\n            scrollable: false\n"
                            "            widgets: [label: { text: \"Next\", align: center, text_font: f_body, text_color: 0x2ED5B8 }]\n"
                            "            on_click: [lvgl.page.show: %s]\n" % nxt)
            active = next((slug(n.get("id", "")) for n in layout.get("nav", []) if n.get("page") == key), None)
            onload = _nav_onload(layout, active)
            onunload = ""
            if len(CAM_CARDS) > cam_n:                    # live camera lifecycle: stream while the page shows
                CAM_CARDS[0] = CAM_CARDS[0][:4] + (pid,)  # remember the hosting page (fullscreen Dismiss target)
                onload += "        - lambda: 'id(cam_stream).start(0, id(%s_cam));'\n" % CAM_CARDS[0][3]
                onunload = "      on_unload:\n        - lambda: 'id(cam_stream).stop();'\n"
            if has_tv and tp_page is None:                # remember where the remote lives (Pad links here back)
                tp_page = (pid, active)
            pages_yaml += (
                "    - id: %s\n      bg_color: 0x0A0B0F\n      scrollable: false\n%s%s      widgets:\n%s" % (pid, onload, onunload, widgets))
    if tp_page is not None:                               # dedicated trackpad page (hand-built clone)
        pages_yaml += gen_trackpad_page(layout, tp_page[0], tp_page[1])
    pages_yaml += gen_settings_page(layout)
    return pages_yaml, sens, txt, clocks


def build_lvgl(layout):
    USED_ICON_CP.clear()  # repopulated by glyph_for() as icons are placed
    USED_ICONSM_CP.clear()  # repopulated by card emitters needing small icons
    EXTRA_CLOCKS.clear()  # repopulated by card emitters (e.g. weather time/date)
    ART_IMAGES.clear()    # repopulated by media/spotify_art card emitters
    CAM_CARDS.clear()     # repopulated by the first live camera card
    pagemap = {key: "page_" + slug(key) for key in layout.get("pages", {})}
    nav = gen_nav(layout, pagemap)
    pages, sens, txt, clocks = gen_pages(layout, pagemap)
    return nav, pages, sens, txt, pagemap, clocks + EXTRA_CLOCKS


# ---- base extraction: keep hardware/font/style sections, drop UI bindings ----
KEEP = ["substitutions", "esphome", "esp32", "psram", "esp_ldo", "esp32_hosted",
        "wifi", "api", "ota", "safe_mode", "logger", "output", "light",
        "external_components", "i2c", "touchscreen", "display", "http_request",
        "image", "font", "globals", "number", "button", "time",
        "ov02c10_support", "esp_video_camera",   # onboard camera (HA entity + RTSP :8554)
        # NOT kept: "mjpeg_stream" — its hand-built targets are sized for the hand
        # dashboard. assemble() ALWAYS emits its own id: cam_stream instance (the
        # kept esphome: on_boot $cam_stream_url_override lambda references that id).
        # NOT kept: "web_server" — internal RAM is the scarcest resource on gen
        # builds (large layouts). The web UI is redundant next to the configurator,
        # and under combined load (live camera RX + art decode + HA API) the panel
        # hit sdio_rx_get_buffer asserts / bad_alloc boot-loops; the web server's
        # listener + per-connection buffers were the biggest discretionary cut.
        "drv2605"]   # DRV2605L haptic driver (id: haptic)


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
# Internal RAM is the panel's scarcest resource (SDIO WiFi RX pool, mbedTLS,
# every `new`); log it so famine shows up as a trend line instead of a crash.
HEAP_LOG_INTERVAL_ITEM = (
    "  - interval: 30s\n"
    "    then:\n"
    "      - lambda: |-\n"
    "          ESP_LOGI(\"heap\", \"internal free=%u largest=%u | psram free=%u\",\n"
    "                   (unsigned) heap_caps_get_free_size(MALLOC_CAP_INTERNAL),\n"
    "                   (unsigned) heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),\n"
    "                   (unsigned) heap_caps_get_free_size(MALLOC_CAP_SPIRAM));\n")

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
    "dow_time": ("%A \\u00b7 %I:%M %p", False),   # "Friday · 9:41 PM" (weather hero)
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


def gen_camera_full_page():
    """Fullscreen live camera (generated only when a live camera card exists):
    black page, mjpeg_stream target 1 (800x600, 4:3) into gen_cam_full_img, the
    shared status pill, Refresh (stream restart) + Dismiss back to the page
    hosting the camera card. Mirrors the hand-built page_doorbell chrome."""
    host_pid = CAM_CARDS[0][4]
    return (
        "    - id: page_camera_full\n      bg_color: 0x000000\n      bg_opa: 100%\n      scrollable: false\n"
        "      on_load:\n"
        "        - lvgl.widget.update: { id: nav_rail, hidden: true }\n"
        "        - lambda: 'id(cam_stream).start(1, id(gen_cam_full_img));'\n"
        "      on_unload:\n"
        "        - lvgl.widget.update: { id: nav_rail, hidden: false }\n"
        "        - lambda: 'id(cam_stream).stop();'\n"
        "      widgets:\n"
        "        - image: { id: gen_cam_full_img, src: ss_image, align: center, hidden: true }\n"
        "        - obj: { id: gen_cam_full_pill, x: 20, y: 20, width: 78, height: 30, radius: 15, bg_color: 0x2A2F3A, "
        "border_width: 0, pad_all: 0, scrollable: false, widgets: "
        "[label: { id: gen_cam_full_pill_lbl, text: \"...\", align: center, text_font: f_small, text_color: 0xFFFFFF }] }\n"
        "        - button:\n            align: bottom_right\n            x: -200\n            y: -20\n            width: 150\n            height: 56\n"
        "            bg_color: 0x1B2230\n            radius: 14\n            pad_all: 0\n            scrollable: false\n"
        "            widgets: [label: { text: \"Refresh\", align: center, text_font: f_body, text_color: 0xEEF0F6 }]\n"
        "            on_click:\n              - lambda: 'id(cam_stream).restart();'\n"
        "        - button:\n            align: bottom_right\n            x: -20\n            y: -20\n            width: 160\n            height: 56\n"
        "            bg_color: 0x0E0F14\n            border_width: 1\n            border_color: 0x3A4150\n            radius: 14\n            pad_all: 0\n            scrollable: false\n"
        "            widgets: [label: { text: \"Dismiss\", align: center, text_font: f_body, text_color: 0xC8CCD6 }]\n"
        "            on_click: [lvgl.page.show: " + host_pid + "]\n"
    )


def gen_cam_stream():
    """The generated mjpeg_stream instance (+ pill on_state). ALWAYS defines
    id: cam_stream — the kept esphome: on_boot lambda applies
    $cam_stream_url_override via id(cam_stream), so the id must exist even in
    layouts with no camera card (always-emit chosen over scrubbing on_boot).
    Album art rides this instance too (fetch_still shares its task + JPEG
    accumulator), so a no-camera layout WITH art widgets keeps a full-size
    accumulator — HA-proxied covers regularly exceed 64kB.
    Idle cost note: setup() unconditionally allocates 2 frame buffers sized to
    the LARGEST target + the max_jpeg_size accumulator + a 12kB-stack task, so
    the no-camera instance (64x64 target, 64kB accumulator) still holds ~0.1MB
    PSRAM while never started — fine on the 32MB P4."""
    head = ("\n# Live camera stream (generated; replaces aurora.yaml's hand-sized instance).\n"
            "# Also the album-art stills worker: gen art readbacks call id(cam_stream).fetch_still().\n"
            "mjpeg_stream:\n"
            "  - id: cam_stream\n    max_fps: 8.0\n"
            "    max_source_width: 2048\n    max_source_height: 1536\n")
    tail = "    task_core: 1\n    task_priority: 4\n    read_timeout: 10s\n"
    if not CAM_CARDS:
        acc = ("    max_jpeg_size: 512kB     # no camera card, but album-art stills use this accumulator\n"
               if ART_IMAGES else
               "    max_jpeg_size: 64kB      # idle instance: shrink the always-allocated accumulator\n")
        return (head + acc
                + "    targets:                 # idle placeholder — no camera card in this layout\n"
                  "      - width: 64\n        height: 64\n" + tail)
    _e, iw, ih, base, _pid = CAM_CARDS[0]

    def pills(bg, text):
        s = ""
        for oid, lid in ((base + "_pill", base + "_pill_lbl"),
                         ("gen_cam_full_pill", "gen_cam_full_pill_lbl")):
            s += ("            - lvgl.widget.update: { id: %s, bg_color: %s }\n"
                  "            - lvgl.label.update: { id: %s, text: \"%s\" }\n" % (oid, bg, lid, text))
        return s

    # First LIVE unhides the image widgets (they start hidden over the ss_image
    # placeholder src) and they STAY visible on later errors — the last real
    # frame beats a black hole.
    unhide = ("            - lvgl.widget.update: { id: %s_cam, hidden: false }\n"
              "            - lvgl.widget.update: { id: gen_cam_full_img, hidden: false }\n" % base)
    cond = "      - if:\n          condition:\n            lambda: 'return %s;'\n          then:\n"
    st = "state == esphome::mjpeg_stream::StreamState::%s"
    return (head
            + "    max_jpeg_size: 512kB\n"
            + "    targets:                 # index = target_idx for start(): 0 = card, 1 = fullscreen\n"
            + "      - width: %d\n        height: %d\n" % (iw, ih)
            # Fullscreen target is 4:3 (pillarboxed on the 1024x600 panel):
            # the stream scale-fills with center-crop, so a 16:9-ish target
            # would chop the top/bottom off 4:3 doorbell frames.
            + "      - width: 800\n        height: 600\n"
            + tail
            + "    on_state:                # drive the card + fullscreen pills (STOPPED = no-op)\n"
            + cond % (st % "LIVE") + pills("0xE5484D", "LIVE") + unhide
            + cond % (st % "CONNECTING") + pills("0x2A2F3A", "...")
            + cond % (st % "ERROR_NET" + " || " + st % "ERROR_AUTH") + pills("0x2A2F3A", "OFFLINE"))


def gen_cam_text_sensor():
    """entity_picture readback (aurora.yaml ha_cam_picture pattern): build the
    tokenized snapshot URL, derive the MJPEG stream URL from it, and stash both
    in the kept g_cam_url / g_cam_stream_url globals (globals: is spliced
    verbatim from aurora.yaml — a second top-level globals: key would be invalid
    YAML, so the gen build reuses those two existing ids). Default the stream to
    the SNAPSHOT proxy (~1fps snapshot-poll): HA's camera_proxy_stream calls the
    camera's direct image method, which Nest never implements -> zero frames."""
    return (
        "  - platform: homeassistant\n    id: ha_gen_cam_0\n    entity_id: %s\n    attribute: entity_picture\n"
        "    on_value:\n      then:\n"
        "        - lambda: |-\n"
        "            id(g_cam_url) = std::string(\"$ha_base\") + x;\n"
        "            std::string sp = x;\n"
        "            size_t p = sp.find(\"/api/camera_proxy/\");\n"
        "            if (p != std::string::npos) sp.replace(p, 18, \"/api/camera_proxy_stream/\");\n"
        "            id(g_cam_stream_url) = std::string(\"$ha_base\") + sp;\n"
        "            std::string ov = \"$cam_stream_url_override\";\n"
        "            id(cam_stream).set_url(!ov.empty() ? ov : id(g_cam_url));\n"
        % CAM_CARDS[0][0])


def assemble(layout):
    with open(AURORA, encoding="utf-8") as f:
        secs = split_sections(f.read())
    lvgl_text = dict(secs).get("lvgl", "")
    nav, pages, sens, txt, _, clocks = build_lvgl(layout)  # populates USED_ICON_CP + ART/CAM registries
    # keep the hardware/font/style base; embed the icons this layout uses into f_icon
    keep_text = "".join(inject_used_glyphs(t) if n == "font" else t
                        for n, t in secs if n in KEEP)
    # scrub references to dropped UI scripts + lvgl widget actions in the base
    keep_text = re.sub(r"(?m)^[ \t]*-?[ \t]*script\.(execute|stop):.*\n", "", keep_text)
    keep_text = scrub_lvgl_actions(keep_text)
    clocks += [("lbl_ss_time", "time_hm"), ("lbl_ss_date", "date_full")]   # screensaver clock
    pages += gen_screensaver_page()
    txt.append(SS_TEXT_SENSOR)
    if CAM_CARDS:                                        # live camera: fullscreen page + URL readback
        pages += gen_camera_full_page()
        txt.append(gen_cam_text_sensor())
    # Album art rides the mjpeg_stream stills channel: per entity, one
    # entity_picture readback whose action queues
    # id(cam_stream).fetch_still(url, widget, size, size) for each art widget
    # bound to that entity. The fetch runs on the stream worker task — never
    # the main loop: hand-rolled HTTP + hardware JPEG decode + PPA scale into
    # a per-widget PSRAM buffer, presented (and the widget unhidden) from the
    # component's loop(). The queue is latest-wins per widget, so track-skip
    # storms self-cancel, and the single worker serializes all art + camera
    # work. This replaced the per-(entity,size) online_image decoders +
    # per-entity mode:restart gen_art_seq scripts + g_gen_art_busy flag:
    # online_image downloads AND decodes on the main loop (measured blocking
    # up to ~8s per fetch when HA's media proxy had to fetch from the Spotify
    # CDN), which stalled LVGL, backed up the network stack, and bad_alloc'd
    # the panel under rapid track skips. The HA proxy URL is still the right
    # source: tokenized per state change, plain HTTP on the LAN (TLS-free),
    # and works for every media_player (Sonos/LG too), not just SpotifyPlus.
    if ART_IMAGES:
        by_ent = {}
        for s, iid, e in ART_IMAGES:
            by_ent.setdefault(e, []).append((s, iid))
        for n_i, ent in enumerate(sorted(by_ent)):
            fetches = "".join(
                "              - lambda: 'id(cam_stream).fetch_still(std::string(\"$ha_base\") + x, id(%s), %d, %d);'\n"
                % (iid, s, s)
                for s, iid in by_ent[ent])
            # entity_picture is a relative tokenized proxy path ("/api/media_player_proxy/...").
            txt.append("  - platform: homeassistant\n    id: ha_gen_art_%d\n    entity_id: %s\n    attribute: entity_picture\n"
                       "    on_value:\n      then:\n        - if:\n            condition:\n"
                       "              lambda: 'return x.rfind(\"/\", 0) == 0;'\n            then:\n%s"
                       % (n_i, ent, fetches))
    # Screen-off paths that do NOT navigate would leave the camera page loaded —
    # its on_unload (cam_stream.stop()) never fires and the stream keeps decoding
    # into a dark panel. With a live camera card, show page_home right before the
    # backlight goes off (a no-op when already home); the page switch fires the
    # camera page's on_unload. Exactly two branches turn the backlight off
    # without a page.show: the SS_ONIDLE screensaver-off else (the screensaver-on
    # path already navigates to page_screensaver) and CAM_WAKE_INTERVAL's
    # night-sleep clause. Other layouts' idle behavior is unchanged.
    ss_onidle, cam_wake = SS_ONIDLE, CAM_WAKE_INTERVAL
    if CAM_CARDS:
        a = "                else:\n                  - light.turn_off: display_backlight\n"
        b = "            - lambda: 'id(g_screen_off) = true;'\n            - light.turn_off: display_backlight\n"
        assert a in ss_onidle and b in cam_wake, "screen-off anchors moved; camera stream would leak while dark"
        ss_onidle = ss_onidle.replace(
            a, "                else:\n                  - lvgl.page.show: page_home\n"
               "                  - light.turn_off: display_backlight\n")
        cam_wake = cam_wake.replace(
            b, "            - lambda: 'id(g_screen_off) = true;'\n"
               "            - lvgl.page.show: page_home\n"
               "            - light.turn_off: display_backlight\n")
    # interval: list = trackpad flush + camera night-wake + screensaver cycle + clock repaint
    out = keep_text + TP_FLUSH_INTERVAL + cam_wake + SS_INTERVAL_ITEM + HEAP_LOG_INTERVAL_ITEM + clock_items(clocks)
    out += SS_ONLINE_IMAGE + SS_SCRIPT + gen_cam_stream()
    if sens:
        out += "\nsensor:\n" + "".join(sens)
    out += "\ntext_sensor:\n" + "".join(txt)
    out += ("\nlvgl:\n"
            "  buffer_size: 25%\n"
            + ss_onidle                                   # enter screensaver on idle timeout
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
    global ART_ENABLED
    ART_ENABLED = False                                  # host build: no online_image decoders
    try:
        nav, pages, _sens, _txt, _, _clocks = build_lvgl(layout)   # host has no ha_time -> no clock interval
    finally:
        ART_ENABLED = True
    keep = "".join(inject_used_glyphs(t) if n == "font" else t
                   for n, t in secs if n in ("substitutions", "globals", "font"))
    keep = re.sub(r"(?m)^[ \t]*-?[ \t]*script\.(execute|stop):.*\n", "", keep)
    keep = scrub_lvgl_actions(keep)
    pages = re.sub(r"(?m)^\s*- image: \{ src: img_aurora_bg.*\n", "", pages)
    # host build has no display_backlight light / restart button / drv2605 haptic —
    # stub those local actions/refs (the emulator has none of that hardware)
    pages = re.sub(r"light\.turn_on: \{ id: display_backlight[^}]*\}", "logger.log: emul", pages)
    pages = pages.replace("button.press: btn_restart_panel", "logger.log: emul")
    pages = re.sub(r"id\(haptic\)\.[A-Za-z_]+\([^;]*\);", "", pages)   # strip haptic calls (settings page)
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
