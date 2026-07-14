#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <preview|final>" >&2
  exit 2
fi

MODE="$1"
case "$MODE" in
  preview)
    DESKTOP_CRF=20
    MOBILE_CRF=22
    ;;
  final)
    DESKTOP_CRF=16
    MOBILE_CRF=20
    ;;
  *) echo "invalid mode: $MODE" >&2; exit 2 ;;
esac

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ASSETS="$ROOT/assets"
OUT="$ASSETS/vid"
LEGS="hero optics tracking comfort beyond"

mkdir -p "$OUT"

previous=""
for leg in $LEGS; do
  source="$ASSETS/$MODE-$leg.mp4"
  desktop="$OUT/$MODE-$leg.mp4"
  mobile="$OUT/$MODE-$leg-m.mp4"
  poster="$ASSETS/poster-$MODE-$leg.webp"
  poster_mobile="$ASSETS/poster-$MODE-$leg-m.webp"

  test -s "$source" || { echo "missing source video: $source" >&2; exit 3; }

  if [ -z "$previous" ]; then
    ffmpeg -hide_banner -loglevel error -y \
      -i "$source" -map 0:v:0 -an \
      -c:v libx264 -preset slow -crf "$DESKTOP_CRF" -g 8 -keyint_min 8 -sc_threshold 0 \
      -pix_fmt yuv420p -movflags +faststart "$desktop"
  else
    seam="$ASSETS/$MODE-$previous-last.jpg"
    test -s "$seam" || { echo "missing seam frame: $seam" >&2; exit 3; }
    ffmpeg -hide_banner -loglevel error -y \
      -loop 1 -framerate 24 -i "$seam" -i "$source" \
      -filter_complex "[0:v]settb=AVTB,setpts=PTS-STARTPTS[a];[1:v]settb=AVTB,setpts=PTS-STARTPTS[b];[a][b]blend=all_expr='A*(1-min(T/0.125,1))+B*min(T/0.125,1)':shortest=1,format=yuv420p[v]" \
      -map "[v]" -an \
      -c:v libx264 -preset slow -crf "$DESKTOP_CRF" -g 8 -keyint_min 8 -sc_threshold 0 \
      -pix_fmt yuv420p -movflags +faststart "$desktop"
  fi

  ffmpeg -hide_banner -loglevel error -y \
    -i "$desktop" -map 0:v:0 -an -vf "scale=-2:720" \
    -c:v libx264 -preset slow -crf "$MOBILE_CRF" -g 4 -keyint_min 4 -sc_threshold 0 \
    -pix_fmt yuv420p -movflags +faststart "$mobile"

  ffmpeg -hide_banner -loglevel error -y -i "$desktop" -frames:v 1 "$poster"
  ffmpeg -hide_banner -loglevel error -y -i "$mobile" -frames:v 1 "$poster_mobile"
  echo "encoded $MODE/$leg"
  previous="$leg"
done
