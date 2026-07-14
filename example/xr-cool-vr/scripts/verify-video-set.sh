#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <preview|final>" >&2
  exit 2
fi

MODE="$1"
case "$MODE" in preview|final) ;; *) echo "invalid mode: $MODE" >&2; exit 2 ;; esac

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ASSETS="$ROOT/assets"
VIDEOS="$ASSETS/vid"
WORK="$ROOT/.work/verify-$MODE"
LEGS="hero optics tracking comfort beyond"

mkdir -p "$WORK"
failed=0
previous=""

for leg in $LEGS; do
  missing=0
  video="$VIDEOS/$MODE-$leg.mp4"
  mobile="$VIDEOS/$MODE-$leg-m.mp4"
  poster="$ASSETS/poster-$MODE-$leg.webp"
  poster_mobile="$ASSETS/poster-$MODE-$leg-m.webp"
  first="$WORK/$leg-first.png"
  reference="$WORK/$leg-reference.png"

  for file in "$video" "$mobile" "$poster" "$poster_mobile"; do
    test -s "$file" || { echo "missing artifact: $file" >&2; failed=1; missing=1; }
  done
  test "$missing" -eq 0 || continue

  codec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of default=nw=1:nk=1 "$video")
  fps=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of default=nw=1:nk=1 "$video")
  audio_count=$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$video" | wc -l | tr -d ' ')
  width=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of default=nw=1:nk=1 "$video")
  height=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=nw=1:nk=1 "$video")

  if [ "$codec" != h264 ] || [ "$fps" != 24/1 ] || [ "$audio_count" != 0 ]; then
    echo "$MODE/$leg invalid stream: codec=$codec fps=$fps audio=$audio_count" >&2
    failed=1
  fi

  ffmpeg -hide_banner -loglevel error -y -i "$video" -vf "select=eq(n\,0)" -frames:v 1 "$first"
  if [ -z "$previous" ]; then
    ffmpeg -hide_banner -loglevel error -y -i "$ASSETS/anchor-hero.jpg" \
      -vf "crop=iw:iw*9/16:0:(ih-iw*9/16)/2,scale=$width:$height" "$reference"
    threshold=0.95
  else
    ffmpeg -hide_banner -loglevel error -y -i "$ASSETS/$MODE-$previous-last.jpg" \
      -vf "scale=$width:$height" "$reference"
    threshold=0.96
  fi

  ssim=$(ffmpeg -hide_banner -i "$reference" -i "$first" -lavfi ssim -f null - 2>&1 \
    | rg -o 'All:[0-9.]+' | cut -d: -f2)
  printf '%s/%s %sx%s codec=%s fps=%s audio=%s ssim=%s\n' \
    "$MODE" "$leg" "$width" "$height" "$codec" "$fps" "$audio_count" "$ssim"

  if ! awk -v value="$ssim" -v min="$threshold" 'BEGIN { exit !(value >= min) }'; then
    echo "$MODE/$leg SSIM below $threshold" >&2
    failed=1
  fi
  previous="$leg"
done

exit "$failed"
