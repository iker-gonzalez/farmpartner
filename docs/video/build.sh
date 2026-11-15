#!/bin/sh
# Builds farmpartner-30s.mp4: voice-over (vo.py) + frames (render.mjs) + ffmpeg. Run from docs/video.
set -e
cd "$(dirname "$0")"
mkdir -p build && [ -d build/node_modules/playwright-core ] || (cd build && npm init -y >/dev/null && npm i playwright-core >/dev/null)
[ -f build/vo0.wav ] || python3 vo.py
[ -f build/music.wav ] || uv run -q --with numpy python music.py
node render.mjs
# each voice-over clip starts with its scene (seconds → ms)
# each voice-over clip starts with its scene (ms); the music sits under it and dips while someone is speaking
ffmpeg -v error -y -framerate 30 -i build/frames/f%04d.png \
  -i build/vo0.wav -i build/vo1.wav -i build/vo2.wav -i build/vo3.wav -i build/vo4.wav -i build/music.wav \
  -filter_complex "[1]adelay=500:all=1[a0];[2]adelay=6600:all=1[a1];[3]adelay=10500:all=1[a2];[4]adelay=21200:all=1[a3];[5]adelay=26000:all=1[a4];\
[a0][a1][a2][a3][a4]amix=inputs=5:normalize=0,apad=whole_dur=30,aformat=channel_layouts=stereo,asplit[vo][key];\
[6]volume=0.5[m];[m][key]sidechaincompress=threshold=0.02:ratio=6:attack=40:release=500[duck];\
[vo][duck]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5[a]" \
  -map 0:v -map "[a]" -t 30 -c:v libx264 -preset slow -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k -ar 48000 -movflags +faststart \
  farmpartner-30s.mp4
ffprobe -v 0 -show_entries format=duration,size -of csv=p=0 farmpartner-30s.mp4
