#!/bin/sh
set -eu

usage() {
  echo "usage: $0 <prompt-file> <output-image> [approved-anchor-image] [--reference <image>]... [--label <name>] [--force]" >&2
  exit 2
}

test "$#" -ge 2 || usage
PROMPT_FILE="$1"
OUTPUT="$2"
shift 2

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
TOOL="$SCRIPT_DIR/sw_tool.py"
MODEL="${ARK_IMAGE_MODEL:-$(python3 "$TOOL" model image default model_id)}"
API_BASE="${ARK_API_BASE:-https://ark.cn-beijing.volces.com/api/v3}"
WORK_ROOT="${ARK_WORK_DIR:-.work/ark-image}"
WORLD="${SW_WORLD_FILE:-world.json}"
LEDGER="${SW_LEDGER_FILE:-.work/usage-ledger.json}"
LABEL=$(basename "$OUTPUT")
FORCE=0

key=$(printf '%s' "$(basename "$OUTPUT")" | tr -c 'A-Za-z0-9._-' '_')
WORK="$WORK_ROOT/$key"
mkdir -p "$WORK" "$(dirname "$OUTPUT")"
REF_LIST="$WORK/reference-files.txt"
: > "$REF_LIST"

# Backward-compatible third positional argument.
if test "$#" -gt 0 && test "${1#--}" = "$1"; then
  printf '%s\n' "$1" >> "$REF_LIST"
  shift
fi
while test "$#" -gt 0; do
  case "$1" in
    --reference)
      test "$#" -ge 2 || usage
      printf '%s\n' "$2" >> "$REF_LIST"
      shift 2
      ;;
    --label)
      test "$#" -ge 2 || usage
      LABEL="$2"
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    *) usage ;;
  esac
done

: "${ARK_API_KEY:?set ARK_API_KEY in the environment or source .env.local}"
test -s "$PROMPT_FILE" || { echo "missing prompt: $PROMPT_FILE" >&2; exit 2; }
ref_count=0
while IFS= read -r reference; do
  test -n "$reference" || continue
  test -s "$reference" || { echo "missing reference image: $reference" >&2; exit 2; }
  ref_count=$((ref_count + 1))
done < "$REF_LIST"
test "$ref_count" -le 10 || { echo "Seedream supports at most 10 reference images" >&2; exit 2; }

set -- fingerprint --model "$MODEL" --prompt "$PROMPT_FILE" --param "size=2K" --param "watermark=false"
while IFS= read -r reference; do
  test -n "$reference" || continue
  set -- "$@" --input "$reference"
done < "$REF_LIST"
FINGERPRINT=$(python3 "$TOOL" "$@")
META="$WORK/cache-meta.json"
RESPONSE="$WORK/response.json"

if test "$FORCE" -eq 1; then
  rejected="$WORK/rejected-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  mkdir -p "$rejected"
  test ! -e "$OUTPUT" || mv "$OUTPUT" "$rejected/$(basename "$OUTPUT")"
  for old in "$META" "$RESPONSE" "$WORK/request-metadata.json"; do
    test ! -e "$old" || mv "$old" "$rejected/$(basename "$old")"
  done
fi

if test -s "$OUTPUT"; then
  cached=$(jq -r '.fingerprint // empty' "$META" 2>/dev/null || true)
  if test "$cached" = "$FINGERPRINT"; then
    echo "image cached (fingerprint matched): $OUTPUT"
    exit 0
  fi
  echo "stale image cache: $OUTPUT (use --force to archive and regenerate)" >&2
  exit 12
fi

# A previous accepted request may only need its temporary URL downloaded again.
cached=$(jq -r '.fingerprint // empty' "$META" 2>/dev/null || true)
url=$(jq -r '.data[0].url // empty' "$RESPONSE" 2>/dev/null || true)
if test "$cached" = "$FINGERPRINT" && test -n "$url"; then
  part="$OUTPUT.part.$$"
  if curl -fsSL "$url" -o "$part"; then
    mv "$part" "$OUTPUT"
    echo "image recovered from accepted response: $OUTPUT"
    exit 0
  fi
  rm -f "$part"
