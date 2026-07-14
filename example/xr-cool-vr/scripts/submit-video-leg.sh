#!/bin/sh
set -eu

usage() {
  echo "usage: $0 <preview|final> <hero|optics|tracking|comfort|beyond> <first-frame-image>" >&2
  exit 2
}

test "$#" -eq 3 || usage
MODE="$1"
LEG="$2"
FIRST_FRAME="$3"

case "$MODE" in
  preview)
    MODEL="doubao-seedance-2-0-mini-260615"
    RESOLUTION="720p"
    ;;
  final)
    MODEL="doubao-seedance-2-0-260128"
    RESOLUTION="1080p"
    ;;
  *) usage ;;
esac

case "$LEG" in
  hero|optics|tracking|comfort|beyond) ;;
  *) usage ;;
esac

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env.local"
WORK="$ROOT/.work/video-$MODE-$LEG"
STYLE="$ROOT/prompts/video-style.txt"
SUBJECT="$ROOT/prompts/video-$LEG.txt"
TASK_ID="$WORK/task-id.txt"

test -f "$ENV_FILE" || { echo "missing $ENV_FILE" >&2; exit 2; }
test -s "$FIRST_FRAME" || { echo "missing first frame: $FIRST_FRAME" >&2; exit 2; }
test -s "$STYLE" || { echo "missing style prompt: $STYLE" >&2; exit 2; }
test -s "$SUBJECT" || { echo "missing leg prompt: $SUBJECT" >&2; exit 2; }

if [ -s "$TASK_ID" ]; then
  echo "task already submitted: $(cat "$TASK_ID")"
  exit 0
fi

set -a
. "$ENV_FILE"
set +a
: "${ARK_API_KEY:?ARK_API_KEY is not set in .env.local}"

mkdir -p "$WORK"
cat "$STYLE" "$SUBJECT" > "$WORK/prompt.txt"

MIME="image/jpeg"
case "$FIRST_FRAME" in
  *.png) MIME="image/png" ;;
  *.webp) MIME="image/webp" ;;
esac

printf 'data:%s;base64,' "$MIME" > "$WORK/first-frame-data-uri.txt"
base64 < "$FIRST_FRAME" | tr -d '\n' >> "$WORK/first-frame-data-uri.txt"

jq -n \
  --arg model "$MODEL" \
  --arg resolution "$RESOLUTION" \
  --rawfile prompt "$WORK/prompt.txt" \
  --rawfile image "$WORK/first-frame-data-uri.txt" \
  '{
    model: $model,
    content: [
      {type: "text", text: $prompt},
      {type: "image_url", image_url: {url: $image}, role: "first_frame"}
    ],
    generate_audio: false,
    ratio: "16:9",
    resolution: $resolution,
    duration: 5,
    watermark: false,
    return_last_frame: true
  }' > "$WORK/request.json"

code=$(curl -sS -o "$WORK/create-response.json" -w '%{http_code}' \
  -X POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  --data-binary @"$WORK/request.json")

if [ "$code" != 200 ]; then
  echo "video task creation failed: HTTP $code" >&2
  jq -r '.error.message // .message // "unknown API error"' "$WORK/create-response.json" >&2
  exit 3
fi

id=$(jq -r '.id // empty' "$WORK/create-response.json")
test -n "$id" || { echo "video task response missing id" >&2; exit 4; }
printf '%s\n' "$id" > "$TASK_ID"
echo "submitted $MODE/$LEG: $id"
