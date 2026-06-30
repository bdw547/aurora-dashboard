#!/usr/bin/env bash
# Aurora LVGL emulator — render the generated device UI on the desktop, the same
# LVGL/fonts/layout as the panel, via ESPHome's host platform + SDL display.
#
#   ./emulate.sh          # screenshot the home page  -> ~/aurora_emul.png   (headless)
#   ./emulate.sh --all    # screenshot every page     -> ~/emul_shots/frame_NN.png (headless)
#   ./emulate.sh --live    # open a LIVE, clickable window on your desktop (WSLg)
#
# Requires: libsdl2, imagemagick, xvfb (apt). Live mode needs WSLg (Win11 WSL2).
set -uo pipefail
ROOT="$HOME/espcontrol"
PY="$ROOT/.venv-dev/bin/python"
ESPHOME="$ROOT/.venv-dev/bin/esphome"
DEV="$ROOT/devices/guition-esp32-p4-jc1060p470"
EMUL="$DEV/aurora-emul.yaml"
BIN="$DEV/.esphome/build/aurora-emul/.pioenvs/aurora-emul/program"
MODE="${1:-}"

GENARGS="--host"; [ "$MODE" = "--all" ] && GENARGS="--host --cycle"
echo "=== generate ($GENARGS) ==="
"$PY" "$ROOT/aurora-build/configurator/gen.py" $GENARGS || exit 1
echo "=== compile (host) ==="
"$ESPHOME" compile "$EMUL" > /tmp/emul_compile.log 2>&1 || { echo "COMPILE FAIL"; tail -40 /tmp/emul_compile.log; exit 1; }
[ -x "$BIN" ] || { echo "no binary at $BIN"; exit 1; }
echo "compiled OK"

if [ "$MODE" = "--live" ]; then
  echo "Opening live window on your desktop — click the nav rail to move between pages."
  echo "Close the window (or Ctrl+C here) to stop."
  exec "$BIN"            # inherits the session DISPLAY (WSLg :0) -> real window
fi

# --- headless screenshot modes (virtual X) ---
pkill -f "Xvfb :99" 2>/dev/null; sleep 1
Xvfb :99 -screen 0 1024x600x24 -nolisten tcp > /tmp/xvfb.log 2>&1 &
XVFB=$!; sleep 2
export DISPLAY=:99 SDL_VIDEODRIVER=x11
"$BIN" > /tmp/emul_prog.log 2>&1 &
PROG=$!; sleep 2
if [ "$MODE" = "--all" ]; then
  SHOTS="$HOME/emul_shots"; mkdir -p "$SHOTS"; rm -f "$SHOTS"/*.png
  for i in $(seq 1 10); do
    n=$(printf "%02d" "$i")
    import -window root "$SHOTS/frame_$n.png" 2>/dev/null && echo "captured frame_$n"
    sleep 4
  done
  echo "shots in $SHOTS"
else
  sleep 7
  import -window root "$HOME/aurora_emul.png" 2>/dev/null && echo "shot -> $HOME/aurora_emul.png"
fi
kill $PROG 2>/dev/null; pkill -f "aurora-emul" 2>/dev/null; kill $XVFB 2>/dev/null
echo "done"