fi

REQUEST="$WORK/request.json"
REQUEST_META="$WORK/request-metadata.json"
IMAGES_JSON="$WORK/reference-images.json"
printf '[]\n' > "$IMAGES_JSON"
index=0
while IFS= read -r reference; do
  test -n "$reference" || continue
  index=$((index + 1))
  mime=image/jpeg
  case "$reference" in
    *.png|*.PNG) mime=image/png ;;
    *.webp|*.WEBP) mime=image/webp ;;
  esac
  uri="$WORK/reference-$index.data-uri"
  printf 'data:%s;base64,' "$mime" > "$uri"
  base64 < "$reference" | tr -d '\n' >> "$uri"
  next="$IMAGES_JSON.next"
  jq --rawfile value "$uri" '. + [$value]' "$IMAGES_JSON" > "$next"
  mv "$next" "$IMAGES_JSON"
done < "$REF_LIST"

jq -n --arg model "$MODEL" --rawfile prompt "$PROMPT_FILE" --slurpfile images "$IMAGES_JSON" \
  '{model:$model,prompt:$prompt,response_format:"url",size:"2K",stream:false,watermark:false}
   + (if ($images[0] | length) > 0 then {image:$images[0]} else {} end)' > "$REQUEST"
jq 'if .image then .image = (.image | map("<redacted:data-uri>")) else . end' "$REQUEST" > "$REQUEST_META"

cleanup_sensitive() {
  rm -f "$REQUEST" "$IMAGES_JSON" "$WORK"/reference-*.data-uri
}
trap cleanup_sensitive EXIT HUP INT TERM

OPERATION_ID=$(python3 "$TOOL" reserve --world "$WORLD" --ledger "$LEDGER" \
  --kind image --mode image --label "$LABEL" --model "$MODEL" --fingerprint "$FINGERPRINT")
jq -n --arg fingerprint "$FINGERPRINT" --arg model "$MODEL" --arg operation_id "$OPERATION_ID" \
  --argjson reference_count "$ref_count" \
  '{fingerprint:$fingerprint,model:$model,operation_id:$operation_id,reference_count:$reference_count,status:"reserved"}' > "$META"

curl_rc=0
code=$(curl -sS -o "$RESPONSE" -w '%{http_code}' \
  -X POST "$API_BASE/images/generations" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ARK_API_KEY" \
  --data-binary @"$REQUEST") || curl_rc=$?
cleanup_sensitive
trap - EXIT HUP INT TERM

if test "$curl_rc" -ne 0; then
  python3 "$TOOL" fail --ledger "$LEDGER" --operation-id "$OPERATION_ID" --ambiguous --reason "curl transport error $curl_rc"
  echo "image request transport failed; reservation kept as ambiguous" >&2
  exit 3
fi
if test "$code" != 200; then
  python3 "$TOOL" fail --ledger "$LEDGER" --operation-id "$OPERATION_ID" --reason "HTTP $code"
  echo "image request failed: HTTP $code" >&2
  jq -r '.error.message // .message // .error.code // "unknown API error"' "$RESPONSE" >&2
  exit 3
fi

python3 "$TOOL" accept-image --ledger "$LEDGER" --operation-id "$OPERATION_ID" --response "$RESPONSE"
url=$(jq -r '.data[0].url // empty' "$RESPONSE")
test -n "$url" || { echo "image response missing data[0].url" >&2; exit 4; }
part="$OUTPUT.part.$$"
trap 'rm -f "$part"' EXIT HUP INT TERM
curl -fsSL "$url" -o "$part"
mv "$part" "$OUTPUT"
trap - EXIT HUP INT TERM
next_meta="$META.next"
jq '.status="succeeded"' "$META" > "$next_meta"
mv "$next_meta" "$META"
echo "image downloaded: $OUTPUT"
