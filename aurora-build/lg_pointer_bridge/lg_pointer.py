# =============================================================================
# Aurora · LG webOS pointer bridge  (pyscript module)
# -----------------------------------------------------------------------------
# Gives Home Assistant the same "Magic Remote" cursor control the LG ThinQ app
# uses, by opening webOS's dedicated *pointer input socket* and streaming
# move/click/scroll/button packets straight to the TV over your LAN.
#
# HA's built-in `webostv` integration cannot do this (it has no pointer socket),
# so Aurora's on-screen trackpad calls the services exposed here instead.
#
# INSTALL
#   1. Install the "Pyscript" integration (HACS or manually) and in
#      configuration.yaml add:
#          pyscript:
#            allow_all_imports: true
#            hass_is_global: true
#   2. Copy this file to:  <config>/pyscript/lg_pointer.py
#   3. Set LG_TV_IP below to your TV's IP.
#   4. Leave LG_CLIENT_KEY blank the first time, reload pyscript, then call
#      service `pyscript.lg_pointer_move` once. The TV shows a pairing prompt —
#      accept it. The client key is then printed to the HA log:
#          "LG pointer: paired, client-key = XXXXdeadbeef..."
#      Paste that value into LG_CLIENT_KEY and reload pyscript so it reconnects
#      silently from now on.
#
# SERVICES
#   pyscript.lg_pointer_move   (dx: int, dy: int)      relative cursor move
#   pyscript.lg_pointer_click  ()                       left click
#   pyscript.lg_pointer_scroll (dy: int)                wheel scroll
#   pyscript.lg_pointer_button (name: str)              UP/DOWN/LEFT/RIGHT/
#                                                        ENTER/BACK/HOME/...
#
# NOTE: latency is panel -> HA -> TV, so expect ~100-200ms vs the native app
# talking straight to the TV. Good for nudging the cursor, not pixel-perfect.
# =============================================================================

import asyncio
import json
import ssl

import aiohttp

LG_TV_IP = "10.0.0.174"      # <-- CHANGE ME to your LG TV's IP
LG_CLIENT_KEY = ""           # <-- paste the key from the log after first pairing

# webOS registration manifest (standard permission set used by remote apps)
_REGISTER_PAYLOAD = {
    "type": "register",
    "id": "register_0",
    "payload": {
        "forcePairing": False,
        "pairingType": "PROMPT",
        "manifest": {
            "manifestVersion": 1,
            "appVersion": "1.1",
            "signed": {
                "created": "20140509",
                "appId": "com.lge.test",
                "vendorId": "com.lge",
                "localizedAppNames": {"": "Aurora Remote"},
                "localizedVendorNames": {"": "LG Electronics"},
                "permissions": ["TEST_SECURE", "CONTROL_INPUT_TEXT",
                                 "CONTROL_MOUSE_AND_KEYBOARD", "READ_INSTALLED_APPS",
                                 "READ_LGE_SDX", "READ_NOTIFICATIONS", "SEARCH",
                                 "WRITE_SETTINGS", "WRITE_NOTIFICATION_ALERT",
                                 "CONTROL_POWER", "READ_CURRENT_CHANNEL",
                                 "READ_RUNNING_APPS"],
                "serial": "2f930e2d2cfe083771f68e4fe7bb07",
            },
            "permissions": [
                "LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CLOSE",
                "TEST_OPEN", "TEST_PROTECTED", "CONTROL_AUDIO",
                "CONTROL_DISPLAY", "CONTROL_INPUT_JOYSTICK",
                "CONTROL_INPUT_MEDIA_RECORDING", "CONTROL_INPUT_MEDIA_PLAYBACK",
                "CONTROL_INPUT_TV", "CONTROL_POWER", "READ_APP_STATUS",
                "READ_CURRENT_CHANNEL", "READ_INPUT_DEVICE_LIST",
                "READ_NETWORK_STATE", "READ_RUNNING_APPS", "READ_TV_CHANNEL_LIST",
                "WRITE_NOTIFICATION_TOAST", "READ_POWER_STATE",
                "READ_COUNTRY_INFO", "CONTROL_INPUT_TEXT",
                "CONTROL_MOUSE_AND_KEYBOARD", "READ_INSTALLED_APPS",
            ],
            "signatures": [{
                "signatureVersion": 1,
                "signature": "eyJhbGdvcml0aG0iOiJSU0EtU0hBMjU2Iiwia2V5SWQiOiJ0ZXN0LXNpZ25pbmctY2VydCIsInNpZ25hdHVyZVZlcnNpb24iOjF9.hrVRgjCwXVvE2OOSpDZ58hR+59aFNwYDyjQgKk3auukd7pcegmE2CzPCa0bJ0ZsRAcKkCTJrWo5iDzNhMBWRyaMOv5zWSrthlf7G128qvIlpMT0YNY+n/FaOHE73uLrS/g7swl3/qH/BGFG2Hu4RlL48eb3lLKqTt2xKHdCs6Cd4RMfJPYnzgvI4BNrFUKsjkcu+WD4OO2A27Pq1n50cMchmcaXadJhGrOqH5YmHdOCj5NSHzJYrsW0HPlpuAx/ECMeIZYDh6RMqaFM2DXzdKX9NmmyqzJ3o/0lkk/N97gfVRLW5hA29yeAwaCViZNCP8iC9aO0q9fQojoa7NQnAtw==",
            }],
        },
    },
}

