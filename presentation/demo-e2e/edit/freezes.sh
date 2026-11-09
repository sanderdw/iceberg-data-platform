#!/usr/bin/env bash
# Static stretches (no pixel change for 0.8 s or more) per clip, for tightening idle time: edit/freezes.json
cd "$(dirname "$0")/.."
echo "{" > edit/freezes.json.tmp
first=1
for f in recordings/*.mp4; do
  name=$(basename "$f" .mp4)
  spans=$(ffmpeg -hide_banner -i "$f" -vf "freezedetect=n=0.00001:d=0.8" -map 0:v -f null - 2>&1 |
    awk '/freeze_start/{s=$NF} /freeze_end/{printf "[%s,%s],", s, $NF}' | sed 's/,$//')
  [ $first = 1 ] || echo "," >> edit/freezes.json.tmp
  first=0
  printf '"%s": [%s]' "$name" "$spans" >> edit/freezes.json.tmp
done
echo "}" >> edit/freezes.json.tmp
mv edit/freezes.json.tmp edit/freezes.json
