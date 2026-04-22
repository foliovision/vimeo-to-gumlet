#!/usr/bin/env bash
# Generate 2 synthetic FV-style video folders for testing a Gumlet uploader.
# Folder naming: "{video_id} {video name}"
# Contents per folder:
#   - {name}-source.mp4          (high-quality source)
#   - {name}-{360|540|720|1080}p.mp4
#   - subtitles-en-English {name}.vtt.vtt
#   - subtitles-en-Updated English {name}.vtt.vtt
#   - subtitles-sk-Slovak {name}.vtt.vtt
#   - subtitles-sk-Updated Slovak {name}.vtt.vtt
#   - thumbnail-{100x75|200x150|295x166|640x360|960x540|1280x720}.jpg
#   - video-details.json
set -euo pipefail

ROOT="${1:-/home/ubuntu/fv-videos}"
mkdir -p "$ROOT"
cd "$ROOT"

declare -A VIDEOS=(
  ["196881410"]="Foliovision Promo Video"
  ["196881411"]="FV Player Demo"
)

# Source resolution → frame size
SRC_SIZE="1920x1080"

make_video() {
  local folder="$1" name="$2" color="$3" label="$4"
  local out

  # Source (1080p high bitrate)
  out="$folder/${name}-source.mp4"
  ffmpeg -y -f lavfi -i "color=c=${color}:s=${SRC_SIZE}:r=25:d=5" \
    -vf "drawtext=text='${label} SOURCE':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=(h-text_h)/2" \
    -c:v libx264 -pix_fmt yuv420p -crf 18 -t 5 "$out" >/dev/null 2>&1

  # Renditions
  for res in 1080 720 540 360; do
    case "$res" in
      1080) size="1920x1080" ;;
      720)  size="1280x720"  ;;
      540)  size="960x540"   ;;
      360)  size="640x360"   ;;
    esac
    out="$folder/${name}-${res}p.mp4"
    ffmpeg -y -f lavfi -i "color=c=${color}:s=${size}:r=25:d=5" \
      -vf "drawtext=text='${label} ${res}p':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2" \
      -c:v libx264 -pix_fmt yuv420p -crf 23 -t 5 "$out" >/dev/null 2>&1
  done
}

make_thumbnails() {
  local folder="$1" label="$2" color="$3"
  for size in 100x75 200x150 295x166 640x360 960x540 1280x720; do
    local out="$folder/thumbnail-${size}.jpg"
    ffmpeg -y -f lavfi -i "color=c=${color}:s=${size}" \
      -vf "drawtext=text='${label}':fontcolor=white:fontsize=20:x=(w-text_w)/2:y=(h-text_h)/2" \
      -frames:v 1 "$out" >/dev/null 2>&1
  done
}

make_vtt() {
  local path="$1" title="$2" lang_label="$3"
  cat >"$path" <<EOF
WEBVTT

00:00:00.000 --> 00:00:02.500
${lang_label}: ${title}

00:00:02.500 --> 00:00:05.000
Sample subtitle line two.
EOF
}

make_details_json() {
  local path="$1" id="$2" name="$3"
  cat >"$path" <<EOF
{
  "id": "${id}",
  "name": "${name}",
  "duration": 5,
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "renditions": ["360p", "540p", "720p", "1080p", "source"],
  "subtitles": [
    {"lang": "en", "label": "English ${name}"},
    {"lang": "en", "label": "Updated English ${name}"},
    {"lang": "sk", "label": "Slovak ${name}"},
    {"lang": "sk", "label": "Updated Slovak ${name}"}
  ]
}
EOF
}

i=0
colors=("SteelBlue" "DarkGreen")
for id in "${!VIDEOS[@]}"; do
  name="${VIDEOS[$id]}"
  folder="$ROOT/${id} ${name}"
  mkdir -p "$folder"

  color="${colors[$i]}"
  i=$((i+1))

  echo "Generating: $folder"

  make_video "$folder" "$name" "$color" "$name"
  make_thumbnails "$folder" "$name" "$color"

  make_vtt "$folder/subtitles-en-English ${name}.vtt.vtt"           "$name" "English"
  make_vtt "$folder/subtitles-en-Updated English ${name}.vtt.vtt"   "$name" "English (updated)"
  make_vtt "$folder/subtitles-sk-Slovak ${name}.vtt.vtt"            "$name" "Slovenčina"
  make_vtt "$folder/subtitles-sk-Updated Slovak ${name}.vtt.vtt"    "$name" "Slovenčina (updated)"

  make_details_json "$folder/video-details.json" "$id" "$name"
done

echo "Done. Sample tree:"
ls -la "$ROOT"