_lock = asyncio.Lock()
_state = {"ws": None, "pointer": None, "session": None, "ip": None}


async def _connect():
    """(Re)establish control socket + pointer socket. Returns pointer ws."""
    if _state["pointer"] is not None and not _state["pointer"].closed:
        return _state["pointer"]

    # Clean up any stale sockets/session.
    for k in ("pointer", "ws"):
        if _state[k] is not None and not _state[k].closed:
            await _state[k].close()
        _state[k] = None
    if _state["session"] is not None and not _state["session"].closed:
        await _state["session"].close()

    session = aiohttp.ClientSession()
    _state["session"] = session
    _state["ip"] = LG_TV_IP

    sslctx = ssl.create_default_context()
    sslctx.check_hostname = False
    sslctx.verify_mode = ssl.CERT_NONE

    # webOS 2nd-gen+ uses wss:3001; older uses ws:3000. Try secure first.
    ws = None
    for url, kw in ((f"wss://{LG_TV_IP}:3001", {"ssl": sslctx}),
                    (f"ws://{LG_TV_IP}:3000", {})):
        try:
            ws = await session.ws_connect(url, heartbeat=30, **kw)
            break
        except Exception as e:  # noqa: BLE001
            log.debug(f"LG pointer: connect {url} failed: {e}")
    if ws is None:
        await session.close()
        raise ConnectionError(f"Cannot reach LG TV at {LG_TV_IP}")
    _state["ws"] = ws

    # Register / pair.
    payload = json.loads(json.dumps(_REGISTER_PAYLOAD))
    if LG_CLIENT_KEY:
        payload["payload"]["client-key"] = LG_CLIENT_KEY
    await ws.send_str(json.dumps(payload))

    registered = False
    for _ in range(60):  # ~30s to accept the on-screen prompt
        msg = await ws.receive(timeout=30)
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        data = json.loads(msg.data)
        if data.get("type") == "registered":
            key = data.get("payload", {}).get("client-key")
            if key and key != LG_CLIENT_KEY:
                log.warning(f"LG pointer: paired, client-key = {key}  "
                            f"(paste this into LG_CLIENT_KEY in lg_pointer.py)")
            registered = True
            break
        if data.get("type") == "response" and data.get("payload", {}).get("pairingType"):
            continue  # prompt shown, keep waiting
    if not registered:
        raise PermissionError("LG TV did not complete pairing (accept the prompt)")

    # Ask for the pointer input socket URL.
    await ws.send_str(json.dumps({
        "type": "request",
        "id": "ptr_0",
        "uri": "ssap://com.webos.service.networkinput/getPointerInputSocket",
    }))
    socket_path = None
    for _ in range(10):
        msg = await ws.receive(timeout=10)
        if msg.type != aiohttp.WSMsgType.TEXT:
            continue
        data = json.loads(msg.data)
        socket_path = data.get("payload", {}).get("socketPath")
        if socket_path:
            break
    if not socket_path:
        raise ConnectionError("LG TV did not return a pointer socket")

    pkw = {"ssl": sslctx} if socket_path.startswith("wss") else {}
    pointer = await session.ws_connect(socket_path, heartbeat=30, **pkw)
    _state["pointer"] = pointer
    log.info("LG pointer: connected")
    return pointer


async def _send(text):
    async with _lock:
        for attempt in (1, 2):
            try:
                ptr = await _connect()
                await ptr.send_str(text)
                return
            except Exception as e:  # noqa: BLE001
                log.warning(f"LG pointer: send failed (attempt {attempt}): {e}")
                _state["pointer"] = None  # force reconnect next time
        log.error("LG pointer: giving up after reconnect")


@service
async def lg_pointer_move(dx=0, dy=0):
    """Move the TV cursor by (dx, dy) pixels."""
    await _send(f"type:move\ndx:{int(dx)}\ndy:{int(dy)}\ndown:0\n\n")


@service
async def lg_pointer_click():
    """Left-click at the current cursor position."""
    await _send("type:click\n\n")


@service
async def lg_pointer_scroll(dy=0):
    """Scroll the wheel by dy (positive = up)."""
    await _send(f"type:scroll\ndx:0\ndy:{int(dy)}\n\n")


@service
async def lg_pointer_button(name="ENTER"):
    """Send a named button over the pointer socket (UP/DOWN/LEFT/RIGHT/ENTER/BACK/HOME/...)."""
    await _send(f"type:button\nname:{str(name).upper()}\n\n")
