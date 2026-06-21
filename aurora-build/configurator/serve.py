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
}

CARD_TEMPLATES = {
    "climate": '''        - obj:
            x: {X}
            y: {Y}
            width: 288
            height: 218
            styles: st_glass
            scrollable: false
            clickable: true
            on_click: [lvgl.page.show: page_climate]
            widgets:
              - label: {{ text: "CLIMATE", x: 4, y: 2, text_font: f_small, text_color: 0x8A8F9E }}
              - label: {{ id: lbl_home_temp_big, text: "--", x: 2, y: 46, text_font: f_display, text_color: 0xEEF0F6 }}
              - label: {{ id: lbl_home_cond, text: "--", x: 4, y: 138, text_font: f_body, text_color: 0x2ED5B8 }}
              - label: {{ text: "Outdoor", x: 4, y: 168, text_font: f_small, text_color: 0x8A8F9E }}
''',
    "lights": '''        - obj:
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
}


def _card_type(block):
    if "page_climate" in block: return "climate"
    if "page_lights" in block: return "lights"
    if "page_security" in block: return "doors"
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
    if sorted(order) != sorted(CARD_TEMPLATES):
        raise ValueError("layout must use each of the 4 cards exactly once")
    text = open(YAML, encoding="utf-8").read()
    cards = "".join(CARD_TEMPLATES[order[i]].format(X=HOME_CELLS[i][0], Y=HOME_CELLS[i][1])
                    for i in range(4))
    head, rest = text.split(GRID_START, 1)
    _, tail = rest.split(GRID_END, 1)
    text = (head + GRID_START + " (generated by the configurator — do not hand-edit)\n"
            + cards + "        " + GRID_END + tail)
    open(YAML, "w", encoding="utf-8").write(text)
    return order


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


# --- flash job (async, log captured for polling) ---
FLASH = {"running": False, "log": "", "done": False, "ok": False}


def flash_job(device):
    FLASH.update(running=True, log="", done=False, ok=False)
    try:
        proc = subprocess.Popen(
            [ESPHOME, "run", YAML, "--device", device, "--no-logs"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            cwd=os.path.join(HERE, "..", ".."))
        for line in proc.stdout:
            FLASH["log"] += line
        proc.wait()
        FLASH["ok"] = proc.returncode == 0
    except Exception as e:  # noqa
        FLASH["log"] += f"\n[error] {e}\n"
    FLASH.update(running=False, done=True)


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _json(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or "{}")

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE, "text/html")
        if self.path == "/api/slots":
            return self._send(200, json.dumps(read_slots()))
        if self.path == "/api/home":
            return self._send(200, json.dumps({"order": read_home_layout(), "meta": CARD_META}))
        if self.path == "/api/flash-status":
            return self._send(200, json.dumps(FLASH))
        return self._send(404, "{}")

    def do_POST(self):
        try:
            d = self._json()
            if self.path == "/api/entities":
                return self._send(200, json.dumps(ha_entities(d["url"], d["token"])))
            if self.path == "/api/save":
                return self._send(200, json.dumps({"saved": write_bindings(d["bindings"])}))
            if self.path == "/api/home":
                return self._send(200, json.dumps({"order": write_home_layout(d["order"])}))
            if self.path == "/api/flash":
                if not FLASH["running"]:
                    threading.Thread(target=flash_job, args=(d["device"],), daemon=True).start()
                return self._send(200, json.dumps({"started": True}))
        except Exception as e:  # noqa
            return self._send(500, json.dumps({"error": str(e)}))
        return self._send(404, "{}")


PAGE = r"""<!doctype html><html><head><meta charset=utf-8>
<title>Aurora Configurator</title><meta name=viewport content="width=device-width,initial-scale=1">
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
</style></head><body>
<header><h1><span>Aurora</span> Configurator</h1><div class=sub>Point the panel at your Home Assistant — no code.</div></header>
<main>
<div class=card><h2>1 · Connect to Home Assistant</h2>
<div class=row><div><label>HA URL <span class=muted>(use the IP, not .local)</span></label><input id=url placeholder="http://10.0.0.50:8123"></div>
<div><label>Long-lived access token <span class=muted>(HA → profile → bottom)</span></label><input id=token type=password placeholder="paste token"></div></div>
<div style="margin-top:12px" class=bar><button onclick=connect()>Load my entities</button><span id=cmsg class=muted></span></div></div>

<div class=card id=slotcard style=display:none><h2>2 · Map entities</h2><div id=slots></div></div>

<div class=card><h2>3 · Home screen layout <span class=muted>(drag the cards to rearrange)</span></h2>
<div class=preview><div class=np>Now&nbsp;Playing<br><small>(fixed)</small></div><div id=homegrid class=hgrid></div></div></div>

<div class=card id=flashcard><h2>4 · Save &amp; flash</h2>
<div class=row><div><label>Panel IP address</label><input id=device placeholder="10.0.0.174"></div><div></div></div>
<div style="margin-top:12px" class=bar><button class=ghost onclick=save()>Save bindings</button><button onclick=flash()>Save &amp; flash panel</button><span id=fmsg class=muted></span></div>
<pre id=flog style=display:none></pre></div>
</main>
<script>
let SLOTS=[],ENTS=[],HORDER=[],HMETA={};
async function j(u,o){const r=await fetch(u,o);if(!r.ok)throw new Error((await r.json()).error||r.status);return r.json()}
async function boot(){SLOTS=await j('/api/slots');loadHome()}
async function loadHome(){const r=await j('/api/home');HORDER=r.order;HMETA=r.meta;renderHome()}
function renderHome(){homegrid.innerHTML=HORDER.map((k,i)=>`<div class=hcell draggable=true data-i="${i}"><div class=n>${HMETA[k].name}</div><div class=h>${HMETA[k].hint}</div><div class=d>cell ${i+1}</div></div>`).join('');
 document.querySelectorAll('.hcell').forEach(c=>{
  c.ondragstart=e=>{e.dataTransfer.setData('i',c.dataset.i);c.classList.add('drag')};
  c.ondragend=()=>c.classList.remove('drag');
  c.ondragover=e=>{e.preventDefault();c.classList.add('over')};
  c.ondragleave=()=>c.classList.remove('over');
  c.ondrop=e=>{e.preventDefault();c.classList.remove('over');const a=+e.dataTransfer.getData('i'),b=+c.dataset.i;[HORDER[a],HORDER[b]]=[HORDER[b],HORDER[a]];renderHome()}})}
async function saveHome(){await j('/api/home',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order:HORDER})})}
async function connect(){
 const url=url_.value.trim(),token=token_.value.trim();cmsg.textContent='Loading…';cmsg.className='muted';
 try{ENTS=await j('/api/entities',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,token})});
  cmsg.textContent='Loaded '+ENTS.length+' entities ✓';cmsg.className='ok';render();slotcard.style.display='';flashcard.style.display=''}
 catch(e){cmsg.textContent='Failed: '+e.message+' (check URL/token & that this PC can reach HA)';cmsg.className='err'}}
function render(){let g='',h='';for(const s of SLOTS){if(s.group!=g){g=s.group;h+=`<div class=grp>${g}</div>`}
  const opts=ENTS.filter(e=>e.domain==s.domain).map(e=>`<option value="${e.entity_id}" ${e.entity_id==s.value?'selected':''}>${e.name} — ${e.entity_id}</option>`).join('');
  h+=`<div class=slot><div class=l>${s.label}<small>${s.domain}</small></div><select data-v="${s.var}"><option value="">— none —</option>${opts}</select></div>`}
 slots.innerHTML=h}
function bindings(){const b={};document.querySelectorAll('#slots select').forEach(x=>b[x.dataset.v]=x.value);return b}
async function save(){fmsg.className='muted';fmsg.textContent='Saving…';try{const r=await j('/api/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bindings:bindings()})});fmsg.textContent='Saved '+r.saved+' bindings ✓';fmsg.className='ok'}catch(e){fmsg.textContent=e.message;fmsg.className='err'}}
async function flash(){await save();await saveHome();const device=device_.value.trim();if(!device){fmsg.textContent='Enter the panel IP';fmsg.className='err';return}
 flog.style.display='';flog.textContent='';fmsg.textContent='Building + flashing…';fmsg.className='muted';
 await j('/api/flash',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({device})});
 const t=setInterval(async()=>{const s=await j('/api/flash-status');flog.textContent=s.log;flog.scrollTop=flog.scrollHeight;
  if(s.done){clearInterval(t);fmsg.textContent=s.ok?'Flashed ✓':'Flash failed — see log';fmsg.className=s.ok?'ok':'err'}},1500)}
window.url_=document.getElementById('url');window.token_=document.getElementById('token');window.device_=document.getElementById('device');
boot()
</script></body></html>"""

if __name__ == "__main__":
    print(f"Aurora Configurator → http://localhost:{PORT}  (firmware: {YAML})")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
