# Aurora · LG TV Trackpad bridge

Gives the Aurora panel a working **cursor trackpad** for your LG webOS TV — the
same "Magic Remote" pointer the LG ThinQ app uses.

## Why this is needed

The LG app feels seamless because it opens webOS's dedicated **pointer input
socket** and streams `move`/`click` packets straight to the TV over your LAN.
Home Assistant's built-in `webostv` integration does **not** expose that socket,
so Aurora can't reach it through normal HA service calls. This bridge
(`lg_pointer.py`) opens that socket itself and exposes simple HA services the
panel's trackpad calls:

| Service | Args | Effect |
|---|---|---|
| `pyscript.lg_pointer_move` | `dx`, `dy` | move cursor |
| `pyscript.lg_pointer_click` | – | left click |
| `pyscript.lg_pointer_scroll` | `dy` | wheel scroll |
| `pyscript.lg_pointer_button` | `name` | UP/DOWN/LEFT/RIGHT/ENTER/BACK/HOME/… |

> **Latency:** moves go panel → HA → TV, so expect ~100–200 ms — fine for
> nudging the cursor, not as instant as the native app talking straight to the TV.

## Install

1. **Install Pyscript.** Via HACS ("Pyscript Python scripting") or manually, then
   add to `configuration.yaml`:
   ```yaml
   pyscript:
     allow_all_imports: true
     hass_is_global: true
   ```
   Restart Home Assistant.

2. **Drop in the module.** Copy `lg_pointer.py` to `<config>/pyscript/lg_pointer.py`.

3. **Set your TV IP.** Edit the top of `lg_pointer.py`:
   ```python
   LG_TV_IP = "10.0.0.174"   # your LG TV
   LG_CLIENT_KEY = ""        # leave blank for first pairing
   ```

4. **Pair once.** Reload pyscript (Developer Tools → YAML → "Pyscript" reload, or
   restart HA). Then in Developer Tools → Actions, call `pyscript.lg_pointer_move`
   with `dx: 10, dy: 0`. The TV shows a pairing prompt — **accept it**. Watch the
   HA log for:
   ```
   LG pointer: paired, client-key = XXXXXXXX...
   ```
   Paste that value into `LG_CLIENT_KEY` and reload pyscript. From now on it
   reconnects silently.

5. **Use it.** On the panel: **Living Room → LG remote → Trackpad**. Drag on the
   big surface to move the cursor; tap to click; use Click / Back / Home / Scroll.

## Troubleshooting

- **No pairing prompt / connection error in log:** confirm the TV IP, that the TV
  is on, and that "Mobile TV On / network standby" is enabled so it accepts
  connections. The bridge tries `wss://IP:3001` then `ws://IP:3000`.
- **Cursor doesn't move but click works (or vice-versa):** check the HA log for
  `LG pointer:` lines; share them and we can adjust.
- **Cursor moves the wrong distance:** tune the move scale on the panel side
  (the trackpad sends raw touch deltas; we can multiply them in firmware).
