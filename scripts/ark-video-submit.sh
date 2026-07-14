#!/bin/sh
set -eu

usage() {
  echo "usage: $0 <preview|final> <prompt-file> <first-frame> <task-dir> [last-frame] [ratio] [duration] [--reference-image <image>]... [--label <name>] [--force]" >&2
  exit 2
}

test "$#" -ge 4 || usage
MODE="$1"
PROMPT_FILE="$2"
FIRST_FRAME="$3"
TASK_DIR="$4"
shift 4
LAST_FRAME=""
RATIO="16:9"
DURATION="5"

# Backward-compatible positional tail.
if test "$#" -gt 0 && test "${1#--}" = "$1"; then LAST_FRAME="$1"; shift; fi
if test "$#" -gt 0 && test "${1#--}" = "$1"; then RATIO="$1"; shift; fi
if test "$#" -gt 0 && test "${1#--}" = "$1"; then DURATION="$1"; shift; fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
TOOL="$SCRIPT_DIR/sw_tool.py"
case "$MODE" in preview|final) ;; *) usage ;; esac
case "$MODE" in
  preview) MODEL_OVERRIDE="${ARK_VIDEO_PREVIEW_MODEL-}" ;;
  final) MODEL_OVERRIDE="${ARK_VIDEO_FINAL_MODEL-}" ;;
esac
MODEL="${MODEL_OVERRIDE:-$(python3 "$TOOL" model video "$MODE" model_id)}"
RESOLUTION=$(python3 "$TOOL" model video "$MODE" resolution)
API_BASE="${ARK_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
WORLD="${SW_WORLD_FILE:-world.json}"
LEDGER="${SW_LEDGER_FILE:-.work/usage-ledger.json}"
LABEL=$(basename "$TASK_DIR")
FORCE=0
REF_LIST=$(mktemp "${TMPDIR:-/tmp}/scroll-world-refs.XXXXXX")
trap 'rm -f "$REF_LIST"' EXIT HUP INT TERM

while test "$#" -gt 0; do
  case "$1" in
    --reference-image)
      test "$#" -ge 2 || usage
      printf '%s\n' "$2" >> "$REF_LIST"
      shift 2
      ;;
    --label)
      test "$#" -ge 2 || usage
      LABEL="$2"
      shift 2
      ;;
    --force) FORCE=1; shift ;;
    *) usage ;;
  esac
done

: "${ARK_API_KEY:?set ARK_API_KEY in the environment or source .env.local}"
test -s "$PROMPT_FILE" || { echo "missing prompt: $PROMPT_FILE" >&2; exit 2; }
test -s "$FIRST_FRAME" || { echo "missing first frame: $FIRST_FRAME" >&2; exit 2; }
if test -n "$LAST_FRAME"; then
  test -s "$LAST_FRAME" || { echo "missing last frame: $LAST_FRAME" >&2; exit 2; }
fi
case "$DURATION" in *[!0-9]*|'') usage ;; esac
test "$DURATION" -ge 4 && test "$DURATION" -le 15 || { echo "duration must be 4..15 seconds" >&2; exit 2; }

ref_count=0
while IFS= read -r reference; do
  test -n "$reference" || continue
  test -s "$reference" || { echo "missing reference image: $reference" >&2; exit 2; }
  ref_count=$((ref_count + 1))
done < "$REF_LIST"
image_count=$((1 + ref_count))
test -z "$LAST_FRAME" || image_count=$((image_count + 1))
test "$image_count" -le 9 || { echo "Seedance supports at most 9 image inputs" >&2; exit 2; }

set -- fingerprint --model "$MODEL" --prompt "$PROMPT_FILE" --input "$FIRST_FRAME" \
  --param "mode=$MODE" --param "resolution=$RESOLUTION" --param "ratio=$RATIO" --param "duration=$DURATION"
test -z "$LAST_FRAME" || set -- "$@" --input "$LAST_FRAME"
while IFS= read -r reference; do
  test -n "$reference" || continue
  set -- "$@" --input "$reference"
done < "$REF_LIST"
FINGERPRINT=$(python3 "$TOOL" "$@")

if test "$FORCE" -eq 1 && test -d "$TASK_DIR"; then
  rejected="$TASK_DIR.rejected-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  mv "$TASK_DIR" "$rejected"
  echo "archived previous task: $rejected"
fi
mkdir -p "$TASK_DIR"
TASK_ID="$TASK_DIR/task-id.txt"
META="$TASK_DIR/task-meta.json"
if test -s "$TASK_ID"; then
  cached=$(jq -r '.fingerprint // empty' "$META" 2>/dev/null || true)
  if test "$cached" = "$FINGERPRINT"; then
    echo "task cached (fingerprint matched): $(cat "$TASK_ID")"
    exit 0
  fi
  echo "stale task cache: $TASK_DIR (use --force to archive and resubmit)" >&2
  exit 12
fi

