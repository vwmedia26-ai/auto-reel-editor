#!/usr/bin/env bash
# Auto Reel Editor: one-command setup.
#   bash install.sh
# Installs Python packages, makes sure ffmpeg and a headless Chromium are present, downloads the
# Whisper model (~1.6 GB, needs network access to huggingface.co and its *.hf.co / xethub hosts),
# then runs a self-test render. Safe to run again; finished steps are skipped.
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
export ARE_MODELS="${ARE_MODELS:-$HOME/.cache/auto-reel-editor/models}"
say() { printf '\n== %s\n' "$*"; }

say "Python packages"
PIP="python3 -m pip install -q"
$PIP faster-whisper playwright numpy pillow 2>/dev/null || $PIP --break-system-packages faster-whisper playwright numpy pillow

say "ffmpeg"
if ! command -v ffmpeg >/dev/null || ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q " zscale "; then
  if command -v apt-get >/dev/null && [ "$(id -u)" = 0 ]; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg >/dev/null
  else
    echo "Please install ffmpeg (with zscale / libzimg) and run this again."; exit 1
  fi
fi
ffmpeg -hide_banner -version | head -1

say "Headless Chromium"
if [ -d /opt/pw-browsers ] && ls /opt/pw-browsers | grep -q chromium; then
  export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
  echo "using preinstalled browsers in /opt/pw-browsers"
else
  python3 -m playwright install chromium || python3 -m playwright install --with-deps chromium
fi

say "Whisper model (large-v3-turbo) into $ARE_MODELS"
mkdir -p "$ARE_MODELS"
python3 - <<'PY'
import os
from faster_whisper import WhisperModel
WhisperModel("large-v3-turbo", device="cpu", compute_type="int8", download_root=os.environ["ARE_MODELS"])
print("model ready")
PY

say "Self-test"
python3 "$KIT/scripts/selftest.py"
say "Done. Start an edit with:  python3 $KIT/scripts/run.py init <folder> <clip1> [clip2 ...]"
