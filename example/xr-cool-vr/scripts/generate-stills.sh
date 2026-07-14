#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env.local"
WORK="$ROOT/.work"
ASSETS="$ROOT/assets"
STYLE="$ROOT/prompts/style-preamble.txt"
ANCHOR="$ASSETS/anchor-hero.jpg"
NAMES="optics tracking comfort beyond"

set -a
. "$ENV_FILE"
set +a
: "${ARK_API_KEY:?ARK_API_KEY is not set in .env.local}"

test -s "$ANCHOR" || { echo "missing approved anchor: $ANCHOR" >&2; exit 2; }
mkdir -p "$WORK" "$ASSETS"

ANCHOR_DATA="$WORK/anchor-data-uri.txt"
if [ ! -s "$ANCHOR_DATA" ]; then
  printf 'data:image/jpeg;base64,' > "$ANCHOR_DATA"
  base64 < "$ANCHOR" | tr -d '\n' >> "$ANCHOR_DATA"
fi

gen_still() {
  name="$1"
  subject="$ROOT/prompts/subject-$name.txt"
  prompt="$WORK/still-$name.txt"
  request="$WORK/still-$name-request.json"
  response="$WORK/still-$name-response.json"
  output="$ASSETS/still-$name.jpg"

  if [ -s "$output" ]; then
    echo "still $name cached"
    return 0
  fi

  test -s "$subject" || { echo "missing subject prompt: $subject" >&2; return 2; }
  { cat "$STYLE"; printf '\n'; cat "$subject"; } > "$prompt"

  jq -n --rawfile prompt "$prompt" --rawfile image "$ANCHOR_DATA" '{
    model: "doubao-seedream-5-0-pro-260628",
    prompt: $prompt,
    image: [$image],
    response_format: "url",
    size: "2K",
    stream: false,
    watermark: false
  }' > "$request"

  code=$(curl -sS -o "$response" -w '%{http_code}' \
    -X POST https://ark.cn-beijing.volces.com/api/v3/images/generations \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $ARK_API_KEY" \
    --data-binary @"$request")

  if [ "$code" != 200 ]; then
    echo "still $name failed: HTTP $code" >&2
    jq -r '.error.message // .message // "unknown API error"' "$response" >&2
    return 3
  fi

  url=$(jq -r '.data[0].url // empty' "$response")
  test -n "$url" || { echo "still $name response missing URL" >&2; return 4; }
  curl -fsSL "$url" -o "$output"
  echo "still $name generated"
}

pids=""
for name in $NAMES; do
  gen_still "$name" &
  pids="$pids $!"
done

status=0
for pid in $pids; do
  wait "$pid" || status=1
done
exit "$status"