make_data_uri() {
  source_file="$1"
  target_file="$2"
  mime=image/jpeg
  case "$source_file" in
    *.png|*.PNG) mime=image/png ;;
    *.webp|*.WEBP) mime=image/webp ;;
  esac
  printf 'data:%s;base64,' "$mime" > "$target_file"
  base64 < "$source_file" | tr -d '\n' >> "$target_file"
}

CONTENT="$TASK_DIR/content.json"
printf '[]\n' > "$CONTENT"
next="$CONTENT.next"
jq --rawfile prompt "$PROMPT_FILE" '. + [{type:"text",text:$prompt}]' "$CONTENT" > "$next" && mv "$next" "$CONTENT"
FIRST_URI="$TASK_DIR/first-frame.data-uri"
make_data_uri "$FIRST_FRAME" "$FIRST_URI"
jq --rawfile value "$FIRST_URI" '. + [{type:"image_url",image_url:{url:$value},role:"first_frame"}]' "$CONTENT" > "$next" && mv "$next" "$CONTENT"
if test -n "$LAST_FRAME"; then
  LAST_URI="$TASK_DIR/last-frame.data-uri"
  make_data_uri "$LAST_FRAME" "$LAST_URI"
  jq --rawfile value "$LAST_URI" '. + [{type:"image_url",image_url:{url:$value},role:"last_frame"}]' "$CONTENT" > "$next" && mv "$next" "$CONTENT"
fi
index=0
while IFS= read -r reference; do
  test -n "$reference" || continue
  index=$((index + 1))
  uri="$TASK_DIR/reference-$index.data-uri"
  make_data_uri "$reference" "$uri"
  jq --rawfile value "$uri" '. + [{type:"image_url",image_url:{url:$value},role:"reference_image"}]' "$CONTENT" > "$next" && mv "$next" "$CONTENT"
done < "$REF_LIST"

REQUEST="$TASK_DIR/request.json"
REQUEST_META="$TASK_DIR/request-metadata.json"
RESPONSE="$TASK_DIR/create-response.json"
jq -n --arg model "$MODEL" --arg resolution "$RESOLUTION" --arg ratio "$RATIO" \
  --argjson duration "$DURATION" --slurpfile content "$CONTENT" \
  '{model:$model,content:$content[0],generate_audio:false,ratio:$ratio,resolution:$resolution,duration:$duration,watermark:false,return_last_frame:true}' > "$REQUEST"
jq '.content |= map(if .type == "image_url" then .image_url.url="<redacted:data-uri>" else . end)' "$REQUEST" > "$REQUEST_META"

cleanup_sensitive() {
  rm -f "$REQUEST" "$CONTENT" "$TASK_DIR"/*.data-uri
}
trap 'cleanup_sensitive; rm -f "$REF_LIST"' EXIT HUP INT TERM

OPERATION_ID=$(python3 "$TOOL" reserve --world "$WORLD" --ledger "$LEDGER" \
  --kind video --mode "$MODE" --label "$LABEL" --model "$MODEL" --fingerprint "$FINGERPRINT")
jq -n --arg fingerprint "$FINGERPRINT" --arg model "$MODEL" --arg mode "$MODE" \
  --arg operation_id "$OPERATION_ID" --arg ratio "$RATIO" --arg resolution "$RESOLUTION" \
  --argjson duration "$DURATION" --argjson reference_count "$ref_count" \
  '{fingerprint:$fingerprint,model:$model,mode:$mode,operation_id:$operation_id,ratio:$ratio,resolution:$resolution,duration:$duration,reference_count:$reference_count,status:"reserved"}' > "$META"

curl_rc=0
code=$(curl -sS -o "$RESPONSE" -w '%{http_code}' \
  -X POST "$API_BASE/contents/generations/tasks" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ARK_API_KEY" \
  --data-binary @"$REQUEST") || curl_rc=$?
cleanup_sensitive
rm -f "$REF_LIST"
trap - EXIT HUP INT TERM

if test "$curl_rc" -ne 0; then
  python3 "$TOOL" fail --ledger "$LEDGER" --operation-id "$OPERATION_ID" --ambiguous --reason "curl transport error $curl_rc"
  echo "video task transport failed; reservation kept as ambiguous" >&2
  exit 3
fi
if test "$code" != 200; then
  python3 "$TOOL" fail --ledger "$LEDGER" --operation-id "$OPERATION_ID" --reason "HTTP $code"
  echo "video task creation failed: HTTP $code" >&2
  jq -r '.error.message // .message // .error.code // "unknown API error"' "$RESPONSE" >&2
  exit 3
fi

id=$(python3 "$TOOL" accept-video --ledger "$LEDGER" --operation-id "$OPERATION_ID" --response "$RESPONSE")
printf '%s\n' "$id" > "$TASK_ID"
next_meta="$META.next"
jq --arg task_id "$id" '.status="submitted" | .task_id=$task_id' "$META" > "$next_meta"
mv "$next_meta" "$META"
echo "video task submitted: $id"
