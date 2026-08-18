#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "FFmpeg not found" >&2; exit 1; }
command -v ffprobe >/dev/null || { echo "ffprobe not found" >&2; exit 1; }
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Fill MISTRAL_API and REFERRAL_URL, then run again." >&2
  exit 1
fi
exec .venv/bin/python main.py "$@"
