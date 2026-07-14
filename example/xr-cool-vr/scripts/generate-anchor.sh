#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env.local"
WORK="$ROOT/.work"
ASSETS="$ROOT/assets"
PROMPT="$ROOT/prompts/still-hero.txt"
OUTPUT="$ASSETS/anchor-hero.jpg"

if [ -s "$OUTPUT" ]; then
  echo "anchor cached: $OUTPUT"
  exit 0
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "missing $ENV_FILE" >&2
  exit 2
fi

set -a
. "$ENV_FILE"
set +a
: "${ARK_API_KEY:?ARK_API_KEY is not set in .env.local}"

mkdir -p "$WORK" "$ASSETS"

jq -n --rawfile prompt "$PROMPT" '{
  model: "doubao-seedream-5-0-pro-260628",
  prompt: $prompt,
  response_format: "url",
  size: "2K",
  stream: false,
  watermark: false
}' > "$WORK/anchor-request.json"

code=$(curl -sS -o "$WORK/anchor-response.json" -w '%{http_code}' \
  -X POST https://ark.cn-beijing.volces.com/api/v3/images/generations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  --data-binary @"$WORK/anchor-request.json")

if [ "$code" != 200 ]; then
  echo "anchor request failed: HTTP $code" >&2
  jq -r '.error.message // .message // "unknown API error"' "$WORK/anchor-response.json" >&2
  exit 3
fi

url=$(jq -r '.data[0].url // empty' "$WORK/anchor-response.json")
if [ -z "$url" ]; then
  echo "anchor response did not contain a download URL" >&2
  exit 4
fi

curl -fsSL "$url" -o "$OUTPUT"
echo "anchor generated: $OUTPUT"
