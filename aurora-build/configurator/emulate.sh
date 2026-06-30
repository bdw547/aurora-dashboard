#!/usr/bin/env bash
# Aurora LVGL emulator — render the generated device UI on the desktop and
# screenshot it, WITHOUT flashing the panel. Uses ESPHome's host platform +
# SDL display + a headless Xvfb, so it's the same LVGL/fonts/layout as the device.
#
#   ./emulate.sh           # render the layout, screenshot the home page -> ~/aurora_emul.png
#   ./emulate.sh --all     # cycle every page, screenshot each -> ~/emul_shots/frame_NN.png
#
# Requires: libsdl2, imagemagick, xvfb (apt).
set -uo pipefail
ROOT="$HOME/espcontrol"
PY="$ROOT/.venv-dev/bin/python"
ESPHOME="$ROOT/.venv-dev/bin/esphome"
DEV="$ROOT/devices/guition-esp32-p4-jc1060p470"
EMUL="$DEV/aurora-emul.yaml"
BIN="$DEV/.esphome/build/aurora-emul/.pioenvs/aurora-emul/program"
ALL=0; [ "${1:-}" = "--all" ] && ALL=1

GENARGS="--host"; [ $ALL -eq 1 ] && GENARGS="--host --cycle"
echo "=== generate ($GENARGS) ==="
"$PY" "$ROOT/aurora-build/configurator/gen.py" $GENARGS || exit 1
echo "=== compile (host) ==="
"$ESPHOME" compile "$EMUL" > /tmp/emul_compile.log 2>&1 || { echo "COMPILE FAIL"; tail -40 /tmp/emul_compile.log; exit 1; }
[ -x "$BIN" ] || { echo "no binary at $BIN"; exit 1; }
echo "compiled OK"

pkill -f "Xvfb :99" 2>/dev/null; sleep 1
Xvfb :99 -screen 0 1024x600x24 -nolisten tcp > /tmp/xvfb.log 2>&1 &
XVFB=$!; sleep 2
export DISPLAY=:99 SDL_VIDEODRIVER=x11
"$BIN" > /tmp/emul_prog.log 2>&1 &
PROG=$!; sleep 2

if [ $ALL -eq 1 ]; then
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
